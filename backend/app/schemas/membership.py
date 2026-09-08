from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ApiModel


class RoleRead(ApiModel):
    id: int
    name: str
    description: str | None = None
    is_system: bool = False


class MemberRead(ApiModel):
    id: int
    user_id: int | None = None
    full_name: str | None = None
    email: str
    is_owner: bool = False
    status: str
    roles: list[RoleRead] = Field(default_factory=list)
    invited_at: datetime | None = None
    accepted_at: datetime | None = None


class MemberRolesUpdate(BaseModel):
    role_ids: list[int] = Field(default_factory=list)
