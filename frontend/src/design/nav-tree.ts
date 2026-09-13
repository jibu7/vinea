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
  /** Required to see this *live* item at all; ignored once phase-tagged. A list means **any
   * of** — used where the endpoint behind the screen accepts more than one permission, as
   * the valuation report does (`inv:reports_view` or `reporting:inventory_valuation_view`),
   * so the nav gates on exactly what the API gates on rather than on a narrower guess. */
  permission?: string | string[];
  /** Set when the screen belongs to a later phase — always rendered, disabled, tagged. */
  phase?: string;
  /** Real route, once the screen exists; items without one fall back to a "coming soon" toast. */
  href?: string;
}

/** Whether `permissions` opens this item. A phase-tagged row is always rendered (disabled),
 * an untagged row with no permission is open to everyone, and a list of permissions means
 * **any of** them — matching how the endpoints behind these screens gate themselves. One
 * predicate rather than a copy in the sidebar and another in the palette, because the two
 * drifting is a screen that is searchable and not navigable, or the reverse. */
export function navItemVisible(item: NavItem, permissions: Set<string>): boolean {
  if (item.phase) return true;
  if (!item.permission) return true;
  const required = Array.isArray(item.permission) ? item.permission : [item.permission];
  return required.some((permission) => permissions.has(permission));
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
      // Appendix C's Inventory block, in the owner's order: Items, Warehouses, Trans types,
      // Variable barcodes, UoM categories, Defaults, Rename Item Code. The last five had no
      // rows at all before P5 step 6 — the tree carried only the two the tag named — so the
      // screens land and the rows arrive in the same edit. "Variable barcodes" turned out to
      // be a P11 screen and keeps its tag; see the note beside it.
      { label: "Items", module: "Inventory", permission: "inv:reports_view", href: "/maintenance/inventory-items" },
      { label: "Warehouses", module: "Inventory", permission: "inv:reports_view", href: "/maintenance/warehouses" },
      { label: "Transaction types", module: "Inventory", permission: "inv:reports_view", href: "/maintenance/inv-transaction-types" },
      // The appendix's own row, and it is **not** the screen P5 built: a variable barcode is
      // the POS scale-label pattern — a prefix, item-code digits, then weight or price digits,
      // decoded at the till — so it belongs to P11 with the tills that read it. P5 step 6
      // shipped the plain per-item listing under this label by mistake; "Barcodes" below is
      // that screen, under the name it should have had.
      { label: "Variable barcodes", module: "Inventory", phase: "P11" },
      { label: "Barcodes", module: "Inventory", permission: "inv:reports_view", href: "/maintenance/barcodes" },
      { label: "Units of measure", module: "Inventory", permission: "inv:reports_view", href: "/maintenance/uom-categories" },
      { label: "Defaults", module: "Inventory", permission: "inv:reports_view", href: "/maintenance/inventory-defaults" },
      { label: "Rename item code", module: "Inventory", permission: "inv:item_rename", href: "/maintenance/rename-item-code" },
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
      // Appendix C's Inventory block, in the owner's order: Journal batches, Transfers,
      // Adjustments, Counts. The tree carried three of the four, out of order, under a P5
      // tag; the screens landed at P5 step 7 and the row the tag was covering arrived with
      // them. Counts opens on the posting permission because the sheet is what a stock-taker
      // fills in; Process is the step that checks `inv:count_process`, on the button.
      { label: "Journal batches", module: "Inventory", permission: "inv:transactions_adjust", href: "/inventory/journal-batches/new" },
      { label: "Transfers", module: "Inventory", permission: "inv:transactions_adjust", href: "/inventory/transfers" },
      { label: "Adjustments", module: "Inventory", permission: "inv:transactions_adjust", href: "/inventory/adjustments/new" },
      { label: "Counts", module: "Inventory", permission: "inv:transactions_adjust", href: "/inventory/counts" },
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
      { label: "Item enquiry", module: "Inventory", permission: "inv:reports_view", href: "/inventory/enquiry" },
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
      // Appendix C's Inventory block, in the owner's order: Movement, Count, Transaction,
      // Valuation. The tree carried Valuation and Movement — two of the four, reversed, both
      // tagged P5 — so the two missing rows arrive with the screens rather than being new
      // scope. Same shape of edit as the step-6 Maintenance and step-7 Transactions blocks.
      { label: "Movement", module: "Inventory", permission: "inv:reports_view", href: "/inventory/reports/movement" },
      { label: "Count", module: "Inventory", permission: "inv:reports_view", href: "/inventory/reports/counts" },
      { label: "Transaction", module: "Inventory", permission: "inv:reports_view", href: "/inventory/reports/transactions" },
      // The one row whose endpoint takes either permission: `reporting:inventory_valuation_view`
      // has existed since P1 for this report, and an accountant holding it and no `inv:*` right
      // can call the endpoint. Gating the nav on `inv:reports_view` alone would leave them a
      // screen they are allowed to open and cannot reach.
      { label: "Valuation", module: "Inventory", permission: ["inv:reports_view", "reporting:inventory_valuation_view"], href: "/inventory/reports/valuation" },
      { label: "Sales analyses", module: "Inventory", phase: "P10" },
      { label: "Slow movers", module: "Inventory", phase: "P10" },
    ],
  },
];
