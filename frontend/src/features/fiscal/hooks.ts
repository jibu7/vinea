"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { FiscalOutboxStatus } from "@/lib/api-enums";
import type {
  AttachReceiptPayload,
  DailyReport,
  DeviceCreatePayload,
  DeviceSyncResult,
  FeedDecisionResult,
  FiscalCode,
  FiscalDevice,
  FiscalDocumentContext,
  FiscalItemClass,
  FiscalReceipt,
  ImportDeclaration,
  ItemRegistration,
  PurchaseFeedRow,
  QueueDevice,
  QueueRow,
  QueueRowDetail,
  ReceiptBlock,
  ReceiptListing,
  TinLookup,
} from "./types";

/** One root key for the module, so a device write invalidates every list derived from it —
 * the same shape `gl`, `inventory` and `order-entry` use. */
const ROOT = "fiscal";

export function useFiscalDevices() {
  return useQuery({
    queryKey: [ROOT, "devices"],
    queryFn: () => api.get<FiscalDevice[]>("/fiscal/devices"),
    staleTime: 30_000,
  });
}

export function useRegisterFiscalDevice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: DeviceCreatePayload) =>
      api.post<FiscalDevice>("/fiscal/devices", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/**
 * Initialize — and therefore **activate**, because there is no other way in.
 *
 * P7 step 1's decision: a device is activated by telling the authority about it and storing
 * what comes back, so a suspended device is brought back by re-initializing rather than by a
 * separate Activate button. `activate()` in `app/fiscal/devices.py` refuses a device with no
 * `sdc_id`, which is what makes that true rather than a convention; a screen offering
 * Activate would be offering a button that could only ever fail.
 *
 * `Idempotency-Key` is **required** by the endpoint, not optional politeness: this call is
 * not naturally idempotent from the caller's side, and a double-click while the authority is
 * slow would otherwise re-initialize a live device — which is how its keys get reissued.
 */
export function useInitializeFiscalDevice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ deviceId, idempotencyKey }: { deviceId: number; idempotencyKey: string }) =>
      api.post<FiscalDevice>(`/fiscal/devices/${deviceId}/initialize`, undefined, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useSuspendFiscalDevice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ deviceId, reason }: { deviceId: number; reason: string }) =>
      api.post<FiscalDevice>(`/fiscal/devices/${deviceId}/suspend`, { reason }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/**
 * Sync codes — both tables, in one press.
 *
 * Two endpoints and one button, deliberately. They are two calls to the authority because
 * they are two of its endpoints with two watermarks, but they are one *job*: an operator who
 * refreshed the code tables and not the classification would have a Tax-types screen offering
 * A–D and an Items screen whose class typeahead found nothing, and no way to tell why. The
 * classification is the slow one, so it runs second and the result carries both counts.
 */
export function useSyncFiscalCodes() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (deviceId: number) => {
      const codes = await api.post<DeviceSyncResult>(`/fiscal/devices/${deviceId}/sync-codes`);
      const classes = await api.post<DeviceSyncResult>(
        `/fiscal/devices/${deviceId}/sync-item-classes`,
      );
      return { codes, classes };
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/** The synced code tables, filtered by class. `enabled` on the class, so a screen that has
 * not decided which table it wants does not fetch all of them. */
export function useFiscalCodes(codeClass?: string) {
  return useQuery({
    queryKey: [ROOT, "codes", codeClass ?? "all"],
    queryFn: () =>
      api.get<FiscalCode[]>(
        codeClass ? `/fiscal/codes?code_class=${encodeURIComponent(codeClass)}` : "/fiscal/codes",
      ),
    staleTime: 5 * 60_000,
  });
}

/**
 * The authority's item classification, searched rather than listed.
 *
 * Tens of thousands of rows: a picker that loaded them all would be a picker nobody could
 * use. The search runs on the server and the page is bounded there too, so what a slow
 * connection carries is fifty rows and not a catalogue.
 *
 * **Debounced**, because the caller is a typeahead: without it, typing a ten-digit class code
 * is ten requests, nine of which are for prefixes nobody wanted. 250ms is under the threshold
 * where a picker feels slow and over the gap between keystrokes.
 */
export function useFiscalItemClasses(search: string) {
  const term = useDebounced(search.trim(), 250);
  return useQuery({
    queryKey: [ROOT, "item-classes", term],
    queryFn: () =>
      api.get<FiscalItemClass[]>(
        term ? `/fiscal/item-classes?search=${encodeURIComponent(term)}` : "/fiscal/item-classes",
      ),
    staleTime: 5 * 60_000,
  });
}

/** `value`, but only once it has stopped changing for `delay` ms. */
function useDebounced<T>(value: T, delay: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

/** Every item with its registration beside it — including the ones RRA has never heard of,
 * which are the ones that would refuse a fiscalized sale (`fiscal_class_missing`). */
export function useItemRegistrations() {
  return useQuery({
    queryKey: [ROOT, "items"],
    queryFn: () => api.get<ItemRegistration[]>("/fiscal/items"),
    staleTime: 30_000,
  });
}

/**
 * Verify TIN, from the Customers and Suppliers screens.
 *
 * A **mutation** over a GET, which looks wrong and is not: the authority's opinion of a TIN
 * is an answer at a moment rather than a fact about the partner, and nothing is stored. What
 * the screen needs is a button that asks *now* and shows what came back — a `useQuery` would
 * cache the answer and re-ask it on a window focus, which is the opposite of both.
 */
export function useVerifyTin() {
  return useMutation({
    mutationFn: ({ deviceId, tin }: { deviceId: number; tin: string }) =>
      api.get<TinLookup>(`/fiscal/devices/${deviceId}/lookup-tin?tin=${encodeURIComponent(tin)}`),
  });
}

// --- What a posting screen needs (P7 step 7) -------------------------------------------------

/**
 * Whether this company fiscalizes, and what a refund may give as its reason.
 *
 * The only fiscal read the Invoice and Credit-note screens make, and it takes **no fiscal
 * permission** — the seeded Sales Manager role holds `ar:transactions_post` and neither
 * `fiscal:setup_manage` nor `fiscal:reports_view`, so answering "is a purchase code required
 * here" out of the device listing would mean handing every sales manager the authority to
 * reconfigure the devices.
 *
 * Long `staleTime`: a device is activated once and RRA's §4.16 table changes when RRA
 * republishes it, neither of which happens while somebody is keying an invoice.
 */
export function useFiscalDocumentContext() {
  return useQuery({
    queryKey: [ROOT, "document-context"],
    queryFn: () => api.get<FiscalDocumentContext>("/fiscal/document-context"),
    staleTime: 5 * 60_000,
  });
}

// --- The queue (decision 4) ------------------------------------------------------------------

/** Every device and what its queue holds. `refetchInterval` because this is a *watch* screen:
 * the worker drains every fifteen seconds and a person staring at a blocked device needs the
 * page to notice when it clears, without pressing anything. */
export function useFiscalQueue(deviceId?: number | null) {
  return useQuery({
    queryKey: [ROOT, "queue", deviceId ?? "all"],
    queryFn: () =>
      api.get<QueueDevice[]>(
        deviceId ? `/fiscal/queue?device_id=${deviceId}` : "/fiscal/queue",
      ),
    refetchInterval: 15_000,
  });
}

/**
 * The rows in queue order — which is send order. `documentId` is the read-only per-document
 * history the document detail shows.
 *
 * **Polls only while something can still move.** The queue screen is a watch screen and wants
 * the page to notice when a stuck device clears, without anybody pressing anything. The
 * document detail is not: a posted document's rows are a closed set, and once they are all
 * terminal there is nothing left to see — so a non-fiscalized company would otherwise poll an
 * endpoint every fifteen seconds, forever, on every document anybody opened.
 */
export function useFiscalQueueRows(
  filters: {
    deviceId?: number | null;
    status?: string;
    documentId?: number | null;
    /** `false` on a closed set: stop once every row has reached a terminal state. */
    watch?: boolean;
  } = {},
) {
  const query = new URLSearchParams();
  if (filters.deviceId) query.set("device_id", String(filters.deviceId));
  if (filters.status) query.set("status", filters.status);
  if (filters.documentId) query.set("document_id", String(filters.documentId));
  const suffix = query.toString() ? `?${query}` : "";
  const watch = filters.watch ?? true;
  return useQuery({
    queryKey: [ROOT, "queue-rows", suffix],
    queryFn: () => api.get<QueueRow[]>(`/fiscal/queue/rows${suffix}`),
    refetchInterval: (query) =>
      watch || (query.state.data ?? []).some((row) => !TERMINAL_STATUSES.has(row.status))
        ? 15_000
        : false,
  });
}

/** A row in one of these will not change again by itself. `cancelled` is terminal too: the
 * document it reported was reversed before RRA ever held it. */
const TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  FiscalOutboxStatus.SENT,
  FiscalOutboxStatus.CANCELLED,
]);

export function useFiscalQueueRow(rowId: number | null) {
  return useQuery({
    queryKey: [ROOT, "queue-row", rowId],
    queryFn: () => api.get<QueueRowDetail>(`/fiscal/queue/rows/${rowId}`),
    enabled: rowId !== null,
  });
}

/** Put a `failed` or backing-off row at the front of its queue, due now. Never offered for
 * `unknown` or `needs_receipt`: RRA may already hold the sale, and a retry on those is exactly
 * the duplicate the policy exists to prevent — the screen hides the button and the endpoint
 * refuses it. */
export function useRetryQueueRow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (rowId: number) => api.post<QueueRow>(`/fiscal/queue/rows/${rowId}/retry`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/** Ask the device what it holds and let its counters decide: below ours means RRA never saw
 * the row (back to `queued`), at or above means it did (`needs_receipt`). */
export function useVerifyQueueRow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (rowId: number) => api.post<QueueRow>(`/fiscal/queue/rows/${rowId}/verify`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/** A receipt read off MyRRA and attached by a person who says so — audited with their note,
 * because it is a human assertion about what a revenue authority is holding. */
export function useAttachQueueReceipt() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ rowId, payload }: { rowId: number; payload: AttachReceiptPayload }) =>
      api.post<FiscalReceipt>(`/fiscal/queue/rows/${rowId}/attach-receipt`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

// --- The receipt a document prints (decision 11) ----------------------------------------------

/**
 * **Every** receipt this document holds, in the order the authority issued them.
 *
 * Usually one. A signed sale that was reversed holds **two** — the `NS` it was declared under
 * and the `NR` that reversed it (decision 7: reversing a fiscalized invoice queues a full
 * refund rather than cancelling the sale) — and the customer is owed the second piece of paper
 * as much as the first. A screen that showed only the latest would make the original receipt
 * unreachable from the document it belongs to.
 */
export function useDocumentReceipts(documentId: number | null) {
  return useQuery({
    queryKey: [ROOT, "document-receipts", documentId],
    queryFn: () => api.get<FiscalReceipt[]>(`/fiscal/receipts?document_id=${documentId}`),
    enabled: documentId !== null,
  });
}

/**
 * What this document prints, `null` when it is not a fiscal receipt at all.
 *
 * Three answers, not two, and the screen needs all three: a block is "print the CIS layout",
 * `null` is "this company does not fiscalize, print the P4 layout", and a
 * `fiscal_receipt_pending` refusal is "it does, and RRA has not signed yet" — which is why the
 * error is kept rather than swallowed. `retry: false` so a refusal is shown rather than asked
 * for three more times.
 */
export function useDocumentReceipt(documentId: number | null, receiptId?: number | null) {
  const suffix = receiptId ? `?receipt_id=${receiptId}` : "";
  return useQuery({
    queryKey: [ROOT, "document-receipt", documentId, receiptId ?? "latest"],
    queryFn: () =>
      api.get<ReceiptBlock | null>(`/fiscal/documents/${documentId}/receipt${suffix}`),
    enabled: documentId !== null,
    retry: false,
  });
}

/** A reprint: `COPY` under the header, the counter incremented and audited, and **nothing sent
 * to RRA** — a copy is a print of a sale already declared (§11, §15). */
export function usePrintReceiptCopy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      documentId,
      receiptId,
    }: {
      documentId: number;
      receiptId?: number | null;
    }) =>
      api.post<ReceiptBlock>(
        `/fiscal/documents/${documentId}/receipt/copy${receiptId ? `?receipt_id=${receiptId}` : ""}`,
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

// --- The purchase feed and the import register (decision 9) ------------------------------------

export function usePurchaseFeed(filters: { deviceId?: number | null; decision?: string } = {}) {
  const query = new URLSearchParams();
  if (filters.deviceId) query.set("device_id", String(filters.deviceId));
  if (filters.decision) query.set("decision", filters.decision);
  const suffix = query.toString() ? `?${query}` : "";
  return useQuery({
    queryKey: [ROOT, "purchase-feed", suffix],
    queryFn: () => api.get<PurchaseFeedRow[]>(`/fiscal/purchase-feed${suffix}`),
    staleTime: 30_000,
  });
}

/** Pull what RRA is holding since the device's watermark. A feed nobody can refresh is a feed
 * that is always yesterday's, which is why the screen carries its own Fetch. */
export function useFetchPurchaseFeed() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (deviceId: number) =>
      api.post<DeviceSyncResult>(`/fiscal/devices/${deviceId}/fetch-purchase-feed`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/**
 * Confirm a purchase RRA is holding, optionally linking the AP document it became.
 *
 * `Idempotency-Key` because the decision claims an `FIP` number and queues a row: a
 * double-click would otherwise spend a number on a confirmation nobody asked for twice.
 */
export function useAcceptPurchaseFeedRow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      rowId,
      apDocumentId,
      idempotencyKey,
    }: {
      rowId: number;
      apDocumentId: number | null;
      idempotencyKey: string;
    }) =>
      api.post<FeedDecisionResult>(
        `/fiscal/purchase-feed/${rowId}/accept`,
        { ap_document_id: apDocumentId },
        { "Idempotency-Key": idempotencyKey },
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useRejectPurchaseFeedRow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ rowId, idempotencyKey }: { rowId: number; idempotencyKey: string }) =>
      api.post<FeedDecisionResult>(`/fiscal/purchase-feed/${rowId}/reject`, undefined, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useImportDeclarations(filters: { deviceId?: number | null; status?: string } = {}) {
  const query = new URLSearchParams();
  if (filters.deviceId) query.set("device_id", String(filters.deviceId));
  if (filters.status) query.set("status", filters.status);
  const suffix = query.toString() ? `?${query}` : "";
  return useQuery({
    queryKey: [ROOT, "import-declarations", suffix],
    queryFn: () => api.get<ImportDeclaration[]>(`/fiscal/import-declarations${suffix}`),
    staleTime: 30_000,
  });
}

export function useFetchImports() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (deviceId: number) =>
      api.post<DeviceSyncResult>(`/fiscal/devices/${deviceId}/fetch-imports`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/** Acknowledge a declared line, naming the Vinea item it became. It moves no stock and posts
 * nothing (decision 9) — the goods reached the ledger through a goods receipt, and saying so
 * twice would double them. */
export function useApproveImportDeclaration() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      declarationId,
      itemId,
      note,
      idempotencyKey,
    }: {
      declarationId: number;
      itemId: number;
      note: string | null;
      idempotencyKey: string;
    }) =>
      api.post<ImportDeclaration>(
        `/fiscal/import-declarations/${declarationId}/approve`,
        { item_id: itemId, note },
        { "Idempotency-Key": idempotencyKey },
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useRejectImportDeclaration() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      declarationId,
      note,
      idempotencyKey,
    }: {
      declarationId: number;
      note: string | null;
      idempotencyKey: string;
    }) =>
      api.post<ImportDeclaration>(
        `/fiscal/import-declarations/${declarationId}/reject`,
        { note },
        { "Idempotency-Key": idempotencyKey },
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

// --- The enquiries and reports (P7 step 8) --------------------------------------------------

/**
 * The receipts RRA signed, searched the way somebody holding one searches.
 *
 * One box over the printed counter (`3`, `3/4`, `3/4 NS`), the document number, the partner
 * and the authority's invoice number. Four fields would make a person guess which one their
 * piece of paper matches; the server anchors each form rather than matching fuzzily, so a
 * search for `1` does not return the day.
 */
export function useFiscalReceipts(
  filters: {
    deviceId?: number | null;
    receiptType?: string;
    partnerId?: number | null;
    dateFrom?: string;
    dateTo?: string;
    search?: string;
  } = {},
) {
  const query = new URLSearchParams();
  if (filters.deviceId) query.set("device_id", String(filters.deviceId));
  if (filters.receiptType) query.set("receipt_type", filters.receiptType);
  if (filters.partnerId) query.set("partner_id", String(filters.partnerId));
  if (filters.dateFrom) query.set("date_from", filters.dateFrom);
  if (filters.dateTo) query.set("date_to", filters.dateTo);
  if (filters.search) query.set("search", filters.search);
  const suffix = query.toString() ? `?${query}` : "";
  return useQuery({
    queryKey: [ROOT, "receipts", suffix],
    queryFn: () => api.get<FiscalReceipt[]>(`/fiscal/receipts${suffix}`),
  });
}

/** One receipt, through the same query the listing uses — so the three ids it drills on
 * (receipt → document → journal entry) cannot disagree with the row that was clicked. */
export function useFiscalReceipt(receiptId: number | null) {
  return useQuery({
    queryKey: [ROOT, "receipt", receiptId],
    queryFn: () => api.get<FiscalReceipt>(`/fiscal/receipts/${receiptId}`),
    enabled: receiptId !== null,
  });
}

/**
 * The tie: what a device declared over a range, beside what the sales ledger holds.
 *
 * `enabled` on all three, because a listing with no device is a total across devices and the
 * counters this report states are per device (decision 5).
 */
export function useReceiptListing(
  deviceId: number | null,
  dateFrom: string,
  dateTo: string,
  reportNo: number | null = null,
) {
  return useQuery({
    queryKey: [ROOT, "receipt-listing", deviceId, dateFrom, dateTo, reportNo],
    queryFn: () =>
      api.get<ReceiptListing>(
        `/fiscal/receipts/listing?device_id=${deviceId}&date_from=${dateFrom}&date_to=${dateTo}` +
          (reportNo === null ? "" : `&report_no=${reportNo}`),
      ),
    enabled: deviceId !== null && Boolean(dateFrom && dateTo),
  });
}

/** The day so far. An X is a question — it stores nothing and changes nothing, so it is never
 * cached for long: the figure a shopkeeper is looking at is the one that has to be current. */
export function useXReport(deviceId: number | null) {
  return useQuery({
    queryKey: [ROOT, "x-report", deviceId],
    queryFn: () => api.get<DailyReport>(`/fiscal/devices/${deviceId}/x-report`),
    enabled: deviceId !== null,
    staleTime: 0,
  });
}

/** The closed days, newest first. A Z is stored and immutable, so this one may be cached. */
export function useZReports(deviceId: number | null) {
  return useQuery({
    queryKey: [ROOT, "z-reports", deviceId],
    queryFn: () => api.get<DailyReport[]>(`/fiscal/devices/${deviceId}/z-reports`),
    enabled: deviceId !== null,
    staleTime: 30_000,
  });
}

/**
 * Take the Z: store the day and open the next one where this one ended.
 *
 * `Idempotency-Key` because a close cannot be taken back — a second one would store an empty
 * day, move the boundary and spend an `FZR` number on it.
 *
 * It does **not** refuse over a queue that still holds rows, and the screen says so before the
 * button rather than after. A Z records `queued_rows` on its face precisely so that a day
 * closed over an unsent sale is evidence rather than a silent gap (decision 11); a refusal
 * would make that figure dead and would leave a shop unable to close because a line was down.
 */
export function useCloseFiscalDay() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      deviceId,
      idempotencyKey,
    }: {
      deviceId: number;
      idempotencyKey: string;
    }) =>
      api.post<DailyReport>(
        `/fiscal/devices/${deviceId}/close-day`,
        {},
        { "Idempotency-Key": idempotencyKey },
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}
