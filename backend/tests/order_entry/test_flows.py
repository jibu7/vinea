"""Order → document (P6 decision 7).

Each flow **prepares** a document and posts nothing. What these tests assert is the quantity it
offers, because that is the whole of a flow's judgement: the remainder under `allow`, capped at
what is on the shelf under `block`, the unmatched lines of a receipt, the service lines a GRN
cannot carry.

The one rule none of them owns is the refusal. A prepared quantity may be lowered and never
raised, and the guard that says so lives in the posting service — which is what these tests
demonstrate by taking a prepared document, raising a line, and watching it be refused.
"""

from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.kernel.errors import LedgerStateError
from app.models.gl import BackorderPolicy
from app.models.inventory import GrnStatus
from app.models.order_entry import PurchaseOrderStatus, SalesOrderStatus
from app.models.partner import PartnerRole
from app.order_entry import flows as order_flows
from app.order_entry import grn as grn_service
from app.order_entry import orders as orders_service
from app.subledger import documents as documents_service
from tests.order_entry.conftest import MARCH, OrderEntry
from tests.order_entry.invariants import assert_order_invariants

ZERO = Decimal(0)


def _purchase_order(db: Session, fixture: OrderEntry, quantity: Decimal = Decimal(100)):  # noqa: ANN202
    order, _ = orders_service.create_purchase_order(
        db,
        fixture.company_id,
        orders_service.PurchaseOrderInput(
            partner_id=fixture.supplier.id,
            order_date=MARCH,
            description="Purchase",
            warehouse_id=fixture.main.id,
            lines=(
                orders_service.OrderLineInput(
                    item_id=fixture.stock_item.id,
                    quantity=quantity,
                    unit_price=Decimal(1000),
                ),
            ),
        ),
        actor=fixture.owner,
    )
    return order


def _sales_order(db: Session, fixture: OrderEntry, quantity: Decimal = Decimal(30)):  # noqa: ANN202
    order, _ = orders_service.create_sales_order(
        db,
        fixture.company_id,
        orders_service.SalesOrderInput(
            partner_id=fixture.customer.id,
            order_date=MARCH,
            description="Order",
            warehouse_id=fixture.main.id,
            lines=(
                orders_service.OrderLineInput(
                    item_id=fixture.stock_item.id,
                    quantity=quantity,
                    unit_price=Decimal(2000),
                ),
            ),
        ),
        actor=fixture.owner,
    )
    return order


def _post_receipt(db: Session, fixture: OrderEntry, prepared, *, quantity=None):  # noqa: ANN001, ANN202
    data = prepared.grn
    if quantity is not None:
        data = replace(data, lines=(replace(data.lines[0], quantity=quantity),))
    grn, _ = grn_service.post_grn(db, fixture.company_id, data, actor=fixture.owner)
    return grn


# --- Receive -----------------------------------------------------------------------------------


def test_receive_offers_the_remainder_into_the_orders_warehouse(
    db: Session, order_entry: OrderEntry
) -> None:
    order = _purchase_order(db, order_entry, Decimal(100))

    prepared = order_flows.prepare_receipt_from_purchase_order(
        db, order_entry.company_id, order
    )

    assert prepared.remaining == (Decimal(100),)
    line = prepared.grn.lines[0]
    assert line.quantity == Decimal(100)
    assert line.unit_cost == Decimal(1000)
    assert line.purchase_order_line_id == order.lines[0].id
    # **The order says where the goods are going** — the branch rule, carried forward.
    assert prepared.grn.warehouse_id == order.warehouse_id

    grn = _post_receipt(db, order_entry, prepared, quantity=Decimal(60))

    assert grn.lines[0].base_quantity == Decimal(60)
    assert order.status == PurchaseOrderStatus.PARTIALLY_RECEIVED

    # The second call offers exactly what is left.
    again = order_flows.prepare_receipt_from_purchase_order(db, order_entry.company_id, order)
    assert again.grn.lines[0].quantity == Decimal(40)
    assert_order_invariants(db, order_entry.company_id)


def test_receive_refuses_an_order_with_nothing_outstanding(
    db: Session, order_entry: OrderEntry
) -> None:
    order = _purchase_order(db, order_entry, Decimal(10))
    _post_receipt(
        db,
        order_entry,
        order_flows.prepare_receipt_from_purchase_order(db, order_entry.company_id, order),
    )

    with pytest.raises(LedgerStateError) as refused:
        order_flows.prepare_receipt_from_purchase_order(db, order_entry.company_id, order)

    assert refused.value.code == "order_not_open"


