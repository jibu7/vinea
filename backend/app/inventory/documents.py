"""Stock documents: adjustments and journal batches (P5 step 3, decisions 3, 9, 11 and 12).

This module is a *mouth*, not a stomach. Every rule that decides what a move is worth lives
in `costing.py`, and every rule about how a move reaches the ledger lives in `stock.py`. What
happens here is the part a user would recognise as a document: a transaction type is resolved
to a direction, a quantity typed in cases becomes a quantity in eaches, a number is claimed,
and the whole thing is refused as one unit or accepted as one unit.

**Direction comes from the transaction type's `kind`** (decision 9), never from the sign of
what was typed. `adjustment_in` and `opening_balance` receive, `adjustment_out` issues,
`revaluation` moves value alone. That is why a user can define "Damaged" and "Samples" as
their own types with their own contra accounts and no code here changes: the set of kinds is
closed, the set of codes is not.

**A batch is one posting, not many.** Decision 3 says one stock document produces one journal
entry, so a batch's lines are composed into a single `post_stock_moves` call rather than
posted one at a time. This is the one place P5 builds `SignedLine`s itself instead of going
through `receive_stock()` / `issue_stock()`: a batch's lines differ in direction from each
other, and the named primitives each commit to one direction for the whole call. The
single-item adjustment path below does use them, which is what keeps decision 13's promise
that P6 finds those primitives unchanged.

**Refused whole.** Nothing here commits. A line that fails raises, the caller's transaction
rolls back, and a batch that was half-valued leaves nothing behind — including the document
header, which is written after the posting returns rather than before.
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
from app.kernel.posting import gl_settings_for
from app.kernel.sequences import DocType, claim_number
from app.models.gl import GLTransactionType
from app.models.inventory import (
    INVENTORY_MODULE,
    InventoryDocument,
    InventoryDocumentLine,
    InventoryDocumentStatus,
    InventoryTransactionKind,
    Item,
    StockMove,
    Uom,
)
from app.models.journal import JournalEntry
from app.models.user import User
from app.services.audit import record_audit

#: The kinds that may appear on an adjustment or a journal batch. `transfer` and
#: `count_variance` belong to documents that raise their own moves (step 4) and are refused
#: here, so a user cannot hand-key a transfer's half and leave stock in transit forever.
DOCUMENT_KINDS = (
    InventoryTransactionKind.ADJUSTMENT_IN,
    InventoryTransactionKind.ADJUSTMENT_OUT,
    InventoryTransactionKind.REVALUATION,
    InventoryTransactionKind.OPENING_BALANCE,
)

#: Kinds that put quantity in. Everything in this set needs a unit cost, because there is no
#: average to charge an incoming unit at — the incoming unit is what *makes* the average.
RECEIVING_KINDS = (
    InventoryTransactionKind.ADJUSTMENT_IN,
    InventoryTransactionKind.OPENING_BALANCE,
)



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
    """Every audited call carries a real actor — never a fabricated "system" user."""
    record_audit(
        db,
        company_id=company_id,
        action=action,
        entity="inventory_documents",
        entity_id=entity_id,
        before=before,
        after=after,
        actor_user_id=actor.id,
        actor_email=actor.email,
        request=request,
    )



# --- Inputs ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentLineInput:
    """One keyed line. `quantity` is a magnitude in `uom_id`; the direction is the kind's."""

    item_id: int
    warehouse_id: int
    quantity: Decimal = ZERO
    uom_id: int | None = None
    unit_cost: Decimal | None = None
    #: A revaluation's amount, signed: positive writes the stock up, negative writes it down.
    value: Decimal | None = None
    transaction_type_id: int | None = None
    contra_account_id: int | None = None
    project_id: int | None = None
    description: str | None = None


@dataclass(frozen=True)
class DocumentInput:
    document_date: date
    description: str
    reference: str | None = None
    #: The header's transaction type. An adjustment always has one; a batch may carry one as
    #: the default for lines that do not name their own.
    transaction_type_id: int | None = None
    lines: Sequence[DocumentLineInput] = field(default_factory=tuple)


