"""An in-repo EBM server (decision 16).

**Why this exists rather than a mock.** The thing that has to work is the whole path — the
adapter's payload, the route table, the response shapes, the retry policy, the receipt
counters — and a mock that returns a canned dict proves none of it. This answers as RRA does:
it validates the header buckets against the items, checks `taxAmt` against
`taxblAmt × r / (100 + r)`, keeps per-type and total receipt counters, refuses a duplicate
`invcNo` with `994`, a business sale without a purchase code with `881` and an unknown TIN with
`884`. A payload this accepts is a payload with a real chance of passing certification; one it
refuses is one that would have come back `881` from Kigali.

It implements **both route profiles**, so the tape can be run twice and the route table is
proven rather than asserted.

`/_sandbox/mode` switches it between `up`, `down`, `timeout`, `accept_then_timeout` and
`reject:<code>` so a test can drive the outbox through every branch of its retry policy, and
`/_sandbox/ledger` exposes what it holds so a test can read the receipt it *would* have
returned — which is how "attach a receipt manually" is exercised without a person reading a
portal.

**It is refused outside development and test.** `Settings.fiscal_sandbox_enabled` gates it and
the production validator refuses to boot with it set, the same shape as the mail sink: an app
that answers `saveSales` itself hands back a receipt nobody filed, and finding that out on a
VAT return is too late.
"""

import base64
import enum
import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse

from app.fiscal.rwanda import codes
from app.fiscal.rwanda.routes import ROUTES, Operation

#: What initialization hands back. Fixed values, because the acceptance tape asserts them as
#: literals and a random `sdcId` would make the tape unwritable.
SANDBOX_SDC_ID = "SDC010000005"
SANDBOX_MRC_NO = "WIS01006230"
SANDBOX_DVC_ID = "1"
SANDBOX_CMC_KEY = "sandbox-cmc-key"
SANDBOX_INTRL_KEY = "sandbox-intrl-key"
SANDBOX_SIGN_KEY = "sandbox-sign-key"

INTERNAL_DATA_LENGTH = 26
RECEIPT_SIGN_LENGTH = 16
HUNDRED = Decimal(100)
PENNY = Decimal("0.01")


class SandboxMode(enum.StrEnum):
    """How the sandbox behaves on the next call.

    `ACCEPT_THEN_TIMEOUT` is the one that matters most: it registers the sale and *then* fails
    to answer, which is the state the whole `unknown` path exists for. Without it that path
    could only be tested by pretending, and "RRA is holding a sale we have no receipt for" is
    exactly the situation nobody should be pretending about.
    """

    UP = "up"
    DOWN = "down"
    TIMEOUT = "timeout"
    ACCEPT_THEN_TIMEOUT = "accept_then_timeout"
    REJECT = "reject"


@dataclass
class DeviceLedger:
    """One device's state: its counters and what it has accepted."""

    last_sale_invc_no: int = 0
    last_purchase_invc_no: int = 0
    receipt_counters: dict[str, int] = field(default_factory=dict)
    total_receipt_no: int = 0
    sales: dict[int, dict[str, Any]] = field(default_factory=dict)
    purchases: dict[int, dict[str, Any]] = field(default_factory=dict)
    stock_movements: list[dict[str, Any]] = field(default_factory=list)
    stock_masters: list[dict[str, Any]] = field(default_factory=list)
    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    import_updates: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SandboxState:
    mode: SandboxMode = SandboxMode.UP
    reject_code: str = codes.RESULT_TEMPORARY
    devices: dict[str, DeviceLedger] = field(default_factory=dict)

    def ledger(self, tin: str, bhf_id: str) -> DeviceLedger:
        return self.devices.setdefault(f"{tin}:{bhf_id}", DeviceLedger())

    def reset(self) -> None:
        self.mode = SandboxMode.UP
        self.reject_code = codes.RESULT_TEMPORARY
        self.devices.clear()


#: TINs the sandbox recognises. An unknown one comes back `884`, which is what makes "Verify
#: TIN" worth having on the customer screen at all.
KNOWN_TAXPAYERS: dict[str, str] = {
    "999000099": "Rugari Wines Ltd",
    "100000001": "Customer C Ltd",
    "100000002": "Supplier S1 Ltd",
    "100000003": "Feed Supplier Ltd",
}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y%m%d%H%M%S")


