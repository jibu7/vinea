"""What sends the queue, and what an operator can do about a row that will not go.

**One device, one row in flight, oldest first.** The drainer takes the head of a device's queue
under `SELECT … FOR UPDATE SKIP LOCKED`, sends it, records the outcome, and takes the next. It
does not skip past a row it cannot send: a `failed`, `unknown` or `needs_receipt` row blocks
everything behind it, because RRA requires a stock movement to follow the sale that caused it
(`921`/`922`) and the receipt counters are a sequence. A queue that reordered itself to make
progress would be making the wrong kind.

**`sending` is never committed.** The row is marked in the caller's transaction and the HTTP
call happens inside it, so a process that dies mid-send rolls back to `queued` rather than
leaving a row nobody will ever finish. That costs a row lock for the length of one call, which
is exactly the serialization "one in flight" asks for.

**The three outcomes that are not "sent".**

* Transport, and `894` — the request did not arrive, or RRA said it could not take it now.
  Safe to retry: back to `queued` on the 1 → 5 → 15 → 60 → 360-minute backoff, then every
  six hours, forever.
* A refusal — `881`–`884`, `9xx`. RRA will refuse it again, so it does not retry: `failed`,
  with the code and message an operator quotes when they ring the authority.
* **A timeout after the request left** — `unknown`. RRA may be holding the sale, and a resend
  is a duplicate that comes back `994` with *no receipt data*, leaving a registered sale with
  nothing to print. There is no automatic anything: a person resolves it by asking the device
  what it holds (`verify_with_device`) or by attaching the receipt off the portal.

The clock is injected everywhere. A backoff tested by sleeping is a test nobody runs.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fiscal import outbox
from app.fiscal.keys import decrypt_key
from app.fiscal.protocol import (
    FiscalizationAdapter,
    FiscalReceiptData,
    FiscalTimeout,
    FiscalTransportError,
)
from app.fiscal.registry import adapter_for
from app.models.company import Company
from app.models.fiscalization import (
    FiscalDevice,
    FiscalItem,
    FiscalOutboxKind,
    FiscalOutboxRow,
    FiscalOutboxStatus,
    FiscalReceipt,
    FiscalReceiptType,
)
from app.models.subledger import PartnerDocument
from app.models.user import User
from app.services.audit import record_audit

logger = logging.getLogger("app.fiscal.drainer")

#: RRA's "server communication" code — the one refusal that is temporary (VSDC §4.19). Held
#: here as a bare string rather than imported from `app/fiscal/rwanda/codes.py`, which rule 12
#: puts out of reach: what this module needs is the *policy*, and the policy is that an
#: authority may say "not now". A second country adds its own code to this set.
RETRYABLE_RESULT_CODES: frozenset[str] = frozenset({"894"})

#: The receipt label each sale-shaped kind issues under (CIS §5).
RECEIPT_TYPE_BY_KIND: dict[FiscalOutboxKind, FiscalReceiptType] = {
    FiscalOutboxKind.SALE: FiscalReceiptType.NORMAL_SALE,
    FiscalOutboxKind.REFUND: FiscalReceiptType.NORMAL_REFUND,
}


@dataclass(frozen=True)
class DrainOutcome:
    """One attempt, for the caller's log and for the queue screen."""

    row_id: int
    kind: FiscalOutboxKind
    status: FiscalOutboxStatus
    code: str | None = None
    message: str | None = None


def drain_company(
    db: Session,
    company_id: int,
    *,
    now: datetime | None = None,
    client: httpx.Client | None = None,
    max_rows_per_device: int = 50,
) -> list[DrainOutcome]:
    """Every active device with work, each on its own queue. Never commits."""
    moment = now or datetime.now(UTC)
    outcomes: list[DrainOutcome] = []
    for device in outbox.devices_with_work(db, company_id):
        outcomes.extend(
            drain_device(
                db,
                company_id,
                device,
                now=moment,
                client=client,
                max_rows=max_rows_per_device,
            )
        )
    return outcomes


