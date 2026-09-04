"""General Ledger API (P2): COA, exchange rates, manual journal & cashbook entries, reversal,
enquiries, periods and year-end. Routers hold no business logic — everything posts through
`app.kernel`."""

from datetime import date

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import AuthContext
from app.core import permissions
from app.core.errors import NotFoundError
from app.db import get_db
from app.kernel import accounts as accounts_service
from app.kernel import balances, enquiries, posting, year_end
from app.kernel import periods as periods_service
from app.kernel.errors import LedgerStateError
from app.kernel.events import (
    CashbookEntry,
    CashbookKind,
    CashbookLineSpec,
    LineSpec,
    ManualJournal,
)
from app.models.currency import ExchangeRate
from app.models.fiscal import AccountingPeriod, FiscalYear, PeriodStatus
from app.models.gl import GLSettings
from app.models.journal import JournalEntry
from app.schemas.common import Page
from app.schemas.gl import (
    AccountTransactionRead,
    AccountTransactionsRead,
    CashbookEntryCreate,
    ExchangeRateCreate,
    ExchangeRateRead,
    FiscalYearCreate,
    FiscalYearRead,
    GLAccountCreate,
    GLAccountRead,
    GLAccountUpdate,
    GLSettingsRead,
    GLSettingsUpdate,
    JournalEntryCreate,
    JournalEntryRead,
    JournalEntrySummary,
    PeriodBalanceDriftRead,
    PeriodRead,
    ReasonBody,
    ReversalCreate,
    TrialBalanceRead,
    TrialBalanceRowRead,
)

router = APIRouter(prefix="/gl", tags=["general-ledger"])

IdempotencyKey = Header(
    alias="Idempotency-Key",
    min_length=1,
    max_length=64,
    description="Client-generated key; replaying it returns the original entry (ADR-11).",
)


def _entry_read(db: Session, entry: JournalEntry) -> JournalEntryRead:
    loaded = db.scalars(
        select(JournalEntry)
        .options(selectinload(JournalEntry.lines))
        .where(JournalEntry.id == entry.id)
    ).one()
    return JournalEntryRead.model_validate(loaded)


def _posted_response(
    db: Session, response: Response, entry: JournalEntry, *, replayed: bool
) -> JournalEntryRead:
    db.commit()
    response.status_code = status.HTTP_200_OK if replayed else status.HTTP_201_CREATED
    return _entry_read(db, entry)


# --- Chart of accounts -------------------------------------------------------------------


