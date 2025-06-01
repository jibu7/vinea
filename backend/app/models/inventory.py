from sqlalchemy import Column, Integer, String, DECIMAL, ForeignKey, Boolean, Enum, DateTime, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.models.base import Base


class ItemType(str, enum.Enum):
    STOCK = "Stock"
    SERVICE = "Service"


class CostingMethod(str, enum.Enum):
    WEIGHTED_AVERAGE = "Weighted Average"


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    item_code = Column(String(20), nullable=False, index=True)
    description = Column(String(255), nullable=False)
    item_type = Column(Enum(ItemType), nullable=False, default=ItemType.STOCK)
    unit_of_measure = Column(String(50), nullable=False, default="EACH")
    cost_price = Column(DECIMAL(15, 2), nullable=False, default=0)
    selling_price = Column(DECIMAL(15, 2), nullable=False, default=0)
    quantity_on_hand = Column(DECIMAL(15, 4), nullable=False, default=0)
    costing_method = Column(Enum(CostingMethod), nullable=False, default=CostingMethod.WEIGHTED_AVERAGE)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    company = relationship("Company", backref="inventory_items")
    inventory_transactions = relationship("InventoryTransaction", back_populates="item")
    
    # Unique constraint for item_code per company
    __table_args__ = (
        UniqueConstraint('company_id', 'item_code', name='_company_item_code_uc'),
    )


class InventoryTransactionType(Base):
    __tablename__ = "inventory_transaction_types"
    
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    code = Column(String(20), nullable=False)
    description = Column(String(255), nullable=False)
    is_increase = Column(Boolean, nullable=False, default=True)  # True for increases, False for decreases
    gl_account_id = Column(Integer, ForeignKey("gl_accounts.id"), nullable=False)
    contra_gl_account_id = Column(Integer, ForeignKey("gl_accounts.id"), nullable=True)  # For cost adjustments
    is_system = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    company = relationship("Company", backref="inventory_transaction_types")
    gl_account = relationship("GLAccount", foreign_keys=[gl_account_id])
    contra_gl_account = relationship("GLAccount", foreign_keys=[contra_gl_account_id])
    inventory_transactions = relationship("InventoryTransaction", back_populates="transaction_type")
    
    # Unique constraint for code per company
    __table_args__ = (
        UniqueConstraint('company_id', 'code', name='_company_inv_tt_code_uc'),
    )


class InventoryTransaction(Base):
    __tablename__ = "inventory_transactions"
    
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    transaction_type_id = Column(Integer, ForeignKey("inventory_transaction_types.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=False)
    transaction_date = Column(DateTime(timezone=True), nullable=False)
    reference = Column(String(100))
    description = Column(String(255))
    quantity = Column(DECIMAL(15, 4), nullable=False)
    unit_cost = Column(DECIMAL(15, 2), nullable=False)
    total_cost = Column(DECIMAL(15, 2), nullable=False)
    source_module = Column(String(20))  # 'INV', 'OE', etc.
    source_document_id = Column(Integer)
    posted_by_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    company = relationship("Company", backref="inventory_transactions")
    transaction_type = relationship("InventoryTransactionType", back_populates="inventory_transactions")
    item = relationship("InventoryItem", back_populates="inventory_transactions")
    posted_by = relationship("User")
    
    # Index for performance
    __table_args__ = (
        Index('ix_inv_trans_date', 'transaction_date'),
        Index('ix_inv_trans_item', 'item_id'),
    ) 