"""DTO → payload. The decision-6 map, and the only place it exists.

Split out of `adapter.py` because it is the part worth reading on its own: the adapter is
transport and error policy, this is *what goes in each field*, and a reviewer checking the map
against `docs/rra/contract-notes.md` should not have to scroll past an HTTP client to do it.

Three rules hold throughout:

**Amounts are the posted figures, converted to the wire's two decimals.** `taxblAmt` is the
line's posted gross in base currency and `taxAmt` the tax it attracted — not a recomputation.
The ledger already decided what the sale was worth; a payload that recomputed it would be a
second opinion about a number an auditor can see.

**An undiscounted line carries `dcRt` 0 and `dcAmt` 0, and the wire tax is derived, not
copied.** Both follow the Sage 200 Evolution integration, which is the certified-in-Rwanda
reference this build takes its conventions from.

The first version of this file did the opposite: it multiplied the VAT-inclusive unit price by
the quantity and put the difference from the posted gross into `dcAmt`, on the reasoning that
the discount amount is the one field the documents do not derive from another. That produces a
*phantom discount* on a line nobody discounted, and the VSDC engine validates line arithmetic
strictly — `dcRt: 0` with a non-zero `dcAmt` is a payload validation failure, not a tolerance.
So `splyAmt` is now the posted gross on an undiscounted line, `dcAmt` is zero, and
`splyAmt − dcAmt == taxblAmt` holds exactly. On a discounted line `splyAmt` is the
inclusive-price extension, `dcRt` the keyed percentage and `dcAmt` the money it came to, and
the same relation holds.

**`taxAmt` is `taxblAmt × r / (100 + r)`, half-up to two decimals** — the RRA split applied to
the inclusive taxable amount, which is how Sage reconciles a zero-decimal ledger against a
two-decimal wire. The consequence is deliberate and worth stating: the receipt's tax can differ
from the *posted* tax by under a franc per line, so the VAT return — a query over the ledger —
reconciles to the receipts rather than equalling them. §7 of `contract-notes.md` records the
evidence and step 4's tie report shows the difference instead of asserting it away.

**The header is Σ of the lines, by class.** Never a separate computation: `totTaxblAmt` is the
sum of the four buckets and each bucket is the sum of its lines, so a payload cannot disagree
with itself. The property test over random documents asserts exactly that.
"""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from app.fiscal.mapping import (
    FiscalImportDecision,
    FiscalItemRegistration,
    FiscalLine,
    FiscalPurchase,
    FiscalPurchaseConfirmation,
    FiscalRefund,
    FiscalSale,
    FiscalStockIO,
    FiscalStockMaster,
    StockMovementFacing,
)
from app.fiscal.rwanda import codes
from app.fiscal.rwanda.payloads import (
    PurchaseItem,
    SalesItem,
    SalesReceipt,
    SaveItemRequest,
    SavePurchaseRequest,
    SaveSalesRequest,
    SaveStockIoRequest,
    SaveStockMasterRequest,
    StockItem,
    UpdateImportItemRequest,
)
from app.models.fiscalization import FiscalDevice, FiscalTaxType, PaymentMethod

ZERO = Decimal(0)
WIRE_EXPONENT = Decimal("0.01")
HUNDRED = Decimal(100)

#: Receipt timestamps are the taxpayer's local time, and the taxpayer is in Kigali. A UTC
#: `cfmDt` would put a 22:30 sale on the following day's Z report.
KIGALI = ZoneInfo("Africa/Kigali")

#: Vinea's payment methods → RRA's `pmtTyCd` (§4.5). A total map, not a `.get` with a default:
#: a new payment method must be mapped deliberately, and failing here is better than sending
#: every unmapped method as "other".
PAYMENT_TYPE_BY_METHOD: dict[PaymentMethod, str] = {
    PaymentMethod.CASH: codes.PaymentType.CASH,
    PaymentMethod.CREDIT: codes.PaymentType.CREDIT,
    PaymentMethod.CASH_CREDIT: codes.PaymentType.CASH_CREDIT,
    PaymentMethod.BANK_CHEQUE: codes.PaymentType.BANK_CHEQUE,
    PaymentMethod.CARD: codes.PaymentType.CARD,
    PaymentMethod.MOBILE_MONEY: codes.PaymentType.MOBILE_MONEY,
    PaymentMethod.OTHER: codes.PaymentType.OTHER,
}

