"""Stock counts (P5 step 4, decision 7).

A count is a **working paper that becomes a posting**. A session freezes what the books say
each location holds, someone walks the aisles and keys what is actually there, the variances
are previewed, and Process posts them as one count-variance document. Until Process, nothing
in the ledger has moved and nothing in this module can move it.

**The snapshot is the whole design.** `system_quantity` is frozen per line at a posting-order
watermark, and every variance is `counted - system_quantity` against *that* figure, never
against a live balance read at Process time. A count is a statement about a moment; scoring it
against a balance that has moved since would produce a variance that is partly a count and
partly a delivery, and post the difference as shrinkage.

**Stale lines, and why the watermark rather than the clock.** If a location is posted to after
its line was frozen, that line's variance no longer means anything, and Process refuses the
whole session with `count_line_stale` until the line is re-snapshotted and recounted
(decision 7). "Posted to after" is `stock_moves.sequence_no > snapshot_sequence` — posting
order, which is the order the costing engine works in. A wall-clock comparison would be wrong
in both directions: a backdated document lands *now* however it is dated, and a long
transaction can commit a move whose timestamp predates a snapshot it should invalidate. The
snapshot holds the costing lock while it reads, so nothing can be in flight underneath it.

**Refusal is per session, not per line.** One stale line refuses Process outright rather than
posting the rest, because a count is one statement about one warehouse at one moment; posting
the part of it that still holds would produce a document that never corresponded to anything
anybody counted.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import NotFoundError
from app.inventory import documents as documents_service
from app.inventory import masters
from app.inventory import stock as stock_service
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.money import ZERO, base_currency, round_amount
from app.kernel.sequences import DocType, claim_number
from app.models.gl import GLTransactionType
from app.models.inventory import (
    InventoryDocument,
    Item,
    ItemType,
    StockBalance,
    StockCountLine,
    StockCountSession,
    StockCountStatus,
    Uom,
    Warehouse,
)
from app.models.user import User
from app.services.audit import record_audit

#: What the moves of a processed count say they came from. Unlike a transfer or an
#: adjustment, a count *can* carry its source id: the session exists long before the posting
#: does, so the moves point back at it from the moment they are written.
SOURCE_DOC_TYPE = "stock_count_session"


def _audit(
    db: Session,
    company_id: int,
    action: str,
    entity_id: int,
    *,
    actor: User,
    before: dict | None = None,
    after: dict | None = None,
    request: Request | None = None,
) -> None:
    record_audit(
        db,
        company_id=company_id,
        action=action,
        entity="stock_count_sessions",
        entity_id=entity_id,
        before=before,
        after=after,
        actor_user_id=actor.id,
        actor_email=actor.email,
        request=request,
    )


# --- Inputs ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class CountSessionInput:
    warehouse_id: int
    count_date: date
    description: str
    reference: str | None = None
    #: Defaults to the company's count-variance type when it has exactly one.
    transaction_type_id: int | None = None
    project_id: int | None = None
    #: Items to put on the sheet beyond those the warehouse already holds — the ones somebody
    #: expects to find where the books say there is nothing.
    include_items: Sequence[int] = field(default_factory=tuple)
    #: Whether items whose balance row exists but reads zero go on the sheet. Off by default,
    #: the "Include zero balances" convention from P3.
    include_zero_balances: bool = False


# --- Opening a session ----------------------------------------------------------------------


def _countable_warehouse(db: Session, company_id: int, warehouse_id: int) -> Warehouse:
    warehouse = masters.get_warehouse(db, company_id, warehouse_id)
    if warehouse.is_in_transit:
        raise LedgerStateError(
            f"{warehouse.code} is the in-transit warehouse; stock in transit is counted where "
            "it arrives",
            code="in_transit_warehouse_not_selectable",
            field_errors={"warehouse_id": ["not selectable"]},
        )
    if not warehouse.is_active:
        raise LedgerStateError(
            f"{warehouse.code} is not active",
            code="warehouse_inactive",
            field_errors={"warehouse_id": ["not active"]},
        )
    return warehouse


def _stock_item(db: Session, company_id: int, item_id: int) -> Item:
    item = db.get(Item, item_id)
    if item is None or item.company_id != company_id:
        raise NotFoundError("Item not found")
    if item.item_type != ItemType.STOCK:
        raise LedgerStateError(
            f"{item.code} is a {item.item_type.value} item; only stock items are counted",
            code="item_not_stocked",
            field_errors={"item_id": ["not a stock item"]},
        )
    return item


def _sheet_items(
    db: Session, company_id: int, data: CountSessionInput
) -> list[tuple[Item, Decimal]]:
    """Which items go on the sheet, and what the books say each of them holds.

    Every item with a balance row at this warehouse, plus anything explicitly asked for.
    Zero-quantity rows are off by default and worth having on request: a line counted at zero
    where the books already said zero is a confirmation, and a line counted at three where
    they said zero is exactly the discovery a count exists to make.
    """
    balances = {
        row.item_id: row.quantity
        for row in db.scalars(
            select(StockBalance).where(
                StockBalance.company_id == company_id,
                StockBalance.warehouse_id == data.warehouse_id,
            )
        )
    }
    wanted = {
        item_id
        for item_id, quantity in balances.items()
        if data.include_zero_balances or quantity != ZERO
    }
    wanted.update(data.include_items)
    items = [_stock_item(db, company_id, item_id) for item_id in sorted(wanted)]
    items.sort(key=lambda item: item.code)
    return [(item, balances.get(item.id, ZERO)) for item in items]


def open_session(
    db: Session,
    company_id: int,
    data: CountSessionInput,
    *,
    actor: User,
    request: Request | None = None,
) -> StockCountSession:
    """Freeze a warehouse: one line per item, each carrying what the books say and the
    watermark it was read at.

    The costing locks are taken before anything is read and held to commit, so no posting can
    be half-applied underneath the snapshot: every move that affects a frozen quantity is
    committed and carries a sequence number at or below the watermark, and every move that
    lands afterwards carries one above it. That is what makes the staleness test in `is_stale`
    a comparison of two integers rather than a guess.
    """
    warehouse = _countable_warehouse(db, company_id, data.warehouse_id)
    txn_type = documents_service.count_variance_type(
        db, company_id, data.transaction_type_id
    )
    sheet = _sheet_items(db, company_id, data)
    stock_service.lock_cost_states(db, company_id, [item.id for item, _ in sheet])
    watermark = stock_service.posting_watermark(db, company_id)
    now = datetime.now(UTC)

    session = StockCountSession(
        company_id=company_id,
        # Claimed here rather than at Process, because a count sheet is handed to somebody and
        # referred to for days before it posts anything — and a cancelled session still has to
        # be findable by the name it was known by.
        #
        # From its **own** run (`CNS-`), not the variance document's (`CNT-`). Every number in
        # a posting run belongs to something that reached the ledger; a session that posts
        # nothing — because it was cancelled, or because the count agreed with the books —
        # would leave a hole in it, and gaplessness is the one property those numbers exist
        # to have.
        number=claim_number(db, company_id, DocType.INV_COUNT_SESSION).number,
        warehouse_id=warehouse.id,
        count_date=data.count_date,
        description=data.description,
        reference=data.reference,
        transaction_type_id=txn_type.id,
        project_id=data.project_id,
        status=StockCountStatus.COUNTING,
        snapshot_at=now,
        snapshot_sequence=watermark,
    )
    db.add(session)
    db.flush()
    for index, (item, quantity) in enumerate(sheet):
        db.add(
            StockCountLine(
                company_id=company_id,
                session_id=session.id,
                line_no=index + 1,
                item_id=item.id,
                system_quantity=quantity,
                snapshot_at=now,
                snapshot_sequence=watermark,
                uom_id=item.base_uom_id,
            )
        )
    db.flush()
    _audit(
        db,
        company_id,
        "stock_count.opened",
        session.id,
        actor=actor,
        after={
            "number": session.number,
            "warehouse_id": session.warehouse_id,
            "count_date": session.count_date.isoformat(),
            "lines": len(sheet),
            "snapshot_sequence": watermark,
        },
        request=request,
    )
    return session


# --- Filling the sheet in --------------------------------------------------------------------


def _open_session(db: Session, company_id: int, session_id: int) -> StockCountSession:
    session = get_session(db, company_id, session_id)
    if session.status != StockCountStatus.COUNTING:
        raise LedgerStateError(
            f"{session.number} is {session.status.value} and cannot be changed",
            code="count_session_closed",
        )
    return session


def add_line(
    db: Session,
    company_id: int,
    session_id: int,
    item_id: int,
    *,
    actor: User,
    request: Request | None = None,
) -> StockCountLine:
    """Put an item on a sheet that did not have it — stock found where the books say none.

    It gets its own watermark, taken now rather than inherited from the session: the line is
    a fresh observation, and freezing it at a moment hours before it was looked at would call
    it stale for movements that happened before anyone laid eyes on the shelf.
    """
    session = _open_session(db, company_id, session_id)
    item = _stock_item(db, company_id, item_id)
    existing = db.scalar(
        select(StockCountLine).where(
            StockCountLine.company_id == company_id,
            StockCountLine.session_id == session.id,
            StockCountLine.item_id == item.id,
        )
    )
    if existing is not None:
        raise LedgerStateError(
            f"{item.code} is already on {session.number}",
            code="count_line_exists",
            field_errors={"item_id": ["already on this count"]},
        )
    stock_service.lock_cost_states(db, company_id, [item.id])
    watermark = stock_service.posting_watermark(db, company_id)
    position = stock_service.location_balance(db, company_id, item.id, session.warehouse_id)
    last = db.scalar(
        select(StockCountLine.line_no)
        .where(
            StockCountLine.company_id == company_id,
            StockCountLine.session_id == session.id,
        )
        .order_by(StockCountLine.line_no.desc())
        .limit(1)
    )
    line = StockCountLine(
        company_id=company_id,
        session_id=session.id,
        line_no=(last or 0) + 1,
        item_id=item.id,
        system_quantity=position.quantity,
        snapshot_at=datetime.now(UTC),
        snapshot_sequence=watermark,
        uom_id=item.base_uom_id,
    )
    db.add(line)
    db.flush()
    _audit(
        db,
        company_id,
        "stock_count.line_added",
        session.id,
        actor=actor,
        after={"item_id": item.id, "system_quantity": str(position.quantity)},
        request=request,
    )
    return line


def enter_count(
    db: Session,
    company_id: int,
    session_id: int,
    line_id: int,
    *,
    quantity: Decimal | None,
    uom_id: int | None = None,
    note: str | None = None,
    actor: User,
    request: Request | None = None,
) -> StockCountLine:
    """Key what was actually on the shelf, in any unit of the item's category.

    A `quantity` of `None` clears the line back to uncounted, which is not the same as
    counting it at zero: an uncounted line contributes nothing to the posting, and a line
    counted at zero writes off everything the location held. That distinction is the reason
    `counted_quantity` is nullable rather than defaulted.

    No audit row. Keying a count is the one high-frequency edit in this module — a sheet of
    four hundred lines is four hundred saves, and often several per line as somebody recounts
    a shelf — and who keyed what is already on the row: `AuditedMixin` stamps `updated_by`
    from the request actor, and `counted_at` says when. An audit row per keystroke would bury
    the events that matter (opened, re-snapshotted, processed, cancelled) in the noise of the
    ones that do not.
    """
    session = _open_session(db, company_id, session_id)
    line = _line_of(db, company_id, session, line_id)
    item = _stock_item(db, company_id, line.item_id)

    if quantity is None:
        line.counted_quantity = None
        line.counted_quantity_base = None
        line.counted_at = None
        line.uom_id = item.base_uom_id
    else:
        if quantity < ZERO:
            raise PostingError(
                "A counted quantity cannot be negative; a shelf holds nothing at worst",
                code="invalid_quantity",
                field_errors={"counted_quantity": ["must not be negative"]},
            )
        uom = db.get(Uom, uom_id or item.base_uom_id)
        if uom is None or uom.company_id != company_id:
            raise NotFoundError("Unit of measure not found")
        base = masters.to_base_quantity(quantity, uom, item)
        if quantity > ZERO and base <= ZERO:
            raise PostingError(
                f"{quantity:f} {uom.code} converts to nothing in {item.code}'s base unit",
                code="quantity_rounds_to_zero",
                field_errors={"counted_quantity": ["too small for this unit of measure"]},
            )
        line.counted_quantity = quantity
        line.counted_quantity_base = base
        line.uom_id = uom.id
        line.counted_at = datetime.now(UTC)
    if note is not None:
        line.note = note or None
    db.flush()
    return line


def resnapshot_line(
    db: Session,
    company_id: int,
    session_id: int,
    line_id: int,
    *,
    actor: User,
    request: Request | None = None,
) -> StockCountLine:
    """Re-freeze one stale line, and clear its count.

    Both halves matter. Re-freezing alone would score an old count against a new system
    quantity — the arithmetic would work and the answer would be a fiction. Decision 7 says
    the line is re-snapshotted *and* recounted, so the count goes with the snapshot it was
    taken against.
    """
    session = _open_session(db, company_id, session_id)
    line = _line_of(db, company_id, session, line_id)
    item = _stock_item(db, company_id, line.item_id)
    stock_service.lock_cost_states(db, company_id, [line.item_id])
    watermark = stock_service.posting_watermark(db, company_id)
    position = stock_service.location_balance(db, company_id, line.item_id, session.warehouse_id)

    before = {
        "system_quantity": str(line.system_quantity),
        "snapshot_sequence": line.snapshot_sequence,
    }
    line.system_quantity = position.quantity
    line.snapshot_at = datetime.now(UTC)
    line.snapshot_sequence = watermark
    line.counted_quantity = None
    line.counted_quantity_base = None
    line.counted_at = None
    line.uom_id = item.base_uom_id
    db.flush()
    _audit(
        db,
        company_id,
        "stock_count.line_resnapshotted",
        session.id,
        actor=actor,
        before=before,
        after={
            "item_id": line.item_id,
            "system_quantity": str(line.system_quantity),
            "snapshot_sequence": watermark,
        },
        request=request,
    )
    return line


def _line_of(
    db: Session, company_id: int, session: StockCountSession, line_id: int
) -> StockCountLine:
    line = db.get(StockCountLine, line_id)
    if line is None or line.company_id != company_id or line.session_id != session.id:
        raise NotFoundError("Count line not found")
    return line


# --- Reading the sheet ------------------------------------------------------------------------


def stale_lines(
    db: Session,
    company_id: int,
    session: StockCountSession,
    lines: Sequence[StockCountLine],
) -> set[int]:
    """Decision 7's stale set: the lines whose location has been posted to since they were
    frozen.

    One query for the whole sheet, and one definition of staleness for everything that asks —
    the preview, the read model and Process all call this, so a line cannot look fresh on the
    screen and stale to the poster.
    """
    latest = stock_service.last_sequence_by_item(db, company_id, session.warehouse_id)
    return {
        line.id
        for line in lines
        if latest.get(line.item_id, 0) > line.snapshot_sequence
    }


def is_stale(
    db: Session, company_id: int, session: StockCountSession, line: StockCountLine
) -> bool:
    """One line, same rule."""
    return bool(stale_lines(db, company_id, session, [line]))


def variance_of(line: StockCountLine) -> Decimal | None:
    """`counted - system`, in the item's base unit — or `None` for an uncounted line.

    Not a column. Storing it would be a second place for the same fact to live, and the two
    would eventually disagree the way every cached total does (architecture rule 1).
    """
    if line.counted_quantity_base is None:
        return None
    return line.counted_quantity_base - line.system_quantity


@dataclass(frozen=True)
class PreviewLine:
    """One line as Process would post it, at today's average."""

    line: StockCountLine
    item: Item
    variance: Decimal | None
    unit_cost: Decimal
    #: What the posting would move: `round(variance × average)` — the figure the stock service
    #: will arrive at for a gain, and for a loss that does not empty the location. An issue
    #: that empties one takes what the location actually holds instead (decision 4's flush),
    #: which can differ by a minor unit or two; the preview says so rather than pretending to
    #: know better than the engine that will do it.
    value: Decimal
    stale: bool
    counted: bool


