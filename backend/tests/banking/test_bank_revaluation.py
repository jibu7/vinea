"""Bank and cash revaluation as a scope of the P7 run (P8 decision 8), with the acceptance
tape's own literals.

The figures here are the tape's row 12, worked by hand and written as constants; nothing in this
file asks the code under test what the answer should be.

    BK-USD  balance   495.00   carried 653 400   at 1 350 → 668 250   gain  14 850
    SIN-4   open USD  100.00   carried 132 000   at 1 350 → 135 000   loss   3 000

    FXR-1 (30 Sep)   Dr 1130  14 850  /  Cr 4410  14 850
                     Dr 6955   3 000  /  Cr 2190   3 000
    FXR-2 ( 1 Oct)   the mirror, sign for sign

**The claim the whole decision turns on is the last one**: `1121` is unchanged at 653 400. A
revaluation never writes to the bank account itself, because a base-only line there — zero
`amount`, non-zero `base_amount` — would be a ledger line the statement can never show and the
reconciliation would carry as outstanding forever. The balance sheet reads `1121 + 1130` exactly
as it reads `1200 + 1290`, and `test_the_balance_sheet_reads_the_pair` asserts that identity
against `495 × 1 350`.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kernel.errors import LedgerStateError, PostingError
from app.models.currency import ExchangeRate
from app.models.fiscalization import FxRevaluationLine, FxRevaluationRole
from app.models.gl import GLAccount
from app.models.journal import JournalLine
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind
from app.subledger import documents as documents_service
from app.subledger import revaluation as revaluation_service
from tests.banking.conftest import Banking, ap_supplier, cashbook
from tests.banking.invariants import assert_bank_invariants
from tests.kernel.conftest import YEAR
from tests.kernel.invariants import assert_ledger_invariants
from tests.subledger.invariants import assert_subledger_invariants

SEP_1 = date(YEAR, 9, 1)
SEP_15 = date(YEAR, 9, 15)
SEP_30 = date(YEAR, 9, 30)
OCT_1 = date(YEAR, 10, 1)

BOOKING_RATE = Decimal(1320)
MONTH_END_RATE = Decimal(1350)

# BK-USD: a 500.00 receipt and a 5.00 fee, both at 1 320.
USD_BALANCE = Decimal("495.00")
USD_CARRYING = Decimal(653400)
USD_REVALUED = Decimal(668250)
USD_GAIN = Decimal(14850)

# SIN-4: a USD 100.00 supplier invoice at 1 320.
AP_OPEN = Decimal("100.00")
AP_CARRYING = Decimal(132000)
AP_REVALUED = Decimal(135000)
AP_LOSS = Decimal(-3000)


def _rates(db: Session, banking: Banking) -> None:
    """1 320 from 1 September, 1 350 from 30 September — the tape's two rates."""
    for on, rate in ((SEP_1, BOOKING_RATE), (SEP_30, MONTH_END_RATE)):
        db.add(
            ExchangeRate(
                company_id=banking.company_id,
                currency_id=banking.ledger.cur("USD"),
                valid_from=on,
                rate=rate,
            )
        )
    db.flush()


def _a_usd_bank_balance(db: Session, banking: Banking) -> None:
    """BK-USD at 495.00, carried at 653 400: a 500.00 receipt less a 5.00 fee, both at 1 320.

    Two entries rather than one of 495.00, because `booking_rate` on a bank line is
    `carrying_base / open_amount` over a balance built from several lines — the thing a document
    line never has — and a single entry would make that arithmetic indistinguishable from a
    document's stored rate.
    """
    cashbook(db, banking, account_code="1121", amount=Decimal("500.00"), on=SEP_15, currency="USD")
    cashbook(db, banking, account_code="1121", amount=Decimal("-5.00"), on=SEP_15, currency="USD")
    db.flush()


def _a_usd_supplier_invoice(db: Session, banking: Banking):  # noqa: ANN201
    supplier = ap_supplier(db, banking, name="Kigali Imports", code="SIN4")
    document, _ = documents_service.post_document(
        db,
        banking.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=supplier.id,
            document_date=SEP_1,
            description="imported stock",
            currency_id=banking.ledger.cur("USD"),
            lines=(
                documents_service.LineInput(
                    unit_price=AP_OPEN, gl_account_id=banking.ledger.acct("6990")
                ),
            ),
        ),
        actor=banking.owner,
    )
    db.flush()
    return document