#: How long RRA's fields are. Truncation is explicit here rather than left to the server,
#: because a server-side truncation is a receipt that says something slightly different from
#: the invoice and nobody finds out.
NAME_LIMIT = 200
CUSTOMER_NAME_LIMIT = 60
REMARK_LIMIT = 400
ACTOR_ID_LIMIT = 20
ACTOR_NAME_LIMIT = 60


def wire(amount: Decimal) -> Decimal:
    """Two decimals, half-up — the same rounding `app.kernel.money` uses, deliberately."""
    return amount.quantize(WIRE_EXPONENT, rounding=ROUND_HALF_UP)


def _payment_type(method: PaymentMethod) -> str:
    return PAYMENT_TYPE_BY_METHOD[method]


def _stamp(moment: datetime) -> str:
    """`yyyyMMddHHmmss` in Kigali."""
    return moment.astimezone(KIGALI).strftime("%Y%m%d%H%M%S")


def _day(value: date) -> str:
    return value.strftime("%Y%m%d")


def _actor(sale_or_purchase: FiscalSale | FiscalPurchase | FiscalStockIO) -> tuple[str, str]:
    return (
        (sale_or_purchase.actor_id or "vinea")[:ACTOR_ID_LIMIT],
        (sale_or_purchase.actor_name or "Vinea")[:ACTOR_NAME_LIMIT],
    )


# --- Lines --------------------------------------------------------------------------------


def _line_amounts(line: FiscalLine) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """`(prc, splyAmt, taxblAmt, dcAmt)` — RRA's own relations, all the way down from `prc`.

        prc      = the VAT-inclusive unit price, two decimals
        splyAmt  = prc x qty
        dcAmt    = splyAmt x dcRt / 100
        taxblAmt = splyAmt - dcAmt
        totAmt   = taxblAmt

    Nothing here reads the posted gross, and that is the point. The build used to set
    `taxblAmt` to the posted figure and let the difference from `prc x qty` fall into `dcAmt`,
    which invents a discount on a line nobody discounted — and an undiscounted line carrying a
    discount amount is a payload validation failure, not a tolerance.

    **The ledger stays as posted.** The wire is derived from the inclusive price, the ledger
    from the posting, and the two can differ by a rounding step per line. That difference is
    not hidden: it is what the step-2 residue census measures, line by line, as `wire - ledger`.
    """
    price = wire(line.unit_price_inclusive)
    supply = wire(price * line.quantity)
    discount = wire(supply * line.discount_percent / HUNDRED)
    return price, supply, supply - discount, discount


def line_tax(line: FiscalLine) -> Decimal:
    """`taxblAmt x r / (100 + r)`, half-up to two decimals — the authority's own split, applied
    to the taxable amount the relations above produce.

    Public because `bucket_totals` must sum exactly these values. A header computed any other
    way would be a payload disagreeing with its own lines, and the per-line rounding is not a
    detail: TESKO's live receipt 10057 prints a header tax that only the sum of rounded lines
    reproduces — splitting the invoice total once is a centime short.
    """
    rate = line.tax_rate_pct
    if rate == ZERO:
        return ZERO
    _, _, taxable, _ = _line_amounts(line)
    return (taxable * rate / (HUNDRED + rate)).quantize(WIRE_EXPONENT, rounding=ROUND_HALF_UP)


def line_taxable(line: FiscalLine) -> Decimal:
    """The wire's taxable amount for one line — `splyAmt - dcAmt`, not the posted gross."""
    return _line_amounts(line)[2]


