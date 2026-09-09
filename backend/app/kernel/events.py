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
    # Reversals only: reuse the frozen base amount rather than re-converting.
    base_amount: Decimal | None = None
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
    # Owning module; scopes transaction-type lookups in the determination chain.
    module: ClassVar[str] = "gl"

    entry_date: date
    description: str
    reference: str | None = None
    branch_id: int | None = None
    source_doc_type: str | None = None
    source_doc_id: int | None = None
    idempotency_key: str | None = None
    # Fingerprint of the originating request; set by the API so a key replayed with a
    # different payload is rejected instead of silently returning the first entry.
    idempotency_hash: str | None = None


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

    amount: Decimal
    gl_account_id: int | None = None
    transaction_type: str | None = None
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


# --- Subledger events (P4 AR/AP) --------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class SubledgerJournal(PostingEvent):
    """A posting whose accounts and dimensions the subledger has already resolved.

    `module` and `doc_type` are instance fields (shadowing the class-level defaults) because
    one code path serves both roles and all six document kinds — the role/kind matrix lives
    in `app.subledger`, not in a family of event classes.
    """

    module: str
    doc_type: str
    lines: tuple[LineSpec, ...]


@dataclass(frozen=True, kw_only=True)
class PartnerDocumentPosted(SubledgerJournal):
    event_type: ClassVar[str] = "partner_document_posted"


@dataclass(frozen=True, kw_only=True)
class AllocationPosted(SubledgerJournal):
    """Realized FX and settlement discount, posted on the allocation date."""

    event_type: ClassVar[str] = "allocation_posted"
    doc_type: str = DocType.ALLOCATION_JOURNAL


@dataclass(frozen=True, kw_only=True)
class InstrumentMatured(SubledgerJournal):
    """A post-dated cheque reaching maturity: post-dated account → bank."""

    event_type: ClassVar[str] = "instrument_matured"
    doc_type: str = DocType.INSTRUMENT_MATURITY


# --- Stubs for later phases (documented vocabulary, not yet postable) ------------------


@dataclass(frozen=True, kw_only=True)
class _StubEvent(PostingEvent):
    """Placeholder: the emitting module arrives in a later phase (P5 Inventory, P6 OE,
    P7 FX, P9 Fixed Assets, P11 POS, P12 BOM)."""

    lines: tuple[LineSpec, ...] = field(default_factory=tuple)


@dataclass(frozen=True, kw_only=True)
class GoodsReceived(_StubEvent):
    event_type: ClassVar[str] = "goods_received"
    doc_type: ClassVar[str] = "GRN"
    module: ClassVar[str] = "oe"


@dataclass(frozen=True, kw_only=True)
class SupplierInvoiceMatched(_StubEvent):
    event_type: ClassVar[str] = "supplier_invoice_matched"
    doc_type: ClassVar[str] = "SINV"
    module: ClassVar[str] = "ap"


@dataclass(frozen=True, kw_only=True)
class StockAdjusted(_StubEvent):
    event_type: ClassVar[str] = "stock_adjusted"
    doc_type: ClassVar[str] = "ADJ"
    module: ClassVar[str] = "inv"


@dataclass(frozen=True, kw_only=True)
class StockTransferred(_StubEvent):
    event_type: ClassVar[str] = "stock_transferred"
    doc_type: ClassVar[str] = "TRF"
    module: ClassVar[str] = "inv"


@dataclass(frozen=True, kw_only=True)
class StockSold(_StubEvent):
    event_type: ClassVar[str] = "stock_sold"
    doc_type: ClassVar[str] = "COGS"
    module: ClassVar[str] = "inv"


@dataclass(frozen=True, kw_only=True)
class FxRevalued(_StubEvent):
    event_type: ClassVar[str] = "fx_revalued"
    doc_type: ClassVar[str] = "FXR"


@dataclass(frozen=True, kw_only=True)
class DepreciationPosted(_StubEvent):
    event_type: ClassVar[str] = "depreciation_posted"
    doc_type: ClassVar[str] = "DEP"
    module: ClassVar[str] = "fa"


@dataclass(frozen=True, kw_only=True)
class ManufactureCompleted(_StubEvent):
    event_type: ClassVar[str] = "manufacture_completed"
    doc_type: ClassVar[str] = "MFG"
    module: ClassVar[str] = "bom"


@dataclass(frozen=True, kw_only=True)
class PosSaleCompleted(_StubEvent):
    event_type: ClassVar[str] = "pos_sale_completed"
    doc_type: ClassVar[str] = "POS"
    module: ClassVar[str] = "pos"
