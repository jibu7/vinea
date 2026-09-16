"""The in-repo EBM server.

It exists to be *strict*: a payload it accepts is a payload with a real chance of passing
certification, and every check here is one a real device makes. So these tests drive it the way
the adapter will — through both profiles, through every mode — and assert on the refusals as
much as on the successes. A sandbox that accepted everything would be a mock with extra steps.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.fiscal.rwanda import codes
from app.fiscal.rwanda.sandbox import (
    SANDBOX_MRC_NO,
    SANDBOX_SDC_ID,
    SandboxState,
    create_sandbox_app,
)

SALE = {
    "tin": "999000099",
    "bhfId": "00",
    "invcNo": 1,
    "custTin": "100000001",
    "custNm": "Customer C Ltd",
    "prcOrdCd": "AB12CD",
    "rcptTyCd": "S",
    "pmtTyCd": "02",
    "salesSttsCd": "02",
    "cfmDt": "20260316103000",
    "salesDt": "20260316",
    "totItemCnt": 1,
    "taxblAmtA": "0.00",
    "taxblAmtB": "11800.00",
    "taxblAmtC": "0.00",
    "taxblAmtD": "0.00",
    "taxAmtA": "0.00",
    "taxAmtB": "1800.00",
    "taxAmtC": "0.00",
    "taxAmtD": "0.00",
    "totTaxblAmt": "11800.00",
    "totTaxAmt": "1800.00",
    "totAmt": "11800.00",
    "regrId": "1",
    "regrNm": "Owner",
    "modrId": "1",
    "modrNm": "Owner",
    "receipt": {"rptNo": 1},
    "itemList": [
        {
            "itemSeq": 1,
            "itemCd": "RW2NTXU0000001",
            "itemClsCd": "5059020800",
            "itemNm": "Rugari Red 75cl",
            "pkgUnitCd": "NT",
            "pkg": "10.00",
            "qtyUnitCd": "U",
            "qty": "10.00",
            "prc": "1180.00",
            "splyAmt": "11800.00",
            "dcRt": "0.00",
            "dcAmt": "0.00",
            "taxTyCd": "B",
            "taxblAmt": "11800.00",
            "taxAmt": "1800.00",
            "totAmt": "11800.00",
        }
    ],
}


@pytest.fixture
def client(sandbox_state: SandboxState):  # noqa: ANN201
    with TestClient(create_sandbox_app(sandbox_state), base_url="http://ebm.sandbox") as http:
        yield http


def sale(**overrides) -> dict:  # noqa: ANN003
    return {**SALE, **overrides}


def test_initialization_returns_the_fixed_identity(client: httpx.Client) -> None:
    """Fixed, because the acceptance tape asserts these as literals; a random `sdcId` would
    make the tape unwritable."""
    response = client.post(
        "/initializer/selectInitInfo",
        json={"tin": "999000099", "bhfId": "00", "dvcSrlNo": "SRL1"},
    )

    info = response.json()["data"]["info"]
    assert info["sdcId"] == SANDBOX_SDC_ID
    assert info["mrcNo"] == SANDBOX_MRC_NO
    assert info["lastSaleInvcNo"] == 0


def test_a_valid_sale_is_signed_and_counted(client: httpx.Client) -> None:
    response = client.post("/trnsSales/saveSales", json=sale())

    body = response.json()
    assert body["resultCd"] == codes.RESULT_OK
    receipt = body["data"]
    assert receipt["rcptNo"] == 1
    assert receipt["totRcptNo"] == 1
    assert len(receipt["intrlData"]) == 26
    assert len(receipt["rcptSign"]) == 16


def test_counters_run_per_type_and_across_types(client: httpx.Client) -> None:
    """The `A/B RT` pair the receipt prints (CIS §7.25): the counter within the type, and the
    counter across all types. A refund is `1/2`, not `2/2`."""
    client.post("/trnsSales/saveSales", json=sale(invcNo=1))
    refund = client.post(
        "/trnsSales/saveSales", json=sale(invcNo=2, rcptTyCd="R", orgInvcNo=1, rfdRsnCd="06")
    ).json()["data"]

    assert (refund["rcptNo"], refund["totRcptNo"]) == (1, 2)


def test_a_duplicate_invoice_number_returns_no_receipt(client: httpx.Client) -> None:
    """`994`, **with no receipt data** — the fact the entire `unknown` policy turns on.

    A blind resend of a sale whose answer was lost succeeds here and leaves the caller with a
    registered sale and nothing to print. That is why an unanswered request is resolved by
    asking the device what it holds rather than by sending again.
    """
    client.post("/trnsSales/saveSales", json=sale())

    body = client.post("/trnsSales/saveSales", json=sale()).json()

    assert body["resultCd"] == codes.RESULT_DUPLICATE
    assert body["data"] is None


def test_a_business_sale_without_a_purchase_code_is_refused(client: httpx.Client) -> None:
    body = client.post("/trnsSales/saveSales", json=sale(prcOrdCd=None)).json()

    assert body["resultCd"] == codes.RESULT_PURCHASE_CODE_REQUIRED


def test_an_unknown_customer_tin_is_refused(client: httpx.Client) -> None:
    body = client.post("/trnsSales/saveSales", json=sale(custTin="123456789")).json()

    assert body["resultCd"] == codes.RESULT_UNKNOWN_TIN


def test_a_walk_in_sale_needs_neither(client: httpx.Client) -> None:
    """No TIN, no purchase code — which is most retail sales, and must not be refused."""
    body = client.post(
        "/trnsSales/saveSales", json=sale(custTin=None, custNm="Walk-in", prcOrdCd=None)
    ).json()

    assert body["resultCd"] == codes.RESULT_OK


def test_a_header_bucket_that_disagrees_with_its_items_is_refused(client: httpx.Client) -> None:
    """The single most likely defect in a payload map, and the reason the sandbox is strict:
    a header computed independently of the lines can disagree with them, and RRA notices."""
    body = client.post("/trnsSales/saveSales", json=sale(taxblAmtB="11000.00")).json()

    assert body["resultCd"] == "802"


def test_a_line_whose_tax_is_not_the_inclusive_rate_is_refused(client: httpx.Client) -> None:
    """`taxAmt == taxblAmt x r/(100+r)`. This is what catches a line that was taxed
    exclusively and reported inclusively."""
    broken = sale()
    broken["itemList"] = [{**broken["itemList"][0], "taxAmt": "2124.00"}]
    broken["taxAmtB"] = "2124.00"
    broken["totTaxAmt"] = "2124.00"

    assert client.post("/trnsSales/saveSales", json=broken).json()["resultCd"] == "804"


def test_the_totals_must_equal_the_sum_of_the_buckets(client: httpx.Client) -> None:
    assert client.post("/trnsSales/saveSales", json=sale(totTaxblAmt="99.00")).json()[
        "resultCd"
    ] == "803"


# --- The mode switch ----------------------------------------------------------------------


def test_down_and_timeout_are_distinguishable(client: httpx.Client) -> None:
    """The adapter treats them differently — `down` is safe to retry, `timeout` is not — so
    the sandbox has to be able to be one and not the other."""
    client.post("/_sandbox/mode", json={"mode": "down"})
    assert client.post("/trnsSales/saveSales", json=sale()).status_code == 503

    client.post("/_sandbox/mode", json={"mode": "timeout"})
    assert client.post("/trnsSales/saveSales", json=sale()).status_code == 504


def test_accept_then_timeout_registers_the_sale_and_loses_the_answer(
    client: httpx.Client,
) -> None:
    """The state `unknown` exists for, and the only honest way to test it.

    After this call RRA *is* holding the sale. The proof is that the sandbox's own counter has
    moved and a resend of the same invoice number comes back a duplicate.
    """
    client.post("/_sandbox/mode", json={"mode": "accept_then_timeout"})

    assert client.post("/trnsSales/saveSales", json=sale()).status_code == 504

    client.post("/_sandbox/mode", json={"mode": "up"})
    ledger = client.get("/_sandbox/ledger").json()["999000099:00"]
    assert ledger["last_sale_invc_no"] == 1
    assert client.post("/trnsSales/saveSales", json=sale()).json()["resultCd"] == (
        codes.RESULT_DUPLICATE
    )


def test_reject_carries_the_code_it_was_given(client: httpx.Client) -> None:
    client.post("/_sandbox/mode", json={"mode": "reject:884"})

    assert client.post("/trnsSales/saveSales", json=sale()).json()["resultCd"] == "884"


def test_the_ledger_exposes_the_receipt_it_would_have_returned(client: httpx.Client) -> None:
    """How "attach a receipt manually" is exercised: the operator's real source is the MyRRA
    portal, and this is the portal's stand-in."""
    client.post("/trnsSales/saveSales", json=sale())

    held = client.get("/_sandbox/ledger").json()["999000099:00"]["sales"]["1"]

    assert held["sdcId"] == SANDBOX_SDC_ID
    assert held["rcptNo"] == 1


# --- Both profiles ------------------------------------------------------------------------


def test_the_osdc_paths_answer_the_same_way(client: httpx.Client) -> None:
    """The route table proven rather than asserted: the same payload on the OSDC path gets the
    same treatment, which is what lets the acceptance tape run twice."""
    body = client.post("/saveTrnsSalesOsdc", json=sale()).json()

    assert body["resultCd"] == codes.RESULT_OK
    assert body["data"]["curRcptNo"] == 1


def test_a_tin_lookup_answers_for_a_known_taxpayer_and_refuses_an_unknown_one(
    client: httpx.Client,
) -> None:
    known = client.post(
        "/customers/selectCustomer",
        json={"tin": "999000099", "bhfId": "00", "custmTin": "100000001"},
    ).json()
    unknown = client.post(
        "/selectCustomer", json={"tin": "999000099", "bhfId": "00", "custmTin": "123456789"}
    ).json()

    assert known["data"]["custList"][0]["taxprNm"] == "Customer C Ltd"
    assert unknown["resultCd"] == codes.RESULT_UNKNOWN_TIN
