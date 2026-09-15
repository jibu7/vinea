import { expect, test, type Page } from "@playwright/test";
import {
  PRIMARY_EMAIL,
  READONLY_EMAIL,
  assertNoSeriousViolations,
  login,
  pageFetch,
  pickCombobox,
  setTheme,
} from "./support/fixtures";
import { todayIso } from "../src/lib/format";

/**
 * P6 step 8 — the enquiries and the reports, driven as the two questions they exist to answer.
 *
 * *What is still outstanding* and *does the accrual account hold what the receipts say it
 * should*. Both are claims a screen makes about the ledger, and both are the kind of claim
 * that is easy to render and hard to notice going wrong — the P4 review found six of exactly
 * that shape (rule 13), every one on a screen no test had opened with data behind it.
 *
 * **The reconciliation is asserted as two rendered strings.** The goods-received report's
 * unmatched total and the balance of 2350 on the trial balance are the same figure by decision
 * 5, and the test reads both off two screens and compares the text. Comparing parsed decimals
 * would pass on a report that printed the right number in the wrong scale, which is a report
 * nobody can reconcile and therefore a broken one.
 *
 * **Both sides of the outstanding claim are here**, and neither is a flag: a backordered sales
 * line is on the report with its remaining quantity and after Close remaining it is not; a
 * purchase line leaves when a receipt covers it. Nothing is written to make either happen — the
 * order drops out of the open statuses, or the arithmetic catches up.
 *
 * The fixture is built through the API. `p6-orders.spec.ts` already proves the order, receipt
 * and landed-cost screens key what they say they key; re-driving them here would add four
 * minutes to every run and prove it twice. What this file is about starts at the enquiry.
 */

const SUFFIX = String(Date.now()).slice(-6);
const WINE = `P8W${SUFFIX}`;
const KIT = `P8K${SUFFIX}`;
const HAULAGE = `P8H${SUFFIX}`;
const CUSTOMER = `P8C${SUFFIX}`;
const SUPPLIER = `P8S${SUFFIX}`;
// The partner **pickers on these screens list names**, not codes — a report is read by
// somebody who knows who they are dealing with, not by somebody who knows their code.
const CUSTOMER_NAME = `P8 Customer ${SUFFIX}`;
const SUPPLIER_NAME = `P8 Supplier ${SUFFIX}`;

interface Named {
  id: number;
  code: string;
  number?: string;
}

interface OrderLine {
  id: number;
  item_id: number;
}

interface Fixture {
  wine: Named;
  kit: Named;
  haulage: Named;
  customer: Named;
  supplier: Named;
  /** PO-1: 100 bottles and a haulage line; 60 received, so 40 stay outstanding. */
  purchaseOrder: Named & { lines: OrderLine[] };
  /** PO-2: 10 bottles, received in full — the line that has to leave the report. */
  coveredOrder: Named & { lines: OrderLine[] };
  firstReceipt: Named & { lines: Array<{ id: number }> };
  landedCost: Named;
  /** SO-1: 80 bottles against 70 on the shelf, part invoiced — the backorder. */
  salesOrder: Named & { lines: OrderLine[] };
  /** SO-2: five bottles, nothing fulfilled, cancelled mid-file. */
  doomedOrder: Named;
  invoice: Named;
  serviceInvoice: Named;
}

let fixture: Fixture;

async function post(page: Page, path: string, body: unknown, key?: string): Promise<unknown> {
  const res = await pageFetch(page, path, {
    method: "POST",
    body,
    headers: key ? { "Idempotency-Key": key } : {},
  });
  expect(res.ok, `POST ${path} → ${res.status}: ${JSON.stringify(res.json)}`).toBe(true);
  return res.json;
}

/**
 * One supplier, one customer, a catalogue, and the consignment the whole file reads.
 *
 * The numbers are chosen so every figure on every screen is a round one a person could check
 * by hand: 100 ordered, 60 received at 1 000, 6 000 of haulage landed on those 60, 80 promised
 * against the 70 on the shelf, 60 of them invoiced.
 */
