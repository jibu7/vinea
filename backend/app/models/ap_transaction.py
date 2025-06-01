from sqlalchemy import Column, Integer, String, Numeric, ForeignKey, Boolean, Date, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from app.models.base import BaseModel


class APTransaction(BaseModel):
    __tablename__ = "ap_transactions"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    transaction_type_id = Column(Integer, ForeignKey("ap_transaction_types.id"), nullable=False)
    
    # Transaction details
    transaction_number = Column(String(50), nullable=False)
    transaction_date = Column(Date, nullable=False)
    due_date = Column(Date)
    reference = Column(String(100))
    description = Column(String(255))
    
    # Amounts
    amount = Column(Numeric(15, 2), nullable=False)
    allocated_amount = Column(Numeric(15, 2), default=0)
    
    # Status
    is_posted = Column(Boolean, default=False)
    is_allocated = Column(Boolean, default=False)
    
    # Audit
    posted_by = Column(Integer, ForeignKey("users.id"))
    posted_at = Column(DateTime)
    
    # Period tracking
    period_id = Column(Integer, ForeignKey("accounting_periods.id"))
    
    # Link to source document (for future OE integration)
    source_module = Column(String(20))  # 'AP', 'OE', 'GRV'
    source_document_id = Column(Integer)
    
    # Relationships
    company = relationship("Company")
    supplier = relationship("Supplier", back_populates="ap_transactions")
    transaction_type = relationship("APTransactionType", back_populates="ap_transactions")
    posted_by_user = relationship("User", foreign_keys=[posted_by])
    period = relationship("AccountingPeriod")
    allocations_from = relationship("APAllocation", foreign_keys="APAllocation.from_transaction_id", back_populates="from_transaction")
    allocations_to = relationship("APAllocation", foreign_keys="APAllocation.to_transaction_id", back_populates="to_transaction") 