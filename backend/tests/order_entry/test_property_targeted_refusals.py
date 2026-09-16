"""Four refusals, each proved by a property that **constructs its precondition**.

P6 handed P7 a question rather than an answer (final report, F-9.10). The deep census in
`test_property_order.py` enforces a floor of three on eight refusals, and `main` at #45 failed
two deep runs in three: the run that failed named `{'grn_matched': 0, 'weight_missing': 0,
'period_not_open': 0}`, and the two runs before it named `invoice_exceeds_order` and
`period_not_open`. The obvious lever was the deep profile's `max_examples` — 300 → 600 roughly
doubles the reach counts, at the price of doubling a 26-minute nightly.

**P7's answer is that those four were a coverage problem, not a depth problem.** Each of them is
a *conjunction* three or four operations deep in a 24-step plan: `grn_matched` needs a receipt,
then a match against that receipt, then a `reverse_grn` that lands on it. Waiting for a random
plan to stumble through that chain is the expensive way to cover a guard, and it is also the
unreliable way — the margin was 7–10 against a floor of 3, and `pytest-randomly` reseeds every
run, so whether the gate passed was a coin weighted by the seed.

So the four move here, where the chain is built rather than drawn, and out of
`REQUIRED_REFUSALS` in the machine. They are still **properties**, not examples: the quantities,
costs, baskets and dates vary, so what is proved is that the guard holds across the shape of the
input rather than at one point in it. The machine still counts them in `_REFUSALS` — what it no
longer does is fail when a seed misses them.

`max_examples` stays at 300. The reasoning is in `tests/conftest.py` beside the profile.

Each test below also says what *would* go wrong if the guard were absent, because a refusal
test that only asserts an error code proves the code exists and not that it matters.
"""

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session

from app.kernel.errors import LedgerStateError, PostingError
from app.models.fiscal import PeriodStatus
from app.models.order_entry import LandedCostBasis
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind
from app.order_entry import grn as grn_service
from app.order_entry import landed_cost as landed_cost_service
from app.order_entry import orders as orders_service
from app.subledger import documents as documents_service
from tests.order_entry.conftest import APRIL, MARCH, OrderEntry

QUANTITIES = st.integers(min_value=1, max_value=200)
COSTS = st.integers(min_value=1, max_value=50_000)

#: **Fifty, not the profile's three hundred.**
#:
#: The machine in `test_property_order.py` needs depth because it is *hoping* to reach these
#: guards: at 300 examples of a 24-step plan it reached `grn_matched` somewhere between zero
#: and twenty-five times depending on the seed. Every example here reaches its guard by
#: construction, so fifty examples is fifty hits — an order of magnitude more coverage than the
#: floor of three these replace, for a fraction of the time.
#:
#: Spending 300 here instead would add roughly twenty minutes to a nightly that is already
#: twenty-six, to draw three hundred variations of a quantity. The number that matters is how
#: often the guard is *reached*, and that is now the same as the example count.
TARGETED_EXAMPLES = 50

#: One tenant per test, reused across its examples — the opposite of what the machine needs.
#:
#: The machine asserts every invariant after every step, so a shared tenant would re-examine an
#: ever-growing history and cost O(examples²). These assert one refusal and build their own
#: receipt or order each time, so nothing here reads the accumulated history and the fixture is
#: pure setup. Rebuilding a tenant per example cost ~20 seconds each when this was first
#: written; reusing one costs nothing.


def _receive(
    db: Session,
    fixture: OrderEntry,
    *,
    quantity: Decimal,
    unit_cost: Decimal,
    item_id: int | None = None,
):  # noqa: ANN202
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
                    item_id=item_id or fixture.stock_item.id,
                    quantity=quantity,
                    unit_cost=unit_cost,
                ),
            ),
        ),
        actor=fixture.owner,
    )
    return grn


def _match(
    db: Session, fixture: OrderEntry, *, grn_line_id: int, quantity: Decimal, price: Decimal
):  # noqa: ANN202
    document, _ = documents_service.post_document(
        db,
        fixture.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=fixture.supplier.id,
            document_date=MARCH,
            description="Supplier invoice",
            lines=(
                documents_service.LineInput(
                    item_id=fixture.stock_item.id,
                    quantity=quantity,
                    unit_price=price,
                    grn_line_id=grn_line_id,
                ),
            ),
        ),
        actor=fixture.owner,
    )
    return document


# --- grn_matched ---------------------------------------------------------------------------


