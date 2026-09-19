"""Fiscalization API schemas (P7 step 1).

**No schema here carries a device key**, and that is load-bearing rather than an oversight to
be tidied later: `cmc_key`, `intrl_key` and `sign_key` are the only secrets this phase holds,
and the thing that keeps them out of a response is that no response model has a field for one.
`tests/fiscal/test_key_redaction.py` asserts it over the model rather than over one endpoint's
output, so a second endpoint serialising a device cannot reintroduce it.

`has_keys` is what a screen actually needs — "is this device holding its keys" — and answers it
without going near the values.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.fiscalization import (
    FiscalDeviceStatus,
    FiscalEnvironment,
    FiscalFeedDecision,
    FiscalImportStatus,
    FiscalOutboxKind,
    FiscalOutboxStatus,
    FiscalProfile,
    FiscalReceiptType,
    PaymentMethod,
)


class DeviceCreate(BaseModel):
    branch_id: int
    profile: FiscalProfile
    environment: FiscalEnvironment = FiscalEnvironment.TEST
    base_url: str = Field(min_length=1, max_length=300)
    #: The serial the owner registered on the authority's portal.
    dvc_srl_no: str = Field(min_length=1, max_length=100)
    #: The authority's branch identifier; the head office is `00`.
    bhf_id: str = Field(default="00", min_length=2, max_length=2)


class DeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    branch_id: int
    profile: FiscalProfile
    environment: FiscalEnvironment
    base_url: str
    tin: str | None
    bhf_id: str
    dvc_srl_no: str
    mrc_no: str | None
    sdc_id: str | None
    dvc_id: str | None
    status: FiscalDeviceStatus
    watermarks: dict[str, str] = Field(default_factory=dict)
    last_success_at: datetime | None
    last_error: str | None
    #: Whether the device holds its three keys — never which, and never their values.
    has_keys: bool = False


class DeviceSuspend(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class DeviceSyncResult(BaseModel):
    """What a sync brought back. The watermark is included because an operator staring at a
    stale code table needs to see what the device thinks it last fetched."""

    device_id: int
    kind: str
    rows: int
    watermark: str | None = None


class TinLookupRead(BaseModel):
    tin: str
    found: bool
    name: str | None = None
    status: str | None = None


def device_read(device) -> DeviceRead:  # noqa: ANN001 - a FiscalDevice; typing it is a cycle
    """The one place a device becomes a response.

    A function rather than `DeviceRead.model_validate(device)` at four call sites, so that
    `has_keys` is computed once — and so that adding a field to the model cannot accidentally
    pick up a column by name.
    """
    payload = DeviceRead.model_validate(device)
    payload.has_keys = bool(device.cmc_key or device.intrl_key or device.sign_key)
    return payload


class DrainResult(BaseModel):
    """What one drain pass did. `rows` counts attempts, `sent` the ones RRA signed — the two
    differ exactly when a device is stuck, which is the number an operator wants."""

    rows: int
    sent: int
    outcomes: list[dict] = []


# --- The purchase feed and the import register (P7 step 3) ----------------------------------


class FeedRowRead(BaseModel):
    """One purchase the authority holds against this taxpayer.

    `payload` is not here and will not be: it is RRA's own record, and the queue-row detail
    screen is where a person reads a payload (redacted, beside the response). What a feed
    listing needs is who, how much, and what was decided.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int
    spplr_tin: str
    spplr_nm: str | None
    spplr_bhf_id: str | None
    spplr_invc_no: int
    sales_dt: date | None
    total_taxable_amount: Decimal
    total_tax_amount: Decimal
    total_amount: Decimal
    fetched_at: datetime
    decision: FiscalFeedDecision
    decided_at: datetime | None
    ap_document_id: int | None


class FeedAccept(BaseModel):
    """`ap_document_id` is optional and is the whole of the no-double-registration rule: a
    purchase this company also keyed is linked, and the link decides which of the two
    registrations RRA keeps (`app/fiscal/feed.accept`)."""

    ap_document_id: int | None = None


