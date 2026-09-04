from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.company import CompanyStatus
from app.schemas.common import ApiModel

PASSWORD_MIN_LENGTH = 12


class SignupRequest(BaseModel):
    company_name: str = Field(min_length=2, max_length=200)
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=128)
    tin: str | None = Field(default=None, max_length=20)
    vat_registered: bool = False


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    company_id: int | None = None


class SwitchCompanyRequest(BaseModel):
    company_id: int


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=16, max_length=256)
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=128)


class EmailVerificationConfirm(BaseModel):
    token: str = Field(min_length=16, max_length=256)


class CompanySummary(ApiModel):
    id: int
    name: str
    status: CompanyStatus
    fiscal_country: str


class MembershipSummary(ApiModel):
    id: int
    company_id: int
    company_name: str
    is_owner: bool


class SessionResponse(BaseModel):
    user_id: int
    email: EmailStr
    full_name: str
    company_id: int | None
    membership_id: int | None
    access_expires_at: datetime


class MeResponse(BaseModel):
    user_id: int
    email: EmailStr
    full_name: str
    is_platform_admin: bool
    is_email_verified: bool
    company: CompanySummary | None
    memberships: list[MembershipSummary]
    permissions: list[str]
    impersonated_by: int | None = None
