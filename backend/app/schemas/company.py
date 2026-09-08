from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ApiModel


class CompanyRead(ApiModel):
    id: int
    name: str
    tin: str | None
    vat_registered: bool
    fiscal_country: str
    address: dict[str, Any] | None
    status: str
    coa_template: str


class CompanyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    tin: str | None = Field(default=None, max_length=20)
    vat_registered: bool | None = None
    fiscal_country: str | None = Field(default=None, min_length=2, max_length=2)
    address: dict[str, Any] | None = None
