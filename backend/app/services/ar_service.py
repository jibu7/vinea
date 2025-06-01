from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
from typing import List, Optional
from datetime import datetime, date
from decimal import Decimal
from fastapi import HTTPException

from app.models import (
    Customer, ARTransaction, ARTransactionType, ARAllocation,
    GLTransaction, TransactionType, AccountingPeriod
)
from app.schemas.ar_transaction import ARTransactionCreate, ARAllocationCreate
from app.services.gl_service import GLService


class ARService:
    """Service class for AR operations with GL integration"""
    
    @staticmethod
    async def validate_customer_exists(db: AsyncSession, customer_id: int, company_id: int):
        """Validate that a customer exists and belongs to the company"""
        customer = await db.execute(
            select(Customer).where(
                and_(
                    Customer.id == customer_id,
                    Customer.company_id == company_id,
                    Customer.is_active == True
                )
            )
        )
        if not customer.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Customer not found or inactive")
    
    @staticmethod
    async def validate_transaction_type(db: AsyncSession, transaction_type_id: int, company_id: int):
        """Validate AR transaction type exists and is active"""
        tt = await db.execute(
            select(ARTransactionType).where(
                and_(
                    ARTransactionType.id == transaction_type_id,
                    ARTransactionType.company_id == company_id,
                    ARTransactionType.is_active == True
                )
            )
        )
        transaction_type = tt.scalar_one_or_none()
        if not transaction_type:
            raise HTTPException(status_code=404, detail="Transaction type not found or inactive")
        return transaction_type
    
    @staticmethod
    async def get_next_transaction_number(db: AsyncSession, company_id: int, prefix: str = "AR") -> str:
        """Generate next transaction number"""
        # Get the latest transaction number
        result = await db.execute(
            select(func.max(ARTransaction.transaction_number)).where(
                and_(
                    ARTransaction.company_id == company_id,
                    ARTransaction.transaction_number.like(f"{prefix}%")
                )
            )
        )
        last_number = result.scalar()
        
        if last_number:
            # Extract numeric part and increment
            numeric_part = last_number.replace(prefix, "")
            try:
                next_num = int(numeric_part) + 1
            except:
                next_num = 1
        else:
            next_num = 1
        
        return f"{prefix}{next_num:06d}"
    
    @staticmethod
    async def post_ar_transaction(
        db: AsyncSession,
        transaction_id: int,
        user_id: int,
        company_id: int
    ):
        """Post an AR transaction and create GL entries"""
        # Get transaction with related data
        result = await db.execute(
            select(ARTransaction).options(
                selectinload(ARTransaction.transaction_type),
                selectinload(ARTransaction.customer),
                selectinload(ARTransaction.period)
            ).where(
                and_(
                    ARTransaction.id == transaction_id,
                    ARTransaction.company_id == company_id,
                    ARTransaction.is_posted == False
                )
            )
        )
        transaction = result.scalar_one_or_none()
        
        if not transaction:
            raise HTTPException(status_code=404, detail="Transaction not found or already posted")
        
        # Validate period is open
        if transaction.period and transaction.period.is_closed:
            raise HTTPException(status_code=400, detail="Cannot post to a closed period")
        
        # Determine GL entries based on transaction type
        gl_entries = []
        
        if transaction.transaction_type.affects_balance == 'debit':
            # Invoice - Debit AR, Credit Revenue
            gl_entries.append({
                'account_id': transaction.transaction_type.ar_control_account_id,
                'debit_amount': transaction.amount,
                'credit_amount': Decimal('0'),
                'description': f"AR Invoice: {transaction.transaction_number} - {transaction.customer.name}"
            })
            if transaction.transaction_type.revenue_account_id:
                gl_entries.append({
                    'account_id': transaction.transaction_type.revenue_account_id,
                    'debit_amount': Decimal('0'),
                    'credit_amount': transaction.amount,
                    'description': f"Revenue: {transaction.transaction_number} - {transaction.customer.name}"
                })
        else:
            # Payment/Credit - Credit AR, Debit Bank/Other
            gl_entries.append({
                'account_id': transaction.transaction_type.ar_control_account_id,
                'debit_amount': Decimal('0'),
                'credit_amount': transaction.amount,
                'description': f"AR Payment: {transaction.transaction_number} - {transaction.customer.name}"
            })
            # Note: Bank account would be handled in a more complete implementation
        
        # Create GL entries
        gl_service = GLService()
        journal_entry_id = await gl_service.create_journal_entry(
            db=db,
            company_id=company_id,
            transaction_date=transaction.transaction_date,
            reference=transaction.transaction_number,
            description=f"AR: {transaction.description or transaction.transaction_number}",
            entries=gl_entries,
            source_module='AR',
            source_document_id=transaction.id,
            period_id=transaction.period_id,
            posted_by=user_id
        )
        
        # Update transaction status
        transaction.is_posted = True
        transaction.posted_by = user_id
        transaction.posted_at = datetime.utcnow()
        
        # Update customer balance
        if transaction.transaction_type.affects_balance == 'debit':
            transaction.customer.current_balance += transaction.amount
        else:
            transaction.customer.current_balance -= transaction.amount
        
        await db.commit()
        return transaction
    
    @staticmethod
    async def allocate_payment(
        db: AsyncSession,
        allocation_data: ARAllocationCreate,
        user_id: int,
        company_id: int
    ):
        """Allocate a payment/credit to an invoice"""
        # Get both transactions
        from_result = await db.execute(
            select(ARTransaction).where(
                and_(
                    ARTransaction.id == allocation_data.from_transaction_id,
                    ARTransaction.company_id == company_id,
                    ARTransaction.is_posted == True
                )
            )
        )
        from_transaction = from_result.scalar_one_or_none()
        
        to_result = await db.execute(
            select(ARTransaction).where(
                and_(
                    ARTransaction.id == allocation_data.to_transaction_id,
                    ARTransaction.company_id == company_id,
                    ARTransaction.is_posted == True
                )
            )
        )
        to_transaction = to_result.scalar_one_or_none()
        
        if not from_transaction or not to_transaction:
            raise HTTPException(status_code=404, detail="One or both transactions not found")
        
        # Validate allocation rules
        if from_transaction.customer_id != to_transaction.customer_id:
            raise HTTPException(status_code=400, detail="Transactions must be for the same customer")
        
        # Check available amounts
        from_available = from_transaction.amount - from_transaction.allocated_amount
        to_available = to_transaction.amount - to_transaction.allocated_amount
        
        if allocation_data.allocated_amount > from_available:
            raise HTTPException(status_code=400, detail=f"Payment only has {from_available} available for allocation")
        
        if allocation_data.allocated_amount > to_available:
            raise HTTPException(status_code=400, detail=f"Invoice only has {to_available} outstanding")
        
        # Create allocation
        allocation = ARAllocation(
            company_id=company_id,
            from_transaction_id=allocation_data.from_transaction_id,
            to_transaction_id=allocation_data.to_transaction_id,
            allocated_amount=allocation_data.allocated_amount,
            allocated_by=user_id,
            allocated_at=datetime.utcnow()
        )
        db.add(allocation)
        
        # Update allocated amounts
        from_transaction.allocated_amount += allocation_data.allocated_amount
        to_transaction.allocated_amount += allocation_data.allocated_amount
        
        # Update allocation status
        if from_transaction.allocated_amount >= from_transaction.amount:
            from_transaction.is_allocated = True
        if to_transaction.allocated_amount >= to_transaction.amount:
            to_transaction.is_allocated = True
        
        await db.commit()
        return allocation
    
    @staticmethod
    async def calculate_customer_ageing(
        db: AsyncSession,
        company_id: int,
        as_at_date: date,
        customer_id: Optional[int] = None
    ):
        """Calculate customer ageing report"""
        # Base query for unallocated transactions
        query = select(ARTransaction).options(
            selectinload(ARTransaction.customer),
            selectinload(ARTransaction.transaction_type)
        ).where(
            and_(
                ARTransaction.company_id == company_id,
                ARTransaction.is_posted == True,
                ARTransaction.transaction_date <= as_at_date,
                ARTransaction.allocated_amount < ARTransaction.amount
            )
        )
        
        if customer_id:
            query = query.where(ARTransaction.customer_id == customer_id)
        
        result = await db.execute(query)
        transactions = result.scalars().all()
        
        # Group by customer and calculate ageing
        ageing_data = {}
        
        for transaction in transactions:
            customer_id = transaction.customer_id
            if customer_id not in ageing_data:
                ageing_data[customer_id] = {
                    'customer_id': customer_id,
                    'customer_code': transaction.customer.customer_code,
                    'customer_name': transaction.customer.name,
                    'current': Decimal('0'),
                    'days_30': Decimal('0'),
                    'days_60': Decimal('0'),
                    'days_90': Decimal('0'),
                    'over_90': Decimal('0'),
                    'total': Decimal('0')
                }
            
            # Calculate outstanding amount
            outstanding = transaction.amount - transaction.allocated_amount
            if transaction.transaction_type.affects_balance == 'credit':
                outstanding = -outstanding
            
            # Calculate days overdue
            due_date = transaction.due_date or transaction.transaction_date
            days_overdue = (as_at_date - due_date).days
            
            # Categorize by age
            if days_overdue <= 0:
                ageing_data[customer_id]['current'] += outstanding
            elif days_overdue <= 30:
                ageing_data[customer_id]['days_30'] += outstanding
            elif days_overdue <= 60:
                ageing_data[customer_id]['days_60'] += outstanding
            elif days_overdue <= 90:
                ageing_data[customer_id]['days_90'] += outstanding
            else:
                ageing_data[customer_id]['over_90'] += outstanding
            
            ageing_data[customer_id]['total'] += outstanding
        
        return list(ageing_data.values()) 