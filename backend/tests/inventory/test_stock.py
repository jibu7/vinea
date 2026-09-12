"""P5 step 2 — the stock ledger, the costing rules and the posting contract.

Each test here pins one sentence of decisions 1–6 and 11, and every one of them ends with the
whole invariant suite: `assert_stock_invariants` plus `assert_ledger_invariants`. Checking the
number a rule produces and stopping there is how a rule that is right on its own and wrong in
company gets shipped — the costing tape in step 5 exists for the same reason at document
level.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.inventory import stock as stock_service
from app.kernel.errors import (
    SQLSTATE_STOCK_IMMUTABLE,
    SQLSTATE_STOCK_WRITER,
    LedgerStateError,
    PostingError,
    kernel_sqlstate,
)
from app.kernel.money import round_amount
from app.kernel.sequences import DocType
from app.models.fiscal import PeriodStatus
from app.models.inventory import (
    INVENTORY_MODULE,
    ItemCostState,
    ItemType,
    NegativeStockPolicy,
    StockMove,
)
from app.models.journal import JournalLine
from tests.inventory.conftest import (
    Stock,
    document,
    issue,
    receive,
    set_policy,
    transfer_now,
)
from tests.inventory.invariants import assert_stock_invariants, location_position
from tests.kernel.invariants import assert_ledger_invariants
from tests.subledger.conftest import MARCH

HUNDRED = Decimal(100)
ZERO_D = Decimal(0)


def _assert_everything(db: Session, fixture: Stock) -> None:
    assert_stock_invariants(db, fixture.company_id)
    assert_ledger_invariants(db, fixture.company_id)


def _average(db: Session, fixture: Stock) -> Decimal:
    return db.scalars(
        select(ItemCostState).where(
            ItemCostState.company_id == fixture.company_id,
            ItemCostState.item_id == fixture.item.id,
        )
    ).one().average_cost


# --- Decision 1: the move is the truth, and the journal line is its other half ---------------


def test_a_receipt_writes_one_move_and_one_balanced_entry(db: Session, stock: Stock) -> None:
    posting = receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)

    move = posting.moves[0]
    assert move.quantity == Decimal(10)
    assert move.value == Decimal(1000)
    assert move.unit_cost == HUNDRED
    assert move.warehouse_id == stock.main.id
    assert move.period_id == posting.entry.period_id
    # The move and the line are the same fact seen from two sides.
    line = db.get(JournalLine, move.journal_line_id)
    assert line.base_amount == move.value
    assert line.item_id == stock.item.id
    assert line.gl_account_id == stock.inventory.settings.inventory_account_id
    assert posting.entry.module == INVENTORY_MODULE
    assert posting.entry.number.startswith("ADJ-")
    _assert_everything(db, stock)


def test_the_caches_are_what_the_moves_say(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    receive(db, stock, quantity=Decimal(10), unit_cost=Decimal(121), on=MARCH)

    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert (position.quantity, position.value) == (Decimal(20), Decimal(2210))
    assert _average(db, stock) == Decimal("110.5")
    assert not stock_service.verify_stock_balances(db, stock.company_id)
    _assert_everything(db, stock)


def test_a_source_document_links_both_ways(db: Session, stock: Stock) -> None:
    """P6 wires its own documents to these primitives; the link it will follow is this one."""
    posting = stock_service.receive_stock(
        db,
        stock.company_id,
        document=document(
            DocType.INV_ADJUSTMENT,
            MARCH,
            transaction_type_id=stock.type_id("ADJIN"),
            source_doc_type="inventory_adjustment",
            source_doc_id=4242,
        ),
        lines=[
            stock_service.StockLine(
                item_id=stock.item.id,
                warehouse_id=stock.main.id,
                quantity=Decimal(5),
                unit_cost=HUNDRED,
                source_line_id=7,
            )
        ],
        actor=stock.owner,
    )

    assert posting.entry.source_doc_type == "inventory_adjustment"
    assert posting.entry.source_doc_id == 4242
    move = posting.moves[0]
    assert (move.source_doc_type, move.source_doc_id, move.source_line_id) == (
        "inventory_adjustment",
        4242,
        7,
    )
    _assert_everything(db, stock)


# --- Decision 4: the costing rules ------------------------------------------------------------


def test_an_issue_is_costed_at_the_average_and_rounded_half_up(db: Session, stock: Stock) -> None:
    """RWF has no minor unit, so 7 × 110.5 = 773.5 is the rounding rule's own test case."""
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    receive(db, stock, quantity=Decimal(10), unit_cost=Decimal(121), on=MARCH)

    posting = issue(db, stock, quantity=Decimal(7), on=MARCH)

    assert posting.moves[0].value == Decimal(-774)
    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert (position.quantity, position.value) == (Decimal(13), Decimal(1436))
    assert _average(db, stock) == Decimal("110.461538")
    _assert_everything(db, stock)


