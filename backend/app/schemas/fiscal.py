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
    FiscalProfile,
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
    items_count: int
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
    by_payment_method: dict[str, Decimal]


class DailyReportRead(BaseModel):
    """An X or a Z in the same shape. They differ by whether anybody stored it."""

    device_id: int
    kind: str
    from_at: datetime
    to_at: datetime
    figures: DailyFiguresRead
    number: str | None = None
    report_no: int | None = None
