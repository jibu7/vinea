from datetime import datetime

from pydantic import BaseModel, Field

from app.models.company import CompanyStatus
from app.schemas.common import ApiModel


class TenantRead(ApiModel):
    id: int
    name: str
    tin: str | None
    status: CompanyStatus
    fiscal_country: str
    vat_registered: bool
    created_at: datetime
    suspended_at: datetime | None
    suspension_reason: str | None
    active_member_count: int = 0


class SuspendTenantRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
