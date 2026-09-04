from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext
from app.core import permissions
from app.db import get_db
from app.models.membership import MembershipRole
from app.schemas.auth import SessionResponse
from app.schemas.invitation import InvitationAccept, InvitationCreate, InvitationRead
from app.services import auth as auth_service
from app.services import invitations as invitation_service

router = APIRouter(prefix="/invitations", tags=["invitations"])


def _to_read(db: Session, membership_ids: list[int]) -> dict[int, list[int]]:
    rows = db.execute(
        select(MembershipRole.membership_id, MembershipRole.role_id).where(
            MembershipRole.membership_id.in_(membership_ids or [0])
        )
    ).all()
    grouped: dict[int, list[int]] = {membership_id: [] for membership_id in membership_ids}
    for membership_id, role_id in rows:
        grouped[membership_id].append(role_id)
    return grouped


@router.post("", status_code=status.HTTP_201_CREATED)
def create_invitation(
    payload: InvitationCreate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.USERS_CREATE),
    db: Session = Depends(get_db),
) -> InvitationRead:
    membership, _token = invitation_service.invite_member(
        db,
        company=auth.company,  # type: ignore[arg-type]
        email=payload.email,
        role_ids=payload.role_ids,
        invited_by=auth.user,
        request=request,
    )
    db.commit()
    return InvitationRead(
        id=membership.id,
        email=membership.email,
        status=membership.status,
        invited_at=membership.invited_at,
        invite_expires_at=membership.invite_expires_at,
        role_ids=payload.role_ids,
    )


@router.get("")
def list_invitations(
    auth: AuthContext = permissions.require(permissions.USERS_READ),
    db: Session = Depends(get_db),
) -> list[InvitationRead]:
    pending = invitation_service.list_invitations(db, auth.company_id)
    role_ids = _to_read(db, [membership.id for membership in pending])
    return [
        InvitationRead(
            id=membership.id,
            email=membership.email,
            status=membership.status,
            invited_at=membership.invited_at,
            invite_expires_at=membership.invite_expires_at,
            role_ids=role_ids.get(membership.id, []),
        )
        for membership in pending
    ]


@router.delete("/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invitation(
    membership_id: int,
    request: Request,
    auth: AuthContext = permissions.require(permissions.USERS_DELETE),
    db: Session = Depends(get_db),
) -> None:
    invitation_service.revoke_invitation(
        db,
        company_id=auth.company_id,
        membership_id=membership_id,
        actor=auth.user,
        request=request,
    )
    db.commit()


@router.post("/accept")
def accept_invitation(
    payload: InvitationAccept,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> SessionResponse:
    user, membership = invitation_service.accept_invitation(
        db,
        token=payload.token,
        full_name=payload.full_name,
        password=payload.password,
        request=request,
    )
    session = auth_service.issue_session(db, user=user, membership=membership, request=request)
    db.commit()
    auth_service.set_auth_cookies(response, session)
    return SessionResponse(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        company_id=session.company_id,
        membership_id=session.membership_id,
        access_expires_at=session.access_expires_at,
    )
