"""Order enquiries and the listing aggregates (P6 step 5).

Three things live here, and they share one property: **nothing in this module is a column.**
Every quantity is the view of decision 4, every value is a sum over posted rows, and a
reversal moves all of them by construction. An enquiry that cached a figure would be the
`quantity_received += …` of the reporting layer.

**The enquiry** answers "what happened to this order" — per line, what was promised, what has
been done, what is left, and how much of what is left the warehouse cannot currently cover;
and, per order, which documents were raised against it and which entries those posted, so the
screen can drill order → line → document → journal entry without a second round trip per row.

**Backordered** is the figure worth explaining, because "ordered − fulfilled on an open line"
(decision 7) is `remaining`, and the acceptance tape wants something else from it: row 5 takes
a 70-unit order against 30 on hand and expects `backordered 40`, not 70. So a backorder here
is the part of `remaining` that **stock cannot cover**, and `_backordered_by_line` says how it
is apportioned when one order has several lines competing for the same shelf.

**The listings** add the columns a document list cannot compute row by row: a goods-received
line's matched and unmatched value, and the unmatched total that has to equal the accrual
account. That total is the same claim `assert_order_invariants` makes after every posting
test, asked of the reporting path instead of the test path — if the listing ever disagrees
with the account, one of the two is reading something it should not be.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.inventory import stock as stock_service
from app.models.inventory import GoodsReceivedNote, GoodsReceivedNoteLine, GrnStatus, Item
from app.models.journal import JournalEntry
from app.models.order_entry import (
    OPEN_PURCHASE_STATUSES,
    OPEN_SALES_STATUSES,
    LandedCostDocument,
    LandedCostLine,
    PurchaseOrder,
    PurchaseOrderLine,
    SalesOrder,
    SalesOrderLine,
)
from app.models.partner import Partner
from app.models.subledger import DocumentStatus, PartnerDocument, PartnerDocumentLine
from app.order_entry import quantities as order_quantities

ZERO = Decimal(0)


# --- Enquiries --------------------------------------------------------------------------------


@dataclass(frozen=True)
class EnquiryLine:
    """One order line, and everything the enquiry screen shows about it."""

    line_id: int
    line_no: int
    item_id: int
    item_code: str
    item_name: str
    #: Nullable, because `sales_order_lines.description` is: a line that took the item's own
    #: name keys nothing here. Declared `str` until step 8 opened the screen, which made every
    #: enquiry on an ordinary order a 500 — see `EnquiryLineRead`.
    description: str | None
    uom_id: int
    warehouse_id: int | None
    quantity: Decimal
    ordered: Decimal
    fulfilled: Decimal
    remaining: Decimal
    #: The part of `remaining` this location cannot currently cover. Zero on a service line
    #: and on any line whose warehouse holds enough.
    backordered: Decimal
    unit_price: Decimal
    net_amount: Decimal
    tax_amount: Decimal
    gross_amount: Decimal
    kit_parent_line_id: int | None = None


@dataclass(frozen=True)
class LinkedDocument:
    """A document raised against the order, with the entries it posted.

    Both entry links are here because a stock-bearing partner document posts **two** entries
    (decision 2) and an enquiry that offered only one of them would drill to the receivable
    and lose the cost of sale, or the other way round.

    **Reversed documents stay in this list**, carrying their own `status`. "What happened to
    this order" includes the invoice that was raised and taken back; dropping it would leave a
    gap the user cannot account for. `quantity` is therefore what *that document* keyed, not a
    net contribution to the order — the netting lives in the line's `fulfilled`, which reads
    the view and counts only posted, unreversed documents. The two answer different questions
    and a screen should render them differently: the history struck through, the totals plain.
    """

    kind: str
    document_id: int
    number: str
    document_date: date
    status: str
    #: What this document did to the order, in the order's own units.
    quantity: Decimal
    journal_entry_id: int | None
    journal_entry_number: str | None
    stock_entry_id: int | None = None
    stock_entry_number: str | None = None


@dataclass(frozen=True)
class OrderEnquiry:
    order_id: int
    number: str
    partner_id: int
    partner_name: str
    order_date: date
    expected_date: date | None
    reference: str | None
    description: str
    currency_id: int
    exchange_rate: Decimal
    branch_id: int
    warehouse_id: int | None
    status: str
    net_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    closed_on: date | None
    cancelled_on: date | None
    lines: list[EnquiryLine]
    documents: list[LinkedDocument]

    @property
    def total_backordered(self) -> Decimal:
        return sum((line.backordered for line in self.lines), ZERO)


def _backordered_by_line(
    db: Session,
    company_id: int,
    order_id: int,
    status,  # noqa: ANN001 - SalesOrderStatus
    lines: Sequence,
    fulfilment: dict,
) -> dict[int, Decimal]:
    """How much of each sales line's remaining quantity the shelf cannot cover.

    **A backorder is a sales concept**, so only the sales enquiry calls this. A purchase order
    is the thing that *fixes* a backorder; asking how much of a PO line is "backordered" would
    have to answer with a figure derived from what *customers* have committed, which says
    nothing about the receipt and would read as a warning about the wrong document.

    **The apportionment.** `committed_by_warehouse(..., exclude_order_id=)` — built in step 3
    for the over-commitment check, and the right tool here for the same reason — gives what
    every *other* open order has claimed. What is left is what this order could draw on, and
    its lines take from that in line order: the first is covered, and a later line for the same
    item at the same warehouse is short by the rest. Showing each line the whole item-level
    shortfall instead would report more backorder than exists as soon as an order had two
    lines, and a picker would go looking for the same missing stock twice.

    Excluding this order rather than adding its own remaining back matters for an order that
    is no longer open: a closed one is not in `committed` at all, so adding it back would
    credit it stock nobody is holding for it.

    Other orders are not re-prioritised — they are already in `committed`, so this order sees
    the shelf as it is after them. The figure is a snapshot rather than a promise, which is
    what an advisory commitment is (reservation that blocks other documents is out of scope).

    **The per-order figures are therefore not additive**, and a screen must not total them.
    Two orders for 25 and 4 against 10 on the shelf report 19 and 4: each is the answer to
    "if everyone else is served first, how short am I?", and both are true. The warehouse is
    short by 19, not 23. Nothing here can say which order gets served, because nothing in the
    system reserves — so the conservative answer is given to each order rather than a priority
    invented for them.
    """
    if status not in OPEN_SALES_STATUSES:
        # **Nothing outstanding, so nothing to be short of.** The view still reports
        # `remaining` on a closed order — it is ordered less invoiced, and closing writes no
        # per-line flag (decision 3: the commitment is released by the order dropping out of
        # the open statuses). But the remainder was *given up*, so reporting a backorder
        # against it would warn about a promise nobody is keeping, and would do it while the
        # stock sits free on the shelf.
        return {}
    wanted = {
        (line.item_id, line.warehouse_id)
        for line in lines
        if line.warehouse_id is not None and fulfilment.get(line.id) is not None
    }
    if not wanted:
        return {}

    free: dict[tuple[int, int], Decimal] = {}
    for item_id, warehouse_id in wanted:
        on_hand = stock_service.location_balance(
            db, company_id, item_id, warehouse_id
        ).quantity
        others = order_quantities.committed_by_warehouse(
            db, company_id, item_id, exclude_order_id=order_id
        ).get(warehouse_id, ZERO)
        free[(item_id, warehouse_id)] = on_hand - others

    out: dict[int, Decimal] = {}
    for line in lines:
        row = fulfilment.get(line.id)
        if row is None or line.warehouse_id is None:
            continue
        key = (line.item_id, line.warehouse_id)
        takeable = max(min(row.remaining, free[key]), ZERO)
        free[key] = free[key] - takeable
        out[line.id] = row.remaining - takeable
    return out


def _entry_numbers(db: Session, company_id: int, entry_ids: set[int]) -> dict[int, str]:
    if not entry_ids:
        return {}
    return {
        int(entry_id): number
        for entry_id, number in db.execute(
            select(JournalEntry.id, JournalEntry.number).where(
                JournalEntry.company_id == company_id, JournalEntry.id.in_(entry_ids)
            )
        ).all()
    }


def _item_names(db: Session, lines: Sequence) -> dict[int, tuple[str, str]]:
    ids = {line.item_id for line in lines}
    if not ids:
        return {}
    return {
        int(row.id): (row.code, row.name)
        for row in db.scalars(select(Item).where(Item.id.in_(ids)))
    }


def sales_order_enquiry(db: Session, company_id: int, order_id: int) -> OrderEnquiry:
    """One sales order, its lines' derived quantities, and every document raised from it."""
    order = db.scalar(
        select(SalesOrder).where(SalesOrder.company_id == company_id, SalesOrder.id == order_id)
    )
    if order is None:
        raise NotFoundError("Sales order not found")

    fulfilment = order_quantities.sales_fulfilment(db, company_id, order_id=order.id)
    backordered = _backordered_by_line(
        db, company_id, order.id, order.status, order.lines, fulfilment
    )
    names = _item_names(db, order.lines)
    partner_name = db.scalar(select(Partner.name).where(Partner.id == order.partner_id))

    line_ids = [line.id for line in order.lines]
    rows = (
        db.execute(
            select(
                PartnerDocument,
                func.sum(PartnerDocumentLine.base_quantity).label("quantity"),
            )
            .join(PartnerDocumentLine, PartnerDocumentLine.document_id == PartnerDocument.id)
            .where(
                PartnerDocument.company_id == company_id,
                PartnerDocumentLine.sales_order_line_id.in_(line_ids),
            )
            .group_by(PartnerDocument.id)
            .order_by(PartnerDocument.document_date, PartnerDocument.id)
        ).all()
        if line_ids
        else []
    )
    numbers = _entry_numbers(
        db,
        company_id,
        {document.journal_entry_id for document, _ in rows if document.journal_entry_id}
        | {document.stock_entry_id for document, _ in rows if document.stock_entry_id},
    )
    documents = [
        LinkedDocument(
            kind=str(document.kind),
            document_id=document.id,
            number=document.number,
            document_date=document.document_date,
            status=str(document.status),
            quantity=quantity or ZERO,
            journal_entry_id=document.journal_entry_id,
            journal_entry_number=numbers.get(document.journal_entry_id),
            stock_entry_id=document.stock_entry_id,
            stock_entry_number=numbers.get(document.stock_entry_id),
        )
        for document, quantity in rows
    ]

    return OrderEnquiry(
        order_id=order.id,
        number=order.number,
        partner_id=order.partner_id,
        partner_name=partner_name or "",
        order_date=order.order_date,
        expected_date=order.expected_date,
        reference=order.reference,
        description=order.description,
        currency_id=order.currency_id,
        exchange_rate=order.exchange_rate,
        branch_id=order.branch_id,
        warehouse_id=order.warehouse_id,
        status=str(order.status),
        net_amount=order.net_amount,
        tax_amount=order.tax_amount,
        total_amount=order.total_amount,
        closed_on=order.closed_on,
        cancelled_on=order.cancelled_on,
        lines=[
            _enquiry_line(line, fulfilment, backordered, names) for line in order.lines
        ],
        documents=documents,
    )


