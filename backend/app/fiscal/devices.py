"""Device lifecycle, code sync and TIN lookup — the service layer of P7 step 1.

A device goes `pending` → `active` → (`suspended`). Only `active` fiscalizes, and only a
company holding an active device is *fiscalized* at all, which is what every refusal in step 2
keys off.

Three rules are enforced here rather than left to the screen:

**The TIN must match.** A device is registered with the authority against a taxpayer number,
and initializing it under a company whose TIN has since been edited would start sending sales
under a number the device was never registered with. `tin_mismatch`, at initialization.

**Activation locks the negative-stock policy at `block`.** CIS §7.30: no receipt for goods the
stock does not hold. P5 already refuses the sale under `block`; what this adds is that the
policy cannot be switched to `allow` while a device is active (`fiscal_requires_block`), so
the refusal cannot be turned off from a settings screen by somebody who does not know what it
is for.

**Activation creates the branch-level number runs.** `FIS`, `FIP`, `FSAR` and `FZR` are
per-device number spaces, and `claim_number` falls back to the company-wide row when no branch
row exists — so the rows have to exist *before* the first sale, and the moment the branch is
known is activation.

Watermarks are written **only after a success**, and in the same call that stores the rows. A
failed sync that advanced the watermark would skip every row the authority published between
the two calls, silently and for good.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import AppError, ConflictError, NotFoundError
from app.fiscal.keys import decrypt_key, encrypt_key
from app.fiscal.mapping import FiscalCodeEntry, FiscalItemClassEntry
from app.fiscal.protocol import FiscalTransportError, TinLookup
from app.fiscal.registry import adapter_for
from app.kernel.sequences import DocType, ensure_sequence
from app.models.company import Branch, Company
from app.models.fiscalization import (
    FiscalCode,
    FiscalDevice,
    FiscalDeviceStatus,
    FiscalEnvironment,
    FiscalItemClass,
    FiscalProfile,
    FiscalSyncKind,
)
from app.models.gl import GLSettings
from app.models.inventory import NegativeStockPolicy
from app.models.user import User
from app.services.audit import record_audit

#: The authority's "no such taxpayer" code. Named because it is an answer, not a failure.
UNKNOWN_TIN_CODE = "884"

#: The runs a device owns. Created at activation, because that is when the branch is known and
#: because `claim_number` silently falls back to the company-wide row if they are missing —
#: which would put two branches' receipts in one number space.
DEVICE_SEQUENCES: tuple[str, ...] = (
    DocType.FISCAL_SALE,
    DocType.FISCAL_PURCHASE,
    DocType.FISCAL_STOCK,
    DocType.FISCAL_Z_REPORT,
)


class FiscalSetupError(AppError):
    """A device cannot be set up as asked. 422 rather than 400: the request was well formed
    and the *state* refuses it, which is the same shape as a posting refusal."""

    status_code = 422
    code = "fiscal_setup_rejected"


class FiscalUpstreamError(AppError):
    """The authority could not be reached, or refused. Its own code and message are carried
    through verbatim — they are what an operator quotes when they ring the authority."""

    status_code = 502
    code = "fiscal_upstream_error"


# --- Queries ------------------------------------------------------------------------------


def list_devices(db: Session, company_id: int) -> Sequence[FiscalDevice]:
    return list(
        db.scalars(
            select(FiscalDevice)
            .where(FiscalDevice.company_id == company_id)
            .order_by(FiscalDevice.branch_id)
        )
    )


def get_device(db: Session, company_id: int, device_id: int) -> FiscalDevice:
    device = db.scalar(
        select(FiscalDevice).where(
            FiscalDevice.company_id == company_id, FiscalDevice.id == device_id
        )
    )
    if device is None:
        raise NotFoundError("Device not found", code="fiscal_device_not_found")
    return device


def device_for_branch(db: Session, company_id: int, branch_id: int) -> FiscalDevice | None:
    return db.scalar(
        select(FiscalDevice).where(
            FiscalDevice.company_id == company_id, FiscalDevice.branch_id == branch_id
        )
    )


def active_device_for_branch(
    db: Session, company_id: int, branch_id: int
) -> FiscalDevice | None:
    device = device_for_branch(db, company_id, branch_id)
    return device if device is not None and device.is_active else None


def is_fiscalized(db: Session, company_id: int) -> bool:
    """A company is fiscalized when it holds at least one **active** device.

    Not "when its country is RW" and not a settings flag: a company that has not finished
    setting a device up is not sending anything to a revenue authority, and pretending it is
    would refuse every invoice it tries to post.
    """
    return (
        db.scalar(
            select(FiscalDevice.id).where(
                FiscalDevice.company_id == company_id,
                FiscalDevice.status == FiscalDeviceStatus.ACTIVE,
            )
        )
        is not None
    )


# --- Registration and lifecycle -----------------------------------------------------------


def register_device(
    db: Session,
    company_id: int,
    *,
    branch_id: int,
    profile: FiscalProfile,
    environment: FiscalEnvironment,
    base_url: str,
    dvc_srl_no: str,
    bhf_id: str,
    actor: User,
    request: Request | None = None,
) -> FiscalDevice:
    """Register a device against a branch. It arrives `pending`: nothing has been said to the
    authority yet, and nothing will be until it is initialized."""
    branch = db.scalar(
        select(Branch).where(Branch.company_id == company_id, Branch.id == branch_id)
    )
    if branch is None:
        raise NotFoundError("Branch not found", code="branch_not_found")
    if device_for_branch(db, company_id, branch_id) is not None:
        raise ConflictError(
            "This branch already has a device. One device per branch: two would each hold "
            "their own receipt counters for the same shop, and the authority reconciles "
            "against those counters.",
            code="fiscal_device_exists",
            field_errors={"branch_id": ["This branch already has a device"]},
        )
    if len(bhf_id) != 2:
        raise FiscalSetupError(
            "The branch id must be exactly two characters (the head office is 00)",
            code="fiscal_bhf_id_invalid",
            field_errors={"bhf_id": ["Two characters, e.g. 00"]},
        )

    device = FiscalDevice(
        company_id=company_id,
        branch_id=branch_id,
        profile=profile,
        environment=environment,
        base_url=base_url.rstrip("/"),
        bhf_id=bhf_id,
        dvc_srl_no=dvc_srl_no,
        status=FiscalDeviceStatus.PENDING,
        watermarks={},
    )
    db.add(device)
    db.flush()
    record_audit(
        db,
        company_id=company_id,
        action="fiscal_device.register",
        entity="fiscal_device",
        entity_id=device.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        after={
            "branch_id": branch_id,
            "profile": str(profile),
            "environment": str(environment),
            "dvc_srl_no": dvc_srl_no,
            "bhf_id": bhf_id,
        },
        request=request,
    )
    return device


def initialize_device(
    db: Session,
    company_id: int,
    device: FiscalDevice,
    *,
    actor: User,
    request: Request | None = None,
    client: httpx.Client | None = None,
) -> FiscalDevice:
    """Call the authority, store what comes back, and activate.

    Also the **verification** call: it returns the counters the authority is holding, which is
    how an outbox row whose answer never arrived is resolved without resending it.
    """
    company = db.get(Company, company_id)
    if company is None or not company.tin:
        raise FiscalSetupError(
            "The company has no TIN. A device is registered against a taxpayer number, so "
            "there is nothing to initialize it as.",
            code="company_tin_missing",
            field_errors={"tin": ["Set the company TIN before initializing a device"]},
        )
    if device.tin and device.tin != company.tin:
        raise FiscalSetupError(
            f"This device was initialized under TIN {device.tin} and the company's TIN is now "
            f"{company.tin}. Re-register the device with the authority rather than sending "
            "sales under a number it was not registered with.",
            code="tin_mismatch",
            field_errors={"tin": ["The device's TIN and the company's TIN differ"]},
        )
    device.tin = company.tin

    adapter = adapter_for(company.fiscal_country, client=client)
    try:
        result, identity = adapter.initialize_device(
            device, cmc_key=decrypt_key(device.cmc_key)
        )
    except FiscalTransportError as unreachable:
        device.last_error = str(unreachable)
        raise FiscalUpstreamError(
            f"The device could not be reached: {unreachable}", code="fiscal_unreachable"
        ) from unreachable

    if not result.ok or identity is None:
        device.last_error = f"{result.code}: {result.message}"
        raise FiscalUpstreamError(
            f"The authority refused initialization ({result.code}): {result.message}",
            code="fiscal_initialization_refused",
        )

    device.sdc_id = identity.sdc_id or device.sdc_id
    device.mrc_no = identity.mrc_no or device.mrc_no
    device.dvc_id = identity.dvc_id or device.dvc_id
    # Encrypted the moment they arrive, and never held in a column, a log or a schema again.
    if identity.cmc_key:
        device.cmc_key = encrypt_key(identity.cmc_key)
    if identity.intrl_key:
        device.intrl_key = encrypt_key(identity.intrl_key)
    if identity.sign_key:
        device.sign_key = encrypt_key(identity.sign_key)
    device.last_success_at = datetime.now(UTC)
    device.last_error = None

    activate(db, company_id, device, actor=actor, request=request)
    record_audit(
        db,
        company_id=company_id,
        action="fiscal_device.initialize",
        entity="fiscal_device",
        entity_id=device.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        # The counters, not the keys. What an auditor needs to know about an initialization is
        # which device answered and what it was holding.
        after={
            "sdc_id": device.sdc_id,
            "mrc_no": device.mrc_no,
            "last_sale_invc_no": identity.last_sale_invc_no,
            "last_purchase_invc_no": identity.last_purchase_invc_no,
            "last_sale_rcpt_no": identity.last_sale_rcpt_no,
        },
        request=request,
    )
    return device


def activate(
    db: Session,
    company_id: int,
    device: FiscalDevice,
    *,
    actor: User,
    request: Request | None = None,
) -> FiscalDevice:
    """Make the device live: lock the stock policy, create its number runs, set `active`."""
    if not device.sdc_id or not device.tin:
        raise FiscalSetupError(
            "A device is activated by initializing it — it has to know who the authority "
            "thinks it is before it can fiscalize anything.",
            code="fiscal_device_not_initialized",
        )
    _lock_negative_stock_policy(db, company_id)
    for doc_type in DEVICE_SEQUENCES:
        ensure_sequence(db, company_id, doc_type, branch_id=device.branch_id)
    device.status = FiscalDeviceStatus.ACTIVE
    # Flushed here rather than left to the commit: the session runs `autoflush=False`, and the
    # very next thing a caller asks is usually "is this company fiscalized now" — a question
    # answered by a query, which would otherwise read the row as it was.
    db.flush()
    record_audit(
        db,
        company_id=company_id,
        action="fiscal_device.activate",
        entity="fiscal_device",
        entity_id=device.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        after={"status": str(FiscalDeviceStatus.ACTIVE)},
        request=request,
    )
    return device


def suspend(
    db: Session,
    company_id: int,
    device: FiscalDevice,
    *,
    reason: str,
    actor: User,
    request: Request | None = None,
) -> FiscalDevice:
    """Stop a device. Its queue is not drained and its branch cannot invoice.

    Deliberately not a delete: the receipts it issued are still the authority's record, and
    the counters it holds are the only thing that can be reconciled against them.
    """
    before = str(device.status)
    device.status = FiscalDeviceStatus.SUSPENDED
    db.flush()
    record_audit(
        db,
        company_id=company_id,
        action="fiscal_device.suspend",
        entity="fiscal_device",
        entity_id=device.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        before={"status": before},
        after={"status": str(FiscalDeviceStatus.SUSPENDED), "reason": reason},
        request=request,
    )
    return device


def _lock_negative_stock_policy(db: Session, company_id: int) -> None:
    settings_row = db.scalar(select(GLSettings).where(GLSettings.company_id == company_id))
    if settings_row is None:
        raise FiscalSetupError(
            "The company has no GL settings row", code="gl_settings_missing"
        )
    settings_row.negative_stock_policy = NegativeStockPolicy.BLOCK


def assert_negative_stock_policy_allowed(
    db: Session, company_id: int, policy: NegativeStockPolicy
) -> None:
    """Refuse `allow` while any device is active (CIS §7.30).

    Called by the inventory defaults screen. Here rather than there because it is a *fiscal*
    rule: no receipt may be issued for goods the stock does not hold, and P5's `block` policy
    is what makes that true — turning it off would make the product capable of issuing one.
    """
    if policy != NegativeStockPolicy.ALLOW:
        return
    if not is_fiscalized(db, company_id):
        return
    raise FiscalSetupError(
        "Negative stock cannot be allowed while an EBM device is active: a fiscal receipt may "
        "not be issued for goods the stock does not hold.",
        code="fiscal_requires_block",
        field_errors={"negative_stock_policy": ["An active EBM device requires 'block'"]},
    )


# --- Synchronisation ----------------------------------------------------------------------


def sync_codes(
    db: Session,
    company_id: int,
    device: FiscalDevice,
    *,
    actor: User,
    request: Request | None = None,
    client: httpx.Client | None = None,
) -> int:
    """Refresh the authority's code tables. Returns how many rows landed."""
    company = db.get(Company, company_id)
    adapter = adapter_for(company.fiscal_country if company else None, client=client)
    since = device.watermarks.get(FiscalSyncKind.CODES)
    result, synced = _call_sync(adapter.sync_codes, device, since=since)
    if not result.ok:
        _record_failure(device, result)
        raise FiscalUpstreamError(
            f"The code sync was refused ({result.code}): {result.message}",
            code="fiscal_sync_refused",
        )
    count = _store_codes(db, company_id, synced.codes)
    _record_success(device, FiscalSyncKind.CODES, synced.watermark)
    record_audit(
        db,
        company_id=company_id,
        action="fiscal_device.sync_codes",
        entity="fiscal_device",
        entity_id=device.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        after={"rows": count, "watermark": synced.watermark},
        request=request,
    )
    return count


