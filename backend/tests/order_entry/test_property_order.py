"""The compounding-error property for order entry (P6 steps 2 and 3).

One Hypothesis machine drives a *random sequence* of goods receipts, partial matches, direct
item invoices, customer returns, supplier returns, reversals and — from step 3 — sales and
purchase orders, partial fulfilments against them, edits, closes and cancels. It asserts the
whole of the ledger, subledger, stock **and** order invariant suites after every single step,
plus two things only this file can say:

* every derived quantity equals a **brute-force recomputation** done in Python over the ORM
  rows, so the two SQL views cannot be quietly wrong in the same way the service is;
* no order line is ever fulfilled beyond what it ordered, however the sequence fell.

Checking only the end state hides an error that one operation introduces and the next one
masks — and the accrual is exactly the sort of account where that happens, because a receipt
and a match move it in opposite directions and a wrong share in one can be cancelled by a
wrong share in the other.

It runs twice, against a base currency with **no** minor unit (RWF) and one with **two**
(USD), because the pro-rata relief rounds differently at each and the "last match takes the
remainder" rule is precisely what absorbs the difference.

**The 1000-over-3 case is generated deliberately**, not left to chance. A receipt of three
units worth 1 000 matched one unit at a time relieves 333 + 333 + 334, and reversing the first
leaves the ledger having relieved 667 where a recomputation over the survivors gives 666. A
generator drawing quantities independently would essentially never produce a receipt whose
value does not divide by its quantity *and* three separate single-unit matches against it, so
the shape that forced `accrual_relieved` to be a stored column is written out as its own
example rather than hoped for.

Illegal steps are skipped rather than failed — matching more than was received, reversing what
is already reversed, issuing what is not there under `block`. The property under test is the
invariant suite, not the plumbing.

Both machines carry `@pytest.mark.slow`, which is how the nightly deep workflow selects them.
"""

import itertools
from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kernel.errors import LedgerStateError, PostingError
from app.models.currency import Currency
from app.models.gl import BackorderPolicy
from app.models.inventory import GrnStatus, Item, ItemType
from app.models.order_entry import (
    OPEN_PURCHASE_STATUSES,
    OPEN_SALES_STATUSES,
    PurchaseOrder,
    PurchaseOrderLine,
    SalesOrder,
    SalesOrderLine,
)
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind, DocumentStatus, PartnerDocument
from app.order_entry import flows as order_flows
from app.order_entry import grn as grn_service
from app.order_entry import orders as orders_service
from app.order_entry import quantities as order_quantities
from app.subledger import documents as documents_service
from tests.inventory.invariants import assert_stock_invariants
from tests.kernel.invariants import assert_ledger_invariants
from tests.order_entry.conftest import MARCH, OrderEntry, build_order_entry
from tests.order_entry.invariants import assert_order_invariants
from tests.subledger.invariants import assert_subledger_invariants

_EXAMPLE = itertools.count()
#: Which refusals the generator actually provoked. **Measured, not assumed**: the machine
#: used to clamp every match to the remaining quantity, which meant `match_exceeds_receipt`
#: could not be drawn at all and the suite looked as though it covered a boundary it never
#: reached. Counting them is how that stays honest.
_REFUSALS: dict[str, int] = {}


@pytest.fixture(scope="module", autouse=True)
def _report_refusals():  # noqa: ANN202
    yield
    if _REFUSALS:
        print("\n[property] refusals provoked:", dict(sorted(_REFUSALS.items())))
ZERO = Decimal(0)

OPERATIONS = (
    "receive",
    "match",
    "sell",
    "return_in",
    "return_out",
    "reverse",
    # Reversing the *receipt* rather than a document. Legal only while nothing has matched
    # against it, so this is what exercises `grn_matched` — and, when it is legal, the path
    # where an unmatched receipt's accrual credit has to come back off the account.
    "reverse_grn",
    # --- Step 3: the orders, and every way one can change after it is taken ----------------
    "create_so",
    "create_po",
    #: Deliberately **partial**: the quantity is drawn independently of what the order has
    #: left, so `invoice_exceeds_order` and `receipt_exceeds_order` are reached from both
    #: sides rather than only by a unit test that asks for them directly.
    "invoice_from_so",
    "receive_from_po",
    "edit_line",
    "close",
    "cancel",
)

