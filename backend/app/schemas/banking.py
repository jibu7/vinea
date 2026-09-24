"""Wire shapes for the banking API (P8).

Two conventions the rest of this build already keeps, restated because they matter here:
every money field is a `Decimal` typed at the column's own scale and never a float, and a
statement line's `amount` is **credit positive** on the wire exactly as it is in the database —
a screen that flipped the sign for display would be the second sign convention this phase
exists to avoid.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.banking import (
    BankAccountKind,
    BankMatchKind,
    BankMatchRule,
    PaymentRunStatus,
    ReconciliationStatus,
    StatementSource,
    StatementStatus,
)


class BankAccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    gl_account_id: int
    kind: BankAccountKind
    code: str
    name: str
    currency_id: int
    bank_name: str | None = None
    account_number: str | None = None
    account_holder: str | None = None
    bank_branch: str | None = None
    swift_bic: str | None = None
    statement_format: dict[str, Any] | None = None
    is_active: bool
    last_reconciled_at: date | None = None
    last_reconciled_balance: Decimal | None = None
    #: Whether the GL account carries any journal line — the one fact that locks `currency_id`
    #: (`bank_account_has_lines`). Computed by the endpoint, not stored: it is a question about
    #: the ledger, and a column holding its answer would be a copy of the ledger that could lie.
    has_lines: bool = False


class UnregisteredAccountRead(BaseModel):
    """A bank/cash control account with no master row. The list is empty on a healthy tenant —
    the hook and the back-fill cover every path — and the screen shows it so that the one that
    is not covered is visible rather than silently missing from every bank listing."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str


class NewGLAccountWrite(BaseModel):
    """The GL account *create* makes alongside the master. Asset, postable, flagged by `kind`
    — none of which is asked, because none of it is a choice for a bank or cash account."""

    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    kind: BankAccountKind
    parent_id: int | None = None


class BankAccountRegister(BaseModel):
    """Exactly one of `gl_account_id` (*Register* an existing flagged account) and
    `new_account` (*create* the GL account and its master in one call)."""

    gl_account_id: int | None = None
    new_account: NewGLAccountWrite | None = None
    code: str | None = Field(default=None, max_length=20)
    name: str | None = Field(default=None, max_length=200)
    currency_id: int | None = None
    bank_name: str | None = Field(default=None, max_length=200)
    account_number: str | None = Field(default=None, max_length=50)
    account_holder: str | None = Field(default=None, max_length=200)
    bank_branch: str | None = Field(default=None, max_length=200)
    swift_bic: str | None = Field(default=None, max_length=20)
    statement_format: dict[str, Any] | None = None


class BankAccountUpdate(BaseModel):
    code: str | None = Field(default=None, max_length=20)
    name: str | None = Field(default=None, max_length=200)
    currency_id: int | None = None
    bank_name: str | None = Field(default=None, max_length=200)
    account_number: str | None = Field(default=None, max_length=50)
    account_holder: str | None = Field(default=None, max_length=200)
    bank_branch: str | None = Field(default=None, max_length=200)
    swift_bic: str | None = Field(default=None, max_length=20)
    statement_format: dict[str, Any] | None = None
    is_active: bool | None = None


class BankRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bank_account_id: int
    pattern: str
    gl_account_id: int | None = None
    tax_code_id: int | None = None
    partner_type: str | None = None
    partner_id: int | None = None
    description: str | None = None
    priority: int
    is_active: bool


class BankRuleWrite(BaseModel):
    pattern: str = Field(min_length=1, max_length=200)
    gl_account_id: int | None = None
    tax_code_id: int | None = None
    partner_type: str | None = Field(default=None, max_length=10)
    partner_id: int | None = None
    description: str | None = Field(default=None, max_length=200)
    priority: int = 100
    is_active: bool | None = None


class ParseErrorRead(BaseModel):
    row: int
    column: str | None = None
    message: str


class PreviewLineRead(BaseModel):
    row: int
    value_date: date
    booking_date: date | None = None
    description: str
    reference: str | None = None
    amount: Decimal
    balance_after: Decimal | None = None
    external_id: str | None = None
    already_held: bool


class StatementPreviewRead(BaseModel):
    bank_account_id: int
    file_name: str | None = None
    file_sha256: str
    format_snapshot: dict[str, Any]
    lines: list[PreviewLineRead]
    line_count: int
    new_count: int
    skipped_count: int
    lines_skipped_no_amount: int
    from_date: date | None = None
    to_date: date | None = None
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    errors: list[ParseErrorRead]
    duplicate_file: bool


class StatementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bank_account_id: int
    number: str
    source: StatementSource
    file_name: str | None = None
    from_date: date
    to_date: date
    opening_balance: Decimal
    closing_balance: Decimal
    line_count: int
    lines_skipped: int
    lines_skipped_no_amount: int
    status: StatementStatus
    imported_at: datetime


class StatementLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    statement_id: int
    bank_account_id: int
    line_no: int
    value_date: date
    booking_date: date | None = None
    description: str
    reference: str | None = None
    amount: Decimal
    balance_after: Decimal | None = None
    external_id: str | None = None


class StatementLineStateRead(BaseModel):
    """A statement line's match, for the detail screen and the workspace's left pane."""

    match_id: int | None = None
    match_kind: BankMatchKind | None = None
    match_rule: BankMatchRule | None = None
    #: The `BRC-` the match was locked into, if it was.
    reconciliation_number: str | None = None
    #: How many ledger lines the match holds — **three** on the bank's single line for a payment
    #: run, which is the one-to-many decision 7 produces and a figure the reader needs.
    journal_line_count: int = 0


class StatementLineDetailRead(StatementLineRead):
    """The line with its match state. `StatementLineRead` stays as it was for the paths that only
    need the bank's own figures (the preview, which has no matches yet)."""

    state: StatementLineStateRead


class StatementDetail(StatementRead):
    format_snapshot: dict[str, Any] | None = None
    lines: list[StatementLineDetailRead] = []


class LedgerLineRead(BaseModel):
    """One ledger line on a bank account with what the reconciliation makes of it — the
    workspace's right pane. Outstanding lines come first."""

    journal_line_id: int
    entry_id: int
    entry_number: str
    entry_date: date
    doc_type: str
    description: str | None = None
    reference: str | None = None
    #: The reconciled amount: the account's own currency, one definition
    #: (`accounts.reconciled_amount`), so this pane and the statement pane are comparable.
    amount: Decimal
    match_id: int | None = None
    match_kind: BankMatchKind | None = None
    match_rule: BankMatchRule | None = None
    reconciliation_number: str | None = None
    reconciliation_id: int | None = None
    #: "dated inside BRC-n" — posted after that reconciliation locked, dated before its date.
    dated_inside: str | None = None
    is_outstanding: bool


class EntryBankLineRead(LedgerLineRead):
    """One line of a journal entry on a bank account, as the GL entry page shows it (decision
    10): its match, the `BRC-` it was locked in, or outstanding."""

    bank_account_id: int
    bank_account_code: str
    bank_account_name: str


class StatementImportResult(BaseModel):
    """What the import screen reports: "N new, M skipped". An overlapping export is the normal
    case rather than an error, so the two counts are the result, not a warning."""

    statement: StatementRead
    new_count: int
    skipped_count: int
    lines_skipped_no_amount: int = 0
    replayed: bool = False


class ManualStatementLine(BaseModel):
    value_date: date
    booking_date: date | None = None
    description: str = Field(min_length=1, max_length=500)
    reference: str | None = Field(default=None, max_length=200)
    #: Credit positive, as everywhere else.
    amount: Decimal
    balance_after: Decimal | None = None


class ManualStatementWrite(BaseModel):
    bank_account_id: int
    opening_balance: Decimal
    closing_balance: Decimal
    lines: list[ManualStatementLine] = Field(min_length=1)


class StatementVoid(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


# --- Matching (P8 decision 4) ------------------------------------------------------------


class MatchCandidateRead(BaseModel):
    journal_line_id: int
    entry_id: int
    entry_number: str
    entry_date: date
    doc_type: str
    description: str | None = None
    reference: str | None = None
    #: The **reconciled** amount — `amount` on a foreign-currency account, `base_amount` on a
    #: base-currency one. Never the raw `amount`, which on a base-currency account can be a
    #: figure the bank never showed.
    amount: Decimal
    rule: BankMatchRule


class MatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bank_account_id: int
    kind: BankMatchKind
    rule: BankMatchRule
    reconciliation_id: int | None = None
    matched_at: datetime
    note: str | None = None
    statement_line_ids: list[int] = []
    journal_line_ids: list[int] = []


class MatchWrite(BaseModel):
    bank_account_id: int
    statement_line_ids: list[int] = []
    journal_line_ids: list[int] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=500)


class TickWrite(BaseModel):
    bank_account_id: int
    journal_line_ids: list[int] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=500)


class AutoMatchResultRead(BaseModel):
    matched: list[MatchRead]
    #: statement line id → the candidates that tied. Ambiguity is not a match: the workspace
    #: shows these and a person chooses.
    ambiguous: dict[int, list[MatchCandidateRead]]


class PrefillRead(BaseModel):
    rule_id: int | None = None
    gl_account_id: int | None = None
    tax_code_id: int | None = None
    partner_type: str | None = None
    partner_id: int | None = None
    description: str
    kind: str


