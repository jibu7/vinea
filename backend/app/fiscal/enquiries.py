"""The fiscal enquiries and listings (P7 step 5).

Five reads, and they are the half of the phase a person looks at rather than the half that
talks to Kigali: **the queue** per device and per row, **the receipts** RRA signed, and **the
items** it holds. The purchase feed, the import register, the VAT returns and the revaluation
runs already list themselves (steps 3 and 4); what was missing is everything about the outbox,
which until now could only be read by a test holding a `Session`.

**Nothing here is a column.** Every count is a count of rows in a state, `offline` is derived
from the age of the oldest unsent row and not stored beside it, and a receipt's "is this
document's" comes from the receipt rather than from a flag on the document. A dashboard that
cached a status would eventually show a device as healthy while its queue was stuck, which is
the one thing this screen exists to prevent.

**The queue detail shows a payload, and it is redacted twice.** Once at enqueue — the adapter
strips the three key fields before anything is stored — and once here, on the way out, because
"the stored rows are clean" is a property of today's writer and this is a screen that will
outlive it. `tests/fiscal/test_key_redaction.py` walks both.

**The action log is the audit trail, not a second history table.** Retry, verify and attach
already write `audit_log` rows (`drainer._audit`), and a queue row's log is exactly those rows
for that entity. A row that recorded its own history would be a second copy to keep honest.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.fiscal import daily, registry
from app.fiscal import outbox as outbox_service
from app.models.audit import AuditLog
from app.models.company import Branch, Company
from app.models.fiscalization import (
    FiscalDailyReport,
    FiscalDevice,
    FiscalItem,
    FiscalOutboxKind,
    FiscalOutboxRow,
    FiscalOutboxStatus,
    FiscalReceipt,
    FiscalReceiptType,
)
from app.models.inventory import Item
from app.models.partner import Partner, PartnerRole
from app.models.subledger import DocumentKind, PartnerDocument
from app.models.user import User

#: The audit actions a queue row can carry. Named here rather than matched by prefix so that a
#: new `fiscal_outbox.*` action has to be added deliberately — an action log that silently
#: grew a row nobody designed is a screen that shows something nobody can explain.
QUEUE_ACTIONS = (
    "fiscal_outbox.retry",
    "fiscal_outbox.verify",
    "fiscal_outbox.attach_receipt",
    "fiscal_outbox.print_copy",
)

QUEUE_ENTITY = "fiscal_outbox"


# --- The queue, per device --------------------------------------------------------------------


@dataclass(frozen=True)
class QueueStatusCount:
    status: FiscalOutboxStatus
    rows: int


@dataclass(frozen=True)
class QueueHead:
    """The row at the front of a device's queue, whatever state it is in.

    It is the first thing an operator needs when a device is stuck, because *every* row behind
    it is waiting on this one (per-device FIFO, decision 4). A dashboard that showed counts
    without naming the head would say "seven queued" and leave the person to find which one is
    the blockage.
    """

    row_id: int
    kind: FiscalOutboxKind
    status: FiscalOutboxStatus
    sequence_no: int
    attempts: int
    next_attempt_at: datetime | None
    last_result_cd: str | None
    last_error: str | None


@dataclass(frozen=True)
class QueueDeviceView:
    """One device's queue as the dashboard reads it."""

    device_id: int
    branch_id: int
    branch_code: str
    branch_name: str
    status: str
    profile: str
    environment: str
    sdc_id: str | None
    mrc_no: str | None
    last_success_at: datetime | None
    last_error: str | None
    #: Set when the oldest unsent row is older than `outbox.OFFLINE_AFTER`. VSDC §2.2 item 4:
    #: a device 24 hours without connectivity stops issuing, so this is the flag that says a
    #: shop is about to be unable to sell.
    offline: bool
    oldest_queued_at: datetime | None
    #: Whole seconds, so a screen can render an age without knowing what "now" the server used.
    oldest_queued_age_seconds: int | None
    counts: tuple[QueueStatusCount, ...]
    head: QueueHead | None

    @property
    def pending_rows(self) -> int:
        return sum(
            count.rows
            for count in self.counts
            if count.status in outbox_service.NON_TERMINAL
        )

    @property
    def blocked(self) -> bool:
        """True when the head is in a state that will not move by itself.

        `failed`, `unknown` and `needs_receipt` each wait for a person: a refusal RRA will give
        again, an answer that never arrived, and a sale RRA holds that Vinea has no receipt for.
        """
        return self.head is not None and self.head.status in (
            FiscalOutboxStatus.FAILED,
            FiscalOutboxStatus.UNKNOWN,
            FiscalOutboxStatus.NEEDS_RECEIPT,
        )


