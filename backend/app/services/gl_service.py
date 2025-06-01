from decimal import Decimal
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import date, datetime

from app.models import GLAccount, GLTransaction, AccountingPeriod
from app.schemas.gl import JournalEntryCreate, JournalEntryLineCreate

class GLService:
    """Service layer for General Ledger business logic"""
    
    @staticmethod
    async def validate_account_code(db: AsyncSession, company_id: int, account_code: str, exclude_id: Optional[int] = None) -> bool:
        """Check if account code is unique within company"""
        query = select(GLAccount).where(
            GLAccount.company_id == company_id,
            GLAccount.account_code == account_code
        )
        if exclude_id:
            query = query.where(GLAccount.id != exclude_id)
        
        result = await db.execute(query)
        return result.scalar_one_or_none() is None
    
    @staticmethod
    async def validate_parent_account(db: AsyncSession, company_id: int, parent_id: int, account_id: Optional[int] = None) -> tuple[bool, str]:
        """Validate parent account selection"""
        parent = await db.get(GLAccount, parent_id)
        
        if not parent or parent.company_id != company_id:
            return False, "Parent account not found"
        
        if not parent.is_active:
            return False, "Parent account is not active"
        
        # Prevent circular reference
        if account_id and account_id == parent_id:
            return False, "Account cannot be its own parent"
        
        # TODO: Check for circular hierarchy (parent's parent is current account, etc.)
        
        return True, ""
    
    @staticmethod
    async def validate_journal_entry(db: AsyncSession, company_id: int, journal_entry: JournalEntryCreate) -> tuple[bool, str]:
        """Validate journal entry before posting"""
        # Check if journal entry ID is unique
        existing_stmt = select(GLTransaction).where(
            GLTransaction.company_id == company_id,
            GLTransaction.journal_entry_id == journal_entry.journal_entry_id
        )
        existing_result = await db.execute(existing_stmt)
        if existing_result.scalar_one_or_none():
            return False, "Journal entry ID already exists"
        
        # Validate total debits = credits
        total_debit = sum(line.debit_amount for line in journal_entry.lines)
        total_credit = sum(line.credit_amount for line in journal_entry.lines)
        
        if total_debit != total_credit:
            return False, f"Debits ({total_debit}) must equal credits ({total_credit})"
        
        if total_debit == Decimal("0.00"):
            return False, "Journal entry cannot be for zero amount"
        
        # Validate at least 2 lines
        if len(journal_entry.lines) < 2:
            return False, "Journal entry must have at least 2 lines"
        
        # Validate each line
        for line in journal_entry.lines:
            if line.debit_amount < 0 or line.credit_amount < 0:
                return False, "Amounts cannot be negative"
            
            if line.debit_amount > 0 and line.credit_amount > 0:
                return False, "A line cannot have both debit and credit amounts"
            
            if line.debit_amount == 0 and line.credit_amount == 0:
                return False, "Each line must have either a debit or credit amount"
        
        return True, ""
    
    @staticmethod
    async def get_account_balance_at_date(db: AsyncSession, account_id: int, as_of_date: date) -> Decimal:
        """Calculate account balance as of a specific date"""
        query = select(
            func.sum(GLTransaction.debit_amount - GLTransaction.credit_amount)
        ).where(
            GLTransaction.account_id == account_id,
            GLTransaction.transaction_date <= as_of_date,
            GLTransaction.is_reversed == False
        )
        
        result = await db.execute(query)
        balance = result.scalar()
        return balance or Decimal("0.00")
    
    @staticmethod
    async def get_period_for_date(db: AsyncSession, company_id: int, transaction_date: date) -> Optional[AccountingPeriod]:
        """Get the accounting period for a given date"""
        period_stmt = select(AccountingPeriod).where(
            AccountingPeriod.company_id == company_id,
            AccountingPeriod.start_date <= transaction_date,
            AccountingPeriod.end_date >= transaction_date
        )
        result = await db.execute(period_stmt)
        return result.scalar_one_or_none()
    
    @staticmethod
    async def can_delete_account(db: AsyncSession, account_id: int) -> tuple[bool, str]:
        """Check if account can be deleted"""
        # Check for transactions
        trans_count_stmt = select(func.count(GLTransaction.id)).where(
            GLTransaction.account_id == account_id
        )
        trans_result = await db.execute(trans_count_stmt)
        trans_count = trans_result.scalar()
        
        if trans_count > 0:
            return False, f"Account has {trans_count} transactions and can only be deactivated"
        
        # Check for child accounts
        child_count_stmt = select(func.count(GLAccount.id)).where(
            GLAccount.parent_account_id == account_id
        )
        child_result = await db.execute(child_count_stmt)
        child_count = child_result.scalar()
        
        if child_count > 0:
            return False, f"Account has {child_count} child accounts"
        
        return True, ""
    
    @staticmethod
    def generate_journal_entry_id(prefix: str = "JE") -> str:
        """Generate a unique journal entry ID"""
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"{prefix}-{timestamp}"
    
    @staticmethod
    async def create_journal_entry(
        db: AsyncSession,
        company_id: int,
        transaction_date: date,
        reference: str,
        description: str,
        entries: List[Dict[str, Any]],
        source_module: Optional[str] = None,
        source_document_id: Optional[int] = None,
        period_id: Optional[int] = None,
        posted_by: Optional[int] = None
    ) -> str:
        """Create GL journal entries from other modules"""
        # Generate journal entry ID
        journal_entry_id = GLService.generate_journal_entry_id(
            prefix=f"JE-{source_module}" if source_module else "JE"
        )
        
        # Create GL transactions for each entry
        for entry in entries:
            gl_transaction = GLTransaction(
                company_id=company_id,
                journal_entry_id=journal_entry_id,
                account_id=entry['account_id'],
                transaction_date=transaction_date,
                period_id=period_id,
                description=entry.get('description', description),
                debit_amount=entry.get('debit_amount', Decimal('0')),
                credit_amount=entry.get('credit_amount', Decimal('0')),
                reference=reference,
                source_module=source_module,
                source_document_id=source_document_id,
                posted_by_user_id=posted_by,
                is_reversed=False
            )
            db.add(gl_transaction)
            
            # Update account balance
            account = await db.get(GLAccount, entry['account_id'])
            if account:
                balance_change = entry.get('debit_amount', Decimal('0')) - entry.get('credit_amount', Decimal('0'))
                account.current_balance += balance_change
        
        # Don't commit here - let the calling function handle the transaction
        return journal_entry_id 