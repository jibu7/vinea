"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/cn";

export type IntentLabel = "Maintenance" | "Transactions" | "Enquiries" | "Reports";

export interface NavItem {
  label: string;
  module: string;
  /** Required to see this *live* item at all; ignored once phase-tagged. */
  permission?: string;
  /** Set when the screen belongs to a later phase — always rendered, disabled, tagged. */
  phase?: string;
}

export interface NavIntent {
  label: IntentLabel;
  items: NavItem[];
}

/**
 * Intent-first sidebar tree per Master Plan Appendix C: Maintenance / Transactions /
 * Enquiries / Reports at the top, modules grouped underneath each. The command
 * palette (Ctrl+K) flattens the same data so search reaches every screen regardless
 * of which intent it lives under.
 */
export const navIntents: NavIntent[] = [
  {
    label: "Maintenance",
    items: [
      { label: "Company details", module: "Common", permission: "company:read" },
      { label: "Foreign currency", module: "Common", permission: "common:setup_currencies" },
      { label: "Tax types", module: "Tax", permission: "common:setup_taxes" },
      { label: "Chart of accounts", module: "General Ledger", permission: "gl:setup_manage" },
      { label: "Branches", module: "General Ledger", permission: "common:setup_branches" },
      { label: "Transaction types", module: "General Ledger", permission: "gl:setup_manage" },
      { label: "Defaults", module: "General Ledger", permission: "gl:setup_manage" },
      { label: "Rename account", module: "General Ledger", permission: "gl:setup_manage" },
      { label: "Projects", module: "General Ledger", permission: "projects:manage" },
      { label: "Customers", module: "Accounts Receivable", phase: "P4" },
      { label: "Sales reps", module: "Accounts Receivable", phase: "P4" },
      { label: "Suppliers", module: "Accounts Payable", phase: "P4" },
      { label: "Items", module: "Inventory", phase: "P5" },
      { label: "Warehouses", module: "Inventory", phase: "P5" },
      { label: "Order defaults", module: "Order Entry", phase: "P6" },
      { label: "BOM items & defaults", module: "Bill of Materials", phase: "P12" },
      { label: "Tills & types", module: "Point of Sale", phase: "P11" },
      { label: "Asset categories", module: "Fixed Assets", phase: "P9" },
    ],
  },
  {
    label: "Transactions",
    items: [
      { label: "Journal batches", module: "General Ledger", permission: "gl:journal_post" },
      { label: "Cashbook batches", module: "General Ledger", permission: "gl:journal_post" },
      { label: "Invoice", module: "Accounts Receivable", phase: "P4" },
      { label: "Credit note", module: "Accounts Receivable", phase: "P4" },
      { label: "Allocate", module: "Accounts Receivable", phase: "P4" },
      { label: "GRV", module: "Accounts Payable", phase: "P4" },
      { label: "Purchase order", module: "Accounts Payable", phase: "P4" },
      { label: "Return to supplier", module: "Accounts Payable", phase: "P4" },
      { label: "Sales order", module: "Order Entry", phase: "P6" },
      { label: "Adjustments", module: "Inventory", phase: "P5" },
      { label: "Transfers", module: "Inventory", phase: "P5" },
      { label: "Counts", module: "Inventory", phase: "P5" },
      { label: "Manufacture process", module: "Bill of Materials", phase: "P12" },
      { label: "Sales", module: "Point of Sale", phase: "P11" },
      { label: "Returns", module: "Point of Sale", phase: "P11" },
    ],
  },
  {
    label: "Enquiries",
    items: [
      { label: "Account enquiry", module: "General Ledger", permission: "gl:reports_view" },
      { label: "Trial balance enquiry", module: "General Ledger", permission: "gl:reports_view" },
      { label: "Customer enquiry", module: "Accounts Receivable", phase: "P4" },
      { label: "Supplier enquiry", module: "Accounts Payable", phase: "P4" },
      { label: "Item enquiry", module: "Inventory", phase: "P5" },
    ],
  },
  {
    label: "Reports",
    items: [
      { label: "Account transactions", module: "General Ledger", permission: "gl:reports_view" },
      { label: "Trial balance", module: "General Ledger", permission: "reporting:trial_balance_view" },
      { label: "Chart of accounts", module: "General Ledger", permission: "gl:reports_view" },
      { label: "Bank reconciliation", module: "General Ledger", phase: "P8" },
      { label: "Cashbooks", module: "General Ledger", phase: "P8" },
      { label: "Balance sheet", module: "General Ledger", phase: "P10" },
      { label: "Income statement", module: "General Ledger", phase: "P10" },
      { label: "Age analysis", module: "Accounts Receivable", phase: "P4" },
      { label: "Statements", module: "Accounts Receivable", phase: "P4" },
      { label: "Valuation", module: "Inventory", phase: "P5" },
      { label: "Movement", module: "Inventory", phase: "P5" },
      { label: "Sales analyses", module: "Inventory", phase: "P10" },
      { label: "Slow movers", module: "Inventory", phase: "P10" },
    ],
  },
];

function groupByModule(items: NavItem[]): Array<[string, NavItem[]]> {
  const order: string[] = [];
  const byModule = new Map<string, NavItem[]>();
  for (const item of items) {
    if (!byModule.has(item.module)) {
      byModule.set(item.module, []);
      order.push(item.module);
    }
    byModule.get(item.module)!.push(item);
  }
  return order.map((module) => [module, byModule.get(module)!]);
}

function IntentNode({ intent, permissions }: { intent: NavIntent; permissions: Set<string> }) {
  const [open, setOpen] = useState(intent.label === "Maintenance" || intent.label === "Transactions");
  const visibleItems = intent.items.filter((item) => item.phase || !item.permission || permissions.has(item.permission));
  if (visibleItems.length === 0) return null;

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-1.5 rounded-[var(--radius-control)] px-2 py-1.5 text-sm font-medium text-[var(--vinea-ink)] hover:bg-[var(--vinea-surface-sunken)]"
      >
        {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        {intent.label}
      </button>
      {open && (
        <div className="ml-2 mt-0.5 space-y-2 border-l border-[var(--vinea-border)] pl-2">
          {groupByModule(visibleItems).map(([module, items]) => (
            <div key={module}>
              <p className="px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-[var(--vinea-ink-subtle)]">
                {module}
              </p>
              <ul className="space-y-0.5">
                {items.map((item) => {
                  const disabled = !!item.phase;
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
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Intent-first sidebar tree — Maintenance/Transactions/Enquiries/Reports per Appendix C. */
export function SidebarNav({ permissions }: { permissions: Set<string> }) {
  return (
    <nav className="space-y-1">
      {navIntents.map((intent) => (
        <IntentNode key={intent.label} intent={intent} permissions={permissions} />
      ))}
    </nav>
  );
}

