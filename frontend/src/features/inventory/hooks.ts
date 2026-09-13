"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type {
  Barcode,
  BarcodeCreatePayload,
  BarcodeListing,
  BarcodeUpdatePayload,
  CountLine,
  CountLineEntryPayload,
  CountPreview,
  CountProcessResult,
  CountReport,
  CountSession,
  CountSessionPayload,
  CountSessionSummary,
  InventoryDefaults,
  InventoryDefaultsPayload,
  Item,
  ItemAuditRecord,
  ItemCreatePayload,
  ItemEnquiry,
  ItemUpdatePayload,
  MovementReport,
  OnHandRow,
  Page,
  StockDocument,
  StockDocumentPayload,
  StockDocumentReversePayload,
  StockDocumentSummary,
  TransactionReport,
  Transfer,
  TransferPayload,
  TransferSummary,
  Uom,
  UomCategoryCreatePayload,
  UomCategoryUpdatePayload,
  UomCategoryWithUnits,
  UomCreatePayload,
  UomUpdatePayload,
  ValuationReport,
  Warehouse,
  WarehouseCreatePayload,
  WarehouseUpdatePayload,
} from "./types";

/** One root key for every inventory query, so a write invalidates the whole module cleanly —
 * renaming an item has to move it in the barcode listing too, and those are different URLs. */
const ROOT = "inventory";

// --- Units of measure -------------------------------------------------------------------

export function useUomCategories(opts: { includeInactive?: boolean } = {}) {
  const includeInactive = opts.includeInactive ?? false;
  return useQuery({
    queryKey: [ROOT, "uom-categories", { includeInactive }],
    queryFn: () =>
      api.get<UomCategoryWithUnits[]>(
        `/inventory/uom-categories${includeInactive ? "?include_inactive=true" : ""}`,
      ),
    staleTime: 60_000,
  });
}