@dataclass(frozen=True)
class CountPreview:
    """What Process would do, and what would stop it — the allocation screen's contract."""

    session: StockCountSession
    lines: list[PreviewLine]
    total_value: Decimal
    counted_lines: int
    uncounted_lines: int
    variance_lines: int
    stale_lines: list[int]

    @property
    def can_process(self) -> bool:
        return not self.stale_lines


def preview(db: Session, company_id: int, session_id: int) -> CountPreview:
    """The posting, before Process — same as the allocation screen's preview (decision 7).

    Costed at the item's **current** average rather than the one in force at the snapshot,
    because that is what Process will use: a variance is a correction applied now, and the
    only cost the ledger can charge it at is the one the stock is carried at now.
    """
    session = get_session(db, company_id, session_id)
    lines = lines_of(db, company_id, session.id)
    stale_ids = stale_lines(db, company_id, session, lines)
    decimal_places = base_currency(db, company_id).decimal_places
    rows: list[PreviewLine] = []
    total = ZERO
    counted = variances = 0
    stale: list[int] = []
    for line in lines:
        item = db.get(Item, line.item_id)
        assert item is not None  # FK-guaranteed; the sheet cannot outlive its items
        variance = variance_of(line)
        average = stock_service.item_state(db, company_id, line.item_id).average
        value = (
            ZERO
            if variance is None
            else round_amount(variance * average, decimal_places)
        )
        line_is_stale = line.id in stale_ids
        if line_is_stale and line.counted_quantity_base is not None:
            stale.append(line.id)
        if variance is not None:
            counted += 1
            if variance != ZERO:
                variances += 1
                total += value
        rows.append(
            PreviewLine(
                line=line,
                item=item,
                variance=variance,
                unit_cost=average,
                value=value,
                stale=line_is_stale,
                counted=variance is not None,
            )
        )
    return CountPreview(
        session=session,
        lines=rows,
        total_value=total,
        counted_lines=counted,
        uncounted_lines=len(rows) - counted,
        variance_lines=variances,
        stale_lines=stale,
    )


