from pydantic import BaseModel, validator
from typing import Optional
from datetime import datetime


class ARTransactionTypeBase(BaseModel):
    code: str
    name: str
    description: Optional[str] = None
    ar_control_account_id: int
    revenue_account_id: Optional[int] = None
    affects_balance: str  # 'debit' or 'credit'
    is_payment: bool = False
    is_active: bool = True
    
    @validator('code')
    def validate_code(cls, v):
        if not v.strip():
            raise ValueError('Transaction type code cannot be empty')
        if len(v) > 20:
            raise ValueError('Transaction type code must be 20 characters or less')
        return v.upper()
    
    @validator('affects_balance')
    def validate_affects_balance(cls, v):
        if v.lower() not in ['debit', 'credit']:
            raise ValueError('affects_balance must be either "debit" or "credit"')
        return v.lower()


class ARTransactionTypeCreate(ARTransactionTypeBase):
    pass


class ARTransactionTypeUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    ar_control_account_id: Optional[int] = None
    revenue_account_id: Optional[int] = None
    affects_balance: Optional[str] = None
    is_payment: Optional[bool] = None
    is_active: Optional[bool] = None
    
    @validator('code')
    def validate_code(cls, v):
        if v is not None:
            if not v.strip():
                raise ValueError('Transaction type code cannot be empty')
            if len(v) > 20:
                raise ValueError('Transaction type code must be 20 characters or less')
            return v.upper()
        return v
    
    @validator('affects_balance')
    def validate_affects_balance(cls, v):
        if v is not None:
            if v.lower() not in ['debit', 'credit']:
                raise ValueError('affects_balance must be either "debit" or "credit"')
            return v.lower()
        return v


class ARTransactionTypeResponse(ARTransactionTypeBase):
    id: int
    company_id: int
    created_at: datetime
    updated_at: Optional[datetime]
    
    class Config:
        from_attributes = True 