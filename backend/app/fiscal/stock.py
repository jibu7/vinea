"""Stock reporting (decision 10) — what a revenue authority is told about the shelf.

**Every stock posting on a fiscalized company reports itself, in the transaction that wrote
the moves.** One `stock_io` row saying what moved, then one `stock_master` row per item saying
what is left. The second is a *snapshot taken at enqueue*: by the time the queue drains the
shelf has moved on, and what the authority is being told is what was true at the moment of the
movement it has just received.

Four things this module decides, and each is a rule rather than a detail:

**The report is per branch.** The authority holds one stock figure per (taxpayer, branch) and a
device belongs to one branch, so a posting's moves are grouped by the branch of their
warehouse and each group is its own movement on its own device. Ordinarily that is one group
and decision 10's "one `stock_io` row" is literally what happens; a posting that reaches two
branches is two movements, because there is no third thing it could be.

**The report is per direction.** `sarTyCd` says which way stock went, so a posting that both
receives and issues — a count that found one item over and another short — is two movements.
Grouping them into one would mean choosing a direction for a figure that has both.

**In-transit stock is nobody's.** Warehouses marked `is_in_transit` are excluded from both the
movement and the on-hand snapshot: goods that have left one shop and not arrived at the next
are not on any branch's shelf, and telling the authority they are would put stock at a branch
that cannot sell it.

**A movement inside one branch is not reported at all.** That is decision 10 for transfers, and
it needs the *other* leg's branch to decide — which is why `counterpart_branch_id` travels with
the posting. Without it a dispatch cannot tell "leaving this branch" from "moving shelves",
because the stock it has just put into transit has not reached its destination yet.

Nothing here talks to a revenue authority and nothing here knows an RRA field name: the facing
and the direction are Vinea's words, and `rwanda/builders.STOCK_IO_TYPE` is what turns them
into a code.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.fiscal import devices as device_service
from app.fiscal import items as item_service
from app.fiscal import outbox
from app.fiscal.mapping import (
    FiscalStockIO,
    FiscalStockLine,
    FiscalStockMaster,
    StockMovementFacing,
)
from app.fiscal.protocol import FiscalizationAdapter
from app.fiscal.registry import adapter_for
from app.kernel.sequences import DocType, claim_number
from app.models.company import Company
from app.models.fiscalization import (
    FiscalDevice,
    FiscalOutboxKind,
    FiscalOutboxRow,
    FiscalOutboxStatus,
)
from app.models.inventory import Item, StockBalance, StockMove, Warehouse
from app.models.tax import TaxCode
from app.models.user import User

ZERO = Decimal(0)

#: `stock_moves.source_doc_type` → which side of the business the movement faces, for the
#: postings whose facing can be read off the document they came from.
#:
#: A **partner document is deliberately absent**: an issue on one is either a sale or a return
#: to a supplier, which the source type cannot tell apart, and — more importantly — a partner
#: document's movement must be reported *after* the sale or purchase that caused it (VSDC §3.1,
#: and RRA answers `921`/`922` when it is not). So `post_document` reports its own companion,
#: once its sale row is in the queue, and passes the facing it knows.
FACING_BY_SOURCE: dict[str, StockMovementFacing] = {
    "goods_received_note": StockMovementFacing.SUPPLIER,
    "inventory_document": StockMovementFacing.INTERNAL,
    "landed_cost_document": StockMovementFacing.INTERNAL,
    "stock_transfer": StockMovementFacing.TRANSFER,
}

#: The source types this module reports by itself, from inside the stock service.
SELF_REPORTING_SOURCES = frozenset(FACING_BY_SOURCE)

#: What a partner document's moves are filed under. Named here rather than imported from the
#: subledger, which would be a cycle.
PARTNER_DOCUMENT_SOURCE = "partner_document"


@dataclass(frozen=True)
class _Group:
    """One movement: a branch, a direction, and the moves that make it up."""

    branch_id: int
    outgoing: bool
    moves: list[StockMove]


def facing_for(source_doc_type: str | None) -> StockMovementFacing | None:
    """The facing a posting reports under, or `None` when nothing here should report it."""
    if source_doc_type is None:
        # A posting with no source document is an adjustment as far as the authority is
        # concerned: something changed on the shelf and nobody is on the other side of it.
        return StockMovementFacing.INTERNAL
    return FACING_BY_SOURCE.get(source_doc_type)


def report_moves(
    db: Session,
    company_id: int,
    *,
    moves: Sequence[StockMove],
    facing: StockMovementFacing,
    occurred_on: date,
    description: str | None = None,
    source_doc_type: str | None = None,
    source_doc_id: int | None = None,
    counterpart_branch_id: int | None = None,
    actor: User,
) -> list[FiscalOutboxRow]:
    """Queue the movement and the on-hand snapshots for one stock posting.

    Returns the rows it queued, oldest first — empty for a company that does not fiscalize, for
    a posting that moved no quantity (a revaluation, a landed-cost share: there is no quantity
    and nothing to report), and for a movement inside one branch.

    Never commits. It runs in the transaction that wrote the moves, which is the whole point:
    a movement that reached the stock ledger has a queue row, and a queue row that exists had
    its moves committed.
    """
    if not device_service.is_fiscalized(db, company_id):
        return []
    groups = _group(db, company_id, moves, counterpart_branch_id=counterpart_branch_id)
    if not groups:
        return []
    company = db.get(Company, company_id)
    adapter = adapter_for(company.fiscal_country if company else None)

    queued: list[FiscalOutboxRow] = []
    for branch_id in sorted({group.branch_id for group in groups}):
        movements: list[FiscalOutboxRow] = []
        device = device_service.active_device_for_branch(db, company_id, branch_id)
        if device is None:
            # Nothing is fiscalizing at this branch, so there is nobody to report to. Not a
            # refusal: decision 2 refuses a *sale* on a branch with no device, because a
            # receipt is owed to a customer, and an adjustment in a depot owes nobody one.
            continue
        for group in [item for item in groups if item.branch_id == branch_id]:
            movements.extend(
                _report_group(
                    db,
                    company_id,
                    device=device,
                    adapter=adapter,
                    group=group,
                    facing=facing,
                    occurred_on=occurred_on,
                    description=description,
                    source_doc_type=source_doc_type,
                    source_doc_id=source_doc_id,
                    actor=actor,
                )
            )
        if not movements:
            # Nothing was reported for this branch, so there is nothing for a snapshot to
            # follow — and a stock master arriving with no movement in front of it is a figure
            # RRA cannot reconcile against anything.
            continue
        queued.extend(movements)
        queued.extend(
            _report_masters(
                db,
                company_id,
                device=device,
                adapter=adapter,
                branch_id=branch_id,
                item_ids=sorted(
                    {
                        move.item_id
                        for group in groups
                        if group.branch_id == branch_id
                        for move in group.moves
                    }
                ),
                source_doc_type=source_doc_type,
                source_doc_id=source_doc_id,
                actor=actor,
            )
        )
    return queued


def _group(
    db: Session,
    company_id: int,
    moves: Sequence[StockMove],
    *,
    counterpart_branch_id: int | None,
) -> list[_Group]:
    """The reportable moves, split by branch and direction.

    A zero-quantity move is dropped: a revaluation and a stockless landed-cost share move
    value and no quantity, and the authority's stock report is about quantity (decision 10 —
    "landed-cost revaluation and every zero-quantity move nothing").
    """
    grouped: dict[tuple[int, bool], list[StockMove]] = {}
    for move in moves:
        if move.quantity == ZERO:
            continue
        warehouse = db.get(Warehouse, move.warehouse_id)
        if warehouse is None or warehouse.is_in_transit:
            continue
        if counterpart_branch_id is not None and warehouse.branch_id == counterpart_branch_id:
            # Both ends of this movement are the same branch: the branch's position is
            # unchanged and the authority is told nothing (decision 10).
            continue
        grouped.setdefault((warehouse.branch_id, move.quantity < ZERO), []).append(move)
    return [
        _Group(branch_id=branch_id, outgoing=outgoing, moves=rows)
        # Incoming before outgoing on the same branch, so a posting that does both reports
        # what arrived before what left — the order the shelf saw them in is not recoverable
        # and a stable one is what makes the tape readable.
        for (branch_id, outgoing), rows in sorted(
            grouped.items(), key=lambda entry: (entry[0][0], entry[0][1])
        )
    ]


def _report_group(
    db: Session,
    company_id: int,
    *,
    device: FiscalDevice,
    adapter: FiscalizationAdapter,
    group: _Group,
    facing: StockMovementFacing,
    occurred_on: date,
    description: str | None,
    source_doc_type: str | None,
    source_doc_id: int | None,
    actor: User,
) -> list[FiscalOutboxRow]:
    lines: list[FiscalStockLine] = []
    for sequence, (item_id, quantity, value, unit_cost) in enumerate(
        _by_item(group.moves), start=1
    ):
        item = db.get(Item, item_id)
        if item is None:
            continue
        fiscal_item = item_service.ensure_registered_for_report(
            db, company_id, device=device, adapter=adapter, item=item, actor=actor
        )
        tax_code = item_service.registration_tax_code(db, company_id, item)
        lines.append(
            FiscalStockLine(
                item_code=fiscal_item.item_cd,
                item_class_code=fiscal_item.item_cls_cd,
                name=item.name,
                quantity=quantity,
                unit_cost=unit_cost,
                value=value,
                tax_class=fiscal_item.tax_ty_cd,
                tax_rate_pct=_rate_of(tax_code),
                package_unit=fiscal_item.pkg_unit_cd,
                quantity_unit=fiscal_item.qty_unit_cd,
                sequence=sequence,
                barcode=fiscal_item.bcd,
            )
        )
    if not lines:
        return []
    claimed = claim_number(db, company_id, DocType.FISCAL_STOCK, branch_id=device.branch_id)
    movement = FiscalStockIO(
        stock_no=claimed.sequence_no,
        facing=facing,
        outgoing=group.outgoing,
        occurred_on=occurred_on,
        lines=tuple(lines),
        actor_id=str(actor.id),
        actor_name=actor.email,
        remark=description,
    )
    return [
        outbox.enqueue(
            db,
            company_id,
            device=device,
            kind=FiscalOutboxKind.STOCK_IO,
            payload=adapter.render(device, FiscalOutboxKind.STOCK_IO, movement),
            source_doc_type=source_doc_type,
            source_doc_id=source_doc_id,
            sar_no=claimed.sequence_no,
        )
    ]


def _by_item(
    moves: Sequence[StockMove],
) -> list[tuple[int, Decimal, Decimal, Decimal]]:
    """`(item_id, quantity, value, unit_cost)` per item, as magnitudes.

    Aggregated per item rather than one line per move, because the authority holds one quantity
    per item and a movement listing the same item twice is a movement it has to add up itself.
    The unit cost is then the weighted one — the value that moved over the quantity that moved —
    which is the only figure that reproduces `splyAmt` from `prc × qty`.
    """
    totals: dict[int, tuple[Decimal, Decimal]] = {}
    for move in moves:
        quantity, value = totals.get(move.item_id, (ZERO, ZERO))
        totals[move.item_id] = (
            quantity + abs(move.quantity),
            value + abs(move.value or ZERO),
        )
    rows: list[tuple[int, Decimal, Decimal, Decimal]] = []
    for item_id in sorted(totals):
        quantity, value = totals[item_id]
        rows.append((item_id, quantity, value, value / quantity if quantity else ZERO))
    return rows


def _rate_of(tax_code: TaxCode | None) -> Decimal:
    return tax_code.rate_pct if tax_code is not None else ZERO


def _report_masters(
    db: Session,
    company_id: int,
    *,
    device: FiscalDevice,
    adapter: FiscalizationAdapter,
    branch_id: int,
    item_ids: Sequence[int],
    source_doc_type: str | None,
    source_doc_id: int | None,
    actor: User,
) -> list[FiscalOutboxRow]:
    """One `stock_master` row per item touched, carrying the branch's on-hand **now**."""
    queued: list[FiscalOutboxRow] = []
    for item_id in item_ids:
        fiscal_item = item_service.get_fiscal_item(db, company_id, item_id)
        if fiscal_item is None:
            continue
        master = FiscalStockMaster(
            item_code=fiscal_item.item_cd,
            quantity_on_hand=on_hand(db, company_id, item_id=item_id, branch_id=branch_id),
            actor_id=str(actor.id),
            actor_name=actor.email,
        )
        queued.append(
            outbox.enqueue(
                db,
                company_id,
                device=device,
                kind=FiscalOutboxKind.STOCK_MASTER,
                payload=adapter.render(device, FiscalOutboxKind.STOCK_MASTER, master),
                source_doc_type=source_doc_type,
                source_doc_id=source_doc_id,
            )
        )
    return queued