@pytest.fixture
def revaluable(db: Session, banking: Banking) -> Banking:
    """The tape's row-12 state: a USD bank balance and an open USD payable."""
    _rates(db, banking)
    _a_usd_bank_balance(db, banking)
    _a_usd_supplier_invoice(db, banking)
    db.commit()
    return banking


def _entry_map(db: Session, banking: Banking, entry_id: int) -> dict[str, Decimal]:
    rows = db.execute(
        select(GLAccount.code, JournalLine.base_amount)
        .join(
            JournalLine,
            (JournalLine.gl_account_id == GLAccount.id)
            & (JournalLine.company_id == GLAccount.company_id),
        )
        .where(JournalLine.entry_id == entry_id)
    ).all()
    totals: dict[str, Decimal] = {}
    for code, amount in rows:
        totals[code] = totals.get(code, Decimal(0)) + amount
    return totals


def _balance(db: Session, banking: Banking, code: str, *, as_of: date) -> Decimal:
    """Base-currency balance of an account from its lines — never a stored column."""
    from app.models.journal import JournalEntry

    return sum(
        db.scalars(
            select(JournalLine.base_amount)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(
                JournalLine.company_id == banking.company_id,
                JournalLine.gl_account_id == banking.ledger.acct(code),
                JournalEntry.entry_date <= as_of,
            )
        ).all(),
        Decimal(0),
    )


# --- The preview -------------------------------------------------------------------------------


def test_the_bank_line_states_the_arithmetic_of_a_balance(
    db: Session, revaluable: Banking
) -> None:
    """A bank line revalues a **balance**, which is the whole difference from a document line.

    `open_amount` is Σ `amount` in the account's own currency; `carrying_base` is Σ `base_amount`
    over the same lines; `booking_rate` is their ratio, informational because a balance has no
    single rate it was booked at.
    """
    view = revaluation_service.preview(
        db, revaluable.company_id, revaluation_date=SEP_30, role=FxRevaluationRole.BANK
    )

    assert len(view.lines) == 1, "one line per foreign-currency bank account"
    line = view.lines[0]
    assert line.scope == "bank"
    assert line.bank_account_code == "BK-USD"
    assert line.document_id is None and line.partner_id is None, "a balance has no partner"
    assert line.open_amount == USD_BALANCE
    assert line.carrying_base == USD_CARRYING
    assert line.rate_at_date == MONTH_END_RATE
    assert line.revalued_base == USD_REVALUED
    assert line.difference == USD_GAIN
    # 653 400 / 495 = 1 320 exactly here, because both lines were booked at that rate.
    assert line.booking_rate == BOOKING_RATE
    assert view.total_difference == USD_GAIN


def test_a_base_currency_account_has_no_exposure(
    db: Session, revaluable: Banking
) -> None:
    """`BK-RWF` is held in the base currency, so it is not revalued **however many
    foreign-currency lines it carries** — its balance is already denominated in base.

    Asserted with a USD receipt sitting on it, which is decision 2's one-sided rule: legal on a
    base-currency account, and reconciled on its base amount.
    """
    cashbook(
        db, revaluable, account_code="1120", amount=Decimal("100.00"), on=SEP_15, currency="USD"
    )
    db.flush()

    view = revaluation_service.preview(
        db, revaluable.company_id, revaluation_date=SEP_30, role=FxRevaluationRole.BANK
    )

    assert [line.bank_account_code for line in view.lines] == ["BK-USD"]


def test_a_zero_balance_account_is_not_revalued(db: Session, revaluable: Banking) -> None:
    """Nothing to revalue, and `carrying_base / open_amount` would be a division by zero.

    Built by taking the balance back out rather than by finding an untouched account, so the
    account has lines and a zero balance — which is the case a "has it any lines" test would
    miss.
    """
    cashbook(
        db, revaluable, account_code="1121", amount=Decimal("-495.00"), on=SEP_15, currency="USD"
    )
    db.flush()

    view = revaluation_service.preview(
        db, revaluable.company_id, revaluation_date=SEP_30, role=FxRevaluationRole.BANK
    )

    assert view.lines == ()


