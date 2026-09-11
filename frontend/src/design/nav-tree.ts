/**
 * The Appendix C navigation tree, as data.
 *
 * Split out of `module-nav.tsx` so it can be read by things that are not React: the
 * Playwright axe sweep (`e2e/nav-axe-sweep.spec.ts`) enumerates every `href` here and
 * visits it, which a `"use client"` module importing `next/link` and `lucide-react`
 * cannot be asked to do from a Node test runner. One definition, three consumers — the
 * sidebar, the Ctrl+K palette, and the sweep — so a screen added to this file is
 * navigable, searchable and accessibility-tested by the same edit.
 */
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
      { label: "Invoice", module: "Accounts Receivable", permission: "ar:transactions_post", href: "/ar/invoices/new" },
      { label: "Credit note", module: "Accounts Receivable", permission: "ar:transactions_post", href: "/ar/credit-notes/new" },
      { label: "Receipt", module: "Accounts Receivable", permission: "ar:transactions_post", href: "/ar/receipts/new" },
      { label: "Allocate", module: "Accounts Receivable", permission: "ar:transactions_post", href: "/ar/allocations/new" },
      // Not in the owner's tree, and neither is the document it acts on: a receipt dated
      // ahead books to the post-dated account and needs a person to bank it once it matures
      // (P4 decision 7). The service shipped as an endpoint and a job with no screen, which
      // made the instrument un-maturable by anyone using the product.
      { label: "Post-dated receipts", module: "Accounts Receivable", permission: "ar:reports_view", href: "/ar/post-dated" },
      { label: "Account receivable batches", module: "Accounts Receivable", permission: "ar:transactions_post", href: "/ar/batches/new" },
      // GRV and Purchase order are P6: goods receipt and the three-way match are the
      // purchasing cycle, not the AP subledger P4 builds. Supplier invoice, Receipt and
      // Payment are additions to the owner's list — master plan C.1.5.
      { label: "GRV", module: "Accounts Payable", phase: "P6" },
      { label: "Purchase order", module: "Accounts Payable", phase: "P6" },
      { label: "Supplier invoice", module: "Accounts Payable", permission: "ap:transactions_post", href: "/ap/supplier-invoices/new" },
      { label: "Return to supplier", module: "Accounts Payable", permission: "ap:transactions_post", href: "/ap/returns/new" },
      { label: "Payment", module: "Accounts Payable", permission: "ap:transactions_post", href: "/ap/payments/new" },
      { label: "Allocate", module: "Accounts Payable", permission: "ap:transactions_post", href: "/ap/allocations/new" },
      { label: "Post-dated payments", module: "Accounts Payable", permission: "ap:reports_view", href: "/ap/post-dated" },
      { label: "Account payable batches", module: "Accounts Payable", permission: "ap:transactions_post", href: "/ap/batches/new" },
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
      { label: "Customer enquiry", module: "Accounts Receivable", permission: "ar:reports_view", href: "/ar/enquiry" },
      { label: "Supplier enquiry", module: "Accounts Payable", permission: "ap:reports_view", href: "/ap/enquiry" },
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
      // that order, per module, plus Transaction listing — an addition to the owner's list,
      // master plan C.1.4. Ageing, allocation, statements and transaction listings all exist
      // on both sides of the subledger; only the partner listing is module-specific.
      { label: "Age analysis", module: "Accounts Receivable", permission: "ar:reports_view", href: "/ar/reports/age-analysis" },
      { label: "Allocation", module: "Accounts Receivable", permission: "ar:reports_view", href: "/ar/reports/allocations" },
      { label: "Customer listing", module: "Accounts Receivable", permission: "ar:reports_view", href: "/ar/reports/customer-listing" },
      { label: "Statements", module: "Accounts Receivable", permission: "ar:reports_view", href: "/ar/reports/statements" },
      { label: "Transaction listing", module: "Accounts Receivable", permission: "ar:reports_view", href: "/ar/reports/transactions" },
      { label: "Age analysis", module: "Accounts Payable", permission: "ap:reports_view", href: "/ap/reports/age-analysis" },
      { label: "Allocation", module: "Accounts Payable", permission: "ap:reports_view", href: "/ap/reports/allocations" },
      { label: "Supplier listing", module: "Accounts Payable", permission: "ap:reports_view", href: "/ap/reports/supplier-listing" },
      { label: "Statements", module: "Accounts Payable", permission: "ap:reports_view", href: "/ap/reports/statements" },
      { label: "Transaction listing", module: "Accounts Payable", permission: "ap:reports_view", href: "/ap/reports/transactions" },
      { label: "Valuation", module: "Inventory", phase: "P5" },
      { label: "Movement", module: "Inventory", phase: "P5" },
      { label: "Sales analyses", module: "Inventory", phase: "P10" },
      { label: "Slow movers", module: "Inventory", phase: "P10" },
    ],
  },
];
