"""The stock service — the only writer of `stock_moves` and of the two caches it proves.

`stock_moves` is to inventory what `journal_lines` is to the general ledger (decision 1):
append-only, signed, and the single source of truth for what is on hand and what it is worth.
`stock_balances` and `item_cost_state` are caches of it, in the same sense `period_balances`
is a cache of the journal — written here, never anywhere else, and re-derivable at any moment
by `verify_stock_balances()`.

**One posting, one entry, one transaction.** Every primitive here values its moves, posts one
journal entry through the PostingEngine, and writes the moves linked to the lines of that
entry — all inside the caller's transaction, which commits the document and the ledger
together or neither. A move that carries value cannot exist without its journal line: a check
constraint says so, which is what makes "stock valuation == inventory GL balance" a property
of the schema rather than of a nightly reconciliation.

**What the entry looks like** (decision 3). One inventory-account line per valued move,
carrying the item, the warehouse's branch and the project; then the contra lines the
transaction type names, grouped per (account, branch, project) so branches stay square. A
transfer leg needs no contra at all — its two moves *are* each other's contra, one on the
inventory account and one on the in-transit account — which is why the contra is derived from
what is left over rather than stipulated per kind.

**Which inventory account.** Stock in the in-transit warehouse belongs to
`inventory_in_transit_account`; everywhere else the item's own `inventory_account_id`, and
failing that the company's `inventory_account`. That mapping depends on the warehouse, not
only the item, which is why the account-determination chain's item link cannot resolve it and
the service passes the account explicitly.

**Ordering.** Costing is in *posting order*, never date order (decision 4). The
`item_cost_state` row is the lock that makes that order real: it is taken `FOR UPDATE` before
anything is valued, so two postings against one item queue instead of both reading the same
average. Locks are taken in item order, then warehouse order, so two postings that touch the
same items in different orders cannot deadlock.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFoundError
from app.inventory.costing import (
    ItemState,
    LocationState,
    crossing_residue,
    stranded_value,
    value_move,
)
from app.kernel import posting
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.events import (
    LineSpec,
    StockAdjusted,
    StockIssued,
    StockJournal,
    StockReceived,
    StockRevalued,
    StockTransferred,
)
from app.kernel.money import ZERO, base_currency
from app.kernel.periods import lock_period_for_posting
from app.kernel.posting import gl_settings_for
from app.kernel.sequences import DocType
from app.models.gl import ControlType, GLAccount, GLSettings, GLTransactionType
from app.models.inventory import (
    INVENTORY_MODULE,
    STOCK_SEQUENCE,
    Item,
    ItemCostState,
    ItemType,
    NegativeStockPolicy,
    StockBalance,
    StockMove,
    Warehouse,
)
from app.models.journal import JournalEntry, JournalLine, JournalStatus
from app.models.user import User

Location = tuple[int, int]  # (item_id, warehouse_id)


# --- Inputs ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class StockDocument:
    """What the ledger needs to know about the thing that caused these moves.

    `source_doc_type` / `source_doc_id` are the link back: the journal entry and every move
    carry them, so an entry found in the GL enquiry leads to the adjustment or count that
    produced it, and a count leads to its entry. P6 passes its own document here without any
    change to the primitives (decision 13).
    """

    doc_type: str
    move_date: date
    description: str
    transaction_type_id: int | None = None
    reference: str | None = None
    source_doc_type: str | None = None
    source_doc_id: int | None = None
    idempotency_key: str | None = None
    idempotency_hash: str | None = None


@dataclass(frozen=True)
class StockLine:
    """One line of a stock posting, in the item's **base unit** — conversion from whatever
    unit it was typed in happens in `masters.to_base_quantity` before it gets here.

    `quantity` is a magnitude: the direction comes from the primitive that is called, so a
    caller cannot accidentally issue by passing a negative receipt. `value` is a frozen value
    — a revaluation's amount, or the value a transfer's receive leg inherits from its
    dispatch.
    """

    item_id: int
    warehouse_id: int
    quantity: Decimal = ZERO
    unit_cost: Decimal | None = None
    value: Decimal | None = None
    transaction_type_id: int | None = None
    contra_account_id: int | None = None
    project_id: int | None = None
    description: str | None = None
    source_line_id: int | None = None


@dataclass(frozen=True)
class SignedLine:
    """A line whose direction is already decided — what the core works in. `quantity` is
    signed; zero means a revaluation, which must then carry a `value`."""

    line: StockLine
    quantity: Decimal
    #: A transfer leg's moves balance each other, so they take no contra line.
    needs_contra: bool = True
    #: The receive leg of a transfer: worth exactly what the line before it gave up
    #: (decision 4). Resolved during planning, under the costing lock, so the two legs
    #: cannot be separated by another posting.
    frozen_from_previous: bool = False
    #: A correction the service raised itself — the variance that clears value a reversal
    #: stranded. It is the one zero-quantity move allowed at an empty location, because
    #: emptying that location is precisely what it is there to finish.
    is_correction: bool = False


@dataclass(frozen=True)
class StockPosting:
    """What a primitive produced: the journal entry, and the moves that hang off it."""

    #: None when nothing in the posting carried value — the moves are there, the ledger has
    #: nothing to record, and so there is no entry. An `Idempotency-Key` has nowhere to live
    #: in that case: the key is a column on `journal_entries`, so a valueless posting is not
    #: replay-protected by the kernel and its caller has to carry that itself (the step-3
    #: documents do, on their own unique key).
    entry: JournalEntry | None
    moves: list[StockMove]
    #: The moves that correspond one-for-one, in order, with the lines the caller passed in.
    #: This is *not* `moves`: the service raises moves of its own — the variance that settles
    #: a negative-stock crossing — and interleaves them with the keyed ones. A caller that
    #: needs to say "this line became that move" (a document writing its lines) must use this
    #: list, because counting positions in `moves` silently shifts the moment a residue lands.
    keyed_moves: list[StockMove] = dc_field(default_factory=list)
    #: True when an `Idempotency-Key` resolved to a posting that already existed. The moves
    #: are the ones written the first time; nothing was posted again.
    replayed: bool = False


# --- Errors ----------------------------------------------------------------------------------


def _on_line(index: int, error: AppError) -> AppError:
    """Re-key an error's field errors onto the line that caused it, the way the Posting
    Engine does for its own line failures. A batch refused whole still has to say *which*
    line was wrong, or the entry grid has nowhere to put the message."""
    error.field_errors = {
        (key if key.startswith("lines.") or key == "lines" else f"lines.{index}.{key}"): value
        for key, value in error.field_errors.items()
    }
    return error


def _insufficient_stock(
    item: Item, warehouse: Warehouse, available: Decimal, requested: Decimal
) -> LedgerStateError:
    """Decision 5's refusal under the `block` policy. Raised by an issue, by a transfer's
    dispatch leg and by a reversal that would undo stock that has since gone out — all three
    are the same event seen from a location: more leaving than is there."""
    return LedgerStateError(
        f"{warehouse.code} holds {available:f} of {item.code}; this posting takes out "
        f"{requested:f}",
        code="insufficient_stock",
        field_errors={"quantity": [f"only {available:f} on hand at {warehouse.code}"]},
    )


# --- Reading the caches ------------------------------------------------------------------------


def location_balance(
    db: Session, company_id: int, item_id: int, warehouse_id: int
) -> LocationState:
    row = db.scalar(
        select(StockBalance).where(
            StockBalance.company_id == company_id,
            StockBalance.item_id == item_id,
            StockBalance.warehouse_id == warehouse_id,
        )
    )
    return LocationState() if row is None else LocationState(row.quantity, row.value)


def item_state(db: Session, company_id: int, item_id: int) -> ItemState:
    row = db.scalar(
        select(ItemCostState).where(
            ItemCostState.company_id == company_id, ItemCostState.item_id == item_id
        )
    )
    totals = db.execute(
        select(
            func.coalesce(func.sum(StockBalance.quantity), ZERO),
            func.coalesce(func.sum(StockBalance.value), ZERO),
        ).where(StockBalance.company_id == company_id, StockBalance.item_id == item_id)
    ).one()
    return ItemState(
        quantity=totals[0],
        value=totals[1],
        last_positive_average=ZERO if row is None else row.last_positive_average_cost,
    )


def has_moves(db: Session, company_id: int, item_id: int) -> bool:
    """Whether anything has ever been posted against this item.

    The masters use it as the line between "still a draft of an item" and "a thing with
    history": an item's type and unit of measure are free to change until a quantity has been
    posted against them and locked from that moment, exactly as a tax code's rate is.
    """
    return (
        db.scalar(
            select(StockMove.id)
            .where(StockMove.company_id == company_id, StockMove.item_id == item_id)
            .limit(1)
        )
        is not None
    )


# --- The single-writer guard -------------------------------------------------------------------


def _stock_service(db: Session, *, on: bool) -> None:
    """Open (or shut) the window in which the stock tables accept writes, the same way
    `posting._write` does for the journal (ADR-05, migration 0004). Transaction-local, so a
    rolled-back posting cannot leave it open, and shut again as soon as the writes are done so
    the rest of the caller's transaction stays guarded."""
    db.execute(
        text("SELECT set_config('app.stock_service', :value, true)"),
        {"value": "on" if on else "off"},
    )


