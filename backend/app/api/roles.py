from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Any, List

from app.core.database import get_db
from app.models.role import Role
from app.models.user import User
from app.schemas.role import (
    RoleCreate, RoleUpdate, RoleResponse, RoleList, 
    AvailablePermissions, PermissionGroup
)
from app.dependencies import get_current_active_user, require_permission

router = APIRouter()

@router.get("/permissions", response_model=AvailablePermissions)
async def get_available_permissions(
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """Get list of all available permissions (REQ-SYS-RBAC-002)"""
    return AvailablePermissions()

@router.get("/", response_model=RoleList)
async def list_roles(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    company_id: int = Query(None, description="Filter by company ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """List all roles with pagination"""
    query = select(Role)
    
    # Filter by company if specified
    if company_id:
        query = query.where(Role.company_id == company_id)
    # Otherwise, filter by user's company
    elif current_user.company_id:
        query = query.where(Role.company_id == current_user.company_id)
    
    # Get total count
    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar()
    
    # Get roles
    result = await db.execute(
        query.offset(skip).limit(limit).order_by(Role.created_at.desc())
    )
    roles = result.scalars().all()
    
    return {
        "total": total,
        "items": roles
    }

@router.post("/", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
async def create_role(
    role_in: RoleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    has_permission: bool = Depends(require_permission("users", "assign_roles"))
) -> Any:
    """Create new role (REQ-SYS-RBAC-001)"""
    # Set company_id if not provided
    if not role_in.company_id and current_user.company_id:
        role_in.company_id = current_user.company_id
    
    # Check if role name already exists for this company
    query = select(Role).where(
        Role.name == role_in.name,
        Role.company_id == role_in.company_id
    )
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role with this name already exists for this company"
        )
    
    # Validate permissions
    available_perms = AvailablePermissions()
    all_perms = []
    for module, perms in available_perms.permissions.items():
        all_perms.extend([f"{module}.{perm}" for perm in perms])
    
    invalid_perms = [p for p in role_in.permissions if p not in all_perms]
    if invalid_perms:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid permissions: {', '.join(invalid_perms)}"
        )
    
    # Create role
    role = Role(**role_in.dict())
    db.add(role)
    await db.commit()
    await db.refresh(role)
    
    return role

@router.get("/{role_id}", response_model=RoleResponse)
async def get_role(
    role_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """Get role by ID"""
    result = await db.execute(
        select(Role).where(Role.id == role_id)
    )
    role = result.scalar_one_or_none()
    
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found"
        )
    
    # Check if user has access to this role's company
    if role.company_id != current_user.company_id and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    return role

@router.put("/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: int,
    role_in: RoleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    has_permission: bool = Depends(require_permission("users", "assign_roles"))
) -> Any:
    """Update role (REQ-SYS-RBAC-002)"""
    # Get role
    result = await db.execute(
        select(Role).where(Role.id == role_id)
    )
    role = result.scalar_one_or_none()
    
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found"
        )
    
    # Check if user has access to this role's company
    if role.company_id != current_user.company_id and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    # Prevent updating system roles
    if role.is_system:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot update system roles"
        )
    
    # Validate permissions if provided
    if role_in.permissions is not None:
        available_perms = AvailablePermissions()
        all_perms = []
        for module, perms in available_perms.permissions.items():
            all_perms.extend([f"{module}.{perm}" for perm in perms])
        
        invalid_perms = [p for p in role_in.permissions if p not in all_perms]
        if invalid_perms:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid permissions: {', '.join(invalid_perms)}"
            )
    
    # Update fields
    update_data = role_in.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(role, field, value)
    
    await db.commit()
    await db.refresh(role)
    
    return role

@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    has_permission: bool = Depends(require_permission("users", "assign_roles"))
) -> None:
    """Delete role"""
    # Get role
    result = await db.execute(
        select(Role).where(Role.id == role_id)
    )
    role = result.scalar_one_or_none()
    
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found"
        )
    
    # Check if user has access to this role's company
    if role.company_id != current_user.company_id and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    # Prevent deleting system roles
    if role.is_system:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete system roles"
        )
    
    # Check if role is assigned to any users
    if role.users:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete role that is assigned to users"
        )
    
    await db.delete(role)
    await db.commit()

@router.post("/{role_id}/assign-to-user/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def assign_role_to_user(
    role_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    has_permission: bool = Depends(require_permission("users", "assign_roles"))
) -> None:
    """Assign role to user (REQ-SYS-UM-003)"""
    # Get role and user
    role_result = await db.execute(select(Role).where(Role.id == role_id))
    role = role_result.scalar_one_or_none()
    
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found"
        )
    
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Check if both role and user belong to the same company
    if role.company_id != user.company_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role and user must belong to the same company"
        )
    
    # Assign role if not already assigned
    if role not in user.roles:
        user.roles.append(role)
        await db.commit()

@router.delete("/{role_id}/remove-from-user/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_role_from_user(
    role_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    has_permission: bool = Depends(require_permission("users", "assign_roles"))
) -> None:
    """Remove role from user"""
    # Get role and user
    role_result = await db.execute(select(Role).where(Role.id == role_id))
    role = role_result.scalar_one_or_none()
    
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found"
        )
    
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Remove role if assigned
    if role in user.roles:
        user.roles.remove(role)
        await db.commit() 