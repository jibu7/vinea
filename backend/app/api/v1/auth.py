from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_auth_context
from app.core.errors import AuthenticationError
from app.db import get_db, platform_scope
from app.models.company import Company
from app.schemas.auth import (
    CompanySummary,
    EmailVerificationConfirm,
    LoginRequest,
    MembershipSummary,
    MeResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    SessionResponse,
    SignupRequest,
    SwitchCompanyRequest,
)
from app.services import auth as auth_service
from app.services import provisioning
from app.services.auth import IssuedSession

router = APIRouter(prefix="/auth", tags=["auth"])


def _session_response(response: Response, session: IssuedSession) -> SessionResponse:
    auth_service.set_auth_cookies(response, session)
    return SessionResponse(
        user_id=session.user.id,
        email=session.user.email,
        full_name=session.user.full_name,
        company_id=session.company_id,
        membership_id=session.membership_id,
        access_expires_at=session.access_expires_at,
    )


@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(
    payload: SignupRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> SessionResponse:
    tenant = provisioning.provision_tenant(
        db,
        company_name=payload.company_name,
        full_name=payload.full_name,
        email_address=payload.email,
        password=payload.password,
        tin=payload.tin,
        vat_registered=payload.vat_registered,
        request=request,
    )
    session = auth_service.issue_session(
        db, user=tenant.user, membership=tenant.membership, request=request
    )
    db.commit()
    return _session_response(response, session)


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> SessionResponse:
    user = auth_service.authenticate(db, email=payload.email, password=payload.password)
    membership = auth_service.select_membership(
        db, user_id=user.id, company_id=payload.company_id
    )
    if membership is not None:
        auth_service.assert_company_usable(auth_service.load_company(db, membership.company_id))
    session = auth_service.issue_session(db, user=user, membership=membership, request=request)
    user.last_login_at = datetime.now(UTC)
    db.commit()
    return _session_response(response, session)


@router.post("/refresh")
def refresh(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> SessionResponse:
    cookie = request.cookies.get(auth_service.REFRESH_COOKIE)
    if not cookie:
        raise AuthenticationError("Missing refresh token", code="invalid_token")
    session = auth_service.rotate_session(db, refresh_token=cookie, request=request)
    db.commit()
    return _session_response(response, session)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> None:
    auth_service.revoke_session(db, refresh_token=request.cookies.get(auth_service.REFRESH_COOKIE))
    db.commit()
    auth_service.clear_auth_cookies(response)


@router.post("/switch-company")
def switch_company(
    payload: SwitchCompanyRequest,
    request: Request,
    response: Response,
    auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> SessionResponse:
    membership = auth_service.select_membership(
        db, user_id=auth.user.id, company_id=payload.company_id
    )
    if membership is None:
        raise AuthenticationError("No active membership for this company", code="no_membership")
    auth_service.assert_company_usable(auth_service.load_company(db, membership.company_id))
    auth_service.revoke_session(db, refresh_token=request.cookies.get(auth_service.REFRESH_COOKIE))
    session = auth_service.issue_session(
        db, user=auth.user, membership=membership, request=request
    )
    db.commit()
    return _session_response(response, session)


@router.get("/me")
def me(auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)) -> MeResponse:
    memberships = auth_service.active_memberships(db, auth.user.id)
    with platform_scope(db):
        names = dict(
            db.execute(
                select(Company.id, Company.name).where(
                    Company.id.in_([m.company_id for m in memberships] or [0])
                )
            ).all()
        )
    return MeResponse(
        user_id=auth.user.id,
        email=auth.user.email,
        full_name=auth.user.full_name,
        is_platform_admin=auth.user.is_platform_admin,
        is_email_verified=auth.user.is_email_verified,
        company=None if auth.company is None else CompanySummary.model_validate(auth.company),
        memberships=[
            MembershipSummary(
                id=membership.id,
                company_id=membership.company_id,
                company_name=names.get(membership.company_id, ""),
                is_owner=membership.is_owner,
            )
            for membership in memberships
        ],
        permissions=sorted(auth.permissions),
        impersonated_by=auth.impersonated_by,
    )


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(
    payload: PasswordResetRequest, db: Session = Depends(get_db)
) -> dict[str, str]:
    auth_service.request_password_reset(db, email=payload.email)
    db.commit()
    return {"status": "accepted"}


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_password_reset(
    payload: PasswordResetConfirm, response: Response, db: Session = Depends(get_db)
) -> None:
    auth_service.confirm_password_reset(
        db, token=payload.token, new_password=payload.new_password
    )
    db.commit()
    auth_service.clear_auth_cookies(response)


@router.post("/email-verification/request", status_code=status.HTTP_202_ACCEPTED)
def request_email_verification(
    auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> dict[str, str]:
    if auth.user.is_email_verified:
        return {"status": "already_verified"}
    provisioning.issue_email_verification(db, auth.user)
    db.commit()
    return {"status": "accepted"}


@router.post("/email-verification/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_email_verification(
    payload: EmailVerificationConfirm, db: Session = Depends(get_db)
) -> None:
    auth_service.confirm_email_verification(db, token=payload.token)
    db.commit()
