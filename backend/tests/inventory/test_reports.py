"""P5 step 5 — the four inventory reports.

The load-bearing test in this file is
`test_the_valuation_report_equals_the_inventory_account_at_every_date`: §6 invariant 3 as a
*report-level* property, not only a suite-level one. `assert_stock_invariants` already proves
the moves agree with the ledger; this proves the thing an auditor actually opens agrees with
it too, which is a different claim — a report can read the right table and still show the
wrong number, and P4 shipped six screens that did exactly that.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.inventory import counts as count_service
from app.inventory import masters, reports
from app.inventory import stock as stock_service
from app.inventory import transfers as transfer_service
from app.models.inventory import NegativeStockPolicy, StockCountStatus
from tests.inventory.conftest import Stock, issue, receive, set_policy, transfer_now
from tests.inventory.invariants import assert_stock_invariants
from tests.kernel.invariants import assert_ledger_invariants
from tests.subledger.conftest import MARCH

BEFORE = MARCH - timedelta(days=5)
AFTER = MARCH + timedelta(days=5)


def _d(text: str) -> Decimal:
    return Decimal(text)


def _second_item(db: Session, fixture: Stock, code: str = "WINE-375"):
    return masters.create_item(
        db,
        fixture.company_id,
        masters.ItemInput(
            code=code,
            name=f"Rugari Red {code}",
            uom_category_id=fixture.inventory.count.id,
            base_uom_id=fixture.inventory.each.id,
        ),
        actor=fixture.owner,
    )


def _receive_item(db: Session, fixture: Stock, item, *, quantity, unit_cost, warehouse, on):
    return stock_service.receive_stock(
        db,
        fixture.company_id,
        document=stock_service.StockDocument(
            doc_type="INAJ",
            move_date=on,
            description="stock in",
            transaction_type_id=fixture.type_id("ADJIN"),
        ),
        lines=[
            stock_service.StockLine(
                item_id=item.id,
                warehouse_id=warehouse.id,
                quantity=quantity,
                unit_cost=unit_cost,
            )
        ],
        actor=fixture.owner,
    )


# --- Valuation ---------------------------------------------------------------------------------


def test_the_valuation_report_values_each_location_at_its_frozen_values(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    receive(db, stock, quantity=_d("10"), unit_cost=_d("121"), on=MARCH)
    transfer_now(
        db, stock, quantity=_d("6"), source=stock.main, destination=stock.depot, on=MARCH
    )

    report = reports.valuation_report(db, stock.company_id, as_of=MARCH)

    by_warehouse = {row.warehouse_code: row for row in report.rows}
    # 6 × 110.5 = 663 exactly; Main keeps the rest of the 2 210.
    assert by_warehouse["DEPOT"].quantity == _d("6")
    assert by_warehouse["DEPOT"].value == _d("663")
    assert by_warehouse["MAIN"].quantity == _d("14")
    assert by_warehouse["MAIN"].value == _d("1547")
    assert report.total_value == _d("2210")
    assert by_warehouse["MAIN"].unit_cost == _d("1547") / _d("14")


def test_the_valuation_report_equals_the_inventory_account_at_every_date(
    db: Session, stock: Stock
) -> None:
    """The Definition of Done, read off the report rather than off the moves.

    Every date anything happened on, twice over: the company total against the inventory
    account, and each branch's total against the same account filtered to that branch. A
    transfer left half-received on purpose, so in-transit stock is in the sample.
    """
    settings = stock.inventory.settings
    inventory_account = settings.inventory_account_id
    transit_account = settings.inventory_in_transit_account_id
    main_branch = stock.main.branch_id
    depot_branch = stock.depot.branch_id

    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=BEFORE)
    receive(db, stock, quantity=_d("10"), unit_cost=_d("121"), on=MARCH)
    issue(db, stock, quantity=_d("7"), on=MARCH)
    transfer_now(
        db, stock, quantity=_d("6"), source=stock.main, destination=stock.depot, on=MARCH
    )
    # Dispatched and not received: value sitting in the in-transit account on AFTER.
    transfer_service.post_transfer(
        db,
        stock.company_id,
        transfer_service.TransferInput(
            transfer_date=AFTER,
            description="still on the road",
            from_warehouse_id=stock.main.id,
            to_warehouse_id=stock.depot.id,
            lines=[transfer_service.TransferLineInput(item_id=stock.item.id, quantity=_d("2"))],
        ),
        receive_now=False,
        actor=stock.owner,
    )
    assert_stock_invariants(db, stock.company_id)
    assert_ledger_invariants(db, stock.company_id)

    for as_of in (BEFORE, BEFORE + timedelta(days=1), MARCH, MARCH + timedelta(days=1), AFTER):
        report = reports.valuation_report(db, stock.company_id, as_of=as_of)
        totals = {total.gl_account_id: total.value for total in report.account_totals}
        assert totals.get(inventory_account, Decimal(0)) == (
            stock_service.inventory_account_balance(
                db, stock.company_id, inventory_account, as_of=as_of
            )
        ), f"the valuation report and the inventory account disagree on {as_of}"
        assert totals.get(transit_account, Decimal(0)) == (
            stock_service.inventory_account_balance(
                db, stock.company_id, transit_account, as_of=as_of
            )
        ), f"the in-transit account and the stock on the road disagree on {as_of}"
        assert report.total_value == sum(
            total.value for total in report.account_totals
        ), "the report's own total is not the sum of the accounts it ties to"

        for branch_id in (main_branch, depot_branch):
            per_branch = reports.valuation_report(
                db, stock.company_id, as_of=as_of, branch_id=branch_id
            )
            branch_totals = {
                total.gl_account_id: total.value for total in per_branch.account_totals
            }
            for account_id in (inventory_account, transit_account):
                assert branch_totals.get(account_id, Decimal(0)) == (
                    stock_service.inventory_account_balance(
                        db,
                        stock.company_id,
                        account_id,
                        as_of=as_of,
                        branch_id=branch_id,
                    )
                ), f"account {account_id} at branch {branch_id} disagrees on {as_of}"


def test_the_valuation_report_is_an_as_of_question_not_a_cache_read(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    receive(db, stock, quantity=_d("10"), unit_cost=_d("140"), on=AFTER)

    assert reports.valuation_report(db, stock.company_id, as_of=MARCH).total_value == _d("1000")
    assert reports.valuation_report(db, stock.company_id, as_of=AFTER).total_value == _d("2400")
    assert reports.valuation_report(db, stock.company_id, as_of=BEFORE).total_value == _d("0")


def test_a_backdated_receipt_appears_on_its_own_date(db: Session, stock: Stock) -> None:
    """Posting order rules costing; date order rules reporting. A receipt dated in the past
    and posted today was, as far as the ledger is concerned, always there — and so it is on
    the valuation report for that date, which is exactly why the report ties to the account."""
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    receive(db, stock, quantity=_d("5"), unit_cost=_d("130"), on=BEFORE)

    assert reports.valuation_report(db, stock.company_id, as_of=BEFORE).total_value == _d("650")
    assert reports.valuation_report(
        db, stock.company_id, as_of=BEFORE
    ).total_value == stock_service.inventory_account_balance(
        db, stock.company_id, stock.inventory.settings.inventory_account_id, as_of=BEFORE
    )


def test_zero_quantity_lines_are_off_by_default_and_change_no_total(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    issue(db, stock, quantity=_d("10"), on=MARCH)
    other = _second_item(db, stock)
    _receive_item(
        db, stock, other, quantity=_d("4"), unit_cost=_d("50"), warehouse=stock.main, on=MARCH
    )

    hidden = reports.valuation_report(db, stock.company_id, as_of=MARCH)
    shown = reports.valuation_report(db, stock.company_id, as_of=MARCH, include_zero=True)

    assert [row.item_code for row in hidden.rows] == ["WINE-375"]
    assert [row.item_code for row in shown.rows] == ["WINE-375", "WINE-750"]
    assert hidden.total_value == shown.total_value == _d("200")


def test_the_valuation_report_names_the_account_holding_each_location(
    db: Session, stock: Stock
) -> None:
    settings = stock.inventory.settings
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    transfer_service.post_transfer(
        db,
        stock.company_id,
        transfer_service.TransferInput(
            transfer_date=MARCH,
            description="on the road",
            from_warehouse_id=stock.main.id,
            to_warehouse_id=stock.depot.id,
            lines=[transfer_service.TransferLineInput(item_id=stock.item.id, quantity=_d("4"))],
        ),
        receive_now=False,
        actor=stock.owner,
    )

    report = reports.valuation_report(db, stock.company_id, as_of=MARCH)

    by_warehouse = {row.warehouse_code: row for row in report.rows}
    assert by_warehouse["MAIN"].gl_account_id == settings.inventory_account_id
    assert by_warehouse["TRANSIT"].gl_account_id == settings.inventory_in_transit_account_id
    assert by_warehouse["TRANSIT"].is_in_transit is True


def test_the_valuation_report_pages_by_item_and_totals_the_whole_set(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    transfer_now(
        db, stock, quantity=_d("4"), source=stock.main, destination=stock.depot, on=MARCH
    )
    for suffix in ("A", "B"):
        other = _second_item(db, stock, code=f"WINE-{suffix}")
        _receive_item(
            db,
            stock,
            other,
            quantity=_d("2"),
            unit_cost=_d("50"),
            warehouse=stock.main,
            on=MARCH,
        )

    # Code order, and "WINE-750" sorts before "WINE-A" because "7" does before "A".
    first = reports.valuation_report(db, stock.company_id, as_of=MARCH, limit=1)

    assert {row.warehouse_code for row in first.rows} == {"MAIN", "DEPOT"}, (
        "an item's warehouse rows travel together, so a page break cannot split a subtotal"
    )
    assert {row.item_code for row in first.rows} == {"WINE-750"}
    assert first.total_value == _d("1200"), (
        "a page of one item still totals the whole company, or it ties to nothing"
    )
    assert first.next_cursor is not None

    second = reports.valuation_report(
        db, stock.company_id, as_of=MARCH, limit=1, cursor=first.next_cursor
    )
    third = reports.valuation_report(
        db, stock.company_id, as_of=MARCH, limit=1, cursor=second.next_cursor
    )
    assert [row.item_code for row in second.rows] == ["WINE-A"]
    assert [row.item_code for row in third.rows] == ["WINE-B"]
    assert third.next_cursor is None


def test_item_totals_add_up_the_warehouses_on_the_page(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    transfer_now(
        db, stock, quantity=_d("4"), source=stock.main, destination=stock.depot, on=MARCH
    )

    report = reports.valuation_report(db, stock.company_id, as_of=MARCH)

    assert len(report.item_totals) == 1
    assert report.item_totals[0].quantity == _d("10")
    assert report.item_totals[0].value == _d("1000")
    assert report.warehouse_totals[stock.main.id] == _d("600")
    assert report.warehouse_totals[stock.depot.id] == _d("400")
    assert report.warehouse_totals[stock.transit.id] == _d("0"), (
        "a warehouse the stock passed through keeps its subtotal, at nothing — the totals "
        "are over every location, filtered or not, which is what makes them tie"
    )


# --- Movement ----------------------------------------------------------------------------------


def test_the_movement_report_splits_opening_in_out_and_closing(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=BEFORE)
    receive(db, stock, quantity=_d("10"), unit_cost=_d("121"), on=MARCH)
    issue(db, stock, quantity=_d("7"), on=MARCH)

    report = reports.movement_report(
        db, stock.company_id, date_from=MARCH, date_to=MARCH
    )

    row = next(row for row in report.rows if row.warehouse_code == "MAIN")
    assert (row.opening_quantity, row.opening_value) == (_d("10"), _d("1000"))
    assert (row.quantity_in, row.value_in) == (_d("10"), _d("1210"))
    assert (row.quantity_out, row.value_out) == (_d("-7"), _d("-774"))
    assert (row.closing_quantity, row.closing_value) == (_d("13"), _d("1436"))
    assert report.opening_value == _d("1000")
    assert report.closing_value == _d("1436")


def test_the_movement_closing_is_the_valuation_at_the_same_date(
    db: Session, stock: Stock
) -> None:
    """Two reports, one ledger: the movement report's closing column has to be the valuation
    report's value, or one of them is lying about the same moves."""
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=BEFORE)
    receive(db, stock, quantity=_d("10"), unit_cost=_d("121"), on=MARCH)
    issue(db, stock, quantity=_d("7"), on=MARCH)
    transfer_now(
        db, stock, quantity=_d("6"), source=stock.main, destination=stock.depot, on=MARCH
    )

    movement = reports.movement_report(db, stock.company_id, date_from=BEFORE, date_to=MARCH)
    valuation = reports.valuation_report(db, stock.company_id, as_of=MARCH)

    assert movement.closing_value == valuation.total_value
    closing = {
        (row.item_id, row.warehouse_id): (row.closing_quantity, row.closing_value)
        for row in movement.rows
    }
    for row in valuation.rows:
        assert closing[(row.item_id, row.warehouse_id)] == (row.quantity, row.value)


