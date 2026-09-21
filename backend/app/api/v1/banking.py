"""The banking API (P8).

The screens arrive at **steps 6 and 7** — Maintenance → General Ledger → Bank accounts, and
Transactions → General Ledger → Bank statements — so every mutating endpoint here carries a
`GAP (P8, step 6)` or `GAP (P8, step 7)` line in `tests/test_api_has_a_caller.py` naming the
step that deletes it. None survives step 9.

The import endpoint takes a **file upload**, not a JSON body, which is the one place this
module departs from the rest of the API's shape. A bank export is a file the user picked; a
base64 field would make the preview and the import disagree about what was read (whitespace,
BOM, line endings all survive one path and not the other), and `file_sha256` — the refusal
that catches the same export twice — has to be over the bytes the bank produced.
"""

from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import AuthContext
from app.api.idempotency import IdempotencyKey
from app.banking import accounts as accounts_service
from app.banking import statements as statements_service
from app.banking.formats import ParsedLine, normalise
from app.core import permissions
from app.db import get_db
from app.schemas.banking import (
    BankAccountRead,
    BankAccountRegister,
    BankAccountUpdate,
    BankRuleRead,
    BankRuleWrite,
    ManualStatementWrite,
    StatementDetail,
    StatementImportResult,
    StatementLineRead,
    StatementPreviewRead,
    StatementRead,
    StatementVoid,
    UnregisteredAccountRead,
)

router = APIRouter(prefix="/banking", tags=["banking"])


def _form_amount(raw: str | None, field: str) -> Decimal | None:
    """A money value off a multipart form, as a `Decimal` and never through `float`.

    A form has no types, so the two keyed balances arrive as text. `Annotated[Decimal, Form()]`
    would hand the parse to Pydantic, which coerces through `float` on some shapes — and a
    `float` anywhere near money is the thing ADR-06 forbids. Empty means "not keyed", which is
    the ordinary case for a format whose file carries a balance column.
    """
    if raw is None or not raw.strip():
        return None
    try:
        return Decimal(raw.strip())
    except InvalidOperation as err:
        raise statements_service.StatementParseError(
            f"{raw!r} is not an amount", field_errors={field: ["not a number"]}
        ) from err


# --- Bank accounts ------------------------------------------------------------------------


