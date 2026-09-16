"use client";

import { useMemo } from "react";
import { useQueries } from "@tanstack/react-query";
import type { SelectOption } from "@/design/components/select";
import type { LineGridRow } from "@/design/components/line-grid";
import { useTransactionTypes } from "@/features/gl/hooks";
import type { TransactionType } from "@/features/gl/types";
import { byId } from "@/features/gl/lookups";
import type { InventoryTransactionKind } from "@/lib/api-enums";
import { api } from "@/lib/api";
import { dotted, formatQuantity, trimDecimalString } from "@/lib/format";
import { useBarcodes, useItems, useUomCategories, useWarehouses } from "./hooks";
import { ItemType } from "@/lib/api-enums";
import type { Item, OnHandRow, Uom } from "./types";

/**
 * What every inventory transaction grid needs to fill its cells — the stock items with
 * their barcodes as typeahead keywords, the warehouses a document may name, the module's
 * transaction types keyed by kind, and each item's units with the conversion to base.
 *
 * One hook rather than five per screen so that the adjustment, the batch, the transfer and
 * the count sheet resolve an item, a unit and a quantity the same way. The conversion string
 * ("6 × BOX12 = 72 EA") and the on-hand figure are formatted here, at the item's base-unit
 * decimals, because the P5 step-6 review made that formatting load-bearing: a quantity read
 * off any of these screens is asserted as the rendered string.
 */
