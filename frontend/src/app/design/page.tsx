"use client";

import { useState } from "react";
import { AlertTriangle, Inbox } from "lucide-react";
import { Button } from "@/design/components/button";
import { Input, Field } from "@/design/components/input";
import { Select } from "@/design/components/select";
import { Combobox } from "@/design/components/combobox";
import { DatePicker } from "@/design/components/date-picker";
import { StatusChip } from "@/design/components/status-chip";
import { Table, THead, TBody, TR, TH, TD } from "@/design/components/table";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/design/components/tabs";
import { Dialog, DialogTrigger, DialogContent } from "@/design/components/dialog";
import { useToast } from "@/design/components/toast";
import { CommandPalette, type CommandPaletteItem } from "@/design/components/command-palette";
import { EmptyState } from "@/design/components/empty-state";
import { LineGrid, emptyLineGridRow, type LineGridRow } from "@/design/components/line-grid";
import { Money } from "@/design/components/money";
import { ThemeToggle } from "@/design/components/theme-toggle";
import { RWF, USD } from "@/lib/format";

const accountOptions = [
  { value: "1000", label: "1000 · Bank — BK RWF Current" },
  { value: "1100", label: "1100 · Petty Cash" },
  { value: "4000", label: "4000 · Sales — Wine" },
  { value: "5000", label: "5000 · Cost of Goods Sold" },
  { value: "6000", label: "6000 · Rent Expense" },
];

const paletteItems: CommandPaletteItem[] = [
  { id: "new-je", label: "New journal entry", group: "Create", shortcut: "N J", onSelect: () => {} },
  { id: "new-cb", label: "New cashbook entry", group: "Create", shortcut: "N C", onSelect: () => {} },
  { id: "switch-co", label: "Switch company", group: "Navigate", onSelect: () => {} },
  { id: "toggle-theme", label: "Toggle theme", group: "Preferences", onSelect: () => {} },
];