def drain_device(
    db: Session,
    company_id: int,
    device: FiscalDevice,
    *,
    now: datetime | None = None,
    client: httpx.Client | None = None,
    max_rows: int = 50,
) -> list[DrainOutcome]:
    """Send this device's queue, head first, until it stops or `max_rows` have gone.

    `max_rows` is a fairness bound, not a correctness one: a device with a thousand rows should
    not hold a drain pass while every other device waits.
    """
    moment = now or datetime.now(UTC)
    company = db.get(Company, company_id)
    adapter = adapter_for(company.fiscal_country if company else None, client=client)
    outcomes: list[DrainOutcome] = []
    for _ in range(max_rows):
        row = outbox.next_row(db, company_id, device.id, now=moment)
        if row is None:
            break
        outcome = send_row(db, company_id, device, row, adapter=adapter, now=moment)
        outcomes.append(outcome)
        if outcome.status != FiscalOutboxStatus.SENT:
            # The head did not go. Everything behind it waits — that is the FIFO, not a
            # failure of it. Logged at device level because "this shop's queue has stopped" is
            # the thing an operator needs to hear, and the row's own reason is on the row.
            logger.info(
                "ebm queue stopped device=%s row=%s status=%s code=%s",
                device.id,
                outcome.row_id,
                outcome.status,
                outcome.code,
            )
            break
    return outcomes


def send_row(
    db: Session,
    company_id: int,
    device: FiscalDevice,
    row: FiscalOutboxRow,
    *,
    adapter: FiscalizationAdapter,
    now: datetime,
) -> DrainOutcome:
    """One row, one attempt, one recorded outcome. Never commits.

    `sending` is set inside the caller's transaction and deliberately not committed: the whole
    attempt is one unit of work, so a crash anywhere in it rolls back to `queued` instead of
    stranding a row in a state nothing clears.
    """
    row.status = FiscalOutboxStatus.SENDING
    row.attempts += 1
    try:
        result, receipt = adapter.send(
            device, row.kind, row.payload, cmc_key=decrypt_key(device.cmc_key)
        )
    except FiscalTimeout as timeout:
        # The request left and nothing came back. **Never resent automatically.**
        row.status = FiscalOutboxStatus.UNKNOWN
        row.last_error = str(timeout)
        row.next_attempt_at = None
        db.flush()
        return DrainOutcome(row.id, row.kind, row.status, message=str(timeout))
    except FiscalTransportError as unreachable:
        _requeue(row, now, str(unreachable))
        device.last_error = str(unreachable)
        db.flush()
        return DrainOutcome(row.id, row.kind, row.status, message=str(unreachable))

    row.last_result_cd = result.code
    row.response = result.data or {}
    if result.ok:
        return _record_success(db, company_id, device, row, receipt=receipt, now=now)
    if result.code in RETRYABLE_RESULT_CODES:
        _requeue(row, now, f"{result.code}: {result.message}")
        device.last_error = f"{result.code}: {result.message}"
        db.flush()
        return DrainOutcome(row.id, row.kind, row.status, result.code, result.message)

    row.status = FiscalOutboxStatus.FAILED
    row.last_error = f"{result.code}: {result.message}"
    row.next_attempt_at = None
    device.last_error = row.last_error
    db.flush()
    return DrainOutcome(row.id, row.kind, row.status, result.code, result.message)


def _requeue(row: FiscalOutboxRow, now: datetime, error: str) -> None:
    row.status = FiscalOutboxStatus.QUEUED
    row.last_error = error
    row.next_attempt_at = now + outbox.backoff_for(row.attempts)


