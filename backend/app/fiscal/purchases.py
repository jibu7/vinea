"""The purchase contract (decision 9): what a posted AP document tells the authority.

A purchase is **not** a sale, and the difference is the whole reason this module is not
`sales.py` with a flag. A sale is issued: the authority signs it, hands back a receipt, and
the customer is owed a printed document with counters on it. A purchase is *declared*: the
authority acknowledges it and there is nothing to print, because the receipt for that
transaction was printed by the supplier's own device. So the row is queued the same way, it
travels the same FIFO, and it comes back with an acknowledgment rather than a signature —
`assert_fiscal_invariants` reads "no receipt on a purchase row" as an invariant, not an
omission.

**What fiscalizes** (decisions 3 and 9): an AP invoice is a purchase (`rcptTyCd P`), an AP
return to supplier is a purchase return (`R`). Settlements, allocations and `APJN` journal
batches are not — a payment against an invoice is not a second purchase, and an opening
balance is not a purchase at all.

**A purchase line need not be an item.** Rent and freight are the ordinary case and
`itemCd` is optional on this endpoint, so a line with no item goes out under the company's
default purchase class (`gl_settings.fiscal_default_purchase_class_code`) with the GL
account's name as the item name. That is the one thing the sale side refuses outright
(`fiscal_item_required`): a receipt line is an item with a code, and a purchase declaration
is a cost with a class.

**Registration versus confirmation.** The same endpoint carries both, and `regTyCd` tells
them apart: `M` for a purchase this system originated, `A` for one the authority is already
holding and an operator has confirmed (`app/fiscal/feed.py`). A supplier invoice that went
out both ways would be registered twice, which is why accepting a feed row cancels the
document's own row when it has not been sent — see `feed.accept`.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fiscal import devices as device_service
from app.fiscal import items as item_service
from app.fiscal import outbox
from app.fiscal import stock as fiscal_stock
from app.fiscal.mapping import FiscalLine, FiscalParty, FiscalPurchase
from app.fiscal.protocol import FiscalizationAdapter
from app.fiscal.registry import adapter_for
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.money import to_base
from app.kernel.sequences import DocType, claim_number
from app.models.company import Branch, Company
from app.models.currency import Currency
from app.models.fiscalization import (
    FiscalDevice,
    FiscalItem,
    FiscalOutboxKind,
    FiscalOutboxStatus,
    FiscalTaxType,
    PaymentMethod,
)
from app.models.gl import GLAccount, GLSettings
from app.models.inventory import Item, Uom
from app.models.partner import Partner
from app.models.subledger import (
    DocumentKind,
    PartnerDocument,
    PartnerDocumentLine,
    PartnerRole,
    TaxMode,
)
from app.models.tax import TaxCode
from app.models.user import User

ZERO = Decimal(0)
HUNDRED = Decimal(100)
ONE = Decimal(1)

#: The kinds that declare a purchase, and whether each is a **return** of one. A settlement is
#: absent for the same reason it is absent from the sale map: paying an invoice is not buying
#: anything. Membership is what decides whether a document declares at all; the value is the
#: direction it declares in.
DECLARES_A_RETURN: dict[DocumentKind, bool] = {
    DocumentKind.INVOICE: False,
    DocumentKind.CREDIT_NOTE: True,
}

def supplier_reference_number(reference: str | None) -> int | None:
    """The supplier's own invoice number, when the reference **is** a number.

    The one implementation, because two things need it and they must agree: the declaration
    carries it to the authority, and `feed.accept` uses it to recognise the purchase a feed row
    is the other side of. A reference like `INV/2026/0042` has no numeric form, so it is not a
    number the authority can key by and not one this build can match on — it stays on the Vinea
    document where a person can read it, and the pair is reconciled by hand.
    """
    if reference is None:
        return None
    digits = reference.strip()
    return int(digits) if digits.isdigit() else None


#: What a purchase line with no unit of measure is measured in. A rent charge has no UoM in
#: Vinea and the authority's item object requires one, so the declaration says "one of it" —
#: §4.6's `U`, which is the code for a piece. Named here, as `items.DEFAULT_PACKAGE_UNIT` is,
#: rather than left as a literal at the call site.
DEFAULT_QUANTITY_UNIT = "U"


@dataclass(frozen=True)
class PlannedPurchaseLine:
    """One AP line resolved to what a declaration needs of it.

    `fiscal_item` is `None` for a line the authority will hold no item record for — a GL-only
    cost, or an item nobody has classed. Both travel under the company's default purchase
    class, which is the field decision 9 gives them.
    """

    index: int
    item: Item | None
    name: str
    quantity: Decimal
    unit_price_inclusive: Decimal
    tax_class: FiscalTaxType
    tax_rate_pct: Decimal
    quantity_unit: str
    package_unit: str
    item_class_code: str
    discount_percent: Decimal
    net: Decimal
    tax: Decimal


@dataclass(frozen=True)
class PurchasePlan:
    """Everything `enqueue()` will need, and proof that none of it can fail."""

    device: FiscalDevice
    adapter: FiscalizationAdapter
    lines: tuple[PlannedPurchaseLine, ...]
    payment_method: PaymentMethod
    is_return: bool


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
    payment_method: PaymentMethod,
) -> PurchasePlan | None:
    """The decision-9 resolution, or `None` when this document does not declare a purchase.

    `None` is the answer for every non-fiscalized company, for the AR side, for a settlement
    and for a journal batch. The refusals are deliberately few: a purchase declaration has
    fewer required facts than a receipt, and refusing to post a supplier invoice over a field
    the authority does not need would be the refusal costing more than it buys.
    """
    if kind not in DECLARES_A_RETURN or role != PartnerRole.AP or is_journal:
        return None
    if not device_service.is_fiscalized(db, company_id):
        return None

    device = require_device(db, company_id, branch_id)
    company = db.get(Company, company_id)
    adapter = adapter_for(company.fiscal_country if company else None)
    default_class = _default_purchase_class(db, company_id)

    lines = tuple(
        _plan_line(db, index, line, tax_mode=tax_mode, default_class=default_class)
        for index, line in enumerate(computed)
        if not line.is_kit_component
    )
    if not lines:
        raise PostingError(
            "A purchase declaration must carry at least one line",
            code="fiscal_line_required",
            field_errors={"lines": ["at least one line required"]},
        )
    return PurchasePlan(
        device=device,
        adapter=adapter,
        lines=lines,
        payment_method=payment_method,
        is_return=DECLARES_A_RETURN[kind],
    )


def require_device(db: Session, company_id: int, branch_id: int | None) -> FiscalDevice:
    """The active device of the branch this document will post to.

    The same resolution the sale side makes, and shared with it through
    `sales._require_device` would have been a circular import — so it is here, exported, and
    `sales` keeps its own copy of the *refusal wording* because the sentence an operator reads
    about an invoice they cannot issue is not the sentence about a supplier invoice they
    cannot declare.
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
            "This branch has no active EBM device, so there is nothing to declare this "
            "purchase to. Activate the branch's device, or key the document on a branch that "
            "has one.",
            code="fiscal_device_missing",
            field_errors={"branch_id": ["no active EBM device on this branch"]},
        )
    return device


