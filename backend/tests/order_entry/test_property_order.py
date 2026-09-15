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
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kernel import posting
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.events import CashbookEntry, CashbookKind, CashbookLineSpec
from app.kernel.money import base_currency, round_amount
from app.models.currency import Currency
from app.models.fiscal import PeriodStatus
from app.models.gl import BackorderPolicy
from app.models.inventory import GrnStatus, Item, ItemType
from app.models.order_entry import (
    OPEN_PURCHASE_STATUSES,
    OPEN_SALES_STATUSES,
    LandedCostBasis,
    LandedCostDocument,
    LandedCostStatus,
    PurchaseOrder,
    PurchaseOrderLine,
    SalesOrder,
    SalesOrderLine,
)
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind, DocumentStatus, PartnerDocument
from app.order_entry import flows as order_flows
from app.order_entry import grn as grn_service
from app.order_entry import landed_cost as landed_cost_service
from app.order_entry import orders as orders_service
from app.order_entry import quantities as order_quantities
from app.subledger import documents as documents_service
from tests.inventory.invariants import assert_stock_invariants
from tests.kernel.invariants import assert_ledger_invariants
from tests.order_entry.conftest import APRIL, MARCH, OrderEntry, build_order_entry
from tests.order_entry.invariants import assert_order_invariants
from tests.subledger.invariants import assert_subledger_invariants

_EXAMPLE = itertools.count()
#: Which refusals the generator actually provoked. **Measured, not assumed**: the machine
#: used to clamp every match to the remaining quantity, which meant `match_exceeds_receipt`
#: could not be drawn at all and the suite looked as though it covered a boundary it never
#: reached. Counting them is how that stays honest.
_REFUSALS: dict[str, int] = {}
#: How far the machine actually got, for the operations whose interesting cases are a
#: *conjunction* rather than a single draw. `grn_matched` needs a receipt that has been matched
#: and then chosen for reversal; a census that only counted the refusal could not distinguish
#: "the guard held" from "the machine never got near it", which is what happened when step 3
#: doubled the operation pool.
_REACH: dict[str, int] = {}


def _count(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


#: The refusals a deep pass has to actually provoke, and the floor each has to clear.
#:
#: **Printed is not enforced.** Until this existed the census was a courtesy: "counted, not
#: assumed" held only while somebody read the output, and step 5 watched three consecutive deep
#: passes come back green with a different one of these at zero each time — `grn_reversed`
#: absent, then `weight_missing`, then `grn_matched` and `period_not_open`. Every one was a
#: generator defect, and every one would have shipped if the number had not been looked at by
#: hand. A floor is what makes the census a gate instead of a report.
#:
#: Three rather than one, because one is indistinguishable from a coincidence: a boundary hit
#: once in 300 examples is a boundary the next seed may well miss, and a guard "covered" by a
#: single draw is covered by luck. Three is not a statistical claim — it is the smallest number
#: that cannot be a single lucky plan.
REQUIRED_REFUSALS = (
    "grn_matched",
    "grn_reversed",
    "weight_missing",
    "period_not_open",
    "match_exceeds_receipt",
    "receipt_exceeds_order",
    "invoice_exceeds_order",
    "exceeds_available",
)
CENSUS_FLOOR = 3
#: Only a run with enough examples can be held to the floors. The per-commit profile draws two
#: and would fail every one of them, so it stays silent and the nightly deep profile is where
#: the census is enforced — the same split the profiles already make for everything else.
_FLOORS_FROM_EXAMPLES = 100


def _census() -> str:
    return (
        f"refusals: {dict(sorted(_REFUSALS.items()))}\n"
        f"reach:    {dict(sorted(_REACH.items()))}"
    )


@pytest.fixture(scope="module", autouse=True)
def _report_refusals():  # noqa: ANN202
    yield
    if _REFUSALS:
        print("\n[property] refusals provoked:", dict(sorted(_REFUSALS.items())))
    if _REACH:
        print("[property] reach:", dict(sorted(_REACH.items())))
    if settings.default.max_examples < _FLOORS_FROM_EXAMPLES:
        return
    short = {
        name: _REFUSALS.get(name, 0)
        for name in REQUIRED_REFUSALS
        if _REFUSALS.get(name, 0) < CENSUS_FLOOR
    }
    assert not short, (
        f"the deep pass did not reach {short} at least {CENSUS_FLOOR} times each.\n"
        "A refusal the machine never provokes is a guard this suite does not cover, however "
        "green it looks. Read the reach counters below before touching the floor: they say "
        "whether the guard held or the generator never got near it.\n" + _census()
    )
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
    # --- Step 5: landed cost, so invariant clauses 7 and 8 run under the generator ---------
    #: Books a cost to the clearing account — a duty payment through the P3 cashbook, which is
    #: only possible because 1370 is a plain account. Without it the machine could only ever
    #: drive the clearing balance negative, and "booked less allocated" would be tested with
    #: `booked` pinned at zero.
    "pay_duty",
    #: 1-3 receipt lines, any basis. The weight basis reaches `weight_missing` whenever a
    #: target is the weightless item, and posts when every target is the weighted one.
    "allocate",
    #: Takes an allocation back out. Its refusal is a **closed period** — a revaluation carries
    #: no quantity, so `block` has nothing to refuse and a landed cost is otherwise always
    #: reversible however the goods have moved since (step 4's negative finding).
    "reverse_lca",
)