class FeedDecisionRead(BaseModel):
    """What a decision did — including the one case where it deliberately queued nothing."""

    row: FeedRowRead
    #: The confirmation's queue row, or `None` when the linked document's registration is
    #: already with RRA and confirming would register the same invoice twice.
    confirmation_row_id: int | None = None
    #: The document's own registration, when the decision cancelled it in favour of this one.
    cancelled_row_id: int | None = None
    note: str = ""


class ImportDeclarationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int
    task_cd: str
    dcl_no: str
    dcl_de: date | None
    item_seq: int
    hs_cd: str | None
    item_nm: str | None
    orgn_nat_cd: str | None
    pkg: Decimal | None
    pkg_unit_cd: str | None
    qty: Decimal | None
    qty_unit_cd: str | None
    spplr_nm: str | None
    agnt_nm: str | None
    invc_fcur_amt: Decimal | None
    invc_fcur_cd: str | None
    invc_fcur_exc_rt: Decimal | None
    fetched_at: datetime
    status: FiscalImportStatus
    item_id: int | None
    decided_at: datetime | None


class ImportApprove(BaseModel):
    """The item the declared line became. Required — it is the whole content of the
    acknowledgment RRA is asking for."""

    item_id: int
    note: str | None = Field(default=None, max_length=400)


class ImportReject(BaseModel):
    note: str | None = Field(default=None, max_length=400)


# --- X and Z (P7 decision 11) -----------------------------------------------------------------


class ClassTotalsRead(BaseModel):
    """One tax class of a day, split by receipt type — §19.1 prints the two apart."""

    taxable_ns: Decimal
    tax_ns: Decimal
    taxable_nr: Decimal
    tax_nr: Decimal
    rate: Decimal


class DailyFiguresRead(BaseModel):
    ns_count: int
    ns_gross: Decimal
    nr_count: int
    nr_gross: Decimal
    net_gross: Decimal
    total_tax: Decimal
    #: Σ of the receipts' line quantities, split NS from NR — how many things were sold and how
    #: many came back. The receipt's own line count is on the receipt, not here.
    items_ns: Decimal
    items_nr: Decimal
    copies_count: int
    copies_gross: Decimal
    #: §19.1 prints the day's discounts.
    discounts: Decimal
    #: The same documents' ledger value, and the wire-versus-ledger residue between the two.
    #: Expected rather than a defect (decision 6), and named so a month-end reconciliation
    #: against the VAT return does not meet an unexplained franc.
    posted_net: Decimal
    declared_less_posted: Decimal
    #: What the device was still holding when the report was taken. A queued row is not a
    #: receipt, so it is no part of the totals — and a Z that closed over one says so.
    queued_rows: int
    classes: dict[str, ClassTotalsRead]
    #: Sales by method, and refunds beside them. Never netted — see `DailyFigures`.
    by_payment_method: dict[str, Decimal]
    refunds_by_payment_method: dict[str, Decimal]


class DailyReportRead(BaseModel):
    """An X or a Z in the same shape. They differ by whether anybody stored it."""

    device_id: int
    kind: str
    from_at: datetime
    to_at: datetime
    figures: DailyFiguresRead
    number: str | None = None
    report_no: int | None = None


# --- The enquiries and listings (P7 step 5) -------------------------------------------------
#
# The queue, the receipts and the item registrations. The screens arrive at steps 7 and 8
# (Transactions → Tax → Fiscal queue, Enquiries → Tax → Fiscal receipts), and none of these is
# a mutating endpoint except the three queue actions and the copy print, which carry their
# `GAP (P7, step 7)` lines until those screens land.


class QueueStatusCountRead(BaseModel):
    status: FiscalOutboxStatus
    rows: int


class QueueHeadRead(BaseModel):
    """The row at the front of a device's queue. Every row behind it is waiting on this one."""

    row_id: int
    kind: FiscalOutboxKind
    status: FiscalOutboxStatus
    sequence_no: int
    attempts: int
    next_attempt_at: datetime | None
    last_result_cd: str | None
    last_error: str | None


