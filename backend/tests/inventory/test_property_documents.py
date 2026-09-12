"""The properties step 4 names, driven by Hypothesis over transfers and counts.

Three statements, each of which is about a *document* rather than about the costing engine
underneath it:

1. **A transfer conserves value.** Dispatch then receive returns the item to the total value
   it had before, whatever the average did in between and whatever quantity moved — because
   the arrival is costed at the value the dispatch froze, never re-derived.
2. **A stale count cannot be processed.** If anything posts to a counted line's location after
   the snapshot, Process refuses the whole session.
3. **Transfer-now under `block` with too little at the source posts nothing at all.** Not the
   source emptied and the destination left waiting: nothing.

Every example runs in a tenant of its own (`fresh_stock`), for the reason step 2's machine
does: Hypothesis reuses a function-scoped fixture across examples, so a suite asserted after
every step would re-examine an ever-growing history and cost O(examples²).

All three carry `@pytest.mark.slow`, which is how the nightly deep workflow selects them
(`pytest -m slow`); `tests/test_property_markers.py` fails the build if one loses the marker.
"""

import itertools
from datetime import timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.inventory import counts as count_service
from app.inventory import documents as documents_service
from app.inventory import stock as stock_service
from app.inventory import transfers as transfer_service
from app.kernel.errors import LedgerStateError
from app.models.inventory import (
    NegativeStockPolicy,
    StockCountStatus,
    StockMove,
    StockTransferStatus,
)
from tests.inventory.conftest import Stock, fresh_stock, receive
from tests.inventory.invariants import assert_stock_invariants, location_position
from tests.kernel.invariants import assert_ledger_invariants
from tests.subledger.conftest import MARCH

_EXAMPLE = itertools.count()

QUANTITIES = st.one_of(
    st.integers(min_value=1, max_value=40).map(Decimal),
    st.integers(min_value=100_000, max_value=3_000_000).map(Decimal),
)
COSTS = st.decimals(
    min_value=Decimal("0.0001"), max_value=Decimal(5000), places=4, allow_nan=False
)


def _both_invariants(db: Session, fixture: Stock) -> None:
    assert_stock_invariants(db, fixture.company_id)
    assert_ledger_invariants(db, fixture.company_id)


def _total_value(db: Session, fixture: Stock) -> Decimal:
    return Decimal(
        db.scalar(
            select(func.coalesce(func.sum(StockMove.value), 0)).where(
                StockMove.company_id == fixture.company_id
            )
        )
    )


def _move_count(db: Session, fixture: Stock) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(StockMove)
            .where(StockMove.company_id == fixture.company_id)
        )
    )


def _transfer_input(fixture: Stock, quantity: Decimal) -> transfer_service.TransferInput:
    return transfer_service.TransferInput(
        transfer_date=MARCH,
        description="property transfer",
        from_warehouse_id=fixture.main.id,
        to_warehouse_id=fixture.depot.id,
        lines=(
            transfer_service.TransferLineInput(item_id=fixture.item.id, quantity=quantity),
        ),
    )