def test_a_backdated_receipt_moves_the_average_forward_and_restates_nothing(
    db: Session, stock: Stock
) -> None:
    """Decision 4's hardest sentence, and the one the DoD tape tests: posting order beats date
    order. Immutability and closed periods outrank the calendar."""
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    receive(db, stock, quantity=Decimal(10), unit_cost=Decimal(121), on=MARCH)
    issued = issue(db, stock, quantity=Decimal(7), on=MARCH)
    assert issued.moves[0].value == Decimal(-774)

    receive(
        db,
        stock,
        quantity=Decimal(5),
        unit_cost=Decimal(130),
        on=MARCH - timedelta(days=5),
    )

    db.refresh(issued.moves[0])
    assert issued.moves[0].value == Decimal(-774), "an earlier-posted issue was restated"
    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert (position.quantity, position.value) == (Decimal(18), Decimal(2086))
    assert _average(db, stock) == Decimal("115.888889")
    _assert_everything(db, stock)


def test_an_issue_that_empties_a_location_takes_everything_it_had(
    db: Session, stock: Stock
) -> None:
    """The flush (decision 4). 12 × 115.888889 is 1 390.666668, which rounds to 1 391 — but
    the rule is not "round and hope": the location is worth 1 391 and the move takes 1 391, so
    a location at zero quantity is at zero value by construction rather than by luck.
    """
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    receive(db, stock, quantity=Decimal(10), unit_cost=Decimal(121), on=MARCH)
    issue(db, stock, quantity=Decimal(7), on=MARCH)
    receive(db, stock, quantity=Decimal(5), unit_cost=Decimal(130), on=MARCH - timedelta(days=5))
    transfer_now(
        db, stock, quantity=Decimal(6), source=stock.main, destination=stock.depot, on=MARCH
    )

    posting = issue(db, stock, quantity=Decimal(12), on=MARCH)

    assert posting.moves[0].value == Decimal(-1391)
    main = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert (main.quantity, main.value) == (Decimal(0), Decimal(0))
    depot = location_position(db, stock.company_id, stock.item.id, stock.depot.id)
    assert (depot.quantity, depot.value) == (Decimal(6), Decimal(695))
    assert _average(db, stock) == Decimal("115.833333")
    _assert_everything(db, stock)


def test_a_large_quantity_does_not_drift_the_way_a_six_decimal_average_would(
    db: Session, stock: Stock
) -> None:
    """The shape the flush rule exists for: an average that cannot be written exactly in six
    decimals, times a quantity big enough for the error to be visible in whole francs.

    1 000 000 units bought for 1 000 001 francs average 1.000001 exactly; buy a second lot at
    a price whose average recurs and the six-decimal average times the quantity is no longer
    the value. Emptying the location must still leave nothing behind.
    """
    receive(db, stock, quantity=Decimal(1_000_000), unit_cost=Decimal(1), on=MARCH)
    receive(db, stock, quantity=Decimal(2_000_000), unit_cost=Decimal("1.0000005"), on=MARCH)
    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    average = _average(db, stock)
    assert average * position.quantity != position.value, (
        "pick quantities where the six-decimal average does not reproduce the value"
    )

    issue(db, stock, quantity=Decimal(1_500_000), on=MARCH)
    before_flush = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    average_then = _average(db, stock)

    posting = issue(db, stock, quantity=Decimal(1_500_000), on=MARCH)

    emptied = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert (emptied.quantity, emptied.value) == (Decimal(0), Decimal(0))
    # The flush took what the location held, and that is *not* what the average would have
    # charged — the difference is the drift the rule exists to absorb.
    assert posting.moves[0].value == -before_flush.value
    assert posting.moves[0].value != -round_amount(Decimal(1_500_000) * average_then, 0)
    _assert_everything(db, stock)