def sync_item_classes(
    db: Session,
    company_id: int,
    device: FiscalDevice,
    *,
    actor: User,
    request: Request | None = None,
    client: httpx.Client | None = None,
) -> int:
    company = db.get(Company, company_id)
    adapter = adapter_for(company.fiscal_country if company else None, client=client)
    since = device.watermarks.get(FiscalSyncKind.ITEM_CLASSES)
    result, synced = _call_sync(adapter.sync_item_classes, device, since=since)
    if not result.ok:
        _record_failure(device, result)
        raise FiscalUpstreamError(
            f"The item-class sync was refused ({result.code}): {result.message}",
            code="fiscal_sync_refused",
        )
    count = _store_item_classes(db, company_id, synced.item_classes)
    _record_success(device, FiscalSyncKind.ITEM_CLASSES, synced.watermark)
    record_audit(
        db,
        company_id=company_id,
        action="fiscal_device.sync_item_classes",
        entity="fiscal_device",
        entity_id=device.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        after={"rows": count, "watermark": synced.watermark},
        request=request,
    )
    return count


def _call_sync(method, device: FiscalDevice, *, since: str | None):  # noqa: ANN001, ANN202
    """One watermarked read, turning an unreachable device into the API's own error.

    The `FiscalTransportError` → `FiscalUpstreamError` translation happens here rather than at
    each call site so that "the device did not answer" reaches the operator as a 502 with the
    reason on it, instead of a 500 with a stack trace.
    """
    try:
        return method(device, since=since, cmc_key=decrypt_key(device.cmc_key))
    except FiscalTransportError as unreachable:
        device.last_error = str(unreachable)
        raise FiscalUpstreamError(
            f"The device could not be reached: {unreachable}", code="fiscal_unreachable"
        ) from unreachable


