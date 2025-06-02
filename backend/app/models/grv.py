from sqlalchemy import Column, Integer, String, ForeignKey, DECIMAL, DateTime, Text, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from .base import Base


class GoodsReceivedVoucher(Base):
    __tablename__ = "goods_received_vouchers"

    id = Column(Integer, primary_key=True, index=True)
    grv_number = Column(String(20), unique=True, nullable=False)
    grv_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    # Link to Purchase Order
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    
    # Document type
    document_type_id = Column(Integer, ForeignKey("oe_document_types.id"), nullable=False)
    
    # Status
    status = Column(String(20), default="DRAFT")  # DRAFT, POSTED, INVOICED, CANCELLED
    
    # Supplier information (denormalized from PO)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    supplier_name = Column(String(100), nullable=False)
    
    # Reference fields
    delivery_note_number = Column(String(50))
    notes = Column(Text)
    
    # Tracking
    received_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Inventory posting
    inventory_posted = Column(Boolean, default=False)
    inventory_posted_at = Column(DateTime)
    
    # Relationships
    purchase_order = relationship("PurchaseOrder", back_populates="grvs")
    supplier = relationship("Supplier", backref="grvs")
    document_type = relationship("OEDocumentType", backref="grvs")
    line_items = relationship("GRVLine", back_populates="grv", cascade="all, delete-orphan")
    received_by_user = relationship("User", backref="received_grvs")


class GRVLine(Base):
    __tablename__ = "grv_lines"

    id = Column(Integer, primary_key=True, index=True)
    grv_id = Column(Integer, ForeignKey("goods_received_vouchers.id"), nullable=False)
    line_number = Column(Integer, nullable=False)
    
    # Link to PO Line
    po_line_id = Column(Integer, ForeignKey("purchase_order_lines.id"), nullable=False)
    
    # Item information (denormalized from PO line)
    item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=False)
    item_code = Column(String(30), nullable=False)
    item_description = Column(String(200), nullable=False)
    
    # Quantities
    ordered_quantity = Column(DECIMAL(15, 3), nullable=False)  # From PO
    received_quantity = Column(DECIMAL(15, 3), nullable=False)  # Actually received
    
    # Unit price from PO
    unit_price = Column(DECIMAL(15, 2), nullable=False)
    
    # Location/Bin
    location = Column(String(50))
    
    # Quality check
    quality_status = Column(String(20), default="PENDING")  # PENDING, PASSED, FAILED
    quality_notes = Column(Text)
    
    # Relationships
    grv = relationship("GoodsReceivedVoucher", back_populates="line_items")
    po_line = relationship("PurchaseOrderLine", backref="grv_lines")
    item = relationship("InventoryItem", backref="grv_lines") 