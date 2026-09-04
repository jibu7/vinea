"""Authentication per ADR-03: httpOnly cookies, rotating refresh tokens, no localStorage."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings
from app.core.errors import AuthenticationError, ConflictError, NotFoundError
from app.core.permissions import ALL_PERMISSIONS
from app.core.security import (
    ACCESS_TOKEN_TYPE,
    REFRESH_TOKEN_TYPE,
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_opaque_token,
    hash_opaque_token,
    hash_password,
    verify_password,
)
from app.db import platform_scope
from app.models.company import Company, CompanyStatus
from app.models.membership import CompanyMembership, MembershipStatus
from app.models.user import RefreshToken, User, UserToken, UserTokenPurpose
from app.services import email as email_service
from app.services.provisioning import normalize_email

ACCESS_COOKIE = "vinea_access"
REFRESH_COOKIE = "vinea_refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth"


@dataclass
class IssuedSession:
    user: User
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime
    company_id: int | None
    membership_id: int | None
    refresh_record: RefreshToken
    impersonated_by: int | None = None


def permissions_for(membership: CompanyMembership) -> set[str]:
    """Owners hold every permission; everyone else gets the union of their roles."""
    if membership.is_owner:
        return set(ALL_PERMISSIONS)
    granted: set[str] = set()
    for role in membership.roles:
        granted.update(role.permissions or [])
    return granted


def authenticate(db: Session, *, email: str, password: str) -> User:
    address = normalize_email(email)
    user = db.scalar(select(User).where(User.email == address))
    if user is None or not verify_password(user.hashed_password, password):
        raise AuthenticationError("Invalid email or password", code="invalid_credentials")
    if not user.is_active:
        raise AuthenticationError("This account is disabled", code="account_disabled")
    return user


def active_memberships(db: Session, user_id: int) -> list[CompanyMembership]:
    with platform_scope(db):
        return list(
            db.scalars(
                select(CompanyMembership)
                .where(
                    CompanyMembership.user_id == user_id,
                    CompanyMembership.status == MembershipStatus.ACTIVE,
                )
                .order_by(CompanyMembership.company_id)
            )
        )


def select_membership(
    db: Session, *, user_id: int, company_id: int | None
) -> CompanyMembership | None:
    """Resolve the membership for a session; auto-select when the user has exactly one."""
    memberships = active_memberships(db, user_id)
    if company_id is None:
        return memberships[0] if len(memberships) == 1 else None
    for membership in memberships:
        if membership.company_id == company_id:
            return membership
    raise AuthenticationError("No active membership for this company", code="no_membership")


def load_company(db: Session, company_id: int) -> Company:
    with platform_scope(db):
        company = db.get(Company, company_id)
    if company is None:
        raise NotFoundError("Company not found")
    return company


def load_user(db: Session, user_id: int) -> User | None:
    with platform_scope(db):
        return db.get(User, user_id)


def load_membership(db: Session, membership_id: int) -> CompanyMembership | None:
    with platform_scope(db):
        return db.get(CompanyMembership, membership_id)


def company_names(db: Session, company_ids: list[int]) -> dict[int, str]:
    if not company_ids:
        return {}
    with platform_scope(db):
        rows = db.execute(
            select(Company.id, Company.name).where(Company.id.in_(company_ids))
        ).all()
    return dict(rows)


def assert_company_usable(company: Company) -> None:
    if company.status != CompanyStatus.ACTIVE:
        raise AuthenticationError(
            f"Company access is {company.status.value}", code="company_suspended"
        )


def issue_session(
    db: Session,
    *,
    user: User,
    membership: CompanyMembership | None,
    company_id: int | None = None,
    impersonated_by: int | None = None,
    request: Request | None = None,
) -> IssuedSession:
    target_company_id = company_id if membership is None else membership.company_id
    access_token, access_expires_at = create_access_token(
        user_id=user.id,
        company_id=target_company_id,
        membership_id=None if membership is None else membership.id,
        impersonated_by=impersonated_by,
    )
    refresh_token, jti, refresh_expires_at = create_refresh_token(
        user_id=user.id, company_id=target_company_id, impersonated_by=impersonated_by
    )
    user_agent = request.headers.get("user-agent") if request is not None else None
    record = RefreshToken(
        user_id=user.id,
        token_hash=hash_opaque_token(jti),
        expires_at=refresh_expires_at,
        user_agent=user_agent[:255] if user_agent else None,
        ip_address=request.client.host if request is not None and request.client else None,
    )
    db.add(record)
    return IssuedSession(
        user=user,
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires_at=access_expires_at,
        refresh_expires_at=refresh_expires_at,
        company_id=target_company_id,
        membership_id=None if membership is None else membership.id,
        refresh_record=record,
        impersonated_by=impersonated_by,
    )


def _stored_refresh(db: Session, jti: str) -> RefreshToken | None:
    return db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_opaque_token(jti)))


def rotate_session(
    db: Session, *, refresh_token: str, request: Request | None = None
) -> IssuedSession:
    try:
        payload = decode_token(refresh_token, REFRESH_TOKEN_TYPE)
    except TokenError as exc:
        raise AuthenticationError("Invalid refresh token", code="invalid_token") from exc

    stored = _stored_refresh(db, payload["jti"])
    if stored is None:
        raise AuthenticationError("Invalid refresh token", code="invalid_token")
    if stored.revoked_at is not None:
        # Replay of an already-rotated token: assume theft, drop every session and persist
        # that immediately — the request itself ends in an error response.
        revoke_all_sessions(db, stored.user_id)
        db.commit()
        raise AuthenticationError("Refresh token reuse detected", code="token_reused")
    if stored.expires_at <= datetime.now(UTC):
        raise AuthenticationError("Refresh token expired", code="token_expired")

    user = db.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("This account is disabled", code="account_disabled")

    company_id = payload.get("cid")
    impersonated_by = payload.get("imp")
    membership = None
    if company_id is not None and impersonated_by is None:
        membership = select_membership(db, user_id=user.id, company_id=company_id)
    if company_id is not None:
        assert_company_usable(load_company(db, company_id))

    session = issue_session(
        db,
        user=user,
        membership=membership,
        company_id=company_id,
        impersonated_by=impersonated_by,
        request=request,
    )
    db.flush()
    stored.revoked_at = datetime.now(UTC)
    stored.replaced_by_id = session.refresh_record.id
    return session


def revoke_session(db: Session, *, refresh_token: str | None) -> None:
    if not refresh_token:
        return
    try:
        payload = decode_token(refresh_token, REFRESH_TOKEN_TYPE)
    except TokenError:
        return
    stored = _stored_refresh(db, payload["jti"])
    if stored is not None and stored.revoked_at is None:
        stored.revoked_at = datetime.now(UTC)


def revoke_all_sessions(db: Session, user_id: int) -> None:
    now = datetime.now(UTC)
    for token in db.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
        )
    ):
        token.revoked_at = now


def read_access_token(request: Request) -> str | None:
    return request.cookies.get(ACCESS_COOKIE)


def decode_access_token(token: str) -> dict[str, object]:
    try:
        return decode_token(token, ACCESS_TOKEN_TYPE)
    except TokenError as exc:
        raise AuthenticationError("Invalid or expired session", code="invalid_token") from exc


def set_auth_cookies(response: Response, session: IssuedSession) -> None:
    common = {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "domain": settings.cookie_domain,
    }
    response.set_cookie(
        ACCESS_COOKIE,
        session.access_token,
        max_age=settings.access_token_ttl_minutes * 60,
        path="/",
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        session.refresh_token,
        max_age=settings.refresh_token_ttl_days * 24 * 3600,
        path=REFRESH_COOKIE_PATH,
        **common,
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/", domain=settings.cookie_domain)
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, domain=settings.cookie_domain)


def request_password_reset(db: Session, *, email: str) -> None:
    """Always succeeds from the caller's point of view — never leaks account existence."""
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None or not user.is_active:
        return
    token = generate_opaque_token()
    db.add(
        UserToken(
            user_id=user.id,
            purpose=UserTokenPurpose.PASSWORD_RESET,
            token_hash=hash_opaque_token(token),
            expires_at=datetime.now(UTC) + timedelta(hours=settings.password_reset_ttl_hours),
        )
    )
    email_service.send_password_reset_email(to=user.email, token=token)


