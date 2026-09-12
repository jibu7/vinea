"""Warehouse transfers (P5 step 4, decision 6).

A transfer is the one stock document whose stock is somewhere in the middle. Dispatch takes
the quantity out of the source warehouse and puts it into the company's system in-transit
warehouse; receive takes it out of transit and puts it into the destination. Each leg is its
own posting, carrying the branch of its own physical warehouse, so a transfer between branches
leaves both branch balances square rather than moving value sideways inside one of them. Stock
that has left and not arrived is a real position on the valuation report — quantity in the
in-transit warehouse, value on the in-transit account — and `assert_stock_invariants` covers
it like any other location.

**What this module does not do.** It does not value anything and it does not write a move:
`stock.transfer_stock` does both, and the receive leg carries the dispatched value frozen, so
a transfer can never create or destroy value however the average moves in between. What
happens here is the part a user would recognise as a document — warehouses are checked, a
quantity keyed in cases becomes a quantity in eaches, a number is claimed, a status says where
the stock is, and the whole thing is refused as one unit.

**Refused whole.** Nothing here commits, and the header is written *after* the posting
returns. A dispatch of four lines whose third has nothing to send leaves no transfer behind,
not a transfer with one line missing.

**Why a transfer is not an `inventory_documents` row.** That table holds one journal entry,
which is right for an adjustment and a journal batch. A transfer posts twice — three times
when a dispatch that never arrived is cancelled — and each posting is a different day, a
different branch and a different half of the story.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import AppError, ConflictError, NotFoundError
from app.inventory import masters
from app.inventory import stock as stock_service
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.money import ZERO
from app.kernel.sequences import DocType, claim_number
from app.models.gl import GLTransactionType
from app.models.inventory import (
    INVENTORY_MODULE,
    InventoryTransactionKind,
    Item,
    StockMove,
    StockTransfer,
    StockTransferLine,
    StockTransferStatus,
    Uom,
    Warehouse,
)
from app.models.user import User
from app.services.audit import record_audit

#: What the moves of a transfer say they came from. There is no id beside it: the transfer
#: row is written after the posting, because it takes the dispatch entry's number and
#: `stock_moves` refuses UPDATE. The link is forward, through the four move columns on the
#: line — the same ordering `inventory_documents` lives with, for the same reason.
SOURCE_DOC_TYPE = "stock_transfer"


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
        entity="stock_transfers",
        entity_id=entity_id,
        before=before,
        after=after,
        actor_user_id=actor.id,
        actor_email=actor.email,
        request=request,
    )


# --- Inputs ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class TransferLineInput:
    """One item to move. A magnitude in `uom_id`: the direction is the document's."""

    item_id: int
    quantity: Decimal
    uom_id: int | None = None
    description: str | None = None


@dataclass(frozen=True)
class TransferInput:
    transfer_date: date
    description: str
    from_warehouse_id: int
    to_warehouse_id: int
    reference: str | None = None
    #: Defaults to the company's transfer-kind transaction type when it has exactly one.
    transaction_type_id: int | None = None
    project_id: int | None = None
    lines: Sequence[TransferLineInput] = field(default_factory=tuple)


# --- Resolution -----------------------------------------------------------------------------


def _on_line(index: int, error: AppError) -> AppError:
    error.field_errors = {
        (key if key.startswith("lines.") else f"lines.{index}.{key}"): value
        for key, value in error.field_errors.items()
    } or {f"lines.{index}": [error.code]}
    return error