def purchase_order_enquiry(db: Session, company_id: int, order_id: int) -> OrderEnquiry:
    """One purchase order — the same shape, with receipts as well as invoices.

    A PO is fulfilled by two different kinds of document (decision 4): a stock line by a GRN,
    a service line by the supplier invoice that pays for it. Both are listed, because "what
    happened to this order" has no useful answer that mentions only one of them.
    """
    order = db.scalar(
        select(PurchaseOrder).where(
            PurchaseOrder.company_id == company_id, PurchaseOrder.id == order_id
        )
    )
    if order is None:
        raise NotFoundError("Purchase order not found")

    fulfilment = order_quantities.purchase_fulfilment(db, company_id, order_id=order.id)
    # No backorder on a purchase order — see `_backordered_by_line`. The column is on the
    # shared shape and reads zero here rather than carrying a sales figure onto a receipt.
    backordered: dict[int, Decimal] = {}
    names = _item_names(db, order.lines)
    partner_name = db.scalar(select(Partner.name).where(Partner.id == order.partner_id))
    line_ids = [line.id for line in order.lines]

    documents: list[LinkedDocument] = []
    entry_ids: set[int] = set()
    grn_rows = (
        db.execute(
            select(
                GoodsReceivedNote,
                func.sum(GoodsReceivedNoteLine.base_quantity).label("quantity"),
            )
            .join(
                GoodsReceivedNoteLine,
                GoodsReceivedNoteLine.grn_id == GoodsReceivedNote.id,
            )
            .where(
                GoodsReceivedNote.company_id == company_id,
                GoodsReceivedNoteLine.purchase_order_line_id.in_(line_ids),
            )
            .group_by(GoodsReceivedNote.id)
            .order_by(GoodsReceivedNote.grn_date, GoodsReceivedNote.id)
        ).all()
        if line_ids
        else []
    )
    invoice_rows = (
        db.execute(
            select(
                PartnerDocument,
                func.sum(PartnerDocumentLine.base_quantity).label("quantity"),
            )
            .join(PartnerDocumentLine, PartnerDocumentLine.document_id == PartnerDocument.id)
            .where(
                PartnerDocument.company_id == company_id,
                PartnerDocumentLine.purchase_order_line_id.in_(line_ids),
            )
            .group_by(PartnerDocument.id)
            .order_by(PartnerDocument.document_date, PartnerDocument.id)
        ).all()
        if line_ids
        else []
    )
    entry_ids |= {grn.journal_entry_id for grn, _ in grn_rows if grn.journal_entry_id}
    entry_ids |= {d.journal_entry_id for d, _ in invoice_rows if d.journal_entry_id}
    entry_ids |= {d.stock_entry_id for d, _ in invoice_rows if d.stock_entry_id}
    numbers = _entry_numbers(db, company_id, entry_ids)

    documents.extend(
        LinkedDocument(
            kind="goods_received_note",
            document_id=grn.id,
            number=grn.number,
            document_date=grn.grn_date,
            status=str(grn.status),
            quantity=quantity or ZERO,
            journal_entry_id=grn.journal_entry_id,
            journal_entry_number=numbers.get(grn.journal_entry_id),
        )
        for grn, quantity in grn_rows
    )
    documents.extend(
        LinkedDocument(
            kind=str(document.kind),
            document_id=document.id,
            number=document.number,
            document_date=document.document_date,
            status=str(document.status),
            quantity=quantity or ZERO,
            journal_entry_id=document.journal_entry_id,
            journal_entry_number=numbers.get(document.journal_entry_id),
            stock_entry_id=document.stock_entry_id,
            stock_entry_number=numbers.get(document.stock_entry_id),
        )
        for document, quantity in invoice_rows
    )
    documents.sort(key=lambda row: (row.document_date, row.number))

    return OrderEnquiry(
        order_id=order.id,
        number=order.number,
        partner_id=order.partner_id,
        partner_name=partner_name or "",
        order_date=order.order_date,
        expected_date=order.expected_date,
        reference=order.reference,
        description=order.description,
        currency_id=order.currency_id,
        exchange_rate=order.exchange_rate,
        branch_id=order.branch_id,
        warehouse_id=order.warehouse_id,
        status=str(order.status),
        net_amount=order.net_amount,
        tax_amount=order.tax_amount,
        total_amount=order.total_amount,
        closed_on=order.closed_on,
        cancelled_on=order.cancelled_on,
        lines=[
            _enquiry_line(line, fulfilment, backordered, names) for line in order.lines
        ],
        documents=documents,
    )


