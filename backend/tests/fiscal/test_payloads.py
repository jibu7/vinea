"""One test per payload model, round-tripping a JSON sample.

**The samples are the documents' own** — lifted from the `JSON REQUEST SAMPLE` blocks of the
two specifications pinned under `docs/rra/`. `tests/fiscal/samples/README.md` records the two
edits made to them (whitespace injected into keys by text extraction, and one smart-quote typo
in the v1.0.5 sales sample).

So this proves the models parse what RRA publishes and re-emit it unchanged: no field dropped,
no amount re-scaled, no required field the document's own sample does not carry. `extra="forbid"`
on requests makes it bite in the other direction too — a sample carrying a field the model does
not know fails here rather than being quietly dropped on the way to a revenue authority. Three
corrections came out of exactly that: `saveStockMaster` takes one item per call rather than a
list, the refund reasons are not the thirteen this build first guessed at, and money crosses the
wire as a JSON number.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from app.fiscal.rwanda.payloads import (
    CodeListData,
    CustomerData,
    CustomerRequest,
    ImportItemsData,
    ImportItemsRequest,
    InitData,
    InitRequest,
    ItemClassData,
    PurchaseFeedData,
    ResultEnvelope,
    SalesResponseData,
    SaveItemRequest,
    SavePurchaseRequest,
    SaveSalesRequest,
    SaveStockIoRequest,
    SaveStockMasterRequest,
    UpdateImportItemRequest,
)

SAMPLES = Path(__file__).parent / "samples"


def load(name: str) -> dict:
    return json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def round_trip(model: type[BaseModel], payload: dict) -> dict:
    """Parse, re-emit, and hand back what came out — so the caller compares, not this."""
    return model.model_validate(payload).model_dump(mode="json", exclude_none=True)


def without_nulls(value: object) -> object:
    """The sample with its explicit `null`s removed, recursively.

    The documents spell optional fields out as `"btchNo": null`; the adapter omits them
    (`exclude_none=True`), which is the same thing to the Jackson server on the other end — an
    absent field and an explicit null both deserialise to null. Comparing after dropping them
    keeps the assertion on what it is actually about: **no value is lost, changed or
    invented**. A field the model did not know would still fail, because requests are
    `extra="forbid"` and parsing would have raised before this ran.
    """
    if isinstance(value, dict):
        return {k: without_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [without_nulls(v) for v in value]
    return value


# --- Requests: every field survives, and no invented field is tolerated --------------------


@pytest.mark.parametrize(
    ("model", "sample"),
    [
        (InitRequest, "init_request"),
        (CustomerRequest, "customer_request"),
        (SaveItemRequest, "save_item_request"),
        (SaveSalesRequest, "save_sales_request"),
        (SavePurchaseRequest, "save_purchase_request"),
        (SaveStockIoRequest, "save_stock_io_request"),
        (SaveStockMasterRequest, "save_stock_master_request"),
        (UpdateImportItemRequest, "update_import_item_request"),
        (ImportItemsRequest, "watermarked_request"),
    ],
)
def test_a_request_round_trips_unchanged(model: type[BaseModel], sample: str) -> None:
    payload = load(sample)

    assert round_trip(model, payload) == without_nulls(payload)


def test_a_request_refuses_a_field_the_model_does_not_know() -> None:
    """The point of `extra="forbid"` on requests.

    A field this code invented is a field RRA ignores while the build believes it was sent —
    which is the worst kind of wrong, because everything looks fine until an auditor asks why
    a figure is missing from a return.
    """
    payload = load("init_request") | {"dvcSerialNo": "typo-for-dvcSrlNo"}

    with pytest.raises(ValidationError) as error:
        InitRequest.model_validate(payload)

    assert "dvcSerialNo" in str(error.value)


# --- Responses: every field survives, and a new one does not break parsing -----------------


@pytest.mark.parametrize(
    ("model", "sample"),
    [
        (InitData, "init_response"),
        (CodeListData, "code_list_response"),
        (ItemClassData, "item_class_response"),
        (CustomerData, "customer_response"),
        (PurchaseFeedData, "purchase_feed_response"),
        (ImportItemsData, "import_items_response"),
    ],
)
def test_a_response_body_round_trips_unchanged(model: type[BaseModel], sample: str) -> None:
    body = load(sample)["data"]

    assert round_trip(model, body) == without_nulls(body)


def test_the_vsdc_sales_response_round_trips_unchanged() -> None:
    body = load("sales_response_vsdc")["data"]

    assert round_trip(SalesResponseData, body) == without_nulls(body)


def test_the_osdc_sales_response_types_its_counters_as_strings() -> None:
    """And the model reads them as numbers, which is coercion doing real work.

    v1.0.5 returns `"rcptNo": 27`; v1.0.1 returns `"curRcptNo": "1"`. Both are receipt
    counters, `fiscal_receipts` stores them as integers, and `assert_fiscal_invariants` asserts
    they strictly increase — which is arithmetic, and `"10" < "9"` as strings. Pinned as its
    own test rather than folded into a round-trip, because the round-trip would emit `1` and
    look like a mismatch where it is the point.
    """
    body = load("sales_response_osdc")["data"]
    assert body["curRcptNo"] == "1", "the document really does quote it"

    parsed = SalesResponseData.model_validate(body)

    assert parsed.curRcptNo == 1
    assert parsed.totRcptNo == 1
    assert parsed.sdcDateTime == "20210502115145"


def test_a_response_tolerates_a_field_rra_adds() -> None:
    """`extra="allow"` on responses, and why the two directions differ.

    RRA adds fields. A response that failed to parse because of one would take a whole tenant
    off the air for a change that does not affect anything this code reads — so unknown fields
    on the way *in* are kept, while unknown fields on the way *out* are refused.
    """
    body = load("sales_response_vsdc")["data"] | {"someFieldAddedNextYear": "x"}

    parsed = SalesResponseData.model_validate(body)

    assert parsed.rcptNo == 27
    assert parsed.model_dump(mode="json", exclude_none=True)["someFieldAddedNextYear"] == "x"


def test_every_envelope_carries_a_result_code() -> None:
    for sample in (
        "init_response",
        "code_list_response",
        "item_class_response",
        "customer_response",
        "sales_response_vsdc",
        "sales_response_osdc",
        "purchase_feed_response",
        "import_items_response",
    ):
        envelope = ResultEnvelope.model_validate(load(sample))
        assert envelope.ok, sample


# --- Money on the wire --------------------------------------------------------------------


def test_money_crosses_the_wire_as_a_json_number() -> None:
    """`NUMBER 18,2`, unquoted, from a base currency with **no** decimal places.

    RRA's own samples are `"taxblAmt":200000` and `"taxAmt":30508` — numbers, integral where
    the value is. This is the conversion the whole payload rests on and it happens in one
    place; a quoted `"200000.00"` would very probably parse on their side, and "probably" is
    not a thing to discover in production.
    """
    payload = load("save_sales_request") | {"totAmt": Decimal("45400")}

    emitted = SaveSalesRequest.model_validate(payload).model_dump(mode="json")

    assert emitted["totAmt"] == 45400
    assert isinstance(emitted["totAmt"], int)
    assert all(
        isinstance(value, int | float)
        for value in (emitted["totTaxAmt"], emitted["totTaxblAmt"], emitted["taxRtB"])
    )


def test_a_fractional_amount_keeps_its_two_decimals() -> None:
    """An inclusive unit price is the one figure on an RWF payload that is not whole:
    2 000 x 1.18 is 2 360, but 105 x 1.18 is 123.90. RRA's Korean samples carry exactly this
    shape (`"taxAmt":100677.97`)."""
    payload = load("save_stock_master_request") | {"rsdQty": Decimal("123.90")}

    assert SaveStockMasterRequest.model_validate(payload).model_dump(mode="json")["rsdQty"] == (
        123.9
    )


def test_money_rounds_half_up_like_the_kernel() -> None:
    """Half-up, not banker's rounding — the same rule as `app.kernel.money`, and the same rule
    the RRA certification checkpoint sheet states in row 47 ("<5 down, >=5 up").

    Two different roundings of one figure is how a receipt comes to disagree with the ledger
    by a franc, which is the kind of difference a revenue authority asks about.
    """
    payload = load("save_stock_master_request") | {"rsdQty": Decimal("0.125")}

    emitted = SaveStockMasterRequest.model_validate(payload).model_dump(mode="json")

    assert emitted["rsdQty"] == 0.13


def test_an_item_name_is_truncated_rather_than_refused() -> None:
    """RRA caps `itemNm`; a server-side truncation is a receipt that says something slightly
    different from the invoice and nobody finds out, so it happens here where it is visible."""
    payload = load("save_item_request") | {"itemNm": "x" * 250}

    assert len(SaveItemRequest.model_validate(payload).itemNm) == 200
