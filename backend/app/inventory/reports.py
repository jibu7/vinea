"""Inventory reports (P5 step 5; Appendix C: Reports → Inventory).

Four reports, one rule: **reconstruct from `stock_moves`, by document date.** None of them
reads `stock_balances`, because the cache is the position *now* and "now" is not a date
anybody reports on. Reconstructing by date is also what makes the valuation report tie to the
inventory account — a backdated receipt was, as far as the ledger is concerned, always there
on its own date, and its journal entry is dated the same day.

* **Movement** — opening, in, out, closing per item × warehouse over a range.
* **Transaction** — the moves themselves, filtered, with the keys to drill to the entry.
* **Valuation** — what stock was worth on a date, per warehouse, with the account totals that
  tie it to the GL. Stock in transit is stock: it has a warehouse and an account, so it lands
  on this report like anything else (decision 6).
* **Count** — sessions, their lines and variances, and the document each one posted.

**Pagination.** Cursor-paged in the kernel's style (`account_transactions`), never by OFFSET.
The grouped reports page by *item* — an item's warehouse rows travel together, so a page
break never splits a subtotal — and order by item code, so the cursor resolves the anchor
item's code and the keyset is on `(code, id)`.

**Totals are not paged.** Every report's totals are computed over the whole filtered set, not
over the rows on this page: a valuation report whose total was the sum of page one would tie
to nothing, and tying to the account is the whole point of it.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, func, select, tuple_
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.inventory import counts as counts_service
from app.inventory.stock import Location, location_accounts
from app.models.gl import GLAccount
from app.models.inventory import (
    InventoryDocument,
    Item,
    StockCountSession,
    StockCountStatus,
    StockMove,
    Warehouse,
)
from app.models.journal import JournalEntry

ZERO = Decimal(0)


# --- Shared scaffolding ------------------------------------------------------------------------


@dataclass(frozen=True)
class _Page:
    """The item ids on this page, and the cursor that continues after them."""

    item_ids: list[int]
    next_cursor: int | None


def _item_page(
    db: Session,
    company_id: int,
    *,
    item_ids: set[int],
    item_id: int | None,
    cursor: int | None,
    limit: int,
) -> tuple[_Page, dict[int, Item]]:
    """One page of items in code order, keyset-paged on `(code, id)`.

    `item_ids` is the set the data actually reached — a report never lists an item nothing
    happened to. The `item_id` filter narrows it further; a filter naming an item with no
    activity yields an empty page rather than an error, because "this item did not move in
    March" is an answer, not a failure.
    """
    if item_id is not None:
        item_ids = item_ids & {item_id}
    if not item_ids:
        return _Page(item_ids=[], next_cursor=None), {}

    query = select(Item).where(Item.company_id == company_id, Item.id.in_(item_ids))
    if cursor is not None:
        anchor = db.execute(
            select(Item.code, Item.id).where(Item.id == cursor, Item.company_id == company_id)
        ).one_or_none()
        if anchor is None:
            raise NotFoundError("Cursor item not found")
        query = query.where(tuple_(Item.code, Item.id) > tuple_(*anchor))
    rows = list(db.scalars(query.order_by(Item.code, Item.id).limit(limit + 1)))
    has_more = len(rows) > limit
    page = rows[:limit]
    return (
        _Page(
            item_ids=[row.id for row in page],
            next_cursor=page[-1].id if has_more and page else None,
        ),
        {row.id: row for row in page},
    )


def _warehouses(db: Session, company_id: int) -> dict[int, Warehouse]:
    return {
        row.id: row
        for row in db.scalars(select(Warehouse).where(Warehouse.company_id == company_id))
    }


def _warehouse_scope(
    db: Session, company_id: int, *, warehouse_id: int | None, branch_id: int | None
) -> tuple[dict[int, Warehouse], set[int] | None]:
    """Every warehouse, and the subset the filters allow — `None` meaning "all of them".

    A branch filter is a filter on warehouses, not on journal lines: a move's branch *is* its
    warehouse's branch (the invariant suite asserts it line by line), so narrowing the
    warehouses is the same narrowing the GL would do, and the per-branch tie holds.
    """
    warehouses = _warehouses(db, company_id)
    if warehouse_id is not None and warehouse_id not in warehouses:
        raise NotFoundError("Warehouse not found")
    if warehouse_id is None and branch_id is None:
        return warehouses, None
    allowed = {
        known
        for known, warehouse in warehouses.items()
        if (warehouse_id is None or known == warehouse_id)
        and (branch_id is None or warehouse.branch_id == branch_id)
    }
    return warehouses, allowed


# --- Movement ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class MovementRow:
    item_id: int
    item_code: str
    item_name: str
    warehouse_id: int
    warehouse_code: str
    warehouse_name: str
    branch_id: int
    is_in_transit: bool
    opening_quantity: Decimal
    opening_value: Decimal
    quantity_in: Decimal
    value_in: Decimal
    quantity_out: Decimal
    value_out: Decimal

    @property
    def closing_quantity(self) -> Decimal:
        return self.opening_quantity + self.quantity_in + self.quantity_out

    @property
    def closing_value(self) -> Decimal:
        return self.opening_value + self.value_in + self.value_out


@dataclass(frozen=True)
class MovementReport:
    date_from: date
    date_to: date
    rows: list[MovementRow]
    next_cursor: int | None
    #: Over the whole filtered set, not over this page.
    opening_value: Decimal
    value_in: Decimal
    value_out: Decimal
    closing_value: Decimal


def _movement_aggregate(
    db: Session,
    company_id: int,
    *,
    date_from: date,
    date_to: date,
    allowed: set[int] | None,
    item_id: int | None,
) -> tuple[
    dict[Location, tuple[Decimal, Decimal]],
    dict[Location, tuple[Decimal, Decimal, Decimal, Decimal]],
]:
    """(opening per location, in/out per location) — two grouped queries, not one per row."""

    def _scoped(statement: Select) -> Select:
        statement = statement.where(StockMove.company_id == company_id)
        if allowed is not None:
            statement = statement.where(StockMove.warehouse_id.in_(allowed or {-1}))
        if item_id is not None:
            statement = statement.where(StockMove.item_id == item_id)
        return statement

    opening = {
        (row[0], row[1]): (row[2], row[3])
        for row in db.execute(
            _scoped(
                select(
                    StockMove.item_id,
                    StockMove.warehouse_id,
                    func.sum(StockMove.quantity),
                    func.sum(StockMove.value),
                ).where(StockMove.move_date < date_from)
            ).group_by(StockMove.item_id, StockMove.warehouse_id)
        ).all()
    }

    # In and out are split on the sign of the *quantity*, so a revaluation (quantity 0, value
    # signed) lands on the side its value points, which is where an operator looks for it.
    movement: dict[Location, tuple[Decimal, Decimal, Decimal, Decimal]] = {}
    for item, warehouse, quantity, value in db.execute(
        _scoped(
            select(
                StockMove.item_id,
                StockMove.warehouse_id,
                StockMove.quantity,
                StockMove.value,
            ).where(StockMove.move_date >= date_from, StockMove.move_date <= date_to)
        )
    ).all():
        in_quantity, in_value, out_quantity, out_value = movement.get(
            (item, warehouse), (ZERO, ZERO, ZERO, ZERO)
        )
        increases = quantity > ZERO or (quantity == ZERO and value >= ZERO)
        if increases:
            in_quantity, in_value = in_quantity + quantity, in_value + value
        else:
            out_quantity, out_value = out_quantity + quantity, out_value + value
        movement[(item, warehouse)] = (in_quantity, in_value, out_quantity, out_value)
    return opening, movement


def movement_report(
    db: Session,
    company_id: int,
    *,
    date_from: date,
    date_to: date,
    warehouse_id: int | None = None,
    branch_id: int | None = None,
    item_id: int | None = None,
    include_zero: bool = False,
    cursor: int | None = None,
    limit: int = 100,
) -> MovementReport:
    """Opening, in, out and closing per item × warehouse over `[date_from, date_to]`.

    A location appears when it moved in the window *or* when it opened with something in it —
    stock that sat still all month is part of what the month looked like. `include_zero` adds
    back the locations that opened empty, moved nothing and closed empty, which exist only
    because something happened there once.
    """
    warehouses, allowed = _warehouse_scope(
        db, company_id, warehouse_id=warehouse_id, branch_id=branch_id
    )
    opening, movement = _movement_aggregate(
        db,
        company_id,
        date_from=date_from,
        date_to=date_to,
        allowed=allowed,
        item_id=item_id,
    )

    locations = set(opening) | set(movement)
    if not include_zero:
        locations = {
            location
            for location in locations
            if any(opening.get(location, (ZERO, ZERO)))
            or any(movement.get(location, (ZERO, ZERO, ZERO, ZERO)))
        }

    totals_opening = totals_in = totals_out = ZERO
    for location in locations:
        totals_opening += opening.get(location, (ZERO, ZERO))[1]
        in_quantity, in_value, out_quantity, out_value = movement.get(
            location, (ZERO, ZERO, ZERO, ZERO)
        )
        totals_in += in_value
        totals_out += out_value

    page, items = _item_page(
        db,
        company_id,
        item_ids={item for item, _ in locations},
        item_id=item_id,
        cursor=cursor,
        limit=limit,
    )
    on_page = {row for row in locations if row[0] in items}

    rows = [
        MovementRow(
            item_id=item,
            item_code=items[item].code,
            item_name=items[item].name,
            warehouse_id=warehouse,
            warehouse_code=warehouses[warehouse].code,
            warehouse_name=warehouses[warehouse].name,
            branch_id=warehouses[warehouse].branch_id,
            is_in_transit=warehouses[warehouse].is_in_transit,
            opening_quantity=opening.get((item, warehouse), (ZERO, ZERO))[0],
            opening_value=opening.get((item, warehouse), (ZERO, ZERO))[1],
            quantity_in=movement.get((item, warehouse), (ZERO, ZERO, ZERO, ZERO))[0],
            value_in=movement.get((item, warehouse), (ZERO, ZERO, ZERO, ZERO))[1],
            quantity_out=movement.get((item, warehouse), (ZERO, ZERO, ZERO, ZERO))[2],
            value_out=movement.get((item, warehouse), (ZERO, ZERO, ZERO, ZERO))[3],
        )
        for item, warehouse in on_page
        if warehouse in warehouses
    ]
    rows.sort(key=lambda row: (row.item_code, row.warehouse_code))
    return MovementReport(
        date_from=date_from,
        date_to=date_to,
        rows=rows,
        next_cursor=page.next_cursor,
        opening_value=totals_opening,
        value_in=totals_in,
        value_out=totals_out,
        closing_value=totals_opening + totals_in + totals_out,
    )


# --- Transaction -------------------------------------------------------------------------------


@dataclass(frozen=True)
class TransactionRow:
    move_id: int
    move_date: date
    sequence_no: int
    item_id: int
    item_code: str
    item_name: str
    warehouse_id: int
    warehouse_code: str
    branch_id: int
    quantity: Decimal
    unit_cost: Decimal | None
    value: Decimal
    cost_provisional: bool
    project_id: int | None
    transaction_type_id: int | None
    journal_entry_id: int | None
    entry_number: str | None
    source_doc_type: str | None
    source_doc_id: int | None
    source_line_id: int | None


@dataclass(frozen=True)
class TransactionReport:
    date_from: date
    date_to: date
    rows: list[TransactionRow]
    next_cursor: int | None
    #: Over the whole filtered set, not over this page.
    total_quantity: Decimal
    total_value: Decimal
    move_count: int


def transaction_report(
    db: Session,
    company_id: int,
    *,
    date_from: date,
    date_to: date,
    item_id: int | None = None,
    warehouse_id: int | None = None,
    branch_id: int | None = None,
    transaction_type_id: int | None = None,
    project_id: int | None = None,
    provisional_only: bool = False,
    cursor: int | None = None,
    limit: int = 100,
) -> TransactionReport:
    """The moves themselves, in date then posting order, with the drill-down keys.

    Ordered `(move_date, sequence_no)` — the same order the item enquiry lists in, so a figure
    read off one is the figure on the other.
    """
    warehouses, allowed = _warehouse_scope(
        db, company_id, warehouse_id=warehouse_id, branch_id=branch_id
    )

    def _scoped(statement: Select) -> Select:
        statement = statement.where(
            StockMove.company_id == company_id,
            StockMove.move_date >= date_from,
            StockMove.move_date <= date_to,
        )
        if allowed is not None:
            statement = statement.where(StockMove.warehouse_id.in_(allowed or {-1}))
        if item_id is not None:
            statement = statement.where(StockMove.item_id == item_id)
        if transaction_type_id is not None:
            statement = statement.where(StockMove.transaction_type_id == transaction_type_id)
        if project_id is not None:
            statement = statement.where(StockMove.project_id == project_id)
        if provisional_only:
            statement = statement.where(StockMove.cost_provisional.is_(True))
        return statement

    totals = db.execute(
        _scoped(
            select(
                func.coalesce(func.sum(StockMove.quantity), ZERO),
                func.coalesce(func.sum(StockMove.value), ZERO),
                func.count(StockMove.id),
            )
        )
    ).one()

    order_key = tuple_(StockMove.move_date, StockMove.sequence_no)
    query = _scoped(select(StockMove))
    if cursor is not None:
        anchor = db.execute(
            select(StockMove.move_date, StockMove.sequence_no).where(
                StockMove.id == cursor, StockMove.company_id == company_id
            )
        ).one_or_none()
        if anchor is None:
            raise NotFoundError("Cursor move not found")
        query = query.where(order_key > tuple_(*anchor))
    listed = list(
        db.scalars(query.order_by(StockMove.move_date, StockMove.sequence_no).limit(limit + 1))
    )
    has_more = len(listed) > limit
    page = listed[:limit]

    items = {
        row.id: row
        for row in db.scalars(
            select(Item).where(
                Item.company_id == company_id,
                Item.id.in_({move.item_id for move in page} or {-1}),
            )
        )
    }
    entry_ids = {move.journal_entry_id for move in page if move.journal_entry_id}
    entries = {
        entry_id: number
        for entry_id, number in db.execute(
            select(JournalEntry.id, JournalEntry.number).where(
                JournalEntry.company_id == company_id, JournalEntry.id.in_(entry_ids or {-1})
            )
        ).all()
    }

    rows = [
        TransactionRow(
            move_id=move.id,
            move_date=move.move_date,
            sequence_no=move.sequence_no,
            item_id=move.item_id,
            item_code=items[move.item_id].code,
            item_name=items[move.item_id].name,
            warehouse_id=move.warehouse_id,
            warehouse_code=warehouses[move.warehouse_id].code,
            branch_id=warehouses[move.warehouse_id].branch_id,
            quantity=move.quantity,
            unit_cost=move.unit_cost,
            value=move.value,
            cost_provisional=move.cost_provisional,
            project_id=move.project_id,
            transaction_type_id=move.transaction_type_id,
            journal_entry_id=move.journal_entry_id,
            entry_number=entries.get(move.journal_entry_id),
            source_doc_type=move.source_doc_type,
            source_doc_id=move.source_doc_id,
            source_line_id=move.source_line_id,
        )
        for move in page
    ]
    return TransactionReport(
        date_from=date_from,
        date_to=date_to,
        rows=rows,
        next_cursor=page[-1].id if has_more and page else None,
        total_quantity=totals[0],
        total_value=totals[1],
        move_count=int(totals[2]),
    )


# --- Valuation ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ValuationRow:
    item_id: int
    item_code: str
    item_name: str
    warehouse_id: int
    warehouse_code: str
    warehouse_name: str
    branch_id: int
    is_in_transit: bool
    quantity: Decimal
    value: Decimal
    gl_account_id: int | None

    @property
    def unit_cost(self) -> Decimal | None:
        """What this location's stock averages out at — `None` at zero quantity, where the
        question has no answer. Derived for display only; nothing costs off this."""
        if self.quantity == ZERO:
            return None
        return self.value / self.quantity


@dataclass(frozen=True)
class ValuationItemTotal:
    item_id: int
    item_code: str
    item_name: str
    quantity: Decimal
    value: Decimal


@dataclass(frozen=True)
class ValuationAccountTotal:
    """What one inventory account should be showing on `as_of` — the tie to the GL, stated by
    the report itself rather than left for a reconciliation to discover."""

    gl_account_id: int | None
    code: str | None
    name: str | None
    value: Decimal


@dataclass(frozen=True)
class ValuationReport:
    as_of: date
    include_zero: bool
    rows: list[ValuationRow]
    item_totals: list[ValuationItemTotal]
    next_cursor: int | None
    #: Over the whole filtered set — every location, `include_zero` or not. A zero-quantity
    #: location is worth nothing (the invariant says so), so hiding it cannot move a total;
    #: computing the totals unfiltered is what makes that a check rather than an assumption.
    total_value: Decimal
    warehouse_totals: dict[int, Decimal] = field(default_factory=dict)
    account_totals: list[ValuationAccountTotal] = field(default_factory=list)


def valuation_report(
    db: Session,
    company_id: int,
    *,
    as_of: date,
    warehouse_id: int | None = None,
    branch_id: int | None = None,
    item_id: int | None = None,
    include_zero: bool = False,
    cursor: int | None = None,
    limit: int = 100,
) -> ValuationReport:
    """What the company's stock was worth on `as_of`, per item × warehouse.

    The value of a location is the sum of its moves' **frozen** values — what each move was
    actually costed at when it posted — never quantity × today's average. That is why this
    report equals the inventory account: both are sums of the same numbers.

    Zero-quantity lines are off by default (the P3 "Include zero balances" convention).
    """
    warehouses, allowed = _warehouse_scope(
        db, company_id, warehouse_id=warehouse_id, branch_id=branch_id
    )

    query = select(
        StockMove.item_id,
        StockMove.warehouse_id,
        func.sum(StockMove.quantity),
        func.sum(StockMove.value),
    ).where(StockMove.company_id == company_id, StockMove.move_date <= as_of)
    if allowed is not None:
        query = query.where(StockMove.warehouse_id.in_(allowed or {-1}))
    if item_id is not None:
        query = query.where(StockMove.item_id == item_id)
    positions = {
        (item, warehouse): (quantity, value)
        for item, warehouse, quantity, value in db.execute(
            query.group_by(StockMove.item_id, StockMove.warehouse_id)
        ).all()
        if warehouse in warehouses
    }

    accounts = location_accounts(db, company_id, list(positions))
    total_value = ZERO
    warehouse_totals: dict[int, Decimal] = defaultdict(lambda: ZERO)
    account_values: dict[int | None, Decimal] = defaultdict(lambda: ZERO)
    for (_, warehouse), (_, value) in positions.items():
        total_value += value
        warehouse_totals[warehouse] += value
    for location, (_, value) in positions.items():
        account_values[accounts.get(location)] += value

    shown = {
        location
        for location, (quantity, value) in positions.items()
        if include_zero or quantity != ZERO or value != ZERO
    }
    page, items = _item_page(
        db,
        company_id,
        item_ids={item for item, _ in shown},
        item_id=item_id,
        cursor=cursor,
        limit=limit,
    )

    rows = [
        ValuationRow(
            item_id=item,
            item_code=items[item].code,
            item_name=items[item].name,
            warehouse_id=warehouse,
            warehouse_code=warehouses[warehouse].code,
            warehouse_name=warehouses[warehouse].name,
            branch_id=warehouses[warehouse].branch_id,
            is_in_transit=warehouses[warehouse].is_in_transit,
            quantity=positions[(item, warehouse)][0],
            value=positions[(item, warehouse)][1],
            gl_account_id=accounts.get((item, warehouse)),
        )
        for item, warehouse in shown
        if item in items
    ]
    rows.sort(key=lambda row: (row.item_code, row.warehouse_code))

    item_totals: dict[int, tuple[Decimal, Decimal]] = defaultdict(lambda: (ZERO, ZERO))
    for row in rows:
        quantity, value = item_totals[row.item_id]
        item_totals[row.item_id] = (quantity + row.quantity, value + row.value)

    return ValuationReport(
        as_of=as_of,
        include_zero=include_zero,
        rows=rows,
        item_totals=[
            ValuationItemTotal(
                item_id=item,
                item_code=items[item].code,
                item_name=items[item].name,
                quantity=quantity,
                value=value,
            )
            for item, (quantity, value) in sorted(
                item_totals.items(), key=lambda pair: items[pair[0]].code
            )
        ],
        next_cursor=page.next_cursor,
        total_value=total_value,
        warehouse_totals=dict(warehouse_totals),
        account_totals=_account_totals(db, company_id, account_values),
    )


def _account_totals(
    db: Session, company_id: int, values: dict[int | None, Decimal]
) -> list[ValuationAccountTotal]:
    known = {
        row.id: row
        for row in db.scalars(
            select(GLAccount).where(
                GLAccount.company_id == company_id,
                GLAccount.id.in_({key for key in values if key is not None} or {-1}),
            )
        )
    }
    totals = [
        ValuationAccountTotal(
            gl_account_id=account_id,
            code=None if account_id is None else known[account_id].code,
            name=None if account_id is None else known[account_id].name,
            value=value,
        )
        for account_id, value in values.items()
        if account_id is None or account_id in known
    ]
    totals.sort(key=lambda total: (total.code is None, total.code or ""))
    return totals


# --- Count -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CountVarianceRow:
    line_id: int
    line_no: int
    item_id: int
    item_code: str
    item_name: str
    system_quantity: Decimal
    counted_quantity: Decimal | None
    variance: Decimal | None
    stale: bool
    stock_move_id: int | None


@dataclass(frozen=True)
class CountSessionRow:
    session_id: int
    number: str
    warehouse_id: int
    warehouse_code: str
    warehouse_name: str
    branch_id: int
    count_date: date
    description: str
    status: StockCountStatus
    snapshot_at: object
    document_id: int | None
    document_number: str | None
    journal_entry_id: int | None
    line_count: int
    counted_count: int
    variance_count: int
    lines: list[CountVarianceRow]


@dataclass(frozen=True)
class CountReport:
    rows: list[CountSessionRow]
    next_cursor: int | None


def count_report(
    db: Session,
    company_id: int,
    *,
    status: StockCountStatus | None = None,
    warehouse_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    with_lines: bool = True,
    variances_only: bool = False,
    cursor: int | None = None,
    limit: int = 50,
) -> CountReport:
    """Count sessions with their lines and variances, and the document each one posted.

    Staleness is recomputed here rather than stored: a line goes stale because something else
    moved, so the only honest answer is the one taken at the moment the report is read.
    """
    sessions, next_cursor = counts_service.list_sessions(
        db,
        company_id,
        status=status,
        warehouse_id=warehouse_id,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
        limit=limit,
    )
    warehouses = _warehouses(db, company_id)
    documents = _count_documents(db, company_id, sessions)

    rows: list[CountSessionRow] = []
    for session in sessions:
        lines = counts_service.lines_of(db, company_id, session.id)
        stale = counts_service.stale_lines(db, company_id, session, lines)
        item_codes = _item_codes(db, company_id, {line.item_id for line in lines})
        variance_count = sum(
            1 for line in lines if (counts_service.variance_of(line) or ZERO) != ZERO
        )
        rows.append(
            CountSessionRow(
                session_id=session.id,
                number=session.number,
                warehouse_id=session.warehouse_id,
                warehouse_code=warehouses[session.warehouse_id].code,
                warehouse_name=warehouses[session.warehouse_id].name,
                branch_id=warehouses[session.warehouse_id].branch_id,
                count_date=session.count_date,
                description=session.description,
                status=session.status,
                snapshot_at=session.snapshot_at,
                document_id=session.document_id,
                document_number=documents.get(session.document_id, (None, None))[0],
                journal_entry_id=documents.get(session.document_id, (None, None))[1],
                line_count=len(lines),
                counted_count=sum(1 for line in lines if line.counted_quantity is not None),
                variance_count=variance_count,
                lines=(
                    []
                    if not with_lines
                    else [
                        CountVarianceRow(
                            line_id=line.id,
                            line_no=line.line_no,
                            item_id=line.item_id,
                            item_code=item_codes[line.item_id][0],
                            item_name=item_codes[line.item_id][1],
                            system_quantity=line.system_quantity,
                            counted_quantity=line.counted_quantity,
                            variance=counts_service.variance_of(line),
                            stale=line.id in stale,
                            stock_move_id=line.stock_move_id,
                        )
                        for line in lines
                        if not variances_only
                        or (counts_service.variance_of(line) or ZERO) != ZERO
                    ]
                ),
            )
        )
    return CountReport(rows=rows, next_cursor=next_cursor)


def _count_documents(
    db: Session, company_id: int, sessions: list[StockCountSession]
) -> dict[int, tuple[str, int | None]]:
    wanted = {session.document_id for session in sessions if session.document_id}
    if not wanted:
        return {}
    return {
        row.id: (row.number, row.journal_entry_id)
        for row in db.scalars(
            select(InventoryDocument).where(
                InventoryDocument.company_id == company_id, InventoryDocument.id.in_(wanted)
            )
        )
    }


def _item_codes(db: Session, company_id: int, item_ids: set[int]) -> dict[int, tuple[str, str]]:
    if not item_ids:
        return {}
    return {
        row.id: (row.code, row.name)
        for row in db.scalars(
            select(Item).where(Item.company_id == company_id, Item.id.in_(item_ids))
        )
    }