def queue_summary(
    db: Session,
    company_id: int,
    *,
    device_id: int | None = None,
    now: datetime | None = None,
) -> list[QueueDeviceView]:
    """Every device and what its queue holds — including the devices holding nothing.

    A device with an empty queue is in the list deliberately. "Which devices are there, and is
    each one keeping up" is the question, and a listing that showed only the ones with work
    would answer it with silence on the healthy case, which is indistinguishable from a device
    nobody registered.
    """
    moment = now or datetime.now(UTC)
    devices = list(
        db.scalars(
            select(FiscalDevice)
            .where(
                FiscalDevice.company_id == company_id,
                *((FiscalDevice.id == device_id,) if device_id is not None else ()),
            )
            .order_by(FiscalDevice.id)
        )
    )
    if device_id is not None and not devices:
        raise NotFoundError("Device not found")

    branches = {
        row.id: row
        for row in db.scalars(select(Branch).where(Branch.company_id == company_id))
    }
    counts: dict[int, list[QueueStatusCount]] = {}
    for row_device_id, status, rows in db.execute(
        select(
            FiscalOutboxRow.device_id,
            FiscalOutboxRow.status,
            func.count(FiscalOutboxRow.id),
        )
        .where(FiscalOutboxRow.company_id == company_id)
        .group_by(FiscalOutboxRow.device_id, FiscalOutboxRow.status)
    ):
        counts.setdefault(row_device_id, []).append(QueueStatusCount(status=status, rows=rows))

    views: list[QueueDeviceView] = []
    for device in devices:
        branch = branches.get(device.branch_id)
        oldest = outbox_service.oldest_queued_at(db, company_id, device.id)
        if oldest is not None and oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=UTC)
        head = outbox_service.head_row(db, company_id, device.id)
        # Derived from the `oldest` already in hand rather than through
        # `outbox.is_offline`, which would run the same aggregate a second time per device.
        # The threshold stays the outbox module's, so the two cannot drift.
        offline = oldest is not None and moment - oldest >= outbox_service.OFFLINE_AFTER
        views.append(
            QueueDeviceView(
                device_id=device.id,
                branch_id=device.branch_id,
                branch_code=branch.code if branch else "",
                branch_name=branch.name if branch else "",
                status=str(device.status),
                profile=str(device.profile),
                environment=str(device.environment),
                sdc_id=device.sdc_id,
                mrc_no=device.mrc_no,
                last_success_at=device.last_success_at,
                last_error=device.last_error,
                offline=offline,
                oldest_queued_at=oldest,
                oldest_queued_age_seconds=(
                    None if oldest is None else int((moment - oldest).total_seconds())
                ),
                counts=tuple(
                    sorted(counts.get(device.id, []), key=lambda count: str(count.status))
                ),
                head=None if head is None else _head(head),
            )
        )
    return views


def _head(row: FiscalOutboxRow) -> QueueHead:
    return QueueHead(
        row_id=row.id,
        kind=row.kind,
        status=row.status,
        sequence_no=row.sequence_no,
        attempts=row.attempts,
        next_attempt_at=row.next_attempt_at,
        last_result_cd=row.last_result_cd,
        last_error=row.last_error,
    )


# --- The queue, row by row ----------------------------------------------------------------


@dataclass(frozen=True)
class QueueRowView:
    """One outbox row, with enough of what raised it to make the listing readable.

    `document_number` and `partner_name` are resolved here rather than left to the screen: a
    queue of `partner_document 412` rows is a list of numbers nobody can act on, and the
    alternative is a request per row.
    """

    row_id: int
    device_id: int
    kind: FiscalOutboxKind
    status: FiscalOutboxStatus
    sequence_no: int
    invc_no: int | None
    sar_no: int | None
    attempts: int
    next_attempt_at: datetime | None
    last_result_cd: str | None
    last_error: str | None
    sent_at: datetime | None
    created_at: datetime | None
    source_doc_type: str | None
    source_doc_id: int | None
    document_number: str | None
    document_id: int | None
    partner_name: str | None
    receipt_id: int | None


@dataclass(frozen=True)
class QueueActionEntry:
    """One line of a row's action log — an `audit_log` row, read rather than copied."""

    at: datetime
    action: str
    actor_email: str | None
    detail: dict[str, Any]


@dataclass(frozen=True)
class QueueRowDetail:
    row: QueueRowView
    #: What was sent and what came back, both redacted on the way out (see the module
    #: docstring). `response` is `None` on a row nothing has answered yet.
    request: dict[str, Any]
    response: dict[str, Any] | None
    resolved_by_email: str | None
    resolution_note: str | None
    actions: tuple[QueueActionEntry, ...]


