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

from app.models.banking import BankAccountKind, StatementSource, StatementStatus


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
