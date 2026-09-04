"""ADR-08: period transitions, posting-time enforcement, year-end close and audited reopen."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kernel import posting
from app.kernel.enquiries import trial_balance
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.periods import create_fiscal_year, find_period, transition_period
from app.kernel.year_end import close_fiscal_year, reopen_fiscal_year
from app.models.audit import AuditLog
from app.models.fiscal import AccountingPeriod, FiscalYear, PeriodStatus
from app.models.journal import JournalEntry, JournalLine
from tests.kernel.conftest import YEAR, Ledger, post_simple
from tests.kernel.invariants import assert_ledger_invariants

DEC = date(YEAR, 12, 31)


def _net(db: Session, ledger: Ledger, as_of: date) -> dict[str, Decimal]:
    return {
        row.code: row.net
        for row in trial_balance(db, ledger.company_id, as_of=as_of).rows
        if row.net != 0
    }


def _actions(db: Session) -> list[str]:
    return list(db.scalars(select(AuditLog.action).order_by(AuditLog.id)))


# --- transitions -----------------------------------------------------------------------------


def test_valid_transitions_are_audited(db: Session, ledger: Ledger) -> None:
    period = ledger.periods[5]
    period.status = PeriodStatus.FUTURE
    db.flush()

    for target in (
        PeriodStatus.OPEN,
        PeriodStatus.CLOSED,
        PeriodStatus.LOCKED,
        PeriodStatus.CLOSED,
        PeriodStatus.OPEN,
    ):
        transition_period(db, period, target, actor=ledger.owner, reason="test")
        assert period.status == target
    db.commit()

    actions = [a for a in _actions(db) if a.startswith("period.")]
    assert actions == [
        "period.opened",
        "period.closed",
        "period.locked",
        "period.unlocked",
        "period.reopened",
    ]


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (PeriodStatus.FUTURE, PeriodStatus.CLOSED),
        (PeriodStatus.FUTURE, PeriodStatus.LOCKED),
        (PeriodStatus.OPEN, PeriodStatus.LOCKED),
        (PeriodStatus.OPEN, PeriodStatus.FUTURE),
        (PeriodStatus.LOCKED, PeriodStatus.OPEN),
        (PeriodStatus.CLOSED, PeriodStatus.CLOSED),
    ],
)
def test_invalid_transitions_are_refused(
    db: Session, ledger: Ledger, start: PeriodStatus, target: PeriodStatus
) -> None:
    period = ledger.periods[0]
    period.status = start
    db.flush()
    with pytest.raises(LedgerStateError) as excinfo:
        transition_period(db, period, target, actor=ledger.owner)
    assert excinfo.value.code == "invalid_period_transition"
    db.rollback()


def test_posting_respects_every_status(db: Session, ledger: Ledger) -> None:
    march = ledger.periods[2]
    on = date(YEAR, 3, 3)
    for status in (PeriodStatus.FUTURE, PeriodStatus.CLOSED, PeriodStatus.LOCKED):
        march.status = status
        db.flush()
        with pytest.raises(PostingError) as excinfo:
            post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(1), on=on)
        assert excinfo.value.code == "period_not_open"
    march.status = PeriodStatus.OPEN
    db.flush()
    post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(1), on=on)
    db.commit()
    assert find_period(db, ledger.company_id, on).id == march.id


# --- fiscal years ------------------------------------------------------------------------------


def test_create_fiscal_year_builds_monthly_periods(db: Session, ledger: Ledger) -> None:
    year = create_fiscal_year(
        db,
        ledger.company_id,
        name=str(YEAR + 1),
        start_date=date(YEAR + 1, 1, 1),
        end_date=date(YEAR + 1, 12, 31),
    )
    periods = list(
        db.scalars(
            select(AccountingPeriod)
            .where(AccountingPeriod.fiscal_year_id == year.id)
            .order_by(AccountingPeriod.period_no)
        )
    )
    assert [p.period_no for p in periods] == list(range(1, 13))
    assert periods[0].start_date == date(YEAR + 1, 1, 1)
    assert periods[-1].end_date == date(YEAR + 1, 12, 31)
    assert {p.status for p in periods} == {PeriodStatus.FUTURE}

    # Management years need not follow calendar months exactly.
    odd = create_fiscal_year(
        db,
        ledger.company_id,
        name="FY-odd",
        start_date=date(YEAR + 2, 3, 15),
        end_date=date(YEAR + 2, 6, 10),
        open_through=date(YEAR + 2, 4, 1),
    )
    odd_periods = list(
        db.scalars(
            select(AccountingPeriod)
            .where(AccountingPeriod.fiscal_year_id == odd.id)
            .order_by(AccountingPeriod.period_no)
        )
    )
    spans = [(p.start_date, p.end_date, p.status) for p in odd_periods]
    assert spans == [
        (date(YEAR + 2, 3, 15), date(YEAR + 2, 3, 31), PeriodStatus.OPEN),
        (date(YEAR + 2, 4, 1), date(YEAR + 2, 4, 30), PeriodStatus.OPEN),
        (date(YEAR + 2, 5, 1), date(YEAR + 2, 5, 31), PeriodStatus.FUTURE),
        (date(YEAR + 2, 6, 1), date(YEAR + 2, 6, 10), PeriodStatus.FUTURE),
    ]
    db.rollback()


def test_fiscal_years_may_not_overlap(db: Session, ledger: Ledger) -> None:
    with pytest.raises(LedgerStateError) as excinfo:
        create_fiscal_year(
            db,
            ledger.company_id,
            name="dup",
            start_date=date(YEAR, 12, 1),
            end_date=date(YEAR + 1, 11, 30),
        )
    assert excinfo.value.code == "fiscal_year_overlap"
    db.rollback()


# --- year-end -----------------------------------------------------------------------------------


def _trade(db: Session, ledger: Ledger) -> None:
    """Income and expenses across two branches, in two currencies."""
    mus = ledger.branches["MUS"].id
    post_simple(db, ledger, debit="2300", credit="4100", amount=Decimal(10000), on=date(YEAR, 2, 5))
    post_simple(db, ledger, debit="6200", credit="2300", amount=Decimal(4000), on=date(YEAR, 5, 5))
    post_simple(
        db,
        ledger,
        debit="2300",
        credit="4200",
        amount=Decimal("100.00"),
        on=date(YEAR, 6, 5),
        currency="USD",
    )
    post_simple(
        db,
        ledger,
        debit="2300",
        credit="4100",
        amount=Decimal(3000),
        on=date(YEAR, 7, 5),
        branch_id=mus,
    )
    post_simple(
        db,
        ledger,
        debit="6100",
        credit="2300",
        amount=Decimal(500),
        on=date(YEAR, 8, 5),
        branch_id=mus,
    )
    db.commit()


def _close_all_but_last(db: Session, ledger: Ledger) -> None:
    for period in ledger.periods[:-1]:
        period.status = PeriodStatus.CLOSED
    db.flush()


def test_close_fiscal_year_moves_profit_to_retained_earnings_per_branch(
    db: Session, ledger: Ledger
) -> None:
    _trade(db, ledger)
    before = _net(db, ledger, DEC)
    assert before["4100"] == Decimal(-13000)
    _close_all_but_last(db, ledger)

    year = close_fiscal_year(db, ledger.company_id, ledger.fiscal_year.id, actor=ledger.owner)
    db.commit()

    assert year.status == PeriodStatus.LOCKED
    assert year.closing_entry_id is not None
    assert {p.status for p in ledger.periods} == {PeriodStatus.LOCKED}
    closing = db.get(JournalEntry, year.closing_entry_id)
    assert closing is not None
    assert (closing.doc_type, closing.event_type, closing.number) == (
        "YE",
        "period_close",
        "YE-000001",
    )
    assert closing.entry_date == DEC

    after = _net(db, ledger, DEC)
    # Every P&L account is zero; retained earnings holds the year's profit (income − expense).
    assert not {code for code in after if code.startswith(("4", "5", "6"))}
    profit = Decimal(10000) + Decimal(4000) * -1 + Decimal(130050) + Decimal(3000) - Decimal(500)
    assert after["3200"] == -profit
    # The day before the close, P&L still shows (the closing entry is dated year end).
    assert _net(db, ledger, date(YEAR, 12, 30))["4100"] == Decimal(-13000)

    # Retained earnings is closed per branch, so each branch's TB still foots on its own.
    mus = ledger.branches["MUS"].id
    lines = db.scalars(select(JournalLine).where(JournalLine.entry_id == closing.id)).all()
    re_lines = {
        line.branch_id: line.base_amount
        for line in lines
        if line.gl_account_id == ledger.acct("3200")
    }
    assert re_lines == {
        ledger.main_branch.id: Decimal(-(10000 - 4000 + 130050)),
        mus: Decimal(-(3000 - 500)),
    }
    assert all(line.currency_id == ledger.base.id for line in lines)
    assert trial_balance(db, ledger.company_id, as_of=DEC, branch_id=mus).foots
    assert_ledger_invariants(db, ledger.company_id)

    # Locked year: nothing else may post into it.
    with pytest.raises(PostingError) as excinfo:
        post_simple(
            db, ledger, debit="6500", credit="2300", amount=Decimal(1), on=date(YEAR, 12, 31)
        )
    assert excinfo.value.code == "period_not_open"
    db.rollback()
    with pytest.raises(LedgerStateError) as state:
        close_fiscal_year(db, ledger.company_id, ledger.fiscal_year.id, actor=ledger.owner)
    assert state.value.code == "year_locked"
    db.rollback()
    assert "fiscal_year.closed" in _actions(db)


def test_close_requires_earlier_periods_closed_and_a_retained_earnings_account(
    db: Session, ledger: Ledger
) -> None:
    _trade(db, ledger)
    with pytest.raises(LedgerStateError) as excinfo:
        close_fiscal_year(db, ledger.company_id, ledger.fiscal_year.id, actor=ledger.owner)
    assert excinfo.value.code == "periods_not_closed"
    db.rollback()

    _close_all_but_last(db, ledger)
    settings = posting.gl_settings_for(db, ledger.company_id)
    settings.retained_earnings_account_id = None
    db.flush()
    with pytest.raises(PostingError) as posting_error:
        close_fiscal_year(db, ledger.company_id, ledger.fiscal_year.id, actor=ledger.owner)
    assert posting_error.value.code == "retained_earnings_unset"
    db.rollback()


def test_close_accepts_a_closed_last_period_and_a_year_without_movement(
    db: Session, ledger: Ledger
) -> None:
    for period in ledger.periods:
        period.status = PeriodStatus.CLOSED
    db.flush()
    year = close_fiscal_year(db, ledger.company_id, ledger.fiscal_year.id, actor=ledger.owner)
    db.commit()
    assert year.status == PeriodStatus.LOCKED
    assert year.closing_entry_id is None  # nothing to close, nothing posted
    assert db.scalar(select(JournalEntry.id)) is None


def test_closing_entry_cannot_be_reversed_directly(db: Session, ledger: Ledger) -> None:
    _trade(db, ledger)
    _close_all_but_last(db, ledger)
    year = close_fiscal_year(db, ledger.company_id, ledger.fiscal_year.id, actor=ledger.owner)
    db.commit()
    # Reopen the last period on a locked year is refused, and so is reversing the close by hand.
    with pytest.raises(LedgerStateError) as excinfo:
        transition_period(db, ledger.periods[-1], PeriodStatus.CLOSED, actor=ledger.owner)
    assert excinfo.value.code == "fiscal_year_locked"
    db.rollback()
    # Into an open period of the next year, so the closing-entry rule (not the period rule) fires.
    create_fiscal_year(
        db,
        ledger.company_id,
        name=str(YEAR + 1),
        start_date=date(YEAR + 1, 1, 1),
        end_date=date(YEAR + 1, 12, 31),
        open_through=date(YEAR + 1, 1, 31),
    )
    db.commit()
    with pytest.raises(LedgerStateError) as excinfo:
        posting.reverse(
            db,
            year.closing_entry_id,  # type: ignore[arg-type]
            company_id=ledger.company_id,
            on_date=date(YEAR + 1, 1, 15),
            reason="x",
            actor=None,
        )
    assert excinfo.value.code == "use_fiscal_year_reopen"
    db.rollback()


def test_reopen_reverses_the_close_and_restores_the_trial_balance(
    db: Session, ledger: Ledger
) -> None:
    _trade(db, ledger)
    before = _net(db, ledger, DEC)
    _close_all_but_last(db, ledger)
    close_fiscal_year(db, ledger.company_id, ledger.fiscal_year.id, actor=ledger.owner)
    db.commit()

    year = reopen_fiscal_year(
        db,
        ledger.company_id,
        ledger.fiscal_year.id,
        actor=ledger.owner,
        reason="late supplier invoice",
    )
    db.commit()

    assert year.status == PeriodStatus.OPEN and year.closing_entry_id is None
    statuses = [p.status for p in ledger.periods]
    assert statuses[:-1] == [PeriodStatus.CLOSED] * 11 and statuses[-1] == PeriodStatus.OPEN
    assert _net(db, ledger, DEC) == before
    entries = db.scalars(
        select(JournalEntry).where(JournalEntry.doc_type == "YE").order_by(JournalEntry.id)
    ).all()
    assert [e.number for e in entries] == ["YE-000001", "YE-000002"]
    assert entries[1].reverses_entry_id == entries[0].id
    assert "fiscal_year.reopened" in _actions(db)
    assert_ledger_invariants(db, ledger.company_id)

    # Adjust, then close again: a fresh closing entry, and the year locks once more.
    post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(250), on=DEC)
    db.commit()
    year = close_fiscal_year(db, ledger.company_id, ledger.fiscal_year.id, actor=ledger.owner)
    db.commit()
    assert year.status == PeriodStatus.LOCKED
    assert db.get(JournalEntry, year.closing_entry_id).number == "YE-000003"  # type: ignore[union-attr]
    assert not {code for code in _net(db, ledger, DEC) if code.startswith(("4", "5", "6"))}
    assert_ledger_invariants(db, ledger.company_id)


def test_reopen_needs_a_locked_year(db: Session, ledger: Ledger) -> None:
    with pytest.raises(LedgerStateError) as excinfo:
        reopen_fiscal_year(
            db, ledger.company_id, ledger.fiscal_year.id, actor=ledger.owner, reason="x"
        )
    assert excinfo.value.code == "year_not_locked"
    assert db.get(FiscalYear, ledger.fiscal_year.id).status == PeriodStatus.OPEN  # type: ignore[union-attr]
