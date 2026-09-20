"""The receipt as a document prints it, and the copy counter (decision 11).

**The layout is the screen's; the facts are this module's.** Step 7 renders the CIS §13/§14
receipt — the taxpayer block, the lines, the totals, the SDC block and the QR — and everything
it needs to do that is assembled here, neutral, so that a second country's receipt is a
different template over the same shape rather than a second reader of somebody's field names.
The class totals and the item count come through the adapter's `normalize_declared_totals`,
which is the same read half of the boundary the daily report uses.

Two rules, and both are refusals rather than conventions:

1. **Print is refused until the receipt exists** (`fiscal_receipt_pending`, CIS §10). A
   document on a fiscalized company whose queue row has not been signed has nothing to print:
   the paper carries a receipt number the authority issued, and there is no draft form of one.
   The button shows the queue status instead, which is why the refusal carries it.
2. **A second print is a copy** (§11, §15). `copy_count` is incremented and audited, and
   **nothing is sent to RRA** — v1.0.5 sends `salesTyCd N` only, and a copy is a print of a
   sale already declared, not a second sale. The Z counts copies separately for exactly this
   reason.

A non-fiscalized company has no receipt and no refusal: `receipt_block` returns `None` and the
P4 layout prints, which is what decision 11's last sentence asks for.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import NotFoundError
from app.fiscal import outbox as outbox_service
from app.fiscal import registry
from app.fiscal.protocol import DeclaredLine
from app.kernel.errors import LedgerStateError
from app.models.company import Branch, Company
from app.models.fiscalization import (
    FiscalDevice,
    FiscalOutboxKind,
    FiscalReceipt,
    FiscalReceiptType,
    PaymentMethod,
)
from app.models.partner import Partner
from app.models.subledger import PartnerDocument
from app.models.user import User
from app.services.audit import record_audit

MONEY_ZERO = Decimal("0.00")


@dataclass(frozen=True)
class ReceiptClassLine:
    """One programmed rate on the printed receipt.

    Every rate greater than zero prints on every receipt and a zero rate prints only when it
    was used (CIS §7.22–7.23), so the decision of *which* lines appear is made here — the
    template prints what it is given.
    """

    tax_class: str
    rate: Decimal
    taxable: Decimal
    tax: Decimal
    used: bool


@dataclass(frozen=True)
class ReceiptBlock:
    """Everything the CIS layout prints, with nobody's field names in it."""

    document_id: int
    document_number: str
    document_date: date
    receipt_id: int
    receipt_type: FiscalReceiptType
    #: `NS` or `NR` — the label §5 gives the receipt, printed beside the counters.
    label: str
    rcpt_no: int
    tot_rcpt_no: int
    receipt_number: str
    invc_no: int
    #: The original receipt's total counter on a refund — `REF. NORMAL RECEIPT#` (§14).
    refund_of_tot_rcpt_no: int | None
    sdc_id: str
    mrc_no: str | None
    sdc_datetime: datetime
    intrl_data: str
    rcpt_sign: str
    qr_payload: str
    taxpayer_name: str
    taxpayer_tin: str | None
    branch_name: str
    branch_address: str | None
    customer_name: str
    customer_tin: str | None
    payment_method: PaymentMethod | None
    purchase_code: str | None
    items_count: int
    discount_total: Decimal
    taxable_total: Decimal
    tax_total: Decimal
    gross_total: Decimal
    classes: tuple[ReceiptClassLine, ...]
    #: What the receipt sold, as the authority received it. **The declaration's lines, not the
    #: document's**: a USD invoice is declared in RWF from its frozen base amounts (decision 3),
    #: so printing the document's own line amounts would put dollars under a franc total. They
    #: also carry the item's name, which a ledger line keyed with no typed description does not.
    lines: tuple[DeclaredLine, ...]
    #: True on every print after the first. The template adds `COPY` and
    #: `THIS IS NOT AN OFFICIAL RECEIPT`; the count is what the Z reports.
    is_copy: bool
    copy_count: int


def receipt_block(
    db: Session,
    company_id: int,
    document_id: int,
    *,
    as_copy: bool = False,
    receipt_id: int | None = None,
) -> ReceiptBlock | None:
    """What this document prints, or `None` when it is not a fiscal receipt at all.

    `None` and a refusal are different answers and the caller needs both: `None` is "this
    company does not fiscalize, print the P4 layout", and `fiscal_receipt_pending` is "this one
    does, and the authority has not signed yet".
    """
    document = _document(db, company_id, document_id)
    receipt = _receipt(db, company_id, document, receipt_id)
    if receipt is None:
        _refuse_or_pass(db, company_id, document)
        return None
    return _block(db, company_id, document, receipt, as_copy=as_copy)


