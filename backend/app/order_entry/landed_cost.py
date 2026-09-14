"""Landed cost — Evolution's Importation Split (P6 decision 9, plan §B.2).

**The problem.** Goods arrive on a GRN valued at what the supplier charges for them. What they
actually cost is more: freight, insurance, duty, clearing and handling, each arriving on its own
document days or weeks later, each from a different party. None of those documents knows which
receipt lines it belongs to, and the receipt does not know they are coming.

**The mechanism.** Every such cost is booked to the **landed-cost clearing account** — a plain
account, so a forwarder's supplier invoice and a cashbook payment to the revenue authority can
both land on it without either needing to be a special kind of document. This module is what
takes the amount off that account again and puts it into the cost of the stock it was incurred
for. The clearing account is therefore `booked - allocated` at all times, and zero once
everything booked has been allocated — which is the property `assert_order_invariants` proves
and the acceptance tape drives to zero twice.

**Shares sum to the amount, exactly.** Each target's share is `amount x weight / sum(weights)`
rounded half-up, *except* the last, which takes whatever is left. Rounding a set of shares
independently leaves a residue of a franc or two, and a residue is precisely what the clearing
account would be left holding forever. `preview_shares` is the whole of that arithmetic and the
UI's share preview calls the same function the posting does, so what a person is shown before
Post is what Post writes.

**Where a share goes.** For a target whose warehouse still holds the item, into carrying value:
a zero-quantity `revalue_stock()` move, Dr Inventory / Cr Clearing. The average rises from that
posting onward and nothing earlier is restated — so a landed cost allocated after a partial sale
does not reach back into the COGS that sale already posted, which is the behaviour the tape's
row 9 pins. For a target whose location holds **none** of the item any more, the goods it was
incurred for have been sold and there is no carrying value to add it to, so the share goes to
cost of sales instead — on the *same entry*, because one allocation is one document and the
clearing account has to clear in one posting.

That last case is the one thing this step asked of P5. `revalue_stock()` refused an empty
location outright (`nothing_to_revalue`); it now accepts a line carrying `stockless_account_id`
and posts it to that account with no move. Decision 9 named that choice — extend the primitive
minimally rather than post a second entry — and the extension is one branch in `_plan`, under
the existing tests, with the refusal still the default for every caller that does not opt in.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.inventory import stock as stock_service
from app.kernel.errors import LedgerStateError
from app.kernel.money import base_currency, round_amount
from app.kernel.posting import gl_settings_for
from app.kernel.sequences import DocType
from app.models.inventory import GoodsReceivedNote, GoodsReceivedNoteLine, GrnStatus, Item
from app.models.order_entry import (
    LandedCostBasis,
    LandedCostDocument,
    LandedCostLine,
    LandedCostStatus,
)
from app.models.user import User
from app.services.audit import record_audit

ZERO = Decimal(0)


@dataclass(frozen=True)
class LandedCostInput:
    cost_date: date
    description: str
    #: Base currency. The clearing account holds what was booked to it in base, and an
    #: allocation working in document currency would clear a different number from the one
    #: sitting there.
    amount: Decimal
    basis: LandedCostBasis
    #: The GRN lines this cost is spread over. Any supplier, any receipt — a single freight
    #: bill routinely covers consignments from several of them.
    grn_line_ids: tuple[int, ...] = field(default_factory=tuple)
    reference: str | None = None
    source_document_id: int | None = None
    source_cashbook_line_id: int | None = None


@dataclass(frozen=True)
class Share:
    """One target's share of the amount, and everything the caller needs to render it."""

    grn_line: GoodsReceivedNoteLine
    item: Item
    #: What this line contributed to the divisor, in the document's basis.
    weight: Decimal
    share: Decimal


# --- Reads ------------------------------------------------------------------------------------


def get_landed_cost(db: Session, company_id: int, document_id: int) -> LandedCostDocument:
    document = db.scalar(
        select(LandedCostDocument).where(
            LandedCostDocument.company_id == company_id, LandedCostDocument.id == document_id
        )
    )
    if document is None:
        raise NotFoundError("Landed cost document not found")
    return document


