"""The order reports of P6 step 8 — what is still outstanding, per line.

Two claims are under test here and neither is about a screen.

**The outstanding filter is the arithmetic, not a flag.** A sales order with a backorder is on
the report with its remaining quantity; after Close remaining it is not. Nothing is written to
make that happen — the order leaves the open statuses and the row stops being selected
(decision 3) — so the test closes an order and asks the report again rather than asserting a
column changed.

**No total sums across units or currencies.** `SalesOrderSummary` used to add base quantities
over lines counted in different units — three kilograms short and two crates short read five —
which step 8 recorded and step 9 settled by making the listing's column a **count** of short
lines. Step 8's rule here was that the reports must not add a second one, so their totals are
subtotalled by unit and by currency, and the tests below put two units and two currencies
behind one filter to prove the split is real rather than a single number wearing a list's
clothes.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory import masters as inventory_masters
from app.models.currency import Currency
from app.models.inventory import ItemType
from app.models.order_entry import PurchaseOrderStatus, SalesOrderStatus
from app.models.partner import TaxMode
from app.order_entry import enquiries as oe_enquiries
from app.order_entry import grn as grn_service
from app.order_entry import orders as orders_service
from app.schemas.order_entry import EnquiryLineRead, OrderEnquiryRead
from tests.order_entry.conftest import MARCH, OrderEntry

D = Decimal
ZERO = Decimal(0)


def _sales_order(db: Session, fixture: OrderEntry, *lines, **header):  # noqa: ANN002, ANN003, ANN202
    order, _ = orders_service.create_sales_order(
        db,
        fixture.company_id,
        orders_service.SalesOrderInput(
            partner_id=fixture.customer.id,
            order_date=MARCH,
            description=header.pop("description", "SO"),
            warehouse_id=fixture.main.id,
            tax_mode=TaxMode.EXCLUSIVE,
            lines=tuple(lines),
            **header,
        ),
        actor=fixture.owner,
    )
    return order


def _purchase_order(db: Session, fixture: OrderEntry, *lines, **header):  # noqa: ANN002, ANN003, ANN202
    order, _ = orders_service.create_purchase_order(
        db,
        fixture.company_id,
        orders_service.PurchaseOrderInput(
            partner_id=fixture.supplier.id,
            order_date=MARCH,
            description=header.pop("description", "PO"),
            warehouse_id=fixture.main.id,
            tax_mode=TaxMode.EXCLUSIVE,
            lines=tuple(lines),
            **header,
        ),
        actor=fixture.owner,
    )
    return order


def _report(db: Session, fixture: OrderEntry, side: str, **kwargs):  # noqa: ANN003, ANN202
    return oe_enquiries.order_line_report(db, fixture.company_id, side=side, **kwargs)


# --- Outstanding: the invariant the report exists to state -------------------------------------


def test_a_backordered_sales_line_is_outstanding_until_the_order_is_closed(
    db: Session, order_entry: OrderEntry
) -> None:
    """The plan's "outstanding orders" claim, both halves, over one order.

    30 promised against an empty shelf, so the line is short and the report shows what is left.
    Close remaining is a *decision* — the quantities are given up and the history kept — and the
    row leaves the report without anything on the line being touched.
    """
    order = _sales_order(
        db,
        order_entry,
        orders_service.OrderLineInput(
            item_id=order_entry.stock_item.id, quantity=D(30), unit_price=D(2000)
        ),
    )

    outstanding = _report(db, order_entry, "sales", outstanding_only=True)
    assert [row.number for row in outstanding.rows] == [order.number]
    row = outstanding.rows[0]
    assert (row.ordered, row.fulfilled, row.remaining) == (D(30), ZERO, D(30))
    assert row.base_uom_id == order_entry.each.id
    assert outstanding.line_count == 1

    orders_service.close_sales_order(db, order, on_date=MARCH, actor=order_entry.owner)
    db.flush()

    after = _report(db, order_entry, "sales", outstanding_only=True)
    assert after.rows == []
    assert after.line_count == 0
    assert after.by_unit == []

    # The line did not go anywhere — it is outstanding no longer, which is a different thing
    # from having been deleted, and a report with no filter still has to show the history.
    everything = _report(db, order_entry, "sales")
    assert [r.number for r in everything.rows] == [order.number]
    assert everything.rows[0].status == SalesOrderStatus.CLOSED


def test_a_purchase_line_leaves_the_report_when_the_receipt_covers_it(
    db: Session, order_entry: OrderEntry
) -> None:
    """The purchase side falls out by **arithmetic**, and the order is deliberately still open.

    Two lines, and only the first is received in full. The order therefore stays
    `partially_received` — which is an *open* status — so the status filter keeps selecting it
    and the only thing that can drop the covered line is `ordered > received` on the line
    itself.

    That distinction is the reason this test has two lines rather than one. Written with a
    single line it passed with the arithmetic clause deleted: a one-line order that is fully
    received is no longer `open` either, so the status filter was doing all the work and the
    test could not tell the two guards apart. Found by reverting the clause and watching
    nothing fail.
    """
    order = _purchase_order(
        db,
        order_entry,
        orders_service.OrderLineInput(
            item_id=order_entry.stock_item.id, quantity=D(100), unit_price=D(1000)
        ),
        orders_service.OrderLineInput(
            item_id=order_entry.weighted_item.id, quantity=D(20), unit_price=D(5000)
        ),
    )
    bottles, cases = order.lines[0], order.lines[1]

    def receive(line, item_id: int, quantity: str) -> None:  # noqa: ANN001
        grn_service.post_grn(
            db,
            order_entry.company_id,
            grn_service.GrnInput(
                partner_id=order_entry.supplier.id,
                grn_date=MARCH,
                description="Receipt",
                warehouse_id=order_entry.main.id,
                purchase_order_id=order.id,
                lines=(
                    grn_service.GrnLineInput(
                        item_id=item_id,
                        quantity=D(quantity),
                        unit_cost=D(1000),
                        purchase_order_line_id=line.id,
                    ),
                ),
            ),
            actor=order_entry.owner,
        )
        db.flush()

    receive(bottles, order_entry.stock_item.id, "60")
    partial = _report(db, order_entry, "purchase", outstanding_only=True)
    assert {row.line_id: row.remaining for row in partial.rows} == {
        bottles.id: D(40),
        cases.id: D(20),
    }

    # The bottles arrive in full. Nobody closed anything, and the order is still open for its
    # other line — but this one owes nothing now, so it is not outstanding.
    receive(bottles, order_entry.stock_item.id, "40")
    covered = _report(db, order_entry, "purchase", outstanding_only=True)
    assert order.status == PurchaseOrderStatus.PARTIALLY_RECEIVED, "still open for the cases"
    assert {row.line_id: row.remaining for row in covered.rows} == {cases.id: D(20)}


def test_a_cancelled_order_owes_nothing(db: Session, order_entry: OrderEntry) -> None:
    """Cancel is the other terminal status, and it drops out of the report the same way Close
    does — through the status set, with no per-line flag to clear.

    The same exclusion the item enquiry's committed and on-order columns rely on, asked of the
    reporting path: if the two ever disagreed, one of them is reading something it should not.
    """
    order = _sales_order(
        db,
        order_entry,
        orders_service.OrderLineInput(
            item_id=order_entry.stock_item.id, quantity=D(12), unit_price=D(2000)
        ),
    )
    assert _report(db, order_entry, "sales", outstanding_only=True).line_count == 1

    orders_service.cancel_sales_order(db, order, on_date=MARCH, actor=order_entry.owner)
    db.flush()
    assert _report(db, order_entry, "sales", outstanding_only=True).line_count == 0


# --- The totals: subtotalled, never summed -----------------------------------------------------


def test_quantities_are_subtotalled_by_unit_and_never_added_together(
    db: Session, order_entry: OrderEntry
) -> None:
    """Two items counted in two different units give **two** subtotal rows, not one sum.

    This is the whole of the rule. A report that added 20 of something counted in cases to 30
    of something counted singly would print 50, which is not a quantity of anything — and it
    would look exactly like a correct report until somebody went looking for fifty of
    something.
    """
    kilogram = order_entry.inventory.base_uoms["WEIGHT"]
    by_weight = inventory_masters.create_item(
        db,
        order_entry.company_id,
        inventory_masters.ItemInput(
            code="GRAPES",
            name="Grapes, loose",
            uom_category_id=order_entry.inventory.categories["WEIGHT"].id,
            base_uom_id=kilogram.id,
            item_type=ItemType.STOCK,
            selling_price=D(900),
            sales_account_id=order_entry.accounts["4100"].id,
            cogs_account_id=order_entry.accounts["5100"].id,
        ),
        actor=order_entry.owner,
    )
    _sales_order(
        db,
        order_entry,
        orders_service.OrderLineInput(
            item_id=order_entry.stock_item.id, quantity=D(30), unit_price=D(2000)
        ),
        orders_service.OrderLineInput(
            item_id=by_weight.id, quantity=D(20), unit_price=D(900)
        ),
    )

    report = _report(db, order_entry, "sales", outstanding_only=True)
    assert report.line_count == 2
    subtotals = {row.uom_id: row.remaining for row in report.by_unit}
    assert subtotals == {order_entry.each.id: D(30), kilogram.id: D(20)}
    # And the shape says so: two rows, each naming its unit. There is nowhere on this object
    # to put 50 — which is what a single total would have read, over 30 bottles and 20 kilos.
    assert len(report.by_unit) == 2


def test_money_is_subtotalled_by_currency(db: Session, order_entry: OrderEntry) -> None:
    """Orders in two currencies give two money subtotals.

    An order's `exchange_rate` is display only (decision 3) — stock is never valued at it — so
    there is no rate on this report that could honestly put RWF and USD on one line.
    """
    usd = db.scalar(
        select(Currency).where(
            Currency.company_id == order_entry.company_id, Currency.code == "USD"
        )
    )
    if usd is None:
        pytest.skip("no USD in the seed pack")

    _sales_order(
        db,
        order_entry,
        orders_service.OrderLineInput(
            item_id=order_entry.stock_item.id, quantity=D(10), unit_price=D(2000)
        ),
        description="Home currency",
    )
    _sales_order(
        db,
        order_entry,
        orders_service.OrderLineInput(
            item_id=order_entry.stock_item.id, quantity=D(5), unit_price=D(20)
        ),
        description="Export",
        currency_id=usd.id,
        exchange_rate=D("1300"),
    )

    report = _report(db, order_entry, "sales", outstanding_only=True)
    subtotals = {row.currency_id: row.net_amount for row in report.by_currency}
    assert len(subtotals) == 2
    assert subtotals[usd.id] == D(100)


def test_the_subtotals_cover_the_filtered_set_and_not_the_page(
    db: Session, order_entry: OrderEntry
) -> None:
    """A page is not the report.

    The goods-received listing states this for `unmatched_total` because the accrual tie
    depends on it; the order reports need it for the same reason one level down — a total that
    changed as you paged could be reconciled against nothing.
    """
    _sales_order(
        db,
        order_entry,
        orders_service.OrderLineInput(
            item_id=order_entry.stock_item.id, quantity=D(7), unit_price=D(2000)
        ),
        orders_service.OrderLineInput(
            item_id=order_entry.stock_item.id, quantity=D(5), unit_price=D(2000)
        ),
        orders_service.OrderLineInput(
            item_id=order_entry.stock_item.id, quantity=D(3), unit_price=D(2000)
        ),
    )

    first = _report(db, order_entry, "sales", outstanding_only=True, limit=2)
    assert len(first.rows) == 2
    assert first.next_cursor is not None
    assert first.line_count == 3
    assert [row.remaining for row in first.by_unit] == [D(15)]

    second = _report(
        db, order_entry, "sales", outstanding_only=True, limit=2, cursor=first.next_cursor
    )
    assert len(second.rows) == 1
    assert second.next_cursor is None
    # Unchanged between pages, which is what makes it a figure anyone can tie to.
    assert [row.remaining for row in second.by_unit] == [D(15)]


def test_a_kit_line_reports_beside_its_components(
    db: Session, order_entry: OrderEntry
) -> None:
    """The explosion is on the report, parent and components both, and says which is which.

    A report that listed only the parent would understate what the warehouse has to find; one
    that listed only the components would lose the line the customer actually ordered. The
    `kit_parent_line_id` on the component rows is how a screen indents them under their parent
    rather than showing two unrelated promises of the same goods.
    """
    _sales_order(
        db,
        order_entry,
        orders_service.OrderLineInput(
            item_id=order_entry.kit_item.id, quantity=D(3), unit_price=D(3500)
        ),
    )
    rows = _report(db, order_entry, "sales", outstanding_only=True).rows
    parents = [row for row in rows if row.kit_parent_line_id is None]
    components = [row for row in rows if row.kit_parent_line_id is not None]
    assert [row.item_id for row in parents] == [order_entry.kit_item.id]
    assert [row.item_id for row in components] == [order_entry.stock_item.id]
    # 3 kits of 2 bottles each — the quantity the shelf is asked for.
    assert components[0].remaining == D(6)
    assert components[0].kit_parent_line_id == parents[0].line_id
    # And the revenue is the parent's alone; a component that priced itself would sell the
    # same goods twice.
    assert components[0].net_amount == ZERO
    assert parents[0].net_amount == D(10500)


# --- What the screens found in the step-5 endpoints --------------------------------------------


def test_the_enquiry_serialises_a_line_that_was_given_no_description(
    db: Session, order_entry: OrderEntry
) -> None:
    """An order line with no description of its own must come back through the API shape.

    `order_lines.description` is optional — a line happy with the item's own name keys nothing
    — and `EnquiryLineRead` required a string, so `GET /oe/sales-orders/{id}/enquiry` answered
    **500** for any such order. Which is most of them: neither the order screen nor the
    order-to-invoice flow fills the column unless somebody types into it.

    The endpoint shipped at step 5 and step 8 is what built its screen. Rule 14 counts mutating
    endpoints and these are GETs, so nothing was ever going to notice — which is the same
    failure one layer down from the one rule 13 is written against: it was not a screen that
    rendered wrong, it was a service that had never been asked a question.

    Asserted through the **Pydantic shape**, not the dataclass: the dataclass was always happy
    with `None`, and validating the response model is the step that failed.
    """
    order = _sales_order(
        db,
        order_entry,
        orders_service.OrderLineInput(
            item_id=order_entry.stock_item.id, quantity=D(4), unit_price=D(2000)
        ),
    )
    assert order.lines[0].description is None, "the fixture must not fill it in"

    enquiry = oe_enquiries.sales_order_enquiry(db, order_entry.company_id, order.id)
    read = OrderEnquiryRead(
        **{
            field: getattr(enquiry, field)
            for field in OrderEnquiryRead.model_fields
            if field not in ("lines", "documents", "backordered_lines")
        },
        lines=[EnquiryLineRead.model_validate(line) for line in enquiry.lines],
        documents=[],
        backordered_lines=enquiry.backordered_lines,
    )
    assert read.lines[0].description is None
    assert read.lines[0].remaining == D(4)
