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
from app.models.fiscalization import FiscalDevice, FiscalOutboxKind

logger = logging.getLogger("app.fiscal.rwanda")

CONNECT_TIMEOUT_SECONDS = 30.0
READ_TIMEOUT_SECONDS = 60.0

#: The documents' "give me everything" watermark, used the first time a device syncs.
EPOCH_WATERMARK = "20180520000000"

#: HTTP statuses that mean **the answer was lost**, not that the request failed. A proxy or a
#: gateway in front of the VSDC answers one of these when the device took too long, and the
#: request may well have arrived — so the outbox must treat them exactly as it treats a read
#: timeout, and never resend.
GATEWAY_TIMEOUT_STATUSES = frozenset({408, 504})

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


#: Outbox kind → the builder that renders it and the operation that sends it. One table, so a
#: kind cannot be rendered onto one path and sent down another — which is exactly the sort of
#: mistake that only shows up as a `921` from Kigali.
_OUTBOX_OPERATIONS: dict[FiscalOutboxKind, Operation] = {
    FiscalOutboxKind.ITEM: Operation.SAVE_ITEM,
    FiscalOutboxKind.SALE: Operation.SAVE_SALES,
    FiscalOutboxKind.REFUND: Operation.SAVE_SALES,
    FiscalOutboxKind.PURCHASE: Operation.SAVE_PURCHASES,
    FiscalOutboxKind.PURCHASE_CONFIRM: Operation.SAVE_PURCHASES,
    FiscalOutboxKind.STOCK_IO: Operation.SAVE_STOCK_ITEMS,
    FiscalOutboxKind.STOCK_MASTER: Operation.SAVE_STOCK_MASTER,
    FiscalOutboxKind.IMPORT_UPDATE: Operation.UPDATE_IMPORT_ITEMS,
}

#: The kinds whose answer carries a receipt. Everything else comes back with an acknowledgment
#: and nothing to print.
_RECEIPT_KINDS = frozenset({FiscalOutboxKind.SALE, FiscalOutboxKind.REFUND})