def test_the_all_role_covers_the_subledgers_and_the_bank_together(
    db: Session, revaluable: Banking
) -> None:
    """The month-end press: one run, three scopes, and the tape's two lines side by side."""
    view = revaluation_service.preview(
        db, revaluable.company_id, revaluation_date=SEP_30, role=FxRevaluationRole.ALL
    )

    by_scope = {line.scope: line for line in view.lines}
    assert set(by_scope) == {"ap", "bank"}, "no open foreign receivable in this tenant"

    bank = by_scope["bank"]
    assert bank.open_amount == USD_BALANCE
    assert bank.difference == USD_GAIN

    payable = by_scope["ap"]
    # An AP invoice is signed negative — the sense the control account holds it in — so a rate
    # that rose makes the exposure *more* negative, which is a loss.
    assert payable.open_amount == -AP_OPEN
    assert payable.carrying_base == -AP_CARRYING
    assert payable.revalued_base == -AP_REVALUED
    assert payable.difference == AP_LOSS

    assert view.total_difference == USD_GAIN + AP_LOSS


# --- The posting map ---------------------------------------------------------------------------


def test_the_run_posts_the_gain_to_1130_and_the_loss_to_2190(
    db: Session, revaluable: Banking
) -> None:
    """Tape row 12's `FXR-1`, line for line.

    Four lines, not two: the bank's gain and the payable's loss reach different P&L accounts and
    different contras, because a bank group and an AP group are separate groups. Netting them
    would put one figure on the balance sheet where the accountant expects two.
    """
    run = revaluation_service.post_revaluation(
        db,
        revaluable.company_id,
        revaluation_date=SEP_30,
        role=FxRevaluationRole.ALL,
        actor=revaluable.owner,
    )
    db.flush()

    posted = _entry_map(db, revaluable, run.journal_entry_id)
    assert posted["1130"] == USD_GAIN, "Dr 1130 14 850"
    assert posted["4410"] == -USD_GAIN, "Cr 4410 14 850"
    assert posted["2190"] == AP_LOSS, "Cr 2190 3 000"
    assert posted["6955"] == -AP_LOSS, "Dr 6955 3 000"
    assert len(posted) == 4, "four accounts, no netting"

    assert_ledger_invariants(db, revaluable.company_id)
    assert_subledger_invariants(db, revaluable.company_id)
    assert_bank_invariants(db, revaluable.company_id)


def test_the_revaluation_never_writes_to_the_bank_account(
    db: Session, revaluable: Banking
) -> None:
    """**The claim decision 8 exists to make.** `1121` is untouched at 653 400.

    A base-only line on a bank account — zero `amount`, non-zero `base_amount` — would be a
    ledger line the statement can never show, and the reconciliation would carry it as
    outstanding forever. So the other side goes to `1130`, and this asserts it from the ledger's
    side rather than from the posting map's.
    """
    before = _balance(db, revaluable, "1121", as_of=SEP_30)
    assert before == USD_CARRYING

    revaluation_service.post_revaluation(
        db,
        revaluable.company_id,
        revaluation_date=SEP_30,
        role=FxRevaluationRole.BANK,
        actor=revaluable.owner,
    )
    db.flush()

    assert _balance(db, revaluable, "1121", as_of=SEP_30) == USD_CARRYING
    assert _balance(db, revaluable, "1121", as_of=OCT_1) == USD_CARRYING

    # And no line on the account carries a base amount without a currency amount.
    lines = db.scalars(
        select(JournalLine).where(
            JournalLine.company_id == revaluable.company_id,
            JournalLine.gl_account_id == revaluable.ledger.acct("1121"),
        )
    ).all()
    assert lines, "the account has lines, so this is not vacuous"
    assert all(line.amount != Decimal(0) for line in lines), (
        "a zero-amount line on a bank account is the thing 1130 exists to prevent"
    )


def test_the_balance_sheet_reads_the_pair(db: Session, revaluable: Banking) -> None:
    """`1121 + 1130 == 495 × 1 350` at the revaluation date — the tape's identity.

    This is what makes `1130` the right answer rather than a dodge: the account and its
    revaluation read together are the balance at the day's rate, exactly as `1200 + 1290` is the
    receivable at the day's rate.
    """
    revaluation_service.post_revaluation(
        db,
        revaluable.company_id,
        revaluation_date=SEP_30,
        role=FxRevaluationRole.BANK,
        actor=revaluable.owner,
    )
    db.flush()

    pair = _balance(db, revaluable, "1121", as_of=SEP_30) + _balance(
        db, revaluable, "1130", as_of=SEP_30
    )
    assert pair == USD_REVALUED
    assert pair == USD_BALANCE * MONTH_END_RATE