def on_hand(db: Session, company_id: int, *, item_id: int, branch_id: int) -> Decimal:
    """What the branch holds of one item, summed over its warehouses.

    **In-transit is excluded**, for the reason the module docstring gives: stock that has left
    one shop and not arrived at the next is on nobody's shelf, and the in-transit warehouse
    sits in whichever branch the seed put it in rather than in the branch that is short of it.
    """
    return (
        db.scalar(
            select(func.coalesce(func.sum(StockBalance.quantity), 0))
            .join(
                Warehouse,
                (Warehouse.company_id == StockBalance.company_id)
                & (Warehouse.id == StockBalance.warehouse_id),
            )
            .where(
                StockBalance.company_id == company_id,
                StockBalance.item_id == item_id,
                Warehouse.branch_id == branch_id,
                Warehouse.is_in_transit.is_(False),
            )
        )
        or ZERO
    )


def was_reported(db: Session, company_id: int, *, document_id: int) -> bool:
    """Did a movement of this document reach the queue and stay there?

    What the **mirror** of a reversal turns on. If the original movement was cancelled — which
    is what happens when RRA never received the document that caused it — then RRA has nothing
    to correct, and a mirror would be a receipt with no issue behind it.
    """
    return (
        db.scalar(
            select(FiscalOutboxRow.id).where(
                FiscalOutboxRow.company_id == company_id,
                FiscalOutboxRow.source_doc_type == PARTNER_DOCUMENT_SOURCE,
                FiscalOutboxRow.source_doc_id == document_id,
                FiscalOutboxRow.kind == FiscalOutboxKind.STOCK_IO,
                FiscalOutboxRow.status != FiscalOutboxStatus.CANCELLED,
            )
        )
        is not None
    )


