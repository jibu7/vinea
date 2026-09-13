"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { ChevronsUpDown } from "lucide-react";
import { cn } from "@/lib/cn";
import { InventoryTransactionKind } from "@/lib/api-enums";
import { Combobox } from "./combobox";
import type { SelectOption } from "./select";

export interface LineGridRow {
  id: string;
  accountId: string;
  description: string;
  /** Journal mode. */
  debit: string;
  credit: string;
  /** Cashbook mode. */
  amount: string;
  taxInclusive: boolean;
  /** Document mode (AR/AP invoice and credit-note lines, P4 decision 11). Item lines arrive
   * with inventory in P5/P6 — this row shape is meant to grow an `itemId`, not be replaced. */
  quantity: string;
  unitPrice: string;
  discountPercent: string;
  /** Batch mode (AR/AP journal batches): one partner per line, charged or credited. */
  partnerId: string;
  /** Inventory mode (P5 step 7): the stock line. `quantity` above is reused as a magnitude —
   * the transaction type's kind gives the direction. `accountId` above is the contra
   * override. Transfers use the same mode with the warehouse/type/cost columns switched off. */
  itemId: string;
  warehouseId: string;
  transactionTypeId: string;
  uomId: string;
  unitCost: string;
  /** A revaluation's signed value; nothing else fills it. */
  value: string;
  /** Shared extra dimensions — collapsible, all default from the header. */
  branchId: string;
  projectId: string;
  currencyId: string;
  exchangeRate: string;
  taxCodeId: string;
}

export type LineErrors = Record<number, Record<string, string>>;

export function emptyLineGridRow(defaults: Partial<LineGridRow> = {}): LineGridRow {
  return {
    id: crypto.randomUUID(),
    accountId: "",
    description: "",
    debit: "",
    credit: "",
    amount: "",
    taxInclusive: true,
    quantity: "1",
    unitPrice: "",
    discountPercent: "",
    partnerId: "",
    itemId: "",
    warehouseId: "",
    transactionTypeId: "",
    uomId: "",
    unitCost: "",
    value: "",
    branchId: "",
    projectId: "",
    currencyId: "",
    exchangeRate: "",
    taxCodeId: "",
    ...defaults,
  };
}

/** Raw digits while editing; thousand-separated once the cell loses focus. */
function displayAmount(raw: string, editing?: boolean): string {
  if (editing || !raw) return raw;
  const n = Number(raw);
  return Number.isFinite(n) ? new Intl.NumberFormat("en-RW").format(n) : raw;
}

/** Net of discount, before tax — the figure the line contributes to the Exclusive footer. */
export function lineNet(row: LineGridRow): number {
  const quantity = Number(row.quantity || 0);
  const price = Number(row.unitPrice || 0);
  const discount = Number(row.discountPercent || 0);
  const gross = quantity * price;
  if (!Number.isFinite(gross)) return 0;
  return gross * (1 - (Number.isFinite(discount) ? discount : 0) / 100);
}

/**
 * Cell coordinates for the keyboard model. These are the ids `focusCell` looks up via
 * `[data-row][data-col]`, so a cell's `data-col` attribute and the id its handlers pass to
 * `onCellKeyDown` must be the same number. They were not: the amount cells rendered a
 * sequential counter while their handlers passed 100/101/102, so ArrowUp/ArrowDown/Enter
 * from an amount cell found nothing to focus and silently stayed put, and the phantom
 * `activeCell` it left behind un-formatted the *next* row's amount. Naming the columns once
 * removes the possibility: there is no counter left to drift.
 */
const COL = {
  account: 0,
  description: 1,
  branch: 2,
  project: 3,
  currency: 4,
  taxCode: 5,
  partner: 6,
  item: 7,
  warehouse: 8,
  transactionType: 9,
  uom: 10,
  debit: 100,
  credit: 101,
  amount: 102,
  taxInclusive: 103,
  quantity: 104,
  unitPrice: 105,
  discountPercent: 106,
  unitCost: 107,
  value: 108,
} as const;

/** Amount-style cells swap formatted display for raw digits while they hold the caret. */
const EDITABLE_AMOUNT_COLS: number[] = [
  COL.debit,
  COL.credit,
  COL.amount,
  COL.quantity,
  COL.unitPrice,
  COL.unitCost,
  COL.value,
];

/** Inventory mode: which of its optional columns a screen wants. Adjustments and batches want
 * all of them; a transfer wants none — its warehouses are on the header and it carries no
 * cost, because the arrival takes the dispatched value frozen. */
export interface InventoryColumns {
  warehouse?: boolean;
  transactionType?: boolean;
  unitCost?: boolean;
  value?: boolean;
  contra?: boolean;
}

const ALL_INVENTORY_COLUMNS: Required<InventoryColumns> = {
  warehouse: true,
  transactionType: true,
  unitCost: true,
  value: true,
  contra: true,
};

/** Which kinds take a unit cost (a receipt is priced by what it cost) and which take a value
 * (a revaluation moves no quantity and states the write-up or write-down). Everything else is
 * costed at the average on Post, and the cell says so by not being there. */