class RwandaEbmAdapter:
    """One device's conversation with RRA.

    A `client` can be injected, which is how the in-process sandbox is mounted in tests
    — the adapter under test is then the real one, not a double.
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
        except httpx.HTTPStatusError as refused:
            # **A gateway timeout is a timeout.** `408` and `504` both mean the request
            # reached something and the answer did not come back, which is the one case that
            # must never be retried blindly — RRA may be holding the sale. Any other status is
            # a transport failure the request did not survive, and is safe to retry.
            elapsed_ms = int((time.monotonic() - started) * 1000)
            status_code = refused.response.status_code
            if status_code in GATEWAY_TIMEOUT_STATUSES:
                self._log(device, path, "timeout", elapsed_ms)
                raise FiscalTimeout(
                    f"{path} answered {status_code} after {elapsed_ms} ms"
                ) from refused
            self._log(device, path, f"http{status_code}", elapsed_ms)
            raise FiscalTransportError(f"{path} failed: HTTP {status_code}") from refused
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
        if self._client is not None:
            # An injected client carries its own timeout policy. The in-process sandbox has no
            # network to time out on, and a per-request timeout handed to a test transport is
            # a setting with nothing to apply to.
            return self._client.post(url, json=body)
        timeout = httpx.Timeout(READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
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

    # --- The outbox seam -------------------------------------------------------------------

    def render(
        self, device: FiscalDevice, kind: FiscalOutboxKind, document: Any
    ) -> dict[str, Any]:
        """One queue row's payload, built from the DTO the posting transaction held.

        Redacted on the way out even though nothing here carries a key: `cmcKey` is added at
        `send`, so a rendered payload *should* be clean — and the cheapest way to keep that
        true is to strip it here anyway rather than to rely on it.
        """
        builders_by_kind = {
            FiscalOutboxKind.ITEM: builders.build_item_request,
            FiscalOutboxKind.SALE: builders.build_sale_request,
            FiscalOutboxKind.REFUND: builders.build_refund_request,
            FiscalOutboxKind.PURCHASE: builders.build_purchase_request,
            FiscalOutboxKind.PURCHASE_CONFIRM: builders.build_purchase_request,
            FiscalOutboxKind.STOCK_IO: builders.build_stock_io_request,
            FiscalOutboxKind.STOCK_MASTER: builders.build_stock_master_request,
        }
        build = builders_by_kind.get(kind)
        if build is None:
            raise LookupError(f"{kind} has no payload builder")
        request = build(device, document)
        return redact(request.model_dump(mode="json", exclude_none=True))

    def send(
        self,
        device: FiscalDevice,
        kind: FiscalOutboxKind,
        payload: dict[str, Any],
        *,
        cmc_key: str | None = None,
    ) -> tuple[FiscalResult, FiscalReceiptData | None]:
        """The frozen bytes, down the path this kind belongs on."""
        body = dict(payload)
        if routes_for(device.profile).carries_cmc_key and cmc_key:
            body["cmcKey"] = cmc_key
        else:
            body.pop("cmcKey", None)
        envelope, elapsed_ms = self._post(device, _OUTBOX_OPERATIONS[kind], body)
        result = self._envelope_result(envelope, elapsed_ms)
        if kind not in _RECEIPT_KINDS:
            return result, None
        return result, self.normalize_receipt(
            device, envelope.data if envelope.ok else None, invc_no=payload.get("invcNo")
        )

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

    def mint_item_code(
        self,
        *,
        origin_country: str,
        product_type: str,
        packaging_unit: str,
        quantity_unit: str,
        sequence_no: int,
    ) -> str:
        """§4.17's composition, and the only caller of it outside this package's own tests."""
        return codes.build_item_code(
            origin_country=origin_country,
            product_type=product_type,
            packaging_unit=packaging_unit,
            quantity_unit=quantity_unit,
            sequence_no=sequence_no,
        )

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
        kind = FiscalOutboxKind.SALE
        return self.send(device, kind, self.render(device, kind, sale), cmc_key=cmc_key)

    def fiscalize_refund(
        self, device: FiscalDevice, refund: FiscalRefund, *, cmc_key: str | None = None
    ) -> tuple[FiscalResult, FiscalReceiptData | None]:
        kind = FiscalOutboxKind.REFUND
        return self.send(device, kind, self.render(device, kind, refund), cmc_key=cmc_key)

    def normalize_receipt(
        self,
        device: FiscalDevice,
        response: dict[str, Any] | None,
        *,
        invc_no: int | None = None,
    ) -> FiscalReceiptData | None:
        """One `FiscalReceiptData` from either profile's sales response.

        The two documents name the same six facts differently — `rcptNo`/`curRcptNo`,
        `vsdcRcptPbctDate`/`sdcDateTime` — and this is where that stops. `sdcId` and `mrcNo`
        fall back to the device's own, because the OSDC response does not repeat what
        initialization already established.

        It takes the **response body**, not an envelope, for one reason beyond tidiness: the
        manual "attach a receipt" path on the queue screen has no envelope. An operator reading
        the receipt off the MyRRA portal is keying the same six facts, and they should be
        normalised by the same code — otherwise the manual path is a second, untested opinion
        about what a receipt is.
        """
        if not response:
            return None
        data = SalesResponseData.model_validate(response)
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
            sdc_datetime=parse_stamp(stamped),
            mrc_no=data.mrcNo or device.mrc_no,
            invc_no=invc_no,
            qr_payload=verification_code(
                stamped=stamped,
                sdc_id=data.sdcId or device.sdc_id or "",
                tot_rcpt_no=data.totRcptNo,
                intrl_data=data.intrlData or "",
                rcpt_sign=data.rcptSign or "",
            ),
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
        self, device: FiscalDevice, master: FiscalStockMaster, *, cmc_key: str | None = None
    ) -> FiscalResult:
        """One item per call (§3.3.8.3), which is also one outbox row per (item, branch)."""
        request = builders.build_stock_master_request(device, master)
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


#: CIS §7.24.7: what the receipt's QR code encodes, `#`-separated.
#:
#: `invoice_date(ddmmyyyy)#time(hhmmss)#sdc number#sdc_receipt_number#internal_data#
#: receipt_signature` — six fields, from the **device's** clock rather than the posting's,
#: because what a person scanning the receipt is checking is what the SDC signed.
#:
#: `sdc_receipt_number` is read as the *total* receipt counter, not the per-type one: the pair
#: prints as `rcptNo/totRcptNo` and the total is the one that identifies a receipt uniquely on
#: the device. The 2018 document does not say which, and a sample receipt from the test
#: environment settles it — carried as a step-5 sandbox question, exactly as the phase brief
#: says a sample receipt overrides this format.
QR_FIELD_SEPARATOR = "#"


def verification_code(
    *,
    stamped: str,
    sdc_id: str,
    tot_rcpt_no: int,
    intrl_data: str,
    rcpt_sign: str,
) -> str:
    day, clock = stamped[:8], stamped[8:14]
    # `ddmmyyyy` from `yyyyMMdd`: the wire and the receipt disagree about field order, and the
    # receipt's is the one a person reads.
    printed_day = f"{day[6:8]}{day[4:6]}{day[0:4]}" if len(day) == 8 else day
    return QR_FIELD_SEPARATOR.join(
        (printed_day, clock, sdc_id, str(tot_rcpt_no), intrl_data, rcpt_sign)
    )


def parse_stamp(stamped: str) -> datetime:
    """`yyyyMMddHHmmss` in **Kigali**, as an instant.

    The documents use one date format and no zone, and the taxpayer is in Kigali — so that is
    the zone, said here where the format is read rather than guessed at by whoever stores the
    value. An unparseable stamp falls back to now: a receipt whose other five fields are
    present is still a receipt, and refusing it would strand a sale RRA has already signed.
    """
    try:
        return datetime.strptime(stamped, "%Y%m%d%H%M%S").replace(tzinfo=builders.KIGALI)
    except ValueError:
        return datetime.now(UTC)


def _now_watermark() -> str:
    """`yyyyMMddHHmmss`, which is the only date format the documents use.

    Taken from the client clock rather than from `resultDt`, and that is a deliberate
    trade-off: a watermark slightly *behind* the server re-fetches a few rows, which the
    upserts absorb, while one slightly ahead skips rows forever.
    """
    return datetime.now(UTC).strftime("%Y%m%d%H%M%S")


__all__ = ["RwandaEbmAdapter", "codes", "parse_stamp", "redact", "verification_code"]
