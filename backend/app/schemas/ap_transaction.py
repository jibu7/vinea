from pydantic import BaseModel, validator
from typing import Optional, List
from decimal import Decimal
from datetime import datetime, date


class APTransactionBase(BaseModel):
    supplier_id: int
    transaction_type_id: int
    transaction_number: str
    transaction_date: date
    due_date: Optional[date] = None
    reference: Optional[str] = None
    description: Optional[str] = None
    amount: Decimal
    period_id: Optional[int] = None
    
    @validator('transaction_number')
    def validate_transaction_number(cls, v):
        if not v.strip():
            raise ValueError('Transaction number cannot be empty')
        return v
    
    @validator('amount')
    def validate_amount(cls, v):
        if v <= 0:
            raise ValueError('Amount must be greater than zero')
        return v


class APTransactionCreate(APTransactionBase):
    pass


class APTransactionUpdate(BaseModel):
    transaction_date: Optional[date] = None
    due_date: Optional[date] = None
    reference: Optional[str] = None
    description: Optional[str] = None
    period_id: Optional[int] = None


class APTransactionPost(BaseModel):
    """Schema for posting an AP transaction"""
    pass


class APTransactionResponse(APTransactionBase):
    id: int
    company_id: int
    allocated_amount: Decimal
    is_posted: bool
    is_allocated: bool
    posted_by: Optional[int]
    posted_at: Optional[datetime]
    source_module: Optional[str]
    source_document_id: Optional[int]
    created_at: datetime
    updated_at: Optional[datetime]
    
    # Include related data
    supplier_name: Optional[str] = None
    transaction_type_name: Optional[str] = None
    
    class Config:
        from_attributes = True


class APAllocationBase(BaseModel):
    from_transaction_id: int  # Payment/Credit
    to_transaction_id: int    # Invoice
    allocated_amount: Decimal
    
    @validator('allocated_amount')
    def validate_amount(cls, v):
        if v <= 0:
            raise ValueError('Allocated amount must be greater than zero')
        return v


class APAllocationCreate(APAllocationBase):
    pass


class APAllocationResponse(APAllocationBase):
    id: int
    company_id: int
    allocated_by: Optional[int]
    allocated_at: datetime
    
    class Config:
        from_attributes = True


class SupplierAgeingItem(BaseModel):
    supplier_id: int
    supplier_code: str
    supplier_name: str
    current: Decimal = Decimal("0.00")
    days_30: Decimal = Decimal("0.00")
    days_60: Decimal = Decimal("0.00")
    days_90: Decimal = Decimal("0.00")
    over_90: Decimal = Decimal("0.00")
    total: Decimal = Decimal("0.00")


class SupplierStatementItem(BaseModel):
    transaction_date: date
    transaction_number: str
    transaction_type: str
    reference: Optional[str]
    description: Optional[str]
    debit: Optional[Decimal] = None
    credit: Optional[Decimal] = None
    balance: Decimal 