export function useCreateUomCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: UomCategoryCreatePayload) =>
      api.post<UomCategoryWithUnits>("/inventory/uom-categories", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useUpdateUomCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ categoryId, payload }: { categoryId: number; payload: UomCategoryUpdatePayload }) =>
      api.patch<UomCategoryWithUnits>(`/inventory/uom-categories/${categoryId}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useUoms(categoryId: number | null, opts: { includeInactive?: boolean } = {}) {
  const includeInactive = opts.includeInactive ?? false;
  const search = new URLSearchParams();
  if (categoryId !== null) search.set("category_id", String(categoryId));
  if (includeInactive) search.set("include_inactive", "true");
  const query = search.toString();
  return useQuery({
    queryKey: [ROOT, "uoms", { categoryId, includeInactive }],
    queryFn: () => api.get<Uom[]>(`/inventory/uoms${query ? `?${query}` : ""}`),
    staleTime: 60_000,
  });
}

export function useCreateUom() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: UomCreatePayload) => api.post<Uom>("/inventory/uoms", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useUpdateUom() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ uomId, payload }: { uomId: number; payload: UomUpdatePayload }) =>
      api.patch<Uom>(`/inventory/uoms/${uomId}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

// --- Items ------------------------------------------------------------------------------

export function useItems(opts: { search?: string; includeInactive?: boolean } = {}) {
  const search = opts.search?.trim() ?? "";
  const includeInactive = opts.includeInactive ?? false;
  const params = new URLSearchParams();
  if (search) params.set("search", search);
  if (includeInactive) params.set("include_inactive", "true");
  const query = params.toString();
  return useQuery({
    queryKey: [ROOT, "items", { search, includeInactive }],
    queryFn: () => api.get<Item[]>(`/inventory/items${query ? `?${query}` : ""}`),
    staleTime: 30_000,
  });
}

export function useItem(itemId: number | null) {
  return useQuery({
    queryKey: [ROOT, "items", itemId],
    queryFn: () => api.get<Item>(`/inventory/items/${itemId}`),
    enabled: itemId !== null,
  });
}

export function useCreateItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: ItemCreatePayload) => api.post<Item>("/inventory/items", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useUpdateItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, payload }: { itemId: number; payload: ItemUpdatePayload }) =>
      api.patch<Item>(`/inventory/items/${itemId}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/** Rename history: audit rows keyed on `item_id`, so a code change never loses its trail. */
export function useItemHistory(itemId: number | null) {
  return useQuery({
    queryKey: [ROOT, "items", itemId, "history"],
    queryFn: () => api.get<ItemAuditRecord[]>(`/inventory/items/${itemId}/history`),
    enabled: itemId !== null,
  });
}

// --- Barcodes ---------------------------------------------------------------------------

export function useItemBarcodes(itemId: number | null) {
  return useQuery({
    queryKey: [ROOT, "items", itemId, "barcodes"],
    queryFn: () => api.get<Barcode[]>(`/inventory/items/${itemId}/barcodes`),
    enabled: itemId !== null,
  });
}

/** The company-wide listing behind the Barcodes screen. */
export function useBarcodes(opts: { search?: string; includeInactive?: boolean } = {}) {
  const search = opts.search?.trim() ?? "";
  const includeInactive = opts.includeInactive ?? false;
  const params = new URLSearchParams();
  if (search) params.set("q", search);
  if (includeInactive) params.set("include_inactive", "true");
  const query = params.toString();
  return useQuery({
    queryKey: [ROOT, "barcodes", { search, includeInactive }],
    queryFn: () => api.get<Page<BarcodeListing>>(`/inventory/barcodes${query ? `?${query}` : ""}`),
    staleTime: 30_000,
  });
}

export function useCreateBarcode() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, payload }: { itemId: number; payload: BarcodeCreatePayload }) =>
      api.post<Barcode>(`/inventory/items/${itemId}/barcodes`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useUpdateBarcode() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ barcodeId, payload }: { barcodeId: number; payload: BarcodeUpdatePayload }) =>
      api.patch<Barcode>(`/inventory/barcodes/${barcodeId}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

// --- Warehouses -------------------------------------------------------------------------

export function useWarehouses(
  opts: { includeInactive?: boolean; includeInTransit?: boolean } = {},
) {
  const includeInactive = opts.includeInactive ?? false;
  // Off by default, matching the endpoint: every picker that reads this would otherwise offer
  // the in-transit location as somewhere to send stock (decision 6). The Warehouses
  // *maintenance* screen turns it on, because that is the one place it should be visible.
  const includeInTransit = opts.includeInTransit ?? false;
  const params = new URLSearchParams();
  if (includeInactive) params.set("include_inactive", "true");
  if (includeInTransit) params.set("include_in_transit", "true");
  const query = params.toString();
  return useQuery({
    queryKey: [ROOT, "warehouses", { includeInactive, includeInTransit }],
    queryFn: () => api.get<Warehouse[]>(`/inventory/warehouses${query ? `?${query}` : ""}`),
    staleTime: 60_000,
  });
}

export function useCreateWarehouse() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: WarehouseCreatePayload) =>
      api.post<Warehouse>("/inventory/warehouses", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useUpdateWarehouse() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ warehouseId, payload }: { warehouseId: number; payload: WarehouseUpdatePayload }) =>
      api.patch<Warehouse>(`/inventory/warehouses/${warehouseId}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

// --- Defaults ---------------------------------------------------------------------------

export function useInventoryDefaults() {
  return useQuery({
    queryKey: [ROOT, "defaults"],
    queryFn: () => api.get<InventoryDefaults>("/inventory/defaults"),
    staleTime: 60_000,
  });
}

export function useSaveInventoryDefaults() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: InventoryDefaultsPayload) =>
      api.patch<InventoryDefaults>("/inventory/defaults", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

// --- Stock documents (P5 step 7) ----------------------------------------------------------

/** `Idempotency-Key` is the draft's UUID, so a retried post replays instead of duplicating. */
export function usePostAdjustment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ payload, idempotencyKey }: { payload: StockDocumentPayload; idempotencyKey: string }) =>
      api.post<StockDocument>("/inventory/adjustments", payload, { "Idempotency-Key": idempotencyKey }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/** A batch is one unit of work: one key, and a refused line refuses all of them. */
export function usePostJournalBatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ payload, idempotencyKey }: { payload: StockDocumentPayload; idempotencyKey: string }) =>
      api.post<StockDocument>("/inventory/journal-batches", payload, { "Idempotency-Key": idempotencyKey }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useReverseStockDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      documentId,
      payload,
      idempotencyKey,
    }: {
      documentId: number;
      payload: StockDocumentReversePayload;
      idempotencyKey: string;
    }) =>
      api.post<StockDocument>(`/inventory/documents/${documentId}/reverse`, payload, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useStockDocuments(opts: { docType?: string; limit?: number } = {}) {
  const params = new URLSearchParams();
  if (opts.docType) params.set("doc_type", opts.docType);
  if (opts.limit) params.set("limit", String(opts.limit));
  const query = params.toString();
  return useQuery({
    queryKey: [ROOT, "documents", { docType: opts.docType ?? null, limit: opts.limit ?? null }],
    queryFn: () => api.get<Page<StockDocumentSummary>>(`/inventory/documents${query ? `?${query}` : ""}`),
  });
}

export function useStockDocument(documentId: number | null) {
  return useQuery({
    queryKey: [ROOT, "documents", documentId],
    queryFn: () => api.get<StockDocument>(`/inventory/documents/${documentId}`),
    enabled: documentId !== null,
  });
}

// --- Warehouse transfers ------------------------------------------------------------------

export function usePostTransfer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ payload, idempotencyKey }: { payload: TransferPayload; idempotencyKey: string }) =>
      api.post<Transfer>("/inventory/transfers", payload, { "Idempotency-Key": idempotencyKey }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useReceiveTransfer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      transferId,
      receiveDate,
      idempotencyKey,
    }: {
      transferId: number;
      receiveDate?: string | null;
      idempotencyKey: string;
    }) =>
      api.post<Transfer>(
        `/inventory/transfers/${transferId}/receive`,
        { receive_date: receiveDate ?? null },
        { "Idempotency-Key": idempotencyKey },
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useCancelTransfer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      transferId,
      reason,
      idempotencyKey,
    }: {
      transferId: number;
      reason: string;
      idempotencyKey: string;
    }) =>
      api.post<Transfer>(
        `/inventory/transfers/${transferId}/cancel`,
        { reason },
        { "Idempotency-Key": idempotencyKey },
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useTransfers(opts: { status?: string; warehouseId?: number; limit?: number } = {}) {
  const params = new URLSearchParams();
  if (opts.status) params.set("status", opts.status);
  if (opts.warehouseId) params.set("warehouse_id", String(opts.warehouseId));
  if (opts.limit) params.set("limit", String(opts.limit));
  const query = params.toString();
  return useQuery({
    queryKey: [ROOT, "transfers", { status: opts.status ?? null, warehouseId: opts.warehouseId ?? null }],
    queryFn: () => api.get<Page<TransferSummary>>(`/inventory/transfers${query ? `?${query}` : ""}`),
  });
}

export function useTransfer(transferId: number | null) {
  return useQuery({
    queryKey: [ROOT, "transfers", transferId],
    queryFn: () => api.get<Transfer>(`/inventory/transfers/${transferId}`),
    enabled: transferId !== null,
  });
}

// --- Stock counts -------------------------------------------------------------------------

export function useOpenCountSession() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: CountSessionPayload) => api.post<CountSession>("/inventory/counts", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useCountSessions(opts: { status?: string; warehouseId?: number } = {}) {
  const params = new URLSearchParams();
  if (opts.status) params.set("status", opts.status);
  if (opts.warehouseId) params.set("warehouse_id", String(opts.warehouseId));
  const query = params.toString();
  return useQuery({
    queryKey: [ROOT, "counts", { status: opts.status ?? null, warehouseId: opts.warehouseId ?? null }],
    queryFn: () => api.get<Page<CountSessionSummary>>(`/inventory/counts${query ? `?${query}` : ""}`),
  });
}

export function useCountSession(sessionId: number | null) {
  return useQuery({
    queryKey: [ROOT, "counts", sessionId],
    queryFn: () => api.get<CountSession>(`/inventory/counts/${sessionId}`),
    enabled: sessionId !== null,
  });
}

export function useAddCountLine() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, itemId }: { sessionId: number; itemId: number }) =>
      api.post<CountLine>(`/inventory/counts/${sessionId}/lines`, { item_id: itemId }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT, "counts"] }),
  });
}

export function useEnterCount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      sessionId,
      lineId,
      payload,
    }: {
      sessionId: number;
      lineId: number;
      payload: CountLineEntryPayload;
    }) => api.patch<CountLine>(`/inventory/counts/${sessionId}/lines/${lineId}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT, "counts"] }),
  });
}

