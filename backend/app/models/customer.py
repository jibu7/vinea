from sqlalchemy import Column, Integer, String, Numeric, ForeignKey, Boolean, JSON
from sqlalchemy.orm import relationship
from app.models.base import BaseModel


class Customer(BaseModel):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    customer_code = Column(String(20), nullable=False)
    name = Column(String(255), nullable=False)
    address = Column(JSON, default={})
    contact_info = Column(JSON, default={})
    payment_terms = Column(Integer, default=30)  # Days
    credit_limit = Column(Numeric(15, 2), default=0)
    current_balance = Column(Numeric(15, 2), default=0)
    ar_account_id = Column(Integer, ForeignKey("gl_accounts.id"))
    is_active = Column(Boolean, default=True)
    
    # Relationships
    company = relationship("Company", back_populates="customers")
    ar_account = relationship("GLAccount", foreign_keys=[ar_account_id])
    ar_transactions = relationship("ARTransaction", back_populates="customer")
    
    # Add unique constraint for customer_code per company
    __table_args__ = (
        {'extend_existing': True}
    ) 