def _envelope(
    result_cd: str, message: str, data: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "resultCd": result_cd,
        "resultMsg": message,
        "resultDt": _now(),
        "data": data,
    }


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


def _signature(sign_key: str, payload: dict[str, Any], length: int, salt: str) -> str:
    """Deterministic, and derived from the payload — so a test can recompute it, and two
    different sales never carry the same signature."""
    material = f"{salt}:{payload.get('invcNo')}:{payload.get('totAmt')}:{payload.get('cfmDt')}"
    digest = hmac.new(sign_key.encode(), material.encode(), hashlib.sha256).digest()
    return base64.b32encode(digest).decode().rstrip("=")[:length]


def _validate_sale(payload: dict[str, Any]) -> tuple[str, str] | None:
    """The checks RRA makes. Returns `(code, message)` on a refusal, `None` when it passes.

    Each one is a refusal a real device makes, and each is here because a payload that fails it
    in Kigali should fail in CI first.
    """
    items = payload.get("itemList") or []
    if not items:
        return "801", "a sale must carry at least one item"

    # 1. The header buckets are Σ of the items, by class. A payload that disagrees with itself
    # is the single most likely defect in a mapping, so it is the first thing checked.
    for tax_class in ("A", "B", "C", "D"):
        expected_taxable = sum(
            (_decimal(item.get("taxblAmt")) for item in items
             if item.get("taxTyCd") == tax_class),
            Decimal(0),
        )
        expected_tax = sum(
            (_decimal(item.get("taxAmt")) for item in items if item.get("taxTyCd") == tax_class),
            Decimal(0),
        )
        if _decimal(payload.get(f"taxblAmt{tax_class}")) != expected_taxable:
            return "802", f"taxblAmt{tax_class} does not equal the sum of its items"
        if _decimal(payload.get(f"taxAmt{tax_class}")) != expected_tax:
            return "802", f"taxAmt{tax_class} does not equal the sum of its items"

    # 2. The totals are Σ of the buckets.
    total_taxable = sum(
        (_decimal(payload.get(f"taxblAmt{c}")) for c in "ABCD"), Decimal(0)
    )
    total_tax = sum((_decimal(payload.get(f"taxAmt{c}")) for c in "ABCD"), Decimal(0))
    if _decimal(payload.get("totTaxblAmt")) != total_taxable:
        return "803", "totTaxblAmt does not equal the sum of the buckets"
    if _decimal(payload.get("totTaxAmt")) != total_tax:
        return "803", "totTaxAmt does not equal the sum of the buckets"

    # 3. Each line's tax is its taxable amount at the programmed rate, VAT-inclusive.
    for item in items:
        rate = Decimal(codes.PROGRAMMED_RATES.get(item.get("taxTyCd", "D"), "0"))
        taxable = _decimal(item.get("taxblAmt"))
        expected = (taxable * rate / (HUNDRED + rate)).quantize(
            PENNY, rounding=ROUND_HALF_UP
        )
        if _decimal(item.get("taxAmt")) != expected:
            return (
                "804",
                f"item {item.get('itemSeq')}: taxAmt {item.get('taxAmt')} is not "
                f"taxblAmt x r/(100+r) = {expected}",
            )

    # 4. A business customer needs a purchase code, and its TIN has to exist.
    customer_tin = payload.get("custTin")
    if customer_tin:
        if customer_tin not in KNOWN_TAXPAYERS:
            return codes.RESULT_UNKNOWN_TIN, f"unknown TIN {customer_tin}"
        if not payload.get("prcOrdCd"):
            return (
                codes.RESULT_PURCHASE_CODE_REQUIRED,
                "a purchase code is required for a business customer",
            )
    return None