# --- Processing -------------------------------------------------------------------------------


def process_session(
    db: Session,
    company_id: int,
    session_id: int,
    *,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> tuple[StockCountSession, InventoryDocument | None, bool]:
    """Post every non-zero variance as one count-variance document, and complete the session.

    Refuses the whole session if any counted line is stale (decision 7). Uncounted lines are
    not checked, and cannot be: a line nobody counted contributes nothing to the posting, so
    whether its location moved is a question about a variance that does not exist.

    A session whose variances are all zero completes with **no document**. A count that agrees
    with the books is a complete and useful answer, and posting an empty document to record it
    would put a number in the `CNT-` run that stands for nothing.
    """
    session = get_session(db, company_id, session_id)
    if session.status == StockCountStatus.CANCELLED:
        raise LedgerStateError(
            f"{session.number} was cancelled", code="count_session_cancelled"
        )
    if session.status == StockCountStatus.COMPLETED:
        document = (
            db.get(InventoryDocument, session.document_id)
            if session.document_id is not None
            else None
        )
        if (
            idempotency_key
            and document is not None
            and document.idempotency_key == idempotency_key
        ):
            return session, document, True
        raise LedgerStateError(
            f"{session.number} was already processed", code="count_already_processed"
        )

    lines = lines_of(db, company_id, session.id)
    moved = stale_lines(db, company_id, session, lines)
    stale = [
        line.id
        for line in lines
        if line.counted_quantity_base is not None and line.id in moved
    ]
    if stale:
        raise LedgerStateError(
            f"{len(stale)} line(s) on {session.number} have moved since they were counted; "
            "re-snapshot and recount them",
            code="count_line_stale",
            field_errors={
                f"lines.{line_id}": ["stock moved after the snapshot"] for line_id in stale
            },
        )

    if lines and not any(line.counted_quantity_base is not None for line in lines):
        # A sheet nobody filled in. Completing it would be the worst of both worlds: the
        # session is terminal, so the count cannot be resumed, and nothing was posted, so
        # there is no record of what was found. Refusing says which of the two the operator
        # meant — count something, or cancel the session.
        raise LedgerStateError(
            f"Nothing on {session.number} has been counted yet",
            code="count_not_started",
            field_errors={"lines": ["count at least one line, or cancel the session"]},
        )

    # `if variance` skips both the uncounted lines (None) and the ones that agreed with the
    # books (zero) — the two cases that have nothing to post, for different reasons.
    variances = [(line, variance) for line in lines if (variance := variance_of(line))]
    document: InventoryDocument | None = None
    if variances:
        txn_type = db.get(GLTransactionType, session.transaction_type_id)
        if txn_type is None:
            raise NotFoundError("Inventory transaction type not found")
        contra_account_id = documents_service.variance_contra_account(db, company_id, txn_type)
        document, _ = documents_service.post_count_variance(
            db,
            company_id,
            documents_service.DocumentInput(
                document_date=session.count_date,
                description=f"Count {session.number}: {session.description}",
                reference=session.reference,
                transaction_type_id=session.transaction_type_id,
            ),
            lines=[
                documents_service.VarianceLine(
                    item_id=line.item_id,
                    warehouse_id=session.warehouse_id,
                    uom_id=line.uom_id,
                    quantity_base=variance,
                    # Decision 7: gains and losses are both taken at the current average. A
                    # loss is costed by the stock service, which is where the flush rule
                    # lives; a gain has to be told what it is worth, and the average is it.
                    unit_cost=(
                        stock_service.item_state(db, company_id, line.item_id).average
                        if variance > ZERO
                        else None
                    ),
                    project_id=session.project_id,
                    description=None,
                    source_line_id=line.id,
                )
                for line, variance in variances
            ],
            transaction_type_id=session.transaction_type_id,
            contra_account_id=contra_account_id,
            source_doc_type=SOURCE_DOC_TYPE,
            source_doc_id=session.id,
            actor=actor,
            idempotency_key=idempotency_key,
            idempotency_hash=idempotency_hash,
            request=request,
        )
        document_lines = documents_service.lines_of(db, company_id, document.id)
        for (line, _), document_line in zip(variances, document_lines, strict=True):
            line.stock_move_id = document_line.stock_move_id

    session.status = StockCountStatus.COMPLETED
    session.document_id = document.id if document is not None else None
    session.processed_at = datetime.now(UTC)
    db.flush()
    _audit(
        db,
        company_id,
        "stock_count.processed",
        session.id,
        actor=actor,
        before={"status": StockCountStatus.COUNTING.value},
        after={
            "status": session.status.value,
            "variances": len(variances),
            "document": document.number if document is not None else None,
        },
        request=request,
    )
    return session, document, False


def cancel_session(
    db: Session,
    company_id: int,
    session_id: int,
    *,
    reason: str,
    actor: User,
    request: Request | None = None,
) -> StockCountSession:
    """Abandon a count. Nothing was posted, so nothing is reversed — the sheet stays, with its
    number and its lines, as the record that a count was started and given up on."""
    session = _open_session(db, company_id, session_id)
    session.status = StockCountStatus.CANCELLED
    session.cancelled_at = datetime.now(UTC)
    db.flush()
    _audit(
        db,
        company_id,
        "stock_count.cancelled",
        session.id,
        actor=actor,
        before={"status": StockCountStatus.COUNTING.value},
        after={"status": session.status.value, "reason": reason},
        request=request,
    )
    return session


# --- Reading -----------------------------------------------------------------------------------


def get_session(db: Session, company_id: int, session_id: int) -> StockCountSession:
    session = db.get(StockCountSession, session_id)
    if session is None or session.company_id != company_id:
        raise NotFoundError("Count session not found")
    return session


def lines_of(db: Session, company_id: int, session_id: int) -> list[StockCountLine]:
    return list(
        db.scalars(
            select(StockCountLine)
            .where(
                StockCountLine.company_id == company_id,
                StockCountLine.session_id == session_id,
            )
            .order_by(StockCountLine.line_no)
        )
    )


def list_sessions(
    db: Session,
    company_id: int,
    *,
    status: StockCountStatus | None = None,
    warehouse_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: int | None = None,
    limit: int = 100,
) -> tuple[list[StockCountSession], int | None]:
    """Newest first, keyset-paged on the id — the P4 listing shape."""
    query = select(StockCountSession).where(StockCountSession.company_id == company_id)
    if status is not None:
        query = query.where(StockCountSession.status == status)
    if warehouse_id is not None:
        query = query.where(StockCountSession.warehouse_id == warehouse_id)
    if date_from is not None:
        query = query.where(StockCountSession.count_date >= date_from)
    if date_to is not None:
        query = query.where(StockCountSession.count_date <= date_to)
    if cursor is not None:
        query = query.where(StockCountSession.id < cursor)
    rows = list(db.scalars(query.order_by(StockCountSession.id.desc()).limit(limit + 1)))
    next_cursor = rows[limit].id if len(rows) > limit else None
    return rows[:limit], next_cursor
