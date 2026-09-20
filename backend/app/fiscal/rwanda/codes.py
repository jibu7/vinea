"""The RRA code tables (VSDC §4), as enums.

These are the **seed and the names the adapter reasons with**, not the authority's live list.
RRA publishes these tables and revises them, so `fiscal_codes` rows are what a screen offers
and what `sync_codes` refreshes; what lives here is the subset the code has to *name* — the
tax classes it maps onto, the movement types it chooses between, the result codes it decides
retry policy from. A code the product never branches on belongs in the synced table alone.

Provenance: **VSDC API documentation v1.0.5 §4**, pinned at
`docs/rra/VSDC_SPECIFICATION_DOCUMENT_v1.0.5_okay.pdf`. Section numbers below are that
document's; `docs/rra/contract-notes.md` summarises them.

The `item_code` builder at the bottom is here rather than in `payloads.py` because it is a
*format*, not a field: §4.17 composes an item code out of four codes and a seven-digit
sequence, and the composition is the thing worth having one implementation of.
"""

import enum

#: Code classes, as `/code/selectCodes` returns them. Named so a sync can key the rows it
#: stores, and so a screen asking for "the packaging units" does not carry a bare "17".
CLASS_TAX_TYPE = "04"  # §4.1
CLASS_NATION = "05"  # §4.4
CLASS_PAYMENT_TYPE = "07"  # §4.10
CLASS_QUANTITY_UNIT = "10"  # §4.6
CLASS_TRANSACTION_PROGRESS = "11"  # §4.11
CLASS_STOCK_IO_TYPE = "12"  # §4.15
CLASS_TRANSACTION_TYPE = "14"  # §4.8 — `salesTyCd` / `pchsTyCd`
CLASS_TAXPAYER_STATUS = "15"  # §4.2
CLASS_PACKAGING_UNIT = "17"  # §4.5
CLASS_PRODUCT_TYPE = "24"  # §4.3
CLASS_IMPORT_ITEM_STATUS = "26"  # §4.18
CLASS_REGISTRATION_TYPE = "31"  # §4.12
CLASS_REFUND_REASON = "32"  # §4.16
CLASS_CURRENCY = "33"  # §4.7
CLASS_SALES_RECEIPT_TYPE = "37"  # §4.9
CLASS_PURCHASE_RECEIPT_TYPE = "38"  # §4.13

#: Every class this phase syncs and offers. A class not in here is one no screen shows.
SYNCED_CODE_CLASSES: tuple[str, ...] = (
    CLASS_TAX_TYPE,
    CLASS_NATION,
    CLASS_PAYMENT_TYPE,
    CLASS_QUANTITY_UNIT,
    CLASS_TRANSACTION_PROGRESS,
    CLASS_STOCK_IO_TYPE,
    CLASS_TRANSACTION_TYPE,
    CLASS_PACKAGING_UNIT,
    CLASS_PRODUCT_TYPE,
    CLASS_IMPORT_ITEM_STATUS,
    CLASS_REGISTRATION_TYPE,
    CLASS_REFUND_REASON,
    CLASS_SALES_RECEIPT_TYPE,
    CLASS_PURCHASE_RECEIPT_TYPE,
)


class TaxType(enum.StrEnum):
    """§4.1 — the four tax classes, and the rates RRA programs them at.

    The published code names are `A-EX`, `B-18.00%`, `C` and `D`: only the standard rate
    carries its percentage in the name, because only a rate above zero prints unconditionally.

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
    """§4.10 — `pmtTyCd`."""

    CASH = "01"
    CREDIT = "02"
    CASH_CREDIT = "03"
    BANK_CHEQUE = "04"
    CARD = "05"
    MOBILE_MONEY = "06"
    OTHER = "07"


class TransactionProgress(enum.StrEnum):
    """§4.11 — `salesSttsCd` / `pchsSttsCd`. Vinea posts finished documents, so everything it
    sends is `APPROVED`; the rest are here because the purchase feed returns them and a
    rejected feed row is sent back as `CANCELLED`."""

    WAIT_APPROVAL = "01"
    APPROVED = "02"
    CANCEL_REQUESTED = "03"
    CANCELLED = "04"
    REFUNDED = "05"
    TRANSFERRED = "06"


class StockIoType(enum.StrEnum):
    """§4.15 — `sarTyCd`. In and out are separate codes for the same business event, which is
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
    #: Outgoing — discarding. No Vinea document maps to it: a write-off is an adjustment out
    #: (decision 10), and a second code for the same movement would split the same figure
    #: across two lines of RRA's stock report. Listed because the table has it.
    DISCARDING_OUT = "15"
    ADJUSTMENT_OUT = "16"