def _sales_item(line: FiscalLine) -> SalesItem:
    price, supply, taxable, discount = _line_amounts(line)
    return SalesItem(
        itemSeq=line.sequence,
        itemCd=line.item_code,
        itemClsCd=line.item_class_code,
        itemNm=line.name[:NAME_LIMIT],
        bcd=line.barcode,
        pkgUnitCd=line.package_unit,
        # `pkg` is the number of packages and `qty` the quantity in the line's unit. Vinea
        # keys one quantity in one unit, so the two are the same number — stated here rather
        # than left to look like a copy-paste.
        pkg=wire(line.quantity),
        qtyUnitCd=line.quantity_unit,
        qty=wire(line.quantity),
        prc=price,
        splyAmt=supply,
        dcRt=wire(line.discount_percent),
        dcAmt=discount,
        taxTyCd=line.tax_class,
        taxblAmt=taxable,
        taxAmt=line_tax(line),
        totAmt=taxable,
    )


def _purchase_item(line: FiscalLine) -> PurchaseItem:
    price, supply, taxable, discount = _line_amounts(line)
    return PurchaseItem(
        itemSeq=line.sequence,
        # Optional on a purchase, and deliberately so: rent and freight have no item, and
        # `itemClsCd` from the company's default purchase class carries the line instead.
        itemCd=line.item_code or None,
        itemClsCd=line.item_class_code,
        itemNm=line.name[:NAME_LIMIT],
        bcd=line.barcode,
        pkgUnitCd=line.package_unit,
        pkg=wire(line.quantity),
        qtyUnitCd=line.quantity_unit,
        qty=wire(line.quantity),
        prc=price,
        splyAmt=supply,
        dcRt=wire(line.discount_percent),
        dcAmt=discount,
        taxTyCd=line.tax_class,
        taxblAmt=taxable,
        taxAmt=line_tax(line),
        totAmt=taxable,
    )


# --- Header buckets -----------------------------------------------------------------------


def bucket_totals(lines: Sequence[FiscalLine]) -> dict[str, Decimal]:
    """Σ of the lines by tax class, and the totals over the buckets.

    Both halves in one function, because that is the property that must hold: the totals are
    sums of the buckets and the buckets are sums of the lines. Computing either independently
    is how a payload comes to disagree with itself.
    """
    taxable = {tax_class: ZERO for tax_class in FiscalTaxType}
    tax = {tax_class: ZERO for tax_class in FiscalTaxType}
    for line in lines:
        taxable[line.tax_class] += line_taxable(line)
        tax[line.tax_class] += line_tax(line)
    totals: dict[str, Decimal] = {}
    for tax_class in FiscalTaxType:
        totals[f"taxblAmt{tax_class.value}"] = taxable[tax_class]
        totals[f"taxAmt{tax_class.value}"] = tax[tax_class]
        totals[f"taxRt{tax_class.value}"] = Decimal(codes.PROGRAMMED_RATES[tax_class.value])
    totals["totTaxblAmt"] = sum(taxable.values(), ZERO)
    totals["totTaxAmt"] = sum(tax.values(), ZERO)
    # `totAmt` is the gross the customer pays, which on a VAT-inclusive receipt is the taxable
    # amount: the tax is already inside it. Adding the two would double the VAT.
    totals["totAmt"] = totals["totTaxblAmt"]
    return totals


# --- Requests -----------------------------------------------------------------------------


def build_item_request(
    device: FiscalDevice, item: FiscalItemRegistration
) -> SaveItemRequest:
    actor_id = (item.actor_id or "vinea")[:ACTOR_ID_LIMIT]
    actor_name = (item.actor_name or "Vinea")[:ACTOR_NAME_LIMIT]
    return SaveItemRequest(
        tin=device.tin or "",
        bhfId=device.bhf_id,
        itemCd=item.item_code,
        itemClsCd=item.item_class_code,
        itemTyCd=item.item_type,
        itemNm=item.name,
        orgnNatCd=item.origin_country,
        pkgUnitCd=item.package_unit,
        qtyUnitCd=item.quantity_unit,
        taxTyCd=item.tax_class,
        bcd=item.barcode,
        dftPrc=wire(item.default_price_inclusive),
        useYn="Y" if item.active else "N",
        regrId=actor_id,
        regrNm=actor_name,
        modrId=actor_id,
        modrNm=actor_name,
    )


