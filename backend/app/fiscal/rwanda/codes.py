"""The RRA code tables (VSDC §4), as enums.

These are the **seed and the names the adapter reasons with**, not the authority's live list.
RRA publishes these tables and revises them, so `fiscal_codes` rows are what a screen offers
and what `sync_codes` refreshes; what lives here is the subset the code has to *name* — the
tax classes it maps onto, the movement types it chooses between, the result codes it decides
retry policy from. A code the product never branches on belongs in the synced table alone.

Provenance: `docs/rra/contract-notes.md`, which records why it exists rather than the PDFs.

The `item_code` builder at the bottom is here rather than in `payloads.py` because it is a
*format*, not a field: §4.17 composes an item code out of four codes and a seven-digit
sequence, and the composition is the thing worth having one implementation of.
"""

import enum

#: Code classes, as `/code/selectCodes` returns them. Named so a sync can key the rows it
#: stores, and so a screen asking for "the packaging units" does not carry a bare "17".
CLASS_TAX_TYPE = "04"
CLASS_PAYMENT_TYPE = "07"
CLASS_QUANTITY_UNIT = "10"
CLASS_TRANSACTION_PROGRESS = "11"
CLASS_STOCK_IO_TYPE = "12"
CLASS_PACKAGING_UNIT = "17"
CLASS_PRODUCT_TYPE = "24"
CLASS_REGISTRATION_TYPE = "31"
CLASS_REFUND_REASON = "32"
CLASS_SALES_RECEIPT_TYPE = "37"
CLASS_PURCHASE_RECEIPT_TYPE = "38"

#: Every class this phase syncs and offers. A class not in here is one no screen shows.
SYNCED_CODE_CLASSES: tuple[str, ...] = (
    CLASS_TAX_TYPE,
    CLASS_PAYMENT_TYPE,
    CLASS_QUANTITY_UNIT,
    CLASS_TRANSACTION_PROGRESS,
    CLASS_STOCK_IO_TYPE,
    CLASS_PACKAGING_UNIT,
    CLASS_PRODUCT_TYPE,
    CLASS_REGISTRATION_TYPE,
    CLASS_REFUND_REASON,
    CLASS_SALES_RECEIPT_TYPE,
    CLASS_PURCHASE_RECEIPT_TYPE,
)


class TaxType(enum.StrEnum):
    """§4.1 — the four tax classes, and the rates RRA programs them at.

    The rate lives beside the class in `PROGRAMMED_RATES` below rather than being read off the
    Vinea tax code, because CIS §7.22–7.23 requires **every programmed rate above zero** to
    print on every receipt — including on a receipt with no line at that rate.
    """

    EXEMPT = "A"
    STANDARD = "B"
    ZERO_RATED = "C"
    NON_VAT = "D"


#: class → the rate RRA has programmed. The only place a rate is hard-coded; when RRA changes
#: it, `sync_codes` brings the new one and this becomes the fallback.
PROGRAMMED_RATES: dict[str, str] = {
    TaxType.EXEMPT: "0.00",
    TaxType.STANDARD: "18.00",
    TaxType.ZERO_RATED: "0.00",
    TaxType.NON_VAT: "0.00",
}


class PaymentType(enum.StrEnum):
    """§4.5 — `pmtTyCd`."""

    CASH = "01"
    CREDIT = "02"
    CASH_CREDIT = "03"
    BANK_CHEQUE = "04"
    CARD = "05"
    MOBILE_MONEY = "06"
    OTHER = "07"


class TransactionProgress(enum.StrEnum):
    """§4.8 — `salesSttsCd` / `pchsSttsCd`. Vinea posts finished documents, so everything it
    sends is `APPROVED`; `CANCELLED` and `REFUND_APPROVED` are here because the purchase feed
    returns them and a rejected feed row is sent back as `CANCELLED`."""

    WAIT_APPROVAL = "01"
    APPROVED = "02"
    CANCEL_REQUESTED = "03"
    CANCELLED = "04"
    CREDIT_NOTE_REQUESTED = "05"
    CREDIT_NOTE_APPROVED = "06"
    TRANSFERRED = "07"


class StockIoType(enum.StrEnum):
    """§4.9 — `sarTyCd`. In and out are separate codes for the same business event, which is
    why the mapping from a Vinea source document to one of these is a table rather than a
    sign: a sale is `SALE` and a customer return is `RETURN_IN`, not "sale with a minus"."""

    IMPORT_IN = "01"
    PURCHASE_IN = "02"
    RETURN_IN = "03"
    MOVEMENT_IN = "04"
    PROCESSING_IN = "05"
    ADJUSTMENT_IN = "06"
    SALE_OUT = "11"
    RETURN_OUT = "12"
    MOVEMENT_OUT = "13"
    PROCESSING_OUT = "14"
    ADJUSTMENT_OUT = "16"


