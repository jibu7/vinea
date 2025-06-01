from typing import List, Optional
from datetime import date, datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query, Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.dependencies import get_current_active_user, require_permission
from app.models import (
    User, APTransaction, APTransactionType, APAllocation, 
    Supplier, GLAccount, AccountingPeriod
)
from app.schemas.ap_transaction_type import (
    APTransactionTypeCreate, APTransactionTypeUpdate, APTransactionTypeResponse
)
from app.schemas.ap_transaction import (
    APTransactionCreate, APTransactionUpdate, APTransactionResponse,
    APTransactionPost, APAllocationCreate, APAllocationResponse,
    SupplierAgeingItem
)
from app.services.ap_service import APService

router = APIRouter()

# ============= AP Transaction Types =============

@router.get("/transaction-types", response_model=List[APTransactionTypeResponse])
async def list_ap_transaction_types(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    is_active: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: bool = Depends(require_permission("ap", "view"))
):
    """
    List AP transaction types.
    
    REQ-AP-TT-001: Define AP transaction types
    """
    query = select(APTransactionType).where(
        APTransactionType.company_id == current_user.company_id
    )
    
    if is_active is not None:
        query = query.where(APTransactionType.is_active == is_active)
    
    query = query.order_by(APTransactionType.code).offset(skip).limit(limit)
    
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/transaction-types", response_model=APTransactionTypeResponse)
async def create_ap_transaction_type(
    transaction_type: APTransactionTypeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: bool = Depends(require_permission("ap", "create"))
):
    """
    Create a new AP transaction type.
    
    REQ-AP-TT-001: Define AP transaction types
    REQ-AP-TT-002: Mark transaction types as affecting debit or credit
    """
    # Validate GL accounts exist
    for account_id in [transaction_type.ap_control_account_id, transaction_type.expense_account_id]:
        if account_id:
            account = await db.execute(
                select(GLAccount).where(
                    and_(
                        GLAccount.id == account_id,
                        GLAccount.company_id == current_user.company_id
                    )
                )
            )
            if not account.scalar_one_or_none():
                raise HTTPException(status_code=400, detail=f"GL Account {account_id} not found")
    
    # Check if code already exists
    existing = await db.execute(
        select(APTransactionType).where(
            and_(
                APTransactionType.company_id == current_user.company_id,
                APTransactionType.code == transaction_type.code
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Transaction type code already exists")
    
    # Create transaction type
    db_transaction_type = APTransactionType(
        **transaction_type.model_dump(),
        company_id=current_user.company_id
    )
    
    db.add(db_transaction_type)
    await db.commit()
    await db.refresh(db_transaction_type)
    
    return db_transaction_type


@router.put("/transaction-types/{type_id}", response_model=APTransactionTypeResponse)
async def update_ap_transaction_type(
    type_id: int,
    transaction_type: APTransactionTypeUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: bool = Depends(require_permission("ap", "edit"))
):
    """Update an AP transaction type."""
    # Get existing type
    result = await db.execute(
        select(APTransactionType).where(
            and_(
                APTransactionType.id == type_id,
                APTransactionType.company_id == current_user.company_id
            )
        )
    )
    db_type = result.scalar_one_or_none()
    
    if not db_type:
        raise HTTPException(status_code=404, detail="Transaction type not found")
    
    # Update fields
    update_data = transaction_type.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_type, field, value)
    
    await db.commit()
    await db.refresh(db_type)
    
    return db_type


# ============= AP Transactions =============

@router.get("/transactions", response_model=List[APTransactionResponse])
async def list_ap_transactions(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    supplier_id: Optional[int] = None,
    transaction_type_id: Optional[int] = None,
    is_posted: Optional[bool] = None,
    is_allocated: Optional[bool] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: bool = Depends(require_permission("ap", "view"))
):
    """
    List AP transactions with filters.
    
    REQ-AP-TP-001: Process supplier transactions
    """
    query = select(APTransaction).options(
        selectinload(APTransaction.supplier),
        selectinload(APTransaction.transaction_type)
    ).where(APTransaction.company_id == current_user.company_id)
    
    # Apply filters
    if supplier_id:
        query = query.where(APTransaction.supplier_id == supplier_id)
    if transaction_type_id:
        query = query.where(APTransaction.transaction_type_id == transaction_type_id)
    if is_posted is not None:
        query = query.where(APTransaction.is_posted == is_posted)
    if is_allocated is not None:
        query = query.where(APTransaction.is_allocated == is_allocated)
    if from_date:
        query = query.where(APTransaction.transaction_date >= from_date)
    if to_date:
        query = query.where(APTransaction.transaction_date <= to_date)
    
    query = query.order_by(APTransaction.transaction_date.desc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    transactions = result.scalars().all()
    
    # Enrich response with related data
    response = []
    for transaction in transactions:
        transaction_dict = {
            **transaction.__dict__,
            "supplier_name": transaction.supplier.name if transaction.supplier else None,
            "transaction_type_name": transaction.transaction_type.name if transaction.transaction_type else None
        }
        response.append(APTransactionResponse(**transaction_dict))
    
    return response


@router.post("/transactions", response_model=APTransactionResponse)
async def create_ap_transaction(
    transaction: APTransactionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: bool = Depends(require_permission("ap", "create"))
):
    """
    Create a new AP transaction.
    
    REQ-AP-TP-001: Process supplier transactions
    """
    ap_service = APService()
    
    # Validate supplier
    await ap_service.validate_supplier_exists(db, transaction.supplier_id, current_user.company_id)
    
    # Validate transaction type
    transaction_type = await ap_service.validate_transaction_type(
        db, transaction.transaction_type_id, current_user.company_id
    )
    
    # Validate period
    if transaction.period_id:
        period = await db.execute(
            select(AccountingPeriod).where(
                and_(
                    AccountingPeriod.id == transaction.period_id,
                    AccountingPeriod.company_id == current_user.company_id,
                    AccountingPeriod.is_closed == False
                )
            )
        )
        if not period.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Invalid or closed accounting period")
    
    # Generate transaction number
    transaction_number = await ap_service.get_next_transaction_number(
        db, current_user.company_id
    )
    
    # Create transaction
    db_transaction = APTransaction(
        **transaction.model_dump(),
        transaction_number=transaction_number,
        company_id=current_user.company_id,
        is_posted=False,
        is_allocated=False,
        allocated_amount=Decimal("0")
    )
    
    db.add(db_transaction)
    await db.commit()
    await db.refresh(db_transaction)
    
    # Load relationships for response
    await db.refresh(db_transaction, ["supplier", "transaction_type"])
    
    return APTransactionResponse(
        **db_transaction.__dict__,
        supplier_name=db_transaction.supplier.name,
        transaction_type_name=db_transaction.transaction_type.name
    )


@router.post("/transactions/{transaction_id}/post", response_model=APTransactionResponse)
async def post_ap_transaction(
    transaction_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: bool = Depends(require_permission("ap", "post"))
):
    """
    Post an AP transaction to GL.
    
    REQ-AP-TP-002: Post AP transactions updating balances and GL
    REQ-CROSS-003: AP to GL integration
    REQ-CROSS-004: AP payment to GL integration
    """
    ap_service = APService()
    
    transaction = await ap_service.post_ap_transaction(
        db, transaction_id, current_user.id, current_user.company_id
    )
    
    # Load relationships for response
    await db.refresh(transaction, ["supplier", "transaction_type"])
    
    return APTransactionResponse(
        **transaction.__dict__,
        supplier_name=transaction.supplier.name,
        transaction_type_name=transaction.transaction_type.name
    )


# ============= AP Allocations =============

@router.post("/allocations", response_model=APAllocationResponse)
async def create_ap_allocation(
    allocation: APAllocationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: bool = Depends(require_permission("ap", "allocate"))
):
    """
    Allocate a payment/credit to an invoice.
    
    REQ-AP-ALLOC-001: Allocate payments and credits to invoices
    REQ-AP-ALLOC-002: Update invoice status upon allocation
    """
    ap_service = APService()
    
    result = await ap_service.allocate_payment(
        db, allocation, current_user.id, current_user.company_id
    )
    
    return result


@router.get("/allocations")
async def list_ap_allocations(
    supplier_id: Optional[int] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: bool = Depends(require_permission("ap", "view"))
):
    """List AP allocations with filters."""
    query = select(APAllocation).options(
        selectinload(APAllocation.from_transaction),
        selectinload(APAllocation.to_transaction)
    ).where(APAllocation.company_id == current_user.company_id)
    
    if supplier_id:
        query = query.join(
            APTransaction, APAllocation.from_transaction_id == APTransaction.id
        ).where(APTransaction.supplier_id == supplier_id)
    
    if from_date or to_date:
        if from_date:
            query = query.where(APAllocation.allocated_at >= datetime.combine(from_date, datetime.min.time()))
        if to_date:
            query = query.where(APAllocation.allocated_at <= datetime.combine(to_date, datetime.max.time()))
    
    query = query.order_by(APAllocation.allocated_at.desc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    return result.scalars().all()


# ============= AP Reports =============

@router.get("/reports/ageing", response_model=List[SupplierAgeingItem])
async def get_supplier_ageing_report(
    as_at_date: date = Query(..., description="Calculate ageing as at this date"),
    supplier_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: bool = Depends(require_permission("ap", "view"))
):
    """
    Generate supplier ageing report.
    
    REQ-AP-AGE-001: Calculate supplier ageing
    REQ-AP-REPORT-001: Generate Supplier Ageing report
    """
    ap_service = APService()
    
    ageing_data = await ap_service.calculate_supplier_ageing(
        db, current_user.company_id, as_at_date, supplier_id
    )
    
    return [SupplierAgeingItem(**item) for item in ageing_data]


@router.get("/reports/supplier-list")
async def get_supplier_listing_report(
    is_active: Optional[bool] = None,
    with_balance: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: bool = Depends(require_permission("ap", "view"))
):
    """
    Generate supplier listing report.
    
    REQ-AP-REPORT-002: Generate Supplier Listing report
    """
    query = select(Supplier).where(Supplier.company_id == current_user.company_id)
    
    if is_active is not None:
        query = query.where(Supplier.is_active == is_active)
    
    if with_balance is not None:
        if with_balance:
            query = query.where(Supplier.current_balance != 0)
        else:
            query = query.where(Supplier.current_balance == 0)
    
    query = query.order_by(Supplier.supplier_code)
    
    result = await db.execute(query)
    suppliers = result.scalars().all()
    
    return [{
        "supplier_code": s.supplier_code,
        "name": s.name,
        "payment_terms": s.payment_terms,
        "current_balance": s.current_balance,
        "is_active": s.is_active,
        "contact_info": s.contact_info
    } for s in suppliers]


@router.get("/reports/transactions")
async def get_ap_transaction_listing_report(
    supplier_id: Optional[int] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    transaction_type_id: Optional[int] = None,
    is_posted: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: bool = Depends(require_permission("ap", "view"))
):
    """
    Generate AP transaction listing report.
    
    REQ-AP-REPORT-003: Generate Supplier Transaction Listing report
    """
    query = select(APTransaction).options(
        selectinload(APTransaction.supplier),
        selectinload(APTransaction.transaction_type)
    ).where(APTransaction.company_id == current_user.company_id)
    
    # Apply filters
    if supplier_id:
        query = query.where(APTransaction.supplier_id == supplier_id)
    if from_date:
        query = query.where(APTransaction.transaction_date >= from_date)
    if to_date:
        query = query.where(APTransaction.transaction_date <= to_date)
    if transaction_type_id:
        query = query.where(APTransaction.transaction_type_id == transaction_type_id)
    if is_posted is not None:
        query = query.where(APTransaction.is_posted == is_posted)
    
    query = query.order_by(APTransaction.transaction_date, APTransaction.transaction_number)
    
    result = await db.execute(query)
    transactions = result.scalars().all()
    
    return [{
        "transaction_date": t.transaction_date,
        "transaction_number": t.transaction_number,
        "supplier_code": t.supplier.supplier_code,
        "supplier_name": t.supplier.name,
        "transaction_type": t.transaction_type.name,
        "reference": t.reference,
        "description": t.description,
        "amount": t.amount,
        "allocated_amount": t.allocated_amount,
        "outstanding": t.amount - t.allocated_amount,
        "is_posted": t.is_posted,
        "is_allocated": t.is_allocated
    } for t in transactions] 