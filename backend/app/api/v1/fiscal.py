"""Fiscalization API (P7). Step 1 is setup — devices, the two syncs, the TIN lookup — and
step 2 adds the one endpoint the queue needs, `POST /fiscal/outbox/drain`.

The screens that drive these arrive at **step 6** (Maintenance → Tax → EBM devices, and Verify
TIN on Customers / Suppliers). Until then every mutating endpoint here carries a
`GAP (P7, step 6)` line in `tests/test_api_has_a_caller.py`, naming the step that deletes it —
which is the register working as intended rather than four endpoints nobody can reach.

The code and item-class *reads* are here now rather than in step 6 because the Item screen's
class typeahead and the Tax-types screen's EBM column read them, and a listing with no caller
is not what rule 14 is about: the rule names mutating endpoints.
"""

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_tenant_context
from app.api.idempotency import IdempotencyKey
from app.core import permissions
from app.core.errors import PermissionDeniedError
from app.db import get_db
from app.fiscal import devices as device_service
from app.fiscal import drainer as drain_service
from app.models.fiscalization import FiscalCode, FiscalItemClass, FiscalSyncKind
from app.schemas.fiscal import (
    DeviceCreate,
    DeviceRead,
    DeviceSuspend,
    DeviceSyncResult,
    DrainResult,
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
