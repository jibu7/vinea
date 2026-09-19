"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type {
  AttachReceiptPayload,
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

/** The rows in queue order — which is send order. `documentId` is the read-only per-document
 * history the document detail shows. */
export function useFiscalQueueRows(
  filters: { deviceId?: number | null; status?: string; documentId?: number | null } = {},
) {
  const query = new URLSearchParams();
  if (filters.deviceId) query.set("device_id", String(filters.deviceId));
  if (filters.status) query.set("status", filters.status);
  if (filters.documentId) query.set("document_id", String(filters.documentId));
  const suffix = query.toString() ? `?${query}` : "";
  return useQuery({
    queryKey: [ROOT, "queue-rows", suffix],
    queryFn: () => api.get<QueueRow[]>(`/fiscal/queue/rows${suffix}`),
    refetchInterval: 15_000,
  });
}

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
 * What this document prints, `null` when it is not a fiscal receipt at all.
 *
 * Three answers, not two, and the screen needs all three: a block is "print the CIS layout",
 * `null` is "this company does not fiscalize, print the P4 layout", and a
 * `fiscal_receipt_pending` refusal is "it does, and RRA has not signed yet" — which is why the
 * error is kept rather than swallowed. `retry: false` so a refusal is shown rather than asked
 * for three more times.
 */
export function useDocumentReceipt(documentId: number | null) {
  return useQuery({
    queryKey: [ROOT, "document-receipt", documentId],
    queryFn: () => api.get<ReceiptBlock | null>(`/fiscal/documents/${documentId}/receipt`),
    enabled: documentId !== null,
    retry: false,
  });
}

/** A reprint: `COPY` under the header, the counter incremented and audited, and **nothing sent
 * to RRA** — a copy is a print of a sale already declared (§11, §15). */
export function usePrintReceiptCopy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentId: number) =>
      api.post<ReceiptBlock>(`/fiscal/documents/${documentId}/receipt/copy`),
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
