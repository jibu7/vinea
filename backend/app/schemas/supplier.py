from pydantic import BaseModel, EmailStr, validator
from typing import Optional, Dict, Any
from decimal import Decimal
from datetime import datetime


class SupplierBase(BaseModel):
    supplier_code: str
    name: str
    address: Optional[Dict[str, Any]] = {}
    contact_info: Optional[Dict[str, Any]] = {}
    payment_terms: int = 30
    ap_account_id: Optional[int] = None
    is_active: bool = True
    
    @validator('supplier_code')
    def validate_supplier_code(cls, v):
        if not v.strip():
            raise ValueError('Supplier code cannot be empty')
        if len(v) > 20:
            raise ValueError('Supplier code must be 20 characters or less')
        return v.upper()
    
    @validator('payment_terms')
    def validate_payment_terms(cls, v):
        if v < 0:
            raise ValueError('Payment terms cannot be negative')
        if v > 365:
            raise ValueError('Payment terms cannot exceed 365 days')
        return v


class SupplierCreate(SupplierBase):
    pass


class SupplierUpdate(BaseModel):
    supplier_code: Optional[str] = None
    name: Optional[str] = None
    address: Optional[Dict[str, Any]] = None
    contact_info: Optional[Dict[str, Any]] = None
    payment_terms: Optional[int] = None
    ap_account_id: Optional[int] = None
    is_active: Optional[bool] = None
    
    @validator('supplier_code')
    def validate_supplier_code(cls, v):
        if v is not None:
            if not v.strip():
                raise ValueError('Supplier code cannot be empty')
            if len(v) > 20:
                raise ValueError('Supplier code must be 20 characters or less')
            return v.upper()
        return v
    
    @validator('payment_terms')
    def validate_payment_terms(cls, v):
        if v is not None:
            if v < 0:
                raise ValueError('Payment terms cannot be negative')
            if v > 365:
                raise ValueError('Payment terms cannot exceed 365 days')
        return v


class SupplierResponse(SupplierBase):
    id: int
    company_id: int
    current_balance: Decimal
    created_at: datetime
    updated_at: Optional[datetime]
    
    class Config:
        from_attributes = True 