def queue_rows(
    db: Session,
    company_id: int,
    *,
    device_id: int | None = None,
    status: FiscalOutboxStatus | None = None,
    kind: FiscalOutboxKind | None = None,
    document_id: int | None = None,
    limit: int = 200,
) -> list[QueueRowView]:
    """The rows themselves, newest last — queue order, which is the order they will be sent in.

    `document_id` is the *document's* history (step 8's read-only "Fiscal queue history, per
    document"), and it matches both source names a document can raise a row under: its own
    registration and the refund a reversal of it owes.
    """
    query: Select = select(FiscalOutboxRow).where(FiscalOutboxRow.company_id == company_id)
    if device_id is not None:
        query = query.where(FiscalOutboxRow.device_id == device_id)
    if status is not None:
        query = query.where(FiscalOutboxRow.status == status)
    if kind is not None:
        query = query.where(FiscalOutboxRow.kind == kind)
    if document_id is not None:
        query = query.where(
            FiscalOutboxRow.source_doc_id == document_id,
            FiscalOutboxRow.source_doc_type.in_(
                (outbox_service.DOCUMENT_SOURCE, outbox_service.REVERSAL_SOURCE)
            ),
        )
    rows = list(db.scalars(query.order_by(FiscalOutboxRow.sequence_no).limit(limit)))
    return _rows_view(db, company_id, rows)


def _rows_view(
    db: Session, company_id: int, rows: Sequence[FiscalOutboxRow]
) -> list[QueueRowView]:
    document_ids = {
        row.source_doc_id
        for row in rows
        if row.source_doc_id is not None
        and row.source_doc_type
        in (outbox_service.DOCUMENT_SOURCE, outbox_service.REVERSAL_SOURCE)
    }
    documents = _documents(db, company_id, document_ids)
    partners = _partners(db, company_id, {doc.partner_id for doc in documents.values()})
    receipts = {
        receipt.outbox_id: receipt.id
        for receipt in db.scalars(
            select(FiscalReceipt).where(
                FiscalReceipt.company_id == company_id,
                FiscalReceipt.outbox_id.in_([row.id for row in rows] or [0]),
            )
        )
    }
    views: list[QueueRowView] = []
    for row in rows:
        document = documents.get(row.source_doc_id) if row.source_doc_id else None
        views.append(
            QueueRowView(
                row_id=row.id,
                device_id=row.device_id,
                kind=row.kind,
                status=row.status,
                sequence_no=row.sequence_no,
                invc_no=row.invc_no,
                sar_no=row.sar_no,
                attempts=row.attempts,
                next_attempt_at=row.next_attempt_at,
                last_result_cd=row.last_result_cd,
                last_error=row.last_error,
                sent_at=row.sent_at,
                created_at=row.created_at,
                source_doc_type=row.source_doc_type,
                source_doc_id=row.source_doc_id,
                document_number=document.number if document else None,
                document_id=document.id if document else None,
                partner_name=(
                    partners.get(document.partner_id) if document is not None else None
                ),
                receipt_id=receipts.get(row.id),
            )
        )
    return views


def queue_row(db: Session, company_id: int, row_id: int) -> QueueRowDetail:
    row = db.scalar(
        select(FiscalOutboxRow).where(
            FiscalOutboxRow.company_id == company_id, FiscalOutboxRow.id == row_id
        )
    )
    if row is None:
        raise NotFoundError("Queue row not found")
    actions = list(
        db.scalars(
            select(AuditLog)
            .where(
                AuditLog.company_id == company_id,
                AuditLog.entity == QUEUE_ENTITY,
                AuditLog.entity_id == str(row.id),
                AuditLog.action.in_(QUEUE_ACTIONS),
            )
            .order_by(AuditLog.at, AuditLog.id)
        )
    )
    resolver = db.get(User, row.resolved_by) if row.resolved_by is not None else None
    # Redacted a second time, through the adapter: which fields are secret is the authority's
    # fact (rule 12), and "the stored rows are clean" is a property of today's writer.
    company = db.get(Company, company_id)
    adapter = registry.adapter_for(company.fiscal_country if company else None)
    return QueueRowDetail(
        row=_rows_view(db, company_id, [row])[0],
        request=adapter.redact_payload(row.payload),
        response=(
            adapter.redact_payload(row.response) if row.response is not None else None
        ),
        resolved_by_email=resolver.email if resolver is not None else None,
        resolution_note=row.resolution_note,
        actions=tuple(
            QueueActionEntry(
                at=entry.at,
                action=entry.action,
                actor_email=entry.actor_email,
                detail=entry.after or {},
            )
            for entry in actions
        ),
    )


# --- Receipts ------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReceiptView:
    """One receipt RRA signed, and the three things a person searches it by."""

    receipt_id: int
    device_id: int
    document_id: int
    document_number: str
    document_date: date
    partner_id: int
    partner_name: str
    receipt_type: FiscalReceiptType
    invc_no: int
    org_invc_no: int | None
    rcpt_no: int
    tot_rcpt_no: int
    #: `rcptNo/totRcptNo LABEL` — the number printed on the paper, which is what somebody
    #: holding a receipt will type into the search box.
    receipt_number: str
    sdc_id: str
    mrc_no: str | None
    sdc_datetime: datetime
    intrl_data: str
    rcpt_sign: str
    qr_payload: str
    copy_count: int
    total_amount: Decimal
    base_total_amount: Decimal
    currency_id: int
    journal_entry_id: int | None


