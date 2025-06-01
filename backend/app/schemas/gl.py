from pydantic import BaseModel, Field
from typing import List, Optional, Annotated
from datetime import date
from decimal import Decimal

# GL Account Schemas
class GLAccountBase(BaseModel):
    account_code: Annotated[str, Field(max_length=20)]
    account_name: Annotated[str, Field(max_length=255)]
    account_type: Annotated[str, Field(max_length=20)]  # ASSET, LIABILITY, EQUITY, INCOME, EXPENSE
    parent_account_id: Optional[int] = None
    is_active: bool = True
    is_system_account: bool = False

class GLAccountCreate(GLAccountBase):
    company_id: int # Should be set from current user or context, not direct input ideally

class GLAccountUpdate(GLAccountBase):
    account_code: Optional[Annotated[str, Field(max_length=20)]] = None
    account_name: Optional[Annotated[str, Field(max_length=255)]] = None
    account_type: Optional[Annotated[str, Field(max_length=20)]] = None
    # company_id should not be updatable generally

class GLAccountInDBBase(GLAccountBase):
    id: int
    company_id: int
    current_balance: Annotated[Decimal, Field(max_digits=15, decimal_places=2)] = Decimal("0.00")

    class Config:
        from_attributes = True # Changed from orm_mode for Pydantic v2

class GLAccountSchema(GLAccountInDBBase):
    pass

# GL Transaction Schemas
class GLTransactionBase(BaseModel):
    journal_entry_id: Annotated[str, Field(max_length=50)] # Could be a user-defined batch ID
    account_id: int
    transaction_date: date
    period_id: Optional[int] = None # Could be auto-derived or validated against transaction_date
    description: Optional[Annotated[str, Field(max_length=1000)]] = None # Assuming a reasonable max length
    debit_amount: Annotated[Decimal, Field(max_digits=15, decimal_places=2)] = Decimal("0.00")
    credit_amount: Annotated[Decimal, Field(max_digits=15, decimal_places=2)] = Decimal("0.00")
    reference: Optional[Annotated[str, Field(max_length=100)]] = None
    source_module: Optional[Annotated[str, Field(max_length=20)]] = None
    source_document_id: Optional[int] = None

class GLTransactionCreate(GLTransactionBase):
    company_id: int # To be set from context
    posted_by_user_id: Optional[int] = None # To be set from current authenticated user

class GLTransactionInDBBase(GLTransactionBase):
    id: int
    company_id: int
    posted_by_user_id: Optional[int] = None
    is_reversed: bool = False

    class Config:
        from_attributes = True

class GLTransactionSchema(GLTransactionInDBBase):
    pass

# For Journal Entry creation which typically involves multiple lines
class JournalEntryLineCreate(BaseModel):
    account_id: int
    description: Optional[Annotated[str, Field(max_length=1000)]] = None
    debit_amount: Annotated[Decimal, Field(max_digits=15, decimal_places=2)] = Decimal("0.00")
    credit_amount: Annotated[Decimal, Field(max_digits=15, decimal_places=2)] = Decimal("0.00")

class JournalEntryCreate(BaseModel):
    transaction_date: date
    journal_entry_id: Annotated[str, Field(max_length=50)] # User defined or system generated prefix + seq
    reference: Optional[Annotated[str, Field(max_length=100)]] = None
    description: Optional[Annotated[str, Field(max_length=1000)]] = None # Overall description for the JE
    lines: List[JournalEntryLineCreate] 