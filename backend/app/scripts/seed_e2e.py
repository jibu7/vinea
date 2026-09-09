"""Deterministic fixture data for the frontend Playwright e2e suite (P3 step 8).

Provisions two tenants through the real signup path (`provision_tenant`, same as the
`/auth/signup` route), then adds two pieces of state the HTTP API has no way to produce
without an email round-trip: a second, already-active membership for the secondary owner
(the invite-accept flow needs a mailed token) and one closed accounting period (closing a
period for real goes through year-end close, which needs prior periods closed too — for a
fixture we just need *a* closed period to exercise the `period_closed` error path).

The cross-company membership goes on the *secondary* user, not the primary one, on purpose:
`auth_service.select_membership()` only auto-selects a company on login when the user has
exactly one membership, so PRIMARY_EMAIL — used by every spec except the switch-company one —
needs to stay single-membership or login leaves every one of those tests with no active
company and everything company-scoped (e.g. `/gl/accounts`) comes back empty.

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

# A read-only member of the primary company, holding the seeded "Clerk" role: gl/ar/ap
# `*_reports_view` and nothing that can write. Every screen that gates its actions on a
# setup permission needs one of these to prove the gate is real rather than decorative
# (P4 step 6's AR/AP defaults, and P4 step 9's credit-limit override).
READONLY_EMAIL = "e2e.readonly@vinea.example"
READONLY_ROLE_NAME = "Clerk"

# One supplier, so the Suppliers master is not an empty table in screenshots or in the AP
# specs. Customers are created by the specs themselves (they assert on creation); nothing
# asserts on creating a supplier, so the fixture provides one.
SUPPLIER_CODE = "E2ESUP001"
SUPPLIER_NAME = "Musanze Packaging Ltd"


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


def _ensure_member_with_role(
    db, *, company: Company, email: str, full_name: str, role_name: str
) -> User:
    """An already-active, non-owner membership carrying exactly one seeded role. Created
    directly rather than through the invite flow, which needs a mailed token — the same
    reason `_ensure_cross_company_membership` exists."""
    from app.core.security import hash_password

    with platform_scope(db):
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(
                email=email,
                hashed_password=hash_password(PASSWORD),
                full_name=full_name,
                email_verified_at=datetime.now(UTC),
            )
            db.add(user)
            db.flush()

        existing = db.scalar(
            select(CompanyMembership).where(
                CompanyMembership.company_id == company.id, CompanyMembership.user_id == user.id
            )
        )
        if existing is not None:
            db.commit()
            return user

        role = db.scalar(
            select(Role).where(Role.company_id == company.id, Role.name == role_name)
        )
        if role is None:
            raise RuntimeError(f"{role_name!r} role missing for company {company.id}")

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
        return user


def _ensure_partners(db, *, company: Company, actor: User) -> str | None:
    """Through the real service with a real actor — never a raw insert, so the seeded row is
    the same shape the application would have written."""
    from app.db import set_tenant
    from app.models.partner import PartnerRole, TaxMode
    from app.subledger import masters

    set_tenant(db, company.id)
    existing = masters.list_partners(db, company.id, role=PartnerRole.AP, include_inactive=True)
    supplier = next((p for p in existing if p.supplier_code == SUPPLIER_CODE), None)
    if supplier is None:
        supplier = masters.create_partner(
            db,
            company.id,
            masters.PartnerInput(
                name=SUPPLIER_NAME,
                supplier_code=SUPPLIER_CODE,
                tin="102345678",
                email="ap@musanze-packaging.example",
                phone="+250788000111",
            ),
            actor=actor,
        )
        terms = {row.code: row for row in masters.list_payment_terms(db, company.id)}
        masters.upsert_role_settings(
            db,
            company.id,
            supplier,
            PartnerRole.AP,
            masters.RoleSettingsInput(
                payment_terms_id=terms["NET30"].id if "NET30" in terms else None,
                tax_mode=TaxMode.EXCLUSIVE,
            ),
            actor=actor,
        )
        db.commit()
    return supplier.supplier_code


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
        secondary_user, secondary_company = _get_or_create_tenant(
            db,
            company_name=SECONDARY_COMPANY,
            email=SECONDARY_EMAIL,
            full_name="E2E Secondary Owner",
        )
        _ensure_cross_company_membership(db, user=secondary_user, company=primary_company)
        _ensure_member_with_role(
            db,
            company=primary_company,
            email=READONLY_EMAIL,
            full_name="E2E Read Only",
            role_name=READONLY_ROLE_NAME,
        )
        supplier_code = _ensure_partners(db, company=primary_company, actor=primary_user)
        closed_period = _ensure_closed_period(db, company=primary_company)

        print(
            json.dumps(
                {
                    "primary_email": PRIMARY_EMAIL,
                    "primary_company": PRIMARY_COMPANY,
                    "secondary_email": SECONDARY_EMAIL,
                    "secondary_company": SECONDARY_COMPANY,
                    "readonly_email": READONLY_EMAIL,
                    "readonly_role": READONLY_ROLE_NAME,
                    "supplier_code": supplier_code,
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
