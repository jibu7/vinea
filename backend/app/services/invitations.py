"""Invitation flow (ADR-02): owner invites by email → pending membership → accept."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.config import settings
from app.core.errors import AuthenticationError, ConflictError, NotFoundError
from app.core.security import generate_opaque_token, hash_opaque_token, hash_password
from app.db import platform_scope, set_tenant
from app.models.company import Company
from app.models.membership import CompanyMembership, MembershipRole, MembershipStatus, Role
from app.models.user import User
from app.services import email as email_service
from app.services.audit import record_audit
from app.services.auth import assert_company_usable
from app.services.provisioning import normalize_email


def _load_roles(db: Session, company_id: int, role_ids: list[int]) -> list[Role]:
    roles = list(
        db.scalars(select(Role).where(Role.company_id == company_id, Role.id.in_(role_ids)))
    )
    if len(roles) != len(set(role_ids)):
        raise NotFoundError("One or more roles do not exist in this company")
    return roles


def invite_member(
    db: Session,
    *,
    company: Company,
    email: str,
    role_ids: list[int],
    invited_by: User,
    request: Request | None = None,
) -> tuple[CompanyMembership, str]:
    address = normalize_email(email)
    existing = db.scalar(
        select(CompanyMembership).where(
            CompanyMembership.company_id == company.id, CompanyMembership.email == address
        )
    )
    if existing is not None:
        raise ConflictError(
            "This email already has a membership or a pending invitation",
            code="membership_exists",
            field_errors={"email": ["already invited"]},
        )

    roles = _load_roles(db, company.id, role_ids)
    token = generate_opaque_token()
    now = datetime.now(UTC)
    membership = CompanyMembership(
        company_id=company.id,
        user_id=None,
        email=address,
        is_owner=False,
        status=MembershipStatus.PENDING,
        invite_token_hash=hash_opaque_token(token),
        invite_expires_at=now + timedelta(days=settings.invitation_ttl_days),
        invited_by_user_id=invited_by.id,
        invited_at=now,
    )
    db.add(membership)
    db.flush()
    for role in roles:
        db.add(
            MembershipRole(company_id=company.id, membership_id=membership.id, role_id=role.id)
        )

    record_audit(
        db,
        company_id=company.id,
        action="membership.invited",
        entity="company_memberships",
        entity_id=membership.id,
        after={"email": address, "role_ids": [role.id for role in roles]},
        actor_user_id=invited_by.id,
        actor_email=invited_by.email,
        request=request,
    )
    email_service.send_invitation_email(to=address, company_name=company.name, token=token)
    return membership, token


def list_invitations(db: Session, company_id: int) -> list[CompanyMembership]:
    return list(
        db.scalars(
            select(CompanyMembership)
            .where(
                CompanyMembership.company_id == company_id,
                CompanyMembership.status == MembershipStatus.PENDING,
            )
            .order_by(CompanyMembership.id)
        )
    )


def revoke_invitation(
    db: Session, *, company_id: int, membership_id: int, actor: User, request: Request | None = None
) -> None:
    membership = db.scalar(
        select(CompanyMembership).where(
            CompanyMembership.company_id == company_id,
            CompanyMembership.id == membership_id,
            CompanyMembership.status == MembershipStatus.PENDING,
        )
    )
    if membership is None:
        raise NotFoundError("Invitation not found")
    record_audit(
        db,
        company_id=company_id,
        action="membership.invitation_revoked",
        entity="company_memberships",
        entity_id=membership.id,
        before={"email": membership.email},
        actor_user_id=actor.id,
        actor_email=actor.email,
        request=request,
    )
    db.delete(membership)


def accept_invitation(
    db: Session,
    *,
    token: str,
    full_name: str | None = None,
    password: str | None = None,
    request: Request | None = None,
) -> tuple[User, CompanyMembership]:
    """Public endpoint path — runs in platform scope because the invitee has no session
    and therefore no tenant context yet."""
    with platform_scope(db):
        membership = db.scalar(
            select(CompanyMembership).where(
                CompanyMembership.invite_token_hash == hash_opaque_token(token),
                CompanyMembership.status == MembershipStatus.PENDING,
            )
        )
        if membership is None:
            raise AuthenticationError("Invalid or already used invitation", code="invalid_token")
        expires_at = membership.invite_expires_at
        if expires_at is None or expires_at <= datetime.now(UTC):
            raise AuthenticationError("Invitation expired", code="token_expired")

        company = db.get(Company, membership.company_id)
        if company is None:
            raise NotFoundError("Company not found")
        assert_company_usable(company)

        user = db.scalar(select(User).where(User.email == membership.email))
        if user is None:
            if not password or not full_name:
                raise ConflictError(
                    "New users must supply a full name and password to accept",
                    code="registration_required",
                )
            user = User(
                email=membership.email,
                full_name=full_name.strip(),
                hashed_password=hash_password(password),
                email_verified_at=datetime.now(UTC),
            )
            db.add(user)
            db.flush()

        duplicate = db.scalar(
            select(CompanyMembership).where(
                CompanyMembership.company_id == membership.company_id,
                CompanyMembership.user_id == user.id,
            )
        )
        if duplicate is not None:
            raise ConflictError(
                "This user already belongs to the company", code="membership_exists"
            )

        membership.user_id = user.id
        membership.status = MembershipStatus.ACTIVE
        membership.accepted_at = datetime.now(UTC)
        membership.invite_token_hash = None
        membership.invite_expires_at = None

        set_tenant(db, company.id)
        record_audit(
            db,
            company_id=company.id,
            action="membership.invitation_accepted",
            entity="company_memberships",
            entity_id=membership.id,
            after={"user_id": user.id, "email": user.email},
            actor_user_id=user.id,
            actor_email=user.email,
            request=request,
        )
        db.flush()
    return user, membership