def _sales_request(
    device: FiscalDevice,
    document: FiscalSale,
    *,
    receipt_type: str,
    z_report_no: int,
    original_invoice_no: int = 0,
    refund_reason: str | None = None,
) -> SaveSalesRequest:
    actor_id, actor_name = _actor(document)
    totals = bucket_totals(document.lines)
    confirmed_at = _stamp(document.posted_at)
    return SaveSalesRequest(
        tin=device.tin or "",
        bhfId=device.bhf_id,
        invcNo=document.invoice_no,
        orgInvcNo=original_invoice_no,
        custTin=document.party.tin,
        custNm=document.party.name[:CUSTOMER_NAME_LIMIT],
        # v1.0.5: send only `N`. A copy is a *print*, not a second sale, so a reprint makes no
        # call at all (decision 11) — whether a copy needs its own counter is a sandbox
        # question, and it is the only reason this field is not a constant in the model.
        salesTyCd=codes.SalesType.NORMAL,
        rcptTyCd=receipt_type,
        pmtTyCd=_payment_type(document.payment_method),
        salesSttsCd=codes.TransactionProgress.APPROVED,
        cfmDt=confirmed_at,
        salesDt=_day(document.document_date),
        # Only when something physically leaves: a services-only invoice releases no stock,
        # and a release date on it would make RRA expect a movement that never comes.
        stockRlsDt=confirmed_at if document.releases_stock else None,
        rfdDt=confirmed_at if refund_reason else None,
        rfdRsnCd=refund_reason,
        totItemCnt=len(document.lines),
        prchrAcptcYn="N",
        remark=(document.remark or "")[:REMARK_LIMIT] or None,
        regrId=actor_id,
        regrNm=actor_name,
        modrId=actor_id,
        modrNm=actor_name,
        prcOrdCd=document.purchase_code,
        receipt=SalesReceipt(
            custTin=document.party.tin,
            custMblNo=document.party.phone,
            rptNo=z_report_no,
            trdeNm=document.branch_name or None,
            adrs=document.branch_address or None,
            prchrAcptcYn="N",
        ),
        itemList=[_sales_item(line) for line in document.lines],
        **{key: value for key, value in totals.items()},
    )


def build_sale_request(device: FiscalDevice, sale: FiscalSale) -> SaveSalesRequest:
    return _sales_request(
        device,
        sale,
        receipt_type=codes.SalesReceiptType.SALE,
        z_report_no=sale.daily_report_no,
    )


def build_refund_request(device: FiscalDevice, refund: FiscalRefund) -> SaveSalesRequest:
    """A refund, sent with **positive** amounts under `rcptTyCd R`.

    No longer an open question. Sage 200 Evolution — the certified Rwandan integration this
    build takes its conventions from — transmits every refund quantity, price and amount as a
    positive number, and communicates the direction entirely through the header: `rcptTyCd R`
    plus `orgInvcNo` naming the sale being reversed. Negative numbers in the payload are
    rejected by the VSDC schema as negative quantities or invalid decimals.

    The minus signs and the REFUND label belong to the **printed** document (CIS §14) and are
    applied by step 8's layout, never to the wire. `test_a_refund_carries_no_negative_number`
    holds the whole payload to that.
    """
    return _sales_request(
        device,
        refund,
        receipt_type=codes.SalesReceiptType.REFUND,
        z_report_no=refund.daily_report_no,
        original_invoice_no=refund.original_invoice_no,
        refund_reason=refund.reason_code,
    )


