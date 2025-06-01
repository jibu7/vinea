from sqlalchemy import Column, Integer, Numeric, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from app.models.base import BaseModel


class APAllocation(BaseModel):
    __tablename__ = "ap_allocations"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    
    # Allocation links
    from_transaction_id = Column(Integer, ForeignKey("ap_transactions.id"), nullable=False)  # Payment/Credit
    to_transaction_id = Column(Integer, ForeignKey("ap_transactions.id"), nullable=False)    # Invoice
    
    # Amount
    allocated_amount = Column(Numeric(15, 2), nullable=False)
    
    # Audit
    allocated_by = Column(Integer, ForeignKey("users.id"))
    allocated_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    company = relationship("Company")
    from_transaction = relationship("APTransaction", foreign_keys=[from_transaction_id], back_populates="allocations_from")
    to_transaction = relationship("APTransaction", foreign_keys=[to_transaction_id], back_populates="allocations_to")
    allocated_by_user = relationship("User", foreign_keys=[allocated_by]) 