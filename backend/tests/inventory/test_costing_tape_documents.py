"""The costing tape, at document level (P5 step 5) — the phase's acceptance test.

The same nine steps as `test_costing_tape.py`, against the same column of hand-worked
literals, but driven through the things an operator actually uses: an opening **journal
batch**, **adjustments**, a **transfer now**, and a **count session** that is snapshotted,
counted and processed. Step 2 proved the engine computes these numbers. This proves the
documents on top of it do not quietly change any of them on the way — and that the step-5
reports print the same figures back.

`ORACLE` is copied from the step-2 tape deliberately, literal for literal. If the two files
ever disagree, a document has started costing differently from the primitive underneath it,
which is precisely the defect this file exists to catch; the fix is never to edit the column.

Row 9 differs from the table in the phase prompt on the owner's direction at the step-2
review — the deficit settles when it is covered rather than being carried into the average.
`docs/` and the step-2 tape carry the same change.

After **every** row this file asserts, beyond the seven tape figures:

* `assert_stock_invariants` and `assert_ledger_invariants`;
* the inventory account equals the value of the stock behind it, and per branch — Main's
  branch against Main's value, Musanze's against Depot's;
* the **valuation report** as at the date agrees with both, account by account, which is the
  step-5 deliverable and the thing rule 13 says is not done until a test has opened it with
  data in it.
"""

from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.inventory import counts as count_service
from app.inventory import documents as documents_service
from app.inventory import reports
from app.inventory import stock as stock_service
from app.inventory import transfers as transfer_service
from app.kernel.errors import LedgerStateError
from app.models.inventory import (
    InventoryDocument,
    ItemCostState,
    NegativeStockPolicy,
    StockCountStatus,
    StockMove,
)
from tests.inventory.conftest import Stock, set_policy
from tests.inventory.invariants import assert_stock_invariants, location_position
from tests.kernel.invariants import assert_ledger_invariants
from tests.subledger.conftest import MARCH

BACKDATED = MARCH - timedelta(days=5)

#: (row, what happened, move value, Main qty, Main value, Depot qty, Depot value, average).
#: Every figure hand-worked; none of it is read back from the code under test. Identical to
#: the step-2 tape's column, on purpose.
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
    """Trailing zeros off, but never scientific notation — see the step-2 tape."""
    rendered = f"{value:f}"
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


# --- Driving the documents ---------------------------------------------------------------------


def _line(
    fixture: Stock,
    *,
    type_code: str,
    quantity: Decimal,
    warehouse=None,
    unit_cost: Decimal | None = None,
) -> documents_service.DocumentLineInput:
    return documents_service.DocumentLineInput(
        item_id=fixture.item.id,
        warehouse_id=(warehouse or fixture.main).id,
        quantity=quantity,
        unit_cost=unit_cost,
        transaction_type_id=fixture.type_id(type_code),
    )


def _adjust(
    db: Session,
    fixture: Stock,
    *,
    type_code: str,
    quantity: Decimal,
    warehouse=None,
    unit_cost: Decimal | None = None,
    on=MARCH,
) -> InventoryDocument:
    document, _ = documents_service.post_adjustment(
        db,
        fixture.company_id,
        documents_service.DocumentInput(
            document_date=on,
            description=f"{type_code} {quantity}",
            lines=[
                _line(
                    fixture,
                    type_code=type_code,
                    quantity=quantity,
                    warehouse=warehouse,
                    unit_cost=unit_cost,
                )
            ],
        ),
        actor=fixture.owner,
    )
    return document


def _watermark(db: Session, fixture: Stock) -> int:
    return stock_service.posting_watermark(db, fixture.company_id)


def _moves_since(db: Session, fixture: Stock, watermark: int) -> list[StockMove]:
    return list(
        db.scalars(
            select(StockMove)
            .where(
                StockMove.company_id == fixture.company_id,
                StockMove.sequence_no > watermark,
            )
            .order_by(StockMove.sequence_no)
        )
    )


def _average(db: Session, fixture: Stock) -> Decimal:
    return (
        db.scalars(
            select(ItemCostState).where(
                ItemCostState.company_id == fixture.company_id,
                ItemCostState.item_id == fixture.item.id,
            )
        )
        .one()
        .average_cost
    )


