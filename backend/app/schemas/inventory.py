"""Inventory API schemas (P5 step 1 — masters)."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, Field

from app.models.inventory import ItemType, NegativeStockPolicy
from app.schemas.common import ApiModel

Money = Annotated[Decimal, Field(max_digits=20, decimal_places=6)]
NonNegativeMoney = Annotated[Decimal, Field(ge=0, max_digits=20, decimal_places=6)]
Quantity = Annotated[Decimal, Field(max_digits=20, decimal_places=6)]
PositiveQuantity = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=6)]
Factor = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=10)]


# --- Units of measure ---------------------------------------------------------------------


class UomCategoryCreate(BaseModel):
    """A category always arrives with its base unit — see `create_uom_category`."""

    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    base_uom_code: str = Field(min_length=1, max_length=20)
    base_uom_name: str = Field(min_length=1, max_length=200)
    base_uom_decimal_places: int = Field(default=0, ge=0, le=6)


class UomCategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None


class UomCategoryRead(ApiModel):
    id: int
    code: str
    name: str
    is_active: bool


class UomCreate(BaseModel):
    category_id: int
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    factor_to_base: Factor
    decimal_places: int = Field(default=0, ge=0, le=6)


class UomUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    factor_to_base: Factor | None = None
    decimal_places: int | None = Field(default=None, ge=0, le=6)
    is_active: bool | None = None


class UomRead(ApiModel):
    id: int
    category_id: int
    code: str
    name: str
    factor_to_base: Decimal
    decimal_places: int
    is_base: bool
    is_active: bool


class UomCategoryWithUnits(UomCategoryRead):
    uoms: list[UomRead] = []


# --- Items --------------------------------------------------------------------------------


class ItemCreate(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=200)
    uom_category_id: int
    base_uom_id: int
    item_type: ItemType = ItemType.STOCK
    description: str | None = None
    inventory_account_id: int | None = None
    cogs_account_id: int | None = None
    sales_account_id: int | None = None
    default_sales_tax_code_id: int | None = None
    default_purchase_tax_code_id: int | None = None
    selling_price: NonNegativeMoney = Decimal(0)
    price_includes_tax: bool = False


class ItemUpdate(BaseModel):
    """`item_type`, `uom_category_id` and `base_uom_id` are absent for now, not forever.

    They are unsafe once the item has moves behind it, and safe before that. P5 step 2 adds
    them back behind a "locked once posted against" check — the rule `update_tax_code` already
    applies to a tax rate — once `stock_moves` exists to ask. See `masters.update_item`."""

    code: str | None = Field(default=None, min_length=1, max_length=30)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    inventory_account_id: int | None = None
    clear_inventory_account: bool = False
    cogs_account_id: int | None = None
    clear_cogs_account: bool = False
    sales_account_id: int | None = None
    clear_sales_account: bool = False
    default_sales_tax_code_id: int | None = None
    clear_sales_tax_code: bool = False
    default_purchase_tax_code_id: int | None = None
    clear_purchase_tax_code: bool = False
    selling_price: NonNegativeMoney | None = None
    price_includes_tax: bool | None = None
    is_active: bool | None = None


class ItemRead(ApiModel):
    id: int
    code: str
    name: str
    description: str | None
    item_type: ItemType
    uom_category_id: int
    base_uom_id: int
    inventory_account_id: int | None
    cogs_account_id: int | None
    sales_account_id: int | None
    default_sales_tax_code_id: int | None
    default_purchase_tax_code_id: int | None
    selling_price: Decimal
    price_includes_tax: bool
    is_active: bool


class ItemLookupRead(BaseModel):
    """What a scan or a typed code resolves to: the item, and the unit the *scan* means."""

    item: ItemRead
    uom: UomRead
    pack_quantity: Decimal
    matched_barcode: str | None = None


class ItemAuditRead(ApiModel):
    id: int
    at: datetime
    action: str
    actor_email: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None


# --- Barcodes -----------------------------------------------------------------------------


class BarcodeCreate(BaseModel):
    barcode: str = Field(min_length=1, max_length=50)
    uom_id: int
    pack_quantity: PositiveQuantity = Decimal(1)


class BarcodeUpdate(BaseModel):
    uom_id: int | None = None
    pack_quantity: PositiveQuantity | None = None
    is_active: bool | None = None


class BarcodeRead(ApiModel):
    id: int
    item_id: int
    barcode: str
    uom_id: int
    pack_quantity: Decimal
    is_active: bool


# --- Warehouses ---------------------------------------------------------------------------


class WarehouseCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    branch_id: int
    is_default: bool = False


class WarehouseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    branch_id: int | None = None
    is_default: bool | None = None
    is_active: bool | None = None


class WarehouseRead(ApiModel):
    id: int
    code: str
    name: str
    branch_id: int
    is_default: bool
    is_in_transit: bool
    is_active: bool


# --- Defaults -----------------------------------------------------------------------------


class InventoryDefaultsRead(ApiModel):
    inventory_account_id: int | None
    inventory_in_transit_account_id: int | None
    inventory_adjustment_account_id: int | None
    stock_count_variance_account_id: int | None
    cogs_account_id: int | None
    negative_stock_policy: NegativeStockPolicy
    default_warehouse_id: int | None


class InventoryDefaultsUpdate(BaseModel):
    inventory_account_id: int | None = None
    inventory_in_transit_account_id: int | None = None
    inventory_adjustment_account_id: int | None = None
    stock_count_variance_account_id: int | None = None
    cogs_account_id: int | None = None
    negative_stock_policy: NegativeStockPolicy | None = None
    default_warehouse_id: int | None = None
    clear_default_warehouse: bool = False
