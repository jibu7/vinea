"""GL API schemas. Money is `Decimal` (serialised as strings); entry lines are entered as
debit/credit columns like an accountant expects and mapped to the kernel's signed amounts."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from app.models.fiscal import PeriodStatus
from app.models.gl import AccountClass, ControlType
from app.models.journal import JournalStatus
from app.schemas.common import ApiModel

Money = Annotated[Decimal, Field(max_digits=20, decimal_places=6)]
Rate = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=10)]
NonNegativeMoney = Annotated[Decimal, Field(ge=0, max_digits=20, decimal_places=6)]
PositiveMoney = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=6)]


# --- Chart of accounts -------------------------------------------------------------------


class GLAccountCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    class_: AccountClass = Field(alias="class")
    parent_id: int | None = None
    is_postable: bool = True
    control_type: ControlType | None = None

    model_config = {"populate_by_name": True}


class GLAccountUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=20)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: int | None = None
    clear_parent: bool = False
    is_postable: bool | None = None
    is_active: bool | None = None


class GLAccountRead(ApiModel):
    id: int
    code: str
    name: str
    class_: AccountClass = Field(serialization_alias="class")
    parent_id: int | None
    is_postable: bool
    is_control: bool
    control_type: ControlType | None
    is_active: bool


class GLSettingsRead(ApiModel):
    retained_earnings_account_id: int | None


class GLSettingsUpdate(BaseModel):
    retained_earnings_account_id: int


# --- Exchange rates ----------------------------------------------------------------------


class ExchangeRateCreate(BaseModel):
    currency_id: int
    valid_from: date
    rate: Rate


class ExchangeRateRead(ApiModel):
    id: int
    currency_id: int
    valid_from: date
    rate: Decimal


# --- Journal entries ---------------------------------------------------------------------


class JournalLineIn(BaseModel):
    """Give `gl_account_id`, or a `transaction_type` whose default account resolves it."""

    gl_account_id: int | None = None
    transaction_type: str | None = Field(default=None, max_length=30)
    debit: NonNegativeMoney = Decimal(0)
    credit: NonNegativeMoney = Decimal(0)
    currency_id: int | None = None
    exchange_rate: Rate | None = None
    branch_id: int | None = None
    project_id: int | None = None
    partner_type: str | None = Field(default=None, max_length=20)
    partner_id: int | None = None
    item_id: int | None = None
    tax_code_id: int | None = None
    tax_amount: Money = Decimal(0)
    description: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _one_side(self) -> "JournalLineIn":
        if (self.debit > 0) == (self.credit > 0):
            raise ValueError("exactly one of debit or credit must be greater than zero")
        if self.gl_account_id is None and self.transaction_type is None:
            raise ValueError("give gl_account_id or transaction_type")
        return self

    @property
    def signed_amount(self) -> Decimal:
        return self.debit if self.debit > 0 else -self.credit


class JournalEntryCreate(BaseModel):
    entry_date: date
    description: str = Field(min_length=1, max_length=500)
    branch_id: int | None = None
    lines: list[JournalLineIn] = Field(min_length=2)


class CashbookLineIn(BaseModel):
    gl_account_id: int | None = None
    transaction_type: str | None = Field(default=None, max_length=30)
    amount: PositiveMoney
    tax_code_id: int | None = None
    tax_inclusive: bool = True
    branch_id: int | None = None
    project_id: int | None = None
    partner_type: str | None = Field(default=None, max_length=20)
    partner_id: int | None = None
    description: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _account_given(self) -> "CashbookLineIn":
        if self.gl_account_id is None and self.transaction_type is None:
            raise ValueError("give gl_account_id or transaction_type")
        return self


class CashbookEntryCreate(BaseModel):
    entry_date: date
    description: str = Field(min_length=1, max_length=500)
    cash_account_id: int
    kind: str = Field(pattern="^(receipt|payment)$")
    currency_id: int | None = None
    exchange_rate: Rate | None = None
    branch_id: int | None = None
    reference: str | None = Field(default=None, max_length=500)
    lines: list[CashbookLineIn] = Field(min_length=1)


class ReversalCreate(BaseModel):
    entry_date: date
    reason: str = Field(min_length=1, max_length=500)


class JournalLineRead(ApiModel):
    id: int
    line_no: int
    gl_account_id: int
    branch_id: int
    project_id: int | None
    partner_type: str | None
    partner_id: int | None
    item_id: int | None
    currency_id: int
    exchange_rate: Decimal
    amount: Decimal
    base_amount: Decimal
    tax_code_id: int | None
    tax_amount: Decimal
    description: str | None
    source_doc_type: str | None
    source_doc_id: int | None
    source_line_id: int | None


class JournalEntryRead(ApiModel):
    id: int
    number: str
    doc_type: str
    event_type: str
    entry_date: date
    period_id: int
    description: str
    status: JournalStatus
    posted_by: int | None
    posted_at: datetime | None
    reverses_entry_id: int | None
    reversal_reason: str | None
    source_doc_type: str | None
    source_doc_id: int | None
    lines: list[JournalLineRead]


class JournalEntrySummary(ApiModel):
    id: int
    number: str
    doc_type: str
    event_type: str
    entry_date: date
    description: str
    status: JournalStatus
    reverses_entry_id: int | None


# --- Enquiries ---------------------------------------------------------------------------


class TrialBalanceRowRead(BaseModel):
    gl_account_id: int
    code: str
    name: str
    class_: AccountClass = Field(serialization_alias="class")
    debit: Decimal
    credit: Decimal
    net: Decimal


class TrialBalanceRead(BaseModel):
    as_of: date
    branch_id: int | None
    project_id: int | None
    rows: list[TrialBalanceRowRead]
    total_debit: Decimal
    total_credit: Decimal
    foots: bool


class AccountTransactionRead(BaseModel):
    line_id: int
    entry_id: int
    entry_number: str
    entry_date: date
    description: str | None
    branch_id: int
    project_id: int | None
    currency_id: int
    amount: Decimal
    base_amount: Decimal
    running_base: Decimal


class AccountTransactionsRead(BaseModel):
    gl_account_id: int
    date_from: date
    date_to: date
    opening_base: Decimal
    items: list[AccountTransactionRead]
    next_cursor: int | None


# --- Periods -----------------------------------------------------------------------------


class PeriodRead(ApiModel):
    id: int
    fiscal_year_id: int
    period_no: int
    name: str
    start_date: date
    end_date: date
    status: PeriodStatus


class FiscalYearRead(ApiModel):
    id: int
    name: str
    start_date: date
    end_date: date
    status: PeriodStatus
    closing_entry_id: int | None


class FiscalYearCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    start_date: date
    end_date: date
    open_through: date | None = None


class ReasonBody(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class PeriodBalanceDriftRead(BaseModel):
    period_id: int
    gl_account_id: int
    branch_id: int
    currency_id: int
    field: str
    cached: Decimal
    recomputed: Decimal


# --- Masters: projects & transaction types ------------------------------------------------


class ProjectCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)


class ProjectUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=20)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None


class ProjectRead(ApiModel):
    id: int
    code: str
    name: str
    is_active: bool


class TransactionTypeCreate(BaseModel):
    module: str = Field(default="gl", min_length=2, max_length=10)
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=200)
    default_gl_account_id: int | None = None


class TransactionTypeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    default_gl_account_id: int | None = None
    clear_default_account: bool = False
    is_active: bool | None = None


class TransactionTypeRead(ApiModel):
    id: int
    module: str
    code: str
    name: str
    default_gl_account_id: int | None
    is_active: bool
