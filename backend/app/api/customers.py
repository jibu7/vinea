from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models import User, Customer
from app.schemas.customer import CustomerCreate, CustomerUpdate, CustomerResponse
from app.dependencies import get_current_active_user, require_permission

router = APIRouter()


@router.get("/", response_model=List[CustomerResponse], dependencies=[Depends(require_permission("ar", "view"))])
async def list_customers(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    List all customers for the current company with pagination and search.
    REQ-AR-CUST-001, REQ-AR-CUST-002
    """
    query = select(Customer).where(Customer.company_id == current_user.company_id)
    
    # Apply filters
    if search:
        query = query.where(
            or_(
                Customer.customer_code.ilike(f"%{search}%"),
                Customer.name.ilike(f"%{search}%")
            )
        )
    
    if is_active is not None:
        query = query.where(Customer.is_active == is_active)
    
    # Apply pagination
    query = query.offset(skip).limit(limit)
    
    result = await db.execute(query)
    customers = result.scalars().all()
    
    return customers


@router.post("/", response_model=CustomerResponse, dependencies=[Depends(require_permission("ar", "create"))])
async def create_customer(
    customer_data: CustomerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Create a new customer.
    REQ-AR-CUST-001
    """
    # Check if customer code already exists
    existing = await db.execute(
        select(Customer).where(
            and_(
                Customer.company_id == current_user.company_id,
                Customer.customer_code == customer_data.customer_code.upper()
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Customer code already exists")
    
    # Validate AR account if provided
    if customer_data.ar_account_id:
        from app.models import GLAccount
        account = await db.execute(
            select(GLAccount).where(
                and_(
                    GLAccount.id == customer_data.ar_account_id,
                    GLAccount.company_id == current_user.company_id,
                    GLAccount.is_active == True
                )
            )
        )
        if not account.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Invalid AR account")
    
    # Create customer
    customer = Customer(
        company_id=current_user.company_id,
        **customer_data.model_dump()
    )
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    
    return customer


@router.get("/{customer_id}", response_model=CustomerResponse, dependencies=[Depends(require_permission("ar", "view"))])
async def get_customer(
    customer_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get a specific customer by ID.
    REQ-AR-CUST-001
    """
    result = await db.execute(
        select(Customer).where(
            and_(
                Customer.id == customer_id,
                Customer.company_id == current_user.company_id
            )
        )
    )
    customer = result.scalar_one_or_none()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    return customer


@router.put("/{customer_id}", response_model=CustomerResponse, dependencies=[Depends(require_permission("ar", "edit"))])
async def update_customer(
    customer_id: int,
    customer_data: CustomerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Update a customer.
    REQ-AR-CUST-001
    """
    result = await db.execute(
        select(Customer).where(
            and_(
                Customer.id == customer_id,
                Customer.company_id == current_user.company_id
            )
        )
    )
    customer = result.scalar_one_or_none()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    # Check if new customer code already exists
    if customer_data.customer_code and customer_data.customer_code.upper() != customer.customer_code:
        existing = await db.execute(
            select(Customer).where(
                and_(
                    Customer.company_id == current_user.company_id,
                    Customer.customer_code == customer_data.customer_code.upper(),
                    Customer.id != customer_id
                )
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Customer code already exists")
    
    # Validate AR account if provided
    if customer_data.ar_account_id is not None:
        from app.models import GLAccount
        account = await db.execute(
            select(GLAccount).where(
                and_(
                    GLAccount.id == customer_data.ar_account_id,
                    GLAccount.company_id == current_user.company_id,
                    GLAccount.is_active == True
                )
            )
        )
        if not account.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Invalid AR account")
    
    # Update fields
    update_data = customer_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(customer, field, value)
    
    await db.commit()
    await db.refresh(customer)
    
    return customer


@router.delete("/{customer_id}", dependencies=[Depends(require_permission("ar", "delete"))])
async def delete_customer(
    customer_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Delete a customer (soft delete by setting is_active = False).
    REQ-AR-CUST-001
    """
    result = await db.execute(
        select(Customer).where(
            and_(
                Customer.id == customer_id,
                Customer.company_id == current_user.company_id
            )
        )
    )
    customer = result.scalar_one_or_none()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    # Check if customer has transactions
    from app.models import ARTransaction
    transactions = await db.execute(
        select(func.count(ARTransaction.id)).where(
            ARTransaction.customer_id == customer_id
        )
    )
    if transactions.scalar() > 0:
        # Soft delete
        customer.is_active = False
        await db.commit()
        return {"message": "Customer deactivated (has transactions)"}
    else:
        # Hard delete
        await db.delete(customer)
        await db.commit()
        return {"message": "Customer deleted"}


@router.get("/{customer_id}/balance", dependencies=[Depends(require_permission("ar", "view"))])
async def get_customer_balance(
    customer_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get customer balance and transaction summary.
    REQ-AR-CUST-002
    """
    # Get customer
    result = await db.execute(
        select(Customer).where(
            and_(
                Customer.id == customer_id,
                Customer.company_id == current_user.company_id
            )
        )
    )
    customer = result.scalar_one_or_none()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    # Get transaction summary
    from app.models import ARTransaction, ARTransactionType
    summary = await db.execute(
        select(
            func.sum(
                func.case(
                    (ARTransactionType.affects_balance == 'debit', ARTransaction.amount),
                    else_=0
                )
            ).label('total_debits'),
            func.sum(
                func.case(
                    (ARTransactionType.affects_balance == 'credit', ARTransaction.amount),
                    else_=0
                )
            ).label('total_credits'),
            func.count(ARTransaction.id).label('transaction_count')
        ).select_from(ARTransaction).join(
            ARTransactionType
        ).where(
            and_(
                ARTransaction.customer_id == customer_id,
                ARTransaction.is_posted == True
            )
        )
    )
    
    summary_data = summary.one()
    
    return {
        "customer_id": customer.id,
        "customer_code": customer.customer_code,
        "customer_name": customer.name,
        "current_balance": customer.current_balance,
        "credit_limit": customer.credit_limit,
        "available_credit": customer.credit_limit - customer.current_balance,
        "total_debits": summary_data.total_debits or 0,
        "total_credits": summary_data.total_credits or 0,
        "transaction_count": summary_data.transaction_count or 0
    } 