# --- Context ------------------------------------------------------------------------------------


class _Context:
    """Everything one posting needs to look up, loaded once and locked in a fixed order."""

    def __init__(self, db: Session, company_id: int, actor: User | None = None) -> None:
        self.db = db
        self.company_id = company_id
        self.actor = actor
        self.settings: GLSettings = gl_settings_for(db, company_id)
        self.decimal_places = base_currency(db, company_id).decimal_places
        self.items: dict[int, Item] = {}
        self.warehouses: dict[int, Warehouse] = {}
        self.transaction_types: dict[int, GLTransactionType] = {}
        self.accounts: dict[int, GLAccount] = {}
        self.cost_states: dict[int, ItemCostState] = {}
        self.balances: dict[Location, StockBalance] = {}
        self.item_states: dict[int, ItemState] = {}
        self.location_states: dict[Location, LocationState] = {}

    # -- masters ---------------------------------------------------------------------------

    def item(self, item_id: int) -> Item:
        if item_id not in self.items:
            item = self.db.get(Item, item_id)
            if item is None or item.company_id != self.company_id:
                raise NotFoundError("Item not found")
            if item.item_type != ItemType.STOCK:
                raise LedgerStateError(
                    f"{item.code} is a {item.item_type.value} item; only stock items move",
                    code="item_not_stocked",
                    field_errors={"item_id": ["not a stock item"]},
                )
            if not item.is_active:
                raise LedgerStateError(
                    f"{item.code} is not active",
                    code="item_inactive",
                    field_errors={"item_id": ["not active"]},
                )
            self.items[item_id] = item
        return self.items[item_id]

    def warehouse(self, warehouse_id: int) -> Warehouse:
        if warehouse_id not in self.warehouses:
            warehouse = self.db.get(Warehouse, warehouse_id)
            if warehouse is None or warehouse.company_id != self.company_id:
                raise NotFoundError("Warehouse not found")
            if not warehouse.is_active:
                raise LedgerStateError(
                    f"{warehouse.code} is not active",
                    code="warehouse_inactive",
                    field_errors={"warehouse_id": ["not active"]},
                )
            self.warehouses[warehouse_id] = warehouse
        return self.warehouses[warehouse_id]

    def transaction_type(self, type_id: int) -> GLTransactionType:
        if type_id not in self.transaction_types:
            row = self.db.get(GLTransactionType, type_id)
            if row is None or row.company_id != self.company_id or row.module != INVENTORY_MODULE:
                raise NotFoundError("Inventory transaction type not found")
            if not row.is_active:
                raise LedgerStateError(
                    f"Transaction type {row.code} is not active",
                    code="transaction_type_inactive",
                    field_errors={"transaction_type_id": ["not active"]},
                )
            self.transaction_types[type_id] = row
        return self.transaction_types[type_id]

    def account(self, account_id: int) -> GLAccount:
        if account_id not in self.accounts:
            account = self.db.get(GLAccount, account_id)
            if account is None or account.company_id != self.company_id:
                raise NotFoundError("GL account not found")
            self.accounts[account_id] = account
        return self.accounts[account_id]

    # -- account resolution ------------------------------------------------------------------

    def inventory_account_for(self, item: Item, warehouse: Warehouse) -> int:
        """Decision 6's one warehouse-level override, and decision 8's item default."""
        if warehouse.is_in_transit:
            return self._required_setting("inventory_in_transit_account_id")
        if item.inventory_account_id is not None:
            return item.inventory_account_id
        return self._required_setting("inventory_account_id")

    def _required_setting(self, field_name: str) -> int:
        """A NULL inventory key means inventory refuses to start rather than posting into an
        account nothing is guarding. The tenants that can reach this are the ones migration
        0012 would not mark — see `docs/ops/inventory-control-account.md` for the way out."""
        value = getattr(self.settings, field_name)
        if value is None:
            raise PostingError(
                f"The {field_name.removesuffix('_id').replace('_', ' ')} is not set; "
                "inventory cannot post without it",
                code="gl_setting_missing",
                field_errors={field_name: ["required"]},
            )
        return int(value)

    def residue_account(self) -> int:
        """Where a negative-stock cost residue lands (see `costing.residue_move`)."""
        return self._required_setting("inventory_adjustment_account_id")

    def contra_account_for(self, line: StockLine, type_id: int | None) -> int:
        if line.contra_account_id is not None:
            account_id = line.contra_account_id
        else:
            if type_id is None:
                raise PostingError(
                    "A stock line needs a transaction type or an explicit contra account",
                    code="transaction_type_required",
                    field_errors={"transaction_type_id": ["required"]},
                )
            default = self.transaction_type(type_id).default_gl_account_id
            if default is None:
                raise PostingError(
                    f"Transaction type {self.transaction_type(type_id).code} has no contra "
                    "account",
                    code="contra_account_undetermined",
                    field_errors={"contra_account_id": ["required"]},
                )
            account_id = int(default)
        account = self.account(account_id)
        if account.control_type == ControlType.INVENTORY:
            # Both legs on an inventory account would post an entry that moves stock nowhere,
            # and the second leg would have no move to stand behind it.
            raise PostingError(
                f"Account {account.code} is an inventory control account and cannot be a "
                "contra",
                code="contra_is_inventory_account",
                field_errors={"contra_account_id": ["cannot be an inventory account"]},
            )
        return account_id

    # -- locking the caches --------------------------------------------------------------------

    def lock(self, locations: Sequence[Location]) -> None:
        """Take the costing locks, in a fixed order so concurrent postings cannot deadlock."""
        item_ids = sorted({item_id for item_id, _ in locations})
        for item_id in item_ids:
            self.cost_states[item_id] = self._lock_cost_state(item_id)
        for location in sorted(set(locations)):
            self.balances[location] = self._lock_balance(*location)
        for item_id in item_ids:
            state = self.cost_states[item_id]
            totals = self.db.execute(
                select(
                    func.coalesce(func.sum(StockBalance.quantity), ZERO),
                    func.coalesce(func.sum(StockBalance.value), ZERO),
                ).where(
                    StockBalance.company_id == self.company_id, StockBalance.item_id == item_id
                )
            ).one()
            self.item_states[item_id] = ItemState(
                quantity=totals[0],
                value=totals[1],
                last_positive_average=state.last_positive_average_cost,
            )
        for location, balance in self.balances.items():
            self.location_states[location] = LocationState(balance.quantity, balance.value)

    def _lock_cost_state(self, item_id: int) -> ItemCostState:
        _stock_service(self.db, on=True)
        try:
            self.db.execute(
                insert(ItemCostState)
                .values(company_id=self.company_id, item_id=item_id)
                .on_conflict_do_nothing(constraint="uq_item_cost_state_item")
            )
        finally:
            _stock_service(self.db, on=False)
        return self.db.scalars(
            select(ItemCostState)
            .where(
                ItemCostState.company_id == self.company_id, ItemCostState.item_id == item_id
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()

    def _lock_balance(self, item_id: int, warehouse_id: int) -> StockBalance:
        _stock_service(self.db, on=True)
        try:
            self.db.execute(
                insert(StockBalance)
                .values(company_id=self.company_id, item_id=item_id, warehouse_id=warehouse_id)
                .on_conflict_do_nothing(constraint="uq_stock_balances_location")
            )
        finally:
            _stock_service(self.db, on=False)
        return self.db.scalars(
            select(StockBalance)
            .where(
                StockBalance.company_id == self.company_id,
                StockBalance.item_id == item_id,
                StockBalance.warehouse_id == warehouse_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()


# --- Planning -------------------------------------------------------------------------------


@dataclass
class _Planned:
    """One move, valued, with everything the journal line and the move row need."""

    line: StockLine
    item: Item
    warehouse: Warehouse
    account_id: int
    contra_account_id: int | None
    transaction_type_id: int | None
    quantity: Decimal
    value: Decimal
    unit_cost: Decimal | None
    cost_provisional: bool = False
    is_residue: bool = False
    journal_line_index: int | None = None


def _plan(
    ctx: _Context, document: StockDocument, moves: Sequence[SignedLine], policy: NegativeStockPolicy
) -> list[_Planned]:
    """Value every move in order, mutating the running state as each one lands.

    In order, because that *is* the costing rule: the average a line is charged at is the one
    left behind by the line before it, and the negative-stock policy is evaluated per location
    as each line lands rather than on the document's net effect (decision 5).
    """
    planned: list[_Planned] = []
    previous_value: Decimal | None = None
    for index, signed in enumerate(moves):
        line = signed.line
        item = ctx.item(line.item_id)
        warehouse = ctx.warehouse(line.warehouse_id)
        location = (item.id, warehouse.id)
        type_id = line.transaction_type_id or document.transaction_type_id
        contra_id = ctx.contra_account_for(line, type_id) if signed.needs_contra else None

        item_state_now = ctx.item_states[item.id]
        location_now = ctx.location_states[location]
        if signed.quantity == ZERO and location_now.quantity == ZERO and not signed.is_correction:
            # A revaluation restates what stock is carried at. With no stock at the location
            # there is nothing to carry, and the value would simply sit there — a location
            # worth something while holding nothing, which is neither reportable nor true.
            raise _on_line(
                index,
                PostingError(
                    f"{warehouse.code} holds none of {item.code}; there is nothing to revalue",
                    code="nothing_to_revalue",
                    field_errors={"value": ["the location holds no stock"]},
                ),
            )
        provisional = False
        if signed.quantity < ZERO:
            resulting = location_now.quantity + signed.quantity
            if resulting < ZERO:
                if policy == NegativeStockPolicy.BLOCK:
                    raise _on_line(
                        index,
                        _insufficient_stock(
                            item, warehouse, location_now.quantity, -signed.quantity
                        ),
                    )
                # `allow`: the issue is costed at the last positive average and flagged. No
                # correction happens when a later receipt restores the quantity — the flag is
                # the review trail (decision 5).
                provisional = True

        frozen = line.value
        if signed.frozen_from_previous:
            if previous_value is None:
                raise PostingError(
                    "A frozen-value line has nothing to take its value from",
                    code="invalid_transfer",
                )
            frozen = -previous_value

        valued = value_move(
            quantity=signed.quantity,
            unit_cost=line.unit_cost,
            frozen_value=frozen,
            item=item_state_now,
            location=location_now,
            decimal_places=ctx.decimal_places,
        )
        planned.append(
            _Planned(
                line=line,
                item=item,
                warehouse=warehouse,
                account_id=ctx.inventory_account_for(item, warehouse),
                contra_account_id=contra_id,
                transaction_type_id=type_id,
                quantity=valued.quantity,
                value=valued.value,
                unit_cost=valued.unit_cost,
                cost_provisional=provisional,
            )
        )
        previous_value = valued.value
        ctx.location_states[location] = location_now.after(valued.quantity, valued.value)
        ctx.item_states[item.id] = item_state_now.after(valued.quantity, valued.value)

        residue = crossing_residue(
            before=location_now,
            after=ctx.location_states[location],
            unit_cost=valued.unit_cost,
            decimal_places=ctx.decimal_places,
        )
        if residue is not None:
            # A stock-valuation variance, whatever the document was doing, so it goes to the
            # inventory adjustment account rather than to this document's contra — a transfer
            # leg has no contra at all, and charging a costing variance to "stock in transit"
            # would be an account saying something untrue.
            planned.append(
                _Planned(
                    line=line,
                    item=item,
                    warehouse=warehouse,
                    account_id=planned[-1].account_id,
                    contra_account_id=ctx.residue_account(),
                    transaction_type_id=type_id,
                    quantity=ZERO,
                    value=residue,
                    unit_cost=None,
                    # Flagged like the issue that caused it. The residue exists only because
                    # a provisional issue took out value the location did not have, so the
                    # provisional filter on the enquiry and the transaction report is exactly
                    # where someone reviewing that guess should find it.
                    cost_provisional=True,
                    is_residue=True,
                )
            )
            ctx.location_states[location] = ctx.location_states[location].after(ZERO, residue)
            ctx.item_states[item.id] = ctx.item_states[item.id].after(ZERO, residue)
    return planned


def _line_specs(document: StockDocument, planned: Sequence[_Planned]) -> list[LineSpec]:
    """The inventory lines, then the contra lines they call for.

    Contras are grouped by (account, branch, project): one adjustment document with four lines
    against the same expense account posts one contra line, and a branch never ends up owing
    itself, which is what the per-branch half of the stock invariant needs.
    """
    specs: list[LineSpec] = []
    index = 0
    for plan in planned:
        if plan.value == ZERO:
            continue
        plan.journal_line_index = index
        index += 1
        specs.append(
            LineSpec(
                amount=plan.value,
                gl_account_id=plan.account_id,
                branch_id=plan.warehouse.branch_id,
                project_id=plan.line.project_id,
                item_id=plan.item.id,
                description=plan.line.description or document.description,
                source_doc_type=document.source_doc_type,
                source_doc_id=document.source_doc_id,
                source_line_id=plan.line.source_line_id,
            )
        )
    contras: dict[tuple[int, int, int | None], Decimal] = {}
    for plan in planned:
        if plan.contra_account_id is None or plan.value == ZERO:
            continue
        key = (plan.contra_account_id, plan.warehouse.branch_id, plan.line.project_id)
        contras[key] = contras.get(key, ZERO) + plan.value
    for (account_id, branch_id, project_id), total in contras.items():
        if total == ZERO:
            continue
        specs.append(
            LineSpec(
                amount=-total,
                gl_account_id=account_id,
                branch_id=branch_id,
                project_id=project_id,
                description=document.description,
                source_doc_type=document.source_doc_type,
                source_doc_id=document.source_doc_id,
            )
        )
    return specs


# --- Posting --------------------------------------------------------------------------------


def post_stock_moves(
    db: Session,
    company_id: int,
    *,
    document: StockDocument,
    moves: Sequence[SignedLine],
    event_class: type[StockJournal] = StockAdjusted,
    actor: User,
) -> StockPosting:
    """Value, post and record one stock document. Never commits.

    The order is deliberate: the period is locked first, so a document dated into a closed
    period is refused *before* a single move exists; then the costing locks; then the journal
    entry; then the moves, which cannot be written without the lines they point at.
    """
    if not moves:
        raise PostingError(
            "A stock posting needs at least one line",
            code="too_few_lines",
            field_errors={"lines": ["at least one line required"]},
        )
    if document.idempotency_key:
        existing = posting.replay(
            db, company_id, document.idempotency_key, document.idempotency_hash
        )
        if existing is not None:
            found = moves_of(db, existing)
            return StockPosting(
                entry=existing, moves=found, keyed_moves=found, replayed=True
            )

    period = lock_period_for_posting(db, company_id, document.move_date)
    ctx = _Context(db, company_id)
    locations = [(line.line.item_id, line.line.warehouse_id) for line in moves]
    for item_id, warehouse_id in locations:
        ctx.item(item_id)
        ctx.warehouse(warehouse_id)
    ctx.lock(locations)

    planned = _plan(ctx, document, moves, ctx.settings.negative_stock_policy)
    specs = _line_specs(document, planned)
    # Quantity moves whether or not value does (decision 1). Stock received at no cost, or
    # issued while the average is zero, is a real change to what is on the shelf and the move
    # ledger is the only record of it — so the move is written either way. What the general
    # ledger has to say about it is nothing: no value changed hands, the Posting Engine will
    # not write a zero-amount line, and an entry with no lines is not an entry. So a posting
    # in which nothing values produces moves and no journal entry, and one in which only some
    # lines value produces an entry carrying exactly those lines.
    entry = (
        posting.post(
            db,
            event_class(
                entry_date=document.move_date,
                description=document.description,
                reference=document.reference,
                doc_type=document.doc_type,
                source_doc_type=document.source_doc_type,
                source_doc_id=document.source_doc_id,
                idempotency_key=document.idempotency_key,
                idempotency_hash=document.idempotency_hash,
                lines=tuple(specs),
            ),
            company_id=company_id,
            actor=actor,
        )
        if specs
        else None
    )
    written = _write_moves(db, ctx, document, planned, entry=entry, period_id=period.id)
    _apply_caches(db, ctx)
    return StockPosting(
        entry=entry,
        moves=written,
        keyed_moves=[
            move for move, plan in zip(written, planned, strict=True) if not plan.is_residue
        ],
    )


def _write_moves(
    db: Session,
    ctx: _Context,
    document: StockDocument,
    planned: Sequence[_Planned],
    *,
    entry: JournalEntry | None,
    period_id: int,
) -> list[StockMove]:
    lines = list(entry.lines) if entry is not None else []
    moves: list[StockMove] = []
    _stock_service(db, on=True)
    try:
        for plan in planned:
            line = (
                lines[plan.journal_line_index]
                if plan.journal_line_index is not None and entry is not None
                else None
            )
            move = StockMove(
                company_id=ctx.company_id,
                item_id=plan.item.id,
                warehouse_id=plan.warehouse.id,
                move_date=document.move_date,
                period_id=period_id,
                sequence_no=_next_sequence(db),
                quantity=plan.quantity,
                unit_cost=plan.unit_cost,
                value=plan.value,
                journal_entry_id=entry.id if line is not None and entry is not None else None,
                journal_line_id=line.id if line is not None else None,
                transaction_type_id=plan.transaction_type_id,
                source_doc_type=document.source_doc_type,
                source_doc_id=document.source_doc_id,
                source_line_id=plan.line.source_line_id,
                project_id=plan.line.project_id,
                cost_provisional=plan.cost_provisional,
            )
            db.add(move)
            moves.append(move)
        db.flush()
    finally:
        _stock_service(db, on=False)
    return moves


def _apply_caches(db: Session, ctx: _Context) -> None:
    """Write back the state the plan left behind. The rows are already locked."""
    _stock_service(db, on=True)
    try:
        for location, state in ctx.location_states.items():
            balance = ctx.balances[location]
            balance.quantity = state.quantity
            balance.value = state.value
        for item_id, state in ctx.item_states.items():
            row = ctx.cost_states[item_id]
            row.average_cost = state.average
            row.last_positive_average_cost = state.last_positive_average
        db.flush()
    finally:
        _stock_service(db, on=False)


def _next_sequence(db: Session) -> int:
    return int(db.execute(text(f"SELECT nextval('{STOCK_SEQUENCE}')")).scalar_one())


def moves_of(db: Session, entry: JournalEntry) -> list[StockMove]:
    return list(
        db.scalars(
            select(StockMove)
            .where(
                StockMove.company_id == entry.company_id,
                StockMove.journal_entry_id == entry.id,
            )
            .order_by(StockMove.sequence_no)
        )
    )


# --- The primitives -------------------------------------------------------------------------


def receive_stock(
    db: Session,
    company_id: int,
    *,
    document: StockDocument,
    lines: Sequence[StockLine],
    actor: User,
) -> StockPosting:
    """Quantity into locations, valued at the unit cost supplied (decision 13).

    P5's callers are adjustments, journal batches, transfers and counts. P6 wires goods
    receipts and returns to this same function without changing it: everything that varies —
    the document type, the source link, the contra — is already an argument.
    """
    signed = [SignedLine(line=line, quantity=_positive(line)) for line in lines]
    return post_stock_moves(
        db,
        company_id,
        document=document,
        moves=signed,
        event_class=StockReceived,
        actor=actor,
    )


def issue_stock(
    db: Session,
    company_id: int,
    *,
    document: StockDocument,
    lines: Sequence[StockLine],
    actor: User,
) -> StockPosting:
    """Quantity out of locations, at the item's weighted average — or at what the location
    had left, when the issue empties it. Subject to the negative-stock policy (decision 5)."""
    signed = [SignedLine(line=line, quantity=-_positive(line)) for line in lines]
    return post_stock_moves(
        db,
        company_id,
        document=document,
        moves=signed,
        event_class=StockIssued,
        actor=actor,
    )


def revalue_stock(
    db: Session,
    company_id: int,
    *,
    document: StockDocument,
    lines: Sequence[StockLine],
    actor: User,
) -> StockPosting:
    """Value with no quantity (decision 4): a write-down or write-up of what is already there.

    Not one of the two primitives decision 13 names, because P6 has no use for it — but a
    revaluation *is* a move, it has to be valued and cached by the same code as every other
    move, and giving it its own entry point is what keeps a zero-quantity move from being a
    special case inside `receive_stock`.
    """
    for line in lines:
        if line.value is None or line.value == ZERO:
            # The one posting that is genuinely nothing at all: a revaluation moves value and
            # only value, so a revaluation of zero moves nothing in either ledger. Every other
            # valueless posting still moves quantity and is written.
            raise PostingError(
                "A revaluation must carry a non-zero value",
                code="zero_value_posting",
                field_errors={"value": ["required and non-zero"]},
            )
        if line.quantity != ZERO:
            raise PostingError(
                "A revaluation moves value, not quantity",
                code="invalid_revaluation",
                field_errors={"quantity": ["must be zero"]},
            )
    signed = [SignedLine(line=line, quantity=ZERO) for line in lines]
    return post_stock_moves(
        db,
        company_id,
        document=document,
        moves=signed,
        event_class=StockRevalued,
        actor=actor,
    )


def transfer_stock(
    db: Session,
    company_id: int,
    *,
    document: StockDocument,
    item_id: int,
    quantity: Decimal,
    from_warehouse_id: int,
    to_warehouse_id: int,
    project_id: int | None = None,
    actor: User,
) -> StockPosting:
    """One leg of a transfer: out of one location and into another, at a frozen value, in one
    entry (decision 6).

    A dispatch is a leg from the source warehouse to the in-transit warehouse; a receive is a
    leg from in-transit to the destination. Both legs of a "transfer now" run in one
    transaction, but each is its own posting, carrying the branch of its own physical
    warehouse — which is what keeps the branch balances right when the two warehouses sit in
    different branches.

    The value is not re-derived on arrival: what the source gave up is exactly what the
    destination takes, so a transfer can never create or destroy value, whatever the average
    does in between.
    """
    if quantity <= ZERO:
        raise PostingError(
            "A transfer moves a positive quantity",
            code="invalid_quantity",
            field_errors={"quantity": ["must be positive"]},
        )
    if from_warehouse_id == to_warehouse_id:
        raise PostingError(
            "A transfer needs two different warehouses",
            code="same_warehouse",
            field_errors={"to_warehouse_id": ["must differ from the source"]},
        )
    out_line = StockLine(
        item_id=item_id,
        warehouse_id=from_warehouse_id,
        quantity=quantity,
        project_id=project_id,
        description=document.description,
    )
    in_line = StockLine(
        item_id=item_id,
        warehouse_id=to_warehouse_id,
        quantity=quantity,
        project_id=project_id,
        description=document.description,
    )
    return post_stock_moves(
        db,
        company_id,
        document=document,
        moves=[
            SignedLine(line=out_line, quantity=-quantity, needs_contra=False),
            SignedLine(
                line=in_line,
                quantity=quantity,
                needs_contra=False,
                frozen_from_previous=True,
            ),
        ],
        event_class=StockTransferred,
        actor=actor,
    )


def _positive(line: StockLine) -> Decimal:
    if line.quantity <= ZERO:
        raise PostingError(
            "A stock line moves a positive quantity",
            code="invalid_quantity",
            field_errors={"quantity": ["must be positive"]},
        )
    return line.quantity


# --- Reversal -------------------------------------------------------------------------------


def reverse_stock_posting(
    db: Session,
    company_id: int,
    *,
    entry_id: int,
    on_date: date,
    reason: str,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
) -> StockPosting:
    """Decision 11: the kernel reversal, plus reversing moves at the original values.

    "At the original values" is the whole point. A reversal that re-costed at today's average
    would move a different amount of value than the thing it reverses, and the two would not
    cancel — the trial balance would return to where it was while the stock ledger did not.
    So each reversing move mirrors its original exactly and the caches follow.

    The negative-stock policy still applies: under `block`, a receipt whose quantity has since
    been issued cannot be reversed, because the reversal would take a location below zero.
    """
    if idempotency_key:
        # `posting.post` resolves a replayed key to the entry it produced the first time and
        # returns it. Without this check the reversal moves would be written a second time
        # against that same entry — the caches would move, the ledger would not.
        existing = posting.replay(db, company_id, idempotency_key, idempotency_hash)
        if existing is not None:
            found = moves_of(db, existing)
            return StockPosting(
                entry=existing, moves=found, keyed_moves=found, replayed=True
            )

    original = db.get(JournalEntry, entry_id)
    if original is None or original.company_id != company_id:
        raise NotFoundError("Journal entry not found")
    if original.module != INVENTORY_MODULE:
        raise LedgerStateError(
            f"{original.number} is not an inventory posting",
            code="not_an_inventory_entry",
        )
    originals = moves_of(db, original)

    ctx = _Context(db, company_id, actor)
    locations = [(move.item_id, move.warehouse_id) for move in originals]
    ctx.lock(locations)
    policy = ctx.settings.negative_stock_policy
    for move in originals:
        location = (move.item_id, move.warehouse_id)
        state = ctx.location_states[location]
        resulting = state.quantity - move.quantity
        if resulting < ZERO and policy == NegativeStockPolicy.BLOCK:
            raise _insufficient_stock(
                ctx.item(move.item_id),
                ctx.warehouse(move.warehouse_id),
                state.quantity,
                move.quantity,
            )
        ctx.location_states[location] = state.after(-move.quantity, -move.value)
        ctx.item_states[move.item_id] = ctx.item_states[move.item_id].after(
            -move.quantity, -move.value
        )

    reversal = posting.reverse(
        db,
        entry_id,
        company_id=company_id,
        on_date=on_date,
        reason=reason,
        actor=actor,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    period_id = reversal.period_id
    mirrored_line = {line.source_line_id: line for line in reversal.lines}

    written: list[StockMove] = []
    _stock_service(db, on=True)
    try:
        for move in originals:
            line = mirrored_line.get(move.journal_line_id)
            written.append(
                StockMove(
                    company_id=company_id,
                    item_id=move.item_id,
                    warehouse_id=move.warehouse_id,
                    move_date=on_date,
                    period_id=period_id,
                    sequence_no=_next_sequence(db),
                    quantity=-move.quantity,
                    unit_cost=move.unit_cost,
                    value=-move.value,
                    journal_entry_id=reversal.id if line is not None else None,
                    journal_line_id=line.id if line is not None else None,
                    transaction_type_id=move.transaction_type_id,
                    source_doc_type=move.source_doc_type,
                    source_doc_id=move.source_doc_id,
                    source_line_id=move.source_line_id,
                    project_id=move.project_id,
                    cost_provisional=move.cost_provisional,
                    reverses_move_id=move.id,
                )
            )
        db.add_all(written)
        db.flush()
    finally:
        _stock_service(db, on=False)
    _apply_caches(db, ctx)

    mirrored_moves = list(written)
    residues = {
        location: value
        for location, state in ctx.location_states.items()
        if (value := stranded_value(state)) is not None
    }
    if residues:
        written.extend(
            _expel_reversal_residues(db, ctx, residues, on_date=on_date, reversal=reversal)
        )
    return StockPosting(entry=reversal, moves=written, keyed_moves=mirrored_moves)


def _expel_reversal_residues(
    db: Session,
    ctx: _Context,
    residues: dict[Location, Decimal],
    *,
    on_date: date,
    reversal: JournalEntry,
) -> list[StockMove]:
    """Clear value left stranded at a location the reversal emptied.

    A reversal takes a move out at the value it went in at, which is the only thing it can
    honestly do. But the value *at* that location may have moved since — a revaluation, or a
    provisional issue — so mirroring a receipt can land the quantity on zero while leaving
    value behind. The forward path meets the same situation and answers it the same way
    (`costing.residue_move`), but it cannot be answered inside the reversing entry: that entry
    is an exact mirror of the one it reverses, line for line, and adding a line to it would
    make it something else.

    So the residue is its own posting, dated with the reversal, against the inventory
    adjustment account. Two documents, because two things happened: a reversal, and a
    write-off of what the reversal could not take with it.
    """
    lines = [
        StockLine(
            item_id=item_id,
            warehouse_id=warehouse_id,
            value=value,
            contra_account_id=ctx.residue_account(),
            description=f"Stock value left at zero quantity by {reversal.number}",
        )
        for (item_id, warehouse_id), value in sorted(residues.items())
    ]
    return post_stock_moves(
        db,
        ctx.company_id,
        document=StockDocument(
            doc_type=DocType.INV_ADJUSTMENT,
            move_date=on_date,
            description=f"Residual stock value after {reversal.number}",
            reference=reversal.number,
            # The residue names its cause. An inline residue inherits the source document of
            # the posting that produced it; this one is a document of its own, so it points
            # at the reversal that stranded the value.
            source_doc_type="journal_entry",
            source_doc_id=reversal.id,
        ),
        moves=[SignedLine(line=line, quantity=ZERO, is_correction=True) for line in lines],
        event_class=StockRevalued,
        actor=ctx.actor,
    ).moves


# --- Verification ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BalanceDrift:
    item_id: int
    warehouse_id: int
    stored_quantity: Decimal
    stored_value: Decimal
    recomputed_quantity: Decimal
    recomputed_value: Decimal


@dataclass(frozen=True)
class CostDrift:
    item_id: int
    stored_average: Decimal
    stored_last_positive: Decimal
    recomputed_average: Decimal
    recomputed_last_positive: Decimal


@dataclass(frozen=True)
class StockDrift:
    balances: list[BalanceDrift]
    costs: list[CostDrift]

    def __bool__(self) -> bool:
        return bool(self.balances or self.costs)


def verify_stock_balances(db: Session, company_id: int) -> StockDrift:
    """Replay every move in posting order and compare the result with both caches.

    The contract `period_balances` has with `journal_lines`, and `partner_documents.open_amount`
    has with `allocation_lines`: the cache is allowed to exist only because this function can
    prove it. An empty result means every stored quantity, value and average is exactly what
    the moves say it is.

    The replay is in `sequence_no` order rather than date order, because that is the order the
    averages were taken in — replaying by date would "correct" the backdated receipt the
    costing rules deliberately do not restate.
    """
    locations: dict[Location, LocationState] = {}
    items: dict[int, ItemState] = {}
    for move in db.scalars(
        select(StockMove)
        .where(StockMove.company_id == company_id)
        .order_by(StockMove.sequence_no)
    ):
        location = (move.item_id, move.warehouse_id)
        locations[location] = locations.get(location, LocationState()).after(
            move.quantity, move.value
        )
        items[move.item_id] = items.get(move.item_id, ItemState()).after(
            move.quantity, move.value
        )

    balance_drift: list[BalanceDrift] = []
    seen: set[Location] = set()
    for row in db.scalars(select(StockBalance).where(StockBalance.company_id == company_id)):
        location = (row.item_id, row.warehouse_id)
        seen.add(location)
        expected = locations.get(location, LocationState())
        if row.quantity != expected.quantity or row.value != expected.value:
            balance_drift.append(
                BalanceDrift(
                    item_id=row.item_id,
                    warehouse_id=row.warehouse_id,
                    stored_quantity=row.quantity,
                    stored_value=row.value,
                    recomputed_quantity=expected.quantity,
                    recomputed_value=expected.value,
                )
            )
    for location, expected in locations.items():
        if location in seen:
            continue
        balance_drift.append(
            BalanceDrift(
                item_id=location[0],
                warehouse_id=location[1],
                stored_quantity=ZERO,
                stored_value=ZERO,
                recomputed_quantity=expected.quantity,
                recomputed_value=expected.value,
            )
        )

    cost_drift: list[CostDrift] = []
    seen_items: set[int] = set()
    for row in db.scalars(select(ItemCostState).where(ItemCostState.company_id == company_id)):
        seen_items.add(row.item_id)
        expected_state = items.get(row.item_id, ItemState())
        if (
            row.average_cost != expected_state.average
            or row.last_positive_average_cost != expected_state.last_positive_average
        ):
            cost_drift.append(
                CostDrift(
                    item_id=row.item_id,
                    stored_average=row.average_cost,
                    stored_last_positive=row.last_positive_average_cost,
                    recomputed_average=expected_state.average,
                    recomputed_last_positive=expected_state.last_positive_average,
                )
            )
    for item_id, expected_state in items.items():
        if item_id in seen_items:
            continue
        cost_drift.append(
            CostDrift(
                item_id=item_id,
                stored_average=ZERO,
                stored_last_positive=ZERO,
                recomputed_average=expected_state.average,
                recomputed_last_positive=expected_state.last_positive_average,
            )
        )
    return StockDrift(balances=balance_drift, costs=cost_drift)


def balances_as_of(
    db: Session, company_id: int, *, as_of: date
) -> dict[Location, LocationState]:
    """What every location held on `as_of`, reconstructed from the moves dated up to it.

    Every as-of question goes through here rather than through `stock_balances`: the cache is
    the position *now*, and "now" is not a date anyone reports on. Date order is the right
    order for this one — a backdated receipt was, from the ledger's point of view, always
    there on its own date, which is exactly why the valuation report ties to the GL.
    """
    rows = db.execute(
        select(
            StockMove.item_id,
            StockMove.warehouse_id,
            func.sum(StockMove.quantity),
            func.sum(StockMove.value),
        )
        .where(StockMove.company_id == company_id, StockMove.move_date <= as_of)
        .group_by(StockMove.item_id, StockMove.warehouse_id)
    ).all()
    return {
        (item_id, warehouse_id): LocationState(quantity=quantity, value=value)
        for item_id, warehouse_id, quantity, value in rows
    }


def inventory_account_balance(
    db: Session, company_id: int, account_id: int, *, as_of: date, branch_id: int | None = None
) -> Decimal:
    statement = (
        select(func.coalesce(func.sum(JournalLine.base_amount), ZERO))
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == company_id,
            JournalLine.gl_account_id == account_id,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.entry_date <= as_of,
        )
    )
    if branch_id is not None:
        statement = statement.where(JournalLine.branch_id == branch_id)
    return db.scalar(statement) or ZERO