def test_a_revaluation_moves_value_with_no_quantity(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)

    posting = stock_service.revalue_stock(
        db,
        stock.company_id,
        document=document(
            DocType.INV_ADJUSTMENT, MARCH, transaction_type_id=stock.type_id("REVAL")
        ),
        lines=[
            stock_service.StockLine(
                item_id=stock.item.id, warehouse_id=stock.main.id, value=Decimal(-200)
            )
        ],
        actor=stock.owner,
    )

    move = posting.moves[0]
    assert (move.quantity, move.value, move.unit_cost) == (Decimal(0), Decimal(-200), None)
    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert (position.quantity, position.value) == (Decimal(10), Decimal(800))
    assert _average(db, stock) == Decimal(80)
    _assert_everything(db, stock)


# --- Decision 5: the negative-stock policy -----------------------------------------------------


def test_block_refuses_an_issue_that_would_go_below_zero(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=Decimal(5), unit_cost=HUNDRED, on=MARCH)

    with pytest.raises(LedgerStateError) as excinfo:
        issue(db, stock, quantity=Decimal(6), on=MARCH)

    assert excinfo.value.code == "insufficient_stock"
    # The message lands on the line that caused it, so a refused batch can say which row.
    assert list(excinfo.value.field_errors) == ["lines.0.quantity"]
    db.rollback()


def test_block_is_evaluated_per_location_not_on_the_company_total(
    db: Session, stock: Stock
) -> None:
    """Stock at the Depot is no help to a picker standing in Main (decision 5)."""
    receive(db, stock, quantity=Decimal(50), unit_cost=HUNDRED, warehouse=stock.depot, on=MARCH)
    receive(db, stock, quantity=Decimal(2), unit_cost=HUNDRED, on=MARCH)

    with pytest.raises(LedgerStateError) as excinfo:
        issue(db, stock, quantity=Decimal(3), warehouse=stock.main, on=MARCH)

    assert excinfo.value.code == "insufficient_stock"
    db.rollback()


def test_allow_posts_at_the_last_positive_average_and_flags_the_move(
    db: Session, stock: Stock
) -> None:
    receive(
        db,
        stock,
        quantity=Decimal(5),
        unit_cost=Decimal("115.8"),
        warehouse=stock.depot,
        on=MARCH,
    )
    set_policy(db, stock, NegativeStockPolicy.ALLOW)

    posting = issue(db, stock, quantity=Decimal(6), warehouse=stock.depot, on=MARCH)

    move = posting.moves[0]
    assert move.cost_provisional is True
    assert move.value == Decimal(-695)  # 6 × 115.8 = 694.8, half-up
    depot = location_position(db, stock.company_id, stock.item.id, stock.depot.id)
    assert (depot.quantity, depot.value) == (Decimal(-1), Decimal(-116))
    # The average stays where it was: with no positive quantity, the last positive average is
    # what an issue costs at, and nothing has restated it.
    assert _average(db, stock) == Decimal("115.8")
    _assert_everything(db, stock)


def test_a_later_receipt_does_not_correct_a_provisional_issue(db: Session, stock: Stock) -> None:
    """Decision 5 in one assertion: the flag is the review trail, not a promise."""
    receive(
        db,
        stock,
        quantity=Decimal(5),
        unit_cost=Decimal("115.8"),
        warehouse=stock.depot,
        on=MARCH,
    )
    set_policy(db, stock, NegativeStockPolicy.ALLOW)
    provisional = issue(db, stock, quantity=Decimal(6), warehouse=stock.depot, on=MARCH)

    receive(
        db, stock, quantity=Decimal(3), unit_cost=Decimal(150), warehouse=stock.depot, on=MARCH
    )

    db.refresh(provisional.moves[0])
    assert provisional.moves[0].value == Decimal(-695)
    assert provisional.moves[0].cost_provisional is True
    # The issue is untouched. The *location* is settled, because the receipt that covered the
    # deficit is what finally priced the units it was guessing about — two units left, worth
    # what they cost. `test_deficit_cover.py` is where that rule is pinned.
    depot = location_position(db, stock.company_id, stock.item.id, stock.depot.id)
    assert (depot.quantity, depot.value) == (Decimal(2), Decimal(300))
    assert _average(db, stock) == Decimal(150)
    _assert_everything(db, stock)