# --- The per-row checks --------------------------------------------------------------------------


def _check_the_ledger_agrees(db: Session, fixture: Stock) -> None:
    """The inventory accounts equal the stock behind them — in total, per branch, and as the
    valuation report prints it. Three ways of asking the same question, because the defect
    class this guards against is a report that reads the right table and shows the wrong
    column."""
    assert_stock_invariants(db, fixture.company_id)
    assert_ledger_invariants(db, fixture.company_id)

    settings = fixture.inventory.settings
    inventory_account = settings.inventory_account_id
    transit_account = settings.inventory_in_transit_account_id
    main = location_position(db, fixture.company_id, fixture.item.id, fixture.main.id)
    depot = location_position(db, fixture.company_id, fixture.item.id, fixture.depot.id)
    transit = location_position(db, fixture.company_id, fixture.item.id, fixture.transit.id)

    assert stock_service.inventory_account_balance(
        db, fixture.company_id, inventory_account, as_of=MARCH
    ) == main.value + depot.value, "the inventory account is not the stock it holds"
    assert (
        stock_service.inventory_account_balance(
            db, fixture.company_id, transit_account, as_of=MARCH
        )
        == transit.value
    ), "the in-transit account is not the stock on the road"

    for branch_id, expected in (
        (fixture.main.branch_id, main.value),
        (fixture.depot.branch_id, depot.value),
    ):
        assert (
            stock_service.inventory_account_balance(
                db, fixture.company_id, inventory_account, as_of=MARCH, branch_id=branch_id
            )
            == expected
        ), f"branch {branch_id} holds stock the branch's GL balance does not agree with"

    report = reports.valuation_report(db, fixture.company_id, as_of=MARCH)
    totals = {total.gl_account_id: total.value for total in report.account_totals}
    assert totals.get(inventory_account, Decimal(0)) == main.value + depot.value
    assert totals.get(transit_account, Decimal(0)) == transit.value
    assert report.total_value == main.value + depot.value + transit.value


def _row(
    db: Session, fixture: Stock, label: str, action: str, value: Decimal
) -> tuple[str, ...]:
    main = location_position(db, fixture.company_id, fixture.item.id, fixture.main.id)
    depot = location_position(db, fixture.company_id, fixture.item.id, fixture.depot.id)
    _check_the_ledger_agrees(db, fixture)
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


def _print_tape(
    capsys: pytest.CaptureFixture, actual: Sequence[tuple[str, ...]]
) -> None:
    header = ("#", "action", "value", "Main qty", "Main val", "Depot qty", "Depot val", "avg")
    with capsys.disabled():
        print("\n\n| " + " | ".join(header) + " |")
        print("|" + "|".join("---" for _ in header) + "|")
        for got, want in zip(actual, ORACLE, strict=True):
            mark = "" if got == want else "  <-- MISMATCH"
            print("| " + " | ".join(got) + " |" + mark)
        print()


# --- The tape ------------------------------------------------------------------------------------