async function seed(page: Page): Promise<Fixture> {
  const categories = await pageFetch(page, "/inventory/uom-categories");
  const rows = categories.json as Array<Named & { uoms: Named[] }>;
  const count = rows.find((c) => c.code === "COUNT")!;
  const each = count.uoms.find((u) => u.code === "EA")!;
  const today = todayIso();

  async function item(code: string, name: string, price: string, itemType: string) {
    return (await post(page, "/inventory/items", {
      code,
      name,
      uom_category_id: count.id,
      base_uom_id: each.id,
      item_type: itemType,
      selling_price: price,
    })) as Named;
  }

  async function partner(role: "ar" | "ap", code: string, name: string) {
    return (await post(page, `/subledger/${role}/partners`, {
      name,
      [role === "ar" ? "customer_code" : "supplier_code"]: code,
    })) as Named;
  }

  const wine = await item(WINE, `P8 Red ${SUFFIX}`, "2000", "stock");
  const haulage = await item(HAULAGE, `P8 Haulage ${SUFFIX}`, "30000", "service");
  const kit = await item(KIT, `P8 Gift pack ${SUFFIX}`, "5000", "kit");
  // A PUT: the explosion is the whole list, replaced (decision 8).
  const defined = await pageFetch(page, `/inventory/items/${kit.id}/kit-components`, {
    method: "PUT",
    body: { components: [{ component_item_id: wine.id, quantity_per_kit: "2" }] },
  });
  expect(defined.ok, JSON.stringify(defined.json)).toBe(true);

  const customer = await partner("ar", CUSTOMER, CUSTOMER_NAME);
  const supplier = await partner("ap", SUPPLIER, SUPPLIER_NAME);

  // --- the purchase side ------------------------------------------------------------------
  const purchaseOrder = (await post(
    page,
    "/oe/purchase-orders",
    {
      partner_id: supplier.id,
      order_date: today,
      description: `P8 consignment ${SUFFIX}`,
      lines: [
        { item_id: wine.id, quantity: "100", unit_price: "1000" },
        { item_id: haulage.id, quantity: "1", unit_price: "30000" },
      ],
    },
    `${SUFFIX}-po1`,
  )) as Fixture["purchaseOrder"];
  const wineLine = purchaseOrder.lines.find((l) => l.item_id === wine.id)!;
  const haulageLine = purchaseOrder.lines.find((l) => l.item_id === haulage.id)!;

  const firstReceipt = (await post(
    page,
    "/oe/goods-received-notes",
    {
      partner_id: supplier.id,
      grn_date: today,
      description: `P8 first delivery ${SUFFIX}`,
      purchase_order_id: purchaseOrder.id,
      lines: [
        {
          item_id: wine.id,
          quantity: "60",
          unit_cost: "1000",
          purchase_order_line_id: wineLine.id,
        },
      ],
    },
    `${SUFFIX}-grn1`,
  )) as Fixture["firstReceipt"];

  // A second order that will be received in full, so the report has a line that *leaves* it.
  const coveredOrder = (await post(
    page,
    "/oe/purchase-orders",
    {
      partner_id: supplier.id,
      order_date: today,
      description: `P8 top-up ${SUFFIX}`,
      lines: [{ item_id: wine.id, quantity: "10", unit_price: "1000" }],
    },
    `${SUFFIX}-po2`,
  )) as Fixture["coveredOrder"];

  // Freight on the first delivery, before anything is sold: the goods are all still there, so
  // every share goes into stock rather than to cost of sales.
  const landedCost = (await post(
    page,
    "/oe/landed-costs",
    {
      cost_date: today,
      description: `P8 haulage ${SUFFIX}`,
      amount: "6000",
      basis: "quantity",
      grn_line_ids: [firstReceipt.lines[0].id],
    },
    `${SUFFIX}-lca1`,
  )) as Named;

  // The service line the receipt could not carry: a service is received by its invoice.
  const serviceInvoice = (await post(
    page,
    "/subledger/ap/documents",
    {
      kind: "invoice",
      partner_id: supplier.id,
      document_date: today,
      description: `P8 haulage invoice ${SUFFIX}`,
      lines: [
        {
          item_id: haulage.id,
          quantity: "1",
          unit_price: "30000",
          purchase_order_line_id: haulageLine.id,
        },
      ],
    },
    `${SUFFIX}-apinv`,
  )) as Named;

  // --- the sales side ---------------------------------------------------------------------
  const salesOrder = (await post(
    page,
    "/oe/sales-orders",
    {
      partner_id: customer.id,
      order_date: today,
      description: `P8 sale ${SUFFIX}`,
      lines: [{ item_id: wine.id, quantity: "80", unit_price: "2000" }],
    },
    `${SUFFIX}-so1`,
  )) as Fixture["salesOrder"];

  const doomedOrder = (await post(
    page,
    "/oe/sales-orders",
    {
      partner_id: customer.id,
      order_date: today,
      description: `P8 cancelled sale ${SUFFIX}`,
      lines: [{ item_id: wine.id, quantity: "5", unit_price: "2000" }],
    },
    `${SUFFIX}-so2`,
  )) as Named;

  return {
    wine,
    kit,
    haulage,
    customer,
    supplier,
    purchaseOrder,
    coveredOrder,
    firstReceipt,
    landedCost,
    salesOrder,
    doomedOrder,
    invoice: { id: 0, code: "" },
    serviceInvoice,
  };
}