def test_a_receipt_landing_exactly_on_a_negative_balance_expels_the_residue(
    db: Session, stock: Stock
) -> None:
    """The one case the plan's rules leave open, and the reason `residue_move` exists.

    Under `allow` a provisional issue takes out value the location did not have. A receipt
    that lands the quantity exactly back on zero cannot land the value there too — the
    receipt is worth what it cost. So the leftover is expelled as a move of its own: zero
    quantity, the residue, against the inventory adjustment account. A location at zero
    quantity holds zero value, always, and the correction is in the ledger where it can be
    seen rather than hidden in a silently repriced receipt.
    """
    receive(
        db,
        stock,
        quantity=Decimal(5),
        unit_cost=Decimal("115.8"),
        warehouse=stock.depot,
        on=MARCH,
    )
    set_policy(db, stock, NegativeStockPolicy.ALLOW)
    issue(db, stock, quantity=Decimal(6), warehouse=stock.depot, on=MARCH)

    posting = receive(
        db, stock, quantity=Decimal(1), unit_cost=Decimal(150), warehouse=stock.depot, on=MARCH
    )

    assert [(m.quantity, m.value) for m in posting.moves] == [
        (Decimal(1), Decimal(150)),
        (Decimal(0), Decimal(-34)),
    ]
    # The residue is flagged like the issue that caused it, so the provisional filter shows
    # the guess and its consequence together rather than only half of the story.
    assert posting.moves[1].cost_provisional is True
    assert posting.moves[1].journal_line_id is not None
    depot = location_position(db, stock.company_id, stock.item.id, stock.depot.id)
    assert (depot.quantity, depot.value) == (Decimal(0), Decimal(0))
    _assert_everything(db, stock)


# --- Decision 6: transfers pass through the in-transit warehouse -------------------------------


def test_a_dispatch_leaves_the_value_in_transit_and_the_receive_takes_it_out(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)

    dispatch = stock_service.transfer_stock(
        db,
        stock.company_id,
        document=document(
            DocType.INV_TRANSFER, MARCH, transaction_type_id=stock.type_id("TRF")
        ),
        lines=[stock_service.TransferLine(item_id=stock.item.id, quantity=Decimal(4))],
        from_warehouse_id=stock.main.id,
        to_warehouse_id=stock.transit.id,
        actor=stock.owner,
    )

    # Two moves, two lines, no contra: what one location gives up the other takes.
    assert [(m.warehouse_id, m.value) for m in dispatch.moves] == [
        (stock.main.id, Decimal(-400)),
        (stock.transit.id, Decimal(400)),
    ]
    assert len(dispatch.entry.lines) == 2
    transit = location_position(db, stock.company_id, stock.item.id, stock.transit.id)
    assert (transit.quantity, transit.value) == (Decimal(4), Decimal(400))
    # A dispatched, unreceived transfer is stock in transit — and value on the in-transit
    # account, which is what the invariant covers.
    assert stock_service.inventory_account_balance(
        db,
        stock.company_id,
        stock.inventory.settings.inventory_in_transit_account_id,
        as_of=MARCH,
    ) == Decimal(400)
    _assert_everything(db, stock)

    stock_service.transfer_stock(
        db,
        stock.company_id,
        document=document(
            DocType.INV_TRANSFER, MARCH, transaction_type_id=stock.type_id("TRF")
        ),
        lines=[stock_service.TransferLine(item_id=stock.item.id, quantity=Decimal(4))],
        from_warehouse_id=stock.transit.id,
        to_warehouse_id=stock.depot.id,
        actor=stock.owner,
    )

    assert location_position(db, stock.company_id, stock.item.id, stock.transit.id).value == ZERO_D
    depot = location_position(db, stock.company_id, stock.item.id, stock.depot.id)
    assert (depot.quantity, depot.value) == (Decimal(4), Decimal(400))
    _assert_everything(db, stock)


