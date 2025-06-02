from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from decimal import Decimal


class GRVLineBase(BaseModel):
    po_line_id: int
    received_quantity: Decimal
    location: Optional[str] = None
    quality_status: str = "PENDING"
    quality_notes: Optional[str] = None


class GRVLineCreate(GRVLineBase):
    pass


class GRVLineUpdate(BaseModel):
    received_quantity: Optional[Decimal] = None
    location: Optional[str] = None
    quality_status: Optional[str] = None
    quality_notes: Optional[str] = None


class GRVLine(GRVLineBase):
    id: int
    grv_id: int
    line_number: int
    item_id: int
    item_code: str
    item_description: str
    ordered_quantity: Decimal
    unit_price: Decimal
    
    class Config:
        orm_mode = True


class GRVBase(BaseModel):
    purchase_order_id: int
    document_type_id: int
    delivery_note_number: Optional[str] = None
    notes: Optional[str] = None


class GRVCreate(GRVBase):
    line_items: List[GRVLineCreate]
    grv_date: Optional[datetime] = None


class GRVUpdate(BaseModel):
    status: Optional[str] = None
    delivery_note_number: Optional[str] = None
    notes: Optional[str] = None


class GoodsReceivedVoucher(GRVBase):
    id: int
    grv_number: str
    grv_date: datetime
    supplier_id: int
    supplier_name: str
    status: str
    inventory_posted: bool
    inventory_posted_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    line_items: List[GRVLine] = []
    
    class Config:
        orm_mode = True


class GRVToInvoice(BaseModel):
    grv_id: int
    invoice_date: Optional[datetime] = None 