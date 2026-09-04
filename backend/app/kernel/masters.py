"""Dimension and defaults masters that hang off the ledger: projects (D8 job costing) and
transaction types (the 4th link of the ADR-05 determination chain).

Both are referenced by `journal_lines`, so neither is ever hard-deleted — deactivating keeps
history intact, exactly like `gl_accounts`.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import ConflictError, NotFoundError
from app.kernel.accounts import get_account
from app.kernel.errors import LedgerStateError
from app.models.gl import GLTransactionType, Project
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
    actor: User | None,
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
        actor_user_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
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
    actor: User | None,
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
    actor: User | None,
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
    actor: User | None,
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
    actor: User | None,
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
