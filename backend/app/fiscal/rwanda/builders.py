"""DTO → payload. The decision-6 map, and the only place it exists.

Split out of `adapter.py` because it is the part worth reading on its own: the adapter is
transport and error policy, this is *what goes in each field*, and a reviewer checking the map
against `docs/rra/contract-notes.md` should not have to scroll past an HTTP client to do it.

Three rules hold throughout:

**Amounts are the posted figures, converted to the wire's two decimals.** `taxblAmt` is the
line's posted gross in base currency and `taxAmt` the tax it attracted — not a recomputation.
The ledger already decided what the sale was worth; a payload that recomputed it would be a
second opinion about a number an auditor can see.

**`prc` is VAT-inclusive at two decimals, and the residue goes to `dcAmt`.** An exclusive
document's unit price becomes `unit_price × (100 + r) / 100`, which on a zero-decimal base
rarely multiplies back to the posted gross exactly. The difference has to land somewhere the
header buckets still add up, and the discount amount is the one field the documents do not
derive from another — so it takes it, while `dcRt` carries the discount somebody actually
keyed. Whether RRA tolerates that is one of the two questions step 5's sandbox run answers.

**The header is Σ of the lines, by class.** Never a separate computation: `totTaxblAmt` is the
sum of the four buckets and each bucket is the sum of its lines, so a payload cannot disagree
with itself. The property test over random documents asserts exactly that.
"""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from app.fiscal.mapping import (
    FiscalItemRegistration,
    FiscalLine,
    FiscalPurchase,
    FiscalRefund,
    FiscalSale,
    FiscalStockIO,
    FiscalStockMaster,
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
    StockMasterItem,
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
    """`(prc, splyAmt, taxblAmt, dcAmt)` for one line.

    `taxblAmt` is the posted gross; `splyAmt` is what the wire price multiplies out to; `dcAmt`
    is the difference. On a discounted line the keyed discount is in `dcRt` and its money value
    is inside this same residue, which is the arithmetic the header buckets need: RRA computes
    `splyAmt − dcAmt` and must land on the posted figure.
    """
    price = wire(line.unit_price_inclusive)
    supply = wire(price * line.quantity)
    taxable = wire(line.taxable_amount)
    return price, supply, taxable, supply - taxable


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
        taxAmt=wire(line.tax_amount),
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
        taxAmt=wire(line.tax_amount),
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
        taxable[line.tax_class] += wire(line.taxable_amount)
        tax[line.tax_class] += wire(line.tax_amount)
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


def build_sale_request(
    device: FiscalDevice, sale: FiscalSale, *, z_report_no: int = 1
) -> SaveSalesRequest:
    return _sales_request(
        device, sale, receipt_type=codes.SalesReceiptType.SALE, z_report_no=z_report_no
    )


def build_refund_request(
    device: FiscalDevice, refund: FiscalRefund, *, z_report_no: int = 1
) -> SaveSalesRequest:
    """A refund, sent with **positive** amounts under `rcptTyCd R`.

    Positive because the receipt type already says which direction this is, and the documents'
    own refund example carries positive figures. Whether RRA expects negatives instead is the
    second sandbox question of decision 6; the ledger and the rounding rule do not change
    either way, and the answer changes this function and nothing else.
    """
    return _sales_request(
        device,
        refund,
        receipt_type=codes.SalesReceiptType.REFUND,
        z_report_no=z_report_no,
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
        # Numeric when it is: RRA types this as a number, and a supplier reference like
        # "INV/2026/0042" has no numeric form — so it goes as null rather than as a mangled
        # integer, and the reference stays on the Vinea document where a human can read it.
        spplrInvcNo=_numeric_or_none(purchase.supplier_invoice_no),
        regTyCd=(
            codes.RegistrationType.AUTOMATIC
            if purchase.confirming
            else codes.RegistrationType.MANUAL
        ),
        pchsTyCd=codes.SalesType.NORMAL,
        rcptTyCd=(
            codes.PurchaseReceiptType.RETURN
            if purchase.is_return
            else codes.PurchaseReceiptType.PURCHASE
        ),
        pmtTyCd=_payment_type(purchase.payment_method),
        pchsSttsCd=(
            codes.TransactionProgress.APPROVED
            if purchase.accepted
            else codes.TransactionProgress.CANCELLED
        ),
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


def build_stock_io_request(
    device: FiscalDevice, movement: FiscalStockIO
) -> SaveStockIoRequest:
    actor_id, actor_name = _actor(movement)
    taxable = sum((wire(line.taxable_amount) for line in movement.lines), ZERO)
    tax = sum((wire(line.tax_amount) for line in movement.lines), ZERO)
    return SaveStockIoRequest(
        tin=device.tin or "",
        bhfId=device.bhf_id,
        sarNo=movement.stock_no,
        orgSarNo=movement.source_invoice_no or 0,
        regTyCd=codes.RegistrationType.MANUAL,
        sarTyCd=movement.movement_type,
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
                taxblAmt=wire(line.taxable_amount),
                taxTyCd=line.tax_class,
                taxAmt=wire(line.tax_amount),
                totAmt=wire(line.taxable_amount),
            )
            for line in movement.lines
        ],
    )


def build_stock_master_request(
    device: FiscalDevice, masters: Sequence[FiscalStockMaster]
) -> SaveStockMasterRequest:
    return SaveStockMasterRequest(
        tin=device.tin or "",
        bhfId=device.bhf_id,
        stockItemList=[
            StockMasterItem(
                itemCd=master.item_code,
                rsdQty=wire(master.quantity_on_hand),
                regrId=(master.actor_id or "vinea")[:ACTOR_ID_LIMIT],
                regrNm=(master.actor_name or "Vinea")[:ACTOR_NAME_LIMIT],
                modrId=(master.actor_id or "vinea")[:ACTOR_ID_LIMIT],
                modrNm=(master.actor_name or "Vinea")[:ACTOR_NAME_LIMIT],
            )
            for master in masters
        ],
    )


def _numeric_or_none(reference: str | None) -> int | None:
    if reference is None:
        return None
    digits = reference.strip()
    return int(digits) if digits.isdigit() else None


def inclusive_unit_price(exclusive_price: Decimal, rate_pct: Decimal) -> Decimal:
    """`unit_price × (100 + r) / 100`, at the wire's two decimals.

    Here rather than at the call site because it is the conversion decision 6 names, and the
    residue it creates is the thing the census counts.
    """
    return wire(exclusive_price * (HUNDRED + rate_pct) / HUNDRED)
