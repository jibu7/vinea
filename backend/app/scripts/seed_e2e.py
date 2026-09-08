"""Deterministic fixture data for the frontend Playwright e2e suite (P3 step 8).

Provisions two tenants through the real signup path (`provision_tenant`, same as the
`/auth/signup` route), then adds two pieces of state the HTTP API has no way to produce
without an email round-trip: a second, already-active membership for the primary owner
(the invite-accept flow needs a mailed token) and one closed accounting period (closing a
period for real goes through year-end close, which needs prior periods closed too — for a
fixture we just need *a* closed period to exercise the `period_closed` error path).

Idempotent: safe to run against a database that already has these fixtures — everything is
looked up by its fixed email/name first and only created if missing. Run with
`uv run python -m app.scripts.seed_e2e`.
"""

import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.permissions import OWNER_ROLE_NAME
from app.db import SessionLocal, platform_scope
from app.models.company import Company
from app.models.fiscal import AccountingPeriod, PeriodStatus
from app.models.membership import CompanyMembership, MembershipRole, MembershipStatus, Role
from app.models.user import User
from app.services.provisioning import ProvisionedTenant, provision_tenant

PASSWORD = "E2E-Sup3rSecret!1"

# `.example` (RFC 2606) — `email-validator` (backing Pydantic's EmailStr on the login/signup
# routes) explicitly rejects `.test`/`.invalid`/`.localhost` as reserved, but allows `.example`.
PRIMARY_EMAIL = "e2e.primary@vinea.example"
PRIMARY_COMPANY = "Rugari Wines E2E"

SECONDARY_EMAIL = "e2e.secondary@vinea.example"
SECONDARY_COMPANY = "Kivu Traders E2E"


def _existing_tenant(db, *, email: str) -> tuple[User, Company] | None:
    with platform_scope(db):
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            return None
        company = db.scalar(
            select(Company)
            .join(CompanyMembership, CompanyMembership.company_id == Company.id)
            .where(CompanyMembership.user_id == user.id, CompanyMembership.is_owner.is_(True))
        )
        if company is None:
            raise RuntimeError(f"user {email} exists but owns no company — inconsistent fixture")
        return user, company


def _get_or_create_tenant(
    db, *, company_name: str, email: str, full_name: str
) -> tuple[User, Company]:
    existing = _existing_tenant(db, email=email)
    if existing is not None:
        return existing
    tenant: ProvisionedTenant = provision_tenant(
        db,
        company_name=company_name,
        full_name=full_name,
        email_address=email,
        password=PASSWORD,
    )
    db.commit()
    return tenant.user, tenant.company


def _ensure_cross_company_membership(db, *, user: User, company: Company) -> None:
    """Give `user` a second, already-active membership in `company` with full access —
    standing in for an accepted invitation so the switch-company e2e flow has somewhere
    real to switch to."""
    with platform_scope(db):
        existing = db.scalar(
            select(CompanyMembership).where(
                CompanyMembership.company_id == company.id, CompanyMembership.user_id == user.id
            )
        )
        if existing is not None:
            return

        role = db.scalar(
            select(Role).where(Role.company_id == company.id, Role.name == OWNER_ROLE_NAME)
        )
        if role is None:
            raise RuntimeError(f"{OWNER_ROLE_NAME!r} role missing for company {company.id}")

        membership = CompanyMembership(
            company_id=company.id,
            user_id=user.id,
            email=user.email,
            is_owner=False,
            status=MembershipStatus.ACTIVE,
            accepted_at=datetime.now(UTC),
        )
        db.add(membership)
        db.flush()
        db.add(MembershipRole(company_id=company.id, membership_id=membership.id, role_id=role.id))
        db.commit()


def _ensure_closed_period(db, *, company: Company) -> str | None:
    with platform_scope(db):
        period = db.scalar(
            select(AccountingPeriod)
            .where(AccountingPeriod.company_id == company.id)
            .order_by(AccountingPeriod.period_no)
            .limit(1)
        )
        if period is None:
            return None
        if period.status != PeriodStatus.CLOSED:
            period.status = PeriodStatus.CLOSED
            db.commit()
        return period.name


def main() -> None:
    db = SessionLocal()
    try:
        primary_user, primary_company = _get_or_create_tenant(
            db, company_name=PRIMARY_COMPANY, email=PRIMARY_EMAIL, full_name="E2E Primary Owner"
        )
        _secondary_user, secondary_company = _get_or_create_tenant(
            db,
            company_name=SECONDARY_COMPANY,
            email=SECONDARY_EMAIL,
            full_name="E2E Secondary Owner",
        )
        _ensure_cross_company_membership(db, user=primary_user, company=secondary_company)
        closed_period = _ensure_closed_period(db, company=primary_company)

        print(
            json.dumps(
                {
                    "primary_email": PRIMARY_EMAIL,
                    "primary_company": PRIMARY_COMPANY,
                    "secondary_email": SECONDARY_EMAIL,
                    "secondary_company": SECONDARY_COMPANY,
                    "password": PASSWORD,
                    "closed_period": closed_period,
                },
                indent=2,
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
