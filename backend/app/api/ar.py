from typing import List, Optional
from datetime import date
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models import User, ARTransaction, ARTransactionType, ARAllocation, Customer, AccountingPeriod
from app.schemas.ar_transaction_type import (
    ARTransactionTypeCreate, ARTransactionTypeUpdate, ARTransactionTypeResponse
)
from app.schemas.ar_transaction import (
    ARTransactionCreate, ARTransactionUpdate, ARTransactionResponse,
    ARAllocationCreate, ARAllocationResponse, CustomerAgeingItem
)
from app.services.ar_service import ARService
from app.dependencies import get_current_active_user, require_permission

router = APIRouter()


# Transaction Types endpoints
@router.get("/transaction-types", response_model=List[ARTransactionTypeResponse], dependencies=[Depends(require_permission("ar", "view"))])
async def list_ar_transaction_types(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    is_active: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    List all AR transaction types for the current company.
    REQ-AR-TT-001, REQ-AR-TT-002
    """
    query = select(ARTransactionType).where(
        ARTransactionType.company_id == current_user.company_id
    )
    
    if is_active is not None:
        query = query.where(ARTransactionType.is_active == is_active)
    
    query = query.offset(skip).limit(limit)
    
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/transaction-types", response_model=ARTransactionTypeResponse, dependencies=[Depends(require_permission("ar", "create"))])
async def create_ar_transaction_type(
    transaction_type_data: ARTransactionTypeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Create a new AR transaction type.
    REQ-AR-TT-001, REQ-AR-TT-002
    """
    # Check if code already exists
    existing = await db.execute(
        select(ARTransactionType).where(
            and_(
                ARTransactionType.company_id == current_user.company_id,
                ARTransactionType.code == transaction_type_data.code.upper()
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Transaction type code already exists")
    
    # Validate GL accounts
    from app.models import GLAccount
    for account_id in [transaction_type_data.ar_control_account_id, transaction_type_data.revenue_account_id]:
        if account_id:
            account = await db.execute(
                select(GLAccount).where(
                    and_(
                        GLAccount.id == account_id,
                        GLAccount.company_id == current_user.company_id,
                        GLAccount.is_active == True
                    )
                )
            )
            if not account.scalar_one_or_none():
                raise HTTPException(status_code=400, detail=f"Invalid GL account ID: {account_id}")
    
    # Create transaction type
    transaction_type = ARTransactionType(
        company_id=current_user.company_id,
        **transaction_type_data.model_dump()
    )
    db.add(transaction_type)
    await db.commit()
    await db.refresh(transaction_type)
    
    return transaction_type


# Transactions endpoints
@router.get("/transactions", response_model=List[ARTransactionResponse], dependencies=[Depends(require_permission("ar", "view"))])
async def list_ar_transactions(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    customer_id: Optional[int] = None,
    transaction_type_id: Optional[int] = None,
    is_posted: Optional[bool] = None,
    is_allocated: Optional[bool] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    List AR transactions with filters.
    REQ-AR-TP-001, REQ-AR-TP-002
    """
    query = select(ARTransaction).options(
        selectinload(ARTransaction.customer),
        selectinload(ARTransaction.transaction_type)
    ).where(ARTransaction.company_id == current_user.company_id)
    
    # Apply filters
    if customer_id:
        query = query.where(ARTransaction.customer_id == customer_id)
    if transaction_type_id:
        query = query.where(ARTransaction.transaction_type_id == transaction_type_id)
    if is_posted is not None:
        query = query.where(ARTransaction.is_posted == is_posted)
    if is_allocated is not None:
        query = query.where(ARTransaction.is_allocated == is_allocated)
    if from_date:
        query = query.where(ARTransaction.transaction_date >= from_date)
    if to_date:
        query = query.where(ARTransaction.transaction_date <= to_date)
    
    query = query.order_by(ARTransaction.transaction_date.desc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    transactions = result.scalars().all()
    
    # Add related data
    for transaction in transactions:
        transaction.customer_name = transaction.customer.name if transaction.customer else None
        transaction.transaction_type_name = transaction.transaction_type.name if transaction.transaction_type else None
    
    return transactions


@router.post("/transactions", response_model=ARTransactionResponse, dependencies=[Depends(require_permission("ar", "create"))])
async def create_ar_transaction(
    transaction_data: ARTransactionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Create a new AR transaction (invoice, credit note, payment).
    REQ-AR-TP-001
    """
    # Validate customer
    await ARService.validate_customer_exists(db, transaction_data.customer_id, current_user.company_id)
    
    # Validate transaction type
    transaction_type = await ARService.validate_transaction_type(
        db, transaction_data.transaction_type_id, current_user.company_id
    )
    
    # Validate period if provided
    if transaction_data.period_id:
        period = await db.execute(
            select(AccountingPeriod).where(
                and_(
                    AccountingPeriod.id == transaction_data.period_id,
                    AccountingPeriod.company_id == current_user.company_id,
                    AccountingPeriod.is_closed == False
                )
            )
        )
        if not period.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Invalid or closed accounting period")
    
    # Check if transaction number already exists
    existing = await db.execute(
        select(ARTransaction).where(
            and_(
                ARTransaction.company_id == current_user.company_id,
                ARTransaction.transaction_number == transaction_data.transaction_number
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Transaction number already exists")
    
    # Create transaction
    transaction = ARTransaction(
        company_id=current_user.company_id,
        source_module='AR',
        **transaction_data.model_dump()
    )
    db.add(transaction)
    await db.commit()
    await db.refresh(transaction)
    
    # Load related data
    await db.refresh(transaction, ['customer', 'transaction_type'])
    transaction.customer_name = transaction.customer.name
    transaction.transaction_type_name = transaction.transaction_type.name
    
    return transaction


@router.get("/transactions/{transaction_id}", response_model=ARTransactionResponse, dependencies=[Depends(require_permission("ar", "view"))])
async def get_ar_transaction(
    transaction_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get a specific AR transaction.
    """
    result = await db.execute(
        select(ARTransaction).options(
            selectinload(ARTransaction.customer),
            selectinload(ARTransaction.transaction_type)
        ).where(
            and_(
                ARTransaction.id == transaction_id,
                ARTransaction.company_id == current_user.company_id
            )
        )
    )
    transaction = result.scalar_one_or_none()
    
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    transaction.customer_name = transaction.customer.name
    transaction.transaction_type_name = transaction.transaction_type.name
    
    return transaction


@router.post("/transactions/{transaction_id}/post", dependencies=[Depends(require_permission("ar", "post"))])
async def post_ar_transaction(
    transaction_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Post an AR transaction to GL.
    REQ-AR-TP-002, REQ-CROSS-001, REQ-CROSS-002
    """
    transaction = await ARService.post_ar_transaction(
        db, transaction_id, current_user.id, current_user.company_id
    )
    
    return {"message": "Transaction posted successfully", "transaction_id": transaction.id}


# Allocation endpoints
@router.post("/allocations", response_model=ARAllocationResponse, dependencies=[Depends(require_permission("ar", "allocate"))])
async def create_allocation(
    allocation_data: ARAllocationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Allocate a payment/credit to an invoice.
    REQ-AR-ALLOC-001, REQ-AR-ALLOC-002
    """
    allocation = await ARService.allocate_payment(
        db, allocation_data, current_user.id, current_user.company_id
    )
    
    return allocation


@router.get("/allocations", dependencies=[Depends(require_permission("ar", "view"))])
async def list_allocations(
    customer_id: Optional[int] = None,
    transaction_id: Optional[int] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    List allocations with optional filters.
    """
    query = select(ARAllocation).where(
        ARAllocation.company_id == current_user.company_id
    )
    
    if customer_id:
        query = query.join(
            ARTransaction, ARAllocation.from_transaction_id == ARTransaction.id
        ).where(ARTransaction.customer_id == customer_id)
    
    if transaction_id:
        query = query.where(
            or_(
                ARAllocation.from_transaction_id == transaction_id,
                ARAllocation.to_transaction_id == transaction_id
            )
        )
    
    query = query.offset(skip).limit(limit)
    
    result = await db.execute(query)
    return result.scalars().all()


# Reports
@router.get("/reports/ageing", response_model=List[CustomerAgeingItem], dependencies=[Depends(require_permission("ar", "view"))])
async def get_customer_ageing_report(
    as_at_date: date = Query(..., description="Date to calculate ageing as at"),
    customer_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Generate customer ageing report.
    REQ-AR-AGE-001, REQ-AR-REPORT-001
    """
    ageing_data = await ARService.calculate_customer_ageing(
        db, current_user.company_id, as_at_date, customer_id
    )
    
    return [CustomerAgeingItem(**item) for item in ageing_data]


@router.get("/reports/statement/{customer_id}", dependencies=[Depends(require_permission("ar", "view"))])
async def get_customer_statement(
    customer_id: int,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Generate customer statement.
    REQ-AR-REPORT-003
    """
    # Validate customer
    await ARService.validate_customer_exists(db, customer_id, current_user.company_id)
    
    # Get transactions
    query = select(ARTransaction).options(
        selectinload(ARTransaction.transaction_type)
    ).where(
        and_(
            ARTransaction.customer_id == customer_id,
            ARTransaction.is_posted == True
        )
    )
    
    if from_date:
        query = query.where(ARTransaction.transaction_date >= from_date)
    if to_date:
        query = query.where(ARTransaction.transaction_date <= to_date)
    
    query = query.order_by(ARTransaction.transaction_date, ARTransaction.id)
    
    result = await db.execute(query)
    transactions = result.scalars().all()
    
    # Build statement
    statement = []
    running_balance = Decimal('0')
    
    for transaction in transactions:
        if transaction.transaction_type.affects_balance == 'debit':
            debit = transaction.amount
            credit = None
            running_balance += transaction.amount
        else:
            debit = None
            credit = transaction.amount
            running_balance -= transaction.amount
        
        statement.append({
            "transaction_date": transaction.transaction_date,
            "transaction_number": transaction.transaction_number,
            "transaction_type": transaction.transaction_type.name,
            "reference": transaction.reference,
            "description": transaction.description,
            "debit": debit,
            "credit": credit,
            "balance": running_balance
        })
    
    return {
        "customer_id": customer_id,
        "from_date": from_date,
        "to_date": to_date,
        "transactions": statement,
        "closing_balance": running_balance
    }


# Auto-generate transaction number
@router.get("/transactions/next-number/{prefix}", dependencies=[Depends(require_permission("ar", "create"))])
async def get_next_transaction_number(
    prefix: str = "AR",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get the next available transaction number with the given prefix.
    """
    next_number = await ARService.get_next_transaction_number(
        db, current_user.company_id, prefix
    )
    
    return {"next_number": next_number} 