export function useResnapshotCountLine() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, lineId }: { sessionId: number; lineId: number }) =>
      api.post<CountLine>(`/inventory/counts/${sessionId}/lines/${lineId}/resnapshot`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT, "counts"] }),
  });
}

/** The posting before Process — from the server, exactly as it will post, never re-derived. */
export function useCountPreview(sessionId: number | null) {
  return useQuery({
    queryKey: [ROOT, "counts", sessionId, "preview"],
    queryFn: () => api.get<CountPreview>(`/inventory/counts/${sessionId}/preview`),
    enabled: sessionId !== null,
  });
}

export function useProcessCount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, idempotencyKey }: { sessionId: number; idempotencyKey: string }) =>
      api.post<CountProcessResult>(
        `/inventory/counts/${sessionId}/process`,
        { session_id: sessionId },
        { "Idempotency-Key": idempotencyKey },
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useCancelCount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, reason }: { sessionId: number; reason: string }) =>
      api.post<CountSessionSummary>(`/inventory/counts/${sessionId}/cancel`, { reason }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT, "counts"] }),
  });
}

// --- On hand ------------------------------------------------------------------------------

/** Quantity on hand per item at one warehouse — the figure beside each option in the item
 * typeahead. One request for the warehouse, refetched on every posting through `ROOT`. */