test.describe.configure({ mode: "serial", timeout: 240_000 });

test.describe("P6 enquiries and reports", () => {
  test.beforeAll(async ({ browser }) => {
    // `describe.configure({ timeout })` sets the *test* timeout and leaves hooks on the
    // config's 45s. This hook is a first-compile login plus a dozen postings, which is
    // comfortably more than that on a cold `next dev`.
    test.setTimeout(240_000);
    const page = await browser.newPage();
    await login(page, PRIMARY_EMAIL);
    fixture = await seed(page);
    await page.close();
  });

  // ---------------------------------------------------------------------------------------
  // PATH: /oe/reports/purchase-orders -> GET /oe/reports/purchase-orders?outstanding_only
  // CANNOT SEE: the ledger. A purchase order posts nothing, which is decision 3 and the
  // acceptance tape's to prove.
  // ---------------------------------------------------------------------------------------
  test("the purchase report shows what is still owed, and loses a line when a receipt covers it", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);

    await page.goto("/oe/reports/purchase-orders");
    await page.waitForSelector("h1:has-text('Purchase orders')");
    await pickCombobox(page, "Supplier", SUPPLIER_NAME);

    // Two orders, three lines: 40 bottles still to come on the first, its haulage line already
    // invoiced and therefore gone, and all 10 of the second still outstanding.
    const first = page.locator("tbody tr", { hasText: fixture.purchaseOrder.number! });
    await expect(first.getByTestId("report-remaining")).toHaveText("40 EA");
    const covered = page.locator("tbody tr", { hasText: fixture.coveredOrder.number! });
    await expect(covered.getByTestId("report-remaining")).toHaveText("10 EA");

    // The money figure and the quantity figure the step-6 standard asks every screen for, both
    // read as the page renders them. 40 left of a line whose whole net is 100 000.
    await expect(first.getByTestId("report-net")).toHaveText("FRw 100,000");
    // Subtotalled by unit, never one number: every line here happens to be counted in EA, so
    // there is one row — and the shape is a list, which is what stops the next item counted in
    // kilograms being added to it.
    await expect(page.getByTestId("report-remaining-by-unit")).toHaveText("50 EA");
    await expect(page.getByTestId("report-line-count")).toHaveText("2");

    // A service line is received by its invoice (decision 4), and the haulage invoice was
    // posted before this screen opened — so PO-1 shows one outstanding line, not two.
    await expect(first).toHaveCount(1);

    // --- and now the receipt that covers the second order in full -------------------------
    const lines = (await pageFetch(page, `/oe/purchase-orders/${fixture.coveredOrder.id}`))
      .json as { lines: OrderLine[] };
    const res = await pageFetch(page, "/oe/goods-received-notes", {
      method: "POST",
      headers: { "Idempotency-Key": `${SUFFIX}-grn2` },
      body: {
        partner_id: fixture.supplier.id,
        grn_date: todayIso(),
        description: `P8 top-up delivery ${SUFFIX}`,
        purchase_order_id: fixture.coveredOrder.id,
        lines: [
          {
            item_id: fixture.wine.id,
            quantity: "10",
            unit_cost: "1000",
            purchase_order_line_id: lines.lines[0].id,
          },
        ],
      },
    });
    expect(res.ok, JSON.stringify(res.json)).toBe(true);

    await page.reload();
    await page.waitForSelector("h1:has-text('Purchase orders')");
    await pickCombobox(page, "Supplier", SUPPLIER_NAME);
    await expect(first.getByTestId("report-remaining")).toHaveText("40 EA");
    // Gone, and nothing was written to make it go: `received` caught `ordered` in the view.
    await expect(covered).toHaveCount(0);
    await expect(page.getByTestId("report-line-count")).toHaveText("1");
  });

  // ---------------------------------------------------------------------------------------
  // PATH: /oe/reports/goods-received -> /gl/enquiries/trial-balance
  // CANNOT SEE: that the *per branch* form of the invariant holds — the seed company has one
  // branch, so this proves the company-wide figure. `assert_order_invariants` proves the rest.
  // ---------------------------------------------------------------------------------------
  test("the unmatched total on the receipts report is the accrual account's balance", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);

    // Unfiltered on purpose: the tie is over every receipt in the company, which is what the
    // account holds. A supplier filter would narrow one side of it and not the other.
    await page.goto("/oe/reports/goods-received");
    await page.waitForSelector("h1:has-text('Goods received')");
    await expect(
      page.locator("tbody tr", { hasText: fixture.firstReceipt.number! }).getByTestId(
        "report-grn-unmatched",
      ),
    ).toHaveText("FRw 60,000");
    const unmatched = await page.getByTestId("report-unmatched-total").innerText();
    expect(unmatched).toMatch(/^FRw /);

    await page.goto("/gl/enquiries/trial-balance");
    await page.waitForSelector("h1:has-text('Trial Balance')");
    const accrual = page.locator("tbody tr", { hasText: "2350" }).first();
    await expect(accrual).toBeVisible();
    // The credit column. Compared as **text**: two figures equal as decimals and different as
    // text are still a report nobody can reconcile.
    const balance = (await accrual.locator("td").nth(4).innerText()).trim();
    expect(balance, `report ${unmatched} vs trial balance ${balance}`).toBe(unmatched.trim());
  });

  // ---------------------------------------------------------------------------------------
  // PATH: /oe/reports/sales-orders -> POST /oe/sales-orders/{id}/close
  // CANNOT SEE: the invoice's own postings — the tape's.
  // ---------------------------------------------------------------------------------------
  test("a backordered sales line is outstanding until the order is closed", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);

    // Invoice 60 of the 80 promised, which is everything the first delivery brought in. The
    // rest is a backorder that nothing can fill yet.
    const order = (await pageFetch(page, `/oe/sales-orders/${fixture.salesOrder.id}`)).json as {
      lines: OrderLine[];
    };
    fixture.invoice = (await post(
      page,
      "/subledger/ar/documents",
      {
        kind: "invoice",
        partner_id: fixture.customer.id,
        document_date: todayIso(),
        description: `P8 part delivery ${SUFFIX}`,
        lines: [
          {
            item_id: fixture.wine.id,
            quantity: "60",
            unit_price: "2000",
            sales_order_line_id: order.lines[0].id,
          },
        ],
      },
      `${SUFFIX}-arinv`,
    )) as Named;

    await page.goto("/oe/reports/sales-orders");
    await page.waitForSelector("h1:has-text('Sales orders')");
    await pickCombobox(page, "Customer", CUSTOMER_NAME);

    const row = page.locator("tbody tr", { hasText: fixture.salesOrder.number! });
    await expect(row.getByTestId("report-remaining")).toHaveText("20 EA");
    await expect(row.getByTestId("report-net")).toHaveText("FRw 160,000");

    // Close remaining: a decision, recorded, and the only thing it does to this report is take
    // the order out of the open statuses it selects on.
    const closed = await pageFetch(page, `/oe/sales-orders/${fixture.salesOrder.id}/close`, {
      method: "POST",
      body: { on_date: todayIso() },
    });
    expect(closed.ok, JSON.stringify(closed.json)).toBe(true);

    await page.reload();
    await page.waitForSelector("h1:has-text('Sales orders')");
    await pickCombobox(page, "Customer", CUSTOMER_NAME);
    await expect(row).toHaveCount(0);

    // The line did not vanish — it is outstanding no longer, and the history is still there.
    await page.getByLabel(/Outstanding lines only/).uncheck();
    await expect(row.getByTestId("report-remaining")).toHaveText("20 EA");
    await expect(row.getByText("Closed")).toBeVisible();
  });

  // ---------------------------------------------------------------------------------------
  // PATH: /inventory/enquiry -> GET /inventory/items/{id}/enquiry
  // CANNOT SEE: the commitment arithmetic itself, which is the property suite's.
  // ---------------------------------------------------------------------------------------
  test("the stock enquiry signs its availability, totals its locations, and drops a cancelled order", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/inventory/enquiry");
    await page.waitForSelector("h1:has-text('Item enquiry')");
    await pickCombobox(page, "Item", WINE);

    // 70 received, 60 invoiced away: 10 on the shelf. Still promised: the 20 the closed order
    // left behind are released, so what is committed is the 5 on the order about to be
    // cancelled. Available is 10 − 5.
    await expect(page.getByTestId("location-committed").first()).toHaveText("5 EA");
    await expect(page.getByTestId("location-available").first()).toHaveText("5 EA");
    // 40 still owed by the supplier on PO-1.
    await expect(page.getByTestId("location-on-order").first()).toHaveText("40 EA");

    // The totals row: one quantity and one money value, at the unit's and the currency's own
    // decimals, worked by hand — 60 at 1 000, then 6 000 of haulage landed on those 60 (1 100
    // each), then 10 more at 1 000, which is 70 for 76 000 and an average of 1 085.714286. The
    // invoice issued 60 of them at that average, leaving 10 worth 10 857.
    //
    // The order of those steps is the whole point of the figure: the haulage was landed before
    // the second delivery arrived, so it raised the average from that posting onward and did
    // not reach back over the bottles that came later. A revaluation that restated history
    // would read 11 000 here.
    await expect(page.getByTestId("locations-total-quantity")).toHaveText("10 EA");
    await expect(page.getByTestId("locations-total-value")).toHaveText("10,857");

    // --- cancel, and watch the column drop -------------------------------------------------
    const cancelled = await pageFetch(page, `/oe/sales-orders/${fixture.doomedOrder.id}/cancel`, {
      method: "POST",
      body: { on_date: todayIso() },
    });
    expect(cancelled.ok, JSON.stringify(cancelled.json)).toBe(true);

    await page.reload();
    await page.waitForSelector("h1:has-text('Item enquiry')");
    await pickCombobox(page, "Item", WINE);
    // Nothing was cleared on the line. The order left the open statuses and the sum stopped
    // counting it, which is the whole of how cancelling releases what an order held.
    await expect(page.getByTestId("location-committed").first()).toHaveText("0 EA");
    await expect(page.getByTestId("location-available").first()).toHaveText("10 EA");
  });

  // ---------------------------------------------------------------------------------------
  // PATH: /inventory/enquiry with a kit selected.
  // ---------------------------------------------------------------------------------------
  test("a kit says it has no position rather than showing zero", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/inventory/enquiry");
    await page.waitForSelector("h1:has-text('Item enquiry')");
    await pickCombobox(page, "Item", KIT);

    // Not "not held anywhere on this date" — never held anywhere, ever, and the screen says
    // which. A kit rendered as 0 reads as an ordinary item that happens to be out of stock.
    await expect(page.getByText("A kit is a virtual bundle")).toBeVisible();
    await expect(page.getByTestId("location-available")).toHaveCount(0);
  });

  // ---------------------------------------------------------------------------------------
  // PATH: /oe/enquiries/sales-orders and /oe/enquiries/purchase-orders.
  // CANNOT SEE: what those entries contain — the tape's.
  // ---------------------------------------------------------------------------------------
  test("both order enquiries carry the documents raised against the order and their entries", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);

    await page.goto("/oe/enquiries/sales-orders");
    await page.waitForSelector("h1:has-text('Sales order enquiry')");
    await pickCombobox(page, "Sales order", fixture.salesOrder.number!);

    await expect(page.getByTestId("enquiry-order-total")).toHaveText("FRw 160,000");
    await expect(page.getByTestId("enquiry-line-ordered").first()).toHaveText("80");
    await expect(page.getByTestId("enquiry-line-remaining").first()).toHaveText("20");
    // The invoice, and **both** entries it posted: a stock-bearing invoice posts the
    // receivable and the companion, and an enquiry offering one would drill past the cost of
    // the sale.
    await expect(page.getByTestId("enquiry-document")).toHaveText(
      new RegExp(fixture.invoice.number!),
    );
    await expect(page.getByTestId("enquiry-entry")).toHaveCount(2);

    await page.goto("/oe/enquiries/purchase-orders");
    await page.waitForSelector("h1:has-text('Purchase order enquiry')");
    await pickCombobox(page, "Purchase order", fixture.purchaseOrder.number!);

    // A purchase order is fulfilled by two kinds of document and both are listed: the receipt
    // that brought the bottles and the invoice that paid for the haulage.
    const documents = page.getByTestId("enquiry-document");
    await expect(documents).toHaveCount(2);
    await expect(documents.first()).toHaveText(new RegExp(fixture.firstReceipt.number!));
    await expect(page.getByTestId("enquiry-line-remaining").first()).toHaveText("40");
  });

  // ---------------------------------------------------------------------------------------
  // PATH: /oe/reports/landed-cost -> GET /oe/landed-cost-allocations
  // ---------------------------------------------------------------------------------------
  test("the landed-cost report is per receipt line and says where each share went", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/oe/reports/landed-cost");
    await page.waitForSelector("h1:has-text('Landed cost')");

    const row = page.locator("tbody tr", { hasText: fixture.landedCost.number! });
    await expect(row.getByTestId("report-share")).toHaveText("FRw 6,000");
    // 60 on the shelf when the share was struck, so it went into what they are worth rather
    // than to cost of sales.
    await expect(row.getByTestId("report-quantity-at-posting")).toHaveText("60");
    await expect(row.getByText("Stock")).toBeVisible();
  });

  // ---------------------------------------------------------------------------------------
  // PATH: /gl/enquiries/trial-balance -> /gl/enquiries/account -> the drawer -> /gl/entries/{id}
  // -> the document. One pass per P6 source that posts.
  // CANNOT SEE: an `LCA-` reversal, which has no entry in this fixture; the backend's
  // `test_entry_drill.py` proves that one from both ends.
  // ---------------------------------------------------------------------------------------
  test("every P6 entry leads back to its own document, from the trial balance", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);

    // Which entry to look for, on which account, and where it must lead. Four sources, four
    // different screens — and three of them are posted by the `inv` module, which is why a
    // link built from the module name sent them all to the same wrong place.
    const grn = (await pageFetch(page, `/oe/goods-received-notes/${fixture.firstReceipt.id}`))
      .json as { number: string; journal_entry_id: number };
    const landed = (await pageFetch(page, `/oe/landed-costs/${fixture.landedCost.id}`)).json as {
      number: string;
      journal_entry_id: number;
    };
    const invoice = (await pageFetch(page, `/subledger/ar/documents/${fixture.invoice.id}`))
      .json as { number: string; journal_entry_id: number; stock_entry_id: number };
    const haulage = (
      await pageFetch(page, `/subledger/ap/documents/${fixture.serviceInvoice.id}`)
    ).json as { number: string; journal_entry_id: number };

    const cases = [
      { source: "goods receipt", account: "2350", entryId: grn.journal_entry_id, document: grn.number, href: `/oe/goods-received/${fixture.firstReceipt.id}` },
      { source: "landed cost", account: "1370", entryId: landed.journal_entry_id, document: landed.number, href: `/oe/landed-costs/${fixture.landedCost.id}` },
      { source: "kit sale companion", account: "5100", entryId: invoice.stock_entry_id, document: invoice.number, href: `/ar/documents/${fixture.invoice.id}` },
      { source: "service-line invoice", account: "2100", entryId: haulage.journal_entry_id, document: haulage.number, href: `/ap/documents/${fixture.serviceInvoice.id}` },
    ];

    // The first one walks the whole path a person walks — trial balance, account, the entry —
    // so the chain itself is proved rather than assumed. The rest join it at the entry, which
    // is where the per-source claim actually lives.
    await page.goto("/gl/enquiries/trial-balance");
    await page.waitForSelector("h1:has-text('Trial Balance')");
    await page.locator("tbody tr", { hasText: cases[0].account }).first().click();
    await page.waitForURL(/\/gl\/enquiries\/account/);
    await page.waitForSelector("text=Opening balance");

    for (const probe of cases) {
      await page.goto(`/gl/entries/${probe.entryId}`);
      await page.waitForSelector("[data-testid='reverse-blocked']");
      // The refusal is a sentence on the page, not a tooltip on a greyed-out button.
      await expect(page.getByTestId("reverse-blocked")).toContainText("reverse it from that");
      const link = page.getByTestId("reverse-via-module");
      await expect(link, probe.source).toContainText(probe.document);
      await expect(link, probe.source).toHaveAttribute("href", probe.href);
    }

    // And the round trip closes: the invoice reached from its companion entry names the
    // companion back. One document, two entries, and a person can get from either to either.
    await page.goto(`/ar/documents/${fixture.invoice.id}`);
    await expect(page.getByTestId("document-stock-entry")).toHaveAttribute(
      "href",
      `/gl/entries/${invoice.stock_entry_id}`,
    );

    // And the link goes where it says it goes.
    await page.goto(`/gl/entries/${haulage.journal_entry_id}`);
    await page.getByTestId("reverse-via-module").click();
    await page.waitForURL(/\/ap\/documents\/\d+/);
  });

  // ---------------------------------------------------------------------------------------
  // PATH: the sidebar, as a Clerk — who holds `*:reports_view` and no `oe:*` at all.
  // CANNOT SEE: the other four roles; `test_permissions.py` owns the matrix.
  // ---------------------------------------------------------------------------------------
  test("a role with no order-entry right is offered none of these screens", async ({ page }) => {
    await login(page, READONLY_EMAIL);

    // The API refuses all six with 403, and the sidebar gates on the **same five permissions**
    // the endpoint accepts — named once in `nav-tree.ts` rather than spelled out at each row,
    // which is what stops the two drifting into a screen the nav offers and the API refuses.
    for (const label of [
      "Sales order enquiry",
      "Purchase order enquiry",
      "Sales orders",
      "Purchase orders",
      "Goods received",
      "Landed cost",
    ]) {
      await expect(page.getByRole("link", { name: label, exact: true })).toHaveCount(0);
    }

    // And typing the route in by hand does not get round it: the report comes back empty
    // because the request was refused, not because there is nothing to report.
    const refused = await pageFetch(page, "/oe/reports/goods-received");
    expect(refused.status).toBe(403);
  });

  test.describe("accessibility", () => {
    // PATH: axe over the six new screens with data on them. The nav sweep visits them empty;
    // a table with rows is a different DOM, and contrast on a status chip only exists once one
    // has been rendered.
    test("the enquiries and reports, light and dark, with rows", async ({ page }) => {
      await login(page, PRIMARY_EMAIL);
      for (const [path, heading] of [
        ["/oe/enquiries/sales-orders", "Sales order enquiry"],
        ["/oe/enquiries/purchase-orders", "Purchase order enquiry"],
        ["/oe/reports/sales-orders", "Sales orders"],
        ["/oe/reports/purchase-orders", "Purchase orders"],
        ["/oe/reports/goods-received", "Goods received"],
        ["/oe/reports/landed-cost", "Landed cost"],
      ] as const) {
        await page.goto(path);
        await page.waitForSelector(`h1:has-text("${heading}")`);
        await setTheme(page, "light");
        await assertNoSeriousViolations(page);
        await setTheme(page, "dark");
        await assertNoSeriousViolations(page);
      }
    });
  });
});