def allocated_per_grn_line(
    db: Session, company_id: int, grn_line_ids: Sequence[int]
) -> dict[int, Decimal]:
    """GRN line id → base-currency landed cost allocated onto it by unreversed documents.

    A query, like every other figure in this phase: reversing an allocation takes its shares
    off this sum by construction, with nothing to correct.
    """
    if not grn_line_ids:
        return {}
    rows = db.execute(
        select(LandedCostLine.grn_line_id, LandedCostLine.share)
        .join(LandedCostDocument, LandedCostDocument.id == LandedCostLine.document_id)
        .where(
            LandedCostLine.company_id == company_id,
            LandedCostLine.grn_line_id.in_(grn_line_ids),
            LandedCostDocument.status == LandedCostStatus.POSTED,
        )
    ).all()
    totals: dict[int, Decimal] = {}
    for grn_line_id, share in rows:
        totals[int(grn_line_id)] = totals.get(int(grn_line_id), ZERO) + share
    return totals


# --- The split ----------------------------------------------------------------------------------


def _weight_of(
    basis: LandedCostBasis, line: GoodsReceivedNoteLine, item: Item, index: int
) -> Decimal:
    if basis == LandedCostBasis.VALUE:
        return line.value
    if basis == LandedCostBasis.QUANTITY:
        return line.base_quantity
    if item.weight_per_base_unit is None:
        # Not treated as a weight of nothing. A line with no weight would take a zero share
        # and push its cost silently onto the lines that did carry one, which is a wrong
        # valuation that nothing downstream could detect — so it is refused at the target.
        raise LedgerStateError(
            f"{item.code} has no weight per base unit; a weight-based split needs one",
            code="weight_missing",
            field_errors={f"grn_line_ids.{index}": ["the item has no weight"]},
        )
    return line.base_quantity * item.weight_per_base_unit


def _resolve_targets(
    db: Session, company_id: int, grn_line_ids: Sequence[int]
) -> list[tuple[GoodsReceivedNoteLine, Item]]:
    if not grn_line_ids:
        raise LedgerStateError(
            "A landed cost needs at least one receipt line to spread over",
            code="empty_document",
            field_errors={"grn_line_ids": ["at least one target required"]},
        )
    if len(set(grn_line_ids)) != len(grn_line_ids):
        # Two shares against one line is not the same as one share twice its size: the two
        # would round separately, and the residue rule has one last line, not two.
        raise LedgerStateError(
            "A receipt line can only be a target once",
            code="duplicate_target",
            field_errors={"grn_line_ids": ["a line appears twice"]},
        )
    targets: list[tuple[GoodsReceivedNoteLine, Item]] = []
    for index, line_id in enumerate(grn_line_ids):
        line = db.scalar(
            select(GoodsReceivedNoteLine).where(
                GoodsReceivedNoteLine.company_id == company_id,
                GoodsReceivedNoteLine.id == line_id,
            )
        )
        if line is None:
            raise LedgerStateError(
                "That goods-received line does not exist",
                code="grn_line_not_found",
                field_errors={f"grn_line_ids.{index}": ["not found"]},
            )
        grn = db.scalar(
            select(GoodsReceivedNote).where(
                GoodsReceivedNote.company_id == company_id, GoodsReceivedNote.id == line.grn_id
            )
        )
        if grn is None or grn.status == GrnStatus.REVERSED:
            # The receipt never happened. Adding cost to goods that were taken back would
            # leave the allocation holding value against a line the accrual proof has already
            # unwound.
            raise LedgerStateError(
                "That receipt was reversed and cannot take a landed cost",
                code="grn_reversed",
                field_errors={f"grn_line_ids.{index}": ["reversed"]},
            )
        item = db.get(Item, line.item_id)
        if item is None or item.company_id != company_id:
            raise NotFoundError("Item not found")
        targets.append((line, item))
    return targets


def preview_shares(
    db: Session,
    company_id: int,
    *,
    amount: Decimal,
    basis: LandedCostBasis,
    grn_line_ids: Sequence[int],
) -> list[Share]:
    """The split, computed and not posted — what the screen previews and what `post` writes.

    One function for both, deliberately: a preview that reimplemented the arithmetic would be
    right until the day it was not, and the day it was not is the day somebody approves a set
    of shares and a different set is posted.
    """
    if amount <= ZERO:
        raise LedgerStateError(
            "A landed cost allocates a positive amount",
            code="invalid_amount",
            field_errors={"amount": ["must be greater than zero"]},
        )
    targets = _resolve_targets(db, company_id, grn_line_ids)
    weights = [
        _weight_of(basis, line, item, index) for index, (line, item) in enumerate(targets)
    ]
    total = sum(weights, ZERO)
    if total <= ZERO:
        # Nothing to apportion against: every target scored zero on this basis. Under `value`
        # that is a set of receipts booked at no cost; the split is undefined rather than
        # equal, and guessing would put the whole amount somewhere arbitrary.
        raise LedgerStateError(
            f"Every target has a {basis.value} of zero; there is nothing to split across",
            code="no_basis_weight",
            field_errors={"basis": ["every target weighs zero on this basis"]},
        )

    decimal_places = base_currency(db, company_id).decimal_places
    shares: list[Share] = []
    running = ZERO
    for index, ((line, item), weight) in enumerate(zip(targets, weights, strict=True)):
        if index == len(targets) - 1:
            # **The residue rule.** The last target takes what is left rather than its own
            # rounded share, so the shares sum to the amount exactly however the rounding fell.
            # Without it the clearing account is left holding a franc or two forever, and the
            # invariant that says it clears becomes a statement that is nearly true.
            share = amount - running
        else:
            share = round_amount(amount * weight / total, decimal_places)
        running += share
        shares.append(Share(grn_line=line, item=item, weight=weight, share=share))
    return shares


