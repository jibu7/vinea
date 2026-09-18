"""`NullAdapter` — the adapter for a company that does not fiscalize.

Most tenants will never fiscalize: a company below the VAT threshold, one outside Rwanda, and
every development database. They still run the same code path, because the alternative is an
`if company.is_fiscalized` at every call site and a second, less-tested path behind it.

So every call here **succeeds and records what it was asked to do**. Nothing is sent, nothing
is stored, and `calls` is readable so a test can assert that a non-fiscalized company produced
no traffic — which is the thing that actually matters about this class.

It is not a stub for the Rwanda adapter and must never become one: a company with no active
device never reaches an adapter at all (`post_document` refuses first), so nothing here stands
in for a device that ought to exist.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.fiscal.mapping import (
    FiscalImportDecision,
    FiscalItemRegistration,
    FiscalPurchase,
    FiscalPurchaseConfirmation,
    FiscalRefund,
    FiscalSale,
    FiscalStockIO,
    FiscalStockMaster,
    FiscalSyncResult,
)
from app.fiscal.protocol import (
    DeclaredTotals,
    DeviceIdentity,
    FiscalReceiptData,
    FiscalResult,
    TinLookup,
)
from app.models.fiscalization import FiscalDevice, FiscalOutboxKind


@dataclass(frozen=True)
class RecordedCall:
    method: str
    at: datetime
    detail: dict[str, Any] = field(default_factory=dict)


class NullAdapter:
    """Every call a recorded no-op."""

    def __init__(self) -> None:
        self.calls: list[RecordedCall] = []

    def _record(self, method: str, **detail: Any) -> FiscalResult:
        self.calls.append(RecordedCall(method=method, at=datetime.now(UTC), detail=detail))
        return FiscalResult.success()

    # --- Setup -----------------------------------------------------------------------------
    def initialize_device(
        self, device: FiscalDevice, *, cmc_key: str | None = None
    ) -> tuple[FiscalResult, DeviceIdentity | None]:
        # No identity, deliberately: a null device that came back holding an `sdc_id` would
        # satisfy `active_device_is_initialized` and become indistinguishable from a real one.
        return self._record("initialize_device", device_id=device.id), None

    def sync_codes(
        self, device: FiscalDevice, *, since: str | None, cmc_key: str | None = None
    ) -> tuple[FiscalResult, FiscalSyncResult]:
        # An empty result **with no watermark**: nothing was fetched, so nothing may be
        # recorded as fetched. A watermark here would make the next real sync skip everything
        # published before it.
        return (
            self._record("sync_codes", device_id=device.id, since=since),
            FiscalSyncResult(),
        )

    def sync_item_classes(
        self, device: FiscalDevice, *, since: str | None, cmc_key: str | None = None
    ) -> tuple[FiscalResult, FiscalSyncResult]:
        return (
            self._record("sync_item_classes", device_id=device.id, since=since),
            FiscalSyncResult(),
        )

    def lookup_tin(
        self, device: FiscalDevice, tin: str, *, cmc_key: str | None = None
    ) -> tuple[FiscalResult, TinLookup]:
        result = self._record("lookup_tin", device_id=device.id, tin=tin)
        # `found=False` rather than True: "we did not look" must not read as "it is valid".
        return result, TinLookup(tin=tin, found=False)

    # --- The outbox seam -------------------------------------------------------------------
    def render(
        self, device: FiscalDevice, kind: FiscalOutboxKind, document: Any
    ) -> dict[str, Any]:
        """A record of what *would* have been sent, in Vinea's own words.

        Not an authority's payload, because there is no authority: this adapter serves a
        company that does not fiscalize, and inventing a revenue authority's field names for
        one would be the country logic rule 12 exists to keep out of a tenant that has none.
        """
        self._record("render", device_id=device.id, kind=str(kind))
        return {"adapter": "null", "kind": str(kind), "document": repr(document)}

    def send(
        self,
        device: FiscalDevice,
        kind: FiscalOutboxKind,
        payload: dict[str, Any],
        *,
        cmc_key: str | None = None,
    ) -> tuple[FiscalResult, FiscalReceiptData | None]:
        result = self._record("send", device_id=device.id, kind=str(kind))
        # No receipt, for the reason `fiscalize_sale` gives below: a receipt is what an
        # authority signed, and none has.
        return result, None

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
        """The sequence, and nothing composed around it.

        A company with no authority has no published item-code format, and borrowing Rwanda's
        would put a `RW` on an item in a country that never issued one.
        """
        self._record("mint_item_code", sequence_no=sequence_no)
        return f"{sequence_no:07d}"

    def register_item(
        self, device: FiscalDevice, item: FiscalItemRegistration, *, cmc_key: str | None = None
    ) -> FiscalResult:
        return self._record("register_item", device_id=device.id, item_code=item.item_code)

    # --- Sales -----------------------------------------------------------------------------
    def fiscalize_sale(
        self, device: FiscalDevice, sale: FiscalSale, *, cmc_key: str | None = None
    ) -> tuple[FiscalResult, FiscalReceiptData | None]:
        result = self._record("fiscalize_sale", device_id=device.id, invoice_no=sale.invoice_no)
        # No receipt. A receipt is what a revenue authority signed, and this adapter has not
        # asked one — a fabricated one here would print on a customer's invoice.
        return result, None

    def fiscalize_refund(
        self, device: FiscalDevice, refund: FiscalRefund, *, cmc_key: str | None = None
    ) -> tuple[FiscalResult, FiscalReceiptData | None]:
        result = self._record(
            "fiscalize_refund", device_id=device.id, invoice_no=refund.invoice_no
        )
        return result, None

    def normalize_receipt(
        self,
        device: FiscalDevice,
        response: dict[str, Any] | None,
        *,
        invc_no: int | None = None,
    ) -> FiscalReceiptData | None:
        """Always `None`. There is no authority, so there is nothing that signed anything, and
        a receipt normalised out of an empty conversation would print on an invoice."""
        self._record("normalize_receipt", device_id=device.id)
        return None

    # --- Purchases and imports -------------------------------------------------------------
    def normalize_declared_totals(
        self,
        request: dict[str, Any] | None,
        response: dict[str, Any] | None = None,
    ) -> DeclaredTotals | None:
        """Always `None`. Nothing was declared to anybody, so there is no day to total."""
        return None

    def register_purchase(
        self, device: FiscalDevice, purchase: FiscalPurchase, *, cmc_key: str | None = None
    ) -> FiscalResult:
        return self._record(
            "register_purchase", device_id=device.id, invoice_no=purchase.invoice_no
        )

    def fetch_purchase_feed(
        self, device: FiscalDevice, *, since: str | None, cmc_key: str | None = None
    ) -> tuple[FiscalResult, FiscalSyncResult]:
        return (
            self._record("fetch_purchase_feed", device_id=device.id, since=since),
            FiscalSyncResult(),
        )

    def confirm_purchase(
        self,
        device: FiscalDevice,
        confirmation: FiscalPurchaseConfirmation,
        *,
        cmc_key: str | None = None,
    ) -> FiscalResult:
        return self._record(
            "confirm_purchase", device_id=device.id, invoice_no=confirmation.invoice_no
        )

    # --- Stock -----------------------------------------------------------------------------
    def report_stock_io(
        self, device: FiscalDevice, movement: FiscalStockIO, *, cmc_key: str | None = None
    ) -> FiscalResult:
        return self._record("report_stock_io", device_id=device.id, stock_no=movement.stock_no)

    def report_stock_master(
        self, device: FiscalDevice, master: FiscalStockMaster, *, cmc_key: str | None = None
    ) -> FiscalResult:
        return self._record(
            "report_stock_master", device_id=device.id, item_code=master.item_code
        )

    # --- Imports ---------------------------------------------------------------------------
    def fetch_imports(
        self, device: FiscalDevice, *, since: str | None, cmc_key: str | None = None
    ) -> tuple[FiscalResult, FiscalSyncResult]:
        return (
            self._record("fetch_imports", device_id=device.id, since=since),
            FiscalSyncResult(),
        )

    def update_import(
        self,
        device: FiscalDevice,
        *,
        declaration: FiscalImportDecision,
        cmc_key: str | None = None,
    ) -> FiscalResult:
        return self._record(
            "update_import", device_id=device.id, approved=declaration.approved
        )
