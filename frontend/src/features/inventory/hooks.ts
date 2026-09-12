"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type {
  Barcode,
  BarcodeCreatePayload,
  BarcodeListing,
  BarcodeUpdatePayload,
  InventoryDefaults,
  InventoryDefaultsPayload,
  Item,
  ItemAuditRecord,
  ItemCreatePayload,
  ItemUpdatePayload,
  Page,
  Uom,
  UomCategoryCreatePayload,
  UomCategoryUpdatePayload,
  UomCategoryWithUnits,
  UomCreatePayload,
  UomUpdatePayload,
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

/** The company-wide listing behind the Variable barcodes screen. */
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
