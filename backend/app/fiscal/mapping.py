"""Country-neutral DTOs: what Vinea hands an adapter, built from Vinea documents.

These are the vocabulary the rest of the product speaks. Not one field here is named after a
revenue authority's JSON — `quantity`, not `qty`; `unit_price_inclusive`, not `prc`; `tax_class`,
not `taxTyCd` — because the moment a subledger or a screen learns one of those names, rule 12
is gone and a second country is a rewrite instead of an adapter.

**Amounts are base currency.** A document in USD is fiscalized in RWF from its *frozen*
`base_amount`s, not by re-converting at today's rate: the ledger already decided what that sale
was worth in francs, and a receipt that disagreed with the ledger would be a second truth.

**The DTOs carry what was posted, not what the payload needs.** `taxable_amount` and
`tax_amount` on a line are the posted figures; the wire price the authority wants is derived in
the adapter, and the residue between the two is the adapter's problem to place. That direction
matters: a DTO shaped to the payload would make every posting a negotiation with the payload.
"""

import enum
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from app.models.fiscalization import FiscalItemTypeCode, FiscalTaxType, PaymentMethod

ZERO = Decimal(0)


@dataclass(frozen=True)
class FiscalParty:
    """The other side of a document — a customer on a sale, a supplier on a purchase."""

    name: str
    tin: str | None = None
    phone: str | None = None
    address: str | None = None

    @property
    def is_business(self) -> bool:
        """A party with a TIN is a business, and a business sale needs a purchase code."""
        return bool(self.tin)


@dataclass(frozen=True)
class FiscalLine:
    """One line of a fiscalized document, as it was **posted**.

    `taxable_amount` is the line's posted gross in base currency and `tax_amount` the tax it
    attracted; `unit_price_inclusive` is the VAT-inclusive unit price the receipt prints.
    Those three are not independent — `quantity × unit_price_inclusive` need not equal
    `taxable_amount` once a unit price has been rounded to two decimals on a zero-decimal
    base — and reconciling them is the adapter's job, not this DTO's.
    """

    sequence: int
    item_code: str
    item_class_code: str
    name: str
    quantity: Decimal
    unit_price_inclusive: Decimal
    taxable_amount: Decimal
    tax_amount: Decimal
    tax_class: FiscalTaxType
    #: The programmed rate this line's tax class carries, as a percentage. Needed because the
    #: wire tax is **derived** from the taxable amount rather than copied from the posting —
    #: see `rwanda/builders.py` and `docs/rra/contract-notes.md` §7.
    tax_rate_pct: Decimal
    #: The authority's packaging and quantity unit codes, from the item and its UoM.
    package_unit: str
    quantity_unit: str
    discount_percent: Decimal = ZERO
    barcode: str | None = None
    #: The original invoice line this one returns, on a refund.
    returns_line_no: int | None = None


@dataclass(frozen=True)
class FiscalSale:
    """An AR invoice, ready to be fiscalized."""

    #: The authority's invoice number for this sale, claimed in the posting transaction.
    invoice_no: int
    #: Vinea's own number (`INV-000123`) — printed on the receipt beside the authority's.
    document_number: str
    document_date: date
    #: When the entry was posted, in the company's own timezone. The receipt prints both this
    #: and the device's clock, because they are two different facts.
    posted_at: datetime
    party: FiscalParty
    lines: tuple[FiscalLine, ...]
    payment_method: PaymentMethod
    purchase_code: str | None = None
    #: True when any line moves stock — the authority wants a release date only then.
    releases_stock: bool = False
    #: Which daily report this sale falls in — the authority prints it on the receipt, and it
    #: is the number the next Z close will carry. Frozen onto the DTO rather than passed to
    #: the adapter beside it, because the outbox renders a payload from the DTO alone: a
    #: parameter the renderer took separately would be a fact the frozen row did not hold.
    daily_report_no: int = 1
    #: The actor, for the authority's "who keyed this" fields.
    actor_id: str = ""
    actor_name: str = ""
    branch_name: str = ""
    branch_address: str = ""
    remark: str | None = None

    @property
    def total_taxable(self) -> Decimal:
        return sum((line.taxable_amount for line in self.lines), ZERO)

    @property
    def total_tax(self) -> Decimal:
        return sum((line.tax_amount for line in self.lines), ZERO)


