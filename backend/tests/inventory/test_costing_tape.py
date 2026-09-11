"""The costing tape, at primitive level (P5 step 2).

Nine steps through an RWF company with one stock item and two warehouses, checking the number
every rule produces against a column of **literals worked by hand** — never against anything
the code under test computed. The oracle is in `ORACLE` below and in the comments beside it;
if the implementation and the oracle disagree, the implementation is what changes.

This is the primitive-level run: it drives `receive_stock` / `issue_stock` / `transfer_stock`
directly, because that is all step 2 has. The same tape runs again at document level in step 5,
through adjustments, transfers and a count session, and must produce the identical column.

Row 9 differs from the table in the phase prompt, on the owner's direction at the step-2
review. The prompt had the deficit's mis-costing carried forward into the average (Depot 2 /
334, average 167); the rule now settles it at the moment the deficit is covered — the two units
left are worth what they cost, 300, the average is 150, and the 34 the provisional issue got
wrong leaves as a variance. `docs/` and the step-5 tape carry the same change.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory import stock as stock_service
from app.kernel.errors import LedgerStateError
from app.models.inventory import ItemCostState, NegativeStockPolicy, StockMove
from tests.inventory.conftest import Stock, issue, receive, set_policy, transfer_now
from tests.inventory.invariants import assert_stock_invariants, location_position
from tests.kernel.invariants import assert_ledger_invariants
from tests.subledger.conftest import MARCH

BACKDATED = MARCH - timedelta(days=5)

#: (row, what happened, move value, Main qty, Main value, Depot qty, Depot value, average).
#: Every figure hand-worked; none of it is read back from the code under test.
ORACLE = (
    #  1  opening 10 @ 100                     10 × 100 = 1 000
    ("1", "Opening batch: 10 @ 100, Main", "1000", "10", "1000", "0", "0", "100"),
    #  2  in 10 @ 121                          10 × 121 = 1 210; 2 210 / 20 = 110.5
    ("2", "Adjustment in: 10 @ 121, Main", "1210", "20", "2210", "0", "0", "110.5"),
    #  3  out 7 @ 110.5                        773.5 → 774 half-up; 1 436 / 13 = 110.461538…
    ("3", "Adjustment out: 7, Main", "-774", "13", "1436", "0", "0", "110.461538"),
    #  4  in 5 @ 130 dated before row 3        650; 2 086 / 18 = 115.888888… → 115.888889
    ("4", "Adjustment in (backdated): 5 @ 130", "650", "18", "2086", "0", "0", "115.888889"),
    #  5  transfer 6                           6 × 115.888889 = 695.333334 → 695
    ("5", "Transfer now 6, Main → Depot", "-695", "12", "1391", "6", "695", "115.888889"),
    #  6  out 12, empties Main                 flush: the whole 1 391; 695 / 6 = 115.833333…
    ("6", "Adjustment out: 12, Main (empties)", "-1391", "0", "0", "6", "695", "115.833333"),
    #  7  count variance −1 @ 115.833333       115.833333 → 116; 579 / 5 = 115.8
    ("7", "Count Depot, variance -1", "-116", "0", "0", "5", "579", "115.8"),
    #  8b out 6 under `allow`                  6 × 115.8 = 694.8 → 695; no stock, so the
    #                                          average stays the last positive one
    ("8b", "Adjustment out: 6, Depot (allow)", "-695", "0", "0", "-1", "-116", "115.8"),
    #  9  in 3 @ 150 covers the 1-unit deficit 3 × 150 = 450; two units remain, worth 2 × 150
    #                                          = 300; 334 − 300 = 34 expelled as the variance
    ("9", "Adjustment in: 3 @ 150, Depot", "450", "0", "0", "2", "300", "150"),
)


def _d(text: str) -> Decimal:
    return Decimal(text)


def _plain(value: Decimal) -> str:
    """Trailing zeros off, but never scientific notation: `Decimal("100.000000").normalize()`
    is `1E+2`, which is a true statement and an unreadable tape."""
    rendered = f"{value:f}"
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _average(db: Session, fixture: Stock) -> Decimal:
    return db.scalars(
        select(ItemCostState).where(
            ItemCostState.company_id == fixture.company_id,
            ItemCostState.item_id == fixture.item.id,
        )
    ).one().average_cost


def _row(db: Session, fixture: Stock, label: str, action: str, value: Decimal) -> tuple[str, ...]:
    main = location_position(db, fixture.company_id, fixture.item.id, fixture.main.id)
    depot = location_position(db, fixture.company_id, fixture.item.id, fixture.depot.id)
    assert_stock_invariants(db, fixture.company_id)
    assert_ledger_invariants(db, fixture.company_id)
    return (
        label,
        action,
        _plain(value),
        f"{main.quantity:.0f}",
        f"{main.value:.0f}",
        f"{depot.quantity:.0f}",
        f"{depot.value:.0f}",
        _plain(_average(db, fixture)),
    )


def test_the_costing_tape(db: Session, stock: Stock, capsys: pytest.CaptureFixture) -> None:
    actual: list[tuple[str, ...]] = []

    # 1 ── opening stock, through the inventory journal rather than a GL journal: the
    #      inventory account is a control account, so this is the only door (decision 2).
    step = receive(
        db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH, type_code="OPEN"
    )
    actual.append(_row(db, stock, "1", "Opening batch: 10 @ 100, Main", step.moves[0].value))

    # 2 ── a second receipt at a different cost moves the average.
    step = receive(db, stock, quantity=_d("10"), unit_cost=_d("121"), on=MARCH)
    actual.append(_row(db, stock, "2", "Adjustment in: 10 @ 121, Main", step.moves[0].value))

    # 3 ── an issue at the average, rounded half-up to RWF's zero decimals.
    issued = issue(db, stock, quantity=_d("7"), on=MARCH)
    actual.append(_row(db, stock, "3", "Adjustment out: 7, Main", issued.moves[0].value))

    # 4 ── a receipt dated *before* row 3 but posted after it. Posting order beats date order:
    #      the average moves from here on and row 3 keeps the 774 it was given.
    step = receive(db, stock, quantity=_d("5"), unit_cost=_d("130"), on=BACKDATED)
    db.refresh(issued.moves[0])
    assert issued.moves[0].value == _d("-774"), "an earlier-posted issue was restated"
    actual.append(
        _row(db, stock, "4", "Adjustment in (backdated): 5 @ 130", step.moves[0].value)
    )

    # 5 ── both legs of a transfer in one transaction, through the in-transit warehouse. The
    #      receive leg carries the dispatched value frozen, so nothing is created or lost.
    dispatch, _ = transfer_now(
        db, stock, quantity=_d("6"), source=stock.main, destination=stock.depot, on=MARCH
    )
    actual.append(_row(db, stock, "5", "Transfer now 6, Main → Depot", dispatch.moves[0].value))

    # 6 ── the flush: an issue that empties a location takes everything that location held,
    #      not what the six-decimal average says it should be worth.
    step = issue(db, stock, quantity=_d("12"), on=MARCH)
    actual.append(_row(db, stock, "6", "Adjustment out: 12, Main (empties)", step.moves[0].value))

    # 7 ── a count finds 5 where the system says 6; the variance posts at the current average.
    step = issue(db, stock, quantity=_d("1"), warehouse=stock.depot, on=MARCH, type_code="CNTV")
    actual.append(_row(db, stock, "7", "Count Depot, variance -1", step.moves[0].value))

    # 8a ── the same issue under `block` is refused outright, and nothing is written.
    before = db.scalar(
        select(StockMove.id)
        .where(StockMove.company_id == stock.company_id)
        .order_by(StockMove.sequence_no.desc())
        .limit(1)
    )
    with pytest.raises(LedgerStateError) as excinfo:
        issue(db, stock, quantity=_d("6"), warehouse=stock.depot, on=MARCH)
    assert excinfo.value.code == "insufficient_stock"
    assert (
        db.scalar(
            select(StockMove.id)
            .where(StockMove.company_id == stock.company_id)
            .order_by(StockMove.sequence_no.desc())
            .limit(1)
        )
        == before
    ), "a refused issue left a move behind"

    # 8b ── under `allow` it posts at the last positive average and is flagged for review.
    set_policy(db, stock, NegativeStockPolicy.ALLOW)
    step = issue(db, stock, quantity=_d("6"), warehouse=stock.depot, on=MARCH)
    assert step.moves[0].cost_provisional is True
    actual.append(
        _row(db, stock, "8b", "Adjustment out: 6, Depot (allow)", step.moves[0].value)
    )

    # 9 ── stock arrives and settles the deficit. The two units left are worth what they cost;
    #      the 34 the provisional issue guessed wrong leaves as a variance of its own.
    step = receive(
        db, stock, quantity=_d("3"), unit_cost=_d("150"), warehouse=stock.depot, on=MARCH
    )
    receipt, residue = step.moves
    assert (residue.quantity, residue.value) == (_d("0"), _d("-34"))
    actual.append(_row(db, stock, "9", "Adjustment in: 3 @ 150, Depot", receipt.value))

    header = ("#", "action", "value", "Main qty", "Main val", "Depot qty", "Depot val", "avg")
    with capsys.disabled():
        print("\n\n| " + " | ".join(header) + " |")
        print("|" + "|".join("---" for _ in header) + "|")
        for got, want in zip(actual, ORACLE, strict=True):
            mark = "" if got == want else "  <-- MISMATCH"
            print("| " + " | ".join(got) + " |" + mark)
        print()

    assert actual == list(ORACLE)


def test_the_tape_leaves_the_inventory_account_equal_to_the_stock_it_holds(
    db: Session, stock: Stock
) -> None:
    """The tape's own closing assertion, kept separate so a failure names the right thing:
    after all nine steps the inventory account is exactly the value of the stock behind it,
    and `assert_stock_invariants` has already said so at every date along the way."""
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH, type_code="OPEN")
    receive(db, stock, quantity=_d("10"), unit_cost=_d("121"), on=MARCH)
    issue(db, stock, quantity=_d("7"), on=MARCH)
    receive(db, stock, quantity=_d("5"), unit_cost=_d("130"), on=BACKDATED)
    transfer_now(
        db, stock, quantity=_d("6"), source=stock.main, destination=stock.depot, on=MARCH
    )
    issue(db, stock, quantity=_d("12"), on=MARCH)
    issue(db, stock, quantity=_d("1"), warehouse=stock.depot, on=MARCH, type_code="CNTV")
    set_policy(db, stock, NegativeStockPolicy.ALLOW)
    issue(db, stock, quantity=_d("6"), warehouse=stock.depot, on=MARCH)
    receive(db, stock, quantity=_d("3"), unit_cost=_d("150"), warehouse=stock.depot, on=MARCH)

    settings = stock.inventory.settings
    inventory = stock_service.inventory_account_balance(
        db, stock.company_id, settings.inventory_account_id, as_of=MARCH
    )
    in_transit = stock_service.inventory_account_balance(
        db, stock.company_id, settings.inventory_in_transit_account_id, as_of=MARCH
    )

    assert inventory == _d("300"), "the inventory account is the closing stock value"
    assert in_transit == _d("0"), "nothing is still in transit"
    assert_stock_invariants(db, stock.company_id)
    assert_ledger_invariants(db, stock.company_id)
