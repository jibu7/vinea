"""Sales and purchase orders: commitments, and the three rules that govern their lives.

An order posts nothing, so almost everything worth asserting here is a **query** — committed,
on order, available, invoiced, received — and the point of each test is that the query moved
because a document posted, not because anybody wrote a column.
"""

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.kernel.errors import LedgerStateError
from app.models.gl import BackorderPolicy
from app.models.order_entry import PurchaseOrderStatus, SalesOrderStatus
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind
from app.order_entry import grn as grn_service
from app.order_entry import orders as orders_service
from app.order_entry import quantities as order_quantities
from app.subledger import documents as documents_service
from tests.order_entry.conftest import MARCH, OrderEntry
from tests.order_entry.invariants import assert_order_invariants, verify_order_statuses

ZERO = Decimal(0)


def _sales_order(
    db: Session,
    fixture: OrderEntry,
    *,
    quantity: Decimal = Decimal(10),
    price: Decimal = Decimal(2000),
    warehouse_id: int | None = None,
    item_id: int | None = None,
):  # noqa: ANN202
    order, _ = orders_service.create_sales_order(
        db,
        fixture.company_id,
        orders_service.SalesOrderInput(
            partner_id=fixture.customer.id,
            order_date=MARCH,
            description="Order",
            warehouse_id=warehouse_id or fixture.main.id,
            lines=(
                orders_service.OrderLineInput(
                    item_id=item_id or fixture.stock_item.id,
                    quantity=quantity,
                    unit_price=price,
                ),
            ),
        ),
        actor=fixture.owner,
    )
    return order


def _purchase_order(
    db: Session,
    fixture: OrderEntry,
    *,
    quantity: Decimal = Decimal(100),
    price: Decimal = Decimal(1000),
    warehouse_id: int | None = None,
    item_id: int | None = None,
):  # noqa: ANN202
    order, _ = orders_service.create_purchase_order(
        db,
        fixture.company_id,
        orders_service.PurchaseOrderInput(
            partner_id=fixture.supplier.id,
            order_date=MARCH,
            description="Purchase",
            warehouse_id=warehouse_id or fixture.main.id,
            lines=(
                orders_service.OrderLineInput(
                    item_id=item_id or fixture.stock_item.id,
                    quantity=quantity,
                    unit_price=price,
                ),
            ),
        ),
        actor=fixture.owner,
    )
    return order


def _receive(db: Session, fixture: OrderEntry, order, quantity: Decimal, *, cost=Decimal(1000)):  # noqa: ANN001, ANN202
    grn, _ = grn_service.post_grn(
        db,
        fixture.company_id,
        grn_service.GrnInput(
            partner_id=fixture.supplier.id,
            grn_date=MARCH,
            description="Receipt",
            purchase_order_id=order.id,
            lines=(
                grn_service.GrnLineInput(
                    item_id=order.lines[0].item_id,
                    quantity=quantity,
                    unit_cost=cost,
                    purchase_order_line_id=order.lines[0].id,
                ),
            ),
        ),
        actor=fixture.owner,
    )
    return grn


def _invoice(db: Session, fixture: OrderEntry, order, quantity: Decimal, *, price=Decimal(2000)):  # noqa: ANN001, ANN202
    document, _ = documents_service.post_document(
        db,
        fixture.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=fixture.customer.id,
            document_date=MARCH,
            description="Sale",
            lines=(
                documents_service.LineInput(
                    item_id=order.lines[0].item_id,
                    quantity=quantity,
                    unit_price=price,
                    warehouse_id=order.lines[0].warehouse_id,
                    sales_order_line_id=order.lines[0].id,
                ),
            ),
        ),
        actor=fixture.owner,
    )
    return document


# --- An order is a commitment, not a posting -------------------------------------------------