def test_a_location_that_did_not_move_still_shows_what_it_holds(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=BEFORE)

    report = reports.movement_report(db, stock.company_id, date_from=MARCH, date_to=AFTER)

    row = next(row for row in report.rows if row.warehouse_code == "MAIN")
    assert row.opening_quantity == _d("10")
    assert (row.quantity_in, row.quantity_out) == (_d("0"), _d("0"))
    assert row.closing_quantity == _d("10")


def test_a_location_emptied_before_the_window_is_off_unless_asked_for(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=BEFORE)
    issue(db, stock, quantity=_d("10"), on=BEFORE)

    assert reports.movement_report(db, stock.company_id, date_from=MARCH, date_to=AFTER).rows == []
    shown = reports.movement_report(
        db, stock.company_id, date_from=MARCH, date_to=AFTER, include_zero=True
    )
    assert [row.warehouse_code for row in shown.rows] == ["MAIN"]


def test_a_revaluation_lands_on_the_side_its_value_points(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    stock_service.revalue_stock(
        db,
        stock.company_id,
        document=stock_service.StockDocument(
            doc_type="INAJ",
            move_date=MARCH,
            description="write-down",
            transaction_type_id=stock.type_id("REVAL"),
        ),
        lines=[
            stock_service.StockLine(
                item_id=stock.item.id,
                warehouse_id=stock.main.id,
                quantity=_d("0"),
                value=_d("-200"),
            )
        ],
        actor=stock.owner,
    )

    row = next(
        row
        for row in reports.movement_report(
            db, stock.company_id, date_from=MARCH, date_to=MARCH
        ).rows
        if row.warehouse_code == "MAIN"
    )
    assert row.value_out == _d("-200")
    assert row.quantity_out == _d("0"), "a revaluation moves no quantity"
    assert row.closing_value == _d("800")


def test_the_movement_report_filters_by_branch(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    transfer_now(
        db, stock, quantity=_d("4"), source=stock.main, destination=stock.depot, on=MARCH
    )

    depot_only = reports.movement_report(
        db,
        stock.company_id,
        date_from=MARCH,
        date_to=MARCH,
        branch_id=stock.depot.branch_id,
    )

    assert [row.warehouse_code for row in depot_only.rows] == ["DEPOT"]
    assert depot_only.closing_value == _d("400")


# --- Transaction -------------------------------------------------------------------------------


def test_the_transaction_report_lists_moves_with_their_drill_down_keys(
    db: Session, stock: Stock
) -> None:
    posting = receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)

    report = reports.transaction_report(db, stock.company_id, date_from=MARCH, date_to=MARCH)

    assert report.move_count == 1
    row = report.rows[0]
    assert row.journal_entry_id == posting.entry.id
    assert row.entry_number == posting.entry.number
    assert row.item_code == "WINE-750"
    assert row.warehouse_code == "MAIN"
    assert row.branch_id == stock.main.branch_id
    assert (report.total_quantity, report.total_value) == (_d("10"), _d("1000"))


def test_the_transaction_report_is_in_date_then_posting_order(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    receive(db, stock, quantity=_d("5"), unit_cost=_d("130"), on=BEFORE)

    report = reports.transaction_report(db, stock.company_id, date_from=BEFORE, date_to=AFTER)

    assert [row.move_date for row in report.rows] == [BEFORE, MARCH]
    assert report.rows[0].sequence_no > report.rows[1].sequence_no


def test_the_transaction_report_filters_to_the_provisional_moves(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    set_policy(db, stock, NegativeStockPolicy.ALLOW)
    issue(db, stock, quantity=_d("12"), on=MARCH)

    flagged = reports.transaction_report(
        db, stock.company_id, date_from=MARCH, date_to=MARCH, provisional_only=True
    )

    assert flagged.move_count == 1
    assert flagged.rows[0].cost_provisional is True
    assert flagged.total_value == _d("-1200")


def test_the_transaction_report_filters_by_transaction_type_and_warehouse(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    issue(db, stock, quantity=_d("2"), on=MARCH)

    outs = reports.transaction_report(
        db,
        stock.company_id,
        date_from=MARCH,
        date_to=MARCH,
        transaction_type_id=stock.type_id("ADJOUT"),
    )
    depot = reports.transaction_report(
        db,
        stock.company_id,
        date_from=MARCH,
        date_to=MARCH,
        warehouse_id=stock.depot.id,
    )

    assert outs.move_count == 1
    assert outs.total_quantity == _d("-2")
    assert depot.move_count == 0


def test_the_transaction_report_pages_without_repeating_a_move(
    db: Session, stock: Stock
) -> None:
    for _ in range(5):
        receive(db, stock, quantity=_d("2"), unit_cost=_d("100"), on=MARCH)

    seen: list[int] = []
    cursor = None
    for _ in range(3):
        page = reports.transaction_report(
            db, stock.company_id, date_from=MARCH, date_to=MARCH, limit=2, cursor=cursor
        )
        assert page.move_count == 5, "the count is of the filtered set, not of the page"
        seen.extend(row.move_id for row in page.rows)
        cursor = page.next_cursor
    assert cursor is None
    assert len(seen) == len(set(seen)) == 5


def test_the_transaction_total_matches_the_movement_report_over_the_same_window(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    issue(db, stock, quantity=_d("3"), on=MARCH)
    transfer_now(
        db, stock, quantity=_d("2"), source=stock.main, destination=stock.depot, on=MARCH
    )

    transactions = reports.transaction_report(
        db, stock.company_id, date_from=MARCH, date_to=MARCH
    )
    movement = reports.movement_report(db, stock.company_id, date_from=MARCH, date_to=MARCH)

    assert transactions.total_value == movement.value_in + movement.value_out


def test_an_unknown_warehouse_filter_is_a_not_found(db: Session, stock: Stock) -> None:
    with pytest.raises(NotFoundError):
        reports.transaction_report(
            db, stock.company_id, date_from=MARCH, date_to=MARCH, warehouse_id=-1
        )


# --- Count -------------------------------------------------------------------------------------


def test_the_count_report_shows_variances_and_the_document_they_posted(
    db: Session, stock: Stock
) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    session = count_service.open_session(
        db,
        stock.company_id,
        count_service.CountSessionInput(
            warehouse_id=stock.main.id, count_date=MARCH, description="March count"
        ),
        actor=stock.owner,
    )
    line = count_service.lines_of(db, stock.company_id, session.id)[0]
    count_service.enter_count(
        db, stock.company_id, session.id, line.id, quantity=_d("9"), actor=stock.owner
    )

    counting = reports.count_report(db, stock.company_id)
    assert counting.rows[0].status == StockCountStatus.COUNTING
    assert counting.rows[0].line_count == 1
    assert counting.rows[0].counted_count == 1
    assert counting.rows[0].variance_count == 1
    assert counting.rows[0].lines[0].variance == _d("-1")
    assert counting.rows[0].document_id is None

    _, document, _ = count_service.process_session(
        db, stock.company_id, session.id, actor=stock.owner
    )

    processed = reports.count_report(db, stock.company_id)
    row = processed.rows[0]
    assert row.status == StockCountStatus.COMPLETED
    assert row.document_id == document.id
    assert row.document_number == document.number
    assert row.journal_entry_id == document.journal_entry_id
    assert row.lines[0].stock_move_id is not None


def test_the_count_report_flags_a_stale_line(db: Session, stock: Stock) -> None:
    """Decision 7's refusal, seen from the report rather than from Process: the line the
    operator has to go back and recount is named before they try to post it."""
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    session = count_service.open_session(
        db,
        stock.company_id,
        count_service.CountSessionInput(
            warehouse_id=stock.main.id, count_date=MARCH, description="March count"
        ),
        actor=stock.owner,
    )
    line = count_service.lines_of(db, stock.company_id, session.id)[0]
    count_service.enter_count(
        db, stock.company_id, session.id, line.id, quantity=_d("9"), actor=stock.owner
    )

    assert reports.count_report(db, stock.company_id).rows[0].lines[0].stale is False

    receive(db, stock, quantity=_d("5"), unit_cost=_d("100"), on=MARCH)

    assert reports.count_report(db, stock.company_id).rows[0].lines[0].stale is True


def test_the_count_report_filters_and_can_drop_the_lines(db: Session, stock: Stock) -> None:
    receive(db, stock, quantity=_d("10"), unit_cost=_d("100"), on=MARCH)
    session = count_service.open_session(
        db,
        stock.company_id,
        count_service.CountSessionInput(
            warehouse_id=stock.main.id,
            count_date=MARCH,
            description="March count",
            include_items=(stock.item.id,),
        ),
        actor=stock.owner,
    )
    lines = count_service.lines_of(db, stock.company_id, session.id)
    count_service.enter_count(
        db, stock.company_id, session.id, lines[0].id, quantity=_d("10"), actor=stock.owner
    )

    summary = reports.count_report(db, stock.company_id, with_lines=False)
    assert summary.rows[0].lines == []
    assert summary.rows[0].line_count == 1

    variances = reports.count_report(db, stock.company_id, variances_only=True)
    assert variances.rows[0].lines == [], "a count that agreed with the books has no variance"
    assert variances.rows[0].variance_count == 0

    elsewhere = reports.count_report(db, stock.company_id, warehouse_id=stock.depot.id)
    assert elsewhere.rows == []

    completed = reports.count_report(db, stock.company_id, status=StockCountStatus.COMPLETED)
    assert completed.rows == []
