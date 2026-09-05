"""Posting Engine behaviour (ADR-04/05): validations in order, multi-currency rounding,
cashbook derivation, reversal, idempotency, account determination, period_balances."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.kernel import masters, posting
from app.kernel.balances import verify_period_balances
from app.kernel.enquiries import account_transactions, trial_balance
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.events import (
    CashbookEntry,
    CashbookKind,
    CashbookLineSpec,
    InvoicePosted,
    LineSpec,
    ManualJournal,
    PeriodClosed,
    ReversalRequested,
)
from app.kernel.posting import (
    RETAINED_EARNINGS_KEY,
    _check_required_dimensions,
    _transaction_type_default,
    resolve_account,
)
from app.models.currency import ExchangeRate
from app.models.fiscal import PeriodStatus
from app.models.gl import ControlType, GLAccount
from app.models.journal import JournalEntry, JournalLine, JournalStatus, PeriodBalance
from tests.kernel.conftest import USD_RATE, YEAR, Ledger, post_simple
from tests.kernel.invariants import assert_ledger_invariants

MARCH = date(YEAR, 3, 15)
APRIL = date(YEAR, 4, 2)


def _tb_by_code(db: Session, ledger: Ledger, as_of: date) -> dict[str, Decimal]:
    return {row.code: row.net for row in trial_balance(db, ledger.company_id, as_of=as_of).rows}


def _lines(db: Session, entry: JournalEntry) -> list[JournalLine]:
    return list(
        db.scalars(
            select(JournalLine)
            .where(JournalLine.entry_id == entry.id)
            .order_by(JournalLine.line_no)
        )
    )


# --- happy paths ---------------------------------------------------------------------------


def test_manual_journal_posts_multi_currency_lines_with_frozen_base_amounts(
    db: Session, ledger: Ledger
) -> None:
    usd, rwf = ledger.cur("USD"), ledger.cur("RWF")
    event = ManualJournal(
        entry_date=MARCH,
        description="USD purchase settled from RWF bank",
        lines=(
            LineSpec(amount=Decimal("100.00"), gl_account_id=ledger.acct("6500"), currency_id=usd),
            LineSpec(amount=Decimal("-100.00"), gl_account_id=ledger.acct("2300"), currency_id=usd),
            LineSpec(amount=Decimal("5000"), gl_account_id=ledger.acct("6700"), currency_id=rwf),
            LineSpec(amount=Decimal("-5000"), gl_account_id=ledger.acct("2500"), currency_id=rwf),
        ),
    )
    entry = posting.post(db, event, company_id=ledger.company_id, actor=ledger.owner)
    db.commit()

    assert entry is not None
    assert entry.number == "JE-000001"
    assert entry.status == JournalStatus.POSTED
    assert entry.posted_by == ledger.owner.id
    assert entry.event_type == "manual_journal"
    lines = _lines(db, entry)
    assert [line.line_no for line in lines] == [1, 2, 3, 4]
    assert lines[0].exchange_rate == USD_RATE
    assert lines[0].base_amount == Decimal("130050")  # 100 × 1300.5, RWF has no decimals
    assert lines[1].base_amount == Decimal("-130050")
    assert lines[2].exchange_rate == 1 and lines[2].base_amount == Decimal("5000")
    assert all(line.branch_id == ledger.main_branch.id for line in lines)
    assert_ledger_invariants(db, ledger.company_id)


def test_branch_and_project_dimensions_are_recorded(db: Session, ledger: Ledger) -> None:
    musanze = ledger.branches["MUS"].id
    alpha = ledger.projects["P-ALPHA"].id
    event = ManualJournal(
        entry_date=MARCH,
        description="dimensioned",
        branch_id=musanze,
        lines=(
            LineSpec(amount=Decimal(700), gl_account_id=ledger.acct("6200"), project_id=alpha),
            LineSpec(
                amount=Decimal(-700),
                gl_account_id=ledger.acct("2300"),
                branch_id=ledger.main_branch.id,
            ),
        ),
    )
    entry = posting.post(db, event, company_id=ledger.company_id, actor=ledger.owner)
    db.commit()

    lines = _lines(db, entry)  # type: ignore[arg-type]
    assert (lines[0].branch_id, lines[0].project_id) == (musanze, alpha)  # event default
    assert (lines[1].branch_id, lines[1].project_id) == (ledger.main_branch.id, None)  # override
    by_project = trial_balance(db, ledger.company_id, as_of=MARCH, project_id=alpha)
    assert {row.code: row.net for row in by_project.rows} == {"6200": Decimal(700)}
    by_branch = trial_balance(db, ledger.company_id, as_of=MARCH, branch_id=musanze)
    assert {row.code: row.net for row in by_branch.rows} == {"6200": Decimal(700)}


def test_period_balances_are_maintained_and_verifiable(db: Session, ledger: Ledger) -> None:
    post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(100), on=MARCH)
    post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(250), on=MARCH)
    post_simple(db, ledger, debit="2300", credit="6500", amount=Decimal(30), on=APRIL)
    db.commit()

    cells = {
        (row.period_id, row.gl_account_id): row
        for row in db.scalars(
            select(PeriodBalance).where(PeriodBalance.company_id == ledger.company_id)
        )
    }
    march = next(p.id for p in ledger.periods if p.start_date <= MARCH <= p.end_date)
    april = next(p.id for p in ledger.periods if p.start_date <= APRIL <= p.end_date)
    assert (
        cells[(march, ledger.acct("6500"))].debit_base,
        cells[(march, ledger.acct("6500"))].credit_base,
    ) == (
        Decimal(350),
        Decimal(0),
    )
    assert cells[(april, ledger.acct("6500"))].credit_base == Decimal(30)
    assert verify_period_balances(db, ledger.company_id) == []

    # The cache is a cache: corrupt it directly and the verifier must say so.
    db.execute(
        text("UPDATE period_balances SET debit_base = debit_base + 1 WHERE gl_account_id = :id"),
        {"id": ledger.acct("6500")},
    )
    drift = verify_period_balances(db, ledger.company_id)
    assert {(d.gl_account_id, d.field) for d in drift} == {(ledger.acct("6500"), "debit_base")}
    db.rollback()


# --- validation order & rules -------------------------------------------------------------


def test_closed_period_is_checked_before_anything_else(db: Session, ledger: Ledger) -> None:
    march = next(p for p in ledger.periods if p.start_date <= MARCH <= p.end_date)
    march.status = PeriodStatus.CLOSED
    db.flush()
    # A header account *and* an unbalanced entry — the period error must win.
    event = ManualJournal(
        entry_date=MARCH,
        description="bad on every axis",
        lines=(
            LineSpec(amount=Decimal(1), gl_account_id=ledger.acct("6000")),
            LineSpec(amount=Decimal(-2), gl_account_id=ledger.acct("2300")),
        ),
    )
    with pytest.raises(PostingError) as excinfo:
        posting.post(db, event, company_id=ledger.company_id, actor=ledger.owner)
    assert excinfo.value.code == "period_not_open"
    db.rollback()


def test_future_period_and_missing_period_are_rejected(db: Session, ledger: Ledger) -> None:
    ledger.periods[-1].status = PeriodStatus.FUTURE
    db.flush()
    with pytest.raises(PostingError) as excinfo:
        post_simple(
            db, ledger, debit="6500", credit="2300", amount=Decimal(1), on=date(YEAR, 12, 5)
        )
    assert excinfo.value.code == "period_not_open"
    with pytest.raises(PostingError) as excinfo:
        post_simple(
            db, ledger, debit="6500", credit="2300", amount=Decimal(1), on=date(YEAR + 1, 1, 5)
        )
    assert excinfo.value.code == "no_accounting_period"
    db.rollback()


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("6000", "account_not_postable"),  # header
        ("1120", "control_account_manual_posting"),  # bank — cashbook only
        ("1200", "control_account_manual_posting"),  # AR — subledger only
    ],
)
def test_manual_journal_account_rules(
    db: Session, ledger: Ledger, code: str, expected: str
) -> None:
    with pytest.raises(PostingError) as excinfo:
        post_simple(db, ledger, debit=code, credit="2300", amount=Decimal(10), on=MARCH)
    assert excinfo.value.code == expected
    db.rollback()


def test_inactive_account_is_rejected(db: Session, ledger: Ledger) -> None:
    ledger.accounts["6500"].is_active = False
    db.flush()
    with pytest.raises(PostingError) as excinfo:
        post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(10), on=MARCH)
    assert excinfo.value.code == "account_inactive"
    db.rollback()


def test_control_accounts_demand_their_subledger_dimension() -> None:
    ar = GLAccount(code="1200", control_type=ControlType.AR, is_control=True)
    with pytest.raises(PostingError) as excinfo:
        _check_required_dimensions(ar, LineSpec(amount=Decimal(1)))
    assert excinfo.value.code == "dimension_required"
    with pytest.raises(PostingError):
        _check_required_dimensions(
            ar, LineSpec(amount=Decimal(1), partner_type="supplier", partner_id=7)
        )
    _check_required_dimensions(
        ar, LineSpec(amount=Decimal(1), partner_type="customer", partner_id=7)
    )

    stock = GLAccount(code="1300", control_type=ControlType.INVENTORY, is_control=True)
    with pytest.raises(PostingError):
        _check_required_dimensions(stock, LineSpec(amount=Decimal(1)))
    _check_required_dimensions(stock, LineSpec(amount=Decimal(1), item_id=3))

    plain = GLAccount(code="6500", control_type=None, is_control=False)
    with pytest.raises(PostingError) as excinfo:
        _check_required_dimensions(plain, LineSpec(amount=Decimal(1), tax_amount=Decimal(1)))
    assert excinfo.value.code == "dimension_invalid"


@pytest.mark.parametrize(
    ("amount", "expected"),
    [(Decimal("10.5"), "amount_precision"), (Decimal(0), "zero_amount_line")],
)
def test_line_amount_rules(db: Session, ledger: Ledger, amount: Decimal, expected: str) -> None:
    with pytest.raises(PostingError) as excinfo:
        post_simple(db, ledger, debit="6500", credit="2300", amount=amount, on=MARCH)
    assert excinfo.value.code == expected
    db.rollback()


def test_missing_exchange_rate_is_rejected(db: Session, ledger: Ledger) -> None:
    db.execute(text("DELETE FROM exchange_rates"))
    with pytest.raises(PostingError) as excinfo:
        post_simple(
            db,
            ledger,
            debit="6500",
            credit="2300",
            amount=Decimal("1.00"),
            on=MARCH,
            currency="USD",
        )
    assert excinfo.value.code == "missing_exchange_rate"
    db.rollback()


def test_tax_code_must_be_effective_on_the_entry_date(db: Session, ledger: Ledger) -> None:
    vat_in = ledger.tax_codes["VAT-IN-18"]
    vat_in.valid_to = date(YEAR, 2, 28)
    db.flush()
    event = ManualJournal(
        entry_date=MARCH,
        description="expired tax code",
        lines=(
            LineSpec(
                amount=Decimal(100),
                gl_account_id=ledger.acct("6500"),
                tax_code_id=vat_in.id,
                tax_amount=Decimal(18),
            ),
            LineSpec(amount=Decimal(-100), gl_account_id=ledger.acct("2300")),
        ),
    )
    with pytest.raises(PostingError) as excinfo:
        posting.post(db, event, company_id=ledger.company_id, actor=ledger.owner)
    assert excinfo.value.code == "tax_code_not_effective"
    db.rollback()


def test_stub_events_are_not_postable_yet(db: Session, ledger: Ledger) -> None:
    with pytest.raises(PostingError) as excinfo:
        posting.post(
            db,
            InvoicePosted(entry_date=MARCH, description="P4"),
            company_id=ledger.company_id,
            actor=ledger.owner,
        )
    assert excinfo.value.code == "unsupported_event"


def test_account_determination_chain(db: Session, ledger: Ledger) -> None:
    event = ManualJournal(entry_date=MARCH, description="x", lines=())
    assert (
        resolve_account(db, ledger.company_id, LineSpec(amount=Decimal(1), gl_account_id=42), event)
        == 42
    )
    with pytest.raises(PostingError) as excinfo:
        resolve_account(db, ledger.company_id, LineSpec(amount=Decimal(1)), event)
    assert excinfo.value.code == "account_undetermined"

    # 4th link: a transaction type's default account, scoped to the event's module.
    masters.create_transaction_type(
        db,
        ledger.company_id,
        module="gl",
        code="BANK-CHARGES",
        name="Bank charges",
        default_gl_account_id=ledger.acct("6700"),
        actor=ledger.owner,
    )
    masters.create_transaction_type(
        db,
        ledger.company_id,
        module="ar",
        code="BANK-CHARGES",
        name="Same code, other module",
        default_gl_account_id=ledger.acct("6900"),
        actor=ledger.owner,
    )
    db.flush()
    by_type = LineSpec(amount=Decimal(1), transaction_type="BANK-CHARGES")
    assert resolve_account(db, ledger.company_id, by_type, event) == ledger.acct("6700")
    # An explicit account still wins over the transaction type (link 1 beats link 4).
    override = LineSpec(amount=Decimal(1), gl_account_id=42, transaction_type="BANK-CHARGES")
    assert resolve_account(db, ledger.company_id, override, event) == 42

    # 5th link: module defaults — only the year-end close uses one in P2.
    close = PeriodClosed(fiscal_year_id=ledger.fiscal_year.id, entry_date=MARCH)
    spec = LineSpec(amount=Decimal(1), transaction_type=RETAINED_EARNINGS_KEY)
    assert resolve_account(db, ledger.company_id, spec, close) == ledger.acct("3200")
    # …and the sentinel is not resolvable as a user transaction type.
    assert _transaction_type_default(db, ledger.company_id, spec, event) is None
    with pytest.raises(LedgerStateError) as state:
        masters.create_transaction_type(
            db,
            ledger.company_id,
            module="gl",
            code=RETAINED_EARNINGS_KEY,
            name="reserved",
            actor=ledger.owner,
        )
    assert state.value.code == "reserved_transaction_type_code"
    db.rollback()


def test_posting_through_a_transaction_type(db: Session, ledger: Ledger) -> None:
    masters.create_transaction_type(
        db,
        ledger.company_id,
        module="gl",
        code="BANK-CHARGES",
        name="Bank charges",
        default_gl_account_id=ledger.acct("6700"),
        actor=ledger.owner,
    )
    db.flush()
    entry = posting.post(
        db,
        ManualJournal(
            entry_date=MARCH,
            description="monthly bank charges",
            lines=(
                LineSpec(amount=Decimal(2500), transaction_type="BANK-CHARGES"),
                LineSpec(amount=Decimal(-2500), gl_account_id=ledger.acct("2300")),
            ),
        ),
        company_id=ledger.company_id,
        actor=ledger.owner,
    )
    db.commit()

    assert entry is not None
    assert _lines(db, entry)[0].gl_account_id == ledger.acct("6700")
    assert_ledger_invariants(db, ledger.company_id)


def test_transaction_type_default_must_be_postable(db: Session, ledger: Ledger) -> None:
    with pytest.raises(LedgerStateError) as excinfo:
        masters.create_transaction_type(
            db,
            ledger.company_id,
            module="gl",
            code="HEADER",
            name="points at a header account",
            default_gl_account_id=ledger.acct("6000"),
            actor=ledger.owner,
        )
    assert excinfo.value.code == "account_not_postable"
    db.rollback()


def test_inactive_transaction_type_stops_resolving(db: Session, ledger: Ledger) -> None:
    transaction_type = masters.create_transaction_type(
        db,
        ledger.company_id,
        module="gl",
        code="RETIRED",
        name="Retired type",
        default_gl_account_id=ledger.acct("6700"),
        actor=ledger.owner,
    )
    masters.update_transaction_type(db, transaction_type, is_active=False, actor=ledger.owner)
    db.flush()
    with pytest.raises(PostingError) as excinfo:
        posting.post(
            db,
            ManualJournal(
                entry_date=MARCH,
                description="x",
                lines=(
                    LineSpec(amount=Decimal(10), transaction_type="RETIRED"),
                    LineSpec(amount=Decimal(-10), gl_account_id=ledger.acct("2300")),
                ),
            ),
            company_id=ledger.company_id,
            actor=ledger.owner,
        )
    assert excinfo.value.code == "account_undetermined"
    db.rollback()


# --- cashbook -------------------------------------------------------------------------------


def _cashbook(
    ledger: Ledger, kind: CashbookKind, *lines: CashbookLineSpec, **kwargs
) -> CashbookEntry:
    return CashbookEntry(
        entry_date=MARCH,
        description="cashbook",
        cash_account_id=kwargs.pop("cash_account_id", ledger.acct("1120")),
        kind=kind,
        lines=lines,
        **kwargs,
    )


def test_cashbook_payment_splits_tax_and_derives_the_bank_line(db: Session, ledger: Ledger) -> None:
    vat_in = ledger.tax_codes["VAT-IN-18"]
    event = _cashbook(
        ledger,
        CashbookKind.PAYMENT,
        CashbookLineSpec(
            gl_account_id=ledger.acct("6500"), amount=Decimal(59), tax_code_id=vat_in.id
        ),
        CashbookLineSpec(gl_account_id=ledger.acct("6700"), amount=Decimal(41)),
        reference="CHQ 1001",
    )
    entry = posting.post(db, event, company_id=ledger.company_id, actor=ledger.owner)
    db.commit()

    assert entry is not None and entry.number == "CB-000001" and entry.doc_type == "CB"
    lines = {(line.gl_account_id, line.line_no): line for line in _lines(db, entry)}
    expense = lines[(ledger.acct("6500"), 1)]
    vat = lines[(ledger.acct("1400"), 2)]
    other = lines[(ledger.acct("6700"), 3)]
    bank = lines[(ledger.acct("1120"), 4)]
    assert (expense.amount, expense.tax_code_id, expense.tax_amount) == (
        Decimal(50),
        vat_in.id,
        Decimal(9),
    )
    assert (vat.amount, vat.tax_code_id, vat.tax_amount) == (Decimal(9), vat_in.id, Decimal(0))
    assert other.amount == Decimal(41)
    assert (bank.amount, bank.description) == (Decimal(-100), "CHQ 1001")
    # VAT-return reconciliation: Σ tax_amount by code == movement on the tax account.
    assert expense.tax_amount == vat.amount
    assert_ledger_invariants(db, ledger.company_id)


def test_cashbook_receipt_and_exclusive_tax(db: Session, ledger: Ledger) -> None:
    vat_out = ledger.tax_codes["VAT-OUT-18"]
    event = _cashbook(
        ledger,
        CashbookKind.RECEIPT,
        CashbookLineSpec(
            gl_account_id=ledger.acct("4200"),
            amount=Decimal(100),
            tax_code_id=vat_out.id,
            tax_inclusive=False,
        ),
        cash_account_id=ledger.acct("1110"),
    )
    entry = posting.post(db, event, company_id=ledger.company_id, actor=ledger.owner)
    db.commit()
    lines = _lines(db, entry)  # type: ignore[arg-type]
    assert [(line.gl_account_id, line.amount) for line in lines] == [
        (ledger.acct("4200"), Decimal(-100)),
        (ledger.acct("2200"), Decimal(-18)),
        (ledger.acct("1110"), Decimal(118)),
    ]
    assert lines[0].tax_amount == Decimal(-18)


def test_cashbook_in_foreign_currency(db: Session, ledger: Ledger) -> None:
    event = _cashbook(
        ledger,
        CashbookKind.PAYMENT,
        CashbookLineSpec(gl_account_id=ledger.acct("6900"), amount=Decimal("250.00")),
        currency_id=ledger.cur("USD"),
    )
    entry = posting.post(db, event, company_id=ledger.company_id, actor=ledger.owner)
    db.commit()
    lines = _lines(db, entry)  # type: ignore[arg-type]
    assert [line.base_amount for line in lines] == [Decimal("325125"), Decimal("-325125")]
    assert_ledger_invariants(db, ledger.company_id)


def test_cashbook_side_rules(db: Session, ledger: Ledger) -> None:
    with pytest.raises(PostingError) as excinfo:
        posting.post(
            db,
            _cashbook(
                ledger,
                CashbookKind.PAYMENT,
                CashbookLineSpec(gl_account_id=ledger.acct("6500"), amount=Decimal(10)),
                cash_account_id=ledger.acct("2300"),  # not a bank/cash account
            ),
            company_id=ledger.company_id,
            actor=ledger.owner,
        )
    assert excinfo.value.code == "not_a_cash_account"
    with pytest.raises(PostingError) as excinfo:
        posting.post(
            db,
            _cashbook(
                ledger,
                CashbookKind.RECEIPT,
                CashbookLineSpec(gl_account_id=ledger.acct("1200"), amount=Decimal(10)),  # AR
            ),
            company_id=ledger.company_id,
            actor=ledger.owner,
        )
    assert excinfo.value.code == "control_account_manual_posting"
    # Bank → cash transfer is the cashbook's own business.
    transfer = posting.post(
        db,
        _cashbook(
            ledger,
            CashbookKind.PAYMENT,
            CashbookLineSpec(gl_account_id=ledger.acct("1110"), amount=Decimal(5000)),
        ),
        company_id=ledger.company_id,
        actor=ledger.owner,
    )
    assert transfer is not None
    db.commit()


# --- reversal ---------------------------------------------------------------------------------


def test_reversal_restores_the_trial_balance_and_is_linked(db: Session, ledger: Ledger) -> None:
    before = _tb_by_code(db, ledger, APRIL)
    original = post_simple(
        db, ledger, debit="6500", credit="2300", amount=Decimal("120.00"), on=MARCH, currency="USD"
    )
    db.commit()
    moved = _tb_by_code(db, ledger, APRIL)
    assert moved["6500"] == Decimal("156060")

    reversal = posting.reverse(
        db,
        original.id,
        company_id=ledger.company_id,
        on_date=APRIL,
        reason="posted to the wrong month",
        actor=ledger.owner,
    )
    db.commit()

    assert reversal.reverses_entry_id == original.id
    assert reversal.reversal_reason == "posted to the wrong month"
    assert reversal.event_type == "reversal"
    assert reversal.doc_type == original.doc_type == "JE"
    assert reversal.number == "JE-000002"
    assert "Reversal of JE-000001" in reversal.description
    original_lines, reversal_lines = _lines(db, original), _lines(db, reversal)
    for o, r in zip(original_lines, reversal_lines, strict=True):
        assert (r.amount, r.base_amount, r.exchange_rate) == (
            -o.amount,
            -o.base_amount,
            o.exchange_rate,
        )
        assert (r.gl_account_id, r.branch_id, r.currency_id) == (
            o.gl_account_id,
            o.branch_id,
            o.currency_id,
        )
        assert r.source_line_id == o.id
    after = _tb_by_code(db, ledger, APRIL)
    assert {k: v for k, v in after.items() if v != 0} == {k: v for k, v in before.items() if v != 0}
    # …but as of March the original still stands (the reversal is dated April).
    assert _tb_by_code(db, ledger, MARCH)["6500"] == Decimal("156060")
    assert_ledger_invariants(db, ledger.company_id)


def test_reversal_rules(db: Session, ledger: Ledger) -> None:
    original = post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(10), on=APRIL)
    db.commit()
    with pytest.raises(PostingError) as excinfo:
        posting.reverse(
            db, original.id, company_id=ledger.company_id, on_date=MARCH, reason="x", actor=None
        )
    assert excinfo.value.code == "reversal_before_original"
    db.rollback()

    posting.reverse(
        db, original.id, company_id=ledger.company_id, on_date=APRIL, reason="x", actor=None
    )
    db.commit()
    with pytest.raises(LedgerStateError) as state:
        posting.reverse(
            db, original.id, company_id=ledger.company_id, on_date=APRIL, reason="y", actor=None
        )
    assert state.value.code == "entry_already_reversed"
    db.rollback()

    with pytest.raises(PostingError) as excinfo:
        posting.reverse(
            db, 999_999, company_id=ledger.company_id, on_date=APRIL, reason="x", actor=None
        )
    assert excinfo.value.code == "entry_not_found"
    db.rollback()


def test_reversal_of_a_cashbook_entry_may_touch_the_bank_control(
    db: Session, ledger: Ledger
) -> None:
    entry = posting.post(
        db,
        _cashbook(
            ledger,
            CashbookKind.PAYMENT,
            CashbookLineSpec(gl_account_id=ledger.acct("6500"), amount=Decimal(10)),
        ),
        company_id=ledger.company_id,
        actor=ledger.owner,
    )
    db.commit()
    reversal = posting.reverse(
        db, entry.id, company_id=ledger.company_id, on_date=MARCH, reason="bounced", actor=None
    )
    db.commit()
    assert reversal.doc_type == "CB" and reversal.number == "CB-000002"
    assert_ledger_invariants(db, ledger.company_id)


def test_engine_level_idempotency(db: Session, ledger: Ledger) -> None:
    first = post_simple(
        db, ledger, debit="6500", credit="2300", amount=Decimal(10), on=MARCH, idempotency_key="k1"
    )
    db.commit()
    again = post_simple(
        db, ledger, debit="6500", credit="2300", amount=Decimal(10), on=MARCH, idempotency_key="k1"
    )
    assert again.id == first.id
    assert db.scalar(select(func.count()).select_from(JournalEntry)) == 1


def test_reversal_specs_are_exact_mirrors_even_when_rates_moved(
    db: Session, ledger: Ledger
) -> None:
    original = post_simple(
        db, ledger, debit="6500", credit="2300", amount=Decimal("10.00"), on=MARCH, currency="USD"
    )
    db.add(
        ExchangeRate(
            company_id=ledger.company_id,
            currency_id=ledger.cur("USD"),
            valid_from=APRIL,
            rate=Decimal(2000),
        )
    )
    db.commit()
    reversal = posting.reverse(
        db, original.id, company_id=ledger.company_id, on_date=APRIL, reason="fx", actor=None
    )
    db.commit()
    assert [line.base_amount for line in _lines(db, reversal)] == [Decimal(-13005), Decimal(13005)]
    assert _tb_by_code(db, ledger, APRIL).get("6500", Decimal(0)) == Decimal(0)


def test_account_transactions_running_balance_and_paging(db: Session, ledger: Ledger) -> None:
    feb = date(YEAR, 2, 10)
    post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(100), on=feb)
    for amount in (Decimal(10), Decimal(20), Decimal(30)):
        post_simple(db, ledger, debit="6500", credit="2300", amount=amount, on=MARCH)
    post_simple(db, ledger, debit="2300", credit="6500", amount=Decimal(5), on=APRIL)
    db.commit()

    page = account_transactions(
        db,
        ledger.company_id,
        ledger.acct("6500"),
        date_from=date(YEAR, 3, 1),
        date_to=date(YEAR, 4, 30),
        limit=2,
    )
    assert page.opening_base == Decimal(100)
    assert [item.running_base for item in page.items] == [Decimal(110), Decimal(130)]
    assert page.next_cursor == page.items[-1].line_id
    rest = account_transactions(
        db,
        ledger.company_id,
        ledger.acct("6500"),
        date_from=date(YEAR, 3, 1),
        date_to=date(YEAR, 4, 30),
        cursor=page.next_cursor,
        limit=10,
    )
    assert [item.running_base for item in rest.items] == [Decimal(160), Decimal(155)]
    assert rest.next_cursor is None


def test_reversal_event_can_be_replayed_via_dataclass_replace(db: Session, ledger: Ledger) -> None:
    """Events are frozen dataclasses; callers derive variants with `replace`, never mutate."""
    base = ReversalRequested(entry_id=1, reason="r", entry_date=MARCH)
    assert replace(base, reason="other").reason == "other"
    with pytest.raises(AttributeError):
        base.reason = "mutated"  # type: ignore[misc]
