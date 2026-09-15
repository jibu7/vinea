"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Page } from "@/features/inventory/types";
import type {
  BreakupPayload,
  Grn,
  GrnListing,
  GrnPayload,
  GrnReversePayload,
  LandedCost,
  LandedCostPayload,
  LandedCostPreview,
  LandedCostPreviewPayload,
  LandedCostReversePayload,
  LandedCostSummary,
  OrderDefaults,
  OrderDefaultsPayload,
  OrderTransitionPayload,
  PreparedDocument,
  PreparedGrn,
  PurchaseOrder,
  PurchaseOrderPayload,
  PurchaseOrderSummary,
  SalesOrder,
  SalesOrderPayload,
  SalesOrderSummary,
} from "./types";

/** One root key for the module, so a write invalidates everything order entry derives from
 * these keys — the same shape `inventory` and `gl` use. */
const ROOT = "order-entry";

// --- Defaults (P6 decision 10) -------------------------------------------------------------

export function useOrderDefaults() {
  return useQuery({
    queryKey: [ROOT, "defaults"],
    queryFn: () => api.get<OrderDefaults>("/oe/defaults"),
    staleTime: 60_000,
  });
}

/** A **PUT**, not a PATCH: `/oe/defaults` takes the settings the screen is showing and the
 * service writes only the keys present in the body. */
export function useSaveOrderDefaults() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: OrderDefaultsPayload) =>
      api.put<OrderDefaults>("/oe/defaults", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

// --- Sales orders (P6 decision 3) -----------------------------------------------------------

export interface OrderListParams {
  partnerId?: number;
  status?: string;
  dateFrom?: string;
  dateTo?: string;
  limit?: number;
  cursor?: number;
}

function orderQuery(opts: OrderListParams): string {
  const params = new URLSearchParams();
  if (opts.partnerId) params.set("partner_id", String(opts.partnerId));
  if (opts.status) params.set("status", opts.status);
  if (opts.dateFrom) params.set("date_from", opts.dateFrom);
  if (opts.dateTo) params.set("date_to", opts.dateTo);
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.cursor) params.set("cursor", String(opts.cursor));
  const query = params.toString();
  return query ? `?${query}` : "";
}

export function useSalesOrders(opts: OrderListParams = {}) {
  return useQuery({
    queryKey: [ROOT, "sales-orders", opts],
    queryFn: () => api.get<Page<SalesOrderSummary>>(`/oe/sales-orders${orderQuery(opts)}`),
  });
}

export function useSalesOrder(orderId: number | null) {
  return useQuery({
    queryKey: [ROOT, "sales-orders", orderId],
    queryFn: () => api.get<SalesOrder>(`/oe/sales-orders/${orderId}`),
    enabled: orderId !== null,
  });
}

/** `Idempotency-Key` is the draft's UUID, so a retried create replays instead of claiming a
 * second `SO-` number (decision 12). */
export function useCreateSalesOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      payload,
      idempotencyKey,
    }: {
      payload: SalesOrderPayload;
      idempotencyKey: string;
    }) => api.post<SalesOrder>("/oe/sales-orders", payload, { "Idempotency-Key": idempotencyKey }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useUpdateSalesOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ orderId, payload }: { orderId: number; payload: SalesOrderPayload }) =>
      api.put<SalesOrder>(`/oe/sales-orders/${orderId}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/** Close cancels the remaining quantities and keeps the history; Cancel is only open while
 * nothing has been fulfilled. Neither posts anything (decision 3). */
export function useCloseSalesOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ orderId, payload }: { orderId: number; payload: OrderTransitionPayload }) =>
      api.post<SalesOrder>(`/oe/sales-orders/${orderId}/close`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useCancelSalesOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ orderId, payload }: { orderId: number; payload: OrderTransitionPayload }) =>
      api.post<SalesOrder>(`/oe/sales-orders/${orderId}/cancel`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/** Breakup edits one kit line's explosion — what ships in *this* order's box. A PUT, because
 * the explosion is one fact (decision 8). */
export function useSaveBreakup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      orderId,
      lineId,
      payload,
    }: {
      orderId: number;
      lineId: number;
      payload: BreakupPayload;
    }) => api.put<SalesOrder>(`/oe/sales-orders/${orderId}/lines/${lineId}/breakup`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/** Prepares an AR invoice from the order's open lines and **posts nothing**: the prepared
 * document goes back through the endpoint that posts invoices, which is where the
 * over-fulfilment guard lives (decision 7). */
export function useInvoiceFromSalesOrder() {
  return useMutation({
    mutationFn: (orderId: number) =>
      api.post<PreparedDocument>(`/oe/sales-orders/${orderId}/invoice`),
  });
}

// --- Purchase orders -------------------------------------------------------------------------

export function usePurchaseOrders(opts: OrderListParams = {}) {
  return useQuery({
    queryKey: [ROOT, "purchase-orders", opts],
    queryFn: () => api.get<Page<PurchaseOrderSummary>>(`/oe/purchase-orders${orderQuery(opts)}`),
  });
}

export function usePurchaseOrder(orderId: number | null) {
  return useQuery({
    queryKey: [ROOT, "purchase-orders", orderId],
    queryFn: () => api.get<PurchaseOrder>(`/oe/purchase-orders/${orderId}`),
    enabled: orderId !== null,
  });
}

export function useCreatePurchaseOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      payload,
      idempotencyKey,
    }: {
      payload: PurchaseOrderPayload;
      idempotencyKey: string;
    }) =>
      api.post<PurchaseOrder>("/oe/purchase-orders", payload, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useUpdatePurchaseOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ orderId, payload }: { orderId: number; payload: PurchaseOrderPayload }) =>
      api.put<PurchaseOrder>(`/oe/purchase-orders/${orderId}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useClosePurchaseOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ orderId, payload }: { orderId: number; payload: OrderTransitionPayload }) =>
      api.post<PurchaseOrder>(`/oe/purchase-orders/${orderId}/close`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useCancelPurchaseOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ orderId, payload }: { orderId: number; payload: OrderTransitionPayload }) =>
      api.post<PurchaseOrder>(`/oe/purchase-orders/${orderId}/cancel`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/** Builds a receipt from the order's open **stock** lines. Prepares; posts nothing. */
export function useReceiveFromPurchaseOrder() {
  return useMutation({
    mutationFn: (orderId: number) =>
      api.post<PreparedGrn>(`/oe/purchase-orders/${orderId}/receive`),
  });
}

/** A service line is received by its invoice — there is no GRN for it (decision 4). */
export function useProcessInvoiceFromPurchaseOrder() {
  return useMutation({
    mutationFn: (orderId: number) =>
      api.post<PreparedDocument>(`/oe/purchase-orders/${orderId}/process-invoice`),
  });
}

// --- Goods receipts (P6 decision 6) -----------------------------------------------------------

export interface GrnListParams {
  partnerId?: number;
  status?: string;
  warehouseId?: number;
  dateFrom?: string;
  dateTo?: string;
  limit?: number;
  cursor?: number;
}

export function useGoodsReceived(opts: GrnListParams = {}) {
  const params = new URLSearchParams();
  if (opts.partnerId) params.set("partner_id", String(opts.partnerId));
  if (opts.status) params.set("status", opts.status);
  if (opts.warehouseId) params.set("warehouse_id", String(opts.warehouseId));
  if (opts.dateFrom) params.set("date_from", opts.dateFrom);
  if (opts.dateTo) params.set("date_to", opts.dateTo);
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.cursor) params.set("cursor", String(opts.cursor));
  const query = params.toString();
  return useQuery({
    queryKey: [ROOT, "goods-received", opts],
    queryFn: () => api.get<GrnListing>(`/oe/goods-received${query ? `?${query}` : ""}`),
  });
}