def _enquiry_line(
    line,  # noqa: ANN001
    fulfilment: dict,
    backordered: dict[int, Decimal],
    names: dict[int, tuple[str, str]],
) -> EnquiryLine:
    row = fulfilment.get(line.id)
    code, name = names.get(line.item_id, ("", ""))
    return EnquiryLine(
        line_id=line.id,
        line_no=line.line_no,
        item_id=line.item_id,
        item_code=code,
        item_name=name,
        description=line.description,
        uom_id=line.uom_id,
        warehouse_id=line.warehouse_id,
        quantity=line.quantity,
        ordered=line.base_quantity,
        fulfilled=row.fulfilled if row is not None else ZERO,
        remaining=row.remaining if row is not None else line.base_quantity,
        backordered=backordered.get(line.id, ZERO),
        unit_price=line.unit_price,
        net_amount=line.net_amount,
        tax_amount=line.tax_amount,
        gross_amount=line.gross_amount,
        kit_parent_line_id=getattr(line, "kit_parent_line_id", None),
    )


def backordered_by_order(
    db: Session, company_id: int, orders: Sequence
) -> dict[int, Decimal]:
    """Total backordered per sales order, in bulk, for the **listing**.

    Decision 7 asks for the backorder on the enquiry, the listing and the line grid. The
    enquiry computes it one order at a time because it needs it per line; a listing cannot
    afford that — fifty orders a page would be a query per order — so the same rule is applied
    over two bulk reads.

    Same apportionment as `_backordered_by_line`, and it has to stay the same: a listing that
    said 40 where the order it links to said 0 would be worse than no column. The rule is
    "what every *other* open order has claimed comes first", so an order's own remaining is
    taken back out of the committed total — but only when the order is open, because a closed
    or cancelled one was never in that total to begin with.
    """
    lines = [(order, line) for order in orders for line in order.lines]
    if not lines:
        return {}
    item_ids = list({line.item_id for _order, line in lines})
    committed, on_hand = order_quantities.committed_and_on_hand(db, company_id, item_ids)
    fulfilment = order_quantities.sales_fulfilment(
        db, company_id, [line.id for _order, line in lines]
    )

    out: dict[int, Decimal] = {}
    for order in orders:
        mine: dict[tuple[int, int], Decimal] = {}
        for line in order.lines:
            row = fulfilment.get(line.id)
            if row is None or line.warehouse_id is None:
                continue
            key = (line.item_id, line.warehouse_id)
            mine[key] = mine.get(key, ZERO) + row.remaining
        if order.status not in OPEN_SALES_STATUSES:
            # Same rule as the enquiry: a released remainder is not a backorder.
            out[order.id] = ZERO
            continue
        free = {
            key: on_hand.get(key, ZERO) - (committed.get(key, ZERO) - mine[key])
            for key in mine
        }
        total = ZERO
        for line in order.lines:
            row = fulfilment.get(line.id)
            if row is None or line.warehouse_id is None:
                continue
            key = (line.item_id, line.warehouse_id)
            takeable = max(min(row.remaining, free[key]), ZERO)
            free[key] = free[key] - takeable
            total += row.remaining - takeable
        out[order.id] = total
    return out


