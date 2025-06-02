from pydantic import BaseModel
from typing import Optional


class OEDocumentTypeBase(BaseModel):
    code: str
    name: str
    document_class: str  # 'SALES' or 'PURCHASE'
    transaction_type: str  # 'ORDER', 'INVOICE', 'CREDIT_NOTE', 'GRV'
    ar_transaction_type_id: Optional[int] = None
    ap_transaction_type_id: Optional[int] = None
    updates_inventory: bool = False
    creates_ar_transaction: bool = False
    creates_ap_transaction: bool = False


class OEDocumentTypeCreate(OEDocumentTypeBase):
    pass


class OEDocumentTypeUpdate(BaseModel):
    name: Optional[str] = None
    ar_transaction_type_id: Optional[int] = None
    ap_transaction_type_id: Optional[int] = None
    updates_inventory: Optional[bool] = None
    creates_ar_transaction: Optional[bool] = None
    creates_ap_transaction: Optional[bool] = None


class OEDocumentType(OEDocumentTypeBase):
    id: int
    
    class Config:
        orm_mode = True 