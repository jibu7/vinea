"""The bank-account master, the registration hook, and the one-sided currency rule (decision 2).

The currency rule is enforced **twice**, in the engine and in a DB trigger, and the two
sensitivity tests below are the reason that is not redundancy: each one disables one half and
asserts the other still refuses. The engine exists so a screen gets a field error naming the
currency; the trigger exists so the rule holds for anything that reaches `journal_lines` by
another route — which is what a guarantee means, as against a convention.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.banking import accounts as accounts_service
from app.core.errors import ConflictError
from app.kernel import accounts as kernel_accounts
from app.kernel import posting
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.events import CashbookEntry, CashbookKind, CashbookLineSpec
from app.models.banking import BankAccount, BankAccountKind
from app.models.gl import AccountClass, ControlType, GLAccount
from app.models.journal import JournalLine
from tests.banking.conftest import Banking
from tests.kernel.conftest import USD_RATE, Ledger

SEPTEMBER = date(date.today().year, 9, 15)


# --- The hook ---------------------------------------------------------------------------------


def test_the_seed_pack_gives_every_flagged_account_its_row(db: Session, ledger: Ledger) -> None:
    """Clause 6, on a tenant nothing has touched: `1110` and `1120` are the only bank/cash
    control accounts `rw_sme_v1` seeds, and each has exactly one master row in the base
    currency with the GL code and name."""
    rows = {row.gl_account_id: row for row in db.scalars(select(BankAccount))}
    flagged = [
        account
        for account in db.scalars(select(GLAccount))
        if account.control_type in (ControlType.BANK, ControlType.CASH)
    ]

    assert sorted(account.code for account in flagged) == ["1110", "1120"]
    assert len(rows) == len(flagged)
    for account in flagged:
        row = rows[account.id]
        assert (row.code, row.name) == (account.code, account.name)
        assert row.currency_id == ledger.cur("RWF")
        assert row.kind.value == account.control_type.value


def test_creating_a_bank_control_account_creates_its_row(db: Session, ledger: Ledger) -> None:
    account = kernel_accounts.create_account(
        db,
        ledger.company_id,
        kernel_accounts.AccountInput(
            code="1121",
            name="Bank Account USD",
            class_=AccountClass.ASSET,
            parent_id=ledger.acct("1100"),
            control_type=ControlType.BANK,
        ),
        actor=ledger.owner,
    )
    row = accounts_service.ensure_row(db, account)
    db.commit()

    assert row is not None
    assert (row.code, row.kind) == ("1121", BankAccountKind.BANK)
    assert accounts_service.unregistered_control_accounts(db, ledger.company_id) == []


def test_the_hook_is_silent_on_an_ordinary_account(db: Session, ledger: Ledger) -> None:
    """`ensure_row` is handed *every* account the create path makes, so that the call site is
    one unconditional line. An expense account must simply produce nothing."""
    account = kernel_accounts.create_account(
        db,
        ledger.company_id,
        kernel_accounts.AccountInput(
            code="6710",
            name="Card Fees",
            class_=AccountClass.EXPENSE,
            parent_id=ledger.acct("6000"),
        ),
        actor=ledger.owner,
    )

    assert accounts_service.ensure_row(db, account) is None


def test_the_hook_is_idempotent(db: Session, banking: Banking) -> None:
    account = banking.ledger.accounts["1120"]
    before = accounts_service.ensure_row(db, account)
    after = accounts_service.ensure_row(db, account)

    assert before is not None and after is not None and before.id == after.id


def test_registering_a_plain_account_is_refused(db: Session, banking: Banking) -> None:
    with pytest.raises(LedgerStateError) as excinfo:
        accounts_service.register(
            db,
            banking.company_id,
            accounts_service.BankAccountInput(gl_account_id=banking.ledger.acct("6700")),
            actor=banking.owner,
        )

    assert excinfo.value.code == "not_a_cash_account"
    db.rollback()


def test_an_unregistered_flagged_account_is_listed(db: Session, ledger: Ledger) -> None:
    """The *Register* list on the Bank accounts screen. Empty on every path the hook and the
    back-fill cover — which is the outcome it exists to report — so the fixture here creates
    an account **without** the hook to prove the listing can see one."""
    db.add(
        GLAccount(
            company_id=ledger.company_id,
            code="1122",
            name="Unhooked bank account",
            class_=AccountClass.ASSET,
            parent_id=ledger.acct("1100"),
            is_postable=True,
            is_control=True,
            control_type=ControlType.BANK,
            is_active=True,
        )
    )
    db.flush()

    unregistered = accounts_service.unregistered_control_accounts(db, ledger.company_id)

    assert [account.code for account in unregistered] == ["1122"]


# --- The currency, and when it may move --------------------------------------------------------


def test_the_currency_may_change_while_the_account_has_no_lines(
    db: Session, banking: Banking
) -> None:
    row = banking.bank("BK-USD")

    assert row.currency_id == banking.ledger.cur("USD")


def test_the_currency_may_not_change_once_the_account_has_lines(
    db: Session, banking: Banking
) -> None:
    """Every `base_amount` on the account was frozen at posting (ADR-06). Re-denominating the
    account afterwards would restate what each line "shows on the statement" without touching
    a posted row, and the reconciliation would then close against figures the ledger never
    held."""
    _receipt(db, banking, account_code="1120", amount=Decimal(1000))
    db.commit()

    with pytest.raises(LedgerStateError) as excinfo:
        accounts_service.update(
            db,
            banking.bank("BK-RWF"),
            currency_id=banking.ledger.cur("USD"),
            actor=banking.owner,
        )

    assert excinfo.value.code == "bank_account_has_lines"
    assert excinfo.value.field_errors == {"currency_id": ["the account already has postings"]}
    db.rollback()


def test_a_cash_account_cannot_carry_a_statement_format(db: Session, banking: Banking) -> None:
    with pytest.raises(LedgerStateError) as excinfo:
        accounts_service.update(
            db, banking.bank("CASH"), statement_format={"preset": "generic"}, actor=banking.owner
        )

    assert excinfo.value.code == "cash_account_has_no_format"
    db.rollback()


# --- The one-sided rule ------------------------------------------------------------------------


def test_a_foreign_currency_account_refuses_a_base_currency_line(
    db: Session, banking: Banking
) -> None:
    """The engine half: `BK-USD` is held in USD, so an RWF cashbook receipt onto `1121` is
    refused with a field error a screen can show."""
    with pytest.raises(PostingError) as excinfo:
        _receipt(db, banking, account_code="1121", amount=Decimal(1000))

    assert excinfo.value.code == "bank_account_currency_mismatch"
    assert "USD" in str(excinfo.value.field_errors)
    db.rollback()


def test_a_base_currency_account_accepts_a_foreign_line(db: Session, banking: Banking) -> None:
    """The rule is **one-sided**, and this is the case it is one-sided for: a customer's USD
    invoice paid into a Rwandan RWF bank account. P4 values it at the keyed rate, and what the
    bank shows for it is the franc figure — which is exactly why the reconciled amount of a
    line on a base-currency account is its `base_amount`."""
    entry = _receipt(
        db, banking, account_code="1120", amount=Decimal("200.00"), currency="USD"
    )
    db.commit()

    bank_line = db.scalars(
        select(JournalLine).where(
            JournalLine.entry_id == entry.id,
            JournalLine.gl_account_id == banking.ledger.acct("1120"),
        )
    ).one()

    assert bank_line.amount == Decimal("200.00")
    assert bank_line.currency_id == banking.ledger.cur("USD")
    assert bank_line.base_amount != Decimal("200.00")
    assert accounts_service.reconciled_amount(
        bank_line, banking.bank("BK-RWF"), banking.ledger.cur("RWF")
    ) == bank_line.base_amount


def test_the_reconciled_amount_of_a_foreign_account_line_is_its_own_currency(
    db: Session, banking: Banking
) -> None:
    entry = _receipt(db, banking, account_code="1121", amount=Decimal("500.00"), currency="USD")
    db.commit()

    bank_line = db.scalars(
        select(JournalLine).where(
            JournalLine.entry_id == entry.id,
            JournalLine.gl_account_id == banking.ledger.acct("1121"),
        )
    ).one()

    assert accounts_service.reconciled_amount(
        bank_line, banking.bank("BK-USD"), banking.ledger.cur("RWF")
    ) == Decimal("500.00")


def test_the_sql_form_of_the_reconciled_amount_agrees_with_the_python_one(
    db: Session, banking: Banking
) -> None:
    """One rule, two renderings — and the reason both exist in one module. The workspace and
    the reports sum this in the database; the matcher and the invariant suite read it a line at
    a time. If the two ever disagreed the reconciliation would close under one and not the
    other, which is the defect a second definition produces.

    The sum is also the account's **book balance**, and on a base-currency account that is Σ
    `base_amount` — which is the trial balance for the account by construction.
    """
    _receipt(db, banking, account_code="1120", amount=Decimal(1000))
    _receipt(db, banking, account_code="1120", amount=Decimal("200.00"), currency="USD")
    _receipt(db, banking, account_code="1121", amount=Decimal("500.00"), currency="USD")
    db.commit()
    base = banking.ledger.cur("RWF")

    for code, gl_code in (("BK-RWF", "1120"), ("BK-USD", "1121")):
        row = banking.bank(code)
        lines = db.scalars(
            select(JournalLine).where(
                JournalLine.company_id == banking.company_id,
                JournalLine.gl_account_id == banking.ledger.acct(gl_code),
            )
        ).all()
        in_python = sum(
            (accounts_service.reconciled_amount(line, row, base) for line in lines), Decimal(0)
        )
        in_sql = db.scalar(
            select(func.coalesce(func.sum(accounts_service.reconciled_amount_column(row, base)), 0))
            .where(
                JournalLine.company_id == banking.company_id,
                JournalLine.gl_account_id == banking.ledger.acct(gl_code),
            )
        )
        assert in_python == in_sql, code

    # And the figures themselves, worked by hand. `1120` took an RWF 1 000 receipt and a
    # USD 200.00 one; the seeded rate is 1 300.5, so the second is 200.00 x 1 300.5 = 260 100
    # base, half-up to RWF's zero decimals, and the account's book balance is 261 100.
    usd_base = (Decimal("200.00") * USD_RATE).quantize(Decimal(1))
    assert usd_base == Decimal(260100)
    assert db.scalar(
        select(func.coalesce(func.sum(JournalLine.base_amount), 0)).where(
            JournalLine.company_id == banking.company_id,
            JournalLine.gl_account_id == banking.ledger.acct("1120"),
        )
    ) == Decimal(1000) + usd_base


# --- Sensitivity: each half of the rule, without the other -------------------------------------


def test_the_trigger_still_refuses_when_the_engine_check_is_disabled(
    db: Session, banking: Banking, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disable the engine's check and the database still refuses, with `VN012`.

    This is the half that matters most: the engine's check is application code, and a posting
    path added later that did not go through `_resolve_lines` would slip past it silently. The
    trigger is what makes the rule a property of the data rather than of the code that happens
    to write it.
    """
    monkeypatch.setattr(posting, "_check_bank_account_currency", lambda *args, **kwargs: None)

    with pytest.raises(PostingError) as excinfo:
        _receipt(db, banking, account_code="1121", amount=Decimal(1000))

    # The **database** refused: `VN012` came back from the trigger and the kernel's error
    # translator put it in the same envelope. Asserting the SQLSTATE on the cause rather than
    # the code alone is what makes this test fail if the engine's check were the one running.
    assert isinstance(excinfo.value.__cause__, DBAPIError)
    assert excinfo.value.__cause__.orig.sqlstate == "VN012"
    assert excinfo.value.code == "bank_account_currency_mismatch"
    db.rollback()