def record_copy(
    db: Session,
    company_id: int,
    document_id: int,
    *,
    actor: User,
    request: Request | None = None,
    receipt_id: int | None = None,
) -> ReceiptBlock:
    """A reprint. The counter moves, the audit records who, and RRA hears nothing.

    `copy_count` is the only mutable column on `fiscal_receipts` and is excluded from the
    immutability trigger by name — a reprint stays auditable without making the receipt
    editable.
    """
    document = _document(db, company_id, document_id)
    receipt = _receipt(db, company_id, document, receipt_id)
    if receipt is None:
        _refuse_or_pass(db, company_id, document)
        raise LedgerStateError(
            f"{document.number} has no fiscal receipt to copy.",
            code="fiscal_receipt_missing",
        )
    receipt.copy_count += 1
    db.flush()
    record_audit(
        db,
        company_id=company_id,
        action="fiscal_outbox.print_copy",
        entity="fiscal_outbox",
        entity_id=receipt.outbox_id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        after={
            "document_id": document.id,
            "document_number": document.number,
            "receipt_id": receipt.id,
            "copy_count": receipt.copy_count,
        },
        request=request,
    )
    return _block(db, company_id, document, receipt, as_copy=True)


# --- The pieces -----------------------------------------------------------------------------


def _document(db: Session, company_id: int, document_id: int) -> PartnerDocument:
    document = db.scalar(
        select(PartnerDocument).where(
            PartnerDocument.company_id == company_id, PartnerDocument.id == document_id
        )
    )
    if document is None:
        raise NotFoundError("Document not found")
    return document


def _receipt(
    db: Session, company_id: int, document: PartnerDocument, receipt_id: int | None = None
) -> FiscalReceipt | None:
    """The receipt to print — a named one, or by default the document's **latest**.

    A signed sale that was reversed holds two receipts: the `NS` it was declared under and the
    `NR` that reversed it (decision 7 — reversing a fiscalized invoice queues a full refund
    rather than cancelling the sale). Both are legal documents the customer is owed, and
    **both are printable**: the screen lists them and prints whichever is chosen.

    The default is the latest, which is the refund when there is one, because that is what the
    document most recently became. It is deliberately **not** `document.fiscal_receipt_id`:
    that column is the document's own receipt — the sale — and the drainer leaves it pointing
    at the `NS` on purpose, so the subledger's link and the open-item history keep naming the
    sale. The two answer different questions and only one of them is "what comes out of the
    printer now".

    A `receipt_id` that is not this document's is `None` rather than somebody else's receipt.
    """
    query = select(FiscalReceipt).where(
        FiscalReceipt.company_id == company_id,
        FiscalReceipt.document_id == document.id,
    )
    if receipt_id is not None:
        return db.scalars(query.where(FiscalReceipt.id == receipt_id)).first()
    return db.scalars(query.order_by(FiscalReceipt.tot_rcpt_no.desc()).limit(1)).first()


def _refuse_or_pass(db: Session, company_id: int, document: PartnerDocument) -> None:
    """Refuse the print when a receipt is *owed*, and say nothing when none ever will be.

    The discriminator is a queue row, not the company's settings: a sale posted before the
    device was switched on has no row and no receipt and is not pending anything, while one
    posted after has a row whose status is the whole of what the person needs to read.
    """
    rows = [
        row
        for source in (outbox_service.DOCUMENT_SOURCE, outbox_service.REVERSAL_SOURCE)
        for row in outbox_service.rows_for_document(
            db, company_id, source_doc_type=source, source_doc_id=document.id
        )
        if row.kind in (FiscalOutboxKind.SALE, FiscalOutboxKind.REFUND)
    ]
    live = sorted(
        (row for row in rows if row.status in outbox_service.NON_TERMINAL),
        key=lambda row: row.sequence_no,
    )
    if not live:
        return
    row = live[0]
    raise LedgerStateError(
        f"{document.number} has not been signed by RRA yet — its EBM queue row is "
        f"'{row.status}'. A fiscal receipt cannot be printed before the authority issues it.",
        code="fiscal_receipt_pending",
        field_errors={"fiscal_status": [str(row.status)]},
    )


