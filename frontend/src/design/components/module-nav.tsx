"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, LayoutGrid } from "lucide-react";
import { cn } from "@/lib/cn";

export interface NavLeaf {
  label: string;
  phase?: string; // set when the screen belongs to a later phase — renders disabled with a tag
}

export interface NavIntent {
  label: "Maintenance" | "Transactions" | "Enquiries" | "Reports";
  items: NavLeaf[];
}

export interface NavModule {
  label: string;
  phase?: string; // whole module not live yet
  permission?: string; // required to see a *live* module at all; ignored once phase-tagged
  intents: NavIntent[];
}

/**
 * Sage-style explorer tree: module first, Maintenance/Transactions/Enquiries/Reports
 * nested per module (matches the owner's reference UI) — distinct from the top-bar
 * intents, which stay as quick filters/command-palette groups, not the primary tree.
 */
export const navModules: NavModule[] = [
  {
    label: "Common",
    permission: "common:setup_currencies",
    intents: [{ label: "Maintenance", items: [{ label: "Company details" }, { label: "Foreign currency" }] }],
  },
  {
    label: "Tax",
    permission: "common:setup_taxes",
    intents: [{ label: "Maintenance", items: [{ label: "Tax types" }] }],
  },
  {
    label: "General Ledger",
    permission: "gl:reports_view",
    intents: [
      {
        label: "Maintenance",
        items: [
          { label: "Chart of accounts" },
          { label: "Branches" },
          { label: "Transaction types" },
          { label: "Defaults" },
          { label: "Rename account" },
          { label: "Projects" },
        ],
      },
      { label: "Transactions", items: [{ label: "Journal batches" }, { label: "Cashbook batches" }] },
      { label: "Enquiries", items: [{ label: "Account enquiry" }, { label: "Trial balance enquiry" }] },
      {
        label: "Reports",
        items: [
          { label: "Account transactions" },
          { label: "Trial balance" },
          { label: "Chart of accounts" },
          { label: "Bank reconciliation", phase: "P8" },
          { label: "Cashbooks", phase: "P8" },
          { label: "Balance sheet", phase: "P10" },
          { label: "Income statement", phase: "P10" },
        ],
      },
    ],
  },
  {
    label: "Accounts Receivable",
    phase: "P4",
    intents: [
      { label: "Maintenance", items: [{ label: "Customers" }, { label: "Sales reps" }] },
      { label: "Transactions", items: [{ label: "Invoice" }, { label: "Credit note" }, { label: "Allocate" }] },
      { label: "Enquiries", items: [{ label: "Customer enquiry" }] },
      { label: "Reports", items: [{ label: "Age analysis" }, { label: "Statements" }] },
    ],
  },
  {
    label: "Accounts Payable",
    phase: "P4",
    intents: [
      { label: "Maintenance", items: [{ label: "Suppliers" }] },
      { label: "Transactions", items: [{ label: "GRV" }, { label: "Purchase order" }, { label: "Return to supplier" }] },
      { label: "Enquiries", items: [{ label: "Supplier enquiry" }] },
      { label: "Reports", items: [{ label: "Age analysis" }] },
    ],
  },
  {
    label: "Inventory",
    phase: "P5",
    intents: [
      { label: "Maintenance", items: [{ label: "Items" }, { label: "Warehouses" }] },
      { label: "Transactions", items: [{ label: "Adjustments" }, { label: "Transfers" }, { label: "Counts" }] },
      { label: "Reports", items: [{ label: "Valuation" }, { label: "Movement" }] },
    ],
  },
  {
    label: "Order Entry",
    phase: "P6",
    intents: [{ label: "Transactions", items: [{ label: "Sales order" }, { label: "Purchase order" }] }],
  },
  {
    label: "Fixed Assets",
    phase: "P9",
    intents: [{ label: "Maintenance", items: [{ label: "Asset register" }] }],
  },
  {
    label: "Point of Sale",
    phase: "P11",
    intents: [{ label: "Transactions", items: [{ label: "Sales" }, { label: "Returns" }] }],
  },
  {
    label: "Bill of Materials",
    phase: "P12",
    intents: [{ label: "Transactions", items: [{ label: "Manufacture process" }] }],
  },
];

function IntentGroup({ intent, moduleDisabled }: { intent: NavIntent; moduleDisabled: boolean }) {
  const [open, setOpen] = useState(intent.label === "Transactions");
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        disabled={moduleDisabled}
        className="flex w-full items-center gap-1 rounded-[var(--radius-control)] px-2 py-1 text-xs font-medium text-[var(--vinea-ink-subtle)] hover:bg-[var(--vinea-surface-sunken)] disabled:hover:bg-transparent"
      >
        {open ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
        {intent.label}
      </button>
      {open && (
        <ul className="ml-4 space-y-0.5 border-l border-[var(--vinea-border)] pl-2">
          {intent.items.map((item) => {
            const disabled = moduleDisabled || !!item.phase;
            return (
              <li key={item.label} className="flex items-center justify-between gap-2">
                <span
                  className={cn(
                    "block flex-1 truncate rounded-[var(--radius-control)] px-2 py-1 text-sm",
                    disabled
                      ? "cursor-not-allowed text-[var(--vinea-ink-subtle)]"
                      : "cursor-pointer text-[var(--vinea-ink-muted)] hover:bg-[var(--vinea-surface-sunken)] hover:text-[var(--vinea-ink)]",
                  )}
                >
                  {item.label}
                </span>
                {item.phase && (
                  <span className="mr-2 rounded-full bg-[var(--vinea-surface-sunken)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--vinea-ink-subtle)]">
                    {item.phase}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function ModuleNode({ module }: { module: NavModule }) {
  const [open, setOpen] = useState(module.label === "General Ledger");
  const disabled = !!module.phase;
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "flex w-full items-center gap-1.5 rounded-[var(--radius-control)] px-2 py-1.5 text-sm font-medium",
          disabled ? "text-[var(--vinea-ink-subtle)]" : "text-[var(--vinea-ink)] hover:bg-[var(--vinea-surface-sunken)]",
        )}
      >
        {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        <span className="flex-1 truncate text-left">{module.label}</span>
        {module.phase && (
          <span className="rounded-full bg-[var(--vinea-surface-sunken)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--vinea-ink-subtle)]">
            {module.phase}
          </span>
        )}
      </button>
      {open && (
        <div className="ml-2 mt-0.5 space-y-1 border-l border-[var(--vinea-border)] pl-2">
          {module.intents.map((intent) => (
            <IntentGroup key={intent.label} intent={intent} moduleDisabled={disabled} />
          ))}
        </div>
      )}
    </div>
  );
}

/** Module-first explorer tree — mirrors the owner's Sage Evolution reference. */
export function ModuleNav({ permissions }: { permissions: Set<string> }) {
  const visibleModules = navModules.filter(
    (module) => module.phase || !module.permission || permissions.has(module.permission),
  );
  return (
    <nav className="space-y-3">
      <div className="flex items-center gap-1.5 rounded-[var(--radius-control)] px-2 py-1.5 text-sm font-medium text-[var(--vinea-ink)] hover:bg-[var(--vinea-surface-sunken)]">
        <LayoutGrid className="size-3.5 text-[var(--vinea-ink-subtle)]" />
        My Desktop
      </div>
      <div className="space-y-0.5">
        {visibleModules.map((module) => (
          <ModuleNode key={module.label} module={module} />
        ))}
      </div>
    </nav>
  );
}

