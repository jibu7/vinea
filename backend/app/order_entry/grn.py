"""Goods receipts and the three-way match (P6 decision 6).

**Receiving is not being billed.** A GRN puts stock on the shelf and credits the GRN accrual:
we owe for these goods, we just do not have the invoice yet. The supplier invoice arrives
later, relieves the accrual for what it actually covers, and sends the difference to purchase
price variance. Between the two the accrual carries exactly the value of everything received
and not yet billed — the phase invariant, asserted by `assert_order_invariants`.

Nothing in this module keeps a running total. `received` is the sum of a GRN line's own
`base_quantity`; `matched` is the sum of the posted, unreversed supplier-invoice lines
carrying that line's id; `relieved` is the sum of what those lines actually took off the
accrual. Every one is a query, so a reversal changes all three by construction rather than by
a correction somebody has to remember to post.

The posting itself is P5's `receive_stock()`, unchanged, with the accrual as the contra —
decision 13's promise that P6 finds the primitives as it left them.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.inventory import masters as inventory_masters
from app.inventory import stock as stock_service
from app.kernel.errors import LedgerStateError
from app.kernel.money import base_currency, rate_on
from app.kernel.posting import gl_settings_for
from app.kernel.sequences import DocType
from app.models.currency import Currency
from app.models.inventory import GoodsReceivedNote, GoodsReceivedNoteLine, GrnStatus, ItemType
from app.models.partner import PartnerRole
from app.models.subledger import DocumentStatus, PartnerDocument, PartnerDocumentLine
from app.models.user import User
from app.services.audit import record_audit
from app.subledger import masters as partner_masters

ZERO = Decimal(0)


@dataclass(frozen=True)
class GrnLineInput:
    item_id: int
    quantity: Decimal
    #: Per **keyed** unit, in the GRN's currency — what the delivery note says. The service
    #: converts it to a per-base-unit cost the same way it converts the quantity, so a receipt
    #: keyed in cases stores a cost per bottle and the arithmetic never has to know about packs.
    unit_cost: Decimal
    uom_id: int | None = None
    warehouse_id: int | None = None
    description: str | None = None
    project_id: int | None = None
    #: Unconstrained until step 3; the remaining-quantity check arrives with it.
    purchase_order_line_id: int | None = None


@dataclass(frozen=True)
class GrnInput:
    partner_id: int
    grn_date: date
    description: str
    warehouse_id: int | None = None
    purchase_order_id: int | None = None
    supplier_reference: str | None = None
    currency_id: int | None = None
    branch_id: int | None = None
    lines: tuple[GrnLineInput, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class _ResolvedLine:
    source: GrnLineInput
    item: object
    uom_id: int
    warehouse: object
    base_quantity: Decimal
    #: Per base unit, in the GRN's currency — what the line stores.
    unit_cost: Decimal
    #: Per base unit, in base currency — what the move is valued at.
    base_unit_cost: Decimal


# --- Reads --------------------------------------------------------------------------------


def get_grn(db: Session, company_id: int, grn_id: int) -> GoodsReceivedNote:
    grn = db.scalar(
        select(GoodsReceivedNote).where(
            GoodsReceivedNote.company_id == company_id, GoodsReceivedNote.id == grn_id
        )
    )
    if grn is None:
        raise NotFoundError("Goods received note not found")
    return grn


def _matching_lines(company_id: int, grn_line_ids: Sequence[int]):  # noqa: ANN202
    """Posted, unreversed supplier-invoice lines against these GRN lines.

    "Unreversed" is the whole of the reversal story: a reversed document's lines are still
    there, and excluding them here is what makes `matched` fall the moment the invoice is
    reversed, with nothing to correct and nothing to remember.
    """
    return (
        select(PartnerDocumentLine)
        .join(PartnerDocument, PartnerDocument.id == PartnerDocumentLine.document_id)
        .where(
            PartnerDocumentLine.company_id == company_id,
            PartnerDocumentLine.grn_line_id.in_(grn_line_ids),
            PartnerDocument.status == DocumentStatus.POSTED,
        )
    )


def matched_quantities(
    db: Session, company_id: int, grn_line_ids: Sequence[int]
) -> dict[int, Decimal]:
    """GRN line id → base quantity matched by posted, unreversed invoice lines."""
    if not grn_line_ids:
        return {}
    rows = db.execute(
        _matching_lines(company_id, grn_line_ids).with_only_columns(
            PartnerDocumentLine.grn_line_id,
            func.coalesce(func.sum(PartnerDocumentLine.base_quantity), 0),
        ).group_by(PartnerDocumentLine.grn_line_id)
    ).all()
    return {int(line_id): Decimal(total) for line_id, total in rows}


def relieved_values(
    db: Session, company_id: int, grn_line_ids: Sequence[int]
) -> dict[int, Decimal]:
    """GRN line id → base-currency value already taken off the accrual.

    The sum of what the matching lines **actually posted**, not a share recomputed from
    today's quantities: see `PartnerDocumentLine.accrual_relieved` for why the difference
    matters after a reversal. Reversed documents drop out through the same status filter
    `matched_quantities` uses, so the accrual proof and the matched quantity always move
    together.
    """
    if not grn_line_ids:
        return {}
    rows = db.execute(
        _matching_lines(company_id, grn_line_ids)
        .with_only_columns(
            PartnerDocumentLine.grn_line_id,
            func.coalesce(func.sum(PartnerDocumentLine.accrual_relieved), 0),
        )
        .group_by(PartnerDocumentLine.grn_line_id)
    ).all()
    return {int(line_id): Decimal(total) for line_id, total in rows}


def derived_status(
    grn: GoodsReceivedNote, matched: dict[int, Decimal]
) -> GrnStatus:
    """What the GRN's status *should* be, from its lines and what has matched them.

    A stored workflow column checked against this, the `open_amount` pattern — the column is a
    convenience for filtering and the query is the truth.
    """
    if grn.status == GrnStatus.REVERSED:
        return GrnStatus.REVERSED
    received = sum((line.base_quantity for line in grn.lines), ZERO)
    done = sum((matched.get(line.id, ZERO) for line in grn.lines), ZERO)
    if done <= ZERO:
        return GrnStatus.RECEIVED
    if done >= received:
        return GrnStatus.MATCHED
    return GrnStatus.PARTIALLY_MATCHED


def refresh_status(db: Session, grn: GoodsReceivedNote) -> GrnStatus:
    """Recompute and store the workflow column. Called by whatever changed the match."""
    matched = matched_quantities(db, grn.company_id, [line.id for line in grn.lines])
    grn.status = derived_status(grn, matched)
    db.flush()
    return grn.status


def _accrual_account_id(db: Session, company_id: int) -> int:
    settings = gl_settings_for(db, company_id)
    if settings.grn_accrual_account_id is None:
        raise LedgerStateError(
            "No GRN accrual account is configured — set it on Order defaults",
            code="gl_setting_missing",
            field_errors={"grn_accrual_account_id": ["required"]},
        )
    return int(settings.grn_accrual_account_id)


# --- Posting ------------------------------------------------------------------------------


def _resolve_lines(
    db: Session,
    company_id: int,
    data: GrnInput,
    *,
    currency: Currency,
    rate: Decimal,
    default_warehouse_id: int | None,
) -> list[_ResolvedLine]:
    resolved: list[_ResolvedLine] = []
    for index, line in enumerate(data.lines):
        item = inventory_masters.get_item(db, company_id, line.item_id)
        if item.item_type == ItemType.KIT:
            # A kit is a virtual bundle exploded at order time; there is no such thing on a
            # shelf, so there is nothing to receive (decision 8).
            raise LedgerStateError(
                f"{item.code} is a kit and cannot be received",
                code="kit_not_purchasable",
                field_errors={f"lines.{index}.item_id": ["a kit cannot be received"]},
            )
        if not item.is_stock:
            # A service is "received" by its invoice, never by a GRN (decision 4).
            raise LedgerStateError(
                f"{item.code} is not a stock item",
                code="not_a_stock_item",
                field_errors={f"lines.{index}.item_id": ["only stock items are received"]},
            )
        uom_id = line.uom_id or item.base_uom_id
        uom = inventory_masters.get_uom(db, company_id, uom_id)
        base_quantity = inventory_masters.to_base_quantity(line.quantity, uom, item)
        if base_quantity <= ZERO:
            raise LedgerStateError(
                "A received quantity must be greater than zero",
                code="invalid_quantity",
                field_errors={f"lines.{index}.quantity": ["must be greater than zero"]},
            )
        if line.unit_cost < ZERO:
            raise LedgerStateError(
                "A unit cost cannot be negative",
                code="invalid_unit_cost",
                field_errors={f"lines.{index}.unit_cost": ["cannot be negative"]},
            )
        warehouse_id = line.warehouse_id or data.warehouse_id or default_warehouse_id
        if warehouse_id is None:
            raise LedgerStateError(
                "No warehouse on the line, the document or the defaults",
                code="warehouse_required",
                field_errors={f"lines.{index}.warehouse_id": ["required"]},
            )
        warehouse = inventory_masters.get_warehouse(db, company_id, warehouse_id)
        if warehouse.is_in_transit:
            raise LedgerStateError(
                "The in-transit warehouse is not selectable on a document",
                code="in_transit_warehouse_locked",
                field_errors={f"lines.{index}.warehouse_id": ["not selectable"]},
            )
        # The cost is keyed per entered unit and stored per base unit, converted through the
        # same quantities the UoM conversion produced rather than through the factor again —
        # so the two can never disagree about what a case holds.
        unit_cost = (line.unit_cost * line.quantity) / base_quantity
        resolved.append(
            _ResolvedLine(
                source=line,
                item=item,
                uom_id=uom.id,
                warehouse=warehouse,
                base_quantity=base_quantity,
                unit_cost=unit_cost,
                base_unit_cost=unit_cost * rate,
            )
        )
    return resolved


def post_grn(
    db: Session,
    company_id: int,
    data: GrnInput,
    *,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> tuple[GoodsReceivedNote, bool]:
    """Receive goods. Never commits.

    `receive_stock()` does the work, with the GRN accrual as the contra: stock rises, and the
    other leg says we owe for it. The GRN *is* the stock document, so the entry takes the
    GRN's number rather than the two being numbered separately — one number for the receipt
    and the entry it produced, which is what P4 and P5 both do.
    """
    if not data.lines:
        raise LedgerStateError(
            "A goods receipt needs at least one line",
            code="empty_document",
            field_errors={"lines": ["at least one line required"]},
        )
    if idempotency_key:
        existing = db.scalar(
            select(GoodsReceivedNote).where(
                GoodsReceivedNote.company_id == company_id,
                GoodsReceivedNote.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if idempotency_hash and existing.idempotency_hash != idempotency_hash:
                raise LedgerStateError(
                    "That Idempotency-Key was used for a different receipt",
                    code="idempotency_key_reused",
                )
            return existing, True

    partner = partner_masters.get_partner(db, company_id, data.partner_id)
    if not partner.has_role(PartnerRole.AP):
        raise LedgerStateError(
            f"{partner.name} is not a supplier",
            code="partner_role_missing",
            field_errors={"partner_id": ["wrong role"]},
        )
    settings = gl_settings_for(db, company_id)
    accrual_account_id = _accrual_account_id(db, company_id)

    currency = _resolve_currency(db, company_id, data.currency_id or partner.currency_id)
    rate = rate_on(db, currency, data.grn_date)
    resolved = _resolve_lines(
        db,
        company_id,
        data,
        currency=currency,
        rate=rate,
        default_warehouse_id=settings.default_warehouse_id,
    )
    branch_id = data.branch_id or resolved[0].warehouse.branch_id

    grn_id = _reserve_grn_id(db)
    document = stock_service.StockDocument(
        doc_type=str(DocType.GOODS_RECEIVED),
        move_date=data.grn_date,
        description=data.description,
        reference=data.supplier_reference,
        source_doc_type="goods_received_note",
        source_doc_id=grn_id,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    posting = stock_service.receive_stock(
        db,
        company_id,
        document=document,
        lines=[
            stock_service.StockLine(
                item_id=line.item.id,
                warehouse_id=line.warehouse.id,
                quantity=line.base_quantity,
                unit_cost=line.base_unit_cost,
                contra_account_id=accrual_account_id,
                project_id=line.source.project_id,
                description=line.source.description or data.description,
            )
            for line in resolved
        ],
        actor=actor,
    )

    number = (
        posting.entry.number
        if posting.entry is not None
        else _claim_valueless_number(db, company_id, branch_id)
    )
    grn = GoodsReceivedNote(
        id=grn_id,
        company_id=company_id,
        number=number,
        partner_id=partner.id,
        purchase_order_id=data.purchase_order_id,
        warehouse_id=resolved[0].warehouse.id,
        branch_id=branch_id,
        grn_date=data.grn_date,
        supplier_reference=data.supplier_reference,
        description=data.description,
        currency_id=currency.id,
        exchange_rate=rate,
        status=GrnStatus.RECEIVED,
        journal_entry_id=posting.entry.id if posting.entry is not None else None,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    db.add(grn)
    db.flush()
    db.add_all(
        [
            GoodsReceivedNoteLine(
                company_id=company_id,
                grn_id=grn.id,
                line_no=index,
                purchase_order_line_id=line.source.purchase_order_line_id,
                item_id=line.item.id,
                uom_id=line.uom_id,
                warehouse_id=line.warehouse.id,
                description=line.source.description,
                quantity=line.source.quantity,
                base_quantity=line.base_quantity,
                unit_cost=line.unit_cost,
                # The value the move was actually posted at, read back rather than computed a
                # second time — `keyed_moves` is the line→move map, never a position in
                # `moves`, which the service interleaves corrections into.
                value=abs(move.value),
                project_id=line.source.project_id,
                stock_move_id=move.id,
            )
            for index, (line, move) in enumerate(
                zip(resolved, posting.keyed_moves, strict=True), 1
            )
        ]
    )
    db.flush()
    record_audit(
        db,
        company_id=company_id,
        action="goods_received_note.posted",
        entity="goods_received_notes",
        entity_id=grn.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        after={
            "number": grn.number,
            "partner_id": partner.id,
            "grn_date": data.grn_date.isoformat(),
            "lines": len(resolved),
        },
        request=request,
    )
    return grn, False


def _resolve_currency(db: Session, company_id: int, currency_id: int | None) -> Currency:
    if currency_id is None:
        return base_currency(db, company_id)
    currency = db.scalar(
        select(Currency).where(Currency.company_id == company_id, Currency.id == currency_id)
    )
    if currency is None:
        raise NotFoundError("Currency not found")
    return currency


def _reserve_grn_id(db: Session) -> int:
    """The same cycle-breaker `inventory_documents` uses: the moves carry `source_doc_id` and
    cannot be updated afterwards, and the GRN's number comes from the entry those moves
    produce, so the id has to exist before either."""
    from sqlalchemy import text

    return int(
        db.execute(
            text("SELECT nextval(pg_get_serial_sequence('goods_received_notes', 'id'))")
        ).scalar_one()
    )


def _claim_valueless_number(db: Session, company_id: int, branch_id: int | None) -> str:
    """A receipt whose every line cost nothing posts no entry, so there is no number to
    inherit and this run's second claimant holds it instead (decision 6)."""
    from app.kernel.sequences import claim_number

    return claim_number(db, company_id, DocType.GOODS_RECEIVED, branch_id).number


