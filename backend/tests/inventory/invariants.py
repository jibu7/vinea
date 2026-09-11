"""The inventory invariant suite (P5 step 2). `assert_stock_invariants` is the acceptance
contract for the stock ledger, exactly as `assert_ledger_invariants` is for the kernel and
`assert_subledger_invariants` is for AR/AP.

Every failure here means the same thing: stock and the general ledger no longer agree about
what the company owns. That is §6 invariant 3, and it is checked at **every date anything was
posted on**, per account and per branch, not only at the end of a scenario — an error that
one operation introduces and the next one masks is exactly the kind this suite exists to
catch.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory.costing import LocationState
from app.inventory.stock import (
    balances_as_of,
    item_state,
    location_balance,
    verify_stock_balances,
)
from app.models.gl import ControlType, GLAccount, GLSettings
from app.models.inventory import (
    INVENTORY_MODULE,
    Item,
    ItemCostState,
    StockBalance,
    StockMove,
    Warehouse,
)
from app.models.journal import JournalEntry, JournalLine, JournalStatus

ZERO = Decimal(0)


def _location_accounts(db: Session, company_id: int) -> dict[tuple[int, int], int]:
    """Which inventory account each (item, warehouse) cell belongs to, from the **masters**.

    Deliberately not read off the journal lines the moves point at. Reading the line would
    make the reconciliation self-fulfilling; reading the masters is what catches an item whose
    inventory account was changed after it had history, which is why the masters refuse that
    change once a move exists.
    """
    settings = db.scalars(select(GLSettings).where(GLSettings.company_id == company_id)).one()
    warehouses = {
        row.id: row
        for row in db.scalars(select(Warehouse).where(Warehouse.company_id == company_id))
    }
    items = {row.id: row for row in db.scalars(select(Item).where(Item.company_id == company_id))}
    mapping: dict[tuple[int, int], int] = {}
    for item_id, warehouse_id in db.execute(
        select(StockMove.item_id, StockMove.warehouse_id)
        .where(StockMove.company_id == company_id)
        .distinct()
    ).all():
        warehouse = warehouses[warehouse_id]
        if warehouse.is_in_transit:
            account_id = settings.inventory_in_transit_account_id
        else:
            account_id = items[item_id].inventory_account_id or settings.inventory_account_id
        assert account_id is not None, "inventory posted without an inventory account configured"
        mapping[(item_id, warehouse_id)] = int(account_id)
    return mapping


def assert_stock_invariants(db: Session, company_id: int) -> None:
    """1. Every move with a value carries exactly one INV journal line, worth the same;
       every INV journal line carries an item and exactly one move.
    2. `verify_stock_balances()` reports no drift between the caches and the moves.
    3. The average equals value / quantity wherever the quantity is positive, and the last
       positive average wherever it is not.
    4. A location holding no quantity holds no value.
    5. No location ever went negative except through a move that says so: an issue flagged
       `cost_provisional` (only the `allow` policy produces one), or a reversal, which mirrors
       a value rather than guessing at one; and an issue that empties a location takes
       everything that location held — the flush, checked at the move that performs it.
    6. For every INV account, at **every date** anything was posted on: the stock value of the
       locations mapped to that account equals the account's balance — in total and per
       branch, in-transit stock included.
    """
    moves = list(
        db.scalars(
            select(StockMove)
            .where(StockMove.company_id == company_id)
            .order_by(StockMove.sequence_no)
        )
    )

    # 2. The caches are exactly the replay of the moves.
    drift = verify_stock_balances(db, company_id)
    assert not drift, f"stock cache drift: balances={drift.balances[:3]} costs={drift.costs[:3]}"

    # 1. Move ↔ journal line, both ways.
    inventory_accounts = {
        row.id
        for row in db.scalars(
            select(GLAccount).where(
                GLAccount.company_id == company_id,
                GLAccount.control_type == ControlType.INVENTORY,
            )
        )
    }
    lines = {
        line.id: line
        for line in db.scalars(
            select(JournalLine)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(
                JournalLine.company_id == company_id,
                JournalLine.gl_account_id.in_(inventory_accounts or {-1}),
                JournalEntry.status == JournalStatus.POSTED,
            )
        )
    }
    warehouses = {
        row.id: row
        for row in db.scalars(select(Warehouse).where(Warehouse.company_id == company_id))
    }
    claimed: set[int] = set()
    for move in moves:
        if move.value == ZERO:
            assert move.journal_line_id is None and move.journal_entry_id is None, (
                f"move {move.id} carries no value but claims a journal line"
            )
            continue
        assert move.journal_line_id is not None, f"move {move.id} has a value and no line"
        line = lines.get(move.journal_line_id)
        assert line is not None, f"move {move.id} points at a line that is not an INV line"
        assert move.journal_line_id not in claimed, (
            f"line {move.journal_line_id} is claimed by more than one move"
        )
        claimed.add(move.journal_line_id)
        assert line.base_amount == move.value, (
            f"move {move.id} is worth {move.value} but its line posted {line.base_amount}"
        )
        assert line.item_id == move.item_id, f"move {move.id} and its line name different items"
        assert line.branch_id == warehouses[move.warehouse_id].branch_id, (
            f"move {move.id} posted to a branch that is not its warehouse's"
        )
        assert line.project_id == move.project_id, (
            f"move {move.id} and its line carry different projects"
        )
    for line_id, line in lines.items():
        assert line.item_id is not None, f"INV line {line_id} carries no item"
        assert line_id in claimed, f"INV line {line_id} has no stock move behind it"

    # Every INV line came from the inventory module — the registry guard, seen from the
    # outside.
    modules = db.execute(
        select(JournalEntry.module)
        .join(JournalLine, JournalLine.entry_id == JournalEntry.id)
        .where(
            JournalLine.company_id == company_id,
            JournalLine.gl_account_id.in_(inventory_accounts or {-1}),
        )
        .distinct()
    ).all()
    assert all(module == INVENTORY_MODULE for (module,) in modules), (
        f"an INV account was posted to by {[m for (m,) in modules if m != INVENTORY_MODULE]}"
    )

    # 3 + 4. The caches say what the arithmetic says.
    balances = list(db.scalars(select(StockBalance).where(StockBalance.company_id == company_id)))
    for balance in balances:
        if balance.quantity == ZERO:
            assert balance.value == ZERO, (
                f"item {balance.item_id} at warehouse {balance.warehouse_id} holds "
                f"{balance.value} with no quantity"
            )
    for state in db.scalars(select(ItemCostState).where(ItemCostState.company_id == company_id)):
        # `item_state` sums the item's locations and carries the stored last-positive average,
        # so `.average` is the rule of decision 4 applied to what the caches actually hold.
        expected = item_state(db, company_id, state.item_id)
        assert state.average_cost == expected.average, (
            f"item {state.item_id} caches an average of {state.average_cost}, but "
            f"{expected.value} / {expected.quantity} is {expected.average}"
        )

    # 5. Every negative position traces back to a move that admitted it was guessing.
    #
    # Stated on the *moves* rather than on the current setting, because the policy is a
    # setting an operator can change: a company that ran under `allow`, went negative and then
    # switched to `block` has a negative balance and has broken no rule — what `block` governs
    # is whether a new issue may open one. `cost_provisional` is the record of the policy in
    # force when a move was posted, so it is the thing worth asserting against.
    running: dict[tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
    held: dict[tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
    for move in moves:
        location = (move.item_id, move.warehouse_id)
        was = running[location]
        running[location] = was + move.quantity
        held[location] += move.value

        # 5b. The flush, stated where it can be seen: an issue that empties a location takes
        # *everything* that location held, so the location is worth nothing the instant the
        # move lands — not after a correcting move tidies up behind it.
        #
        # This clause exists because the obvious invariant ("a location at zero quantity holds
        # no value") cannot see the flush at all: delete the flush rule and the residue rule
        # expels the leftover on the next line, so the end state is identical and every other
        # assertion here still passes. Measured, not assumed — the deep property suite passed
        # green with the flush rule removed until this clause was added.
        #
        # Reversals are exempt: they mirror a value rather than compute one, so emptying a
        # location through a reversal legitimately strands value, which is then expelled as
        # its own posting.
        if (
            move.quantity < ZERO
            and was != ZERO
            and running[location] == ZERO
            and move.reverses_move_id is None
        ):
            assert held[location] == ZERO, (
                f"move {move.id} emptied a location but left {held[location]} behind — the "
                "issue was costed at the average instead of taking what was there"
            )
        if running[location] < ZERO and was >= ZERO and move.quantity < ZERO:
            # A *reversal* is exempt, and deliberately so: it is costed at the value of the
            # move it mirrors, not at the last positive average, so it is not provisional in
            # the only sense the flag means. Under `allow`, reversing a receipt whose stock
            # has since gone out legitimately leaves the location negative at a value nobody
            # guessed at. Every other issue that opens a negative is a guess, and says so.
            assert move.cost_provisional or move.reverses_move_id is not None, (
                f"move {move.id} took a location negative without being flagged provisional"
            )

    # 6. Stock value == the inventory GL balance, at every date, per account and per branch.
    #
    # Both sides are accumulated in one pass rather than re-queried per date. The obvious
    # shape — "for each date, ask the database for the balance" — is quadratic, and this suite
    # runs after *every step* of a property test whose history keeps growing, so the obvious
    # shape is the difference between a guard that runs and one that gets switched off.
    accounts = _location_accounts(db, company_id)
    stock_by_date: dict[date, dict[tuple[int, int | None], Decimal]] = defaultdict(
        lambda: defaultdict(lambda: ZERO)
    )
    for move in moves:
        account_id = accounts[(move.item_id, move.warehouse_id)]
        branch_id = warehouses[move.warehouse_id].branch_id
        stock_by_date[move.move_date][(account_id, None)] += move.value
        stock_by_date[move.move_date][(account_id, branch_id)] += move.value

    ledger_by_date: dict[date, dict[tuple[int, int | None], Decimal]] = defaultdict(
        lambda: defaultdict(lambda: ZERO)
    )
    for entry_date, account_id, branch_id, amount in db.execute(
        select(
            JournalEntry.entry_date,
            JournalLine.gl_account_id,
            JournalLine.branch_id,
            JournalLine.base_amount,
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == company_id,
            JournalLine.gl_account_id.in_(inventory_accounts or {-1}),
            JournalEntry.status == JournalStatus.POSTED,
        )
    ).all():
        ledger_by_date[entry_date][(account_id, None)] += amount
        ledger_by_date[entry_date][(account_id, branch_id)] += amount

    stock_running: dict[tuple[int, int | None], Decimal] = defaultdict(lambda: ZERO)
    ledger_running: dict[tuple[int, int | None], Decimal] = defaultdict(lambda: ZERO)
    for as_of in sorted(set(stock_by_date) | set(ledger_by_date)):
        for key, amount in stock_by_date.get(as_of, {}).items():
            stock_running[key] += amount
        for key, amount in ledger_by_date.get(as_of, {}).items():
            ledger_running[key] += amount
        for key in set(stock_running) | set(ledger_running):
            account_id, branch_id = key
            expected = stock_running.get(key, ZERO)
            actual = ledger_running.get(key, ZERO)
            where = "" if branch_id is None else f" at branch {branch_id}"
            assert actual == expected, (
                f"inventory account {account_id}{where} is {actual} as of {as_of} but the "
                f"stock it holds is worth {expected}"
            )


def location_position(
    db: Session, company_id: int, item_id: int, warehouse_id: int, *, as_of: date | None = None
) -> LocationState:
    """What one location holds — from the cache, or reconstructed as of a date."""
    if as_of is not None:
        return balances_as_of(db, company_id, as_of=as_of).get(
            (item_id, warehouse_id), LocationState()
        )
    return location_balance(db, company_id, item_id, warehouse_id)
