from sqlalchemy import Column, Integer, String, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from app.models.base import BaseModel


class ARTransactionType(BaseModel):
    __tablename__ = "ar_transaction_types"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    code = Column(String(20), nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(String(255))
    
    # GL Account links
    ar_control_account_id = Column(Integer, ForeignKey("gl_accounts.id"))
    revenue_account_id = Column(Integer, ForeignKey("gl_accounts.id"))
    
    # Transaction behavior
    affects_balance = Column(String(10), nullable=False)  # 'debit' or 'credit'
    is_payment = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    
    # Relationships
    company = relationship("Company")
    ar_control_account = relationship("GLAccount", foreign_keys=[ar_control_account_id])
    revenue_account = relationship("GLAccount", foreign_keys=[revenue_account_id])
    ar_transactions = relationship("ARTransaction", back_populates="transaction_type") 