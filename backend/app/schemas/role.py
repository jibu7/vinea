from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict
from datetime import datetime

class RoleBase(BaseModel):
    """Base role schema"""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    permissions: List[str] = Field(default_factory=list)
    company_id: Optional[int] = None
    
    @validator('name')
    def validate_name(cls, v):
        """Validate role name"""
        if not v.strip():
            raise ValueError('Role name cannot be empty')
        return v.strip()

class RoleCreate(RoleBase):
    """Role creation schema (REQ-SYS-RBAC-001)"""
    pass

class RoleUpdate(BaseModel):
    """Role update schema"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    permissions: Optional[List[str]] = None

class RoleResponse(RoleBase):
    """Role response schema"""
    id: int
    is_system: bool
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class RoleList(BaseModel):
    """Role list response schema"""
    total: int
    items: List[RoleResponse]

class PermissionGroup(BaseModel):
    """Permission group schema for organizing permissions"""
    module: str
    permissions: List[str]

class AvailablePermissions(BaseModel):
    """Available permissions schema"""
    permissions: Dict[str, List[str]] = {
        "gl": ["view", "create", "edit", "delete", "post"],
        "ar": ["view", "create", "edit", "delete", "post", "allocate"],
        "ap": ["view", "create", "edit", "delete", "post", "allocate"],
        "inventory": ["view", "create", "edit", "delete", "adjust"],
        "oe": ["view", "create", "edit", "delete", "approve"],
        "users": ["view", "create", "edit", "delete", "assign_roles"],
        "companies": ["view", "create", "edit", "delete"],
        "reports": ["view", "export"],
        "system": ["configure", "backup", "restore"]
    } 