class PostCashbookFromLine(BaseModel):
    gl_account_id: int
    tax_code_id: int | None = None
    description: str | None = Field(default=None, max_length=500)
    reference: str | None = Field(default=None, max_length=500)
    entry_date: date | None = None
    branch_id: int | None = None
    project_id: int | None = None


class PostSettlementFromLine(BaseModel):
    partner_id: int
    description: str | None = Field(default=None, max_length=500)
    reference: str | None = Field(default=None, max_length=500)
    document_date: date | None = None
    branch_id: int | None = None
    project_id: int | None = None


class PostedFromStatementRead(BaseModel):
    entry_id: int
    entry_number: str
    journal_line_id: int
    match: MatchRead
    document_id: int | None = None
    document_number: str | None = None


# --- Reconciliation (P8 decision 5) ------------------------------------------------------


class OutstandingLineRead(BaseModel):
    journal_line_id: int
    entry_id: int
    entry_number: str
    entry_date: date
    doc_type: str
    description: str | None = None
    amount: Decimal
    #: The `BRC-` number this line is dated inside but was posted after — decision 5's late
    #: line. `None` on an ordinary outstanding item, and the two call for different actions.
    dated_inside: str | None = None


class FiguresRead(BaseModel):
    """The strip. Live while the reconciliation is open; on a locked one the report shows the
    stored figures beside this."""

    reconciliation_date: date
    statement_balance: Decimal
    ledger_balance: Decimal
    outstanding_total: Decimal
    difference: Decimal
    adjusted_bank_balance: Decimal
    outstanding: list[OutstandingLineRead]
    unmatched_statement: list[StatementLineRead]
    unmatched_statement_count: int


class ReconciliationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bank_account_id: int
    number: str
    reconciliation_date: date
    statement_balance: Decimal
    ledger_balance: Decimal | None = None
    outstanding_total: Decimal | None = None
    difference: Decimal | None = None
    status: ReconciliationStatus
    high_water_line_id: int | None = None
    locked_at: datetime | None = None
    reopened_at: datetime | None = None
    reopened_reason: str | None = None


class ReconciliationDetail(ReconciliationRead):
    figures: FiguresRead
    #: On a locked one only: what it *said*, reproduced from the lines that existed at the
    #: lock, beside the live recomputation above.
    stored: FiguresRead | None = None
    #: Lines dated inside a locked reconciliation but posted after it — the report's
    #: *Posted after lock* section.
    late_line_ids: list[int] = []


class ReconciliationOpen(BaseModel):
    bank_account_id: int
    reconciliation_date: date
    #: Defaulted from the latest statement line's `balance_after` where the format carries one.
    statement_balance: Decimal | None = None


class DefaultStatementBalanceRead(BaseModel):
    #: `None` where no live statement line with a balance reaches the date — the user keys it.
    statement_balance: Decimal | None = None


class ReconciliationLock(BaseModel):
    #: Re-keying it at lock is how the tape's row 9 corrects a wrong balance without reopening.
    statement_balance: Decimal | None = None


