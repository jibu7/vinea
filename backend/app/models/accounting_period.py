from sqlalchemy import Column, String, Integer, ForeignKey, Date, Boolean, UniqueConstraint
from sqlalchemy.orm import relationship
from app.models.base import BaseModel

class AccountingPeriod(BaseModel):
    """Accounting Period model (REQ-SYS-PERIOD-*)"""
    __tablename__ = "accounting_periods"
    __table_args__ = (
        UniqueConstraint('company_id', 'start_date', 'end_date', name='_period_dates_uc'),
    )
    
    company_id = Column(Integer, ForeignKey('companies.id', ondelete='CASCADE'), nullable=False)
    period_name = Column(String(100), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    is_closed = Column(Boolean, default=False)
    financial_year = Column(Integer, nullable=False)
    
    # Relationships
    company = relationship("Company", back_populates="accounting_periods")
