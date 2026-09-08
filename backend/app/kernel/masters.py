"""Dimension and defaults masters that hang off the ledger: projects (D8 job costing) and
transaction types (the 4th link of the ADR-05 determination chain).

Both are referenced by `journal_lines`, so neither is ever hard-deleted — deactivating keeps
history intact, exactly like `gl_accounts`.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import exists, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import ConflictError, NotFoundError
from app.kernel.accounts import get_account
from app.kernel.errors import LedgerStateError
from app.models.company import Branch
from app.models.currency import Currency
from app.models.gl import GLTransactionType, Project
from app.models.journal import JournalLine
from app.models.tax import TaxCode, TaxNature
from app.models.user import User
from app.services.audit import record_audit

RESERVED_PREFIX = "__"


def _audit(
    db: Session,
    company_id: int,
    action: str,
    entity: str,
    entity_id: int,
    *,
    actor: User,
    before: dict | None = None,
    after: dict | None = None,
    request: Request | None = None,
) -> None:
    record_audit(
        db,
        company_id=company_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        before=before,
        after=after,
        actor_user_id=actor.id,
        actor_email=actor.email,
        request=request,
    )


# --- Projects ------------------------------------------------------------------------------


def list_projects(db: Session, company_id: int, *, include_inactive: bool = False) -> list[Project]:
    statement = select(Project).where(Project.company_id == company_id)
    if not include_inactive:
        statement = statement.where(Project.is_active)
    return list(db.scalars(statement.order_by(Project.code)))


def get_project(db: Session, company_id: int, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.company_id != company_id:
        raise NotFoundError("Project not found")
    return project


def create_project(
    db: Session,
    company_id: int,
    *,
    code: str,
    name: str,
    actor: User,
    request: Request | None = None,
) -> Project:
    duplicate = db.scalar(
        select(Project.id).where(Project.company_id == company_id, Project.code == code)
    )
    if duplicate is not None:
        raise ConflictError(
            f"Project code {code} already exists",
            code="project_code_taken",
            field_errors={"code": ["already in use"]},
        )
    project = Project(company_id=company_id, code=code, name=name, is_active=True)
    db.add(project)
    db.flush()
    _audit(
        db,
        company_id,
        "project.created",
        "projects",
        project.id,
        actor=actor,
        after={"code": code, "name": name},
        request=request,
    )
    return project


def update_project(
    db: Session,
    project: Project,
    *,
    code: str | None = None,
    name: str | None = None,
    is_active: bool | None = None,
    actor: User,
    request: Request | None = None,
) -> Project:
    before = {"code": project.code, "name": project.name, "is_active": project.is_active}
    if code is not None and code != project.code:
        clash = db.scalar(
            select(Project.id).where(Project.company_id == project.company_id, Project.code == code)
        )
        if clash is not None:
            raise ConflictError(
                f"Project code {code} already exists",
                code="project_code_taken",
                field_errors={"code": ["already in use"]},
            )
        project.code = code
    if name is not None:
        project.name = name
    if is_active is not None:
        project.is_active = is_active
    db.flush()
    after = {"code": project.code, "name": project.name, "is_active": project.is_active}
    if after != before:
        _audit(
            db,
            project.company_id,
            "project.renamed" if after["code"] != before["code"] else "project.updated",
            "projects",
            project.id,
            actor=actor,
            before=before,
            after=after,
            request=request,
        )
    return project


# --- Transaction types ---------------------------------------------------------------------


def list_transaction_types(
    db: Session, company_id: int, *, module: str | None = None, include_inactive: bool = False
) -> list[GLTransactionType]:
    statement = select(GLTransactionType).where(GLTransactionType.company_id == company_id)
    if module is not None:
        statement = statement.where(GLTransactionType.module == module)
    if not include_inactive:
        statement = statement.where(GLTransactionType.is_active)
    return list(db.scalars(statement.order_by(GLTransactionType.module, GLTransactionType.code)))


def get_transaction_type(db: Session, company_id: int, type_id: int) -> GLTransactionType:
    transaction_type = db.get(GLTransactionType, type_id)
    if transaction_type is None or transaction_type.company_id != company_id:
        raise NotFoundError("Transaction type not found")
    return transaction_type


def _assert_usable_default(db: Session, company_id: int, account_id: int | None) -> None:
    if account_id is None:
        return
    account = get_account(db, company_id, account_id)
    if not account.is_postable or not account.is_active:
        raise LedgerStateError(
            f"Account {account.code} is not an active postable account",
            code="account_not_postable",
        )


def create_transaction_type(
    db: Session,
    company_id: int,
    *,
    module: str,
    code: str,
    name: str,
    default_gl_account_id: int | None = None,
    actor: User,
    request: Request | None = None,
) -> GLTransactionType:
    if code.startswith(RESERVED_PREFIX):
        raise LedgerStateError(
            f"Codes starting with {RESERVED_PREFIX} are reserved by the kernel",
            code="reserved_transaction_type_code",
        )
    duplicate = db.scalar(
        select(GLTransactionType.id).where(
            GLTransactionType.company_id == company_id,
            GLTransactionType.module == module,
            GLTransactionType.code == code,
        )
    )
    if duplicate is not None:
        raise ConflictError(
            f"Transaction type {module}/{code} already exists",
            code="transaction_type_code_taken",
            field_errors={"code": ["already in use"]},
        )
    _assert_usable_default(db, company_id, default_gl_account_id)
    transaction_type = GLTransactionType(
        company_id=company_id,
        module=module,
        code=code,
        name=name,
        default_gl_account_id=default_gl_account_id,
        is_active=True,
    )
    db.add(transaction_type)
    db.flush()
    _audit(
        db,
        company_id,
        "gl_transaction_type.created",
        "gl_transaction_types",
        transaction_type.id,
        actor=actor,
        after={"module": module, "code": code, "default_gl_account_id": default_gl_account_id},
        request=request,
    )
    return transaction_type


def update_transaction_type(
    db: Session,
    transaction_type: GLTransactionType,
    *,
    name: str | None = None,
    default_gl_account_id: int | None | object = ...,
    is_active: bool | None = None,
    actor: User,
    request: Request | None = None,
) -> GLTransactionType:
    before = {
        "name": transaction_type.name,
        "default_gl_account_id": transaction_type.default_gl_account_id,
        "is_active": transaction_type.is_active,
    }
    if name is not None:
        transaction_type.name = name
    if default_gl_account_id is not ...:
        _assert_usable_default(
            db,
            transaction_type.company_id,
            default_gl_account_id,  # type: ignore[arg-type]
        )
        transaction_type.default_gl_account_id = default_gl_account_id  # type: ignore[assignment]
    if is_active is not None:
        transaction_type.is_active = is_active
    db.flush()
    after = {
        "name": transaction_type.name,
        "default_gl_account_id": transaction_type.default_gl_account_id,
        "is_active": transaction_type.is_active,
    }
    if after != before:
        _audit(
            db,
            transaction_type.company_id,
            "gl_transaction_type.updated",
            "gl_transaction_types",
            transaction_type.id,
            actor=actor,
            before=before,
            after=after,
            request=request,
        )
    return transaction_type


# --- Branches, tax codes, currencies (read-only; CRUD screens land with Maintenance, P3 step 4) ---


def list_branches(db: Session, company_id: int, *, include_inactive: bool = False) -> list[Branch]:
    statement = select(Branch).where(Branch.company_id == company_id)
    if not include_inactive:
        statement = statement.where(Branch.is_active)
    return list(db.scalars(statement.order_by(Branch.code)))


def get_branch(db: Session, company_id: int, branch_id: int) -> Branch:
    branch = db.get(Branch, branch_id)
    if branch is None or branch.company_id != company_id:
        raise NotFoundError("Branch not found")
    return branch


def create_branch(
    db: Session,
    company_id: int,
    *,
    code: str,
    name: str,
    is_main: bool = False,
    actor: User,
    request: Request | None = None,
) -> Branch:
    clash = db.scalar(
        select(Branch.id).where(Branch.company_id == company_id, Branch.code == code)
    )
    if clash is not None:
        raise ConflictError(
            f"Branch code {code} already exists",
            code="branch_code_taken",
            field_errors={"code": ["already in use"]},
        )
    if is_main:
        current_main = db.scalars(
            select(Branch).where(Branch.company_id == company_id, Branch.is_main)
        ).all()
        for b in current_main:
            b.is_main = False
    branch = Branch(company_id=company_id, code=code, name=name, is_main=is_main, is_active=True)
    db.add(branch)
    db.flush()
    _audit(
        db,
        company_id,
        "branch.created",
        "branches",
        branch.id,
        actor=actor,
        after={"code": code, "name": name, "is_main": is_main},
        request=request,
    )
    return branch


def update_branch(
    db: Session,
    branch: Branch,
    *,
    name: str | None = None,
    is_main: bool | None = None,
    is_active: bool | None = None,
    actor: User,
    request: Request | None = None,
) -> Branch:
    before = {"name": branch.name, "is_main": branch.is_main, "is_active": branch.is_active}
    if is_main and not branch.is_main:
        current_main = db.scalars(
            select(Branch).where(Branch.company_id == branch.company_id, Branch.is_main)
        ).all()
        for b in current_main:
            b.is_main = False
        branch.is_main = True
    elif is_main is False and branch.is_main:
        raise LedgerStateError("Cannot unset the only main branch", code="main_branch_required")

    if is_active is False and branch.is_main:
        raise LedgerStateError(
            "The main branch cannot be deactivated", code="cannot_deactivate_main_branch"
        )

    if name is not None:
        branch.name = name
    if is_active is not None:
        branch.is_active = is_active
    db.flush()
    after = {"name": branch.name, "is_main": branch.is_main, "is_active": branch.is_active}
    if after != before:
        _audit(
            db,
            branch.company_id,
            "branch.updated",
            "branches",
            branch.id,
            actor=actor,
            before=before,
            after=after,
            request=request,
        )
    return branch


def list_tax_codes(
    db: Session, company_id: int, *, include_inactive: bool = False
) -> list[TaxCode]:
    statement = select(TaxCode).where(TaxCode.company_id == company_id)
    if not include_inactive:
        statement = statement.where(TaxCode.is_active)
    return list(db.scalars(statement.order_by(TaxCode.code)))


def get_tax_code(db: Session, company_id: int, tax_code_id: int) -> TaxCode:
    tax_code = db.get(TaxCode, tax_code_id)
    if tax_code is None or tax_code.company_id != company_id:
        raise NotFoundError("Tax code not found")
    return tax_code


def create_tax_code(
    db: Session,
    company_id: int,
    *,
    code: str,
    name: str,
    nature: TaxNature,
    rate_pct: Decimal,
    gl_account_id: int | None = None,
    valid_from: date,
    valid_to: date | None = None,
    actor: User,
    request: Request | None = None,
) -> TaxCode:
    clash = db.scalar(
        select(TaxCode.id).where(TaxCode.company_id == company_id, TaxCode.code == code)
    )
    if clash is not None:
        raise ConflictError(
            f"Tax code {code} already exists",
            code="tax_code_taken",
            field_errors={"code": ["already in use"]},
        )
    if gl_account_id is not None:
        _assert_usable_default(db, company_id, gl_account_id)
    tax_code = TaxCode(
        company_id=company_id,
        code=code,
        name=name,
        nature=nature,
        rate_pct=rate_pct,
        gl_account_id=gl_account_id,
        valid_from=valid_from,
        valid_to=valid_to,
        is_active=True,
    )
    db.add(tax_code)
    db.flush()
    _audit(
        db,
        company_id,
        "tax_code.created",
        "tax_codes",
        tax_code.id,
        actor=actor,
        after={"code": code, "rate_pct": str(rate_pct)},
        request=request,
    )
    return tax_code


def _tax_code_has_postings(db: Session, company_id: int, tax_code_id: int) -> bool:
    return bool(
        db.scalar(
            select(
                exists().where(
                    JournalLine.company_id == company_id,
                    JournalLine.tax_code_id == tax_code_id,
                )
            )
        )
    )


def _currency_has_postings(db: Session, company_id: int, currency_id: int) -> bool:
    return bool(
        db.scalar(
            select(
                exists().where(
                    JournalLine.company_id == company_id,
                    JournalLine.currency_id == currency_id,
                )
            )
        )
    )


def update_tax_code(
    db: Session,
    tax_code: TaxCode,
    *,
    name: str | None = None,
    rate_pct: Decimal | None = None,
    gl_account_id: int | None | object = ...,
    valid_to: date | None | object = ...,
    is_active: bool | None = None,
    actor: User,
    request: Request | None = None,
) -> TaxCode:
    before = {
        "name": tax_code.name,
        "rate_pct": str(tax_code.rate_pct),
        "gl_account_id": tax_code.gl_account_id,
        "is_active": tax_code.is_active,
    }
    if name is not None:
        tax_code.name = name
    if rate_pct is not None and rate_pct != tax_code.rate_pct:
        if _tax_code_has_postings(db, tax_code.company_id, tax_code.id):
            raise LedgerStateError(
                "Tax rate cannot be changed after postings exist; "
                "rate changes must be a new row with valid_from",
                code="tax_code_has_postings",
            )
        tax_code.rate_pct = rate_pct
    if gl_account_id is not ...:
        if gl_account_id is not None:
            _assert_usable_default(db, tax_code.company_id, gl_account_id)  # type: ignore[arg-type]
        tax_code.gl_account_id = gl_account_id  # type: ignore[assignment]
    if valid_to is not ...:
        tax_code.valid_to = valid_to  # type: ignore[assignment]
    if is_active is not None:
        tax_code.is_active = is_active
    db.flush()
    after = {
        "name": tax_code.name,
        "rate_pct": str(tax_code.rate_pct),
        "gl_account_id": tax_code.gl_account_id,
        "is_active": tax_code.is_active,
    }
    if after != before:
        _audit(
            db,
            tax_code.company_id,
            "tax_code.updated",
            "tax_codes",
            tax_code.id,
            actor=actor,
            before=before,
            after=after,
            request=request,
        )
    return tax_code


def list_currencies(
    db: Session, company_id: int, *, include_inactive: bool = False
) -> list[Currency]:
    statement = select(Currency).where(Currency.company_id == company_id)
    if not include_inactive:
        statement = statement.where(Currency.is_active)
    return list(db.scalars(statement.order_by(Currency.code)))


def get_currency(db: Session, company_id: int, currency_id: int) -> Currency:
    currency = db.get(Currency, currency_id)
    if currency is None or currency.company_id != company_id:
        raise NotFoundError("Currency not found")
    return currency


def create_currency(
    db: Session,
    company_id: int,
    *,
    code: str,
    name: str,
    symbol: str | None = None,
    decimal_places: int = 2,
    actor: User,
    request: Request | None = None,
) -> Currency:
    clash = db.scalar(
        select(Currency.id).where(Currency.company_id == company_id, Currency.code == code.upper())
    )
    if clash is not None:
        raise ConflictError(
            f"Currency {code} already exists",
            code="currency_code_taken",
            field_errors={"code": ["already in use"]},
        )
    currency = Currency(
        company_id=company_id,
        code=code.upper(),
        name=name,
        symbol=symbol,
        decimal_places=decimal_places,
        is_base=False,
        is_active=True,
    )
    db.add(currency)
    db.flush()
    _audit(
        db,
        company_id,
        "currency.created",
        "currencies",
        currency.id,
        actor=actor,
        after={"code": currency.code, "name": name},
        request=request,
    )
    return currency


def update_currency(
    db: Session,
    currency: Currency,
    *,
    name: str | None = None,
    symbol: str | None = None,
    decimal_places: int | None = None,
    is_active: bool | None = None,
    actor: User,
    request: Request | None = None,
) -> Currency:
    if is_active is False and currency.is_base:
        raise LedgerStateError(
            "The base currency cannot be deactivated", code="cannot_deactivate_base_currency"
        )
    before = {
        "name": currency.name,
        "symbol": currency.symbol,
        "decimal_places": currency.decimal_places,
        "is_active": currency.is_active,
    }
    if name is not None:
        currency.name = name
    if symbol is not None:
        currency.symbol = symbol
    if decimal_places is not None and decimal_places != currency.decimal_places:
        if _currency_has_postings(db, currency.company_id, currency.id):
            raise LedgerStateError(
                "Decimal places cannot be changed after postings exist in this currency",
                code="currency_has_postings",
            )
        currency.decimal_places = decimal_places
    if is_active is not None:
        currency.is_active = is_active
    db.flush()
    after = {
        "name": currency.name,
        "symbol": currency.symbol,
        "decimal_places": currency.decimal_places,
        "is_active": currency.is_active,
    }
    if after != before:
        _audit(
            db,
            currency.company_id,
            "currency.updated",
            "currencies",
            currency.id,
            actor=actor,
            before=before,
            after=after,
            request=request,
        )
    return currency