class ReconciliationReopen(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


# --- Payment runs (decision 7) -----------------------------------------------------------------


class SelectableDocumentRead(BaseModel):
    """An open supplier invoice the run could pay. `discount_available` is P4's own
    computation at the payment date, not a figure this phase invents."""

    document_id: int
    number: str
    partner_id: int
    partner_name: str
    supplier_code: str | None = None
    document_date: date
    due_date: date | None = None
    currency_id: int
    total_amount: Decimal
    open_amount: Decimal
    discount_available: Decimal


class PaymentRunLineWrite(BaseModel):
    document_id: int
    #: What comes off the invoice's open amount. `None` means "all of it".
    amount: Decimal | None = None
    take_discount: bool = True


class PaymentRunWrite(BaseModel):
    bank_account_id: int
    payment_date: date
    lines: list[PaymentRunLineWrite] = Field(min_length=1)


class PaymentRunPreviewLineRead(BaseModel):
    document_id: int
    document_number: str
    due_date: date | None = None
    open_amount: Decimal
    amount: Decimal
    discount_available: Decimal
    discount_amount: Decimal
    #: What leaves the bank for this line: `amount` less the discount taken.
    cash_amount: Decimal


class PaymentRunPreviewSupplierRead(BaseModel):
    partner_id: int
    partner_name: str
    supplier_code: str | None = None
    bank_name: str | None = None
    bank_account_number: str | None = None
    bank_account_holder: str | None = None
    lines: list[PaymentRunPreviewLineRead]
    #: `bank_details_missing` and `open_credits: …`. Shown beside the supplier, never blocking
    #: the run.
    warnings: list[str] = []
    total: Decimal
    discount_total: Decimal


class PaymentRunPreviewRead(BaseModel):
    bank_account_id: int
    bank_account_code: str
    payment_date: date
    currency_id: int
    currency_code: str
    suppliers: list[PaymentRunPreviewSupplierRead]
    total: Decimal
    discount_total: Decimal


class PaymentRunLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    partner_id: int
    partner_name: str
    supplier_code: str | None = None
    document_id: int
    #: The invoice paid, the `PMT-` and the `ALC-` — the three links the run's detail draws.
    document_number: str
    #: The **cash** paid on this line — what the preview called `cash_amount`. The invoice was
    #: settled by `amount + discount_amount`.
    amount: Decimal
    discount_amount: Decimal
    settlement_document_id: int | None = None
    settlement_number: str | None = None
    #: `posted`, or `reversed` once the run is.
    settlement_status: str | None = None
    allocation_id: int | None = None
    allocation_number: str | None = None


class PaymentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bank_account_id: int
    number: str
    payment_date: date
    currency_id: int
    total: Decimal
    reference: str
    status: PaymentRunStatus
    posted_at: datetime
    reversed_at: datetime | None = None
    reversal_reason: str | None = None
    #: How many suppliers the run paid — one `PMT-` each. Filled by the listing and the detail.
    supplier_count: int = 0


class PaymentRunDetail(PaymentRunRead):
    lines: list[PaymentRunLineRead] = []
    #: The `remittance_pdf` jobs this run queued, one per supplier, with their artifacts.
    remittance_job_ids: list[int] = []
    #: The locked reconciliation holding the run's bank line, or null. Reverse is refused
    #: `reconciliation_locked` while it is set, and the screen says so before the button.
    reconciliation_locked: str | None = None


class PaymentRunReverse(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    #: Defaults to the run's payment date. Never earlier than it — P4 refuses
    #: `reversal_before_original`.
    on_date: date | None = None


# --- Cashbooks, the reconciliation report and the enquiry (decisions 6 and 10) -------------------


class CashbookRowRead(BaseModel):
    journal_line_id: int
    entry_id: int
    entry_date: date
    entry_number: str
    #: `journal_entries.doc_type` — decision 6's *document type* column.
    doc_type: str
    module: str
    reference: str | None = None
    description: str | None = None
    partner_name: str | None = None
    receipt: Decimal
    payment: Decimal
    running_balance: Decimal
    base_amount: Decimal
    #: `BRC-000002`, `matched`, or null for outstanding.
    reconciled: str | None = None


class CashbookDetailRead(BaseModel):
    bank_account_id: int
    code: str
    name: str
    currency_id: int
    currency_code: str
    date_from: date
    date_to: date
    opening_balance: Decimal
    opening_base: Decimal
    receipts_total: Decimal
    payments_total: Decimal
    #: In the account's own currency. `closing_base` is the figure that ties to the trial
    #: balance; see `app/banking/reports.py` for why there are two.
    closing_balance: Decimal
    closing_base: Decimal
    rows: list[CashbookRowRead]


class CashbookSummaryRowRead(BaseModel):
    bank_account_id: int
    code: str
    name: str
    kind: str
    currency_code: str
    opening_balance: Decimal
    receipts: Decimal
    payments: Decimal
    closing_balance: Decimal
    closing_base: Decimal
    last_reconciled_at: date | None = None
    last_reconciled_balance: Decimal | None = None
    #: The latest locked reconciliation, so the summary's date links to its report.
    last_reconciliation_id: int | None = None
    unmatched_statement_lines: int
    outstanding_lines: int


class ReconciliationReportRead(BaseModel):
    """`stored` is what a locked reconciliation *said*; `live` is what its date computes now.
    They differ by exactly `posted_after_lock`."""

    reconciliation_id: int
    number: str
    bank_account_id: int
    bank_account_code: str
    bank_account_name: str
    currency_code: str
    reconciliation_date: date
    status: ReconciliationStatus
    live: FiguresRead
    stored: FiguresRead | None = None
    posted_after_lock: list[OutstandingLineRead] = []


class BankAccountEnquiryRead(BaseModel):
    bank_account_id: int
    code: str
    name: str
    kind: str
    currency_code: str
    #: In the account's own currency, and in base. Decision 10 names both: a USD balance means
    #: nothing to a balance sheet and its base value means nothing to the bank.
    book_balance: Decimal
    book_balance_base: Decimal
    last_reconciliation_id: int | None = None
    last_reconciliation_number: str | None = None
    last_reconciled_at: date | None = None
    last_reconciled_balance: Decimal | None = None
    open_reconciliation_id: int | None = None
    open_reconciliation_number: str | None = None
    unmatched_statement_count: int
    unmatched_statement_total: Decimal
    outstanding_count: int
    outstanding_total: Decimal
    latest_statement_id: int | None = None
    latest_statement_number: str | None = None
    latest_statement_to: date | None = None