def receipt_number(receipt: FiscalReceipt) -> str:
    return f"{receipt.rcpt_no}/{receipt.tot_rcpt_no} {receipt.receipt_type}"


def receipts(
    db: Session,
    company_id: int,
    *,
    device_id: int | None = None,
    receipt_type: FiscalReceiptType | None = None,
    document_id: int | None = None,
    partner_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
    limit: int = 200,
) -> list[ReceiptView]:
    """The receipts listing and its search.

    `search` is the free-text box, and it matches the four things somebody has in front of them
    when they come looking: the printed counter (`3`, `3/4` or `3/4 NS`), the document number,
    the partner's name, and the authority's invoice number. Matching all four in one box beats
    four fields nobody knows which to use, and each is anchored rather than fuzzy so a search
    for `1` does not return the day.
    """
    query: Select = (
        select(FiscalReceipt, PartnerDocument, Partner)
        .join(
            PartnerDocument,
            (PartnerDocument.company_id == FiscalReceipt.company_id)
            & (PartnerDocument.id == FiscalReceipt.document_id),
        )
        .join(
            Partner,
            (Partner.company_id == PartnerDocument.company_id)
            & (Partner.id == PartnerDocument.partner_id),
        )
        .where(FiscalReceipt.company_id == company_id)
    )
    if device_id is not None:
        query = query.where(FiscalReceipt.device_id == device_id)
    if receipt_type is not None:
        query = query.where(FiscalReceipt.receipt_type == receipt_type)
    if document_id is not None:
        query = query.where(FiscalReceipt.document_id == document_id)
    if partner_id is not None:
        query = query.where(PartnerDocument.partner_id == partner_id)
    if date_from is not None:
        query = query.where(PartnerDocument.document_date >= date_from)
    if date_to is not None:
        query = query.where(PartnerDocument.document_date <= date_to)
    if search:
        query = query.where(_search_clause(search.strip()))

    rows = db.execute(query.order_by(FiscalReceipt.tot_rcpt_no, FiscalReceipt.id).limit(limit))
    return [
        ReceiptView(
            receipt_id=receipt.id,
            device_id=receipt.device_id,
            document_id=document.id,
            document_number=document.number,
            document_date=document.document_date,
            partner_id=partner.id,
            partner_name=partner.name,
            receipt_type=receipt.receipt_type,
            invc_no=receipt.invc_no,
            org_invc_no=receipt.org_invc_no,
            rcpt_no=receipt.rcpt_no,
            tot_rcpt_no=receipt.tot_rcpt_no,
            receipt_number=receipt_number(receipt),
            sdc_id=receipt.sdc_id,
            mrc_no=receipt.mrc_no,
            sdc_datetime=receipt.sdc_datetime,
            intrl_data=receipt.intrl_data,
            rcpt_sign=receipt.rcpt_sign,
            qr_payload=receipt.qr_payload,
            copy_count=receipt.copy_count,
            total_amount=document.total_amount,
            base_total_amount=document.base_total_amount,
            currency_id=document.currency_id,
            journal_entry_id=document.journal_entry_id,
        )
        for receipt, document, partner in rows
    ]


def _search_clause(term: str):  # noqa: ANN202 - a SQLAlchemy clause
    """`3`, `3/4`, `3/4 NS`, a document number or a partner name.

    The counter forms are parsed rather than matched as text, because `rcpt_no` and
    `tot_rcpt_no` are integers and a `LIKE` over a cast would not use the index the receipts
    table carries for exactly this lookup.
    """
    clauses = [
        PartnerDocument.number.ilike(f"%{term}%"),
        Partner.name.ilike(f"%{term}%"),
    ]
    head, _, tail = term.partition("/")
    counter, _, _label = tail.partition(" ")
    if head.strip().isdigit():
        number = int(head.strip())
        if counter.strip().isdigit():
            clauses.append(
                (FiscalReceipt.rcpt_no == number)
                & (FiscalReceipt.tot_rcpt_no == int(counter.strip()))
            )
        else:
            clauses.append(FiscalReceipt.tot_rcpt_no == number)
            clauses.append(FiscalReceipt.invc_no == number)
    return or_(*clauses)


def receipt_detail(db: Session, company_id: int, receipt_id: int) -> ReceiptView:
    """One receipt, through the same query the listing uses.

    Deliberately not a second assembly of `ReceiptView`: the detail screen drills receipt →
    document → journal entry, and a lookup that built the view its own way would be the place
    those three ids could disagree with the list the person clicked from.
    """
    receipt = db.scalar(
        select(FiscalReceipt).where(
            FiscalReceipt.company_id == company_id, FiscalReceipt.id == receipt_id
        )
    )
    if receipt is None:
        raise NotFoundError("Receipt not found")
    for row in receipts(db, company_id, document_id=receipt.document_id):
        if row.receipt_id == receipt_id:
            return row
    raise NotFoundError("Receipt not found")