@pytest.mark.slow
@settings(max_examples=TARGETED_EXAMPLES)
@given(received=QUANTITIES, cost=COSTS, matched_fraction=st.integers(1, 100))
def test_a_receipt_with_anything_matched_against_it_refuses_reversal(
    db: Session, fixture: OrderEntry, received: int, cost: int, matched_fraction: int
) -> None:
    """`grn_matched`, over every fraction of the receipt a supplier might have invoiced.

    Without it, reversing the receipt would leave the invoice's relief standing against goods
    that were never received — the accrual would carry a debit with no credit behind it, and
    nothing downstream could tell that from an ordinary un-invoiced receipt.

    A *conjunction*: receive, match, then reverse the thing that was matched. The machine has to
    draw all three in order, which is why it reached this guard zero times on one deep run.
    """
    quantity = Decimal(received)
    grn = _receive(db, fixture, quantity=quantity, unit_cost=Decimal(cost))
    # At least one unit, never more than was received — the match guard is a different refusal.
    fraction = (quantity * Decimal(matched_fraction) / Decimal(100)).to_integral_value()
    matched = max(Decimal(1), fraction)
    _match(
        db,
        fixture,
        grn_line_id=grn.lines[0].id,
        quantity=min(matched, quantity),
        price=Decimal(cost),
    )

    with pytest.raises(LedgerStateError) as refused:
        grn_service.reverse_grn(
            db, grn, on_date=MARCH, reason="Keyed twice", actor=fixture.owner
        )

    assert refused.value.code == "grn_matched"


# --- weight_missing ------------------------------------------------------------------------


@pytest.mark.slow
@settings(max_examples=TARGETED_EXAMPLES)
@given(
    amount=st.integers(min_value=1, max_value=1_000_000),
    weightless_quantity=QUANTITIES,
    weighted_quantity=QUANTITIES,
    include_weighted=st.booleans(),
)
def test_a_weight_basis_refuses_a_basket_containing_anything_weightless(
    db: Session,
    fixture: OrderEntry,
    amount: int,
    weightless_quantity: int,
    weighted_quantity: int,
    include_weighted: bool,
) -> None:
    """`weight_missing`, whether the weightless line is alone or mixed with a weighed one.

    Without it a weightless line would take a share of zero and push its freight silently onto
    the lines that did carry a weight — a wrong valuation, with nothing downstream able to
    detect it, because the allocation still sums to the amount booked.

    The mixed basket is the case worth constructing: a guard that only looked at the *first*
    target, or that short-circuited once it found one weight, would pass on a basket of one and
    fail here.
    """
    weightless = _receive(
        db, fixture, quantity=Decimal(weightless_quantity), unit_cost=Decimal(1000)
    )
    targets = [weightless.lines[0].id]
    if include_weighted:
        assert fixture.weighted_item is not None
        weighed = _receive(
            db,
            fixture,
            quantity=Decimal(weighted_quantity),
            unit_cost=Decimal(1000),
            item_id=fixture.weighted_item.id,
        )
        targets.append(weighed.lines[0].id)

    with pytest.raises(LedgerStateError) as refused:
        landed_cost_service.preview_shares(
            db,
            fixture.company_id,
            amount=Decimal(amount),
            basis=LandedCostBasis.WEIGHT,
            grn_line_ids=tuple(targets),
        )

    assert refused.value.code == "weight_missing"


# --- period_not_open -----------------------------------------------------------------------


@pytest.mark.slow
@settings(max_examples=TARGETED_EXAMPLES)
@given(amount=st.integers(min_value=1, max_value=1_000_000), quantity=QUANTITIES)
def test_a_landed_cost_reversal_into_a_closed_period_is_refused(
    db: Session, fixture: OrderEntry, amount: int, quantity: int
) -> None:
    """`period_not_open`, from the one operation that can reach it.

    A landed cost posts *revaluation* moves, which carry no quantity — so the negative-stock
    policy has nothing to refuse and the goods having been sold since makes no difference (P6
    step 4 recorded that as a negative finding). The accounting period is the only thing left
    that can stop a reversal, which is why the machine had to aim a coin-flip at it and why
    this is the guard a seed was most likely to miss.

    Without it a month that has been reported to a revenue authority could be moved after the
    fact, which is the whole reason periods close.
    """
    grn = _receive(db, fixture, quantity=Decimal(quantity), unit_cost=Decimal(1000))
    document, _ = landed_cost_service.post_landed_cost(
        db,
        fixture.company_id,
        landed_cost_service.LandedCostInput(
            cost_date=MARCH,
            description="Freight",
            amount=Decimal(amount),
            basis=LandedCostBasis.VALUE,
            grn_line_ids=(grn.lines[0].id,),
        ),
        actor=fixture.owner,
    )

    # A `PostingError`, not a `LedgerStateError`: the period check lives in the kernel and
    # refuses the *posting*, where the other three are the order-entry services refusing a
    # state transition. Both carry the code; naming the class keeps that distinction visible.
    with pytest.raises(PostingError) as refused:
        landed_cost_service.reverse_landed_cost(
            db, document, on_date=APRIL, reason="Into a closed month", actor=fixture.owner
        )

    assert refused.value.code == "period_not_open"