function DesignPageInner() {
  const toast = useToast();
  const [account, setAccount] = useState("");
  const [comboAccount, setComboAccount] = useState("");
  const [date, setDate] = useState<Date | null>(null);
  const [rows, setRows] = useState<LineGridRow[]>([
    emptyLineGridRow({ accountId: "1000", description: "Cash sale — invoice 1042", debit: "150000" }),
    emptyLineGridRow({ accountId: "4000", description: "Cash sale — invoice 1042", credit: "150000" }),
  ]);

  return (
    <main className="mx-auto max-w-5xl px-6 py-12" data-density="airy">
      <CommandPalette items={paletteItems} />
      <header className="mb-10 flex items-start justify-between">
        <div>
          <h1 className="font-display text-3xl font-semibold">Design System</h1>
          <p className="mt-2 text-[var(--vinea-ink-muted)]">
            Primitives for the Vinea hybrid UI — airy shell, dense work screens.
          </p>
        </div>
        <ThemeToggle />
      </header>

      <section className="mb-12 space-y-4">
        <h2 className="font-display text-xl font-medium">Buttons</h2>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="primary">Primary</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="danger">Danger</Button>
          <Button variant="primary" disabled>Disabled</Button>
          <Button variant="primary" size="sm">Small</Button>
          <Button variant="primary" size="lg">Large</Button>
        </div>
      </section>

      <section className="mb-12 space-y-4">
        <h2 className="font-display text-xl font-medium">Inputs</h2>
        <div className="grid max-w-md gap-4">
          <Field label="Reference">
            <Input placeholder="JE-2026-00042" />
          </Field>
          <Field label="Amount" error="Must be greater than zero">
            <Input placeholder="0" defaultValue="-100" />
          </Field>
        </div>
      </section>

      <section className="mb-12 space-y-4">
        <h2 className="font-display text-xl font-medium">Select / combobox</h2>
        <div className="grid max-w-md gap-4">
          <Field label="Account (select)">
            <Select options={accountOptions} value={account} onValueChange={setAccount} placeholder="Choose an account" />
          </Field>
          <Field label="Account (typeahead combobox)">
            <Combobox options={accountOptions} value={comboAccount} onValueChange={setComboAccount} placeholder="Search accounts…" />
          </Field>
          <Field label="Date (dd/MM/yyyy)">
            <DatePicker value={date} onValueChange={setDate} />
          </Field>
        </div>
      </section>

      <section className="mb-12 space-y-4">
        <h2 className="font-display text-xl font-medium">Status chips</h2>
        <div className="flex flex-wrap gap-2">
          <StatusChip tone="neutral">Draft</StatusChip>
          <StatusChip tone="success">Posted</StatusChip>
          <StatusChip tone="warning">Overdue</StatusChip>
          <StatusChip tone="danger">Unbalanced</StatusChip>
          <StatusChip tone="info">Open period</StatusChip>
        </div>
      </section>

      <section className="mb-12 space-y-4">
        <h2 className="font-display text-xl font-medium">Table</h2>
        <Table>
          <THead>
            <TR>
              <TH>Account</TH>
              <TH>Type</TH>
              <TH className="text-right">Balance</TH>
            </TR>
          </THead>
          <TBody>
            <TR>
              <TD>1000 · Bank — BK RWF Current</TD>
              <TD>Asset</TD>
              <TD className="text-right"><Money amount={4250000} currency={RWF} /></TD>
            </TR>
            <TR>
              <TD>4000 · Sales — Wine</TD>
              <TD>Income</TD>
              <TD className="text-right"><Money amount={-1850000} currency={RWF} /></TD>
            </TR>
          </TBody>
        </Table>
      </section>

      <section className="mb-12 space-y-4">
        <h2 className="font-display text-xl font-medium">Tabs</h2>
        <Tabs defaultValue="details">
          <TabsList>
            <TabsTrigger value="details">Details</TabsTrigger>
            <TabsTrigger value="history">History</TabsTrigger>
            <TabsTrigger value="attachments">Attachments</TabsTrigger>
          </TabsList>
          <TabsContent value="details" className="mt-3 text-sm text-[var(--vinea-ink-muted)]">
            Account details panel.
          </TabsContent>
          <TabsContent value="history" className="mt-3 text-sm text-[var(--vinea-ink-muted)]">
            Rename history panel.
          </TabsContent>
          <TabsContent value="attachments" className="mt-3 text-sm text-[var(--vinea-ink-muted)]">
            No attachments yet.
          </TabsContent>
        </Tabs>
      </section>

      <section className="mb-12 space-y-4">
        <h2 className="font-display text-xl font-medium">Dialog</h2>
        <Dialog>
          <DialogTrigger asChild>
            <Button variant="secondary">Open dialog</Button>
          </DialogTrigger>
          <DialogContent title="Reverse entry" description="This creates a reversing entry dated today.">
            <Field label="Reason">
              <Input placeholder="Duplicate posting" />
            </Field>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="ghost">Cancel</Button>
              <Button variant="danger">Reverse</Button>
            </div>
          </DialogContent>
        </Dialog>
      </section>

      <section className="mb-12 space-y-4">
        <h2 className="font-display text-xl font-medium">Toast</h2>
        <div className="flex gap-3">
          <Button
            variant="secondary"
            onClick={() => toast.show({ title: "Entry posted", description: "JE-2026-00042", tone: "success" })}
          >
            Show success toast
          </Button>
          <Button
            variant="secondary"
            onClick={() => toast.show({ title: "Unbalanced entry", description: "Debits must equal credits", tone: "danger" })}
          >
            Show error toast
          </Button>
        </div>
      </section>

      <section className="mb-12 space-y-4">
        <h2 className="font-display text-xl font-medium">Command palette</h2>
        <p className="text-sm text-[var(--vinea-ink-muted)]">Press <kbd className="rounded border border-[var(--vinea-border)] px-1">Ctrl</kbd>+<kbd className="rounded border border-[var(--vinea-border)] px-1">K</kbd> anywhere on this page.</p>
      </section>

      <section className="mb-12 space-y-4">
        <h2 className="font-display text-xl font-medium">Empty state</h2>
        <EmptyState
          icon={<Inbox className="size-8" />}
          title="No journal batches yet"
          description="Create your first journal entry to see it here."
          action={<Button variant="primary" size="sm">New journal entry</Button>}
        />
      </section>

      <section className="mb-12 space-y-4">
        <h2 className="font-display text-xl font-medium">Dense data grid</h2>
        <div data-density="dense">
          <LineGrid mode="journal" rows={rows} onRowsChange={setRows} accountOptions={accountOptions} />
        </div>
      </section>

      <section className="mb-12 space-y-4">
        <h2 className="font-display text-xl font-medium">Money formatting</h2>
        <div className="flex flex-wrap gap-6 text-sm">
          <div>RWF (0dp): <Money amount={1234567} currency={RWF} /></div>
          <div>USD (2dp): <Money amount={1234.5} currency={USD} /></div>
          <div>Negative: <Money amount={-42000} currency={RWF} /></div>
          <div className="flex items-center gap-1">
            <AlertTriangle className="size-4 text-[var(--vinea-warning)]" />
            Half-up display: <Money amount={99.995} currency={USD} />
          </div>
        </div>
      </section>
    </main>
  );
}

export default function DesignPage() {
  return <DesignPageInner />;
}