QUANTITIES = st.integers(min_value=1, max_value=40).map(Decimal)
COSTS = st.decimals(min_value=Decimal("1"), max_value=Decimal("2000"), places=2)

PLAN = st.lists(
    st.tuples(
        st.sampled_from(OPERATIONS),
        QUANTITIES,
        COSTS,
        st.integers(min_value=0, max_value=20),  # which GRN line / document to act on
        # Which warehouse the goods move through, and — **drawn independently** — which
        # branch the document is keyed on. Independently is the whole point: the accrual is
        # proved per branch, and a rule that used the document's branch instead of the
        # warehouse's is invisible until the two differ.
        st.booleans(),  # depot, or main
        st.booleans(),  # key the document on the other branch
    ),
    min_size=1,
    max_size=10,
)


def _assert_everything(db: Session, company_id: int) -> None:
    assert_ledger_invariants(db, company_id)
    assert_subledger_invariants(db, company_id)
    assert_stock_invariants(db, company_id)
    assert_order_invariants(db, company_id)
    _assert_derived_quantities_match_a_recomputation(db, company_id)


# --- The brute-force recomputation -----------------------------------------------------------
#
# `app.order_entry.quantities` reads two SQL views. These functions answer the same questions
# by walking the ORM rows in Python, and the property is that the two agree after every step.
#
# The point is not that SQL is untrustworthy. It is that the view and the service share an
# author and therefore share his misunderstandings: a view that forgot to exclude reversed
# documents and a service that read it would be consistent with each other and wrong about the
# business. A second implementation written from the *rule* rather than from the first one is
# the only kind of check that can catch that.


def _posted_invoice_lines(db: Session, company_id: int) -> list:
    """Every line of every posted, unreversed invoice — the population both derived quantities
    are summed out of."""
    return [
        line
        for document in db.scalars(
            select(PartnerDocument).where(
                PartnerDocument.company_id == company_id,
                PartnerDocument.status == DocumentStatus.POSTED,
                PartnerDocument.kind == DocumentKind.INVOICE,
            )
        )
        for line in document.lines
    ]


def _recompute_sales(db: Session, company_id: int) -> dict[int, Decimal]:
    """Sales order line id → invoiced, from the rule: Σ base quantity of the posted, unreversed
    AR invoice lines carrying that line's id (decision 4)."""
    out: dict[int, Decimal] = {}
    for line in db.scalars(
        select(SalesOrderLine).where(SalesOrderLine.company_id == company_id)
    ):
        out[line.id] = ZERO
    for line in _posted_invoice_lines(db, company_id):
        if line.sales_order_line_id is not None:
            out[line.sales_order_line_id] = (
                out.get(line.sales_order_line_id, ZERO) + (line.base_quantity or ZERO)
            )
    return out


def _recompute_purchase(db: Session, company_id: int) -> dict[int, Decimal]:
    """Purchase order line id → received: unreversed GRN lines, **plus** the invoice lines that
    carried the goods themselves (a direct purchase, and every service line — a service is
    received by its invoice and never by a receipt)."""
    out: dict[int, Decimal] = {}
    for line in db.scalars(
        select(PurchaseOrderLine).where(PurchaseOrderLine.company_id == company_id)
    ):
        out[line.id] = ZERO
    for grn in db.scalars(
        select(grn_service.GoodsReceivedNote).where(
            grn_service.GoodsReceivedNote.company_id == company_id
        )
    ):
        if grn.status == GrnStatus.REVERSED:
            continue
        for line in grn.lines:
            if line.purchase_order_line_id is not None:
                out[line.purchase_order_line_id] = (
                    out.get(line.purchase_order_line_id, ZERO) + line.base_quantity
                )
    for line in _posted_invoice_lines(db, company_id):
        if line.purchase_order_line_id is not None and line.grn_line_id is None:
            out[line.purchase_order_line_id] = (
                out.get(line.purchase_order_line_id, ZERO) + (line.base_quantity or ZERO)
            )
    return out