def _default_purchase_class(db: Session, company_id: int) -> str | None:
    settings_row = db.scalar(select(GLSettings).where(GLSettings.company_id == company_id))
    return settings_row.fiscal_default_purchase_class_code if settings_row else None


def _plan_line(
    db: Session,
    index: int,
    line,  # noqa: ANN001 - a `_ComputedLine`; typing it is a circular import
    *,
    tax_mode: TaxMode,
    default_class: str | None,
) -> PlannedPurchaseLine:
    """One line's resolution, refusing on the field that is missing.

    Two refusals, and both are about a field the authority requires and nothing else can
    supply: a class for the line, and a tax class for its tax code.
    """
    field = f"lines.{index}"
    item: Item | None = line.item
    tax_code = db.get(TaxCode, line.tax_code_id) if line.tax_code_id is not None else None
    if tax_code is not None and tax_code.fiscal_tax_type is None:
        raise PostingError(
            f"Line {index + 1}'s tax code has no EBM tax class. Set A, B, C or D on the tax "
            "code — the class is what RRA reports the input VAT under.",
            code="tax_class_unmapped",
            field_errors={f"{field}.tax_code_id": ["the tax code has no EBM tax class"]},
        )
    # **No tax code at all is `D`, not a refusal.** `D` is RRA's "non-VAT" class and a cost
    # keyed with no tax code is exactly that; refusing it would stop an AP invoice over a
    # line the authority has a code for.
    tax_class = tax_code.fiscal_tax_type if tax_code is not None else FiscalTaxType.D
    rate_pct = tax_code.rate_pct if tax_code is not None else ZERO

    if item is not None and item.fiscal_class_code:
        item_class_code = item.fiscal_class_code
    elif default_class:
        # Decision 9: a line the authority holds no item record for travels under the
        # company's default purchase class. An *unclassed item* is that case too — decision 8
        # requires a class of anything sold, and a raw material nobody sells has none.
        item_class_code = default_class
    else:
        raise PostingError(
            f"Line {index + 1} has no EBM item class and the company has no default purchase "
            "class. Either choose a class on the item, or set the default purchase class on "
            "Defaults — RRA requires a class on every purchase line.",
            code="fiscal_purchase_class_missing",
            field_errors={
                f"{field}.item_id"
                if item is not None
                else f"{field}.gl_account_id": ["no EBM item class"]
            },
        )

    uom = db.get(Uom, line.uom_id) if line.uom_id is not None else None
    if item is not None and (uom is None or not uom.fiscal_quantity_unit):
        raise PostingError(
            f"Line {index + 1} is keyed in a unit with no EBM quantity unit. Map the unit on "
            "the Units of measure screen — a wrong unit on a declaration is a wrong "
            "declaration.",
            code="fiscal_uom_unmapped",
            field_errors={f"{field}.uom_id": ["the unit has no EBM quantity unit"]},
        )

    # **A line with no item is one of it, priced at its posted gross.** Not
    # `_inclusive_price(net)`: that grosses an exclusive amount up by the rate, which is right
    # for a unit price and wrong here — on an *inclusive* document `net` is already net of a
    # tax the keyed figure included, and grossing it again would over-state the line. The
    # posted gross is the one figure that is the inclusive amount in either tax mode.
    quantity = line.source.quantity if item is not None else ONE
    inclusive = (
        _inclusive_price(line.unit_price, rate_pct, tax_mode=tax_mode)
        if item is not None
        else line.gross
    )
    return PlannedPurchaseLine(
        index=index,
        item=item,
        name=_line_name(db, line, item),
        quantity=quantity,
        unit_price_inclusive=inclusive,
        tax_class=tax_class,
        tax_rate_pct=rate_pct,
        quantity_unit=(
            uom.fiscal_quantity_unit
            if uom is not None and uom.fiscal_quantity_unit
            else DEFAULT_QUANTITY_UNIT
        ),
        package_unit=(
            item.fiscal_package_unit or item_service.DEFAULT_PACKAGE_UNIT
            if item is not None
            else item_service.DEFAULT_PACKAGE_UNIT
        ),
        item_class_code=item_class_code,
        discount_percent=line.source.discount_percent if item is not None else ZERO,
        net=line.net,
        tax=line.tax,
    )


