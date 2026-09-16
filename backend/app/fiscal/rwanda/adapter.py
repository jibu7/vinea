"""`RwandaEbmAdapter` — the one place that talks to RRA.

Transport is `httpx` with a 30 s connect / 60 s read timeout, and **every call is logged with
the device, the path, the `resultCd` and the elapsed time — never the body, never a key.** A
sale payload contains a customer's TIN and what they bought; a response contains a receipt
signature; the device holds three secrets. None of that belongs in a log file that a support
engineer greps, so the log line carries what an operator actually needs when a queue is stuck
and nothing else.

Two profiles, one class (decision 1): `routes.py` says which path, and `cmcKey` is added on the
`osdc` profile alone. `normalize_receipt()` collapses the two sales-response shapes into one
`FiscalReceiptData`, which is the whole reason the difference stops here.

**A refusal is not an exception.** RRA answering `884` is an outcome the outbox records and
decides about; only a request that got no answer raises, and the two kinds of no-answer are
kept apart with some care — `FiscalTransportError` never reached RRA and is safe to retry,
`FiscalTimeout` may have, and is not.
"""

import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.fiscal.mapping import (
    FiscalCodeEntry,
    FiscalItemClassEntry,
    FiscalItemRegistration,
    FiscalPurchase,
    FiscalRefund,
    FiscalSale,
    FiscalStockIO,
    FiscalStockMaster,
    FiscalSyncResult,
)
from app.fiscal.protocol import (
    DeviceIdentity,
    FiscalReceiptData,
    FiscalResult,
    FiscalTimeout,
    FiscalTransportError,
    TinLookup,
)
from app.fiscal.rwanda import builders, codes
from app.fiscal.rwanda.payloads import (
    CodeListData,
    CodeListRequest,
    CustomerData,
    CustomerRequest,
    ImportItemsData,
    ImportItemsRequest,
    InitData,
    InitRequest,
    ItemClassData,
    ItemClassRequest,
    PurchaseFeedData,
    PurchaseFeedRequest,
    ResultEnvelope,
    SalesResponseData,
    UpdateImportItemRequest,
)
from app.fiscal.rwanda.routes import Operation, routes_for
from app.models.fiscalization import FiscalDevice

logger = logging.getLogger("app.fiscal.rwanda")

CONNECT_TIMEOUT_SECONDS = 30.0
READ_TIMEOUT_SECONDS = 60.0

#: The documents' "give me everything" watermark, used the first time a device syncs.
EPOCH_WATERMARK = "20180520000000"

#: The three key fields, by every name they appear under. Stripped from anything stored or
#: logged — `sgnKey` is the initialization response's spelling and `signKey` the column's, and
#: a set that knew only one of them would leak the other.
SECRET_FIELDS = frozenset({"cmcKey", "intrlKey", "sgnKey", "signKey", "cmc_key",
                           "intrl_key", "sign_key"})


