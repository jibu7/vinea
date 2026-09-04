"""Operator console: tenant list, suspend/activate and impersonate-with-audit."""

from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import NotFoundError
from app.db import platform_scope
from app.models.company import Company, CompanyStatus
from app.models.membership import CompanyMembership, MembershipStatus
from app.models.user import User
from app.services.audit import record_audit
from app.services.auth import IssuedSession, issue_session


def list_tenants(
    db: Session,
    *,
    cursor: int | None = None,
    limit: int = 50,
    status: CompanyStatus | None = None,
    search: str | None = None,
) -> list[Company]:
    query = select(Company).order_by(Company.id).limit(limit + 1)
    if cursor is not None:
        query = query.where(Company.id > cursor)
    if status is not None:
        query = query.where(Company.status == status)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(or_(Company.name.ilike(pattern), Company.tin.ilike(pattern)))
    return list(db.scalars(query))


def member_counts(db: Session, company_ids: list[int]) -> dict[int, int]:
    if not company_ids:
        return {}
    with platform_scope(db):
        rows = db.execute(
            select(CompanyMembership.company_id, CompanyMembership.id).where(
                CompanyMembership.company_id.in_(company_ids),
                CompanyMembership.status == MembershipStatus.ACTIVE,
            )
        ).all()
    counts: dict[int, int] = dict.fromkeys(company_ids, 0)
    for company_id, _ in rows:
        counts[company_id] += 1
    return counts


def get_tenant(db: Session, company_id: int) -> Company:
    company = db.get(Company, company_id)
    if company is None:
        raise NotFoundError("Company not found")
    return company


def set_tenant_status(
    db: Session,
    *,
    company: Company,
    status: CompanyStatus,
    actor: User,
    reason: str | None = None,
    request: Request | None = None,
) -> Company:
    before = {"status": company.status.value, "suspension_reason": company.suspension_reason}
    company.status = status
    if status == CompanyStatus.SUSPENDED:
        company.suspended_at = datetime.now(UTC)
        company.suspension_reason = reason
    else:
        company.suspended_at = None
        company.suspension_reason = None

    with platform_scope(db):
        record_audit(
            db,
            company_id=company.id,
            action=f"operator.tenant_{status.value}",
            entity="companies",
            entity_id=company.id,
            before=before,
            after={"status": status.value, "suspension_reason": company.suspension_reason},
            actor_user_id=actor.id,
            actor_email=actor.email,
            request=request,
        )
        db.flush()
    return company


def impersonate(
    db: Session, *, company: Company, actor: User, request: Request | None = None
) -> IssuedSession:
    """Issue a tenant session for a platform admin. Always audited — ADR-09 lists
    operator impersonation as a sensitive action."""
    with platform_scope(db):
        session = issue_session(
            db,
            user=actor,
            membership=None,
            company_id=company.id,
            impersonated_by=actor.id,
            request=request,
        )
        record_audit(
            db,
            company_id=company.id,
            action="operator.impersonate",
            entity="companies",
            entity_id=company.id,
            after={"actor_user_id": actor.id, "actor_email": actor.email},
            actor_user_id=actor.id,
            actor_email=actor.email,
            impersonated_by_user_id=actor.id,
            request=request,
        )
        db.flush()
    return session
