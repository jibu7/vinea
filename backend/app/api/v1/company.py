from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import AuthContext
from app.core import permissions
from app.db import get_db
from app.schemas.company import CompanyRead, CompanyUpdate
from app.services.audit import record_audit

router = APIRouter(prefix="/company", tags=["company"])


@router.get("")
def get_company(
    auth: AuthContext = permissions.require(permissions.COMPANY_READ),
) -> CompanyRead:
    return CompanyRead.model_validate(auth.company)


@router.patch("")
def update_company(
    payload: CompanyUpdate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.COMPANY_UPDATE),
    db: Session = Depends(get_db),
) -> CompanyRead:
    company = auth.company
    assert company is not None
    before = {
        "name": company.name,
        "tin": company.tin,
        "vat_registered": company.vat_registered,
        "fiscal_country": company.fiscal_country,
        "address": company.address,
    }
    if payload.name is not None:
        company.name = payload.name
    if payload.tin is not None:
        company.tin = payload.tin
    if payload.vat_registered is not None:
        company.vat_registered = payload.vat_registered
    if payload.fiscal_country is not None:
        company.fiscal_country = payload.fiscal_country
    if payload.address is not None:
        company.address = payload.address

    after = {
        "name": company.name,
        "tin": company.tin,
        "vat_registered": company.vat_registered,
        "fiscal_country": company.fiscal_country,
        "address": company.address,
    }
    if after != before:
        record_audit(
            db,
            company_id=company.id,
            action="company.updated",
            entity="companies",
            entity_id=company.id,
            before=before,
            after=after,
            actor_user_id=auth.user.id,
            actor_email=auth.user.email,
            request=request,
        )
    db.commit()
    return CompanyRead.model_validate(company)
