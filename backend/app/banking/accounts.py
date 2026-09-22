"""The bank-account master, and the hook the chart of accounts calls (P8 decision 2).

A `bank_accounts` row is a **master over a flagged GL account**, never a second balance for
it. The cashbook balance stays Σ `journal_lines` on the GL account, as it has since P2. What
the row adds is the three facts the GL cannot hold: which currency the account is *held* in
(`gl_accounts` carry no currency at all), how its statement exports are laid out, and what the
bank calls it.

**Every bank/cash control account has exactly one row**, and `ensure_row` is how. It is called
from `POST /gl/accounts` and from the seed pack, in the same transaction as the account — not
from `app/kernel/accounts.py`, because the kernel may not import this package (P5's rule about
`events.py` importing upward). A path that forgets is caught by `assert_bank_invariants`
clause 6 rather than by review, and the back-fill in `0027_p8_banking` gives every account
that predates the phase its row.

**The currency rule is one-sided** (decision 2), and this module is where the rule's *data*
lives; the enforcement is in two other places on purpose. `app/kernel/posting.py` refuses a
mismatched line so the screen gets a field error; the `VN012` trigger refuses it so the rule
is a guarantee rather than a convention. Each is proven sensitive without the other.

**The reconciled amount** is the one figure the statement side ever compares against, and
`reconciled_amount` is its only definition — `amount` on a foreign-currency account, because
that is what the bank's own statement is denominated in; `base_amount` on a base-currency one,
because a USD receipt into a Rwandan RWF account reaches the bank as francs at the keyed rate.
The matcher, the reconciliation figures, the Cashbooks report and the invariant suite all read
it from here. A second definition would be two answers to "what does this line show on the
statement", and the reconciliation would close under one and not the other.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, exists, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.banking.formats import validate_format
from app.core.errors import ConflictError, NotFoundError
from app.kernel.errors import LedgerStateError
from app.kernel.money import base_currency
from app.models.banking import BankAccount, BankAccountKind, BankRule
from app.models.gl import CASHBOOK_CONTROL_TYPES, ControlType, GLAccount
from app.models.journal import JournalLine
from app.models.user import User
from app.services.audit import record_audit

#: control type → the master's `kind`. One mapping, so "a cash account" means the same thing
#: on the GL side and on the banking side; clause 6 asserts the two never diverge.
KIND_FOR_CONTROL_TYPE = {
    ControlType.BANK: BankAccountKind.BANK,
    ControlType.CASH: BankAccountKind.CASH,
}


def ensure_row(db: Session, account: GLAccount) -> BankAccount | None:
    """Give a `bank` / `cash` control account its master row, in the base currency.

    Idempotent and silent on a non-cash account, so a caller may hand it every account it
    creates without asking what kind it is — which is what makes the hook a single line at
    each call site and therefore one that does not get forgotten in a branch.

    **Base currency, always.** A foreign-currency account is registered and *then* has its
    currency set, which is safe because `currency_id` may only change while the GL account has
    no journal line (`bank_account_has_lines`). Creating it in a currency nobody asked for
    would be the master inventing a fact; creating it in the base currency states the default
    every Rwandan bank account actually has.
    """
    kind = KIND_FOR_CONTROL_TYPE.get(account.control_type) if account.control_type else None
    if kind is None:
        return None
    existing = db.scalar(
        select(BankAccount).where(
            BankAccount.company_id == account.company_id,
            BankAccount.gl_account_id == account.id,
        )
    )
    if existing is not None:
        return existing
    row = BankAccount(
        company_id=account.company_id,
        gl_account_id=account.id,
        kind=kind,
        code=account.code,
        name=account.name,
        currency_id=base_currency(db, account.company_id).id,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def unregistered_control_accounts(db: Session, company_id: int) -> list[GLAccount]:
    """Bank/cash control accounts with no master row.

    Empty on any tenant the hook and the back-fill have both covered, which is every tenant —
    so this is the *Register* list on the Bank accounts screen, and a permanently empty one is
    the outcome it exists to report. It is a listing rather than a repair: a row appearing here
    means a path created a control account without calling `ensure_row`, and the fix is that
    path, not a button that papers over it.
    """
    registered = select(BankAccount.gl_account_id).where(BankAccount.company_id == company_id)
    return list(
        db.scalars(
            select(GLAccount)
            .where(
                GLAccount.company_id == company_id,
                GLAccount.control_type.in_(tuple(CASHBOOK_CONTROL_TYPES)),
                GLAccount.id.not_in(registered),
            )
            .order_by(GLAccount.code)
        )
    )


def get(db: Session, company_id: int, bank_account_id: int) -> BankAccount:
    row = db.get(BankAccount, bank_account_id)
    if row is None or row.company_id != company_id:
        raise NotFoundError("Bank account not found")
    return row


def list_accounts(
    db: Session, company_id: int, *, include_inactive: bool = False
) -> list[BankAccount]:
    statement = select(BankAccount).where(BankAccount.company_id == company_id)
    if not include_inactive:
        statement = statement.where(BankAccount.is_active)
    return list(db.scalars(statement.order_by(BankAccount.code)))


@dataclass(frozen=True, kw_only=True)
class BankAccountInput:
    gl_account_id: int
    code: str | None = None
    name: str | None = None
    currency_id: int | None = None
    bank_name: str | None = None
    account_number: str | None = None
    account_holder: str | None = None
    bank_branch: str | None = None
    swift_bic: str | None = None
    statement_format: dict[str, Any] | None = None


def register(
    db: Session,
    company_id: int,
    data: BankAccountInput,
    *,
    actor: User,
    request: Request | None = None,
) -> BankAccount:
    """Create the master over an existing flagged GL account, or fill in the one the hook
    already made.

    Registering an account the hook covered is not an error — it is how the Bank accounts
    screen creates a GL account and its master in one call, and how an account registered in
    the base currency gets its real currency and bank details.
    """
    account = db.get(GLAccount, data.gl_account_id)
    if account is None or account.company_id != company_id:
        raise NotFoundError("GL account not found")
    kind = KIND_FOR_CONTROL_TYPE.get(account.control_type) if account.control_type else None
    if kind is None:
        raise LedgerStateError(
            f"Account {account.code} is not a bank or cash control account",
            code="not_a_cash_account",
            field_errors={"gl_account_id": ["must be a bank/cash control account"]},
        )
    row = ensure_row(db, account)
    assert row is not None  # `kind` is not None, so `ensure_row` created or found the row
    # A field left out stays as it was. Registering is not always a first write — an account
    # the hook already made is registered again to give it its real currency and bank details
    # — and a register call that blanked whatever it did not mention would quietly erase an
    # account number the last one set.
    supplied = {
        name: value
        for name, value in (
            ("bank_name", data.bank_name),
            ("account_number", data.account_number),
            ("account_holder", data.account_holder),
            ("bank_branch", data.bank_branch),
            ("swift_bic", data.swift_bic),
            ("statement_format", data.statement_format),
        )
        if value is not None
    }
    return update(
        db,
        row,
        code=data.code,
        name=data.name,
        currency_id=data.currency_id,
        actor=actor,
        request=request,
        action="bank_account.registered",
        **supplied,
    )


def update(
    db: Session,
    row: BankAccount,
    *,
    code: str | None = None,
    name: str | None = None,
    currency_id: int | None = None,
    bank_name: str | None | object = ...,
    account_number: str | None | object = ...,
    account_holder: str | None | object = ...,
    bank_branch: str | None | object = ...,
    swift_bic: str | None | object = ...,
    statement_format: dict[str, Any] | None | object = ...,
    is_active: bool | None = None,
    actor: User,
    request: Request | None = None,
    action: str = "bank_account.updated",
) -> BankAccount:
    before = _snapshot(row)
    if code is not None and code != row.code:
        clash = db.scalar(
            select(BankAccount.id).where(
                BankAccount.company_id == row.company_id,
                BankAccount.code == code,
                BankAccount.id != row.id,
            )
        )
        if clash is not None:
            raise ConflictError(
                f"Bank account code {code} already exists",
                code="bank_account_code_taken",
                field_errors={"code": ["already in use"]},
            )
        row.code = code
    if name is not None:
        row.name = name
    if currency_id is not None and currency_id != row.currency_id:
        # The one field that cannot move once money has landed. Every base amount on the
        # account was frozen at posting under the old rule (ADR-06), so re-denominating the
        # account afterwards would restate what a line "shows on the statement" without
        # touching a single posted row — the reconciliation would then close against figures
        # the ledger never held.
        if _has_lines(db, row):
            raise LedgerStateError(
                "This account already carries journal lines; its currency cannot change",
                code="bank_account_has_lines",
                field_errors={"currency_id": ["the account already has postings"]},
            )
        row.currency_id = currency_id
    for attribute, value in (
        ("bank_name", bank_name),
        ("account_number", account_number),
        ("account_holder", account_holder),
        ("bank_branch", bank_branch),
        ("swift_bic", swift_bic),
    ):
        if value is not ...:
            setattr(row, attribute, value)
    if statement_format is not ...:
        if statement_format is not None:
            if row.kind == BankAccountKind.CASH:
                raise LedgerStateError(
                    "A cash account has no statement to import",
                    code="cash_account_has_no_format",
                    field_errors={"statement_format": ["not available on a cash account"]},
                )
            validate_format(statement_format)  # type: ignore[arg-type]
        row.statement_format = statement_format  # type: ignore[assignment]
    if is_active is not None:
        row.is_active = is_active
    db.flush()
    after = _snapshot(row)
    if after != before:
        record_audit(
            db,
            company_id=row.company_id,
            action=action,
            entity="bank_accounts",
            entity_id=row.id,
            before=before,
            after=after,
            actor_user_id=actor.id,
            actor_email=actor.email,
            request=request,
        )
    return row


def _snapshot(row: BankAccount) -> dict[str, Any]:
    return {
        "code": row.code,
        "name": row.name,
        "currency_id": row.currency_id,
        "bank_name": row.bank_name,
        "account_number": row.account_number,
        "account_holder": row.account_holder,
        "bank_branch": row.bank_branch,
        "swift_bic": row.swift_bic,
        "statement_format": row.statement_format,
        "is_active": row.is_active,
    }


def _has_lines(db: Session, row: BankAccount) -> bool:
    return bool(
        db.scalar(
            select(
                exists().where(
                    JournalLine.company_id == row.company_id,
                    JournalLine.gl_account_id == row.gl_account_id,
                )
            )
        )
    )


# --- The reconciled amount (decision 2) -------------------------------------------------------


def is_foreign(row: BankAccount, base_currency_id: int) -> bool:
    return row.currency_id != base_currency_id


def reconciled_amount(
    line: JournalLine, row: BankAccount, base_currency_id: int
) -> Decimal:
    """What this ledger line shows on the bank's statement, in the account's currency.

    The only definition. `amount` on a foreign-currency account — the account is *held* in
    that currency, the rule refuses any other on it, and the statement is denominated in it.
    `base_amount` on a base-currency account — because that account may legitimately carry a
    USD receipt (decision 2's rule is one-sided), and what the Rwandan bank shows for it is
    the franc figure P4 valued it at.

    Σ over an account's lines to a date is therefore its **book balance** at that date, in the
    account's currency, and it equals the trial balance for the account at that date by
    construction: on a base-currency account the sum is Σ `base_amount`, which is what the
    trial balance is; on a foreign-currency one every line is in the account's currency, so Σ
    `amount` is the same balance read in the only currency the account holds.
    """
    return line.amount if is_foreign(row, base_currency_id) else line.base_amount


def reconciled_amount_column(row: BankAccount, base_currency_id: int) -> ColumnElement[Decimal]:
    """`reconciled_amount` as a SQL expression, for the sums the workspace and the reports do
    in the database rather than in Python. Same rule, same function's docstring — it returns a
    *column*, not a second answer."""
    return JournalLine.amount if is_foreign(row, base_currency_id) else JournalLine.base_amount


# --- Rules (the master half) ------------------------------------------------------------------
#
# A `bank_rules` row is **setup**, maintained beside the account it belongs to and under
# `bank:setup_manage`, which is why its CRUD is here rather than in `matching.py`. Reading the
# rules — deciding which one a statement line prefills the drawer from — is matching's, and
# arrives with it. Splitting it this way keeps `matching.py` about matching and keeps the
# Bank accounts screen able to edit the rules without importing the matcher.


def list_rules(db: Session, company_id: int, bank_account_id: int) -> list[BankRule]:
    return list(
        db.scalars(
            select(BankRule)
            .where(
                BankRule.company_id == company_id,
                BankRule.bank_account_id == bank_account_id,
            )
            .order_by(BankRule.priority, BankRule.id)
        )
    )


def get_rule(db: Session, company_id: int, rule_id: int) -> BankRule:
    row = db.get(BankRule, rule_id)
    if row is None or row.company_id != company_id:
        raise NotFoundError("Bank rule not found")
    return row


def create_rule(
    db: Session,
    company_id: int,
    *,
    bank_account_id: int,
    pattern: str,
    gl_account_id: int | None = None,
    tax_code_id: int | None = None,
    partner_type: str | None = None,
    partner_id: int | None = None,
    description: str | None = None,
    priority: int = 100,
    actor: User,
    request: Request | None = None,
) -> BankRule:
    get(db, company_id, bank_account_id)
    row = BankRule(
        company_id=company_id,
        bank_account_id=bank_account_id,
        pattern=pattern,
        gl_account_id=gl_account_id,
        tax_code_id=tax_code_id,
        partner_type=partner_type,
        partner_id=partner_id,
        description=description,
        priority=priority,
        is_active=True,
    )
    db.add(row)
    db.flush()
    record_audit(
        db,
        company_id=company_id,
        action="bank_rule.created",
        entity="bank_rules",
        entity_id=row.id,
        after={"pattern": pattern, "gl_account_id": gl_account_id, "priority": priority},
        actor_user_id=actor.id,
        actor_email=actor.email,
        request=request,
    )
    return row


def update_rule(
    db: Session,
    row: BankRule,
    *,
    pattern: str | None = None,
    gl_account_id: int | None | object = ...,
    tax_code_id: int | None | object = ...,
    partner_type: str | None | object = ...,
    partner_id: int | None | object = ...,
    description: str | None | object = ...,
    priority: int | None = None,
    is_active: bool | None = None,
    actor: User,
    request: Request | None = None,
) -> BankRule:
    before = {"pattern": row.pattern, "gl_account_id": row.gl_account_id, "active": row.is_active}
    if pattern is not None:
        row.pattern = pattern
    for attribute, value in (
        ("gl_account_id", gl_account_id),
        ("tax_code_id", tax_code_id),
        ("partner_type", partner_type),
        ("partner_id", partner_id),
        ("description", description),
    ):
        if value is not ...:
            setattr(row, attribute, value)
    if priority is not None:
        row.priority = priority
    if is_active is not None:
        row.is_active = is_active
    db.flush()
    after = {"pattern": row.pattern, "gl_account_id": row.gl_account_id, "active": row.is_active}
    if after != before:
        record_audit(
            db,
            company_id=row.company_id,
            action="bank_rule.updated",
            entity="bank_rules",
            entity_id=row.id,
            before=before,
            after=after,
            actor_user_id=actor.id,
            actor_email=actor.email,
            request=request,
        )
    return row
