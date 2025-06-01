from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func # Added select and func
from typing import List, Optional
from datetime import date # Add this import
from decimal import Decimal

from app.core.database import get_db
from app.models import GLAccount, GLTransaction, User, AccountingPeriod # Assuming AccountingPeriod model exists
from app.schemas.gl import GLAccountSchema, GLAccountCreate, GLAccountUpdate, GLTransactionSchema, JournalEntryCreate, JournalEntryLineCreate
from app.dependencies import get_current_active_user # Assuming this dependency provides the current user
# from app.services.gl_service import validate_journal_entry # Example service for business logic

router = APIRouter()

# REQ-GL-COA-001: Create, Edit, Delete GL Accounts
# REQ-GL-COA-002: Support account types
# REQ-GL-COA-003: Support account grouping/hierarchies (parent_account_id)

@router.post("/accounts", response_model=GLAccountSchema, status_code=status.HTTP_201_CREATED)
async def create_gl_account(
    account_in: GLAccountCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user) # Add permission check dependency later
):
    # Basic validation (more can be added in a service layer)
    if account_in.parent_account_id:
        parent_account = await db.get(GLAccount, account_in.parent_account_id)
        if not parent_account or parent_account.company_id != current_user.company_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid parent account ID")

    # Check for duplicate account_code within the company
    existing_account_stmt = select(GLAccount).where(
        GLAccount.company_id == current_user.company_id, 
        GLAccount.account_code == account_in.account_code
    )
    existing_account_result = await db.execute(existing_account_stmt)
    if existing_account_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account code already exists for this company")

    db_account = GLAccount(
        **account_in.model_dump(),
        company_id=current_user.company_id # Ensure company_id is set correctly
    )
    db.add(db_account)
    await db.commit()
    await db.refresh(db_account)
    return db_account

@router.get("/accounts/{account_id}", response_model=GLAccountSchema)
async def get_gl_account(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    db_account = await db.get(GLAccount, account_id)
    if not db_account or db_account.company_id != current_user.company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="GL Account not found")
    return db_account

@router.get("/accounts", response_model=List[GLAccountSchema])
async def list_gl_accounts(
    skip: int = 0,
    limit: int = 100,
    is_active: Optional[bool] = None,
    account_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    query = select(GLAccount).where(GLAccount.company_id == current_user.company_id)
    if is_active is not None:
        query = query.where(GLAccount.is_active == is_active)
    if account_type:
        query = query.where(GLAccount.account_type == account_type)
    
    # Apply ordering, e.g., by account_code
    query = query.order_by(GLAccount.account_code)
    
    result = await db.execute(query.offset(skip).limit(limit))
    accounts = result.scalars().all()
    return accounts

@router.put("/accounts/{account_id}", response_model=GLAccountSchema)
async def update_gl_account(
    account_id: int,
    account_in: GLAccountUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    db_account = await db.get(GLAccount, account_id)
    if not db_account or db_account.company_id != current_user.company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="GL Account not found")

    update_data = account_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_account, key, value)
    
    await db.commit()
    await db.refresh(db_account)
    return db_account

# REQ-GL-JE-001: Create multi-line journal entries
# REQ-GL-JE-002: Enforce debit/credit balance
# REQ-GL-JE-003: Post journal entries, update balances

@router.post("/journal-entries", response_model=List[GLTransactionSchema], status_code=status.HTTP_201_CREATED)
async def create_journal_entry(
    journal_entry_in: JournalEntryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    # Validate total debits == total credits
    total_debit = sum(line.debit_amount for line in journal_entry_in.lines)
    total_credit = sum(line.credit_amount for line in journal_entry_in.lines)
    if total_debit != total_credit:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Debits must equal credits")
    if total_debit == Decimal("0.00"):
         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Journal entry cannot be for zero amount")

    # Validate transaction date against open accounting periods (REQ-SYS-PERIOD-003)
    period_stmt = select(AccountingPeriod).where(
        AccountingPeriod.company_id == current_user.company_id,
        AccountingPeriod.start_date <= journal_entry_in.transaction_date,
        AccountingPeriod.end_date >= journal_entry_in.transaction_date,
        AccountingPeriod.is_closed == False
    )
    active_period_result = await db.execute(period_stmt)
    active_period = active_period_result.scalar_one_or_none()
    if not active_period:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Transaction date is not in an open accounting period.")
    
    created_transactions = []
    for line in journal_entry_in.lines:
        if line.debit_amount == Decimal("0.00") and line.credit_amount == Decimal("0.00"):
            continue # Skip zero lines if any

        # Validate account exists and belongs to the company
        account = await db.get(GLAccount, line.account_id)
        if not account or account.company_id != current_user.company_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid GL Account ID: {line.account_id}")
        if not account.is_active:
             raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"GL Account {account.account_code} is not active.")

        db_transaction = GLTransaction(
            company_id=current_user.company_id,
            journal_entry_id=journal_entry_in.journal_entry_id,
            account_id=line.account_id,
            transaction_date=journal_entry_in.transaction_date,
            period_id=active_period.id, # Set this once period validation is active
            description=line.description or journal_entry_in.description, # Line desc takes precedence
            debit_amount=line.debit_amount,
            credit_amount=line.credit_amount,
            reference=journal_entry_in.reference,
            source_module="GL",
            posted_by_user_id=current_user.id
        )
        db.add(db_transaction)
        
        # Update GLAccount balance (REQ-GL-JE-003)
        # This should ideally be handled carefully, possibly with optimistic locking or specific DB functions
        # to prevent race conditions if many transactions hit the same account concurrently.
        account.current_balance += line.debit_amount
        account.current_balance -= line.credit_amount
        db.add(account) # Mark account for update
        
        created_transactions.append(db_transaction)

    if not created_transactions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid transaction lines provided.")

    await db.commit()
    for trans in created_transactions:
        await db.refresh(trans)
        # Refresh related account if balance is displayed in response or for subsequent ops
        # await db.refresh(trans.account) 

    return created_transactions