# --- Goods-received listing ---------------------------------------------------------------------


@dataclass(frozen=True)
class GrnListRow:
    grn_id: int
    number: str
    grn_date: date
    partner_id: int
    partner_name: str
    warehouse_id: int
    branch_id: int
    status: str
    reference: str | None
    journal_entry_id: int | None
    #: Frozen base value of every line on the receipt — what it accrued.
    received_value: Decimal
    #: What supplier invoices have since relieved, read from `accrual_relieved` on the
    #: matching lines: the amount that was **posted**, never a share recomputed from today's
    #: quantities. Step 2's finding, and the reason that column exists.
    matched_value: Decimal

    @property
    def unmatched_value(self) -> Decimal:
        return self.received_value - self.matched_value


@dataclass(frozen=True)
class GrnListing:
    rows: list[GrnListRow]
    next_cursor: int | None
    #: Σ unmatched over **every** receipt the filters select, not over this page — and the
    #: figure that must equal the GRN accrual account's balance. A page total would be a
    #: different number on every page and could never be tied to anything.
    unmatched_total: Decimal


def _received_values(db: Session, company_id: int, grn_ids: Sequence[int]) -> dict[int, Decimal]:
    if not grn_ids:
        return {}
    return {
        int(grn_id): Decimal(total or 0)
        for grn_id, total in db.execute(
            select(GoodsReceivedNoteLine.grn_id, func.sum(GoodsReceivedNoteLine.value))
            .where(
                GoodsReceivedNoteLine.company_id == company_id,
                GoodsReceivedNoteLine.grn_id.in_(grn_ids),
            )
            .group_by(GoodsReceivedNoteLine.grn_id)
        ).all()
    }