def _transaction_type(db: Session, company_id: int, type_id: int | None) -> GLTransactionType:
    """The transfer's type, or the company's own if it has exactly one.

    Defaulting rather than requiring, because a transfer has no direction to choose and no
    contra to name — both legs are inventory accounts. The type is here so a company that
    wants to tell inter-branch moves from inter-warehouse ones can, not because the posting
    needs to be told what a transfer does (decision 9).
    """
    if type_id is None:
        candidates = list(
            db.scalars(
                select(GLTransactionType).where(
                    GLTransactionType.company_id == company_id,
                    GLTransactionType.module == INVENTORY_MODULE,
                    GLTransactionType.kind == InventoryTransactionKind.TRANSFER,
                    GLTransactionType.is_active,
                )
            )
        )
        if len(candidates) != 1:
            raise LedgerStateError(
                "This company has no single default transfer type; name one on the document"
                if candidates
                else "This company has no active transfer transaction type",
                code="transaction_type_required",
                field_errors={"transaction_type_id": ["required"]},
            )
        return candidates[0]

    row = db.get(GLTransactionType, type_id)
    if row is None or row.company_id != company_id or row.module != INVENTORY_MODULE:
        raise NotFoundError("Inventory transaction type not found")
    if not row.is_active:
        raise LedgerStateError(
            f"Transaction type {row.code} is not active",
            code="transaction_type_inactive",
            field_errors={"transaction_type_id": ["not active"]},
        )
    if row.kind != InventoryTransactionKind.TRANSFER:
        raise LedgerStateError(
            f"{row.code} is a {row.kind.value} type and cannot move stock between warehouses",
            code="transaction_kind_not_allowed",
            field_errors={"transaction_type_id": ["not valid on a transfer"]},
        )
    return row


def _physical_warehouse(
    db: Session, company_id: int, warehouse_id: int, field_name: str
) -> Warehouse:
    """A warehouse a user may name on a transfer: a real place, and not the transit one.

    In-transit is a system location (decision 6). Letting it be keyed as a source or a
    destination would let someone post half a transfer by hand and leave stock in transit
    with no document able to bring it out.
    """
    warehouse = masters.get_warehouse(db, company_id, warehouse_id)
    if warehouse.is_in_transit:
        raise LedgerStateError(
            f"{warehouse.code} is the in-transit warehouse and cannot be keyed on a transfer",
            code="in_transit_warehouse_not_selectable",
            field_errors={field_name: ["not selectable"]},
        )
    if not warehouse.is_active:
        raise LedgerStateError(
            f"{warehouse.code} is not active",
            code="warehouse_inactive",
            field_errors={field_name: ["not active"]},
        )
    return warehouse


@dataclass(frozen=True)
class _Resolved:
    source: TransferLineInput
    uom: Uom
    quantity_base: Decimal


def _resolve(db: Session, company_id: int, index: int, line: TransferLineInput) -> _Resolved:
    if line.quantity <= ZERO:
        raise _on_line(
            index,
            PostingError(
                "A transfer moves a positive quantity",
                code="invalid_quantity",
                field_errors={"quantity": ["must be positive"]},
            ),
        )
    item = db.get(Item, line.item_id)
    if item is None or item.company_id != company_id:
        raise _on_line(index, NotFoundError("Item not found"))
    uom = db.get(Uom, line.uom_id or item.base_uom_id)
    if uom is None or uom.company_id != company_id:
        raise _on_line(index, NotFoundError("Unit of measure not found"))
    try:
        quantity_base = masters.to_base_quantity(line.quantity, uom, item)
    except AppError as err:
        raise _on_line(index, err) from err
    if quantity_base <= ZERO:
        raise _on_line(
            index,
            PostingError(
                f"{line.quantity:f} {uom.code} converts to nothing in {item.code}'s base unit",
                code="quantity_rounds_to_zero",
                field_errors={"quantity": ["too small for this unit of measure"]},
            ),
        )
    return _Resolved(source=line, uom=uom, quantity_base=quantity_base)


# --- Posting --------------------------------------------------------------------------------


