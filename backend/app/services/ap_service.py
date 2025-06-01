from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
from typing import List, Optional
from datetime import datetime, date
from decimal import Decimal
from fastapi import HTTPException

from app.models import (
    Supplier, APTransaction, APTransactionType, APAllocation,
    GLTransaction, TransactionType, AccountingPeriod
)
from app.schemas.ap_transaction import APTransactionCreate, APAllocationCreate
from app.services.gl_service import GLService


class APService:
    """Service class for AP operations with GL integration"""
    
    @staticmethod
    async def validate_supplier_exists(db: AsyncSession, supplier_id: int, company_id: int):
        """Validate that a supplier exists and belongs to the company"""
        supplier = await db.execute(
            select(Supplier).where(
                and_(
                    Supplier.id == supplier_id,
                    Supplier.company_id == company_id,
                    Supplier.is_active == True
                )
            )
        )
        if not supplier.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Supplier not found or inactive")
    
    @staticmethod
    async def validate_transaction_type(db: AsyncSession, transaction_type_id: int, company_id: int):
        """Validate AP transaction type exists and is active"""
        tt = await db.execute(
            select(APTransactionType).where(
                and_(
                    APTransactionType.id == transaction_type_id,
                    APTransactionType.company_id == company_id,
                    APTransactionType.is_active == True
                )
            )
        )
        transaction_type = tt.scalar_one_or_none()
        if not transaction_type:
            raise HTTPException(status_code=404, detail="Transaction type not found or inactive")
        return transaction_type
    
    @staticmethod
    async def get_next_transaction_number(db: AsyncSession, company_id: int, prefix: str = "AP") -> str:
        """Generate next transaction number"""
        # Get the latest transaction number
        result = await db.execute(
            select(func.max(APTransaction.transaction_number)).where(
                and_(
                    APTransaction.company_id == company_id,
                    APTransaction.transaction_number.like(f"{prefix}%")
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
    async def post_ap_transaction(
        db: AsyncSession,
        transaction_id: int,
        user_id: int,
        company_id: int
    ):
        """Post an AP transaction and create GL entries"""
        # Get transaction with related data
        result = await db.execute(
            select(APTransaction).options(
                selectinload(APTransaction.transaction_type),
                selectinload(APTransaction.supplier),
                selectinload(APTransaction.period)
            ).where(
                and_(
                    APTransaction.id == transaction_id,
                    APTransaction.company_id == company_id,
                    APTransaction.is_posted == False
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
        
        if transaction.transaction_type.affects_balance == 'credit':
            # Invoice - Credit AP, Debit Expense
            gl_entries.append({
                'account_id': transaction.transaction_type.ap_control_account_id,
                'debit_amount': Decimal('0'),
                'credit_amount': transaction.amount,
                'description': f"AP Invoice: {transaction.transaction_number} - {transaction.supplier.name}"
            })
            if transaction.transaction_type.expense_account_id:
                gl_entries.append({
                    'account_id': transaction.transaction_type.expense_account_id,
                    'debit_amount': transaction.amount,
                    'credit_amount': Decimal('0'),
                    'description': f"Expense: {transaction.transaction_number} - {transaction.supplier.name}"
                })
        else:
            # Payment/Credit - Debit AP, Credit Bank/Other
            gl_entries.append({
                'account_id': transaction.transaction_type.ap_control_account_id,
                'debit_amount': transaction.amount,
                'credit_amount': Decimal('0'),
                'description': f"AP Payment: {transaction.transaction_number} - {transaction.supplier.name}"
            })
            # Note: Bank account would be handled in a more complete implementation
        
        # Create GL entries
        gl_service = GLService()
        journal_entry_id = await gl_service.create_journal_entry(
            db=db,
            company_id=company_id,
            transaction_date=transaction.transaction_date,
            reference=transaction.transaction_number,
            description=f"AP: {transaction.description or transaction.transaction_number}",
            entries=gl_entries,
            source_module='AP',
            source_document_id=transaction.id,
            period_id=transaction.period_id,
            posted_by=user_id
        )
        
        # Update transaction status
        transaction.is_posted = True
        transaction.posted_by = user_id
        transaction.posted_at = datetime.utcnow()
        
        # Update supplier balance
        if transaction.transaction_type.affects_balance == 'credit':
            transaction.supplier.current_balance += transaction.amount
        else:
            transaction.supplier.current_balance -= transaction.amount
        
        await db.commit()
        return transaction
    
    @staticmethod
    async def allocate_payment(
        db: AsyncSession,
        allocation_data: APAllocationCreate,
        user_id: int,
        company_id: int
    ):
        """Allocate a payment/credit to an invoice"""
        # Get both transactions
        from_result = await db.execute(
            select(APTransaction).where(
                and_(
                    APTransaction.id == allocation_data.from_transaction_id,
                    APTransaction.company_id == company_id,
                    APTransaction.is_posted == True
                )
            )
        )
        from_transaction = from_result.scalar_one_or_none()
        
        to_result = await db.execute(
            select(APTransaction).where(
                and_(
                    APTransaction.id == allocation_data.to_transaction_id,
                    APTransaction.company_id == company_id,
                    APTransaction.is_posted == True
                )
            )
        )
        to_transaction = to_result.scalar_one_or_none()
        
        if not from_transaction or not to_transaction:
            raise HTTPException(status_code=404, detail="One or both transactions not found")
        
        # Validate allocation rules
        if from_transaction.supplier_id != to_transaction.supplier_id:
            raise HTTPException(status_code=400, detail="Transactions must be for the same supplier")
        
        # Check available amounts
        from_available = from_transaction.amount - from_transaction.allocated_amount
        to_available = to_transaction.amount - to_transaction.allocated_amount
        
        if allocation_data.allocated_amount > from_available:
            raise HTTPException(status_code=400, detail=f"Payment only has {from_available} available for allocation")
        
        if allocation_data.allocated_amount > to_available:
            raise HTTPException(status_code=400, detail=f"Invoice only has {to_available} outstanding")
        
        # Create allocation
        allocation = APAllocation(
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
    async def calculate_supplier_ageing(
        db: AsyncSession,
        company_id: int,
        as_at_date: date,
        supplier_id: Optional[int] = None
    ):
        """Calculate supplier ageing report"""
        # Base query for unallocated transactions
        query = select(APTransaction).options(
            selectinload(APTransaction.supplier),
            selectinload(APTransaction.transaction_type)
        ).where(
            and_(
                APTransaction.company_id == company_id,
                APTransaction.is_posted == True,
                APTransaction.transaction_date <= as_at_date,
                APTransaction.allocated_amount < APTransaction.amount
            )
        )
        
        if supplier_id:
            query = query.where(APTransaction.supplier_id == supplier_id)
        
        result = await db.execute(query)
        transactions = result.scalars().all()
        
        # Group by supplier and calculate ageing
        ageing_data = {}
        
        for transaction in transactions:
            supplier_id = transaction.supplier_id
            if supplier_id not in ageing_data:
                ageing_data[supplier_id] = {
                    'supplier_id': supplier_id,
                    'supplier_code': transaction.supplier.supplier_code,
                    'supplier_name': transaction.supplier.name,
                    'current': Decimal('0'),
                    'days_30': Decimal('0'),
                    'days_60': Decimal('0'),
                    'days_90': Decimal('0'),
                    'over_90': Decimal('0'),
                    'total': Decimal('0')
                }
            
            # Calculate outstanding amount
            outstanding = transaction.amount - transaction.allocated_amount
            if transaction.transaction_type.affects_balance == 'debit':
                outstanding = -outstanding
            
            # Calculate days overdue
            due_date = transaction.due_date or transaction.transaction_date
            days_overdue = (as_at_date - due_date).days
            
            # Categorize by age
            if days_overdue <= 0:
                ageing_data[supplier_id]['current'] += outstanding
            elif days_overdue <= 30:
                ageing_data[supplier_id]['days_30'] += outstanding
            elif days_overdue <= 60:
                ageing_data[supplier_id]['days_60'] += outstanding
            elif days_overdue <= 90:
                ageing_data[supplier_id]['days_90'] += outstanding
            else:
                ageing_data[supplier_id]['over_90'] += outstanding
            
            ageing_data[supplier_id]['total'] += outstanding
        
        return list(ageing_data.values()) 