from pydantic import BaseModel, Field, validator
from typing import Optional, List
from decimal import Decimal
from datetime import datetime
from app.models.inventory import ItemType, CostingMethod


# Inventory Item Schemas
class InventoryItemBase(BaseModel):
    item_code: str = Field(..., min_length=1, max_length=20)
    description: str = Field(..., min_length=1, max_length=255)
    item_type: ItemType = ItemType.STOCK
    unit_of_measure: str = Field(default="EACH", max_length=50)
    cost_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    selling_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    costing_method: CostingMethod = CostingMethod.WEIGHTED_AVERAGE
    is_active: bool = True
    
    @validator('item_code')
    def validate_item_code(cls, v):
        if not v.strip():
            raise ValueError('Item code cannot be empty')
        return v.upper()
    
    @validator('description')
    def validate_description(cls, v):
        if not v.strip():
            raise ValueError('Description cannot be empty')
        return v.strip()
    
    @validator('unit_of_measure')
    def validate_unit_of_measure(cls, v):
        if not v.strip():
            raise ValueError('Unit of measure cannot be empty')
        return v.upper()


class InventoryItemCreate(InventoryItemBase):
    pass


class InventoryItemUpdate(BaseModel):
    description: Optional[str] = Field(None, max_length=255)
    item_type: Optional[ItemType] = None
    unit_of_measure: Optional[str] = Field(None, max_length=50)
    cost_price: Optional[Decimal] = Field(None, ge=0)
    selling_price: Optional[Decimal] = Field(None, ge=0)
    is_active: Optional[bool] = None
    
    @validator('description')
    def validate_description(cls, v):
        if v is not None and not v.strip():
            raise ValueError('Description cannot be empty')
        return v.strip() if v else v
    
    @validator('unit_of_measure')
    def validate_unit_of_measure(cls, v):
        if v is not None and not v.strip():
            raise ValueError('Unit of measure cannot be empty')
        return v.upper() if v else v


class InventoryItemResponse(InventoryItemBase):
    id: int
    company_id: int
    quantity_on_hand: Decimal
    created_at: datetime
    updated_at: Optional[datetime]
    
    class Config:
        from_attributes = True


# Inventory Transaction Type Schemas
class InventoryTransactionTypeBase(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)
    description: str = Field(..., min_length=1, max_length=255)
    is_increase: bool = True
    gl_account_id: int
    contra_gl_account_id: Optional[int] = None
    
    @validator('code')
    def validate_code(cls, v):
        if not v.strip():
            raise ValueError('Code cannot be empty')
        return v.upper()
    
    @validator('description')
    def validate_description(cls, v):
        if not v.strip():
            raise ValueError('Description cannot be empty')
        return v.strip()


class InventoryTransactionTypeCreate(InventoryTransactionTypeBase):
    pass


class InventoryTransactionTypeUpdate(BaseModel):
    description: Optional[str] = Field(None, max_length=255)
    is_increase: Optional[bool] = None
    gl_account_id: Optional[int] = None
    contra_gl_account_id: Optional[int] = None
    
    @validator('description')
    def validate_description(cls, v):
        if v is not None and not v.strip():
            raise ValueError('Description cannot be empty')
        return v.strip() if v else v


class InventoryTransactionTypeResponse(InventoryTransactionTypeBase):
    id: int
    company_id: int
    is_system: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


# Inventory Transaction Schemas
class InventoryAdjustmentRequest(BaseModel):
    item_id: int
    transaction_type_id: int
    transaction_date: datetime
    quantity: Decimal = Field(...)
    reference: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=255)
    
    @validator('quantity')
    def validate_quantity(cls, v):
        if v == 0:
            raise ValueError('Quantity cannot be zero')
        if v < 0:
            raise ValueError('Quantity must be positive')
        return v
    
    @validator('reference')
    def validate_reference(cls, v):
        return v.strip() if v else v
    
    @validator('description')
    def validate_description(cls, v):
        return v.strip() if v else v


class InventoryTransactionResponse(BaseModel):
    id: int
    company_id: int
    transaction_type_id: int
    item_id: int
    transaction_date: datetime
    reference: Optional[str]
    description: Optional[str]
    quantity: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    source_module: Optional[str]
    source_document_id: Optional[int]
    posted_by_id: Optional[int]
    created_at: datetime
    
    # Related data
    item: Optional[InventoryItemResponse] = None
    transaction_type: Optional[InventoryTransactionTypeResponse] = None
    
    class Config:
        from_attributes = True


# Report Schemas
class InventoryItemListingRequest(BaseModel):
    item_type: Optional[ItemType] = None
    is_active: Optional[bool] = None
    search: Optional[str] = None


class StockQuantityReportRequest(BaseModel):
    item_type: Optional[ItemType] = ItemType.STOCK
    show_zero_qty: bool = False
    item_code_from: Optional[str] = None
    item_code_to: Optional[str] = None


class InventoryItemReportRow(BaseModel):
    item_code: str
    description: str
    item_type: ItemType
    unit_of_measure: str
    quantity_on_hand: Decimal
    cost_price: Decimal
    selling_price: Decimal
    total_value: Decimal
    is_active: bool 