@pytest.mark.slow
@given(
    on_hand=QUANTITIES,
    unit_cost=COSTS,
    moved=QUANTITIES,
    interfering_cost=COSTS,
)
def test_dispatch_then_receive_returns_the_item_to_the_same_total_value(
    db: Session,
    on_hand: Decimal,
    unit_cost: Decimal,
    moved: Decimal,
    interfering_cost: Decimal,
) -> None:
    """Property 1. A receipt at an unrelated cost lands *between* the legs, so the average the
    arrival would be re-costed at is never the one the dispatch used — which is what makes
    "the total is unchanged" a statement about the frozen value rather than a coincidence."""
    fixture = fresh_stock(db, f"trf-{next(_EXAMPLE)}")
    # Clamped rather than assumed away: a third of the draws had `moved` above `on_hand` and
    # were thrown out, and clamping spends them on the case worth having instead — a transfer
    # that empties the source, which is where the flush rule decides the value that travels.
    moved = min(moved, on_hand)
    receive(db, fixture, quantity=on_hand, unit_cost=unit_cost, on=MARCH)

    before = _total_value(db, fixture)
    transfer, _ = transfer_service.post_transfer(
        db,
        fixture.company_id,
        _transfer_input(fixture, moved),
        receive_now=False,
        actor=fixture.owner,
    )
    _both_invariants(db, fixture)
    in_transit = _total_value(db, fixture)

    # Something else changes the average while the stock is on the road.
    receive(db, fixture, quantity=Decimal(7), unit_cost=interfering_cost, on=MARCH)
    moved_value = _total_value(db, fixture) - in_transit

    transfer_service.receive_transfer(
        db, fixture.company_id, transfer.id, on_date=MARCH, actor=fixture.owner
    )

    assert transfer.status == StockTransferStatus.COMPLETED
    assert _total_value(db, fixture) == before + moved_value, (
        "the transfer created or destroyed value: the arrival was re-costed instead of "
        "taking what the dispatch froze"
    )
    assert location_position(
        db, fixture.company_id, fixture.item.id, fixture.transit.id
    ) == stock_service.LocationState(), "stock was left in transit"
    _both_invariants(db, fixture)


@pytest.mark.slow
@given(
    on_hand=QUANTITIES,
    unit_cost=COSTS,
    counted=QUANTITIES,
    interference=QUANTITIES,
    interfering_cost=COSTS,
    days=st.integers(min_value=0, max_value=5),
)
def test_a_count_processed_against_a_stale_line_is_impossible(
    db: Session,
    on_hand: Decimal,
    unit_cost: Decimal,
    counted: Decimal,
    interference: Decimal,
    interfering_cost: Decimal,
    days: int,
) -> None:
    """Property 2. Whatever was counted, whatever moved afterwards and whenever it is dated —
    including *before* the snapshot, which is the case a wall-clock check would miss —
    Process refuses the session and nothing is posted."""
    fixture = fresh_stock(db, f"cnt-{next(_EXAMPLE)}")
    receive(db, fixture, quantity=on_hand, unit_cost=unit_cost, on=MARCH)

    session = count_service.open_session(
        db,
        fixture.company_id,
        count_service.CountSessionInput(
            warehouse_id=fixture.main.id, count_date=MARCH, description="property count"
        ),
        actor=fixture.owner,
    )
    line = count_service.lines_of(db, fixture.company_id, session.id)[0]
    count_service.enter_count(
        db,
        fixture.company_id,
        session.id,
        line.id,
        quantity=counted,
        actor=fixture.owner,
    )

    # A delivery lands on the counted shelf. Dated backwards as often as forwards: posting
    # order is what staleness is measured in, and a backdated document still lands *now*.
    receive(
        db,
        fixture,
        quantity=interference,
        unit_cost=interfering_cost,
        on=MARCH - timedelta(days=days),
    )
    before = _move_count(db, fixture)

    with pytest.raises(LedgerStateError) as error:
        count_service.process_session(
            db, fixture.company_id, session.id, actor=fixture.owner
        )

    assert error.value.code == "count_line_stale"
    assert session.status == StockCountStatus.COUNTING
    assert session.document_id is None
    assert _move_count(db, fixture) == before, "a refused count posted moves anyway"
    _both_invariants(db, fixture)

    # And the way out: re-snapshot, recount, process.
    count_service.resnapshot_line(
        db, fixture.company_id, session.id, line.id, actor=fixture.owner
    )
    fresh = location_position(
        db, fixture.company_id, fixture.item.id, fixture.main.id
    ).quantity
    count_service.enter_count(
        db,
        fixture.company_id,
        session.id,
        line.id,
        quantity=fresh,
        actor=fixture.owner,
    )
    session, document, _ = count_service.process_session(
        db, fixture.company_id, session.id, actor=fixture.owner
    )
    assert session.status == StockCountStatus.COMPLETED
    assert document is None, "a recount that agrees with the books has nothing to post"
    _both_invariants(db, fixture)