def test_the_costing_tape_through_documents(
    db: Session, stock: Stock, capsys: pytest.CaptureFixture
) -> None:
    actual: list[tuple[str, ...]] = []

    # 1 ── opening stock through the **journal batch** (decision 2 and decision 9). The
    #      inventory account is a control account, so a GL journal cannot reach it: this
    #      endpoint is the go-live door and the batch is the document that opens it.
    watermark = _watermark(db, stock)
    opening, _ = documents_service.post_batch(
        db,
        stock.company_id,
        documents_service.DocumentInput(
            document_date=MARCH,
            description="Opening stock",
            lines=[
                _line(stock, type_code="OPEN", quantity=_d("10"), unit_cost=_d("100")),
            ],
        ),
        actor=stock.owner,
        idempotency_key="tape-opening",
    )
    assert opening.number.startswith("IJN-"), "a batch takes a number from the batch run"
    moves = _moves_since(db, stock, watermark)
    actual.append(_row(db, stock, "1", "Opening batch: 10 @ 100, Main", moves[0].value))

    # 2 ── a second receipt at a different cost moves the average.
    watermark = _watermark(db, stock)
    _adjust(db, stock, type_code="ADJIN", quantity=_d("10"), unit_cost=_d("121"))
    moves = _moves_since(db, stock, watermark)
    actual.append(_row(db, stock, "2", "Adjustment in: 10 @ 121, Main", moves[0].value))

    # 3 ── an issue at the average, rounded half-up to RWF's zero decimals.
    watermark = _watermark(db, stock)
    issued = _adjust(db, stock, type_code="ADJOUT", quantity=_d("7"))
    issue_move = _moves_since(db, stock, watermark)[0]
    actual.append(_row(db, stock, "3", "Adjustment out: 7, Main", issue_move.value))

    # 4 ── a receipt *dated* before row 3 but *posted* after it. Posting order beats date
    #      order: the average moves from here on and row 3 keeps the 774 it was given.
    watermark = _watermark(db, stock)
    _adjust(
        db, stock, type_code="ADJIN", quantity=_d("5"), unit_cost=_d("130"), on=BACKDATED
    )
    db.refresh(issue_move)
    assert issue_move.value == _d("-774"), "an earlier-posted issue was restated"
    assert issued.status.value == "posted"
    moves = _moves_since(db, stock, watermark)
    actual.append(_row(db, stock, "4", "Adjustment in (backdated): 5 @ 130", moves[0].value))

    # 5 ── the default UI action: both legs in one transaction, through the in-transit
    #      warehouse. The receive leg carries the dispatched value frozen.
    watermark = _watermark(db, stock)
    transfer, _ = transfer_service.post_transfer(
        db,
        stock.company_id,
        transfer_service.TransferInput(
            transfer_date=MARCH,
            description="Main to Depot",
            from_warehouse_id=stock.main.id,
            to_warehouse_id=stock.depot.id,
            lines=[transfer_service.TransferLineInput(item_id=stock.item.id, quantity=_d("6"))],
        ),
        receive_now=True,
        actor=stock.owner,
        idempotency_key="tape-transfer",
    )
    legs = _moves_since(db, stock, watermark)
    assert len(legs) == 4, "transfer now is two postings of two moves, not one of two"
    assert sum(move.value for move in legs) == _d("0"), "a transfer creates no value"
    actual.append(_row(db, stock, "5", "Transfer now 6, Main → Depot", legs[0].value))

    # 6 ── the flush: an issue that empties a location takes everything that location held,
    #      not what the six-decimal average says it should be worth.
    watermark = _watermark(db, stock)
    _adjust(db, stock, type_code="ADJOUT", quantity=_d("12"))
    moves = _moves_since(db, stock, watermark)
    actual.append(_row(db, stock, "6", "Adjustment out: 12, Main (empties)", moves[0].value))

    # 7 ── a real count session on Depot: snapshot 6, count 5, process. One variance document,
    #      costed at the current average, and the session completes linked to it.
    watermark = _watermark(db, stock)
    session = count_service.open_session(
        db,
        stock.company_id,
        count_service.CountSessionInput(
            warehouse_id=stock.depot.id,
            count_date=MARCH,
            description="Depot count",
        ),
        actor=stock.owner,
    )
    line = count_service.lines_of(db, stock.company_id, session.id)[0]
    assert line.system_quantity == _d("6"), "the sheet froze what the books held"
    count_service.enter_count(
        db, stock.company_id, session.id, line.id, quantity=_d("5"), actor=stock.owner
    )
    preview = count_service.preview(db, stock.company_id, session.id)
    assert preview.can_process is True
    assert preview.total_value == _d("-116"), (
        "the preview promises what Process will post, or it is decoration"
    )
    session, variance_document, _ = count_service.process_session(
        db, stock.company_id, session.id, actor=stock.owner, idempotency_key="tape-count"
    )
    assert session.status == StockCountStatus.COMPLETED
    assert variance_document is not None
    moves = _moves_since(db, stock, watermark)
    assert moves[0].value == preview.total_value, "Process posted something else than it showed"
    actual.append(_row(db, stock, "7", "Count Depot, variance -1", moves[0].value))

    # 8a ── the same issue under `block` is refused outright, and nothing is written: no move,
    #       no journal entry, and no document either.
    before_moves = _move_count(db, stock)
    before_documents = _document_count(db, stock)
    with pytest.raises(LedgerStateError) as excinfo:
        _adjust(db, stock, type_code="ADJOUT", quantity=_d("6"), warehouse=stock.depot)
    assert excinfo.value.code == "insufficient_stock"
    assert "lines.0.quantity" in excinfo.value.field_errors, (
        "the refusal lands on the quantity cell the step-7 grid binds to"
    )
    assert _move_count(db, stock) == before_moves, "a refused issue left a move behind"
    assert _document_count(db, stock) == before_documents, (
        "a refused issue left a document behind"
    )

    # 8b ── under `allow` it posts at the last positive average and is flagged for review.
    set_policy(db, stock, NegativeStockPolicy.ALLOW)
    watermark = _watermark(db, stock)
    _adjust(db, stock, type_code="ADJOUT", quantity=_d("6"), warehouse=stock.depot)
    provisional = _moves_since(db, stock, watermark)[0]
    assert provisional.cost_provisional is True
    actual.append(_row(db, stock, "8b", "Adjustment out: 6, Depot (allow)", provisional.value))

    # 9 ── stock arrives and settles the deficit. The two units left are worth what they cost;
    #      the 34 the provisional issue guessed wrong leaves as a variance of its own, and the
    #      provisional move is *not* restated.
    watermark = _watermark(db, stock)
    _adjust(
        db,
        stock,
        type_code="ADJIN",
        quantity=_d("3"),
        unit_cost=_d("150"),
        warehouse=stock.depot,
    )
    receipt, residue = _moves_since(db, stock, watermark)
    assert (residue.quantity, residue.value) == (_d("0"), _d("-34"))
    db.refresh(provisional)
    assert provisional.value == _d("-695"), "covering the deficit restated an earlier issue"
    actual.append(_row(db, stock, "9", "Adjustment in: 3 @ 150, Depot", receipt.value))

    _print_tape(capsys, actual)
    assert actual == list(ORACLE)