def _relieved_values(db: Session, company_id: int, grn_ids: Sequence[int]) -> dict[int, Decimal]:
    """What has been matched off each receipt, from the posted relief.

    Only posted, unreversed documents count — a reversed invoice's relief came back off the
    account, so counting it here would leave the listing claiming an accrual that is not there.
    """
    if not grn_ids:
        return {}
    return {
        int(grn_id): Decimal(total or 0)
        for grn_id, total in db.execute(
            select(
                GoodsReceivedNoteLine.grn_id,
                func.sum(func.coalesce(PartnerDocumentLine.accrual_relieved, 0)),
            )
            .join(
                GoodsReceivedNoteLine,
                GoodsReceivedNoteLine.id == PartnerDocumentLine.grn_line_id,
            )
            .join(PartnerDocument, PartnerDocument.id == PartnerDocumentLine.document_id)
            .where(
                GoodsReceivedNoteLine.company_id == company_id,
                GoodsReceivedNoteLine.grn_id.in_(grn_ids),
                PartnerDocument.status == DocumentStatus.POSTED,
            )
            .group_by(GoodsReceivedNoteLine.grn_id)
        ).all()
    }


def _grn_filters(
    statement: Select,
    *,
    partner_id: int | None,
    status: GrnStatus | None,
    warehouse_id: int | None,
    date_from: date | None,
    date_to: date | None,
) -> Select:
    if partner_id is not None:
        statement = statement.where(GoodsReceivedNote.partner_id == partner_id)
    if status is not None:
        statement = statement.where(GoodsReceivedNote.status == status)
    if warehouse_id is not None:
        statement = statement.where(GoodsReceivedNote.warehouse_id == warehouse_id)
    if date_from is not None:
        statement = statement.where(GoodsReceivedNote.grn_date >= date_from)
    if date_to is not None:
        statement = statement.where(GoodsReceivedNote.grn_date <= date_to)
    return statement


def _unmatched_total(db: Session, company_id: int, filtered) -> Decimal:  # noqa: ANN001
    """Σ (received − relieved) over the filtered set, as two aggregates rather than a scan.

    The first draft of this pulled every id the filters selected and summed in Python, which
    made a listing's cost grow with the tenant's whole receipt history on **every page**. Both
    halves are group-free sums, so the database can do them.

    Reversed receipts are excluded from both halves: a reversal takes its accrual credit back
    off the account, so a reversed receipt is not outstanding and counting it would leave the
    total claiming an accrual that is not there.
    """
    live = filtered.where(GoodsReceivedNote.status != GrnStatus.REVERSED).subquery()
    received = Decimal(
        db.scalar(
            select(func.coalesce(func.sum(GoodsReceivedNoteLine.value), 0)).join(
                live, live.c.id == GoodsReceivedNoteLine.grn_id
            )
        )
        or 0
    )
    relieved = Decimal(
        db.scalar(
            select(func.coalesce(func.sum(PartnerDocumentLine.accrual_relieved), 0))
            .join(
                GoodsReceivedNoteLine,
                GoodsReceivedNoteLine.id == PartnerDocumentLine.grn_line_id,
            )
            .join(live, live.c.id == GoodsReceivedNoteLine.grn_id)
            .join(PartnerDocument, PartnerDocument.id == PartnerDocumentLine.document_id)
            .where(PartnerDocument.status == DocumentStatus.POSTED)
        )
        or 0
    )
    return received - relieved


def goods_received_listing(  # noqa: PLR0913
    db: Session,
    company_id: int,
    *,
    partner_id: int | None = None,
    status: GrnStatus | None = None,
    warehouse_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: int | None = None,
    limit: int = 50,
) -> GrnListing:
    """Receipts with what each one still has sitting in the accrual.

    The unmatched total is computed over the **filtered set**, not the page, so "the total on
    this screen equals the accrual account" is a claim the screen can make honestly — and the
    tape checks it against the account after every row rather than trusting it.
    """
    filtered = _grn_filters(
        select(GoodsReceivedNote).where(GoodsReceivedNote.company_id == company_id),
        partner_id=partner_id,
        status=status,
        warehouse_id=warehouse_id,
        date_from=date_from,
        date_to=date_to,
    )
    listed = list(
        db.scalars(
            (filtered.where(GoodsReceivedNote.id > cursor) if cursor is not None else filtered)
            .order_by(GoodsReceivedNote.id)
            .limit(limit + 1)
        )
    )
    has_more = len(listed) > limit
    page = listed[:limit]

    # Per-row values are needed for **this page only**.
    page_ids = [grn.id for grn in page]
    received = _received_values(db, company_id, page_ids)
    relieved = _relieved_values(db, company_id, page_ids)
    partners = {
        int(row.id): row.name
        for row in db.scalars(
            select(Partner).where(Partner.id.in_({grn.partner_id for grn in page} or {-1}))
        )
    }
    rows = [
        GrnListRow(
            grn_id=grn.id,
            number=grn.number,
            grn_date=grn.grn_date,
            partner_id=grn.partner_id,
            partner_name=partners.get(grn.partner_id, ""),
            warehouse_id=grn.warehouse_id,
            branch_id=grn.branch_id,
            status=str(grn.status),
            reference=grn.supplier_reference,
            journal_entry_id=grn.journal_entry_id,
            received_value=received.get(grn.id, ZERO),
            matched_value=relieved.get(grn.id, ZERO),
        )
        for grn in page
    ]
    return GrnListing(
        rows=rows,
        next_cursor=page[-1].id if has_more and page else None,
        unmatched_total=_unmatched_total(db, company_id, filtered),
    )


