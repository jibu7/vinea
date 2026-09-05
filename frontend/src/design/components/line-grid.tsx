"use client";

import { useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { Combobox } from "./combobox";
import type { SelectOption } from "./select";

export interface LineGridRow {
  id: string;
  accountId: string;
  description: string;
  debit: string;
  credit: string;
}

/** Raw digits while editing; thousand-separated once the cell loses focus. */
function displayAmount(raw: string, editing?: boolean): string {
  if (editing || !raw) return raw;
  const n = Number(raw);
  return Number.isFinite(n) ? new Intl.NumberFormat("en-RW").format(n) : raw;
}

/**
 * Spreadsheet-grade line grid primitive shared by Journal/Cashbook workspaces.
 * Keyboard model: Tab/Enter move across cells, arrow keys navigate rows,
 * Enter on the last row adds a row. Full autosave/posting wiring lands in P3 step 5.
 */
export function LineGrid({
  rows,
  onRowsChange,
  accountOptions,
}: {
  rows: LineGridRow[];
  onRowsChange: (rows: LineGridRow[]) => void;
  accountOptions: SelectOption[];
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [activeCell, setActiveCell] = useState<{ row: number; col: number } | null>(null);

  function updateRow(index: number, patch: Partial<LineGridRow>) {
    const next = rows.slice();
    next[index] = { ...next[index], ...patch };
    onRowsChange(next);
  }

  function addRow() {
    onRowsChange([
      ...rows,
      { id: crypto.randomUUID(), accountId: "", description: "", debit: "", credit: "" },
    ]);
  }

  function focusCell(row: number, col: number) {
    const el = containerRef.current?.querySelector<HTMLElement>(
      `[data-row="${row}"][data-col="${col}"] input, [data-row="${row}"][data-col="${col}"] button`,
    );
    el?.focus();
    setActiveCell({ row, col });
  }

  function onKeyDown(e: React.KeyboardEvent, row: number, col: number) {
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

  return (
    <div ref={containerRef} role="grid" aria-label="Journal lines" className="overflow-auto rounded-[var(--radius-card)] border border-[var(--vinea-border)]">
      <table className="w-full border-collapse text-sm">
        <thead className="bg-[var(--vinea-surface-sunken)] text-xs uppercase tracking-wide text-[var(--vinea-ink-subtle)]">
          <tr>
            <th className="px-3 py-2 text-left">Account</th>
            <th className="px-3 py-2 text-left">Description</th>
            <th className="px-3 py-2 text-right">Debit</th>
            <th className="px-3 py-2 text-right">Credit</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--vinea-border)]">
          {rows.map((row, r) => (
            <tr key={row.id} className={cn(activeCell?.row === r && "bg-[var(--vinea-brand-soft)]/30")}>
              <td data-row={r} data-col={0} className="min-w-48 p-1">
                <Combobox
                  options={accountOptions}
                  value={row.accountId}
                  onValueChange={(v) => updateRow(r, { accountId: v })}
                  placeholder="Account…"
                  className="h-8"
                />
              </td>
              <td data-row={r} data-col={1} className="p-1">
                <input
                  value={row.description}
                  onChange={(e) => updateRow(r, { description: e.target.value })}
                  onKeyDown={(e) => onKeyDown(e, r, 1)}
                  onFocus={() => setActiveCell({ row: r, col: 1 })}
                  className="h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 focus:border-[var(--vinea-brand)]"
                  placeholder="Line description"
                />
              </td>
              <td data-row={r} data-col={2} className="w-32 p-1">
                <input
                  value={displayAmount(row.debit, activeCell?.row === r && activeCell.col === 2)}
                  onChange={(e) => updateRow(r, { debit: e.target.value, credit: e.target.value ? "" : row.credit })}
                  onKeyDown={(e) => onKeyDown(e, r, 2)}
                  onFocus={() => setActiveCell({ row: r, col: 2 })}
                  inputMode="decimal"
                  className="h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 text-right font-mono tabular-nums focus:border-[var(--vinea-brand)]"
                  placeholder="0"
                />
              </td>
              <td data-row={r} data-col={3} className="w-32 p-1">
                <input
                  value={displayAmount(row.credit, activeCell?.row === r && activeCell.col === 3)}
                  onChange={(e) => updateRow(r, { credit: e.target.value, debit: e.target.value ? "" : row.debit })}
                  onKeyDown={(e) => onKeyDown(e, r, 3)}
                  onFocus={() => setActiveCell({ row: r, col: 3 })}
                  inputMode="decimal"
                  className="h-8 w-full rounded-[var(--radius-control)] border border-transparent bg-transparent px-2 text-right font-mono tabular-nums focus:border-[var(--vinea-brand)]"
                  placeholder="0"
                />
              </td>
            </tr>
          ))}
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
  );
}
