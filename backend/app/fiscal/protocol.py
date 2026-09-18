"""The fiscalization boundary (Master Plan §2.2, architecture rule 12).

**Country logic never leaks out of an adapter.** Everything above this line — the posting
engine, AR/AP, the stock service, the screens — speaks the DTOs in `app.fiscal.mapping` and
calls the methods below — fourteen business calls, plus the three seams P7 step 2 added and
names where they are declared. Everything about how a particular revenue authority spells its
JSON, numbers its receipts or names its codes lives under the adapter that implements them,
and `tests/fiscal/test_boundary.py` fails the build if any module outside
`app/fiscal/rwanda/` imports that package.

The Protocol is **grown to what Rwanda needs**, deliberately and with that stated. A boundary
designed from one implementation is a boundary that fits one implementation; the honest move
is to say so now, keep the *vocabulary* country-neutral, and widen it when the second country
arrives rather than pretending to have anticipated it. What is already general is the shape:
every call takes a device, a DTO and the device's decrypted CMC key, returns a `FiscalResult`,
and raises nothing for a refusal the authority made — a refusal is an outcome, not an
exception, because the outbox has to record it and decide whether to retry.

Only transport failures raise. `FiscalTransportError` means the request did not get an answer;
`FiscalTimeout` means it may have been received and the answer lost, which is the one case
that must never be retried blindly (a duplicate returns no receipt data, so the sale would be
registered with nothing to print).
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from app.fiscal.mapping import (
    FiscalImportDecision,
    FiscalItemRegistration,
    FiscalPurchase,
    FiscalPurchaseConfirmation,
    FiscalRefund,
    FiscalSale,
    FiscalStockIO,
    FiscalStockMaster,
    FiscalSyncResult,
)
from app.models.fiscalization import FiscalDevice, FiscalOutboxKind

#: The one result code that means "accepted". Everything else is a refusal with a reason.
RESULT_OK = "000"


class FiscalTransportError(Exception):
    """The request never reached the authority, or the connection failed before it was read.

    Safe to retry: nothing was registered. The outbox backs off and tries again.
    """


class FiscalTimeout(FiscalTransportError):
    """The request left and no answer came back.

    **Not safe to retry.** The authority may be holding the sale; a resend is a duplicate,
    and a duplicate returns no receipt data, so the second attempt would leave a registered
    sale with nothing to print. The outbox marks the row `unknown` and a person resolves it by
    asking the device what it has — never by sending again.
    """


@dataclass(frozen=True)
class FiscalResult:
    """What an adapter call came back with.

    `ok` is the only thing most callers read. `code` and `message` are the authority's own,
    kept verbatim because they are what an operator sees on the queue screen and what they
    quote when they ring the authority; `data` is the response body with device keys removed.
    """

    ok: bool
    code: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    #: Round-trip time in milliseconds, for the queue screen's "how slow is this device".
    elapsed_ms: int = 0

    @classmethod
    def success(cls, data: dict[str, Any] | None = None, *, elapsed_ms: int = 0) -> "FiscalResult":
        return cls(ok=True, code=RESULT_OK, message="OK", data=data or {}, elapsed_ms=elapsed_ms)


@dataclass(frozen=True)
class DeviceIdentity:
    """What initialization hands back: who the authority thinks this device is, the keys it
    will sign with, and the counters it is already holding.

    The counters are the whole reason `initialize_device` is also the *verification* call. A
    row whose answer never arrived is resolved by re-initializing and comparing: a counter
    below ours means the authority never saw it, at or above means it did.
    """

    sdc_id: str
    mrc_no: str | None = None
    dvc_id: str | None = None
    cmc_key: str | None = None
    intrl_key: str | None = None
    sign_key: str | None = None
    #: The last numbers the authority holds for this device, when it reports them.
    last_sale_invc_no: int | None = None
    last_purchase_invc_no: int | None = None
    last_sale_rcpt_no: int | None = None


@dataclass(frozen=True)
class FiscalReceiptData:
    """One receipt, normalised across every response shape an authority might use.

    `normalize_receipt()` in an adapter is what makes this one type rather than two: the VSDC
    and OSDC sales responses name the same six facts differently, and the difference stops at
    the adapter.
    """

    rcpt_no: int
    tot_rcpt_no: int
    intrl_data: str
    rcpt_sign: str
    sdc_id: str
    #: When the device signed, as an **instant**. Parsed by the adapter because both the format
    #: and the zone are the authority's — RRA stamps `yyyyMMddHHmmss` in Kigali, and a neutral
    #: module reading that as UTC would shift every receipt by two hours on a daily report.
    sdc_datetime: datetime
    mrc_no: str | None = None
    invc_no: int | None = None
    #: The refunded sale's number, on a refund. Read off the request by the adapter, because
    #: the field is the authority's and a neutral module reaching into a frozen payload for it
    #: would be exactly the leak rule 12 is about.
    org_invc_no: int | None = None
    #: What the receipt's verification code encodes, in the authority's own format. Built by
    #: the adapter because the format is the authority's (Rwanda: CIS §7.24.7) and stored
    #: rather than recomputed, so a receipt reprinted years later carries the string that was
    #: issued with it rather than whatever today's code would produce.
    qr_payload: str = ""


@dataclass(frozen=True)
class DeclaredClassTotals:
    """One tax class, as the authority received it."""

    #: `A`–`D`, the class the line was declared under. Neutral: `tax_codes.fiscal_tax_type` is
    #: Vinea's own mapping onto it, and it is the same four buckets a receipt prints.
    tax_class: str
    taxable: Decimal
    tax: Decimal
    rate: Decimal


@dataclass(frozen=True)
class DeclaredTotals:
    """What one receipt **declared**, normalised out of the payload it was issued against.

    The sibling of `FiscalReceiptData`, and it exists for the same reason. That one normalises
    the authority's *answer*; this one normalises the *request* that answer was given to, so a
    daily report can state what was declared without a neutral module reading a field name.

    **Why not the ledger.** A Z says what the authority signed, and on a discounted line the
    wire's taxable amount is `splyAmt − dcAmt` — derived from the two-decimal inclusive price —
    **not** the posted gross (`rwanda/builders.py` says so where it computes them). The two
    coincide on round, undiscounted prices and part company everywhere else, so a Z computed
    off `journal_lines` would be a Z that quietly disagreed with the receipts it is a summary
    of. The VAT return reads the ledger because a return declares what was posted; a Z reads
    the receipts because a Z reports what was declared. Both are right, and they are not the
    same question.
    """

    gross: Decimal
    taxable: Decimal
    tax: Decimal
    #: How many **lines** the receipt carried — the receipt's own "ITEMS NUMBER" (CIS §7.27),
    #: and the authority's `totItemCnt`.
    item_count: int
    #: How many **things** it sold — Σ of the line quantities. A different question from
    #: `item_count` and the one a day's report asks: §19.1's items figure is "how many items
    #: were sold today", which on a four-line invoice of 10 + 1 + 5 + 2 is 18, not 4.
    quantity: Decimal
    #: Σ of the line discounts on the receipt. §19.1 prints the day's discounts, so a Z that
    #: could not state them would fail the checkpoint sheet.
    discount: Decimal
    classes: tuple[DeclaredClassTotals, ...] = ()
    #: True when `gross`/`taxable`/`tax` came from the authority's **answer** rather than from
    #: the request it answered. See `normalize_declared_totals`: the authority countersigns
    #: three totals and returns nothing else, so a Z's class split is always the request's and
    #: only its headline figures can be the authority's own.
    countersigned: bool = False



@dataclass(frozen=True)
class TinLookup:
    """`Verify TIN` on the customer screen. A synchronous read, never a queue row: the person
    keying an invoice is waiting for the answer, and a queued lookup helps nobody."""

    tin: str
    found: bool
    name: str | None = None
    status: str | None = None


@runtime_checkable
class FiscalizationAdapter(Protocol):
    """One revenue authority, one implementation.

    Two exist: `NullAdapter`, which records every call and sends nothing, and the Rwanda EBM
    adapter. A third country is a third class here and rows in `fiscal_codes` — not a branch
    anywhere above this line.
    """

    # --- Setup -----------------------------------------------------------------------------
    def initialize_device(
        self, device: FiscalDevice, *, cmc_key: str | None = None
    ) -> tuple[FiscalResult, DeviceIdentity | None]:
        """Register (or re-verify) the device and read back its identity, keys and counters."""
        ...

    def sync_codes(
        self, device: FiscalDevice, *, since: str | None, cmc_key: str | None = None
    ) -> tuple[FiscalResult, FiscalSyncResult]:
        """The authority's published code tables, from a `since` watermark."""
        ...

    def sync_item_classes(
        self, device: FiscalDevice, *, since: str | None, cmc_key: str | None = None
    ) -> tuple[FiscalResult, FiscalSyncResult]:
        """The authority's item classification, from a `since` watermark."""
        ...

    def lookup_tin(
        self, device: FiscalDevice, tin: str, *, cmc_key: str | None = None
    ) -> tuple[FiscalResult, TinLookup]:
        """Is this a taxpayer the authority knows, and what is it called?"""
        ...

    # --- The outbox seam -------------------------------------------------------------------
    #
    # Two calls, and they exist because a queue row is **frozen at enqueue and sent later**
    # (decision 4). The fourteen business calls below each do both halves at once, which is
    # what a caller holding a document wants; the drainer holds neither a document nor a
    # session that ever saw one — it holds bytes that were rendered inside the posting
    # transaction, and it must send exactly those. A drainer that re-rendered from a DTO
    # would send whatever today's masters say, which is the one thing the frozen payload is
    # for.
    #
    # So `render` is what the posting transaction calls and `send` is what the drainer calls;
    # the business methods are `send(render(...))` and stay the vocabulary a caller reads.
    def render(
        self, device: FiscalDevice, kind: FiscalOutboxKind, document: Any
    ) -> dict[str, Any]:
        """The wire payload for one queue row, ready to be frozen. Device keys are never in
        it: they are added at `send`, from a caller that decrypted one on purpose."""
        ...

    def send(
        self,
        device: FiscalDevice,
        kind: FiscalOutboxKind,
        payload: dict[str, Any],
        *,
        cmc_key: str | None = None,
    ) -> tuple[FiscalResult, FiscalReceiptData | None]:
        """Send a payload rendered earlier, exactly as it was frozen. The receipt comes back
        on the kinds that produce one and is `None` on the rest."""
        ...

    # --- Masters ---------------------------------------------------------------------------
    def mint_item_code(
        self,
        *,
        origin_country: str,
        product_type: str,
        packaging_unit: str,
        quantity_unit: str,
        sequence_no: int,
    ) -> str:
        """The authority's own item code, composed from the item's attributes and a sequence.

        Here rather than in a service because the composition is a *format the authority
        publishes* — Rwanda's is VSDC §4.17 — and a service that built it would be country
        logic above the boundary. What the service supplies is the sequence, claimed gaplessly
        from `document_sequences` like every other number this build issues.
        """
        ...

    def register_item(
        self, device: FiscalDevice, item: FiscalItemRegistration, *, cmc_key: str | None = None
    ) -> FiscalResult:
        """Register or update an item. Idempotent by item code on the authority's side."""
        ...

    # --- Sales -----------------------------------------------------------------------------
    def fiscalize_sale(
        self, device: FiscalDevice, sale: FiscalSale, *, cmc_key: str | None = None
    ) -> tuple[FiscalResult, FiscalReceiptData | None]:
        ...

    def fiscalize_refund(
        self, device: FiscalDevice, refund: FiscalRefund, *, cmc_key: str | None = None
    ) -> tuple[FiscalResult, FiscalReceiptData | None]:
        ...

    def normalize_receipt(
        self,
        device: FiscalDevice,
        response: dict[str, Any] | None,
        *,
        invc_no: int | None = None,
    ) -> FiscalReceiptData | None:
        """One receipt out of whatever shape the authority answered in.

        On the Protocol rather than inside the adapter's own `send`, because the queue's
        **manual attach** needs it: a person reading a receipt off the authority's portal is
        keying the same facts, and normalising them anywhere else would be a second opinion
        about what a receipt is. `None` when the fields are not a receipt — a success carrying
        no receipt data is not one, and a part-filled row would make "sent means signed" false.
        """
        ...

    def redact_payload(self, value: Any) -> Any:
        """The same payload with every secret of this authority's replaced by a marker.

        On the Protocol because **which fields are secret is the authority's fact**: Rwanda's
        three device keys are `cmcKey`, `intrlKey` and `sgnKey`, nested under `data.info`, and
        a neutral module that knew that would be country logic above the boundary (rule 12).

        The drainer already applies this before anything is stored, so a stored row is clean.
        It is on the Protocol because the *queue screen* applies it again on the way out
        (`app/fiscal/enquiries.py`): "the stored rows are clean" is a property of today's
        writer, and a screen that shows a payload will outlive it.
        """
        ...

    def normalize_declared_totals(
        self,
        request: dict[str, Any] | None,
        response: dict[str, Any] | None = None,
    ) -> DeclaredTotals | None:
        """What one receipt declared, in neutral vocabulary — **the answer where there is one**.

        The read half of `normalize_receipt`. A daily report (decision 11) totals what the
        authority signed, and "signed" means its own answer: the request is what Vinea sent, and
        the two are equal exactly until the day they are not, which is the day a Z has to be
        right. So `gross`, `taxable` and `tax` are taken from the response when it carries them
        and `countersigned` says so.

        Everything else has to come from the request, because the authority does not send it
        back: the per-class split, the rates, the item count and the line discounts appear in no
        sales response. That asymmetry is the authority's, not a shortcut — an adapter whose
        response carries more may prefer more of it.

        `None` when the payload is not a sale or refund: a stock or purchase row declared no
        receipt and is no part of anybody's till.
        """
        ...

    # --- Purchases and imports -------------------------------------------------------------
    def register_purchase(
        self, device: FiscalDevice, purchase: FiscalPurchase, *, cmc_key: str | None = None
    ) -> FiscalResult:
        ...

    def fetch_purchase_feed(
        self, device: FiscalDevice, *, since: str | None, cmc_key: str | None = None
    ) -> tuple[FiscalResult, FiscalSyncResult]:
        """Purchases the authority holds against this taxpayer, from a watermark."""
        ...

    def confirm_purchase(
        self,
        device: FiscalDevice,
        confirmation: FiscalPurchaseConfirmation,
        *,
        cmc_key: str | None = None,
    ) -> FiscalResult:
        """Accept or reject one of them. The same endpoint as `register_purchase`, with the
        authority's own figures and a registration type that says "this is a confirmation"."""
        ...

    # --- Stock -----------------------------------------------------------------------------
    def report_stock_io(
        self, device: FiscalDevice, movement: FiscalStockIO, *, cmc_key: str | None = None
    ) -> FiscalResult:
        ...

    def report_stock_master(
        self, device: FiscalDevice, master: FiscalStockMaster, *, cmc_key: str | None = None
    ) -> FiscalResult:
        """On-hand after the movement. Follows the movement, which follows the sale."""
        ...

    # --- Imports ---------------------------------------------------------------------------
    def fetch_imports(
        self, device: FiscalDevice, *, since: str | None, cmc_key: str | None = None
    ) -> tuple[FiscalResult, FiscalSyncResult]:
        ...

    def update_import(
        self,
        device: FiscalDevice,
        *,
        declaration: FiscalImportDecision,
        cmc_key: str | None = None,
    ) -> FiscalResult:
        """Approve or reject a customs line, naming the item it became."""
        ...
