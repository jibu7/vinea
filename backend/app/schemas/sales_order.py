from pydantic import BaseModel, validator
from typing import List, Optional
from datetime import datetime
from decimal import Decimal


class SalesOrderLineBase(BaseModel):
    item_id: int
    quantity: Decimal
    unit_price: Decimal
    discount_percent: Decimal = Decimal('0')
    tax_percent: Decimal = Decimal('0')
    gl_account_id: Optional[int] = None


class SalesOrderLineCreate(SalesOrderLineBase):
    pass


class SalesOrderLineUpdate(BaseModel):
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    discount_percent: Optional[Decimal] = None
    tax_percent: Optional[Decimal] = None
    gl_account_id: Optional[int] = None


class SalesOrderLine(SalesOrderLineBase):
    id: int
    sales_order_id: int
    line_number: int
    item_code: str
    item_description: str
    line_total: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    net_amount: Decimal
    
    class Config:
        orm_mode = True


class SalesOrderBase(BaseModel):
    customer_id: int
    document_type_id: int
    customer_po_number: Optional[str] = None
    notes: Optional[str] = None


class SalesOrderCreate(SalesOrderBase):
    line_items: List[SalesOrderLineCreate]
    order_date: Optional[datetime] = None


class SalesOrderUpdate(BaseModel):
    status: Optional[str] = None
    customer_po_number: Optional[str] = None
    notes: Optional[str] = None


class SalesOrder(SalesOrderBase):
    id: int
    order_number: str
    order_date: datetime
    customer_name: str
    customer_address: Optional[str]
    status: str
    total_amount: Decimal
    tax_amount: Decimal
    discount_amount: Decimal
    net_amount: Decimal
    created_at: datetime
    updated_at: datetime
    line_items: List[SalesOrderLine] = []
    
    class Config:
        orm_mode = True


class SalesOrderToInvoice(BaseModel):
    sales_order_id: int
    invoice_date: Optional[datetime] = None 