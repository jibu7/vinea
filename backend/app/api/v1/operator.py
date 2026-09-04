from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_platform_admin
from app.db import get_db, platform_scope
from app.models.company import CompanyStatus
from app.schemas.auth import SessionResponse
from app.schemas.common import Page
from app.schemas.operator import SuspendTenantRequest, TenantRead
from app.services import auth as auth_service
from app.services import operator as operator_service

router = APIRouter(prefix="/operator", tags=["operator"])


def _tenant_read(company: object, member_count: int) -> TenantRead:
    return TenantRead.model_validate(company).model_copy(
        update={"active_member_count": member_count}
    )


@router.get("/tenants")
def list_tenants(
    cursor: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    status_filter: CompanyStatus | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None, max_length=100),
    _admin: AuthContext = Depends(get_platform_admin),
    db: Session = Depends(get_db),
) -> Page[TenantRead]:
    companies = operator_service.list_tenants(
        db, cursor=cursor, limit=limit, status=status_filter, search=search
    )
    next_cursor = companies[limit - 1].id if len(companies) > limit else None
    page = companies[:limit]
    counts = operator_service.member_counts(db, [company.id for company in page])
    return Page(
        items=[_tenant_read(company, counts.get(company.id, 0)) for company in page],
        next_cursor=next_cursor,
    )


@router.post("/tenants/{company_id}/suspend")
def suspend_tenant(
    company_id: int,
    payload: SuspendTenantRequest,
    request: Request,
    admin: AuthContext = Depends(get_platform_admin),
    db: Session = Depends(get_db),
) -> TenantRead:
    company = operator_service.get_tenant(db, company_id)
    operator_service.set_tenant_status(
        db,
        company=company,
        status=CompanyStatus.SUSPENDED,
        actor=admin.user,
        reason=payload.reason,
        request=request,
    )
    db.commit()
    return _tenant_read(company, operator_service.member_counts(db, [company.id])[company.id])


@router.post("/tenants/{company_id}/activate")
def activate_tenant(
    company_id: int,
    request: Request,
    admin: AuthContext = Depends(get_platform_admin),
    db: Session = Depends(get_db),
) -> TenantRead:
    company = operator_service.get_tenant(db, company_id)
    operator_service.set_tenant_status(
        db,
        company=company,
        status=CompanyStatus.ACTIVE,
        actor=admin.user,
        request=request,
    )
    db.commit()
    return _tenant_read(company, operator_service.member_counts(db, [company.id])[company.id])


@router.post("/tenants/{company_id}/impersonate", status_code=status.HTTP_201_CREATED)
def impersonate_tenant(
    company_id: int,
    request: Request,
    response: Response,
    admin: AuthContext = Depends(get_platform_admin),
    db: Session = Depends(get_db),
) -> SessionResponse:
    company = operator_service.get_tenant(db, company_id)
    session = operator_service.impersonate(db, company=company, actor=admin.user, request=request)
    db.commit()
    auth_service.set_auth_cookies(response, session)
    return SessionResponse(
        user_id=admin.user.id,
        email=admin.user.email,
        full_name=admin.user.full_name,
        company_id=session.company_id,
        membership_id=session.membership_id,
        access_expires_at=session.access_expires_at,
    )


@router.get("/tenants/{company_id}")
def get_tenant(
    company_id: int,
    _admin: AuthContext = Depends(get_platform_admin),
    db: Session = Depends(get_db),
) -> TenantRead:
    with platform_scope(db):
        company = operator_service.get_tenant(db, company_id)
        counts = operator_service.member_counts(db, [company.id])
    return _tenant_read(company, counts[company.id])