def _store_codes(db: Session, company_id: int, entries: Sequence[FiscalCodeEntry]) -> int:
    """Upsert by (class, code). Upsert rather than replace, because a refresh by watermark
    brings only what changed — deleting what it did not mention would empty the table."""
    if not entries:
        return 0
    existing = {
        (row.code_class, row.code): row
        for row in db.scalars(select(FiscalCode).where(FiscalCode.company_id == company_id))
    }
    now = datetime.now(UTC)
    for entry in entries:
        row = existing.get((entry.code_class, entry.code))
        if row is None:
            row = FiscalCode(
                company_id=company_id, code_class=entry.code_class, code=entry.code
            )
            db.add(row)
        row.code_class_name = entry.code_class_name
        row.name = entry.name
        row.description = entry.description
        row.sort_order = entry.sort_order
        row.user_defined_1, row.user_defined_2, row.user_defined_3 = entry.user_defined
        row.is_active = entry.active
        row.synced_at = now
    db.flush()
    return len(entries)


def _store_item_classes(
    db: Session, company_id: int, entries: Sequence[FiscalItemClassEntry]
) -> int:
    if not entries:
        return 0
    existing = {
        row.item_cls_cd: row
        for row in db.scalars(
            select(FiscalItemClass).where(FiscalItemClass.company_id == company_id)
        )
    }
    now = datetime.now(UTC)
    for entry in entries:
        row = existing.get(entry.code)
        if row is None:
            row = FiscalItemClass(company_id=company_id, item_cls_cd=entry.code)
            db.add(row)
        row.item_cls_nm = entry.name
        row.item_cls_lvl = entry.level
        row.tax_ty_cd = entry.tax_class
        row.mjr_tg_yn = entry.major_target
        row.is_active = entry.active
        row.synced_at = now
    db.flush()
    return len(entries)