class QueueDeviceRead(BaseModel):
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
    #: A device whose oldest unsent row is over 24 hours old (VSDC §2.2 item 4).
    offline: bool
    oldest_queued_at: datetime | None
    oldest_queued_age_seconds: int | None
    pending_rows: int
    #: The head is in a state that will not move by itself — a person has to act.
    blocked: bool
    counts: list[QueueStatusCountRead]
    head: QueueHeadRead | None


class QueueRowRead(BaseModel):
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


class QueueActionRead(BaseModel):
    at: datetime
    action: str
    actor_email: str | None
    detail: dict


class QueueRowDetailRead(BaseModel):
    """The row, what was sent, what came back, and who has touched it.

    `request` and `response` are redacted twice — at enqueue and again here — so that a screen
    showing a payload can never be the place a device key escapes.
    """

    row: QueueRowRead
    request: dict
    response: dict | None
    resolved_by_email: str | None
    resolution_note: str | None
    actions: list[QueueActionRead]


class QueueAttachReceipt(BaseModel):
    """The six fields off the authority's portal, plus the note that says who read them.

    `fields` is passed to the adapter untouched: an operator copying a sales response is keying
    one, and a second parser here would eventually disagree with `normalize_receipt`.
    """

    fields: dict
    note: str = Field(min_length=1, max_length=500)


class ReceiptRead(BaseModel):
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
    #: `rcptNo/totRcptNo LABEL`, as the paper prints it — what somebody holding one will type.
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


class ItemRegistrationRead(BaseModel):
    """An item and what RRA holds about it. The unregistered ones are why this exists: a
    fiscalized sale of an item with no class is refused, so "which items would refuse" is the
    question, and a listing of `fiscal_items` alone could never answer it."""

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


class ReceiptClassLineRead(BaseModel):
    tax_class: str
    rate: Decimal
    taxable: Decimal
    tax: Decimal
    used: bool


class ReceiptLineRead(BaseModel):
    """One line of the receipt, as the authority received it — **in base currency**, because
    that is what was declared."""

    sequence: int
    name: str
    quantity: Decimal
    unit_price: Decimal
    discount_percent: Decimal
    discount_amount: Decimal
    taxable: Decimal
    tax: Decimal
    tax_class: str


class ReceiptBlockRead(BaseModel):
    """What the CIS §13/§14 layout prints. The template is step 7's; these are the facts."""

    document_id: int
    document_number: str
    document_date: date
    receipt_id: int
    receipt_type: FiscalReceiptType
    label: str
    rcpt_no: int
    tot_rcpt_no: int
    receipt_number: str
    invc_no: int
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
    classes: list[ReceiptClassLineRead]
    lines: list[ReceiptLineRead]
    #: True on every print after the first: the template adds `COPY` and the warning line.
    is_copy: bool
    copy_count: int


# --- What a posting screen needs, and nothing more (P7 step 7) -------------------------------


class RefundReasonRead(BaseModel):
    """One of the authority's published refund reasons, as a code and a name.

    Read out of `fiscal_codes` rather than off the Rwanda enum, so the picker on a credit note
    is fed by the same synced table every other code picker reads and no country's vocabulary
    leaves `app/fiscal/`.
    """

    code: str
    name: str


class DocumentContextRead(BaseModel):
    """The two facts an AR/AP posting screen needs before it can draw its fiscal fields.

    Deliberately its own endpoint rather than a corner of the device listing. Keying an invoice
    is not a fiscal-setup job and not a fiscal-reporting one: the seeded **Sales Manager** role
    holds `ar:transactions_post` and neither `fiscal:setup_manage` nor `fiscal:reports_view`, so
    reading the device list to find out whether a purchase code is required would mean handing
    every sales manager the authority to reconfigure the devices. What is here instead is what
    the screen asks — does this company fiscalize, and what may a refund say for itself — and
    neither answer is a secret.
    """

    fiscalized: bool
    refund_reasons: list[RefundReasonRead]
