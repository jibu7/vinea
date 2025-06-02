from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from decimal import Decimal


class PurchaseOrderLineBase(BaseModel):
    item_id: int
    quantity: Decimal
    unit_price: Decimal
    discount_percent: Decimal = Decimal('0')
    tax_percent: Decimal = Decimal('0')
    gl_account_id: Optional[int] = None


class PurchaseOrderLineCreate(PurchaseOrderLineBase):
    pass


class PurchaseOrderLineUpdate(BaseModel):
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    discount_percent: Optional[Decimal] = None
    tax_percent: Optional[Decimal] = None
    gl_account_id: Optional[int] = None


class PurchaseOrderLine(PurchaseOrderLineBase):
    id: int
    purchase_order_id: int
    line_number: int
    item_code: str
    item_description: str
    line_total: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    net_amount: Decimal
    received_quantity: Decimal
    
    class Config:
        orm_mode = True


class PurchaseOrderBase(BaseModel):
    supplier_id: int
    document_type_id: int
    supplier_ref: Optional[str] = None
    delivery_date: Optional[datetime] = None
    delivery_address: Optional[str] = None
    notes: Optional[str] = None


class PurchaseOrderCreate(PurchaseOrderBase):
    line_items: List[PurchaseOrderLineCreate]
    order_date: Optional[datetime] = None


class PurchaseOrderUpdate(BaseModel):
    status: Optional[str] = None
    supplier_ref: Optional[str] = None
    delivery_date: Optional[datetime] = None
    delivery_address: Optional[str] = None
    notes: Optional[str] = None


class PurchaseOrder(PurchaseOrderBase):
    id: int
    order_number: str
    order_date: datetime
    supplier_name: str
    supplier_address: Optional[str]
    status: str
    total_amount: Decimal
    tax_amount: Decimal
    discount_amount: Decimal
    net_amount: Decimal
    created_at: datetime
    updated_at: datetime
    line_items: List[PurchaseOrderLine] = []
    
    class Config:
        orm_mode = True 