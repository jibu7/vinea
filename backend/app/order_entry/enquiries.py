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
    OPEN_SALES_STATUSES,
    LandedCostDocument,
    LandedCostLine,
    PurchaseOrder,
    SalesOrder,
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
    description: str
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