def _line_name(db: Session, line, item: Item | None) -> str:  # noqa: ANN001
    """What the declaration calls this line.

    The item's name when there is one; otherwise the **GL account's** name, which decision 9
    names and which is the only thing on a rent line a person would recognise. The keyed
    description is deliberately not preferred: it is free text that often says "March" and
    tells the authority nothing about what was bought.
    """
    if item is not None:
        return item.name
    if line.gl_account_id is not None:
        account = db.get(GLAccount, line.gl_account_id)
        if account is not None:
            return account.name
    return line.source.description or "Purchase"


def _inclusive_price(
    unit_price: Decimal, rate_pct: Decimal, *, tax_mode: TaxMode
) -> Decimal:
    """The VAT-inclusive unit price in the document's currency — the sale side's rule, applied
    to a cost. Not rounded here: the wire's two decimals are the adapter's to apply."""
    if tax_mode == TaxMode.INCLUSIVE or rate_pct == ZERO:
        return unit_price
    return unit_price * (HUNDRED + rate_pct) / HUNDRED


# --- Enqueue (inside the posting transaction) -----------------------------------------------


def enqueue(
    db: Session,
    company_id: int,
    *,
    plan: PurchasePlan,
    document: PartnerDocument,
    partner: Partner,
    currency: Currency,
    base: Currency,
    posted_at: datetime | None,
    actor: User,
    source_doc_type: str = outbox.DOCUMENT_SOURCE,
):
    """Register any unknown item, claim `invcNo` from `FIP`, render the payload, insert the row.

    In that order, and the order is the contract — an `item` row inserted first sits ahead of
    the purchase in the device's FIFO, so the authority has been told about the item by the
    time a declaration naming it arrives.

    The direction is the **plan's**: `plan()` reads it off the document's kind and
    `plan_reversal_purchase` flips it, so a reversed invoice is declared back as a return of
    the same figures without this function knowing which it is looking at.
    """
    device = plan.device
    adapter = plan.adapter
    fiscal_items = {
        planned.index: item_service.ensure_registered_for_report(
            db,
            company_id,
            device=device,
            adapter=adapter,
            item=planned.item,
            actor=actor,
        )
        for planned in plan.lines
        if planned.item is not None
    }

    claimed = claim_number(db, company_id, DocType.FISCAL_PURCHASE, branch_id=device.branch_id)
    lines = tuple(
        _fiscal_line(
            db,
            planned,
            fiscal_items.get(planned.index),
            currency,
            base,
            document,
            sequence,
        )
        for sequence, planned in enumerate(plan.lines, start=1)
    )
    dto = FiscalPurchase(
        invoice_no=claimed.sequence_no,
        document_number=document.number,
        document_date=document.document_date,
        posted_at=posted_at or datetime.now(UTC),
        supplier=FiscalParty(
            name=partner.name,
            tin=partner.tin,
            phone=partner.phone,
            address=_format_address(partner.address),
        ),
        lines=lines,
        payment_method=plan.payment_method,
        supplier_invoice_no=supplier_reference_number(document.reference),
        is_return=plan.is_return,
        actor_id=str(actor.id),
        actor_name=actor.email,
        remark=document.description,
    )
    return outbox.enqueue(
        db,
        company_id,
        device=device,
        kind=FiscalOutboxKind.PURCHASE,
        payload=adapter.render(device, FiscalOutboxKind.PURCHASE, dto),
        source_doc_type=source_doc_type,
        source_doc_id=document.id,
        invc_no=claimed.sequence_no,
    )