def test_a_transfer_changes_no_total_and_no_average(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    receive(db, stock, quantity=Decimal(10), unit_cost=Decimal(121), on=MARCH)
    before = _average(db, stock)

    transfer_now(
        db, stock, quantity=Decimal(6), source=stock.main, destination=stock.depot, on=MARCH
    )

    assert _average(db, stock) == before
    main = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    depot = location_position(db, stock.company_id, stock.item.id, stock.depot.id)
    assert main.value + depot.value == Decimal(2210)
    assert depot.value == Decimal(663)  # 6 × 110.5, frozen at dispatch
    _assert_everything(db, stock)


def test_a_transfer_leg_carries_the_branch_of_its_own_warehouse(
    db: Session, stock: Stock
) -> None:
    """Decision 6: each leg posts with the branch of its physical warehouse, which is what
    keeps the per-branch balances right when the two warehouses sit in different branches."""
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)

    _, arrival = transfer_now(
        db, stock, quantity=Decimal(4), source=stock.main, destination=stock.depot, on=MARCH
    )

    branches = {line.gl_account_id: line.branch_id for line in arrival.entry.lines}
    assert branches[stock.inventory.settings.inventory_account_id] == stock.depot.branch_id
    assert (
        branches[stock.inventory.settings.inventory_in_transit_account_id]
        == stock.transit.branch_id
    )
    _assert_everything(db, stock)


# --- Decision 11: reversal ----------------------------------------------------------------------


def test_a_reversal_mirrors_the_moves_at_their_original_values(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    adjustment = receive(db, stock, quantity=Decimal(10), unit_cost=Decimal(121), on=MARCH)

    reversal = stock_service.reverse_stock_posting(
        db,
        stock.company_id,
        entry_id=adjustment.entry.id,
        on_date=MARCH,
        reason="keyed twice",
        actor=stock.owner,
    )

    assert reversal.moves[0].quantity == Decimal(-10)
    assert reversal.moves[0].value == Decimal(-1210)
    assert reversal.moves[0].reverses_move_id == adjustment.moves[0].id
    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert (position.quantity, position.value) == (Decimal(10), Decimal(1000))
    assert _average(db, stock) == HUNDRED
    _assert_everything(db, stock)


def test_a_receipt_whose_stock_has_been_issued_cannot_be_reversed_under_block(
    db: Session, stock: Stock
) -> None:
    """Decision 11's own example. The reversal would take the location below zero, and under
    `block` that is not a thing the ledger may be asked to hold."""
    first = receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    issue(db, stock, quantity=Decimal(8), on=MARCH)

    with pytest.raises(LedgerStateError) as excinfo:
        stock_service.reverse_stock_posting(
            db,
            stock.company_id,
            entry_id=first.entry.id,
            on_date=MARCH,
            reason="too late",
            actor=stock.owner,
        )

    assert excinfo.value.code == "insufficient_stock"
    db.rollback()


# --- The posting contract ------------------------------------------------------------------------


def test_a_document_in_a_closed_period_is_refused_before_any_move_exists(
    db: Session, stock: Stock
) -> None:
    period = next(p for p in stock.inventory.ledger.periods if p.start_date <= MARCH <= p.end_date)
    period.status = PeriodStatus.CLOSED
    db.flush()

    with pytest.raises(PostingError) as excinfo:
        receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)

    assert excinfo.value.code == "period_not_open"
    assert db.scalar(select(StockMove.id).where(StockMove.company_id == stock.company_id)) is None
    db.rollback()


def test_replaying_an_idempotency_key_returns_the_first_posting(
    db: Session, stock: Stock
) -> None:
    first = receive(
        db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH, idempotency_key="k-1"
    )

    again = receive(
        db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH, idempotency_key="k-1"
    )

    assert again.replayed is True
    assert again.entry.id == first.entry.id
    assert [move.id for move in again.moves] == [move.id for move in first.moves]
    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert position.quantity == Decimal(10), "a replay posted the moves a second time"
    _assert_everything(db, stock)