def _claim_user_token(db: Session, *, token: str, purpose: UserTokenPurpose) -> UserToken:
    stored = db.scalar(
        select(UserToken).where(
            UserToken.token_hash == hash_opaque_token(token), UserToken.purpose == purpose
        )
    )
    if stored is None or stored.used_at is not None:
        raise AuthenticationError("Invalid or already used token", code="invalid_token")
    if stored.expires_at <= datetime.now(UTC):
        raise AuthenticationError("Token expired", code="token_expired")
    stored.used_at = datetime.now(UTC)
    return stored


def confirm_password_reset(db: Session, *, token: str, new_password: str) -> User:
    stored = _claim_user_token(db, token=token, purpose=UserTokenPurpose.PASSWORD_RESET)
    user = db.get(User, stored.user_id)
    if user is None:
        raise AuthenticationError("Invalid or already used token", code="invalid_token")
    user.hashed_password = hash_password(new_password)
    revoke_all_sessions(db, user.id)
    return user


def confirm_email_verification(db: Session, *, token: str) -> User:
    stored = _claim_user_token(db, token=token, purpose=UserTokenPurpose.EMAIL_VERIFICATION)
    user = db.get(User, stored.user_id)
    if user is None:
        raise AuthenticationError("Invalid or already used token", code="invalid_token")
    if user.email_verified_at is not None:
        raise ConflictError("Email address is already verified", code="already_verified")
    user.email_verified_at = datetime.now(UTC)
    return user