# --- The item enquiry ----------------------------------------------------------------------


@dataclass(frozen=True)
class ItemRegistrationView:
    """An item and what the authority holds about it (decision 8).

    `registered` is "has this ever been accepted", and `pending_rows` is "is a registration in
    flight". They are separate because the interesting state is both at once: an item whose
    class code changed is registered *and* queued, and a single "status" word would have to
    pick one of those to lie about.
    """

    item_id: int
    item_code: str
    item_name: str
    item_type: str
    registered: bool
    item_cd: str | None
    item_cls_cd: str | None
    item_ty_cd: str | None
    orgn_nat_cd: str | None
    pkg_unit_cd: str | None
    qty_unit_cd: str | None
    tax_ty_cd: str | None
    default_price_inclusive: Decimal | None
    barcode: str | None
    active: bool
    registered_at: datetime | None
    pending_rows: int
    last_error: str | None


def item_registrations(
    db: Session,
    company_id: int,
    *,
    registered: bool | None = None,
    search: str | None = None,
    limit: int = 500,
) -> list[ItemRegistrationView]:
    """Every item, with its registration beside it — including the ones RRA has never heard of.

    The unregistered ones are the point. Decision 3 refuses a fiscalized invoice line whose
    item has no class (`fiscal_class_missing`), so "which items would refuse a sale" is the
    question this screen answers, and a listing of `fiscal_items` alone could never answer it.
    """
    query: Select = (
        select(Item, FiscalItem)
        .outerjoin(
            FiscalItem,
            (FiscalItem.company_id == Item.company_id) & (FiscalItem.item_id == Item.id),
        )
        .where(Item.company_id == company_id)
    )
    if registered is True:
        query = query.where(FiscalItem.id.is_not(None))
    if registered is False:
        query = query.where(FiscalItem.id.is_(None))
    if search:
        term = f"%{search.strip()}%"
        query = query.where(
            or_(
                Item.code.ilike(term),
                Item.name.ilike(term),
                FiscalItem.item_cd.ilike(term),
            )
        )

    rows = list(db.execute(query.order_by(Item.code).limit(limit)))
    # The rows themselves rather than an aggregate, because the error wanted is the **latest**
    # one and `max()` over text is alphabetical: it would have shown whichever refusal happened
    # to sort last. Pending item registrations are a handful of rows even on a busy tenant.
    pending: dict[int, int] = {}
    errors: dict[int, str | None] = {}
    for row in db.scalars(
        select(FiscalOutboxRow)
        .where(
            FiscalOutboxRow.company_id == company_id,
            FiscalOutboxRow.kind == FiscalOutboxKind.ITEM,
            FiscalOutboxRow.status.in_(tuple(outbox_service.NON_TERMINAL)),
        )
        .order_by(FiscalOutboxRow.sequence_no)
    ):
        if row.source_doc_id is None:
            continue
        pending[row.source_doc_id] = pending.get(row.source_doc_id, 0) + 1
        errors[row.source_doc_id] = row.last_error

    return [
        ItemRegistrationView(
            item_id=item.id,
            item_code=item.code,
            item_name=item.name,
            item_type=str(item.item_type),
            registered=fiscal is not None and fiscal.registered_at is not None,
            item_cd=fiscal.item_cd if fiscal else None,
            item_cls_cd=fiscal.item_cls_cd if fiscal else None,
            item_ty_cd=str(fiscal.item_ty_cd) if fiscal else None,
            orgn_nat_cd=fiscal.orgn_nat_cd if fiscal else None,
            pkg_unit_cd=fiscal.pkg_unit_cd if fiscal else None,
            qty_unit_cd=fiscal.qty_unit_cd if fiscal else None,
            tax_ty_cd=str(fiscal.tax_ty_cd) if fiscal else None,
            default_price_inclusive=fiscal.dft_prc if fiscal else None,
            barcode=fiscal.bcd if fiscal else None,
            active=fiscal.use_yn if fiscal else False,
            registered_at=fiscal.registered_at if fiscal else None,
            pending_rows=pending.get(item.id, 0),
            last_error=errors.get(item.id),
        )
        for item, fiscal in rows
    ]


