"""Accounting periods (ADR-08): lookup, posting-time enforcement and status transitions.

    future ──open──▶ open ──close──▶ closed ──lock──▶ locked
                      ▲               │  ▲              │
                      └───reopen──────┘  └────unlock────┘

Reopen/unlock are audited and need `accounting_periods:reopen`. Once a fiscal year is
locked by `close_fiscal_year`, its periods can only be reopened through
`reopen_fiscal_year` (which reverses the closing entry first).
"""

import calendar
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import NotFoundError
from app.kernel.errors import LedgerStateError, PostingError
from app.models.fiscal import AccountingPeriod, FiscalYear, PeriodStatus
from app.models.user import User
from app.services.audit import record_audit

TRANSITIONS: dict[tuple[PeriodStatus, PeriodStatus], str] = {
    (PeriodStatus.FUTURE, PeriodStatus.OPEN): "period.opened",
    (PeriodStatus.OPEN, PeriodStatus.CLOSED): "period.closed",
    (PeriodStatus.CLOSED, PeriodStatus.OPEN): "period.reopened",
    (PeriodStatus.CLOSED, PeriodStatus.LOCKED): "period.locked",
    (PeriodStatus.LOCKED, PeriodStatus.CLOSED): "period.unlocked",
}
REOPENING_TRANSITIONS = frozenset(
    {(PeriodStatus.CLOSED, PeriodStatus.OPEN), (PeriodStatus.LOCKED, PeriodStatus.CLOSED)}
)


def find_period(db: Session, company_id: int, on_date: date) -> AccountingPeriod:
    period = db.scalar(
        select(AccountingPeriod).where(
            AccountingPeriod.company_id == company_id,
            AccountingPeriod.start_date <= on_date,
            AccountingPeriod.end_date >= on_date,
        )
    )
    if period is None:
        raise PostingError(
            f"No accounting period covers {on_date.isoformat()}",
            code="no_accounting_period",
            field_errors={"entry_date": ["no accounting period covers this date"]},
        )
    return period


def lock_period_for_posting(db: Session, company_id: int, on_date: date) -> AccountingPeriod:
    """Find the period and take a `FOR SHARE` lock on it, so a concurrent close (which needs
    the exclusive row lock to UPDATE the status) waits for this posting to commit."""
    period = db.scalar(
        select(AccountingPeriod)
        .where(
            AccountingPeriod.company_id == company_id,
            AccountingPeriod.start_date <= on_date,
            AccountingPeriod.end_date >= on_date,
        )
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if period is None:
        raise PostingError(
            f"No accounting period covers {on_date.isoformat()}",
            code="no_accounting_period",
            field_errors={"entry_date": ["no accounting period covers this date"]},
        )
    assert_period_open(period)
    return period


def assert_period_open(period: AccountingPeriod) -> None:
    if period.status != PeriodStatus.OPEN:
        raise PostingError(
            f"Accounting period {period.name} is {period.status.value}; posting requires an "
            "open period",
            code="period_not_open",
            field_errors={"entry_date": [f"period is {period.status.value}"]},
        )


def get_period(db: Session, company_id: int, period_id: int) -> AccountingPeriod:
    period = db.get(AccountingPeriod, period_id)
    if period is None or period.company_id != company_id:
        raise NotFoundError("Accounting period not found")
    return period


def get_fiscal_year(db: Session, company_id: int, fiscal_year_id: int) -> FiscalYear:
    year = db.get(FiscalYear, fiscal_year_id)
    if year is None or year.company_id != company_id:
        raise NotFoundError("Fiscal year not found")
    return year


def periods_of(db: Session, fiscal_year: FiscalYear) -> list[AccountingPeriod]:
    return list(
        db.scalars(
            select(AccountingPeriod)
            .where(AccountingPeriod.fiscal_year_id == fiscal_year.id)
            .order_by(AccountingPeriod.period_no)
        )
    )


def transition_period(
    db: Session,
    period: AccountingPeriod,
    target: PeriodStatus,
    *,
    actor: User | None,
    reason: str | None = None,
    request: Request | None = None,
    allow_locked_year: bool = False,
) -> AccountingPeriod:
    key = (period.status, target)
    action = TRANSITIONS.get(key)
    if action is None:
        raise LedgerStateError(
            f"Cannot move period {period.name} from {period.status.value} to {target.value}",
            code="invalid_period_transition",
        )
    year = db.get(FiscalYear, period.fiscal_year_id)
    if (
        year is not None
        and year.status == PeriodStatus.LOCKED
        and key in REOPENING_TRANSITIONS
        and not allow_locked_year
    ):
        raise LedgerStateError(
            f"Fiscal year {year.name} is locked; reopen the year first",
            code="fiscal_year_locked",
        )
    before = period.status.value
    period.status = target
    record_audit(
        db,
        company_id=period.company_id,
        action=action,
        entity="accounting_periods",
        entity_id=period.id,
        before={"status": before},
        after={"status": target.value, "reason": reason},
        actor_user_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        request=request,
    )
    db.flush()
    return period


def _month_periods(start_date: date, end_date: date) -> list[tuple[date, date]]:
    spans: list[tuple[date, date]] = []
    cursor = start_date
    while cursor <= end_date:
        last_day = date(
            cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1]
        )
        span_end = min(last_day, end_date)
        spans.append((cursor, span_end))
        cursor = span_end + timedelta(days=1)
    return spans


def create_fiscal_year(
    db: Session,
    company_id: int,
    *,
    name: str,
    start_date: date,
    end_date: date,
    open_through: date | None = None,
) -> FiscalYear:
    """One period per calendar month (partial first/last months allowed). Periods starting
    on or before `open_through` are `open`, the rest `future`."""
    if end_date <= start_date:
        raise PostingError("Fiscal year must end after it starts", code="invalid_fiscal_year")
    overlapping = db.scalar(
        select(FiscalYear.id).where(
            FiscalYear.company_id == company_id,
            FiscalYear.start_date <= end_date,
            FiscalYear.end_date >= start_date,
        )
    )
    if overlapping is not None:
        raise LedgerStateError("Fiscal year overlaps an existing one", code="fiscal_year_overlap")
    year = FiscalYear(
        company_id=company_id,
        name=name,
        start_date=start_date,
        end_date=end_date,
        status=PeriodStatus.OPEN,
    )
    db.add(year)
    db.flush()
    for period_no, (span_start, span_end) in enumerate(_month_periods(start_date, end_date), 1):
        is_open = open_through is not None and span_start <= open_through
        db.add(
            AccountingPeriod(
                company_id=company_id,
                fiscal_year_id=year.id,
                period_no=period_no,
                name=f"{calendar.month_abbr[span_start.month]} {span_start.year}",
                start_date=span_start,
                end_date=span_end,
                status=PeriodStatus.OPEN if is_open else PeriodStatus.FUTURE,
            )
        )
    db.flush()
    return year
