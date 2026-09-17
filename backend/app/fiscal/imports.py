"""Import declarations (decision 9) — customs lines the authority is holding.

**Approving one moves no stock and posts nothing.** The goods reached the ledger through a
goods receipt; saying so a second time would double them. What approval does is tell the
authority which Vinea item the declared line became, so that its own import register and its
own stock figures agree about the same thing. It is a compliance acknowledgment, and the only
row it writes in Vinea is the decision on the declaration itself.

That is why there is no ledger entry here, no `StockPosting`, and no link to a goods receipt:
the declaration and the receipt are two records of one arrival, and the one that moves stock is
the receipt. The item link is the whole content of the acknowledgment.

Like the purchase feed, the fetch is watermarked and the watermark is stored only after a
success — and, unlike the feed, RRA is strict about it: certification checkpoint 67 requires
each `lastReqDt` to be greater than the previous request's, which is why `record_watermark`
refuses one that went backwards.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import NotFoundError
from app.fiscal import items as item_service
from app.fiscal import outbox
from app.fiscal.devices import (
    FiscalSetupError,
    FiscalUpstreamError,
    call_sync,
    record_watermark,
)
from app.fiscal.mapping import FiscalImportDecision, FiscalImportEntry
from app.fiscal.registry import adapter_for
from app.models.company import Company
from app.models.fiscalization import (
    FiscalDevice,
    FiscalImportDeclaration,
    FiscalImportStatus,
    FiscalOutboxKind,
    FiscalOutboxRow,
    FiscalSyncKind,
)
from app.models.inventory import Item
from app.models.user import User
from app.services.audit import record_audit

#: What an import decision's queue row points at.
IMPORT_SOURCE = "fiscal_import_declarations"


# --- Queries --------------------------------------------------------------------------------


def list_declarations(
    db: Session,
    company_id: int,
    *,
    device_id: int | None = None,
    status: FiscalImportStatus | None = None,
) -> Sequence[FiscalImportDeclaration]:
    query = select(FiscalImportDeclaration).where(
        FiscalImportDeclaration.company_id == company_id
    )
    if device_id is not None:
        query = query.where(FiscalImportDeclaration.device_id == device_id)
    if status is not None:
        query = query.where(FiscalImportDeclaration.status == status)
    return list(
        db.scalars(
            query.order_by(
                FiscalImportDeclaration.dcl_de.desc(),
                FiscalImportDeclaration.dcl_no,
                FiscalImportDeclaration.item_seq,
            )
        )
    )


def get_declaration(
    db: Session, company_id: int, declaration_id: int
) -> FiscalImportDeclaration:
    row = db.scalar(
        select(FiscalImportDeclaration).where(
            FiscalImportDeclaration.company_id == company_id,
            FiscalImportDeclaration.id == declaration_id,
        )
    )
    if row is None:
        raise NotFoundError(
            "Import declaration not found", code="fiscal_declaration_not_found"
        )
    return row


# --- Fetching -------------------------------------------------------------------------------


def fetch(
    db: Session,
    company_id: int,
    device: FiscalDevice,
    *,
    actor: User,
    request: Request | None = None,
    client: httpx.Client | None = None,
) -> int:
    """Pull the import register by watermark and upsert what came back."""
    company = db.get(Company, company_id)
    adapter = adapter_for(company.fiscal_country if company else None, client=client)
    since = device.watermarks.get(FiscalSyncKind.IMPORTS)
    result, synced = call_sync(adapter.fetch_imports, device, since=since)
    if not result.ok:
        device.last_error = f"{result.code}: {result.message}"
        raise FiscalUpstreamError(
            f"The import register was refused ({result.code}): {result.message}",
            code="fiscal_sync_refused",
        )
    count = _store(db, company_id, device, synced.imports)
    record_watermark(device, FiscalSyncKind.IMPORTS, synced.watermark)
    record_audit(
        db,
        company_id=company_id,
        action="fiscal_imports.fetch",
        entity="fiscal_device",
        entity_id=device.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        after={"rows": count, "watermark": synced.watermark},
        request=request,
    )
    return count


def _store(
    db: Session,
    company_id: int,
    device: FiscalDevice,
    entries: Sequence[FiscalImportEntry],
) -> int:
    """Upsert by (device, task, declaration, line) — the authority's own key for the line.

    A line an operator has already decided is left alone, for the reason the feed's is: the
    figures are customs' and have not changed, and re-fetching should not un-answer a question
    somebody answered.
    """
    if not entries:
        return 0
    existing = {
        (row.task_cd, row.dcl_no, row.item_seq): row
        for row in db.scalars(
            select(FiscalImportDeclaration).where(
                FiscalImportDeclaration.company_id == company_id,
                FiscalImportDeclaration.device_id == device.id,
            )
        )
    }
    now = datetime.now(UTC)
    for entry in entries:
        key = (entry.task_code, entry.declaration_no, entry.line_no)
        row = existing.get(key)
        if row is None:
            row = FiscalImportDeclaration(
                company_id=company_id,
                device_id=device.id,
                task_cd=entry.task_code,
                dcl_no=entry.declaration_no,
                item_seq=entry.line_no,
                status=FiscalImportStatus.PENDING,
            )
            db.add(row)
        elif row.status != FiscalImportStatus.PENDING:
            continue
        row.dcl_de = entry.declared_on
        row.hs_cd = entry.hs_code
        row.item_nm = entry.name
        row.orgn_nat_cd = entry.origin_country
        row.pkg = entry.packages
        row.pkg_unit_cd = entry.package_unit
        row.qty = entry.quantity
        row.qty_unit_cd = entry.quantity_unit
        row.spplr_nm = entry.supplier_name
        row.agnt_nm = entry.agent_name
        row.invc_fcur_amt = entry.foreign_amount
        row.invc_fcur_cd = entry.foreign_currency
        row.invc_fcur_exc_rt = entry.foreign_rate
        row.payload = entry.source
        row.fetched_at = now
    db.flush()
    return len(entries)


# --- Deciding -------------------------------------------------------------------------------


def approve(
    db: Session,
    company_id: int,
    declaration: FiscalImportDeclaration,
    *,
    item_id: int,
    note: str | None = None,
    actor: User,
    request: Request | None = None,
) -> FiscalOutboxRow:
    """Acknowledge a declared line, naming the Vinea item it became (`imptItemSttsCd 3`).

    The item is **required**, which is the whole content of the acknowledgment: RRA's question
    is "what did this become in your catalogue", and an approval with no answer to it tells
    them nothing. The item must be registered, because what goes on the wire is its `itemCd` —
    so an unregistered item is registered here, ahead of the acknowledgment in the device's
    FIFO, exactly as a sale registers the item it names.
    """
    _assert_pending(declaration)
    item = db.scalar(
        select(Item).where(Item.company_id == company_id, Item.id == item_id)
    )
    if item is None:
        raise NotFoundError("Item not found", code="item_not_found")
    company = db.get(Company, company_id)
    adapter = adapter_for(company.fiscal_country if company else None)
    device = _device(db, company_id, declaration)
    fiscal_item = item_service.ensure_registered_for_report(
        db, company_id, device=device, adapter=adapter, item=item, actor=actor
    )
    row = _queue(
        db,
        company_id,
        declaration,
        device=device,
        adapter=adapter,
        decision=FiscalImportDecision(
            source=declaration.payload,
            approved=True,
            item_code=fiscal_item.item_cd,
            item_class_code=fiscal_item.item_cls_cd,
            note=note,
            actor_id=str(actor.id),
            actor_name=actor.email,
        ),
    )
    declaration.status = FiscalImportStatus.APPROVED
    declaration.item_id = item.id
    _record(db, company_id, declaration, row=row, actor=actor, request=request, note=note)
    return row


def reject(
    db: Session,
    company_id: int,
    declaration: FiscalImportDeclaration,
    *,
    note: str | None = None,
    actor: User,
    request: Request | None = None,
) -> FiscalOutboxRow:
    """Decline a declared line (`imptItemSttsCd 4`, published as *Cancelled*).

    No item: declining says the goods on this line are not this taxpayer's, and naming a
    catalogue row for them would contradict that.
    """
    _assert_pending(declaration)
    company = db.get(Company, company_id)
    adapter = adapter_for(company.fiscal_country if company else None)
    device = _device(db, company_id, declaration)
    row = _queue(
        db,
        company_id,
        declaration,
        device=device,
        adapter=adapter,
        decision=FiscalImportDecision(
            source=declaration.payload,
            approved=False,
            note=note,
            actor_id=str(actor.id),
            actor_name=actor.email,
        ),
    )
    declaration.status = FiscalImportStatus.REJECTED
    _record(db, company_id, declaration, row=row, actor=actor, request=request, note=note)
    return row


def _assert_pending(declaration: FiscalImportDeclaration) -> None:
    if declaration.status != FiscalImportStatus.PENDING:
        raise FiscalSetupError(
            f"This declaration line was already {declaration.status}.",
            code="fiscal_declaration_already_decided",
        )


def _device(
    db: Session, company_id: int, declaration: FiscalImportDeclaration
) -> FiscalDevice:
    device = db.scalar(
        select(FiscalDevice).where(
            FiscalDevice.company_id == company_id,
            FiscalDevice.id == declaration.device_id,
        )
    )
    if device is None:
        raise NotFoundError("Device not found", code="fiscal_device_not_found")
    return device


def _queue(
    db: Session,
    company_id: int,
    declaration: FiscalImportDeclaration,
    *,
    device: FiscalDevice,
    adapter,  # noqa: ANN001 - a FiscalizationAdapter; annotating it is an import cycle
    decision: FiscalImportDecision,
) -> FiscalOutboxRow:
    return outbox.enqueue(
        db,
        company_id,
        device=device,
        kind=FiscalOutboxKind.IMPORT_UPDATE,
        payload=adapter.render(device, FiscalOutboxKind.IMPORT_UPDATE, decision),
        source_doc_type=IMPORT_SOURCE,
        source_doc_id=declaration.id,
    )


def _record(
    db: Session,
    company_id: int,
    declaration: FiscalImportDeclaration,
    *,
    row: FiscalOutboxRow,
    actor: User,
    request: Request | None,
    note: str | None,
) -> None:
    declaration.decided_by = actor.id
    declaration.decided_at = datetime.now(UTC)
    db.flush()
    record_audit(
        db,
        company_id=company_id,
        action=f"fiscal_import.{declaration.status}",
        entity="fiscal_import_declarations",
        entity_id=declaration.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        after={
            "status": str(declaration.status),
            "item_id": declaration.item_id,
            "outbox_row_id": row.id,
            "note": note,
        },
        request=request,
    )