# --- Posting ------------------------------------------------------------------------------------


def _clearing_account_id(db: Session, company_id: int) -> int:
    settings = gl_settings_for(db, company_id)
    if settings.landed_cost_clearing_account_id is None:
        raise LedgerStateError(
            "No landed-cost clearing account is configured — set it on Order defaults",
            code="gl_setting_missing",
            field_errors={"landed_cost_clearing_account_id": ["required"]},
        )
    return int(settings.landed_cost_clearing_account_id)


def _cogs_account_id(item: Item, default_account_id: int | None) -> int:
    """Where a **stockless** share lands: the item's own COGS account, then the company
    default — the same chain a sale's cost follows, because it is the same cost.

    Resolved for **every** target, not only the ones that turn out to be stockless. Which
    targets those are is not knowable until the costing lock is held inside the stock service,
    and a posting that got that far and then discovered it had no account to use would have to
    refuse after the lock rather than before it. So the account is required of any item taking a
    landed cost — which costs nothing in practice, since a stock item with no COGS account
    cannot be sold either (decision 2 refuses that too).
    """
    account_id = item.cogs_account_id or default_account_id
    if account_id is None:
        raise LedgerStateError(
            f"{item.code} has no COGS account and no company default is set",
            code="gl_setting_missing",
            field_errors={"cogs_account_id": ["required"]},
        )
    return int(account_id)


def _reserve_document_id(db: Session) -> int:
    """The same cycle-breaker the GRN uses: the moves carry `source_doc_id` and cannot be
    updated afterwards, and the document's number comes from the entry those moves produce."""
    from sqlalchemy import text

    return int(
        db.execute(
            text("SELECT nextval(pg_get_serial_sequence('landed_cost_documents', 'id'))")
        ).scalar_one()
    )