const INCREASE_KINDS: ReadonlySet<string> = new Set([
  InventoryTransactionKind.ADJUSTMENT_IN,
  InventoryTransactionKind.OPENING_BALANCE,
]);

export interface LineGridProps {
  mode: "journal" | "cashbook" | "document" | "batch" | "inventory";
  rows: LineGridRow[];
  onRowsChange: (rows: LineGridRow[]) => void;
  errors?: LineErrors;
  accountOptions: SelectOption[];
  branchOptions?: SelectOption[];
  projectOptions?: SelectOption[];
  currencyOptions?: SelectOption[];
  taxCodeOptions?: SelectOption[];
  /** Batch mode only: who each line charges or credits. */
  partnerOptions?: SelectOption[];
  /** Inventory mode. The item options carry barcodes and descriptions as `keywords`, so a
   * scan finds the row; the callbacks let the screen answer per-row questions the grid has
   * no business knowing — what is on hand, what the unit converts to, what the type does. */
  itemOptions?: SelectOption[];
  warehouseOptions?: SelectOption[];
  transactionTypeOptions?: SelectOption[];
  uomOptionsFor?: (row: LineGridRow) => SelectOption[];
  /** Formatted quantity on hand of the row's item at the row's warehouse, or undefined. */
  onHandFor?: (row: LineGridRow) => string | undefined;
  /** "= 12 EA" — the row's quantity in the item's base unit, or undefined in the base unit. */
  conversionFor?: (row: LineGridRow) => string | undefined;
  /** The kind of the row's transaction type, which decides whether a unit cost or a value is
   * asked for. Null while no type is chosen. */
  lineKindFor?: (row: LineGridRow) => InventoryTransactionKind | null;
  inventoryColumns?: InventoryColumns;
  /** Caps the grid at this many rows: the add-line control goes and Enter on the last row
   * stays put. An inventory adjustment is one line by definition (the service refuses
   * more), so its grid must not offer a second. */
  maxRows?: number;
  baseCurrencyId?: string;
  /** Looks up the latest dated rate for a currency, to prefill the rate cell. */
  rateForCurrency?: (currencyId: string) => string | undefined;
  rowDefaults?: Partial<LineGridRow>;
}

/**
 * Spreadsheet-grade line grid shared by the Journal and Cashbook document workspaces.
 * Keyboard model: Tab/Enter move across cells, arrow keys navigate rows, Enter on the
 * last row adds a row. Esc is two-level: while editing a cell, it reverts that cell's edit;
 * when no cell is in edit, Esc leaves the document. Branch/project/currency+rate/tax code
 * are collapsible — hidden by default, defaulted from the document header when shown.
 */
