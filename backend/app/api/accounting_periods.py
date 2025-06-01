from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from typing import Any, List
from datetime import date

from app.core.database import get_db
from app.models.accounting_period import AccountingPeriod
from app.models.user import User
from app.schemas.accounting_period import (
    AccountingPeriodCreate, AccountingPeriodUpdate, 
    AccountingPeriodResponse, AccountingPeriodList,
    PeriodValidation
)
from app.dependencies import get_current_active_user, require_permission

router = APIRouter()

@router.get("/", response_model=AccountingPeriodList)
async def list_accounting_periods(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    company_id: int = Query(None, description="Filter by company ID"),
    financial_year: int = Query(None, description="Filter by financial year"),
    include_closed: bool = Query(True, description="Include closed periods"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """List all accounting periods with pagination (REQ-SYS-PERIOD-001)"""
    query = select(AccountingPeriod)
    
    # Filter by company if specified
    if company_id:
        query = query.where(AccountingPeriod.company_id == company_id)
    # Otherwise, filter by user's company
    elif current_user.company_id:
        query = query.where(AccountingPeriod.company_id == current_user.company_id)
    
    # Filter by financial year if specified
    if financial_year:
        query = query.where(AccountingPeriod.financial_year == financial_year)
    
    # Filter closed periods if requested
    if not include_closed:
        query = query.where(AccountingPeriod.is_closed == False)
    
    # Get total count
    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar()
    
    # Get periods
    result = await db.execute(
        query.offset(skip).limit(limit).order_by(AccountingPeriod.start_date)
    )
    periods = result.scalars().all()
    
    return {
        "total": total,
        "items": periods
    }

@router.post("/validate", response_model=PeriodValidation)
async def validate_period(
    period_in: AccountingPeriodCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """Validate a new accounting period before creation"""
    # Set company_id if not provided
    company_id = period_in.company_id or current_user.company_id
    
    if not company_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company ID is required"
        )
    
    # Check for overlapping periods
    query = select(AccountingPeriod).where(
        and_(
            AccountingPeriod.company_id == company_id,
            or_(
                and_(
                    AccountingPeriod.start_date <= period_in.start_date,
                    AccountingPeriod.end_date >= period_in.start_date
                ),
                and_(
                    AccountingPeriod.start_date <= period_in.end_date,
                    AccountingPeriod.end_date >= period_in.end_date
                )
            )
        )
    )
    result = await db.execute(query)
    overlapping_periods = result.scalars().all()
    
    if overlapping_periods:
        return {
            "is_valid": False,
            "message": "Period overlaps with existing periods",
            "overlapping_periods": overlapping_periods
        }
    
    return {
        "is_valid": True,
        "message": "Period is valid and can be created",
        "overlapping_periods": []
    }

@router.post("/", response_model=AccountingPeriodResponse, status_code=status.HTTP_201_CREATED)
async def create_accounting_period(
    period_in: AccountingPeriodCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    has_permission: bool = Depends(require_permission("system", "configure"))
) -> Any:
    """Create new accounting period (REQ-SYS-PERIOD-001)"""
    # Set company_id if not provided
    if not period_in.company_id and current_user.company_id:
        period_in.company_id = current_user.company_id
    
    if not period_in.company_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company ID is required"
        )
    
    # Validate period doesn't overlap
    validation = await validate_period(period_in, db, current_user)
    if not validation["is_valid"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=validation["message"]
        )
    
    # Create period
    period = AccountingPeriod(**period_in.dict())
    db.add(period)
    await db.commit()
    await db.refresh(period)
    
    return period

@router.get("/current", response_model=AccountingPeriodResponse)
async def get_current_period(
    company_id: int = Query(None, description="Company ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """Get current open accounting period"""
    # Use provided company_id or user's company
    target_company_id = company_id or current_user.company_id
    
    if not target_company_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company ID is required"
        )
    
    # Get current date
    today = date.today()
    
    # Find period containing today's date
    query = select(AccountingPeriod).where(
        and_(
            AccountingPeriod.company_id == target_company_id,
            AccountingPeriod.start_date <= today,
            AccountingPeriod.end_date >= today,
            AccountingPeriod.is_closed == False
        )
    )
    result = await db.execute(query)
    period = result.scalar_one_or_none()
    
    if not period:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No current open period found"
        )
    
    return period

@router.get("/{period_id}", response_model=AccountingPeriodResponse)
async def get_accounting_period(
    period_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """Get accounting period by ID"""
    result = await db.execute(
        select(AccountingPeriod).where(AccountingPeriod.id == period_id)
    )
    period = result.scalar_one_or_none()
    
    if not period:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Accounting period not found"
        )
    
    # Check if user has access to this period's company
    if period.company_id != current_user.company_id and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    return period

@router.put("/{period_id}", response_model=AccountingPeriodResponse)
async def update_accounting_period(
    period_id: int,
    period_in: AccountingPeriodUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    has_permission: bool = Depends(require_permission("system", "configure"))
) -> Any:
    """Update accounting period (REQ-SYS-PERIOD-002)"""
    # Get period
    result = await db.execute(
        select(AccountingPeriod).where(AccountingPeriod.id == period_id)
    )
    period = result.scalar_one_or_none()
    
    if not period:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Accounting period not found"
        )
    
    # Check if user has access to this period's company
    if period.company_id != current_user.company_id and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    # Update fields
    update_data = period_in.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(period, field, value)
    
    await db.commit()
    await db.refresh(period)
    
    return period

@router.post("/{period_id}/close", response_model=AccountingPeriodResponse)
async def close_accounting_period(
    period_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    has_permission: bool = Depends(require_permission("system", "configure"))
) -> Any:
    """Close an accounting period (REQ-SYS-PERIOD-002)"""
    # Get period
    result = await db.execute(
        select(AccountingPeriod).where(AccountingPeriod.id == period_id)
    )
    period = result.scalar_one_or_none()
    
    if not period:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Accounting period not found"
        )
    
    # Check if user has access to this period's company
    if period.company_id != current_user.company_id and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    # Check if already closed
    if period.is_closed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Period is already closed"
        )
    
    # TODO: Add additional checks here (e.g., unposted transactions)
    
    # Close the period
    period.is_closed = True
    await db.commit()
    await db.refresh(period)
    
    return period

@router.post("/{period_id}/reopen", response_model=AccountingPeriodResponse)
async def reopen_accounting_period(
    period_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    has_permission: bool = Depends(require_permission("system", "configure"))
) -> Any:
    """Reopen a closed accounting period"""
    # Get period
    result = await db.execute(
        select(AccountingPeriod).where(AccountingPeriod.id == period_id)
    )
    period = result.scalar_one_or_none()
    
    if not period:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Accounting period not found"
        )
    
    # Check if user has access to this period's company
    if period.company_id != current_user.company_id and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    # Check if not closed
    if not period.is_closed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Period is not closed"
        )
    
    # Reopen the period
    period.is_closed = False
    await db.commit()
    await db.refresh(period)
    
    return period

@router.delete("/{period_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_accounting_period(
    period_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    has_permission: bool = Depends(require_permission("system", "configure"))
) -> None:
    """Delete accounting period"""
    # Get period
    result = await db.execute(
        select(AccountingPeriod).where(AccountingPeriod.id == period_id)
    )
    period = result.scalar_one_or_none()
    
    if not period:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Accounting period not found"
        )
    
    # Check if user has access to this period's company
    if period.company_id != current_user.company_id and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    # TODO: Add check for transactions in this period
    
    await db.delete(period)
    await db.commit() 