# --- Resolution -----------------------------------------------------------------------------


def _on_line(index: int, error: AppError) -> AppError:
    """Re-key an error onto the line that caused it, so the entry grid can show it in place —
    the same re-pointing `stock._on_line` and P4's batch do."""
    error.field_errors = {
        (key if key.startswith("lines.") else f"lines.{index}.{key}"): value
        for key, value in error.field_errors.items()
    } or {f"lines.{index}": [error.code]}
    return error


def _transaction_type(db: Session, company_id: int, type_id: int) -> GLTransactionType:
    row = db.get(GLTransactionType, type_id)
    if row is None or row.company_id != company_id or row.module != INVENTORY_MODULE:
        raise NotFoundError("Inventory transaction type not found")
    if not row.is_active:
        raise LedgerStateError(
            f"Transaction type {row.code} is not active",
            code="transaction_type_inactive",
            field_errors={"transaction_type_id": ["not active"]},
        )
    if row.kind not in DOCUMENT_KINDS:
        raise LedgerStateError(
            f"{row.code} is a {row.kind.value} type and cannot be keyed on this document",
            code="transaction_kind_not_allowed",
            field_errors={"transaction_type_id": ["not valid on this document"]},
        )
    return row


@dataclass(frozen=True)
class _Resolved:
    """A line with its direction, its base quantity and the unit it was keyed in decided."""

    source: DocumentLineInput
    kind: InventoryTransactionKind
    transaction_type_id: int
    uom: Uom
    quantity_base: Decimal
    signed_quantity: Decimal
    unit_cost: Decimal | None
    value: Decimal | None


