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
 * Amended at P6 step 7: the last three `P6` tags cleared, and the two Order Entry rows the
 * appendix wants beside them added. "GRV", "Purchase order" and "Sales order" are the
 * ordinary kind of edit — the screens landed, at `/oe/goods-received`, `/oe/purchase-orders`
 * and `/oe/sales-orders`. **"Breakup"** and **"Landed cost"** are the other kind, and the same
 * kind P5 steps 6 and 7 made twice: not new scope, but rows the tag was covering. Appendix C
 * reads "Sales Order, Breakup, Landed Cost" for Transactions → Order Entry and the tree only
 * ever carried the first, so the tag stood for three screens and could only ever be cleared
 * by one. `navIntents` now carries no P6 tag at all, which the guard below pins.
 *
 * Amended at P6 step 8 with an Order Entry block under **Enquiries** and another under
 * **Reports**, recorded in the plan as **Appendix C.1.8**. Neither block is in the owner's
 * tree, and the reason is in the plan rather than in a judgement call here: the appendix's
 * Enquiries and Reports sections carry no Order Entry at all, while P6's own step list names
 * six screens — "Sales order enquiry and Purchase order enquiry under Enquiries (module Order
 * Entry)" and "Reports → Order Entry: Sales orders, Purchase orders, Goods received, Landed
 * cost". Same footing as C.1.5's additions: a change to the contract belongs in the document
 * the contract lives in.
 *
 * The Goods received row is the one worth naming twice. Its unmatched total is Σ (received −
 * relieved) over every receipt the filters select, which is what decision 5 makes the GRN
 * accrual account's balance — so it is where an operator watches the accrual prove itself
 * against the trial balance, rather than taking the invariant suite's word for it.
 *
 * Nothing outside the two new blocks moved, and both sit last in their intent, after Inventory.
 *
 * Amended at P7 step 6 with **"EBM devices"** under Maintenance → Tax, directly after Tax
 * types. It is an addition to the appendix rather than a tag being cleared: Appendix C's
 * Maintenance → Tax block carries "Tax types" and nothing else, and the owner's tree has Tax
 * under Maintenance only. A device is a maintenance object by the same reading that makes a
 * branch or a warehouse one — registered once, initialized, and then left alone — and P7's
 * own step list names the screen and its position ("Maintenance → Tax, after Tax types").
 * Same footing as C.1.5's additions and C.1.8's two blocks: a change to the contract belongs
 * in the document the contract lives in, which is why the Master Plan carries it too.
 *
 * Nothing else in the tree moved. The rest of what P7 step 6 built is **columns and sections
 * on screens that already exist** — the EBM class on Tax types, the RRA quantity unit on
 * Units of measure, a Fiscal section on Items, Verify TIN on Customers and Suppliers, and the
 * tax block on GL Defaults — none of which is a navigable row and none of which belongs here.
 * Step 7 brings the **Transactions → Tax** block (C.1.10) and the FX revaluation row under
 * Transactions → GL (C.1.11), and pins both the same way.
 *
 * Amended at P7 step 7 with the **Transactions → Tax** block — Fiscal queue, EBM purchases,
 * Import declarations, VAT return — recorded in the plan as **Appendix C.1.10**, and with
 * **"FX revaluation"** under Transactions → General Ledger as **Appendix C.1.11**.
 *
 * Neither is in the owner's tree, and C.1.10 is the C.2 promise made good: Appendix C.2 has
 * always said "fiscalization status/queue screens (P7)" without saying where they hang. The
 * owner's tree carries Tax under **Maintenance only**, which is right for what was there —
 * tax types, and now a device. These four are not maintenance. A queue row is a declaration in
 * flight, a feed row is a purchase somebody has to say yes or no to, an import declaration is
 * an acknowledgment the authority is waiting for, and a VAT return posts a settlement entry
 * through the kernel. Every one is a transaction: done on a day, by a person, with a
 * consequence in the ledger or at the authority.
 *
 * C.1.11 is one row and the same reasoning in miniature. A revaluation **posts** — an entry at
 * the date and its mirror the day after, in one transaction — so it belongs beside Journal
 * batches and Cashbook batches rather than under Reports. The *report* over a posted run is
 * step 8's, and lands under Reports → General Ledger.
 *
 * Position: both blocks go after Inventory and before the phase-tagged Bill of Materials and
 * Point of Sale rows, which keeps the live modules together and leaves the owner's own tail
 * where it is. Same footing as C.1.5's additions and C.1.8's two blocks — a change to the
 * contract belongs in the document the contract lives in, which is why the Master Plan carries
 * both entries too.
 *
 * Nothing outside the five new rows moved, and no P7 tag is left in the tree: the phase's
 * remaining screens (the two Tax enquiries and the five Tax/GL reports) are step 8's and have
 * no row to be tagged yet, exactly as step 6's columns and sections had none.
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
    ["Tax", "EBM devices", null],
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
    ["General Ledger", "FX revaluation", null],
    ["Accounts Receivable", "Invoice", null],
    ["Accounts Receivable", "Credit note", null],
    ["Accounts Receivable", "Receipt", null],
    ["Accounts Receivable", "Allocate", null],
    ["Accounts Receivable", "Post-dated receipts", null],
    ["Accounts Receivable", "Account receivable batches", null],
    ["Accounts Receivable", "Documents", null],
    ["Accounts Payable", "GRV", null],
    ["Accounts Payable", "Purchase order", null],
    ["Accounts Payable", "Supplier invoice", null],
    ["Accounts Payable", "Return to supplier", null],
    ["Accounts Payable", "Payment", null],
    ["Accounts Payable", "Allocate", null],
    ["Accounts Payable", "Post-dated payments", null],
    ["Accounts Payable", "Account payable batches", null],
    ["Accounts Payable", "Documents", null],
    ["Order Entry", "Sales order", null],
    ["Order Entry", "Breakup", null],
    ["Order Entry", "Landed cost", null],
    ["Inventory", "Journal batches", null],
    ["Inventory", "Transfers", null],
    ["Inventory", "Adjustments", null],
    ["Inventory", "Counts", null],
    ["Inventory", "Documents", null],
    ["Tax", "Fiscal queue", null],
    ["Tax", "EBM purchases", null],
    ["Tax", "Import declarations", null],
    ["Tax", "VAT return", null],
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
    ["Order Entry", "Sales order enquiry", null],
    ["Order Entry", "Purchase order enquiry", null],
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
    ["Order Entry", "Sales orders", null],
    ["Order Entry", "Purchase orders", null],
    ["Order Entry", "Goods received", null],
    ["Order Entry", "Landed cost", null],
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

  it("has no P4, P5, P6 or P7 tag left anywhere in the tree", () => {
    // All four phases are complete in the nav: P4 at its step 9, P5 at step 8, P6 at step 7,
    // P7 at step 7 — every P7 row it will ever carry is live, and steps 8 and 9 add enquiries
    // and reports under existing modules rather than tagged placeholders. A tag left on a
    // screen that exists is a row nobody can reach, which is exactly how such a row goes
    // unnoticed — the table above would still pass, because it pins the tag it finds.
    const done = new Set(["P4", "P5", "P6", "P7"]);
    const stillTagged = navIntents.flatMap((intent) =>
      intent.items
        .filter((item) => item.phase !== undefined && done.has(item.phase))
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