export function useOnHand(warehouseId: number | null) {
  return useQuery({
    queryKey: [ROOT, "on-hand", warehouseId],
    queryFn: () => api.get<OnHandRow[]>(`/inventory/on-hand?warehouse_id=${warehouseId}`),
    enabled: warehouseId !== null,
    staleTime: 10_000,
  });
}

// --- Enquiry and reports (P5 step 8) ------------------------------------------------------

/** Drops the params a screen left unset, so the URL carries only what the endpoint should
 * read and two screens that differ by an untouched filter share a cache key. `false` is
 * dropped with the empties because every boolean here is a flag whose default is off. */
function queryString(
  params: Record<string, string | number | boolean | null | undefined>,
): string {
  const out = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "" || value === false) continue;
    out.set(key, String(value));
  }
  const query = out.toString();
  return query ? `?${query}` : "";
}

export interface ItemEnquiryParams {
  asOf?: string;
  dateFrom?: string;
  warehouseId?: number | null;
  provisionalOnly?: boolean;
  includeZeroLocations?: boolean;
  cursor?: number | null;
}

export function useItemEnquiry(itemId: number | null, params: ItemEnquiryParams = {}) {
  const query = queryString({
    as_of: params.asOf,
    date_from: params.dateFrom,
    warehouse_id: params.warehouseId,
    provisional_only: params.provisionalOnly,
    include_zero_locations: params.includeZeroLocations,
    cursor: params.cursor,
  });
  return useQuery({
    queryKey: [ROOT, "enquiry", itemId, query],
    queryFn: () => api.get<ItemEnquiry>(`/inventory/items/${itemId}/enquiry${query}`),
    enabled: itemId !== null,
  });
}

export interface MovementReportParams {
  dateFrom: string;
  dateTo: string;
  warehouseId?: number | null;
  branchId?: number | null;
  itemId?: number | null;
  includeZero?: boolean;
  cursor?: number | null;
}

/** `null` params means the screen has not got a date range yet — the endpoint requires both,
 * so the query stays disabled rather than firing a 422 the user would see as an error. */
export function useMovementReport(params: MovementReportParams | null) {
  const query = params
    ? queryString({
        date_from: params.dateFrom,
        date_to: params.dateTo,
        warehouse_id: params.warehouseId,
        branch_id: params.branchId,
        item_id: params.itemId,
        include_zero: params.includeZero,
        cursor: params.cursor,
      })
    : "";
  return useQuery({
    queryKey: [ROOT, "reports", "movement", query],
    queryFn: () => api.get<MovementReport>(`/inventory/reports/movement${query}`),
    enabled: params !== null,
  });
}

export interface TransactionReportParams {
  dateFrom: string;
  dateTo: string;
  itemId?: number | null;
  warehouseId?: number | null;
  branchId?: number | null;
  transactionTypeId?: number | null;
  projectId?: number | null;
  provisionalOnly?: boolean;
  cursor?: number | null;
}

export function useTransactionReport(params: TransactionReportParams | null) {
  const query = params
    ? queryString({
        date_from: params.dateFrom,
        date_to: params.dateTo,
        item_id: params.itemId,
        warehouse_id: params.warehouseId,
        branch_id: params.branchId,
        transaction_type_id: params.transactionTypeId,
        project_id: params.projectId,
        provisional_only: params.provisionalOnly,
        cursor: params.cursor,
      })
    : "";
  return useQuery({
    queryKey: [ROOT, "reports", "transactions", query],
    queryFn: () => api.get<TransactionReport>(`/inventory/reports/transactions${query}`),
    enabled: params !== null,
  });
}

export interface ValuationReportParams {
  asOf?: string;
  warehouseId?: number | null;
  branchId?: number | null;
  itemId?: number | null;
  includeZero?: boolean;
  cursor?: number | null;
}

export function useValuationReport(params: ValuationReportParams = {}) {
  const query = queryString({
    as_of: params.asOf,
    warehouse_id: params.warehouseId,
    branch_id: params.branchId,
    item_id: params.itemId,
    include_zero: params.includeZero,
    cursor: params.cursor,
  });
  return useQuery({
    queryKey: [ROOT, "reports", "valuation", query],
    queryFn: () => api.get<ValuationReport>(`/inventory/reports/valuation${query}`),
  });
}

export interface CountReportParams {
  status?: string | null;
  warehouseId?: number | null;
  dateFrom?: string;
  dateTo?: string;
  variancesOnly?: boolean;
  cursor?: number | null;
}

export function useCountReport(params: CountReportParams = {}) {
  const query = queryString({
    status: params.status,
    warehouse_id: params.warehouseId,
    date_from: params.dateFrom,
    date_to: params.dateTo,
    variances_only: params.variancesOnly,
    cursor: params.cursor,
  });
  return useQuery({
    queryKey: [ROOT, "reports", "counts", query],
    queryFn: () => api.get<CountReport>(`/inventory/reports/counts${query}`),
  });
}
