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

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.api.deps import AuthContext
from app.api.idempotency import IdempotencyKey
from app.banking import accounts as accounts_service
from app.banking import matching
from app.banking import reconciliation as reconciliation_service
from app.banking import statements as statements_service
from app.banking.formats import ParsedLine, normalise
from app.core import permissions
from app.db import get_db
from app.models.banking import (
    BankMatchKind,
    BankMatchRule,
    BankStatementLine,
    ReconciliationStatus,
)
from app.schemas.banking import (
    AutoMatchResultRead,
    BankAccountRead,
    BankAccountRegister,
    BankAccountUpdate,
    BankRuleRead,
    BankRuleWrite,
    FiguresRead,
    ManualStatementWrite,
    MatchCandidateRead,
    MatchRead,
    MatchWrite,
    OutstandingLineRead,
    PostCashbookFromLine,
    PostedFromStatementRead,
    PostSettlementFromLine,
    PrefillRead,
    ReconciliationDetail,
    ReconciliationLock,
    ReconciliationOpen,
    ReconciliationRead,
    ReconciliationReopen,
    StatementDetail,
    StatementImportResult,
    StatementLineRead,
    StatementPreviewRead,
    StatementRead,
    StatementVoid,
    TickWrite,
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


# --- The workspace: matching (P8 decision 4) ------------------------------------------------
#
# Every route below is the reconciliation workspace seen from the server. It arrives as a
# screen at **step 7**, so each mutating one carries a `GAP (P8, step 7)` line in the register.


def _match_read(db: Session, company_id: int, match) -> MatchRead:  # noqa: ANN001
    statement_lines, journal_lines = matching.members_of(db, company_id, match.id)
    read = MatchRead.model_validate(match)
    read.statement_line_ids = statement_lines
    read.journal_line_ids = journal_lines
    return read


@router.get("/accounts/{bank_account_id}/unmatched-statement-lines")
def list_unmatched_statement_lines(
    bank_account_id: int,
    on_or_before: date | None = Query(default=None),
    auth: AuthContext = permissions.require(permissions.BANK_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> list[StatementLineRead]:
    """The workspace's left pane. Void lines are already gone: a voided statement leaves every
    listing and every count (decision 3)."""
    row = accounts_service.get(db, auth.company_id, bank_account_id)
    return [
        StatementLineRead.model_validate(line)
        for line in db.scalars(
            matching.unmatched_statement_lines(
                db, auth.company_id, row, on_or_before=on_or_before
            ).order_by(BankStatementLine.value_date, BankStatementLine.id)
        )
    ]


@router.get("/statement-lines/{statement_line_id}/candidates")
def list_candidates(
    statement_line_id: int,
    auth: AuthContext = permissions.require(permissions.BANK_RECONCILE),
    db: Session = Depends(get_db),
) -> list[MatchCandidateRead]:
    """What this line could be, by the first rule that has anything to say. Where it returns
    more than one the workspace shows the choice rather than guessing."""
    line = matching.get_statement_line(db, auth.company_id, statement_line_id)
    row = accounts_service.get(db, auth.company_id, line.bank_account_id)
    return [
        MatchCandidateRead(**vars(candidate))
        for candidate in matching.candidates_for(db, auth.company_id, row, line)
    ]


@router.get("/statement-lines/{statement_line_id}/prefill")
def read_prefill(
    statement_line_id: int,
    auth: AuthContext = permissions.require(permissions.BANK_RECONCILE),
    db: Session = Depends(get_db),
) -> PrefillRead:
    """What the post-from-a-statement-line drawer opens with. A suggestion: a `bank_rules` row
    never posts, and the engine's own control-account refusals are what stop one aiming
    somewhere it should not."""
    line = matching.get_statement_line(db, auth.company_id, statement_line_id)
    return PrefillRead(**matching.prefill_for(db, auth.company_id, line).as_dict())


@router.post("/matches", status_code=status.HTTP_201_CREATED)
def create_match(
    payload: MatchWrite,
    request: Request,
    auth: AuthContext = permissions.require(permissions.BANK_RECONCILE),
    db: Session = Depends(get_db),
) -> MatchRead:
    """A manual n:m match. Refused unless it balances, with the difference named — the
    workspace shows that figure beside the button before it is pressed."""
    match = matching.create_match(
        db,
        auth.company_id,
        bank_account_id=payload.bank_account_id,
        statement_line_ids=payload.statement_line_ids,
        journal_line_ids=payload.journal_line_ids,
        kind=BankMatchKind.MANUAL,
        rule=BankMatchRule.MANUAL,
        note=payload.note,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return _match_read(db, auth.company_id, match)


@router.post("/matches/tick", status_code=status.HTTP_201_CREATED)
def tick_lines(
    payload: TickWrite,
    request: Request,
    auth: AuthContext = permissions.require(permissions.BANK_RECONCILE),
    db: Session = Depends(get_db),
) -> MatchRead:
    """Paper mode: the ledger line is ticked against a statement nobody imported."""
    match = matching.tick(
        db,
        auth.company_id,
        bank_account_id=payload.bank_account_id,
        journal_line_ids=payload.journal_line_ids,
        note=payload.note,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return _match_read(db, auth.company_id, match)


@router.post("/accounts/{bank_account_id}/auto-match")
def run_auto_match(
    bank_account_id: int,
    request: Request,
    auth: AuthContext = permissions.require(permissions.BANK_RECONCILE),
    db: Session = Depends(get_db),
) -> AutoMatchResultRead:
    result = matching.auto_match(
        db, auth.company_id, bank_account_id, actor=auth.user, request=request
    )
    db.commit()
    return AutoMatchResultRead(
        matched=[_match_read(db, auth.company_id, match) for match in result.matched],
        ambiguous={
            line_id: [MatchCandidateRead(**vars(candidate)) for candidate in candidates]
            for line_id, candidates in result.ambiguous.items()
        },
    )


@router.delete("/matches/{match_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_match(
    match_id: int,
    request: Request,
    auth: AuthContext = permissions.require(permissions.BANK_RECONCILE),
    db: Session = Depends(get_db),
) -> Response:
    """Withdraw the assertion. Refused once it belongs to a locked reconciliation — reopen
    first."""
    matching.unmatch(db, auth.company_id, match_id, actor=auth.user, request=request)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/statement-lines/{statement_line_id}/post-cashbook", status_code=201)
def post_cashbook_from_statement_line(
    statement_line_id: int,
    payload: PostCashbookFromLine,
    request: Request,
    auth: AuthContext = permissions.require(
        permissions.BANK_RECONCILE, permissions.GL_JOURNAL_POST
    ),
    db: Session = Depends(get_db),
    idempotency_key: str = IdempotencyKey,
) -> PostedFromStatementRead:
    """The fee, the interest, the movement the ledger lacks — posted through the kernel and
    matched in the same transaction.

    **Two permissions**, deliberately (decision 11): working the match is `bank:reconcile`, and
    writing the ledger is `gl:journal_post`. A reconciler who may tick cannot quietly post.
    """
    posted = matching.post_cashbook_from_line(
        db,
        auth.company_id,
        statement_line_id,
        gl_account_id=payload.gl_account_id,
        tax_code_id=payload.tax_code_id,
        description=payload.description,
        reference=payload.reference,
        entry_date=payload.entry_date,
        branch_id=payload.branch_id,
        project_id=payload.project_id,
        actor=auth.user,
        idempotency_key=idempotency_key,
        request=request,
    )
    db.commit()
    return PostedFromStatementRead(
        entry_id=posted.entry_id,
        entry_number=posted.entry_number,
        journal_line_id=posted.journal_line_id,
        match=_match_read(db, auth.company_id, posted.match),
    )


@router.post("/statement-lines/{statement_line_id}/post-settlement", status_code=201)
def post_settlement_from_statement_line(
    statement_line_id: int,
    payload: PostSettlementFromLine,
    request: Request,
    auth: AuthContext = permissions.require(permissions.BANK_RECONCILE),
    db: Session = Depends(get_db),
    idempotency_key: str = IdempotencyKey,
) -> PostedFromStatementRead:
    """The customer receipt or supplier payment the bank is showing, posted through P4 and
    matched in the same transaction — **unallocated**, because which invoices it pays is the
    allocation screen's decision and not a bank statement's.

    The role follows the line's sign, so the permission the posting needs does too:
    `ar:transactions_post` on a credit, `ap:transactions_post` on a debit. Passed to
    `post_document` as the caller's granted set rather than declared here, because which one is
    required is not known until the line is read.
    """
    posted = matching.post_settlement_from_line(
        db,
        auth.company_id,
        statement_line_id,
        partner_id=payload.partner_id,
        description=payload.description,
        reference=payload.reference,
        document_date=payload.document_date,
        branch_id=payload.branch_id,
        project_id=payload.project_id,
        actor=auth.user,
        permissions=set(auth.permissions),
        idempotency_key=idempotency_key,
        request=request,
    )
    db.commit()
    return PostedFromStatementRead(
        entry_id=posted.entry_id,
        entry_number=posted.entry_number,
        journal_line_id=posted.journal_line_id,
        match=_match_read(db, auth.company_id, posted.match),
        document_id=posted.document_id,
        document_number=posted.document_number,
    )


# --- The workspace: reconciliation (P8 decision 5) -------------------------------------------


def _figures_read(figures) -> FiguresRead:  # noqa: ANN001
    return FiguresRead(
        reconciliation_date=figures.reconciliation_date,
        statement_balance=figures.statement_balance,
        ledger_balance=figures.ledger_balance,
        outstanding_total=figures.outstanding_total,
        difference=figures.difference,
        adjusted_bank_balance=figures.adjusted_bank_balance,
        outstanding=[OutstandingLineRead(**vars(item)) for item in figures.outstanding],
        unmatched_statement=[
            StatementLineRead.model_validate(line) for line in figures.unmatched_statement
        ],
        unmatched_statement_count=figures.unmatched_statement_count,
    )


@router.get("/reconciliations")
def list_reconciliations(
    bank_account_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = permissions.require(permissions.BANK_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> list[ReconciliationRead]:
    return [
        ReconciliationRead.model_validate(row)
        for row in reconciliation_service.list_reconciliations(
            db, auth.company_id, bank_account_id=bank_account_id, limit=limit
        )
    ]


@router.get("/reconciliations/{reconciliation_id}")
def read_reconciliation(
    reconciliation_id: int,
    auth: AuthContext = permissions.require(permissions.BANK_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> ReconciliationDetail:
    """The workspace, and the report. A locked one carries **both** figure sets: what it said,
    reproduced from the lines that existed at the lock, and what the same date computes now —
    with the late lines that account for any difference between them."""
    reconciliation = reconciliation_service.get(db, auth.company_id, reconciliation_id)
    locked = reconciliation.status == ReconciliationStatus.LOCKED
    # Built rather than `model_validate`-then-assigned: `figures` is computed, not a column, so
    # validating the ORM row against a model that requires it fails before anything is filled
    # in. Constructing it says which parts are stored and which are derived.
    return ReconciliationDetail(
        **ReconciliationRead.model_validate(reconciliation).model_dump(),
        figures=_figures_read(
            reconciliation_service.live_figures(db, auth.company_id, reconciliation)
        ),
        stored=_figures_read(
            reconciliation_service.stored_figures(db, auth.company_id, reconciliation)
        )
        if locked
        else None,
        late_line_ids=(
            reconciliation_service.late_lines(db, auth.company_id, reconciliation)
            if locked
            else []
        ),
    )


@router.post("/reconciliations", status_code=status.HTTP_201_CREATED)
def open_reconciliation(
    payload: ReconciliationOpen,
    request: Request,
    auth: AuthContext = permissions.require(permissions.BANK_RECONCILE_LOCK),
    db: Session = Depends(get_db),
    idempotency_key: str = IdempotencyKey,
) -> ReconciliationDetail:
    reconciliation = reconciliation_service.open_reconciliation(
        db,
        auth.company_id,
        bank_account_id=payload.bank_account_id,
        reconciliation_date=payload.reconciliation_date,
        statement_balance=payload.statement_balance,
        actor=auth.user,
        idempotency_key=idempotency_key,
        request=request,
    )
    db.commit()
    return read_reconciliation(reconciliation.id, auth=auth, db=db)


@router.post("/reconciliations/{reconciliation_id}/lock")
def lock_reconciliation(
    reconciliation_id: int,
    payload: ReconciliationLock,
    request: Request,
    auth: AuthContext = permissions.require(permissions.BANK_RECONCILE_LOCK),
    db: Session = Depends(get_db),
    idempotency_key: str = IdempotencyKey,
) -> ReconciliationDetail:
    """Sign it off — at a zero difference with every statement line explained, and at nothing
    else. Both refusals name their figure, so the workspace can show them before the button."""
    reconciliation_service.lock(
        db,
        auth.company_id,
        reconciliation_id,
        statement_balance=payload.statement_balance,
        actor=auth.user,
        idempotency_key=idempotency_key,
        request=request,
    )
    db.commit()
    return read_reconciliation(reconciliation_id, auth=auth, db=db)


@router.post("/reconciliations/{reconciliation_id}/reopen")
def reopen_reconciliation(
    reconciliation_id: int,
    payload: ReconciliationReopen,
    request: Request,
    auth: AuthContext = permissions.require(permissions.BANK_RECONCILE_LOCK),
    db: Session = Depends(get_db),
) -> ReconciliationDetail:
    """Withdraw the signature on the account's latest locked reconciliation. The matches stand;
    what is withdrawn is the sign-off."""
    reconciliation_service.reopen(
        db,
        auth.company_id,
        reconciliation_id,
        reason=payload.reason,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return read_reconciliation(reconciliation_id, auth=auth, db=db)
