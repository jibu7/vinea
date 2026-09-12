"""Decision 1, the half of it that is about *quantity*: a move exists because something moved
on a shelf, not because something moved in the ledger.

The two are not the same event. Stock can arrive at no cost — samples, a supplier's
replacement, a promotional case — and it is on the shelf whether or not anyone paid for it. If
the move ledger only recorded what the general ledger had an opinion about, "what is on hand"
would stop being derivable from moves, which is the one thing this rebuild exists to
guarantee.

So: **moves post regardless of value; journal lines exist only for non-zero values; and a
posting in which nothing values produces no entry at all.** An entry with no lines is not an
entry, and the Posting Engine will not write a zero-amount line — correctly, because the
ledger genuinely has nothing to say.

The single exception is a revaluation of zero, which is the one posting that moves nothing in
either ledger: it carries no quantity by definition, so with no value it is not a document,
it is a no-op. That is the only thing `zero_value_posting` refuses.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory import documents as documents_service
from app.inventory import stock as stock_service
from app.kernel.errors import PostingError
from app.kernel.sequences import DocType
from app.models.inventory import StockMove
from tests.inventory.conftest import Stock, document, issue, receive
from tests.inventory.invariants import assert_stock_invariants, location_position
from tests.kernel.invariants import assert_ledger_invariants
from tests.subledger.conftest import MARCH

ZERO = Decimal(0)


def _assert_everything(db: Session, fixture: Stock) -> None:
    assert_stock_invariants(db, fixture.company_id)
    assert_ledger_invariants(db, fixture.company_id)


def test_a_free_sample_receipt_moves_stock_and_posts_no_entry(
    db: Session, stock: Stock
) -> None:
    """Twelve promotional bottles arrive at no charge. They are on the shelf; the ledger has
    nothing to record; the move ledger records all of it."""
    posting = receive(db, stock, quantity=Decimal(12), unit_cost=ZERO, on=MARCH)

    assert posting.entry is None, "a valueless posting has nothing to post"
    move = posting.moves[0]
    assert (move.quantity, move.value) == (Decimal(12), ZERO)
    assert move.journal_entry_id is None and move.journal_line_id is None
    assert move.unit_cost == ZERO

    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert (position.quantity, position.value) == (Decimal(12), ZERO)
    _assert_everything(db, stock)


def test_an_issue_whose_value_rounds_away_still_moves_the_quantity(
    db: Session, stock: Stock
) -> None:
    """RWF has no minor unit, so an item averaging 0.04 a unit is worth nothing per unit to
    the ledger. One unit leaving is still one unit leaving."""
    receive(db, stock, quantity=Decimal(1000), unit_cost=Decimal("0.04"), on=MARCH)
    opening = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert (opening.quantity, opening.value) == (Decimal(1000), Decimal(40))

    posting = issue(db, stock, quantity=Decimal(1), on=MARCH)

    assert posting.entry is None
    assert (posting.moves[0].quantity, posting.moves[0].value) == (Decimal(-1), ZERO)
    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert (position.quantity, position.value) == (Decimal(999), Decimal(40))
    _assert_everything(db, stock)


def test_a_count_variance_on_a_zero_cost_item_still_corrects_the_quantity(
    db: Session, stock: Stock
) -> None:
    """The case that matters operationally: a count finds one of the free samples missing.
    The shelf is wrong and must be corrected; the valuation is right and must not move."""
    receive(db, stock, quantity=Decimal(12), unit_cost=ZERO, on=MARCH)

    variance = issue(db, stock, quantity=Decimal(1), on=MARCH, type_code="CNTV")

    assert variance.entry is None
    assert (variance.moves[0].quantity, variance.moves[0].value) == (Decimal(-1), ZERO)
    position = location_position(db, stock.company_id, stock.item.id, stock.main.id)
    assert (position.quantity, position.value) == (Decimal(11), ZERO)
    _assert_everything(db, stock)


def test_a_posting_with_one_valued_line_and_one_valueless_line_posts_only_the_valued_one(
    db: Session, stock: Stock
) -> None:
    """The mixed case. The entry carries exactly the lines that moved value; the move ledger
    carries both moves, and the valueless one points at no line."""
    receive(db, stock, quantity=Decimal(5), unit_cost=Decimal(200), warehouse=stock.depot, on=MARCH)

    posting = stock_service.receive_stock(
        db,
        stock.company_id,
        document=document(
            DocType.INV_JOURNAL, MARCH, transaction_type_id=stock.type_id("ADJIN")
        ),
        lines=[
            stock_service.StockLine(
                item_id=stock.item.id,
                warehouse_id=stock.main.id,
                quantity=Decimal(3),
                unit_cost=ZERO,
            ),
            stock_service.StockLine(
                item_id=stock.item.id,
                warehouse_id=stock.depot.id,
                quantity=Decimal(2),
                unit_cost=Decimal(150),
            ),
        ],
        actor=stock.owner,
    )

    assert posting.entry is not None
    free, paid = posting.moves
    assert (free.value, free.journal_line_id) == (ZERO, None)
    assert paid.value == Decimal(300)
    assert paid.journal_line_id is not None
    # One inventory line and one contra line — the free bottles are not in the entry at all.
    assert len(posting.entry.lines) == 2
    _assert_everything(db, stock)


def test_a_revaluation_of_zero_is_the_one_posting_that_is_refused(
    db: Session, stock: Stock
) -> None:
    """A revaluation carries no quantity by definition. With no value either it is not a
    document, it is a no-op, and `zero_value_posting` says so."""
    receive(db, stock, quantity=Decimal(5), unit_cost=Decimal(200), on=MARCH)

    with pytest.raises(PostingError) as excinfo:
        stock_service.revalue_stock(
            db,
            stock.company_id,
            document=document(
                DocType.INV_ADJUSTMENT, MARCH, transaction_type_id=stock.type_id("REVAL")
            ),
            lines=[
                stock_service.StockLine(
                    item_id=stock.item.id, warehouse_id=stock.main.id, value=ZERO
                )
            ],
            actor=stock.owner,
        )

    assert excinfo.value.code == "zero_value_posting"
    assert db.scalar(
        select(StockMove.id).where(
            StockMove.company_id == stock.company_id, StockMove.quantity == ZERO
        )
    ) is None
    db.rollback()


def test_a_revaluation_of_a_location_holding_no_stock_is_refused(
    db: Session, stock: Stock
) -> None:
    """Found by the property test, once valueless postings started writing moves.

    A revaluation restates what stock is *carried at*. Against a location holding none, the
    value has nowhere to sit: the location would be worth something while holding nothing,
    which no valuation report can render and no average can explain. Refused at the line, so
    the operator is told which warehouse is empty rather than shown a figure that cannot be
    right.
    """
    receive(db, stock, quantity=Decimal(5), unit_cost=Decimal(200), on=MARCH)

    with pytest.raises(PostingError) as excinfo:
        stock_service.revalue_stock(
            db,
            stock.company_id,
            document=document(
                DocType.INV_ADJUSTMENT, MARCH, transaction_type_id=stock.type_id("REVAL")
            ),
            lines=[
                stock_service.StockLine(
                    item_id=stock.item.id,
                    warehouse_id=stock.depot.id,  # never received anything
                    value=Decimal(75),
                )
            ],
            actor=stock.owner,
        )

    assert excinfo.value.code == "nothing_to_revalue"
    assert excinfo.value.field_errors == {"lines.0.value": ["the location holds no stock"]}
    db.rollback()


def test_a_revaluation_of_a_location_that_holds_stock_is_accepted(
    db: Session, stock: Stock
) -> None:
    """The positive half, so the refusal above is a rule and not a blanket ban."""
    receive(db, stock, quantity=Decimal(5), unit_cost=Decimal(200), warehouse=stock.depot, on=MARCH)

    posting = stock_service.revalue_stock(
        db,
        stock.company_id,
        document=document(
            DocType.INV_ADJUSTMENT, MARCH, transaction_type_id=stock.type_id("REVAL")
        ),
        lines=[
            stock_service.StockLine(
                item_id=stock.item.id, warehouse_id=stock.depot.id, value=Decimal(-250)
            )
        ],
        actor=stock.owner,
    )

    assert posting.entry is not None
    position = location_position(db, stock.company_id, stock.item.id, stock.depot.id)
    assert (position.quantity, position.value) == (Decimal(5), Decimal(750))
    _assert_everything(db, stock)


def test_a_valueless_document_and_a_valued_one_share_one_gapless_run(
    db: Session, stock: Stock
) -> None:
    """The numbering half of decision 1, which step 3 stated and nothing tested.

    A valueless posting has no entry to take a number from, so the document claims one itself
    — and the next valued document's entry takes the one after it. Both numbers are in the
    `ADJ-` run, neither is skipped, and neither belongs to two things.

    This is a regression test for a real failure: `assert_ledger_invariants` counted only the
    numbers on *journal entries*, so a company with one zero-cost adjustment and one ordinary
    one failed the standing acceptance contract with "gap in INAJ: [2]" — the ledger was
    correct and the checker's model of it was a version behind. Found while building step 4,
    whose transfers claim numbers the same way.
    """
    free, _ = documents_service.post_adjustment(
        db,
        stock.company_id,
        documents_service.DocumentInput(
            document_date=MARCH,
            description="promotional cases, no charge",
            lines=(
                documents_service.DocumentLineInput(
                    item_id=stock.item.id,
                    warehouse_id=stock.main.id,
                    quantity=Decimal(12),
                    unit_cost=ZERO,
                    transaction_type_id=stock.type_id("ADJIN"),
                ),
            ),
        ),
        actor=stock.owner,
    )
    paid, _ = documents_service.post_adjustment(
        db,
        stock.company_id,
        documents_service.DocumentInput(
            document_date=MARCH,
            description="stock bought and paid for",
            lines=(
                documents_service.DocumentLineInput(
                    item_id=stock.item.id,
                    warehouse_id=stock.main.id,
                    quantity=Decimal(5),
                    unit_cost=Decimal(100),
                    transaction_type_id=stock.type_id("ADJIN"),
                ),
            ),
        ),
        actor=stock.owner,
    )

    assert free.journal_entry_id is None
    assert paid.journal_entry_id is not None
    assert (free.number, paid.number) == ("ADJ-000001", "ADJ-000002")
    _assert_everything(db, stock)