def test_a_contra_on_an_inventory_account_is_refused(db: Session, stock: Stock) -> None:
    """Both legs on an inventory account would post an entry that moves stock nowhere, and
    the second leg would be an INV line with no move behind it."""
    with pytest.raises(PostingError) as excinfo:
        stock_service.receive_stock(
            db,
            stock.company_id,
            document=document(DocType.INV_ADJUSTMENT, MARCH),
            lines=[
                stock_service.StockLine(
                    item_id=stock.item.id,
                    warehouse_id=stock.main.id,
                    quantity=Decimal(1),
                    unit_cost=HUNDRED,
                    contra_account_id=stock.inventory.settings.inventory_account_id,
                )
            ],
            actor=stock.owner,
        )

    assert excinfo.value.code == "contra_is_inventory_account"
    db.rollback()


def test_a_service_item_has_no_moves(db: Session, stock: Stock) -> None:
    from app.inventory import masters

    service = masters.create_item(
        db,
        stock.company_id,
        masters.ItemInput(
            code="FITTING",
            name="Fitting service",
            uom_category_id=stock.inventory.count.id,
            base_uom_id=stock.inventory.each.id,
            item_type=ItemType.SERVICE,
        ),
        actor=stock.owner,
    )
    db.flush()

    with pytest.raises(LedgerStateError) as excinfo:
        stock_service.receive_stock(
            db,
            stock.company_id,
            document=document(
                DocType.INV_ADJUSTMENT, MARCH, transaction_type_id=stock.type_id("ADJIN")
            ),
            lines=[
                stock_service.StockLine(
                    item_id=service.id,
                    warehouse_id=stock.main.id,
                    quantity=Decimal(1),
                    unit_cost=HUNDRED,
                )
            ],
            actor=stock.owner,
        )

    assert excinfo.value.code == "item_not_stocked"
    db.rollback()


# --- The database's own rules ---------------------------------------------------------------------


def test_the_database_refuses_a_stock_move_written_outside_the_service(
    db: Session, stock: Stock
) -> None:
    """ADR-05, applied to the stock ledger: one writer, enforced by trigger rather than by
    code review — the same guard the journal has had since P2."""
    posting = receive(db, stock, quantity=Decimal(1), unit_cost=HUNDRED, on=MARCH)

    with pytest.raises(DBAPIError) as excinfo:
        db.execute(
            text(
                "INSERT INTO stock_moves (company_id, item_id, warehouse_id, move_date, "
                "period_id, sequence_no, quantity, unit_cost, value, cost_provisional) "
                "VALUES (:cid, :item, :wh, :on, :period, 999999, 1, 1, 0, false)"
            ),
            {
                "cid": stock.company_id,
                "item": stock.item.id,
                "wh": stock.main.id,
                "on": MARCH,
                "period": posting.entry.period_id,
            },
        )

    assert kernel_sqlstate(excinfo.value) == SQLSTATE_STOCK_WRITER
    db.rollback()


def test_the_database_refuses_to_change_a_posted_move(db: Session, stock: Stock) -> None:
    posting = receive(db, stock, quantity=Decimal(1), unit_cost=HUNDRED, on=MARCH)

    with pytest.raises(DBAPIError) as excinfo:
        db.execute(
            text("SELECT set_config('app.stock_service', 'on', true)")
        )
        db.execute(
            text("UPDATE stock_moves SET quantity = 99 WHERE id = :id"),
            {"id": posting.moves[0].id},
        )

    assert kernel_sqlstate(excinfo.value) == SQLSTATE_STOCK_IMMUTABLE
    db.rollback()


def test_a_valued_move_cannot_exist_without_its_journal_line(db: Session, stock: Stock) -> None:
    """The check constraint that makes stock-equals-GL a property of the schema. Written with
    the writer guard deliberately open — the point is the constraint underneath it."""
    posting = receive(db, stock, quantity=Decimal(1), unit_cost=HUNDRED, on=MARCH)

    with pytest.raises(DBAPIError) as excinfo:
        db.execute(text("SELECT set_config('app.stock_service', 'on', true)"))
        db.execute(
            text(
                "INSERT INTO stock_moves (company_id, item_id, warehouse_id, move_date, "
                "period_id, sequence_no, quantity, unit_cost, value, cost_provisional) "
                "VALUES (:cid, :item, :wh, :on, :period, 999999, 1, 1, 500, false)"
            ),
            {
                "cid": stock.company_id,
                "item": stock.item.id,
                "wh": stock.main.id,
                "on": MARCH,
                "period": posting.entry.period_id,
            },
        )

    assert "ck_stock_moves_value_matches_journal_link" in str(excinfo.value)
    db.rollback()