def test_receive_skips_service_lines(db: Session, order_entry: OrderEntry) -> None:
    """A service has no shelf. The Receive flow must not offer it, or the GRN it prepares is
    refused by `not_a_stock_item` the moment it is submitted."""
    order, _ = orders_service.create_purchase_order(
        db,
        order_entry.company_id,
        orders_service.PurchaseOrderInput(
            partner_id=order_entry.supplier.id,
            order_date=MARCH,
            description="Goods and a delivery charge",
            warehouse_id=order_entry.main.id,
            lines=(
                orders_service.OrderLineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(10),
                    unit_price=Decimal(1000),
                ),
                orders_service.OrderLineInput(
                    item_id=order_entry.service_item.id,
                    quantity=Decimal(1),
                    unit_price=Decimal(5000),
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    prepared = order_flows.prepare_receipt_from_purchase_order(
        db, order_entry.company_id, order
    )

    assert [line.item_id for line in prepared.grn.lines] == [order_entry.stock_item.id]

    # And the other half: the service line is invoiced instead, which is what receives it.
    service = order_flows.prepare_service_invoice_from_purchase_order(
        db, order_entry.company_id, order
    )
    assert [line.item_id for line in service.document.lines] == [order_entry.service_item.id]
    assert service.role == PartnerRole.AP


# --- Process invoice (matching mode) -------------------------------------------------------------


def test_process_invoice_prepares_one_matched_line_per_unmatched_receipt_line(
    db: Session, order_entry: OrderEntry
) -> None:
    order = _purchase_order(db, order_entry, Decimal(100))
    grn = _post_receipt(
        db,
        order_entry,
        order_flows.prepare_receipt_from_purchase_order(db, order_entry.company_id, order),
        quantity=Decimal(60),
    )

    prepared = order_flows.prepare_invoice_from_grn(db, order_entry.company_id, grn)

    assert prepared.role == PartnerRole.AP
    line = prepared.document.lines[0]
    assert line.grn_line_id == grn.lines[0].id
    assert line.quantity == Decimal(60)
    # Priced at what the receipt was costed at, so a price that has not moved posts no variance.
    assert line.unit_price == Decimal(1000)

    documents_service.post_document(
        db, order_entry.company_id, prepared.role, prepared.document, actor=order_entry.owner
    )

    assert grn.status == GrnStatus.MATCHED
    with pytest.raises(LedgerStateError) as refused:
        order_flows.prepare_invoice_from_grn(db, order_entry.company_id, grn)
    assert refused.value.code == "nothing_to_match"
    assert_order_invariants(db, order_entry.company_id)


# --- Invoice a sales order ---------------------------------------------------------------------


def test_invoice_offers_the_whole_remainder_under_allow(
    db: Session, order_entry: OrderEntry
) -> None:
    """Under `allow` — the default — the flow offers the remainder even when the shelf is
    empty. That is a backorder, not an error."""
    order = _sales_order(db, order_entry, Decimal(30))

    prepared = order_flows.prepare_invoice_from_sales_order(db, order_entry.company_id, order)

    assert prepared.document.lines[0].quantity == Decimal(30)
    assert prepared.remaining == (Decimal(30),)


def test_invoice_caps_at_what_is_available_under_block(
    db: Session, order_entry: OrderEntry
) -> None:
    """Under `block` an invoice for more than is there cannot post at all, so offering the full
    remainder would hand the operator a document that is refused on submission."""
    stock_order = _purchase_order(db, order_entry, Decimal(10))
    _post_receipt(
        db,
        order_entry,
        order_flows.prepare_receipt_from_purchase_order(
            db, order_entry.company_id, stock_order
        ),
    )
    order = _sales_order(db, order_entry, Decimal(30))
    order_entry.settings.backorder_policy = BackorderPolicy.BLOCK
    db.flush()

    prepared = order_flows.prepare_invoice_from_sales_order(db, order_entry.company_id, order)

    assert prepared.document.lines[0].quantity == Decimal(10)
    # `remaining` still reports the order's full outstanding quantity, so the screen can show
    # "10 of 30" rather than pretending the order was only ever for ten.
    assert prepared.remaining == (Decimal(30),)


def test_a_prepared_quantity_may_be_lowered_and_not_raised(
    db: Session, order_entry: OrderEntry
) -> None:
    """The flow offers a default; the posting service owns the rule. Raising a prepared line
    above what the order has left is refused where the write happens."""
    stock_order = _purchase_order(db, order_entry, Decimal(100))
    _post_receipt(
        db,
        order_entry,
        order_flows.prepare_receipt_from_purchase_order(
            db, order_entry.company_id, stock_order
        ),
    )
    order = _sales_order(db, order_entry, Decimal(30))
    prepared = order_flows.prepare_invoice_from_sales_order(db, order_entry.company_id, order)

    lowered = replace(
        prepared.document,
        lines=(replace(prepared.document.lines[0], quantity=Decimal(12)),),
    )
    documents_service.post_document(
        db, order_entry.company_id, prepared.role, lowered, actor=order_entry.owner
    )
    assert order.status == SalesOrderStatus.PARTIALLY_INVOICED

    raised = replace(
        prepared.document,
        lines=(replace(prepared.document.lines[0], quantity=Decimal(19)),),
    )
    with pytest.raises(LedgerStateError) as refused:
        documents_service.post_document(
            db, order_entry.company_id, prepared.role, raised, actor=order_entry.owner
        )

    assert refused.value.code == "invoice_exceeds_order"
    assert_order_invariants(db, order_entry.company_id)