class RegistrationType(enum.StrEnum):
    """§4.12 — `regTyCd`. `MANUAL` is a purchase this system originated; `AUTOMATIC` is a
    confirmation of one RRA is already holding."""

    MANUAL = "M"
    AUTOMATIC = "A"


class SalesReceiptType(enum.StrEnum):
    """§4.9 — `rcptTyCd`. `R` is "Refund after Sale"."""

    SALE = "S"
    REFUND = "R"


class PurchaseReceiptType(enum.StrEnum):
    """§4.13 — the purchase side's `rcptTyCd`. `R` is "Refund after Purchase"."""

    PURCHASE = "P"
    RETURN = "R"


class SalesType(enum.StrEnum):
    """§4.8 — `salesTyCd` / `pchsTyCd`. v1.0.5 says to send `NORMAL` only, so the other three
    are here for reading a response rather than for writing a request. Training and proforma
    are out of scope for this phase; `COPY` is an open question decision 11 leaves open."""

    NORMAL = "N"
    COPY = "C"
    TRAINING = "T"
    PROFORMA = "P"


class ProductType(enum.StrEnum):
    """§4.3 — the product-type segment of an item code. `3` is "Service without stock"."""

    RAW_MATERIAL = "1"
    FINISHED_PRODUCT = "2"
    SERVICE = "3"


class ImportItemStatus(enum.StrEnum):
    """§4.18 — `imptItemSttsCd`. Only the two an operator can choose are ever sent.

    `4` is published as **Cancelled**, not "rejected" — the operator's act is to decline the
    declaration, and the name says what RRA calls the resulting state rather than what the
    button says.
    """

    UNSENT = "1"
    WAITING = "2"
    APPROVED = "3"
    CANCELLED = "4"


class RefundReason(enum.StrEnum):
    """§4.16 — `rfdRsnCd`, required on every credit note.

    The names are RRA's own, verbatim from the table. They are not a tidy taxonomy — "Refund"
    is itself one of the thirteen reasons for a refund — and they are kept as published rather
    than improved, because the code is what RRA reports on and a friendlier name here would
    only mislead whoever compares a Vinea credit note with an EBM report.

    Required and **not defaulted**: "why was this refunded" is a question only the person
    issuing the credit note can answer, and a default would put the same answer on every one.
    """

    MISSING_QUANTITY = "01"
    MISSING_ITEM = "02"
    DAMAGED = "03"
    WASTED = "04"
    RAW_MATERIAL_SHORTAGE = "05"
    REFUND = "06"
    WRONG_CUSTOMER_TIN = "07"
    WRONG_CUSTOMER_NAME = "08"
    WRONG_AMOUNT_OR_PRICE = "09"
    WRONG_QUANTITY = "10"
    WRONG_ITEMS = "11"
    WRONG_TAX_TYPE = "12"
    OTHER_REASON = "13"


#: §4.16's names beside its codes, so the sandbox can publish the table the way
#: `/code/selectCodes` does and a credit note's reason picker is fed by a **sync** rather than
#: by thirteen strings typed into a screen. Keeping them here keeps them inside
#: `app/fiscal/rwanda/`, which is the only place that may know what RRA calls anything.
REFUND_REASON_NAMES: dict[str, str] = {
    RefundReason.MISSING_QUANTITY: "Missing quantity",
    RefundReason.MISSING_ITEM: "Missing item",
    RefundReason.DAMAGED: "Damaged",
    RefundReason.WASTED: "Wasted",
    RefundReason.RAW_MATERIAL_SHORTAGE: "Raw material shortage",
    RefundReason.REFUND: "Refund",
    RefundReason.WRONG_CUSTOMER_TIN: "Wrong customer TIN",
    RefundReason.WRONG_CUSTOMER_NAME: "Wrong customer name",
    RefundReason.WRONG_AMOUNT_OR_PRICE: "Wrong amount or price",
    RefundReason.WRONG_QUANTITY: "Wrong quantity",
    RefundReason.WRONG_ITEMS: "Wrong items",
    RefundReason.WRONG_TAX_TYPE: "Wrong tax type",
    RefundReason.OTHER_REASON: "Other reason",
}


# --- Response codes ----------------------------------------------------------------------
#
# The retry policy is decided from these, so they are constants rather than literals scattered
# through the drainer.