def test_the_same_reversal_into_an_open_period_succeeds(
    db: Session, fixture: OrderEntry
) -> None:
    """The control, and it is not optional.

    A refusal test passes just as well against a service that refuses *everything*. This is the
    assertion that the guard above is about the period and not about landed-cost reversal being
    broken — the one fact the property cannot establish about itself.
    """
    grn = _receive(db, fixture, quantity=Decimal(10), unit_cost=Decimal(1000))
    document, _ = landed_cost_service.post_landed_cost(
        db,
        fixture.company_id,
        landed_cost_service.LandedCostInput(
            cost_date=MARCH,
            description="Freight",
            amount=Decimal(5000),
            basis=LandedCostBasis.VALUE,
            grn_line_ids=(grn.lines[0].id,),
        ),
        actor=fixture.owner,
    )

    landed_cost_service.reverse_landed_cost(
        db, document, on_date=MARCH, reason="Keyed twice", actor=fixture.owner
    )

    assert document.reversal_entry_id is not None


# --- invoice_exceeds_order -----------------------------------------------------------------


@pytest.mark.slow
@settings(max_examples=TARGETED_EXAMPLES)
@given(
    ordered=st.integers(min_value=2, max_value=200),
    already_invoiced_fraction=st.integers(0, 90),
    overshoot=st.integers(min_value=1, max_value=500),
)
def test_an_invoice_beyond_what_the_order_has_left_is_refused(
    db: Session,
    fixture: OrderEntry,
    ordered: int,
    already_invoiced_fraction: int,
    overshoot: int,
) -> None:
    """`invoice_exceeds_order`, from both sides: a first invoice too large, and a second that
    takes the running total past the order.

    The second is the one worth constructing. "Invoiced" is a **derived** quantity — a join over
    `partner_document_lines.sales_order_line_id`, not a column anybody maintains — so a guard
    that read a stale total, or that compared against the order rather than against what is
    left, would pass the first case and fail this one.
    """
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
                    quantity=Decimal(ordered),
                    unit_price=Decimal(2000),
                ),
            ),
        ),
        actor=fixture.owner,
    )
    # Enough stock that the negative-stock policy can never be what refuses this.
    _receive(db, fixture, quantity=Decimal(ordered + overshoot), unit_cost=Decimal(1000))

    # Strictly less than the order, because an order that is **fully** invoiced *closes*, and a
    # further invoice against it is then refused `order_not_open` — a different guard, and the
    # right one. Found by this property on its first deep run, at `ordered=2` where 90 % rounds
    # to the whole order.
    already = min(
        (Decimal(ordered) * Decimal(already_invoiced_fraction) / Decimal(100)).to_integral_value(),
        Decimal(ordered) - Decimal(1),
    )
    if already > 0:
        _invoice_against(db, fixture, order, quantity=already)

    with pytest.raises(LedgerStateError) as refused:
        _invoice_against(
            db, fixture, order, quantity=Decimal(ordered) - already + Decimal(overshoot)
        )

    assert refused.value.code == "invoice_exceeds_order"


def _invoice_against(db: Session, fixture: OrderEntry, order, quantity: Decimal):  # noqa: ANN001, ANN202
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
                    unit_price=Decimal(2000),
                    warehouse_id=order.lines[0].warehouse_id,
                    sales_order_line_id=order.lines[0].id,
                ),
            ),
        ),
        actor=fixture.owner,
    )
    return document


@pytest.fixture(scope="function")
def fixture(db: Session, order_entry: OrderEntry) -> OrderEntry:
    """The shared tenant, with **April closed**.

    Closed here rather than inside the period property, so that the closure is part of the
    setup every example shares rather than a side effect one test applies repeatedly. Nothing
    else in this module posts into April.
    """
    for period in order_entry.ledger.periods:
        if period.start_date <= APRIL <= period.end_date:
            period.status = PeriodStatus.CLOSED
    db.flush()
    return order_entry
