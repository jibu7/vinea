"""Typed posting events (ADR-05). Modules emit these; only `app.kernel.posting.post`
turns them into journal entries.

Amounts on line specs are **signed** in the transaction currency: positive = debit,
negative = credit. Events for modules that do not exist yet are declared here as stubs so
the event vocabulary is fixed now; `post()` rejects them with `unsupported_event` until
their phase lands.

A phase that lands either turns its stub into a real class or deletes it and says which real
event carries the vocabulary instead — never leaves it standing as a class nothing constructs.
See the P6 block below `StockAdjusted` for the worked example.
"""

import enum
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import ClassVar

from app.kernel.sequences import DocType
from app.models.inventory import INVENTORY_MODULE

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


# --- Inventory events (P5 stock ledger) ------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class StockJournal(SubledgerJournal):
    """A posting whose accounts and dimensions the **stock service** has already resolved.

    Inventory is a subledger like AR/AP, so it reuses their shape rather than a family of its
    own: `module` and `doc_type` are instance fields because one code path serves adjustments,
    journal batches, transfers and counts, and the kind/direction matrix lives in
    `app.inventory.stock`, not in the event classes.

    The account on each line is always explicit. Inventory cannot use the determination
    chain's item link, because which inventory account a line belongs to depends on the
    **warehouse** as well as the item: the same item in the in-transit warehouse posts to the
    in-transit account (decision 6), and only the stock service knows where the stock is.
    """

    module: str = INVENTORY_MODULE
    doc_type: str = DocType.INV_ADJUSTMENT


@dataclass(frozen=True, kw_only=True)
class StockReceived(StockJournal):
    """Quantity into a location, valued at the cost supplied (decision 13)."""

    event_type: ClassVar[str] = "stock_received"


@dataclass(frozen=True, kw_only=True)
class StockIssued(StockJournal):
    """Quantity out of a location, valued at the item's weighted average — or at whatever
    the location had left, when the issue empties it (decision 4)."""

    event_type: ClassVar[str] = "stock_issued"


@dataclass(frozen=True, kw_only=True)
class StockRevalued(StockJournal):
    """Value without quantity: the move shape a write-down or write-up takes."""

    event_type: ClassVar[str] = "stock_revalued"


@dataclass(frozen=True, kw_only=True)
class StockTransferred(StockJournal):
    """One leg of a transfer — source → in-transit, or in-transit → destination. Both moves
    are inventory-account lines of the same entry, so the leg needs no contra: what one
    location gives up, the other takes, at the frozen value (decision 6)."""

    event_type: ClassVar[str] = "stock_transferred"
    doc_type: str = DocType.INV_TRANSFER


@dataclass(frozen=True, kw_only=True)
class StockAdjusted(StockJournal):
    """A posting that mixes directions — the journal batch, and a count's variance document,
    where one line puts stock in and the next takes it out under one header."""

    event_type: ClassVar[str] = "stock_adjusted"


@dataclass(frozen=True, kw_only=True)
class StockSold(StockJournal):
    """The companion issue behind a sale: goods leaving on an AR invoice, at the average or
    at the flush (P6 decision 2).

    **This was a stub until P6 step 5** and is now the real class, which is what decision 13
    asked for: a `StockJournal` on the `STK` companion document type. It exists as its own
    class rather than as `StockIssued` with a different `doc_type` because the `event_type`
    is written onto the journal entry, and "why did this stock leave" is the question that
    column is there to answer — P10's sales analysis reads entries, not documents.

    It covers the **sale** only. A return to supplier is also a companion issue on `STK`, and
    it posts under `StockIssued`, because calling it `stock_sold` would put a false answer in
    that column. Decision 13 reads as though one class covers every companion issue; that is
    the one place this step departs from it, and the reason is written here.
    """

    event_type: ClassVar[str] = "stock_sold"
    doc_type: str = DocType.STOCK_COMPANION


# --- Tax and unrealized FX (P7 decisions 12 and 13) -----------------------------------