def cancel_unsent_movements(
    db: Session, company_id: int, *, document_id: int, note: str
) -> list[FiscalOutboxRow]:
    """Cancel the movement rows of a document whose own row RRA never received.

    The FIFO is what makes this necessary. A document's movement sits *behind* its sale or its
    purchase (decision 10), so a sale still `queued` means its movement is queued too — and
    cancelling only the sale would leave the movement at the head of the queue, to be sent to
    an authority that has no document to attach it to. RRA answers `921`/`922` to exactly that.

    Only `queued` and `failed` rows, for the reason decision 7 cancels only those: any other
    state means RRA may already hold it.
    """
    rows = [
        row
        for row in db.scalars(
            select(FiscalOutboxRow)
            .where(
                FiscalOutboxRow.company_id == company_id,
                FiscalOutboxRow.source_doc_type == PARTNER_DOCUMENT_SOURCE,
                FiscalOutboxRow.source_doc_id == document_id,
                FiscalOutboxRow.kind.in_(
                    (FiscalOutboxKind.STOCK_IO, FiscalOutboxKind.STOCK_MASTER)
                ),
                FiscalOutboxRow.status.in_(
                    (FiscalOutboxStatus.QUEUED, FiscalOutboxStatus.FAILED)
                ),
            )
            .order_by(FiscalOutboxRow.sequence_no)
        )
    ]
    for row in rows:
        row.status = FiscalOutboxStatus.CANCELLED
        row.resolution_note = note
    if rows:
        db.flush()
    return rows


