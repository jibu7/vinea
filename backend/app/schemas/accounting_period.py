from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import date, datetime

class AccountingPeriodBase(BaseModel):
    """Base accounting period schema"""
    period_name: str = Field(..., min_length=1, max_length=100)
    start_date: date
    end_date: date
    financial_year: int = Field(..., ge=1900, le=2100)
    company_id: Optional[int] = None
    
    @validator('end_date')
    def validate_dates(cls, v, values):
        """Validate that end_date is after start_date"""
        if 'start_date' in values and v <= values['start_date']:
            raise ValueError('End date must be after start date')
        return v

class AccountingPeriodCreate(AccountingPeriodBase):
    """Accounting period creation schema (REQ-SYS-PERIOD-001)"""
    pass

class AccountingPeriodUpdate(BaseModel):
    """Accounting period update schema"""
    period_name: Optional[str] = Field(None, min_length=1, max_length=100)
    is_closed: Optional[bool] = None

class AccountingPeriodResponse(AccountingPeriodBase):
    """Accounting period response schema"""
    id: int
    is_closed: bool
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class AccountingPeriodList(BaseModel):
    """Accounting period list response schema"""
    total: int
    items: List[AccountingPeriodResponse]

class PeriodValidation(BaseModel):
    """Period validation result schema"""
    is_valid: bool
    message: Optional[str] = None
    overlapping_periods: List[AccountingPeriodResponse] = [] 