#: What the machine actually draws from, and it is **not** `OPERATIONS`.
#:
#: `st.sampled_from` is uniform, so adding step 3's seven operations to step 2's seven halved
#: the density of every posting operation — and the refusals that need a *conjunction* stopped
#: being reached at all. `grn_matched` needs a receipt, a match against that receipt, and then a
#: `reverse_grn` that lands on it; at 1/14 per draw over a 10-step plan that is close to never,
#: and the reach counter measured it as exactly never: 389 `reverse_grn` draws, 328 with no
#: receipt at all to reverse and a matched one available on none of the remaining 61.
#:
#: So the posting operations — the ones that build the state everything else needs — are drawn
#: twice as often as the order operations, and plans run longer. That is a statement about how
#: hard each shape is to *reach*, not about how likely it is in a business, and the reach
#: counter is what keeps it honest rather than a number somebody tuned once and forgot.
#: Step 5 adds three landed-cost operations at the end, and they are drawn **twice** like the
#: posting operations rather than once like the order operations. `allocate` needs a receipt to
#: land on and `reverse_lca` needs an allocation to take back out, so both are conjunctions of
#: the same shape as `grn_matched`, and the same lever applies: draw them more often rather
#: than hope. `_REACH` counts what each one actually found, so the claim stays measured.
DRAW_POOL = (
    *OPERATIONS[:7],
    *OPERATIONS[:7],
    *OPERATIONS[7:14],
    *OPERATIONS[14:],
    *OPERATIONS[14:],
)

QUANTITIES = st.integers(min_value=1, max_value=40).map(Decimal)
COSTS = st.decimals(min_value=Decimal("1"), max_value=Decimal("2000"), places=2)

