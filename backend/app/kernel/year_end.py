"""Year-end close and its audited reversal (ADR-08).

`close_fiscal_year` posts the `PeriodClosed` event into the year's **last period** (which
must be open — the closing entry obeys the same period rule as everything else), then locks
every period and the year. `reopen_fiscal_year` reverses that entry and returns the periods
to `closed` (last one `open`) so adjustments can be made and the year closed again.
"""

from sqlalchemy.orm import Session
from starlette.requests import Request

from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.events import PeriodClosed, ReversalRequested
from app.kernel.periods import get_fiscal_year, periods_of, transition_period
from app.kernel.posting import gl_settings_for, post
from app.models.fiscal import FiscalYear, PeriodStatus
from app.models.user import User
from app.services.audit import record_audit


def close_fiscal_year(
    db: Session,
    company_id: int,
    fiscal_year_id: int,
    *,
    actor: User | None,
    request: Request | None = None,
) -> FiscalYear:
    year = get_fiscal_year(db, company_id, fiscal_year_id)
    if year.status == PeriodStatus.LOCKED:
        raise LedgerStateError(f"Fiscal year {year.name} is already closed", code="year_locked")
    settings = gl_settings_for(db, company_id)
    if settings.retained_earnings_account_id is None:
        raise PostingError(
            "Set the retained earnings account in GL settings before closing a year",
            code="retained_earnings_unset",
        )

    periods = periods_of(db, year)
    if not periods:
        raise LedgerStateError("Fiscal year has no periods", code="year_without_periods")
    *earlier, last = periods
    not_closed = [
        p.name for p in earlier if p.status not in (PeriodStatus.CLOSED, PeriodStatus.LOCKED)
    ]
    if not_closed:
        raise LedgerStateError(
            f"Close these periods first: {', '.join(not_closed)}", code="periods_not_closed"
        )
    if last.status == PeriodStatus.CLOSED:
        transition_period(
            db, last, PeriodStatus.OPEN, actor=actor, reason="year-end close", request=request
        )
    elif last.status != PeriodStatus.OPEN:
        raise LedgerStateError(
            f"Period {last.name} must be open (or closed) to receive the closing entry",
            code="last_period_not_open",
        )

    entry = post(
        db,
        PeriodClosed(
            fiscal_year_id=year.id,
            entry_date=year.end_date,
            description=f"Year-end close {year.name}",
        ),
        company_id=company_id,
        actor=actor,
    )
    year.closing_entry_id = entry.id if entry is not None else None
    for period in periods:
        period.status = PeriodStatus.LOCKED
    year.status = PeriodStatus.LOCKED
    record_audit(
        db,
        company_id=company_id,
        action="fiscal_year.closed",
        entity="fiscal_years",
        entity_id=year.id,
        before={"status": PeriodStatus.OPEN.value},
        after={
            "status": PeriodStatus.LOCKED.value,
            "closing_entry_id": year.closing_entry_id,
            "closing_entry_number": entry.number if entry is not None else None,
            "periods_locked": len(periods),
        },
        actor_user_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        request=request,
    )
    db.flush()
    return year


def reopen_fiscal_year(
    db: Session,
    company_id: int,
    fiscal_year_id: int,
    *,
    actor: User | None,
    reason: str,
    request: Request | None = None,
) -> FiscalYear:
    year = get_fiscal_year(db, company_id, fiscal_year_id)
    if year.status != PeriodStatus.LOCKED:
        raise LedgerStateError(f"Fiscal year {year.name} is not closed", code="year_not_locked")
    periods = periods_of(db, year)
    *earlier, last = periods
    for period in earlier:
        period.status = PeriodStatus.CLOSED
    last.status = PeriodStatus.OPEN
    db.flush()  # the reversal below must see the last period open

    reversed_number: str | None = None
    if year.closing_entry_id is not None:
        reversal = post(
            db,
            ReversalRequested(
                entry_id=year.closing_entry_id,
                reason=reason,
                entry_date=year.end_date,
                description=f"Reopen {year.name}: {reason}",
                allow_closing_entry=True,
            ),
            company_id=company_id,
            actor=actor,
        )
        reversed_number = reversal.number if reversal is not None else None
    before_entry = year.closing_entry_id
    year.closing_entry_id = None
    year.status = PeriodStatus.OPEN
    record_audit(
        db,
        company_id=company_id,
        action="fiscal_year.reopened",
        entity="fiscal_years",
        entity_id=year.id,
        before={"status": PeriodStatus.LOCKED.value, "closing_entry_id": before_entry},
        after={
            "status": PeriodStatus.OPEN.value,
            "reason": reason,
            "reversal_entry_number": reversed_number,
        },
        actor_user_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        request=request,
    )
    db.flush()
    return year
