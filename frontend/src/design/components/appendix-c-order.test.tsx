import { describe, expect, it } from "vitest";
import { navIntents, type IntentLabel } from "@/design/nav-tree";

/**
 * Appendix C of the Master Plan adopts the owner's menu ordering as the navigation contract.
 * This pins it: `[module, label, phase]` in declared order, for every intent. A phase that
 * goes from a tag to `null` is a screen landing — expected, and the reason to edit this file.
 * Anything else moving is a contract change and should be argued for, not slipped in.
 *
 * Snapshot taken after P4 step 6, where the only edit was the AR/AP Maintenance block:
 * Customers, Sales reps and Suppliers lost their `P4` tag, and the seven other AR/AP
 * maintenance screens were added in place. Nothing outside that block moved.
 *
 * Amended at P4 step 9 with "Post-dated receipts" / "Post-dated payments" under Transactions,
 * after Allocate. Not in the owner's tree — but neither is the document they act on: P4
 * decision 7 books a receipt or payment dated ahead to the post-dated account, and the
 * `mature_instruments` service that banks it shipped as an endpoint and a scheduled job with
 * no screen. An instrument the product can raise and cannot mature is a hole in the phase,
 * so the tree gains the screen that closes it.
 *
 * Recorded in the plan as **Appendix C.1.6**, on the same footing as C.1.5's additions: the
 * tree is a contract, and a change to it belongs in the document the contract lives in rather
 * than only in the code that happens to satisfy it.
 *
 * Amended at P5 step 6 with the Maintenance → Inventory block. Two things happened at once,
 * and only the first is the ordinary kind of edit this file expects:
 *
 * 1. "Items" and "Warehouses" lost their `P5` tag, because their screens landed.
 * 2. Five rows were **added**: Transaction types, Variable barcodes, Units of measure,
 *    Defaults, Rename item code. These are not new scope. Appendix C has always read
 *    "Items, Warehouses, Trans types, Variable barcodes, UoM categories, Defaults, Rename
 *    Item Code" for this block; the tree only ever carried the first two, so the phase tag
 *    was covering five screens that had no row to be tagged. The order above is the
 *    appendix's own, and "Units of measure" / "Rename item code" are its "UoM categories" /
 *    "Rename Item Code" in the tree's sentence case.
 *
 * Amended again at the P5 step-6 review, on the owner's correction — a **plan deviation**,
 * recorded here and carried into `docs/p5-final-report.md`:
 *
 * "Variable barcodes" in the appendix is the **POS scale-label pattern** — a prefix, then
 * item-code digits, then weight or price digits, decoded at the till. It is a P11 screen, not
 * a P5 one. P5 step 6 read the label as "the barcode listing" and built that instead, which is
 * a real and needed screen but a different one. So the row keeps the appendix's label and
 * position and is tagged `P11`, and the screen that was built sits beside it as **"Barcodes"**:
 * per-item barcodes with their unit and pack quantity, a listing and a duplicate detector.
 *
 * The misreading is on the specification side, not the build — the prompt's step-6 list named
 * "Variable barcodes" among the maintenance screens to ship. Noted so the next reader of the
 * appendix does not repeat it.
 *
 * Nothing outside the Inventory block moved.
 *
 * Amended at P5 step 7 with the Transactions → Inventory block. The appendix reads "Journal
 * batches, Transfers, Adjustments, Counts"; the tree carried Adjustments, Transfers, Counts —
 * three of the four, in a different order, all tagged P5. The four rows now stand in the
 * appendix's order with their tags cleared, and "Journal batches" is the row that was
 * missing, not new scope. Same shape of edit as the step-6 Maintenance block: the tag was
 * covering a screen that had no row to be tagged.
 *
 * Amended at P5 step 8 with the Enquiries and Reports → Inventory blocks — the last P5 tags
 * in the tree, so `navIntents` now carries none. "Item enquiry" simply lost its tag. The
 * Reports block is the same shape of edit as steps 6 and 7: the appendix reads "Movement,
 * Count, Transaction, Valuation"; the tree carried Valuation and Movement — two of the four,
 * in the opposite order, both tagged — so the four rows now stand in the appendix's order
 * with their tags cleared, and Count and Transaction are rows that were missing, not new
 * scope. Their endpoints shipped at step 5 with the other two.
 *
 * Amended at P5 step 9 with **"Documents"** under Transactions → Inventory, an addition to
 * the owner's tree recorded as **Appendix C.1.7**. Decision 11 gives every stock document a
 * reversal and nothing called it: `useReverseStockDocument` sat unused, and the only Reverse
 * button in the product was the general ledger's — which posts the reversing entry and no
 * reversing moves, so on an inventory adjustment it moved the inventory account and left the
 * stock behind. The kernel now refuses that (`reverse_via_module_document`), which would have
 * left a document the product could post and never undo. Same shape of hole as C.1.6's
 * post-dated instruments, and the same answer: the tree gains the screen that closes it.
 *
 * Amended at P6 step 6: **"Order defaults"** lost its `P6` tag. The ordinary kind of edit —
 * the screen landed, at `/maintenance/order-defaults`, and nothing else in the Maintenance
 * block moved. It is the first P6 row to go live; GRV, Purchase order and Sales order are
 * step 7's, and the Breakup and Landed cost rows the appendix wants under Transactions →
 * Order Entry arrive with them.
 *
 * Amended before P6 step 1 with **"Documents"** under Transactions → AR and → AP, the other
 * half of that same C.1.7 entry. The appendix already recorded it: "the same hole is open in
 * AR and AP, one phase older and twice over". `POST /{role}/documents/{id}/reverse` and
 * `POST /{role}/allocations/{id}/unallocate` both shipped in P4 with no caller anywhere in
 * the frontend, and there was no `/ar/documents/{id}` route to put one on — so an invoice
 * posted in error, or an allocation made against the wrong invoice, was uncorrectable by
 * anybody using the product. Two rows, in the same position their inventory counterpart
 * holds: last in each module's Transactions block.
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
    ["Inventory", "Items", null],
    ["Inventory", "Warehouses", null],
    ["Inventory", "Transaction types", null],
    ["Inventory", "Variable barcodes", "P11"],
    ["Inventory", "Barcodes", null],
    ["Inventory", "Units of measure", null],
    ["Inventory", "Defaults", null],
    ["Inventory", "Rename item code", null],
    ["Order Entry", "Order defaults", null],
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
    ["Accounts Receivable", "Post-dated receipts", null],
    ["Accounts Receivable", "Account receivable batches", null],
    ["Accounts Receivable", "Documents", null],
    ["Accounts Payable", "GRV", "P6"],
    ["Accounts Payable", "Purchase order", "P6"],
    ["Accounts Payable", "Supplier invoice", null],
    ["Accounts Payable", "Return to supplier", null],
    ["Accounts Payable", "Payment", null],
    ["Accounts Payable", "Allocate", null],
    ["Accounts Payable", "Post-dated payments", null],
    ["Accounts Payable", "Account payable batches", null],
    ["Accounts Payable", "Documents", null],
    ["Order Entry", "Sales order", "P6"],
    ["Inventory", "Journal batches", null],
    ["Inventory", "Transfers", null],
    ["Inventory", "Adjustments", null],
    ["Inventory", "Counts", null],
    ["Inventory", "Documents", null],
    ["Bill of Materials", "Manufacture process", "P12"],
    ["Point of Sale", "Sales", "P11"],
    ["Point of Sale", "Returns", "P11"],
  ],
  "Enquiries": [
    ["General Ledger", "Account enquiry", null],
    ["General Ledger", "Trial balance enquiry", null],
    ["Accounts Receivable", "Customer enquiry", null],
    ["Accounts Payable", "Supplier enquiry", null],
    ["Inventory", "Item enquiry", null],
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
    ["Inventory", "Movement", null],
    ["Inventory", "Count", null],
    ["Inventory", "Transaction", null],
    ["Inventory", "Valuation", null],
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

  it("has no P4 or P5 tag left anywhere in the tree", () => {
    // Both phases are complete in the nav: P4 at its step 9, P5 at step 8. A tag left on a
    // screen that exists is a row nobody can reach, which is exactly how such a row goes
    // unnoticed — the table above would still pass, because it pins the tag it finds.
    const stillTagged = navIntents.flatMap((intent) =>
      intent.items
        .filter((item) => item.phase === "P4" || item.phase === "P5")
        .map((item) => `${intent.label}/${item.module}/${item.label} (${item.phase})`),
    );
    expect(stillTagged).toEqual([]);
  });

  it("gives every untagged row a route, so nothing reads as live and goes nowhere", () => {
    const liveWithoutHref = navIntents.flatMap((intent) =>
      intent.items
        .filter((item) => !item.phase && !item.href)
        .map((item) => `${intent.label}/${item.module}/${item.label}`),
    );
    expect(liveWithoutHref).toEqual([]);
  });
});