def create_sandbox_router(state: SandboxState) -> APIRouter:
    """Both profiles' paths, wired to one set of handlers.

    Registered from `ROUTES` rather than by hand: the route table is the thing under test, and
    a sandbox that listed its own paths could answer on a path the adapter never calls.
    """
    router = APIRouter()

    def _guard() -> JSONResponse | None:
        """The mode switch, applied before anything is read."""
        if state.mode == SandboxMode.DOWN:
            return JSONResponse(status_code=503, content={"detail": "sandbox is down"})
        if state.mode == SandboxMode.TIMEOUT:
            # A timeout is modelled as a 504 rather than by sleeping: the adapter maps a read
            # timeout and a gateway timeout to the same `FiscalTimeout`, and a test that waits
            # 60 seconds to prove it is a test nobody runs.
            return JSONResponse(status_code=504, content={"detail": "sandbox timed out"})
        if state.mode == SandboxMode.REJECT:
            return JSONResponse(
                content=_envelope(state.reject_code, f"refused with {state.reject_code}")
            )
        return None

    def initialize(payload: dict[str, Any]) -> Any:
        ledger = state.ledger(payload.get("tin", ""), payload.get("bhfId", ""))
        return _envelope(
            codes.RESULT_OK,
            "It is succeeded",
            {
                "info": {
                    "tin": payload.get("tin"),
                    "taxprNm": KNOWN_TAXPAYERS.get(payload.get("tin", ""), "Sandbox Taxpayer"),
                    "bhfId": payload.get("bhfId"),
                    "bhfNm": "Head Office",
                    "sdcId": SANDBOX_SDC_ID,
                    "mrcNo": SANDBOX_MRC_NO,
                    "dvcId": SANDBOX_DVC_ID,
                    "cmcKey": SANDBOX_CMC_KEY,
                    "intrlKey": SANDBOX_INTRL_KEY,
                    "sgnKey": SANDBOX_SIGN_KEY,
                    "lastSaleInvcNo": ledger.last_sale_invc_no,
                    "lastPchsInvcNo": ledger.last_purchase_invc_no,
                    "lastSaleRcptNo": ledger.receipt_counters.get(
                        codes.SalesReceiptType.SALE, 0
                    ),
                }
            },
        )

    def select_codes(_: dict[str, Any]) -> Any:
        return _envelope(
            codes.RESULT_OK,
            "It is succeeded",
            {
                "clsList": [
                    {
                        "cdCls": codes.CLASS_TAX_TYPE,
                        "cdClsNm": "Taxation Type",
                        "useYn": "Y",
                        "dtlList": [
                            {
                                "cd": tax_class.value,
                                "cdNm": name,
                                "useYn": "Y",
                                "srtOrd": index,
                                "userDfnCd1": codes.PROGRAMMED_RATES[tax_class.value],
                            }
                            for index, (tax_class, name) in enumerate(
                                (
                                    (codes.TaxType.EXEMPT, "A-EX"),
                                    (codes.TaxType.STANDARD, "B-18.00%"),
                                    (codes.TaxType.ZERO_RATED, "C-0.00%"),
                                    (codes.TaxType.NON_VAT, "D-0.00%"),
                                ),
                                start=1,
                            )
                        ],
                    },
                    {
                        "cdCls": codes.CLASS_PACKAGING_UNIT,
                        "cdClsNm": "Packing Unit",
                        "useYn": "Y",
                        "dtlList": [
                            {"cd": "NT", "cdNm": "Net", "useYn": "Y", "srtOrd": 1},
                            {"cd": "BX", "cdNm": "Box", "useYn": "Y", "srtOrd": 2},
                        ],
                    },
                    {
                        "cdCls": codes.CLASS_QUANTITY_UNIT,
                        "cdClsNm": "Quantity Unit",
                        "useYn": "Y",
                        "dtlList": [
                            {"cd": "U", "cdNm": "Piece", "useYn": "Y", "srtOrd": 1},
                            {"cd": "KG", "cdNm": "Kilogram", "useYn": "Y", "srtOrd": 2},
                            {"cd": "L", "cdNm": "Litre", "useYn": "Y", "srtOrd": 3},
                        ],
                    },
                    {
                        "cdCls": codes.CLASS_PAYMENT_TYPE,
                        "cdClsNm": "Payment Type",
                        "useYn": "Y",
                        "dtlList": [
                            {"cd": "01", "cdNm": "CASH", "useYn": "Y", "srtOrd": 1},
                            {"cd": "02", "cdNm": "CREDIT", "useYn": "Y", "srtOrd": 2},
                            {"cd": "06", "cdNm": "MOBILE MONEY", "useYn": "Y", "srtOrd": 6},
                        ],
                    },
                ]
            },
        )

    def select_item_classes(_: dict[str, Any]) -> Any:
        return _envelope(
            codes.RESULT_OK,
            "It is succeeded",
            {
                "itemClsList": [
                    {
                        "itemClsCd": "5059020800",
                        "itemClsNm": "Wine of fresh grapes",
                        "itemClsLvl": 5,
                        "taxTyCd": "B",
                        "mjrTgYn": "N",
                        "useYn": "Y",
                    },
                    {
                        "itemClsCd": "8514900000",
                        "itemClsNm": "General services",
                        "itemClsLvl": 5,
                        "taxTyCd": "B",
                        "mjrTgYn": "N",
                        "useYn": "Y",
                    },
                    {
                        "itemClsCd": "1001910000",
                        "itemClsNm": "Cereal seed",
                        "itemClsLvl": 5,
                        "taxTyCd": "A",
                        "mjrTgYn": "N",
                        "useYn": "Y",
                    },
                ]
            },
        )

    def select_customer(payload: dict[str, Any]) -> Any:
        tin = payload.get("custmTin", "")
        name = KNOWN_TAXPAYERS.get(tin)
        if name is None:
            return _envelope(codes.RESULT_UNKNOWN_TIN, f"unknown TIN {tin}")
        return _envelope(
            codes.RESULT_OK,
            "It is succeeded",
            {"custList": [{"tin": tin, "taxprNm": name, "taxprSttsCd": "01"}]},
        )

    def save_item(payload: dict[str, Any]) -> Any:
        ledger = state.ledger(payload.get("tin", ""), payload.get("bhfId", ""))
        ledger.items[payload["itemCd"]] = payload
        return _envelope(codes.RESULT_OK, "It is succeeded")

    def save_sales(payload: dict[str, Any]) -> Any:
        ledger = state.ledger(payload.get("tin", ""), payload.get("bhfId", ""))
        invoice_no = int(payload.get("invcNo", 0))
        if invoice_no in ledger.sales:
            # A duplicate, and **with no receipt data** — which is the fact the whole `unknown`
            # policy turns on. A blind resend of a sale whose answer was lost succeeds here and
            # leaves the caller with nothing to print.
            return _envelope(codes.RESULT_DUPLICATE, "the invoice number is already registered")
        refusal = _validate_sale(payload)
        if refusal is not None:
            return _envelope(*refusal)

        receipt_type = payload.get("rcptTyCd", codes.SalesReceiptType.SALE)
        ledger.receipt_counters[receipt_type] = ledger.receipt_counters.get(receipt_type, 0) + 1
        ledger.total_receipt_no += 1
        stamped = _now()
        receipt = {
            "rcptNo": ledger.receipt_counters[receipt_type],
            "curRcptNo": ledger.receipt_counters[receipt_type],
            "totRcptNo": ledger.total_receipt_no,
            "intrlData": _signature(
                SANDBOX_INTRL_KEY, payload, INTERNAL_DATA_LENGTH, "intrl"
            ),
            "rcptSign": _signature(SANDBOX_SIGN_KEY, payload, RECEIPT_SIGN_LENGTH, "sign"),
            "vsdcRcptPbctDate": stamped,
            "sdcDateTime": stamped,
            "sdcId": SANDBOX_SDC_ID,
            "mrcNo": SANDBOX_MRC_NO,
            "totTaxblAmt": payload.get("totTaxblAmt"),
            "totTaxAmt": payload.get("totTaxAmt"),
            "totAmt": payload.get("totAmt"),
        }
        ledger.sales[invoice_no] = {"request": payload, "receipt": receipt}
        ledger.last_sale_invc_no = max(ledger.last_sale_invc_no, invoice_no)
        if state.mode == SandboxMode.ACCEPT_THEN_TIMEOUT:
            # Registered, and the answer lost. Exactly the state `unknown` exists for.
            return JSONResponse(status_code=504, content={"detail": "sandbox timed out"})
        return _envelope(codes.RESULT_OK, "It is succeeded", receipt)

    def save_purchases(payload: dict[str, Any]) -> Any:
        ledger = state.ledger(payload.get("tin", ""), payload.get("bhfId", ""))
        invoice_no = int(payload.get("invcNo", 0))
        if invoice_no in ledger.purchases:
            return _envelope(codes.RESULT_DUPLICATE, "the invoice number is already registered")
        ledger.purchases[invoice_no] = payload
        ledger.last_purchase_invc_no = max(ledger.last_purchase_invc_no, invoice_no)
        return _envelope(codes.RESULT_OK, "It is succeeded")

    def select_purchases(payload: dict[str, Any]) -> Any:
        """A fixture feed: one purchase somebody else registered against this taxpayer."""
        return _envelope(
            codes.RESULT_OK,
            "It is succeeded",
            {
                "saleList": [
                    {
                        "spplrTin": "100000003",
                        "spplrBhfId": "00",
                        "spplrNm": KNOWN_TAXPAYERS["100000003"],
                        "spplrInvcNo": 77,
                        "rcptTyCd": "S",
                        "pmtTyCd": "01",
                        "cfmDt": _now(),
                        "salesDt": datetime.now(UTC).strftime("%Y%m%d"),
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
                        "itemList": [
                            {
                                "itemSeq": 1,
                                "itemCd": "RW2NTXU0000009",
                                "itemClsCd": "5059020800",
                                "itemNm": "Supplied goods",
                                "pkgUnitCd": "NT",
                                "pkg": "1.00",
                                "qtyUnitCd": "U",
                                "qty": "1.00",
                                "prc": "11800.00",
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
                ]
            },
        )

    def save_stock_items(payload: dict[str, Any]) -> Any:
        ledger = state.ledger(payload.get("tin", ""), payload.get("bhfId", ""))
        ledger.stock_movements.append(payload)
        return _envelope(codes.RESULT_OK, "It is succeeded")

    def save_stock_master(payload: dict[str, Any]) -> Any:
        ledger = state.ledger(payload.get("tin", ""), payload.get("bhfId", ""))
        ledger.stock_masters.append(payload)
        return _envelope(codes.RESULT_OK, "It is succeeded")

    def select_stock_items(_: dict[str, Any]) -> Any:
        return _envelope(codes.RESULT_OK, "It is succeeded", {"stockList": []})

    def select_import_items(_: dict[str, Any]) -> Any:
        return _envelope(
            codes.RESULT_OK,
            "It is succeeded",
            {
                "itemList": [
                    {
                        "taskCd": "2231990000",
                        "dclDe": datetime.now(UTC).strftime("%Y%m%d"),
                        "itemSeq": 1,
                        "dclNo": "IM 2026 000123",
                        "hsCd": "22042100",
                        "itemNm": "Imported wine",
                        "imptItemSttsCd": "2",
                        "orgnNatCd": "ZA",
                        "exptNatCd": "ZA",
                        "pkg": "20.00",
                        "pkgUnitCd": "BX",
                        "qty": "240.00",
                        "qtyUnitCd": "U",
                        "totWt": "300.00",
                        "netWt": "280.00",
                        "spplrNm": "Cape Vineyards",
                        "agntNm": "Kigali Clearing",
                        "invcFcurAmt": "1200.00",
                        "invcFcurCd": "USD",
                        "invcFcurExcrt": "1320.00",
                    }
                ]
            },
        )

    def update_import_items(payload: dict[str, Any]) -> Any:
        ledger = state.ledger(payload.get("tin", ""), payload.get("bhfId", ""))
        ledger.import_updates.append(payload)
        return _envelope(codes.RESULT_OK, "It is succeeded")

    def select_branches(_: dict[str, Any]) -> Any:
        return _envelope(
            codes.RESULT_OK,
            "It is succeeded",
            {"bhfList": [{"tin": "999000099", "bhfId": "00", "bhfNm": "Head Office"}]},
        )

    def select_notices(_: dict[str, Any]) -> Any:
        return _envelope(codes.RESULT_OK, "It is succeeded", {"noticeList": []})

    handlers = {
        Operation.INITIALIZE: initialize,
        Operation.SELECT_CODES: select_codes,
        Operation.SELECT_ITEM_CLASSES: select_item_classes,
        Operation.SELECT_CUSTOMER: select_customer,
        Operation.SELECT_BRANCHES: select_branches,
        Operation.SELECT_NOTICES: select_notices,
        Operation.SAVE_ITEM: save_item,
        Operation.SELECT_ITEMS: lambda _: _envelope(
            codes.RESULT_OK, "It is succeeded", {"itemList": []}
        ),
        Operation.SELECT_IMPORT_ITEMS: select_import_items,
        Operation.UPDATE_IMPORT_ITEMS: update_import_items,
        Operation.SAVE_SALES: save_sales,
        Operation.SELECT_PURCHASES: select_purchases,
        Operation.SAVE_PURCHASES: save_purchases,
        Operation.SAVE_STOCK_ITEMS: save_stock_items,
        Operation.SELECT_STOCK_ITEMS: select_stock_items,
        Operation.SAVE_STOCK_MASTER: save_stock_master,
    }

    def _register(path: str, operation: Operation) -> None:
        handler = handlers[operation]

        async def endpoint(payload: dict[str, Any]) -> Any:
            refused = _guard()
            if refused is not None:
                return refused
            return handler(payload)

        # One function object per path, named after the operation so the OpenAPI schema and
        # any traceback say which call it was.
        endpoint.__name__ = f"{operation.value}"
        router.post(path)(endpoint)

    registered: set[str] = set()
    for operation, (vsdc_path, osdc_path) in ROUTES.items():
        if operation not in handlers:
            continue
        for path in (vsdc_path, osdc_path):
            if path is None or path in registered:
                continue
            registered.add(path)
            _register(path, operation)

    # --- The control surface ---------------------------------------------------------------

    @router.post("/_sandbox/mode")
    async def set_mode(payload: dict[str, Any]) -> dict[str, Any]:
        """`{"mode": "down"}` or `{"mode": "reject", "code": "884"}`.

        Never routed in production: `Settings.fiscal_sandbox_enabled` gates the whole router
        and the production validator refuses to boot with it set.
        """
        raw = str(payload.get("mode", SandboxMode.UP))
        if raw.startswith("reject:"):
            state.mode = SandboxMode.REJECT
            state.reject_code = raw.split(":", 1)[1]
        else:
            state.mode = SandboxMode(raw)
            if "code" in payload:
                state.reject_code = str(payload["code"])
        return {"mode": state.mode, "code": state.reject_code}

    @router.get("/_sandbox/ledger")
    async def read_ledger() -> dict[str, Any]:
        """What the sandbox holds — so a test can read the receipt it *would* have returned.

        This is how "attach a receipt manually" is exercised: the operator's real source is
        the MyRRA portal, and this is the portal's stand-in.
        """
        return {
            key: {
                "last_sale_invc_no": ledger.last_sale_invc_no,
                "last_purchase_invc_no": ledger.last_purchase_invc_no,
                "receipt_counters": dict(ledger.receipt_counters),
                "total_receipt_no": ledger.total_receipt_no,
                "sales": {str(no): sale["receipt"] for no, sale in ledger.sales.items()},
                "purchases": sorted(ledger.purchases),
                "items": sorted(ledger.items),
                "stock_movements": len(ledger.stock_movements),
                "stock_masters": len(ledger.stock_masters),
                "import_updates": len(ledger.import_updates),
            }
            for key, ledger in state.devices.items()
        }

    @router.post("/_sandbox/reset")
    async def reset() -> dict[str, str]:
        state.reset()
        return {"status": "reset"}

    return router


def create_sandbox_app(state: SandboxState | None = None) -> FastAPI:
    """A standalone app, for `docker-compose.e2e.yml` and for `httpx.ASGITransport` in tests."""
    app = FastAPI(title="Vinea EBM sandbox", docs_url=None, redoc_url=None)
    app.state.sandbox = state or SandboxState()
    app.include_router(create_sandbox_router(app.state.sandbox))
    return app