@dataclass(frozen=True, kw_only=True)
class VatReturnPosted(SubledgerJournal):
    """The settlement entry of a filed VAT return (decision 12).

    Its own module, `tax`, for two reasons. It is what makes the return reversible **only
    through the return** — `module_reversal("tax")` is the window, so a reversal asked for from
    the general ledger is refused with `reverse_via_module_document` and cannot leave
    `vat_returns.status` saying `posted` over an entry that is gone. And it is what tells the
    *next* return's tie that these lines are a payment rather than tax: they sit on the VAT
    accounts carrying the same tax codes, and no amount distinguishes them, so the module does.
    """

    event_type: ClassVar[str] = "vat_return_posted"
    module: str = "tax"
    doc_type: str = DocType.VAT_RETURN


@dataclass(frozen=True, kw_only=True)
class FxRevalued(SubledgerJournal):
    """Unrealized FX on open foreign-currency partner documents (decision 13).

    Module `gl`, as ADR-05 reserved it: the accounts it touches are ordinary GL accounts, and
    deliberately **not** the AR/AP controls — those are subledger-only and their balance is the
    sum of open items at booking rates, which a revaluation posting into them would break. The
    balance sheet reads `1200` and `1290` together instead.

    The run posts this entry at the revaluation date and its mirror the following day, in one
    transaction, so the date carries the revaluation and the next period does not. Realized FX
    at allocation stays P4's.
    """

    event_type: ClassVar[str] = "fx_revalued"
    module: str = "gl"
    doc_type: str = DocType.FX_REVALUATION


# --- ADR-05's P6 vocabulary, and the classes that actually carry it --------------------
#
# `GoodsReceived`, `SupplierInvoiceMatched` and `StockSold` were declared here as stubs so the
# vocabulary would be fixed before Order Entry existed. P6 built it, and decision 13 said what
# to do with them: ADR-05's names are **vocabulary, not a promise of one class each**. Two of
# the three had no posting of their own to name, so they are gone rather than left standing as
# classes nothing constructs. The mapping, which is the part worth keeping:
#
#   * `GoodsReceived` → **`StockReceived`**, posted by `receive_stock()` on the `GRN` document
#     type (`app/order_entry/grn.py`). A goods receipt is a stock receipt whose contra is the
#     GRN accrual; it needed no event of its own, only a contra account and a document type.
#   * `SupplierInvoiceMatched` → **`PartnerDocumentPosted`**, posted by the AP invoice whose
#     line carries `grn_line_id` (`app/order_entry/matching.py`). The match is not a separate
#     posting: it is what the ordinary supplier-invoice entry does when its line names a
#     receipt — relieve the accrual at the frozen value and send the difference to PPV.
#   * `StockSold` → **`StockSold` above**, now a real `StockJournal`.
#
# --- Stubs for later phases (documented vocabulary, not yet postable) ------------------


@dataclass(frozen=True, kw_only=True)
class _StubEvent(PostingEvent):
    """Placeholder: the emitting module arrives in a later phase (P5 Inventory, P6 OE,
    P7 FX, P9 Fixed Assets, P11 POS, P12 BOM)."""

    lines: tuple[LineSpec, ...] = field(default_factory=tuple)

# `FxRevalued` stood here as a stub from P2 until P7 step 4, which built it: it is a real
# `SubledgerJournal` above, posted by `app/subledger/revaluation.py`. ADR-05 called it the last
# kernel-side stub the fiscalization phase retires, and this is that.
#
# **P8 Banking adds no event, and that is the decision rather than an omission** (decision 11).
# Every posting the phase causes already has an owner: a fee or a deposit posted from a
# statement line is a `CashbookEntry`, a payment run's settlements are `PartnerDocumentPosted`
# and their allocations `AllocationPosted`, and bank revaluation is `FxRevalued` with a wider
# scope. A `BankStatementImported` or a `PaymentRunPosted` would be an event with no ledger
# consequence of its own — a second name for a posting that already has one — and the module
# string it carried would be the second posting authority ADR-05 exists to prevent.
# `app/models/banking.BANKING_MODULE` is kept as a name for the thing that does not happen, and
# `tests/banking/test_boundary.py` asserts no entry is ever posted under it.


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