def _block(
    db: Session,
    company_id: int,
    document: PartnerDocument,
    receipt: FiscalReceipt,
    *,
    as_copy: bool,
) -> ReceiptBlock:
    company = db.get(Company, company_id)
    device = db.scalar(
        select(FiscalDevice).where(
            FiscalDevice.company_id == company_id, FiscalDevice.id == receipt.device_id
        )
    )
    branch = (
        db.scalar(
            select(Branch).where(
                Branch.company_id == company_id, Branch.id == device.branch_id
            )
        )
        if device is not None
        else None
    )
    partner = db.scalar(
        select(Partner).where(
            Partner.company_id == company_id, Partner.id == document.partner_id
        )
    )
    adapter = registry.adapter_for(company.fiscal_country if company else None)
    declared = adapter.normalize_declared_totals(receipt.request, receipt.response)
    declared_lines = adapter.normalize_declared_lines(receipt.request)

    classes = _class_lines(declared)
    original = (
        db.scalars(
            select(FiscalReceipt)
            .where(
                FiscalReceipt.company_id == company_id,
                FiscalReceipt.device_id == receipt.device_id,
                FiscalReceipt.invc_no == receipt.org_invc_no,
                FiscalReceipt.receipt_type == FiscalReceiptType.NORMAL_SALE,
            )
            .limit(1)
        ).first()
        if receipt.org_invc_no is not None
        else None
    )
    return ReceiptBlock(
        document_id=document.id,
        document_number=document.number,
        document_date=document.document_date,
        receipt_id=receipt.id,
        receipt_type=receipt.receipt_type,
        label=str(receipt.receipt_type),
        rcpt_no=receipt.rcpt_no,
        tot_rcpt_no=receipt.tot_rcpt_no,
        receipt_number=f"{receipt.rcpt_no}/{receipt.tot_rcpt_no} {receipt.receipt_type}",
        invc_no=receipt.invc_no,
        refund_of_tot_rcpt_no=original.tot_rcpt_no if original is not None else None,
        sdc_id=receipt.sdc_id,
        mrc_no=receipt.mrc_no,
        sdc_datetime=receipt.sdc_datetime,
        intrl_data=receipt.intrl_data,
        rcpt_sign=receipt.rcpt_sign,
        qr_payload=receipt.qr_payload,
        taxpayer_name=company.name if company else "",
        taxpayer_tin=company.tin if company else None,
        branch_name=branch.name if branch else "",
        branch_address=branch.address if branch else None,
        customer_name=partner.name if partner else "",
        customer_tin=partner.tin if partner else None,
        payment_method=document.payment_method,
        purchase_code=document.purchase_code,
        items_count=declared.item_count if declared else 0,
        discount_total=declared.discount if declared else MONEY_ZERO,
        taxable_total=declared.taxable if declared else MONEY_ZERO,
        tax_total=declared.tax if declared else MONEY_ZERO,
        gross_total=declared.gross if declared else MONEY_ZERO,
        classes=classes,
        lines=declared_lines,
        is_copy=as_copy or receipt.copy_count > 0,
        copy_count=receipt.copy_count,
    )


def _class_lines(declared) -> tuple[ReceiptClassLine, ...]:  # noqa: ANN001 - DeclaredTotals|None
    """Every programmed rate above zero, plus the zero rates this receipt actually used.

    CIS §7.22–7.23, and the selection is the adapter's: `normalize_declared_totals` keeps a
    class that carries amounts **or** a rate above zero, and drops a zero-rate class nothing
    was sold under. The rates come from the declaration rather than from a constant, because
    what is "programmed" is what the device was initialized with and a hard-coded 18 would be
    wrong the day RRA changes it.

    `used` is what the template needs to tell "TOTAL B-18.00%: 0" — a rate that must print
    whether or not anything was sold under it — from a line the receipt has figures for.
    """
    if declared is None:
        return ()
    return tuple(
        ReceiptClassLine(
            tax_class=row.tax_class,
            rate=row.rate,
            taxable=row.taxable,
            tax=row.tax,
            used=row.taxable != MONEY_ZERO or row.tax != MONEY_ZERO,
        )
        for row in sorted(declared.classes, key=lambda row: row.tax_class)
    )


__all__ = ["ReceiptBlock", "ReceiptClassLine", "receipt_block", "record_copy"]
