"""P5 step 4 — warehouse transfers (decision 6).

What is under test here is the *document* over `stock.transfer_stock`: that a transfer is two
postings and one header, that stock which has left and not arrived is a real position in the
in-transit warehouse and real value on the in-transit account, that the destination takes
exactly the value the source gave up however the average moves in between, that each leg
carries the branch of its own physical warehouse, and that the in-transit warehouse cannot be
keyed by hand.

Every test that moves stock asserts `assert_stock_invariants` and `assert_ledger_invariants`
afterwards — the standing contract since step 2.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.inventory import stock as stock_service
from app.inventory import transfers as transfer_service
from app.kernel.errors import LedgerStateError, PostingError
from app.models.fiscal import PeriodStatus
from app.models.inventory import (
    NegativeStockPolicy,
    StockMove,
    StockTransferStatus,
    Uom,
)
from app.models.journal import JournalEntry, JournalLine
from tests.inventory.conftest import Stock, receive, set_policy
from tests.inventory.invariants import assert_stock_invariants, location_position
from tests.kernel.conftest import YEAR
from tests.kernel.invariants import assert_ledger_invariants

MARCH = date(YEAR, 3, 10)
HUNDRED = Decimal(100)


def _move_count(db: Session, fixture: Stock) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(StockMove)
            .where(StockMove.company_id == fixture.company_id)
        )
    )


def _both_invariants(db: Session, fixture: Stock) -> None:
    assert_stock_invariants(db, fixture.company_id)
    assert_ledger_invariants(db, fixture.company_id)


def _input(
    fixture: Stock,
    *,
    quantity: Decimal = Decimal(4),
    source=None,
    destination=None,
    on: date = MARCH,
    uom_id: int | None = None,
    lines=None,
) -> transfer_service.TransferInput:
    return transfer_service.TransferInput(
        transfer_date=on,
        description="Main to Depot",
        from_warehouse_id=(source or fixture.main).id,
        to_warehouse_id=(destination or fixture.depot).id,
        lines=lines
        or (
            transfer_service.TransferLineInput(
                item_id=fixture.item.id, quantity=quantity, uom_id=uom_id
            ),
        ),
    )


def _transfer(
    db: Session, fixture: Stock, *, receive_now: bool = True, **kwargs
) -> tuple:
    return transfer_service.post_transfer(
        db,
        fixture.company_id,
        _input(fixture, **kwargs),
        receive_now=receive_now,
        actor=fixture.owner,
    )


# --- The two legs -----------------------------------------------------------------------------


def test_transfer_now_moves_the_stock_and_leaves_nothing_in_transit(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)

    transfer, replayed = _transfer(db, stock)

    assert replayed is False
    assert transfer.status == StockTransferStatus.COMPLETED
    assert transfer.received_date == MARCH
    main = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    depot = location_position(db, stock.company_id, stock.item.id, stock.depot.id)
    transit = location_position(db, stock.company_id, stock.item.id, stock.transit.id)
    assert (main.quantity, main.value) == (Decimal(6), Decimal(600))
    assert (depot.quantity, depot.value) == (Decimal(4), Decimal(400))
    assert (transit.quantity, transit.value) == (Decimal(0), Decimal(0))
    _both_invariants(db, stock)


def test_a_dispatch_leaves_the_stock_in_transit_until_it_is_received(
    db: Session, stock: Stock
) -> None:
    """Decision 6: a dispatched, unreceived transfer is stock in transit on the valuation
    report and value on the in-transit account — and the invariant covers it."""
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)

    transfer, _ = _transfer(db, stock, receive_now=False)

    assert transfer.status == StockTransferStatus.IN_TRANSIT
    assert transfer.receive_entry_id is None
    transit = location_position(db, stock.company_id, stock.item.id, stock.transit.id)
    assert (transit.quantity, transit.value) == (Decimal(4), Decimal(400))
    assert stock_service.inventory_account_balance(
        db,
        stock.company_id,
        stock.inventory.settings.inventory_in_transit_account_id,
        as_of=MARCH,
    ) == Decimal(400)
    _both_invariants(db, stock)

    transfer_service.receive_transfer(
        db, stock.company_id, transfer.id, on_date=MARCH, actor=stock.owner
    )

    assert transfer.status == StockTransferStatus.COMPLETED
    assert location_position(
        db, stock.company_id, stock.item.id, stock.transit.id
    ).value == Decimal(0)
    assert location_position(
        db, stock.company_id, stock.item.id, stock.depot.id
    ).value == Decimal(400)
    _both_invariants(db, stock)


def test_each_leg_is_its_own_entry_and_the_header_takes_the_dispatch_number(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)

    transfer, _ = _transfer(db, stock)

    assert transfer.dispatch_entry_id != transfer.receive_entry_id
    dispatch = db.get(JournalEntry, transfer.dispatch_entry_id)
    arrival = db.get(JournalEntry, transfer.receive_entry_id)
    assert transfer.number == dispatch.number
    assert dispatch.number != arrival.number
    # Both numbers come out of the same gapless `TRF-` run, so nothing in it is skipped.
    assert dispatch.doc_type == arrival.doc_type == "INTR"


def test_each_leg_carries_the_branch_of_its_own_physical_warehouse(
    db: Session, stock: Stock
) -> None:
    """The reason the legs are separate postings at all: Depot is in Musanze and Main is not,
    so a single entry spanning both would have to move value between branches with no line
    saying so."""
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)

    transfer, _ = _transfer(db, stock)

    def branch_of(entry_id: int, account_id: int) -> set[int]:
        return {
            line.branch_id
            for line in db.scalars(
                select(JournalLine).where(
                    JournalLine.entry_id == entry_id,
                    JournalLine.gl_account_id == account_id,
                )
            )
        }

    inventory_account = stock.inventory.settings.inventory_account_id
    transit_account = stock.inventory.settings.inventory_in_transit_account_id
    # The dispatch takes stock off Main, in Main's branch, and parks it in transit, which sits
    # in the branch the in-transit warehouse belongs to.
    assert branch_of(transfer.dispatch_entry_id, inventory_account) == {stock.main.branch_id}
    assert branch_of(transfer.dispatch_entry_id, transit_account) == {
        stock.transit.branch_id
    }
    # The arrival lands in Musanze, where Depot is — not smeared into the branch it came from.
    assert branch_of(transfer.receive_entry_id, inventory_account) == {stock.depot.branch_id}
    assert branch_of(transfer.receive_entry_id, transit_account) == {stock.transit.branch_id}
    assert stock.depot.branch_id != stock.main.branch_id, "the fixture's point is two branches"
    _both_invariants(db, stock)


def test_the_destination_takes_the_value_the_source_gave_up(db: Session, stock: Stock) -> None:
    """The frozen value of decision 4, seen from the document: a receipt at a different cost
    lands between the two legs and moves the average, and the arrival is unaffected."""
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    transfer, _ = _transfer(db, stock, receive_now=False)

    receive(db, stock, quantity=Decimal(10), unit_cost=Decimal(500), on=MARCH)
    transfer_service.receive_transfer(
        db, stock.company_id, transfer.id, on_date=MARCH, actor=stock.owner
    )

    depot = location_position(db, stock.company_id, stock.item.id, stock.depot.id)
    assert (depot.quantity, depot.value) == (Decimal(4), Decimal(400)), (
        "the arrival was re-costed at the new average instead of the value dispatched"
    )
    _both_invariants(db, stock)


def test_a_transfer_carries_many_items_in_one_pair_of_entries(
    db: Session, stock: Stock, inventory
) -> None:
    from app.inventory import masters

    second = masters.create_item(
        db,
        stock.company_id,
        masters.ItemInput(
            code="WINE-375",
            name="Rugari Red 375ml",
            uom_category_id=inventory.count.id,
            base_uom_id=inventory.each.id,
        ),
        actor=stock.owner,
    )
    db.flush()
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    stock_service.receive_stock(
        db,
        stock.company_id,
        document=stock_service.StockDocument(
            doc_type="INAJ",
            move_date=MARCH,
            description="second item in",
            transaction_type_id=stock.type_id("ADJIN"),
        ),
        lines=[
            stock_service.StockLine(
                item_id=second.id,
                warehouse_id=stock.main.id,
                quantity=Decimal(6),
                unit_cost=Decimal(50),
            )
        ],
        actor=stock.owner,
    )

    transfer, _ = _transfer(
        db,
        stock,
        lines=(
            transfer_service.TransferLineInput(item_id=stock.item.id, quantity=Decimal(4)),
            transfer_service.TransferLineInput(item_id=second.id, quantity=Decimal(2)),
        ),
    )

    lines = transfer_service.lines_of(db, stock.company_id, transfer.id)
    assert len(lines) == 2
    # One entry per leg however many items ride on it (decision 3).
    assert len({line.dispatch_out_move_id for line in lines}) == 2
    dispatch_moves = db.scalars(
        select(StockMove).where(StockMove.journal_entry_id == transfer.dispatch_entry_id)
    ).all()
    assert len(dispatch_moves) == 4, "two items, out and in, in one dispatch entry"
    assert location_position(
        db, stock.company_id, second.id, stock.depot.id
    ).value == Decimal(100)
    _both_invariants(db, stock)


def test_every_line_links_to_the_four_moves_it_became(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)

    transfer, _ = _transfer(db, stock)

    line = transfer_service.lines_of(db, stock.company_id, transfer.id)[0]
    moves = [
        db.get(StockMove, move_id)
        for move_id in (
            line.dispatch_out_move_id,
            line.dispatch_in_move_id,
            line.receive_out_move_id,
            line.receive_in_move_id,
        )
    ]
    assert all(move is not None for move in moves)
    assert [move.warehouse_id for move in moves] == [
        stock.main.id,
        stock.transit.id,
        stock.transit.id,
        stock.depot.id,
    ]
    assert [move.quantity for move in moves] == [
        Decimal(-4),
        Decimal(4),
        Decimal(-4),
        Decimal(4),
    ]


def test_a_quantity_keyed_in_cases_moves_in_eaches(
    db: Session, stock: Stock, inventory
) -> None:
    from app.inventory import masters

    case = masters.create_uom(
        db,
        stock.company_id,
        category_id=inventory.count.id,
        code="CS12",
        name="Case of 12",
        factor_to_base=Decimal(12),
        actor=stock.owner,
    )
    db.flush()
    receive(db, stock, quantity=Decimal(24), unit_cost=HUNDRED, on=MARCH)

    transfer, _ = _transfer(db, stock, quantity=Decimal(1), uom_id=case.id)

    line = transfer_service.lines_of(db, stock.company_id, transfer.id)[0]
    assert (line.quantity, line.uom_id, line.quantity_base) == (
        Decimal(1),
        case.id,
        Decimal(12),
    )
    assert location_position(
        db, stock.company_id, stock.item.id, stock.depot.id
    ).quantity == Decimal(12)
    _both_invariants(db, stock)


# --- Refusals ---------------------------------------------------------------------------------


def test_the_in_transit_warehouse_cannot_be_keyed_on_a_transfer(
    db: Session, stock: Stock
) -> None:
    """Decision 6: it is a system location. Keying it by hand would let someone post half a
    transfer and leave stock nothing can bring out."""
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)

    with pytest.raises(LedgerStateError) as error:
        _transfer(db, stock, destination=stock.transit)

    assert error.value.code == "in_transit_warehouse_not_selectable"


def test_a_transfer_to_the_same_warehouse_is_refused(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)

    with pytest.raises(LedgerStateError) as error:
        _transfer(db, stock, destination=stock.main)

    assert error.value.code == "same_warehouse"


def test_transfer_now_under_block_with_too_little_stock_posts_nothing(
    db: Session, stock: Stock
) -> None:
    """The property step 4 names: refused whole. Not the source emptied and the destination
    left waiting — nothing at all."""
    receive(db, stock, quantity=Decimal(3), unit_cost=HUNDRED, on=MARCH)
    set_policy(db, stock, NegativeStockPolicy.BLOCK)
    before = _move_count(db, stock)

    with pytest.raises(LedgerStateError) as error:
        _transfer(db, stock, quantity=Decimal(10))

    assert error.value.code == "insufficient_stock"
    assert _move_count(db, stock) == before, "a refused transfer left moves behind"
    assert transfer_service.list_transfers(db, stock.company_id)[0] == []
    assert location_position(
        db, stock.company_id, stock.item.id, stock.transit.id
    ).quantity == Decimal(0)


def test_a_second_line_that_fails_takes_the_whole_transfer_with_it(
    db: Session, stock: Stock, inventory
) -> None:
    from app.inventory import masters

    second = masters.create_item(
        db,
        stock.company_id,
        masters.ItemInput(
            code="WINE-375",
            name="Rugari Red 375ml",
            uom_category_id=inventory.count.id,
            base_uom_id=inventory.each.id,
        ),
        actor=stock.owner,
    )
    db.flush()
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    set_policy(db, stock, NegativeStockPolicy.BLOCK)

    with pytest.raises(LedgerStateError) as error:
        _transfer(
            db,
            stock,
            lines=(
                transfer_service.TransferLineInput(item_id=stock.item.id, quantity=Decimal(4)),
                transfer_service.TransferLineInput(item_id=second.id, quantity=Decimal(1)),
            ),
        )

    assert error.value.code == "insufficient_stock"
    assert "lines.1.quantity" in error.value.field_errors, (
        "the refusal has to say which line was wrong or the grid has nowhere to show it"
    )
    assert location_position(
        db, stock.company_id, stock.item.id, stock.main.id
    ).quantity == Decimal(10)


def test_a_transfer_dated_into_a_closed_period_is_refused_before_any_move(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    period = stock.inventory.ledger.periods[MARCH.month - 1]
    period.status = PeriodStatus.CLOSED
    db.flush()
    before = _move_count(db, stock)

    with pytest.raises((LedgerStateError, PostingError)) as error:
        _transfer(db, stock)

    assert error.value.code in ("period_closed", "period_not_open")
    assert _move_count(db, stock) == before


def test_a_received_transfer_cannot_be_received_twice(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    transfer, _ = _transfer(db, stock)

    with pytest.raises(LedgerStateError) as error:
        transfer_service.receive_transfer(
            db, stock.company_id, transfer.id, actor=stock.owner
        )

    assert error.value.code == "transfer_already_received"


def test_stock_cannot_arrive_before_it_left(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    transfer, _ = _transfer(db, stock, receive_now=False)

    with pytest.raises(LedgerStateError) as error:
        transfer_service.receive_transfer(
            db,
            stock.company_id,
            transfer.id,
            on_date=MARCH - timedelta(days=1),
            actor=stock.owner,
        )

    assert error.value.code == "receive_before_dispatch"


# --- Cancellation -----------------------------------------------------------------------------


def test_cancelling_an_unreceived_transfer_brings_the_stock_home(
    db: Session, stock: Stock
) -> None:
    """The only way out of the in-transit warehouse other than arriving. Without it a
    mis-keyed dispatch strands both the quantity and its value there for good."""
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    transfer, _ = _transfer(db, stock, receive_now=False)

    transfer_service.cancel_transfer(
        db, stock.company_id, transfer.id, reason="loaded the wrong pallet", actor=stock.owner
    )

    assert transfer.status == StockTransferStatus.CANCELLED
    assert transfer.cancellation_entry_id is not None
    main = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    transit = location_position(db, stock.company_id, stock.item.id, stock.transit.id)
    assert (main.quantity, main.value) == (Decimal(10), Decimal(1000))
    assert (transit.quantity, transit.value) == (Decimal(0), Decimal(0))
    _both_invariants(db, stock)


def test_a_received_transfer_cannot_be_cancelled(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    transfer, _ = _transfer(db, stock)

    with pytest.raises(LedgerStateError) as error:
        transfer_service.cancel_transfer(
            db, stock.company_id, transfer.id, reason="changed my mind", actor=stock.owner
        )

    assert error.value.code == "transfer_already_received"


def test_a_cancelled_transfer_cannot_then_be_received(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    transfer, _ = _transfer(db, stock, receive_now=False)
    transfer_service.cancel_transfer(
        db, stock.company_id, transfer.id, reason="never left", actor=stock.owner
    )

    with pytest.raises(LedgerStateError) as error:
        transfer_service.receive_transfer(
            db, stock.company_id, transfer.id, actor=stock.owner
        )

    assert error.value.code == "transfer_cancelled"


# --- Valueless stock --------------------------------------------------------------------------


def test_a_transfer_of_stock_carried_at_nothing_moves_without_an_entry(
    db: Session, stock: Stock
) -> None:
    """Decision 1: quantity moves whether or not value does. The transfer still gets a number,
    claimed from the same gapless run, because there is no entry to take one from."""
    receive(db, stock, quantity=Decimal(10), unit_cost=Decimal(0), on=MARCH)

    transfer, _ = _transfer(db, stock)

    assert transfer.dispatch_entry_id is None
    assert transfer.receive_entry_id is None
    assert transfer.number.startswith("TRF-")
    assert location_position(
        db, stock.company_id, stock.item.id, stock.depot.id
    ).quantity == Decimal(4)
    _both_invariants(db, stock)


def test_a_valueless_dispatch_can_still_be_cancelled(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=Decimal(0), on=MARCH)
    transfer, _ = _transfer(db, stock, receive_now=False)

    transfer_service.cancel_transfer(
        db, stock.company_id, transfer.id, reason="valueless mistake", actor=stock.owner
    )

    assert transfer.status == StockTransferStatus.CANCELLED
    assert transfer.cancellation_entry_id is None
    assert location_position(
        db, stock.company_id, stock.item.id, stock.main.id
    ).quantity == Decimal(10)
    _both_invariants(db, stock)


# --- Idempotency ------------------------------------------------------------------------------


def test_replaying_the_dispatch_key_returns_the_same_transfer(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    data = _input(stock)

    first, first_replay = transfer_service.post_transfer(
        db, stock.company_id, data, actor=stock.owner, idempotency_key="trf-1"
    )
    second, second_replay = transfer_service.post_transfer(
        db, stock.company_id, data, actor=stock.owner, idempotency_key="trf-1"
    )

    assert first_replay is False
    assert second_replay is True
    assert second.id == first.id
    assert location_position(
        db, stock.company_id, stock.item.id, stock.depot.id
    ).quantity == Decimal(4), "the replay posted the transfer a second time"
    _both_invariants(db, stock)


def test_a_transfer_of_a_unit_from_another_category_is_refused(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    kilogram = db.scalars(
        select(Uom).where(Uom.company_id == stock.company_id, Uom.code == "KG")
    ).one()

    with pytest.raises(LedgerStateError) as error:
        _transfer(db, stock, uom_id=kilogram.id)

    assert error.value.code == "uom_category_mismatch"
