"""Request-scoped identity and tenancy (ADR-01, ADR-02)."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from fastapi import Depends
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.permissions import ALL_PERMISSIONS
from app.db import get_db, set_actor, set_tenant
from app.models.company import Company
from app.models.membership import CompanyMembership, MembershipStatus
from app.models.user import User
from app.services import auth as auth_service


@dataclass
class AuthContext:
    user: User
    company: Company | None = None
    membership: CompanyMembership | None = None
    permissions: set[str] = field(default_factory=set)
    impersonated_by: int | None = None

    @property
    def company_id(self) -> int:
        if self.company is None:
            raise AuthenticationError("No company selected", code="company_required")
        return self.company.id

    @property
    def is_impersonating(self) -> bool:
        return self.impersonated_by is not None


def get_auth_context(
    request: Request, db: Session = Depends(get_db)
) -> AuthContext:
    """Authenticate the caller and bind the session to their tenant.

    Everything after this point runs with `app.company_id` set, so a query that forgets
    its `company_id` filter returns nothing instead of another tenant's rows.
    """
    token = auth_service.read_access_token(request)
    if not token:
        raise AuthenticationError("Authentication required")
    payload = auth_service.decode_access_token(token)

    user = auth_service.load_user(db, int(payload["sub"]))
    if user is None or not user.is_active:
        raise AuthenticationError("This account is disabled", code="account_disabled")
    set_actor(db, user.id)

    company_id = payload.get("cid")
    impersonated_by = payload.get("imp")
    if company_id is None:
        set_tenant(db, None)
        return AuthContext(user=user)

    company = auth_service.load_company(db, int(company_id))
    auth_service.assert_company_usable(company)

    if impersonated_by is not None:
        if not user.is_platform_admin:
            raise AuthenticationError("Impersonation is not permitted", code="invalid_token")
        set_tenant(db, company.id)
        return AuthContext(
            user=user,
            company=company,
            permissions=set(ALL_PERMISSIONS),
            impersonated_by=int(impersonated_by),
        )

    membership_id = payload.get("mid")
    membership = (
        auth_service.load_membership(db, int(membership_id)) if membership_id else None
    )
    if (
        membership is None
        or membership.user_id != user.id
        or membership.company_id != company.id
        or membership.status != MembershipStatus.ACTIVE
    ):
        raise AuthenticationError("Membership is no longer active", code="no_membership")

    set_tenant(db, company.id)
    return AuthContext(
        user=user,
        company=company,
        membership=membership,
        permissions=auth_service.permissions_for(membership),
    )


def get_tenant_context(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if auth.company is None:
        raise AuthenticationError("Select a company for this session", code="company_required")
    return auth


def get_platform_admin(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if not auth.user.is_platform_admin:
        raise PermissionDeniedError("Operator console access required")
    return auth


def require_permissions(permissions: Iterable[str]) -> Callable[..., AuthContext]:
    required = tuple(permissions)

    def dependency(auth: AuthContext = Depends(get_tenant_context)) -> AuthContext:
        missing = [name for name in required if name not in auth.permissions]
        if missing:
            raise PermissionDeniedError(
                f"Missing required permission(s): {', '.join(missing)}",
                code="permission_denied",
            )
        return auth

    return dependency
