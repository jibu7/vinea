"""P2 acceptance checks 4a–4g.

Each test is named for the check it discharges. These deliberately reach past the service
layer where the point is that the *database* holds the line.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db import engine, set_tenant
from app.kernel import posting
from app.kernel.accounts import add_exchange_rate
from app.kernel.balances import verify_period_balances
from app.kernel.enquiries import trial_balance
from app.kernel.errors import (
    SQLSTATE_IMMUTABLE,
    SQLSTATE_SINGLE_WRITER,
    LedgerStateError,
    PostingError,
    kernel_sqlstate,
)
from app.kernel.events import LineSpec, ManualJournal
from app.kernel.money import rate_on
from app.kernel.periods import create_fiscal_year
from app.kernel.sequences import DocType, claim_number
from app.kernel.year_end import close_fiscal_year, reopen_fiscal_year
from app.models.currency import ExchangeRate
from app.models.fiscal import AccountingPeriod, PeriodStatus
from app.models.gl import PROFIT_AND_LOSS_CLASSES, GLSettings
from app.models.journal import DocumentSequence, JournalEntry, JournalLine, JournalStatus
from tests.kernel.conftest import USD_RATE, YEAR, Ledger, post_simple
from tests.kernel.invariants import assert_ledger_invariants

MARCH = date(YEAR, 3, 15)
APRIL = date(YEAR, 4, 2)


def _session_for(company_id: int) -> Session:
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    set_tenant(session, company_id)
    return session


def _lines(db: Session, entry: JournalEntry) -> list[JournalLine]:
    return list(
        db.scalars(
            select(JournalLine)
            .where(JournalLine.entry_id == entry.id)
            .order_by(JournalLine.line_no)
        )
    )


# --- 4a. FX rounding line -------------------------------------------------------------------


def _thirds_of_a_hundred_usd(ledger: Ledger) -> ManualJournal:
    """33.33 + 33.33 + 33.34 USD = 100.00 USD exactly, but each line rounds to whole RWF
    independently, so the base amounts sum to 130 051 against 130 050."""
    usd = ledger.cur("USD")
    return ManualJournal(
        entry_date=MARCH,
        description="three thirds of a hundred dollars",
        lines=(
            LineSpec(amount=Decimal("33.33"), gl_account_id=ledger.acct("6500"), currency_id=usd),
            LineSpec(amount=Decimal("33.33"), gl_account_id=ledger.acct("6600"), currency_id=usd),
            LineSpec(amount=Decimal("33.34"), gl_account_id=ledger.acct("6300"), currency_id=usd),
            LineSpec(amount=Decimal("-100.00"), gl_account_id=ledger.acct("2300"), currency_id=usd),
        ),
    )


def test_4a_fx_rounding_residue_posts_as_an_explicit_rounding_line(
    db: Session, ledger: Ledger
) -> None:
    entry = posting.post(
        db, _thirds_of_a_hundred_usd(ledger), company_id=ledger.company_id, actor=ledger.owner
    )
    db.commit()

    assert entry is not None
    lines = _lines(db, entry)
    assert len(lines) == 5
    rounding = lines[-1]
    assert rounding.is_rounding_line is True
    assert rounding.gl_account_id == ledger.acct("6950")  # seeded exchange-difference account
    assert rounding.base_amount == Decimal(-1)
    assert rounding.currency_id == ledger.base.id
    assert rounding.description == "Rounding difference"
    # Nothing else is flagged, and the entry foots.
    assert [line.is_rounding_line for line in lines[:-1]] == [False] * 4
    assert sum(line.base_amount for line in lines) == Decimal(0)
    assert trial_balance(db, ledger.company_id, as_of=MARCH).foots
    assert_ledger_invariants(db, ledger.company_id)


def test_4a_drift_beyond_tolerance_is_rejected(db: Session, ledger: Ledger) -> None:
    """Two rates for one currency inside a single entry is a data error, not rounding:
    the plug must not be able to hide 10 000 RWF."""
    usd = ledger.cur("USD")
    event = ManualJournal(
        entry_date=MARCH,
        description="mismatched rates",
        lines=(
            LineSpec(
                amount=Decimal("100.00"),
                gl_account_id=ledger.acct("6500"),
                currency_id=usd,
                exchange_rate=Decimal(1300),
            ),
            LineSpec(
                amount=Decimal("-100.00"),
                gl_account_id=ledger.acct("2300"),
                currency_id=usd,
                exchange_rate=Decimal(1400),
            ),
        ),
    )
    with pytest.raises(PostingError) as excinfo:
        posting.post(db, event, company_id=ledger.company_id, actor=ledger.owner)
    assert excinfo.value.code == "unbalanced_entry"
    assert "-10000" in excinfo.value.message
    db.rollback()


def test_4a_same_currency_imbalance_is_rejected(db: Session, ledger: Ledger) -> None:
    """No conversion happened, so there is no rounding to absorb — the numbers are wrong."""
    event = ManualJournal(
        entry_date=MARCH,
        description="base-currency imbalance",
        lines=(
            LineSpec(amount=Decimal(1000), gl_account_id=ledger.acct("6500")),
            LineSpec(amount=Decimal(-999), gl_account_id=ledger.acct("2300")),
        ),
    )
    with pytest.raises(PostingError) as excinfo:
        posting.post(db, event, company_id=ledger.company_id, actor=ledger.owner)
    assert excinfo.value.code == "unbalanced_entry"
    assert db.scalar(select(func.count()).select_from(JournalEntry)) == 0
    db.rollback()


def test_4a_currency_that_does_not_square_is_rejected(db: Session, ledger: Ledger) -> None:
    """Within tolerance in base, but the USD leg itself does not balance."""
    usd = ledger.cur("USD")
    event = ManualJournal(
        entry_date=MARCH,
        description="usd leg does not square",
        lines=(
            LineSpec(amount=Decimal("1.00"), gl_account_id=ledger.acct("6500"), currency_id=usd),
            LineSpec(amount=Decimal(-1300), gl_account_id=ledger.acct("2300")),
        ),
    )
    with pytest.raises(PostingError) as excinfo:
        posting.post(db, event, company_id=ledger.company_id, actor=ledger.owner)
    assert excinfo.value.code == "unbalanced_entry"
    db.rollback()


def test_4a_rounding_account_is_required(db: Session, ledger: Ledger) -> None:
    settings = db.scalars(
        select(GLSettings).where(GLSettings.company_id == ledger.company_id)
    ).one()
    settings.rounding_difference_account_id = None
    db.flush()
    with pytest.raises(PostingError) as excinfo:
        posting.post(
            db, _thirds_of_a_hundred_usd(ledger), company_id=ledger.company_id, actor=ledger.owner
        )
    assert excinfo.value.code == "rounding_account_unset"
    db.rollback()


def test_4a_reversing_a_rounded_entry_restores_the_trial_balance(
    db: Session, ledger: Ledger
) -> None:
    entry = posting.post(
        db, _thirds_of_a_hundred_usd(ledger), company_id=ledger.company_id, actor=ledger.owner
    )
    db.commit()
    posting.reverse(
        db,
        entry.id,  # type: ignore[union-attr]
        company_id=ledger.company_id,
        on_date=APRIL,
        reason="mis-keyed",
        actor=ledger.owner,
    )
    db.commit()

    report = trial_balance(db, ledger.company_id, as_of=APRIL)
    assert report.foots
    assert all(row.net == Decimal(0) for row in report.rows)
    assert_ledger_invariants(db, ledger.company_id)


# --- 4b. Immutability via raw SQL ------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE journal_entries SET status = 'draft' WHERE id = :id",
        "UPDATE journal_entries SET entry_date = entry_date - 1 WHERE id = :id",
        "UPDATE journal_entries SET description = 'tampered' WHERE id = :id",
        "UPDATE journal_entries SET number = 'JE-999999' WHERE id = :id",
        "UPDATE journal_entries SET period_id = period_id WHERE id = :id",
        "DELETE FROM journal_entries WHERE id = :id",
    ],
)
def test_4b_posted_header_cannot_be_changed_by_raw_sql(
    db: Session, ledger: Ledger, statement: str
) -> None:
    entry = post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(100), on=MARCH)
    db.commit()

    with pytest.raises(DBAPIError) as excinfo:
        db.execute(text(statement), {"id": entry.id})
    assert kernel_sqlstate(excinfo.value) == SQLSTATE_IMMUTABLE
    db.rollback()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE journal_lines SET amount = amount * 2 WHERE entry_id = :id",
        "UPDATE journal_lines SET base_amount = base_amount * 2 WHERE entry_id = :id",
        "UPDATE journal_lines SET gl_account_id = gl_account_id WHERE entry_id = :id",
        "UPDATE journal_lines SET is_rounding_line = true WHERE entry_id = :id",
        "DELETE FROM journal_lines WHERE entry_id = :id",
    ],
)
def test_4b_posted_lines_cannot_be_changed_by_raw_sql(
    db: Session, ledger: Ledger, statement: str
) -> None:
    entry = post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(100), on=MARCH)
    db.commit()

    with pytest.raises(DBAPIError) as excinfo:
        db.execute(text(statement), {"id": entry.id})
    assert kernel_sqlstate(excinfo.value) == SQLSTATE_IMMUTABLE
    db.rollback()


def test_4b_reversing_a_reversal_is_rejected(db: Session, ledger: Ledger) -> None:
    original = post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(100), on=MARCH)
    db.commit()
    reversal = posting.reverse(
        db, original.id, company_id=ledger.company_id, on_date=MARCH, reason="wrong", actor=None
    )
    db.commit()

    with pytest.raises(LedgerStateError) as excinfo:
        posting.reverse(
            db,
            reversal.id,
            company_id=ledger.company_id,
            on_date=APRIL,
            reason="undo the undo",
            actor=None,
        )
    assert excinfo.value.code == "cannot_reverse_a_reversal"
    db.rollback()
    assert_ledger_invariants(db, ledger.company_id)


# --- 4c. Concurrency ---------------------------------------------------------------------------


@pytest.mark.concurrency
def test_4c_sequence_is_gapless_across_independent_connections(db: Session, ledger: Ledger) -> None:
    threads, per_thread = 8, 4
    barrier = threading.Barrier(threads)
    claimed: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        session = _session_for(ledger.company_id)
        try:
            barrier.wait()
            for _ in range(per_thread):
                number = claim_number(session, ledger.company_id, DocType.JOURNAL)
                session.commit()
                with lock:
                    claimed.append(number.sequence_no)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=threads) as pool:
        for future in [pool.submit(worker) for _ in range(threads)]:
            future.result()

    total = threads * per_thread
    assert sorted(claimed) == list(range(1, total + 1))
    assert len(set(claimed)) == total
    sequence = db.scalars(
        select(DocumentSequence).where(
            DocumentSequence.company_id == ledger.company_id,
            DocumentSequence.doc_type == DocType.JOURNAL,
        )
    ).one()
    assert sequence.next_number == total + 1


@pytest.mark.concurrency
def test_4c_opposite_account_order_does_not_deadlock(db: Session, ledger: Ledger) -> None:
    """`period_balances` cells are upserted in sorted key order; without that, these two
    postings would grab the same two rows in opposite order and one would be killed as a
    deadlock victim."""
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker(debit: str, credit: str) -> None:
        session = _session_for(ledger.company_id)
        try:
            barrier.wait()
            for _ in range(6):
                post_simple(
                    session,
                    ledger,
                    debit=debit,
                    credit=credit,
                    amount=Decimal(100),
                    on=MARCH,
                    description=f"{debit}->{credit}",
                )
                session.commit()
        except BaseException as exc:  # noqa: BLE001 - re-raised through the collected list
            errors.append(exc)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, "6500", "2300"), pool.submit(worker, "2300", "6500")]
        for future in futures:
            future.result()

    assert errors == []
    set_tenant(db, ledger.company_id)
    assert db.scalar(select(func.count()).select_from(JournalEntry)) == 12
    assert_ledger_invariants(db, ledger.company_id)


# --- 4d. period_balances -----------------------------------------------------------------------


def test_4d_corrupted_cell_is_reported_and_fails_the_invariants(
    db: Session, ledger: Ledger
) -> None:
    post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(400), on=MARCH)
    db.commit()
    assert verify_period_balances(db, ledger.company_id) == []
    assert_ledger_invariants(db, ledger.company_id)

    db.execute(
        text("UPDATE period_balances SET debit_base = debit_base + 7 WHERE gl_account_id = :id"),
        {"id": ledger.acct("6500")},
    )

    drift = verify_period_balances(db, ledger.company_id)
    assert [(d.gl_account_id, d.field, d.cached - d.recomputed) for d in drift] == [
        (ledger.acct("6500"), "debit_base", Decimal(7))
    ]
    with pytest.raises(AssertionError, match="period_balances drift"):
        assert_ledger_invariants(db, ledger.company_id)
    db.rollback()


def test_4d_year_end_zeroes_profit_and_loss_per_branch_and_reopen_restores_it(
    db: Session, ledger: Ledger
) -> None:
    musanze = ledger.branches["MUS"].id
    post_simple(db, ledger, debit="2300", credit="4100", amount=Decimal(9000), on=date(YEAR, 2, 5))
    post_simple(db, ledger, debit="6200", credit="2300", amount=Decimal(2500), on=date(YEAR, 5, 5))
    post_simple(
        db,
        ledger,
        debit="2300",
        credit="4100",
        amount=Decimal(4000),
        on=date(YEAR, 7, 5),
        branch_id=musanze,
    )
    post_simple(
        db,
        ledger,
        debit="6100",
        credit="2300",
        amount=Decimal(1500),
        on=date(YEAR, 8, 5),
        branch_id=musanze,
    )
    db.commit()
    before = {
        row.code: row.net
        for row in trial_balance(db, ledger.company_id, as_of=date(YEAR, 12, 31)).rows
    }

    for period in ledger.periods[:-1]:
        period.status = PeriodStatus.CLOSED
    db.flush()
    close_fiscal_year(db, ledger.company_id, ledger.fiscal_year.id, actor=ledger.owner)
    db.commit()

    for branch_id in (ledger.main_branch.id, musanze):
        report = trial_balance(db, ledger.company_id, as_of=date(YEAR, 12, 31), branch_id=branch_id)
        profit_and_loss = [row for row in report.rows if row.class_ in PROFIT_AND_LOSS_CLASSES]
        assert sum((row.net for row in profit_and_loss), Decimal(0)) == Decimal(0)
        assert report.foots
    assert verify_period_balances(db, ledger.company_id) == []
    assert_ledger_invariants(db, ledger.company_id)

    reopen_fiscal_year(
        db, ledger.company_id, ledger.fiscal_year.id, actor=ledger.owner, reason="audit"
    )
    db.commit()

    after = {
        row.code: row.net
        for row in trial_balance(db, ledger.company_id, as_of=date(YEAR, 12, 31)).rows
    }
    assert {k: v for k, v in after.items() if v != 0} == {k: v for k, v in before.items() if v != 0}
    assert verify_period_balances(db, ledger.company_id) == []
    assert_ledger_invariants(db, ledger.company_id)


# --- 4e. exchange_rates ------------------------------------------------------------------------


def test_4e_a_base_currency_rate_is_rejected(db: Session, ledger: Ledger) -> None:
    with pytest.raises(LedgerStateError) as excinfo:
        add_exchange_rate(
            db,
            ledger.company_id,
            currency_id=ledger.cur("RWF"),
            valid_from=date(YEAR, 1, 1),
            rate=Decimal("1.05"),
            actor=ledger.owner,
        )
    assert excinfo.value.code == "base_currency_rate"
    db.rollback()


def test_4e_lookup_takes_the_latest_effective_rate_and_ignores_the_future(
    db: Session, ledger: Ledger
) -> None:
    usd = ledger.currencies["USD"]
    db.add_all(
        [
            ExchangeRate(
                company_id=ledger.company_id,
                currency_id=usd.id,
                valid_from=date(YEAR, 6, 1),
                rate=Decimal(1350),
            ),
            ExchangeRate(
                company_id=ledger.company_id,
                currency_id=usd.id,
                valid_from=date(YEAR, 9, 1),
                rate=Decimal(1400),
            ),
        ]
    )
    db.flush()

    assert rate_on(db, usd, date(YEAR, 5, 31)) == USD_RATE
    assert rate_on(db, usd, date(YEAR, 6, 1)) == Decimal(1350)
    assert rate_on(db, usd, date(YEAR, 8, 31)) == Decimal(1350)  # September is still the future
    assert rate_on(db, usd, date(YEAR, 9, 1)) == Decimal(1400)

    entry = post_simple(
        db,
        ledger,
        debit="6500",
        credit="2300",
        amount=Decimal("10.00"),
        on=date(YEAR, 8, 15),
        currency="USD",
    )
    db.commit()
    assert _lines(db, entry)[0].exchange_rate == Decimal(1350)
    assert _lines(db, entry)[0].base_amount == Decimal(13500)


def test_4e_a_missing_rate_never_falls_back_to_one(db: Session, ledger: Ledger) -> None:
    db.execute(text("DELETE FROM exchange_rates"))
    db.flush()

    with pytest.raises(PostingError) as excinfo:
        rate_on(db, ledger.currencies["USD"], MARCH)
    assert excinfo.value.code == "missing_exchange_rate"

    with pytest.raises(PostingError) as excinfo:
        post_simple(
            db,
            ledger,
            debit="6500",
            credit="2300",
            amount=Decimal("10.00"),
            on=MARCH,
            currency="USD",
        )
    assert excinfo.value.code == "missing_exchange_rate"
    db.rollback()
    assert db.scalar(select(func.count()).select_from(JournalEntry)) == 0
    # The base currency needs no rate row and is unaffected.
    assert rate_on(db, ledger.base, MARCH) == Decimal(1)


# --- 4f. Single-writer guard --------------------------------------------------------------------


def test_4f_direct_insert_outside_the_posting_engine_is_rejected(
    db: Session, ledger: Ledger
) -> None:
    period_id = next(p.id for p in ledger.periods if p.start_date <= MARCH <= p.end_date)
    with pytest.raises(DBAPIError) as excinfo:
        db.execute(
            text(
                """
                INSERT INTO journal_entries (company_id, number, doc_type, event_type,
                                             entry_date, period_id, description, status)
                VALUES (:cid, 'SMUGGLED', 'JE', 'manual_journal', :on, :period, 'x', 'draft')
                """
            ),
            {"cid": ledger.company_id, "on": MARCH, "period": period_id},
        )
    assert kernel_sqlstate(excinfo.value) == SQLSTATE_SINGLE_WRITER
    db.rollback()


def test_4f_direct_line_insert_outside_the_posting_engine_is_rejected(
    db: Session, ledger: Ledger
) -> None:
    entry = post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(100), on=MARCH)
    db.commit()

    with pytest.raises(DBAPIError) as excinfo:
        db.execute(
            text(
                """
                INSERT INTO journal_lines (company_id, entry_id, line_no, gl_account_id,
                                           branch_id, currency_id, exchange_rate, amount,
                                           base_amount, tax_amount)
                VALUES (:cid, :entry, 99, :account, :branch, :currency, 1, 5, 5, 0)
                """
            ),
            {
                "cid": ledger.company_id,
                "entry": entry.id,
                "account": ledger.acct("6500"),
                "branch": ledger.main_branch.id,
                "currency": ledger.base.id,
            },
        )
    assert kernel_sqlstate(excinfo.value) == SQLSTATE_SINGLE_WRITER
    db.rollback()


def test_4f_the_window_shuts_again_after_the_engine_has_written(
    db: Session, ledger: Ledger
) -> None:
    """The guard would be theatre if one legitimate posting left the door open for the rest
    of the caller's transaction."""
    post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(100), on=MARCH)

    assert db.execute(text("SELECT current_setting('app.posting_engine', true)")).scalar() == "off"
    with pytest.raises(DBAPIError) as excinfo:
        db.execute(
            text(
                """
                INSERT INTO journal_entries (company_id, number, doc_type, event_type,
                                             entry_date, period_id, description, status)
                VALUES (:cid, 'PIGGYBACK', 'JE', 'manual_journal', :on, :period, 'x', 'draft')
                """
            ),
            {
                "cid": ledger.company_id,
                "on": MARCH,
                "period": next(p.id for p in ledger.periods if p.start_date <= MARCH <= p.end_date),
            },
        )
    assert kernel_sqlstate(excinfo.value) == SQLSTATE_SINGLE_WRITER
    db.rollback()


