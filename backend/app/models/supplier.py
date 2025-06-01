from sqlalchemy import Column, Integer, String, Numeric, ForeignKey, Boolean, JSON
from sqlalchemy.orm import relationship
from app.models.base import BaseModel


class Supplier(BaseModel):
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    supplier_code = Column(String(20), nullable=False)
    name = Column(String(255), nullable=False)
    address = Column(JSON, default={})
    contact_info = Column(JSON, default={})
    payment_terms = Column(Integer, default=30)  # Days
    current_balance = Column(Numeric(15, 2), default=0)
    ap_account_id = Column(Integer, ForeignKey("gl_accounts.id"))
    is_active = Column(Boolean, default=True)
    
    # Relationships
    company = relationship("Company", back_populates="suppliers")
    ap_account = relationship("GLAccount", foreign_keys=[ap_account_id])
    ap_transactions = relationship("APTransaction", back_populates="supplier")
    
    # Add unique constraint for supplier_code per company
    __table_args__ = (
        {'extend_existing': True}
    ) 