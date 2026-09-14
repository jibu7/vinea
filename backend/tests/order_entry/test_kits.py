"""Kits and Breakup (P6 decision 8).

A kit is a **virtual bundle**: it has no moves, is never received, and cannot be purchased. What
it does is explode at line entry into components that are real, and the two halves of that
sentence are what these tests separate — the parent carries the revenue, the components carry
the goods and the cost.

The load-bearing case is the last one. Editing the catalogue after an order has been taken must
change what the *next* kit line explodes into and restate nothing that has already been keyed,
which is the whole reason `item_kit_components` is a definition and the order stores its own
copy.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory import masters as inventory_masters
from app.kernel.errors import LedgerStateError
from app.models.inventory import StockMove
from app.models.order_entry import SalesOrderStatus
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind
from app.order_entry import flows as order_flows
from app.order_entry import grn as grn_service
from app.order_entry import kits as order_kits
from app.order_entry import orders as orders_service
from app.order_entry import quantities as order_quantities
from app.subledger import documents as documents_service
from tests.order_entry.conftest import MARCH, OrderEntry
from tests.order_entry.invariants import assert_order_invariants

ZERO = Decimal(0)


def _stock_up(db: Session, fixture: OrderEntry, quantity: Decimal) -> None:
    grn_service.post_grn(
        db,
        fixture.company_id,
        grn_service.GrnInput(
            partner_id=fixture.supplier.id,
            grn_date=MARCH,
            description="Opening receipt",
            warehouse_id=fixture.main.id,
            lines=(
                grn_service.GrnLineInput(
                    item_id=fixture.stock_item.id, quantity=quantity, unit_cost=Decimal(1000)
                ),
            ),
        ),
        actor=fixture.owner,
    )


def _kit_order(db: Session, fixture: OrderEntry, kits_ordered: Decimal = Decimal(1)):  # noqa: ANN202
    order, _ = orders_service.create_sales_order(
        db,
        fixture.company_id,
        orders_service.SalesOrderInput(
            partner_id=fixture.customer.id,
            order_date=MARCH,
            description="Gift packs",
            warehouse_id=fixture.main.id,
            lines=(
                orders_service.OrderLineInput(
                    item_id=fixture.kit_item.id,
                    quantity=kits_ordered,
                    unit_price=Decimal(3500),
                ),
            ),
        ),
        actor=fixture.owner,
    )
    return order


def _components(order) -> list:  # noqa: ANN001
    parent = order.lines[0]
    return [line for line in order.lines if line.kit_parent_line_id == parent.id]


# --- Explosion at line entry --------------------------------------------------------------------


def test_a_kit_line_on_an_order_explodes_into_its_components(
    db: Session, order_entry: OrderEntry
) -> None:
    order = _kit_order(db, order_entry, Decimal(3))

    parent = order.lines[0]
    components = _components(order)
    assert parent.item_id == order_entry.kit_item.id
    assert parent.net_amount == Decimal(10_500)
    assert len(components) == 1
    # kit quantity x per-kit, in the component's base unit.
    assert components[0].item_id == order_entry.stock_item.id
    assert components[0].base_quantity == Decimal(6)
    # The money is entirely on the parent: a component that priced itself would sell the same
    # goods twice.
    assert components[0].net_amount == ZERO
    assert components[0].tax_code_id is None
    assert_order_invariants(db, order_entry.company_id)


def test_commitment_comes_from_the_components_not_the_kit(
    db: Session, order_entry: OrderEntry
) -> None:
    """A kit is never on a shelf, so a commitment against the kit item would be a commitment
    against nothing. What is promised is the bottles."""
    _kit_order(db, order_entry, Decimal(3))

    bottles = order_quantities.position(
        db, order_entry.company_id, order_entry.stock_item.id, order_entry.main.id
    )
    kits_position = order_quantities.position(
        db, order_entry.company_id, order_entry.kit_item.id, order_entry.main.id
    )
    assert bottles.committed == Decimal(6)
    assert kits_position.committed == ZERO


def test_a_kit_cannot_be_purchased_or_received(db: Session, order_entry: OrderEntry) -> None:
    with pytest.raises(LedgerStateError) as on_the_order:
        orders_service.create_purchase_order(
            db,
            order_entry.company_id,
            orders_service.PurchaseOrderInput(
                partner_id=order_entry.supplier.id,
                order_date=MARCH,
                description="Buying gift packs",
                warehouse_id=order_entry.main.id,
                lines=(
                    orders_service.OrderLineInput(
                        item_id=order_entry.kit_item.id,
                        quantity=Decimal(1),
                        unit_price=Decimal(2000),
                    ),
                ),
            ),
            actor=order_entry.owner,
        )
    assert on_the_order.value.code == "kit_not_purchasable"

    with pytest.raises(LedgerStateError) as on_the_receipt:
        grn_service.post_grn(
            db,
            order_entry.company_id,
            grn_service.GrnInput(
                partner_id=order_entry.supplier.id,
                grn_date=MARCH,
                description="Receiving gift packs",
                warehouse_id=order_entry.main.id,
                lines=(
                    grn_service.GrnLineInput(
                        item_id=order_entry.kit_item.id,
                        quantity=Decimal(1),
                        unit_cost=Decimal(2000),
                    ),
                ),
            ),
            actor=order_entry.owner,
        )
    assert on_the_receipt.value.code == "kit_not_purchasable"


def test_a_kit_invoice_moves_the_components_and_bills_the_kit(
    db: Session, order_entry: OrderEntry
) -> None:
    """The two halves of a kit line, on the ledger and on the stock ledger at once: the parent
    is the revenue line and the components are the moves."""
    _stock_up(db, order_entry, Decimal(20))

    document, _ = documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=order_entry.customer.id,
            document_date=MARCH,
            description="Two gift packs",
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.kit_item.id,
                    quantity=Decimal(2),
                    unit_price=Decimal(3500),
                    warehouse_id=order_entry.main.id,
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    parent, component = document.lines
    assert parent.item_id == order_entry.kit_item.id
    assert parent.net_amount == Decimal(7000)
    assert component.item_id == order_entry.stock_item.id
    assert component.base_quantity == Decimal(4)
    assert component.net_amount == ZERO
    assert component.kit_parent_line_id == parent.id
    # The companion moved the components and nothing else: 4 bottles at the 1 000 average.
    moves = list(
        db.scalars(
            select(StockMove).where(
                StockMove.company_id == order_entry.company_id,
                StockMove.journal_entry_id == document.stock_entry_id,
            )
        )
    )
    assert len(moves) == 1
    assert moves[0].quantity == Decimal(-4)
    assert moves[0].value == Decimal(-4000)
    assert moves[0].source_line_id == component.id
    assert_order_invariants(db, order_entry.company_id)


# --- Breakup -------------------------------------------------------------------------------------


def test_breakup_replaces_the_explosion_and_records_that_it_did(
    db: Session, order_entry: OrderEntry
) -> None:
    order = _kit_order(db, order_entry, Decimal(2))
    other = inventory_masters.create_item(
        db,
        order_entry.company_id,
        inventory_masters.ItemInput(
            code="WINE-375",
            name="Rugari Red 375ml",
            uom_category_id=order_entry.inventory.count.id,
            base_uom_id=order_entry.each.id,
            sales_account_id=order_entry.accounts["4100"].id,
            cogs_account_id=order_entry.accounts["5100"].id,
        ),
        actor=order_entry.owner,
    )

    orders_service.breakup_sales_order_line(
        db,
        order_entry.company_id,
        order,
        order.lines[0].id,
        (
            order_kits.ComponentInput(
                item_id=order_entry.stock_item.id, base_quantity=Decimal(3)
            ),
            order_kits.ComponentInput(item_id=other.id, base_quantity=Decimal(1)),
        ),
        actor=order_entry.owner,
    )
    db.refresh(order)

    components = _components(order)
    assert {line.item_id: line.base_quantity for line in components} == {
        order_entry.stock_item.id: Decimal(3),
        other.id: Decimal(1),
    }
    # The parent is untouched: the customer agreed a price for a gift pack, and Breakup is
    # about what goes in the box.
    assert order.lines[0].net_amount == Decimal(7000)
    assert order.lines[0].kit_breakup_edited is True
    assert_order_invariants(db, order_entry.company_id)


def test_breakup_is_refused_on_a_line_that_has_been_invoiced(
    db: Session, order_entry: OrderEntry
) -> None:
    _stock_up(db, order_entry, Decimal(20))
    order = _kit_order(db, order_entry, Decimal(1))
    prepared = order_flows.prepare_invoice_from_sales_order(db, order_entry.company_id, order)
    documents_service.post_document(
        db,
        order_entry.company_id,
        prepared.role,
        prepared.document,
        actor=order_entry.owner,
    )

    with pytest.raises(LedgerStateError) as refused:
        orders_service.breakup_sales_order_line(
            db,
            order_entry.company_id,
            order,
            order.lines[0].id,
            (
                order_kits.ComponentInput(
                    item_id=order_entry.stock_item.id, base_quantity=Decimal(5)
                ),
            ),
            actor=order_entry.owner,
        )

    assert refused.value.code == "line_has_fulfilment"


def test_breakup_refuses_a_nested_kit(db: Session, order_entry: OrderEntry) -> None:
    order = _kit_order(db, order_entry, Decimal(1))

    with pytest.raises(LedgerStateError) as refused:
        orders_service.breakup_sales_order_line(
            db,
            order_entry.company_id,
            order,
            order.lines[0].id,
            (
                order_kits.ComponentInput(
                    item_id=order_entry.kit_item.id, base_quantity=Decimal(1)
                ),
            ),
            actor=order_entry.owner,
        )

    # It is the kit's own id, so `kit_is_its_own_component` fires before the nesting rule.
    assert refused.value.code == "kit_is_its_own_component"


def test_an_invoice_raised_from_an_order_ships_what_the_order_promised(
    db: Session, order_entry: OrderEntry
) -> None:
    """The load-bearing one.

    The order is taken, Breakup edits its explosion, and the *catalogue definition then
    changes*. The invoice must ship what was sold — re-exploding from the definition would put
    different goods in the box and would leave the order's own component lines uninvoiced
    forever, so the order could never reach `invoiced`.
    """
    _stock_up(db, order_entry, Decimal(50))
    order = _kit_order(db, order_entry, Decimal(1))
    orders_service.breakup_sales_order_line(
        db,
        order_entry.company_id,
        order,
        order.lines[0].id,
        (
            order_kits.ComponentInput(
                item_id=order_entry.stock_item.id, base_quantity=Decimal(5)
            ),
        ),
        actor=order_entry.owner,
    )
    db.refresh(order)
    # The catalogue moves on after the order was taken.
    inventory_masters.replace_kit_components(
        db,
        order_entry.company_id,
        order_entry.kit_item,
        [{"component_item_id": order_entry.stock_item.id, "quantity_per_kit": Decimal(9)}],
        actor=order_entry.owner,
    )

    prepared = order_flows.prepare_invoice_from_sales_order(db, order_entry.company_id, order)
    document, _ = documents_service.post_document(
        db, order_entry.company_id, prepared.role, prepared.document, actor=order_entry.owner
    )

    component = document.lines[1]
    assert component.base_quantity == Decimal(5), (
        "the invoice shipped the definition, not what the order promised"
    )
    # And the order is fully invoiced — parent *and* component — so its status can reach
    # `invoiced` at all.
    assert order.status == SalesOrderStatus.INVOICED
    assert_order_invariants(db, order_entry.company_id)


# --- Editing a kit line whose breakup was hand-edited -----------------------------------------


def _edit_kit_quantity(
    db: Session,
    fixture: OrderEntry,
    order,  # noqa: ANN001
    quantity: Decimal,
    *,
    reset_breakup: bool = False,
):  # noqa: ANN202
    parent = order.lines[0]
    return orders_service.update_sales_order(
        db,
        fixture.company_id,
        order,
        orders_service.SalesOrderInput(
            partner_id=fixture.customer.id,
            order_date=MARCH,
            description="Gift packs",
            warehouse_id=fixture.main.id,
            lines=(
                orders_service.OrderLineInput(
                    line_id=parent.id,
                    item_id=parent.item_id,
                    quantity=quantity,
                    unit_price=Decimal(3500),
                    reset_breakup=reset_breakup,
                ),
            ),
        ),
        actor=fixture.owner,
    )


def _break_up(db: Session, fixture: OrderEntry, order, bottles: Decimal) -> None:  # noqa: ANN001
    orders_service.breakup_sales_order_line(
        db,
        fixture.company_id,
        order,
        order.lines[0].id,
        (
            order_kits.ComponentInput(
                item_id=fixture.stock_item.id, base_quantity=bottles
            ),
        ),
        actor=fixture.owner,
    )
    db.refresh(order)


def test_changing_a_hand_broken_up_kit_lines_quantity_is_refused(
    db: Session, order_entry: OrderEntry
) -> None:
    """Re-exploding reads the **catalogue definition** — which is exactly what Breakup was used
    to override. An operator who substituted a component and then changed the quantity would get
    the catalogue back with no warning that their substitution had gone.
    """
    order = _kit_order(db, order_entry, Decimal(2))
    _break_up(db, order_entry, order, Decimal(7))
    assert order.lines[0].kit_breakup_edited is True

    with pytest.raises(LedgerStateError) as refused:
        _edit_kit_quantity(db, order_entry, order, Decimal(3))

    assert refused.value.code == "kit_breakup_would_reset"
    # And the refusal wrote nothing: the order still has its hand-edited explosion and its
    # original quantity, so the caller can re-send with consent rather than repair anything.
    db.refresh(order)
    assert order.lines[0].base_quantity == Decimal(2)
    assert order.lines[0].kit_breakup_edited is True
    assert [line.base_quantity for line in _components(order)] == [Decimal(7)]
    assert_order_invariants(db, order_entry.company_id)


def test_reset_breakup_re_explodes_from_the_definition(
    db: Session, order_entry: OrderEntry
) -> None:
    """The other path: the screen asked, somebody said yes, and the explosion goes back to what
    the catalogue says for the new quantity."""
    order = _kit_order(db, order_entry, Decimal(2))
    _break_up(db, order_entry, order, Decimal(7))

    _edit_kit_quantity(db, order_entry, order, Decimal(3), reset_breakup=True)
    db.refresh(order)

    parent = order.lines[0]
    assert parent.base_quantity == Decimal(3)
    # 3 kits x 2 bottles per kit, from the definition — the hand-edited 7 is gone, as asked.
    assert [line.base_quantity for line in _components(order)] == [Decimal(6)]
    assert parent.kit_breakup_edited is False
    assert_order_invariants(db, order_entry.company_id)


def test_an_edit_that_leaves_the_quantity_alone_keeps_the_breakup(
    db: Session, order_entry: OrderEntry
) -> None:
    """Consent is only needed where there is something to re-explode.

    Changing a reference or an expected date must not cost a substitution, and requiring
    `reset_breakup` for every edit would either train operators to send it always or make the
    header un-editable on any order with a kit on it.
    """
    order = _kit_order(db, order_entry, Decimal(2))
    _break_up(db, order_entry, order, Decimal(7))

    orders_service.update_sales_order(
        db,
        order_entry.company_id,
        order,
        orders_service.SalesOrderInput(
            partner_id=order_entry.customer.id,
            order_date=MARCH,
            description="Gift packs",
            reference="Their PO 4471",
            warehouse_id=order_entry.main.id,
            lines=(
                orders_service.OrderLineInput(
                    line_id=order.lines[0].id,
                    item_id=order.lines[0].item_id,
                    quantity=Decimal(2),
                    unit_price=Decimal(3500),
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    db.refresh(order)

    assert order.reference == "Their PO 4471"
    assert order.lines[0].kit_breakup_edited is True
    assert [line.base_quantity for line in _components(order)] == [Decimal(7)]
    assert_order_invariants(db, order_entry.company_id)


def test_an_untouched_kit_line_needs_no_consent_to_change_quantity(
    db: Session, order_entry: OrderEntry
) -> None:
    """The refusal is about *losing an edit*, not about kits. A line nobody broke up has nothing
    to lose, so it re-explodes as it always did."""
    order = _kit_order(db, order_entry, Decimal(2))
    assert order.lines[0].kit_breakup_edited is False

    _edit_kit_quantity(db, order_entry, order, Decimal(5))
    db.refresh(order)

    assert order.lines[0].base_quantity == Decimal(5)
    assert [line.base_quantity for line in _components(order)] == [Decimal(10)]
    assert_order_invariants(db, order_entry.company_id)