@pytest.mark.slow
@given(on_hand=QUANTITIES, unit_cost=COSTS, excess=QUANTITIES)
def test_transfer_now_under_block_with_too_little_stock_posts_nothing(
    db: Session, on_hand: Decimal, unit_cost: Decimal, excess: Decimal
) -> None:
    """Property 3. Refused whole: no moves, no transfer header, nothing in transit, and the
    source still holding everything it held."""
    fixture = fresh_stock(db, f"blk-{next(_EXAMPLE)}")
    fixture.inventory.settings.negative_stock_policy = NegativeStockPolicy.BLOCK
    db.flush()
    receive(db, fixture, quantity=on_hand, unit_cost=unit_cost, on=MARCH)
    before_moves = _move_count(db, fixture)
    before_value = _total_value(db, fixture)

    with pytest.raises(LedgerStateError) as error:
        transfer_service.post_transfer(
            db,
            fixture.company_id,
            _transfer_input(fixture, on_hand + excess),
            receive_now=True,
            actor=fixture.owner,
        )

    assert error.value.code == "insufficient_stock"
    assert _move_count(db, fixture) == before_moves
    assert _total_value(db, fixture) == before_value
    assert transfer_service.list_transfers(db, fixture.company_id)[0] == []
    assert location_position(
        db, fixture.company_id, fixture.item.id, fixture.transit.id
    ) == stock_service.LocationState()
    assert location_position(
        db, fixture.company_id, fixture.item.id, fixture.main.id
    ).quantity == on_hand
    _both_invariants(db, fixture)


def test_a_fixed_run_of_transfers_and_counts_leaves_a_ledger_that_verifies(
    db: Session, stock: Stock
) -> None:
    """One deterministic script alongside the generators, as step 2 keeps: something for
    `pytest -k` to reproduce against, and a run that does not depend on what was drawn.

    It also covers the combination the generators keep apart — a count of a warehouse whose
    stock arrived by transfer, processed while another transfer is still on the road.
    """
    receive(db, stock, quantity=Decimal(100), unit_cost=Decimal("12.5"), on=MARCH)
    transfer_service.post_transfer(
        db,
        stock.company_id,
        _transfer_input(stock, Decimal(40)),
        receive_now=True,
        actor=stock.owner,
    )
    _both_invariants(db, stock)

    on_the_road, _ = transfer_service.post_transfer(
        db,
        stock.company_id,
        _transfer_input(stock, Decimal(10)),
        receive_now=False,
        actor=stock.owner,
    )
    _both_invariants(db, stock)

    session = count_service.open_session(
        db,
        stock.company_id,
        count_service.CountSessionInput(
            warehouse_id=stock.depot.id,
            count_date=MARCH,
            description="depot count while stock is on the road",
        ),
        actor=stock.owner,
    )
    line = count_service.lines_of(db, stock.company_id, session.id)[0]
    assert line.system_quantity == Decimal(40)
    count_service.enter_count(
        db, stock.company_id, session.id, line.id, quantity=Decimal(38), actor=stock.owner
    )
    session, document, _ = count_service.process_session(
        db, stock.company_id, session.id, actor=stock.owner
    )

    assert document is not None
    assert session.status == StockCountStatus.COMPLETED
    _both_invariants(db, stock)

    transfer_service.receive_transfer(
        db, stock.company_id, on_the_road.id, on_date=MARCH, actor=stock.owner
    )
    _both_invariants(db, stock)

    reversal = documents_service.reverse_document(
        db,
        stock.company_id,
        document.id,
        on_date=MARCH,
        reason="recounted",
        actor=stock.owner,
    )
    assert reversal.reverses_document_id == document.id
    assert location_position(
        db, stock.company_id, stock.item.id, stock.depot.id
    ).quantity == Decimal(50)
    _both_invariants(db, stock)

    # And the transfer that arrived while all this was going on is reversed too — both legs,
    # in reverse posting order, with the count's variance already posted against the stock it
    # is taking back (decision 11).
    transfer_service.reverse_transfer(
        db, stock.company_id, on_the_road.id, reason="wrong depot", actor=stock.owner
    )
    assert on_the_road.status == StockTransferStatus.REVERSED
    assert location_position(
        db, stock.company_id, stock.item.id, stock.depot.id
    ).quantity == Decimal(40)
    _both_invariants(db, stock)