def test_a_sales_order_posts_nothing_and_commits_the_quantity(
    db: Session, order_entry: OrderEntry
) -> None:
    entries_before = db.scalar(
        text("SELECT count(*) FROM journal_entries WHERE company_id = :cid").bindparams(
            cid=order_entry.company_id
        )
    )
    moves_before = db.scalar(
        text("SELECT count(*) FROM stock_moves WHERE company_id = :cid").bindparams(
            cid=order_entry.company_id
        )
    )

    order = _sales_order(db, order_entry, quantity=Decimal(30))

    assert order.number.startswith("SO-")
    assert order.status == SalesOrderStatus.OPEN
    assert order.total_amount == Decimal(60_000)
    # Nothing reached either ledger.
    assert (
        db.scalar(
            text("SELECT count(*) FROM journal_entries WHERE company_id = :cid").bindparams(
                cid=order_entry.company_id
            )
        )
        == entries_before
    )
    assert (
        db.scalar(
            text("SELECT count(*) FROM stock_moves WHERE company_id = :cid").bindparams(
                cid=order_entry.company_id
            )
        )
        == moves_before
    )
    position = order_quantities.position(
        db, order_entry.company_id, order_entry.stock_item.id, order_entry.main.id
    )
    assert position.committed == Decimal(30)
    assert position.on_hand == ZERO
    # Sold what is not there: a backorder, which the default policy permits and the enquiry
    # is required to show as a negative rather than as a zero.
    assert position.available == Decimal(-30)
    assert position.backordered == Decimal(30)
    assert_order_invariants(db, order_entry.company_id)


def test_a_purchase_order_puts_the_quantity_on_order(
    db: Session, order_entry: OrderEntry
) -> None:
    order = _purchase_order(db, order_entry, quantity=Decimal(100))

    assert order.number.startswith("PO-")
    position = order_quantities.position(
        db, order_entry.company_id, order_entry.stock_item.id, order_entry.main.id
    )
    assert position.on_order == Decimal(100)
    assert position.on_hand == ZERO
    assert_order_invariants(db, order_entry.company_id)


# --- Derived quantities move when documents post ----------------------------------------------


def test_receiving_against_an_order_moves_on_order_and_the_status(
    db: Session, order_entry: OrderEntry
) -> None:
    order = _purchase_order(db, order_entry, quantity=Decimal(100))

    _receive(db, order_entry, order, Decimal(60))

    assert order.status == PurchaseOrderStatus.PARTIALLY_RECEIVED
    fulfilment = order_quantities.purchase_fulfilment(
        db, order_entry.company_id, order_id=order.id
    )
    assert fulfilment[order.lines[0].id].fulfilled == Decimal(60)
    position = order_quantities.position(
        db, order_entry.company_id, order_entry.stock_item.id, order_entry.main.id
    )
    assert position.on_order == Decimal(40)
    assert position.on_hand == Decimal(60)

    _receive(db, order_entry, order, Decimal(40))

    assert order.status == PurchaseOrderStatus.RECEIVED
    assert (
        order_quantities.position(
            db, order_entry.company_id, order_entry.stock_item.id, order_entry.main.id
        ).on_order
        == ZERO
    )
    assert_order_invariants(db, order_entry.company_id)


def test_invoicing_a_sales_order_moves_committed_and_the_status(
    db: Session, order_entry: OrderEntry
) -> None:
    purchase = _purchase_order(db, order_entry, quantity=Decimal(100))
    _receive(db, order_entry, purchase, Decimal(100))
    order = _sales_order(db, order_entry, quantity=Decimal(30))

    _invoice(db, order_entry, order, Decimal(10))

    assert order.status == SalesOrderStatus.PARTIALLY_INVOICED
    position = order_quantities.position(
        db, order_entry.company_id, order_entry.stock_item.id, order_entry.main.id
    )
    assert position.committed == Decimal(20)
    assert position.on_hand == Decimal(90)
    assert position.available == Decimal(70)

    _invoice(db, order_entry, order, Decimal(20))

    assert order.status == SalesOrderStatus.INVOICED
    assert (
        order_quantities.position(
            db, order_entry.company_id, order_entry.stock_item.id, order_entry.main.id
        ).committed
        == ZERO
    )
    assert_order_invariants(db, order_entry.company_id)


