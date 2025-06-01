from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from app.core.database import get_db
from app.dependencies import get_current_active_user, require_permission
from app.models import User, Supplier
from app.schemas.supplier import SupplierCreate, SupplierUpdate, SupplierResponse

router = APIRouter()


@router.get("/", response_model=List[SupplierResponse])
async def list_suppliers(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: bool = Depends(require_permission("ap", "view"))
):
    """
    List suppliers for the current company with optional filtering.
    
    REQ-AP-SUPP-001: Allow viewing supplier records
    """
    query = select(Supplier).where(Supplier.company_id == current_user.company_id)
    
    # Apply filters
    if search:
        search_term = f"%{search}%"
        query = query.where(
            or_(
                Supplier.supplier_code.ilike(search_term),
                Supplier.name.ilike(search_term)
            )
        )
    
    if is_active is not None:
        query = query.where(Supplier.is_active == is_active)
    
    # Order by supplier code
    query = query.order_by(Supplier.supplier_code).offset(skip).limit(limit)
    
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/", response_model=SupplierResponse)
async def create_supplier(
    supplier_data: SupplierCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: bool = Depends(require_permission("ap", "create"))
):
    """
    Create a new supplier.
    
    REQ-AP-SUPP-001: Allow creating supplier records
    """
    # Check if supplier code already exists
    existing = await db.execute(
        select(Supplier).where(
            and_(
                Supplier.company_id == current_user.company_id,
                Supplier.supplier_code == supplier_data.supplier_code
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Supplier code already exists")
    
    # Create supplier
    supplier = Supplier(
        **supplier_data.model_dump(),
        company_id=current_user.company_id
    )
    
    db.add(supplier)
    await db.commit()
    await db.refresh(supplier)
    
    return supplier


@router.get("/{supplier_id}", response_model=SupplierResponse)
async def get_supplier(
    supplier_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: bool = Depends(require_permission("ap", "view"))
):
    """
    Get a specific supplier by ID.
    
    REQ-AP-SUPP-001: Allow viewing supplier records
    """
    result = await db.execute(
        select(Supplier).where(
            and_(
                Supplier.id == supplier_id,
                Supplier.company_id == current_user.company_id
            )
        )
    )
    supplier = result.scalar_one_or_none()
    
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    
    return supplier


@router.put("/{supplier_id}", response_model=SupplierResponse)
async def update_supplier(
    supplier_id: int,
    supplier_data: SupplierUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: bool = Depends(require_permission("ap", "edit"))
):
    """
    Update a supplier.
    
    REQ-AP-SUPP-001: Allow editing supplier records
    """
    # Get existing supplier
    result = await db.execute(
        select(Supplier).where(
            and_(
                Supplier.id == supplier_id,
                Supplier.company_id == current_user.company_id
            )
        )
    )
    supplier = result.scalar_one_or_none()
    
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    
    # Check if new supplier code already exists (if changing)
    if supplier_data.supplier_code and supplier_data.supplier_code != supplier.supplier_code:
        existing = await db.execute(
            select(Supplier).where(
                and_(
                    Supplier.company_id == current_user.company_id,
                    Supplier.supplier_code == supplier_data.supplier_code,
                    Supplier.id != supplier_id
                )
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Supplier code already exists")
    
    # Update supplier
    update_data = supplier_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(supplier, field, value)
    
    await db.commit()
    await db.refresh(supplier)
    
    return supplier


@router.delete("/{supplier_id}")
async def delete_supplier(
    supplier_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: bool = Depends(require_permission("ap", "delete"))
):
    """
    Delete a supplier (soft delete by setting is_active=False).
    
    REQ-AP-SUPP-001: Allow managing supplier records
    """
    # Get supplier
    result = await db.execute(
        select(Supplier).where(
            and_(
                Supplier.id == supplier_id,
                Supplier.company_id == current_user.company_id
            )
        )
    )
    supplier = result.scalar_one_or_none()
    
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    
    # Check if supplier has transactions
    if supplier.current_balance != 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete supplier with outstanding balance"
        )
    
    # Soft delete
    supplier.is_active = False
    await db.commit()
    
    return {"detail": "Supplier deactivated successfully"}


@router.get("/{supplier_id}/balance")
async def get_supplier_balance(
    supplier_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: bool = Depends(require_permission("ap", "view"))
):
    """
    Get supplier balance information.
    
    REQ-AP-SUPP-002: Track supplier balances
    """
    result = await db.execute(
        select(Supplier).where(
            and_(
                Supplier.id == supplier_id,
                Supplier.company_id == current_user.company_id
            )
        )
    )
    supplier = result.scalar_one_or_none()
    
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    
    # Get transaction count
    from app.models import APTransaction
    transaction_count = await db.execute(
        select(func.count(APTransaction.id)).where(
            and_(
                APTransaction.supplier_id == supplier_id,
                APTransaction.company_id == current_user.company_id
            )
        )
    )
    
    return {
        "supplier_id": supplier.id,
        "supplier_code": supplier.supplier_code,
        "supplier_name": supplier.name,
        "current_balance": supplier.current_balance,
        "payment_terms": supplier.payment_terms,
        "transaction_count": transaction_count.scalar()
    } 