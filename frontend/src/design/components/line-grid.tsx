"use client";

import { useRef, useState } from "react";
import { ChevronsUpDown } from "lucide-react";
import { cn } from "@/lib/cn";
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
  /** Shared extra dimensions — collapsible, all default from the header. */
  branchId: string;
  projectId: string;
  currencyId: string;
  exchangeRate: string;
  taxCodeId: string;
}

export function emptyLineGridRow(defaults: Partial<LineGridRow> = {}): LineGridRow {
  return {
    id: crypto.randomUUID(),
    accountId: "",
    description: "",
    debit: "",
    credit: "",
    amount: "",
    taxInclusive: true,
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

export interface LineGridProps {
  mode: "journal" | "cashbook";
  rows: LineGridRow[];
  onRowsChange: (rows: LineGridRow[]) => void;
  accountOptions: SelectOption[];
  branchOptions?: SelectOption[];
  projectOptions?: SelectOption[];
  currencyOptions?: SelectOption[];
  taxCodeOptions?: SelectOption[];
  baseCurrencyId?: string;
  /** Looks up the latest dated rate for a currency, to prefill the rate cell. */
  rateForCurrency?: (currencyId: string) => string | undefined;
  rowDefaults?: Partial<LineGridRow>;
}

/**
 * Spreadsheet-grade line grid shared by the Journal and Cashbook document workspaces.
 * Keyboard model: Tab/Enter move across cells, arrow keys navigate rows, Enter on the
 * last row adds a row. Branch/project/currency+rate/tax code are collapsible — hidden
 * by default, defaulted from the document header when shown.
 */
export function LineGrid({
  mode,
  rows,
  onRowsChange,
  accountOptions,
  branchOptions = [],
  projectOptions = [],
  currencyOptions = [],
  taxCodeOptions = [],
  baseCurrencyId,
  rateForCurrency,
  rowDefaults,
}: LineGridProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [activeCell, setActiveCell] = useState<{ row: number; col: number } | null>(null);
  const [showExtra, setShowExtra] = useState(false);

  function updateRow(index: number, patch: Partial<LineGridRow>) {
    const next = rows.slice();
    next[index] = { ...next[index], ...patch };
    onRowsChange(next);
  }

  function addRow() {
    onRowsChange([...rows, emptyLineGridRow(rowDefaults)]);
  }

  function focusCell(row: number, col: number) {
    const el = containerRef.current?.querySelector<HTMLElement>(
      `[data-row="${row}"][data-col="${col}"] input, [data-row="${row}"][data-col="${col}"] button`,
    );
    el?.focus();
    setActiveCell({ row, col });
  }

  function onCellKeyDown(e: React.KeyboardEvent, row: number, col: number) {
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
          {showExtra ? "Fewer columns" : "More columns (branch, project, currency, tax)"}
        </button>
      </div>
      <div
        ref={containerRef}
        role="grid"
        aria-label={mode === "journal" ? "Journal lines" : "Cashbook lines"}
        className="overflow-auto rounded-[var(--radius-card)] border border-[var(--vinea-border)]"
      >
        <table className="w-full border-collapse text-sm">
          <thead className="bg-[var(--vinea-surface-sunken)] text-xs uppercase tracking-wide text-[var(--vinea-ink-subtle)]">
            <tr>
              <th className="px-3 py-2 text-left">Account</th>
              <th className="px-3 py-2 text-left">Description</th>
              {showExtra && <th className="px-3 py-2 text-left">Branch</th>}
              {showExtra && <th className="px-3 py-2 text-left">Project</th>}
              {showExtra && <th className="px-3 py-2 text-left">Currency / rate</th>}
              {showExtra && <th className="px-3 py-2 text-left">Tax code</th>}
              {mode === "journal" ? (
                <>
                  <th className="px-3 py-2 text-right">Debit</th>
                  <th className="px-3 py-2 text-right">Credit</th>
                </>
              ) : (
                <>
                  <th className="px-3 py-2 text-right">Amount</th>
                  <th className="px-3 py-2 text-center">Tax incl.</th>
                </>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--vinea-border)]">
            {rows.map((row, r) => {
              let col = 0;
              return (
                <tr key={row.id} className={cn(activeCell?.row === r && "bg-[var(--vinea-brand-soft)]/30")}>
                  <td data-row={r} data-col={col++} className="min-w-48 p-1">
                    <Combobox
                      options={accountOptions}
                      value={row.accountId}
                      onValueChange={(v) => updateRow(r, { accountId: v })}
                      placeholder="Account…"
                      className="h-8"
                      onFocus={() => setActiveCell({ row: r, col: 0 })}
                    />
                  </td>
                  <td data-row={r} data-col={col++} className="p-1">
                    <input
                      value={row.description}
                      onChange={(e) => updateRow(r, { description: e.target.value })}
                      onKeyDown={(e) => onCellKeyDown(e, r, 1)}
                      onFocus={() => setActiveCell({ row: r, col: 1 })}
                      className="h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 focus:border-[var(--vinea-brand)]"
                      placeholder="Line description"
                    />
                  </td>
                  {showExtra && (
                    <td data-row={r} data-col={col++} className="min-w-36 p-1">
                      <Combobox
                        options={branchOptions}
                        value={row.branchId}
                        onValueChange={(v) => updateRow(r, { branchId: v })}
                        placeholder="Branch…"
                        className="h-8"
                      />
                    </td>
                  )}
                  {showExtra && (
                    <td data-row={r} data-col={col++} className="min-w-36 p-1">
                      <Combobox
                        options={projectOptions}
                        value={row.projectId}
                        onValueChange={(v) => updateRow(r, { projectId: v })}
                        placeholder="Project…"
                        className="h-8"
                      />
                    </td>
                  )}
                  {showExtra && (
                    <td data-row={r} data-col={col++} className="min-w-44 p-1">
                      <div className="flex gap-1">
                        <Combobox
                          options={currencyOptions}
                          value={row.currencyId}
                          onValueChange={(v) => onCurrencyChange(r, row, v)}
                          placeholder="Currency…"
                          className="h-8 w-24"
                        />
                        {row.currencyId && row.currencyId !== baseCurrencyId && (
                          <input
                            value={row.exchangeRate}
                            onChange={(e) => updateRow(r, { exchangeRate: e.target.value })}
                            inputMode="decimal"
                            placeholder="Rate"
                            className="h-8 w-20 rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] bg-transparent px-2 text-right font-mono text-xs"
                          />
                        )}
                      </div>
                    </td>
                  )}
                  {showExtra && (
                    <td data-row={r} data-col={col++} className="min-w-36 p-1">
                      <Combobox
                        options={taxCodeOptions}
                        value={row.taxCodeId}
                        onValueChange={(v) => updateRow(r, { taxCodeId: v })}
                        placeholder="Tax code…"
                        className="h-8"
                      />
                    </td>
                  )}
                  {mode === "journal" ? (
                    <>
                      <td data-row={r} data-col={col++} className="w-32 p-1">
                        <input
                          value={displayAmount(row.debit, activeCell?.row === r && activeCell.col === 100)}
                          onChange={(e) => updateRow(r, { debit: e.target.value, credit: e.target.value ? "" : row.credit })}
                          onKeyDown={(e) => onCellKeyDown(e, r, 100)}
                          onFocus={() => setActiveCell({ row: r, col: 100 })}
                          inputMode="decimal"
                          className="h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 text-right font-mono tabular-nums focus:border-[var(--vinea-brand)]"
                          placeholder="0"
                        />
                      </td>
                      <td data-row={r} data-col={col++} className="w-32 p-1">
                        <input
                          value={displayAmount(row.credit, activeCell?.row === r && activeCell.col === 101)}
                          onChange={(e) => updateRow(r, { credit: e.target.value, debit: e.target.value ? "" : row.debit })}
                          onKeyDown={(e) => onCellKeyDown(e, r, 101)}
                          onFocus={() => setActiveCell({ row: r, col: 101 })}
                          inputMode="decimal"
                          className="h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 text-right font-mono tabular-nums focus:border-[var(--vinea-brand)]"
                          placeholder="0"
                        />
                      </td>
                    </>
                  ) : (
                    <>
                      <td data-row={r} data-col={col++} className="w-32 p-1">
                        <input
                          value={displayAmount(row.amount, activeCell?.row === r && activeCell.col === 102)}
                          onChange={(e) => updateRow(r, { amount: e.target.value })}
                          onKeyDown={(e) => onCellKeyDown(e, r, 102)}
                          onFocus={() => setActiveCell({ row: r, col: 102 })}
                          inputMode="decimal"
                          className="h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 text-right font-mono tabular-nums focus:border-[var(--vinea-brand)]"
                          placeholder="0"
                        />
                      </td>
                      <td className="w-16 p-1 text-center">
                        <input
                          type="checkbox"
                          checked={row.taxInclusive}
                          onChange={(e) => updateRow(r, { taxInclusive: e.target.checked })}
                          className="size-4 accent-[var(--vinea-brand)]"
                        />
                      </td>
                    </>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
        <button
          type="button"
          onClick={addRow}
          className="w-full border-t border-[var(--vinea-border)] px-3 py-2 text-left text-xs font-medium text-[var(--vinea-brand)] hover:bg-[var(--vinea-surface-sunken)]"
        >
          + Add line
        </button>
      </div>
    </div>
  );
}

