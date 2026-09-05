"""Chart of accounts maintenance (hierarchical, control accounts; Appendix C "Rename
Accounts" = changing `code` with history intact, recorded in `audit_log`)."""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import exists, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import ConflictError, NotFoundError
from app.kernel.errors import LedgerStateError
from app.models.currency import Currency, ExchangeRate
from app.models.gl import AccountClass, ControlType, GLAccount, GLSettings
from app.models.journal import JournalLine
from app.models.user import User
from app.services.audit import record_audit


def get_account(db: Session, company_id: int, account_id: int) -> GLAccount:
    account = db.get(GLAccount, account_id)
    if account is None or account.company_id != company_id:
        raise NotFoundError("GL account not found")
    return account


def list_accounts(
    db: Session, company_id: int, *, include_inactive: bool = False
) -> list[GLAccount]:
    statement = select(GLAccount).where(GLAccount.company_id == company_id)
    if not include_inactive:
        statement = statement.where(GLAccount.is_active)
    return list(db.scalars(statement.order_by(GLAccount.code)))


def _has_postings(db: Session, account_id: int) -> bool:
    return bool(db.scalar(select(exists().where(JournalLine.gl_account_id == account_id))))


def _has_children(db: Session, account_id: int) -> bool:
    return bool(db.scalar(select(exists().where(GLAccount.parent_id == account_id))))


def _validate_parent(
    db: Session, company_id: int, parent_id: int, class_: AccountClass
) -> GLAccount:
    parent = get_account(db, company_id, parent_id)
    if parent.is_postable:
        raise LedgerStateError(
            f"Parent {parent.code} is postable; only header accounts can have children",
            code="parent_must_be_header",
        )
    if parent.class_ != class_:
        raise LedgerStateError(
            f"Parent {parent.code} is {parent.class_.value}; a child must share its class",
            code="parent_class_mismatch",
        )
    return parent


@dataclass(frozen=True, kw_only=True)
class AccountInput:
    code: str
    name: str
    class_: AccountClass
    parent_id: int | None = None
    is_postable: bool = True
    control_type: ControlType | None = None


def create_account(
    db: Session,
    company_id: int,
    data: AccountInput,
    *,
    actor: User | None,
    request: Request | None = None,
) -> GLAccount:
    if data.parent_id is not None:
        _validate_parent(db, company_id, data.parent_id, data.class_)
    if data.control_type is not None and not data.is_postable:
        raise LedgerStateError("Control accounts must be postable", code="control_must_be_postable")
    duplicate = db.scalar(
        select(GLAccount.id).where(GLAccount.company_id == company_id, GLAccount.code == data.code)
    )
    if duplicate is not None:
        raise ConflictError(
            f"Account code {data.code} already exists",
            code="account_code_taken",
            field_errors={"code": ["already in use"]},
        )
    account = GLAccount(
        company_id=company_id,
        code=data.code,
        name=data.name,
        class_=data.class_,
        parent_id=data.parent_id,
        is_postable=data.is_postable,
        is_control=data.control_type is not None,
        control_type=data.control_type,
        is_active=True,
    )
    db.add(account)
    db.flush()
    record_audit(
        db,
        company_id=company_id,
        action="gl_account.created",
        entity="gl_accounts",
        entity_id=account.id,
        after={"code": account.code, "name": account.name, "class": account.class_.value},
        actor_user_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        request=request,
    )
    return account


def update_account(
    db: Session,
    account: GLAccount,
    *,
    code: str | None = None,
    name: str | None = None,
    parent_id: int | None | object = ...,
    is_postable: bool | None = None,
    is_active: bool | None = None,
    actor: User | None,
    request: Request | None = None,
) -> GLAccount:
    before = {
        "code": account.code,
        "name": account.name,
        "parent_id": account.parent_id,
        "is_postable": account.is_postable,
        "is_active": account.is_active,
    }
    if code is not None and code != account.code:
        clash = db.scalar(
            select(GLAccount.id).where(
                GLAccount.company_id == account.company_id, GLAccount.code == code
            )
        )
        if clash is not None:
            raise ConflictError(
                f"Account code {code} already exists",
                code="account_code_taken",
                field_errors={"code": ["already in use"]},
            )
        account.code = code
    if name is not None:
        account.name = name
    if parent_id is not ...:
        if parent_id is not None:
            if parent_id == account.id:
                raise LedgerStateError("An account cannot be its own parent", code="invalid_parent")
            _validate_parent(db, account.company_id, parent_id, account.class_)  # type: ignore[arg-type]
        account.parent_id = parent_id  # type: ignore[assignment]
    if is_postable is not None and is_postable != account.is_postable:
        if not is_postable and _has_postings(db, account.id):
            raise LedgerStateError(
                "An account with postings must stay postable", code="account_has_postings"
            )
        if not is_postable and account.is_control:
            raise LedgerStateError(
                "Control accounts must be postable", code="control_must_be_postable"
            )
        if is_postable and _has_children(db, account.id):
            raise LedgerStateError(
                "A header account with children cannot become postable", code="account_has_children"
            )
        account.is_postable = is_postable
    if is_active is not None and is_active != account.is_active:
        if not is_active:
            settings = db.scalar(
                select(GLSettings).where(GLSettings.company_id == account.company_id)
            )
            in_use = settings is not None and account.id in {
                settings.retained_earnings_account_id,
                settings.rounding_difference_account_id,
            }
            if in_use:
                raise LedgerStateError(
                    "This account is referenced by GL settings and cannot be deactivated",
                    code="account_in_use",
                )
        account.is_active = is_active
    db.flush()
    after = {
        "code": account.code,
        "name": account.name,
        "parent_id": account.parent_id,
        "is_postable": account.is_postable,
        "is_active": account.is_active,
    }
    if after != before:
        record_audit(
            db,
            company_id=account.company_id,
            action="gl_account.renamed"
            if after["code"] != before["code"]
            else "gl_account.updated",
            entity="gl_accounts",
            entity_id=account.id,
            before=before,
            after=after,
            actor_user_id=actor.id if actor else None,
            actor_email=actor.email if actor else None,
            request=request,
        )
    return account


def add_exchange_rate(
    db: Session, company_id: int, *, currency_id: int, valid_from: date, rate, actor: User | None
) -> ExchangeRate:
    currency = db.get(Currency, currency_id)
    if currency is None or currency.company_id != company_id:
        raise NotFoundError("Currency not found")
    if currency.is_base:
        raise LedgerStateError("The base currency has no exchange rate", code="base_currency_rate")
    existing = db.scalar(
        select(ExchangeRate).where(
            ExchangeRate.currency_id == currency_id, ExchangeRate.valid_from == valid_from
        )
    )
    if existing is not None:
        raise ConflictError(
            f"A rate for {currency.code} on {valid_from.isoformat()} already exists",
            code="rate_exists",
        )
    row = ExchangeRate(
        company_id=company_id, currency_id=currency_id, valid_from=valid_from, rate=rate
    )
    db.add(row)
    db.flush()
    record_audit(
        db,
        company_id=company_id,
        action="exchange_rate.created",
        entity="exchange_rates",
        entity_id=row.id,
        after={"currency": currency.code, "valid_from": valid_from.isoformat(), "rate": str(rate)},
        actor_user_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
    )
    return row