# --- The receipts listing: the tie between EBM and the sales ledger (P7 step 8) ------------
#
# The one screen on this phase that is **not** a listing of receipts. A listing of receipts is
# what the enquiry above is for; this is the report an accountant closes a month with, and its
# job is to put two independently-derived figures beside each other and name every document
# that is on one side and not the other.
#
# **The two sides are derived differently on purpose.** The receipts side reads
# `fiscal_receipts.request` through the adapter, exactly as the Z does, so a day's listing and
# that day's Z are the same arithmetic over the same rows and must agree to the cent. The
# ledger side reads `partner_documents`, which knows nothing about RRA. Agreeing is therefore
# evidence; a tie whose two sides came from one query would agree whatever had gone wrong.
#
# **They are also indexed differently, and that is not a defect.** A receipt belongs to the day
# the *device signed it* (`sdc_datetime` — the same axis a Z is cut on), and a document belongs
# to the day it is *dated* (`document_date`). A sale keyed on the 31st and drained on the 1st
# sits on opposite sides of a month end, and so does the `NR` receipt for a reversal of an
# invoice from last week (decision 3: RRA cannot un-sign a sale, so the reversal is signed
# today against a document dated then). Both land in the asymmetry lists below with the reason
# showing, which is the answer an accountant needs; an axis fudged to make the totals match
# would hide exactly the rows worth looking at.


@dataclass(frozen=True)
class ListingReceipt:
    """One signed receipt on the listing, with what it declared."""

    receipt_id: int
    document_id: int
    document_number: str
    document_date: date
    partner_name: str
    receipt_type: FiscalReceiptType
    receipt_number: str
    invc_no: int
    sdc_datetime: datetime
    declared_gross: Decimal
    declared_tax: Decimal
    #: The ledger's own figure for the same document, or `None` when the document is gone.
    #: Beside the declaration rather than instead of it — the two differ by the wire residue
    #: `DailyFigures.declared_less_posted` names, and a reader comparing a row to the sales
    #: ledger wants both numbers rather than a subtraction somebody else performed.
    posted_base_total: Decimal | None
    #: Set when this receipt's document is **not** in the ledger side of this range, with why.
    outside_range: str | None = None


@dataclass(frozen=True)
class ListingDocument:
    """One sales-ledger document with no receipt in this range, and what it is waiting on."""

    document_id: int
    number: str
    document_date: date
    partner_name: str
    kind: DocumentKind
    base_total_amount: Decimal
    #: The queue row's status (`queued`, `failed`, `unknown`, `needs_receipt`, `cancelled`), or
    #: `unqueued` when nothing was ever enqueued for it. The word an operator acts on: a
    #: `failed` row is somebody's decision, a `queued` one is a drain away.
    reason: str


@dataclass(frozen=True)
class ReceiptListing:
    """Per device, per range: what RRA signed, what the ledger holds, and the difference."""

    device_id: int
    device_label: str
    sdc_id: str | None
    mrc_no: str | None
    date_from: date
    date_to: date
    ns_count: int
    ns_gross: Decimal
    ns_tax: Decimal
    nr_count: int
    nr_gross: Decimal
    nr_tax: Decimal
    ledger_invoice_count: int
    ledger_invoice_total: Decimal
    ledger_credit_note_count: int
    ledger_credit_note_total: Decimal
    receipts: tuple[ListingReceipt, ...]
    only_in_ledger: tuple[ListingDocument, ...]
    only_on_receipts: tuple[ListingReceipt, ...]
    #: Set when the listing was asked for **one Z** rather than a date range: its number, and
    #: the counter window it owns. The screen states them because "these are the receipts that
    #: Z counted" is the whole claim the accountant is checking, and a heading that said only
    #: the dates would be the range that stopped deciding membership at 0026.
    report_no: int | None = None
    report_number: str | None = None
    from_key: int | None = None
    to_key: int | None = None

    @property
    def declared_net(self) -> Decimal:
        """Sales less refunds, as declared — the figure a day's Z calls `net_gross`."""
        return self.ns_gross - self.nr_gross

    @property
    def ledger_net(self) -> Decimal:
        return self.ledger_invoice_total - self.ledger_credit_note_total

    @property
    def difference(self) -> Decimal:
        """Declared less posted. Zero is the happy month; the lists below explain the rest."""
        return self.declared_net - self.ledger_net


