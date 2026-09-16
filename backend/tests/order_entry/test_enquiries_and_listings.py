"""The enquiries and listings of P6 step 5, and the drill targets they resolve.

These are the read side, and the read side is where P4 lost six defects (rule 13): a report
that read the wrong field and showed "Nothing to report" over a full subledger, a document
type nothing could action. Every one was on a screen no test had opened with data behind it.
So each test here puts real postings behind the query and asserts a **figure**, never that a
call returned without raising.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory import enquiries as inventory_enquiries
from app.inventory import masters as inventory_masters
from app.inventory import reports as inventory_reports
from app.models.inventory import GrnStatus, ItemType
from app.models.journal import JournalEntry
from app.models.order_entry import LandedCostBasis
from app.models.partner import PartnerRole, TaxMode
from app.models.subledger import DocumentKind
from app.order_entry import enquiries as oe_enquiries
from app.order_entry import grn as grn_service
from app.order_entry import landed_cost as landed_cost_service
from app.order_entry import orders as orders_service
from app.order_entry import sources as order_sources
from app.subledger import documents as documents_service
from tests.order_entry.conftest import MARCH, OrderEntry

ZERO = Decimal(0)
D = Decimal


def _receive(db: Session, fixture: OrderEntry, quantity: str, cost: str, **kwargs):  # noqa: ANN003, ANN202
    grn, _ = grn_service.post_grn(
        db,
        fixture.company_id,
        grn_service.GrnInput(
            partner_id=fixture.supplier.id,
            grn_date=MARCH,
            description="Receipt",
            warehouse_id=fixture.main.id,
            lines=(
                grn_service.GrnLineInput(
                    item_id=fixture.stock_item.id,
                    quantity=D(quantity),
                    unit_cost=D(cost),
                    **kwargs,
                ),
            ),
        ),
        actor=fixture.owner,
    )
    return grn


def _accrual(db: Session, fixture: OrderEntry) -> Decimal:
    from app.models.journal import JournalLine, JournalStatus

    rows = db.scalars(
        select(JournalLine.base_amount)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == fixture.company_id,
            JournalLine.gl_account_id == fixture.accounts["2350"].id,
            JournalEntry.status == JournalStatus.POSTED,
        )
    ).all()
    return sum(rows, ZERO)


# --- Sales order enquiry ----------------------------------------------------------------


def test_the_sales_order_enquiry_carries_the_documents_and_their_entries(
    db: Session, order_entry: OrderEntry
) -> None:
    """Order → line → the invoice raised from it → both entries that invoice posted.

    The companion is the one worth asserting: a stock-bearing invoice posts two entries, and
    an enquiry offering only the receivable would drill past the cost of the sale.
    """
    _receive(db, order_entry, "100", "1000")
    order, _ = orders_service.create_sales_order(
        db,
        order_entry.company_id,
        orders_service.SalesOrderInput(
            partner_id=order_entry.customer.id,
            order_date=MARCH,
            description="SO",
            warehouse_id=order_entry.main.id,
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                orders_service.OrderLineInput(
                    item_id=order_entry.stock_item.id, quantity=D(40), unit_price=D(2000)
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    line = order.lines[0]
    invoice, _ = documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=order_entry.customer.id,
            document_date=MARCH,
            description="Part invoice",
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=D(15),
                    unit_price=D(2000),
                    warehouse_id=order_entry.main.id,
                    sales_order_line_id=line.id,
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    enquiry = oe_enquiries.sales_order_enquiry(db, order_entry.company_id, order.id)
    row = enquiry.lines[0]
    assert (row.ordered, row.fulfilled, row.remaining) == (D(40), D(15), D(25))
    # 100 received, 25 still committed on this order — the shelf covers it, so nothing is short.
    assert row.backordered == ZERO
    assert enquiry.status == "partially_invoiced"

    assert len(enquiry.documents) == 1
    linked = enquiry.documents[0]
    assert linked.number == invoice.number
    assert linked.quantity == D(15)
    assert linked.journal_entry_id == invoice.journal_entry_id
    assert linked.journal_entry_number == db.get(JournalEntry, invoice.journal_entry_id).number
    assert linked.stock_entry_id == invoice.stock_entry_id
    assert linked.stock_entry_number == db.get(JournalEntry, invoice.stock_entry_id).number


def test_the_enquiry_shares_the_shelf_between_two_lines_of_one_order(
    db: Session, order_entry: OrderEntry
) -> None:
    """Two lines for the same item at the same warehouse split what is there rather than each
    claiming all of it.

    With 10 on hand and lines of 8 and 6, the first is covered and the second is 4 short — 4
    in total, which is what the warehouse is actually short by. Showing each line the
    item-level shortfall would report 8, and a picker would go looking for stock twice.
    """
    _receive(db, order_entry, "10", "1000")
    order, _ = orders_service.create_sales_order(
        db,
        order_entry.company_id,
        orders_service.SalesOrderInput(
            partner_id=order_entry.customer.id,
            order_date=MARCH,
            description="Two lines",
            warehouse_id=order_entry.main.id,
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                orders_service.OrderLineInput(
                    item_id=order_entry.stock_item.id, quantity=D(8), unit_price=D(2000)
                ),
                orders_service.OrderLineInput(
                    item_id=order_entry.stock_item.id, quantity=D(6), unit_price=D(2000)
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    enquiry = oe_enquiries.sales_order_enquiry(db, order_entry.company_id, order.id)
    assert [line.backordered for line in enquiry.lines] == [ZERO, D(4)]
    assert enquiry.backordered_lines == 1


def test_the_purchase_order_enquiry_lists_receipts_and_invoices_together(
    db: Session, order_entry: OrderEntry
) -> None:
    """A PO is fulfilled by two kinds of document, and the enquiry has to show both or it
    cannot answer "what happened to this order"."""
    order, _ = orders_service.create_purchase_order(
        db,
        order_entry.company_id,
        orders_service.PurchaseOrderInput(
            partner_id=order_entry.supplier.id,
            order_date=MARCH,
            description="PO",
            warehouse_id=order_entry.main.id,
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                orders_service.OrderLineInput(
                    item_id=order_entry.stock_item.id, quantity=D(50), unit_price=D(1000)
                ),
                orders_service.OrderLineInput(
                    item_id=order_entry.service_item.id, quantity=D(1), unit_price=D(5000)
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    stock_line = order.lines[0]
    service_line = order.lines[1]
    grn = _receive(db, order_entry, "20", "1000", purchase_order_line_id=stock_line.id)
    invoice, _ = documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=order_entry.supplier.id,
            document_date=MARCH,
            description="Service invoice",
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.service_item.id,
                    quantity=D(1),
                    unit_price=D(5000),
                    purchase_order_line_id=service_line.id,
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    enquiry = oe_enquiries.purchase_order_enquiry(db, order_entry.company_id, order.id)
    kinds = {row.kind for row in enquiry.documents}
    assert kinds == {"goods_received_note", DocumentKind.INVOICE}
    numbers = {row.number for row in enquiry.documents}
    assert numbers == {grn.number, invoice.number}
    # The stock line is 20 of 50 received; the service line is "received" by its invoice.
    assert [line.remaining for line in enquiry.lines] == [D(30), ZERO]


# --- Goods-received listing -------------------------------------------------------------


def test_the_goods_received_listing_ties_to_the_accrual_account(
    db: Session, order_entry: OrderEntry
) -> None:
    """Matched and unmatched value per receipt, and the total that equals the account.

    The tie is the listing's reason to exist: the accrual is provable from a screen, not only
    from the invariant suite. The sign differs on purpose — the account carries a credit
    balance, the screen shows the positive figure a person reads.
    """
    grn1 = _receive(db, order_entry, "60", "1000")
    grn2 = _receive(db, order_entry, "40", "1000")
    listing = oe_enquiries.goods_received_listing(db, order_entry.company_id)
    assert [row.received_value for row in listing.rows] == [D(60_000), D(40_000)]
    assert [row.matched_value for row in listing.rows] == [ZERO, ZERO]
    assert listing.unmatched_total == D(100_000) == -_accrual(db, order_entry)

    # Match GRN-1 in full at a different price: the relief is the frozen value, not the price.
    documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=order_entry.supplier.id,
            document_date=MARCH,
            description="INV",
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=D(60),
                    unit_price=D(1020),
                    grn_line_id=grn1.lines[0].id,
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    listing = oe_enquiries.goods_received_listing(db, order_entry.company_id)
    by_number = {row.number: row for row in listing.rows}
    assert by_number[grn1.number].matched_value == D(60_000)
    assert by_number[grn1.number].unmatched_value == ZERO
    assert by_number[grn2.number].unmatched_value == D(40_000)
    assert listing.unmatched_total == D(40_000) == -_accrual(db, order_entry)


def test_the_listing_filters_and_pages_without_losing_the_total(
    db: Session, order_entry: OrderEntry
) -> None:
    """`unmatched_total` is over the filtered set, not the page.

    A per-page total could not be compared with an account balance, which is the only thing
    the figure is for — so paging must not change it.
    """
    for _ in range(3):
        _receive(db, order_entry, "10", "1000")
    whole = oe_enquiries.goods_received_listing(db, order_entry.company_id)
    assert len(whole.rows) == 3
    assert whole.unmatched_total == D(30_000)

    first = oe_enquiries.goods_received_listing(db, order_entry.company_id, limit=2)
    assert len(first.rows) == 2
    assert first.next_cursor is not None
    assert first.unmatched_total == D(30_000), "the total is the filtered set, not the page"

    second = oe_enquiries.goods_received_listing(
        db, order_entry.company_id, cursor=first.next_cursor, limit=2
    )
    assert len(second.rows) == 1
    assert second.next_cursor is None

    # A status filter narrows both the rows and the total.
    received_only = oe_enquiries.goods_received_listing(
        db, order_entry.company_id, status=GrnStatus.MATCHED
    )
    assert received_only.rows == []
    assert received_only.unmatched_total == ZERO


def test_a_reversed_receipt_is_not_outstanding(db: Session, order_entry: OrderEntry) -> None:
    """Its accrual came back off the account with the reversal, so the listing must not still
    be claiming it."""
    grn = _receive(db, order_entry, "25", "1000")
    assert oe_enquiries.goods_received_listing(db, order_entry.company_id).unmatched_total == D(
        25_000
    )
    grn_service.reverse_grn(
        db, grn, on_date=MARCH, reason="Wrong delivery", actor=order_entry.owner
    )
    db.flush()
    listing = oe_enquiries.goods_received_listing(db, order_entry.company_id)
    assert listing.unmatched_total == ZERO == -_accrual(db, order_entry)


# --- Landed-cost listing, per GRN line ---------------------------------------------------


def test_the_landed_cost_listing_is_per_receipt_line(
    db: Session, order_entry: OrderEntry
) -> None:
    """One row per target line, carrying the share and how it was posted.

    Per-line is the grain that answers "what did this consignment cost"; the per-document
    listing at `/landed-costs` answers "what did we book", which is a different question.
    """
    grn1 = _receive(db, order_entry, "60", "1000")
    grn2 = _receive(db, order_entry, "40", "1000")
    documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=order_entry.supplier.id,
            document_date=MARCH,
            description="Freight",
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                documents_service.LineInput(
                    gl_account_id=order_entry.accounts["1370"].id,
                    quantity=D(1),
                    unit_price=D(7777),
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    document, _ = landed_cost_service.post_landed_cost(
        db,
        order_entry.company_id,
        landed_cost_service.LandedCostInput(
            cost_date=MARCH,
            description="Freight",
            amount=D(7777),
            basis=LandedCostBasis.QUANTITY,
            grn_line_ids=(grn1.lines[0].id, grn2.lines[0].id),
        ),
        actor=order_entry.owner,
    )

    listing = oe_enquiries.landed_cost_listing(db, order_entry.company_id)
    assert [row.share for row in listing.rows] == [D(4666), D(3111)]
    assert listing.total_allocated == D(7777) == document.amount
    assert {row.grn_number for row in listing.rows} == {grn1.number, grn2.number}
    assert all(row.quantity_at_posting == D(100) for row in listing.rows), (
        "both targets are the same item at the same location, which held 100"
    )
    assert all(not row.went_to_cogs for row in listing.rows)

    # Narrowed to one receipt, the listing and its total narrow together.
    one = oe_enquiries.landed_cost_listing(db, order_entry.company_id, grn_id=grn2.id)
    assert [row.share for row in one.rows] == [D(3111)]
    assert one.total_allocated == D(3111)


# --- Drill targets on the item enquiry and the Transaction report ------------------------


def test_the_item_enquiry_resolves_every_p6_source_to_a_drill_target(
    db: Session, order_entry: OrderEntry
) -> None:
    """Before this step a P6 move rendered an empty cell: the screen knew one source type.

    All four kinds are put on the shelf here and every one has to come back with a number and
    a routing key — including the split of `partner_document` into the AR and AP screens,
    which is a fact about the document rather than about the screen.
    """
    grn = _receive(db, order_entry, "60", "1000")
    sale, _ = documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=order_entry.customer.id,
            document_date=MARCH,
            description="Sale",
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=D(10),
                    unit_price=D(2000),
                    warehouse_id=order_entry.main.id,
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    purchase, _ = documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=order_entry.supplier.id,
            document_date=MARCH,
            description="Direct purchase, no receipt",
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=D(5),
                    unit_price=D(1000),
                    warehouse_id=order_entry.main.id,
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=order_entry.supplier.id,
            document_date=MARCH,
            description="Freight",
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                documents_service.LineInput(
                    gl_account_id=order_entry.accounts["1370"].id,
                    quantity=D(1),
                    unit_price=D(1000),
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    landed, _ = landed_cost_service.post_landed_cost(
        db,
        order_entry.company_id,
        landed_cost_service.LandedCostInput(
            cost_date=MARCH,
            description="Freight",
            amount=D(1000),
            basis=LandedCostBasis.QUANTITY,
            grn_line_ids=(grn.lines[0].id,),
        ),
        actor=order_entry.owner,
    )
    db.flush()

    enquiry = inventory_enquiries.item_enquiry(
        db, order_entry.company_id, order_entry.stock_item.id, as_of=MARCH
    )
    resolved = {
        move.source.number: move.source.target for move in enquiry.moves if move.source
    }
    assert resolved[grn.number] == order_sources.GOODS_RECEIVED_NOTE
    assert resolved[sale.number] == "ar_document"
    assert resolved[purchase.number] == "ap_document"
    assert resolved[landed.number] == order_sources.LANDED_COST_DOCUMENT
    # Every move on the page resolved — no P6 move renders an empty cell.
    assert all(move.source is not None for move in enquiry.moves)


def test_the_transaction_report_resolves_the_same_sources(
    db: Session, order_entry: OrderEntry
) -> None:
    """The Transaction report is the drill-down report, so a row whose source cannot be
    opened is a dead end — and after P6 most rows have a P6 source."""
    grn = _receive(db, order_entry, "30", "1000")
    report = inventory_reports.transaction_report(
        db, order_entry.company_id, date_from=MARCH, date_to=MARCH
    )
    assert report.rows
    row = report.rows[0]
    assert row.source is not None
    assert row.source.number == grn.number
    assert row.source.target == order_sources.GOODS_RECEIVED_NOTE


def test_an_unresolvable_source_is_absent_rather_than_invented(
    db: Session, order_entry: OrderEntry
) -> None:
    """`allocation` and `journal_entry` are not documents with pages of their own. A resolver
    that guessed a route for them would send a user somewhere that does not exist."""
    assert order_sources.resolve(db, order_entry.company_id, [("allocation", 1)]) == {}
    assert order_sources.resolve(db, order_entry.company_id, [("goods_received_note", 99999)]) == {}
    assert order_sources.resolve(db, order_entry.company_id, []) == {}


def test_the_sales_order_listing_carries_the_same_backorder_as_the_enquiry(
    db: Session, order_entry: OrderEntry
) -> None:
    """Decision 7 asks for the backorder on the order, the enquiry **and** the listing.

    The listing computes it in bulk — a page of orders cannot afford a query per order — so
    the risk is two implementations of one rule drifting apart. A listing that said 40 where
    the order it links to said 0 would be worse than no column at all, so the two are asserted
    against each other rather than each against a literal.
    """
    _receive(db, order_entry, "10", "1000")
    short, _ = orders_service.create_sales_order(
        db,
        order_entry.company_id,
        orders_service.SalesOrderInput(
            partner_id=order_entry.customer.id,
            order_date=MARCH,
            description="Short",
            warehouse_id=order_entry.main.id,
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                orders_service.OrderLineInput(
                    item_id=order_entry.stock_item.id, quantity=D(25), unit_price=D(2000)
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    # A second order for the same shelf: it is behind the first, so it is short by its whole
    # quantity — the first order already claimed everything there was.
    behind, _ = orders_service.create_sales_order(
        db,
        order_entry.company_id,
        orders_service.SalesOrderInput(
            partner_id=order_entry.customer.id,
            order_date=MARCH,
            description="Behind",
            warehouse_id=order_entry.main.id,
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                orders_service.OrderLineInput(
                    item_id=order_entry.stock_item.id, quantity=D(4), unit_price=D(2000)
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    listed = oe_enquiries.backordered_lines_by_order(
        db, order_entry.company_id, [short, behind]
    )
    # The listing counts the lines that are short. One each, and the count is what the listing
    # carries (step 9) — a quantity at order level could not survive an order in two units.
    assert listed[short.id] == 1
    assert listed[behind.id] == 1

    # **The apportionment behind the count is unchanged**, and it is the enquiry that shows
    # it, per line and in the line's own unit. Each order sees the shelf after every other
    # order's claim, which is the conservative answer and the only one available: nothing in
    # the system reserves, so nothing can say which of these two gets served. The 25-line sees
    # 10 less the other order's 4, so 6 are coverable and 19 are not; the 4-line sees 10 less
    # 25, so none are coverable.
    enquiries = {}
    for order in (short, behind):
        enquiry = oe_enquiries.sales_order_enquiry(db, order_entry.company_id, order.id)
        enquiries[order.id] = enquiry
        # The enquiry counts them the same way, so the two screens cannot disagree about
        # *which* lines are short even though only one of them shows by how much.
        assert enquiry.backordered_lines == listed[order.id], order.number
    assert [line.backordered for line in enquiries[short.id].lines] == [D(19)]
    assert [line.backordered for line in enquiries[behind.id].lines] == [D(4)]
    # And those two do **not** sum to the warehouse's own shortfall of 19 — they answer a
    # per-order question, and nothing may total them.
    assert D(19) + D(4) == D(23)


def test_the_listing_counts_short_lines_because_their_units_cannot_be_added(
    db: Session, order_entry: OrderEntry
) -> None:
    """The step-9 decision, and the defect it settles.

    An order for 3 kg of coffee and 2 crates of wine, with nothing on the shelf for either, is
    short on both lines. The listing used to report `5` — the sum of two base quantities in
    two different units, a number in no unit at all, which is what it would still report if
    the count were reverted to a sum. It now reports **2**: two lines are short, which is true
    whatever they are counted in and is the question the listing is actually for.

    The quantities themselves are on the order, each with its own unit, and this asserts them
    there so the decision is "moved", not "dropped".
    """
    kilogram = order_entry.inventory.base_uoms["WEIGHT"]
    coffee = inventory_masters.create_item(
        db,
        order_entry.company_id,
        inventory_masters.ItemInput(
            code="COFFEE-B",
            name="Green coffee, bulk",
            uom_category_id=order_entry.inventory.categories["WEIGHT"].id,
            base_uom_id=kilogram.id,
            item_type=ItemType.STOCK,
            selling_price=D(4000),
            sales_account_id=order_entry.accounts["4100"].id,
            cogs_account_id=order_entry.accounts["5100"].id,
        ),
        actor=order_entry.owner,
    )
    order, _ = orders_service.create_sales_order(
        db,
        order_entry.company_id,
        orders_service.SalesOrderInput(
            partner_id=order_entry.customer.id,
            order_date=MARCH,
            description="Three kilos and two crates",
            warehouse_id=order_entry.main.id,
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                orders_service.OrderLineInput(
                    item_id=coffee.id, quantity=D(3), unit_price=D(4000)
                ),
                orders_service.OrderLineInput(
                    item_id=order_entry.stock_item.id, quantity=D(2), unit_price=D(2000)
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    listed = oe_enquiries.backordered_lines_by_order(db, order_entry.company_id, [order])
    assert listed[order.id] == 2

    enquiry = oe_enquiries.sales_order_enquiry(db, order_entry.company_id, order.id)
    assert enquiry.backordered_lines == 2
    # Three kilograms and two each — and the units they are counted in, so the reader can see
    # that the 3 and the 2 are not addable and that nothing here added them.
    assert [line.backordered for line in enquiry.lines] == [D(3), D(2)]
    assert [line.uom_id for line in enquiry.lines] == [
        kilogram.id,
        order_entry.inventory.each.id,
    ]
    # The number the old sum produced, spelled out: it is what a reverted count would report,
    # and it is in neither unit.
    assert sum(line.backordered for line in enquiry.lines) == D(5)


def test_a_line_with_no_shelf_is_never_backordered(
    db: Session, order_entry: OrderEntry
) -> None:
    """A kit line and a service line report **zero**, and the enquiry and the listing agree.

    Found at step 9 by driving order-to-cash through the screens: an order for 30 bottles plus
    2 gift packs, against 25 bottles on the shelf, showed a backorder of 5 on the bottle line,
    **2 on the kit line** and 4 on the kit's bottle component — the same promise counted twice,
    once against an item that is never on a shelf and never committed (decision 8). A service
    line was worse: its whole remaining quantity, every time, because a delivery charge has no
    location balance to draw on.

    The two paths also *disagreed*, which neither docstring allowed for. The bulk path adds an
    order's own remaining back into `free`; the per-order path excludes the order from
    `committed` instead. For an item that is never in `committed` those are not the same
    arithmetic, so the listing said "none short" where the enquiry said 2.
    """
    _receive(db, order_entry, "25", "1000")
    order, _ = orders_service.create_sales_order(
        db,
        order_entry.company_id,
        orders_service.SalesOrderInput(
            partner_id=order_entry.customer.id,
            order_date=MARCH,
            description="Bottles, a gift pack and a delivery",
            warehouse_id=order_entry.main.id,
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                orders_service.OrderLineInput(
                    item_id=order_entry.stock_item.id, quantity=D(30), unit_price=D(2000)
                ),
                orders_service.OrderLineInput(
                    item_id=order_entry.kit_item.id, quantity=D(2), unit_price=D(3500)
                ),
                orders_service.OrderLineInput(
                    item_id=order_entry.service_item.id, quantity=D(1), unit_price=D(5000)
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    enquiry = oe_enquiries.sales_order_enquiry(db, order_entry.company_id, order.id)
    # Four lines, not the three that were keyed: the kit exploded at entry into two bottles per
    # pack, and the explosion is numbered before the line that followed it. Asserted as the
    # whole shape — line number, item, shortfall — so a change in either the numbering or a
    # figure fails with something readable.
    assert [(line.line_no, line.item_id, line.backordered) for line in enquiry.lines] == [
        # The bottle line takes the 25 that are there and is 5 short.
        (1, order_entry.stock_item.id, D(5)),
        # The kit itself: never on a shelf, never committed, never short.
        (2, order_entry.kit_item.id, ZERO),
        # Its four bottles find nothing left, and are short in their own right — which is
        # where a kit's promise is counted.
        (3, order_entry.stock_item.id, D(4)),
        # The delivery charge: no shelf, so nothing to be short of. This read `1` before.
        (4, order_entry.service_item.id, ZERO),
    ]

    # Two lines short, and the listing counts the same two.
    assert enquiry.backordered_lines == 2
    assert oe_enquiries.backordered_lines_by_order(
        db, order_entry.company_id, [order]
    ) == {order.id: 2}


def test_a_closed_order_is_not_credited_stock_nobody_is_holding(
    db: Session, order_entry: OrderEntry
) -> None:
    """A closed order is not in `committed` at all, so its own remaining must not be added
    back when working out what it could have drawn on.

    Closing releases the remainder (decision 3). If the apportionment credited a closed order
    its own released quantity, it would report a backorder against stock that is in fact free
    — the figure would be describing a promise nobody is keeping.
    """
    _receive(db, order_entry, "5", "1000")
    order, _ = orders_service.create_sales_order(
        db,
        order_entry.company_id,
        orders_service.SalesOrderInput(
            partner_id=order_entry.customer.id,
            order_date=MARCH,
            description="To be closed",
            warehouse_id=order_entry.main.id,
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                orders_service.OrderLineInput(
                    item_id=order_entry.stock_item.id, quantity=D(20), unit_price=D(2000)
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    before = oe_enquiries.sales_order_enquiry(db, order_entry.company_id, order.id)
    assert [line.backordered for line in before.lines] == [D(15)]
    assert before.backordered_lines == 1

    orders_service.close_sales_order(db, order, on_date=MARCH, actor=order_entry.owner)
    db.flush()
    enquiry = oe_enquiries.sales_order_enquiry(db, order_entry.company_id, order.id)
    # The remainder is released, so there is no longer a promise to be short against: the
    # 5 on the shelf now cover everything this order still shows as outstanding.
    assert [line.backordered for line in enquiry.lines] == [ZERO]
    assert enquiry.backordered_lines == 0
    assert oe_enquiries.backordered_lines_by_order(
        db, order_entry.company_id, [order]
    ) == {order.id: 0}


# --- What the enquiry reports for a kit (P6 step 8) -------------------------------------------


def test_the_item_enquiry_reports_nothing_at_all_for_a_kit(
    db: Session, order_entry: OrderEntry
) -> None:
    """A kit has no position, no commitment and no availability — and the screen has to be able
    to say so rather than print a zero.

    A kit is a virtual bundle (decision 8): it is never on a shelf, never received, and
    `committed_by_warehouse` counts stock items only, because what a kit line promises is its
    **components**, which are summed on their own rows. Counting the parent as well would
    double the promise and leave the kit reading a permanent negative `available` against stock
    that by definition can never exist.

    So the honest answer for a kit is *nothing*, and this pins it: an open sales order for 3
    kits commits 6 bottles and commits the kit itself not at all. What the enquiry must not do
    is render that as `0`, which is what an ordinary item nobody holds looks like — a different
    fact. `item_type` is on the read so the screen can tell the two apart, and that is the only
    thing it is there for.
    """
    _receive(db, order_entry, "100", "1000")
    orders_service.create_sales_order(
        db,
        order_entry.company_id,
        orders_service.SalesOrderInput(
            partner_id=order_entry.customer.id,
            order_date=MARCH,
            description="Gift packs",
            warehouse_id=order_entry.main.id,
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                orders_service.OrderLineInput(
                    item_id=order_entry.kit_item.id, quantity=D(3), unit_price=D(3500)
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    kit = inventory_enquiries.item_enquiry(
        db, order_entry.company_id, order_entry.kit_item.id, as_of=MARCH
    )
    assert kit.item.item_type == ItemType.KIT
    assert kit.locations == [], "no warehouse holds, commits or has a kit on order"
    assert (kit.total_quantity, kit.total_value) == (ZERO, ZERO)

    # And the promise did land — on the component, where the warehouse can act on it.
    component = inventory_enquiries.item_enquiry(
        db, order_entry.company_id, order_entry.stock_item.id, as_of=MARCH
    )
    main = next(row for row in component.locations if row.warehouse_id == order_entry.main.id)
    assert main.committed == D(6)
    assert main.available == D(94)
