"""What an order holds, and what has been done about it (P6 decision 4).

**Every figure in this module is a query.** Nothing here reads a running column, because there
is no running column to read: `sales_orders` and `purchase_orders` carry no `quantity_invoiced`
and no `quantity_received`, and the two SQL views this module reads —
`sales_order_line_quantities` and `purchase_order_line_quantities`, created in migration 0020 —
compute fulfilment from the documents that actually posted. A reversal therefore moves every
number here by construction: the invoice stops being posted, and the sum stops counting it.
There is nothing to correct and nothing for anybody to remember.

The view is the single definition of each join, which matters more than it looks. `invoiced`
and `received` are read by the order service (to refuse an over-fulfilment), by the status
refresh, by the enquiry, by the flows that pre-fill a document, and by the property suite's
brute-force check. Six readers of one join in Python is six chances for one of them to forget
that a reversed document does not count.

**Commitment, on order and available** are the same sums narrowed to *open* orders and grouped
by (item, warehouse). They are per warehouse, not per company, because a commitment against
stock in Musanze is not satisfied by stock in Kigali — the same reasoning the branch rule
(step 2) applies to the accrual, one level down.

`available = on_hand - committed` and may be **negative**, deliberately. That is a backorder,
and under `backorder_policy = allow` — the default — it is an ordinary thing for a business to
promise. Clamping it to zero would hide exactly the number the enquiry, the listing and the
line grid all exist to show.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Row, select, table
from sqlalchemy import column as sql_column
from sqlalchemy.orm import Session

from app.inventory import stock as stock_service
from app.models.inventory import Item, ItemType, StockBalance
from app.models.order_entry import (
    OPEN_PURCHASE_STATUSES,
    OPEN_SALES_STATUSES,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    SalesOrder,
    SalesOrderLine,
    SalesOrderStatus,
)

ZERO = Decimal(0)

#: The two views, as lightweight table clauses. Declared here rather than as mapped classes
#: because they are read-only aggregates with no identity: there is no such thing as "the
#: quantities row", only the answer a join gives right now.
sales_order_line_quantities = table(
    "sales_order_line_quantities",
    sql_column("company_id"),
    sql_column("sales_order_line_id"),
    sql_column("sales_order_id"),
    sql_column("item_id"),
    sql_column("warehouse_id"),
    sql_column("ordered"),
    sql_column("invoiced"),
)
purchase_order_line_quantities = table(
    "purchase_order_line_quantities",
    sql_column("company_id"),
    sql_column("purchase_order_line_id"),
    sql_column("purchase_order_id"),
    sql_column("item_id"),
    sql_column("warehouse_id"),
    sql_column("ordered"),
    sql_column("received"),
)


@dataclass(frozen=True)
class LineFulfilment:
    """One order line: what was promised, what has happened, and what is left.

    `remaining` is floored at zero and `outstanding` is not. They differ only in a state that
    should be impossible — more fulfilled than ordered — and keeping both means the guard that
    refuses to create that state reads `remaining` while the check that proves it never happened
    reads `outstanding` and can see the sign.
    """

    line_id: int
    order_id: int
    item_id: int
    warehouse_id: int
    ordered: Decimal
    fulfilled: Decimal

    @property
    def outstanding(self) -> Decimal:
        return self.ordered - self.fulfilled

    @property
    def remaining(self) -> Decimal:
        return max(self.outstanding, ZERO)

    @property
    def is_over_fulfilled(self) -> bool:
        return self.fulfilled > self.ordered


def _rows_to_fulfilment(
    rows: Iterable[Row], line_key: str, done_key: str
) -> dict[int, LineFulfilment]:
    out: dict[int, LineFulfilment] = {}
    for row in rows:
        line_id = int(getattr(row, line_key))
        out[line_id] = LineFulfilment(
            line_id=line_id,
            order_id=int(row.order_id),
            item_id=int(row.item_id),
            warehouse_id=int(row.warehouse_id),
            ordered=Decimal(row.ordered),
            fulfilled=Decimal(getattr(row, done_key)),
        )
    return out


# --- Per line -------------------------------------------------------------------------------


def sales_fulfilment(
    db: Session,
    company_id: int,
    line_ids: Sequence[int] | None = None,
    *,
    order_id: int | None = None,
) -> dict[int, LineFulfilment]:
    """Sales order line id → ordered and invoiced. Pass `line_ids`, `order_id`, or neither for
    the whole company."""
    view = sales_order_line_quantities
    statement = select(
        view.c.sales_order_line_id,
        view.c.sales_order_id.label("order_id"),
        view.c.item_id,
        view.c.warehouse_id,
        view.c.ordered,
        view.c.invoiced,
    ).where(view.c.company_id == company_id)
    if line_ids is not None:
        if not line_ids:
            return {}
        statement = statement.where(view.c.sales_order_line_id.in_(line_ids))
    if order_id is not None:
        statement = statement.where(view.c.sales_order_id == order_id)
    return _rows_to_fulfilment(
        db.execute(statement).all(), "sales_order_line_id", "invoiced"
    )


def purchase_fulfilment(
    db: Session,
    company_id: int,
    line_ids: Sequence[int] | None = None,
    *,
    order_id: int | None = None,
) -> dict[int, LineFulfilment]:
    """Purchase order line id → ordered and received."""
    view = purchase_order_line_quantities
    statement = select(
        view.c.purchase_order_line_id,
        view.c.purchase_order_id.label("order_id"),
        view.c.item_id,
        view.c.warehouse_id,
        view.c.ordered,
        view.c.received,
    ).where(view.c.company_id == company_id)
    if line_ids is not None:
        if not line_ids:
            return {}
        statement = statement.where(view.c.purchase_order_line_id.in_(line_ids))
    if order_id is not None:
        statement = statement.where(view.c.purchase_order_id == order_id)
    return _rows_to_fulfilment(
        db.execute(statement).all(), "purchase_order_line_id", "received"
    )


# --- Per (item, warehouse) --------------------------------------------------------------------


@dataclass(frozen=True)
class Position:
    """What one item is worth promising at one warehouse.

    `available` may be negative: that is a backorder, which `backorder_policy = allow` permits
    and the screens are required to show.
    """

    item_id: int
    warehouse_id: int
    on_hand: Decimal
    committed: Decimal
    on_order: Decimal

    @property
    def available(self) -> Decimal:
        return self.on_hand - self.committed

    @property
    def backordered(self) -> Decimal:
        """How much of the commitment the shelf cannot cover — the positive face of a negative
        `available`, which is the number an order screen shows on the line."""
        return max(-self.available, ZERO)


def committed_by_warehouse(
    db: Session, company_id: int, item_id: int, *, exclude_order_id: int | None = None
) -> dict[int, Decimal]:
    """Warehouse → Σ (ordered − invoiced) over the lines of **open** sales orders.

    Closed and cancelled orders drop out through the status filter, which is the whole of how
    closing an order releases what it held: there is no per-line flag to clear and no total to
    decrement.

    **Stock items only.** A commitment is a claim on a shelf, and a kit has none — it is a
    virtual bundle, and what a kit line promises is its component lines, which are summed here
    on their own rows (decision 8). Counting the parent as well would double the promise and
    would leave the kit item itself reading a permanent negative `available` on an enquiry,
    against stock that by definition can never exist. A service line commits nothing either.

    `exclude_order_id` leaves one order out. The backorder check asks "what would this order
    commit, on top of everything else?", and an *edit* is already in the set it is being
    measured against — counted twice, an order edited from 10 to 10 would look like a
    commitment of 20 and be refused for asking for what it already holds.
    """
    view = sales_order_line_quantities
    statement = (
        select(view.c.warehouse_id, view.c.ordered, view.c.invoiced)
        .join(SalesOrder, SalesOrder.id == view.c.sales_order_id)
        .join(Item, Item.id == view.c.item_id)
        .where(
            view.c.company_id == company_id,
            view.c.item_id == item_id,
            Item.item_type == ItemType.STOCK,
            SalesOrder.status.in_(OPEN_SALES_STATUSES),
        )
    )
    if exclude_order_id is not None:
        statement = statement.where(view.c.sales_order_id != exclude_order_id)
    return _sum_outstanding(db.execute(statement).all())


def on_order_by_warehouse(db: Session, company_id: int, item_id: int) -> dict[int, Decimal]:
    """Warehouse → Σ (ordered − received) over the lines of **open** purchase orders.

    Stock items only, like `committed_by_warehouse` and for the same reason: a service on order
    is a bill that has not arrived, not goods that are coming.
    """
    view = purchase_order_line_quantities
    rows = db.execute(
        select(view.c.warehouse_id, view.c.ordered, view.c.received)
        .join(PurchaseOrder, PurchaseOrder.id == view.c.purchase_order_id)
        .join(Item, Item.id == view.c.item_id)
        .where(
            view.c.company_id == company_id,
            view.c.item_id == item_id,
            Item.item_type == ItemType.STOCK,
            PurchaseOrder.status.in_(OPEN_PURCHASE_STATUSES),
        )
    ).all()
    return _sum_outstanding(rows)


def _sum_outstanding(rows: Iterable[Row]) -> dict[int, Decimal]:
    """Σ max(ordered − fulfilled, 0) per warehouse.

    Floored per line rather than in total: a line fulfilled beyond what it ordered must not
    lend its excess to a sibling line and quietly reduce what that one still commits. The
    floor is belt and braces — `invoice_exceeds_order` and `receipt_exceeds_order` refuse the
    state that would make it bite, and the property suite asserts no line ever reaches it.
    """
    totals: dict[int, Decimal] = {}
    for warehouse_id, ordered, fulfilled in rows:
        outstanding = max(Decimal(ordered) - Decimal(fulfilled), ZERO)
        key = int(warehouse_id)
        totals[key] = totals.get(key, ZERO) + outstanding
    return totals


def position(db: Session, company_id: int, item_id: int, warehouse_id: int) -> Position:
    """On hand, committed and on order for one (item, warehouse)."""
    return Position(
        item_id=item_id,
        warehouse_id=warehouse_id,
        on_hand=stock_service.location_balance(db, company_id, item_id, warehouse_id).quantity,
        committed=committed_by_warehouse(db, company_id, item_id).get(warehouse_id, ZERO),
        on_order=on_order_by_warehouse(db, company_id, item_id).get(warehouse_id, ZERO),
    )


def positions(db: Session, company_id: int, item_id: int) -> dict[int, Position]:
    """Every warehouse this item is held at, committed at or on order to.

    The union of three sets, not just the warehouses holding stock: an item with nothing on the
    shelf and 100 on order has a position at that warehouse, and an enquiry that listed only
    stocked locations would show the buyer nothing at all.
    """
    committed = committed_by_warehouse(db, company_id, item_id)
    on_order = on_order_by_warehouse(db, company_id, item_id)
    on_hand = {
        int(row.warehouse_id): row.quantity
        for row in db.scalars(
            select(StockBalance).where(
                StockBalance.company_id == company_id,
                StockBalance.item_id == item_id,
            )
        )
    }
    return {
        warehouse_id: Position(
            item_id=item_id,
            warehouse_id=warehouse_id,
            on_hand=on_hand.get(warehouse_id, ZERO),
            committed=committed.get(warehouse_id, ZERO),
            on_order=on_order.get(warehouse_id, ZERO),
        )
        for warehouse_id in set(on_hand) | set(committed) | set(on_order)
    }


def committed_and_on_hand(
    db: Session, company_id: int, item_ids: Sequence[int]
) -> tuple[dict[tuple[int, int], Decimal], dict[tuple[int, int], Decimal]]:
    """`(item, warehouse) →` committed, and `(item, warehouse) →` on hand, for several items
    at once.

    The per-item helpers above answer one item per call, which is right for an enquiry on one
    item and wrong for a **listing**: a page of fifty orders would be a query per order per
    item. Same view, same status filter, same stock-item rule — read in bulk. It lives here
    rather than in the caller because the view is the single definition of this join, and the
    module docstring says why a second reader of it in Python is a liability.
    """
    if not item_ids:
        return {}, {}
    view = sales_order_line_quantities
    rows = db.execute(
        select(view.c.item_id, view.c.warehouse_id, view.c.ordered, view.c.invoiced)
        .join(SalesOrder, SalesOrder.id == view.c.sales_order_id)
        .join(Item, Item.id == view.c.item_id)
        .where(
            view.c.company_id == company_id,
            view.c.item_id.in_(item_ids),
            Item.item_type == ItemType.STOCK,
            SalesOrder.status.in_(OPEN_SALES_STATUSES),
        )
    ).all()
    committed: dict[tuple[int, int], Decimal] = {}
    for item_id, warehouse_id, ordered, invoiced in rows:
        key = (int(item_id), int(warehouse_id))
        committed[key] = committed.get(key, ZERO) + max(
            Decimal(ordered) - Decimal(invoiced), ZERO
        )
    on_hand = {
        (int(row.item_id), int(row.warehouse_id)): row.quantity
        for row in db.scalars(
            select(StockBalance).where(
                StockBalance.company_id == company_id,
                StockBalance.item_id.in_(item_ids),
            )
        )
    }
    return committed, on_hand


# --- Statuses (the `open_amount` pattern) -------------------------------------------------------


def derived_sales_status(
    order: SalesOrder, fulfilment: dict[int, LineFulfilment]
) -> SalesOrderStatus:
    """What a sales order's status *should* be, from its lines and what has invoiced them.

    `CLOSED` and `CANCELLED` are terminal and are returned unchanged: they record a decision a
    person made, and no arithmetic over the lines can produce or undo one. Everything else is
    derived, and the stored column is checked against this by `verify_order_statuses()`.
    """
    if order.status in (SalesOrderStatus.CLOSED, SalesOrderStatus.CANCELLED):
        return order.status
    return _derived(
        [fulfilment.get(line.id) for line in order.lines],
        whole=SalesOrderStatus.INVOICED,
        part=SalesOrderStatus.PARTIALLY_INVOICED,
        none=SalesOrderStatus.OPEN,
    )


def derived_purchase_status(
    order: PurchaseOrder, fulfilment: dict[int, LineFulfilment]
) -> PurchaseOrderStatus:
    """The purchase-side mirror, against receipts rather than invoices."""
    if order.status in (PurchaseOrderStatus.CLOSED, PurchaseOrderStatus.CANCELLED):
        return order.status
    return _derived(
        [fulfilment.get(line.id) for line in order.lines],
        whole=PurchaseOrderStatus.RECEIVED,
        part=PurchaseOrderStatus.PARTIALLY_RECEIVED,
        none=PurchaseOrderStatus.OPEN,
    )


def _derived(lines: list[LineFulfilment | None], *, whole, part, none):  # noqa: ANN001, ANN202
    """Nothing fulfilled → open; every line fulfilled to the hilt → done; anything between →
    partially.

    Read **per line**, not off the totals: an order for 10 of A and 10 of B with 20 of A
    delivered is not complete, and a comparison of sums would say it was. That cannot arise
    while the over-fulfilment guards hold, and stating the rule per line means the status does
    not depend on them holding.
    """
    done = [line for line in lines if line is not None and line.fulfilled > ZERO]
    if not done:
        return none
    if all(line is not None and line.fulfilled >= line.ordered for line in lines):
        return whole
    return part


def refresh_sales_order_status(db: Session, order: SalesOrder) -> SalesOrderStatus:
    """Recompute and store the workflow column. Called by whatever changed the fulfilment."""
    fulfilment = sales_fulfilment(db, order.company_id, order_id=order.id)
    order.status = derived_sales_status(order, fulfilment)
    db.flush()
    return order.status


def refresh_purchase_order_status(db: Session, order: PurchaseOrder) -> PurchaseOrderStatus:
    fulfilment = purchase_fulfilment(db, order.company_id, order_id=order.id)
    order.status = derived_purchase_status(order, fulfilment)
    db.flush()
    return order.status


def refresh_sales_orders_for_lines(db: Session, company_id: int, line_ids: Iterable[int]) -> None:
    """Refresh every sales order the given lines belong to, each once."""
    orders = set(
        db.scalars(
            select(SalesOrderLine.order_id).where(
                SalesOrderLine.company_id == company_id,
                SalesOrderLine.id.in_(list(line_ids) or [0]),
            )
        )
    )
    for order in db.scalars(
        select(SalesOrder).where(
            SalesOrder.company_id == company_id, SalesOrder.id.in_(orders or [0])
        )
    ):
        refresh_sales_order_status(db, order)


def refresh_purchase_orders_for_lines(
    db: Session, company_id: int, line_ids: Iterable[int]
) -> None:
    orders = set(
        db.scalars(
            select(PurchaseOrderLine.order_id).where(
                PurchaseOrderLine.company_id == company_id,
                PurchaseOrderLine.id.in_(list(line_ids) or [0]),
            )
        )
    )
    for order in db.scalars(
        select(PurchaseOrder).where(
            PurchaseOrder.company_id == company_id, PurchaseOrder.id.in_(orders or [0])
        )
    ):
        refresh_purchase_order_status(db, order)
