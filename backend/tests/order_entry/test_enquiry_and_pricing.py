"""Two things step 3 adds that nothing else asserts: what the item enquiry now says, and the
catalogue price conversion of decision 1.

The enquiry tests carry the figures the step report quotes. They are written as literals worked
by hand, never computed from the code under test.
"""

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.inventory import enquiries as inventory_enquiries
from app.inventory import masters as inventory_masters
from app.models.partner import TaxMode
from app.order_entry import grn as grn_service
from app.order_entry import orders as orders_service
from app.order_entry import pricing
from tests.order_entry.conftest import MARCH, OrderEntry

ZERO = Decimal(0)


def _receive(db: Session, fixture: OrderEntry, warehouse_id: int, quantity: Decimal) -> None:
    grn_service.post_grn(
        db,
        fixture.company_id,
        grn_service.GrnInput(
            partner_id=fixture.supplier.id,
            grn_date=MARCH,
            description="Receipt",
            warehouse_id=warehouse_id,
            lines=(
                grn_service.GrnLineInput(
                    item_id=fixture.stock_item.id, quantity=quantity, unit_cost=Decimal(1000)
                ),
            ),
        ),
        actor=fixture.owner,
    )


# --- The item enquiry ------------------------------------------------------------------------


def test_the_item_enquiry_shows_on_order_committed_and_available_per_warehouse(
    db: Session, order_entry: OrderEntry
) -> None:
    """Per **warehouse**, not per company: a commitment against stock in Musanze is not
    satisfied by stock in Kigali.

    Main: 70 received, 30 committed by a sales order → 40 available, nothing on order.
    Depot: nothing on the shelf, 25 committed and 100 on order → −25 available, a backorder of
    25 that the screen has to show as a negative rather than as a zero.
    """
    _receive(db, order_entry, order_entry.main.id, Decimal(70))
    orders_service.create_sales_order(
        db,
        order_entry.company_id,
        orders_service.SalesOrderInput(
            partner_id=order_entry.customer.id,
            order_date=MARCH,
            description="Kigali order",
            warehouse_id=order_entry.main.id,
            lines=(
                orders_service.OrderLineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(30),
                    unit_price=Decimal(2000),
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    orders_service.create_sales_order(
        db,
        order_entry.company_id,
        orders_service.SalesOrderInput(
            partner_id=order_entry.customer.id,
            order_date=MARCH,
            description="Musanze order",
            warehouse_id=order_entry.depot.id,
            lines=(
                orders_service.OrderLineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(25),
                    unit_price=Decimal(2000),
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    orders_service.create_purchase_order(
        db,
        order_entry.company_id,
        orders_service.PurchaseOrderInput(
            partner_id=order_entry.supplier.id,
            order_date=MARCH,
            description="Restock Musanze",
            warehouse_id=order_entry.depot.id,
            lines=(
                orders_service.OrderLineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(100),
                    unit_price=Decimal(1000),
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    enquiry = inventory_enquiries.item_enquiry(
        db, order_entry.company_id, order_entry.stock_item.id, as_of=MARCH
    )
    rows = {row.warehouse_code: row for row in enquiry.locations}

    assert rows["MAIN"].quantity == Decimal(70)
    assert rows["MAIN"].committed == Decimal(30)
    assert rows["MAIN"].on_order == ZERO
    assert rows["MAIN"].available == Decimal(40)

    # The depot holds nothing at all and still has a row, because it has a commitment and an
    # order against it. A listing built only from `stock_balances` would show the buyer nothing.
    assert rows["DEP"].quantity == ZERO
    assert rows["DEP"].committed == Decimal(25)
    assert rows["DEP"].on_order == Decimal(100)
    assert rows["DEP"].available == Decimal(-25)


def test_a_closed_order_drops_out_of_the_enquiry(
    db: Session, order_entry: OrderEntry
) -> None:
    """Closing an order releases what it held by dropping out of the open statuses the sum is
    taken over — there is no per-line flag to clear and no total to decrement."""
    _receive(db, order_entry, order_entry.main.id, Decimal(10))
    order, _ = orders_service.create_sales_order(
        db,
        order_entry.company_id,
        orders_service.SalesOrderInput(
            partner_id=order_entry.customer.id,
            order_date=MARCH,
            description="Order",
            warehouse_id=order_entry.main.id,
            lines=(
                orders_service.OrderLineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(8),
                    unit_price=Decimal(2000),
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    before = inventory_enquiries.item_enquiry(
        db, order_entry.company_id, order_entry.stock_item.id, as_of=MARCH
    ).locations[0]
    assert before.committed == Decimal(8)
    assert before.available == Decimal(2)

    orders_service.close_sales_order(db, order, on_date=MARCH, actor=order_entry.owner)

    after = inventory_enquiries.item_enquiry(
        db, order_entry.company_id, order_entry.stock_item.id, as_of=MARCH
    ).locations[0]
    assert after.committed == ZERO
    assert after.available == Decimal(10)


# --- The two views are tenant-scoped -------------------------------------------------------------


def test_the_quantity_views_are_security_invoker(db: Session, order_entry: OrderEntry) -> None:
    """A view without `security_invoker` runs with its **owner's** privileges — the migration
    superuser, which bypasses row level security — so every tenant would read every other
    tenant's orders through a join that looks entirely innocent.

    The policy linter cannot catch this: it checks tables, and a view is not a table. So it is
    checked here, on the one thing that makes these two views safe to exist at all.
    """
    for view in ("sales_order_line_quantities", "purchase_order_line_quantities"):
        options = db.execute(
            text("SELECT reloptions FROM pg_class WHERE relname = :name").bindparams(name=view)
        ).scalar_one()
        assert options is not None and "security_invoker=true" in options, (
            f"{view} is not security_invoker — it would read across tenants"
        )


# --- The catalogue price conversion (decision 1) ----------------------------------------------


def test_an_inclusive_catalogue_price_is_converted_for_an_exclusive_document(
    db: Session, order_entry: OrderEntry
) -> None:
    """`price_includes_tax` is a fact about the catalogue; `tax_mode` is a fact about the
    document. Where they disagree the price must be converted, or an inclusive catalogue sold on
    an exclusive document charges the tax twice.

    Worked by hand: 2 360 inclusive of 18 % is 2 360 / 1.18 = 2 000 exclusive.
    """
    vat = db.execute(
        text(
            "SELECT id, rate_pct FROM tax_codes WHERE company_id = :cid AND rate_pct = 18 "
            "LIMIT 1"
        ).bindparams(cid=order_entry.company_id)
    ).one()
    item = inventory_masters.create_item(
        db,
        order_entry.company_id,
        inventory_masters.ItemInput(
            code="SHELF-PRICE",
            name="Priced on the shelf",
            uom_category_id=order_entry.inventory.count.id,
            base_uom_id=order_entry.each.id,
            selling_price=Decimal(2360),
            price_includes_tax=True,
            sales_account_id=order_entry.accounts["4100"].id,
            cogs_account_id=order_entry.accounts["5100"].id,
            default_sales_tax_code_id=vat.id,
        ),
        actor=order_entry.owner,
    )

    exclusive = pricing.catalogue_unit_price(
        db,
        order_entry.company_id,
        item,
        tax_mode=TaxMode.EXCLUSIVE,
        tax_code_id=vat.id,
        on_date=MARCH,
    )
    inclusive = pricing.catalogue_unit_price(
        db,
        order_entry.company_id,
        item,
        tax_mode=TaxMode.INCLUSIVE,
        tax_code_id=vat.id,
        on_date=MARCH,
    )

    assert exclusive == Decimal(2000)
    # The document and the catalogue agree, so nothing is touched.
    assert inclusive == Decimal(2360)


def test_the_conversion_reaches_an_order_line(db: Session, order_entry: OrderEntry) -> None:
    """The order service and the document service share one helper, so an invoice raised from a
    sales order reproduces the order's figures rather than approximating them."""
    vat = db.execute(
        text(
            "SELECT id FROM tax_codes WHERE company_id = :cid AND rate_pct = 18 LIMIT 1"
        ).bindparams(cid=order_entry.company_id)
    ).scalar_one()
    item = inventory_masters.create_item(
        db,
        order_entry.company_id,
        inventory_masters.ItemInput(
            code="SHELF-PRICE-2",
            name="Priced on the shelf",
            uom_category_id=order_entry.inventory.count.id,
            base_uom_id=order_entry.each.id,
            selling_price=Decimal(2360),
            price_includes_tax=True,
            sales_account_id=order_entry.accounts["4100"].id,
            cogs_account_id=order_entry.accounts["5100"].id,
            default_sales_tax_code_id=vat,
        ),
        actor=order_entry.owner,
    )

    order, _ = orders_service.create_sales_order(
        db,
        order_entry.company_id,
        orders_service.SalesOrderInput(
            partner_id=order_entry.customer.id,
            order_date=MARCH,
            description="Ten off the shelf",
            warehouse_id=order_entry.main.id,
            tax_mode=TaxMode.EXCLUSIVE,
            # No price keyed: the catalogue speaks.
            lines=(orders_service.OrderLineInput(item_id=item.id, quantity=Decimal(10)),),
        ),
        actor=order_entry.owner,
    )

    # 10 x 2 000 net, 18 % on top — and *not* 10 x 2 360 with the tax charged a second time.
    assert order.lines[0].unit_price == Decimal(2000)
    assert order.net_amount == Decimal(20_000)
    assert order.tax_amount == Decimal(3_600)
    assert order.total_amount == Decimal(23_600)
