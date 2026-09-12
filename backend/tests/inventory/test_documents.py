"""P5 step 3 — adjustments, journal batches and reversal.

Step 2 proved the costing engine. What is under test here is the *document layer* on top of
it: that a transaction type decides direction, that a quantity keyed in cases becomes a
quantity in eaches, that a batch is one entry and is refused whole, that a number is claimed
once, and that a retry posts nothing twice.

Every test that moves stock asserts `assert_stock_invariants` afterwards, which is the
standing contract from step 2 — valuation equals the inventory GL balance, per branch, and
every cache agrees with the moves behind it.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.inventory import documents as documents_service
from app.inventory import masters
from app.kernel.errors import LedgerStateError, PostingError
from app.models.fiscal import PeriodStatus
from app.models.inventory import (
    InventoryDocument,
    InventoryDocumentStatus,
    NegativeStockPolicy,
    StockMove,
)
from app.models.journal import JournalEntry, JournalLine
from tests.inventory.conftest import Stock
from tests.inventory.invariants import assert_stock_invariants, location_position
from tests.kernel.invariants import assert_ledger_invariants

JANUARY = date(2026, 1, 15)


def _line(
    fixture: Stock,
    *,
    type_code: str,
    quantity: Decimal = Decimal(0),
    unit_cost: Decimal | None = None,
    value: Decimal | None = None,
    warehouse=None,
    uom_id: int | None = None,
) -> documents_service.DocumentLineInput:
    return documents_service.DocumentLineInput(
        item_id=fixture.item.id,
        warehouse_id=(warehouse or fixture.main).id,
        quantity=quantity,
        uom_id=uom_id,
        unit_cost=unit_cost,
        value=value,
        transaction_type_id=fixture.type_id(type_code),
    )


def _input(
    fixture: Stock,
    *lines: documents_service.DocumentLineInput,
    on: date = JANUARY,
    description: str = "stock document",
) -> documents_service.DocumentInput:
    return documents_service.DocumentInput(
        document_date=on, description=description, lines=lines
    )


def _post_in(
    db: Session, fixture: Stock, quantity: Decimal, unit_cost: Decimal, **kwargs
) -> InventoryDocument:
    document, _ = documents_service.post_adjustment(
        db,
        fixture.company_id,
        _input(
            fixture,
            _line(fixture, type_code="ADJIN", quantity=quantity, unit_cost=unit_cost, **kwargs),
        ),
        actor=fixture.owner,
    )
    return document


def _both_invariants(db: Session, fixture: Stock) -> None:
    assert_stock_invariants(db, fixture.company_id)
    assert_ledger_invariants(db, fixture.company_id)


# --- The document itself ---------------------------------------------------------------------


def test_an_adjustment_in_posts_moves_and_one_entry(db: Session, stock: Stock) -> None:
    document = _post_in(db, stock, Decimal(10), Decimal(100))

    assert document.status == InventoryDocumentStatus.POSTED
    assert document.journal_entry_id is not None
    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert position.quantity == Decimal(10)
    assert position.value == Decimal(1000)
    _both_invariants(db, stock)


def test_the_document_takes_the_number_of_the_entry_it_posted(
    db: Session, stock: Stock
) -> None:
    """One number for the document and the entry, as `partner_documents` does in P4 — so a
    trial balance and a stock enquiry name the same thing the same way."""
    document = _post_in(db, stock, Decimal(4), Decimal(50))
    entry = db.get(JournalEntry, document.journal_entry_id)

    assert entry is not None
    assert document.number == entry.number
    assert document.number.startswith("ADJ-") or document.doc_type == "INAJ"


def test_every_line_links_to_the_move_it_became(db: Session, stock: Stock) -> None:
    document = _post_in(db, stock, Decimal(6), Decimal(25))
    lines = documents_service.lines_of(db, stock.company_id, document.id)

    assert len(lines) == 1
    move = db.get(StockMove, lines[0].stock_move_id)
    assert move is not None
    assert move.quantity == Decimal(6)
    assert move.value == Decimal(150)


def test_direction_comes_from_the_transaction_type_not_the_sign(
    db: Session, stock: Stock
) -> None:
    """A positive quantity under an `adjustment_out` type takes stock *out*. The payload never
    carries a sign, which is what lets a user define "Damaged" without any code knowing."""
    _post_in(db, stock, Decimal(10), Decimal(100))
    documents_service.post_adjustment(
        db,
        stock.company_id,
        _input(stock, _line(stock, type_code="ADJOUT", quantity=Decimal(4))),
        actor=stock.owner,
    )

    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert position.quantity == Decimal(6)
    assert position.value == Decimal(600)
    _both_invariants(db, stock)


def test_a_revaluation_moves_value_and_no_quantity(db: Session, stock: Stock) -> None:
    _post_in(db, stock, Decimal(10), Decimal(100))
    documents_service.post_adjustment(
        db,
        stock.company_id,
        _input(stock, _line(stock, type_code="REVAL", value=Decimal(-200))),
        actor=stock.owner,
    )

    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert position.quantity == Decimal(10)
    assert position.value == Decimal(800)
    _both_invariants(db, stock)


def test_an_adjustment_carries_exactly_one_line(db: Session, stock: Stock) -> None:
    with pytest.raises(LedgerStateError) as err:
        documents_service.post_adjustment(
            db,
            stock.company_id,
            _input(
                stock,
                _line(stock, type_code="ADJIN", quantity=Decimal(1), unit_cost=Decimal(1)),
                _line(stock, type_code="ADJIN", quantity=Decimal(2), unit_cost=Decimal(1)),
            ),
            actor=stock.owner,
        )
    assert err.value.code == "adjustment_is_single_line"


# --- Units of measure ------------------------------------------------------------------------


def test_a_quantity_keyed_in_another_unit_becomes_base_units(
    db: Session, stock: Stock
) -> None:
    """Decision 8: entry in any unit of the item's category converts to base before it is a
    move. The *rate* converts with it, so the value is what the user meant to spend."""
    case = masters.create_uom(
        db,
        stock.company_id,
        code="CASE12",
        name="Case of 12",
        category_id=stock.inventory.count.id,
        factor_to_base=Decimal(12),
        actor=stock.owner,
    )
    db.flush()

    document = _post_in(
        db, stock, Decimal(2), Decimal(1200), uom_id=case.id
    )
    lines = documents_service.lines_of(db, stock.company_id, document.id)

    # Keyed: 2 cases at 1 200 a case. In base units: 24 each at 100 each, worth 2 400.
    assert lines[0].quantity == Decimal(2)
    assert lines[0].quantity_base == Decimal(24)
    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert position.quantity == Decimal(24)
    assert position.value == Decimal(2400)
    _both_invariants(db, stock)


def test_a_unit_from_another_category_is_refused(db: Session, stock: Stock) -> None:
    litre = stock.inventory.base_uoms["VOLUME"]
    with pytest.raises(LedgerStateError) as err:
        _post_in(db, stock, Decimal(3), Decimal(10), uom_id=litre.id)
    assert err.value.code == "uom_category_mismatch"


# --- What a line may and may not say ---------------------------------------------------------


def test_an_increase_needs_a_unit_cost(db: Session, stock: Stock) -> None:
    """There is no average to charge an incoming unit at — the incoming unit is what makes
    the average."""
    with pytest.raises(PostingError) as err:
        documents_service.post_adjustment(
            db,
            stock.company_id,
            _input(stock, _line(stock, type_code="ADJIN", quantity=Decimal(5))),
            actor=stock.owner,
        )
    assert err.value.code == "unit_cost_required"
    assert "lines.0.unit_cost" in err.value.field_errors


def test_a_decrease_refuses_a_unit_cost(db: Session, stock: Stock) -> None:
    """Accepting a rate here and ignoring it would be worse than refusing it: the user would
    believe it applied."""
    _post_in(db, stock, Decimal(10), Decimal(100))
    with pytest.raises(PostingError) as err:
        documents_service.post_adjustment(
            db,
            stock.company_id,
            _input(
                stock,
                _line(
                    stock, type_code="ADJOUT", quantity=Decimal(2), unit_cost=Decimal(999)
                ),
            ),
            actor=stock.owner,
        )
    assert err.value.code == "unit_cost_not_allowed"


def test_a_transfer_type_cannot_be_keyed_on_an_adjustment(db: Session, stock: Stock) -> None:
    """A hand-keyed transfer half would leave stock in transit with nothing to receive it."""
    with pytest.raises(LedgerStateError) as err:
        documents_service.post_adjustment(
            db,
            stock.company_id,
            _input(
                stock,
                _line(stock, type_code="TRF", quantity=Decimal(1), unit_cost=Decimal(1)),
            ),
            actor=stock.owner,
        )
    assert err.value.code == "transaction_kind_not_allowed"


def test_a_revaluation_of_nothing_is_refused(db: Session, stock: Stock) -> None:
    with pytest.raises(PostingError) as err:
        documents_service.post_adjustment(
            db,
            stock.company_id,
            _input(stock, _line(stock, type_code="REVAL", value=Decimal(0))),
            actor=stock.owner,
        )
    assert err.value.code == "zero_value_posting"


# --- Batches ---------------------------------------------------------------------------------


def test_a_batch_of_mixed_directions_posts_one_entry(db: Session, stock: Stock) -> None:
    """Decision 3: one stock document, one journal entry — however many lines, whichever way
    they point."""
    _post_in(db, stock, Decimal(20), Decimal(100))

    document, _ = documents_service.post_batch(
        db,
        stock.company_id,
        _input(
            stock,
            _line(stock, type_code="ADJIN", quantity=Decimal(5), unit_cost=Decimal(120)),
            _line(stock, type_code="ADJOUT", quantity=Decimal(3)),
            _line(
                stock,
                type_code="ADJIN",
                quantity=Decimal(2),
                unit_cost=Decimal(90),
                warehouse=stock.depot,
            ),
        ),
        actor=stock.owner,
    )

    assert document.journal_entry_id is not None
    entries = db.scalars(
        select(StockMove.journal_entry_id).where(
            StockMove.company_id == stock.company_id,
            StockMove.journal_entry_id.is_not(None),
        )
    ).all()
    # Two documents so far, so two distinct entries — the batch contributed exactly one.
    assert len(set(entries)) == 2
    assert len(documents_service.lines_of(db, stock.company_id, document.id)) == 3
    _both_invariants(db, stock)


def test_a_batch_is_refused_whole(db: Session, stock: Stock) -> None:
    """The fourth line names no unit cost, so the first three do not post. Nothing here is
    committed, so "refused whole" is the absence of any move at all."""
    before = db.scalar(
        select(func.count()).select_from(StockMove).where(
            StockMove.company_id == stock.company_id
        )
    )
    with pytest.raises(PostingError) as err:
        documents_service.post_batch(
            db,
            stock.company_id,
            _input(
                stock,
                _line(stock, type_code="ADJIN", quantity=Decimal(1), unit_cost=Decimal(10)),
                _line(stock, type_code="ADJIN", quantity=Decimal(2), unit_cost=Decimal(10)),
                _line(stock, type_code="ADJIN", quantity=Decimal(3), unit_cost=Decimal(10)),
                _line(stock, type_code="ADJIN", quantity=Decimal(4)),
            ),
            actor=stock.owner,
        )
    assert err.value.code == "unit_cost_required"
    # Re-keyed onto the offending line so the grid can show it in place.
    assert "lines.3.unit_cost" in err.value.field_errors
    after = db.scalar(
        select(func.count()).select_from(StockMove).where(
            StockMove.company_id == stock.company_id
        )
    )
    assert after == before


def test_opening_stock_arrives_through_the_batch(db: Session, stock: Stock) -> None:
    """Decision 2's consequence: inventory accounts are control accounts, so opening stock
    cannot be a GL journal. This is the go-live path."""
    document, _ = documents_service.post_batch(
        db,
        stock.company_id,
        _input(
            stock,
            _line(stock, type_code="OPEN", quantity=Decimal(10), unit_cost=Decimal(100)),
            _line(
                stock,
                type_code="OPEN",
                quantity=Decimal(4),
                unit_cost=Decimal(150),
                warehouse=stock.depot,
            ),
        ),
        actor=stock.owner,
    )

    assert document.doc_type == "INJN"
    main = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    depot = location_position(db, stock.company_id, stock.item.id, stock.depot.id)
    assert (main.quantity, main.value) == (Decimal(10), Decimal(1000))
    assert (depot.quantity, depot.value) == (Decimal(4), Decimal(600))
    _both_invariants(db, stock)


# --- Idempotency -----------------------------------------------------------------------------


def test_the_same_key_returns_the_same_document(db: Session, stock: Stock) -> None:
    first, replayed_first = documents_service.post_adjustment(
        db,
        stock.company_id,
        _input(stock, _line(stock, type_code="ADJIN", quantity=Decimal(5), unit_cost=Decimal(20))),
        actor=stock.owner,
        idempotency_key="key-1",
    )
    second, replayed_second = documents_service.post_adjustment(
        db,
        stock.company_id,
        _input(stock, _line(stock, type_code="ADJIN", quantity=Decimal(5), unit_cost=Decimal(20))),
        actor=stock.owner,
        idempotency_key="key-1",
    )

    assert replayed_first is False
    assert replayed_second is True
    assert first.id == second.id
    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert position.quantity == Decimal(5)
    _both_invariants(db, stock)


def test_a_posting_that_valued_nothing_is_still_replay_protected(
    db: Session, stock: Stock
) -> None:
    """The reason this table carries a key at all.

    A receipt at zero cost moves quantity and no value, so the Posting Engine writes no entry
    — and `Idempotency-Key` lives on `journal_entries`. Without a key of its own, a retry
    would put the quantity on the shelf a second time with nothing to stop it.
    """
    first, _ = documents_service.post_adjustment(
        db,
        stock.company_id,
        _input(stock, _line(stock, type_code="ADJIN", quantity=Decimal(7), unit_cost=Decimal(0))),
        actor=stock.owner,
        idempotency_key="free-samples",
    )
    assert first.journal_entry_id is None, "a valueless posting writes no entry"

    second, replayed = documents_service.post_adjustment(
        db,
        stock.company_id,
        _input(stock, _line(stock, type_code="ADJIN", quantity=Decimal(7), unit_cost=Decimal(0))),
        actor=stock.owner,
        idempotency_key="free-samples",
    )

    assert replayed is True
    assert second.id == first.id
    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert position.quantity == Decimal(7), "the retry must not stock it twice"
    _both_invariants(db, stock)


def test_a_valueless_document_still_claims_a_number(db: Session, stock: Stock) -> None:
    """No entry to inherit a number from, so it claims one from the same gapless sequence —
    every number in the run is accounted for by either an entry or a valueless document."""
    document = _post_in(db, stock, Decimal(3), Decimal(0))

    assert document.journal_entry_id is None
    assert document.number


def test_a_key_reused_with_a_different_payload_is_refused(db: Session, stock: Stock) -> None:
    documents_service.post_adjustment(
        db,
        stock.company_id,
        _input(stock, _line(stock, type_code="ADJIN", quantity=Decimal(5), unit_cost=Decimal(20))),
        actor=stock.owner,
        idempotency_key="key-2",
        idempotency_hash="hash-of-the-first",
    )
    with pytest.raises(ConflictError) as err:
        documents_service.post_adjustment(
            db,
            stock.company_id,
            _input(
                stock,
                _line(stock, type_code="ADJIN", quantity=Decimal(99), unit_cost=Decimal(20)),
            ),
            actor=stock.owner,
            idempotency_key="key-2",
            idempotency_hash="a-different-hash",
        )
    assert err.value.code == "idempotency_key_reused"


# --- Periods and negative stock ---------------------------------------------------------------


def test_a_closed_period_is_refused_before_any_move_exists(db: Session, stock: Stock) -> None:
    """Period enforcement is inherited from the kernel, and it bites *first*: the posting
    locks the period before it values anything, so a document dated into a closed period
    cannot leave a move behind even briefly."""
    january = stock.inventory.ledger.periods[0]
    january.status = PeriodStatus.CLOSED
    db.flush()

    before = db.scalar(
        select(func.count())
        .select_from(StockMove)
        .where(StockMove.company_id == stock.company_id)
    )
    with pytest.raises((LedgerStateError, PostingError)) as err:
        _post_in(db, stock, Decimal(5), Decimal(10))
    assert err.value.code in ("period_closed", "period_not_open")

    after = db.scalar(
        select(func.count())
        .select_from(StockMove)
        .where(StockMove.company_id == stock.company_id)
    )
    assert after == before
    assert (
        db.scalar(
            select(func.count())
            .select_from(InventoryDocument)
            .where(InventoryDocument.company_id == stock.company_id)
        )
        == 0
    )


def test_insufficient_stock_lands_on_the_quantity_cell(db: Session, stock: Stock) -> None:
    """Decision 5's default. The error is re-keyed onto the line so the grid can show it on
    the quantity cell, which is what the step-7 screen binds to."""
    _post_in(db, stock, Decimal(2), Decimal(100))
    with pytest.raises(LedgerStateError) as err:
        documents_service.post_adjustment(
            db,
            stock.company_id,
            _input(stock, _line(stock, type_code="ADJOUT", quantity=Decimal(5))),
            actor=stock.owner,
        )
    assert err.value.code == "insufficient_stock"
    assert "lines.0.quantity" in err.value.field_errors


# --- Reversal --------------------------------------------------------------------------------


def test_a_reversal_returns_the_quantity_and_the_value(db: Session, stock: Stock) -> None:
    """Decision 11: the kernel reversal plus reversing moves at the *original* values. A
    reversal that re-costed at today's average would move a different amount than the thing
    it reverses, and the two would not cancel."""
    _post_in(db, stock, Decimal(10), Decimal(100))
    adjustment = _post_in(db, stock, Decimal(5), Decimal(160))

    before = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert (before.quantity, before.value) == (Decimal(15), Decimal(1800))

    reversal = documents_service.reverse_document(
        db,
        stock.company_id,
        adjustment.id,
        on_date=JANUARY,
        reason="keyed twice",
        actor=stock.owner,
    )

    after = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert (after.quantity, after.value) == (Decimal(10), Decimal(1000))
    assert reversal.reverses_document_id == adjustment.id
    assert adjustment.status == InventoryDocumentStatus.REVERSED
    _both_invariants(db, stock)


def test_a_reversal_is_itself_a_document_with_its_own_lines(
    db: Session, stock: Stock
) -> None:
    adjustment = _post_in(db, stock, Decimal(8), Decimal(50))
    reversal = documents_service.reverse_document(
        db,
        stock.company_id,
        adjustment.id,
        on_date=JANUARY,
        reason="wrong warehouse",
        actor=stock.owner,
    )

    lines = documents_service.lines_of(db, stock.company_id, reversal.id)
    assert len(lines) == 1
    move = db.get(StockMove, lines[0].stock_move_id)
    assert move is not None
    assert move.quantity == Decimal(-8), "the reversing move mirrors the original"
    assert move.value == Decimal(-400)


def test_a_valueless_document_reverses_as_a_valueless_mirror(
    db: Session, stock: Stock
) -> None:
    """Decision 4: a posting the ledger never saw still has to be undoable.

    A zero-cost receipt moves quantity and no value, so the Posting Engine writes no entry.
    Refusing to reverse it would leave it as the one posting in the system that cannot be
    undone. It mirrors instead: the quantity comes back off the shelf, the location returns
    to 0 / 0, the two documents link, and *neither* carries a journal entry — because there
    was nothing for the ledger to say in either direction.
    """
    receipt = _post_in(db, stock, Decimal(7), Decimal(0))
    assert receipt.journal_entry_id is None

    after_receipt = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert (after_receipt.quantity, after_receipt.value) == (Decimal(7), Decimal(0))

    reversal = documents_service.reverse_document(
        db,
        stock.company_id,
        receipt.id,
        on_date=JANUARY,
        reason="never arrived",
        actor=stock.owner,
    )

    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert (position.quantity, position.value) == (Decimal(0), Decimal(0))

    assert reversal.reverses_document_id == receipt.id
    assert receipt.status == InventoryDocumentStatus.REVERSED
    assert reversal.journal_entry_id is None, "the mirror posts no entry either"
    assert receipt.reversal_entry_id is None
    assert reversal.number and reversal.number != receipt.number

    lines = documents_service.lines_of(db, stock.company_id, reversal.id)
    assert len(lines) == 1
    move = db.get(StockMove, lines[0].stock_move_id)
    assert move is not None
    assert move.quantity == Decimal(-7)
    assert move.value == Decimal(0)
    assert move.journal_entry_id is None
    assert move.reverses_move_id is not None
    _both_invariants(db, stock)


def test_block_still_refuses_a_valueless_reversal_whose_stock_has_gone(
    db: Session, stock: Stock
) -> None:
    """The mirror is not a way around the negative-stock policy: a zero-cost receipt whose
    quantity has since gone out cannot be taken back under `block` either."""
    receipt = _post_in(db, stock, Decimal(7), Decimal(0))
    documents_service.post_adjustment(
        db,
        stock.company_id,
        _input(stock, _line(stock, type_code="ADJOUT", quantity=Decimal(5))),
        actor=stock.owner,
    )

    with pytest.raises(LedgerStateError) as err:
        documents_service.reverse_document(
            db, stock.company_id, receipt.id, on_date=JANUARY, reason="too late",
            actor=stock.owner,
        )
    assert err.value.code == "insufficient_stock"


def test_a_document_cannot_be_reversed_twice(db: Session, stock: Stock) -> None:
    adjustment = _post_in(db, stock, Decimal(4), Decimal(10))
    documents_service.reverse_document(
        db, stock.company_id, adjustment.id, on_date=JANUARY, reason="once",
        actor=stock.owner,
    )
    with pytest.raises(LedgerStateError) as err:
        documents_service.reverse_document(
            db, stock.company_id, adjustment.id, on_date=JANUARY, reason="twice",
            actor=stock.owner,
        )
    assert err.value.code == "document_already_reversed"


def test_a_reversal_cannot_itself_be_reversed(db: Session, stock: Stock) -> None:
    adjustment = _post_in(db, stock, Decimal(4), Decimal(10))
    reversal = documents_service.reverse_document(
        db, stock.company_id, adjustment.id, on_date=JANUARY, reason="once",
        actor=stock.owner,
    )
    with pytest.raises(LedgerStateError) as err:
        documents_service.reverse_document(
            db, stock.company_id, reversal.id, on_date=JANUARY, reason="undo the undo",
            actor=stock.owner,
        )
    assert err.value.code == "cannot_reverse_a_reversal"


def test_block_refuses_a_reversal_whose_stock_has_since_gone(
    db: Session, stock: Stock
) -> None:
    """Decision 11's clause: a receipt whose quantity has since been issued cannot be reversed
    under `block`, because the reversing move would take the location below zero."""
    receipt = _post_in(db, stock, Decimal(10), Decimal(100))
    documents_service.post_adjustment(
        db,
        stock.company_id,
        _input(stock, _line(stock, type_code="ADJOUT", quantity=Decimal(6))),
        actor=stock.owner,
    )

    with pytest.raises(LedgerStateError) as err:
        documents_service.reverse_document(
            db, stock.company_id, receipt.id, on_date=JANUARY, reason="too late",
            actor=stock.owner,
        )
    assert err.value.code == "insufficient_stock"


def test_allow_lets_that_same_reversal_through(db: Session, stock: Stock) -> None:
    """The policy is the only thing that changes — proving the refusal above is the policy
    talking and not an accident of the reversal path."""
    stock.inventory.settings.negative_stock_policy = NegativeStockPolicy.ALLOW
    db.flush()

    receipt = _post_in(db, stock, Decimal(10), Decimal(100))
    documents_service.post_adjustment(
        db,
        stock.company_id,
        _input(stock, _line(stock, type_code="ADJOUT", quantity=Decimal(6))),
        actor=stock.owner,
    )
    documents_service.reverse_document(
        db, stock.company_id, receipt.id, on_date=JANUARY, reason="allowed",
        actor=stock.owner,
    )

    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert position.quantity == Decimal(-6)
    _both_invariants(db, stock)


# --- Listing ---------------------------------------------------------------------------------


def test_documents_list_newest_first_and_filter_by_type(db: Session, stock: Stock) -> None:
    _post_in(db, stock, Decimal(1), Decimal(10))
    documents_service.post_batch(
        db,
        stock.company_id,
        _input(stock, _line(stock, type_code="OPEN", quantity=Decimal(2), unit_cost=Decimal(10))),
        actor=stock.owner,
    )

    rows, _ = documents_service.list_documents(db, stock.company_id)
    assert [row.doc_type for row in rows] == ["INJN", "INAJ"]

    batches, _ = documents_service.list_documents(db, stock.company_id, doc_type="INJN")
    assert [row.doc_type for row in batches] == ["INJN"]


# --- Lines and moves under a negative-stock crossing -------------------------------------------


def test_a_line_after_a_residue_still_points_at_its_own_move(
    db: Session, stock: Stock
) -> None:
    """The moves a document writes are not the only moves its posting makes.

    Under `allow`, a receipt that covers a negative location settles the guess the earlier
    provisional issue made, and the service raises a *variance move of its own* to carry the
    difference. That move belongs to no keyed line and is interleaved with the ones that do,
    so a document matching lines to moves by counting positions would attribute every line
    after it to the wrong move — quietly, and only for the companies that run `allow`.

    Here line 1 empties the location into deficit, line 2 covers it and raises the residue,
    and line 3 is the line that would be mis-attributed.
    """
    stock.inventory.settings.negative_stock_policy = NegativeStockPolicy.ALLOW
    db.flush()
    _post_in(db, stock, Decimal(5), Decimal(100))

    document, _ = documents_service.post_batch(
        db,
        stock.company_id,
        _input(
            stock,
            _line(stock, type_code="ADJOUT", quantity=Decimal(8)),
            _line(stock, type_code="ADJIN", quantity=Decimal(10), unit_cost=Decimal(130)),
            _line(
                stock,
                type_code="ADJIN",
                quantity=Decimal(2),
                unit_cost=Decimal(70),
                warehouse=stock.depot,
            ),
        ),
        actor=stock.owner,
    )

    lines = documents_service.lines_of(db, stock.company_id, document.id)
    assert len(lines) == 3
    moves = [db.get(StockMove, line.stock_move_id) for line in lines]
    assert all(move is not None for move in moves)

    # Each line points at a move that actually matches what that line said.
    assert moves[0].quantity == Decimal(-8)
    assert moves[0].warehouse_id == stock.main.id
    assert moves[1].quantity == Decimal(10)
    assert moves[1].warehouse_id == stock.main.id
    assert moves[2].quantity == Decimal(2)
    assert moves[2].warehouse_id == stock.depot.id, "the third line is not the residue's move"

    # And the residue really is there, unclaimed by any line — otherwise this test would
    # pass for the wrong reason.
    claimed = {line.stock_move_id for line in lines}
    all_moves = db.scalars(
        select(StockMove).where(
            StockMove.company_id == stock.company_id,
            StockMove.journal_entry_id == document.journal_entry_id,
        )
    ).all()
    residues = [move for move in all_moves if move.id not in claimed]
    assert len(residues) == 1, "the crossing raised exactly one variance move"
    assert residues[0].quantity == Decimal(0)
    _both_invariants(db, stock)


# --- Decision 9: what a batch line may carry ---------------------------------------------------


def test_a_batch_line_carries_its_own_contra_project_and_description(
    db: Session, stock: Stock
) -> None:
    """Decision 9 lists what a batch line keys: item, warehouse, quantity, unit cost,
    transaction type, **contra override, project** and description. The override is the point
    — it is how one batch books two lines of the same type to different accounts without
    defining a transaction type per account.
    """
    override = stock.ledger_account("5100")
    project = stock.inventory.ledger.projects["P-ALPHA"]

    document, _ = documents_service.post_batch(
        db,
        stock.company_id,
        documents_service.DocumentInput(
            document_date=JANUARY,
            description="two lines, two contras",
            lines=(
                documents_service.DocumentLineInput(
                    item_id=stock.item.id,
                    warehouse_id=stock.main.id,
                    quantity=Decimal(4),
                    unit_cost=Decimal(100),
                    transaction_type_id=stock.type_id("ADJIN"),
                    contra_account_id=override,
                    project_id=project.id,
                    description="booked to the override",
                ),
                documents_service.DocumentLineInput(
                    item_id=stock.item.id,
                    warehouse_id=stock.depot.id,
                    quantity=Decimal(2),
                    unit_cost=Decimal(150),
                    transaction_type_id=stock.type_id("ADJIN"),
                    description="booked to the type's own contra",
                ),
            ),
        ),
        actor=stock.owner,
    )

    lines = documents_service.lines_of(db, stock.company_id, document.id)
    assert lines[0].contra_account_id == override
    assert lines[0].project_id == project.id
    assert lines[0].description == "booked to the override"
    assert lines[1].contra_account_id is None

    # The override reached the ledger, and the second line did not follow it there.
    contras = {
        line.gl_account_id
        for line in db.scalars(
            select(JournalLine).where(
                JournalLine.company_id == stock.company_id,
                JournalLine.entry_id == document.journal_entry_id,
            )
        )
        if line.gl_account_id
        not in {stock.ledger_account("1300"), stock.ledger_account("1350")}
    }
    assert override in contras
    assert stock.ledger_account("5200") in contras, "the type's own contra is still used"

    # The project rides through to the move and to the journal line that posted it.
    move = db.get(StockMove, lines[0].stock_move_id)
    assert move is not None
    assert move.project_id == project.id
    _both_invariants(db, stock)