def test_the_engine_still_refuses_when_the_trigger_is_dropped(
    db: Session, banking: Banking, admin_engine
) -> None:
    """The other half. The trigger is dropped for the length of this test — from the admin
    connection, because the app role does not own the table — and the engine's refusal is what
    is left."""
    with admin_engine.begin() as conn:
        conn.execute(
            text("DROP TRIGGER trg_journal_lines_needs_bank_currency ON journal_lines")
        )
    try:
        with pytest.raises(PostingError) as excinfo:
            _receipt(db, banking, account_code="1121", amount=Decimal(1000))
        assert excinfo.value.code == "bank_account_currency_mismatch"
        db.rollback()
    finally:
        with admin_engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TRIGGER trg_journal_lines_needs_bank_currency "
                    "BEFORE INSERT ON journal_lines FOR EACH ROW "
                    "EXECUTE FUNCTION kernel_check_bank_account_currency()"
                )
            )


def _receipt(
    db: Session,
    banking: Banking,
    *,
    account_code: str,
    amount: Decimal,
    currency: str = "RWF",
):  # noqa: ANN202 - the JournalEntry the engine returns
    """A cashbook receipt onto a bank account, contra `3400 Opening Balance Suspense` — the P2
    way an account is opened, and the way the acceptance tape opens one."""
    entry = posting.post(
        db,
        CashbookEntry(
            entry_date=SEPTEMBER,
            description="opening",
            cash_account_id=banking.ledger.acct(account_code),
            kind=CashbookKind.RECEIPT,
            currency_id=banking.ledger.cur(currency),
            lines=(
                CashbookLineSpec(gl_account_id=banking.ledger.acct("3400"), amount=amount),
            ),
        ),
        company_id=banking.company_id,
        actor=banking.owner,
    )
    assert entry is not None
    return entry


def test_two_bank_accounts_cannot_share_a_code(db: Session, banking: Banking) -> None:
    with pytest.raises(ConflictError) as excinfo:
        accounts_service.update(db, banking.bank("CASH"), code="BK-RWF", actor=banking.owner)

    assert excinfo.value.code == "bank_account_code_taken"
    db.rollback()