@router.get("/accounts")
def list_bank_accounts(
    include_inactive: bool = Query(default=False),
    auth: AuthContext = permissions.require(permissions.BANK_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> list[BankAccountRead]:
    return [
        BankAccountRead.model_validate(row)
        for row in accounts_service.list_accounts(
            db, auth.company_id, include_inactive=include_inactive
        )
    ]


@router.get("/accounts/unregistered")
def list_unregistered_accounts(
    auth: AuthContext = permissions.require(permissions.BANK_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> list[UnregisteredAccountRead]:
    return [
        UnregisteredAccountRead.model_validate(row)
        for row in accounts_service.unregistered_control_accounts(db, auth.company_id)
    ]


@router.get("/accounts/{bank_account_id}")
def read_bank_account(
    bank_account_id: int,
    auth: AuthContext = permissions.require(permissions.BANK_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> BankAccountRead:
    return BankAccountRead.model_validate(
        accounts_service.get(db, auth.company_id, bank_account_id)
    )


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
def register_bank_account(
    payload: BankAccountRegister,
    request: Request,
    auth: AuthContext = permissions.require(permissions.BANK_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> BankAccountRead:
    row = accounts_service.register(
        db,
        auth.company_id,
        accounts_service.BankAccountInput(
            gl_account_id=payload.gl_account_id,
            code=payload.code,
            name=payload.name,
            currency_id=payload.currency_id,
            bank_name=payload.bank_name,
            account_number=payload.account_number,
            account_holder=payload.account_holder,
            bank_branch=payload.bank_branch,
            swift_bic=payload.swift_bic,
            statement_format=payload.statement_format,
        ),
        actor=auth.user,
        request=request,
    )
    db.commit()
    return BankAccountRead.model_validate(row)


@router.patch("/accounts/{bank_account_id}")
def update_bank_account(
    bank_account_id: int,
    payload: BankAccountUpdate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.BANK_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> BankAccountRead:
    row = accounts_service.get(db, auth.company_id, bank_account_id)
    fields = payload.model_dump(exclude_unset=True)
    row = accounts_service.update(
        db,
        row,
        code=fields.get("code"),
        name=fields.get("name"),
        currency_id=fields.get("currency_id"),
        bank_name=fields.get("bank_name", ...),
        account_number=fields.get("account_number", ...),
        account_holder=fields.get("account_holder", ...),
        bank_branch=fields.get("bank_branch", ...),
        swift_bic=fields.get("swift_bic", ...),
        statement_format=fields.get("statement_format", ...),
        is_active=fields.get("is_active"),
        actor=auth.user,
        request=request,
    )
    db.commit()
    return BankAccountRead.model_validate(row)


# --- Rules --------------------------------------------------------------------------------


@router.get("/accounts/{bank_account_id}/rules")
def list_rules(
    bank_account_id: int,
    auth: AuthContext = permissions.require(permissions.BANK_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> list[BankRuleRead]:
    return [
        BankRuleRead.model_validate(row)
        for row in accounts_service.list_rules(db, auth.company_id, bank_account_id)
    ]


@router.post("/accounts/{bank_account_id}/rules", status_code=status.HTTP_201_CREATED)
def create_rule(
    bank_account_id: int,
    payload: BankRuleWrite,
    request: Request,
    auth: AuthContext = permissions.require(permissions.BANK_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> BankRuleRead:
    row = accounts_service.create_rule(
        db,
        auth.company_id,
        bank_account_id=bank_account_id,
        pattern=payload.pattern,
        gl_account_id=payload.gl_account_id,
        tax_code_id=payload.tax_code_id,
        partner_type=payload.partner_type,
        partner_id=payload.partner_id,
        description=payload.description,
        priority=payload.priority,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return BankRuleRead.model_validate(row)


@router.patch("/rules/{rule_id}")
def update_rule(
    rule_id: int,
    payload: BankRuleWrite,
    request: Request,
    auth: AuthContext = permissions.require(permissions.BANK_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> BankRuleRead:
    row = accounts_service.get_rule(db, auth.company_id, rule_id)
    fields = payload.model_dump(exclude_unset=True)
    row = accounts_service.update_rule(
        db,
        row,
        pattern=fields.get("pattern"),
        gl_account_id=fields.get("gl_account_id", ...),
        tax_code_id=fields.get("tax_code_id", ...),
        partner_type=fields.get("partner_type", ...),
        partner_id=fields.get("partner_id", ...),
        description=fields.get("description", ...),
        priority=fields.get("priority"),
        is_active=fields.get("is_active"),
        actor=auth.user,
        request=request,
    )
    db.commit()
    return BankRuleRead.model_validate(row)


# --- Statements ---------------------------------------------------------------------------


@router.get("/statements")
def list_statements(
    bank_account_id: int | None = Query(default=None),
    include_void: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = permissions.require(permissions.BANK_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> list[StatementRead]:
    return [
        StatementRead.model_validate(row)
        for row in statements_service.list_statements(
            db,
            auth.company_id,
            bank_account_id=bank_account_id,
            include_void=include_void,
            limit=limit,
        )
    ]


@router.get("/statements/{statement_id}")
def read_statement(
    statement_id: int,
    auth: AuthContext = permissions.require(permissions.BANK_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> StatementDetail:
    statement = statements_service.get(db, auth.company_id, statement_id)
    detail = StatementDetail.model_validate(statement)
    detail.lines = [
        StatementLineRead.model_validate(line)
        for line in statements_service.lines_of(db, auth.company_id, statement_id)
    ]
    return detail


@router.post("/statements/preview")
def preview_statement(
    bank_account_id: Annotated[int, Form()],
    file: Annotated[UploadFile, File()],
    auth: AuthContext = permissions.require(permissions.BANK_STATEMENT_IMPORT),
    db: Session = Depends(get_db),
) -> StatementPreviewRead:
    """Parse and report; write nothing.

    A POST because it takes a file, not because it changes anything — which is why it needs no
    `Idempotency-Key`. `test_api_has_a_caller.py` still counts it as mutating (it goes by the
    method, correctly: a register that trusted a docstring would be a register nobody could
    check), so it carries a `GAP` line until the import screen ships.
    """
    preview = statements_service.preview(
        db,
        auth.company_id,
        bank_account_id=bank_account_id,
        content=file.file.read(),
        file_name=file.filename,
    )
    return StatementPreviewRead.model_validate(preview, from_attributes=True)


@router.post("/statements", status_code=status.HTTP_201_CREATED)
def import_statement(
    request: Request,
    bank_account_id: Annotated[int, Form()],
    file: Annotated[UploadFile, File()],
    opening_balance: Annotated[str | None, Form()] = None,
    closing_balance: Annotated[str | None, Form()] = None,
    auth: AuthContext = permissions.require(permissions.BANK_STATEMENT_IMPORT),
    db: Session = Depends(get_db),
    idempotency_key: str = IdempotencyKey,
) -> StatementImportResult:
    """Import the file. Refused on any parse error, and on the same file twice.

    The two balances arrive as **strings** and are parsed as `Decimal`, never through `float`:
    a multipart form has no types, and `Annotated[Decimal, Form()]` would route the value
    through Pydantic's float coercion on some shapes. Money is `Decimal` at every boundary in
    this build, including this one (ADR-06).
    """
    result = statements_service.import_statement(
        db,
        auth.company_id,
        bank_account_id=bank_account_id,
        content=file.file.read(),
        file_name=file.filename,
        opening_balance=_form_amount(opening_balance, "opening_balance"),
        closing_balance=_form_amount(closing_balance, "closing_balance"),
        actor=auth.user,
        idempotency_key=idempotency_key,
        request=request,
    )
    db.commit()
    return StatementImportResult(
        statement=StatementRead.model_validate(result.statement),
        new_count=result.new_count,
        skipped_count=result.skipped_count,
        replayed=result.replayed,
    )


@router.post("/statements/manual", status_code=status.HTTP_201_CREATED)
def key_manual_statement(
    payload: ManualStatementWrite,
    request: Request,
    auth: AuthContext = permissions.require(permissions.BANK_STATEMENT_IMPORT),
    db: Session = Depends(get_db),
    idempotency_key: str = IdempotencyKey,
) -> StatementImportResult:
    """A paper statement, keyed line by line. Same table, same fingerprints, same dedup — so
    nothing downstream can tell a keyed statement from an imported one."""
    lines = [
        ParsedLine(
            row=index + 1,
            value_date=line.value_date,
            booking_date=line.booking_date,
            description=line.description,
            reference=line.reference,
            amount=line.amount,
            balance_after=line.balance_after,
            external_id=None,
            occurrence=sum(
                1
                for earlier in payload.lines[:index]
                if (
                    earlier.value_date,
                    earlier.amount,
                    normalise(earlier.description),
                    normalise(earlier.reference),
                )
                == (
                    line.value_date,
                    line.amount,
                    normalise(line.description),
                    normalise(line.reference),
                )
            ),
        )
        for index, line in enumerate(payload.lines)
    ]
    result = statements_service.import_manual(
        db,
        auth.company_id,
        bank_account_id=payload.bank_account_id,
        lines=lines,
        opening_balance=payload.opening_balance,
        closing_balance=payload.closing_balance,
        actor=auth.user,
        idempotency_key=idempotency_key,
        request=request,
    )
    db.commit()
    return StatementImportResult(
        statement=StatementRead.model_validate(result.statement),
        new_count=result.new_count,
        skipped_count=result.skipped_count,
        replayed=result.replayed,
    )


@router.post("/statements/{statement_id}/void")
def void_statement(
    statement_id: int,
    payload: StatementVoid,
    request: Request,
    auth: AuthContext = permissions.require(permissions.BANK_STATEMENT_IMPORT),
    db: Session = Depends(get_db),
) -> StatementRead:
    statement = statements_service.void(
        db,
        auth.company_id,
        statement_id,
        reason=payload.reason,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return StatementRead.model_validate(statement)