def receipt_listing(
    db: Session,
    company_id: int,
    device_id: int,
    *,
    date_from: date,
    date_to: date,
    report_no: int | None = None,
) -> ReceiptListing:
    """The listing and its tie, for one device over one closed date range — or over one Z.

    One device rather than all of them, because the counters this report states are per device
    (decision 5) and a total across two devices is a number with no receipt behind it. The
    ledger side is narrowed to the device's **branch** for the same reason: a device is a
    branch's, so the sales it is answerable for are that branch's sales.

    **`report_no` is the "this Z" filter** (0026). A Z owns its receipts by counter now, and a
    date range cannot express that: a receipt signed either side of midnight, or across the
    skew between RRA's clock and Vinea's, is in a Z whose dates do not contain it. Asked for a
    Z, the receipts side is exactly that Z's members — `daily.receipts_of`, the same function
    the close itself used, so the two cannot disagree — and the dates become the Z's own span,
    which is what the ledger side and the asymmetry lists are cut on. Without it the accountant's
    tie compares a Z's total to a *different* set of receipts and calls the difference a
    finding.
    """
    device = db.scalar(
        select(FiscalDevice).where(
            FiscalDevice.company_id == company_id, FiscalDevice.id == device_id
        )
    )
    if device is None:
        raise NotFoundError("Fiscal device not found")
    branch = db.scalar(
        select(Branch).where(Branch.company_id == company_id, Branch.id == device.branch_id)
    )

    if report_no is not None:
        report = db.scalar(
            select(FiscalDailyReport).where(
                FiscalDailyReport.company_id == company_id,
                FiscalDailyReport.device_id == device_id,
                FiscalDailyReport.report_no == report_no,
            )
        )
        if report is None:
            raise NotFoundError("Fiscal daily report not found")
        members = daily.membership_of(db, company_id, report)
        signed = daily.receipts_of(db, company_id, device_id, members)
        date_from = report.from_at.date()
        date_to = report.to_at.date()
        zed = (report.report_no, report.number, members.from_key, members.to_key)
    else:
        zed = (None, None, None, None)
        from_at = datetime.combine(date_from, time.min, tzinfo=UTC)
        to_at = datetime.combine(date_to, time.max, tzinfo=UTC)
        signed = list(
            db.scalars(
                select(FiscalReceipt)
                .where(
                    FiscalReceipt.company_id == company_id,
                    FiscalReceipt.device_id == device_id,
                    FiscalReceipt.sdc_datetime >= from_at,
                    FiscalReceipt.sdc_datetime <= to_at,
                )
                .order_by(FiscalReceipt.tot_rcpt_no, FiscalReceipt.id)
            )
        )

    documents = _documents(db, company_id, {receipt.document_id for receipt in signed})
    ledger = _sales_ledger(
        db, company_id, branch_id=device.branch_id, date_from=date_from, date_to=date_to
    )
    partners = _partners(
        db,
        company_id,
        {document.partner_id for document in documents.values()}
        | {document.partner_id for document in ledger},
    )
    reader = registry.adapter_for_company(db, company_id)

    in_ledger = {document.id for document in ledger}
    rows: list[ListingReceipt] = []
    counted: dict[FiscalReceiptType, list[Decimal]] = {
        FiscalReceiptType.NORMAL_SALE: [Decimal(0), Decimal(0), Decimal(0)],
        FiscalReceiptType.NORMAL_REFUND: [Decimal(0), Decimal(0), Decimal(0)],
    }
    for receipt in signed:
        declared = reader.normalize_declared_totals(receipt.request, receipt.response)
        document = documents.get(receipt.document_id)
        bucket = counted.setdefault(receipt.receipt_type, [Decimal(0), Decimal(0), Decimal(0)])
        bucket[0] += 1
        bucket[1] += Decimal(0) if declared is None else declared.gross
        bucket[2] += Decimal(0) if declared is None else declared.tax
        rows.append(
            ListingReceipt(
                receipt_id=receipt.id,
                document_id=receipt.document_id,
                document_number="" if document is None else document.number,
                document_date=(
                    receipt.sdc_datetime.date() if document is None else document.document_date
                ),
                partner_name=(
                    "" if document is None else partners.get(document.partner_id, "")
                ),
                receipt_type=receipt.receipt_type,
                receipt_number=receipt_number(receipt),
                invc_no=receipt.invc_no,
                sdc_datetime=receipt.sdc_datetime,
                declared_gross=Decimal(0) if declared is None else declared.gross,
                declared_tax=Decimal(0) if declared is None else declared.tax,
                posted_base_total=None if document is None else document.base_total_amount,
                outside_range=_outside_range(document, in_ledger, date_from, date_to),
            )
        )

    with_receipts = {receipt.document_id for receipt in signed}
    missing = [document for document in ledger if document.id not in with_receipts]
    statuses = _outbox_statuses(db, company_id, [document.id for document in missing])
    sale = counted[FiscalReceiptType.NORMAL_SALE]
    refund = counted[FiscalReceiptType.NORMAL_REFUND]
    return ReceiptListing(
        device_id=device.id,
        device_label="" if branch is None else f"{branch.code} — {branch.name}",
        sdc_id=device.sdc_id,
        mrc_no=device.mrc_no,
        date_from=date_from,
        date_to=date_to,
        ns_count=int(sale[0]),
        ns_gross=sale[1],
        ns_tax=sale[2],
        nr_count=int(refund[0]),
        nr_gross=refund[1],
        nr_tax=refund[2],
        ledger_invoice_count=sum(1 for d in ledger if d.kind is DocumentKind.INVOICE),
        ledger_invoice_total=sum(
            (d.base_total_amount for d in ledger if d.kind is DocumentKind.INVOICE), Decimal(0)
        ),
        ledger_credit_note_count=sum(
            1 for d in ledger if d.kind is DocumentKind.CREDIT_NOTE
        ),
        ledger_credit_note_total=sum(
            (
                d.base_total_amount
                for d in ledger
                if d.kind is DocumentKind.CREDIT_NOTE
            ),
            Decimal(0),
        ),
        receipts=tuple(rows),
        only_in_ledger=tuple(
            ListingDocument(
                document_id=document.id,
                number=document.number,
                document_date=document.document_date,
                partner_name=partners.get(document.partner_id, ""),
                kind=document.kind,
                base_total_amount=document.base_total_amount,
                reason=statuses.get(document.id, "unqueued"),
            )
            for document in missing
        ),
        only_on_receipts=tuple(row for row in rows if row.outside_range is not None),
        report_no=zed[0],
        report_number=zed[1],
        from_key=zed[2],
        to_key=zed[3],
    )


