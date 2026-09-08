"use client";

import { useEffect, useRef, useState } from "react";
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
  errors?: LineErrors;
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
  baseCurrencyId,
  rateForCurrency,
  rowDefaults,
}: LineGridProps) {
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
    if (!activeCell || ![100, 101, 102].includes(activeCell.col)) return;
    const el = document.activeElement;
    if (el instanceof HTMLInputElement) el.select();
  }, [activeCell]);

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

              return (
                <tr key={row.id} className={cn(activeCell?.row === r && "bg-[var(--vinea-brand-soft)]/30")}>
                  <td data-row={r} data-col={col++} className="min-w-48 p-1 align-top">
                    <Combobox
                      options={accountOptions}
                      value={row.accountId}
                      onValueChange={(v) => updateRow(r, { accountId: v })}
                      placeholder="Account…"
                      ariaLabel={`Account, row ${r + 1}`}
                      className={cn("h-8", accountErr && "border-[var(--vinea-danger)]")}
                      onFocus={() => startCellEdit(r, 0, "accountId", row.accountId)}
                      onKeyDown={(e) => onCellKeyDown(e, r, 0, "accountId")}
                    />
                    {accountErr && <p className="mt-0.5 px-1 text-xs text-[var(--vinea-danger)]">{accountErr}</p>}
                  </td>
                  <td data-row={r} data-col={col++} className="p-1 align-top">
                    <input
                      value={row.description}
                      onChange={(e) => updateRow(r, { description: e.target.value })}
                      onKeyDown={(e) => onCellKeyDown(e, r, 1, "description")}
                      onFocus={() => startCellEdit(r, 1, "description", row.description)}
                      aria-label={`Description, row ${r + 1}`}
                      className={cn(
                        "h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 focus:border-[var(--vinea-brand)]",
                        descErr && "border-[var(--vinea-danger)]",
                      )}
                      placeholder="Line description"
                    />
                    {descErr && <p className="mt-0.5 px-1 text-xs text-[var(--vinea-danger)]">{descErr}</p>}
                  </td>
                  {showExtra && (
                    <td data-row={r} data-col={col++} className="min-w-36 p-1 align-top">
                      <Combobox
                        options={branchOptions}
                        value={row.branchId}
                        onValueChange={(v) => updateRow(r, { branchId: v })}
                        placeholder="Branch…"
                        ariaLabel={`Branch, row ${r + 1}`}
                        className={cn("h-8", branchErr && "border-[var(--vinea-danger)]")}
                        onFocus={() => startCellEdit(r, 2, "branchId", row.branchId)}
                        onKeyDown={(e) => onCellKeyDown(e, r, 2, "branchId")}
                      />
                      {branchErr && <p className="mt-0.5 px-1 text-xs text-[var(--vinea-danger)]">{branchErr}</p>}
                    </td>
                  )}
                  {showExtra && (
                    <td data-row={r} data-col={col++} className="min-w-36 p-1 align-top">
                      <Combobox
                        options={projectOptions}
                        value={row.projectId}
                        onValueChange={(v) => updateRow(r, { projectId: v })}
                        placeholder="Project…"
                        ariaLabel={`Project, row ${r + 1}`}
                        className={cn("h-8", projectErr && "border-[var(--vinea-danger)]")}
                        onFocus={() => startCellEdit(r, 3, "projectId", row.projectId)}
                        onKeyDown={(e) => onCellKeyDown(e, r, 3, "projectId")}
                      />
                      {projectErr && <p className="mt-0.5 px-1 text-xs text-[var(--vinea-danger)]">{projectErr}</p>}
                    </td>
                  )}
                  {showExtra && (
                    <td data-row={r} data-col={col++} className="min-w-44 p-1 align-top">
                      <div className="flex gap-1">
                        <Combobox
                          options={currencyOptions}
                          value={row.currencyId}
                          onValueChange={(v) => onCurrencyChange(r, row, v)}
                          placeholder="Currency…"
                          ariaLabel={`Currency, row ${r + 1}`}
                          className={cn("h-8 w-24", currencyErr && "border-[var(--vinea-danger)]")}
                          onFocus={() => startCellEdit(r, 4, "currencyId", row.currencyId)}
                          onKeyDown={(e) => onCellKeyDown(e, r, 4, "currencyId")}
                        />
                        {row.currencyId && row.currencyId !== baseCurrencyId && (
                          <input
                            value={row.exchangeRate}
                            onChange={(e) => updateRow(r, { exchangeRate: e.target.value })}
                            onKeyDown={(e) => onCellKeyDown(e, r, 4, "exchangeRate")}
                            onFocus={() => startCellEdit(r, 4, "exchangeRate", row.exchangeRate)}
                            inputMode="decimal"
                            placeholder="Rate"
                            aria-label={`Exchange rate, row ${r + 1}`}
                            className="h-8 w-20 rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] bg-transparent px-2 text-right font-mono text-xs"
                          />
                        )}
                      </div>
                      {currencyErr && <p className="mt-0.5 px-1 text-xs text-[var(--vinea-danger)]">{currencyErr}</p>}
                    </td>
                  )}
                  {showExtra && (
                    <td data-row={r} data-col={col++} className="min-w-36 p-1 align-top">
                      <Combobox
                        options={taxCodeOptions}
                        value={row.taxCodeId}
                        onValueChange={(v) => updateRow(r, { taxCodeId: v })}
                        placeholder="Tax code…"
                        ariaLabel={`Tax code, row ${r + 1}`}
                        className={cn("h-8", taxErr && "border-[var(--vinea-danger)]")}
                        onFocus={() => startCellEdit(r, 5, "taxCodeId", row.taxCodeId)}
                        onKeyDown={(e) => onCellKeyDown(e, r, 5, "taxCodeId")}
                      />
                      {taxErr && <p className="mt-0.5 px-1 text-xs text-[var(--vinea-danger)]">{taxErr}</p>}
                    </td>
                  )}
                  {mode === "journal" ? (
                    <>
                      <td data-row={r} data-col={col++} className="w-32 p-1 align-top">
                        <input
                          value={displayAmount(row.debit, activeCell?.row === r && activeCell.col === 100)}
                          onChange={(e) => updateRow(r, { debit: e.target.value, credit: e.target.value ? "" : row.credit })}
                          onKeyDown={(e) => onCellKeyDown(e, r, 100, "debit")}
                          onFocus={() => startCellEdit(r, 100, "debit", row.debit)}
                          onBlur={() => setActiveCell(null)}
                          inputMode="decimal"
                          aria-label={`Debit, row ${r + 1}`}
                          className={cn(
                            "h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 text-right font-mono tabular-nums focus:border-[var(--vinea-brand)]",
                            debitErr && "border-[var(--vinea-danger)]",
                          )}
                          placeholder="0"
                        />
                        {debitErr && <p className="mt-0.5 text-right text-xs text-[var(--vinea-danger)]">{debitErr}</p>}
                      </td>
                      <td data-row={r} data-col={col++} className="w-32 p-1 align-top">
                        <input
                          value={displayAmount(row.credit, activeCell?.row === r && activeCell.col === 101)}
                          onChange={(e) => updateRow(r, { credit: e.target.value, debit: e.target.value ? "" : row.debit })}
                          onKeyDown={(e) => onCellKeyDown(e, r, 101, "credit")}
                          onFocus={() => startCellEdit(r, 101, "credit", row.credit)}
                          onBlur={() => setActiveCell(null)}
                          inputMode="decimal"
                          aria-label={`Credit, row ${r + 1}`}
                          className={cn(
                            "h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 text-right font-mono tabular-nums focus:border-[var(--vinea-brand)]",
                            creditErr && "border-[var(--vinea-danger)]",
                          )}
                          placeholder="0"
                        />
                        {creditErr && <p className="mt-0.5 text-right text-xs text-[var(--vinea-danger)]">{creditErr}</p>}
                      </td>
                    </>
                  ) : (
                    <>
                      <td data-row={r} data-col={col++} className="w-32 p-1 align-top">
                        <input
                          value={displayAmount(row.amount, activeCell?.row === r && activeCell.col === 102)}
                          onChange={(e) => updateRow(r, { amount: e.target.value })}
                          onKeyDown={(e) => onCellKeyDown(e, r, 102, "amount")}
                          onFocus={() => startCellEdit(r, 102, "amount", row.amount)}
                          onBlur={() => setActiveCell(null)}
                          inputMode="decimal"
                          aria-label={`Amount, row ${r + 1}`}
                          className={cn(
                            "h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 text-right font-mono tabular-nums focus:border-[var(--vinea-brand)]",
                            amountErr && "border-[var(--vinea-danger)]",
                          )}
                          placeholder="0"
                        />
                        {amountErr && <p className="mt-0.5 text-right text-xs text-[var(--vinea-danger)]">{amountErr}</p>}
                      </td>
                      <td className="w-16 p-1 text-center align-top">
                        <input
                          type="checkbox"
                          checked={row.taxInclusive}
                          onChange={(e) => updateRow(r, { taxInclusive: e.target.checked })}
                          onKeyDown={(e) => onCellKeyDown(e, r, 103)}
                          aria-label={`Tax inclusive, row ${r + 1}`}
                          className="mt-2 size-4 accent-[var(--vinea-brand)]"
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

