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


class UnregisteredAccountRead(BaseModel):
    """A bank/cash control account with no master row. The list is empty on a healthy tenant —
    the hook and the back-fill cover every path — and the screen shows it so that the one that
    is not covered is visible rather than silently missing from every bank listing."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str


class BankAccountRegister(BaseModel):
    gl_account_id: int
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


class StatementDetail(StatementRead):
    format_snapshot: dict[str, Any] | None = None
    lines: list[StatementLineRead] = []


class StatementImportResult(BaseModel):
    """What the import screen reports: "N new, M skipped". An overlapping export is the normal
    case rather than an error, so the two counts are the result, not a warning."""

    statement: StatementRead
    new_count: int
    skipped_count: int
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


class ReconciliationLock(BaseModel):
    #: Re-keying it at lock is how the tape's row 9 corrects a wrong balance without reopening.
    statement_balance: Decimal | None = None


class ReconciliationReopen(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
