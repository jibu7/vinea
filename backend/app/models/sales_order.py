from sqlalchemy import Column, Integer, String, ForeignKey, DECIMAL, DateTime, Text, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from .base import Base


class SalesOrder(Base):
    __tablename__ = "sales_orders"

    id = Column(Integer, primary_key=True, index=True)
    order_number = Column(String(20), unique=True, nullable=False)
    order_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    # Customer information
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    customer_name = Column(String(100), nullable=False)  # Denormalized for performance
    customer_address = Column(Text)
    
    # Document type
    document_type_id = Column(Integer, ForeignKey("oe_document_types.id"), nullable=False)
    
    # Status
    status = Column(String(20), default="DRAFT")  # DRAFT, CONFIRMED, INVOICED, CANCELLED
    
    # Financial
    total_amount = Column(DECIMAL(15, 2), default=0)
    tax_amount = Column(DECIMAL(15, 2), default=0)
    discount_amount = Column(DECIMAL(15, 2), default=0)
    net_amount = Column(DECIMAL(15, 2), default=0)
    
    # Reference fields
    customer_po_number = Column(String(50))
    notes = Column(Text)
    
    # Tracking
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    customer = relationship("Customer", backref="sales_orders")
    document_type = relationship("OEDocumentType", backref="sales_orders")
    line_items = relationship("SalesOrderLine", back_populates="sales_order", cascade="all, delete-orphan")
    created_by_user = relationship("User", backref="created_sales_orders")


class SalesOrderLine(Base):
    __tablename__ = "sales_order_lines"

    id = Column(Integer, primary_key=True, index=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id"), nullable=False)
    line_number = Column(Integer, nullable=False)
    
    # Item information
    item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=False)
    item_code = Column(String(30), nullable=False)  # Denormalized
    item_description = Column(String(200), nullable=False)  # Denormalized
    
    # Quantities and pricing
    quantity = Column(DECIMAL(15, 3), nullable=False)
    unit_price = Column(DECIMAL(15, 2), nullable=False)
    discount_percent = Column(DECIMAL(5, 2), default=0)
    tax_percent = Column(DECIMAL(5, 2), default=0)
    
    # Calculated fields
    line_total = Column(DECIMAL(15, 2), nullable=False)
    discount_amount = Column(DECIMAL(15, 2), default=0)
    tax_amount = Column(DECIMAL(15, 2), default=0)
    net_amount = Column(DECIMAL(15, 2), nullable=False)
    
    # GL Account for revenue
    gl_account_id = Column(Integer, ForeignKey("gl_accounts.id"))
    
    # Relationships
    sales_order = relationship("SalesOrder", back_populates="line_items")
    item = relationship("InventoryItem", backref="sales_order_lines")
    gl_account = relationship("GLAccount", backref="sales_order_lines") 