# REQ-GL-REPORT-001: Trial Balance
# REQ-GL-REPORT-002: GL Detail Report
# These would typically be GET requests with query parameters for date ranges, periods, etc.
# The implementation would involve more complex SQL queries.

@router.get("/reports/trial-balance", response_model=List[dict]) # Define a Pydantic model for Trial Balance line
async def get_trial_balance(
    report_date: date, # Or period_id e.g. period_id: Optional[int] = None
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    # This query calculates balances based on transactions up to the report_date
    # It assumes GLAccount.current_balance is a snapshot and might not be used directly for historical reports
    query = select(
        GLAccount.id,
        GLAccount.account_code, 
        GLAccount.account_name,
        GLAccount.account_type,
        func.sum(GLTransaction.debit_amount).label("total_debit"),
        func.sum(GLTransaction.credit_amount).label("total_credit")
    ).select_from(GLAccount).join(
        GLTransaction, GLAccount.id == GLTransaction.account_id, isouter=True 
        # Use isouter=True to include accounts with no transactions
    ).where(
        GLAccount.company_id == current_user.company_id,
        GLTransaction.transaction_date <= report_date, # Filter transactions by date
        # GLTransaction.period_id == period_id # Alternative filtering if using period_id
        GLAccount.is_active == True # Typically only active accounts
    ).group_by(
        GLAccount.id, GLAccount.account_code, GLAccount.account_name, GLAccount.account_type
    ).order_by(GLAccount.account_code)
    
    result = await db.execute(query)
    
    trial_balance_lines = []
    for row in result.all(): # Changed from result.fetchall() to result.all() for SQLAlchemy 2.0 style
        total_debit = row.total_debit or Decimal("0.00")
        total_credit = row.total_credit or Decimal("0.00")
        balance = total_debit - total_credit
        trial_balance_lines.append({
            "account_id": row.id,
            "account_code": row.account_code,
            "account_name": row.account_name,
            "account_type": row.account_type,
            "debit": total_debit if balance >= 0 else Decimal("0.00"), # Simplified presentation
            "credit": -balance if balance < 0 else Decimal("0.00"), # Simplified presentation
            "balance": balance # Raw balance can also be useful
        })
    return trial_balance_lines

@router.get("/reports/gl-detail", response_model=List[GLTransactionSchema])
async def get_gl_detail_report(
    account_id: int,
    start_date: date,
    end_date: date,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    account = await db.get(GLAccount, account_id)
    if not account or account.company_id != current_user.company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="GL Account not found")
    
    query = select(GLTransaction).where(
        GLTransaction.company_id == current_user.company_id,
        GLTransaction.account_id == account_id,
        GLTransaction.transaction_date >= start_date,
        GLTransaction.transaction_date <= end_date
    ).order_by(GLTransaction.transaction_date, GLTransaction.id) # Added GLTransaction.id for deterministic order
    result = await db.execute(query)
    return result.scalars().all()

# TODO: Add endpoints for:
# - Deleting GL Accounts (soft delete preferred: mark as inactive, check for transactions)
# - Reversing Journal Entries (creates counter-entries) 

# Add after the existing endpoints

# REQ-GL-COA-001: Delete GL Account (soft delete)
@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_gl_account(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    db_account = await db.get(GLAccount, account_id)
    if not db_account or db_account.company_id != current_user.company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="GL Account not found")
    
    # Check if account has transactions
    trans_check_stmt = select(func.count(GLTransaction.id)).where(
        GLTransaction.account_id == account_id
    )
    trans_count_result = await db.execute(trans_check_stmt)
    trans_count = trans_count_result.scalar()
    
    # Check if account has child accounts
    child_check_stmt = select(func.count(GLAccount.id)).where(
        GLAccount.parent_account_id == account_id
    )
    child_count_result = await db.execute(child_check_stmt)
    child_count = child_count_result.scalar()
    
    if trans_count > 0 or child_count > 0:
        # Soft delete - just mark as inactive if has transactions or child accounts
        db_account.is_active = False
        await db.commit()
    else:
        # Hard delete only if no transactions and no child accounts
        await db.delete(db_account)
        await db.commit()
    
    return None

# Journal Entry Reversal
@router.post("/journal-entries/{journal_entry_id}/reverse", response_model=List[GLTransactionSchema])
async def reverse_journal_entry(
    journal_entry_id: str,
    reversal_date: date,
    reversal_reference: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    # Get all transactions for the journal entry
    trans_stmt = select(GLTransaction).where(
        GLTransaction.company_id == current_user.company_id,
        GLTransaction.journal_entry_id == journal_entry_id,
        GLTransaction.is_reversed == False
    )
    trans_result = await db.execute(trans_stmt)
    original_transactions = trans_result.scalars().all()
    
    if not original_transactions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Journal entry not found or already reversed"
        )
    
    # Validate reversal date against open periods
    period_stmt = select(AccountingPeriod).where(
        AccountingPeriod.company_id == current_user.company_id,
        AccountingPeriod.start_date <= reversal_date,
        AccountingPeriod.end_date >= reversal_date,
        AccountingPeriod.is_closed == False
    )
    active_period_result = await db.execute(period_stmt)
    active_period = active_period_result.scalar_one_or_none()
    
    if not active_period:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Reversal date is not in an open accounting period"
        )
    
    # Create reversal transactions
    reversal_transactions = []
    reversal_je_id = f"REV-{journal_entry_id}"
    
    for orig_trans in original_transactions:
        # Get the account to update balance
        account = await db.get(GLAccount, orig_trans.account_id)
        if not account:
            continue
            
        # Create reversal transaction with swapped debit/credit
        reversal_trans = GLTransaction(
            company_id=current_user.company_id,
            journal_entry_id=reversal_je_id,
            account_id=orig_trans.account_id,
            transaction_date=reversal_date,
            period_id=active_period.id,
            description=f"Reversal of: {orig_trans.description or ''}",
            debit_amount=orig_trans.credit_amount,  # Swap
            credit_amount=orig_trans.debit_amount,  # Swap
            reference=reversal_reference or f"Reversal of {orig_trans.reference or journal_entry_id}",
            source_module="GL",
            posted_by_user_id=current_user.id,
            is_reversed=False
        )
        db.add(reversal_trans)
        
        # Update account balance
        account.current_balance -= orig_trans.debit_amount
        account.current_balance += orig_trans.credit_amount
        
        # Mark original transaction as reversed
        orig_trans.is_reversed = True
        
        reversal_transactions.append(reversal_trans)
    
    await db.commit()
    for trans in reversal_transactions:
        await db.refresh(trans)
    
    return reversal_transactions