def _recompute_positions(db: Session, company_id: int) -> dict[tuple[int, int], tuple]:
    """(item, warehouse) → (committed, on order), from open orders and stock items only."""
    invoiced = _recompute_sales(db, company_id)
    received = _recompute_purchase(db, company_id)
    out: dict[tuple[int, int], list[Decimal]] = {}

    def _add(item_id: int, warehouse_id: int, slot: int, amount: Decimal) -> None:
        item = db.get(Item, item_id)
        if item.item_type != ItemType.STOCK:
            # A kit is never on a shelf and a service has none; what a kit line promises is
            # its component lines, which are counted on their own rows.
            return
        cell = out.setdefault((item_id, warehouse_id), [ZERO, ZERO])
        cell[slot] += max(amount, ZERO)

    for order in db.scalars(select(SalesOrder).where(SalesOrder.company_id == company_id)):
        if order.status not in OPEN_SALES_STATUSES:
            continue
        for line in order.lines:
            _add(
                line.item_id,
                line.warehouse_id,
                0,
                line.base_quantity - invoiced.get(line.id, ZERO),
            )
    for order in db.scalars(select(PurchaseOrder).where(PurchaseOrder.company_id == company_id)):
        if order.status not in OPEN_PURCHASE_STATUSES:
            continue
        for line in order.lines:
            _add(
                line.item_id,
                line.warehouse_id,
                1,
                line.base_quantity - received.get(line.id, ZERO),
            )
    return {key: tuple(value) for key, value in out.items()}


def _assert_derived_quantities_match_a_recomputation(db: Session, company_id: int) -> None:
    sales = order_quantities.sales_fulfilment(db, company_id)
    expected_sales = _recompute_sales(db, company_id)
    assert {key: row.fulfilled for key, row in sales.items()} == expected_sales, (
        "the sales_order_line_quantities view disagrees with a recomputation"
    )

    purchase = order_quantities.purchase_fulfilment(db, company_id)
    expected_purchase = _recompute_purchase(db, company_id)
    assert {key: row.fulfilled for key, row in purchase.items()} == expected_purchase, (
        "the purchase_order_line_quantities view disagrees with a recomputation"
    )

    expected_positions = _recompute_positions(db, company_id)
    for (item_id, warehouse_id), (committed, on_order) in expected_positions.items():
        position = order_quantities.position(db, company_id, item_id, warehouse_id)
        assert position.committed == committed, (
            f"committed for item {item_id} at warehouse {warehouse_id}: "
            f"service {position.committed}, recomputation {committed}"
        )
        assert position.on_order == on_order, (
            f"on order for item {item_id} at warehouse {warehouse_id}: "
            f"service {position.on_order}, recomputation {on_order}"
        )
        # `available` is the definition, restated: a negative is a backorder, not an error.
        assert position.available == position.on_hand - committed


def _grn_lines(db: Session, fixture: OrderEntry) -> list:
    out = []
    for grn in db.scalars(
        select(grn_service.GoodsReceivedNote).where(
            grn_service.GoodsReceivedNote.company_id == fixture.company_id
        )
    ):
        if grn.status != GrnStatus.REVERSED:
            out.extend(grn.lines)
    return out


def _documents(db: Session, fixture: OrderEntry) -> list[PartnerDocument]:
    return list(
        db.scalars(
            select(PartnerDocument).where(
                PartnerDocument.company_id == fixture.company_id,
                PartnerDocument.status == DocumentStatus.POSTED,
            )
        )
    )