# --- Landed-cost listing, per GRN line ----------------------------------------------------------


@dataclass(frozen=True)
class LandedCostAllocationRow:
    """One allocation onto one receipt line — the grain the plan asks for ("landed cost per
    GRN line"), which is the grain that answers "what did this consignment actually cost"."""

    landed_cost_id: int
    number: str
    cost_date: date
    description: str
    basis: str
    status: str
    document_amount: Decimal
    line_id: int
    grn_line_id: int
    grn_id: int
    grn_number: str
    item_id: int
    warehouse_id: int
    weight: Decimal
    share: Decimal
    #: The share went to cost of sales because the location held none of the item.
    went_to_cogs: bool
    quantity_at_posting: Decimal
    stock_move_id: int | None
    journal_entry_id: int | None


@dataclass(frozen=True)
class LandedCostListing:
    rows: list[LandedCostAllocationRow]
    next_cursor: int | None
    total_allocated: Decimal


def landed_cost_listing(  # noqa: PLR0913
    db: Session,
    company_id: int,
    *,
    status: str | None = None,
    grn_id: int | None = None,
    item_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: int | None = None,
    limit: int = 50,
) -> LandedCostListing:
    statement = (
        select(
            LandedCostLine,
            LandedCostDocument,
            GoodsReceivedNote.number,
            GoodsReceivedNoteLine.grn_id,
        )
        .join(LandedCostDocument, LandedCostDocument.id == LandedCostLine.document_id)
        .join(
            GoodsReceivedNoteLine,
            GoodsReceivedNoteLine.id == LandedCostLine.grn_line_id,
        )
        .join(GoodsReceivedNote, GoodsReceivedNote.id == GoodsReceivedNoteLine.grn_id)
        .where(LandedCostLine.company_id == company_id)
    )
    if status is not None:
        statement = statement.where(LandedCostDocument.status == status)
    if grn_id is not None:
        statement = statement.where(GoodsReceivedNoteLine.grn_id == grn_id)
    if item_id is not None:
        statement = statement.where(LandedCostLine.item_id == item_id)
    if date_from is not None:
        statement = statement.where(LandedCostDocument.cost_date >= date_from)
    if date_to is not None:
        statement = statement.where(LandedCostDocument.cost_date <= date_to)

    total = Decimal(
        db.scalar(statement.with_only_columns(func.coalesce(func.sum(LandedCostLine.share), 0)))
        or 0
    )
    if cursor is not None:
        statement = statement.where(LandedCostLine.id > cursor)
    found = db.execute(statement.order_by(LandedCostLine.id).limit(limit + 1)).all()
    has_more = len(found) > limit
    page = found[:limit]

    rows = [
        LandedCostAllocationRow(
            landed_cost_id=document.id,
            number=document.number,
            cost_date=document.cost_date,
            description=document.description,
            basis=str(document.basis),
            status=str(document.status),
            document_amount=document.amount,
            line_id=line.id,
            grn_line_id=line.grn_line_id,
            grn_id=int(grn_id_of),
            grn_number=grn_number,
            item_id=line.item_id,
            warehouse_id=line.warehouse_id,
            weight=line.weight,
            share=line.share,
            went_to_cogs=line.went_to_cogs,
            quantity_at_posting=line.quantity_at_posting,
            stock_move_id=line.stock_move_id,
            journal_entry_id=document.journal_entry_id,
        )
        for line, document, grn_number, grn_id_of in page
    ]
    return LandedCostListing(
        rows=rows,
        next_cursor=page[-1][0].id if has_more and page else None,
        total_allocated=total,
    )


# --- The order reports, per line (P6 step 8) ----------------------------------------------------
#
# The two listings above answer "which receipts" and "what did the freight cost". These answer
# the question the plan's step 8 names for the order reports — *what is still outstanding* —
# and they answer it **per line**, which is the only grain that can.
#
# An order-level figure cannot. `SalesOrderSummary.backordered` sums base quantities across
# lines that may be counted in different units, so three kilograms short and two crates short
# reads five; that is a recorded step-9 finding, and the rule here is that step 8 must not add
# a second one. So nothing in this section sums a quantity across units: each row carries its
# own unit, and the totals are **subtotalled by unit** and counted, never added together. The
# same rule runs one column across for money, because an order's `exchange_rate` is display
# only (decision 3) and there is therefore no rate that could put two currencies on one line.