def _move_count(db: Session, fixture: Stock) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(StockMove)
            .where(StockMove.company_id == fixture.company_id)
        )
    )


def _document_count(db: Session, fixture: Stock) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(InventoryDocument)
            .where(InventoryDocument.company_id == fixture.company_id)
        )
    )


def test_the_reports_print_the_tape_back(db: Session, stock: Stock) -> None:
    """Rule 13, applied to the tape: run it, then read the closing position off each of the
    step-5 reports rather than off the caches, and check they all say the same thing.

    This is the test that would have caught the P4 defect class — a report reading the wrong
    field shows a number that is internally consistent and wrong. Here the valuation report,
    the movement report's closing column, the transaction report's total and the item enquiry
    are each derived differently from the same moves, so agreeing is evidence.
    """
    _run_the_tape(db, stock)
    settings = stock.inventory.settings

    valuation = reports.valuation_report(db, stock.company_id, as_of=MARCH)
    movement = reports.movement_report(
        db, stock.company_id, date_from=BACKDATED, date_to=MARCH
    )
    transactions = reports.transaction_report(
        db, stock.company_id, date_from=BACKDATED, date_to=MARCH
    )

    # Closing: Main empty, Depot 2 units worth 300, nothing in transit.
    by_warehouse = {row.warehouse_code: row for row in valuation.rows}
    assert set(by_warehouse) == {"DEPOT"}, "a location holding nothing is off the report"
    assert (by_warehouse["DEPOT"].quantity, by_warehouse["DEPOT"].value) == (
        _d("2"),
        _d("300"),
    )
    assert valuation.total_value == _d("300")
    assert {total.gl_account_id: total.value for total in valuation.account_totals} == {
        settings.inventory_account_id: _d("300"),
        settings.inventory_in_transit_account_id: _d("0"),
    }

    # The movement report over the whole history closes where the valuation report values.
    assert movement.opening_value == _d("0")
    assert movement.closing_value == valuation.total_value
    closing = {
        (row.item_id, row.warehouse_id): (row.closing_quantity, row.closing_value)
        for row in movement.rows
    }
    assert closing[(stock.item.id, stock.depot.id)] == (_d("2"), _d("300"))
    assert closing[(stock.item.id, stock.main.id)] == (_d("0"), _d("0"))

    # Every move ever posted sums to the closing value — the report's own restatement of
    # "the moves are the truth".
    assert transactions.total_value == valuation.total_value
    assert transactions.total_quantity == _d("2")

    # The moves costed against thin air, and the correction they made necessary: the issue
    # that guessed at 115.8 and the residue that expelled the 34 it got wrong. Both carry the
    # flag, because both are the review trail for the same guess — the residue exists only
    # because the issue did.
    flagged = reports.transaction_report(
        db,
        stock.company_id,
        date_from=BACKDATED,
        date_to=MARCH,
        provisional_only=True,
    )
    assert flagged.move_count == 2
    assert [row.value for row in flagged.rows] == [_d("-695"), _d("-34")]
    assert flagged.total_value == _d("-729")

    # And the count session that ran in row 7 is on the count report with its document.
    count = reports.count_report(db, stock.company_id)
    assert count.rows[0].status == StockCountStatus.COMPLETED
    assert count.rows[0].variance_count == 1
    assert count.rows[0].lines[0].variance == _d("-1")
    assert count.rows[0].document_number is not None
    assert count.rows[0].journal_entry_id is not None