def test_reversing_an_invoice_reopens_the_order_by_construction(
    db: Session, order_entry: OrderEntry
) -> None:
    """The whole reason there is no `quantity_invoiced` column.

    Nothing recalculates anything here: the invoice stops being posted, so the sum that counted
    it stops counting it, and the status follows the sum.
    """
    purchase = _purchase_order(db, order_entry, quantity=Decimal(100))
    _receive(db, order_entry, purchase, Decimal(100))
    order = _sales_order(db, order_entry, quantity=Decimal(30))
    invoice = _invoice(db, order_entry, order, Decimal(30))
    assert order.status == SalesOrderStatus.INVOICED

    documents_service.reverse_document(
        db, invoice, on_date=MARCH, reason="Keyed twice", actor=order_entry.owner
    )

    assert order.status == SalesOrderStatus.OPEN
    assert (
        order_quantities.position(
            db, order_entry.company_id, order_entry.stock_item.id, order_entry.main.id
        ).committed
        == Decimal(30)
    )
    assert verify_order_statuses(db, order_entry.company_id) == []
    assert_order_invariants(db, order_entry.company_id)


def test_reversing_a_receipt_puts_the_quantity_back_on_order(
    db: Session, order_entry: OrderEntry
) -> None:
    order = _purchase_order(db, order_entry, quantity=Decimal(100))
    grn = _receive(db, order_entry, order, Decimal(60))
    assert order.status == PurchaseOrderStatus.PARTIALLY_RECEIVED

    grn_service.reverse_grn(
        db, grn, on_date=MARCH, reason="Wrong depot", actor=order_entry.owner
    )

    assert order.status == PurchaseOrderStatus.OPEN
    assert (
        order_quantities.position(
            db, order_entry.company_id, order_entry.stock_item.id, order_entry.main.id
        ).on_order
        == Decimal(100)
    )
    assert_order_invariants(db, order_entry.company_id)