def redact(value: Any) -> Any:
    """The same payload with every key field replaced by a marker.

    Applied to everything written to `fiscal_outbox.payload`, `fiscal_receipts.request` and
    any response stored beside them. Recursive, because the keys arrive nested under `data.info`
    and a top-level pass would have missed them — which is the sort of near-miss the redaction
    test walks every stored row to catch.
    """
    if isinstance(value, dict):
        return {
            key: "***" if key in SECRET_FIELDS else redact(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class RwandaEbmAdapter:
    """One device's conversation with RRA.

    A `client` can be injected, which is how the in-process sandbox is mounted in tests
    (`httpx.ASGITransport`) — the adapter under test is then the real one, not a double.
    """

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self._client = client

    # --- Transport -------------------------------------------------------------------------

    def _post(
        self, device: FiscalDevice, operation: Operation, body: dict[str, Any]
    ) -> tuple[ResultEnvelope, int]:
        """One call. Returns the envelope and the elapsed milliseconds.

        Raises only for transport: `FiscalTimeout` when the request left and nothing came
        back, `FiscalTransportError` when it never left or the connection broke. Everything
        RRA actually said — including every refusal — comes back as an envelope.
        """
        path = routes_for(device.profile).path(operation)
        url = f"{device.base_url.rstrip('/')}{path}"
        started = time.monotonic()
        try:
            response = self._request(url, body)
            response.raise_for_status()
            envelope = ResultEnvelope.model_validate(response.json())
        except httpx.TimeoutException as timeout:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            self._log(device, path, "timeout", elapsed_ms)
            raise FiscalTimeout(f"{path} timed out after {elapsed_ms} ms") from timeout
        except (httpx.HTTPError, ValueError) as failure:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            self._log(device, path, "transport", elapsed_ms)
            # `str(failure)` and not the response body: an HTML error page from a proxy is
            # not information, and a JSON body might be a payload.
            raise FiscalTransportError(f"{path} failed: {type(failure).__name__}") from failure
        elapsed_ms = int((time.monotonic() - started) * 1000)
        self._log(device, path, envelope.resultCd, elapsed_ms)
        return envelope, elapsed_ms

    def _request(self, url: str, body: dict[str, Any]) -> httpx.Response:
        timeout = httpx.Timeout(READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
        if self._client is not None:
            return self._client.post(url, json=body, timeout=timeout)
        with httpx.Client(timeout=timeout) as client:
            return client.post(url, json=body)

    def _log(self, device: FiscalDevice, path: str, result_cd: str, elapsed_ms: int) -> None:
        """Device, path, result code, elapsed. **No body and no key**, ever — what is here is
        what somebody debugging a stuck queue needs, and a payload in a log file is a
        customer's TIN and shopping list in a log file."""
        logger.info(
            "ebm call device=%s bhf=%s path=%s result=%s elapsed_ms=%s",
            device.id,
            device.bhf_id,
            path,
            result_cd,
            elapsed_ms,
        )

    def _envelope_result(self, envelope: ResultEnvelope, elapsed_ms: int) -> FiscalResult:
        return FiscalResult(
            ok=envelope.ok,
            code=envelope.resultCd,
            message=envelope.resultMsg or "",
            data=redact(envelope.data or {}),
            elapsed_ms=elapsed_ms,
        )

    def _body(self, device: FiscalDevice, request: Any, *, cmc_key: str | None = None) -> dict:
        """A request as JSON, with `cmcKey` added on the `osdc` profile alone.

        The key is passed in rather than read off the device, because the device column holds
        *ciphertext*: only the caller that decrypted it can supply it, and an adapter that
        could decrypt would be a second place secrets live.
        """
        body = request.model_dump(mode="json", exclude_none=True)
        if routes_for(device.profile).carries_cmc_key and cmc_key:
            body["cmcKey"] = cmc_key
        else:
            body.pop("cmcKey", None)
        return body

    # --- Setup -----------------------------------------------------------------------------

    def initialize_device(
        self, device: FiscalDevice, *, cmc_key: str | None = None
    ) -> tuple[FiscalResult, DeviceIdentity | None]:
        request = InitRequest(
            tin=device.tin or "", bhfId=device.bhf_id, dvcSrlNo=device.dvc_srl_no
        )
        envelope, elapsed_ms = self._post(
            device, Operation.INITIALIZE, self._body(device, request, cmc_key=cmc_key)
        )
        result = self._envelope_result(envelope, elapsed_ms)
        if not envelope.ok or not envelope.data:
            return result, None
        info = InitData.model_validate(envelope.data).info
        identity = DeviceIdentity(
            sdc_id=info.sdcId or "",
            mrc_no=info.mrcNo,
            dvc_id=info.dvcId,
            cmc_key=info.cmcKey,
            intrl_key=info.intrlKey,
            sign_key=info.sgnKey,
            last_sale_invc_no=info.lastSaleInvcNo,
            last_purchase_invc_no=info.lastPchsInvcNo,
            last_sale_rcpt_no=info.lastSaleRcptNo,
        )
        return result, identity

    def sync_codes(
        self, device: FiscalDevice, *, since: str | None, cmc_key: str | None = None
    ) -> tuple[FiscalResult, FiscalSyncResult]:
        request = CodeListRequest(
            tin=device.tin or "", bhfId=device.bhf_id, lastReqDt=since or EPOCH_WATERMARK
        )
        envelope, elapsed_ms = self._post(
            device, Operation.SELECT_CODES, self._body(device, request, cmc_key=cmc_key)
        )
        result = self._envelope_result(envelope, elapsed_ms)
        if not envelope.ok or not envelope.data:
            return result, FiscalSyncResult()
        parsed = CodeListData.model_validate(envelope.data)
        entries = tuple(
            FiscalCodeEntry(
                code_class=code_class.cdCls,
                code_class_name=code_class.cdClsNm,
                code=detail.cd,
                name=detail.cdNm,
                description=detail.cdDesc,
                sort_order=detail.srtOrd,
                user_defined=(detail.userDfnCd1, detail.userDfnCd2, detail.userDfnCd3),
                active=detail.useYn == "Y",
            )
            for code_class in parsed.clsList
            for detail in code_class.dtlList
        )
        return result, FiscalSyncResult(codes=entries, watermark=_now_watermark())

    def sync_item_classes(
        self, device: FiscalDevice, *, since: str | None, cmc_key: str | None = None
    ) -> tuple[FiscalResult, FiscalSyncResult]:
        request = ItemClassRequest(
            tin=device.tin or "", bhfId=device.bhf_id, lastReqDt=since or EPOCH_WATERMARK
        )
        envelope, elapsed_ms = self._post(
            device, Operation.SELECT_ITEM_CLASSES, self._body(device, request, cmc_key=cmc_key)
        )
        result = self._envelope_result(envelope, elapsed_ms)
        if not envelope.ok or not envelope.data:
            return result, FiscalSyncResult()
        parsed = ItemClassData.model_validate(envelope.data)
        entries = tuple(
            FiscalItemClassEntry(
                code=row.itemClsCd,
                name=row.itemClsNm,
                level=row.itemClsLvl,
                tax_class=row.taxTyCd,
                major_target=None if row.mjrTgYn is None else row.mjrTgYn == "Y",
                active=row.useYn == "Y",
            )
            for row in parsed.itemClsList
        )
        return result, FiscalSyncResult(item_classes=entries, watermark=_now_watermark())

    def lookup_tin(
        self, device: FiscalDevice, tin: str, *, cmc_key: str | None = None
    ) -> tuple[FiscalResult, TinLookup]:
        request = CustomerRequest(tin=device.tin or "", bhfId=device.bhf_id, custmTin=tin)
        envelope, elapsed_ms = self._post(
            device, Operation.SELECT_CUSTOMER, self._body(device, request, cmc_key=cmc_key)
        )
        result = self._envelope_result(envelope, elapsed_ms)
        if not envelope.ok or not envelope.data:
            return result, TinLookup(tin=tin, found=False)
        customers = CustomerData.model_validate(envelope.data).custList
        if not customers:
            return result, TinLookup(tin=tin, found=False)
        found = customers[0]
        return result, TinLookup(
            tin=found.tin or tin, found=True, name=found.taxprNm, status=found.taxprSttsCd
        )

    # --- Masters ---------------------------------------------------------------------------

    def register_item(
        self, device: FiscalDevice, item: FiscalItemRegistration, *, cmc_key: str | None = None
    ) -> FiscalResult:
        request = builders.build_item_request(device, item)
        envelope, elapsed_ms = self._post(
            device, Operation.SAVE_ITEM, self._body(device, request, cmc_key=cmc_key)
        )
        return self._envelope_result(envelope, elapsed_ms)

    # --- Sales -----------------------------------------------------------------------------

    def fiscalize_sale(
        self, device: FiscalDevice, sale: FiscalSale, *, cmc_key: str | None = None
    ) -> tuple[FiscalResult, FiscalReceiptData | None]:
        request = builders.build_sale_request(device, sale)
        envelope, elapsed_ms = self._post(
            device, Operation.SAVE_SALES, self._body(device, request, cmc_key=cmc_key)
        )
        result = self._envelope_result(envelope, elapsed_ms)
        return result, self.normalize_receipt(device, envelope, invc_no=sale.invoice_no)

    def fiscalize_refund(
        self, device: FiscalDevice, refund: FiscalRefund, *, cmc_key: str | None = None
    ) -> tuple[FiscalResult, FiscalReceiptData | None]:
        request = builders.build_refund_request(device, refund)
        envelope, elapsed_ms = self._post(
            device, Operation.SAVE_SALES, self._body(device, request, cmc_key=cmc_key)
        )
        result = self._envelope_result(envelope, elapsed_ms)
        return result, self.normalize_receipt(device, envelope, invc_no=refund.invoice_no)

    def normalize_receipt(
        self, device: FiscalDevice, envelope: ResultEnvelope, *, invc_no: int | None = None
    ) -> FiscalReceiptData | None:
        """One `FiscalReceiptData` from either profile's sales response.

        The two documents name the same six facts differently — `rcptNo`/`curRcptNo`,
        `vsdcRcptPbctDate`/`sdcDateTime` — and this is where that stops. `sdcId` and `mrcNo`
        fall back to the device's own, because the OSDC response does not repeat what
        initialization already established.
        """
        if not envelope.ok or not envelope.data:
            return None
        data = SalesResponseData.model_validate(envelope.data)
        rcpt_no = data.rcptNo if data.rcptNo is not None else data.curRcptNo
        stamped = data.vsdcRcptPbctDate or data.sdcDateTime
        if rcpt_no is None or data.totRcptNo is None or stamped is None:
            # A `000` with no receipt in it is not a receipt. Returning None rather than a
            # part-filled row keeps "sent means signed" true, and the outbox treats a sent row
            # without a receipt as an invariant failure rather than as a normal state.
            return None
        return FiscalReceiptData(
            rcpt_no=rcpt_no,
            tot_rcpt_no=data.totRcptNo,
            intrl_data=data.intrlData or "",
            rcpt_sign=data.rcptSign or "",
            sdc_id=data.sdcId or device.sdc_id or "",
            sdc_datetime=stamped,
            mrc_no=data.mrcNo or device.mrc_no,
            invc_no=invc_no,
        )

    # --- Purchases -------------------------------------------------------------------------

    def register_purchase(
        self, device: FiscalDevice, purchase: FiscalPurchase, *, cmc_key: str | None = None
    ) -> FiscalResult:
        request = builders.build_purchase_request(device, purchase)
        envelope, elapsed_ms = self._post(
            device, Operation.SAVE_PURCHASES, self._body(device, request, cmc_key=cmc_key)
        )
        return self._envelope_result(envelope, elapsed_ms)

    def confirm_purchase(
        self, device: FiscalDevice, purchase: FiscalPurchase, *, cmc_key: str | None = None
    ) -> FiscalResult:
        """The same endpoint as `register_purchase` — what differs is `regTyCd` and whose
        figures are being sent, which `builders` reads off `FiscalPurchase.confirming`."""
        return self.register_purchase(device, purchase, cmc_key=cmc_key)

    def fetch_purchase_feed(
        self, device: FiscalDevice, *, since: str | None, cmc_key: str | None = None
    ) -> tuple[FiscalResult, FiscalSyncResult]:
        request = PurchaseFeedRequest(
            tin=device.tin or "", bhfId=device.bhf_id, lastReqDt=since or EPOCH_WATERMARK
        )
        envelope, elapsed_ms = self._post(
            device, Operation.SELECT_PURCHASES, self._body(device, request, cmc_key=cmc_key)
        )
        result = self._envelope_result(envelope, elapsed_ms)
        if not envelope.ok or not envelope.data:
            return result, FiscalSyncResult()
        parsed = PurchaseFeedData.model_validate(envelope.data)
        rows = tuple(sale.model_dump(mode="json") for sale in parsed.saleList)
        return result, FiscalSyncResult(rows=rows, watermark=_now_watermark())

    # --- Stock -----------------------------------------------------------------------------

    def report_stock_io(
        self, device: FiscalDevice, movement: FiscalStockIO, *, cmc_key: str | None = None
    ) -> FiscalResult:
        request = builders.build_stock_io_request(device, movement)
        envelope, elapsed_ms = self._post(
            device, Operation.SAVE_STOCK_ITEMS, self._body(device, request, cmc_key=cmc_key)
        )
        return self._envelope_result(envelope, elapsed_ms)

    def report_stock_master(
        self,
        device: FiscalDevice,
        master: FiscalStockMaster | tuple[FiscalStockMaster, ...],
        *,
        cmc_key: str | None = None,
    ) -> FiscalResult:
        masters = (master,) if isinstance(master, FiscalStockMaster) else master
        request = builders.build_stock_master_request(device, masters)
        envelope, elapsed_ms = self._post(
            device, Operation.SAVE_STOCK_MASTER, self._body(device, request, cmc_key=cmc_key)
        )
        return self._envelope_result(envelope, elapsed_ms)

    # --- Imports ---------------------------------------------------------------------------

    def fetch_imports(
        self, device: FiscalDevice, *, since: str | None, cmc_key: str | None = None
    ) -> tuple[FiscalResult, FiscalSyncResult]:
        request = ImportItemsRequest(
            tin=device.tin or "", bhfId=device.bhf_id, lastReqDt=since or EPOCH_WATERMARK
        )
        envelope, elapsed_ms = self._post(
            device, Operation.SELECT_IMPORT_ITEMS, self._body(device, request, cmc_key=cmc_key)
        )
        result = self._envelope_result(envelope, elapsed_ms)
        if not envelope.ok or not envelope.data:
            return result, FiscalSyncResult()
        parsed = ImportItemsData.model_validate(envelope.data)
        rows = tuple(item.model_dump(mode="json") for item in parsed.itemList)
        return result, FiscalSyncResult(rows=rows, watermark=_now_watermark())

    def update_import(
        self,
        device: FiscalDevice,
        *,
        declaration: dict[str, Any],
        cmc_key: str | None = None,
    ) -> FiscalResult:
        request = UpdateImportItemRequest(
            tin=device.tin or "", bhfId=device.bhf_id, **declaration
        )
        envelope, elapsed_ms = self._post(
            device, Operation.UPDATE_IMPORT_ITEMS, self._body(device, request, cmc_key=cmc_key)
        )
        return self._envelope_result(envelope, elapsed_ms)


def _now_watermark() -> str:
    """`yyyyMMddHHmmss`, which is the only date format the documents use.

    Taken from the client clock rather than from `resultDt`, and that is a deliberate
    trade-off: a watermark slightly *behind* the server re-fetches a few rows, which the
    upserts absorb, while one slightly ahead skips rows forever.
    """
    return datetime.now(UTC).strftime("%Y%m%d%H%M%S")


__all__ = ["RwandaEbmAdapter", "codes", "redact"]