export function LineGrid({
  mode,
  rows,
  onRowsChange,
  errors = {},
  accountOptions,
  branchOptions = [],
  projectOptions = [],
  currencyOptions = [],
  taxCodeOptions = [],
  partnerOptions = [],
  itemOptions = [],
  warehouseOptions = [],
  transactionTypeOptions = [],
  uomOptionsFor,
  onHandFor,
  conversionFor,
  lineKindFor,
  inventoryColumns,
  maxRows,
  baseCurrencyId,
  rateForCurrency,
  rowDefaults,
}: LineGridProps) {
  const inv = { ...ALL_INVENTORY_COLUMNS, ...(inventoryColumns ?? {}) };
  const isInventory = mode === "inventory";
  // Document mode is P4 and fully externalised; the journal/cashbook literals below predate
  // it and are part of the P3 i18n backfill (docs/i18n-backfill-p3.md, issue #6).
  const t = useTranslations("lineGrid");
  const containerRef = useRef<HTMLDivElement>(null);
  const [activeCell, setActiveCell] = useState<{ row: number; col: number } | null>(null);
  const [showExtra, setShowExtra] = useState(false);
  const cellSnapshotRef = useRef<{ row: number; field: keyof LineGridRow; value: string } | null>(null);

  // Amount cells swap between a comma-formatted display and the raw digits while editing
  // (activeCell). Selecting the old text synchronously in onFocus races that swap — the value
  // can change out from under the selection before a fill()/keystroke replaces it, so a small
  // slice of the stale formatted text survives and gets concatenated with the new digits
  // (this is exactly how a typed 65,000 became 1,000,065,000). Select only after the raw value
  // has actually committed to the DOM, i.e. in an effect that runs after the re-render. Note:
  // 100/101/102 are logical column ids for debit/credit/amount, distinct from the sequential
  // `data-col` DOM attribute, so we select via document.activeElement (React preserves the
  // focused DOM node across the re-render) rather than re-querying by data-col.
  useEffect(() => {
    if (!activeCell || !EDITABLE_AMOUNT_COLS.includes(activeCell.col)) return;
    const el = document.activeElement;
    if (el instanceof HTMLInputElement) el.select();
  }, [activeCell]);

  function updateRow(index: number, patch: Partial<LineGridRow>) {
    const next = rows.slice();
    next[index] = { ...next[index], ...patch };
    onRowsChange(next);
  }

  const canAddRow = maxRows === undefined || rows.length < maxRows;

  function addRow() {
    if (!canAddRow) return;
    onRowsChange([...rows, emptyLineGridRow(rowDefaults)]);
  }

  function focusCell(row: number, col: number) {
    const el = containerRef.current?.querySelector<HTMLElement>(
      `[data-row="${row}"][data-col="${col}"] input, [data-row="${row}"][data-col="${col}"] button`,
    );
    el?.focus();
    setActiveCell({ row, col });
  }

  function startCellEdit(row: number, col: number, field: keyof LineGridRow, value: string) {
    cellSnapshotRef.current = { row, field, value };
    setActiveCell({ row, col });
  }

  function onCellKeyDown(e: React.KeyboardEvent, row: number, col: number, field?: keyof LineGridRow) {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      if (field && cellSnapshotRef.current && cellSnapshotRef.current.row === row && cellSnapshotRef.current.field === field) {
        updateRow(row, { [field]: cellSnapshotRef.current.value });
      }
      cellSnapshotRef.current = null;
      setActiveCell(null);
      (e.target as HTMLElement)?.blur();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      focusCell(Math.min(row + 1, rows.length - 1), col);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      focusCell(Math.max(row - 1, 0), col);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (row === rows.length - 1) addRow();
      else focusCell(row + 1, col);
    }
  }

  function onCurrencyChange(r: number, row: LineGridRow, currencyId: string) {
    const isBase = !baseCurrencyId || currencyId === baseCurrencyId;
    const prefill = !isBase && !row.exchangeRate ? rateForCurrency?.(currencyId) : undefined;
    updateRow(r, { currencyId, exchangeRate: isBase ? "" : prefill ?? row.exchangeRate });
  }

  return (
    <div className="space-y-2">
      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => setShowExtra((v) => !v)}
          className="flex items-center gap-1.5 text-xs font-medium text-[var(--vinea-ink-muted)] hover:text-[var(--vinea-ink)]"
        >
          <ChevronsUpDown className="size-3.5" />
          {mode === "inventory"
            ? showExtra
              ? t("inventoryFewerColumns")
              : t("inventoryMoreColumns")
            : mode === "document"
            ? showExtra
              ? t("fewerColumns")
              : t("moreColumns")
            : mode === "batch"
            ? showExtra
              ? t("fewerColumns")
              : t("moreColumns")
            : showExtra
              ? "Fewer columns"
              : "More columns (branch, project, currency, tax)"}
        </button>
      </div>
      <div
        ref={containerRef}
        className="overflow-auto rounded-[var(--radius-card)] border border-[var(--vinea-border)]"
      >
        <table
          role="grid"
          aria-label={t(`${mode}Lines`)}
          className="w-full border-collapse text-sm"
        >
          <thead className="bg-[var(--vinea-surface-sunken)] text-xs uppercase tracking-wide text-[var(--vinea-ink-subtle)]">
            <tr>
              {mode === "batch" && <th className="px-3 py-2 text-left">{t("partner")}</th>}
              {isInventory && (
                <>
                  <th className="px-3 py-2 text-left">{t("item")}</th>
                  {inv.warehouse && <th className="px-3 py-2 text-left">{t("warehouse")}</th>}
                  <th className="px-3 py-2 text-right">{t("onHand")}</th>
                  {inv.transactionType && <th className="px-3 py-2 text-left">{t("transactionType")}</th>}
                  <th className="px-3 py-2 text-right">{t("quantity")}</th>
                  <th className="px-3 py-2 text-left">{t("uom")}</th>
                  {inv.unitCost && <th className="px-3 py-2 text-right">{t("unitCost")}</th>}
                  {inv.value && <th className="px-3 py-2 text-right">{t("value")}</th>}
                </>
              )}
              {!isInventory && (
                <th className="px-3 py-2 text-left">{mode === "batch" ? t("contraAccount") : t("account")}</th>
              )}
              {isInventory && showExtra && inv.contra && (
                <th className="px-3 py-2 text-left">{t("contraAccount")}</th>
              )}
              <th className="px-3 py-2 text-left">{t("description")}</th>
              {showExtra && !isInventory && <th className="px-3 py-2 text-left">{t("branch")}</th>}
              {showExtra && <th className="px-3 py-2 text-left">{t("project")}</th>}
              {showExtra && !isInventory && <th className="px-3 py-2 text-left">{t("currencyRate")}</th>}
              {showExtra && !isInventory && <th className="px-3 py-2 text-left">{t("taxCode")}</th>}
              {mode === "journal" && (
                <>
                  <th className="px-3 py-2 text-right">{t("debit")}</th>
                  <th className="px-3 py-2 text-right">{t("credit")}</th>
                </>
              )}
              {mode === "cashbook" && (
                <>
                  <th className="px-3 py-2 text-right">{t("amount")}</th>
                  <th className="px-3 py-2 text-center">{t("taxInclusive")}</th>
                </>
              )}
              {mode === "batch" && (
                <>
                  <th className="px-3 py-2 text-right">{t("amount")}</th>
                </>
              )}
              {mode === "document" && (
                <>
                  <th className="px-3 py-2 text-right">{t("quantity")}</th>
                  <th className="px-3 py-2 text-right">{t("unitPrice")}</th>
                  <th className="px-3 py-2 text-right">{t("discountPercent")}</th>
                  <th className="px-3 py-2 text-right">{t("net")}</th>
                </>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--vinea-border)]">
            {rows.map((row, r) => {
              const rowErr = errors[r];
              const accountErr = rowErr?.gl_account_id || rowErr?.account;
              const descErr = rowErr?.description;
              const branchErr = rowErr?.branch_id;
              const projectErr = rowErr?.project_id;
              const currencyErr = rowErr?.currency_id;
              const taxErr = rowErr?.tax_code_id;
              const debitErr = rowErr?.debit || (rowErr?.amount && mode === "journal" ? rowErr.amount : undefined);
              const creditErr = rowErr?.credit;
              const amountErr = rowErr?.amount;
              const quantityErr = rowErr?.quantity;
              const unitPriceErr = rowErr?.unit_price;
              const discountErr = rowErr?.discount_percent;
              const itemErr = rowErr?.item_id;
              const warehouseErr = rowErr?.warehouse_id;
              const typeErr = rowErr?.transaction_type_id;
              const uomErr = rowErr?.uom_id;
              const unitCostErr = rowErr?.unit_cost;
              const valueErr = rowErr?.value;
              const contraErr = rowErr?.contra_account_id;
              const kind = isInventory ? (lineKindFor?.(row) ?? null) : null;
              const takesUnitCost = kind !== null && INCREASE_KINDS.has(kind);
              const takesValue = kind === InventoryTransactionKind.REVALUATION;

              return (
                <tr key={row.id} className={cn(activeCell?.row === r && "bg-[var(--vinea-brand-soft)]/30")}>
                  {mode === "batch" && (
                    <td data-row={r} data-col={COL.partner} className="min-w-48 p-1 align-top">
                      <Combobox
                        options={partnerOptions}
                        value={row.partnerId}
                        onValueChange={(v) => updateRow(r, { partnerId: v })}
                        placeholder={t("partnerPlaceholder")}
                        ariaLabel={t("partnerAria", { row: r + 1 })}
                        className={cn("h-8", rowErr?.partner_id && "border-[var(--vinea-danger)]")}
                        onFocus={() => startCellEdit(r, COL.partner, "partnerId", row.partnerId)}
                        onKeyDown={(e) => onCellKeyDown(e, r, COL.partner, "partnerId")}
                      />
                      {rowErr?.partner_id && (
                        <p className="mt-0.5 px-1 text-xs text-[var(--vinea-danger)]">
                          {rowErr.partner_id}
                        </p>
                      )}
                    </td>
                  )}
                  {isInventory && (
                    <>
                      <td data-row={r} data-col={COL.item} className="min-w-56 p-1 align-top">
                        <Combobox
                          options={itemOptions}
                          value={row.itemId}
                          // A new item means a new base unit: the unit picked for the old one
                          // may not even be in the new item's category.
                          onValueChange={(v) => updateRow(r, { itemId: v, uomId: "" })}
                          placeholder={t("itemPlaceholder")}
                          ariaLabel={t("itemAria", { row: r + 1 })}
                          // Fixed widths on the pickers, not the cells: an auto-layout
                          // table ignores a cell's max-width, and an item label with its
                          // on-hand suffix would otherwise push unit cost off the screen.
                          className={cn("h-8 w-64", itemErr && "border-[var(--vinea-danger)]")}
                          onFocus={() => startCellEdit(r, COL.item, "itemId", row.itemId)}
                          onKeyDown={(e) => onCellKeyDown(e, r, COL.item, "itemId")}
                        />
                        {itemErr && <p className="mt-0.5 px-1 text-xs text-[var(--vinea-danger)]">{itemErr}</p>}
                      </td>
                      {inv.warehouse && (
                        <td data-row={r} data-col={COL.warehouse} className="min-w-40 p-1 align-top">
                          <Combobox
                            options={warehouseOptions}
                            value={row.warehouseId}
                            onValueChange={(v) => updateRow(r, { warehouseId: v })}
                            placeholder={t("warehousePlaceholder")}
                            ariaLabel={t("warehouseAria", { row: r + 1 })}
                            className={cn("h-8 w-40", warehouseErr && "border-[var(--vinea-danger)]")}
                            onFocus={() => startCellEdit(r, COL.warehouse, "warehouseId", row.warehouseId)}
                            onKeyDown={(e) => onCellKeyDown(e, r, COL.warehouse, "warehouseId")}
                          />
                          {warehouseErr && <p className="mt-0.5 px-1 text-xs text-[var(--vinea-danger)]">{warehouseErr}</p>}
                        </td>
                      )}
                      {/* Read-only: what the warehouse holds of this item right now, from the
                          cache the step-2 checker proves. The figure the typeahead exists to
                          show, and the one a refusal will be about. */}
                      <td className="w-24 p-1 text-right align-top">
                        <span
                          aria-label={t("onHandAria", { row: r + 1 })}
                          className="block px-2 py-1.5 font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]"
                        >
                          {onHandFor?.(row) ?? t("notApplicable")}
                        </span>
                      </td>
                      {inv.transactionType && (
                        <td data-row={r} data-col={COL.transactionType} className="min-w-40 p-1 align-top">
                          <Combobox
                            options={transactionTypeOptions}
                            value={row.transactionTypeId}
                            onValueChange={(v) => updateRow(r, { transactionTypeId: v })}
                            placeholder={t("transactionTypePlaceholder")}
                            ariaLabel={t("transactionTypeAria", { row: r + 1 })}
                            className={cn("h-8 w-44", typeErr && "border-[var(--vinea-danger)]")}
                            onFocus={() => startCellEdit(r, COL.transactionType, "transactionTypeId", row.transactionTypeId)}
                            onKeyDown={(e) => onCellKeyDown(e, r, COL.transactionType, "transactionTypeId")}
                          />
                          {typeErr && <p className="mt-0.5 px-1 text-xs text-[var(--vinea-danger)]">{typeErr}</p>}
                        </td>
                      )}
                      <td data-row={r} data-col={COL.quantity} className="w-28 p-1 align-top">
                        <input
                          value={displayAmount(row.quantity, activeCell?.row === r && activeCell.col === COL.quantity)}
                          onChange={(e) => updateRow(r, { quantity: e.target.value })}
                          onKeyDown={(e) => onCellKeyDown(e, r, COL.quantity, "quantity")}
                          onFocus={() => startCellEdit(r, COL.quantity, "quantity", row.quantity)}
                          onBlur={() => setActiveCell(null)}
                          inputMode="decimal"
                          disabled={takesValue}
                          aria-label={t("quantityAria", { row: r + 1 })}
                          className={cn(
                            "h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 text-right font-mono tabular-nums focus:border-[var(--vinea-brand)] disabled:opacity-40",
                            quantityErr && "border-[var(--vinea-danger)]",
                          )}
                          placeholder={t("zeroPlaceholder")}
                        />
                        {quantityErr && <p className="mt-0.5 text-right text-xs text-[var(--vinea-danger)]">{quantityErr}</p>}
                      </td>
                      <td data-row={r} data-col={COL.uom} className="min-w-32 p-1 align-top">
                        <Combobox
                          options={uomOptionsFor?.(row) ?? []}
                          value={row.uomId}
                          onValueChange={(v) => updateRow(r, { uomId: v })}
                          placeholder={t("uomPlaceholder")}
                          ariaLabel={t("uomAria", { row: r + 1 })}
                          className={cn("h-8 w-36", uomErr && "border-[var(--vinea-danger)]")}
                          onFocus={() => startCellEdit(r, COL.uom, "uomId", row.uomId)}
                          onKeyDown={(e) => onCellKeyDown(e, r, COL.uom, "uomId")}
                        />
                        {/* The conversion, shown rather than implied: "6 CS = 36 EA" is the
                            number the move will actually carry. */}
                        {conversionFor?.(row) && (
                          <p
                            aria-label={t("conversionAria", { row: r + 1 })}
                            className="mt-0.5 px-1 font-mono text-xs tabular-nums text-[var(--vinea-ink-subtle)]"
                          >
                            {conversionFor(row)}
                          </p>
                        )}
                        {uomErr && <p className="mt-0.5 px-1 text-xs text-[var(--vinea-danger)]">{uomErr}</p>}
                      </td>
                      {inv.unitCost && (
                        <td data-row={r} data-col={COL.unitCost} className="min-w-28 p-1 align-top">
                          {takesUnitCost ? (
                            <input
                              value={displayAmount(row.unitCost, activeCell?.row === r && activeCell.col === COL.unitCost)}
                              onChange={(e) => updateRow(r, { unitCost: e.target.value })}
                              onKeyDown={(e) => onCellKeyDown(e, r, COL.unitCost, "unitCost")}
                              onFocus={() => startCellEdit(r, COL.unitCost, "unitCost", row.unitCost)}
                              onBlur={() => setActiveCell(null)}
                              inputMode="decimal"
                              aria-label={t("unitCostAria", { row: r + 1 })}
                              className={cn(
                                "h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 text-right font-mono tabular-nums focus:border-[var(--vinea-brand)]",
                                unitCostErr && "border-[var(--vinea-danger)]",
                              )}
                              placeholder={t("zeroPlaceholder")}
                            />
                          ) : (
                            // Not disabled — absent. A decrease is costed at the average on
                            // Post, and a greyed-out cell invites the question of how to
                            // ungrey it.
                            <span
                              aria-label={t("unitCostAria", { row: r + 1 })}
                              className="block px-2 py-1.5 text-right font-mono text-xs text-[var(--vinea-ink-subtle)]"
                            >
                              {t("notApplicable")}
                            </span>
                          )}
                          {unitCostErr && <p className="mt-0.5 text-right text-xs text-[var(--vinea-danger)]">{unitCostErr}</p>}
                        </td>
                      )}
                      {inv.value && (
                        <td data-row={r} data-col={COL.value} className="min-w-28 p-1 align-top">
                          {takesValue ? (
                            <input
                              value={displayAmount(row.value, activeCell?.row === r && activeCell.col === COL.value)}
                              onChange={(e) => updateRow(r, { value: e.target.value })}
                              onKeyDown={(e) => onCellKeyDown(e, r, COL.value, "value")}
                              onFocus={() => startCellEdit(r, COL.value, "value", row.value)}
                              onBlur={() => setActiveCell(null)}
                              inputMode="decimal"
                              aria-label={t("valueAria", { row: r + 1 })}
                              className={cn(
                                "h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 text-right font-mono tabular-nums focus:border-[var(--vinea-brand)]",
                                valueErr && "border-[var(--vinea-danger)]",
                              )}
                              placeholder={t("zeroPlaceholder")}
                            />
                          ) : (
                            <span
                              aria-label={t("valueAria", { row: r + 1 })}
                              className="block px-2 py-1.5 text-right font-mono text-xs text-[var(--vinea-ink-subtle)]"
                            >
                              {t("notApplicable")}
                            </span>
                          )}
                          {valueErr && <p className="mt-0.5 text-right text-xs text-[var(--vinea-danger)]">{valueErr}</p>}
                        </td>
                      )}
                    </>
                  )}
                  {(!isInventory || (showExtra && inv.contra)) && (
                  <td data-row={r} data-col={COL.account} className="min-w-48 p-1 align-top">
                    <Combobox
                      options={accountOptions}
                      value={row.accountId}
                      onValueChange={(v) => updateRow(r, { accountId: v })}
                      placeholder={mode === "batch" || isInventory ? t("contraAccountPlaceholder") : "Account…"}
                      // Batch mode's header calls this column "Contra account"; the accessible
                      // name has to say the same, or a screen-reader user is hunting for a
                      // column that does not exist by that name.
                      ariaLabel={
                        mode === "batch" || isInventory
                          ? t("contraAccountAria", { row: r + 1 })
                          : `Account, row ${r + 1}`
                      }
                      className={cn("h-8", (accountErr || contraErr) && "border-[var(--vinea-danger)]")}
                      onFocus={() => startCellEdit(r, COL.account, "accountId", row.accountId)}
                      onKeyDown={(e) => onCellKeyDown(e, r, COL.account, "accountId")}
                    />
                    {(accountErr || contraErr) && <p className="mt-0.5 px-1 text-xs text-[var(--vinea-danger)]">{accountErr || contraErr}</p>}
                  </td>
                  )}
                  <td data-row={r} data-col={COL.description} className="p-1 align-top">
                    <input
                      value={row.description}
                      onChange={(e) => updateRow(r, { description: e.target.value })}
                      onKeyDown={(e) => onCellKeyDown(e, r, COL.description, "description")}
                      onFocus={() => startCellEdit(r, COL.description, "description", row.description)}
                      aria-label={`Description, row ${r + 1}`}
                      className={cn(
                        "h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 focus:border-[var(--vinea-brand)]",
                        descErr && "border-[var(--vinea-danger)]",
                      )}
                      placeholder={t("descriptionPlaceholder")}
                    />
                    {descErr && <p className="mt-0.5 px-1 text-xs text-[var(--vinea-danger)]">{descErr}</p>}
                  </td>
                  {showExtra && !isInventory && (
                    <td data-row={r} data-col={COL.branch} className="min-w-36 p-1 align-top">
                      <Combobox
                        options={branchOptions}
                        value={row.branchId}
                        onValueChange={(v) => updateRow(r, { branchId: v })}
                        placeholder={t("branchPlaceholder")}
                        ariaLabel={`Branch, row ${r + 1}`}
                        className={cn("h-8", branchErr && "border-[var(--vinea-danger)]")}
                        onFocus={() => startCellEdit(r, COL.branch, "branchId", row.branchId)}
                        onKeyDown={(e) => onCellKeyDown(e, r, COL.branch, "branchId")}
                      />
                      {branchErr && <p className="mt-0.5 px-1 text-xs text-[var(--vinea-danger)]">{branchErr}</p>}
                    </td>
                  )}
                  {showExtra && (
                    <td data-row={r} data-col={COL.project} className="min-w-36 p-1 align-top">
                      <Combobox
                        options={projectOptions}
                        value={row.projectId}
                        onValueChange={(v) => updateRow(r, { projectId: v })}
                        placeholder={t("projectPlaceholder")}
                        ariaLabel={`Project, row ${r + 1}`}
                        className={cn("h-8", projectErr && "border-[var(--vinea-danger)]")}
                        onFocus={() => startCellEdit(r, COL.project, "projectId", row.projectId)}
                        onKeyDown={(e) => onCellKeyDown(e, r, COL.project, "projectId")}
                      />
                      {projectErr && <p className="mt-0.5 px-1 text-xs text-[var(--vinea-danger)]">{projectErr}</p>}
                    </td>
                  )}
                  {showExtra && !isInventory && (
                    <td data-row={r} data-col={COL.currency} className="min-w-44 p-1 align-top">
                      <div className="flex gap-1">
                        <Combobox
                          options={currencyOptions}
                          value={row.currencyId}
                          onValueChange={(v) => onCurrencyChange(r, row, v)}
                          placeholder={t("currencyPlaceholder")}
                          ariaLabel={`Currency, row ${r + 1}`}
                          className={cn("h-8 w-24", currencyErr && "border-[var(--vinea-danger)]")}
                          onFocus={() => startCellEdit(r, COL.currency, "currencyId", row.currencyId)}
                          onKeyDown={(e) => onCellKeyDown(e, r, COL.currency, "currencyId")}
                        />
                        {row.currencyId && row.currencyId !== baseCurrencyId && (
                          <input
                            value={row.exchangeRate}
                            onChange={(e) => updateRow(r, { exchangeRate: e.target.value })}
                            onKeyDown={(e) => onCellKeyDown(e, r, COL.currency, "exchangeRate")}
                            onFocus={() => startCellEdit(r, COL.currency, "exchangeRate", row.exchangeRate)}
                            inputMode="decimal"
                            placeholder={t("ratePlaceholder")}
                            aria-label={`Exchange rate, row ${r + 1}`}
                            className="h-8 w-20 rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] bg-transparent px-2 text-right font-mono text-xs"
                          />
                        )}
                      </div>
                      {currencyErr && <p className="mt-0.5 px-1 text-xs text-[var(--vinea-danger)]">{currencyErr}</p>}
                    </td>
                  )}
                  {showExtra && !isInventory && (
                    <td data-row={r} data-col={COL.taxCode} className="min-w-36 p-1 align-top">
                      <Combobox
                        options={taxCodeOptions}
                        value={row.taxCodeId}
                        onValueChange={(v) => updateRow(r, { taxCodeId: v })}
                        placeholder={t("taxCodePlaceholder")}
                        ariaLabel={`Tax code, row ${r + 1}`}
                        className={cn("h-8", taxErr && "border-[var(--vinea-danger)]")}
                        onFocus={() => startCellEdit(r, COL.taxCode, "taxCodeId", row.taxCodeId)}
                        onKeyDown={(e) => onCellKeyDown(e, r, COL.taxCode, "taxCodeId")}
                      />
                      {taxErr && <p className="mt-0.5 px-1 text-xs text-[var(--vinea-danger)]">{taxErr}</p>}
                    </td>
                  )}
                  {mode === "journal" && (
                    <>
                      <td data-row={r} data-col={COL.debit} className="w-32 p-1 align-top">
                        <input
                          value={displayAmount(row.debit, activeCell?.row === r && activeCell.col === COL.debit)}
                          onChange={(e) => updateRow(r, { debit: e.target.value, credit: e.target.value ? "" : row.credit })}
                          onKeyDown={(e) => onCellKeyDown(e, r, COL.debit, "debit")}
                          onFocus={() => startCellEdit(r, COL.debit, "debit", row.debit)}
                          onBlur={() => setActiveCell(null)}
                          inputMode="decimal"
                          aria-label={`Debit, row ${r + 1}`}
                          className={cn(
                            "h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 text-right font-mono tabular-nums focus:border-[var(--vinea-brand)]",
                            debitErr && "border-[var(--vinea-danger)]",
                          )}
                          placeholder={t("zeroPlaceholder")}
                        />
                        {debitErr && <p className="mt-0.5 text-right text-xs text-[var(--vinea-danger)]">{debitErr}</p>}
                      </td>
                      <td data-row={r} data-col={COL.credit} className="w-32 p-1 align-top">
                        <input
                          value={displayAmount(row.credit, activeCell?.row === r && activeCell.col === COL.credit)}
                          onChange={(e) => updateRow(r, { credit: e.target.value, debit: e.target.value ? "" : row.debit })}
                          onKeyDown={(e) => onCellKeyDown(e, r, COL.credit, "credit")}
                          onFocus={() => startCellEdit(r, COL.credit, "credit", row.credit)}
                          onBlur={() => setActiveCell(null)}
                          inputMode="decimal"
                          aria-label={`Credit, row ${r + 1}`}
                          className={cn(
                            "h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 text-right font-mono tabular-nums focus:border-[var(--vinea-brand)]",
                            creditErr && "border-[var(--vinea-danger)]",
                          )}
                          placeholder={t("zeroPlaceholder")}
                        />
                        {creditErr && <p className="mt-0.5 text-right text-xs text-[var(--vinea-danger)]">{creditErr}</p>}
                      </td>
                    </>
                  )}
                  {mode === "cashbook" && (
                    <>
                      <td data-row={r} data-col={COL.amount} className="w-32 p-1 align-top">
                        <input
                          value={displayAmount(row.amount, activeCell?.row === r && activeCell.col === COL.amount)}
                          onChange={(e) => updateRow(r, { amount: e.target.value })}
                          onKeyDown={(e) => onCellKeyDown(e, r, COL.amount, "amount")}
                          onFocus={() => startCellEdit(r, COL.amount, "amount", row.amount)}
                          onBlur={() => setActiveCell(null)}
                          inputMode="decimal"
                          aria-label={`Amount, row ${r + 1}`}
                          className={cn(
                            "h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 text-right font-mono tabular-nums focus:border-[var(--vinea-brand)]",
                            amountErr && "border-[var(--vinea-danger)]",
                          )}
                          placeholder={t("zeroPlaceholder")}
                        />
                        {amountErr && <p className="mt-0.5 text-right text-xs text-[var(--vinea-danger)]">{amountErr}</p>}
                      </td>
                      <td className="w-16 p-1 text-center align-top">
                        <input
                          type="checkbox"
                          checked={row.taxInclusive}
                          onChange={(e) => updateRow(r, { taxInclusive: e.target.checked })}
                          onKeyDown={(e) => onCellKeyDown(e, r, COL.taxInclusive)}
                          aria-label={`Tax inclusive, row ${r + 1}`}
                          className="mt-2 size-4 accent-[var(--vinea-brand)]"
                        />
                      </td>
                    </>
                  )}
                  {mode === "batch" && (
                    <td data-row={r} data-col={COL.amount} className="w-36 p-1 align-top">
                      <input
                        value={displayAmount(row.amount, activeCell?.row === r && activeCell.col === COL.amount)}
                        onChange={(e) => updateRow(r, { amount: e.target.value })}
                        onKeyDown={(e) => onCellKeyDown(e, r, COL.amount, "amount")}
                        onFocus={() => startCellEdit(r, COL.amount, "amount", row.amount)}
                        onBlur={() => setActiveCell(null)}
                        inputMode="decimal"
                        aria-label={t("amountAria", { row: r + 1 })}
                        className={cn(
                          "h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 text-right font-mono tabular-nums focus:border-[var(--vinea-brand)]",
                          amountErr && "border-[var(--vinea-danger)]",
                        )}
                        placeholder={t("zeroPlaceholder")}
                      />
                      {amountErr && <p className="mt-0.5 text-right text-xs text-[var(--vinea-danger)]">{amountErr}</p>}
                    </td>
                  )}
                  {mode === "document" && (
                    <>
                      <td data-row={r} data-col={COL.quantity} className="w-24 p-1 align-top">
                        <input
                          value={displayAmount(row.quantity, activeCell?.row === r && activeCell.col === COL.quantity)}
                          onChange={(e) => updateRow(r, { quantity: e.target.value })}
                          onKeyDown={(e) => onCellKeyDown(e, r, COL.quantity, "quantity")}
                          onFocus={() => startCellEdit(r, COL.quantity, "quantity", row.quantity)}
                          onBlur={() => setActiveCell(null)}
                          inputMode="decimal"
                          aria-label={t("quantityAria", { row: r + 1 })}
                          className={cn(
                            "h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 text-right font-mono tabular-nums focus:border-[var(--vinea-brand)]",
                            quantityErr && "border-[var(--vinea-danger)]",
                          )}
                          placeholder={t("onePlaceholder")}
                        />
                        {quantityErr && <p className="mt-0.5 text-right text-xs text-[var(--vinea-danger)]">{quantityErr}</p>}
                      </td>
                      <td data-row={r} data-col={COL.unitPrice} className="w-32 p-1 align-top">
                        <input
                          value={displayAmount(row.unitPrice, activeCell?.row === r && activeCell.col === COL.unitPrice)}
                          onChange={(e) => updateRow(r, { unitPrice: e.target.value })}
                          onKeyDown={(e) => onCellKeyDown(e, r, COL.unitPrice, "unitPrice")}
                          onFocus={() => startCellEdit(r, COL.unitPrice, "unitPrice", row.unitPrice)}
                          onBlur={() => setActiveCell(null)}
                          inputMode="decimal"
                          aria-label={t("unitPriceAria", { row: r + 1 })}
                          className={cn(
                            "h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 text-right font-mono tabular-nums focus:border-[var(--vinea-brand)]",
                            unitPriceErr && "border-[var(--vinea-danger)]",
                          )}
                          placeholder={t("zeroPlaceholder")}
                        />
                        {unitPriceErr && <p className="mt-0.5 text-right text-xs text-[var(--vinea-danger)]">{unitPriceErr}</p>}
                      </td>
                      <td data-row={r} data-col={COL.discountPercent} className="w-24 p-1 align-top">
                        <input
                          value={row.discountPercent}
                          onChange={(e) => updateRow(r, { discountPercent: e.target.value })}
                          onKeyDown={(e) => onCellKeyDown(e, r, COL.discountPercent, "discountPercent")}
                          onFocus={() => startCellEdit(r, COL.discountPercent, "discountPercent", row.discountPercent)}
                          onBlur={() => setActiveCell(null)}
                          inputMode="decimal"
                          aria-label={t("discountPercentAria", { row: r + 1 })}
                          className={cn(
                            "h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 text-right font-mono tabular-nums focus:border-[var(--vinea-brand)]",
                            discountErr && "border-[var(--vinea-danger)]",
                          )}
                          placeholder={t("zeroPlaceholder")}
                        />
                        {discountErr && <p className="mt-0.5 text-right text-xs text-[var(--vinea-danger)]">{discountErr}</p>}
                      </td>
                      {/* Read-only: the server computes tax and totals, this is only the
                          quantity x price less discount the operator just typed. */}
                      <td className="w-32 p-1 text-right align-top">
                        <span className="block px-2 py-1.5 font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                          {displayAmount(String(lineNet(row)))}
                        </span>
                      </td>
                    </>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
        {canAddRow && (
          <button
            type="button"
            onClick={addRow}
            className="w-full border-t border-[var(--vinea-border)] px-3 py-2 text-left text-xs font-medium text-[var(--vinea-brand)] hover:bg-[var(--vinea-surface-sunken)]"
          >
            {mode === "document" || mode === "batch" || isInventory ? t("addLine") : "+ Add line"}
          </button>
        )}
      </div>
    </div>
  );
}

