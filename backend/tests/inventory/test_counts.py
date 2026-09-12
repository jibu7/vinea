"""P5 step 4 — stock count sessions (decision 7).

The count is the one document in P5 that exists before it posts anything, so most of what is
under test here is *not* arithmetic: it is that the snapshot is frozen and stays frozen, that a
line whose location moved afterwards is stale and cannot be processed, that re-snapshotting
clears the count that was taken against the old figure, and that an uncounted line is not a
count of zero.

The posting itself is the step-2 engine: gains at the current average, losses costed like any
other issue including the flush when one empties a location. Every test that reaches the
ledger asserts `assert_stock_invariants` and `assert_ledger_invariants`.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.inventory import counts as count_service
from app.inventory import documents as documents_service
from app.inventory import masters
from app.kernel.errors import LedgerStateError, PostingError
from app.models.inventory import (
    InventoryDocumentStatus,
    StockCountStatus,
    StockMove,
)
from app.models.journal import JournalEntry
from tests.inventory.conftest import Stock, issue, receive
from tests.inventory.invariants import assert_stock_invariants, location_position
from tests.kernel.conftest import YEAR
from tests.kernel.invariants import assert_ledger_invariants

MARCH = date(YEAR, 3, 10)
HUNDRED = Decimal(100)


def _both_invariants(db: Session, fixture: Stock) -> None:
    assert_stock_invariants(db, fixture.company_id)
    assert_ledger_invariants(db, fixture.company_id)


def _move_count(db: Session, fixture: Stock) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(StockMove)
            .where(StockMove.company_id == fixture.company_id)
        )
    )


def _open(db: Session, fixture: Stock, *, warehouse=None, **kwargs):
    return count_service.open_session(
        db,
        fixture.company_id,
        count_service.CountSessionInput(
            warehouse_id=(warehouse or fixture.main).id,
            count_date=MARCH,
            description="March stock take",
            **kwargs,
        ),
        actor=fixture.owner,
    )


def _only_line(db: Session, fixture: Stock, session):
    lines = count_service.lines_of(db, fixture.company_id, session.id)
    assert len(lines) == 1, f"expected one line on the sheet, found {len(lines)}"
    return lines[0]


def _count(db: Session, fixture: Stock, session, line, quantity, **kwargs):
    return count_service.enter_count(
        db,
        fixture.company_id,
        session.id,
        line.id,
        quantity=quantity,
        actor=fixture.owner,
        **kwargs,
    )


# --- The snapshot -----------------------------------------------------------------------------


def test_a_session_freezes_one_line_per_item_the_warehouse_holds(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)

    session = _open(db, stock)

    line = _only_line(db, stock, session)
    assert session.status == StockCountStatus.COUNTING
    assert session.number.startswith("CNS-"), (
        "a session is not a posting; the CNT- run belongs to the documents it produces"
    )
    assert line.system_quantity == Decimal(10)
    assert line.counted_quantity is None, "a fresh sheet is uncounted, not counted at zero"
    assert line.snapshot_sequence > 0


def test_the_snapshot_does_not_follow_the_balance(db: Session, stock: Stock) -> None:
    """The whole design in one test: a delivery after the freeze does not change what the
    sheet says the books held, because a count is a statement about a moment."""
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    session = _open(db, stock)
    line = _only_line(db, stock, session)

    receive(db, stock, quantity=Decimal(5), unit_cost=HUNDRED, on=MARCH)
    db.refresh(line)

    assert line.system_quantity == Decimal(10)
    assert location_position(
        db, stock.company_id, stock.item.id, stock.main.id
    ).quantity == Decimal(15)


def test_an_item_with_no_balance_is_left_off_the_sheet_unless_asked_for(
    db: Session, stock: Stock
) -> None:
    session = _open(db, stock)
    assert count_service.lines_of(db, stock.company_id, session.id) == []

    wanted = _open(db, stock, include_items=(stock.item.id,))

    line = _only_line(db, stock, wanted)
    assert line.system_quantity == Decimal(0)


def test_a_line_can_be_added_to_a_sheet_that_did_not_have_it(db: Session, stock: Stock) -> None:
    """Stock found where the books say none — the discovery a count exists to make."""
    session = _open(db, stock)

    line = count_service.add_line(
        db, stock.company_id, session.id, stock.item.id, actor=stock.owner
    )

    assert line.system_quantity == Decimal(0)
    with pytest.raises(LedgerStateError) as error:
        count_service.add_line(
            db, stock.company_id, session.id, stock.item.id, actor=stock.owner
        )
    assert error.value.code == "count_line_exists"


def test_the_in_transit_warehouse_cannot_be_counted(db: Session, stock: Stock) -> None:
    with pytest.raises(LedgerStateError) as error:
        _open(db, stock, warehouse=stock.transit)

    assert error.value.code == "in_transit_warehouse_not_selectable"


# --- Entering counts --------------------------------------------------------------------------


def test_a_count_keyed_in_cases_converts_to_the_base_unit(
    db: Session, stock: Stock, inventory
) -> None:
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
    session = _open(db, stock)
    line = _only_line(db, stock, session)

    _count(db, stock, session, line, Decimal(2), uom_id=case.id)

    assert (line.counted_quantity, line.counted_quantity_base) == (Decimal(2), Decimal(24))
    assert count_service.variance_of(line) == Decimal(0)


def test_an_uncounted_line_is_not_a_count_of_zero(db: Session, stock: Stock) -> None:
    """The distinction the nullable column exists for: one posts nothing, the other writes
    off everything the location held."""
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    session = _open(db, stock)
    line = _only_line(db, stock, session)

    assert count_service.variance_of(line) is None

    _count(db, stock, session, line, Decimal(0))
    assert count_service.variance_of(line) == Decimal(-10)

    _count(db, stock, session, line, None)
    assert count_service.variance_of(line) is None
    assert line.counted_at is None


def test_a_negative_count_is_refused(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    session = _open(db, stock)
    line = _only_line(db, stock, session)

    with pytest.raises(PostingError) as error:
        _count(db, stock, session, line, Decimal(-1))

    assert error.value.code == "invalid_quantity"


def test_a_closed_session_refuses_further_counting(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    session = _open(db, stock)
    line = _only_line(db, stock, session)
    count_service.cancel_session(
        db, stock.company_id, session.id, reason="counted the wrong aisle", actor=stock.owner
    )

    with pytest.raises(LedgerStateError) as error:
        _count(db, stock, session, line, Decimal(9))

    assert error.value.code == "count_session_closed"


# --- Staleness --------------------------------------------------------------------------------


def test_a_line_whose_location_moved_after_the_snapshot_is_stale(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    session = _open(db, stock)
    line = _only_line(db, stock, session)
    _count(db, stock, session, line, Decimal(9))
    assert count_service.is_stale(db, stock.company_id, session, line) is False

    issue(db, stock, quantity=Decimal(2), on=MARCH)

    assert count_service.is_stale(db, stock.company_id, session, line) is True


def test_a_move_at_another_warehouse_does_not_make_the_line_stale(
    db: Session, stock: Stock
) -> None:
    """Staleness is per location, as decision 7 says: the same item moving at Depot tells us
    nothing about the shelf at Main that was counted."""
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    session = _open(db, stock)
    line = _only_line(db, stock, session)
    _count(db, stock, session, line, Decimal(9))

    receive(db, stock, quantity=Decimal(4), unit_cost=HUNDRED, warehouse=stock.depot, on=MARCH)

    assert count_service.is_stale(db, stock.company_id, session, line) is False


def test_processing_a_stale_line_is_impossible(db: Session, stock: Stock) -> None:
    """The property step 4 names. The refusal is the whole session, not the line: a count is
    one statement about one warehouse at one moment, and posting the part of it that still
    holds would produce a document nobody counted."""
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    session = _open(db, stock)
    line = _only_line(db, stock, session)
    _count(db, stock, session, line, Decimal(9))
    issue(db, stock, quantity=Decimal(2), on=MARCH)
    before = _move_count(db, stock)

    with pytest.raises(LedgerStateError) as error:
        count_service.process_session(
            db, stock.company_id, session.id, actor=stock.owner
        )

    assert error.value.code == "count_line_stale"
    assert f"lines.{line.id}" in error.value.field_errors
    assert _move_count(db, stock) == before, "a refused count posted moves anyway"
    assert session.status == StockCountStatus.COUNTING


def test_an_uncounted_stale_line_does_not_block_the_session(db: Session, stock: Stock) -> None:
    """A line nobody counted contributes nothing to the posting, so whether its location moved
    is a question about a variance that does not exist."""
    second = masters.create_item(
        db,
        stock.company_id,
        masters.ItemInput(
            code="WINE-375",
            name="Rugari Red 375ml",
            uom_category_id=stock.inventory.count.id,
            base_uom_id=stock.inventory.each.id,
        ),
        actor=stock.owner,
    )
    db.flush()
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    session = _open(db, stock, include_items=(second.id,))
    lines = count_service.lines_of(db, stock.company_id, session.id)
    counted = next(line for line in lines if line.item_id == stock.item.id)
    _count(db, stock, session, counted, Decimal(9))

    # The *other* line's location moves; nobody counted it.
    documents_service.post_adjustment(
        db,
        stock.company_id,
        documents_service.DocumentInput(
            document_date=MARCH,
            description="delivery of the other item",
            lines=(
                documents_service.DocumentLineInput(
                    item_id=second.id,
                    warehouse_id=stock.main.id,
                    quantity=Decimal(5),
                    unit_cost=HUNDRED,
                    transaction_type_id=stock.type_id("ADJIN"),
                ),
            ),
        ),
        actor=stock.owner,
    )

    session, document, _ = count_service.process_session(
        db, stock.company_id, session.id, actor=stock.owner
    )

    assert session.status == StockCountStatus.COMPLETED
    assert document is not None
    _both_invariants(db, stock)


def test_resnapshotting_refreezes_the_line_and_clears_its_count(
    db: Session, stock: Stock
) -> None:
    """Both halves, as decision 7 says. Re-freezing alone would score an old count against a
    new system quantity — the arithmetic would work and the answer would be a fiction."""
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    session = _open(db, stock)
    line = _only_line(db, stock, session)
    _count(db, stock, session, line, Decimal(9))
    issue(db, stock, quantity=Decimal(2), on=MARCH)

    count_service.resnapshot_line(
        db, stock.company_id, session.id, line.id, actor=stock.owner
    )

    assert line.system_quantity == Decimal(8)
    assert line.counted_quantity is None
    assert count_service.is_stale(db, stock.company_id, session, line) is False

    _count(db, stock, session, line, Decimal(8))
    session, document, _ = count_service.process_session(
        db, stock.company_id, session.id, actor=stock.owner
    )
    assert document is None, "a count that agrees with the books posts nothing"
    assert session.status == StockCountStatus.COMPLETED


# --- The preview ------------------------------------------------------------------------------


def test_the_preview_shows_the_posting_before_process(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    session = _open(db, stock)
    line = _only_line(db, stock, session)
    _count(db, stock, session, line, Decimal(8))

    preview = count_service.preview(db, stock.company_id, session.id)

    assert preview.can_process is True
    assert preview.variance_lines == 1
    assert preview.counted_lines == 1
    assert preview.uncounted_lines == 0
    assert preview.total_value == Decimal(-200)
    assert preview.lines[0].variance == Decimal(-2)
    assert preview.lines[0].unit_cost == Decimal(100)


def test_the_preview_names_the_stale_lines_that_would_stop_process(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    session = _open(db, stock)
    line = _only_line(db, stock, session)
    _count(db, stock, session, line, Decimal(8))
    issue(db, stock, quantity=Decimal(1), on=MARCH)

    preview = count_service.preview(db, stock.company_id, session.id)

    assert preview.stale_lines == [line.id]
    assert preview.can_process is False


# --- Processing -------------------------------------------------------------------------------


def test_a_shortfall_posts_one_variance_document_and_completes_the_session(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    session = _open(db, stock)
    line = _only_line(db, stock, session)
    _count(db, stock, session, line, Decimal(8))

    session, document, replayed = count_service.process_session(
        db, stock.company_id, session.id, actor=stock.owner
    )

    assert replayed is False
    assert session.status == StockCountStatus.COMPLETED
    assert document is not None
    assert document.doc_type == "INCT"
    assert document.number.startswith("CNT-")
    assert session.document_id == document.id
    assert document.status == InventoryDocumentStatus.POSTED
    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert (position.quantity, position.value) == (Decimal(8), Decimal(800))
    assert line.stock_move_id is not None
    _both_invariants(db, stock)


def test_a_surplus_is_taken_in_at_the_current_average(db: Session, stock: Stock) -> None:
    """Decision 7: gains and losses are both costed at the current average. Two receipts at
    different prices make an average that is not either of them, which is the only way to see
    that the found stock was not costed at the last price paid."""
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    receive(db, stock, quantity=Decimal(10), unit_cost=Decimal(200), on=MARCH)
    session = _open(db, stock)
    line = _only_line(db, stock, session)
    _count(db, stock, session, line, Decimal(23))

    _, document, _ = count_service.process_session(
        db, stock.company_id, session.id, actor=stock.owner
    )

    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert position.quantity == Decimal(23)
    # 20 units worth 3 000 → average 150; three found are worth 450.
    assert position.value == Decimal(3450)
    move = db.get(StockMove, line.stock_move_id)
    assert (move.quantity, move.value) == (Decimal(3), Decimal(450))
    assert document.journal_entry_id is not None
    _both_invariants(db, stock)


def test_gains_and_losses_ride_in_one_entry(db: Session, stock: Stock) -> None:
    second = masters.create_item(
        db,
        stock.company_id,
        masters.ItemInput(
            code="WINE-375",
            name="Rugari Red 375ml",
            uom_category_id=stock.inventory.count.id,
            base_uom_id=stock.inventory.each.id,
        ),
        actor=stock.owner,
    )
    db.flush()
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    documents_service.post_adjustment(
        db,
        stock.company_id,
        documents_service.DocumentInput(
            document_date=MARCH,
            description="the other item in",
            lines=(
                documents_service.DocumentLineInput(
                    item_id=second.id,
                    warehouse_id=stock.main.id,
                    quantity=Decimal(10),
                    unit_cost=Decimal(50),
                    transaction_type_id=stock.type_id("ADJIN"),
                ),
            ),
        ),
        actor=stock.owner,
    )
    session = _open(db, stock)
    lines = count_service.lines_of(db, stock.company_id, session.id)
    for line in lines:
        # 12 found of the 50-a-unit item (+100), 8 of the 100-a-unit item (−200).
        counted = Decimal(12) if line.item_id == second.id else Decimal(8)
        _count(db, stock, session, line, counted)

    _, document, _ = count_service.process_session(
        db, stock.company_id, session.id, actor=stock.owner
    )

    # One document, one entry — a gain and a loss under one header (decision 3).
    assert len(documents_service.lines_of(db, stock.company_id, document.id)) == 2
    entry = db.get(JournalEntry, document.journal_entry_id)
    assert entry is not None
    contra = stock.inventory.transaction_types["CNTV"].default_gl_account_id
    amounts = {
        (line.gl_account_id, line.item_id): line.base_amount for line in entry.lines
    }
    assert amounts[(stock.inventory.settings.inventory_account_id, second.id)] == Decimal(100)
    assert amounts[
        (stock.inventory.settings.inventory_account_id, stock.item.id)
    ] == Decimal(-200)
    # One contra line for both, netted per (account, branch, project) as decision 3 says.
    assert amounts[(contra, None)] == Decimal(100)
    assert len(amounts) == 3
    _both_invariants(db, stock)


def test_a_count_of_zero_empties_the_location_and_takes_what_it_held(
    db: Session, stock: Stock
) -> None:
    """The flush of decision 4, reached through a count: the issue that empties a location
    takes that location's remaining value rather than the average times the quantity."""
    receive(db, stock, quantity=Decimal(3), unit_cost=Decimal("33.3333"), on=MARCH)
    session = _open(db, stock)
    line = _only_line(db, stock, session)
    held = location_position(db, stock.company_id, stock.item.id, stock.main.id).value
    _count(db, stock, session, line, Decimal(0))

    _, document, _ = count_service.process_session(
        db, stock.company_id, session.id, actor=stock.owner
    )

    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert (position.quantity, position.value) == (Decimal(0), Decimal(0))
    move = db.get(StockMove, line.stock_move_id)
    assert move.value == -held
    _both_invariants(db, stock)