def report_partner_document(
    db: Session,
    company_id: int,
    *,
    document_id: int,
    facing: StockMovementFacing,
    occurred_on: date,
    description: str | None,
    actor: User,
    moves: Sequence[StockMove] | None = None,
) -> list[FiscalOutboxRow]:
    """The companion movement of a partner document, reported **after** its sale or purchase.

    Called by `post_document` and by `reverse_document` rather than from inside the stock
    service, and the ordering is the reason: VSDC §3.1 requires a movement to be preceded by
    the document that caused it, and the companion stock entry posts *first* (P6 decision 2 —
    a return to supplier needs the value the stock ledger moved before its partner side can be
    built). Reporting it from the stock service would put the movement ahead of its sale in the
    device's FIFO, which RRA answers `921`/`922` to.

    `moves` may be passed by a caller that already holds them; otherwise they are read back by
    source link, which is what the reversal path does.
    """
    rows = (
        list(moves)
        if moves is not None
        else list(
            db.scalars(
                select(StockMove)
                .where(
                    StockMove.company_id == company_id,
                    StockMove.source_doc_type == PARTNER_DOCUMENT_SOURCE,
                    StockMove.source_doc_id == document_id,
                )
                .order_by(StockMove.sequence_no)
            )
        )
    )
    if not rows:
        return []
    return report_moves(
        db,
        company_id,
        moves=rows,
        facing=facing,
        occurred_on=occurred_on,
        description=description,
        source_doc_type=PARTNER_DOCUMENT_SOURCE,
        source_doc_id=document_id,
        actor=actor,
    )

