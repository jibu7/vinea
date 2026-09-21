"""Fiscalization API (P7). Step 1 is setup — devices, the two syncs, the TIN lookup — step 2
adds the one endpoint the queue needs, `POST /fiscal/outbox/drain`, and step 3 adds the purchase
feed and the import register.

The screens that drive these arrive at **steps 6 and 7**: Maintenance → Tax → EBM devices and
Verify TIN on Customers / Suppliers at step 6, Transactions → Tax → EBM purchases and Import
declarations at step 7. Until then every mutating endpoint here carries a `GAP (P7, step N)`
line in `tests/test_api_has_a_caller.py`, naming the step that deletes it — which is the
register working as intended rather than eleven endpoints nobody can reach.

**Nothing here registers a purchase or reports stock**, and that is not an omission: a
declaration is written by the posting that caused it, in the same transaction, by a row in
`fiscal_outbox` (decision 4). What these endpoints do is the half a person decides — fetching
what RRA is holding, and saying yes or no to it.

The code and item-class *reads* are here now rather than in step 6 because the Item screen's
class typeahead and the Tax-types screen's EBM column read them, and a listing with no caller
is not what rule 14 is about: the rule names mutating endpoints.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_tenant_context
from app.api.idempotency import IdempotencyKey
from app.core import permissions
from app.core.errors import NotFoundError, PermissionDeniedError
from app.db import get_db
from app.fiscal import daily as daily_service
from app.fiscal import devices as device_service
from app.fiscal import drainer as drain_service
from app.fiscal import enquiries as enquiry_service
from app.fiscal import feed as feed_service
from app.fiscal import imports as import_service
from app.fiscal import printing as printing_service
from app.kernel.errors import LedgerStateError
from app.models.fiscalization import (
    FiscalCode,
    FiscalFeedDecision,
    FiscalImportStatus,
    FiscalItemClass,
    FiscalOutboxKind,
    FiscalOutboxRow,
    FiscalOutboxStatus,
    FiscalReceiptType,
    FiscalSyncKind,
)
from app.schemas.fiscal import (
    DailyFiguresRead,
    DailyReportRead,
    DeviceCreate,
    DeviceRead,
    DeviceSuspend,
    DeviceSyncResult,
    DocumentContextRead,
    DrainResult,
    FeedAccept,
    FeedDecisionRead,
    FeedRowRead,
    ImportApprove,
    ImportDeclarationRead,
    ImportReject,
    ItemRegistrationRead,
    ListingDocumentRead,
    ListingReceiptRead,
    QueueActionRead,
    QueueAttachReceipt,
    QueueDeviceRead,
    QueueHeadRead,
    QueueRowDetailRead,
    QueueRowRead,
    QueueStatusCountRead,
    ReceiptBlockRead,
    ReceiptClassLineRead,
    ReceiptLineRead,
    ReceiptListingRead,
    ReceiptRead,
    RefundReasonRead,
    TinLookupRead,
    device_read,
)

router = APIRouter(prefix="/fiscal", tags=["fiscal"])

#: Reading the fiscal setup is either a setup or a reporting job — a device list is on the
#: maintenance screen and on the queue dashboard, and requiring the setup permission for the
#: latter would mean a clerk watching a stuck queue needed the authority to reconfigure it.
_VIEW_PERMISSIONS = (permissions.FISCAL_SETUP_MANAGE, permissions.FISCAL_REPORTS_VIEW)


def _require_view(auth: AuthContext) -> None:
    if not any(permission in auth.permissions for permission in _VIEW_PERMISSIONS):
        raise PermissionDeniedError("Insufficient permissions")


@router.get("/devices")
def list_devices(
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[DeviceRead]:
    _require_view(auth)
    return [device_read(device) for device in device_service.list_devices(db, auth.company_id)]


@router.get("/devices/{device_id}")
def read_device(
    device_id: int,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> DeviceRead:
    _require_view(auth)
    return device_read(device_service.get_device(db, auth.company_id, device_id))


@router.post("/devices", status_code=status.HTTP_201_CREATED)
def register_device(
    payload: DeviceCreate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.FISCAL_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> DeviceRead:
    device = device_service.register_device(
        db,
        auth.company_id,
        branch_id=payload.branch_id,
        profile=payload.profile,
        environment=payload.environment,
        base_url=payload.base_url,
        dvc_srl_no=payload.dvc_srl_no,
        bhf_id=payload.bhf_id,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return device_read(device)


@router.post("/devices/{device_id}/initialize")
def initialize_device(
    device_id: int,
    request: Request,
    auth: AuthContext = permissions.require(permissions.FISCAL_SETUP_MANAGE),
    db: Session = Depends(get_db),
    idempotency_key: str = IdempotencyKey,
) -> DeviceRead:
    """Register the device with the authority, store what comes back, and activate it.

    `Idempotency-Key` is required because this call is not naturally idempotent from the
    caller's side: a double-click while the authority is slow would otherwise re-initialize a
    live device, and re-initialization is how a device's keys are reissued.
    """
    device = device_service.get_device(db, auth.company_id, device_id)
    device_service.initialize_device(
        db, auth.company_id, device, actor=auth.user, request=request
    )
    db.commit()
    return device_read(device)


@router.post("/devices/{device_id}/suspend")
def suspend_device(
    device_id: int,
    payload: DeviceSuspend,
    request: Request,
    auth: AuthContext = permissions.require(permissions.FISCAL_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> DeviceRead:
    device = device_service.get_device(db, auth.company_id, device_id)
    device_service.suspend(
        db, auth.company_id, device, reason=payload.reason, actor=auth.user, request=request
    )
    db.commit()
    return device_read(device)


@router.post("/devices/{device_id}/sync-codes")
def sync_codes(
    device_id: int,
    request: Request,
    auth: AuthContext = permissions.require(permissions.FISCAL_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> DeviceSyncResult:
    device = device_service.get_device(db, auth.company_id, device_id)
    rows = device_service.sync_codes(
        db, auth.company_id, device, actor=auth.user, request=request
    )
    db.commit()
    return DeviceSyncResult(
        device_id=device.id,
        kind=FiscalSyncKind.CODES,
        rows=rows,
        watermark=device.watermarks.get(FiscalSyncKind.CODES),
    )


@router.post("/devices/{device_id}/sync-item-classes")
def sync_item_classes(
    device_id: int,
    request: Request,
    auth: AuthContext = permissions.require(permissions.FISCAL_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> DeviceSyncResult:
    device = device_service.get_device(db, auth.company_id, device_id)
    rows = device_service.sync_item_classes(
        db, auth.company_id, device, actor=auth.user, request=request
    )
    db.commit()
    return DeviceSyncResult(
        device_id=device.id,
        kind=FiscalSyncKind.ITEM_CLASSES,
        rows=rows,
        watermark=device.watermarks.get(FiscalSyncKind.ITEM_CLASSES),
    )


@router.get("/devices/{device_id}/lookup-tin")
def lookup_tin(
    device_id: int,
    tin: str = Query(min_length=1, max_length=20),
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> TinLookupRead:
    """`Verify TIN`, from the Customers and Suppliers screens.

    A `GET` because it changes nothing: the authority's opinion of a TIN is an answer at a
    moment, not a fact about the partner, and nothing is written.
    """
    _require_view(auth)
    device = device_service.get_device(db, auth.company_id, device_id)
    lookup = device_service.lookup_tin(db, auth.company_id, device, tin)
    return TinLookupRead(
        tin=lookup.tin, found=lookup.found, name=lookup.name, status=lookup.status
    )


@router.get("/codes")
def list_codes(
    code_class: str | None = Query(default=None, max_length=10),
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[dict]:
    """The synced code tables. Filtered by class, because the Units-of-measure screen wants
    the quantity units and nothing else."""
    _require_view(auth)
    query = select(FiscalCode).where(FiscalCode.company_id == auth.company_id)
    if code_class:
        query = query.where(FiscalCode.code_class == code_class)
    rows = db.scalars(query.order_by(FiscalCode.code_class, FiscalCode.sort_order, FiscalCode.code))
    return [
        {
            "code_class": row.code_class,
            "code_class_name": row.code_class_name,
            "code": row.code,
            "name": row.name,
            "is_active": row.is_active,
        }
        for row in rows
    ]


@router.get("/item-classes")
def list_item_classes(
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[dict]:
    """The authority's classification, searched rather than listed.

    Tens of thousands of rows: a picker that loaded them all would be a picker nobody could
    use, so this is a typeahead by code or name with a bounded page.
    """
    _require_view(auth)
    query = select(FiscalItemClass).where(
        FiscalItemClass.company_id == auth.company_id, FiscalItemClass.is_active.is_(True)
    )
    if search:
        pattern = f"%{search}%"
        query = query.where(
            FiscalItemClass.item_cls_cd.ilike(pattern)
            | FiscalItemClass.item_cls_nm.ilike(pattern)
        )
    rows = db.scalars(query.order_by(FiscalItemClass.item_cls_cd).limit(limit))
    return [
        {
            "item_cls_cd": row.item_cls_cd,
            "item_cls_nm": row.item_cls_nm,
            "item_cls_lvl": row.item_cls_lvl,
            "tax_ty_cd": row.tax_ty_cd,
        }
        for row in rows
    ]


#: The authority's refund-reason table (§4.16). Named here rather than spelled `"32"` at the
#: query, for the reason every other code class is named: a bare table number in an endpoint is
#: a country's vocabulary leaking out of `app/fiscal/`.
REFUND_REASON_CODE_CLASS = "32"


@router.get("/document-context")
def document_context(
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> DocumentContextRead:
    """What an AR/AP posting screen needs to draw its fiscal fields.

    **No fiscal permission**, and that is the point of the endpoint existing at all. The
    Invoice and Credit-note screens gate on `ar:transactions_post`, which the seeded Sales
    Manager role holds without either fiscal permission; answering "is a purchase code
    required here" out of `/fiscal/devices` would mean a sales manager needed the authority to
    reconfigure the devices in order to key an invoice. Both answers are public facts about the
    company — whether it fiscalizes, and the thirteen reasons RRA publishes for a refund — and
    neither is a device, a key or a payload.
    """
    reasons = db.scalars(
        select(FiscalCode)
        .where(
            FiscalCode.company_id == auth.company_id,
            FiscalCode.code_class == REFUND_REASON_CODE_CLASS,
            FiscalCode.is_active.is_(True),
        )
        .order_by(FiscalCode.sort_order, FiscalCode.code)
    )
    return DocumentContextRead(
        fiscalized=device_service.is_fiscalized(db, auth.company_id),
        refund_reasons=[
            RefundReasonRead(code=row.code, name=row.name) for row in reasons
        ],
    )


@router.post("/outbox/drain")
def drain_outbox(
    auth: AuthContext = permissions.require(permissions.FISCAL_QUEUE_MANAGE),
    db: Session = Depends(get_db),
) -> DrainResult:
    """Drain every active device's queue for this company, now.

    The scheduler's hook, and `by design` in the rule-14 register for the same reason
    `jobs/sweep` is: the queue drains by itself — the worker loop every fifteen seconds and an
    after-response kick from the posting that filled it — so there is no moment at which a
    person wants to press this. What it exists for is a deployment with no worker process and
    an external scheduler, and for the e2e stack, which drives it instead of waiting.
    """
    outcomes = drain_service.drain_company(db, auth.company_id)
    db.commit()
    return DrainResult(
        rows=len(outcomes),
        sent=sum(1 for outcome in outcomes if outcome.status == "sent"),
        outcomes=[
            {
                "row_id": outcome.row_id,
                "kind": str(outcome.kind),
                "status": str(outcome.status),
                "code": outcome.code,
                "message": outcome.message,
            }
            for outcome in outcomes
        ],
    )


# --- The purchase feed and the import register (P7 step 3) ----------------------------------
#
# The screens arrive at **step 7** (Transactions → Tax → EBM purchases and Import
# declarations), so every mutating endpoint below carries a `GAP (P7, step 7)` line in
# `tests/test_api_has_a_caller.py` naming the step that deletes it.


@router.get("/purchase-feed")
def list_purchase_feed(
    device_id: int | None = Query(default=None),
    decision: FiscalFeedDecision | None = Query(default=None),
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[FeedRowRead]:
    _require_view(auth)
    return [
        FeedRowRead.model_validate(row)
        for row in feed_service.list_rows(
            db, auth.company_id, device_id=device_id, decision=decision
        )
    ]


@router.post("/devices/{device_id}/fetch-purchase-feed")
def fetch_purchase_feed(
    device_id: int,
    request: Request,
    auth: AuthContext = permissions.require(permissions.FISCAL_QUEUE_MANAGE),
    db: Session = Depends(get_db),
) -> DeviceSyncResult:
    device = device_service.get_device(db, auth.company_id, device_id)
    rows = feed_service.fetch(db, auth.company_id, device, actor=auth.user, request=request)
    db.commit()
    return DeviceSyncResult(
        device_id=device.id,
        kind=FiscalSyncKind.PURCHASES,
        rows=rows,
        watermark=device.watermarks.get(FiscalSyncKind.PURCHASES),
    )


@router.post("/purchase-feed/{row_id}/accept")
def accept_purchase_feed_row(
    row_id: int,
    payload: FeedAccept,
    request: Request,
    auth: AuthContext = permissions.require(permissions.FISCAL_QUEUE_MANAGE),
    db: Session = Depends(get_db),
    idempotency_key: str = IdempotencyKey,
) -> FeedDecisionRead:
    """Confirm a purchase RRA is holding, optionally linking the AP document it became.

    `Idempotency-Key` because the decision claims an `FIP` number and queues a row: a
    double-click would otherwise spend a number on a confirmation nobody asked for twice.
    """
    row = feed_service.get_row(db, auth.company_id, row_id)
    decided = feed_service.accept(
        db,
        auth.company_id,
        row,
        ap_document_id=payload.ap_document_id,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return _feed_decision(decided)


@router.post("/purchase-feed/{row_id}/reject")
def reject_purchase_feed_row(
    row_id: int,
    request: Request,
    auth: AuthContext = permissions.require(permissions.FISCAL_QUEUE_MANAGE),
    db: Session = Depends(get_db),
    idempotency_key: str = IdempotencyKey,
) -> FeedDecisionRead:
    row = feed_service.get_row(db, auth.company_id, row_id)
    decided = feed_service.reject(
        db, auth.company_id, row, actor=auth.user, request=request
    )
    db.commit()
    return _feed_decision(decided)


def _feed_decision(decided: feed_service.FeedDecision) -> FeedDecisionRead:
    return FeedDecisionRead(
        row=FeedRowRead.model_validate(decided.row),
        confirmation_row_id=(
            decided.confirmation.id if decided.confirmation is not None else None
        ),
        cancelled_row_id=decided.cancelled.id if decided.cancelled is not None else None,
        note=decided.note,
    )


@router.get("/import-declarations")
def list_import_declarations(
    device_id: int | None = Query(default=None),
    status_filter: FiscalImportStatus | None = Query(default=None, alias="status"),
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[ImportDeclarationRead]:
    _require_view(auth)
    return [
        ImportDeclarationRead.model_validate(row)
        for row in import_service.list_declarations(
            db, auth.company_id, device_id=device_id, status=status_filter
        )
    ]


@router.post("/devices/{device_id}/fetch-imports")
def fetch_imports(
    device_id: int,
    request: Request,
    auth: AuthContext = permissions.require(permissions.FISCAL_QUEUE_MANAGE),
    db: Session = Depends(get_db),
) -> DeviceSyncResult:
    device = device_service.get_device(db, auth.company_id, device_id)
    rows = import_service.fetch(
        db, auth.company_id, device, actor=auth.user, request=request
    )
    db.commit()
    return DeviceSyncResult(
        device_id=device.id,
        kind=FiscalSyncKind.IMPORTS,
        rows=rows,
        watermark=device.watermarks.get(FiscalSyncKind.IMPORTS),
    )


@router.post("/import-declarations/{declaration_id}/approve")
def approve_import_declaration(
    declaration_id: int,
    payload: ImportApprove,
    request: Request,
    auth: AuthContext = permissions.require(permissions.FISCAL_QUEUE_MANAGE),
    db: Session = Depends(get_db),
    idempotency_key: str = IdempotencyKey,
) -> ImportDeclarationRead:
    """Acknowledge a declared line, naming the Vinea item it became.

    It moves no stock and posts nothing (decision 9) — the goods reached the ledger through a
    goods receipt, and saying so twice would double them.
    """
    declaration = import_service.get_declaration(db, auth.company_id, declaration_id)
    import_service.approve(
        db,
        auth.company_id,
        declaration,
        item_id=payload.item_id,
        note=payload.note,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return ImportDeclarationRead.model_validate(declaration)


@router.post("/import-declarations/{declaration_id}/reject")
def reject_import_declaration(
    declaration_id: int,
    payload: ImportReject,
    request: Request,
    auth: AuthContext = permissions.require(permissions.FISCAL_QUEUE_MANAGE),
    db: Session = Depends(get_db),
    idempotency_key: str = IdempotencyKey,
) -> ImportDeclarationRead:
    declaration = import_service.get_declaration(db, auth.company_id, declaration_id)
    import_service.reject(
        db,
        auth.company_id,
        declaration,
        note=payload.note,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return ImportDeclarationRead.model_validate(declaration)


# --- X and Z (decision 11) ---------------------------------------------------------------
#
# The screen arrives at **step 8** — Transactions → Tax → Close day, showing the X and offering
# the close — so `close-day` carries a `GAP (P7, step 8)` line in
# `tests/test_api_has_a_caller.py`. The X and the listing are reads and need no exemption.


def _daily_read(view: daily_service.DailyReportView) -> DailyReportRead:
    return DailyReportRead(
        device_id=view.device_id,
        kind=view.kind,
        from_at=view.from_at,
        to_at=view.to_at,
        figures=DailyFiguresRead.model_validate(view.figures.as_dict()),
        number=view.number,
        report_no=view.report_no,
    )


@router.get("/devices/{device_id}/x-report")
def x_report(
    device_id: int,
    auth: AuthContext = permissions.require(permissions.FISCAL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> DailyReportRead:
    """The day so far. An X is a question — it stores nothing and changes nothing."""
    return _daily_read(daily_service.x_report(db, auth.company_id, device_id))


@router.get("/devices/{device_id}/z-reports")
def list_z_reports(
    device_id: int,
    auth: AuthContext = permissions.require(permissions.FISCAL_REPORTS_VIEW),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[DailyReportRead]:
    return [
        DailyReportRead(
            device_id=report.device_id,
            kind="Z",
            from_at=report.from_at,
            to_at=report.to_at,
            figures=DailyFiguresRead.model_validate(report.figures),
            number=report.number,
            report_no=report.report_no,
        )
        for report in daily_service.reports_of(db, auth.company_id, device_id, limit=limit)
    ]


@router.post("/devices/{device_id}/close-day", status_code=status.HTTP_201_CREATED)
def close_day(
    device_id: int,
    request: Request,
    auth: AuthContext = permissions.require(permissions.FISCAL_CLOSE_DAY),
    db: Session = Depends(get_db),
    idempotency_key: str = IdempotencyKey,
) -> DailyReportRead:
    """Take the Z: store the day, and open the next one where this one ended.

    `Idempotency-Key` because a close is an act that cannot be taken back — a second one would
    store an empty day and move the boundary, and the `FZR` run would carry a number for it.
    """
    report = daily_service.close_day(
        db,
        auth.company_id,
        device_id,
        actor=auth.user,
        idempotency_key=idempotency_key,
        request=request,
    )
    db.commit()
    return DailyReportRead(
        device_id=report.device_id,
        kind="Z",
        from_at=report.from_at,
        to_at=report.to_at,
        figures=DailyFiguresRead.model_validate(report.figures),
        number=report.number,
        report_no=report.report_no,
    )


# --- The enquiries and listings (P7 step 5) -------------------------------------------------
#
# The queue per device and per row, the receipts RRA signed, the item registrations, and the
# receipt a document prints. The screens arrive at **steps 7 and 8** — Transactions → Tax →
# Fiscal queue and the document detail's print at step 7, Enquiries → Tax at step 8 — so the
# four mutating endpoints below carry `GAP (P7, step 7)` lines in
# `tests/test_api_has_a_caller.py`. Everything else here is a read.


def _queue_device_read(view: enquiry_service.QueueDeviceView) -> QueueDeviceRead:
    return QueueDeviceRead(
        device_id=view.device_id,
        branch_id=view.branch_id,
        branch_code=view.branch_code,
        branch_name=view.branch_name,
        status=view.status,
        profile=view.profile,
        environment=view.environment,
        sdc_id=view.sdc_id,
        mrc_no=view.mrc_no,
        last_success_at=view.last_success_at,
        last_error=view.last_error,
        offline=view.offline,
        oldest_queued_at=view.oldest_queued_at,
        oldest_queued_age_seconds=view.oldest_queued_age_seconds,
        pending_rows=view.pending_rows,
        blocked=view.blocked,
        counts=[
            QueueStatusCountRead(status=count.status, rows=count.rows) for count in view.counts
        ],
        head=None if view.head is None else QueueHeadRead(**vars(view.head)),
    )


@router.get("/queue")
def list_queue(
    device_id: int | None = Query(default=None),
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[QueueDeviceRead]:
    """Every device and what its queue holds — the dashboard of decision 4."""
    _require_view(auth)
    return [
        _queue_device_read(view)
        for view in enquiry_service.queue_summary(db, auth.company_id, device_id=device_id)
    ]


@router.get("/queue/rows")
def list_queue_rows(
    device_id: int | None = Query(default=None),
    status_filter: FiscalOutboxStatus | None = Query(default=None, alias="status"),
    kind: FiscalOutboxKind | None = Query(default=None),
    document_id: int | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[QueueRowRead]:
    """The rows in queue order — which is send order. `document_id` is step 8's read-only
    "Fiscal queue history, per document"."""
    _require_view(auth)
    return [
        QueueRowRead(**vars(row))
        for row in enquiry_service.queue_rows(
            db,
            auth.company_id,
            device_id=device_id,
            status=status_filter,
            kind=kind,
            document_id=document_id,
            limit=limit,
        )
    ]


@router.get("/queue/rows/{row_id}")
def read_queue_row(
    row_id: int,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> QueueRowDetailRead:
    _require_view(auth)
    detail = enquiry_service.queue_row(db, auth.company_id, row_id)
    return QueueRowDetailRead(
        row=QueueRowRead(**vars(detail.row)),
        request=detail.request,
        response=detail.response,
        resolved_by_email=detail.resolved_by_email,
        resolution_note=detail.resolution_note,
        actions=[QueueActionRead(**vars(entry)) for entry in detail.actions],
    )


def _queue_row(db: Session, company_id: int, row_id: int) -> FiscalOutboxRow:
    row = db.scalar(
        select(FiscalOutboxRow).where(
            FiscalOutboxRow.company_id == company_id, FiscalOutboxRow.id == row_id
        )
    )
    if row is None:
        raise NotFoundError("Queue row not found")
    return row


def _action_error(error: drain_service.QueueActionError) -> LedgerStateError:
    """A queue action asked of a row it does not apply to is a state refusal, not a 500.

    The three actions are deliberately narrow — retry is not offered on `unknown` because RRA
    may already hold the sale — so being told "no" is an ordinary outcome of the screen and has
    to read as one.
    """
    return LedgerStateError(str(error), code="fiscal_queue_action_refused")


@router.post("/queue/rows/{row_id}/retry")
def retry_queue_row(
    row_id: int,
    auth: AuthContext = permissions.require(permissions.FISCAL_QUEUE_MANAGE),
    db: Session = Depends(get_db),
) -> QueueRowRead:
    """Put a `failed` or backing-off row at the front of its queue, due now.

    Never offered for `unknown` or `needs_receipt`: those mean RRA may already be holding the
    sale, and a retry on them is precisely the duplicate the policy exists to prevent.
    """
    row = _queue_row(db, auth.company_id, row_id)
    try:
        drain_service.retry_now(db, auth.company_id, row, actor=auth.user)
    except drain_service.QueueActionError as error:
        raise _action_error(error) from error
    db.commit()
    return QueueRowRead(**vars(enquiry_service.queue_row(db, auth.company_id, row_id).row))


@router.post("/queue/rows/{row_id}/verify")
def verify_queue_row(
    row_id: int,
    auth: AuthContext = permissions.require(permissions.FISCAL_QUEUE_MANAGE),
    db: Session = Depends(get_db),
) -> QueueRowRead:
    """Ask the device what it holds, and let its counters decide.

    An `unknown` row is resolved by asking, never by resending: re-initialization returns
    `lastSaleInvcNo`, and a counter at or above this row's number means RRA has the sale and a
    person must attach the receipt.
    """
    row = _queue_row(db, auth.company_id, row_id)
    device = device_service.get_device(db, auth.company_id, row.device_id)
    try:
        drain_service.verify_with_device(db, auth.company_id, device, row, actor=auth.user)
    except drain_service.QueueActionError as error:
        raise _action_error(error) from error
    db.commit()
    return QueueRowRead(**vars(enquiry_service.queue_row(db, auth.company_id, row_id).row))


@router.post("/queue/rows/{row_id}/attach-receipt", status_code=status.HTTP_201_CREATED)
def attach_queue_receipt(
    row_id: int,
    payload: QueueAttachReceipt,
    auth: AuthContext = permissions.require(permissions.FISCAL_QUEUE_MANAGE),
    db: Session = Depends(get_db),
) -> ReceiptRead:
    """A receipt read off the authority's portal, attached by a person who says so.

    Normalised through the adapter rather than parsed here, and audited with the note, because
    this is a human assertion about what a revenue authority is holding.
    """
    row = _queue_row(db, auth.company_id, row_id)
    device = device_service.get_device(db, auth.company_id, row.device_id)
    try:
        receipt = drain_service.attach_receipt(
            db,
            auth.company_id,
            device,
            row,
            fields=payload.fields,
            note=payload.note,
            actor=auth.user,
        )
    except drain_service.QueueActionError as error:
        raise _action_error(error) from error
    db.commit()
    return ReceiptRead(
        **vars(enquiry_service.receipt_detail(db, auth.company_id, receipt.id))
    )


@router.get("/receipts")
def list_receipts(
    device_id: int | None = Query(default=None),
    receipt_type: FiscalReceiptType | None = Query(default=None),
    document_id: int | None = Query(default=None),
    partner_id: int | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=200, ge=1, le=1000),
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[ReceiptRead]:
    """The receipts RRA signed, searched the way somebody holding one searches.

    One box over the printed counter, the document number, the partner and the authority's
    invoice number — the four things a person has in front of them — rather than four fields
    nobody knows which to use.
    """
    _require_view(auth)
    return [
        ReceiptRead(**vars(view))
        for view in enquiry_service.receipts(
            db,
            auth.company_id,
            device_id=device_id,
            receipt_type=receipt_type,
            document_id=document_id,
            partner_id=partner_id,
            date_from=date_from,
            date_to=date_to,
            search=search,
            limit=limit,
        )
    ]


@router.get("/receipts/listing")
def receipt_listing(
    device_id: int = Query(...),
    date_from: date = Query(...),
    date_to: date = Query(...),
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> ReceiptListingRead:
    """The **tie**: what this device declared over a range, beside what the sales ledger holds.

    Declared above `/receipts/{receipt_id}` deliberately — FastAPI matches in declaration
    order, and `listing` would otherwise be handed to the path below as a receipt id and fail
    validation.
    """
    _require_view(auth)
    view = enquiry_service.receipt_listing(
        db, auth.company_id, device_id, date_from=date_from, date_to=date_to
    )
    return ReceiptListingRead(
        device_id=view.device_id,
        device_label=view.device_label,
        sdc_id=view.sdc_id,
        mrc_no=view.mrc_no,
        date_from=view.date_from,
        date_to=view.date_to,
        ns_count=view.ns_count,
        ns_gross=view.ns_gross,
        ns_tax=view.ns_tax,
        nr_count=view.nr_count,
        nr_gross=view.nr_gross,
        nr_tax=view.nr_tax,
        declared_net=view.declared_net,
        ledger_invoice_count=view.ledger_invoice_count,
        ledger_invoice_total=view.ledger_invoice_total,
        ledger_credit_note_count=view.ledger_credit_note_count,
        ledger_credit_note_total=view.ledger_credit_note_total,
        ledger_net=view.ledger_net,
        difference=view.difference,
        receipts=[ListingReceiptRead(**vars(row)) for row in view.receipts],
        only_in_ledger=[ListingDocumentRead(**vars(row)) for row in view.only_in_ledger],
        only_on_receipts=[ListingReceiptRead(**vars(row)) for row in view.only_on_receipts],
    )


@router.get("/receipts/{receipt_id}")
def read_receipt(
    receipt_id: int,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> ReceiptRead:
    _require_view(auth)
    return ReceiptRead(**vars(enquiry_service.receipt_detail(db, auth.company_id, receipt_id)))


@router.get("/items")
def list_item_registrations(
    registered: bool | None = Query(default=None),
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=500, ge=1, le=2000),
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[ItemRegistrationRead]:
    """Every item with its registration beside it — including the ones RRA has never heard of,
    which are the ones that would refuse a fiscalized sale."""
    _require_view(auth)
    return [
        ItemRegistrationRead(**vars(view))
        for view in enquiry_service.item_registrations(
            db, auth.company_id, registered=registered, search=search, limit=limit
        )
    ]


def _block_read(block: printing_service.ReceiptBlock) -> ReceiptBlockRead:
    fields = vars(block) | {
        "classes": [ReceiptClassLineRead(**vars(line)) for line in block.classes],
        "lines": [ReceiptLineRead(**vars(line)) for line in block.lines],
    }
    return ReceiptBlockRead(**fields)


@router.get("/documents/{document_id}/receipt")
def read_document_receipt(
    document_id: int,
    receipt_id: int | None = Query(default=None),
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> ReceiptBlockRead | None:
    """What this document prints, `null` when it is not a fiscal receipt at all, and
    `fiscal_receipt_pending` when the authority has not signed it yet (CIS §10).

    `receipt_id` names **which** of the document's receipts to print. A sale that was reversed
    holds two — the `NS` it was declared under and the `NR` that reversed it (decision 7) — and
    both are legal documents the customer is owed. Left out, the latest is printed, which is
    the refund when there is one.
    """
    _require_view(auth)
    block = printing_service.receipt_block(
        db, auth.company_id, document_id, receipt_id=receipt_id
    )
    return None if block is None else _block_read(block)


@router.post("/documents/{document_id}/receipt/copy")
def print_document_receipt_copy(
    document_id: int,
    request: Request,
    receipt_id: int | None = Query(default=None),
    auth: AuthContext = permissions.require(permissions.FISCAL_REPORTS_VIEW),
    db: Session = Depends(get_db),
) -> ReceiptBlockRead:
    """A reprint: `COPY` under the header, the counter incremented and audited, and **nothing
    sent to RRA** — a copy is a print of a sale already declared (§11, §15).

    The counter is per **receipt**, so copying the refund does not make the sale look reprinted
    and the Z's copy count stays the count of pieces of paper.
    """
    block = printing_service.record_copy(
        db,
        auth.company_id,
        document_id,
        actor=auth.user,
        request=request,
        receipt_id=receipt_id,
    )
    db.commit()
    return _block_read(block)