def _record_success(
    db: Session,
    company_id: int,
    device: FiscalDevice,
    row: FiscalOutboxRow,
    *,
    receipt: FiscalReceiptData | None,
    now: datetime,
) -> DrainOutcome:
    """`000`, and what that means for each kind.

    A sale or a refund that came back **without** receipt data is not sent: `needs_receipt`,
    because the authority is now holding a sale Vinea cannot print. Treating it as sent would
    make "sent means signed" false everywhere else — including in `assert_fiscal_invariants`,
    which is the thing that would have to be weakened to let it pass.
    """
    receipt_type = RECEIPT_TYPE_BY_KIND.get(row.kind)
    if receipt_type is not None:
        if receipt is None:
            row.status = FiscalOutboxStatus.NEEDS_RECEIPT
            row.last_error = (
                "RRA accepted the sale and returned no receipt. Read the receipt off MyRRA and "
                "attach it — the sale is registered and cannot be resent."
            )
            row.next_attempt_at = None
            db.flush()
            return DrainOutcome(row.id, row.kind, row.status, row.last_result_cd)
        _write_receipt(db, company_id, device, row, receipt=receipt, receipt_type=receipt_type)
    elif row.kind == FiscalOutboxKind.ITEM and row.source_doc_id is not None:
        item = db.scalar(
            select(FiscalItem).where(
                FiscalItem.company_id == company_id,
                FiscalItem.item_id == row.source_doc_id,
            )
        )
        if item is not None:
            item.registered_at = now

    row.status = FiscalOutboxStatus.SENT
    row.sent_at = now
    row.last_error = None
    row.next_attempt_at = None
    device.last_success_at = now
    device.last_error = None
    db.flush()
    return DrainOutcome(row.id, row.kind, row.status, row.last_result_cd)


def _write_receipt(
    db: Session,
    company_id: int,
    device: FiscalDevice,
    row: FiscalOutboxRow,
    *,
    receipt: FiscalReceiptData,
    receipt_type: FiscalReceiptType,
) -> FiscalReceipt:
    """The authority's signature, stored once and never edited (a trigger enforces it).

    `request` is the **frozen payload** rather than a rebuild: what an auditor needs beside a
    receipt is the bytes that produced it.
    """
    stored = FiscalReceipt(
        company_id=company_id,
        document_id=row.source_doc_id,
        device_id=device.id,
        outbox_id=row.id,
        receipt_type=receipt_type,
        invc_no=row.invc_no or receipt.invc_no or 0,
        org_invc_no=receipt.org_invc_no,
        rcpt_no=receipt.rcpt_no,
        tot_rcpt_no=receipt.tot_rcpt_no,
        intrl_data=receipt.intrl_data,
        rcpt_sign=receipt.rcpt_sign,
        sdc_id=receipt.sdc_id,
        mrc_no=receipt.mrc_no,
        sdc_datetime=receipt.sdc_datetime,
        qr_payload=receipt.qr_payload,
        request=row.payload,
        response=row.response or {},
    )
    db.add(stored)
    db.flush()
    # A document points at **its own** receipt. A reversal's refund is a receipt *about* the
    # invoice rather than the invoice's own, so it is deliberately not linked: linking it
    # would make the invoice print the refund that undid it.
    if row.source_doc_type == outbox.DOCUMENT_SOURCE and row.source_doc_id is not None:
        document = db.get(PartnerDocument, row.source_doc_id)
        if document is not None:
            document.fiscal_receipt_id = stored.id
    db.flush()
    return stored


# --- The queue screen's four actions (decision 4) -------------------------------------------


class QueueActionError(Exception):
    """An action that does not apply to the row it was asked of."""


def retry_now(
    db: Session,
    company_id: int,
    row: FiscalOutboxRow,
    *,
    actor: User,
    now: datetime | None = None,
) -> FiscalOutboxRow:
    """Put a `failed` or backing-off row back at the front of its own queue, due immediately.

    **Not offered for `unknown` or `needs_receipt`**, and that is the point of the refusal
    rather than an oversight: those two mean RRA may already hold the sale, and "retry" on
    them is precisely the duplicate the whole policy exists to prevent.
    """
    if row.status not in (FiscalOutboxStatus.FAILED, FiscalOutboxStatus.QUEUED):
        raise QueueActionError(
            f"a row in state '{row.status}' is not retried — RRA may already hold it. Verify "
            "it with the device, or attach the receipt."
        )
    row.status = FiscalOutboxStatus.QUEUED
    row.next_attempt_at = now or datetime.now(UTC)
    row.last_error = None
    db.flush()
    _audit(db, company_id, row, action="fiscal_outbox.retry", actor=actor, after={})
    return row