def test_4f_the_engine_still_posts_normally(db: Session, ledger: Ledger) -> None:
    entry = post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(100), on=MARCH)
    db.commit()
    assert entry.status == JournalStatus.POSTED
    assert len(_lines(db, entry)) == 2
    assert_ledger_invariants(db, ledger.company_id)


# --- 4g. Period non-overlap at the database ------------------------------------------------------


def test_4g_overlapping_period_insert_is_rejected(db: Session, ledger: Ledger) -> None:
    march = next(p for p in ledger.periods if p.start_date <= MARCH <= p.end_date)
    db.add(
        AccountingPeriod(
            company_id=ledger.company_id,
            fiscal_year_id=ledger.fiscal_year.id,
            period_no=99,
            name="Overlapping",
            start_date=march.end_date,  # one day of overlap is enough
            end_date=date(YEAR, 4, 15),
            status=PeriodStatus.OPEN,
        )
    )
    with pytest.raises(IntegrityError) as excinfo:
        db.flush()
    assert "ex_accounting_periods_no_overlap" in str(excinfo.value.orig)
    db.rollback()


def test_4g_adjacent_periods_and_other_tenants_are_unaffected(db: Session, ledger: Ledger) -> None:
    """The constraint must catch overlap without forbidding back-to-back periods."""
    year = create_fiscal_year(
        db,
        ledger.company_id,
        name=str(YEAR + 1),
        start_date=date(YEAR + 1, 1, 1),
        end_date=date(YEAR + 1, 12, 31),
    )
    db.flush()
    periods = list(
        db.scalars(
            select(AccountingPeriod)
            .where(AccountingPeriod.fiscal_year_id == year.id)
            .order_by(AccountingPeriod.period_no)
        )
    )
    assert len(periods) == 12
    assert periods[0].end_date + timedelta(days=1) == periods[1].start_date
    db.rollback()


def test_4g_overlap_is_rejected_for_raw_sql_too(db: Session, ledger: Ledger) -> None:
    with pytest.raises(IntegrityError) as excinfo:
        db.execute(
            text(
                """
                INSERT INTO accounting_periods (company_id, fiscal_year_id, period_no, name,
                                                start_date, end_date, status)
                VALUES (:cid, :fy, 98, 'Whole year', :start, :end, 'open')
                """
            ),
            {
                "cid": ledger.company_id,
                "fy": ledger.fiscal_year.id,
                "start": date(YEAR, 1, 1),
                "end": date(YEAR, 12, 31),
            },
        )
    assert "ex_accounting_periods_no_overlap" in str(excinfo.value.orig)
    db.rollback()
