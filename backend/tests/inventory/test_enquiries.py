"""P5 step 5 — the item enquiry.

The enquiry is the screen an operator opens when the valuation report and their instinct
disagree, so what is under test here is mostly *which number it shows*: the position as at a
date rather than the cached position now, a running column that continues across pages, and
the provisional flag decision 5 leaves behind.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.inventory import enquiries
from app.models.inventory import NegativeStockPolicy, StockMove
from tests.inventory.conftest import Stock, issue, receive, set_policy, transfer_now
from tests.subledger.conftest import MARCH

BEFORE = MARCH - timedelta(days=5)
AFTER = MARCH + timedelta(days=5)


def _d(text: str) -> Decimal:
    return Decimal(text)


def test_the_enquiry_shows_the_position_per_warehouse(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    transfer_now(
        db, stock, quantity=_d("4"), source=stock.main, destination=stock.depot, on=MARCH
    )

    enquiry = enquiries.item_enquiry(db, stock.company_id, stock.item.id, as_of=AFTER)

    by_code = {row.warehouse_code: row for row in enquiry.locations}
    assert by_code["MAIN"].quantity == _d("6")
    assert by_code["MAIN"].value == _d("600")
    assert by_code["DEPOT"].quantity == _d("4")
    assert by_code["DEPOT"].value == _d("400")
    assert "TRANSIT" not in by_code, "a transfer that arrived leaves nothing in transit"
    assert enquiry.total_quantity == _d("10")
    assert enquiry.total_value == _d("1000")
    assert enquiry.average_cost == _d("100")
    assert by_code["DEPOT"].branch_id != by_code["MAIN"].branch_id


def test_stock_still_in_transit_is_shown_where_it_actually_is(
    db: Session, stock: Stock
) -> None:
    """Decision 6: a dispatched, unreceived transfer is a real position in a real warehouse,
    not stock that has quietly left the company."""
    from app.inventory import transfers as transfer_service

    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    transfer_service.post_transfer(
        db,
        stock.company_id,
        transfer_service.TransferInput(
            transfer_date=MARCH,
            description="to the depot",
            from_warehouse_id=stock.main.id,
            to_warehouse_id=stock.depot.id,
            lines=[transfer_service.TransferLineInput(item_id=stock.item.id, quantity=_d("4"))],
        ),
        receive_now=False,
        actor=stock.owner,
    )

    enquiry = enquiries.item_enquiry(db, stock.company_id, stock.item.id, as_of=MARCH)

    by_code = {row.warehouse_code: row for row in enquiry.locations}
    assert by_code["TRANSIT"].quantity == _d("4")
    assert by_code["TRANSIT"].value == _d("400")
    assert by_code["TRANSIT"].is_in_transit is True
    assert enquiry.total_quantity == _d("10"), "in transit is still on hand"


def test_as_of_is_a_date_not_the_cache(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=BEFORE)
    receive(db, stock, quantity=_d("10"), unit_cost=_d("140"), on=AFTER)

    early = enquiries.item_enquiry(db, stock.company_id, stock.item.id, as_of=MARCH)
    late = enquiries.item_enquiry(db, stock.company_id, stock.item.id, as_of=AFTER)

    assert (early.total_quantity, early.total_value) == (_d("10"), _d("1000"))
    assert early.average_cost == _d("100"), (
        "the average on the 10th is the one the company had on the 10th, not today's"
    )
    assert (late.total_quantity, late.total_value) == (_d("20"), _d("2400"))
    assert late.average_cost == _d("120")


def test_the_running_column_ends_at_the_position(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=BEFORE)
    receive(db, stock, quantity=_d("10"), unit_cost=_d("121"), on=MARCH)
    issue(db, stock, quantity=_d("7"), on=MARCH)

    enquiry = enquiries.item_enquiry(
        db, stock.company_id, stock.item.id, as_of=AFTER, warehouse_id=stock.main.id
    )

    last = enquiry.moves[-1]
    assert last.running_quantity == _d("13")
    assert last.running_value == _d("1436")
    assert [move.move_date for move in enquiry.moves] == sorted(
        move.move_date for move in enquiry.moves
    ), "the listing is in date order"


def test_a_backdated_move_lands_in_date_order_and_keeps_its_posting_order(
    db: Session, stock: Stock
) -> None:
    """The two orders, visible on one screen. The backdated receipt sorts first because that
    is where the ledger put it; its `sequence_no` is last because that is when it was costed,
    and that is what explains why the issue above it was not restated."""
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    issue(db, stock, quantity=_d("4"), on=MARCH)
    receive(db, stock, quantity=_d("5"), unit_cost=_d("130"), on=BEFORE)

    enquiry = enquiries.item_enquiry(db, stock.company_id, stock.item.id, as_of=AFTER)

    assert [move.move_date for move in enquiry.moves] == [BEFORE, MARCH, MARCH]
    assert enquiry.moves[0].sequence_no > enquiry.moves[1].sequence_no
    assert enquiry.moves[-1].running_quantity == _d("11")
    assert enquiry.moves[-1].running_value == _d("1250")


def test_date_from_brings_a_balance_forward_without_changing_the_position(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=BEFORE)
    receive(db, stock, quantity=_d("5"), unit_cost=_d("120"), on=AFTER)

    windowed = enquiries.item_enquiry(
        db, stock.company_id, stock.item.id, as_of=AFTER, date_from=MARCH
    )

    assert windowed.opening_quantity == _d("10")
    assert windowed.opening_value == _d("1000")
    assert [move.move_date for move in windowed.moves] == [AFTER]
    assert windowed.moves[0].running_quantity == _d("15"), (
        "the running column continues from the opening, it does not restart at the window"
    )
    assert windowed.total_value == _d("1600"), "a narrower window is not a smaller company"


def test_every_move_carries_its_drill_down_keys(db: Session, stock: Stock) -> None:
    posting = receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)

    enquiry = enquiries.item_enquiry(db, stock.company_id, stock.item.id, as_of=MARCH)

    move = enquiry.moves[0]
    assert move.journal_entry_id == posting.entry.id
    assert move.entry_number == posting.entry.number
    assert move.transaction_type_code == "ADJIN"
    assert move.transaction_type_name
    assert move.unit_cost == _d("100")


def test_the_provisional_filter_hides_rows_and_not_the_balance(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    set_policy(db, stock, NegativeStockPolicy.ALLOW)
    issue(db, stock, quantity=_d("12"), on=MARCH)

    everything = enquiries.item_enquiry(db, stock.company_id, stock.item.id, as_of=MARCH)
    flagged = enquiries.item_enquiry(
        db, stock.company_id, stock.item.id, as_of=MARCH, provisional_only=True
    )

    assert [move.cost_provisional for move in everything.moves] == [False, True]
    assert len(flagged.moves) == 1
    assert flagged.moves[0].cost_provisional is True
    assert flagged.moves[0].running_quantity == _d("-2"), (
        "the running column counts every move in the window, filtered or not"
    )
    assert flagged.total_quantity == everything.total_quantity


def test_paging_continues_the_running_column(db: Session, stock: Stock) -> None:
    for _ in range(5):
        receive(db, stock, quantity=_d("2"), unit_cost=_d("100"), on=MARCH)

    first = enquiries.item_enquiry(db, stock.company_id, stock.item.id, as_of=MARCH, limit=2)
    second = enquiries.item_enquiry(
        db, stock.company_id, stock.item.id, as_of=MARCH, limit=2, cursor=first.next_cursor
    )
    third = enquiries.item_enquiry(
        db, stock.company_id, stock.item.id, as_of=MARCH, limit=2, cursor=second.next_cursor
    )

    assert [move.running_quantity for move in first.moves] == [_d("2"), _d("4")]
    assert [move.running_quantity for move in second.moves] == [_d("6"), _d("8")]
    assert [move.running_quantity for move in third.moves] == [_d("10")]
    assert third.next_cursor is None
    seen = [move.move_id for move in first.moves + second.moves + third.moves]
    assert len(seen) == len(set(seen)) == 5, "paging repeated or dropped a move"


def test_paging_skips_past_rows_the_provisional_filter_hid(db: Session, stock: Stock) -> None:
    """The cursor is the last move of the page, not of the filtered rows — otherwise a page
    of nothing but unflagged moves would hand back the cursor it arrived with and the screen
    would fetch it forever."""
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    set_policy(db, stock, NegativeStockPolicy.ALLOW)
    issue(db, stock, quantity=_d("25"), on=MARCH)

    first = enquiries.item_enquiry(
        db, stock.company_id, stock.item.id, as_of=MARCH, provisional_only=True, limit=2
    )

    assert first.moves == [], "neither receipt is provisional"
    assert first.next_cursor is not None

    second = enquiries.item_enquiry(
        db,
        stock.company_id,
        stock.item.id,
        as_of=MARCH,
        provisional_only=True,
        limit=2,
        cursor=first.next_cursor,
    )
    assert [move.cost_provisional for move in second.moves] == [True]


def test_an_empty_location_is_hidden_unless_asked_for(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    issue(db, stock, quantity=_d("10"), on=MARCH)

    hidden = enquiries.item_enquiry(db, stock.company_id, stock.item.id, as_of=MARCH)
    shown = enquiries.item_enquiry(
        db, stock.company_id, stock.item.id, as_of=MARCH, include_zero_locations=True
    )

    assert hidden.locations == []
    assert [row.warehouse_code for row in shown.locations] == ["MAIN"]
    assert shown.locations[0].quantity == _d("0")


def test_an_unknown_warehouse_filter_is_a_not_found(db: Session, stock: Stock) -> None:
    with pytest.raises(NotFoundError):
        enquiries.item_enquiry(
            db, stock.company_id, stock.item.id, as_of=MARCH, warehouse_id=-1
        )


def test_an_unknown_cursor_is_a_not_found(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=_d("1"), unit_cost=_d("100"), on=MARCH)
    highest = db.scalar(select(StockMove.id).order_by(StockMove.id.desc()).limit(1))

    with pytest.raises(NotFoundError):
        enquiries.item_enquiry(
            db, stock.company_id, stock.item.id, as_of=MARCH, cursor=highest + 1000
        )