@dataclass(frozen=True)
class OrderLineRow:
    """One order line, its header, and what is still owed on it.

    `ordered`, `fulfilled` and `remaining` are in the **item's base unit**: they come from the
    same view the order service and the enquiry read, so the report cannot disagree with the
    screen that raised the order. `quantity` beside them is what was keyed, in `uom_id`, and
    the two differ on any line entered in something other than the base unit.
    """

    order_id: int
    number: str
    order_date: date
    expected_date: date | None
    partner_id: int
    partner_name: str
    status: str
    currency_id: int
    reference: str | None
    line_id: int
    line_no: int
    item_id: int
    item_code: str
    item_name: str
    #: The unit the line was keyed in.
    uom_id: int
    #: The unit `ordered`, `fulfilled` and `remaining` are counted in.
    base_uom_id: int
    warehouse_id: int | None
    description: str | None
    quantity: Decimal
    ordered: Decimal
    fulfilled: Decimal
    remaining: Decimal
    unit_price: Decimal
    net_amount: Decimal
    tax_amount: Decimal
    gross_amount: Decimal
    kit_parent_line_id: int | None


@dataclass(frozen=True)
class UnitSubtotal:
    """Σ ordered and Σ remaining for **one unit**, over the whole filtered set.

    A list of these rather than one number, because there is no such thing as the total of
    3 kg and 2 crates. Printing one anyway is easy, which is exactly why the rule has to be
    stated somewhere rather than left to whoever writes the next report.
    """

    uom_id: int
    ordered: Decimal
    remaining: Decimal


@dataclass(frozen=True)
class CurrencySubtotal:
    """Σ net and Σ gross for **one currency**, over the whole filtered set."""

    currency_id: int
    net_amount: Decimal
    gross_amount: Decimal


@dataclass(frozen=True)
class OrderLineListing:
    rows: list[OrderLineRow]
    next_cursor: int | None
    #: How many lines the filters select — the one total that is always a number, and the
    #: fallback the plan names: "count lines or subtotal by unit".
    line_count: int
    by_unit: list[UnitSubtotal]
    by_currency: list[CurrencySubtotal]


#: The statuses that can still owe something, per side. A closed or cancelled order owes
#: nothing by decision; an invoiced or fully received one owes nothing by arithmetic.
_OPEN_BY_SIDE = {
    "sales": OPEN_SALES_STATUSES,
    "purchase": OPEN_PURCHASE_STATUSES,
}


@dataclass(frozen=True)
class _Side:
    """Which tables the two reports differ by — everything else about them is identical."""

    view: object
    line_model: type
    order_model: type
    line_key: str
    order_key: str
    done_key: str


_SIDES = {
    "sales": _Side(
        view=order_quantities.sales_order_line_quantities,
        line_model=SalesOrderLine,
        order_model=SalesOrder,
        line_key="sales_order_line_id",
        order_key="sales_order_id",
        done_key="invoiced",
    ),
    "purchase": _Side(
        view=order_quantities.purchase_order_line_quantities,
        line_model=PurchaseOrderLine,
        order_model=PurchaseOrder,
        line_key="purchase_order_line_id",
        order_key="purchase_order_id",
        done_key="received",
    ),
}


def _order_line_filters(  # noqa: PLR0913
    statement: Select,
    side: _Side,
    *,
    company_id: int,
    partner_id: int | None,
    status: str | None,
    warehouse_id: int | None,
    item_id: int | None,
    date_from: date | None,
    date_to: date | None,
    outstanding_only: bool,
    side_name: str,
) -> Select:
    """The joins and the wheres, applied to whatever column list the caller asked for.

    One function rather than two because the page and its subtotals must select the **same**
    rows: a total computed over a set the page is not a window into is a number that agrees
    with nothing, and that divergence is invisible until somebody reconciles the report against
    the ledger — which is precisely what the goods-received report is for.
    """
    view = side.view
    done_column = getattr(view.c, side.done_key)
    # `select_from(view)` rather than letting the planner infer the first table: a subtotal
    # selects only aggregate columns, none of which names the view, and SQLAlchemy would open
    # the FROM on whichever mapped table it saw first — leaving the view dangling as a second
    # FROM entry and the join condition referring to nothing. Postgres refuses that outright,
    # which is the good case; the bad one is a query that runs and cross-joins.
    statement = (
        statement.select_from(view)
        .join(side.line_model, side.line_model.id == getattr(view.c, side.line_key))
        .join(side.order_model, side.order_model.id == getattr(view.c, side.order_key))
        .join(Item, Item.id == view.c.item_id)
        .join(Partner, Partner.id == side.order_model.partner_id)
        .where(
            view.c.company_id == company_id,
            side.line_model.company_id == company_id,
            side.order_model.company_id == company_id,
            Item.company_id == company_id,
        )
    )
    if partner_id is not None:
        statement = statement.where(side.order_model.partner_id == partner_id)
    if status is not None:
        statement = statement.where(side.order_model.status == status)
    if warehouse_id is not None:
        statement = statement.where(view.c.warehouse_id == warehouse_id)
    if item_id is not None:
        statement = statement.where(view.c.item_id == item_id)
    if date_from is not None:
        statement = statement.where(side.order_model.order_date >= date_from)
    if date_to is not None:
        statement = statement.where(side.order_model.order_date <= date_to)
    if outstanding_only:
        # Both halves are needed. The status is the **decision** — Close remaining takes the
        # order out of this set and that is the whole of how closing releases what it held
        # (decision 3) — and `ordered > fulfilled` is the **arithmetic**, which drops a line
        # that has been delivered in full while its order is still open for its siblings.
        statement = statement.where(
            side.order_model.status.in_(_OPEN_BY_SIDE[side_name]),
            view.c.ordered > done_column,
        )
    return statement


