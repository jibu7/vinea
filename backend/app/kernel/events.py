"""Typed posting events (ADR-05). Modules emit these; only `app.kernel.posting.post`
turns them into journal entries.

Amounts on line specs are **signed** in the transaction currency: positive = debit,
negative = credit. Events for modules that do not exist yet are declared here as stubs so
the event vocabulary is fixed now; `post()` rejects them with `unsupported_event` until
their phase lands.
"""

import enum
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import ClassVar

from app.kernel.sequences import DocType

ZERO = Decimal(0)


@dataclass(frozen=True, kw_only=True)
class LineSpec:
    amount: Decimal
    gl_account_id: int | None = None  # explicit override — first link of the chain
    currency_id: int | None = None  # None → company base currency
    exchange_rate: Decimal | None = None  # None → dated rate from exchange_rates
    branch_id: int | None = None  # None → event branch → main branch
    project_id: int | None = None
    partner_type: str | None = None
    partner_id: int | None = None
    item_id: int | None = None
    tax_code_id: int | None = None
    tax_amount: Decimal = ZERO
    description: str | None = None
    transaction_type: str | None = None  # key into transaction-type defaults (P4+)
    source_doc_type: str | None = None
    source_doc_id: int | None = None
    source_line_id: int | None = None


@dataclass(frozen=True, kw_only=True)
class PostingEvent:
    event_type: ClassVar[str]
    doc_type: ClassVar[str]

    entry_date: date
    description: str
    branch_id: int | None = None
    source_doc_type: str | None = None
    source_doc_id: int | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True, kw_only=True)
class ManualJournal(PostingEvent):
    event_type: ClassVar[str] = "manual_journal"
    doc_type: ClassVar[str] = DocType.JOURNAL

    lines: tuple[LineSpec, ...]


class CashbookKind(enum.StrEnum):
    RECEIPT = "receipt"  # money in: bank debited, counterparts credited
    PAYMENT = "payment"  # money out: bank credited, counterparts debited


@dataclass(frozen=True, kw_only=True)
class CashbookLineSpec:
    """One counterpart line; `amount` is positive. With a tax code the engine splits it into
    a net line (carrying the tax dimension) and a tax line on the tax code's GL account."""

    gl_account_id: int
    amount: Decimal
    tax_code_id: int | None = None
    tax_inclusive: bool = True
    branch_id: int | None = None
    project_id: int | None = None
    partner_type: str | None = None
    partner_id: int | None = None
    description: str | None = None


@dataclass(frozen=True, kw_only=True)
class CashbookEntry(PostingEvent):
    """The bank/cash side is derived: one line for the gross total of the counterparts."""

    event_type: ClassVar[str] = "cashbook_entry"
    doc_type: ClassVar[str] = DocType.CASHBOOK

    cash_account_id: int
    kind: CashbookKind
    lines: tuple[CashbookLineSpec, ...]
    currency_id: int | None = None
    exchange_rate: Decimal | None = None
    reference: str | None = None


@dataclass(frozen=True, kw_only=True)
class ReversalRequested(PostingEvent):
    event_type: ClassVar[str] = "reversal"
    doc_type: ClassVar[str] = DocType.JOURNAL  # overridden with the original's doc type

    entry_id: int
    reason: str
    description: str = ""
    # Only `reopen_fiscal_year` sets this; closing entries are otherwise not reversible.
    allow_closing_entry: bool = False


@dataclass(frozen=True, kw_only=True)
class PeriodClosed(PostingEvent):
    """Year-end: P&L balances (per branch, base currency) close into retained earnings."""

    event_type: ClassVar[str] = "period_close"
    doc_type: ClassVar[str] = DocType.YEAR_END

    fiscal_year_id: int
    description: str = ""


# --- Stubs for later phases (documented vocabulary, not yet postable) ------------------


@dataclass(frozen=True, kw_only=True)
class _StubEvent(PostingEvent):
    """Placeholder: the emitting module arrives in a later phase (P4 AR/AP, P5 Inventory,
    P6 OE, P7 FX, P9 Fixed Assets, P11 POS, P12 BOM)."""

    lines: tuple[LineSpec, ...] = field(default_factory=tuple)


@dataclass(frozen=True, kw_only=True)
class InvoicePosted(_StubEvent):
    event_type: ClassVar[str] = "invoice_posted"
    doc_type: ClassVar[str] = "INV"


@dataclass(frozen=True, kw_only=True)
class CreditNotePosted(_StubEvent):
    event_type: ClassVar[str] = "credit_note_posted"
    doc_type: ClassVar[str] = "CN"


@dataclass(frozen=True, kw_only=True)
class ReceiptPosted(_StubEvent):
    event_type: ClassVar[str] = "receipt_posted"
    doc_type: ClassVar[str] = "RCT"


@dataclass(frozen=True, kw_only=True)
class PaymentPosted(_StubEvent):
    event_type: ClassVar[str] = "payment_posted"
    doc_type: ClassVar[str] = "PAY"


@dataclass(frozen=True, kw_only=True)
class GoodsReceived(_StubEvent):
    event_type: ClassVar[str] = "goods_received"
    doc_type: ClassVar[str] = "GRN"


@dataclass(frozen=True, kw_only=True)
class SupplierInvoiceMatched(_StubEvent):
    event_type: ClassVar[str] = "supplier_invoice_matched"
    doc_type: ClassVar[str] = "SINV"


@dataclass(frozen=True, kw_only=True)
class StockAdjusted(_StubEvent):
    event_type: ClassVar[str] = "stock_adjusted"
    doc_type: ClassVar[str] = "ADJ"


@dataclass(frozen=True, kw_only=True)
class StockTransferred(_StubEvent):
    event_type: ClassVar[str] = "stock_transferred"
    doc_type: ClassVar[str] = "TRF"


@dataclass(frozen=True, kw_only=True)
class StockSold(_StubEvent):
    event_type: ClassVar[str] = "stock_sold"
    doc_type: ClassVar[str] = "COGS"


@dataclass(frozen=True, kw_only=True)
class FxRevalued(_StubEvent):
    event_type: ClassVar[str] = "fx_revalued"
    doc_type: ClassVar[str] = "FXR"


@dataclass(frozen=True, kw_only=True)
class DepreciationPosted(_StubEvent):
    event_type: ClassVar[str] = "depreciation_posted"
    doc_type: ClassVar[str] = "DEP"


@dataclass(frozen=True, kw_only=True)
class ManufactureCompleted(_StubEvent):
    event_type: ClassVar[str] = "manufacture_completed"
    doc_type: ClassVar[str] = "MFG"


@dataclass(frozen=True, kw_only=True)
class PosSaleCompleted(_StubEvent):
    event_type: ClassVar[str] = "pos_sale_completed"
    doc_type: ClassVar[str] = "POS"