def _record_success(device: FiscalDevice, kind: FiscalSyncKind, watermark: str | None) -> None:
    """Store the watermark **only here** — after a success, beside the rows it brought, and
    only when it moves forward.

    Two rules in one function, for the same reason: a watermark is a promise about what has
    already been fetched, and both ways of breaking it are silent.

    A failed sync that advanced it would skip every row published between the two calls, for
    good, because nothing ever asks for that window again. And a watermark that went
    *backwards* — a clock skew, a restored backup, a device whose host drifted — would re-fetch
    harmlessly here but is refused outright by RRA on the imports feed: certification
    checkpoint 67 requires each `lastReqDt` to be greater than the previous request's. Keeping
    the stored value monotonic makes that true of every kind rather than of the one that is
    checked.
    """
    device.last_success_at = datetime.now(UTC)
    device.last_error = None
    if not watermark:
        return
    previous = device.watermarks.get(str(kind))
    if previous is not None and watermark <= previous:
        return
    # Reassigned rather than mutated: SQLAlchemy does not track in-place changes to a JSONB
    # dict, so `device.watermarks[kind] = …` would be written nowhere.
    device.watermarks = {**device.watermarks, str(kind): watermark}


def _record_failure(device: FiscalDevice, result) -> None:  # noqa: ANN001
    device.last_error = f"{result.code}: {result.message}"


