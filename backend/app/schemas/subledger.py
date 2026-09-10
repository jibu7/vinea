"""AR/AP API schemas. One set of shapes for both roles — the role is a path segment, never
a duplicated model."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from app.models.partner import AgeingBasis, DueBasis, TaxMode
from app.schemas.common import ApiModel

Money = Annotated[Decimal, Field(max_digits=20, decimal_places=6)]
NonNegativeMoney = Annotated[Decimal, Field(ge=0, max_digits=20, decimal_places=6)]
PositiveMoney = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=6)]
Rate = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=10)]
Percent = Annotated[Decimal, Field(ge=0, lt=100, max_digits=20, decimal_places=10)]
Quantity = Annotated[Decimal, Field(max_digits=20, decimal_places=6)]


# --- Partners ------------------------------------------------------------------------------


class PartnerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    customer_code: str | None = Field(default=None, min_length=1, max_length=30)
    supplier_code: str | None = Field(default=None, min_length=1, max_length=30)
    tin: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=30)
    address: dict | None = None
    notes: str | None = None
    currency_id: int | None = None

    @model_validator(mode="after")
    def _has_a_role(self) -> "PartnerCreate":
        if self.customer_code is None and self.supplier_code is None:
            raise ValueError("give a customer_code, a supplier_code, or both")
        return self


class PartnerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    customer_code: str | None = Field(default=None, max_length=30)
    clear_customer_code: bool = False
    supplier_code: str | None = Field(default=None, max_length=30)
    clear_supplier_code: bool = False
    tin: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=30)
    address: dict | None = None
    notes: str | None = None
    currency_id: int | None = None
    clear_currency: bool = False
    is_active: bool | None = None


class PartnerRead(ApiModel):
    id: int
    name: str
    customer_code: str | None
    supplier_code: str | None
    is_customer: bool
    is_supplier: bool
    tin: str | None
    email: str | None
    phone: str | None
    address: dict | None
    notes: str | None
    currency_id: int | None
    is_active: bool


class PartnerAuditRead(ApiModel):
    """One `audit_log` row against a partner — what the "Rename customer/supplier" screen
    shows as history, exactly as the GL rename screen reads `gl_accounts` history."""

    id: int
    action: str
    at: datetime
    actor_email: str | None
    before: dict | None
    after: dict | None


class RoleSettingsWrite(BaseModel):
    control_account_id: int | None = None
    payment_terms_id: int | None = None
    credit_limit: NonNegativeMoney | None = None
    sales_rep_id: int | None = None
    default_tax_code_id: int | None = None
    default_branch_id: int | None = None
    default_project_id: int | None = None
    default_gl_account_id: int | None = None
    tax_mode: TaxMode = TaxMode.EXCLUSIVE
    is_on_hold: bool = False


class RoleSettingsRead(ApiModel):
    id: int
    partner_id: int
    control_account_id: int | None
    payment_terms_id: int | None
    credit_limit: Decimal | None
    sales_rep_id: int | None
    default_tax_code_id: int | None
    default_branch_id: int | None
    default_project_id: int | None
    default_gl_account_id: int | None
    tax_mode: TaxMode
    is_on_hold: bool


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    role: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=30)
    notes: str | None = None
    is_primary: bool = False


class ContactUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    role: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=30)
    notes: str | None = None
    is_primary: bool | None = None
    is_active: bool | None = None


class ContactRead(ApiModel):
    id: int
    partner_id: int
    name: str
    role: str | None
    email: str | None
    phone: str | None
    notes: str | None
    is_primary: bool
    is_active: bool


# --- Sales reps, terms, ageing sets ------------------------------------------------------


class SalesRepCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)


class SalesRepUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    is_active: bool | None = None


class SalesRepRead(ApiModel):
    id: int
    code: str
    name: str
    email: str | None
    is_active: bool


class PaymentTermsWrite(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    due_basis: DueBasis = DueBasis.DAYS_FROM_DOCUMENT_DATE
    due_days: int = Field(default=0, ge=0, le=365)
    due_day_of_month: int | None = Field(default=None, ge=1, le=31)
    discount_percent: Percent = Decimal(0)
    discount_days: int = Field(default=0, ge=0, le=365)
    is_active: bool | None = None


class PaymentTermsRead(ApiModel):
    id: int
    code: str
    name: str
    due_basis: DueBasis
    due_days: int
    due_day_of_month: int | None
    discount_percent: Decimal
    discount_days: int
    is_active: bool


class BucketWrite(BaseModel):
    label: str = Field(min_length=1, max_length=50)
    from_days: int = Field(ge=0, le=3650)
    to_days: int | None = Field(default=None, ge=0, le=3650)


class BucketRead(ApiModel):
    id: int
    sequence: int
    label: str
    from_days: int
    to_days: int | None


class BucketSetCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    basis: AgeingBasis = AgeingBasis.DUE_DATE
    is_default: bool = False
    buckets: list[BucketWrite] = Field(min_length=1)


class BucketSetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    basis: AgeingBasis | None = None
    is_default: bool | None = None
    is_active: bool | None = None
    buckets: list[BucketWrite] | None = None


class BucketSetRead(ApiModel):
    id: int
    code: str
    name: str
    basis: AgeingBasis
    is_default: bool
    is_active: bool
    buckets: list[BucketRead] = []


class ArApDefaultsRead(ApiModel):
    ar_control_account_id: int | None
    ap_control_account_id: int | None
    realized_fx_gain_account_id: int | None
    realized_fx_loss_account_id: int | None
    settlement_discount_granted_account_id: int | None
    settlement_discount_received_account_id: int | None
    post_dated_receivable_account_id: int | None
    post_dated_payable_account_id: int | None


class ArApDefaultsUpdate(BaseModel):
    ar_control_account_id: int | None = None
    ap_control_account_id: int | None = None
    realized_fx_gain_account_id: int | None = None
    realized_fx_loss_account_id: int | None = None
    settlement_discount_granted_account_id: int | None = None
    settlement_discount_received_account_id: int | None = None
    post_dated_receivable_account_id: int | None = None
    post_dated_payable_account_id: int | None = None


# --- Documents -----------------------------------------------------------------------------


class DocumentLineIn(BaseModel):
    description: str | None = Field(default=None, max_length=500)
    quantity: Quantity = Decimal(1)
    unit_price: Money
    discount_percent: Percent = Decimal(0)
    gl_account_id: int | None = None
    transaction_type: str | None = Field(default=None, max_length=30)
    tax_code_id: int | None = None
    branch_id: int | None = None
    project_id: int | None = None


class DocumentCreate(BaseModel):
    kind: str = Field(pattern="^(invoice|credit_note|settlement)$")
    partner_id: int
    document_date: date
    due_date: date | None = None
    currency_id: int | None = None
    exchange_rate: Rate | None = None
    branch_id: int | None = None
    project_id: int | None = None
    payment_terms_id: int | None = None
    sales_rep_id: int | None = None
    tax_mode: TaxMode | None = None
    reference: str | None = Field(default=None, max_length=500)
    description: str = Field(min_length=1, max_length=500)
    lines: list[DocumentLineIn] = []
    # Settlements (receipts / payments) carry the amount on the header instead of lines.
    amount: PositiveMoney | None = None
    cash_account_id: int | None = None
    instrument_type: str | None = Field(default=None, pattern="^(cash|bank|cheque|mobile|other)$")
    maturity_date: date | None = None

    @model_validator(mode="after")
    def _shape_matches_kind(self) -> "DocumentCreate":
        if self.kind == "settlement":
            if self.lines:
                raise ValueError("a receipt/payment carries an amount, not lines")
            if self.amount is None or self.cash_account_id is None:
                raise ValueError("a receipt/payment needs an amount and a cash account")
        else:
            if not self.lines:
                raise ValueError("an invoice or credit note needs at least one line")
            if self.amount is not None or self.cash_account_id is not None:
                raise ValueError("amount/cash_account_id apply to receipts and payments only")
        return self


class DocumentLineRead(ApiModel):
    id: int
    line_no: int
    description: str | None
    quantity: Decimal
    unit_price: Decimal
    discount_percent: Decimal
    gl_account_id: int
    tax_code_id: int | None
    branch_id: int
    project_id: int | None
    net_amount: Decimal
    tax_amount: Decimal
    gross_amount: Decimal


class BatchLineIn(BaseModel):
    partner_id: int
    contra_account_id: int
    #: Signed in the partner's normal direction; negative is the credit side.
    amount: Money
    description: str = Field(min_length=1, max_length=500)
    tax_code_id: int | None = None
    branch_id: int | None = None
    project_id: int | None = None
    due_date: date | None = None


class BatchCreate(BaseModel):
    batch_date: date
    reference: str | None = Field(default=None, max_length=500)
    lines: list[BatchLineIn] = Field(min_length=1)


class BatchRead(ApiModel):
    batch_date: date
    reference: str | None
    documents: list["DocumentSummary"] = []


class DocumentRead(ApiModel):
    id: int
    role: str
    kind: str
    #: What the document *is*, for display and for any sales/purchase figure. `kind` is its
    #: shape in the ledger; a journal debit is invoice-shaped and is not an invoice.
    transaction_type: str
    number: str
    doc_type: str
    partner_id: int
    journal_entry_id: int
    document_date: date
    due_date: date | None
    currency_id: int
    exchange_rate: Decimal
    branch_id: int
    project_id: int | None
    payment_terms_id: int | None
    sales_rep_id: int | None
    tax_mode: TaxMode
    control_account_id: int
    reference: str | None
    description: str
    net_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    base_total_amount: Decimal
    open_amount: Decimal
    direction: int
    instrument_type: str | None
    maturity_date: date | None
    cash_account_id: int | None
    matured_entry_id: int | None
    status: str
    reversal_entry_id: int | None
    reversed_on: date | None
    lines: list[DocumentLineRead] = []


class DocumentSummary(ApiModel):
    id: int
    role: str
    kind: str
    transaction_type: str
    number: str
    partner_id: int
    document_date: date
    due_date: date | None
    currency_id: int
    total_amount: Decimal
    open_amount: Decimal
    direction: int
    status: str
    reference: str | None
    description: str


# --- Allocations ---------------------------------------------------------------------------


class AllocationPairIn(BaseModel):
    debit_document_id: int
    credit_document_id: int
    amount: PositiveMoney
    discount_amount: NonNegativeMoney = Decimal(0)


class AllocationCreate(BaseModel):
    partner_id: int
    allocation_date: date
    description: str | None = Field(default=None, max_length=500)
    pairs: list[AllocationPairIn] = Field(min_length=1)


class AutoAllocateCreate(BaseModel):
    partner_id: int
    allocation_date: date
    # Restricts the run to one credit document (a single receipt) when given.
    credit_document_id: int | None = None
    description: str | None = Field(default=None, max_length=500)


class AllocationLineRead(ApiModel):
    id: int
    line_no: int
    debit_document_id: int
    credit_document_id: int
    amount: Decimal
    discount_amount: Decimal
    discount_document_id: int | None
    fx_base_amount: Decimal


class AllocationRead(ApiModel):
    id: int
    number: str
    role: str
    partner_id: int
    currency_id: int
    allocation_date: date
    journal_entry_id: int | None
    reverses_allocation_id: int | None
    description: str | None
    lines: list[AllocationLineRead] = []


class AllocationPreviewLine(BaseModel):
    debit_document_id: int
    debit_number: str
    credit_document_id: int
    credit_number: str
    amount: Decimal
    discount_amount: Decimal
    fx_base_amount: Decimal


class AllocationPreviewPosting(BaseModel):
    gl_account_id: int
    description: str
    base_amount: Decimal


class AllocationPreview(BaseModel):
    """What the allocation screen shows before Post: the realized FX and discount effect."""

    currency_id: int
    lines: list[AllocationPreviewLine]
    postings: list[AllocationPreviewPosting]
    total_allocated: Decimal
    total_discount: Decimal
    total_fx_base: Decimal


# --- Ageing, enquiries, statements ---------------------------------------------------------


class AgeingBucketAmount(BaseModel):
    label: str
    from_days: int
    to_days: int | None
    amount: Decimal


class AgeingRow(BaseModel):
    partner_id: int
    partner_code: str | None
    partner_name: str
    total: Decimal
    buckets: list[AgeingBucketAmount]


class AgeingReport(BaseModel):
    role: str
    as_of: date
    bucket_set_id: int
    bucket_set_code: str
    basis: AgeingBasis
    rows: list[AgeingRow]
    totals: list[AgeingBucketAmount]
    grand_total: Decimal


class OpenItemRead(BaseModel):
    document_id: int
    number: str
    #: Shape in the ledger. Present for machines; screens show the transaction type instead.
    kind: str
    transaction_type: str
    transaction_type_name: str
    document_date: date
    due_date: date | None
    currency_id: int
    total_amount: Decimal
    open_amount: Decimal
    open_base_amount: Decimal
    direction: int
    days_overdue: int


class PartnerEnquiryEntry(BaseModel):
    document_id: int
    number: str
    #: Shape in the ledger. Present for machines; screens show the transaction type instead.
    kind: str
    transaction_type: str
    transaction_type_name: str
    document_date: date
    due_date: date | None
    currency_id: int
    total_amount: Decimal
    open_amount: Decimal
    direction: int
    journal_entry_id: int
    running_base: Decimal
    status: str


class PartnerEnquiry(BaseModel):
    role: str
    partner_id: int
    partner_name: str
    partner_code: str | None
    as_of: date
    balance_base: Decimal
    credit_limit: Decimal | None
    credit_available: Decimal | None
    entries: list[PartnerEnquiryEntry]
    open_items: list[OpenItemRead]


class PartnerAllocationRead(BaseModel):
    allocation_id: int
    number: str
    allocation_date: date
    #: The entry the allocation wrote, or `None` when it wrote none — a same-currency
    #: allocation with no discount closes open items and posts nothing. The Allocation report
    #: drills through this, so an FX figure on screen leads to the ledger behind it.
    journal_entry_id: int | None
    partner_id: int
    #: The allocation's currency. Allocated and discount amounts are in it, not in base.
    currency_id: int
    debit_document_id: int
    debit_number: str
    credit_document_id: int
    credit_number: str
    amount: Decimal
    discount_amount: Decimal
    fx_base_amount: Decimal


class PendingInstrumentRead(ApiModel):
    """A post-dated receipt or payment whose cash has not landed yet: posted, carrying a
    maturity date, and with no maturity entry against it. `is_due` is relative to the as-of
    date the caller asked about — an instrument is allocatable either way, it is only the
    cash that waits."""

    id: int
    number: str
    partner_id: int
    document_date: date
    maturity_date: date
    instrument_type: str | None
    currency_id: int
    total_amount: Decimal
    open_amount: Decimal
    cash_account_id: int | None
    is_due: bool
    description: str


class MaturityRunRequest(BaseModel):
    as_of: date


class ReversalRequest(BaseModel):
    on_date: date
    reason: str = Field(min_length=3, max_length=500)


class MaturityRunResult(BaseModel):
    as_of: date
    matured_document_ids: list[int]
    journal_entry_ids: list[int]


class StatementRequest(BaseModel):
    partner_ids: list[int] = Field(default_factory=list)
    as_of: date
    variant: str = Field(default="open_item", pattern="^(open_item|activity)$")
    date_from: date | None = None


class JobRead(ApiModel):
    id: int
    kind: str
    status: str
    params: dict | None
    result: dict | None
    error: str | None
    artifact_name: str | None
    artifact_content_type: str | None
    artifact_size: int | None
    expires_at: datetime | None


class JobSweepResult(BaseModel):
    abandoned: int
    deleted: int
