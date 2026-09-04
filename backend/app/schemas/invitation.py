from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.membership import MembershipStatus
from app.schemas.auth import PASSWORD_MIN_LENGTH
from app.schemas.common import ApiModel


class InvitationCreate(BaseModel):
    email: EmailStr
    role_ids: list[int] = Field(default_factory=list)


class InvitationRead(ApiModel):
    id: int
    email: EmailStr
    status: MembershipStatus
    invited_at: datetime | None
    invite_expires_at: datetime | None
    role_ids: list[int] = Field(default_factory=list)


class InvitationAccept(BaseModel):
    token: str = Field(min_length=16, max_length=256)
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    password: str | None = Field(default=None, min_length=PASSWORD_MIN_LENGTH, max_length=128)