def test_verify_stock_balances_reports_a_cache_that_has_drifted(
    db: Session, stock: Stock
) -> None:
    """The cache is allowed to exist only because this function can prove it — so prove that
    the proof can fail."""
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    assert not stock_service.verify_stock_balances(db, stock.company_id)

    db.execute(text("SELECT set_config('app.stock_service', 'on', true)"))
    db.execute(
        text("UPDATE stock_balances SET quantity = quantity + 1 WHERE company_id = :cid"),
        {"cid": stock.company_id},
    )
    db.execute(text("SELECT set_config('app.stock_service', 'off', true)"))

    drift = stock_service.verify_stock_balances(db, stock.company_id)
    assert drift.balances[0].stored_quantity == Decimal(11)
    assert drift.balances[0].recomputed_quantity == Decimal(10)
    db.rollback()



def test_replaying_a_reversals_key_does_not_reverse_it_twice(
    db: Session, stock: Stock
) -> None:
    """The engine resolves a replayed key to the entry it produced the first time and returns
    it. Without a matching check here the reversing *moves* would be written again against
    that same entry — the caches would move and the ledger would not."""
    adjustment = receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    first = stock_service.reverse_stock_posting(
        db,
        stock.company_id,
        entry_id=adjustment.entry.id,
        on_date=MARCH,
        reason="keyed twice",
        actor=stock.owner,
        idempotency_key="rev-1",
    )

    again = stock_service.reverse_stock_posting(
        db,
        stock.company_id,
        entry_id=adjustment.entry.id,
        on_date=MARCH,
        reason="keyed twice",
        actor=stock.owner,
        idempotency_key="rev-1",
    )

    assert again.replayed is True
    assert again.entry.id == first.entry.id
    assert [move.id for move in again.moves] == [move.id for move in first.moves]
    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert (position.quantity, position.value) == (Decimal(0), Decimal(0))
    _assert_everything(db, stock)


def test_a_reversal_that_empties_a_location_expels_what_it_cannot_take_with_it(
    db: Session, stock: Stock
) -> None:
    """Found by the property test, and the reason a reversal is not always one document.

    A reversal takes a move out at the value it went in at — the only thing it can honestly
    do. But the value *at* that location can have moved since. Here a receipt is revalued
    upward and then reversed: the quantity lands on zero while a franc of value stays behind.
    It cannot be dealt with inside the reversing entry, which is an exact mirror of the entry
    it reverses, so it becomes its own posting against the adjustment account.
    """
    receipt = receive(
        db, stock, quantity=Decimal(1), unit_cost=Decimal(1), warehouse=stock.depot, on=MARCH
    )
    stock_service.revalue_stock(
        db,
        stock.company_id,
        document=document(
            DocType.INV_ADJUSTMENT, MARCH, transaction_type_id=stock.type_id("REVAL")
        ),
        lines=[
            stock_service.StockLine(
                item_id=stock.item.id, warehouse_id=stock.depot.id, value=Decimal(1)
            )
        ],
        actor=stock.owner,
    )
    assert location_position(db, stock.company_id, stock.item.id, stock.depot.id).value == Decimal(
        2
    )

    reversal = stock_service.reverse_stock_posting(
        db,
        stock.company_id,
        entry_id=receipt.entry.id,
        on_date=MARCH,
        reason="keyed against the wrong warehouse",
        actor=stock.owner,
    )

    mirror, residue = reversal.moves
    assert (mirror.quantity, mirror.value) == (Decimal(-1), Decimal(-1))
    assert (residue.quantity, residue.value) == (Decimal(0), Decimal(1) * -1)
    # Two documents, because two things happened.
    assert residue.journal_entry_id != reversal.entry.id
    depot = location_position(db, stock.company_id, stock.item.id, stock.depot.id)
    assert (depot.quantity, depot.value) == (Decimal(0), Decimal(0))
    _assert_everything(db, stock)