def test_the_mirror_takes_the_adjustment_back_out_the_next_day(
    db: Session, revaluable: Banking
) -> None:
    """P7's mirror, unchanged, over a bank line. `1130` is back to zero on 1 October, so the next
    month end revalues from booking rates rather than from last month's adjusted figure."""
    run = revaluation_service.post_revaluation(
        db,
        revaluable.company_id,
        revaluation_date=SEP_30,
        role=FxRevaluationRole.BANK,
        actor=revaluable.owner,
    )
    db.flush()

    assert run.mirror_entry_id is not None
    assert _balance(db, revaluable, "1130", as_of=SEP_30) == USD_GAIN
    assert _balance(db, revaluable, "1130", as_of=OCT_1) == Decimal(0)
    assert _balance(db, revaluable, "4410", as_of=OCT_1) == Decimal(0)


def test_two_bank_accounts_in_one_currency_post_two_groups(
    db: Session, revaluable: Banking
) -> None:
    """A bank group is per **account**, not per currency, because `1130`'s other side is per
    account — and a second USD account gaining while this one loses must show as two figures.

    Built by draining `BK-USD` to a loss and giving the second account a gain, so the entry has
    a debit and a credit to `1130` rather than one netted line. A per-currency grouping would
    post a single `1130` line for the difference and the two accounts could never be told apart
    on the balance sheet.
    """
    from app.banking import accounts as accounts_service
    from app.kernel import accounts as kernel_accounts
    from app.models.gl import AccountClass, ControlType

    second_gl = kernel_accounts.create_account(
        db,
        revaluable.company_id,
        kernel_accounts.AccountInput(
            code="1122",
            name="Bank Account USD Two",
            class_=AccountClass.ASSET,
            parent_id=revaluable.ledger.acct("1100"),
            control_type=ControlType.BANK,
        ),
        actor=revaluable.owner,
    )
    row = accounts_service.ensure_row(db, second_gl)
    db.flush()
    assert row is not None
    accounts_service.update(
        db, row, code="BK-USD-2", currency_id=revaluable.ledger.cur("USD"), actor=revaluable.owner
    )
    revaluable.ledger.accounts["1122"] = second_gl
    db.flush()
    cashbook(
        db, revaluable, account_code="1122", amount=Decimal("200.00"), on=SEP_15, currency="USD"
    )
    db.flush()

    view = revaluation_service.preview(
        db, revaluable.company_id, revaluation_date=SEP_30, role=FxRevaluationRole.BANK
    )
    groups = view.by_group()
    assert len(groups) == 2, "one group per account, not one per currency"
    assert {key[0] for key in groups} == {"bank"}
    assert {key[2] for key in groups} == {
        revaluable.bank("BK-USD").id,
        row.id,
    }, "the group key carries the account"

    run = revaluation_service.post_revaluation(
        db,
        revaluable.company_id,
        revaluation_date=SEP_30,
        role=FxRevaluationRole.BANK,
        actor=revaluable.owner,
    )
    db.flush()

    # 200.00 at 1 320 carried is 264 000; at 1 350 it is 270 000 — a 6 000 gain beside
    # BK-USD's 14 850, and both reach 1130 and 4410 as their own lines.
    lines = db.scalars(
        select(JournalLine).where(
            JournalLine.company_id == revaluable.company_id,
            JournalLine.entry_id == run.journal_entry_id,
            JournalLine.gl_account_id == revaluable.ledger.acct("1130"),
        )
    ).all()
    assert len(lines) == 2, "two 1130 lines, one per account, never netted"
    assert sorted(line.base_amount for line in lines) == [Decimal(6000), USD_GAIN]
    assert_ledger_invariants(db, revaluable.company_id)
    assert_bank_invariants(db, revaluable.company_id)


# --- The scope overlap -------------------------------------------------------------------------


def test_a_second_bank_run_at_the_same_date_is_refused(
    db: Session, revaluable: Banking
) -> None:
    """`fx_revaluation_exists`, the scope test. `bank` after `all` intersects."""
    revaluation_service.post_revaluation(
        db,
        revaluable.company_id,
        revaluation_date=SEP_30,
        role=FxRevaluationRole.ALL,
        actor=revaluable.owner,
    )
    db.flush()

    with pytest.raises(LedgerStateError) as error:
        revaluation_service.post_revaluation(
            db,
            revaluable.company_id,
            revaluation_date=SEP_30,
            role=FxRevaluationRole.BANK,
            actor=revaluable.owner,
        )
    assert error.value.code == "fx_revaluation_exists"


