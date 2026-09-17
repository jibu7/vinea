"""VAT return API schemas (P7 decision 12).

The return is a *reconciliation*, not a total, so the response carries the tie with it: every
VAT account's movement, what the return declares of it, and the lines making up the rest. A
screen that showed only the sections would be hiding the one thing an accountant opens it for.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel

from app.models.fiscalization import VatReturnStatus
from app.schemas.common import ApiModel


class LineMovementRead(BaseModel):
    """One journal line behind a tie's difference, named well enough to be looked up."""

    line_id: int
    entry_id: int
    entry_number: str
    entry_date: date
    doc_type: str
    module: str
    description: str | None
    base_amount: Decimal


class AccountTieRead(BaseModel):
    account_id: int
    code: str
    name: str
    movement: Decimal
    declared_in_range: Decimal
    late_total: Decimal
    difference: Decimal
    reconciled: bool
    untagged: list[LineMovementRead]


class LateEntryRead(BaseModel):
    """Declared on this return, dated inside a filed one."""

    entry_id: int
    entry_number: str
    entry_date: date
    filed_return_number: str
    code: str
    side: str
    base: Decimal
    tax: Decimal


class CodeTotalRead(BaseModel):
    tax_code_id: int
    code: str
    name: str
    rate_pct: Decimal
    side: str
    base: Decimal
    tax: Decimal
    late_base: Decimal
    late_tax: Decimal
    declared_base: Decimal
    declared_tax: Decimal


class VatReturnSections(BaseModel):
    """The figures as they go on the form."""

    sales_standard_base: Decimal
    sales_standard_vat: Decimal
    sales_zero_rated_base: Decimal
    sales_exempt_base: Decimal
    purchases_standard_base: Decimal
    purchases_standard_vat: Decimal
    purchases_imports_base: Decimal
    purchases_imports_vat: Decimal
    purchases_zero_rated_base: Decimal
    purchases_exempt_base: Decimal
    output_vat: Decimal
    input_vat: Decimal
    #: Positive is payable to the authority; negative is a credit carried forward.
    net_payable: Decimal


class VatReturnPreview(BaseModel):
    """The return over a range as it stands right now — nothing stored."""

    period_from: date
    period_to: date
    high_water_entry_id: int
    sections: VatReturnSections
    codes: list[CodeTotalRead]
    late_entries: list[LateEntryRead]
    ties: list[AccountTieRead]


class VatReturnFile(BaseModel):
    period_from: date
    period_to: date


class VatReturnReverse(BaseModel):
    reason: str


class VatReturnRead(ApiModel):
    id: int
    number: str
    period_from: date
    period_to: date
    output_vat: Decimal
    input_vat: Decimal
    net_payable: Decimal
    status: VatReturnStatus
    high_water_entry_id: int
    journal_entry_id: int | None
    reversal_entry_id: int | None
    filed_by: int | None
    filed_at: datetime


class VatReturnDetail(VatReturnRead):
    """A filed return, with the snapshot it was filed on.

    `figures` is the JSON as submitted rather than a recomputation: what this return said is
    what it said, and a later entry into the period must not change it.
    """

    figures: dict