class RegistrationType(enum.StrEnum):
    """§4.13 — `regTyCd`. `MANUAL` is a purchase this system originated; `AUTOMATIC` is a
    confirmation of one RRA is already holding."""

    MANUAL = "M"
    AUTOMATIC = "A"


class SalesReceiptType(enum.StrEnum):
    """§4.14 — `rcptTyCd`."""

    SALE = "S"
    REFUND = "R"


class PurchaseReceiptType(enum.StrEnum):
    """§4.15 — the purchase side's `rcptTyCd`."""

    PURCHASE = "P"
    RETURN = "R"


class SalesType(enum.StrEnum):
    """§4.14 — `salesTyCd`. v1.0.5 says to send `NORMAL` only, so the other three are here for
    reading a response rather than for writing a request. Training and proforma are out of
    scope for this phase; `COPY` is an open question the sandbox run settles (decision 11)."""

    NORMAL = "N"
    COPY = "C"
    TRAINING = "T"
    PROFORMA = "P"


class ProductType(enum.StrEnum):
    """§4.4 — the product-type segment of an item code."""

    RAW_MATERIAL = "1"
    FINISHED_PRODUCT = "2"
    SERVICE = "3"


class ImportItemStatus(enum.StrEnum):
    """§4.18 — `imptItemSttsCd`. Only the two an operator can choose are used."""

    WAIT = "2"
    APPROVED = "3"
    REJECTED = "4"


class RefundReason(enum.StrEnum):
    """§4.16 — `rfdRsnCd`, required on every credit note.

    Required and **not defaulted**: "why was this refunded" is a question only the person
    issuing the credit note can answer, and a default would put the same answer on every one.
    """

    WRONG_QUANTITY = "01"
    WRONG_PRICE = "02"
    DAMAGED_GOODS = "03"
    WRONG_ITEM = "04"
    ORDER_CANCELLED = "05"
    RETURN_OF_GOODS = "06"
    INVOICE_ERROR = "07"
    DUPLICATE_INVOICE = "08"
    CUSTOMER_DISSATISFIED = "09"
    EXPIRED_GOODS = "10"
    DISCOUNT_ADJUSTMENT = "11"
    TAX_CORRECTION = "12"
    OTHER = "13"


# --- Response codes ----------------------------------------------------------------------
#
# The retry policy is decided from these, so they are constants rather than literals scattered
# through the drainer.

RESULT_OK = "000"
#: Transport and temporary server trouble: retry on the backoff, forever.
RESULT_TEMPORARY = "894"
#: A business customer's sale with no purchase code, and the codes for one that is wrong.
RESULT_PURCHASE_CODE_REQUIRED = "881"
RESULT_PURCHASE_CODE_INVALID = "882"
RESULT_PURCHASE_CODE_USED = "883"
RESULT_UNKNOWN_TIN = "884"
#: A duplicate. **Returns no receipt data**, which is the whole reason a request whose answer
#: never arrived is never resent blindly: the resend succeeds as a duplicate and there is
#: nothing to print.
RESULT_DUPLICATE = "994"

#: Codes the drainer retries. Everything else that is not `000` is a refusal RRA will make
#: again, so retrying it only fills a log.
RETRYABLE_RESULT_CODES: frozenset[str] = frozenset({RESULT_TEMPORARY})


def is_retryable(result_code: str) -> bool:
    return result_code in RETRYABLE_RESULT_CODES


# --- §4.17, the item code -----------------------------------------------------------------

#: How wide the quantity-unit segment is, and what it is padded with. The documents' own
#: samples show `RW1NTXU0000006` — a one-character unit `U` padded to two with `X` — and the
#: padding character is not stated in prose anywhere the build could find. Written as
#: constants so that the sandbox run at step 5 changes one line rather than a format string.
QUANTITY_UNIT_WIDTH = 2
QUANTITY_UNIT_PAD = "X"
ITEM_SEQUENCE_WIDTH = 7


def build_item_code(
    *,
    origin_country: str,
    product_type: str,
    packaging_unit: str,
    quantity_unit: str,
    sequence_no: int,
) -> str:
    """§4.17: origin + product type + packaging unit + quantity unit + a 7-digit sequence.

    The sequence is claimed from the `FITM` run, so it is gapless per taxpayer — which is
    what makes the code itself a usable audit key rather than an opaque string.
    """
    padded_unit = quantity_unit.rjust(QUANTITY_UNIT_WIDTH, QUANTITY_UNIT_PAD)[
        :QUANTITY_UNIT_WIDTH
    ]
    return (
        f"{origin_country.upper()}"
        f"{product_type}"
        f"{packaging_unit.upper()}"
        f"{padded_unit.upper()}"
        f"{sequence_no:0{ITEM_SEQUENCE_WIDTH}d}"
    )
