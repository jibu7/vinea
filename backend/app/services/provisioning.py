"""Tenant provisioning: signup → company → Rwanda seed pack → owner membership."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.config import settings
from app.core.errors import ConflictError
from app.core.permissions import OWNER_ROLE_NAME
from app.core.security import generate_opaque_token, hash_opaque_token, hash_password
from app.db import platform_scope, set_tenant
from app.models.company import Company, CompanyStatus
from app.models.membership import CompanyMembership, MembershipRole, MembershipStatus, Role
from app.models.user import User, UserToken, UserTokenPurpose
from app.services import email
from app.services.audit import record_audit
from app.services.seed_rwanda import COA_TEMPLATE, seed_company


@dataclass
class ProvisionedTenant:
    user: User
    company: Company
    membership: CompanyMembership


def normalize_email(value: str) -> str:
    return value.strip().lower()


def issue_email_verification(db: Session, user: User) -> str:
    token = generate_opaque_token()
    db.add(
        UserToken(
            user_id=user.id,
            purpose=UserTokenPurpose.EMAIL_VERIFICATION,
            token_hash=hash_opaque_token(token),
            expires_at=datetime.now(UTC) + timedelta(hours=settings.email_verification_ttl_hours),
        )
    )
    email.send_email_verification(to=user.email, token=token)
    return token


def provision_tenant(
    db: Session,
    *,
    company_name: str,
    full_name: str,
    email_address: str,
    password: str,
    tin: str | None = None,
    vat_registered: bool = False,
    fiscal_country: str = "RW",
    request: Request | None = None,
) -> ProvisionedTenant:
    address = normalize_email(email_address)

    with platform_scope(db):
        if db.scalar(select(User).where(User.email == address)) is not None:
            raise ConflictError(
                "An account with this email already exists — sign in and create the "
                "company from your account instead.",
                code="email_taken",
                field_errors={"email": ["already registered"]},
            )

        user = User(
            email=address,
            hashed_password=hash_password(password),
            full_name=full_name.strip(),
        )
        db.add(user)
        db.flush()

        company = Company(
            name=company_name.strip(),
            tin=tin,
            vat_registered=vat_registered,
            fiscal_country=fiscal_country,
            status=CompanyStatus.ACTIVE,
            coa_template=COA_TEMPLATE,
        )
        db.add(company)
        db.flush()

        set_tenant(db, company.id)
        roles = seed_company(db, company)
        owner_role = next(role for role in roles if role.name == OWNER_ROLE_NAME)

        membership = CompanyMembership(
            company_id=company.id,
            user_id=user.id,
            email=address,
            is_owner=True,
            status=MembershipStatus.ACTIVE,
            accepted_at=datetime.now(UTC),
        )
        db.add(membership)
        db.flush()
        db.add(
            MembershipRole(
                company_id=company.id, membership_id=membership.id, role_id=owner_role.id
            )
        )

        record_audit(
            db,
            company_id=company.id,
            action="company.provisioned",
            entity="companies",
            entity_id=company.id,
            after={"name": company.name, "fiscal_country": company.fiscal_country},
            actor_user_id=user.id,
            actor_email=user.email,
            request=request,
        )
        issue_email_verification(db, user)
        db.flush()

    return ProvisionedTenant(user=user, company=company, membership=membership)


def owner_role_for(db: Session, company_id: int) -> Role:
    return db.scalars(
        select(Role).where(Role.company_id == company_id, Role.name == OWNER_ROLE_NAME)
    ).one()
