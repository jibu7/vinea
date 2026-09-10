import { describe, expect, it } from "vitest";
import { navIntents, type IntentLabel } from "./module-nav";

/**
 * Appendix C of the Master Plan adopts the owner's menu ordering as the navigation contract.
 * This pins it: `[module, label, phase]` in declared order, for every intent. A phase that
 * goes from a tag to `null` is a screen landing — expected, and the reason to edit this file.
 * Anything else moving is a contract change and should be argued for, not slipped in.
 *
 * Snapshot taken after P4 step 6, where the only edit was the AR/AP Maintenance block:
 * Customers, Sales reps and Suppliers lost their `P4` tag, and the seven other AR/AP
 * maintenance screens were added in place. Nothing outside that block moved.
 */
const APPENDIX_C: Record<IntentLabel, Array<[string, string, string | null]>> = {
  "Maintenance": [
    ["Common", "Company details", null],
    ["Common", "Foreign currency", null],
    ["Tax", "Tax types", null],
    ["General Ledger", "Chart of accounts", null],
    ["General Ledger", "Branches", null],
    ["General Ledger", "Transaction types", null],
    ["General Ledger", "Defaults", null],
    ["General Ledger", "Rename account", null],
    ["General Ledger", "Projects", null],
    ["Accounts Receivable", "Customers", null],
    ["Accounts Receivable", "Sales reps", null],
    ["Accounts Receivable", "Payment terms", null],
    ["Accounts Receivable", "Ageing bucket sets", null],
    ["Accounts Receivable", "Transaction types", null],
    ["Accounts Receivable", "Defaults", null],
    ["Accounts Receivable", "Rename customer code", null],
    ["Accounts Payable", "Suppliers", null],
    ["Accounts Payable", "Payment terms", null],
    ["Accounts Payable", "Ageing bucket sets", null],
    ["Accounts Payable", "Transaction types", null],
    ["Accounts Payable", "Defaults", null],
    ["Accounts Payable", "Rename supplier code", null],
    ["Inventory", "Items", "P5"],
    ["Inventory", "Warehouses", "P5"],
    ["Order Entry", "Order defaults", "P6"],
    ["Bill of Materials", "BOM items & defaults", "P12"],
    ["Point of Sale", "Tills & types", "P11"],
    ["Fixed Assets", "Asset categories", "P9"],
  ],
  "Transactions": [
    ["General Ledger", "Journal batches", null],
    ["General Ledger", "Cashbook batches", null],
    ["Accounts Receivable", "Invoice", null],
    ["Accounts Receivable", "Credit note", null],
    ["Accounts Receivable", "Receipt", null],
    ["Accounts Receivable", "Allocate", null],
    ["Accounts Receivable", "Account receivable batches", null],
    ["Accounts Payable", "GRV", "P6"],
    ["Accounts Payable", "Purchase order", "P6"],
    ["Accounts Payable", "Supplier invoice", null],
    ["Accounts Payable", "Return to supplier", null],
    ["Accounts Payable", "Payment", null],
    ["Accounts Payable", "Allocate", null],
    ["Accounts Payable", "Account payable batches", null],
    ["Order Entry", "Sales order", "P6"],
    ["Inventory", "Adjustments", "P5"],
    ["Inventory", "Transfers", "P5"],
    ["Inventory", "Counts", "P5"],
    ["Bill of Materials", "Manufacture process", "P12"],
    ["Point of Sale", "Sales", "P11"],
    ["Point of Sale", "Returns", "P11"],
  ],
  "Enquiries": [
    ["General Ledger", "Account enquiry", null],
    ["General Ledger", "Trial balance enquiry", null],
    ["Accounts Receivable", "Customer enquiry", null],
    ["Accounts Payable", "Supplier enquiry", null],
    ["Inventory", "Item enquiry", "P5"],
  ],
  "Reports": [
    ["General Ledger", "Account transactions", null],
    ["General Ledger", "Trial balance", null],
    ["General Ledger", "Chart of accounts", null],
    ["General Ledger", "Bank reconciliation", "P8"],
    ["General Ledger", "Cashbooks", "P8"],
    ["General Ledger", "Balance sheet", "P10"],
    ["General Ledger", "Income statement", "P10"],
    ["Accounts Receivable", "Age analysis", null],
    ["Accounts Receivable", "Allocation", null],
    ["Accounts Receivable", "Customer listing", null],
    ["Accounts Receivable", "Statements", null],
    ["Accounts Receivable", "Transaction listing", null],
    ["Accounts Payable", "Age analysis", null],
    ["Accounts Payable", "Allocation", null],
    ["Accounts Payable", "Supplier listing", null],
    ["Accounts Payable", "Statements", null],
    ["Accounts Payable", "Transaction listing", null],
    ["Inventory", "Valuation", "P5"],
    ["Inventory", "Movement", "P5"],
    ["Inventory", "Sales analyses", "P10"],
    ["Inventory", "Slow movers", "P10"],
  ],
};

describe("the Appendix C navigation contract", () => {
  it.each(Object.keys(APPENDIX_C) as IntentLabel[])(
    "keeps %s in the owner's declared order",
    (intent) => {
      const actual = navIntents
        .find((i) => i.label === intent)!
        .items.map((item) => [item.module, item.label, item.phase ?? null]);
      expect(actual).toEqual(APPENDIX_C[intent]);
    },
  );

  it("covers every intent the sidebar renders", () => {
    expect(navIntents.map((i) => i.label).sort()).toEqual(
      (Object.keys(APPENDIX_C) as IntentLabel[]).sort(),
    );
  });

  it("leaves a P4 tag on the transaction, enquiry and report screens step 6 did not build", () => {
    // Step 6 was Maintenance only. Steps 7 and 8 clear the rest; until then they must stay
    // tagged, so an untagged-but-missing screen cannot slip through as "done".
    const stillTagged = navIntents.flatMap((intent) =>
      intent.items
        .filter((item) => item.phase === "P4")
        .map((item) => `${intent.label}/${item.module}/${item.label}`),
    );
    expect(stillTagged).toEqual([

    ]);
  });

  it("has no P4 tag left anywhere under Maintenance", () => {
    const maintenance = navIntents.find((i) => i.label === "Maintenance")!;
    expect(maintenance.items.filter((item) => item.phase === "P4")).toEqual([]);
  });
});