def _outside_range(
    document: PartnerDocument | None,
    in_ledger: set[int],
    date_from: date,
    date_to: date,
) -> str | None:
    """Why this receipt's document is not on the ledger side, or `None` when it is.

    Three ways a receipt can have no counterpart, and they are different problems:
    `document_gone` is a receipt whose document no longer resolves, `dated_outside` is the
    ordinary month-end straddle and the refund of an older invoice, and `other_branch` is a
    device that signed for a sale posted on a branch it does not answer for — which should
    never happen and is worth a line on the screen if it ever does.
    """
    if document is None:
        return "document_gone"
    if document.id in in_ledger:
        return None
    if not (date_from <= document.document_date <= date_to):
        return "dated_outside"
    return "other_branch"


def _sales_ledger(
    db: Session,
    company_id: int,
    *,
    branch_id: int,
    date_from: date,
    date_to: date,
) -> list[PartnerDocument]:
    """The AR invoices and credit notes a branch posted in a range — the ledger's own answer.

    **Reversed documents are included.** A reversed invoice was posted, was signed, and its
    reversal is a second document with an `NR` receipt of its own; dropping it would take the
    sale off the ledger side while leaving both receipts on the other, and turn a tie that
    balances into one that does not.
    """
    return list(
        db.scalars(
            select(PartnerDocument)
            .where(
                PartnerDocument.company_id == company_id,
                PartnerDocument.role == PartnerRole.AR,
                PartnerDocument.kind.in_(
                    (DocumentKind.INVOICE, DocumentKind.CREDIT_NOTE)
                ),
                PartnerDocument.branch_id == branch_id,
                PartnerDocument.document_date >= date_from,
                PartnerDocument.document_date <= date_to,
            )
            .order_by(PartnerDocument.document_date, PartnerDocument.id)
        )
    )


def _outbox_statuses(
    db: Session, company_id: int, document_ids: Sequence[int]
) -> dict[int, str]:
    """The **latest** queue row's status per document, for the documents with no receipt.

    Latest rather than any: a sale that failed, was reversed and had its row cancelled has two
    rows, and the one that says what is true now is the last. `max(id)` is the send order the
    outbox is written in.
    """
    if not document_ids:
        return {}
    latest = (
        select(
            FiscalOutboxRow.source_doc_id.label("document_id"),
            func.max(FiscalOutboxRow.id).label("row_id"),
        )
        .where(
            FiscalOutboxRow.company_id == company_id,
            FiscalOutboxRow.source_doc_type.in_(
                (outbox_service.DOCUMENT_SOURCE, outbox_service.REVERSAL_SOURCE)
            ),
            FiscalOutboxRow.source_doc_id.in_(list(document_ids)),
        )
        .group_by(FiscalOutboxRow.source_doc_id)
        .subquery()
    )
    rows = db.execute(
        select(latest.c.document_id, FiscalOutboxRow.status).join(
            FiscalOutboxRow, FiscalOutboxRow.id == latest.c.row_id
        )
    ).all()
    return {int(document_id): str(status) for document_id, status in rows}


# --- Shared lookups ------------------------------------------------------------------------


def _documents(
    db: Session, company_id: int, ids: set[int | None]
) -> dict[int, PartnerDocument]:
    wanted = [value for value in ids if value is not None]
    if not wanted:
        return {}
    return {
        row.id: row
        for row in db.scalars(
            select(PartnerDocument).where(
                PartnerDocument.company_id == company_id, PartnerDocument.id.in_(wanted)
            )
        )
    }


def _partners(db: Session, company_id: int, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return {
        row.id: row.name
        for row in db.scalars(
            select(Partner).where(Partner.company_id == company_id, Partner.id.in_(ids))
        )
    }


__all__ = [
    "ItemRegistrationView",
    "ListingDocument",
    "ListingReceipt",
    "QueueActionEntry",
    "QueueDeviceView",
    "QueueRowDetail",
    "QueueRowView",
    "ReceiptListing",
    "ReceiptView",
    "item_registrations",
    "queue_row",
    "queue_rows",
    "queue_summary",
    "receipt_detail",
    "receipt_listing",
    "receipt_number",
    "receipts",
]