def _fiscal_line(
    db: Session,
    planned: PlannedPurchaseLine,
    fiscal_item: FiscalItem | None,
    currency: Currency,
    base: Currency,
    document: PartnerDocument,
    sequence: int,
) -> FiscalLine:
    return FiscalLine(
        sequence=sequence,
        # Empty rather than absent: `FiscalLine.item_code` is a string and the adapter turns
        # an empty one into the omitted `itemCd` the purchase endpoint allows.
        item_code=fiscal_item.item_cd if fiscal_item is not None else "",
        item_class_code=planned.item_class_code,
        name=planned.name,
        quantity=planned.quantity,
        unit_price_inclusive=_price_in_base(
            planned.unit_price_inclusive, currency, document
        ),
        # Converted the way the ledger converted them — net and tax separately, because that
        # is how they were posted. See `sales._fiscal_line`.
        taxable_amount=(
            _posted_base(db, planned.net, currency, base, document)
            + _posted_base(db, planned.tax, currency, base, document)
        ),
        tax_amount=_posted_base(db, planned.tax, currency, base, document),
        tax_class=planned.tax_class,
        tax_rate_pct=planned.tax_rate_pct,
        package_unit=planned.package_unit,
        quantity_unit=planned.quantity_unit,
        discount_percent=planned.discount_percent,
        barcode=fiscal_item.bcd if fiscal_item is not None else None,
    )


def _posted_base(
    db: Session,
    amount: Decimal,
    currency: Currency,
    base: Currency,
    document: PartnerDocument,
) -> Decimal:
    return to_base(
        db,
        amount,
        currency,
        document.document_date,
        base=base,
        rate=document.exchange_rate,
    ).base_amount


def _price_in_base(price: Decimal, currency: Currency, document: PartnerDocument) -> Decimal:
    if currency.is_base:
        return price
    return price * document.exchange_rate


def _format_address(address: dict | None) -> str | None:
    if not address:
        return None
    parts = [str(value).strip() for value in address.values() if str(value or "").strip()]
    return ", ".join(parts) or None


# --- Reversal -------------------------------------------------------------------------------


def rows_for(db: Session, company_id: int, document_id: int) -> list:
    """This document's live purchase rows, oldest first."""
    rows = outbox.rows_for_document(
        db,
        company_id,
        source_doc_type=outbox.DOCUMENT_SOURCE,
        source_doc_id=document_id,
    )
    return [
        row
        for row in rows
        if row.kind in (FiscalOutboxKind.PURCHASE, FiscalOutboxKind.PURCHASE_CONFIRM)
        and row.status != FiscalOutboxStatus.CANCELLED
    ]


