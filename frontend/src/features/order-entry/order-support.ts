"use client";

import { useMemo } from "react";
import type { SelectOption } from "@/design/components/select";
import type { LineGridRow } from "@/design/components/line-grid";
import { useCurrencies, useTaxCodes } from "@/features/gl/hooks";
import { byId } from "@/features/gl/lookups";
import { useItems, useUomCategories, useWarehouses } from "@/features/inventory/hooks";
import type { Item, Uom } from "@/features/inventory/types";
import { ItemType } from "@/lib/api-enums";
import { dotted, formatQuantity, trimDecimalString } from "@/lib/format";

/**
 * A quantity that is **not** one item's — the order-level backorder, which the endpoint sums
 * in base units across lines that may be counted in different ones. There is no single unit to
 * take decimals from, so it is rendered to the decimals the figure itself carries: 40 reads
 * `40`, 0.75 reads `0.75`. Inventing a scale either way would print a precision the number does
 * not have, or hide one it does.
 */
export function formatOrderQuantity(value: string | number): string {
  const text = trimDecimalString(String(value));
  const decimals = text.includes(".") ? text.split(".")[1].length : 0;
  return formatQuantity(Number(value), decimals);
}

/**
 * What an **order** grid needs to fill its cells, next to `useInventoryLineSupport` rather
 * than inside it.
 *
 * The difference is the item set, and it is the whole reason this exists: an inventory
 * document moves stock, so its picker offers stock items and nothing else. An order is a
 * commitment about anything sellable or buyable — a service line is received by its invoice
 * (decision 4) and a **kit** is sold as one line that explodes into components (decision 8),
 * and neither ever reaches `stock_moves`. Filtering those out of an order grid would leave
 * the operator unable to order half the catalogue; filtering them *in* to the inventory grid
 * would offer an adjustment of something that cannot be adjusted. Two sets, one set of
 * lookups.
 */
export function useOrderLineSupport(opts: { role?: "sales" | "purchase" } = {}) {
  const role = opts.role ?? "sales";
  const items = useItems({});
  const warehouses = useWarehouses();
  const categories = useUomCategories();
  const taxCodes = useTaxCodes();
  const currencies = useCurrencies();

  const itemById = useMemo(() => byId(items.data), [items.data]);
  const uomById = useMemo(() => {
    const out = new Map<number, Uom>();
    for (const category of categories.data ?? []) {
      for (const uom of category.uoms) out.set(uom.id, uom);
    }
    return out;
  }, [categories.data]);

  /** A kit is never purchasable — `kit_not_purchasable` refuses it on the AP side, so the
   * purchase grid does not offer one (decision 8). */
  const orderItems = useMemo(
    () =>
      (items.data ?? []).filter(
        (item) => item.is_active && (role === "sales" || item.item_type !== ItemType.KIT),
      ),
    [items.data, role],
  );

  const itemOptions: SelectOption[] = useMemo(
    () =>
      orderItems.map((item) => ({
        value: String(item.id),
        label: dotted(item.code, item.name),
        keywords: item.description ? [item.description] : [],
      })),
    [orderItems],
  );

  const warehouseOptions: SelectOption[] = useMemo(
    () =>
      (warehouses.data ?? [])
        .filter((w) => w.is_active && !w.is_in_transit)
        .map((w) => ({ value: String(w.id), label: dotted(w.code, w.name) })),
    [warehouses.data],
  );

  const taxCodeOptions: SelectOption[] = useMemo(
    () =>
      (taxCodes.data ?? [])
        .filter((code) => code.is_active)
        .map((code) => ({ value: String(code.id), label: dotted(code.code, code.name) })),
    [taxCodes.data],
  );

  const currencyOptions: SelectOption[] = useMemo(
    () =>
      (currencies.data ?? []).map((currency) => ({
        value: String(currency.id),
        label: dotted(currency.code, currency.name),
      })),
    [currencies.data],
  );

  const baseCurrency = useMemo(
    () => (currencies.data ?? []).find((currency) => currency.is_base),
    [currencies.data],
  );

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

  function uomOf(row: LineGridRow): Uom | undefined {
    const item = itemById.get(Number(row.itemId));
    if (!item) return undefined;
    return uomById.get(row.uomId ? Number(row.uomId) : item.base_uom_id);
  }

  /** "6 × BOX12 = 72 EA", shown only when the unit is not the base one. */
  function conversionFor(row: LineGridRow): string | undefined {
    const item = itemById.get(Number(row.itemId));
    const uom = uomOf(row);
    const base = item ? uomById.get(item.base_uom_id) : undefined;
    if (!uom || !base || uom.is_base || !row.quantity) return undefined;
    const quantity = Number(row.quantity);
    if (!Number.isFinite(quantity)) return undefined;
    return `${formatQuantity(quantity, uom.decimal_places)} × ${uom.code} = ${formatQuantity(
      quantity * Number(uom.factor_to_base),
      base.decimal_places,
    )} ${base.code}`;
  }

/** The decimals a quantity of this item is counted to — its **unit's**, never a guess. */
  function quantityDecimals(itemId: number | string | null | undefined): number {
    const item = itemById.get(Number(itemId));
    const uom = item ? uomById.get(item.base_uom_id) : undefined;
    return uom?.decimal_places ?? 0;
  }

  function itemLabel(itemId: number | string | null | undefined): string {
    const item = itemById.get(Number(itemId));
    return item ? dotted(item.code, item.name) : "";
  }

  function isKit(itemId: number | string | null | undefined): boolean {
    return itemById.get(Number(itemId))?.item_type === ItemType.KIT;
  }

  /** The selling price the item carries, trimmed for an editable cell. */
  function sellingPrice(itemId: number | string | null | undefined): string {
    const item = itemById.get(Number(itemId));
    return item ? trimDecimalString(item.selling_price) : "";
  }

  function defaultTaxCode(itemId: number | string | null | undefined): string {
    const item = itemById.get(Number(itemId));
    const code =
      role === "sales" ? item?.default_sales_tax_code_id : item?.default_purchase_tax_code_id;
    return code ? String(code) : "";
  }

  return {
    isLoading: items.isLoading || categories.isLoading || warehouses.isLoading,
    items: items.data ?? [],
    itemById,
    uomById,
    warehouses: warehouses.data ?? [],
    currencies: currencies.data ?? [],
    baseCurrency,
    itemOptions,
    warehouseOptions,
    taxCodeOptions,
    currencyOptions,
    uomOptionsFor,
    uomOf,
    conversionFor,
    quantityDecimals,
    itemLabel,
    isKit,
    sellingPrice,
    defaultTaxCode,
  };
}