@dataclass(frozen=True)
class FiscalRefund(FiscalSale):
    """An AR credit note. A refund in the authority's vocabulary, and it must name exactly one
    original: there is no refund spanning two sales, so the resolution happens before this DTO
    is built and the failure is a posting refusal rather than a payload problem."""

    #: The original sale's authority invoice number.
    original_invoice_no: int = 0
    #: The authority's reason code for the refund.
    reason_code: str = ""


@dataclass(frozen=True)
class FiscalItemRegistration:
    """An item as the authority should hold it."""

    item_code: str
    item_class_code: str
    name: str
    item_type: FiscalItemTypeCode
    origin_country: str
    package_unit: str
    quantity_unit: str
    tax_class: FiscalTaxType
    default_price_inclusive: Decimal
    barcode: str | None = None
    active: bool = True
    actor_id: str = ""
    actor_name: str = ""


@dataclass(frozen=True)
class FiscalPurchase:
    """An AP invoice or return — a purchase **this company originated** and is declaring.

    A purchase the authority is already holding is a different thing and has its own DTO
    (`FiscalPurchaseConfirmation`): that one echoes the authority's figures, this one carries
    the document's. Sending the second as the first would register one supplier invoice twice,
    which is the failure decision 9 spends a whole paragraph on.
    """

    invoice_no: int
    document_number: str
    document_date: date
    posted_at: datetime
    supplier: FiscalParty
    lines: tuple[FiscalLine, ...]
    payment_method: PaymentMethod
    #: The supplier's own reference for this invoice.
    supplier_invoice_no: str | None = None
    is_return: bool = False
    supplier_branch_id: str | None = None
    actor_id: str = ""
    actor_name: str = ""
    remark: str | None = None


class StockMovementFacing(enum.StrEnum):
    """Which side of the business a stock movement faces — the neutral half of decision 10.

    Not the document kind, and that is the point. A revenue authority reports a movement by
    *who it was with* and *which way it went*, so a sale out and a customer return in are the
    same pair of facts read in two directions: `CUSTOMER` plus a direction covers both, and a
    reversal is then the same facing with the direction flipped rather than a case of its own.

    The mapping onto an authority's own in/out codes — Rwanda's `sarTyCd`, VSDC §4.15 — is the
    adapter's, because those codes are the authority's.
    """

    #: A sale and a customer return. What left the shop through the front, or came back in.
    CUSTOMER = "customer"
    #: A goods receipt, an unmatched supplier invoice, a return to a supplier.
    SUPPLIER = "supplier"
    #: An adjustment, a count variance, a write-off — a movement with nobody on the other side.
    INTERNAL = "internal"
    #: One leg of a movement **between branches**. A movement inside one branch changes no
    #: branch's position and is reported by nobody (decision 10).
    TRANSFER = "transfer"


@dataclass(frozen=True)
class FiscalStockLine:
    item_code: str
    item_class_code: str
    name: str
    #: In the item's **base** unit, as a magnitude — the authority holds one quantity per item,
    #: the unit it holds it in is the one the item was registered with, and the direction is
    #: the movement's rather than the line's.
    quantity: Decimal
    unit_cost: Decimal
    value: Decimal
    tax_class: FiscalTaxType
    #: The programmed rate the class carries. Here for the same reason it is on `FiscalLine`:
    #: the wire tax is **derived** from the reported value rather than copied from a posting,
    #: because a stock movement posts a cost and not a tax.
    tax_rate_pct: Decimal
    package_unit: str
    quantity_unit: str
    sequence: int = 1
    barcode: str | None = None


@dataclass(frozen=True)
class FiscalStockIO:
    """One stock movement, reported after the document that caused it.

    `facing` and `outgoing` are the neutral pair the adapter's table turns into the authority's
    own in/out code: a sale's companion issue is not the same movement as a stock-count
    shrinkage, and the authority reports them differently.
    """

    stock_no: int
    facing: StockMovementFacing
    #: True when stock left. One row is one direction: a posting that both receives and issues
    #: is two movements, because the authority's code says which way it went.
    outgoing: bool
    occurred_on: date
    lines: tuple[FiscalStockLine, ...]
    actor_id: str = ""
    actor_name: str = ""
    remark: str | None = None