export function useInventoryLineSupport(
  opts: {
    includeInactiveItems?: boolean;
    includeInTransitWarehouses?: boolean;
    includeNonStockItems?: boolean;
  } = {},
) {
  // The first two flags exist for the reports, and neither reaches a picker: `stockItems`
  // still filters to the active items and `warehouseOptions` still drops the in-transit
  // location, so no document gains an option decision 6 says it must not offer. What the
  // reports need is the *lookup* — a row naming a deactivated item prints its quantity at the
  // wrong scale, and stock in transit is a line on the valuation report with a warehouse code
  // to resolve.
  //
  // `includeNonStockItems` is the one that **does** reach a picker, and only the enquiry's.
  // An enquiry is a read: asking what a kit or a service item holds is a fair question with a
  // real answer, and the answer happens to be "nothing, and here is why" (P6 decision 8). A
  // document grid must keep offering stock items alone — there is nothing to adjust, transfer
  // or count about a kit — which is why this is a flag rather than a widening of the set.
  const items = useItems({ includeInactive: opts.includeInactiveItems });
  const barcodes = useBarcodes();
  const warehouses = useWarehouses({ includeInTransit: opts.includeInTransitWarehouses });
  const categories = useUomCategories();
  const types = useTransactionTypes("inv");

  const stockItems = useMemo(
    () => (items.data ?? []).filter((item) => item.item_type === ItemType.STOCK && item.is_active),
    [items.data],
  );
  /** What `itemOptions()` offers — stock items, plus the rest when the caller asked for them. */
  const pickableItems = useMemo(
    () =>
      opts.includeNonStockItems
        ? (items.data ?? []).filter((item) => item.is_active)
        : stockItems,
    [items.data, opts.includeNonStockItems, stockItems],
  );
  const itemById = useMemo(() => byId(items.data), [items.data]);
  const uomById = useMemo(() => {
    const out = new Map<number, Uom>();
    for (const category of categories.data ?? []) {
      for (const uom of category.uoms) out.set(uom.id, uom);
    }
    return out;
  }, [categories.data]);
  const typeById = useMemo(() => byId(types.data), [types.data]);

  /** Barcodes and the description ride along as keywords, so typing either finds the item —
   * the label stays "code · name", what the row will read back. */
  const keywordsByItem = useMemo(() => {
    const out = new Map<number, string[]>();
    for (const row of barcodes.data?.items ?? []) {
      out.set(row.item_id, [...(out.get(row.item_id) ?? []), row.barcode]);
    }
    return out;
  }, [barcodes.data]);

  const warehouseOptions: SelectOption[] = useMemo(
    () =>
      (warehouses.data ?? [])
        .filter((w) => w.is_active && !w.is_in_transit)
        .map((w) => ({ value: String(w.id), label: dotted(w.code, w.name) })),
    [warehouses.data],
  );

  const typeOptions: SelectOption[] = useMemo(
    () =>
      (types.data ?? [])
        .filter((tt) => tt.is_active)
        .map((tt) => ({ value: String(tt.id), label: dotted(tt.code, tt.name) })),
    [types.data],
  );

  function itemOptions(onHand?: Map<number, OnHandRow>): SelectOption[] {
    return pickableItems.map((item) => {
      const base = uomById.get(item.base_uom_id);
      const held = onHand?.get(item.id);
      const suffix =
        held && base ? ` — ${formatQuantity(Number(held.quantity), base.decimal_places)} ${base.code}` : "";
      return {
        value: String(item.id),
        label: `${dotted(item.code, item.name)}${suffix}`,
        keywords: [...(keywordsByItem.get(item.id) ?? []), ...(item.description ? [item.description] : [])],
      };
    });
  }

  function uomsFor(item: Item | undefined): Uom[] {
    if (!item) return [];
    const category = (categories.data ?? []).find((c) => c.id === item.uom_category_id);
    return (category?.uoms ?? []).filter((u) => u.is_active);
  }

  function uomOptionsFor(row: LineGridRow): SelectOption[] {
    return uomsFor(itemById.get(Number(row.itemId))).map((u) => ({
      value: String(u.id),
      label: dotted(u.code, u.name),
    }));
  }

  /** The unit the line will post in: the picked one, else the item's base unit. */
  function uomOf(row: LineGridRow): Uom | undefined {
    const item = itemById.get(Number(row.itemId));
    if (!item) return undefined;
    return uomById.get(row.uomId ? Number(row.uomId) : item.base_uom_id);
  }

  function baseUomOf(row: LineGridRow): Uom | undefined {
    const item = itemById.get(Number(row.itemId));
    return item ? uomById.get(item.base_uom_id) : undefined;
  }

  /** "6 × BOX12 = 72 EA" — shown only when the unit is not the base one, because
   * "6 × EA = 6 EA" says nothing. */
  function conversionFor(row: LineGridRow): string | undefined {
    const uom = uomOf(row);
    const base = baseUomOf(row);
    if (!uom || !base || uom.is_base || !row.quantity) return undefined;
    const quantity = Number(row.quantity);
    if (!Number.isFinite(quantity)) return undefined;
    const factor = Number(uom.factor_to_base);
    return `${formatQuantity(quantity, uom.decimal_places)} × ${uom.code} = ${formatQuantity(
      quantity * factor,
      base.decimal_places,
    )} ${base.code}`;
  }

  function quantityInBase(row: LineGridRow): number {
    const uom = uomOf(row);
    const quantity = Number(row.quantity);
    if (!uom || !Number.isFinite(quantity)) return 0;
    return quantity * Number(uom.factor_to_base);
  }

  function kindOf(typeId: string | number | null | undefined): InventoryTransactionKind | null {
    if (!typeId) return null;
    return typeById.get(Number(typeId))?.kind ?? null;
  }

  function typeByKind(kind: InventoryTransactionKind): TransactionType | undefined {
    return (types.data ?? []).find((tt) => tt.is_active && tt.kind === kind);
  }

  /** A quantity at an item's base-unit decimals — the way every figure on these screens is
   * printed. `"12.000000"` reads "12" for a Count item and "0.500" for a Weight one. */
  function formatBase(itemId: number, quantity: string | number): string {
    const item = itemById.get(itemId);
    const base = item ? uomById.get(item.base_uom_id) : undefined;
    return base ? formatQuantity(Number(quantity), base.decimal_places) : trimDecimalString(String(quantity));
  }

  /** The same figure with its unit — "2.500 KG". On a report that mixes items the unit is
   * not decoration: 2.5 of one item and 2.500 of another are two different scales, and the
   * column is unreadable without saying which. */
  function formatBaseWithUnit(itemId: number, quantity: string | number): string {
    const item = itemById.get(itemId);
    const base = item ? uomById.get(item.base_uom_id) : undefined;
    if (!base) return formatBase(itemId, quantity);
    return `${formatQuantity(Number(quantity), base.decimal_places)} ${base.code}`;
  }

  return {
    isLoading: items.isLoading || warehouses.isLoading || categories.isLoading || types.isLoading,
    stockItems,
    pickableItems,
    itemById,
    uomById,
    typeById,
    warehouses: warehouses.data ?? [],
    types: types.data ?? [],
    warehouseOptions,
    typeOptions,
    itemOptions,
    uomOptionsFor,
    uomOf,
    baseUomOf,
    conversionFor,
    quantityInBase,
    kindOf,
    typeByKind,
    formatBase,
    formatBaseWithUnit,
  };
}

/** On-hand rows for every warehouse a grid currently names — a batch may spread its lines
 * across several. One query per warehouse, cached under the inventory root so a posting
 * refreshes them all. */
export function useOnHandByWarehouse(warehouseIds: number[]) {
  const unique = useMemo(() => [...new Set(warehouseIds.filter((id) => id > 0))].sort(), [warehouseIds]);
  const results = useQueries({
    queries: unique.map((warehouseId) => ({
      queryKey: ["inventory", "on-hand", warehouseId],
      queryFn: () => api.get<OnHandRow[]>(`/inventory/on-hand?warehouse_id=${warehouseId}`),
      staleTime: 10_000,
    })),
  });
  return useMemo(() => {
    const out = new Map<number, Map<number, OnHandRow>>();
    results.forEach((result, index) => {
      if (result.data) out.set(unique[index], new Map(result.data.map((row) => [row.item_id, row])));
    });
    return out;
  }, [results, unique]);
}