def build_purchase_request(
    device: FiscalDevice, purchase: FiscalPurchase
) -> SavePurchaseRequest:
    actor_id, actor_name = _actor(purchase)
    totals = bucket_totals(purchase.lines)
    return SavePurchaseRequest(
        tin=device.tin or "",
        bhfId=device.bhf_id,
        invcNo=purchase.invoice_no,
        spplrTin=purchase.supplier.tin,
        spplrBhfId=purchase.supplier_branch_id,
        spplrNm=purchase.supplier.name[:CUSTOMER_NAME_LIMIT],
        # Already a number or already `None`: RRA types this field as a number, and which
        # references have a numeric form is decided once, above this boundary, because
        # `feed.accept` has to recognise the same invoice by the same rule.
        spplrInvcNo=purchase.supplier_invoice_no,
        # `M`, always: this builder renders a purchase **Vinea originated**, and a
        # confirmation of one the authority already holds is `build_purchase_confirmation_
        # request` with its own `A`. One builder that chose between them would make the
        # difference a field on a DTO rather than two named calls.
        regTyCd=codes.RegistrationType.MANUAL,
        pchsTyCd=codes.SalesType.NORMAL,
        rcptTyCd=(
            codes.PurchaseReceiptType.RETURN
            if purchase.is_return
            else codes.PurchaseReceiptType.PURCHASE
        ),
        pmtTyCd=_payment_type(purchase.payment_method),
        pchsSttsCd=codes.TransactionProgress.APPROVED,
        cfmDt=_stamp(purchase.posted_at),
        pchsDt=_day(purchase.document_date),
        totItemCnt=len(purchase.lines),
        remark=(purchase.remark or "")[:REMARK_LIMIT] or None,
        regrId=actor_id,
        regrNm=actor_name,
        modrId=actor_id,
        modrNm=actor_name,
        itemList=[_purchase_item(line) for line in purchase.lines],
        **{key: value for key, value in totals.items()},
    )


#: Decision 10's table, as (facing, outgoing) → `sarTyCd` (§4.15).
#:
#: **Keyed on the direction rather than on the document kind**, which is what lets one row
#: serve a movement and its reversal: reversing a sale is a customer return (`03`), reversing a
#: goods receipt is a return to the supplier (`12`), and neither needs a case of its own.
#:
#: A transfer is `MOVEMENT_OUT` at dispatch and `MOVEMENT_IN` at receipt, and only **between
#: branches** — a movement inside one branch never reaches this table, because the caller does
#: not report it at all. An import (`01`), processing (`05`/`14`) and discarding (`15`) have no
#: Vinea document: goods arrive through a goods receipt whatever the customs paperwork says
#: (decision 9 — an import declaration moves no stock), assembly is P12, and a write-off is an
#: adjustment out, because a second code for one movement would split one figure across two
#: lines of RRA's stock report.
STOCK_IO_TYPE: dict[tuple[StockMovementFacing, bool], str] = {
    (StockMovementFacing.CUSTOMER, True): codes.StockIoType.SALE_OUT,
    (StockMovementFacing.CUSTOMER, False): codes.StockIoType.RETURN_IN,
    (StockMovementFacing.SUPPLIER, False): codes.StockIoType.PURCHASE_IN,
    (StockMovementFacing.SUPPLIER, True): codes.StockIoType.RETURN_OUT,
    (StockMovementFacing.INTERNAL, False): codes.StockIoType.ADJUSTMENT_IN,
    (StockMovementFacing.INTERNAL, True): codes.StockIoType.ADJUSTMENT_OUT,
    (StockMovementFacing.TRANSFER, True): codes.StockIoType.MOVEMENT_OUT,
    (StockMovementFacing.TRANSFER, False): codes.StockIoType.MOVEMENT_IN,
}


def stock_io_type(facing: StockMovementFacing, *, outgoing: bool) -> str:
    """The authority's in/out code for one movement. A total map: a facing with no code is a
    `KeyError` here rather than a `sarTyCd` guessed at the call site."""
    return STOCK_IO_TYPE[(facing, outgoing)]


def _stock_item_amounts(line) -> tuple[Decimal, Decimal]:  # noqa: ANN001 - FiscalStockLine
    """`(taxblAmt, taxAmt)` for one stock line — the same relation every other payload uses.

    The movement reports a **cost**, and the authority's stock report carries a taxable amount
    and a tax amount beside it. Both are derived here rather than passed in, exactly as
    `line_tax` derives a sale's: `taxblAmt` is the value that moved and `taxAmt` is
    `taxblAmt × r / (100 + r)` at the class's programmed rate. Deriving it the other way —
    grossing the cost up — would make `splyAmt ≠ taxblAmt` on a line nobody discounted, which
    is the phantom-discount failure `_line_amounts` exists to avoid.
    """
    taxable = wire(line.value)
    rate = line.tax_rate_pct
    if rate == ZERO:
        return taxable, ZERO
    return taxable, (taxable * rate / (HUNDRED + rate)).quantize(
        WIRE_EXPONENT, rounding=ROUND_HALF_UP
    )