def _step(  # noqa: PLR0913
    db: Session,
    fixture: OrderEntry,
    operation: str,
    quantity,  # noqa: ANN001
    cost,  # noqa: ANN001
    pick: int,
    use_depot: bool,
    cross_branch: bool,
) -> None:
    """One operation, or nothing when the draw does not describe a legal one."""
    warehouse_id = fixture.depot.id if use_depot else fixture.main.id
    # The branch the *document* is keyed on, which may be neither the warehouse's nor the
    # default. `None` lets the document resolve its own.
    document_branch_id = (
        (fixture.main.branch_id if use_depot else fixture.depot_branch_id)
        if cross_branch
        else None
    )
    if operation == "receive":
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
                        item_id=fixture.stock_item.id, quantity=quantity, unit_cost=cost
                    ),
                ),
            ),
            actor=fixture.owner,
        )
        return

    if operation == "match":
        lines = _grn_lines(db, fixture)
        if not lines:
            return
        line = lines[pick % len(lines)]
        # **Not clamped to the remaining quantity, deliberately.** Clamping made the machine
        # unable to draw an over-match at all, so `match_exceeds_receipt` was never exercised
        # by the property suite — only by a unit test that asks for it directly. The refusal
        # is caught and skipped like any other illegal step, and the quantity is drawn as it
        # fell, so the boundary gets hit from both sides.
        documents_service.post_document(
            db,
            fixture.company_id,
            PartnerRole.AP,
            documents_service.DocumentInput(
                kind=DocumentKind.INVOICE,
                partner_id=fixture.supplier.id,
                document_date=MARCH,
                branch_id=document_branch_id,
                description="Supplier invoice",
                lines=(
                    documents_service.LineInput(
                        item_id=fixture.stock_item.id,
                        quantity=quantity,
                        unit_price=cost,
                        grn_line_id=line.id,
                    ),
                ),
            ),
            actor=fixture.owner,
        )
        return

    if operation in ("sell", "return_in"):
        documents_service.post_document(
            db,
            fixture.company_id,
            PartnerRole.AR,
            documents_service.DocumentInput(
                kind=DocumentKind.INVOICE if operation == "sell" else DocumentKind.CREDIT_NOTE,
                partner_id=fixture.customer.id,
                document_date=MARCH,
                branch_id=document_branch_id,
                description="Sale" if operation == "sell" else "Return",
                lines=(
                    documents_service.LineInput(
                        item_id=fixture.stock_item.id,
                        quantity=quantity,
                        unit_price=cost,
                        warehouse_id=warehouse_id,
                    ),
                ),
            ),
            actor=fixture.owner,
        )
        return

    if operation == "return_out":
        documents_service.post_document(
            db,
            fixture.company_id,
            PartnerRole.AP,
            documents_service.DocumentInput(
                kind=DocumentKind.CREDIT_NOTE,
                partner_id=fixture.supplier.id,
                document_date=MARCH,
                branch_id=document_branch_id,
                description="Return to supplier",
                lines=(
                    documents_service.LineInput(
                        item_id=fixture.stock_item.id,
                        quantity=quantity,
                        unit_price=cost,
                        warehouse_id=warehouse_id,
                    ),
                ),
            ),
            actor=fixture.owner,
        )
        return

    if operation == "reverse_grn":
        grns = [
            grn
            for grn in db.scalars(
                select(grn_service.GoodsReceivedNote).where(
                    grn_service.GoodsReceivedNote.company_id == fixture.company_id
                )
            )
            if grn.status != GrnStatus.REVERSED
        ]
        if not grns:
            return
        grn_service.reverse_grn(
            db,
            grns[pick % len(grns)],
            on_date=MARCH,
            reason="Property reversal",
            actor=fixture.owner,
        )
        return

    if operation == "reverse":
        documents = _documents(db, fixture)
        if not documents:
            return
        document = documents[pick % len(documents)]
        documents_service.reverse_document(
            db, document, on_date=MARCH, reason="Property reversal", actor=fixture.owner
        )
        return

    # --- Step 3: the orders ---------------------------------------------------------------
    #
    # Every one of these refuses **before it writes**, which is what lets the driver treat a
    # refusal as a no-op and carry on with the same tenant. A service that half-wrote an order
    # before refusing would leave the next step reading a state nothing produced.

    if operation == "create_so":
        # Half the draws order a **kit**, so the explosion, the component commitment and the
        # "components sum to kit quantity x per-kit" property are exercised by the machine and
        # not only by the kit unit tests.
        item = fixture.kit_item if cross_branch else fixture.stock_item
        orders_service.create_sales_order(
            db,
            fixture.company_id,
            orders_service.SalesOrderInput(
                partner_id=fixture.customer.id,
                order_date=MARCH,
                description="Sales order",
                warehouse_id=warehouse_id,
                lines=(
                    orders_service.OrderLineInput(
                        item_id=item.id, quantity=quantity, unit_price=cost
                    ),
                ),
            ),
            actor=fixture.owner,
        )
        return

    if operation == "create_po":
        orders_service.create_purchase_order(
            db,
            fixture.company_id,
            orders_service.PurchaseOrderInput(
                partner_id=fixture.supplier.id,
                order_date=MARCH,
                description="Purchase order",
                warehouse_id=warehouse_id,
                lines=(
                    orders_service.OrderLineInput(
                        item_id=fixture.stock_item.id, quantity=quantity, unit_price=cost
                    ),
                ),
            ),
            actor=fixture.owner,
        )
        return

    if operation == "invoice_from_so":
        orders = _open_sales_orders(db, fixture)
        if not orders:
            return
        order = orders[pick % len(orders)]
        prepared = order_flows.prepare_invoice_from_sales_order(db, fixture.company_id, order)
        documents_service.post_document(
            db,
            fixture.company_id,
            prepared.role,
            # **Not clamped to what remains.** The quantity is drawn as it fell, so
            # `invoice_exceeds_order` is reached from above as well as from below. The refusal
            # is caught and skipped like any other illegal step.
            _with_quantity(prepared.document, quantity),
            actor=fixture.owner,
        )
        return

    if operation == "receive_from_po":
        orders = _open_purchase_orders(db, fixture)
        if not orders:
            return
        order = orders[pick % len(orders)]
        prepared = order_flows.prepare_receipt_from_purchase_order(db, fixture.company_id, order)
        grn_service.post_grn(
            db,
            fixture.company_id,
            replace(
                prepared.grn,
                lines=(replace(prepared.grn.lines[0], quantity=quantity, unit_cost=cost),),
            ),
            actor=fixture.owner,
        )
        return

    if operation == "edit_line":
        orders = _open_purchase_orders(db, fixture)
        if not orders:
            return
        order = orders[pick % len(orders)]
        line = order.lines[0]
        orders_service.update_purchase_order(
            db,
            fixture.company_id,
            order,
            orders_service.PurchaseOrderInput(
                partner_id=order.partner_id,
                order_date=MARCH,
                description="Edited",
                warehouse_id=order.warehouse_id,
                lines=(
                    orders_service.OrderLineInput(
                        line_id=line.id,
                        item_id=line.item_id,
                        # Drawn, so an edit below what has already been received is attempted
                        # and refused rather than never tried.
                        quantity=quantity,
                        unit_price=cost,
                    ),
                ),
            ),
            actor=fixture.owner,
        )
        return

    if operation in ("close", "cancel"):
        sales = _open_sales_orders(db, fixture)
        purchases = _open_purchase_orders(db, fixture)
        if use_depot and sales:
            order = sales[pick % len(sales)]
            action = (
                orders_service.close_sales_order
                if operation == "close"
                else orders_service.cancel_sales_order
            )
        elif purchases:
            order = purchases[pick % len(purchases)]
            action = (
                orders_service.close_purchase_order
                if operation == "close"
                else orders_service.cancel_purchase_order
            )
        else:
            return
        action(db, order, on_date=MARCH, actor=fixture.owner)


