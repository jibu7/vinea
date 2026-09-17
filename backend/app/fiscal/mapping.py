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
    """An AP invoice or return, or a confirmation of one the authority is already holding.

    `confirming` is what tells the two apart: a purchase this company originated is registered,
    one the authority already knows about is confirmed with *its* figures. Sending the second
    as the first would register the supplier's invoice twice.
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
    confirming: bool = False
    accepted: bool = True
    supplier_branch_id: str | None = None
    actor_id: str = ""
    actor_name: str = ""
    remark: str | None = None


@dataclass(frozen=True)
class FiscalStockLine:
    item_code: str
    item_class_code: str
    name: str
    #: In the item's **base** unit — the authority holds one quantity per item, and the unit
    #: it holds it in is the one the item was registered with.
    quantity: Decimal
    unit_cost: Decimal
    value: Decimal
    tax_class: FiscalTaxType
    taxable_amount: Decimal
    tax_amount: Decimal
    package_unit: str
    quantity_unit: str
    sequence: int = 1
    barcode: str | None = None


@dataclass(frozen=True)
class FiscalStockIO:
    """One stock movement, reported after the document that caused it.

    `movement_type` is the authority's own in/out code, resolved from the source document by
    the adapter's table — a sale's companion issue is not the same movement as a stock-count
    shrinkage, and the authority reports them differently.
    """

    stock_no: int
    movement_type: str
    occurred_on: date
    lines: tuple[FiscalStockLine, ...]
    #: The sale or purchase this movement belongs to, where it has one.
    source_invoice_no: int | None = None
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
    rows: tuple[dict, ...] = field(default_factory=tuple)
    watermark: str | None = None