def build_purchase_confirmation_request(
    device: FiscalDevice, confirmation: FiscalPurchaseConfirmation
) -> SavePurchaseRequest:
    """Accept or decline a purchase RRA is already holding (decision 9).

    The **same endpoint** as a registration — what differs is `regTyCd A`, which says "this is
    a confirmation", and `pchsSttsCd`, which says which way the operator decided. Every figure
    comes from the authority's own record rather than from anything Vinea computed: a
    confirmation that disagreed with the record it confirms would be a third opinion about
    somebody else's sale.

    This is the only builder that reads an authority payload as *input*, which is exactly why
    it is here: the field names below never appear above this package.
    """
    record = confirmation.source
    actor_id = (confirmation.actor_id or "vinea")[:ACTOR_ID_LIMIT]
    actor_name = (confirmation.actor_name or "Vinea")[:ACTOR_NAME_LIMIT]
    items = record.get("itemList") or []
    return SavePurchaseRequest(
        tin=device.tin or "",
        bhfId=device.bhf_id,
        invcNo=confirmation.invoice_no,
        spplrTin=record.get("spplrTin"),
        spplrBhfId=record.get("spplrBhfId"),
        spplrNm=(record.get("spplrNm") or "")[:CUSTOMER_NAME_LIMIT] or None,
        spplrInvcNo=record.get("spplrInvcNo"),
        regTyCd=codes.RegistrationType.AUTOMATIC,
        pchsTyCd=codes.SalesType.NORMAL,
        rcptTyCd=record.get("rcptTyCd") or codes.PurchaseReceiptType.PURCHASE,
        pmtTyCd=record.get("pmtTyCd") or codes.PaymentType.CASH,
        pchsSttsCd=(
            codes.TransactionProgress.APPROVED
            if confirmation.accepted
            else codes.TransactionProgress.CANCELLED
        ),
        cfmDt=record.get("cfmDt") or _stamp(datetime.now(KIGALI)),
        pchsDt=record.get("salesDt") or _day(datetime.now(KIGALI).date()),
        totItemCnt=record.get("totItemCnt") or len(items),
        remark=None,
        regrId=actor_id,
        regrNm=actor_name,
        modrId=actor_id,
        modrNm=actor_name,
        itemList=[PurchaseItem.model_validate(item) for item in items],
        **{
            field: _money(record.get(field))
            for field in (
                "taxblAmtA", "taxblAmtB", "taxblAmtC", "taxblAmtD",
                "taxAmtA", "taxAmtB", "taxAmtC", "taxAmtD",
                "totTaxblAmt", "totTaxAmt", "totAmt",
            )
        },
        **{
            f"taxRt{tax_class}": Decimal(codes.PROGRAMMED_RATES[tax_class])
            for tax_class in ("A", "B", "C", "D")
        },
    )


def build_import_decision_request(
    device: FiscalDevice, decision: FiscalImportDecision
) -> UpdateImportItemRequest:
    """Approve (`imptItemSttsCd 3`) or decline (`4`) one customs line (§4.18).

    The three keys that identify the line — task, declaration date and sequence — come back off
    the authority's own record, because they are the authority's keys and nothing Vinea holds
    reproduces them.
    """
    record = decision.source
    actor_id = (decision.actor_id or "vinea")[:ACTOR_ID_LIMIT]
    actor_name = (decision.actor_name or "Vinea")[:ACTOR_NAME_LIMIT]
    return UpdateImportItemRequest(
        tin=device.tin or "",
        bhfId=device.bhf_id,
        taskCd=record.get("taskCd") or "",
        dclDe=record.get("dclDe") or "",
        itemSeq=int(record.get("itemSeq") or 0),
        hsCd=record.get("hsCd"),
        itemClsCd=decision.item_class_code,
        itemCd=decision.item_code,
        imptItemSttsCd=(
            codes.ImportItemStatus.APPROVED
            if decision.approved
            else codes.ImportItemStatus.CANCELLED
        ),
        remark=(decision.note or "")[:REMARK_LIMIT] or None,
        modrId=actor_id,
        modrNm=actor_name,
    )