@router.get("/accounts")
def list_accounts(
    include_inactive: bool = False,
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> list[GLAccountRead]:
    rows = accounts_service.list_accounts(db, auth.company_id, include_inactive=include_inactive)
    return [GLAccountRead.model_validate(row) for row in rows]


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
def create_account(
    payload: GLAccountCreate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.GL_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> GLAccountRead:
    account = accounts_service.create_account(
        db,
        auth.company_id,
        accounts_service.AccountInput(
            code=payload.code,
            name=payload.name,
            class_=payload.class_,
            parent_id=payload.parent_id,
            is_postable=payload.is_postable,
            control_type=payload.control_type,
        ),
        actor=auth.user,
        request=request,
    )
    db.commit()
    return GLAccountRead.model_validate(account)


@router.get("/accounts/{account_id}")
def get_account(
    account_id: int,
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> GLAccountRead:
    return GLAccountRead.model_validate(
        accounts_service.get_account(db, auth.company_id, account_id)
    )


@router.patch("/accounts/{account_id}")
def update_account(
    account_id: int,
    payload: GLAccountUpdate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.GL_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> GLAccountRead:
    account = accounts_service.get_account(db, auth.company_id, account_id)
    parent: int | None | object = ...
    if payload.clear_parent:
        parent = None
    elif payload.parent_id is not None:
        parent = payload.parent_id
    accounts_service.update_account(
        db,
        account,
        code=payload.code,
        name=payload.name,
        parent_id=parent,
        is_postable=payload.is_postable,
        is_active=payload.is_active,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return GLAccountRead.model_validate(account)


@router.get("/settings")
def get_settings(
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> GLSettingsRead:
    return GLSettingsRead.model_validate(posting.gl_settings_for(db, auth.company_id))


@router.put("/settings")
def update_settings(
    payload: GLSettingsUpdate,
    auth: AuthContext = permissions.require(permissions.GL_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> GLSettingsRead:
    settings: GLSettings = posting.gl_settings_for(db, auth.company_id)
    account = accounts_service.get_account(
        db, auth.company_id, payload.retained_earnings_account_id
    )
    if not account.is_postable or account.is_control or not account.is_active:
        raise LedgerStateError(
            "Retained earnings must be an active, postable, non-control account",
            code="invalid_retained_earnings",
        )
    settings.retained_earnings_account_id = account.id
    db.commit()
    return GLSettingsRead.model_validate(settings)


# --- Exchange rates ----------------------------------------------------------------------


@router.get("/exchange-rates")
def list_exchange_rates(
    currency_id: int | None = None,
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> list[ExchangeRateRead]:
    statement = select(ExchangeRate).where(ExchangeRate.company_id == auth.company_id)
    if currency_id is not None:
        statement = statement.where(ExchangeRate.currency_id == currency_id)
    rows = db.scalars(statement.order_by(ExchangeRate.currency_id, ExchangeRate.valid_from))
    return [ExchangeRateRead.model_validate(row) for row in rows]


@router.post("/exchange-rates", status_code=status.HTTP_201_CREATED)
def create_exchange_rate(
    payload: ExchangeRateCreate,
    auth: AuthContext = permissions.require(permissions.COMMON_SETUP_CURRENCIES),
    db: Session = Depends(get_db),
) -> ExchangeRateRead:
    row = accounts_service.add_exchange_rate(
        db,
        auth.company_id,
        currency_id=payload.currency_id,
        valid_from=payload.valid_from,
        rate=payload.rate,
        actor=auth.user,
    )
    db.commit()
    return ExchangeRateRead.model_validate(row)


# --- Journal & cashbook entries ----------------------------------------------------------


@router.post("/journal-entries", status_code=status.HTTP_201_CREATED)
def create_journal_entry(
    payload: JournalEntryCreate,
    response: Response,
    idempotency_key: str = IdempotencyKey,
    auth: AuthContext = permissions.require(permissions.GL_JOURNAL_POST),
    db: Session = Depends(get_db),
) -> JournalEntryRead:
    existing = posting.find_by_idempotency_key(db, auth.company_id, idempotency_key)
    if existing is not None:
        return _posted_response(db, response, existing, replayed=True)
    event = ManualJournal(
        entry_date=payload.entry_date,
        description=payload.description,
        branch_id=payload.branch_id,
        idempotency_key=idempotency_key,
        lines=tuple(
            LineSpec(
                amount=line.signed_amount,
                gl_account_id=line.gl_account_id,
                currency_id=line.currency_id,
                exchange_rate=line.exchange_rate,
                branch_id=line.branch_id,
                project_id=line.project_id,
                partner_type=line.partner_type,
                partner_id=line.partner_id,
                item_id=line.item_id,
                tax_code_id=line.tax_code_id,
                tax_amount=line.tax_amount,
                description=line.description,
            )
            for line in payload.lines
        ),
    )
    entry = posting.post(db, event, company_id=auth.company_id, actor=auth.user)
    assert entry is not None
    return _posted_response(db, response, entry, replayed=False)


@router.post("/cashbook-entries", status_code=status.HTTP_201_CREATED)
def create_cashbook_entry(
    payload: CashbookEntryCreate,
    response: Response,
    idempotency_key: str = IdempotencyKey,
    auth: AuthContext = permissions.require(permissions.GL_JOURNAL_POST),
    db: Session = Depends(get_db),
) -> JournalEntryRead:
    existing = posting.find_by_idempotency_key(db, auth.company_id, idempotency_key)
    if existing is not None:
        return _posted_response(db, response, existing, replayed=True)
    event = CashbookEntry(
        entry_date=payload.entry_date,
        description=payload.description,
        branch_id=payload.branch_id,
        idempotency_key=idempotency_key,
        cash_account_id=payload.cash_account_id,
        kind=CashbookKind(payload.kind),
        currency_id=payload.currency_id,
        exchange_rate=payload.exchange_rate,
        reference=payload.reference,
        lines=tuple(
            CashbookLineSpec(
                gl_account_id=line.gl_account_id,
                amount=line.amount,
                tax_code_id=line.tax_code_id,
                tax_inclusive=line.tax_inclusive,
                branch_id=line.branch_id,
                project_id=line.project_id,
                partner_type=line.partner_type,
                partner_id=line.partner_id,
                description=line.description,
            )
            for line in payload.lines
        ),
    )
    entry = posting.post(db, event, company_id=auth.company_id, actor=auth.user)
    assert entry is not None
    return _posted_response(db, response, entry, replayed=False)


@router.get("/journal-entries")
def list_journal_entries(
    cursor: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    date_from: date | None = None,
    date_to: date | None = None,
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> Page[JournalEntrySummary]:
    statement = select(JournalEntry).where(JournalEntry.company_id == auth.company_id)
    if date_from is not None:
        statement = statement.where(JournalEntry.entry_date >= date_from)
    if date_to is not None:
        statement = statement.where(JournalEntry.entry_date <= date_to)
    if cursor is not None:
        statement = statement.where(JournalEntry.id > cursor)
    rows = db.scalars(statement.order_by(JournalEntry.id).limit(limit + 1)).all()
    items = [JournalEntrySummary.model_validate(row) for row in rows[:limit]]
    return Page(items=items, next_cursor=items[-1].id if len(rows) > limit else None)


@router.get("/journal-entries/{entry_id}")
def get_journal_entry(
    entry_id: int,
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> JournalEntryRead:
    entry = db.get(JournalEntry, entry_id)
    if entry is None or entry.company_id != auth.company_id:
        raise NotFoundError("Journal entry not found")
    return _entry_read(db, entry)


@router.post("/journal-entries/{entry_id}/reverse", status_code=status.HTTP_201_CREATED)
def reverse_journal_entry(
    entry_id: int,
    payload: ReversalCreate,
    response: Response,
    idempotency_key: str = IdempotencyKey,
    auth: AuthContext = permissions.require(permissions.GL_JOURNAL_POST),
    db: Session = Depends(get_db),
) -> JournalEntryRead:
    existing = posting.find_by_idempotency_key(db, auth.company_id, idempotency_key)
    if existing is not None:
        return _posted_response(db, response, existing, replayed=True)
    entry = posting.reverse(
        db,
        entry_id,
        company_id=auth.company_id,
        on_date=payload.entry_date,
        reason=payload.reason,
        actor=auth.user,
        idempotency_key=idempotency_key,
    )
    return _posted_response(db, response, entry, replayed=False)


# --- Enquiries ---------------------------------------------------------------------------


@router.get("/trial-balance")
def get_trial_balance(
    as_of: date,
    branch_id: int | None = None,
    project_id: int | None = None,
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> TrialBalanceRead:
    report = enquiries.trial_balance(
        db, auth.company_id, as_of=as_of, branch_id=branch_id, project_id=project_id
    )
    return TrialBalanceRead(
        as_of=report.as_of,
        branch_id=report.branch_id,
        project_id=report.project_id,
        rows=[
            TrialBalanceRowRead(
                gl_account_id=row.gl_account_id,
                code=row.code,
                name=row.name,
                class_=row.class_,
                debit=row.debit,
                credit=row.credit,
                net=row.net,
            )
            for row in report.rows
        ],
        total_debit=report.total_debit,
        total_credit=report.total_credit,
        foots=report.foots,
    )


@router.get("/accounts/{account_id}/transactions")
def get_account_transactions(
    account_id: int,
    date_from: date,
    date_to: date,
    branch_id: int | None = None,
    project_id: int | None = None,
    cursor: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> AccountTransactionsRead:
    report = enquiries.account_transactions(
        db,
        auth.company_id,
        account_id,
        date_from=date_from,
        date_to=date_to,
        branch_id=branch_id,
        project_id=project_id,
        cursor=cursor,
        limit=limit,
    )
    return AccountTransactionsRead(
        gl_account_id=report.gl_account_id,
        date_from=report.date_from,
        date_to=report.date_to,
        opening_base=report.opening_base,
        items=[AccountTransactionRead(**vars(item)) for item in report.items],
        next_cursor=report.next_cursor,
    )


@router.get("/period-balances/verify")
def verify_period_balances(
    auth: AuthContext = permissions.require(permissions.GL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> list[PeriodBalanceDriftRead]:
    drift = balances.verify_period_balances(db, auth.company_id)
    return [PeriodBalanceDriftRead(**vars(item)) for item in drift]


# --- Periods & fiscal years --------------------------------------------------------------


@router.get("/fiscal-years")
def list_fiscal_years(
    auth: AuthContext = permissions.require(permissions.COMPANY_READ),
    db: Session = Depends(get_db),
) -> list[FiscalYearRead]:
    rows = db.scalars(
        select(FiscalYear)
        .where(FiscalYear.company_id == auth.company_id)
        .order_by(FiscalYear.start_date)
    )
    return [FiscalYearRead.model_validate(row) for row in rows]


@router.post("/fiscal-years", status_code=status.HTTP_201_CREATED)
def create_fiscal_year(
    payload: FiscalYearCreate,
    auth: AuthContext = permissions.require(permissions.ACCOUNTING_PERIODS_MANAGE),
    db: Session = Depends(get_db),
) -> FiscalYearRead:
    year = periods_service.create_fiscal_year(
        db,
        auth.company_id,
        name=payload.name,
        start_date=payload.start_date,
        end_date=payload.end_date,
        open_through=payload.open_through,
    )
    db.commit()
    return FiscalYearRead.model_validate(year)


@router.post("/fiscal-years/{year_id}/close")
def close_fiscal_year(
    year_id: int,
    request: Request,
    auth: AuthContext = permissions.require(permissions.ACCOUNTING_PERIODS_MANAGE),
    db: Session = Depends(get_db),
) -> FiscalYearRead:
    year = year_end.close_fiscal_year(
        db, auth.company_id, year_id, actor=auth.user, request=request
    )
    db.commit()
    return FiscalYearRead.model_validate(year)


@router.post("/fiscal-years/{year_id}/reopen")
def reopen_fiscal_year(
    year_id: int,
    payload: ReasonBody,
    request: Request,
    auth: AuthContext = permissions.require(
        permissions.ACCOUNTING_PERIODS_MANAGE, permissions.ACCOUNTING_PERIODS_REOPEN
    ),
    db: Session = Depends(get_db),
) -> FiscalYearRead:
    year = year_end.reopen_fiscal_year(
        db, auth.company_id, year_id, actor=auth.user, reason=payload.reason, request=request
    )
    db.commit()
    return FiscalYearRead.model_validate(year)


@router.get("/periods")
def list_periods(
    fiscal_year_id: int | None = None,
    auth: AuthContext = permissions.require(permissions.COMPANY_READ),
    db: Session = Depends(get_db),
) -> list[PeriodRead]:
    statement = select(AccountingPeriod).where(AccountingPeriod.company_id == auth.company_id)
    if fiscal_year_id is not None:
        statement = statement.where(AccountingPeriod.fiscal_year_id == fiscal_year_id)
    rows = db.scalars(statement.order_by(AccountingPeriod.start_date))
    return [PeriodRead.model_validate(row) for row in rows]


def _transition(
    db: Session,
    auth: AuthContext,
    request: Request,
    period_id: int,
    target: PeriodStatus,
    reason: str | None = None,
) -> PeriodRead:
    period = periods_service.get_period(db, auth.company_id, period_id)
    periods_service.transition_period(
        db, period, target, actor=auth.user, reason=reason, request=request
    )
    db.commit()
    return PeriodRead.model_validate(period)


@router.post("/periods/{period_id}/open")
def open_period(
    period_id: int,
    request: Request,
    auth: AuthContext = permissions.require(permissions.ACCOUNTING_PERIODS_MANAGE),
    db: Session = Depends(get_db),
) -> PeriodRead:
    return _transition(db, auth, request, period_id, PeriodStatus.OPEN)


@router.post("/periods/{period_id}/close")
def close_period(
    period_id: int,
    request: Request,
    auth: AuthContext = permissions.require(permissions.ACCOUNTING_PERIODS_MANAGE),
    db: Session = Depends(get_db),
) -> PeriodRead:
    return _transition(db, auth, request, period_id, PeriodStatus.CLOSED)


@router.post("/periods/{period_id}/lock")
def lock_period(
    period_id: int,
    request: Request,
    auth: AuthContext = permissions.require(permissions.ACCOUNTING_PERIODS_MANAGE),
    db: Session = Depends(get_db),
) -> PeriodRead:
    return _transition(db, auth, request, period_id, PeriodStatus.LOCKED)


@router.post("/periods/{period_id}/reopen")
def reopen_period(
    period_id: int,
    payload: ReasonBody,
    request: Request,
    auth: AuthContext = permissions.require(
        permissions.ACCOUNTING_PERIODS_MANAGE, permissions.ACCOUNTING_PERIODS_REOPEN
    ),
    db: Session = Depends(get_db),
) -> PeriodRead:
    """closed → open, or locked → closed (audited)."""
    period = periods_service.get_period(db, auth.company_id, period_id)
    target = PeriodStatus.OPEN if period.status == PeriodStatus.CLOSED else PeriodStatus.CLOSED
    return _transition(db, auth, request, period_id, target, payload.reason)