def _resolve(
    db: Session, company_id: int, data: DocumentInput, index: int, line: DocumentLineInput
) -> _Resolved:
    """Turn one keyed line into the facts a posting needs, or refuse it.

    Every refusal here happens before a single move exists, which is what decision 12's
    "refused whole" means in practice: a batch whose fourth line names a service item does not
    post its first three.
    """
    type_id = line.transaction_type_id or data.transaction_type_id
    if type_id is None:
        raise _on_line(
            index,
            PostingError(
                "This line has no transaction type",
                code="transaction_type_required",
                field_errors={"transaction_type_id": ["required"]},
            ),
        )
    try:
        txn_type = _transaction_type(db, company_id, type_id)
    except AppError as err:
        raise _on_line(index, err) from err
    kind = txn_type.kind

    item = db.get(Item, line.item_id)
    if item is None or item.company_id != company_id:
        raise _on_line(index, NotFoundError("Item not found"))

    # The unit the quantity was keyed in. Defaulting to the item's own base unit keeps the
    # API usable without a UoM column; `to_base_quantity` still refuses a foreign category.
    uom_id = line.uom_id or item.base_uom_id
    uom = db.get(Uom, uom_id)
    if uom is None or uom.company_id != company_id:
        raise _on_line(index, NotFoundError("Unit of measure not found"))

    if kind == InventoryTransactionKind.REVALUATION:
        if line.value is None or line.value == ZERO:
            raise _on_line(
                index,
                PostingError(
                    "A revaluation line needs a non-zero value",
                    code="zero_value_posting",
                    field_errors={"value": ["required and non-zero"]},
                ),
            )
        if line.quantity != ZERO:
            raise _on_line(
                index,
                PostingError(
                    "A revaluation moves value, not quantity",
                    code="invalid_revaluation",
                    field_errors={"quantity": ["must be zero"]},
                ),
            )
        return _Resolved(
            source=line,
            kind=kind,
            transaction_type_id=type_id,
            uom=uom,
            quantity_base=ZERO,
            signed_quantity=ZERO,
            unit_cost=None,
            value=line.value,
        )

    if line.quantity <= ZERO:
        raise _on_line(
            index,
            PostingError(
                "A stock line moves a positive quantity",
                code="invalid_quantity",
                field_errors={"quantity": ["must be positive"]},
            ),
        )
    try:
        quantity_base = masters.to_base_quantity(line.quantity, uom, item)
    except AppError as err:
        raise _on_line(index, err) from err
    if quantity_base <= ZERO:
        # A pack factor small enough to round a real quantity away to nothing. Posting it
        # would write a move that moves neither quantity nor value, which the schema refuses
        # anyway — better to say why here than to surface a check-constraint violation.
        raise _on_line(
            index,
            PostingError(
                f"{line.quantity:f} {uom.code} converts to nothing in {item.code}'s base unit",
                code="quantity_rounds_to_zero",
                field_errors={"quantity": ["too small for this unit of measure"]},
            ),
        )

    receiving = kind in RECEIVING_KINDS
    if receiving:
        if line.unit_cost is None:
            raise _on_line(
                index,
                PostingError(
                    "An increase needs a unit cost",
                    code="unit_cost_required",
                    field_errors={"unit_cost": ["required"]},
                ),
            )
        if line.unit_cost < ZERO:
            raise _on_line(
                index,
                PostingError(
                    "A unit cost cannot be negative",
                    code="invalid_unit_cost",
                    field_errors={"unit_cost": ["must not be negative"]},
                ),
            )
    elif line.unit_cost is not None:
        # An issue is costed at the item's average, always. Accepting a rate here and
        # ignoring it would be worse than refusing it: the user would believe it applied.
        raise _on_line(
            index,
            PostingError(
                "A decrease is costed at the item's average; it takes no unit cost",
                code="unit_cost_not_allowed",
                field_errors={"unit_cost": ["not valid on a decrease"]},
            ),
        )

    # The unit cost is per *keyed* unit; the move is in base units. Converting the rate the
    # other way keeps `quantity_base × unit_cost` equal to what the user meant to spend.
    unit_cost = line.unit_cost
    if receiving and unit_cost is not None and quantity_base != line.quantity:
        unit_cost = (line.quantity * unit_cost) / quantity_base

    return _Resolved(
        source=line,
        kind=kind,
        transaction_type_id=type_id,
        uom=uom,
        quantity_base=quantity_base,
        signed_quantity=quantity_base if receiving else -quantity_base,
        unit_cost=unit_cost,
        value=None,
    )


# --- Posting --------------------------------------------------------------------------------


def _replay(
    db: Session, company_id: int, key: str, request_hash: str | None
) -> InventoryDocument | None:
    """Resolve an `Idempotency-Key` to the document it already produced.

    This is the check the kernel cannot do for us. `posting.replay` finds a *journal entry* by
    its key, and a posting in which nothing carried value has no entry — so a retried batch of
    zero-cost receipts would post its moves a second time with nothing to stop it. The key is
    unique on this table, which makes the replay total: valued or not, a document answers for
    its own key.
    """
    document = db.scalar(
        select(InventoryDocument).where(
            InventoryDocument.company_id == company_id,
            InventoryDocument.idempotency_key == key,
        )
    )
    if document is None:
        return None
    if request_hash is not None and document.idempotency_hash not in (None, request_hash):
        raise ConflictError(
            f"Idempotency-Key {key} was already used for a different request "
            f"({document.number}); use a new key",
            code="idempotency_key_reused",
        )
    return document