def _money(value: object) -> Decimal:
    """A `NUMBER 18,2` field off an authority record, at the wire's two decimals."""
    return wire(Decimal(str(value))) if value is not None else ZERO


def build_stock_io_request(
    device: FiscalDevice, movement: FiscalStockIO
) -> SaveStockIoRequest:
    actor_id, actor_name = _actor(movement)
    amounts = [_stock_item_amounts(line) for line in movement.lines]
    taxable = sum((pair[0] for pair in amounts), ZERO)
    tax = sum((pair[1] for pair in amounts), ZERO)
    return SaveStockIoRequest(
        tin=device.tin or "",
        bhfId=device.bhf_id,
        sarNo=movement.stock_no,
        # **`orgSarNo` is this movement's own number.** The field reads as "the movement this
        # one corrects", the documents neither say so nor show one, and the only sample
        # §3.3.8.2 gives is a plain sale-out movement with `sarNo` and `orgSarNo` both `2`
        # (`tests/fiscal/samples/save_stock_io_request.json`). Nothing in this phase corrects a
        # movement by stock number, so the sample's reading is the only value there is — and
        # the step-3 report carries it as a question for the live run.
        orgSarNo=movement.stock_no,
        regTyCd=codes.RegistrationType.MANUAL,
        sarTyCd=stock_io_type(movement.facing, outgoing=movement.outgoing),
        ocrnDt=_day(movement.occurred_on),
        totItemCnt=len(movement.lines),
        totTaxblAmt=taxable,
        totTaxAmt=tax,
        totAmt=taxable,
        remark=(movement.remark or "")[:REMARK_LIMIT] or None,
        regrId=actor_id,
        regrNm=actor_name,
        modrId=actor_id,
        modrNm=actor_name,
        itemList=[
            StockItem(
                itemSeq=line.sequence,
                itemCd=line.item_code,
                itemClsCd=line.item_class_code,
                itemNm=line.name[:NAME_LIMIT],
                bcd=line.barcode,
                pkgUnitCd=line.package_unit,
                pkg=wire(line.quantity),
                qtyUnitCd=line.quantity_unit,
                qty=wire(line.quantity),
                prc=wire(line.unit_cost),
                splyAmt=wire(line.value),
                totDcAmt=ZERO,
                taxblAmt=item_taxable,
                taxTyCd=line.tax_class,
                taxAmt=item_tax,
                totAmt=item_taxable,
            )
            for line, (item_taxable, item_tax) in zip(
                movement.lines, amounts, strict=True
            )
        ],
    )


def build_stock_master_request(
    device: FiscalDevice, master: FiscalStockMaster
) -> SaveStockMasterRequest:
    """One item per call — §3.3.8.3's request object is flat."""
    actor_id = (master.actor_id or "vinea")[:ACTOR_ID_LIMIT]
    actor_name = (master.actor_name or "Vinea")[:ACTOR_NAME_LIMIT]
    return SaveStockMasterRequest(
        tin=device.tin or "",
        bhfId=device.bhf_id,
        itemCd=master.item_code,
        rsdQty=wire(master.quantity_on_hand),
        regrId=actor_id,
        regrNm=actor_name,
        modrId=actor_id,
        modrNm=actor_name,
    )


def inclusive_unit_price(exclusive_price: Decimal, rate_pct: Decimal) -> Decimal:
    """`unit_price × (100 + r) / 100`, at the wire's two decimals.

    Here rather than at the call site because it is the conversion decision 6 names, and the
    residue it creates is the thing the census counts.
    """
    return wire(exclusive_price * (HUNDRED + rate_pct) / HUNDRED)