def post_landed_cost(
    db: Session,
    company_id: int,
    data: LandedCostInput,
    *,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> tuple[LandedCostDocument, bool]:
    """Spread a cost into the goods it was incurred for, in one entry. Never commits.

    Posted from the moment it exists — there is no draft state (`LandedCostStatus` says why).
    The shares are struck against the receipts and the stock position **as they are now**, and
    a document that sat in a drawer would post shares computed against a position that had
    moved underneath it.
    """
    if idempotency_key:
        existing = db.scalar(
            select(LandedCostDocument).where(
                LandedCostDocument.company_id == company_id,
                LandedCostDocument.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if idempotency_hash and existing.idempotency_hash != idempotency_hash:
                raise LedgerStateError(
                    "That Idempotency-Key was used for a different landed cost",
                    code="idempotency_key_reused",
                )
            return existing, True

    shares = preview_shares(
        db,
        company_id,
        amount=data.amount,
        basis=data.basis,
        grn_line_ids=data.grn_line_ids,
    )
    settings = gl_settings_for(db, company_id)
    clearing_account_id = _clearing_account_id(db, company_id)
    default_cogs_account_id = settings.cogs_account_id
    document_id = _reserve_document_id(db)

    # A share of zero posts nothing — no move, no journal line — so it is not handed to the
    # primitive at all: `revalue_stock` refuses a valueless revaluation (`zero_value_posting`)
    # and it is right to. The target still gets its row; see `LandedCostLine`.
    posting_shares = [(index, share) for index, share in enumerate(shares) if share.share != ZERO]
    stock_document = stock_service.StockDocument(
        doc_type=str(DocType.LANDED_COST),
        move_date=data.cost_date,
        description=data.description,
        reference=data.reference,
        source_doc_type="landed_cost_document",
        source_doc_id=document_id,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    posting = stock_service.revalue_stock(
        db,
        company_id,
        document=stock_document,
        lines=[
            stock_service.StockLine(
                item_id=share.grn_line.item_id,
                warehouse_id=share.grn_line.warehouse_id,
                value=share.share,
                contra_account_id=clearing_account_id,
                # **The stockless rule** (decision 9), decided inside the costing lock rather
                # than here: if that warehouse still holds the item the share goes into
                # carrying value, and if it holds none of it the share goes to cost of sales on
                # this same entry. Reading the balance here instead would let a concurrent sale
                # empty the location between the read and the posting.
                stockless_account_id=_cogs_account_id(share.item, default_cogs_account_id),
                project_id=share.grn_line.project_id,
                description=data.description,
                source_line_id=share.grn_line.id,
            )
            for _index, share in posting_shares
        ],
        actor=actor,
    )

    # **`posting.entry` is never None here**, and that is a property of this call rather than
    # an assumption about the primitive. A stock posting has no entry only when nothing it
    # carried valued anything; every line passed above carries a non-zero share, and the amount
    # is positive, so there is always at least one. The number is the entry's, the way a GRN's
    # is — and unlike a GRN there is no valueless case to claim one separately.
    document = LandedCostDocument(
        id=document_id,
        company_id=company_id,
        number=posting.entry.number,
        cost_date=data.cost_date,
        description=data.description,
        reference=data.reference,
        amount=data.amount,
        basis=data.basis,
        status=LandedCostStatus.POSTED,
        source_document_id=data.source_document_id,
        source_cashbook_line_id=data.source_cashbook_line_id,
        journal_entry_id=posting.entry.id,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    db.add(document)
    db.flush()

    # `keyed_moves` holds a place per line passed in, with `None` where the share went to cost
    # of sales and wrote no move. That `None` *is* the stockless answer, read back from what the
    # service decided under the lock rather than re-derived here from a balance that has since
    # been written.
    moves = dict(zip((index for index, _ in posting_shares), posting.keyed_moves, strict=True))
    db.add_all(
        [
            LandedCostLine(
                company_id=company_id,
                document_id=document.id,
                line_no=index + 1,
                grn_line_id=share.grn_line.id,
                item_id=share.grn_line.item_id,
                warehouse_id=share.grn_line.warehouse_id,
                weight=share.weight,
                share=share.share,
                went_to_cogs=index in moves and moves[index] is None,
                stock_move_id=moves[index].id if moves.get(index) is not None else None,
            )
            for index, share in enumerate(shares)
        ]
    )
    db.flush()
    record_audit(
        db,
        company_id=company_id,
        action="landed_cost_document.posted",
        entity="landed_cost_documents",
        entity_id=document.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        after={
            "number": document.number,
            "amount": str(data.amount),
            "basis": data.basis.value,
            "cost_date": data.cost_date.isoformat(),
            "targets": len(shares),
        },
        request=request,
    )
    return document, False


# --- Reversal -------------------------------------------------------------------------------


def reverse_landed_cost(
    db: Session,
    document: LandedCostDocument,
    *,
    on_date: date,
    reason: str,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> LandedCostDocument:
    """Take the allocation back out — the P5 stock reversal at the original values.

    One call does both halves: the revaluation moves mirror exactly, and the cost-of-sales
    lines come back with them because they are lines on the same entry.

    **The fallible half goes first, and here it is the only half** — the step-3 rule, which
    this document is a caller of rather than a second copy of. `reverse_stock_posting()` is
    what can refuse: under `block` it will not take value back off a location the goods have
    since left. Everything after it is a status column and an audit row, neither of which can
    fail, so a refused reversal has written nothing at the point it raises. That matters
    because this function is a **multi-step caller** — a caller that reverses as one step of
    several and has no savepoint to unwind — and `test_a_refused_reversal_leaves_nothing_behind`
    is the shape of the test that holds it to it.
    """
    if document.status == LandedCostStatus.REVERSED:
        raise LedgerStateError(
            f"{document.number} was already reversed", code="landed_cost_already_reversed"
        )
    if document.journal_entry_id is not None:
        reversal = stock_service.reverse_stock_posting(
            db,
            document.company_id,
            entry_id=document.journal_entry_id,
            on_date=on_date,
            reason=reason,
            actor=actor,
            idempotency_key=idempotency_key,
            idempotency_hash=idempotency_hash,
        )
        document.reversal_entry_id = reversal.entry.id if reversal.entry is not None else None
    document.status = LandedCostStatus.REVERSED
    document.reversed_on = on_date
    db.flush()
    record_audit(
        db,
        company_id=document.company_id,
        action="landed_cost_document.reversed",
        entity="landed_cost_documents",
        entity_id=document.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        after={"reason": reason, "on": on_date.isoformat()},
        request=request,
    )
    return document
