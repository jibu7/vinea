"""Order → document (P6 decision 7).

Three actions, and none of them posts anything: each **prepares** a document and hands it back
for a person to look at, change and submit through the endpoint that already posts that kind of
document. "Invoice this order" is not a button that silently raises an invoice; it is a button
that opens an invoice with the order's open lines in it.

That split is deliberate and it is what makes the guards meaningful. The prepared quantities are
defaults — the user may lower any of them and may never raise one above what the order has left
(`invoice_exceeds_order`, `receipt_exceeds_order`), and those refusals live in the posting
services where the write happens, not here where nothing does. A flow that posted directly would
have to re-implement every one of them, and the two copies would drift.

* **Invoice** (sales order → AR invoice). Each open line defaults to its remaining quantity
  under `backorder_policy = allow`, and to `min(remaining, available)` under `block` — because
  under `block` an invoice for more than is on the shelf cannot post at all, and offering it
  would be offering a document that is refused the moment it is submitted.
* **Receive** (purchase order → GRN). The open **stock** lines, at the order's price, into the
  order's delivery warehouse. Service lines are not here: a service is received by its invoice.
* **Process invoice** (goods receipt → supplier invoice, or purchase order → supplier invoice).
  From a GRN it is the *matching* shape — one invoice line per unmatched GRN line, carrying
  `grn_line_id`, priced at what the receipt was costed at so a price that has not moved shows no
  variance. From a PO it is the open **service** lines, which have no receipt to match.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory import masters as inventory_masters
from app.kernel.errors import LedgerStateError
from app.kernel.money import round_amount
from app.models.gl import BackorderPolicy
from app.models.inventory import GoodsReceivedNote, GrnStatus, ItemType
from app.models.order_entry import (
    OPEN_PURCHASE_STATUSES,
    OPEN_SALES_STATUSES,
    PurchaseOrder,
    SalesOrder,
)
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind
from app.order_entry import grn as grn_service
from app.order_entry import quantities as order_quantities
from app.subledger import documents as documents_service

ZERO = Decimal(0)
ONE = Decimal(1)
#: `base_quantity` is NUMERIC(20, 6) everywhere it is stored.
QUANTITY_SCALE = 6


@dataclass(frozen=True)
class PreparedDocument:
    """A `DocumentInput` that has not been posted, plus what the screen needs to show it.

    `role` travels with it because the endpoint that posts it is keyed on the role, and a
    prepared supplier invoice handed to the AR endpoint would be a quiet disaster rather than a
    loud one.
    """

    role: PartnerRole
    document: documents_service.DocumentInput
    #: Per prepared line, the remaining quantity it was defaulted from — so a screen can show
    #: "5 of 20" without asking the server a second question.
    remaining: tuple[Decimal, ...]


@dataclass(frozen=True)
class PreparedReceipt:
    grn: grn_service.GrnInput
    remaining: tuple[Decimal, ...]


# --- Sales order → AR invoice --------------------------------------------------------------


def prepare_invoice_from_sales_order(
    db: Session, company_id: int, order: SalesOrder
) -> PreparedDocument:
    """Build an AR invoice from a sales order's open lines. Posts nothing.

    **Component lines carry the goods and the parent carries the money**, exactly as they do on
    the order, so the invoice reproduces the explosion the order recorded rather than exploding
    the kit again from a definition that may have moved since. That is the whole reason a kit
    line stores its components: an invoice raised six months later ships what was sold.
    """
    _assert_open(order.status, OPEN_SALES_STATUSES, order.number)
    fulfilment = order_quantities.sales_fulfilment(db, company_id, order_id=order.id)
    settings = _gl_settings(db, company_id)
    blocking = settings.backorder_policy == BackorderPolicy.BLOCK

    lines: list[documents_service.LineInput] = []
    remaining: list[Decimal] = []
    for line in order.lines:
        if line.kit_parent_line_id is not None:
            # The components come with their parent, below.
            continue
        left = _remaining(fulfilment, line.id, line.base_quantity)
        if left <= ZERO:
            continue
        quantity = _cap_to_available(db, company_id, line, left, blocking=blocking)
        if quantity <= ZERO:
            continue
        lines.append(
            documents_service.LineInput(
                item_id=line.item_id,
                # A base quantity, so the line is keyed in the item's base unit (`uom_id`
                # left unset resolves to exactly that) and the price has to be per base unit
                # to match it.
                quantity=quantity,
                unit_price=_base_unit_price(line),
                discount_percent=line.discount_percent,
                tax_code_id=line.tax_code_id,
                warehouse_id=line.warehouse_id,
                project_id=line.project_id,
                description=line.description,
                sales_order_line_id=line.id,
                kit_components=_kit_components(
                    order, line, fulfilment, invoicing=quantity, ordered=line.base_quantity
                ),
            )
        )
        remaining.append(left)
    if not lines:
        raise LedgerStateError(
            f"{order.number} has nothing left to invoice",
            code="nothing_to_invoice",
            field_errors={"order_id": ["nothing outstanding"]},
        )
    return PreparedDocument(
        role=PartnerRole.AR,
        document=documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=order.partner_id,
            document_date=order.order_date,
            description=order.description,
            reference=order.reference or order.number,
            currency_id=order.currency_id,
            branch_id=order.branch_id,
            project_id=order.project_id,
            payment_terms_id=order.payment_terms_id,
            sales_rep_id=order.sales_rep_id,
            tax_mode=order.tax_mode,
            lines=tuple(lines),
        ),
        remaining=tuple(remaining),
    )


def _kit_components(
    order: SalesOrder,
    parent,  # noqa: ANN001
    fulfilment: dict,
    *,
    invoicing: Decimal,
    ordered: Decimal,
) -> tuple[documents_service.LineInput, ...] | None:
    """The order's own explosion, scaled to the part of the kit being invoiced.

    `None` for a line that is not a kit, which is what tells the document service to explode
    from the catalogue. For a kit it is never `None` — even an unedited explosion is passed
    through, because the order is the record of what was sold and re-deriving it from a
    definition that has since moved would ship a different bundle.

    Scaling is proportional and then **capped at each component's own remaining quantity**. The
    proportion is exact whenever the kit is invoiced in full, which is the ordinary case; on a
    partial the cap is what keeps `invoice_exceeds_order` from firing on a rounding.
    """
    components = [line for line in order.lines if line.kit_parent_line_id == parent.id]
    if not components:
        return None
    share = ONE if invoicing >= ordered or ordered == ZERO else invoicing / ordered
    out: list[documents_service.LineInput] = []
    for component in components:
        left = _remaining(fulfilment, component.id, component.base_quantity)
        quantity = component.base_quantity if share == ONE else round_amount(
            component.base_quantity * share, QUANTITY_SCALE
        )
        quantity = min(quantity, left)
        if quantity <= ZERO:
            continue
        out.append(
            documents_service.LineInput(
                item_id=component.item_id,
                quantity=quantity,
                unit_price=ZERO,
                warehouse_id=component.warehouse_id,
                project_id=component.project_id,
                description=component.description,
                sales_order_line_id=component.id,
            )
        )
    return tuple(out)


def _cap_to_available(
    db: Session, company_id: int, line, left: Decimal, *, blocking: bool  # noqa: ANN001
) -> Decimal:
    """Under `block`, offer `min(remaining, available)` rather than the whole remainder.

    An invoice for more than the location holds is refused by `insufficient_stock` at the
    moment it posts, so pre-filling the full remainder under `block` would hand the operator a
    document that cannot be submitted and no explanation of why. Under `allow` the remainder is
    offered in full: a negative available is a backorder, not an error.

    A **kit** parent is not capped here. What ships is its components, and each of them is
    checked on its own row; capping the parent by the kit item's own availability would cap it
    by a number that is always zero, because a kit is never on a shelf.
    """
    if not blocking:
        return left
    item = inventory_masters.get_item(db, company_id, line.item_id)
    if item.item_type != ItemType.STOCK:
        return left
    available = order_quantities.position(
        db, company_id, line.item_id, line.warehouse_id
    ).on_hand
    return min(left, max(available, ZERO))


# --- Purchase order → GRN -------------------------------------------------------------------


def prepare_receipt_from_purchase_order(
    db: Session, company_id: int, order: PurchaseOrder
) -> PreparedReceipt:
    """Build a goods receipt from a purchase order's open **stock** lines. Posts nothing.

    Service and non-stock lines are deliberately absent: there is nothing to put on a shelf, and
    decision 4 receives them by their invoice instead. A purchase order of nothing but services
    therefore has no receipt to prepare, and says so rather than offering an empty GRN.
    """
    _assert_open(order.status, OPEN_PURCHASE_STATUSES, order.number)
    fulfilment = order_quantities.purchase_fulfilment(db, company_id, order_id=order.id)
    lines: list[grn_service.GrnLineInput] = []
    remaining: list[Decimal] = []
    for line in order.lines:
        item = inventory_masters.get_item(db, company_id, line.item_id)
        if item.item_type != ItemType.STOCK:
            continue
        left = _remaining(fulfilment, line.id, line.base_quantity)
        if left <= ZERO:
            continue
        lines.append(
            grn_service.GrnLineInput(
                item_id=line.item_id,
                quantity=left,
                # Per base unit: the remaining quantity is a base quantity, so the cost that
                # goes with it has to be too. Keying the order's price per *ordered* unit
                # against a base quantity would misprice every line keyed in cases.
                unit_cost=_base_unit_price(line),
                warehouse_id=line.warehouse_id,
                description=line.description,
                project_id=line.project_id,
                purchase_order_line_id=line.id,
            )
        )
        remaining.append(left)
    if not lines:
        raise LedgerStateError(
            f"{order.number} has no stock lines left to receive",
            code="nothing_to_receive",
            field_errors={"order_id": ["nothing outstanding"]},
        )
    return PreparedReceipt(
        grn=grn_service.GrnInput(
            partner_id=order.partner_id,
            grn_date=order.order_date,
            description=order.description,
            warehouse_id=order.warehouse_id,
            purchase_order_id=order.id,
            supplier_reference=order.reference,
            currency_id=order.currency_id,
            lines=tuple(lines),
        ),
        remaining=tuple(remaining),
    )


def _base_unit_price(line) -> Decimal:  # noqa: ANN001
    """The line's price restated per base unit, through the quantities the order already
    converted rather than through the conversion factor a second time — so the two can never
    disagree about what a case holds."""
    if line.base_quantity == ZERO:
        return ZERO
    return (line.unit_price * line.quantity) / line.base_quantity


# --- GRN / purchase order → supplier invoice -------------------------------------------------


def prepare_invoice_from_grn(
    db: Session, company_id: int, grn: GoodsReceivedNote
) -> PreparedDocument:
    """Build a supplier invoice in **matching mode** from a receipt's unmatched lines.

    One invoice line per unmatched GRN line, carrying `grn_line_id` — which is what makes it a
    match rather than a second receipt: the goods arrived on the GRN, so the invoice moves no
    stock and only says what they cost. Prices default to what the receipt was costed at, so a
    price that has not moved posts no variance and the operator changes only the lines the
    supplier actually re-priced.
    """
    if grn.status == GrnStatus.REVERSED:
        raise LedgerStateError(
            f"{grn.number} was reversed", code="grn_reversed", field_errors={"grn_id": ["reversed"]}
        )
    matched = grn_service.matched_quantities(db, company_id, [line.id for line in grn.lines])
    lines: list[documents_service.LineInput] = []
    remaining: list[Decimal] = []
    for line in grn.lines:
        left = line.base_quantity - matched.get(line.id, ZERO)
        if left <= ZERO:
            continue
        item = inventory_masters.get_item(db, company_id, line.item_id)
        lines.append(
            documents_service.LineInput(
                item_id=line.item_id,
                quantity=left,
                unit_price=line.unit_cost,
                tax_code_id=item.default_purchase_tax_code_id,
                warehouse_id=line.warehouse_id,
                project_id=line.project_id,
                description=line.description,
                grn_line_id=line.id,
                purchase_order_line_id=line.purchase_order_line_id,
            )
        )
        remaining.append(left)
    if not lines:
        raise LedgerStateError(
            f"{grn.number} is fully matched",
            code="nothing_to_match",
            field_errors={"grn_id": ["nothing unmatched"]},
        )
    return PreparedDocument(
        role=PartnerRole.AP,
        document=documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=grn.partner_id,
            document_date=grn.grn_date,
            description=grn.description,
            reference=grn.supplier_reference or grn.number,
            currency_id=grn.currency_id,
            branch_id=grn.branch_id,
            lines=tuple(lines),
        ),
        remaining=tuple(remaining),
    )


def prepare_service_invoice_from_purchase_order(
    db: Session, company_id: int, order: PurchaseOrder
) -> PreparedDocument:
    """Build a supplier invoice from a purchase order's open **service and non-stock** lines.

    These are the lines a GRN cannot carry. A service is received by its invoice (decision 4),
    so this document is both the bill and the receipt, and the line carries
    `purchase_order_line_id` — which is what `purchase_order_line_quantities` counts as received.
    """
    _assert_open(order.status, OPEN_PURCHASE_STATUSES, order.number)
    fulfilment = order_quantities.purchase_fulfilment(db, company_id, order_id=order.id)
    lines: list[documents_service.LineInput] = []
    remaining: list[Decimal] = []
    for line in order.lines:
        item = inventory_masters.get_item(db, company_id, line.item_id)
        if item.item_type == ItemType.STOCK:
            continue
        left = _remaining(fulfilment, line.id, line.base_quantity)
        if left <= ZERO:
            continue
        lines.append(
            documents_service.LineInput(
                item_id=line.item_id,
                quantity=left,
                unit_price=_base_unit_price(line),
                discount_percent=line.discount_percent,
                tax_code_id=line.tax_code_id,
                project_id=line.project_id,
                description=line.description,
                purchase_order_line_id=line.id,
            )
        )
        remaining.append(left)
    if not lines:
        raise LedgerStateError(
            f"{order.number} has no service lines left to invoice",
            code="nothing_to_invoice",
            field_errors={"order_id": ["nothing outstanding"]},
        )
    return PreparedDocument(
        role=PartnerRole.AP,
        document=documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=order.partner_id,
            document_date=order.order_date,
            description=order.description,
            reference=order.reference or order.number,
            currency_id=order.currency_id,
            branch_id=order.branch_id,
            project_id=order.project_id,
            tax_mode=order.tax_mode,
            lines=tuple(lines),
        ),
        remaining=tuple(remaining),
    )


# --- Shared ----------------------------------------------------------------------------------


def _remaining(fulfilment: dict, line_id: int, ordered: Decimal) -> Decimal:
    done = fulfilment.get(line_id)
    return ordered - done.fulfilled if done is not None else ordered


def _assert_open(status, open_statuses: tuple, number: str) -> None:  # noqa: ANN001
    if status not in open_statuses:
        raise LedgerStateError(
            f"{number} is {status.value}",
            code="order_not_open",
            field_errors={"order_id": ["order not open"]},
        )


def _gl_settings(db: Session, company_id: int):  # noqa: ANN202
    from app.kernel.posting import gl_settings_for

    return gl_settings_for(db, company_id)


def open_grns_for_supplier(db: Session, company_id: int, partner_id: int) -> list:
    """Receipts from this supplier with something still unmatched — what the GRV screen's
    "Process invoice" picker offers."""
    rows = list(
        db.scalars(
            select(GoodsReceivedNote).where(
                GoodsReceivedNote.company_id == company_id,
                GoodsReceivedNote.partner_id == partner_id,
                GoodsReceivedNote.status.in_((GrnStatus.RECEIVED, GrnStatus.PARTIALLY_MATCHED)),
            )
        )
    )
    return rows