def _with_quantity(document, quantity: Decimal):  # noqa: ANN001, ANN202
    """The prepared document with its first line re-keyed. Kit components are scaled with it,
    because a kit line that shipped its parent quantity and its components' original ones would
    be a bundle nobody ordered."""
    line = document.lines[0]
    if line.quantity == ZERO:
        return document
    share = quantity / line.quantity
    components = (
        tuple(
            replace(component, quantity=component.quantity * share)
            for component in line.kit_components
        )
        if line.kit_components is not None
        else None
    )
    return replace(
        document,
        lines=(replace(line, quantity=quantity, kit_components=components), *document.lines[1:]),
    )


def _open_sales_orders(db: Session, fixture: OrderEntry) -> list:
    return [
        order
        for order in db.scalars(
            select(SalesOrder).where(SalesOrder.company_id == fixture.company_id)
        )
        if order.status in OPEN_SALES_STATUSES
    ]


def _open_purchase_orders(db: Session, fixture: OrderEntry) -> list:
    return [
        order
        for order in db.scalars(
            select(PurchaseOrder).where(PurchaseOrder.company_id == fixture.company_id)
        )
        if order.status in OPEN_PURCHASE_STATUSES
    ]


def _drive(db: Session, fixture: OrderEntry, plan: list[tuple]) -> None:
    _assert_everything(db, fixture.company_id)
    for operation, quantity, cost, pick, use_depot, cross_branch in plan:
        try:
            _step(
                db,
                fixture,
                operation,
                quantity,
                Decimal(cost),
                pick,
                use_depot,
                cross_branch,
            )
        except (LedgerStateError, PostingError) as refused:
            _REFUSALS[getattr(refused, "code", "?")] = (
                _REFUSALS.get(getattr(refused, "code", "?"), 0) + 1
            )
            # An illegal step for the state we are in: matched beyond the receipt, issued
            # what is not there, reversed what is already reversed. Skipped, not failed.
            #
            # **No rollback**, deliberately — every one of these services refuses before it
            # writes, so there is nothing to undo, and rolling back here would discard the
            # tenant itself: the company, its partners and its items were created in this same
            # transaction, and the next step would fail looking for a supplier that no longer
            # existed. That is what it did before this comment was written.
            continue
        # **No status refresh here, deliberately.** Every workflow column in this phase is
        # written by the service that changed the fact underneath it (decision 4), and
        # `verify_order_statuses()` inside `assert_order_invariants` is what proves it. A
        # refresh loop in the driver would repair exactly the drift the assertion exists to
        # find, and the suite would pass over a service that had stopped writing its column.
        db.flush()
        _assert_everything(db, fixture.company_id)


