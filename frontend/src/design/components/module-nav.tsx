"use client";

import { useState } from "react";
import Link from "next/link";
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
  /** Real route, once the screen exists; items without one fall back to a "coming soon" toast. */
  href?: string;
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
      { label: "Company details", module: "Common", permission: "company:read", href: "/maintenance/company-details" },
      { label: "Foreign currency", module: "Common", permission: "common:setup_currencies", href: "/maintenance/currencies" },
      { label: "Tax types", module: "Tax", permission: "common:setup_taxes", href: "/maintenance/taxes" },
      { label: "Chart of accounts", module: "General Ledger", permission: "gl:setup_manage", href: "/maintenance/chart-of-accounts" },
      { label: "Branches", module: "General Ledger", permission: "common:setup_branches", href: "/maintenance/branches" },
      { label: "Transaction types", module: "General Ledger", permission: "gl:setup_manage", href: "/maintenance/transaction-types" },
      { label: "Defaults", module: "General Ledger", permission: "gl:setup_manage", href: "/maintenance/defaults" },
      { label: "Rename account", module: "General Ledger", permission: "gl:setup_manage", href: "/maintenance/rename-account" },
      { label: "Projects", module: "General Ledger", permission: "projects:manage", href: "/maintenance/projects" },
      { label: "Customers", module: "Accounts Receivable", permission: "ar:reports_view", href: "/maintenance/customers" },
      { label: "Sales reps", module: "Accounts Receivable", permission: "ar:reports_view", href: "/maintenance/sales-reps" },
      { label: "Payment terms", module: "Accounts Receivable", permission: "ar:reports_view", href: "/maintenance/payment-terms" },
      { label: "Ageing bucket sets", module: "Accounts Receivable", permission: "ar:reports_view", href: "/maintenance/ageing-bucket-sets" },
      { label: "Transaction types", module: "Accounts Receivable", permission: "ar:reports_view", href: "/maintenance/ar-transaction-types" },
      { label: "Defaults", module: "Accounts Receivable", permission: "ar:reports_view", href: "/maintenance/ar-ap-defaults" },
      { label: "Rename customer code", module: "Accounts Receivable", permission: "ar:setup_manage", href: "/maintenance/rename-partner-code?role=ar" },
      { label: "Suppliers", module: "Accounts Payable", permission: "ap:reports_view", href: "/maintenance/suppliers" },
      // Payment terms and ageing bucket sets are one shared master each, listed under both
      // modules so neither an AR-only nor an AP-only role has to borrow the other's nav.
      { label: "Payment terms", module: "Accounts Payable", permission: "ap:reports_view", href: "/maintenance/payment-terms" },
      { label: "Ageing bucket sets", module: "Accounts Payable", permission: "ap:reports_view", href: "/maintenance/ageing-bucket-sets" },
      { label: "Transaction types", module: "Accounts Payable", permission: "ap:reports_view", href: "/maintenance/ap-transaction-types" },
      { label: "Defaults", module: "Accounts Payable", permission: "ap:reports_view", href: "/maintenance/ar-ap-defaults" },
      { label: "Rename supplier code", module: "Accounts Payable", permission: "ap:setup_manage", href: "/maintenance/rename-partner-code?role=ap" },
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
      { label: "Journal batches", module: "General Ledger", permission: "gl:journal_post", href: "/gl/journal-batches/new" },
      { label: "Cashbook batches", module: "General Ledger", permission: "gl:journal_post", href: "/gl/cashbook-batches/new" },
      // Appendix C's AR block, in the owner's order, plus Receipt (the settlement side of
      // the same subledger) and "Account receivable batches" — the spec lists AR batches and
      // both are P4 screens, so they belong in the tree from the start rather than appearing
      // when they happen to be built.
      { label: "Invoice", module: "Accounts Receivable", phase: "P4" },
      { label: "Credit note", module: "Accounts Receivable", phase: "P4" },
      { label: "Receipt", module: "Accounts Receivable", phase: "P4" },
      { label: "Allocate", module: "Accounts Receivable", phase: "P4" },
      { label: "Account receivable batches", module: "Accounts Receivable", phase: "P4" },
      // GRV and Purchase order are P6: goods receipt and the three-way match are the
      // purchasing cycle, not the AP subledger P4 builds.
      { label: "GRV", module: "Accounts Payable", phase: "P6" },
      { label: "Purchase order", module: "Accounts Payable", phase: "P6" },
      { label: "Supplier invoice", module: "Accounts Payable", phase: "P4" },
      { label: "Return to supplier", module: "Accounts Payable", phase: "P4" },
      { label: "Payment", module: "Accounts Payable", phase: "P4" },
      { label: "Allocate", module: "Accounts Payable", phase: "P4" },
      { label: "Account payable batches", module: "Accounts Payable", phase: "P4" },
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
      { label: "Account enquiry", module: "General Ledger", permission: "gl:reports_view", href: "/gl/enquiries/account" },
      { label: "Trial balance enquiry", module: "General Ledger", permission: "gl:reports_view", href: "/gl/enquiries/trial-balance" },
      { label: "Customer enquiry", module: "Accounts Receivable", phase: "P4" },
      { label: "Supplier enquiry", module: "Accounts Payable", phase: "P4" },
      { label: "Item enquiry", module: "Inventory", phase: "P5" },
    ],
  },
  {
    label: "Reports",
    items: [
      { label: "Account transactions", module: "General Ledger", permission: "gl:reports_view", href: "/gl/reports/account-transactions" },
      { label: "Trial balance", module: "General Ledger", permission: "gl:reports_view", href: "/gl/reports/trial-balance" },
      { label: "Chart of accounts", module: "General Ledger", permission: "gl:reports_view", href: "/gl/reports/chart-of-accounts" },
      { label: "Bank reconciliation", module: "General Ledger", phase: "P8" },
      { label: "Cashbooks", module: "General Ledger", phase: "P8" },
      { label: "Balance sheet", module: "General Ledger", phase: "P10" },
      { label: "Income statement", module: "General Ledger", phase: "P10" },
      // Appendix C's "Reports → AR / AP | Age analyses, Allocation, Listings, Statements" in
      // that order, per module, plus the Transaction listing P4 step 8 adds. Ageing,
      // allocation, statements and transaction listings all exist on both sides of the
      // subledger; only the partner listing is module-specific.
      { label: "Age analysis", module: "Accounts Receivable", phase: "P4" },
      { label: "Allocation", module: "Accounts Receivable", phase: "P4" },
      { label: "Customer listing", module: "Accounts Receivable", phase: "P4" },
      { label: "Statements", module: "Accounts Receivable", phase: "P4" },
      { label: "Transaction listing", module: "Accounts Receivable", phase: "P4" },
      { label: "Age analysis", module: "Accounts Payable", phase: "P4" },
      { label: "Allocation", module: "Accounts Payable", phase: "P4" },
      { label: "Supplier listing", module: "Accounts Payable", phase: "P4" },
      { label: "Statements", module: "Accounts Payable", phase: "P4" },
      { label: "Transaction listing", module: "Accounts Payable", phase: "P4" },
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
                  const itemClass = cn(
                    "block flex-1 truncate rounded-[var(--radius-control)] px-2 py-1 text-sm",
                    disabled
                      ? "cursor-not-allowed text-[var(--vinea-ink-subtle)]"
                      : "cursor-pointer text-[var(--vinea-ink-muted)] hover:bg-[var(--vinea-surface-sunken)] hover:text-[var(--vinea-ink)]",
                  );
                  return (
                    <li key={item.label} className="flex items-center justify-between gap-2">
                      {!disabled && item.href ? (
                        <Link href={item.href} className={itemClass}>
                          {item.label}
                        </Link>
                      ) : (
                        <span className={itemClass}>{item.label}</span>
                      )}
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