def test_the_enquiry_walks_the_tape_from_the_first_move_to_the_last(
    db: Session, stock: Stock
) -> None:
    """The item enquiry, opened on the item the tape moved, with the running column checked
    against the tape's own Depot figures."""
    from app.inventory import enquiries

    _run_the_tape(db, stock)

    enquiry = enquiries.item_enquiry(
        db, stock.company_id, stock.item.id, as_of=MARCH, warehouse_id=stock.depot.id
    )

    assert [_plain(move.running_value) for move in enquiry.moves] == [
        "695",  # the transfer arrives
        "579",  # the count variance
        "-116",  # the provisional issue takes Depot negative
        "334",  # the receipt at 150
        "300",  # the residue leaves, settling the deficit
    ]
    assert enquiry.moves[2].cost_provisional is True
    assert enquiry.total_quantity == _d("2")
    assert enquiry.total_value == _d("300")
    assert enquiry.average_cost == _d("150")
    assert all(move.entry_number for move in enquiry.moves), (
        "every valued move drills to the entry that carried it"
    )


def _run_the_tape(db: Session, fixture: Stock) -> None:
    """The nine rows, without the assertions — for the tests that check what they left behind."""
    documents_service.post_batch(
        db,
        fixture.company_id,
        documents_service.DocumentInput(
            document_date=MARCH,
            description="Opening stock",
            lines=[_line(fixture, type_code="OPEN", quantity=_d("10"), unit_cost=_d("100"))],
        ),
        actor=fixture.owner,
    )
    _adjust(db, fixture, type_code="ADJIN", quantity=_d("10"), unit_cost=_d("121"))
    _adjust(db, fixture, type_code="ADJOUT", quantity=_d("7"))
    _adjust(
        db, fixture, type_code="ADJIN", quantity=_d("5"), unit_cost=_d("130"), on=BACKDATED
    )
    transfer_service.post_transfer(
        db,
        fixture.company_id,
        transfer_service.TransferInput(
            transfer_date=MARCH,
            description="Main to Depot",
            from_warehouse_id=fixture.main.id,
            to_warehouse_id=fixture.depot.id,
            lines=[transfer_service.TransferLineInput(item_id=fixture.item.id, quantity=_d("6"))],
        ),
        receive_now=True,
        actor=fixture.owner,
    )
    _adjust(db, fixture, type_code="ADJOUT", quantity=_d("12"))
    session = count_service.open_session(
        db,
        fixture.company_id,
        count_service.CountSessionInput(
            warehouse_id=fixture.depot.id, count_date=MARCH, description="Depot count"
        ),
        actor=fixture.owner,
    )
    line = count_service.lines_of(db, fixture.company_id, session.id)[0]
    count_service.enter_count(
        db, fixture.company_id, session.id, line.id, quantity=_d("5"), actor=fixture.owner
    )
    count_service.process_session(db, fixture.company_id, session.id, actor=fixture.owner)
    set_policy(db, fixture, NegativeStockPolicy.ALLOW)
    _adjust(db, fixture, type_code="ADJOUT", quantity=_d("6"), warehouse=fixture.depot)
    _adjust(
        db,
        fixture,
        type_code="ADJIN",
        quantity=_d("3"),
        unit_cost=_d("150"),
        warehouse=fixture.depot,
    )