def test_a_count_that_agrees_with_the_books_posts_nothing(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    session = _open(db, stock)
    line = _only_line(db, stock, session)
    _count(db, stock, session, line, Decimal(10))
    before = _move_count(db, stock)

    session, document, _ = count_service.process_session(
        db, stock.company_id, session.id, actor=stock.owner
    )

    assert document is None
    assert session.status == StockCountStatus.COMPLETED
    assert session.document_id is None
    assert _move_count(db, stock) == before


def test_a_processed_count_cannot_be_processed_again(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    session = _open(db, stock)
    line = _only_line(db, stock, session)
    _count(db, stock, session, line, Decimal(8))
    count_service.process_session(db, stock.company_id, session.id, actor=stock.owner)

    with pytest.raises(LedgerStateError) as error:
        count_service.process_session(db, stock.company_id, session.id, actor=stock.owner)

    assert error.value.code == "count_already_processed"


def test_replaying_the_process_key_returns_the_same_document(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    session = _open(db, stock)
    line = _only_line(db, stock, session)
    _count(db, stock, session, line, Decimal(8))

    _, first, first_replay = count_service.process_session(
        db, stock.company_id, session.id, actor=stock.owner, idempotency_key="cnt-1"
    )
    _, second, second_replay = count_service.process_session(
        db, stock.company_id, session.id, actor=stock.owner, idempotency_key="cnt-1"
    )

    assert first_replay is False
    assert second_replay is True
    assert second.id == first.id
    assert location_position(
        db, stock.company_id, stock.item.id, stock.main.id
    ).quantity == Decimal(8), "the replay posted the variance twice"


def test_a_cancelled_session_posts_nothing_and_keeps_its_number(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    session = _open(db, stock)
    line = _only_line(db, stock, session)
    _count(db, stock, session, line, Decimal(4))
    before = _move_count(db, stock)

    count_service.cancel_session(
        db, stock.company_id, session.id, reason="counted the wrong aisle", actor=stock.owner
    )

    assert session.status == StockCountStatus.CANCELLED
    assert session.number
    assert _move_count(db, stock) == before
    with pytest.raises(LedgerStateError) as error:
        count_service.process_session(db, stock.company_id, session.id, actor=stock.owner)
    assert error.value.code == "count_session_cancelled"


def test_the_variance_document_reverses_and_the_count_stays_completed(
    db: Session, stock: Stock
) -> None:
    """Decision 11: a processed count stays Completed and links to the reversal. It reverses
    through step 3's machinery because the variance really is an ordinary stock document."""
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    session = _open(db, stock)
    line = _only_line(db, stock, session)
    _count(db, stock, session, line, Decimal(8))
    session, document, _ = count_service.process_session(
        db, stock.company_id, session.id, actor=stock.owner
    )

    reversal = documents_service.reverse_document(
        db,
        stock.company_id,
        document.id,
        on_date=MARCH,
        reason="recounted and the first count was wrong",
        actor=stock.owner,
    )

    assert reversal.reverses_document_id == document.id
    assert document.status == InventoryDocumentStatus.REVERSED
    assert session.status == StockCountStatus.COMPLETED
    assert session.document_id == document.id
    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert (position.quantity, position.value) == (Decimal(10), Decimal(1000))
    _both_invariants(db, stock)


def test_the_variance_lands_on_the_count_variance_account(db: Session, stock: Stock) -> None:
    """Decision 10's key, and decision 9's type, in the order this module resolves them: the
    seeded `CNTV` type names its own contra, so that is where the variance goes."""
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    session = _open(db, stock)
    line = _only_line(db, stock, session)
    _count(db, stock, session, line, Decimal(8))

    _, document, _ = count_service.process_session(
        db, stock.company_id, session.id, actor=stock.owner
    )

    entry = db.get(JournalEntry, document.journal_entry_id)
    contra = stock.inventory.transaction_types["CNTV"].default_gl_account_id
    assert contra is not None
    assert any(
        line.gl_account_id == contra and line.base_amount == Decimal(200)
        for line in entry.lines
    ), "the variance did not land on the count-variance type's contra account"


def test_processing_a_sheet_nobody_filled_in_is_refused(db: Session, stock: Stock) -> None:
    """Completing an uncounted sheet would be the worst of both worlds: terminal, so the
    count cannot be resumed, and empty, so there is no record of what was found."""
    receive(db, stock, quantity=Decimal(10), unit_cost=HUNDRED, on=MARCH)
    session = _open(db, stock)

    with pytest.raises(LedgerStateError) as error:
        count_service.process_session(db, stock.company_id, session.id, actor=stock.owner)

    assert error.value.code == "count_not_started"
    assert session.status == StockCountStatus.COUNTING