def test_a_bank_run_after_a_subledger_run_at_the_same_date_is_allowed(
    db: Session, revaluable: Banking
) -> None:
    """**The reason scopes replaced role equality.** `bank` after `ap` does *not* intersect, so
    an accountant who revalued the subledgers on the 30th can still revalue the bank.

    P7's equality test would have refused this, and the refusal would have been wrong: nothing
    was revalued twice. The pair below is the discriminating case for the whole decision.
    """
    revaluation_service.post_revaluation(
        db,
        revaluable.company_id,
        revaluation_date=SEP_30,
        role=FxRevaluationRole.AP,
        actor=revaluable.owner,
    )
    db.flush()

    bank_run = revaluation_service.post_revaluation(
        db,
        revaluable.company_id,
        revaluation_date=SEP_30,
        role=FxRevaluationRole.BANK,
        actor=revaluable.owner,
    )
    db.flush()
    assert bank_run.journal_entry_id is not None

    # And now `all` is refused, because it intersects both.
    with pytest.raises(LedgerStateError) as error:
        revaluation_service.post_revaluation(
            db,
            revaluable.company_id,
            revaluation_date=SEP_30,
            role=FxRevaluationRole.ALL,
            actor=revaluable.owner,
        )
    assert error.value.code == "fx_revaluation_exists"
    assert_ledger_invariants(db, revaluable.company_id)
    assert_subledger_invariants(db, revaluable.company_id)


def test_a_bank_run_is_still_a_period_end_run(db: Session, revaluable: Banking) -> None:
    """P7's own refusals are unchanged by the new scope — the date must be a period end."""
    with pytest.raises(PostingError) as error:
        revaluation_service.post_revaluation(
            db,
            revaluable.company_id,
            revaluation_date=SEP_30 - timedelta(days=1),
            role=FxRevaluationRole.BANK,
            actor=revaluable.owner,
        )
    assert error.value.code == "fx_revaluation_not_period_end"


# --- Reversal and what is stored ---------------------------------------------------------------


def test_the_stored_line_names_the_bank_account_and_no_document(
    db: Session, revaluable: Banking
) -> None:
    """The table's CHECK says exactly one of the two is set; this says which one a bank line
    fills, so a future path that filled neither is caught by the database and a path that filled
    the wrong one is caught here."""
    run = revaluation_service.post_revaluation(
        db,
        revaluable.company_id,
        revaluation_date=SEP_30,
        role=FxRevaluationRole.BANK,
        actor=revaluable.owner,
    )
    db.flush()

    stored = db.scalars(
        select(FxRevaluationLine).where(FxRevaluationLine.revaluation_id == run.id)
    ).all()
    assert len(stored) == 1
    line = stored[0]
    assert line.bank_account_id == revaluable.bank("BK-USD").id
    assert line.document_id is None
    assert line.open_amount == USD_BALANCE
    assert line.carrying_base == USD_CARRYING
    assert line.revalued_base == USD_REVALUED
    assert line.difference == USD_GAIN
    assert line.rate_at_date == MONTH_END_RATE


def test_reversing_a_bank_run_takes_both_entries_back_out(
    db: Session, revaluable: Banking
) -> None:
    """P7's reversal, unchanged, over a bank run: the entry and its mirror both come back, and
    `1130` and `4410` are flat afterwards."""
    run = revaluation_service.post_revaluation(
        db,
        revaluable.company_id,
        revaluation_date=SEP_30,
        role=FxRevaluationRole.BANK,
        actor=revaluable.owner,
    )
    db.flush()

    revaluation_service.reverse_revaluation(
        db,
        revaluable.company_id,
        run.id,
        reason="the month-end rate was restated",
        actor=revaluable.owner,
    )
    db.flush()

    for code in ("1130", "4410"):
        assert _balance(db, revaluable, code, as_of=OCT_1 + timedelta(days=40)) == Decimal(0), (
            f"{code} is flat once the run is reversed"
        )
    assert _balance(db, revaluable, "1121", as_of=SEP_30) == USD_CARRYING
    assert_ledger_invariants(db, revaluable.company_id)
    assert_bank_invariants(db, revaluable.company_id)

    # And the date is free again, which is what makes a reversal useful rather than only tidy.
    again = revaluation_service.post_revaluation(
        db,
        revaluable.company_id,
        revaluation_date=SEP_30,
        role=FxRevaluationRole.BANK,
        actor=revaluable.owner,
    )
    db.flush()
    assert again.journal_entry_id is not None