def on_reverse(db: Session, company_id: int, document: PartnerDocument, *, reason: str | None):
    """What reversing a declared purchase does to its queue row, before anything is written.

    Three cases, and the symmetry with the sale side is deliberate:

    * the row is `queued` or `failed` — RRA never held it, so it is **cancelled** and the
      ordinary P4 reversal proceeds;
    * the row is `sent` — RRA holds the declaration, so the reversal declares the opposite
      (`plan_reversal_purchase` below): a reversed invoice goes back as a return, a reversed
      return as a purchase. Unlike a refund of a refund on the sale side, both directions are
      in the vocabulary, so there is nothing to refuse;
    * the row is `unknown` or `needs_receipt` — refused until somebody resolves it. RRA may be
      holding the declaration, and cancelling the row would leave it standing.
    """
    rows = rows_for(db, company_id, document.id)
    if not rows:
        return []
    row = rows[-1]
    if row.status in (FiscalOutboxStatus.UNKNOWN, FiscalOutboxStatus.NEEDS_RECEIPT):
        raise LedgerStateError(
            f"{document.number} has an EBM queue row in state '{row.status}': RRA may be "
            "holding this purchase. Resolve the row on the EBM queue screen before reversing.",
            code="fiscal_status_unresolved",
        )
    if row.status == FiscalOutboxStatus.SENT:
        # Declared, and the declaration cannot be withdrawn. The reversal declares the
        # opposite — queued by the caller once the ledger half has gone through.
        return []
    row.status = FiscalOutboxStatus.CANCELLED
    note = (
        f"cancelled by the reversal of {document.number}"
        f"{f': {reason}' if reason else ''}"
    )
    row.resolution_note = note
    db.flush()
    # And the movement behind it, for the reason `sales.on_reverse` gives.
    return [row, *fiscal_stock.cancel_unsent_movements(
        db, company_id, document_id=document.id, note=note
    )]


def needs_reversal_declaration(
    db: Session, company_id: int, document: PartnerDocument
) -> bool:
    """True when reversing this document owes RRA the opposite declaration."""
    return any(
        row.status == FiscalOutboxStatus.SENT for row in rows_for(db, company_id, document.id)
    )


def plan_reversal_purchase(
    db: Session, company_id: int, document: PartnerDocument
) -> PurchasePlan | None:
    """The opposite declaration a reversal owes, rebuilt from the document **as posted**.

    From the stored lines rather than from whatever was keyed, for the same reason the sale
    side rebuilds a refund: what RRA is being asked to undo is the declaration it
    acknowledged, and the catalogue may have moved since.
    """
    if not needs_reversal_declaration(db, company_id, document):
        return None
    device = require_device(db, company_id, document.branch_id)
    company = db.get(Company, company_id)
    adapter = adapter_for(company.fiscal_country if company else None)
    default_class = _default_purchase_class(db, company_id)

    lines: list[PlannedPurchaseLine] = []
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
        item_class_code = (
            item.fiscal_class_code
            if item is not None and item.fiscal_class_code
            else default_class
        )
        if not item_class_code:
            raise LedgerStateError(
                f"{document.number} cannot be declared back to RRA: line {index + 1} has no "
                "EBM item class and the company has no default purchase class.",
                code="fiscal_purchase_class_missing",
            )
        rate_pct = tax_code.rate_pct if tax_code is not None else ZERO
        lines.append(
            PlannedPurchaseLine(
                index=index,
                item=item,
                name=_line_name_of_stored(db, line, item),
                quantity=line.quantity if item is not None else ONE,
                unit_price_inclusive=(
                    _inclusive_price(line.unit_price, rate_pct, tax_mode=document.tax_mode)
                    if item is not None
                    else line.gross_amount
                ),
                tax_class=(
                    tax_code.fiscal_tax_type
                    if tax_code is not None and tax_code.fiscal_tax_type is not None
                    else FiscalTaxType.D
                ),
                tax_rate_pct=rate_pct,
                quantity_unit=(
                    uom.fiscal_quantity_unit
                    if uom is not None and uom.fiscal_quantity_unit
                    else DEFAULT_QUANTITY_UNIT
                ),
                package_unit=(
                    item.fiscal_package_unit or item_service.DEFAULT_PACKAGE_UNIT
                    if item is not None
                    else item_service.DEFAULT_PACKAGE_UNIT
                ),
                item_class_code=item_class_code,
                discount_percent=line.discount_percent if item is not None else ZERO,
                net=line.net_amount,
                tax=line.tax_amount,
            )
        )
    return PurchasePlan(
        device=device,
        adapter=adapter,
        lines=tuple(lines),
        payment_method=document.payment_method or PaymentMethod.CASH,
        # The opposite of what was declared: an invoice comes back as a return.
        is_return=document.kind == DocumentKind.INVOICE,
    )


def _line_name_of_stored(db: Session, line: PartnerDocumentLine, item: Item | None) -> str:
    if item is not None:
        return item.name
    if line.gl_account_id is not None:
        account = db.get(GLAccount, line.gl_account_id)
        if account is not None:
            return account.name
    return line.description or "Purchase"