export function useGrn(grnId: number | null) {
  return useQuery({
    queryKey: [ROOT, "goods-received-notes", grnId],
    queryFn: () => api.get<Grn>(`/oe/goods-received-notes/${grnId}`),
    enabled: grnId !== null,
  });
}

export function useCreateGrn() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ payload, idempotencyKey }: { payload: GrnPayload; idempotencyKey: string }) =>
      api.post<Grn>("/oe/goods-received-notes", payload, { "Idempotency-Key": idempotencyKey }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/** A receipt with any matched quantity refuses reversal (`grn_matched`) — reverse the invoice
 * first. An unmatched one reverses under the negative-stock policy.
 *
 * A reversal **posts**, so it carries an `Idempotency-Key` like every other posting endpoint:
 * a retried reversal replays rather than taking the goods out twice. */
export function useReverseGrn() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      grnId,
      payload,
      idempotencyKey,
    }: {
      grnId: number;
      payload: GrnReversePayload;
      idempotencyKey: string;
    }) =>
      api.post<Grn>(`/oe/goods-received-notes/${grnId}/reverse`, payload, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/** Opens the supplier invoice in matching mode: one prepared line per unmatched receipt line. */
export function useProcessInvoiceFromGrn() {
  return useMutation({
    mutationFn: (grnId: number) =>
      api.post<PreparedDocument>(`/oe/goods-received-notes/${grnId}/process-invoice`),
  });
}

// --- Landed cost (P6 decision 9) ---------------------------------------------------------------

export function useLandedCosts(opts: { status?: string; limit?: number } = {}) {
  const params = new URLSearchParams();
  if (opts.status) params.set("status", opts.status);
  if (opts.limit) params.set("limit", String(opts.limit));
  const query = params.toString();
  return useQuery({
    queryKey: [ROOT, "landed-costs", opts],
    queryFn: () => api.get<Page<LandedCostSummary>>(`/oe/landed-costs${query ? `?${query}` : ""}`),
  });
}

export function useLandedCost(documentId: number | null) {
  return useQuery({
    queryKey: [ROOT, "landed-costs", documentId],
    queryFn: () => api.get<LandedCost>(`/oe/landed-costs/${documentId}`),
    enabled: documentId !== null,
  });
}

/** The shares this amount *would* take, shown before anything is committed. The same function
 * computes these and posts them, so what is previewed is what is written. A POST because the
 * target list is a body and the answer depends on today's stock position — it writes nothing. */
export function useLandedCostPreview() {
  return useMutation({
    mutationFn: (payload: LandedCostPreviewPayload) =>
      api.post<LandedCostPreview>("/oe/landed-costs/preview", payload),
  });
}

export function useCreateLandedCost() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      payload,
      idempotencyKey,
    }: {
      payload: LandedCostPayload;
      idempotencyKey: string;
    }) =>
      api.post<LandedCost>("/oe/landed-costs", payload, { "Idempotency-Key": idempotencyKey }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useReverseLandedCost() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      documentId,
      payload,
      idempotencyKey,
    }: {
      documentId: number;
      payload: LandedCostReversePayload;
      idempotencyKey: string;
    }) =>
      api.post<LandedCost>(`/oe/landed-costs/${documentId}/reverse`, payload, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}