def _write_document(
    db: Session,
    company_id: int,
    *,
    doc_type: str,
    data: DocumentInput,
    resolved: Sequence[_Resolved],
    posting: stock_service.StockPosting,
    actor: User,
    idempotency_key: str | None,
    idempotency_hash: str | None,
    request: Request | None,
) -> InventoryDocument:
    """Write the header and its lines, after the posting has returned.

    The order is forced, not chosen. `stock_moves` refuses UPDATE, so a move's `source_doc_id`
    would have to be known before the move is written; and the document's number is the
    entry's number, which is not known until the entry is posted. So the document follows the
    posting and links forward, through `stock_move_id` on each line. P4's `partner_documents`
    sits in exactly the same order for the same reason.
    """
    entry: JournalEntry | None = posting.entry
    if entry is not None:
        number = entry.number
    else:
        # Nothing valued, so there is no entry to take a number from. Claiming one from the
        # same sequence keeps it gapless: every number in the run belongs to either an entry
        # or a valueless document, and none is skipped.
        number = claim_number(db, company_id, doc_type).number

    document = InventoryDocument(
        company_id=company_id,
        doc_type=str(doc_type),
        number=number,
        document_date=data.document_date,
        description=data.description,
        reference=data.reference,
        transaction_type_id=data.transaction_type_id,
        status=InventoryDocumentStatus.POSTED,
        journal_entry_id=entry.id if entry is not None else None,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    db.add(document)
    db.flush()

    # `keyed_moves` is the service's own statement of which move each input line became.
    # Counting positions in `posting.moves` would be wrong: the service raises moves of its
    # own — the variance that settles a negative-stock crossing — and interleaves them, so
    # every line after the first residue would be attributed to the wrong move.
    keyed_moves = posting.keyed_moves
    for index, item in enumerate(resolved):
        move = keyed_moves[index] if index < len(keyed_moves) else None
        db.add(
            InventoryDocumentLine(
                company_id=company_id,
                document_id=document.id,
                line_no=index + 1,
                item_id=item.source.item_id,
                warehouse_id=item.source.warehouse_id,
                quantity=item.source.quantity,
                uom_id=item.uom.id,
                quantity_base=item.quantity_base,
                unit_cost=item.unit_cost,
                value=item.value,
                transaction_type_id=item.transaction_type_id,
                contra_account_id=item.source.contra_account_id,
                project_id=item.source.project_id,
                description=item.source.description,
                stock_move_id=move.id if move is not None else None,
            )
        )
    db.flush()
    _audit(
        db,
        company_id,
        "inventory_document.posted",
        document.id,
        actor=actor,
        after={
            "number": document.number,
            "doc_type": document.doc_type,
            "document_date": document.document_date.isoformat(),
            "lines": len(resolved),
            "journal_entry_id": document.journal_entry_id,
        },
        request=request,
    )
    return document


def _stock_lines(
    resolved: Sequence[_Resolved], data: DocumentInput
) -> list[stock_service.StockLine]:
    return [
        stock_service.StockLine(
            item_id=item.source.item_id,
            warehouse_id=item.source.warehouse_id,
            quantity=item.quantity_base,
            unit_cost=item.unit_cost,
            value=item.value,
            transaction_type_id=item.transaction_type_id,
            contra_account_id=item.source.contra_account_id,
            project_id=item.source.project_id,
            description=item.source.description or data.description,
        )
        for item in resolved
    ]


def post_document(
    db: Session,
    company_id: int,
    data: DocumentInput,
    *,
    doc_type: str = DocType.INV_ADJUSTMENT,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> tuple[InventoryDocument, bool]:
    """Post one adjustment or one journal batch. Never commits.

    One code path serves both, because they differ in exactly two ways that are already
    arguments: which sequence the number comes from, and whether the lines are allowed to
    disagree about direction. The endpoints below fix those two and share everything else —
    the validation, the refusal semantics, the numbering, the audit row.
    """
    if not data.lines:
        raise LedgerStateError(
            "A stock document needs at least one line",
            code="empty_document",
            field_errors={"lines": ["at least one line required"]},
        )
    if idempotency_key:
        existing = _replay(db, company_id, idempotency_key, idempotency_hash)
        if existing is not None:
            return existing, True

    resolved = [
        _resolve(db, company_id, data, index, line) for index, line in enumerate(data.lines)
    ]
    document = stock_service.StockDocument(
        doc_type=str(doc_type),
        move_date=data.document_date,
        description=data.description,
        reference=data.reference,
        transaction_type_id=data.transaction_type_id,
        source_doc_type="inventory_document",
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    lines = _stock_lines(resolved, data)

    kinds = {item.kind for item in resolved}
    if kinds == {InventoryTransactionKind.REVALUATION}:
        posting = stock_service.revalue_stock(
            db, company_id, document=document, lines=lines, actor=actor
        )
    elif kinds <= set(RECEIVING_KINDS):
        posting = stock_service.receive_stock(
            db, company_id, document=document, lines=lines, actor=actor
        )
    elif kinds == {InventoryTransactionKind.ADJUSTMENT_OUT}:
        posting = stock_service.issue_stock(
            db, company_id, document=document, lines=lines, actor=actor
        )
    else:
        # A mixed batch. Decision 3 wants one entry for the whole document, so the directions
        # are composed here rather than split across several postings — the one place P5 signs
        # its own lines instead of calling a primitive that signs them for it.
        posting = stock_service.post_stock_moves(
            db,
            company_id,
            document=document,
            moves=[
                stock_service.SignedLine(line=line, quantity=item.signed_quantity)
                for line, item in zip(lines, resolved, strict=True)
            ],
            actor=actor,
        )

    return (
        _write_document(
            db,
            company_id,
            doc_type=doc_type,
            data=data,
            resolved=resolved,
            posting=posting,
            actor=actor,
            idempotency_key=idempotency_key,
            idempotency_hash=idempotency_hash,
            request=request,
        ),
        False,
    )


def post_adjustment(
    db: Session,
    company_id: int,
    data: DocumentInput,
    *,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> tuple[InventoryDocument, bool]:
    """A single-item adjustment: an increase, a decrease or a revaluation (decision 9).

    "Single item" is the document's shape, not a limit of the machinery underneath — it is
    what the Adjustments screen keys. Anything wider is a journal batch, which is why the two
    endpoints exist rather than one with a mode flag.
    """
    if len(data.lines) != 1:
        raise LedgerStateError(
            "An adjustment carries exactly one line; use a journal batch for more",
            code="adjustment_is_single_line",
            field_errors={"lines": ["exactly one line"]},
        )
    return post_document(
        db,
        company_id,
        data,
        doc_type=DocType.INV_ADJUSTMENT,
        actor=actor,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
        request=request,
    )


def post_batch(
    db: Session,
    company_id: int,
    data: DocumentInput,
    *,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> tuple[InventoryDocument, bool]:
    """A journal batch: many items, many warehouses, mixed directions, one unit of work.

    This is the go-live path for opening stock (decision 2): inventory accounts are control
    accounts, so opening balances cannot arrive as a GL journal — they come through here under
    an `opening_balance` transaction type.
    """
    return post_document(
        db,
        company_id,
        data,
        doc_type=DocType.INV_JOURNAL,
        actor=actor,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
        request=request,
    )


# --- Count variances -------------------------------------------------------------------------


@dataclass(frozen=True)
class VarianceLine:
    """One non-zero variance from a count session, already converted and already costed.

    `quantity_base` is **signed** — positive is stock found, negative is stock missing. This
    is the one document whose direction is a fact about the line rather than about the
    transaction type (decision 7 gives a count exactly one kind for both), which is why it
    does not arrive as a `DocumentLineInput` with its sign in the type's `kind`.
    """

    item_id: int
    warehouse_id: int
    uom_id: int
    quantity_base: Decimal
    #: Only on a gain: what the found stock is taken in at, which decision 7 fixes as the
    #: item's current average. A loss is costed by the stock service like any other issue.
    unit_cost: Decimal | None = None
    project_id: int | None = None
    description: str | None = None
    source_line_id: int | None = None


def count_variance_type(
    db: Session, company_id: int, type_id: int | None
) -> GLTransactionType:
    """The count's transaction type, or the company's own when it has exactly one.

    Defaulting rather than requiring: a count sheet is opened by whoever is going to walk the
    aisles, and asking them which transaction type a variance should post under is asking the
    wrong person. Naming one is still allowed, which is how "Shrinkage" and "Breakage" get
    their own contra accounts (decision 9).
    """
    if type_id is None:
        candidates = list(
            db.scalars(
                select(GLTransactionType).where(
                    GLTransactionType.company_id == company_id,
                    GLTransactionType.module == INVENTORY_MODULE,
                    GLTransactionType.kind == InventoryTransactionKind.COUNT_VARIANCE,
                    GLTransactionType.is_active,
                )
            )
        )
        if len(candidates) != 1:
            raise LedgerStateError(
                "This company has no single default count-variance type; name one on the "
                "session"
                if candidates
                else "This company has no active count-variance transaction type",
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
    if row.kind != InventoryTransactionKind.COUNT_VARIANCE:
        raise LedgerStateError(
            f"{row.code} is a {row.kind.value} type and cannot carry a count variance",
            code="transaction_kind_not_allowed",
            field_errors={"transaction_type_id": ["not valid on a count"]},
        )
    return row


def variance_contra_account(
    db: Session, company_id: int, txn_type: GLTransactionType
) -> int | None:
    """Where a count variance lands: the transaction type's contra, or — when it names none —
    the `stock_count_variance_account` setting of decision 10.

    Precedence in that order because decision 9 makes the type the place a user says "count
    this kind of loss to that account", while decision 10 gives the company one default for
    when nobody has said anything. Returning `None` when the type has its own contra leaves
    the stock service to resolve it, rather than resolving it twice in two places.
    """
    if txn_type.default_gl_account_id is not None:
        return None
    settings = gl_settings_for(db, company_id)
    account_id = settings.stock_count_variance_account_id
    if account_id is None:
        raise PostingError(
            "The stock count variance account is not set; a count cannot post without it",
            code="gl_setting_missing",
            field_errors={"stock_count_variance_account_id": ["required"]},
        )
    return int(account_id)


def post_count_variance(
    db: Session,
    company_id: int,
    data: DocumentInput,
    *,
    lines: Sequence[VarianceLine],
    transaction_type_id: int,
    contra_account_id: int | None = None,
    source_doc_type: str,
    source_doc_id: int,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> tuple[InventoryDocument, bool]:
    """The document a processed count posts: every non-zero variance, in one entry.

    It is an ordinary `inventory_documents` row of doc type `INCT`, which is the whole point —
    a count variance is reversed, listed, numbered and drilled into by exactly the machinery
    step 3 built, and decision 11's "a processed count stays Completed and links to the
    reversal" needs no count-specific reversal path to be true.

    Gains and losses ride in one posting rather than two, so the entry balances the way the
    count reads: one document, one number, one set of variances, refused whole.
    """
    if not lines:
        raise LedgerStateError(
            "A count variance document needs at least one line",
            code="empty_document",
            field_errors={"lines": ["at least one line required"]},
        )
    if idempotency_key:
        existing = _replay(db, company_id, idempotency_key, idempotency_hash)
        if existing is not None:
            return existing, True

    def _uom(uom_id: int) -> Uom:
        uom = db.get(Uom, uom_id)
        if uom is None or uom.company_id != company_id:
            raise NotFoundError("Unit of measure not found")
        return uom

    resolved = [
        _Resolved(
            source=DocumentLineInput(
                item_id=line.item_id,
                warehouse_id=line.warehouse_id,
                # Signed, deliberately: on this one document the direction belongs to the
                # line, so the sheet reads the way it was counted — found 3, missing 1.
                quantity=line.quantity_base,
                uom_id=line.uom_id,
                unit_cost=line.unit_cost,
                transaction_type_id=transaction_type_id,
                contra_account_id=contra_account_id,
                project_id=line.project_id,
                description=line.description,
            ),
            kind=InventoryTransactionKind.COUNT_VARIANCE,
            transaction_type_id=transaction_type_id,
            uom=_uom(line.uom_id),
            quantity_base=line.quantity_base,
            signed_quantity=line.quantity_base,
            unit_cost=line.unit_cost,
            value=None,
        )
        for line in lines
    ]
    document = stock_service.StockDocument(
        doc_type=str(DocType.INV_COUNT),
        move_date=data.document_date,
        description=data.description,
        reference=data.reference,
        transaction_type_id=transaction_type_id,
        source_doc_type=source_doc_type,
        source_doc_id=source_doc_id,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    posting = stock_service.post_stock_moves(
        db,
        company_id,
        document=document,
        moves=[
            stock_service.SignedLine(
                line=stock_service.StockLine(
                    item_id=line.item_id,
                    warehouse_id=line.warehouse_id,
                    quantity=abs(line.quantity_base),
                    unit_cost=line.unit_cost,
                    transaction_type_id=transaction_type_id,
                    contra_account_id=contra_account_id,
                    project_id=line.project_id,
                    description=line.description or data.description,
                    source_line_id=line.source_line_id,
                ),
                quantity=line.quantity_base,
            )
            for line in lines
        ],
        actor=actor,
    )
    return (
        _write_document(
            db,
            company_id,
            doc_type=DocType.INV_COUNT,
            data=data,
            resolved=resolved,
            posting=posting,
            actor=actor,
            idempotency_key=idempotency_key,
            idempotency_hash=idempotency_hash,
            request=request,
        ),
        False,
    )


# --- Reversal -------------------------------------------------------------------------------


def reverse_document(
    db: Session,
    company_id: int,
    document_id: int,
    *,
    on_date: date,
    reason: str,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> InventoryDocument:
    """Decision 11: the kernel reversal, plus reversing moves at the original values.

    The reversal is itself a document, so it has a number, appears in the listings, and can be
    found from the thing it reverses. The original stays `posted` in every sense that matters
    to the ledger — it is not unwritten — but its status says `reversed` so a screen does not
    offer to reverse it twice.

    The negative-stock policy still applies underneath: under `block`, a receipt whose quantity
    has since been issued cannot be reversed, and `stock.reverse_stock_posting` refuses it with
    `insufficient_stock` before anything is written.
    """
    if idempotency_key:
        existing = _replay(db, company_id, idempotency_key, idempotency_hash)
        if existing is not None:
            return existing

    original = db.get(InventoryDocument, document_id)
    if original is None or original.company_id != company_id:
        raise NotFoundError("Stock document not found")
    if original.status == InventoryDocumentStatus.REVERSED:
        raise LedgerStateError(
            f"{original.number} was already reversed",
            code="document_already_reversed",
        )
    if original.reverses_document_id is not None:
        raise LedgerStateError(
            f"{original.number} is itself a reversal; post a new document instead",
            code="cannot_reverse_a_reversal",
        )
    original_lines = list(
        db.scalars(
            select(InventoryDocumentLine)
            .where(
                InventoryDocumentLine.company_id == company_id,
                InventoryDocumentLine.document_id == original.id,
            )
            .order_by(InventoryDocumentLine.line_no)
        )
    )

    if original.journal_entry_id is None:
        # Decision 4: a valueless document reverses as a valueless mirror.
        #
        # Nothing here valued, so the kernel has no entry to reverse — but the quantity is on
        # the shelf and has to be able to come back off it. Refusing would leave a zero-cost
        # receipt as the one posting in the system that cannot be undone, which is a worse
        # answer than mirroring it: the moves reverse, the caches follow, and the ledger says
        # nothing in both directions because it had nothing to say in the first place.
        #
        # The document's moves are found through its own lines rather than through an entry,
        # which is the reason `stock_move_id` is on the line at all.
        originals = [
            move
            for line in original_lines
            if line.stock_move_id is not None
            and (move := db.get(StockMove, line.stock_move_id)) is not None
        ]
        posting = stock_service.reverse_unvalued_moves(
            db,
            company_id,
            originals=originals,
            on_date=on_date,
            actor=actor,
        )
    else:
        posting = stock_service.reverse_stock_posting(
            db,
            company_id,
            entry_id=original.journal_entry_id,
            on_date=on_date,
            reason=reason,
            actor=actor,
            idempotency_key=idempotency_key,
            idempotency_hash=idempotency_hash,
        )
    reversal = InventoryDocument(
        company_id=company_id,
        doc_type=original.doc_type,
        number=(
            posting.entry.number
            if posting.entry is not None
            # A valueless mirror has no entry to take a number from, so it claims its own —
            # the same rule the valueless document it reverses followed on the way in. Never
            # the original's number: two documents sharing one number would break the
            # per-company uniqueness the sequence exists to guarantee.
            else claim_number(db, company_id, original.doc_type).number
        ),
        document_date=on_date,
        description=f"Reversal of {original.number}: {reason}",
        reference=original.reference,
        transaction_type_id=original.transaction_type_id,
        status=InventoryDocumentStatus.POSTED,
        journal_entry_id=posting.entry.id if posting.entry is not None else None,
        reverses_document_id=original.id,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    db.add(reversal)
    db.flush()

    mirrored = {move.reverses_move_id: move for move in posting.moves}
    for line in original_lines:
        move = mirrored.get(line.stock_move_id)
        db.add(
            InventoryDocumentLine(
                company_id=company_id,
                document_id=reversal.id,
                line_no=line.line_no,
                item_id=line.item_id,
                warehouse_id=line.warehouse_id,
                quantity=line.quantity,
                uom_id=line.uom_id,
                quantity_base=line.quantity_base,
                unit_cost=line.unit_cost,
                value=-line.value if line.value is not None else None,
                transaction_type_id=line.transaction_type_id,
                contra_account_id=line.contra_account_id,
                project_id=line.project_id,
                description=line.description,
                stock_move_id=move.id if move is not None else None,
            )
        )
    original.status = InventoryDocumentStatus.REVERSED
    original.reversal_entry_id = reversal.journal_entry_id
    db.flush()
    _audit(
        db,
        company_id,
        "inventory_document.reversed",
        original.id,
        actor=actor,
        before={"status": InventoryDocumentStatus.POSTED.value},
        after={"status": original.status.value, "reversal": reversal.number},
        request=request,
    )
    return reversal


# --- Reading --------------------------------------------------------------------------------


def get_document(db: Session, company_id: int, document_id: int) -> InventoryDocument:
    document = db.get(InventoryDocument, document_id)
    if document is None or document.company_id != company_id:
        raise NotFoundError("Stock document not found")
    return document


def lines_of(
    db: Session, company_id: int, document_id: int
) -> list[InventoryDocumentLine]:
    return list(
        db.scalars(
            select(InventoryDocumentLine)
            .where(
                InventoryDocumentLine.company_id == company_id,
                InventoryDocumentLine.document_id == document_id,
            )
            .order_by(InventoryDocumentLine.line_no)
        )
    )


def list_documents(
    db: Session,
    company_id: int,
    *,
    doc_type: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: int | None = None,
    limit: int = 100,
) -> tuple[list[InventoryDocument], int | None]:
    """Newest first, keyset-paged on the id — the P4 listing shape."""
    query = select(InventoryDocument).where(InventoryDocument.company_id == company_id)
    if doc_type is not None:
        query = query.where(InventoryDocument.doc_type == str(doc_type))
    if date_from is not None:
        query = query.where(InventoryDocument.document_date >= date_from)
    if date_to is not None:
        query = query.where(InventoryDocument.document_date <= date_to)
    if cursor is not None:
        query = query.where(InventoryDocument.id < cursor)
    rows = list(db.scalars(query.order_by(InventoryDocument.id.desc()).limit(limit + 1)))
    next_cursor = rows[limit].id if len(rows) > limit else None
    return rows[:limit], next_cursor