# --- TIN lookup ---------------------------------------------------------------------------


def lookup_tin(
    db: Session,
    company_id: int,
    device: FiscalDevice,
    tin: str,
    *,
    client: httpx.Client | None = None,
) -> TinLookup:
    """`Verify TIN` from the customer screen.

    A synchronous read, never a queue row: the person keying an invoice is waiting for the
    answer, and a queued lookup helps nobody. It writes nothing — the authority's opinion of a
    TIN is not a fact about the partner, it is an answer at a moment.
    """
    company = db.get(Company, company_id)
    adapter = adapter_for(company.fiscal_country if company else None, client=client)
    try:
        result, lookup = adapter.lookup_tin(device, tin, cmc_key=decrypt_key(device.cmc_key))
    except FiscalTransportError as unreachable:
        raise FiscalUpstreamError(
            f"The device could not be reached: {unreachable}", code="fiscal_unreachable"
        ) from unreachable
    # `884` is "no such taxpayer", which is an *answer* and the one the screen most needs to
    # show. Every other refusal is a failure to look, and says so.
    if not result.ok and result.code != UNKNOWN_TIN_CODE:
        raise FiscalUpstreamError(
            f"The lookup was refused ({result.code}): {result.message}",
            code="fiscal_lookup_refused",
        )
    return lookup