def _replay(
    db: Session, company_id: int, key: str, request_hash: str | None
) -> StockTransfer | None:
    """Resolve an `Idempotency-Key` to the transfer it already created.

    On the transfer rather than only on the journal entry, for the reason step 3's documents
    carry their own: a dispatch of stock carried at zero value posts moves and no entry, so
    the kernel has nothing to recognise a retry by. It also covers "transfer now", where one
    request produces two postings and only the first can hold the key.
    """
    transfer = db.scalar(
        select(StockTransfer).where(
            StockTransfer.company_id == company_id,
            StockTransfer.idempotency_key == key,
        )
    )
    if transfer is None:
        return None
    if request_hash is not None and transfer.idempotency_hash not in (None, request_hash):
        raise ConflictError(
            f"Idempotency-Key {key} was already used for a different request "
            f"({transfer.number}); use a new key",
            code="idempotency_key_reused",
        )
    return transfer


def _on_document_line(error: AppError) -> AppError:
    """Re-point a leg's line errors at the document's lines.

    A leg is two moves per line — out of one location and into another — so the stock service
    numbers a two-item transfer's lines 0 to 3 while the user keyed two. Left alone,
    `insufficient_stock` on the second item would land on a row the grid does not have, which
    is the same as landing nowhere.
    """
    rekeyed: dict[str, list[str]] = {}
    for key, messages in error.field_errors.items():
        parts = key.split(".", 2)
        if len(parts) >= 2 and parts[0] == "lines" and parts[1].isdigit():
            index = int(parts[1]) // 2
            rest = f".{parts[2]}" if len(parts) == 3 else ""
            rekeyed[f"lines.{index}{rest}"] = messages
        else:
            rekeyed[key] = messages
    error.field_errors = rekeyed
    return error


def _post_leg(
    db: Session,
    company_id: int,
    *,
    document: stock_service.StockDocument,
    lines: Sequence[stock_service.TransferLine],
    from_warehouse_id: int,
    to_warehouse_id: int,
    actor: User,
) -> stock_service.StockPosting:
    try:
        return stock_service.transfer_stock(
            db,
            company_id,
            document=document,
            lines=lines,
            from_warehouse_id=from_warehouse_id,
            to_warehouse_id=to_warehouse_id,
            actor=actor,
        )
    except AppError as err:
        raise _on_document_line(err) from err


def _legs(
    resolved: Sequence[_Resolved], project_id: int | None
) -> list[stock_service.TransferLine]:
    return [
        stock_service.TransferLine(
            item_id=item.source.item_id,
            quantity=item.quantity_base,
            project_id=project_id,
            description=item.source.description,
        )
        for item in resolved
    ]


def _leg_document(
    data_description: str,
    reference: str | None,
    *,
    on: date,
    transaction_type_id: int,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
) -> stock_service.StockDocument:
    return stock_service.StockDocument(
        doc_type=str(DocType.INV_TRANSFER),
        move_date=on,
        description=data_description,
        reference=reference,
        transaction_type_id=transaction_type_id,
        source_doc_type=SOURCE_DOC_TYPE,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )


def post_transfer(
    db: Session,
    company_id: int,
    data: TransferInput,
    *,
    receive_now: bool = True,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> tuple[StockTransfer, bool]:
    """Dispatch a transfer, and receive it in the same transaction when asked to.

    `receive_now=True` is the default UI action of decision 6: both legs, one transaction, two
    postings. `receive_now=False` is the two-step flow — the stock sits in transit until
    somebody at the other end says it arrived, which is the whole reason the in-transit
    warehouse exists.

    Never commits. The caller's transaction carries the document, the moves and the ledger
    together or none of them.
    """
    if not data.lines:
        raise LedgerStateError(
            "A transfer needs at least one line",
            code="empty_document",
            field_errors={"lines": ["at least one line required"]},
        )
    if idempotency_key:
        existing = _replay(db, company_id, idempotency_key, idempotency_hash)
        if existing is not None:
            return existing, True

    source = _physical_warehouse(db, company_id, data.from_warehouse_id, "from_warehouse_id")
    destination = _physical_warehouse(db, company_id, data.to_warehouse_id, "to_warehouse_id")
    if source.id == destination.id:
        raise LedgerStateError(
            "A transfer needs two different warehouses",
            code="same_warehouse",
            field_errors={"to_warehouse_id": ["must differ from the source"]},
        )
    transit = masters.in_transit_warehouse(db, company_id)
    txn_type = _transaction_type(db, company_id, data.transaction_type_id)
    resolved = [_resolve(db, company_id, index, line) for index, line in enumerate(data.lines)]

    dispatch = _post_leg(
        db,
        company_id,
        document=_leg_document(
            data.description,
            data.reference,
            on=data.transfer_date,
            transaction_type_id=txn_type.id,
            idempotency_key=idempotency_key,
            idempotency_hash=idempotency_hash,
        ),
        lines=_legs(resolved, data.project_id),
        from_warehouse_id=source.id,
        to_warehouse_id=transit.id,
        actor=actor,
    )

    transfer = StockTransfer(
        company_id=company_id,
        number=(
            dispatch.entry.number
            if dispatch.entry is not None
            # Stock carried at zero value moves without the ledger noticing (decision 1), so
            # there is no entry to take a number from. The transfer claims its own from the
            # same sequence, which keeps the `TRF-` run gapless either way.
            else claim_number(db, company_id, DocType.INV_TRANSFER).number
        ),
        transfer_date=data.transfer_date,
        description=data.description,
        reference=data.reference,
        from_warehouse_id=source.id,
        to_warehouse_id=destination.id,
        transaction_type_id=txn_type.id,
        project_id=data.project_id,
        status=StockTransferStatus.IN_TRANSIT,
        dispatch_entry_id=dispatch.entry.id if dispatch.entry is not None else None,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    db.add(transfer)
    db.flush()

    # `keyed_moves` pairs up with the lines that were passed in — out, in, out, in — and
    # excludes the moves the service raised itself, so the pairing does not shift the moment
    # a negative-stock residue lands in the middle of a leg.
    pairs = _pairs(dispatch, len(resolved))
    for index, item in enumerate(resolved):
        out_move, in_move = pairs[index]
        db.add(
            StockTransferLine(
                company_id=company_id,
                transfer_id=transfer.id,
                line_no=index + 1,
                item_id=item.source.item_id,
                quantity=item.source.quantity,
                uom_id=item.uom.id,
                quantity_base=item.quantity_base,
                description=item.source.description,
                dispatch_out_move_id=out_move.id,
                dispatch_in_move_id=in_move.id,
            )
        )
    db.flush()
    _audit(
        db,
        company_id,
        "stock_transfer.dispatched",
        transfer.id,
        actor=actor,
        after={
            "number": transfer.number,
            "transfer_date": transfer.transfer_date.isoformat(),
            "from_warehouse_id": transfer.from_warehouse_id,
            "to_warehouse_id": transfer.to_warehouse_id,
            "lines": len(resolved),
            "journal_entry_id": transfer.dispatch_entry_id,
        },
        request=request,
    )

    if receive_now:
        receive_transfer(
            db,
            company_id,
            transfer.id,
            on_date=data.transfer_date,
            actor=actor,
            request=request,
        )
    return transfer, False


def _pairs(
    posting: stock_service.StockPosting, expected: int
) -> list[tuple[StockMove, StockMove]]:
    moves = posting.keyed_moves
    assert len(moves) == expected * 2, (
        f"a transfer leg of {expected} lines wrote {len(moves)} keyed moves; the out/in "
        "pairing the line links depend on is broken"
    )
    return [(moves[index * 2], moves[index * 2 + 1]) for index in range(expected)]


def receive_transfer(
    db: Session,
    company_id: int,
    transfer_id: int,
    *,
    on_date: date | None = None,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> StockTransfer:
    """The second leg: in-transit → destination, at the value the dispatch froze.

    Everything that was dispatched arrives — P5 ships direct transfers, and a partial receipt
    is the requisition workflow the plan puts in the backlog (§B.2). So there is nothing to
    key here: the leg is the dispatch's lines again, in the other direction.

    A transfer that has already arrived refuses with `transfer_already_received` rather than
    posting a second leg. That status check, not the `Idempotency-Key`, is what makes a second
    Receive safe — the key is passed to the posting as well, but by the time a retry reaches
    us the transfer itself already says the stock is home.
    """
    transfer = get_transfer(db, company_id, transfer_id)
    if transfer.status == StockTransferStatus.CANCELLED:
        raise LedgerStateError(
            f"{transfer.number} was cancelled and its stock is back at the source",
            code="transfer_cancelled",
        )
    if transfer.status == StockTransferStatus.COMPLETED:
        raise LedgerStateError(
            f"{transfer.number} was already received",
            code="transfer_already_received",
        )
    lines = lines_of(db, company_id, transfer.id)
    transit = masters.in_transit_warehouse(db, company_id)
    arrival = on_date or transfer.transfer_date
    if arrival < transfer.transfer_date:
        # Stock cannot arrive before it left: the receive leg is costed at what the dispatch
        # froze, and a date before the dispatch would report value in two places at once on
        # any as-of question asked in between.
        raise LedgerStateError(
            f"{transfer.number} was dispatched on {transfer.transfer_date}; it cannot arrive "
            f"on {arrival}",
            code="receive_before_dispatch",
            field_errors={"receive_date": ["cannot precede the dispatch"]},
        )

    posting = _post_leg(
        db,
        company_id,
        document=_leg_document(
            transfer.description,
            transfer.reference,
            on=arrival,
            transaction_type_id=transfer.transaction_type_id,
            idempotency_key=idempotency_key,
            idempotency_hash=idempotency_hash,
        ),
        lines=[
            stock_service.TransferLine(
                item_id=line.item_id,
                quantity=line.quantity_base,
                project_id=transfer.project_id,
                description=line.description,
                source_line_id=line.id,
            )
            for line in lines
        ],
        from_warehouse_id=transit.id,
        to_warehouse_id=transfer.to_warehouse_id,
        actor=actor,
    )
    pairs = _pairs(posting, len(lines))
    for index, line in enumerate(lines):
        out_move, in_move = pairs[index]
        line.receive_out_move_id = out_move.id
        line.receive_in_move_id = in_move.id
    transfer.status = StockTransferStatus.COMPLETED
    transfer.receive_entry_id = posting.entry.id if posting.entry is not None else None
    transfer.received_date = arrival
    db.flush()
    _audit(
        db,
        company_id,
        "stock_transfer.received",
        transfer.id,
        actor=actor,
        before={"status": StockTransferStatus.IN_TRANSIT.value},
        after={
            "status": transfer.status.value,
            "received_date": arrival.isoformat(),
            "journal_entry_id": transfer.receive_entry_id,
        },
        request=request,
    )
    return transfer


def cancel_transfer(
    db: Session,
    company_id: int,
    transfer_id: int,
    *,
    on_date: date | None = None,
    reason: str,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> StockTransfer:
    """Send stock in transit back where it came from, by reversing the dispatch leg.

    This is decision 11 applied to the one state a transfer can get stuck in. Without it a
    mis-keyed dispatch would leave quantity in the in-transit warehouse and value on the
    in-transit account with nothing in the system able to move either: the destination cannot
    receive stock that was never meant to go there, and no other document may name the
    in-transit warehouse (decision 6).

    A **received** transfer is not cancelled — its stock is somewhere real, and unwinding it is
    a transfer back, which is a document a user posts rather than a state a machine restores.
    """
    transfer = get_transfer(db, company_id, transfer_id)
    if transfer.status == StockTransferStatus.COMPLETED:
        raise LedgerStateError(
            f"{transfer.number} has already arrived; transfer it back instead of cancelling",
            code="transfer_already_received",
        )
    if transfer.status == StockTransferStatus.CANCELLED:
        raise LedgerStateError(
            f"{transfer.number} was already cancelled",
            code="transfer_already_cancelled",
        )
    lines = lines_of(db, company_id, transfer.id)
    on = on_date or transfer.transfer_date
    if on < transfer.transfer_date:
        raise LedgerStateError(
            f"{transfer.number} was dispatched on {transfer.transfer_date}; it cannot be "
            f"cancelled on {on}",
            code="cancel_before_dispatch",
            field_errors={"cancellation_date": ["cannot precede the dispatch"]},
        )

    if transfer.dispatch_entry_id is None:
        # A valueless dispatch mirrors as a valueless cancellation, exactly as a valueless
        # document reverses in step 3: the quantity is in transit and has to come home, and
        # the ledger says nothing in both directions because it had nothing to say.
        originals = [
            move
            for line in lines
            for move_id in (line.dispatch_out_move_id, line.dispatch_in_move_id)
            if move_id is not None and (move := db.get(StockMove, move_id)) is not None
        ]
        posting = stock_service.reverse_unvalued_moves(
            db, company_id, originals=originals, on_date=on, actor=actor
        )
    else:
        posting = stock_service.reverse_stock_posting(
            db,
            company_id,
            entry_id=transfer.dispatch_entry_id,
            on_date=on,
            reason=reason,
            actor=actor,
            idempotency_key=idempotency_key,
            idempotency_hash=idempotency_hash,
        )
    transfer.status = StockTransferStatus.CANCELLED
    transfer.cancellation_entry_id = posting.entry.id if posting.entry is not None else None
    transfer.cancelled_date = on
    db.flush()
    _audit(
        db,
        company_id,
        "stock_transfer.cancelled",
        transfer.id,
        actor=actor,
        before={"status": StockTransferStatus.IN_TRANSIT.value},
        after={
            "status": transfer.status.value,
            "cancelled_date": on.isoformat(),
            "reason": reason,
            "journal_entry_id": transfer.cancellation_entry_id,
        },
        request=request,
    )
    return transfer


# --- Reading --------------------------------------------------------------------------------


def get_transfer(db: Session, company_id: int, transfer_id: int) -> StockTransfer:
    transfer = db.get(StockTransfer, transfer_id)
    if transfer is None or transfer.company_id != company_id:
        raise NotFoundError("Transfer not found")
    return transfer


def lines_of(db: Session, company_id: int, transfer_id: int) -> list[StockTransferLine]:
    return list(
        db.scalars(
            select(StockTransferLine)
            .where(
                StockTransferLine.company_id == company_id,
                StockTransferLine.transfer_id == transfer_id,
            )
            .order_by(StockTransferLine.line_no)
        )
    )


def list_transfers(
    db: Session,
    company_id: int,
    *,
    status: StockTransferStatus | None = None,
    warehouse_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: int | None = None,
    limit: int = 100,
) -> tuple[list[StockTransfer], int | None]:
    """Newest first, keyset-paged on the id — the P4 listing shape.

    `warehouse_id` matches either end: the question a user asks of this list is "what is
    coming to me and what have I sent", and answering only one of those needs two calls.
    """
    query = select(StockTransfer).where(StockTransfer.company_id == company_id)
    if status is not None:
        query = query.where(StockTransfer.status == status)
    if warehouse_id is not None:
        query = query.where(
            (StockTransfer.from_warehouse_id == warehouse_id)
            | (StockTransfer.to_warehouse_id == warehouse_id)
        )
    if date_from is not None:
        query = query.where(StockTransfer.transfer_date >= date_from)
    if date_to is not None:
        query = query.where(StockTransfer.transfer_date <= date_to)
    if cursor is not None:
        query = query.where(StockTransfer.id < cursor)
    rows = list(db.scalars(query.order_by(StockTransfer.id.desc()).limit(limit + 1)))
    next_cursor = rows[limit].id if len(rows) > limit else None
    return rows[:limit], next_cursor
