"""The posting contract (decisions 2, 3, 5, 6 and 7): what a fiscalized AR document must be,
and the queue row it leaves behind.

Two halves, deliberately separated, and the separation is the whole design.

**`plan()` refuses, and writes nothing.** It runs before the companion stock entry, before the
credit-limit audit, before the journal — so a document that cannot be fiscalized is refused
before the first write rather than rolled back after several. Every refusal names the field it
is about, because the alternative is an operator staring at a banner that says "this cannot be
posted" with no idea which line is wrong.

**`enqueue()` writes, inside the posting transaction.** It registers any item the authority has
not been told about (ahead of the sale in the queue, because creation order is queue order),
claims the authority's invoice number from the gapless `FIS` run, renders the payload from the
document *as posted*, and inserts the row. By the time `post_document()` returns, the row is
there; it commits with the document or not at all.

Nothing here calls a revenue authority. A sale is fiscalized because it was posted, by a row —
never by a call made from a request handler.

**What fiscalizes** (decision 3): an AR invoice is a sale, an AR credit note is a refund. A
settlement, an allocation, a journal batch and every AP document are not sales — AP documents
are purchases and arrive at step 3. A kit goes out as its parent line; its components are on
the stock report and never on the receipt.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.fiscal import devices as device_service
from app.fiscal import items as item_service
from app.fiscal import outbox
from app.fiscal.mapping import FiscalLine, FiscalParty, FiscalRefund, FiscalSale
from app.fiscal.protocol import FiscalizationAdapter
from app.fiscal.registry import adapter_for
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.money import to_base
from app.kernel.sequences import DocType, claim_number
from app.models.company import Branch, Company
from app.models.currency import Currency
from app.models.fiscalization import (
    FiscalDailyReport,
    FiscalDevice,
    FiscalItem,
    FiscalOutboxKind,
    FiscalOutboxStatus,
    FiscalTaxType,
    PaymentMethod,
)
from app.models.inventory import Item, Uom
from app.models.partner import Partner
from app.models.subledger import (
    DocumentKind,
    DocumentStatus,
    PartnerDocument,
    PartnerDocumentLine,
    PartnerRole,
    TaxMode,
)
from app.models.tax import TaxCode
from app.models.user import User

ZERO = Decimal(0)
HUNDRED = Decimal(100)

#: Re-exported from the outbox, which owns them: a queue row and a journal line point at a
#: partner document by the same name.
DOCUMENT_SOURCE = outbox.DOCUMENT_SOURCE
REVERSAL_SOURCE = outbox.REVERSAL_SOURCE

#: The kinds that fiscalize, and as what. A settlement is not here and neither is anything on
#: the AP side: a receipt against an invoice is not a second sale, and registering it as one
#: would double every cash customer's turnover.
OUTBOX_KIND_BY_DOCUMENT: dict[DocumentKind, FiscalOutboxKind] = {
    DocumentKind.INVOICE: FiscalOutboxKind.SALE,
    DocumentKind.CREDIT_NOTE: FiscalOutboxKind.REFUND,
}


@dataclass(frozen=True)
class PlannedLine:
    """One document line, resolved to what the authority needs of it.

    Built during `plan()` — which is to say, *before* anything is written — so that every
    lookup that could fail has failed by the time the first row is inserted.
    """

    index: int
    item: Item
    quantity: Decimal
    #: The VAT-inclusive unit price in the **document's** currency. Converted to base at
    #: enqueue, when the rate the document booked at is known to be the rate it kept.
    unit_price_inclusive: Decimal
    tax_class: FiscalTaxType
    tax_rate_pct: Decimal
    quantity_unit: str
    discount_percent: Decimal
    net: Decimal
    tax: Decimal
    description: str | None
    returns_line_no: int | None = None


@dataclass(frozen=True)
class FiscalPlan:
    """Everything `enqueue()` will need, and proof that none of it can fail."""

    device: FiscalDevice
    adapter: FiscalizationAdapter
    kind: FiscalOutboxKind
    lines: tuple[PlannedLine, ...]
    payment_method: PaymentMethod
    purchase_code: str | None = None
    original_invoice_no: int | None = None
    reason_code: str | None = None


def default_payment_method(has_terms: bool) -> PaymentMethod:
    """`credit` when the document has payment terms, `cash` otherwise (decision 7).

    Defaulted rather than asked for. A required field nobody knows the answer to at the moment
    they are keying is a field that gets the same wrong answer every time; a default that is
    right nearly always, and editable, is the better trade.
    """
    return PaymentMethod.CREDIT if has_terms else PaymentMethod.CASH


# --- Planning (refusals only; nothing is written) -------------------------------------------


def plan(
    db: Session,
    company_id: int,
    *,
    role: PartnerRole,
    kind: DocumentKind,
    is_journal: bool,
    partner: Partner,
    computed: list,
    branch_id: int | None,
    tax_mode: TaxMode,
    purchase_code: str | None,
    refund_of_document_id: int | None,
    refund_reason: str | None,
    payment_method: PaymentMethod,
) -> FiscalPlan | None:
    """The decision-2/3/7/8 refusals, or a plan. `None` means "this document does not
    fiscalize", which is the answer for every non-fiscalized company and for every document
    kind that is not a sale.

    A **journal batch** is excluded by `is_journal`: `ARJN` documents are opening balances and
    corrections keyed under the `JNL` transaction type, not sales, and registering one as a
    sale would put a balance brought forward on a customer's receipt.
    """
    outbox_kind = OUTBOX_KIND_BY_DOCUMENT.get(kind)
    if outbox_kind is None or role != PartnerRole.AR or is_journal:
        return None
    if not device_service.is_fiscalized(db, company_id):
        return None

    device = _require_device(db, company_id, branch_id)
    company = db.get(Company, company_id)
    adapter = adapter_for(company.fiscal_country if company else None)

    lines = tuple(
        _plan_line(db, index, line, tax_mode=tax_mode)
        for index, line in enumerate(computed)
        if not line.is_kit_component
    )
    if not lines:
        # Every line was a kit component, which cannot happen for a keyed document — a
        # component only exists beneath a parent. Refusing rather than sending an empty
        # `itemList`, which RRA answers `801` to.
        raise PostingError(
            "A fiscalized document must carry at least one item line",
            code="fiscal_item_required",
            field_errors={"lines": ["at least one item line"]},
        )

    if partner.tin and not purchase_code:
        raise PostingError(
            f"{partner.name} has a TIN, so RRA requires the purchase code from their EBM "
            "portal on this sale. Without it the sale is refused with 881.",
            code="purchase_code_required",
            field_errors={"purchase_code": ["required for a customer with a TIN"]},
        )

    original_invoice_no: int | None = None
    reason: str | None = None
    if outbox_kind == FiscalOutboxKind.REFUND:
        original_invoice_no = _resolve_refund_original(
            db, company_id, computed=computed, refund_of_document_id=refund_of_document_id
        )
        reason = _require_refund_reason(refund_reason)

    return FiscalPlan(
        device=device,
        adapter=adapter,
        kind=outbox_kind,
        lines=lines,
        payment_method=payment_method,
        purchase_code=purchase_code,
        original_invoice_no=original_invoice_no,
        reason_code=reason,
    )


def _require_device(db: Session, company_id: int, branch_id: int | None) -> FiscalDevice:
    """The active device of the branch this document will post to.

    `branch_id` is what the caller keyed, which is often nothing: the kernel resolves an unset
    branch to the company's main branch when it posts. So the same resolution happens here,
    before the posting rather than inside it — otherwise a company with one branch and one
    device would be told it has no device on a document that is about to post to that very
    branch.
    """
    resolved = branch_id
    if resolved is None:
        main = db.scalar(
            select(Branch).where(
                Branch.company_id == company_id, Branch.is_main, Branch.is_active
            )
        )
        resolved = main.id if main is not None else None
    device = (
        device_service.active_device_for_branch(db, company_id, resolved)
        if resolved is not None
        else None
    )
    if device is None:
        raise PostingError(
            "This branch has no active EBM device. A sale that reached the ledger and never "
            "reached RRA is the failure this refusal exists to prevent — activate the "
            "branch's device, or key the document on a branch that has one.",
            code="fiscal_device_missing",
            field_errors={"branch_id": ["no active EBM device on this branch"]},
        )
    return device


def _plan_line(db: Session, index: int, line, *, tax_mode: TaxMode) -> PlannedLine:
    """One line's fiscal resolution, refusing on the field that is missing.

    The order is the order an operator can fix them in: no item, then no class on the item,
    then no unit mapping, then no tax class. Each refusal names one field, because "this line
    is not fiscalizable" is not something anybody can act on.
    """
    field = f"lines.{index}"
    if line.item is None:
        raise PostingError(
            f"Line {index + 1} has no item. Every line of a fiscalized invoice is an item "
            "with a code and a class — a GL-only line cannot be put on a receipt.",
            code="fiscal_item_required",
            field_errors={f"{field}.item_id": ["required on a fiscalized document"]},
        )
    item: Item = line.item
    if not item.fiscal_class_code:
        raise PostingError(
            f"{item.code} has no EBM item class. Choose one on the item before selling it.",
            code="fiscal_class_missing",
            field_errors={f"{field}.item_id": ["the item has no EBM item class"]},
        )
    uom = db.get(Uom, line.uom_id) if line.uom_id is not None else None
    if uom is None or not uom.fiscal_quantity_unit:
        raise PostingError(
            f"Line {index + 1} is keyed in a unit with no EBM quantity unit. Map the unit on "
            "the Units of measure screen — a wrong unit on a receipt is a wrong receipt.",
            code="fiscal_uom_unmapped",
            field_errors={f"{field}.uom_id": ["the unit has no EBM quantity unit"]},
        )
    tax_code = (
        db.get(TaxCode, line.tax_code_id) if line.tax_code_id is not None else None
    )
    if tax_code is None or tax_code.fiscal_tax_type is None:
        raise PostingError(
            f"Line {index + 1}'s tax code has no EBM tax class. Set A, B, C or D on the tax "
            "code — the class is what RRA computes the tax from.",
            code="tax_class_unmapped",
            field_errors={f"{field}.tax_code_id": ["the tax code has no EBM tax class"]},
        )
    return PlannedLine(
        index=index,
        item=item,
        quantity=line.source.quantity,
        unit_price_inclusive=inclusive_price(
            line.unit_price, tax_code.rate_pct, tax_mode=tax_mode
        ),
        tax_class=tax_code.fiscal_tax_type,
        tax_rate_pct=tax_code.rate_pct,
        quantity_unit=uom.fiscal_quantity_unit,
        discount_percent=line.source.discount_percent,
        net=line.net,
        tax=line.tax,
        description=line.source.description,
        returns_line_no=line.source.returns_line_id,
    )


def inclusive_price(
    unit_price: Decimal, rate_pct: Decimal, *, tax_mode: TaxMode
) -> Decimal:
    """The VAT-inclusive unit price, in the document's currency (decision 6).

    The wire's `prc` always carries the tax inside it, so an exclusive line is grossed up by
    the programmed rate and an inclusive one is taken exactly as keyed. Not rounded here: the
    wire's two decimals are the adapter's to apply, and rounding a unit price to the base
    currency's decimals before multiplying by a quantity is how a line comes to disagree with
    its own extension.
    """
    if tax_mode == TaxMode.INCLUSIVE or rate_pct == ZERO:
        return unit_price
    return unit_price * (HUNDRED + rate_pct) / HUNDRED


def _format_address(address: dict | None) -> str | None:
    """A JSONB address as one line, for the authority's single address field.

    Values in insertion order rather than a fixed set of keys: the shape is the tenant's, and
    an address printed with a key missing is better than one printed with a key invented.
    """
    if not address:
        return None
    parts = [str(value).strip() for value in address.values() if str(value or "").strip()]
    return ", ".join(parts) or None


def _resolve_refund_original(
    db: Session, company_id: int, *, computed: list, refund_of_document_id: int | None
) -> int:
    """The one invoice this credit note refunds, as RRA's invoice number.

    **One**, and the refusals say which way it went wrong. A credit note whose lines return two
    different invoices has no `orgInvcNo` to carry; one whose lines return nothing and whose
    header names nothing has no original at all. CIS §7.17 allows one cancellation per
    original, and RRA keys it by that number.
    """
    returned_line_ids = [
        line.source.returns_line_id
        for line in computed
        if line.source.returns_line_id is not None
    ]
    original_id: int | None = refund_of_document_id
    if returned_line_ids:
        documents = set(
            db.scalars(
                select(PartnerDocumentLine.document_id).where(
                    PartnerDocumentLine.company_id == company_id,
                    PartnerDocumentLine.id.in_(returned_line_ids),
                )
            )
        )
        if len(documents) > 1:
            raise PostingError(
                "This credit note returns lines from more than one invoice. RRA registers a "
                "refund against exactly one original — raise one credit note per invoice.",
                code="refund_spans_invoices",
                field_errors={"lines": ["lines return more than one invoice"]},
            )
        if documents:
            only = documents.pop()
            if original_id is not None and original_id != only:
                raise PostingError(
                    "The invoice named on this credit note is not the invoice its lines "
                    "return.",
                    code="refund_spans_invoices",
                    field_errors={
                        "refund_of_document_id": ["does not match the returned lines"]
                    },
                )
            original_id = only
        _assert_within_original(db, company_id, computed)

    if original_id is None:
        raise PostingError(
            "A credit note on a fiscalized company must say which invoice it refunds — "
            "either by returning its lines or by naming it.",
            code="refund_original_required",
            field_errors={"refund_of_document_id": ["required"]},
        )
    invoice_no = _fiscal_invoice_no(db, company_id, original_id)
    if invoice_no is None:
        raise PostingError(
            "The invoice this credit note refunds was never registered with RRA, so there is "
            "no receipt to refund against.",
            code="refund_original_required",
            field_errors={"refund_of_document_id": ["not a fiscalized invoice"]},
        )
    return invoice_no


def _assert_within_original(db: Session, company_id: int, computed: list) -> None:
    """Cumulative refunded quantity per original line may not exceed what was invoiced.

    Cumulative, not per document: three credit notes of one unit each against a line of two is
    the case a per-document check waves through, and it is the one that actually happens.
    """
    for index, line in enumerate(computed):
        original_line_id = line.source.returns_line_id
        if original_line_id is None:
            continue
        original = db.get(PartnerDocumentLine, original_line_id)
        if original is None or original.company_id != company_id:
            continue
        invoiced = original.quantity
        already = (
            db.scalar(
                select(func.coalesce(func.sum(PartnerDocumentLine.quantity), 0))
                .join(
                    PartnerDocument,
                    PartnerDocument.id == PartnerDocumentLine.document_id,
                )
                .where(
                    PartnerDocumentLine.company_id == company_id,
                    PartnerDocumentLine.returns_line_id == original_line_id,
                    PartnerDocument.status == DocumentStatus.POSTED,
                )
            )
            or ZERO
        )
        if already + line.source.quantity > invoiced:
            raise PostingError(
                f"Line {index + 1} returns more than was invoiced: {invoiced} sold, "
                f"{already} already credited.",
                code="refund_exceeds_original",
                field_errors={
                    f"lines.{index}.quantity": ["more than the invoice's remaining quantity"]
                },
            )


def _require_refund_reason(refund_reason: str | None) -> str:
    if not refund_reason:
        raise PostingError(
            "A credit note on a fiscalized company needs a refund reason — RRA reports on "
            "it, and only the person issuing the credit can answer it.",
            code="refund_reason_required",
            field_errors={"refund_reason": ["required"]},
        )
    return refund_reason


def _fiscal_invoice_no(db: Session, company_id: int, document_id: int) -> int | None:
    """The authority's invoice number for a document's sale row, if it has one."""
    rows = outbox.rows_for_document(
        db, company_id, source_doc_type=DOCUMENT_SOURCE, source_doc_id=document_id
    )
    for row in rows:
        if row.kind == FiscalOutboxKind.SALE and row.status != FiscalOutboxStatus.CANCELLED:
            return row.invc_no
    return None


# --- Enqueue (inside the posting transaction) -----------------------------------------------


def enqueue(
    db: Session,
    company_id: int,
    *,
    plan: FiscalPlan,
    document: PartnerDocument,
    partner: Partner,
    currency: Currency,
    base: Currency,
    posted_at: datetime | None,
    actor: User,
    source_doc_type: str = DOCUMENT_SOURCE,
):
    """Register any unknown item, claim `invcNo`, render the payload, insert the row.

    In that order, and the order is the contract: an `item` row inserted first sits ahead of
    the sale in the device's FIFO, so RRA has been told about the item by the time the sale
    naming it arrives. Nothing sequences those calls by hand — creation order is queue order.
    """
    device = plan.device
    adapter = plan.adapter
    fiscal_items = {
        planned.index: item_service.ensure_registered(
            db,
            company_id,
            device=device,
            adapter=adapter,
            item=planned.item,
            quantity_unit=planned.quantity_unit,
            tax_class=planned.tax_class,
            price_inclusive=_catalogue_price_inclusive(planned),
            actor=actor,
        )
        for planned in plan.lines
    }

    claimed = claim_number(db, company_id, DocType.FISCAL_SALE, branch_id=device.branch_id)
    lines = tuple(
        _fiscal_line(db, planned, fiscal_items[planned.index], currency, base, document, seq)
        for seq, planned in enumerate(plan.lines, start=1)
    )
    branch = db.get(Branch, device.branch_id)
    common = {
        "invoice_no": claimed.sequence_no,
        "document_number": document.number,
        "document_date": document.document_date,
        "posted_at": posted_at or datetime.now(UTC),
        "party": FiscalParty(
            name=partner.name,
            tin=partner.tin,
            phone=partner.phone,
            address=_format_address(partner.address),
        ),
        "lines": lines,
        "payment_method": plan.payment_method,
        "purchase_code": plan.purchase_code,
        "releases_stock": any(
            planned.item.is_stock or planned.item.is_kit for planned in plan.lines
        ),
        "actor_id": str(actor.id),
        "actor_name": actor.email,
        "branch_name": branch.name if branch is not None else "",
        "branch_address": (_format_address(branch.address) or "") if branch is not None else "",
        "remark": document.description,
        "daily_report_no": next_daily_report_no(db, company_id, device.id),
    }
    if plan.kind == FiscalOutboxKind.REFUND:
        dto = FiscalRefund(
            **common,
            original_invoice_no=plan.original_invoice_no or 0,
            reason_code=plan.reason_code or "",
        )
    else:
        dto = FiscalSale(**common)

    return outbox.enqueue(
        db,
        company_id,
        device=device,
        kind=plan.kind,
        payload=adapter.render(device, plan.kind, dto),
        source_doc_type=source_doc_type,
        source_doc_id=document.id,
        invc_no=claimed.sequence_no,
    )


def next_daily_report_no(db: Session, company_id: int, device_id: int) -> int:
    """The Z number this sale will fall under — the last close plus one.

    Read rather than claimed: the number belongs to the close that will eventually take it
    (decision 11), and claiming it here would burn a number every time somebody sold
    something.
    """
    last = db.scalar(
        select(func.max(FiscalDailyReport.report_no)).where(
            FiscalDailyReport.company_id == company_id,
            FiscalDailyReport.device_id == device_id,
        )
    )
    return int(last or 0) + 1


def _fiscal_line(
    db: Session,
    planned: PlannedLine,
    fiscal_item: FiscalItem,
    currency: Currency,
    base: Currency,
    document: PartnerDocument,
    sequence: int,
) -> FiscalLine:
    return FiscalLine(
        sequence=sequence,
        item_code=fiscal_item.item_cd,
        item_class_code=fiscal_item.item_cls_cd,
        name=planned.item.name,
        quantity=planned.quantity,
        unit_price_inclusive=_price_in_base(
            planned.unit_price_inclusive, currency, document
        ),
        # **Converted the way the ledger converted them** — net and tax separately, because
        # that is how they were posted: two journal lines, each rounded to the base currency on
        # its own. Converting the sum once would differ from the pair by a franc on a
        # foreign-currency document, and the receipt would disagree with the entry behind it.
        taxable_amount=(
            _posted_base(db, planned.net, currency, base, document)
            + _posted_base(db, planned.tax, currency, base, document)
        ),
        tax_amount=_posted_base(db, planned.tax, currency, base, document),
        tax_class=planned.tax_class,
        tax_rate_pct=planned.tax_rate_pct,
        package_unit=fiscal_item.pkg_unit_cd,
        quantity_unit=fiscal_item.qty_unit_cd,
        discount_percent=planned.discount_percent,
        barcode=fiscal_item.bcd,
        returns_line_no=planned.returns_line_no,
    )


def _posted_base(
    db: Session,
    amount: Decimal,
    currency: Currency,
    base: Currency,
    document: PartnerDocument,
) -> Decimal:
    """A posted amount in base currency — the kernel's own converter, at the document's own
    frozen rate.

    Not a second opinion about what a foreign-currency sale was worth: this is the same
    function, on the same inputs, that produced the `base_amount` on every journal line the
    document posted. A receipt that disagreed with the ledger about the francs would be the
    second truth this phase exists to avoid.
    """
    return to_base(
        db,
        amount,
        currency,
        document.document_date,
        base=base,
        rate=document.exchange_rate,
    ).base_amount


def _catalogue_price_inclusive(planned: PlannedLine) -> Decimal:
    """What the authority lists the item at — the **catalogue** price, VAT-inclusive, in base.

    Not the price this line happened to be sold at, and the difference is not cosmetic: the
    registered price is part of the hash that decides whether an item is re-registered, so a
    line price would queue an `item` row on every sale at a new figure. A shop that negotiates
    would spend its queue telling RRA about its own discounts.

    `items.selling_price` is kept in base currency and `price_includes_tax` says which side of
    the tax it is on, so the conversion is the line's own programmed rate applied once.
    """
    price = planned.item.selling_price
    if planned.item.price_includes_tax or planned.tax_rate_pct == ZERO:
        return price
    return price * (HUNDRED + planned.tax_rate_pct) / HUNDRED


def _price_in_base(price: Decimal, currency: Currency, document: PartnerDocument) -> Decimal:
    """A **unit price** in base currency, unrounded.

    Deliberately not through `_posted_base`: a unit price is not a posted amount, and rounding
    it to the base currency's decimals before the adapter multiplies it by a quantity is how a
    line comes to disagree with its own extension. RWF has no decimals; a USD 2.36 unit price
    at 1 320 is 3 115.20 francs on the wire and rounding it to 3 115 first would lose 4 francs
    over twenty units. The wire's own two decimals are what the adapter applies.
    """
    if currency.is_base:
        return price
    return price * document.exchange_rate


# --- Reversal (decision 7) ------------------------------------------------------------------


def on_reverse(db: Session, company_id: int, document: PartnerDocument, *, reason: str | None):
    """What reversing a fiscalized document does to its queue row, before anything is written.

    Four cases, and the two refusals are the interesting ones:

    * the row is `queued` or `failed` — RRA never held it, so the row is **cancelled** and the
      ordinary P4 reversal proceeds. This is why a `failed` sale is corrected by reversing and
      re-posting rather than by editing a payload: the ledger entry is what changes.
    * the row is `sent` and it was a **sale** — RRA holds a receipt, so the reversal queues a
      full **refund** (label NR) against it.
    * the row is `sent` and it was a **refund** — refused. A refund of a refund is not in the
      vocabulary; the correction is a new invoice.
    * the row is `unknown` or `needs_receipt` — refused until somebody resolves it. RRA may be
      holding the sale, and cancelling the row would leave a registered sale unrefunded.

    Returns the queue rows it cancelled, so the caller can say what happened.
    """
    rows = outbox.rows_for_document(
        db, company_id, source_doc_type=DOCUMENT_SOURCE, source_doc_id=document.id
    )
    sale_rows = [
        row
        for row in rows
        if row.kind in (FiscalOutboxKind.SALE, FiscalOutboxKind.REFUND)
        and row.status != FiscalOutboxStatus.CANCELLED
    ]
    if not sale_rows:
        return []
    row = sale_rows[-1]
    if row.status in (FiscalOutboxStatus.UNKNOWN, FiscalOutboxStatus.NEEDS_RECEIPT):
        raise LedgerStateError(
            f"{document.number} has an EBM queue row in state '{row.status}': RRA may be "
            "holding this sale. Resolve the row on the EBM queue screen — verify it with the "
            "device, or attach the receipt — before reversing.",
            code="fiscal_status_unresolved",
        )
    if row.status == FiscalOutboxStatus.SENT:
        if row.kind == FiscalOutboxKind.REFUND:
            raise LedgerStateError(
                f"{document.number} has already been refunded with RRA. A refund of a refund "
                "is not something EBM can express — correct it with a new invoice.",
                code="fiscal_refund_irreversible",
            )
        # A sale RRA signed cannot be un-signed. The reversal is a refund, and the caller
        # queues it once the ledger half has gone through — `plan_reversal_refund` below.
        return []
    row.status = FiscalOutboxStatus.CANCELLED
    row.resolution_note = (
        f"cancelled by the reversal of {document.number}"
        f"{f': {reason}' if reason else ''}"
    )
    db.flush()
    return [row]


def needs_reversal_refund(db: Session, company_id: int, document: PartnerDocument) -> bool:
    """True when reversing this document owes RRA a refund — its sale was signed."""
    rows = outbox.rows_for_document(
        db, company_id, source_doc_type=DOCUMENT_SOURCE, source_doc_id=document.id
    )
    return any(
        row.kind == FiscalOutboxKind.SALE and row.status == FiscalOutboxStatus.SENT
        for row in rows
    )


def plan_reversal_refund(
    db: Session,
    company_id: int,
    document: PartnerDocument,
    *,
    refund_reason: str | None,
) -> FiscalPlan | None:
    """The full refund a reversal owes RRA, rebuilt from the document **as posted**.

    From the stored lines rather than from whatever was keyed, because the reversal is of what
    was posted: prices may have moved, the catalogue may have been edited, and what RRA is
    being asked to reverse is the receipt it signed.

    `None` when nothing is owed — the sale was never sent, so `on_reverse` cancelled its row
    and the ledger reversal is the whole of it.
    """
    if not needs_reversal_refund(db, company_id, document):
        return None
    device = _require_device(db, company_id, document.branch_id)
    company = db.get(Company, company_id)
    adapter = adapter_for(company.fiscal_country if company else None)
    reason = _require_refund_reason(refund_reason)
    original_invoice_no = _fiscal_invoice_no(db, company_id, document.id)

    lines: list[PlannedLine] = []
    stored = db.scalars(
        select(PartnerDocumentLine)
        .where(
            PartnerDocumentLine.company_id == company_id,
            PartnerDocumentLine.document_id == document.id,
            PartnerDocumentLine.kit_parent_line_id.is_(None),
        )
        .order_by(PartnerDocumentLine.line_no)
    )
    for index, line in enumerate(stored):
        item = db.get(Item, line.item_id) if line.item_id is not None else None
        uom = db.get(Uom, line.uom_id) if line.uom_id is not None else None
        tax_code = db.get(TaxCode, line.tax_code_id) if line.tax_code_id is not None else None
        if item is None or uom is None or tax_code is None:
            # Unreachable for a document that was fiscalized — every one of these was proved
            # present at post. Refusing rather than sending a line with a blank item code,
            # because a receipt RRA cannot key is worse than a reversal that stops and says so.
            raise LedgerStateError(
                f"{document.number} cannot be refunded with RRA: line {index + 1} no longer "
                "resolves to an item, a unit and a tax code.",
                code="fiscal_status_unresolved",
            )
        lines.append(
            PlannedLine(
                index=index,
                item=item,
                quantity=line.quantity,
                # The line was posted in the document's own tax mode; `unit_price` is what it
                # was priced at in that mode, so the same conversion applies.
                unit_price_inclusive=inclusive_price(
                    line.unit_price, tax_code.rate_pct, tax_mode=document.tax_mode
                ),
                tax_class=tax_code.fiscal_tax_type or FiscalTaxType.D,
                tax_rate_pct=tax_code.rate_pct,
                quantity_unit=uom.fiscal_quantity_unit or "",
                discount_percent=line.discount_percent,
                net=line.net_amount,
                tax=line.tax_amount,
                description=line.description,
            )
        )
    return FiscalPlan(
        device=device,
        adapter=adapter,
        kind=FiscalOutboxKind.REFUND,
        lines=tuple(lines),
        payment_method=document.payment_method or PaymentMethod.CASH,
        purchase_code=document.purchase_code,
        original_invoice_no=original_invoice_no,
        reason_code=reason,
    )
