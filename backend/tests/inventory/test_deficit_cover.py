"""Covering a deficit: what happens when stock arrives at a location that owes units.

A location goes negative only under the `allow` policy, and only because an issue was costed
at the last positive average — a *guess* at what units that were not on the shelf were worth.
When stock arrives, the guess is settled. The units that were owed cost what this receipt paid
for them, so the location ends holding the **remainder at the receipt's cost**, and the gap
between guess and fact leaves as a variance of its own.

Three shapes, and the rule has to hold for all of them:

* **exact cover** — the receipt lands the quantity on zero, and a location at zero quantity
  holds no value;
* **overshoot** — the receipt covers the deficit and leaves stock behind, which must be worth
  exactly what it cost;
* **partial cover across two receipts** — the first receipt leaves the location still in
  deficit and expels nothing (there is no remainder to rate yet); the second crosses, and
  settles the whole thing.

None of this restates the provisional issue. That move keeps the value it posted and keeps its
`cost_provisional` flag; decision 5 is explicit that a later receipt does not go back and
correct it. What happens here is a *new* fact, recognised on the day the deficit was covered,
which is the first day anyone could have known it.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory import NegativeStockPolicy, StockMove
from tests.inventory.conftest import Stock, issue, receive, set_policy
from tests.inventory.invariants import assert_stock_invariants, location_position
from tests.kernel.invariants import assert_ledger_invariants
from tests.subledger.conftest import MARCH

ZERO = Decimal(0)


def _assert_everything(db: Session, fixture: Stock) -> None:
    assert_stock_invariants(db, fixture.company_id)
    assert_ledger_invariants(db, fixture.company_id)


def _depot(db: Session, fixture: Stock) -> tuple[Decimal, Decimal]:
    position = location_position(db, fixture.company_id, fixture.item.id, fixture.depot.id)
    return position.quantity, position.value


def _owing_one_unit(db: Session, fixture: Stock) -> None:
    """Depot at −1 unit holding −116, the state the tape reaches at row 8b: five units at
    115.8 each, six issued at that average under `allow` (6 × 115.8 = 694.8 → 695)."""
    receive(
        db,
        fixture,
        quantity=Decimal(5),
        unit_cost=Decimal("115.8"),
        warehouse=fixture.depot,
        on=MARCH,
    )
    set_policy(db, fixture, NegativeStockPolicy.ALLOW)
    issue(db, fixture, quantity=Decimal(6), warehouse=fixture.depot, on=MARCH)
    assert _depot(db, fixture) == (Decimal(-1), Decimal(-116))


def test_exact_cover_leaves_the_location_at_nothing(db: Session, stock: Stock) -> None:
    """One unit owed, one unit arrives at 150. The unit cost 150; the issue guessed 116; the
    34 difference is the variance, and the location is empty and worth nothing."""
    _owing_one_unit(db, stock)

    posting = receive(
        db, stock, quantity=Decimal(1), unit_cost=Decimal(150), warehouse=stock.depot, on=MARCH
    )

    receipt, residue = posting.moves
    assert (receipt.quantity, receipt.value) == (Decimal(1), Decimal(150))
    assert (residue.quantity, residue.value) == (ZERO, Decimal(-34))
    assert _depot(db, stock) == (ZERO, ZERO)
    _assert_everything(db, stock)


def test_overshoot_leaves_the_remainder_at_the_receipt_cost(db: Session, stock: Stock) -> None:
    """Tape row 9. One unit owed, three arrive at 150: the deficit is settled and two units
    remain, worth exactly what they cost — 300, not the 334 that carrying the guess forward
    would have left, and an average of 150 rather than 167."""
    _owing_one_unit(db, stock)

    posting = receive(
        db, stock, quantity=Decimal(3), unit_cost=Decimal(150), warehouse=stock.depot, on=MARCH
    )

    receipt, residue = posting.moves
    assert (receipt.quantity, receipt.value) == (Decimal(3), Decimal(450))
    assert (residue.quantity, residue.value) == (ZERO, Decimal(-34))
    assert _depot(db, stock) == (Decimal(2), Decimal(300))
    _assert_everything(db, stock)


def test_partial_cover_expels_nothing_until_the_deficit_is_actually_covered(
    db: Session, stock: Stock
) -> None:
    """Three units owed. Two arrive — still in deficit, so there is no remainder to rate and
    nothing is expelled. Four more arrive and cross: now the location holds three units, worth
    three times what the covering receipt paid."""
    receive(
        db, stock, quantity=Decimal(2), unit_cost=Decimal(100), warehouse=stock.depot, on=MARCH
    )
    set_policy(db, stock, NegativeStockPolicy.ALLOW)
    issue(db, stock, quantity=Decimal(5), warehouse=stock.depot, on=MARCH)
    assert _depot(db, stock) == (Decimal(-3), Decimal(-300))

    first = receive(
        db, stock, quantity=Decimal(2), unit_cost=Decimal(150), warehouse=stock.depot, on=MARCH
    )

    assert len(first.moves) == 1, "a receipt that leaves the location in deficit expels nothing"
    assert _depot(db, stock) == (Decimal(-1), ZERO)
    _assert_everything(db, stock)

    second = receive(
        db, stock, quantity=Decimal(4), unit_cost=Decimal(150), warehouse=stock.depot, on=MARCH
    )

    receipt, residue = second.moves
    assert (receipt.quantity, receipt.value) == (Decimal(4), Decimal(600))
    assert (residue.quantity, residue.value) == (ZERO, Decimal(-150))
    assert _depot(db, stock) == (Decimal(3), Decimal(450))
    _assert_everything(db, stock)


def test_the_provisional_issue_is_never_restated(db: Session, stock: Stock) -> None:
    """Decision 5, held against decision 2's generalisation: the variance is a new move, not
    an edit. The issue keeps its 695 and keeps its flag."""
    _owing_one_unit(db, stock)
    provisional = db.scalars(
        select(StockMove)
        .where(StockMove.company_id == stock.company_id, StockMove.cost_provisional)
        .order_by(StockMove.sequence_no)
    ).first()
    assert provisional.value == Decimal(-695)

    receive(
        db, stock, quantity=Decimal(3), unit_cost=Decimal(150), warehouse=stock.depot, on=MARCH
    )

    db.refresh(provisional)
    assert provisional.value == Decimal(-695)
    assert provisional.cost_provisional is True


def test_a_residue_names_the_document_that_caused_it(db: Session, stock: Stock) -> None:
    """A variance nobody can trace is a number in a P&L with no story. The residue carries the
    source document of the posting that produced it, so the enquiry drills from the variance
    straight back to the receipt that settled the deficit."""
    _owing_one_unit(db, stock)

    posting = receive(
        db, stock, quantity=Decimal(3), unit_cost=Decimal(150), warehouse=stock.depot, on=MARCH
    )

    receipt, residue = posting.moves
    assert residue.journal_entry_id == receipt.journal_entry_id
    assert residue.source_doc_type == receipt.source_doc_type
    assert residue.source_doc_id == receipt.source_doc_id
    assert residue.transaction_type_id == receipt.transaction_type_id
