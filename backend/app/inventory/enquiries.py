"""Item enquiry (P5 step 5; Appendix C: Enquiries → Inventory → Item enquiry).

What one item is worth and where it sits, and every move that got it there — read from
`stock_moves` and never from the caches, so the answer is correct at any date including one
in the middle of a period.

Two orders exist in this module and they are not the same order.

* **Posting order** (`sequence_no`) is the order the costing engine worked in, and the order
  a move's own value was decided in. A backdated receipt is late in posting order however
  early its date.
* **Date order** is what a person means by "the position on the 15th", and what the GL means
  by it too, which is why `balances_as_of` reconstructs by `move_date` and the valuation
  report ties to the account.

The enquiry lists in **date order, posting order as the tie-break**, so the running quantity
and value at the bottom of a page are the position on that date — the same figure the
valuation report and the inventory account would give for it. Ordering by posting order
instead would show a running total that disagrees with both for as long as a backdated move
was in view, which is exactly the kind of "the screen says something the ledger does not"
defect rule 13 exists to catch. Each move still carries its `sequence_no`, so the costing
order remains visible where it matters: on the row that got a value the date order cannot
explain.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, func, select, tuple_
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.inventory import masters
from app.inventory.costing import ItemState, LocationState
from app.models.gl import GLTransactionType
from app.models.inventory import Item, StockMove, Warehouse
from app.models.journal import JournalEntry
from app.order_entry import quantities as order_quantities
from app.order_entry import sources as order_sources
from app.order_entry.sources import SourceDocument

ZERO = Decimal(0)


@dataclass(frozen=True)
class LocationPosition:
    """One (item, warehouse) cell of the enquiry's top half.

    `quantity` is what is on the shelf; the three P6 columns beside it are what has been
    promised out of it and what is coming in (decision 4). Each is a query over open order
    lines — `app.order_entry.quantities` — and none of them is a column on any table.

    `available` may be **negative**, and the enquiry shows it that way. That is a backorder: the
    company has sold more than it holds, which `backorder_policy = allow` permits and which this
    screen exists to make visible. Clamping it at zero would hide the only number a buyer
    actually needs.
    """

    warehouse_id: int
    warehouse_code: str
    warehouse_name: str
    branch_id: int
    is_in_transit: bool
    quantity: Decimal
    value: Decimal
    #: Σ (ordered − invoiced) over the lines of open sales orders at this warehouse.
    committed: Decimal = ZERO
    #: Σ (ordered − received) over the lines of open purchase orders at this warehouse.
    on_order: Decimal = ZERO

    @property
    def available(self) -> Decimal:
        return self.quantity - self.committed


@dataclass(frozen=True)
class MoveRow:
    """One move, with the drill-down keys the screen needs to get anywhere from here.

    `unit_cost` is the move's own posted cost — the real one, frozen at posting. The enquiry
    deliberately carries **no** running average: `running_value / running_quantity` is the
    ratio on that date, not the cost the issues above it were posted at, and a backdated
    receipt makes the two differ in view. On the owner's direction at the step-5 gate, step 8
    either omits such a column or labels it as the as-at ratio — never as "cost".
    """

    move_id: int
    move_date: date
    sequence_no: int
    warehouse_id: int
    warehouse_code: str
    quantity: Decimal
    unit_cost: Decimal | None
    value: Decimal
    running_quantity: Decimal
    running_value: Decimal
    cost_provisional: bool
    project_id: int | None
    journal_entry_id: int | None
    entry_number: str | None
    transaction_type_id: int | None
    transaction_type_code: str | None
    transaction_type_name: str | None
    source_doc_type: str | None
    source_doc_id: int | None
    source_line_id: int | None
    reverses_move_id: int | None
    #: The source document resolved to its number and a routing key, or `None` when the move
    #: names no source or names one this phase cannot open (P6 step 5). The raw pair above
    #: stays: it is what the move actually carries, and a screen that wants to show an
    #: unresolvable source can still say what it was.
    source: SourceDocument | None = None


@dataclass(frozen=True)
class ItemEnquiry:
    item: Item
    as_of: date
    date_from: date | None
    warehouse_id: int | None
    provisional_only: bool
    locations: list[LocationPosition]
    #: The item's totals across every location as at `as_of` — including any the warehouse
    #: filter hides, because the average is an item-wide number and showing one warehouse
    #: must not change it.
    state: ItemState
    opening_quantity: Decimal
    opening_value: Decimal
    moves: list[MoveRow]
    next_cursor: int | None

    @property
    def total_quantity(self) -> Decimal:
        return self.state.quantity

    @property
    def total_value(self) -> Decimal:
        return self.state.value

    @property
    def average_cost(self) -> Decimal:
        return self.state.average


def _positions(
    db: Session, company_id: int, item_id: int, *, as_of: date
) -> tuple[dict[int, LocationState], ItemState]:
    """Every location this item sits in as at `as_of`, and the item-wide state that follows.

    `last_positive_average` is replayed from the moves rather than read off `item_cost_state`:
    the cached one is the average *now*, and an enquiry as at a past date that borrowed it
    would report an average the company did not have on that date. Replay is in posting
    order, because that is the order the average was ever computed in.
    """
    rows = db.execute(
        select(StockMove.warehouse_id, func.sum(StockMove.quantity), func.sum(StockMove.value))
        .where(
            StockMove.company_id == company_id,
            StockMove.item_id == item_id,
            StockMove.move_date <= as_of,
        )
        .group_by(StockMove.warehouse_id)
    ).all()
    positions = {
        warehouse_id: LocationState(quantity=quantity, value=value)
        for warehouse_id, quantity, value in rows
    }

    state = ItemState()
    for quantity, value in db.execute(
        select(StockMove.quantity, StockMove.value)
        .where(
            StockMove.company_id == company_id,
            StockMove.item_id == item_id,
            StockMove.move_date <= as_of,
        )
        .order_by(StockMove.sequence_no)
    ).all():
        state = state.after(quantity, value)
    return positions, state


def _every_location(
    positions: dict[int, LocationState],
    committed: dict[int, Decimal],
    on_order: dict[int, Decimal],
) -> dict[int, LocationState | None]:
    """Warehouse → its stock position, or `None` where it holds none of the item but has a
    commitment or an order against it. The union of the three, so a location that is empty and
    oversold still appears on the enquiry with its backorder visible."""
    out: dict[int, LocationState | None] = dict(positions)
    for warehouse_id in set(committed) | set(on_order):
        out.setdefault(warehouse_id, None)
    return out


def item_enquiry(
    db: Session,
    company_id: int,
    item_id: int,
    *,
    as_of: date,
    date_from: date | None = None,
    warehouse_id: int | None = None,
    provisional_only: bool = False,
    include_zero_locations: bool = False,
    cursor: int | None = None,
    limit: int = 100,
) -> ItemEnquiry:
    """The position per warehouse as at `as_of`, and the moves that made it.

    `date_from` narrows the *listing* only: the position, the opening figures and the average
    are always the whole history up to `as_of`, so narrowing the window never changes what the
    item is worth — it changes which moves you can see.
    """
    item = masters.get_item(db, company_id, item_id)
    positions, state = _positions(db, company_id, item_id, as_of=as_of)

    warehouses = {
        row.id: row
        for row in db.scalars(select(Warehouse).where(Warehouse.company_id == company_id))
    }
    if warehouse_id is not None and warehouse_id not in warehouses:
        raise NotFoundError("Warehouse not found")

    # Committed and on order are read per item, not per location: a warehouse holding none of
    # the item can still have 100 on order against it, and a listing built only from
    # `stock_balances` would show the buyer nothing at all.
    committed = order_quantities.committed_by_warehouse(db, company_id, item_id)
    on_order = order_quantities.on_order_by_warehouse(db, company_id, item_id)
    locations = [
        LocationPosition(
            warehouse_id=known,
            warehouse_code=warehouses[known].code,
            warehouse_name=warehouses[known].name,
            branch_id=warehouses[known].branch_id,
            is_in_transit=warehouses[known].is_in_transit,
            quantity=position.quantity if position is not None else ZERO,
            value=position.value if position is not None else ZERO,
            committed=committed.get(known, ZERO),
            on_order=on_order.get(known, ZERO),
        )
        for known, position in _every_location(positions, committed, on_order).items()
        if known in warehouses
        and (warehouse_id is None or known == warehouse_id)
        and (
            include_zero_locations
            or (position is not None and (position.quantity != ZERO or position.value != ZERO))
            or committed.get(known, ZERO) != ZERO
            or on_order.get(known, ZERO) != ZERO
        )
    ]
    locations.sort(key=lambda row: row.warehouse_code)

    moves, opening, next_cursor = _moves(
        db,
        company_id,
        item_id,
        as_of=as_of,
        date_from=date_from,
        warehouse_id=warehouse_id,
        provisional_only=provisional_only,
        warehouses=warehouses,
        cursor=cursor,
        limit=limit,
    )
    return ItemEnquiry(
        item=item,
        as_of=as_of,
        date_from=date_from,
        warehouse_id=warehouse_id,
        provisional_only=provisional_only,
        locations=locations,
        state=state,
        opening_quantity=opening.quantity,
        opening_value=opening.value,
        moves=moves,
        next_cursor=next_cursor,
    )


def _moves(
    db: Session,
    company_id: int,
    item_id: int,
    *,
    as_of: date,
    date_from: date | None,
    warehouse_id: int | None,
    provisional_only: bool,
    warehouses: dict[int, Warehouse],
    cursor: int | None,
    limit: int,
) -> tuple[list[MoveRow], LocationState, int | None]:
    """The move listing, its opening brought-forward, and the cursor for the next page.

    The running totals are the *listed* moves' running totals: they continue from the opening
    figure and from the cursor, so page two starts where page one stopped. The provisional
    filter deliberately does **not** change the opening or the running total — hiding the
    unflagged moves would make the running column a total of nothing in particular. It filters
    which rows are shown; the column keeps counting every move in the window.
    """

    def _scoped(statement: Select) -> Select:
        statement = statement.where(
            StockMove.company_id == company_id, StockMove.item_id == item_id
        )
        if warehouse_id is not None:
            statement = statement.where(StockMove.warehouse_id == warehouse_id)
        return statement

    opening = LocationState()
    if date_from is not None:
        before = db.execute(
            _scoped(
                select(
                    func.coalesce(func.sum(StockMove.quantity), ZERO),
                    func.coalesce(func.sum(StockMove.value), ZERO),
                ).where(StockMove.move_date < date_from)
            )
        ).one()
        opening = LocationState(quantity=before[0], value=before[1])

    order_key = tuple_(StockMove.move_date, StockMove.sequence_no)
    window = _scoped(select(StockMove).where(StockMove.move_date <= as_of))
    if date_from is not None:
        window = window.where(StockMove.move_date >= date_from)

    running = opening
    if cursor is not None:
        anchor = db.execute(
            select(StockMove.move_date, StockMove.sequence_no).where(
                StockMove.id == cursor, StockMove.company_id == company_id
            )
        ).one_or_none()
        if anchor is None:
            raise NotFoundError("Cursor move not found")
        consumed_query = _scoped(
            select(
                func.coalesce(func.sum(StockMove.quantity), ZERO),
                func.coalesce(func.sum(StockMove.value), ZERO),
            ).where(StockMove.move_date <= as_of, order_key <= tuple_(*anchor))
        )
        if date_from is not None:
            consumed_query = consumed_query.where(StockMove.move_date >= date_from)
        consumed = db.execute(consumed_query).one()
        running = running.after(consumed[0], consumed[1])
        window = window.where(order_key > tuple_(*anchor))

    listed = list(
        db.scalars(
            window.order_by(StockMove.move_date, StockMove.sequence_no).limit(limit + 1)
        )
    )
    has_more = len(listed) > limit
    page = listed[:limit]

    types = _transaction_types(db, company_id, page)
    entries = _entry_numbers(db, company_id, page)
    # One query per source type present on the page, not one per row.
    sources = order_sources.resolve(
        db,
        company_id,
        (
            (move.source_doc_type, move.source_doc_id)
            for move in page
            if move.source_doc_type is not None and move.source_doc_id is not None
        ),
    )

    rows: list[MoveRow] = []
    for move in page:
        running = running.after(move.quantity, move.value)
        if provisional_only and not move.cost_provisional:
            continue
        txn_type = types.get(move.transaction_type_id)
        rows.append(
            MoveRow(
                move_id=move.id,
                move_date=move.move_date,
                sequence_no=move.sequence_no,
                warehouse_id=move.warehouse_id,
                warehouse_code=warehouses[move.warehouse_id].code,
                quantity=move.quantity,
                unit_cost=move.unit_cost,
                value=move.value,
                running_quantity=running.quantity,
                running_value=running.value,
                cost_provisional=move.cost_provisional,
                project_id=move.project_id,
                journal_entry_id=move.journal_entry_id,
                entry_number=entries.get(move.journal_entry_id),
                transaction_type_id=move.transaction_type_id,
                transaction_type_code=None if txn_type is None else txn_type.code,
                transaction_type_name=None if txn_type is None else txn_type.name,
                source_doc_type=move.source_doc_type,
                source_doc_id=move.source_doc_id,
                source_line_id=move.source_line_id,
                reverses_move_id=move.reverses_move_id,
                source=sources.get((move.source_doc_type, move.source_doc_id)),
            )
        )
    # The cursor is the last move of the *page*, not of the filtered rows: paging has to
    # continue past moves the provisional filter hid, or the next page would repeat them.
    return rows, opening, page[-1].id if has_more and page else None


def _transaction_types(
    db: Session, company_id: int, moves: list[StockMove]
) -> dict[int, GLTransactionType]:
    wanted = {move.transaction_type_id for move in moves if move.transaction_type_id}
    if not wanted:
        return {}
    return {
        row.id: row
        for row in db.scalars(
            select(GLTransactionType).where(
                GLTransactionType.company_id == company_id, GLTransactionType.id.in_(wanted)
            )
        )
    }


def _entry_numbers(db: Session, company_id: int, moves: list[StockMove]) -> dict[int, str]:
    wanted = {move.journal_entry_id for move in moves if move.journal_entry_id}
    if not wanted:
        return {}
    return {
        entry_id: number
        for entry_id, number in db.execute(
            select(JournalEntry.id, JournalEntry.number).where(
                JournalEntry.company_id == company_id, JournalEntry.id.in_(wanted)
            )
        ).all()
    }