def verify_with_device(
    db: Session,
    company_id: int,
    device: FiscalDevice,
    row: FiscalOutboxRow,
    *,
    actor: User,
    client: httpx.Client | None = None,
) -> FiscalOutboxRow:
    """Ask the device what it is holding, and decide from its counters.

    Re-initialization is also the verification call (it returns `lastSaleInvcNo` and friends),
    which is why an `unknown` row is resolved by asking rather than by resending. A counter
    **below** this row's number means RRA never saw it — back to `queued`. **At or above**
    means RRA has it and Vinea has no receipt for it — `needs_receipt`, and a person attaches
    one.
    """
    if row.status not in (FiscalOutboxStatus.UNKNOWN, FiscalOutboxStatus.FAILED):
        raise QueueActionError(
            f"there is nothing to verify about a row in state '{row.status}'"
        )
    company = db.get(Company, company_id)
    adapter = adapter_for(company.fiscal_country if company else None, client=client)
    result, identity = adapter.initialize_device(device, cmc_key=decrypt_key(device.cmc_key))
    if not result.ok or identity is None:
        raise QueueActionError(
            f"the device could not be asked ({result.code}): {result.message}"
        )
    held = (
        identity.last_purchase_invc_no
        if row.kind
        in (FiscalOutboxKind.PURCHASE, FiscalOutboxKind.PURCHASE_CONFIRM)
        else identity.last_sale_invc_no
    )
    ours = row.invc_no or 0
    if held is not None and held >= ours:
        row.status = FiscalOutboxStatus.NEEDS_RECEIPT
        row.last_error = (
            f"RRA holds invoice {held}, which includes this row's {ours}. Attach the receipt "
            "from MyRRA — it must not be resent."
        )
        row.next_attempt_at = None
    else:
        row.status = FiscalOutboxStatus.QUEUED
        row.next_attempt_at = datetime.now(UTC)
        row.last_error = None
    db.flush()
    _audit(
        db,
        company_id,
        row,
        action="fiscal_outbox.verify",
        actor=actor,
        after={"device_holds": held, "row_invc_no": ours, "status": str(row.status)},
    )
    return row


def attach_receipt(
    db: Session,
    company_id: int,
    device: FiscalDevice,
    row: FiscalOutboxRow,
    *,
    fields: dict,
    note: str,
    actor: User,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> FiscalReceipt:
    """A receipt read off the authority's portal, attached by a person who says so.

    Normalised through the **adapter**, not parsed here: an operator copying the six fields off
    MyRRA is keying a sales response, and a second way of reading one would eventually disagree
    with the first. Audited with the actor's note, because this is a human assertion about what
    a revenue authority holds.
    """
    if row.status != FiscalOutboxStatus.NEEDS_RECEIPT:
        raise QueueActionError(
            f"a receipt is attached to a row that needs one; this row is '{row.status}'"
        )
    receipt_type = RECEIPT_TYPE_BY_KIND.get(row.kind)
    if receipt_type is None:
        raise QueueActionError(f"a {row.kind} row has no receipt")
    company = db.get(Company, company_id)
    adapter = adapter_for(company.fiscal_country if company else None, client=client)
    receipt = adapter.normalize_receipt(device, fields, invc_no=row.invc_no)
    if receipt is None:
        raise QueueActionError(
            "those fields are not a receipt — a receipt carries its counters, its internal "
            "data, its signature and the device's date and time."
        )
    moment = now or datetime.now(UTC)
    stored = _write_receipt(
        db, company_id, device, row, receipt=receipt, receipt_type=receipt_type
    )
    row.status = FiscalOutboxStatus.SENT
    row.sent_at = moment
    row.last_error = None
    row.next_attempt_at = None
    row.resolved_by = actor.id
    row.resolution_note = note
    db.flush()
    _audit(
        db,
        company_id,
        row,
        action="fiscal_outbox.attach_receipt",
        actor=actor,
        after={"receipt_id": stored.id, "note": note},
    )
    return stored


def _audit(
    db: Session,
    company_id: int,
    row: FiscalOutboxRow,
    *,
    action: str,
    actor: User,
    after: dict,
) -> None:
    record_audit(
        db,
        company_id=company_id,
        action=action,
        entity="fiscal_outbox",
        entity_id=row.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        after={"kind": str(row.kind), "status": str(row.status), **after},
    )