@dataclass(frozen=True)
class FiscalStockMaster:
    """On-hand per item after a movement, **snapshotted when the row is enqueued**.

    Computed in the same transaction as the moves rather than when the row is sent, because by
    the time the queue drains the shelf has moved on — and what the authority is being told is
    what was true at the moment of the movement it just received.
    """

    item_code: str
    quantity_on_hand: Decimal
    actor_id: str = ""
    actor_name: str = ""


@dataclass(frozen=True)
class FiscalPurchaseConfirmation:
    """An authority-held purchase, confirmed or declined by an operator (decision 9).

    The one DTO here whose figures are **not Vinea's**, and deliberately so: a confirmation
    echoes the record the authority is already holding, and Vinea has no document of its own to
    derive it from — the whole point of the feed is that somebody *else* registered this sale.
    So the record travels verbatim in `source` and only the adapter reads inside it, which is
    the same boundary every other DTO keeps. What is neutral is the decision: accepted or not,
    by whom, under which number.
    """

    invoice_no: int
    #: The authority's own record of the purchase, exactly as it was fetched.
    source: dict
    accepted: bool = True
    actor_id: str = ""
    actor_name: str = ""


@dataclass(frozen=True)
class FiscalImportDecision:
    """One customs line, approved or declined, naming the item it became (decision 9).

    Approval is a **compliance acknowledgment**: it moves no stock and posts nothing. The goods
    reached the ledger through a goods receipt, and saying so twice would double them.
    """

    #: The authority's own keys for the declaration line, verbatim — a customs declaration is
    #: the authority's document and Vinea holds no version of it to rebuild.
    source: dict
    approved: bool
    item_code: str | None = None
    item_class_code: str | None = None
    note: str | None = None
    actor_id: str = ""
    actor_name: str = ""


@dataclass(frozen=True)
class FiscalPurchaseFeedEntry:
    """One purchase the authority holds against this taxpayer, in Vinea's words.

    `source` is the authority's record kept whole beside the fields Vinea reads, because the
    confirmation has to echo those figures rather than a re-derivation of them.
    """

    supplier: FiscalParty
    supplier_invoice_no: int
    supplier_branch_id: str | None = None
    document_date: date | None = None
    total_taxable: Decimal = ZERO
    total_tax: Decimal = ZERO
    total_amount: Decimal = ZERO
    source: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FiscalImportEntry:
    """One customs declaration line the authority is holding, in Vinea's words."""

    #: The authority's keys for the line. Three fields identify it — a task, a declaration and
    #: a sequence within it — and all three go back on the decision.
    task_code: str
    declaration_no: str
    line_no: int
    declared_on: date | None = None
    hs_code: str | None = None
    name: str | None = None
    origin_country: str | None = None
    packages: Decimal | None = None
    package_unit: str | None = None
    quantity: Decimal | None = None
    quantity_unit: str | None = None
    supplier_name: str | None = None
    agent_name: str | None = None
    foreign_amount: Decimal | None = None
    foreign_currency: str | None = None
    foreign_rate: Decimal | None = None
    source: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FiscalCodeEntry:
    """One row of a synced code table."""

    code_class: str
    code_class_name: str | None
    code: str
    name: str
    description: str | None = None
    sort_order: int | None = None
    user_defined: tuple[str | None, str | None, str | None] = (None, None, None)
    active: bool = True


@dataclass(frozen=True)
class FiscalItemClassEntry:
    code: str
    name: str
    level: int | None = None
    tax_class: str | None = None
    major_target: bool | None = None
    active: bool = True


@dataclass(frozen=True)
class FiscalSyncResult:
    """What a watermarked sync brought back, and the watermark to store **only on success**.

    The two travel together on purpose: the discipline the documents ask for is "store the new
    watermark after a success and not before", and a result that carried the rows without the
    watermark would invite storing it in the wrong place.
    """

    codes: tuple[FiscalCodeEntry, ...] = ()
    item_classes: tuple[FiscalItemClassEntry, ...] = ()
    purchases: tuple[FiscalPurchaseFeedEntry, ...] = ()
    imports: tuple[FiscalImportEntry, ...] = ()
    watermark: str | None = None