def test_a_service_line_is_received_by_its_invoice(
    db: Session, order_entry: OrderEntry
) -> None:
    """Decision 4: a service has no shelf and no GRN. The invoice is the receipt."""
    order, _ = orders_service.create_purchase_order(
        db,
        order_entry.company_id,
        orders_service.PurchaseOrderInput(
            partner_id=order_entry.supplier.id,
            order_date=MARCH,
            description="Consultancy",
            warehouse_id=order_entry.main.id,
            lines=(
                orders_service.OrderLineInput(
                    item_id=order_entry.service_item.id,
                    quantity=Decimal(4),
                    unit_price=Decimal(5000),
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
            description="Consultancy invoice",
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.service_item.id,
                    quantity=Decimal(4),
                    unit_price=Decimal(5000),
                    purchase_order_line_id=order.lines[0].id,
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    assert order.status == PurchaseOrderStatus.RECEIVED
    assert_order_invariants(db, order_entry.company_id)


# --- The refusals ------------------------------------------------------------------------------


def test_a_receipt_may_not_exceed_the_order(db: Session, order_entry: OrderEntry) -> None:
    order = _purchase_order(db, order_entry, quantity=Decimal(100))
    _receive(db, order_entry, order, Decimal(60))

    with pytest.raises(LedgerStateError) as refused:
        _receive(db, order_entry, order, Decimal(41))

    assert refused.value.code == "receipt_exceeds_order"
    # And the boundary is exactly the remainder, not one less.
    _receive(db, order_entry, order, Decimal(40))
    assert order.status == PurchaseOrderStatus.RECEIVED


def test_two_lines_of_one_receipt_cannot_together_exceed_the_order(
    db: Session, order_entry: OrderEntry
) -> None:
    """A check made only against what was received *before* this document would let two lines
    of the same receipt each pass and together overrun the order."""
    order = _purchase_order(db, order_entry, quantity=Decimal(100))

    with pytest.raises(LedgerStateError) as refused:
        grn_service.post_grn(
            db,
            order_entry.company_id,
            grn_service.GrnInput(
                partner_id=order_entry.supplier.id,
                grn_date=MARCH,
                description="Two drops",
                purchase_order_id=order.id,
                lines=(
                    grn_service.GrnLineInput(
                        item_id=order_entry.stock_item.id,
                        quantity=Decimal(60),
                        unit_cost=Decimal(1000),
                        purchase_order_line_id=order.lines[0].id,
                    ),
                    grn_service.GrnLineInput(
                        item_id=order_entry.stock_item.id,
                        quantity=Decimal(60),
                        unit_cost=Decimal(1000),
                        purchase_order_line_id=order.lines[0].id,
                    ),
                ),
            ),
            actor=order_entry.owner,
        )

    assert refused.value.code == "receipt_exceeds_order"


def test_an_invoice_may_not_exceed_the_order(db: Session, order_entry: OrderEntry) -> None:
    purchase = _purchase_order(db, order_entry, quantity=Decimal(100))
    _receive(db, order_entry, purchase, Decimal(100))
    order = _sales_order(db, order_entry, quantity=Decimal(30))
    _invoice(db, order_entry, order, Decimal(10))

    with pytest.raises(LedgerStateError) as refused:
        _invoice(db, order_entry, order, Decimal(21))

    assert refused.value.code == "invoice_exceeds_order"
    assert_order_invariants(db, order_entry.company_id)


def test_under_block_a_sales_order_cannot_promise_what_is_not_there(
    db: Session, order_entry: OrderEntry
) -> None:
    order_entry.settings.backorder_policy = BackorderPolicy.BLOCK
    db.flush()
    purchase = _purchase_order(db, order_entry, quantity=Decimal(10))
    _receive(db, order_entry, purchase, Decimal(10))

    with pytest.raises(LedgerStateError) as refused:
        _sales_order(db, order_entry, quantity=Decimal(11))

    assert refused.value.code == "exceeds_available"
    # Ten is available, so ten is allowed — the boundary is inclusive.
    order = _sales_order(db, order_entry, quantity=Decimal(10))
    assert order.status == SalesOrderStatus.OPEN
    assert_order_invariants(db, order_entry.company_id)


def test_the_block_refusal_writes_nothing(db: Session, order_entry: OrderEntry) -> None:
    """Every service in this phase refuses **before** it writes, which is what lets a caller
    treat a refusal as a no-op. A check made after the insert would leave a numbered order
    behind for a refusal the caller was told about."""
    order_entry.settings.backorder_policy = BackorderPolicy.BLOCK
    db.flush()
    before = db.scalar(
        text("SELECT count(*) FROM sales_orders WHERE company_id = :cid").bindparams(
            cid=order_entry.company_id
        )
    )

    with pytest.raises(LedgerStateError):
        _sales_order(db, order_entry, quantity=Decimal(5))

    db.flush()
    assert (
        db.scalar(
            text("SELECT count(*) FROM sales_orders WHERE company_id = :cid").bindparams(
                cid=order_entry.company_id
            )
        )
        == before
    )


def test_editing_an_order_is_measured_against_its_own_effect(
    db: Session, order_entry: OrderEntry
) -> None:
    """Under `block`, re-saving an order unchanged must not be refused for asking for what it
    already holds — which is what happens if the check counts the order's own commitment twice.
    """
    order_entry.settings.backorder_policy = BackorderPolicy.BLOCK
    db.flush()
    purchase = _purchase_order(db, order_entry, quantity=Decimal(10))
    _receive(db, order_entry, purchase, Decimal(10))
    order = _sales_order(db, order_entry, quantity=Decimal(10))

    orders_service.update_sales_order(
        db,
        order_entry.company_id,
        order,
        orders_service.SalesOrderInput(
            partner_id=order_entry.customer.id,
            order_date=MARCH,
            description="Order",
            warehouse_id=order_entry.main.id,
            lines=(
                orders_service.OrderLineInput(
                    line_id=order.lines[0].id,
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(10),
                    unit_price=Decimal(2000),
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    assert order.lines[0].base_quantity == Decimal(10)


# --- Edit, close, cancel -------------------------------------------------------------------------


def test_a_line_cannot_be_edited_below_what_has_been_fulfilled(
    db: Session, order_entry: OrderEntry
) -> None:
    order = _purchase_order(db, order_entry, quantity=Decimal(100))
    _receive(db, order_entry, order, Decimal(60))

    with pytest.raises(LedgerStateError) as refused:
        orders_service.update_purchase_order(
            db,
            order_entry.company_id,
            order,
            orders_service.PurchaseOrderInput(
                partner_id=order_entry.supplier.id,
                order_date=MARCH,
                description="Purchase",
                warehouse_id=order_entry.main.id,
                lines=(
                    orders_service.OrderLineInput(
                        line_id=order.lines[0].id,
                        item_id=order_entry.stock_item.id,
                        quantity=Decimal(40),
                        unit_price=Decimal(1000),
                    ),
                ),
            ),
            actor=order_entry.owner,
        )

    assert refused.value.code == "below_fulfilled_quantity"
    assert_order_invariants(db, order_entry.company_id)


def test_a_fulfilled_line_cannot_be_removed(db: Session, order_entry: OrderEntry) -> None:
    order = _purchase_order(db, order_entry, quantity=Decimal(100))
    _receive(db, order_entry, order, Decimal(60))

    with pytest.raises(LedgerStateError) as refused:
        orders_service.update_purchase_order(
            db,
            order_entry.company_id,
            order,
            orders_service.PurchaseOrderInput(
                partner_id=order_entry.supplier.id,
                order_date=MARCH,
                description="Purchase",
                warehouse_id=order_entry.main.id,
                lines=(
                    orders_service.OrderLineInput(
                        item_id=order_entry.service_item.id,
                        quantity=Decimal(1),
                        unit_price=Decimal(100),
                    ),
                ),
            ),
            actor=order_entry.owner,
        )

    assert refused.value.code == "line_has_fulfilment"


def test_partner_and_currency_lock_once_anything_is_fulfilled(
    db: Session, order_entry: OrderEntry
) -> None:
    order = _purchase_order(db, order_entry, quantity=Decimal(100))
    _receive(db, order_entry, order, Decimal(60))
    other = order_entry.customer  # a partner that is not this supplier

    with pytest.raises(LedgerStateError) as refused:
        orders_service.update_purchase_order(
            db,
            order_entry.company_id,
            order,
            orders_service.PurchaseOrderInput(
                partner_id=other.id,
                order_date=MARCH,
                description="Purchase",
                warehouse_id=order_entry.main.id,
                lines=(
                    orders_service.OrderLineInput(
                        line_id=order.lines[0].id,
                        item_id=order_entry.stock_item.id,
                        quantity=Decimal(100),
                        unit_price=Decimal(1000),
                    ),
                ),
            ),
            actor=order_entry.owner,
        )

    assert refused.value.code == "order_partner_locked"


def test_closing_an_order_releases_the_remainder_and_keeps_the_number(
    db: Session, order_entry: OrderEntry
) -> None:
    purchase = _purchase_order(db, order_entry, quantity=Decimal(100))
    _receive(db, order_entry, purchase, Decimal(100))
    order = _sales_order(db, order_entry, quantity=Decimal(30))
    _invoice(db, order_entry, order, Decimal(10))
    number = order.number

    orders_service.close_sales_order(
        db, order, on_date=MARCH, actor=order_entry.owner
    )

    assert order.status == SalesOrderStatus.CLOSED
    assert order.number == number
    assert (
        order_quantities.position(
            db, order_entry.company_id, order_entry.stock_item.id, order_entry.main.id
        ).committed
        == ZERO
    )
    assert verify_order_statuses(db, order_entry.company_id) == []
    assert_order_invariants(db, order_entry.company_id)


def test_cancel_is_refused_once_anything_has_been_fulfilled(
    db: Session, order_entry: OrderEntry
) -> None:
    order = _purchase_order(db, order_entry, quantity=Decimal(100))
    _receive(db, order_entry, order, Decimal(60))

    with pytest.raises(LedgerStateError) as refused:
        orders_service.cancel_purchase_order(
            db, order, on_date=MARCH, actor=order_entry.owner
        )

    assert refused.value.code == "order_has_fulfilment"


def test_a_cancelled_order_keeps_its_number_and_commits_nothing(
    db: Session, order_entry: OrderEntry
) -> None:
    order = _sales_order(db, order_entry, quantity=Decimal(30))
    number = order.number

    orders_service.cancel_sales_order(db, order, on_date=MARCH, actor=order_entry.owner)

    assert order.status == SalesOrderStatus.CANCELLED
    assert order.number == number
    assert (
        order_quantities.position(
            db, order_entry.company_id, order_entry.stock_item.id, order_entry.main.id
        ).committed
        == ZERO
    )
    # A cancelled order still holds its number: the `SO-` run has no hole in it, and the
    # gapless check in `assert_ledger_invariants` reads that claim off this table.
    assert_order_invariants(db, order_entry.company_id)


def test_a_closed_order_cannot_be_invoiced(db: Session, order_entry: OrderEntry) -> None:
    purchase = _purchase_order(db, order_entry, quantity=Decimal(100))
    _receive(db, order_entry, purchase, Decimal(100))
    order = _sales_order(db, order_entry, quantity=Decimal(30))
    orders_service.close_sales_order(db, order, on_date=MARCH, actor=order_entry.owner)

    with pytest.raises(LedgerStateError) as refused:
        _invoice(db, order_entry, order, Decimal(5))

    assert refused.value.code == "order_not_open"


# --- The order links are declarative --------------------------------------------------------------


def test_an_ar_line_cannot_name_a_purchase_order(db: Session, order_entry: OrderEntry) -> None:
    """The role rule of decision 1, as a refusal the grid can show — before the CHECK that
    backs it ever has to fire."""
    purchase = _purchase_order(db, order_entry, quantity=Decimal(100))

    with pytest.raises(LedgerStateError) as refused:
        documents_service.post_document(
            db,
            order_entry.company_id,
            PartnerRole.AR,
            documents_service.DocumentInput(
                kind=DocumentKind.INVOICE,
                partner_id=order_entry.customer.id,
                document_date=MARCH,
                description="Sale",
                lines=(
                    documents_service.LineInput(
                        item_id=order_entry.stock_item.id,
                        quantity=Decimal(1),
                        unit_price=Decimal(2000),
                        warehouse_id=order_entry.main.id,
                        purchase_order_line_id=purchase.lines[0].id,
                    ),
                ),
            ),
            actor=order_entry.owner,
        )

    assert refused.value.code == "order_link_not_allowed"


def test_the_database_refuses_an_ar_line_carrying_a_purchase_order_link(
    db: Session, order_entry: OrderEntry
) -> None:
    """The declarative half: with the service bypassed, the CHECK still holds the rule.

    This is what makes `role` worth denormalising. The column is held equal to the document's by
    a foreign key, so the CHECK over it cannot be fooled by a line claiming the other role.
    """
    purchase = _purchase_order(db, order_entry, quantity=Decimal(100))
    invoice = _invoice_without_an_order(db, order_entry)

    with pytest.raises(Exception) as refused:  # noqa: B017 - the DB error type is the point
        db.execute(
            text(
                "UPDATE partner_document_lines SET purchase_order_line_id = :line "
                "WHERE document_id = :doc"
            ).bindparams(line=purchase.lines[0].id, doc=invoice.id)
        )
        db.flush()

    assert "order_link_matches_role" in str(refused.value)
    db.rollback()


def _invoice_without_an_order(db: Session, fixture: OrderEntry):  # noqa: ANN202
    purchase = _purchase_order(db, fixture, quantity=Decimal(10))
    _receive(db, fixture, purchase, Decimal(10))
    document, _ = documents_service.post_document(
        db,
        fixture.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=fixture.customer.id,
            document_date=MARCH,
            description="Sale",
            lines=(
                documents_service.LineInput(
                    item_id=fixture.stock_item.id,
                    quantity=Decimal(1),
                    unit_price=Decimal(2000),
                    warehouse_id=fixture.main.id,
                ),
            ),
        ),
        actor=fixture.owner,
    )
    return document
