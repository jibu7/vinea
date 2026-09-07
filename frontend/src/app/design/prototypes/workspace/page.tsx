"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Command as CommandIcon } from "lucide-react";
import { Button } from "@/design/components/button";
import { Input, Field } from "@/design/components/input";
import { DatePicker } from "@/design/components/date-picker";
import { StatusChip } from "@/design/components/status-chip";
import { LineGrid, type LineGridRow } from "@/design/components/line-grid";
import { Money } from "@/design/components/money";
import { CommandPalette, type CommandPaletteItem } from "@/design/components/command-palette";
import { ThemeToggle } from "@/design/components/theme-toggle";
import { RWF } from "@/lib/format";

const accountOptions = [
  { value: "1000", label: "1000 · Bank — BK RWF Current" },
  { value: "1100", label: "1100 · Petty Cash" },
  { value: "4000", label: "4000 · Sales — Wine" },
  { value: "5000", label: "5000 · Cost of Goods Sold" },
  { value: "6000", label: "6000 · Rent Expense" },
  { value: "6200", label: "6200 · Salaries & Wages" },
];

const paletteItems: CommandPaletteItem[] = [
  { id: "post", label: "Post entry", group: "This document", shortcut: "Ctrl Enter", onSelect: () => {} },
  { id: "cancel", label: "Cancel edit", group: "This document", shortcut: "Esc", onSelect: () => {} },
  { id: "new-je", label: "New journal entry", group: "Create", onSelect: () => {} },
  { id: "switch-co", label: "Switch company", group: "Navigate", onSelect: () => {} },
];

function toNumber(v: string) {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

export default function DocumentWorkspacePrototype() {
  const [date, setDate] = useState<Date>(new Date(2026, 8, 5));
  const [rows, setRows] = useState<LineGridRow[]>([
    { id: "1", accountId: "6200", description: "September payroll — accrual", debit: "2400000", credit: "" },
    { id: "2", accountId: "1000", description: "September payroll — accrual", debit: "", credit: "1900000" },
    { id: "3", accountId: "5000", description: "PAYE withheld", debit: "", credit: "500000" },
  ]);

  const totals = useMemo(() => {
    const debit = rows.reduce((s, r) => s + toNumber(r.debit), 0);
    const credit = rows.reduce((s, r) => s + toNumber(r.credit), 0);
    return { debit, credit, difference: debit - credit };
  }, [rows]);

  const balanced = totals.difference === 0;

  return (
    <div className="flex min-h-screen flex-col" data-density="dense">
      <CommandPalette items={paletteItems} />

      <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href="/design/prototypes/dashboard" className="text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]">
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <h1 className="font-display text-lg font-semibold">Journal Batch</h1>
            <p className="text-xs text-[var(--vinea-ink-muted)]">Batch JE-2026-00044 · draft</p>
          </div>
          <StatusChip tone={balanced ? "success" : "danger"}>{balanced ? "Balanced" : "Unbalanced"}</StatusChip>
        </div>
        <div className="flex items-center gap-2">
          <button className="flex items-center gap-2 rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] px-3 py-1.5 text-sm text-[var(--vinea-ink-subtle)]">
            <CommandIcon className="size-3.5" /> Search
            <kbd className="rounded border border-[var(--vinea-border)] px-1 text-[10px]">Ctrl K</kbd>
          </button>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 overflow-auto px-6 py-6">
        <div className="mx-auto max-w-4xl space-y-6">
          <div className="grid grid-cols-1 gap-4 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-5 sm:grid-cols-3">
            <Field label="Date">
              <DatePicker value={date} onValueChange={setDate} />
            </Field>
            <Field label="Reference">
              <Input defaultValue="PAYROLL-SEP-26" />
            </Field>
            <Field label="Description">
              <Input defaultValue="September payroll accrual" />
            </Field>
          </div>

          <LineGrid rows={rows} onRowsChange={setRows} accountOptions={accountOptions} />
        </div>
      </main>

      <footer className="sticky bottom-0 border-t border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3 shadow-[var(--elevation-2)]">
        <div className="mx-auto flex max-w-4xl items-center justify-between">
          <div className="flex gap-8 text-sm">
            <div>
              <p className="text-xs text-[var(--vinea-ink-subtle)]">Total debit</p>
              <Money amount={totals.debit} currency={RWF} className="font-semibold" />
            </div>
            <div>
              <p className="text-xs text-[var(--vinea-ink-subtle)]">Total credit</p>
              <Money amount={totals.credit} currency={RWF} className="font-semibold" />
            </div>
            <div>
              <p className="text-xs text-[var(--vinea-ink-subtle)]">Difference</p>
              <Money
                amount={totals.difference}
                currency={RWF}
                className={balanced ? "font-semibold text-[var(--vinea-success)]" : "font-semibold text-[var(--vinea-danger)]"}
              />
            </div>
          </div>
          <div className="flex gap-2">
            <Button variant="ghost">Cancel (Esc)</Button>
            <Button variant="primary" disabled={!balanced}>Post (Ctrl+Enter)</Button>
          </div>
        </div>
      </footer>
    </div>
  );
}