# Add export functionality for reports
@router.get("/reports/trial-balance/export")
async def export_trial_balance(
    report_date: date,
    format: str = "csv",  # csv or excel
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    from fastapi.responses import StreamingResponse
    import csv
    import io
    
    # Get trial balance data using existing logic
    query = select(
        GLAccount.id,
        GLAccount.account_code, 
        GLAccount.account_name,
        GLAccount.account_type,
        func.sum(GLTransaction.debit_amount).label("total_debit"),
        func.sum(GLTransaction.credit_amount).label("total_credit")
    ).select_from(GLAccount).join(
        GLTransaction, GLAccount.id == GLTransaction.account_id, isouter=True 
    ).where(
        GLAccount.company_id == current_user.company_id,
        GLTransaction.transaction_date <= report_date,
        GLAccount.is_active == True
    ).group_by(
        GLAccount.id, GLAccount.account_code, GLAccount.account_name, GLAccount.account_type
    ).order_by(GLAccount.account_code)
    
    result = await db.execute(query)
    
    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write headers
    writer.writerow(["Account Code", "Account Name", "Account Type", "Debit", "Credit", "Balance"])
    
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")
    
    for row in result.all():
        debit = row.total_debit or Decimal("0.00")
        credit = row.total_credit or Decimal("0.00")
        balance = debit - credit
        
        display_debit = debit if balance >= 0 else Decimal("0.00")
        display_credit = -balance if balance < 0 else Decimal("0.00")
        
        writer.writerow([
            row.account_code,
            row.account_name,
            row.account_type,
            str(display_debit),
            str(display_credit),
            str(balance)
        ])
        
        total_debit += display_debit
        total_credit += display_credit
    
    # Write totals
    writer.writerow([])
    writer.writerow(["", "TOTALS", "", str(total_debit), str(total_credit), ""])
    
    output.seek(0)
    
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=trial_balance_{report_date}.csv"}
    ) 