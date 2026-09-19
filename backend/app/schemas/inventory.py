"""Inventory API schemas (P5 step 1 — masters)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.fiscalization import FiscalItemTypeCode
from app.models.inventory import ItemType, NegativeStockPolicy
from app.schemas.common import ApiModel

Money = Annotated[Decimal, Field(max_digits=20, decimal_places=6)]
NonNegativeMoney = Annotated[Decimal, Field(ge=0, max_digits=20, decimal_places=6)]
Quantity = Annotated[Decimal, Field(max_digits=20, decimal_places=6)]
NonNegativeQuantity = Annotated[Decimal, Field(ge=0, max_digits=20, decimal_places=6)]
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
    #: The authority's quantity-unit code (RRA §4.6), from the synced `fiscal_codes` table.
    #: Vinea's own unit codes are the company's; this is the one RRA reads, and a fiscalized
    #: line whose unit has none is refused with `fiscal_uom_unmapped` rather than guessed at.
    fiscal_quantity_unit: str | None = Field(default=None, max_length=5)


class UomUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    factor_to_base: Factor | None = None
    decimal_places: int | None = Field(default=None, ge=0, le=6)
    is_active: bool | None = None
    fiscal_quantity_unit: str | None = Field(default=None, max_length=5)
    clear_fiscal_quantity_unit: bool = False


class UomRead(ApiModel):
    id: int
    category_id: int
    code: str
    name: str
    factor_to_base: Decimal
    decimal_places: int
    is_base: bool
    is_active: bool
    fiscal_quantity_unit: str | None


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
    purchase_account_id: int | None = None
    selling_price: NonNegativeMoney = Decimal(0)
    price_includes_tax: bool = False
    weight_per_base_unit: PositiveQuantity | None = None
    # --- P7 fiscalization (decision 8) ----------------------------------------------------
    #: The authority's classification. No default and no guess: a class is a claim about what
    #: the thing *is* and RRA reports on it, so a fiscalized sale of an item without one is
    #: refused with `fiscal_class_missing`.
    fiscal_class_code: str | None = Field(default=None, max_length=20)
    #: ISO-3166 alpha-2. Defaults to `RW` at registration when unset.
    fiscal_origin_country: str | None = Field(default=None, min_length=2, max_length=2)
    fiscal_package_unit: str | None = Field(default=None, max_length=5)
    #: Overrides what the item type would register as — a stock item is a finished product and
    #: a service is a service unless the catalogue says otherwise.
    fiscal_item_type: FiscalItemTypeCode | None = None


class ItemUpdate(BaseModel):
    """`item_type`, the unit of measure and the inventory account are changeable only while
    the item still has no moves behind it; from the first posted move the service refuses
    them with `item_has_moves`. The rule is the one a tax rate already follows — free until
    something is posted against it, fixed from then on. See `masters.update_item`."""

    code: str | None = Field(default=None, min_length=1, max_length=30)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    item_type: ItemType | None = None
    uom_category_id: int | None = None
    base_uom_id: int | None = None
    inventory_account_id: int | None = None
    clear_inventory_account: bool = False
    cogs_account_id: int | None = None
    clear_cogs_account: bool = False
    sales_account_id: int | None = None
    clear_sales_account: bool = False
    purchase_account_id: int | None = None
    clear_purchase_account: bool = False
    default_sales_tax_code_id: int | None = None
    clear_sales_tax_code: bool = False
    default_purchase_tax_code_id: int | None = None
    clear_purchase_tax_code: bool = False
    weight_per_base_unit: PositiveQuantity | None = None
    clear_weight_per_base_unit: bool = False
    selling_price: NonNegativeMoney | None = None
    price_includes_tax: bool | None = None
    is_active: bool | None = None
    fiscal_class_code: str | None = Field(default=None, max_length=20)
    clear_fiscal_class_code: bool = False
    fiscal_origin_country: str | None = Field(default=None, min_length=2, max_length=2)
    clear_fiscal_origin_country: bool = False
    fiscal_package_unit: str | None = Field(default=None, max_length=5)
    clear_fiscal_package_unit: bool = False
    fiscal_item_type: FiscalItemTypeCode | None = None
    clear_fiscal_item_type: bool = False


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
    purchase_account_id: int | None
    default_sales_tax_code_id: int | None
    default_purchase_tax_code_id: int | None
    selling_price: Decimal
    price_includes_tax: bool
    weight_per_base_unit: Decimal | None
    is_active: bool
    fiscal_class_code: str | None
    fiscal_origin_country: str | None
    fiscal_package_unit: str | None
    fiscal_item_type: FiscalItemTypeCode | None


class KitComponentIn(BaseModel):
    component_item_id: int
    #: In the component's own base unit, so the explosion is a multiplication.
    quantity_per_kit: PositiveQuantity


class KitComponentsUpdate(BaseModel):
    """The kit's whole definition. An empty list clears it — a kit with no components is a
    kit nobody has finished defining, which the Items screen shows and an order line refuses,
    rather than something this endpoint should guess at."""

    components: list[KitComponentIn] = Field(default_factory=list)


class KitComponentRead(ApiModel):
    id: int
    kit_item_id: int
    component_item_id: int
    quantity_per_kit: Decimal
    line_no: int


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


class BarcodeListingRead(ApiModel):
    """A barcode with its item and pack resolved — what the Variable barcodes screen lists."""

    id: int
    barcode: str
    item_id: int
    item_code: str
    item_name: str
    uom_id: int
    uom_code: str
    uom_name: str
    pack_quantity: Decimal
    is_active: bool


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


class OnHandRead(ApiModel):
    """One cache row: what a warehouse holds of an item, right now."""

    item_id: int
    warehouse_id: int
    quantity: Decimal
    value: Decimal


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


# --- Stock documents (P5 step 3) -----------------------------------------------------------

Cost = Annotated[Decimal, Field(max_digits=20, decimal_places=10)]


class StockDocumentLineCreate(BaseModel):
    """One keyed line.

    `quantity` is a **magnitude**, never signed: the direction comes from the transaction
    type's kind (decision 9), so the same payload cannot mean "in" on one type and "out" on
    another. A revaluation is the exception that proves it — it moves no quantity at all and
    states a signed `value` instead.
    """

    item_id: int
    warehouse_id: int
    quantity: Quantity = Decimal(0)
    #: Defaults to the item's own base unit. Any unit of the same category converts at 6 dp.
    uom_id: int | None = None
    #: Required on an increase, refused on a decrease — an issue is costed at the average.
    unit_cost: Cost | None = None
    #: A revaluation's signed amount: positive writes the stock up, negative down.
    value: Money | None = None
    #: Omit to inherit the header's type. A batch's lines normally carry their own.
    transaction_type_id: int | None = None
    contra_account_id: int | None = None
    project_id: int | None = None
    description: str | None = Field(default=None, max_length=500)


class StockDocumentCreate(BaseModel):
    document_date: date
    description: str = Field(min_length=1, max_length=500)
    reference: str | None = Field(default=None, max_length=100)
    transaction_type_id: int | None = None
    lines: list[StockDocumentLineCreate] = Field(min_length=1)


class StockDocumentReverse(BaseModel):
    reversal_date: date
    reason: str = Field(min_length=1, max_length=500)


class StockDocumentLineRead(ApiModel):
    id: int
    line_no: int
    item_id: int
    warehouse_id: int
    quantity: Decimal
    uom_id: int
    quantity_base: Decimal
    unit_cost: Decimal | None
    #: What was **keyed**. Only a revaluation states a value, so this is null on almost every
    #: line — the engine decides an ordinary receipt's or issue's value. Read `posted_value`
    #: for what the ledger actually recorded.
    value: Decimal | None
    #: What the move behind this line was posted at. The figure a screen means by "value".
    posted_value: Decimal | None = None
    transaction_type_id: int
    contra_account_id: int | None
    project_id: int | None
    description: str | None
    #: The move this line became. Null only inside the posting transaction.
    stock_move_id: int | None


class StockDocumentSummary(ApiModel):
    id: int
    doc_type: str
    number: str
    document_date: date
    description: str
    reference: str | None
    status: str
    #: Null when nothing in the posting carried value, which is a real and legal outcome.
    journal_entry_id: int | None
    reversal_entry_id: int | None
    reverses_document_id: int | None
    #: What the lines add up to — computed by the service over the whole document, never by
    #: the screen over the rows it happens to be showing.
    line_count: int = 0
    total_value: Decimal = Decimal(0)
    #: The one warehouse / transaction type every line names, or null when they differ. A
    #: journal batch legitimately spreads across several of both (decision 9).
    warehouse_id: int | None = None
    transaction_type_id: int | None = None


class StockDocumentRead(StockDocumentSummary):
    # `transaction_type_id` comes from the summary now — on a document it is the header's own,
    # on a listing row it is the one every line agrees on. Same field, same meaning.
    lines: list[StockDocumentLineRead]


# --- Warehouse transfers (P5 step 4) --------------------------------------------------------


class TransferLineCreate(BaseModel):
    """One item to move. A magnitude: a transfer has a direction already."""

    item_id: int
    quantity: PositiveQuantity
    #: Defaults to the item's own base unit; any unit of the same category converts at 6 dp.
    uom_id: int | None = None
    description: str | None = Field(default=None, max_length=500)


class TransferCreate(BaseModel):
    transfer_date: date
    description: str = Field(min_length=1, max_length=500)
    reference: str | None = Field(default=None, max_length=100)
    from_warehouse_id: int
    to_warehouse_id: int
    #: Defaults to the company's transfer transaction type when it has exactly one.
    transaction_type_id: int | None = None
    project_id: int | None = None
    #: "Transfer now" — both legs in one transaction (decision 6). False dispatches only, and
    #: the stock waits in the in-transit warehouse until somebody receives it.
    receive_now: bool = True
    lines: list[TransferLineCreate] = Field(min_length=1)


class TransferReceive(BaseModel):
    #: Defaults to the dispatch date; never earlier than it.
    receive_date: date | None = None


class TransferCancel(BaseModel):
    cancellation_date: date | None = None
    reason: str = Field(min_length=1, max_length=500)


class TransferReverse(BaseModel):
    reversal_date: date | None = None
    reason: str = Field(min_length=1, max_length=500)


class TransferLineRead(ApiModel):
    id: int
    line_no: int
    item_id: int
    quantity: Decimal
    uom_id: int
    quantity_base: Decimal
    description: str | None
    dispatch_out_move_id: int | None
    dispatch_in_move_id: int | None
    receive_out_move_id: int | None
    receive_in_move_id: int | None


class TransferSummary(ApiModel):
    id: int
    number: str
    transfer_date: date
    description: str
    reference: str | None
    from_warehouse_id: int
    to_warehouse_id: int
    status: str
    #: Null when the leg valued nothing — stock carried at zero still moves (decision 1).
    dispatch_entry_id: int | None
    receive_entry_id: int | None
    #: The mirrors. A cancellation sets the dispatch one; reversing an arrived transfer sets
    #: both, the receive leg first (decision 11).
    dispatch_reversal_entry_id: int | None
    receive_reversal_entry_id: int | None
    received_date: date | None
    #: When it was cancelled or reversed — `status` says which.
    undone_date: date | None


class TransferRead(TransferSummary):
    transaction_type_id: int
    project_id: int | None
    lines: list[TransferLineRead]


# --- Stock counts (P5 step 4) ----------------------------------------------------------------


class CountSessionCreate(BaseModel):
    warehouse_id: int
    count_date: date
    description: str = Field(min_length=1, max_length=500)
    reference: str | None = Field(default=None, max_length=100)
    #: Defaults to the company's count-variance transaction type when it has exactly one.
    transaction_type_id: int | None = None
    project_id: int | None = None
    #: Items to put on the sheet that the warehouse does not currently hold.
    include_items: list[int] = Field(default_factory=list)
    include_zero_balances: bool = False


class CountLineCreate(BaseModel):
    item_id: int


class CountLineEntry(BaseModel):
    """What was on the shelf. `null` clears the line back to uncounted, which is not the same
    as counting it at zero — the first posts nothing, the second writes off the location."""

    counted_quantity: NonNegativeQuantity | None = None
    uom_id: int | None = None
    note: str | None = Field(default=None, max_length=500)


class CountCancel(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class CountLineRead(ApiModel):
    id: int
    line_no: int
    item_id: int
    system_quantity: Decimal
    counted_quantity: Decimal | None
    uom_id: int
    counted_quantity_base: Decimal | None
    #: `counted - system`, in the item's base unit; null while the line is uncounted. Derived
    #: on the way out, never stored.
    variance: Decimal | None
    #: The location has been posted to since this line was frozen (decision 7). Process
    #: refuses while any counted line says this.
    stale: bool
    snapshot_at: datetime
    counted_at: datetime | None
    note: str | None
    stock_move_id: int | None


class CountSessionSummary(ApiModel):
    id: int
    number: str
    warehouse_id: int
    count_date: date
    description: str
    reference: str | None
    status: str
    snapshot_at: datetime
    #: The variance document Process posted; null while counting, when cancelled, and when
    #: the count agreed with the books.
    document_id: int | None
    processed_at: datetime | None
    cancelled_at: datetime | None


class CountSessionRead(CountSessionSummary):
    transaction_type_id: int
    project_id: int | None
    lines: list[CountLineRead]


class CountPreviewLine(BaseModel):
    line_id: int
    item_id: int
    item_code: str
    item_name: str
    system_quantity: Decimal
    counted_quantity: Decimal | None
    variance: Decimal | None
    #: The item's current average — what decision 7 costs both gains and losses at.
    unit_cost: Decimal
    value: Decimal
    counted: bool
    stale: bool


class CountPreviewRead(BaseModel):
    """The posting before Process, as the allocation screen does it (decision 7)."""

    session_id: int
    number: str
    warehouse_id: int
    count_date: date
    total_value: Decimal
    counted_lines: int
    uncounted_lines: int
    variance_lines: int
    #: Empty when Process would go through. Any entry here is a line that has to be
    #: re-snapshotted and recounted first.
    stale_lines: list[int]
    can_process: bool
    lines: list[CountPreviewLine]


class CountProcessRequest(BaseModel):
    """Process takes no body — what identifies the request is the session it is processing.

    It exists so the `Idempotency-Key` fingerprint has something to be a fingerprint *of*:
    the same key sent against a different session is a different request and must be caught,
    which is the whole point of hashing the body on every other posting endpoint.
    """

    session_id: int


class CountProcessResult(BaseModel):
    session: CountSessionSummary
    #: Null when every variance was zero: a count that agrees with the books posts nothing.
    document: StockDocumentRead | None


# --- Item enquiry (P5 step 5) --------------------------------------------------------------


class EnquiryLocationRead(BaseModel):
    warehouse_id: int
    warehouse_code: str
    warehouse_name: str
    branch_id: int
    is_in_transit: bool
    quantity: Decimal
    value: Decimal
    # --- P6 (decision 4). Queries over open order lines, never columns. ---------------------
    #: Σ (ordered − invoiced) over open sales order lines at this warehouse.
    committed: Decimal = Decimal(0)
    #: Σ (ordered − received) over open purchase order lines at this warehouse.
    on_order: Decimal = Decimal(0)
    #: `quantity − committed`. **Negative is a backorder**, and the screen shows the sign: the
    #: company has promised more than it holds, which the default policy permits.
    available: Decimal = Decimal(0)


class SourceDocumentRead(BaseModel):
    """The document a move came from, resolved (P6 step 5).

    `target` is a routing key, not a URL: the screen owns its own routes and maps this to one
    of them. Values are `inventory_document`, `ar_document`, `ap_document`,
    `goods_received_note` and `landed_cost_document` — a partner document splits by its role
    here so no screen has to look the partner up to know which subledger it belongs to.
    """

    model_config = ConfigDict(from_attributes=True)

    source_doc_type: str
    source_doc_id: int
    number: str
    target: str


class EnquiryMoveRead(BaseModel):
    """One move, with every key the screen needs to drill onwards from it."""

    move_id: int
    move_date: date
    #: Posting order. Shown beside the date because the two differ for a backdated document,
    #: and when they differ it is the only thing that explains the value on the row.
    sequence_no: int
    warehouse_id: int
    warehouse_code: str
    quantity: Decimal
    unit_cost: Decimal | None
    value: Decimal
    running_quantity: Decimal
    running_value: Decimal
    #: Costed at the last positive average because there was no stock to cost it against
    #: (decision 5). The review trail, never corrected retroactively.
    cost_provisional: bool
    project_id: int | None
    journal_entry_id: int | None
    entry_number: str | None
    transaction_type_id: int | None
    transaction_type_code: str | None
    transaction_type_name: str | None
    source_doc_type: str | None
    source_doc_id: int | None
    source_line_id: int | None
    reverses_move_id: int | None
    source: SourceDocumentRead | None = None


class ItemEnquiryRead(BaseModel):
    item_id: int
    item_code: str
    item_name: str
    base_uom_id: int
    #: What kind of item this is, so the screen can say what its figures mean rather than
    #: leaving a reader to guess. A **kit** is the case that needs saying: it is a virtual
    #: bundle (decision 8), it is never on a shelf and never committed, and
    #: `committed_by_warehouse` counts stock items only — so the service reports *nothing* for
    #: one, and `locations` comes back empty. Rendered without this, that is indistinguishable
    #: from an ordinary item nobody holds, which is a different fact.
    item_type: ItemType
    as_of: date
    date_from: date | None
    warehouse_id: int | None
    provisional_only: bool
    locations: list[EnquiryLocationRead]
    #: Item-wide as at `as_of`, across every location — a warehouse filter narrows the rows,
    #: never the average.
    total_quantity: Decimal
    total_value: Decimal
    average_cost: Decimal
    opening_quantity: Decimal
    opening_value: Decimal
    moves: list[EnquiryMoveRead]
    next_cursor: int | None


# --- Reports (P5 step 5) ---------------------------------------------------------------------


class MovementRowRead(BaseModel):
    item_id: int
    item_code: str
    item_name: str
    warehouse_id: int
    warehouse_code: str
    warehouse_name: str
    branch_id: int
    is_in_transit: bool
    opening_quantity: Decimal
    opening_value: Decimal
    quantity_in: Decimal
    value_in: Decimal
    quantity_out: Decimal
    value_out: Decimal
    closing_quantity: Decimal
    closing_value: Decimal


class MovementReportRead(BaseModel):
    date_from: date
    date_to: date
    rows: list[MovementRowRead]
    next_cursor: int | None
    #: Over the whole filtered set, not over this page.
    opening_value: Decimal
    value_in: Decimal
    value_out: Decimal
    closing_value: Decimal


class TransactionRowRead(BaseModel):
    move_id: int
    move_date: date
    sequence_no: int
    item_id: int
    item_code: str
    item_name: str
    warehouse_id: int
    warehouse_code: str
    branch_id: int
    quantity: Decimal
    unit_cost: Decimal | None
    value: Decimal
    cost_provisional: bool
    project_id: int | None
    transaction_type_id: int | None
    journal_entry_id: int | None
    entry_number: str | None
    source_doc_type: str | None
    source_doc_id: int | None
    source_line_id: int | None
    source: SourceDocumentRead | None = None


class TransactionReportRead(BaseModel):
    date_from: date
    date_to: date
    rows: list[TransactionRowRead]
    next_cursor: int | None
    total_quantity: Decimal
    total_value: Decimal
    move_count: int


class ValuationRowRead(BaseModel):
    item_id: int
    item_code: str
    item_name: str
    warehouse_id: int
    warehouse_code: str
    warehouse_name: str
    branch_id: int
    is_in_transit: bool
    quantity: Decimal
    value: Decimal
    #: Value / quantity **as at `as_of`**; null at zero quantity. Not the cost anything was
    #: posted at — a backdated receipt makes the two differ — so it is never labelled "cost"
    #: on a screen. See `ValuationRow.average_as_at`.
    average_as_at: Decimal | None
    gl_account_id: int | None


class ValuationItemTotalRead(BaseModel):
    item_id: int
    item_code: str
    item_name: str
    quantity: Decimal
    value: Decimal


class ValuationWarehouseTotalRead(BaseModel):
    warehouse_id: int
    value: Decimal


class ValuationAccountTotalRead(BaseModel):
    """What the inventory account should read on `as_of`. The report states its own tie to
    the GL, so a screen can show the two side by side instead of taking it on trust."""

    gl_account_id: int | None
    code: str | None
    name: str | None
    value: Decimal


class ValuationReportRead(BaseModel):
    as_of: date
    include_zero: bool
    rows: list[ValuationRowRead]
    item_totals: list[ValuationItemTotalRead]
    warehouse_totals: list[ValuationWarehouseTotalRead]
    account_totals: list[ValuationAccountTotalRead]
    next_cursor: int | None
    total_value: Decimal


class CountVarianceRowRead(BaseModel):
    line_id: int
    line_no: int
    item_id: int
    item_code: str
    item_name: str
    system_quantity: Decimal
    counted_quantity: Decimal | None
    variance: Decimal | None
    stale: bool
    stock_move_id: int | None


class CountReportRowRead(BaseModel):
    session_id: int
    number: str
    warehouse_id: int
    warehouse_code: str
    warehouse_name: str
    branch_id: int
    count_date: date
    description: str
    status: str
    snapshot_at: datetime
    document_id: int | None
    document_number: str | None
    journal_entry_id: int | None
    line_count: int
    counted_count: int
    variance_count: int
    lines: list[CountVarianceRowRead]


class CountReportRead(BaseModel):
    rows: list[CountReportRowRead]
    next_cursor: int | None