PLAN = st.lists(
    st.tuples(
        st.sampled_from(DRAW_POOL),
        QUANTITIES,
        COSTS,
        st.integers(min_value=0, max_value=20),  # which GRN line / document to act on
        # Which warehouse the goods move through, and — **drawn independently** — which
        # branch the document is keyed on. Independently is the whole point: the accrual is
        # proved per branch, and a rule that used the document's branch instead of the
        # warehouse's is invisible until the two differ.
        st.booleans(),  # depot, or main
        st.booleans(),  # key the document on the other branch
        # **Which item** — the weightless `stock_item` or the weighted one. Drawn rather than
        # fixed because the landed cost's `weight` basis needs both to be reachable: an item
        # with a weight for it to succeed on, and one without for `weight_missing` to fire.
        st.booleans(),
    ),
    min_size=1,
    # Longer than step 2's ten, and longer again at step 5. A three-step conjunction in a short
    # plan is the other half of why `grn_matched` stopped being reached, and step 5 added three
    # more operations, each of which lengthens the chains the others need: `reverse_lca` wants a
    # receipt, an allocation, and then itself. With the census now enforced as a floor rather
    # than printed, the margin has to come from somewhere, and the lever is the plan — biasing
    # the draw was measured at step 3 and bought nothing. The deep pass costs a couple more
    # minutes; the per-commit profile draws two examples either way.
    max_size=24,
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


def _all_grn_lines(db: Session, fixture: OrderEntry) -> list:
    """Every receipt line, **including reversed receipts** — which `_grn_lines` drops.

    `allocate` is the one operation that must see them. Adding cost to goods that were taken
    back is refused with `grn_reversed`, and a machine drawing only from live receipts could
    never provoke it: the first deep pass came back with that refusal absent from the census
    while every other landed-cost guard was reached, which is exactly the "the guard held" /
    "the machine never got near it" confusion `_REACH` exists to make visible.
    """
    return [
        line
        for grn in db.scalars(
            select(grn_service.GoodsReceivedNote).where(
                grn_service.GoodsReceivedNote.company_id == fixture.company_id
            )
        )
        for line in grn.lines
    ]


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
    weighted: bool = False,
) -> None:
    """One operation, or nothing when the draw does not describe a legal one."""
    warehouse_id = fixture.depot.id if use_depot else fixture.main.id
    #: The item this step acts on. Only `receive` varies it — everything downstream follows the
    #: receipt it lands on — so the two items stay distinguishable without the rest of the
    #: machine having to thread an item through every operation.
    item_id = (
        fixture.weighted_item.id
        if weighted and fixture.weighted_item is not None
        else fixture.stock_item.id
    )
    # The branch the *document* is keyed on, which may be neither the warehouse's nor the
    # default. `None` lets the document resolve its own.
    document_branch_id = (
        (fixture.main.branch_id if use_depot else fixture.depot_branch_id)
        if cross_branch
        else None
    )
    if operation == "receive":
        # **Which item the shelf actually gets.** The landed cost's weight basis can only be
        # exercised on a weighed item, and `weight_missing` only on an unweighed one, so the
        # mix of receipts is a precondition for two other counters below. A census that
        # reported the weight draws without reporting this could not say whether a zero meant
        # "the guard held" or "no receipt of that kind ever existed to draw on".
        _count(
            _REACH,
            "receive: weighed item" if item_id != fixture.stock_item.id
            else "receive: unweighed item",
        )
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
                        item_id=item_id, quantity=quantity, unit_cost=cost
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
        # **Clamped on half the draws, and only half.**
        #
        # Leaving the quantity entirely as it fell is what makes `match_exceeds_receipt`
        # reachable, and step 2 introduced that deliberately. But it also meant most matches
        # were refused, and `grn_matched` — which needs a receipt that has been matched *and*
        # then chosen for reversal — came back zero in a 300-example pass while
        # `match_exceeds_receipt` came back 62. The suite was exercising the boundary and
        # never the ordinary case behind it.
        #
        # So the over-match is drawn on odd picks and a legal match on even ones. Both paths
        # are counted below, because "the guard held" and "the machine never matched anything"
        # have to stay distinguishable — that is the whole lesson of the step-3 census.
        matched_already = grn_service.matched_quantities(db, fixture.company_id, [line.id])
        remaining = line.base_quantity - matched_already.get(line.id, ZERO)
        if pick % 2 == 0 and remaining > ZERO:
            quantity = min(quantity, remaining)
            _count(_REACH, "match: within the receipt")
        else:
            _count(_REACH, "match: as drawn, may exceed")
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
                        # The receipt's own item: receipts vary between the two stock items
                        # now, and an invoice line naming the other one is not a match.
                        item_id=line.item_id,
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
            _count(_REACH, "reverse_grn: no receipt to reverse")
            return
        # **Aim at a matched receipt whenever one exists**, which is what `grn_matched` needs.
        #
        # That refusal takes a conjunction — receive, match *that* receipt, then draw
        # `reverse_grn` and land on it — and step 3 doubled the operation pool from 7 to 14 while
        # the plan stayed at 10 steps, so the conjunction stopped happening. Measured over a
        # 300-example pass on the unbiased machine: `reverse_grn` drawn 252 times, 209 of those
        # with **no receipt at all** to reverse, and a matched one available on none of the rest.
        #
        # Biasing the *choice* costs nothing, because a matched receipt exists in a small
        # minority of states — `_REACH` below counts the split on every run, so that claim stays
        # measured rather than asserted, and the unmatched path (where an accrual credit has to
        # come back off the account) keeps the overwhelming majority of the draws.
        matched = grn_service.matched_quantities(
            db, fixture.company_id, [line.id for grn in grns for line in grn.lines]
        )
        with_a_match = [
            grn
            for grn in grns
            if any(matched.get(line.id, ZERO) > ZERO for line in grn.lines)
        ]
        _count(_REACH, "reverse_grn: a matched receipt existed" if with_a_match
               else "reverse_grn: nothing matched yet")
        candidates = with_a_match or grns
        grn_service.reverse_grn(
            db,
            candidates[pick % len(candidates)],
            on_date=MARCH,
            reason="Property reversal",
            actor=fixture.owner,
        )
        return

    if operation == "pay_duty":
        # **The booked side of clause 7.** Duty paid to the revenue authority lands on the
        # landed-cost clearing account through the P3 cashbook — a plain account, which is
        # exactly why decision 5 left 1370 plain while making 2350 a control account.
        clearing_id = fixture.settings.landed_cost_clearing_account_id
        if clearing_id is None:
            return
        posting.post(
            db,
            CashbookEntry(
                entry_date=MARCH,
                description="Duty",
                cash_account_id=fixture.accounts["1120"].id,
                kind=CashbookKind.PAYMENT,
                lines=(CashbookLineSpec(gl_account_id=clearing_id, amount=cost),),
            ),
            company_id=fixture.company_id,
            actor=fixture.owner,
        )
        return

    if operation == "allocate":
        lines = _all_grn_lines(db, fixture)
        if not lines:
            _count(_REACH, "allocate: no receipt to allocate onto")
            return
        # One to three targets, so the residue rule is exercised over a set that does not
        # divide evenly as often as a single target would.
        #
        # **Derived from a different digit of `pick` than the basis is**, which the first
        # version was not: `pick % 3` chose both, so the weight basis always had three targets,
        # `value` always had one, and `quantity` always had two. Three of the nine
        # (basis, width) combinations, and the residue rule — the thing three targets are here
        # to stress — was never once exercised on the weight basis with a single target.
        width = ((pick // 3) % 3) + 1
        start = pick % len(lines)
        targets = [lines[(start + offset) % len(lines)] for offset in range(width)]
        # De-duplicated, because naming one line twice is `duplicate_target` — a refusal about
        # the request rather than about the state, and not what this operation is for.
        chosen: list = []
        for line in targets:
            if line.id not in {other.id for other in chosen}:
                chosen.append(line)
        basis = (LandedCostBasis.VALUE, LandedCostBasis.QUANTITY, LandedCostBasis.WEIGHT)[
            pick % 3
        ]
        if basis == LandedCostBasis.WEIGHT:
            # Counted rather than avoided: whether the weight basis can post at all depends on
            # what the receipts in this example happened to be for, and a census that could not
            # tell "refused" from "never attempted on a weighed item" is the thing `_REACH`
            # exists to prevent.
            weighed = all(
                line.item_id == fixture.weighted_item.id
                for line in chosen
                if fixture.weighted_item is not None
            )
            _count(
                _REACH,
                "allocate: weight basis on weighed targets"
                if weighed
                else "allocate: weight basis with an unweighed target",
            )
        _count(
            _REACH,
            "allocate: a reversed receipt among the targets"
            if any(line.grn.status == GrnStatus.REVERSED for line in chosen)
            else "allocate: live receipts only",
        )
        # **Quantized to the base currency**, because `amount_precision` is a refusal about
        # the *keystroke* rather than about the state. The costs are drawn at two decimal
        # places and the RWF machine has none, so a quarter of every allocate draw was being
        # thrown away on a formatting complaint before it could reach a rule worth testing:
        # 250 `amount_precision` refusals in the first deep pass, against 15 weight draws.
        amount = round_amount(cost, base_currency(db, fixture.company_id).decimal_places)
        if amount <= ZERO:
            return
        landed_cost_service.post_landed_cost(
            db,
            fixture.company_id,
            landed_cost_service.LandedCostInput(
                cost_date=MARCH,
                description="Freight",
                amount=amount,
                basis=basis,
                grn_line_ids=tuple(line.id for line in chosen),
            ),
            actor=fixture.owner,
        )
        return

    if operation == "reverse_lca":
        documents = [
            document
            for document in db.scalars(
                select(LandedCostDocument).where(
                    LandedCostDocument.company_id == fixture.company_id
                )
            )
            if document.status == LandedCostStatus.POSTED
        ]
        if not documents:
            _count(_REACH, "reverse_lca: nothing allocated to reverse")
            return
        # **Half the reversals are dated into a closed period**, because that is the only thing
        # that can refuse this one. A landed cost posts revaluation moves, which carry no
        # quantity, so `block` has nothing to refuse and the goods having been sold since makes
        # no difference — step 4 asserted that as a negative finding. The period is what is
        # left, and without aiming at it `period_not_open` would sit at zero in the census.
        into_a_closed_period = bool(pick % 2)
        _count(
            _REACH,
            "reverse_lca: into a closed period"
            if into_a_closed_period
            else "reverse_lca: into an open one",
        )
        landed_cost_service.reverse_landed_cost(
            db,
            documents[pick % len(documents)],
            on_date=APRIL if into_a_closed_period else MARCH,
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
            _count(_REACH, "invoice_from_so: no open sales order")
            return
        _count(_REACH, "invoice_from_so: an open order to invoice")
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
            _count(_REACH, "receive_from_po: no open purchase order")
            return
        _count(_REACH, "receive_from_po: an open order to receive")
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
    # **Costs are quantized to the base currency once, here.** They are drawn at two decimal
    # places and the RWF machine has none, so every step keyed with a fraction of a franc was
    # refused with `amount_precision` before it could reach a rule worth testing — 201 of them
    # in a 300-example pass, a seventh of every step the machine took, spent on a complaint
    # about a keystroke. The refusal is request validation and has its own unit test; the
    # two-decimal machine still draws cents, so the precision path is not lost here either.
    places = base_currency(db, fixture.company_id).decimal_places
    for operation, quantity, cost, pick, use_depot, cross_branch, weighted in plan:
        priced = round_amount(Decimal(cost), places)
        if priced <= ZERO:
            continue
        # **A savepoint per step**, so a refused step leaves nothing behind whatever the service
        # did before refusing.
        #
        # This used to run without one, on the premise that every service in the phase refuses
        # before it writes, and a plain rollback was rejected because it would discard the tenant
        # itself: the company, its partners and its items are created in this same transaction.
        #
        # The premise was false, and the machine found where — `reverse_document` posted the
        # partner-side reversal before asking the inventory service to reverse the companion,
        # which under `block` raises `insufficient_stock` once the goods have been sold on. That
        # left a posted reversal entry for a document still marked posted and still fully open:
        # `AR control account is 16.00 but open items total 15.00`, on the plan
        # receive 1 · receive 14 · return_in 1 · sell 16 · reverse · receive 1.
        #
        # The ordering is fixed at the source now — the companion goes first, because it is the
        # only half that can fail — and `test_a_refused_reversal_leaves_nothing_behind` holds the
        # service to it **without** a savepoint. This stays because the premise should be
        # enforced here rather than assumed of every service a later step adds: it undoes the
        # step and keeps the tenant, which is exactly what was wanted.
        step = db.begin_nested()
        try:
            _step(
                db,
                fixture,
                operation,
                quantity,
                priced,
                pick,
                use_depot,
                cross_branch,
                weighted,
            )
        except (LedgerStateError, PostingError) as refused:
            step.rollback()
            _count(_REFUSALS, getattr(refused, "code", "?"))
            # An illegal step for the state we are in: matched beyond the receipt, issued
            # what is not there, reversed what is already reversed. Skipped, not failed.
            continue
        step.commit()
        # **No status refresh here, deliberately.** Every workflow column in this phase is
        # written by the service that changed the fact underneath it (decision 4), and
        # `verify_order_statuses()` inside `assert_order_invariants` is what proves it. A
        # refresh loop in the driver would repair exactly the drift the assertion exists to
        # find, and the suite would pass over a service that had stopped writing its column.
        db.flush()
        _assert_everything(db, fixture.company_id)


def _machine_fixture(db: Session, tag: str) -> OrderEntry:
    """A tenant for one example, with **April closed**.

    The closed period is the machine's only way to reach `period_not_open`, and it is reached
    through `reverse_lca`: a landed cost carries no quantity, so the negative-stock policy can
    never refuse one, and step 4 recorded that as a finding rather than leaving it as a gap.
    Closing a period the tape never posts into costs the rest of the machine nothing — every
    other operation is dated in March.
    """
    fixture = build_order_entry(db, tag)
    for period in fixture.ledger.periods:
        if period.start_date <= APRIL <= period.end_date:
            period.status = PeriodStatus.CLOSED
    db.flush()
    return fixture


@pytest.mark.slow
@given(plan=PLAN)
def test_the_invariants_hold_after_every_step_at_zero_decimals(
    db: Session, plan: list[tuple]
) -> None:
    """RWF: no minor unit, so every pro-rata relief rounds by up to half a franc and the
    "last match takes the remainder" rule is doing the most work it ever does."""
    _drive(db, _machine_fixture(db, f"oe-rwf-{next(_EXAMPLE)}"), plan)


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
    fixture = _machine_fixture(db, f"oe-block-{next(_EXAMPLE)}")
    fixture.settings.backorder_policy = BackorderPolicy.BLOCK
    db.flush()
    _drive(db, fixture, plan)


@pytest.mark.slow
@given(plan=PLAN)
def test_the_invariants_hold_after_every_step_at_two_decimals(
    db: Session, plan: list[tuple]
) -> None:
    """The same machine against a base currency with a minor unit."""
    fixture = _machine_fixture(db, f"oe-usd-{next(_EXAMPLE)}")
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


def test_a_refused_reversal_leaves_nothing_behind(db: Session, order_entry: OrderEntry) -> None:
    """The plan the machine shrank to, written out — because a Hypothesis example database is
    not committed and the next contributor would find this only by drawing it again.

    `reverse_document` used to post the partner-side reversal and only then ask the inventory
    service to reverse the companion; under `block` that raises `insufficient_stock` once the
    goods have been sold on, so the refusal arrived after the ledger had been written. The
    caller was left holding a posted reversal entry for a document still marked posted and still
    fully open: `AR control account is 16.00 but open items total 15.00`.

    The companion goes first now, because it is the only half that can fail. This test drives
    the plan with **no savepoint and no rollback**, so it passes only while that holds — an
    endpoint would hide the difference, because closing its session rolls the whole request back
    either way.
    """
    _use_a_two_decimal_base(db, order_entry)
    plan = [
        ("receive", Decimal(1), Decimal("1.00")),
        ("receive", Decimal(14), Decimal("1.00")),
        ("return_in", Decimal(1), Decimal("1.00")),
        ("sell", Decimal(16), Decimal("1.00")),
        ("reverse", Decimal(1), Decimal("1.00")),
        ("receive", Decimal(1), Decimal("1.00")),
    ]
    # **Driven without savepoints, deliberately.** The claim here is about the *service*: that
    # `reverse_document` refuses before it writes, so a caller inside a larger unit of work is
    # left with nothing to unwind. An endpoint gets that for free — its session is closed, and
    # closing rolls back — but the property driver does not, and neither will the landed-cost
    # reversal or any other caller that reverses a document as one step of several.
    refusals: list[str] = []
    for operation, quantity, cost in plan:
        try:
            _step(db, order_entry, operation, quantity, cost, 0, False, False)
        except (LedgerStateError, PostingError) as refused:
            refusals.append(refused.code)
        db.flush()
        _assert_everything(db, order_entry.company_id)

    # The reversal really was refused — an assertion that only proved the invariants would pass
    # just as well on a sequence where nothing interesting happened.
    assert refusals == ["insufficient_stock"], refusals


def test_the_census_floor_would_notice_a_guard_the_machine_stopped_reaching() -> None:
    """Anti-vacuity for the floors: the check has to fail on a census that falls short, and
    has to put the census in the message.

    Without this the floors are themselves unenforced — a typo in the names, or a comparison
    that can never be false, would leave a gate that greets every run with approval. The three
    passes that shipped a zero in the census are what this is standing in for.
    """
    census = {"exceeds_available": 40, "grn_matched": 2}
    short = {
        name: census.get(name, 0)
        for name in REQUIRED_REFUSALS
        if census.get(name, 0) < CENSUS_FLOOR
    }
    # One below the floor and six absent — absent has to count as short, not as "not
    # applicable", which is the reading that let `grn_reversed` sit missing for a whole pass.
    assert short["grn_matched"] == 2
    assert set(short) == set(REQUIRED_REFUSALS) - {"exceeds_available"}

    # A census that clears every floor produces nothing to report.
    clear = dict.fromkeys(REQUIRED_REFUSALS, CENSUS_FLOOR)
    assert not {
        name: clear[name] for name in REQUIRED_REFUSALS if clear[name] < CENSUS_FLOOR
    }
