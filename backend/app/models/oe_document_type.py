from sqlalchemy import Column, Integer, String, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from .base import Base


class OEDocumentType(Base):
    __tablename__ = "oe_document_types"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(10), unique=True, nullable=False)
    name = Column(String(50), nullable=False)
    document_class = Column(String(20), nullable=False)  # 'SALES' or 'PURCHASE'
    transaction_type = Column(String(20), nullable=False)  # 'ORDER', 'INVOICE', 'CREDIT_NOTE', 'GRV'
    
    # Link to AR/AP transaction types for posting
    ar_transaction_type_id = Column(Integer, ForeignKey("ar_transaction_types.id"), nullable=True)
    ap_transaction_type_id = Column(Integer, ForeignKey("ap_transaction_types.id"), nullable=True)
    
    # Relationships
    ar_transaction_type = relationship("ARTransactionType", backref="oe_document_types")
    ap_transaction_type = relationship("APTransactionType", backref="oe_document_types")
    
    # Control flags
    updates_inventory = Column(Boolean, default=False)
    creates_ar_transaction = Column(Boolean, default=False)
    creates_ap_transaction = Column(Boolean, default=False) 