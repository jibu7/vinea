"""Order-entry API shapes (P6).

Step 1 carries the **Order defaults** screen only. The order, GRN and landed-cost documents
arrive with the services that write them.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field

from app.models.gl import BackorderPolicy
from app.models.inventory import GrnStatus
from app.models.order_entry import PurchaseOrderStatus, SalesOrderStatus
from app.models.partner import PartnerRole, TaxMode
from app.models.subledger import DocumentKind
from app.schemas.common import ApiModel


class OrderDefaultsRead(ApiModel):
    grn_accrual_account_id: int | None
    purchase_price_variance_account_id: int | None
    landed_cost_clearing_account_id: int | None
    backorder_policy: BackorderPolicy
    #: Read-only here. Sales and purchase orders default their warehouse from it, but it is
    #: the *inventory* default and the Inventory defaults screen owns writing it — two
    #: screens writing one key is how they come to disagree.
    default_warehouse_id: int | None


class OrderDefaultsUpdate(BaseModel):
    grn_accrual_account_id: int | None = None
    purchase_price_variance_account_id: int | None = None
    landed_cost_clearing_account_id: int | None = None
    backorder_policy: BackorderPolicy | None = None


# --- Orders (P6 step 3) ------------------------------------------------------------------

Money = Annotated[Decimal, Field(max_digits=20, decimal_places=6)]
NonNegativeMoney = Annotated[Decimal, Field(ge=0, max_digits=20, decimal_places=6)]
PositiveQuantity = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=6)]
Quantity = Annotated[Decimal, Field(max_digits=20, decimal_places=6)]
Rate = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=10)]
Percent = Annotated[Decimal, Field(ge=0, lt=100, max_digits=20, decimal_places=10)]


class OrderLineIn(BaseModel):
    """One order line as a grid sends it.

    `line_id` names an existing line on an edit and is what an invoice line points at, so it has
    to survive the edit; a line without one is new. A **kit** line carries only the kit item and
    its quantity — its components are the service's to explode and Breakup's to edit.
    """

    item_id: int
    quantity: PositiveQuantity
    line_id: int | None = None
    uom_id: int | None = None
    #: Omitted on a sales line takes the item's selling price, converted between inclusive and
    #: exclusive to suit the order's tax mode. There is no purchase catalogue: an AP line with
    #: no price is priced at nothing.
    unit_price: NonNegativeMoney | None = None
    discount_percent: Percent = Decimal(0)
    tax_code_id: int | None = None
    warehouse_id: int | None = None
    project_id: int | None = None
    description: str | None = Field(default=None, max_length=500)
    #: Consent to re-explode a hand-edited kit line from the catalogue, losing what Breakup put
    #: there. Without it, changing such a line's quantity is refused with
    #: `kit_breakup_would_reset`; the Sales order screen asks before setting this.
    reset_breakup: bool = False


class SalesOrderWrite(BaseModel):
    partner_id: int
    order_date: date
    description: str = Field(min_length=1, max_length=500)
    expected_date: date | None = None
    reference: str | None = Field(default=None, max_length=500)
    currency_id: int | None = None
    exchange_rate: Rate | None = None
    branch_id: int | None = None
    project_id: int | None = None
    warehouse_id: int | None = None
    payment_terms_id: int | None = None
    sales_rep_id: int | None = None
    tax_mode: TaxMode | None = None
    lines: list[OrderLineIn] = Field(min_length=1)


class PurchaseOrderWrite(BaseModel):
    partner_id: int
    order_date: date
    description: str = Field(min_length=1, max_length=500)
    expected_date: date | None = None
    reference: str | None = Field(default=None, max_length=500)
    currency_id: int | None = None
    exchange_rate: Rate | None = None
    branch_id: int | None = None
    project_id: int | None = None
    #: The delivery warehouse. A goods receipt against this order lands in it.
    warehouse_id: int | None = None
    tax_mode: TaxMode | None = None
    lines: list[OrderLineIn] = Field(min_length=1)


class OrderTransition(BaseModel):
    """Close or cancel. The date is the day the decision was taken, not today by default:
    back-office work lags the decision it records."""

    on_date: date


class OrderLineRead(ApiModel):
    id: int
    line_no: int
    item_id: int
    description: str | None
    uom_id: int
    quantity: Decimal
    base_quantity: Decimal
    unit_price: Decimal
    discount_percent: Decimal
    tax_code_id: int | None
    net_amount: Decimal
    tax_amount: Decimal
    gross_amount: Decimal
    warehouse_id: int
    project_id: int | None


class SalesOrderLineRead(OrderLineRead):
    kit_parent_line_id: int | None
    #: True when Breakup edited this kit line's explosion. Not a total — the one fact no
    #: arithmetic over the definition can recover.
    kit_breakup_edited: bool
    #: Derived, never stored: Σ of the posted, unreversed AR invoice lines carrying this line.
    invoiced: Decimal
    remaining: Decimal


class PurchaseOrderLineRead(OrderLineRead):
    #: Derived: unreversed GRN lines, plus invoice lines that carried the goods themselves.
    received: Decimal
    remaining: Decimal


class SalesOrderRead(ApiModel):
    id: int
    number: str
    partner_id: int
    order_date: date
    expected_date: date | None
    reference: str | None
    description: str
    currency_id: int
    #: Display only — an order never values stock.
    exchange_rate: Decimal
    branch_id: int
    project_id: int | None
    warehouse_id: int
    payment_terms_id: int | None
    sales_rep_id: int | None
    tax_mode: TaxMode
    status: SalesOrderStatus
    net_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    closed_on: date | None
    cancelled_on: date | None
    lines: list[SalesOrderLineRead] = []


class PurchaseOrderRead(ApiModel):
    id: int
    number: str
    partner_id: int
    order_date: date
    expected_date: date | None
    reference: str | None
    description: str
    currency_id: int
    exchange_rate: Decimal
    branch_id: int
    project_id: int | None
    warehouse_id: int
    tax_mode: TaxMode
    status: PurchaseOrderStatus
    net_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    closed_on: date | None
    cancelled_on: date | None
    lines: list[PurchaseOrderLineRead] = []


class SalesOrderSummary(ApiModel):
    id: int
    number: str
    partner_id: int
    order_date: date
    expected_date: date | None
    currency_id: int
    status: SalesOrderStatus
    total_amount: Decimal


class PurchaseOrderSummary(ApiModel):
    id: int
    number: str
    partner_id: int
    order_date: date
    expected_date: date | None
    currency_id: int
    status: PurchaseOrderStatus
    total_amount: Decimal


class BreakupComponentIn(BaseModel):
    """`quantity` is what ships in **this** order's box, in the component's base unit — not a
    per-kit rate. Breakup edits a delivery, and asking an operator to think in rates while
    looking at one is how the wrong number gets typed."""

    item_id: int
    quantity: PositiveQuantity


class BreakupWrite(BaseModel):
    components: list[BreakupComponentIn] = Field(min_length=1)


# --- Goods receipts -----------------------------------------------------------------------


class GrnLineIn(BaseModel):
    item_id: int
    quantity: PositiveQuantity
    #: Per **keyed** unit, in the receipt's currency — what the delivery note says. Zero is a
    #: real case: a free replacement moves quantity and posts no entry.
    unit_cost: NonNegativeMoney
    uom_id: int | None = None
    warehouse_id: int | None = None
    description: str | None = Field(default=None, max_length=500)
    project_id: int | None = None
    purchase_order_line_id: int | None = None


class GrnCreate(BaseModel):
    partner_id: int
    grn_date: date
    description: str = Field(min_length=1, max_length=500)
    warehouse_id: int | None = None
    purchase_order_id: int | None = None
    #: The supplier's own delivery-note number, as written on the paper that came with the
    #: goods. Not ours, not unique, and the thing a storeman actually quotes.
    supplier_reference: str | None = Field(default=None, max_length=50)
    currency_id: int | None = None
    branch_id: int | None = None
    lines: list[GrnLineIn] = Field(min_length=1)


class GrnReverse(BaseModel):
    on_date: date
    reason: str = Field(min_length=1, max_length=500)


class GrnLineRead(ApiModel):
    id: int
    line_no: int
    purchase_order_line_id: int | None
    item_id: int
    uom_id: int
    warehouse_id: int
    description: str | None
    quantity: Decimal
    base_quantity: Decimal
    unit_cost: Decimal
    #: Frozen at receipt: what the accrual carries and the match relieves.
    value: Decimal
    project_id: int | None
    stock_move_id: int | None
    #: Derived: Σ of the posted, unreversed supplier-invoice lines carrying this line.
    matched: Decimal
    unmatched: Decimal


class GrnRead(ApiModel):
    id: int
    number: str
    partner_id: int
    purchase_order_id: int | None
    warehouse_id: int
    branch_id: int
    grn_date: date
    supplier_reference: str | None
    description: str
    currency_id: int
    exchange_rate: Decimal
    status: GrnStatus
    journal_entry_id: int | None
    reversal_entry_id: int | None
    reversed_on: date | None
    lines: list[GrnLineRead] = []


class GrnSummary(ApiModel):
    id: int
    number: str
    partner_id: int
    grn_date: date
    status: GrnStatus
    currency_id: int


# --- Prepared documents (decision 7) --------------------------------------------------------


class PreparedLineRead(BaseModel):
    """One line of a document a flow prepared but did not post."""

    item_id: int | None
    quantity: Decimal
    unit_price: Decimal | None
    discount_percent: Decimal
    tax_code_id: int | None
    warehouse_id: int | None
    project_id: int | None
    description: str | None
    sales_order_line_id: int | None = None
    purchase_order_line_id: int | None = None
    grn_line_id: int | None = None
    kit_components: list["PreparedLineRead"] | None = None
    #: What the order or receipt still had outstanding on this line, so a screen can show
    #: "5 of 20" without a second request.
    remaining: Decimal | None = None


class PreparedDocumentRead(BaseModel):
    """A document a flow built and **did not post** (decision 7).

    The screen shows it, the user changes it, and it goes back through the endpoint that posts
    that kind of document — which is where every over-fulfilment guard lives. A flow that posted
    directly would have to re-implement all of them.
    """

    role: PartnerRole
    kind: DocumentKind
    partner_id: int
    document_date: date
    description: str
    reference: str | None
    currency_id: int | None
    branch_id: int | None
    project_id: int | None
    payment_terms_id: int | None = None
    sales_rep_id: int | None = None
    tax_mode: TaxMode | None
    lines: list[PreparedLineRead]


class PreparedGrnLineRead(BaseModel):
    item_id: int
    quantity: Decimal
    unit_cost: Decimal
    warehouse_id: int | None
    description: str | None
    project_id: int | None
    purchase_order_line_id: int | None
    remaining: Decimal


class PreparedGrnRead(BaseModel):
    partner_id: int
    grn_date: date
    description: str
    warehouse_id: int | None
    purchase_order_id: int | None
    supplier_reference: str | None
    currency_id: int | None
    lines: list[PreparedGrnLineRead]
