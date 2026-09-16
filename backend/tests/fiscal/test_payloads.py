"""One test per payload model, round-tripping a JSON sample.

**What this proves and what it does not.** It proves the models parse the shape the code was
written to and re-emit it unchanged — no field silently dropped, no amount re-scaled, no
required field the sample does not carry. It does **not** prove that shape is RRA's: the
samples are reconstructed from `docs/rra/contract-notes.md` because the PDFs could not be
fetched (`tests/fiscal/samples/README.md` and `docs/rra/README.md` both say so). Replacing a
sample with the document's own example is a one-file change and the tests do not move.

`extra="forbid"` on requests is what makes the round-trip meaningful in that direction: a
sample carrying a field the model does not know fails here rather than being quietly dropped
on the way to a revenue authority.
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

    assert round_trip(model, payload) == payload


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

    assert round_trip(model, body) == body


@pytest.mark.parametrize("sample", ["sales_response_vsdc", "sales_response_osdc"])
def test_a_sales_response_round_trips_unchanged(sample: str) -> None:
    body = load(sample)["data"]

    assert round_trip(SalesResponseData, body) == body


def test_a_response_tolerates_a_field_rra_adds() -> None:
    """`extra="allow"` on responses, and why the two directions differ.

    RRA adds fields. A response that failed to parse because of one would take a whole tenant
    off the air for a change that does not affect anything this code reads — so unknown fields
    on the way *in* are kept, while unknown fields on the way *out* are refused.
    """
    body = load("sales_response_vsdc")["data"] | {"someFieldAddedNextYear": "x"}

    parsed = SalesResponseData.model_validate(body)

    assert parsed.rcptNo == 1
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


def test_money_serialises_at_two_decimals_from_a_franc_figure() -> None:
    """`NUMBER 18,2`, from a base currency with **no** decimal places.

    This is the conversion the whole payload rests on: a franc figure crosses the boundary as
    a two-decimal string, in exactly one place, and never as a float.
    """
    payload = load("save_sales_request") | {"totAmt": Decimal("45400")}

    emitted = SaveSalesRequest.model_validate(payload).model_dump(mode="json")

    assert emitted["totAmt"] == "45400.00"
    assert emitted["itemList"][0]["prc"] == "2360.00"
    assert all(isinstance(value, str) for value in (emitted["totTaxAmt"], emitted["totTaxblAmt"]))


def test_money_rounds_half_up_like_the_kernel() -> None:
    """Half-up, not banker's rounding — the same rule as `app.kernel.money`.

    Two different roundings of one figure is how a receipt comes to disagree with the ledger
    by a franc, which is the kind of difference a revenue authority asks about.
    """
    payload = load("save_stock_master_request")
    payload["stockItemList"][0]["rsdQty"] = Decimal("0.125")

    emitted = SaveStockMasterRequest.model_validate(payload).model_dump(mode="json")

    assert emitted["stockItemList"][0]["rsdQty"] == "0.13"


def test_an_item_name_is_truncated_rather_than_refused() -> None:
    """RRA caps `itemNm`; a server-side truncation is a receipt that says something slightly
    different from the invoice and nobody finds out, so it happens here where it is visible."""
    payload = load("save_item_request") | {"itemNm": "x" * 250}

    assert len(SaveItemRequest.model_validate(payload).itemNm) == 200