RESULT_OK = "000"
#: "There is no search result" — an *answer*, not a failure. A TIN lookup that finds nothing
#: comes back here or on `884`, and both mean the same thing to the person who asked.
RESULT_NO_RESULT = "001"
#: "An error regarding server communication occurred": retry on the backoff, forever.
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
#: The ordering rules, in RRA's own words: `921` "Sales or sales invoice data which is declared
#: cannot be received" and `922` "Sales invoice data can be received after receiving the sales
#: data". They are the authority's statement of why the outbox drains per device in FIFO with
#: one row in flight — a stock report that overtakes its sale is refused, not merely untidy.
RESULT_SALES_NOT_RECEIVABLE = "921"
RESULT_SALES_MUST_COME_FIRST = "922"

#: Codes the drainer retries. Everything else that is not `000` is a refusal RRA will make
#: again, so retrying it only fills a log.
RETRYABLE_RESULT_CODES: frozenset[str] = frozenset({RESULT_TEMPORARY})


def is_retryable(result_code: str) -> bool:
    return result_code in RETRYABLE_RESULT_CODES


# --- §4.17, the item code -----------------------------------------------------------------

#: §4.6's own list, seeded from the pinned document and refreshed by `sync_codes` like the
#: other code tables. It is here so that nothing — production or test — can compose an item
#: code out of a unit RRA does not publish: the first version of this file was pinned against
#: a `NOX` that does not exist in §4.6, which made a wrong padding rule look right.
QUANTITY_UNITS: tuple[str, ...] = (
    "4B", "AV", "BA", "BE", "BG", "BL", "BLL", "BX", "CA", "CEL", "CMT", "CR", "DR", "DZ",
    "GLL", "GRM", "GRO", "KG", "KTM", "KWT", "L", "LBR", "LK", "LTR", "M", "M2", "M3", "MGM",
    "MTR", "MWT", "NO", "NX", "PA", "PG", "PR", "RL", "RO", "SET", "ST", "TNE", "TU", "U",
    "YRD",
)

#: **`X` terminates a two-character segment.** Both segments — packaging unit and quantity
#: unit — get it, and one- and three-character codes are left exactly as they are.
#:
#: The rule is nowhere in prose. §4.17 gives a format and some worked examples, so the examples
#: are the specification, and there are five of them. Four come out of real VSDC/EBM systems
#: and agree with each other:
#:
#: * `RW1NTXU0000006` — VSDC, whose item carries `qtyUnitCd: "U"`. `NT`→`NTX`, `U` stands.
#: * `KR2AMXBLL0000001` — VSDC's Korean sample. `AM`→`AMX`, `BLL` stands.
#: * `RW2NTXU0000002` — a live EBM 2.1 receipt. `NT`→`NTX`, `U` stands.
#: * `RW2NTXNOX0000014` — a live EBM 2.1 receipt. `NT`→`NTX`, `NO`→`NOX`.
#:
#: The fifth, §4.17's hand-written worked example `RW2NTBA0000012`, is the **outlier**: its
#: own prose breaks it down as `NT` packaging + `BA` quantity with no `X` anywhere, which no
#: machine-generated code does. It is prose in a specification, not output from a device, and
#: it is the only one of the five that a rule fitting the other four gets wrong.
#:
#: Why a terminator rather than padding: the segments are variable width (§4.5 and §4.6 both
#: publish one-, two- and three-character codes), so a reader needs to know where one ends.
#: Reading `X` as *padding to two characters* — which is what this file did first — fits only
#: the two shortest examples and mangles every three-character unit. Both readings are still
#: guesses about a format RRA has not written down, which is why the live run at step 5 carries
#: it as a question.
SEGMENT_TERMINATED_WIDTH = 2
SEGMENT_TERMINATOR = "X"
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

    `RW2NTBA0000012` — Rwanda, finished product, Net packaging, Barrel quantity, item 12.

    The sequence is claimed from the `FITM` run, so it is gapless per taxpayer — which is
    what makes the code itself a usable audit key rather than an opaque string.
    """
    return (
        f"{origin_country.upper()}"
        f"{product_type}"
        f"{_terminated(packaging_unit)}"
        f"{_terminated(quantity_unit)}"
        f"{sequence_no:0{ITEM_SEQUENCE_WIDTH}d}"
    )


def _terminated(code: str) -> str:
    """A code-table segment of an item code: `X`-terminated when it is two characters wide."""
    upper = code.upper()
    return f"{upper}{SEGMENT_TERMINATOR}" if len(upper) == SEGMENT_TERMINATED_WIDTH else upper