@pytest.mark.slow
@given(plan=PLAN)
def test_the_invariants_hold_after_every_step_at_zero_decimals(
    db: Session, plan: list[tuple]
) -> None:
    """RWF: no minor unit, so every pro-rata relief rounds by up to half a franc and the
    "last match takes the remainder" rule is doing the most work it ever does."""
    _drive(db, build_order_entry(db, f"oe-rwf-{next(_EXAMPLE)}"), plan)


@pytest.mark.slow
@given(plan=PLAN)
def test_the_invariants_hold_under_the_blocking_backorder_policy(
    db: Session, plan: list[tuple]
) -> None:
    """The same machine with `backorder_policy = block`.

    It exists for one refusal the other two machines can never provoke: `exceeds_available`
    only fires under `block`, so a suite that ran only the default policy would report a census
    with a zero in it and would be covering a boundary it never reached. It also puts the
    *combination* under test — an order refused for lack of stock, then stock received, then the
    same order taken successfully — which is the sequence a shop actually lives.
    """
    fixture = build_order_entry(db, f"oe-block-{next(_EXAMPLE)}")
    fixture.settings.backorder_policy = BackorderPolicy.BLOCK
    db.flush()
    _drive(db, fixture, plan)


@pytest.mark.slow
@given(plan=PLAN)
def test_the_invariants_hold_after_every_step_at_two_decimals(
    db: Session, plan: list[tuple]
) -> None:
    """The same machine against a base currency with a minor unit."""
    fixture = build_order_entry(db, f"oe-usd-{next(_EXAMPLE)}")
    _use_a_two_decimal_base(db, fixture)
    _drive(db, fixture, plan)


def _use_a_two_decimal_base(db: Session, fixture: OrderEntry) -> None:
    base = db.scalar(
        select(Currency).where(
            Currency.company_id == fixture.company_id, Currency.is_base.is_(True)
        )
    )
    base.decimal_places = 2
    db.flush()


def test_the_1000_over_3_reversal_ties_out(db: Session, order_entry: OrderEntry) -> None:
    """The shape a generator would never draw, written out.

    Three units worth 1 000 relieve 333 + 333 + 334. Reverse the first and the ledger has
    relieved 667 where a recomputation over the survivors gives 666 — the franc that makes
    `accrual_relieved` a stored column rather than a derived one.
    """
    grn, _ = grn_service.post_grn(
        db,
        order_entry.company_id,
        grn_service.GrnInput(
            partner_id=order_entry.supplier.id,
            grn_date=MARCH,
            description="Three units worth a thousand",
            warehouse_id=order_entry.main.id,
            lines=(
                grn_service.GrnLineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(3),
                    unit_cost=Decimal("333.333333"),
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    assert grn.lines[0].value == Decimal(1_000)

    posted = []
    for _ in range(3):
        document, _ = documents_service.post_document(
            db,
            order_entry.company_id,
            PartnerRole.AP,
            documents_service.DocumentInput(
                kind=DocumentKind.INVOICE,
                partner_id=order_entry.supplier.id,
                document_date=MARCH,
                description="One unit",
                lines=(
                    documents_service.LineInput(
                        item_id=order_entry.stock_item.id,
                        quantity=Decimal(1),
                        unit_price=Decimal("333.333333"),
                        grn_line_id=grn.lines[0].id,
                    ),
                ),
            ),
            actor=order_entry.owner,
        )
        posted.append(document)
    shares = [document.lines[0].accrual_relieved for document in posted]
    assert sorted(shares) == [Decimal(333), Decimal(333), Decimal(334)], shares

    grn_service.refresh_status(db, grn)
    _assert_everything(db, order_entry.company_id)

    documents_service.reverse_document(
        db, posted[0], on_date=MARCH, reason="Billed twice", actor=order_entry.owner
    )
    grn_service.refresh_status(db, grn)
    _assert_everything(db, order_entry.company_id)