# --- Reversal -----------------------------------------------------------------------------


def reverse_grn(
    db: Session,
    grn: GoodsReceivedNote,
    *,
    on_date: date,
    reason: str,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> GoodsReceivedNote:
    """Undo a receipt: the P5 stock reversal at the original values, and the accrual with it.

    **A matched GRN cannot be reversed** (`grn_matched`). The invoice that matched it has
    already relieved part of the accrual, and reversing the receipt underneath it would leave
    the accrual holding a relief for goods that were never received — the invariant broken by
    a correction. The invoice comes back first; then this does.
    """
    if grn.status == GrnStatus.REVERSED:
        raise LedgerStateError(
            f"{grn.number} was already reversed", code="grn_already_reversed"
        )
    matched = matched_quantities(db, grn.company_id, [line.id for line in grn.lines])
    if any(quantity > ZERO for quantity in matched.values()):
        raise LedgerStateError(
            f"{grn.number} has matched lines; reverse the supplier invoice first",
            code="grn_matched",
            field_errors={"grn_id": ["has matched lines"]},
        )

    if grn.journal_entry_id is not None:
        reversal = stock_service.reverse_stock_posting(
            db,
            grn.company_id,
            entry_id=grn.journal_entry_id,
            on_date=on_date,
            reason=reason,
            actor=actor,
            idempotency_key=idempotency_key,
            idempotency_hash=idempotency_hash,
        )
        grn.reversal_entry_id = reversal.entry.id if reversal.entry is not None else None
    grn.status = GrnStatus.REVERSED
    grn.reversed_on = on_date
    db.flush()
    record_audit(
        db,
        company_id=grn.company_id,
        action="goods_received_note.reversed",
        entity="goods_received_notes",
        entity_id=grn.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        after={"reason": reason, "on": on_date.isoformat()},
        request=request,
    )
    return grn
