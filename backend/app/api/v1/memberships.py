from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import AuthContext
from app.core import permissions
from app.core.errors import NotFoundError
from app.db import get_db
from app.kernel.errors import LedgerStateError
from app.models.membership import CompanyMembership, MembershipRole, MembershipStatus, Role
from app.models.user import User
from app.schemas.membership import MemberRead, MemberRolesUpdate, RoleRead
from app.services.audit import record_audit

router = APIRouter(prefix="/memberships", tags=["memberships"])


@router.get("")
def list_memberships(
    auth: AuthContext = permissions.require(permissions.USERS_READ),
    db: Session = Depends(get_db),
) -> list[MemberRead]:
    statement = (
        select(CompanyMembership, User.full_name)
        .outerjoin(User, User.id == CompanyMembership.user_id)
        .options(selectinload(CompanyMembership.roles))
        .where(CompanyMembership.company_id == auth.company_id)
        .order_by(CompanyMembership.id)
    )
    rows = db.execute(statement).all()
    results: list[MemberRead] = []
    for membership, full_name in rows:
        results.append(
            MemberRead(
                id=membership.id,
                user_id=membership.user_id,
                full_name=full_name,
                email=membership.email,
                is_owner=membership.is_owner,
                status=membership.status.value,
                roles=[RoleRead.model_validate(r) for r in membership.roles],
                invited_at=membership.invited_at,
                accepted_at=membership.accepted_at,
            )
        )
    return results


@router.get("/roles")
def list_roles(
    auth: AuthContext = permissions.require(permissions.USERS_READ),
    db: Session = Depends(get_db),
) -> list[RoleRead]:
    statement = (
        select(Role)
        .where(Role.company_id == auth.company_id)
        .order_by(Role.id)
    )
    roles = db.scalars(statement).all()
    return [RoleRead.model_validate(r) for r in roles]


@router.put("/{membership_id}/roles")
def update_member_roles(
    membership_id: int,
    payload: MemberRolesUpdate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.USERS_MANAGE_ROLES),
    db: Session = Depends(get_db),
) -> MemberRead:
    membership = db.get(CompanyMembership, membership_id)
    if membership is None or membership.company_id != auth.company_id:
        raise NotFoundError("Membership not found")

    roles = list(
        db.scalars(
            select(Role).where(
                Role.company_id == auth.company_id, Role.id.in_(payload.role_ids or [0])
            )
        )
    )
    if len(roles) != len(set(payload.role_ids)):
        raise NotFoundError("One or more roles do not exist in this company")

    before_role_ids = [r.id for r in membership.roles]
    db.execute(
        delete(MembershipRole).where(
            MembershipRole.company_id == auth.company_id,
            MembershipRole.membership_id == membership.id,
        )
    )
    for role in roles:
        db.add(
            MembershipRole(
                company_id=auth.company_id,
                membership_id=membership.id,
                role_id=role.id,
            )
        )
    db.flush()

    record_audit(
        db,
        company_id=auth.company_id,
        action="membership.roles_updated",
        entity="company_memberships",
        entity_id=membership.id,
        before={"role_ids": before_role_ids},
        after={"role_ids": payload.role_ids},
        actor_user_id=auth.user.id,
        actor_email=auth.user.email,
        request=request,
    )
    db.commit()

    # Re-read
    db.refresh(membership)
    user = db.get(User, membership.user_id) if membership.user_id else None
    return MemberRead(
        id=membership.id,
        user_id=membership.user_id,
        full_name=user.full_name if user else None,
        email=membership.email,
        is_owner=membership.is_owner,
        status=membership.status.value,
        roles=[RoleRead.model_validate(r) for r in membership.roles],
        invited_at=membership.invited_at,
        accepted_at=membership.accepted_at,
    )


@router.post("/{membership_id}/deactivate")
def deactivate_member(
    membership_id: int,
    request: Request,
    auth: AuthContext = permissions.require(permissions.USERS_UPDATE),
    db: Session = Depends(get_db),
) -> MemberRead:
    membership = db.get(CompanyMembership, membership_id)
    if membership is None or membership.company_id != auth.company_id:
        raise NotFoundError("Membership not found")
    if membership.is_owner:
        raise LedgerStateError(
            "The company owner cannot be deactivated", code="cannot_deactivate_owner"
        )

    before_status = membership.status.value
    membership.status = MembershipStatus.SUSPENDED
    db.flush()

    record_audit(
        db,
        company_id=auth.company_id,
        action="membership.deactivated",
        entity="company_memberships",
        entity_id=membership.id,
        before={"status": before_status},
        after={"status": membership.status.value},
        actor_user_id=auth.user.id,
        actor_email=auth.user.email,
        request=request,
    )
    db.commit()

    user = db.get(User, membership.user_id) if membership.user_id else None
    return MemberRead(
        id=membership.id,
        user_id=membership.user_id,
        full_name=user.full_name if user else None,
        email=membership.email,
        is_owner=membership.is_owner,
        status=membership.status.value,
        roles=[RoleRead.model_validate(r) for r in membership.roles],
        invited_at=membership.invited_at,
        accepted_at=membership.accepted_at,
    )


@router.post("/{membership_id}/activate")
def activate_member(
    membership_id: int,
    request: Request,
    auth: AuthContext = permissions.require(permissions.USERS_UPDATE),
    db: Session = Depends(get_db),
) -> MemberRead:
    membership = db.get(CompanyMembership, membership_id)
    if membership is None or membership.company_id != auth.company_id:
        raise NotFoundError("Membership not found")

    before_status = membership.status.value
    membership.status = MembershipStatus.ACTIVE
    db.flush()

    record_audit(
        db,
        company_id=auth.company_id,
        action="membership.activated",
        entity="company_memberships",
        entity_id=membership.id,
        before={"status": before_status},
        after={"status": membership.status.value},
        actor_user_id=auth.user.id,
        actor_email=auth.user.email,
        request=request,
    )
    db.commit()

    user = db.get(User, membership.user_id) if membership.user_id else None
    return MemberRead(
        id=membership.id,
        user_id=membership.user_id,
        full_name=user.full_name if user else None,
        email=membership.email,
        is_owner=membership.is_owner,
        status=membership.status.value,
        roles=[RoleRead.model_validate(r) for r in membership.roles],
        invited_at=membership.invited_at,
        accepted_at=membership.accepted_at,
    )