def order_line_report(  # noqa: PLR0913
    db: Session,
    company_id: int,
    *,
    side: str,
    partner_id: int | None = None,
    status: str | None = None,
    warehouse_id: int | None = None,
    item_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    outstanding_only: bool = False,
    cursor: int | None = None,
    limit: int = 50,
) -> OrderLineListing:
    """Order lines with what each one still owes — the Sales orders and Purchase orders reports.

    `outstanding_only` is the filter the *outstanding orders* invariant is stated against: a
    sales order with a backorder appears here with its remaining quantity, and after **Close
    remaining** it does not. Nothing is written to make that true and no flag is cleared — the
    order leaves `_OPEN_BY_SIDE` and the row stops being selected. The purchase side falls out
    the same way after a receipt of the full quantity, by arithmetic rather than by decision.

    The subtotals are over the **filtered set** and not the page, for the reason
    `unmatched_total` is: a page is not the report.
    """
    chosen = _SIDES[side]
    view = chosen.view
    line_id_column = getattr(view.c, chosen.line_key)
    done_column = getattr(view.c, chosen.done_key)
    #: Floored, like `_sum_outstanding`: a line delivered beyond what it ordered must not lend
    #: its excess to the subtotal and quietly reduce what a sibling still owes.
    remaining = func.greatest(view.c.ordered - done_column, 0)

    def filtered(statement: Select) -> Select:
        return _order_line_filters(
            statement,
            chosen,
            company_id=company_id,
            partner_id=partner_id,
            status=status,
            warehouse_id=warehouse_id,
            item_id=item_id,
            date_from=date_from,
            date_to=date_to,
            outstanding_only=outstanding_only,
            side_name=side,
        )

    page_statement = filtered(
        select(
            line_id_column.label("line_id"),
            view.c.ordered,
            done_column.label("fulfilled"),
            remaining.label("remaining"),
            Item.code.label("item_code"),
            Item.name.label("item_name"),
            Item.base_uom_id,
            Partner.name.label("partner_name"),
            chosen.line_model,
            chosen.order_model,
        )
    )
    if cursor is not None:
        page_statement = page_statement.where(line_id_column > cursor)
    listed = db.execute(page_statement.order_by(line_id_column).limit(limit + 1)).all()
    has_more = len(listed) > limit
    page = listed[:limit]

    rows = [
        OrderLineRow(
            order_id=order.id,
            number=order.number,
            order_date=order.order_date,
            expected_date=order.expected_date,
            partner_id=order.partner_id,
            partner_name=row.partner_name,
            status=str(order.status),
            currency_id=order.currency_id,
            reference=order.reference,
            line_id=int(row.line_id),
            line_no=line.line_no,
            item_id=line.item_id,
            item_code=row.item_code,
            item_name=row.item_name,
            uom_id=line.uom_id,
            base_uom_id=int(row.base_uom_id),
            warehouse_id=line.warehouse_id,
            description=line.description,
            quantity=line.quantity,
            ordered=Decimal(row.ordered),
            fulfilled=Decimal(row.fulfilled),
            remaining=Decimal(row.remaining),
            unit_price=line.unit_price,
            net_amount=line.net_amount,
            tax_amount=line.tax_amount,
            gross_amount=line.gross_amount,
            kit_parent_line_id=getattr(line, "kit_parent_line_id", None),
        )
        for row, line, order in (
            (
                row,
                getattr(row, chosen.line_model.__name__),
                getattr(row, chosen.order_model.__name__),
            )
            for row in page
        )
    ]

    by_unit = [
        UnitSubtotal(
            uom_id=int(uom_id), ordered=Decimal(ordered or 0), remaining=Decimal(left or 0)
        )
        for uom_id, ordered, left in db.execute(
            filtered(
                select(
                    Item.base_uom_id,
                    func.sum(view.c.ordered),
                    func.sum(remaining),
                )
            )
            .group_by(Item.base_uom_id)
            .order_by(Item.base_uom_id)
        ).all()
    ]
    by_currency = [
        CurrencySubtotal(
            currency_id=int(currency_id),
            net_amount=Decimal(net or 0),
            gross_amount=Decimal(gross or 0),
        )
        for currency_id, net, gross in db.execute(
            filtered(
                select(
                    chosen.order_model.currency_id,
                    func.sum(chosen.line_model.net_amount),
                    func.sum(chosen.line_model.gross_amount),
                )
            )
            .group_by(chosen.order_model.currency_id)
            .order_by(chosen.order_model.currency_id)
        ).all()
    ]
    line_count = int(db.scalar(filtered(select(func.count(line_id_column)))) or 0)

    return OrderLineListing(
        rows=rows,
        next_cursor=int(page[-1].line_id) if has_more and page else None,
        line_count=line_count,
        by_unit=by_unit,
        by_currency=by_currency,
    )
