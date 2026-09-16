import { expect, test, type Page } from "@playwright/test";
import {
  PRIMARY_EMAIL,
  assertNoSeriousViolations,
  login,
  pageFetch,
  pickCombobox,
  setTheme,
} from "./support/fixtures";

/**
 * P6 step 7 — the order-entry transaction screens, driven as the **two cycles they exist for**
 * rather than as nine screens opened one at a time.
 *
 * *Procure to pay* runs first and *order to cash* second, and that order is the fixture: the
 * goods the second cycle sells are the goods the first one received. Seeding stock through an
 * adjustment instead would have been a shorter file and a weaker one — it would prove the
 * sales screens against a position no purchasing screen had put there.
 *
 * Rule 13 shapes every assertion: each screen is opened with data on it and a **figure** is
 * read back as the page renders it, never a heading. Per the step-6 standard each cycle reads
 * at least one formatted **money** value and one formatted **quantity**:
 *
 * * money — the purchase order's total `FRw 1,830,000`, the receipt's value, the landed cost's
 *   share, the sales order's total;
 * * quantity — 40 ordered against 25 received, and the 10 bottles and 2 kits on the sale.
 *
 * **What this file cannot see.** The journal behind any of it. Every posting screen here hands
 * off to an entry the GL owns, and what those entries contain — that a receipt credits 2350,
 * that a matched invoice relieves it, that a landed cost debits stock and credits clearing —
 * is the acceptance tape's and the backend suite's to prove. This file proves that a person
 * can drive the cycle end to end and that the figures they read on the way are the right ones.
 */

const SUFFIX = String(Date.now()).slice(-6);
const WINE = `P7W${SUFFIX}`;
const BOX = `P7B${SUFFIX}`;
const SERVICE = `P7S${SUFFIX}`;
const KIT = `P7K${SUFFIX}`;
const CUSTOMER = `P7C${SUFFIX}`;
const SUPPLIER = `P7P${SUFFIX}`;

interface Named {
  id: number;
  code: string;
}

interface Fixture {
  wine: Named;
  box: Named;
  service: Named;
  kit: Named;
}

/** The catalogue and the two partners, made through the API.
 *
 * Through the API and not through the screens on purpose: `p6-maintenance.spec.ts` already
 * proves the item dialog and the kit editor, and re-keying them here would buy nothing and
 * cost two minutes on every run. What this file is about starts at the order.
 */
async function seed(page: Page): Promise<Fixture> {
  const categories = await pageFetch(page, "/inventory/uom-categories");
  const rows = categories.json as Array<Named & { uoms: Named[] }>;
  const count = rows.find((c) => c.code === "COUNT")!;
  const each = count.uoms.find((u) => u.code === "EA")!;

  async function item(code: string, name: string, price: string, itemType: string): Promise<Named> {
    const res = await pageFetch(page, "/inventory/items", {
      method: "POST",
      body: {
        code,
        name,
        uom_category_id: count.id,
        base_uom_id: each.id,
        item_type: itemType,
        selling_price: price,
        weight_per_base_unit: itemType === "stock" ? "1.2" : null,
      },
    });
    expect(res.ok, JSON.stringify(res.json)).toBe(true);
    return res.json as Named;
  }

  async function partner(role: "ar" | "ap", code: string, name: string): Promise<Named> {
    const res = await pageFetch(page, `/subledger/${role}/partners`, {
      method: "POST",
      body: { name, [role === "ar" ? "customer_code" : "supplier_code"]: code },
    });
    expect(res.ok, JSON.stringify(res.json)).toBe(true);
    return res.json as Named;
  }

  const wine = await item(WINE, `P7 Red ${SUFFIX}`, "2000", "stock");
  const box = await item(BOX, `P7 Gift box ${SUFFIX}`, "800", "stock");
  const service = await item(SERVICE, `P7 Delivery ${SUFFIX}`, "50000", "service");
  const kit = await item(KIT, `P7 Gift pack ${SUFFIX}`, "5000", "kit");

  const defined = await pageFetch(page, `/inventory/items/${kit.id}/kit-components`, {
    method: "PUT",
    body: {
      components: [
        { component_item_id: wine.id, quantity_per_kit: "2" },
        { component_item_id: box.id, quantity_per_kit: "1" },
      ],
    },
  });
  expect(defined.ok, JSON.stringify(defined.json)).toBe(true);

  await partner("ar", CUSTOMER, `P7 Customer ${SUFFIX}`);
  await partner("ap", SUPPLIER, `P7 Supplier ${SUFFIX}`);

  return { wine, box, service, kit };
}

/** One line of an order grid: item, then quantity, then a price if the catalogue's is not
 * what this document says. Rows are one-based in the accessible names, as a person counts
 * them. */
async function orderLine(
  page: Page,
  row: number,
  code: string,
  quantity: string,
  unitPrice?: string,
) {
  await page.getByRole("button", { name: `Item, row ${row}`, exact: true }).click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(code);
  await page.locator(`[cmdk-item]:has-text("${code}")`).first().click();
  await page.getByLabel(`Quantity, row ${row}`, { exact: true }).fill(quantity);
  if (unitPrice !== undefined) {
    await page.getByLabel(`Unit price, row ${row}`, { exact: true }).fill(unitPrice);
  }
}

let fixture: Fixture;

// Each cycle is a dozen screens and half a dozen postings; the default per-test budget is
// sized for one screen. Serial because the second cycle sells what the first one received.
test.describe.configure({ mode: "serial", timeout: 240_000 });

test.describe("P6 order entry", () => {
  test.beforeAll(async ({ browser }) => {
    const page = await browser.newPage();
    await login(page, PRIMARY_EMAIL);
    fixture = await seed(page);
    await page.close();
  });

  // ---------------------------------------------------------------------------------------
  // Procure to pay: order 40 bottles, receive 25, land the freight on them, invoice the
  // receipt, and invoice the service line the receipt could not carry.
  // ---------------------------------------------------------------------------------------
  // PATH: /oe/purchase-orders/new -> POST /oe/purchase-orders -> the detail, and out through
  // Receive and Process invoice.
  // CANNOT SEE: the accrual entry behind the receipt — the tape's.
  test("procure to pay: order, receive part of it, land a cost, invoice both halves", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);

    // --- the order -----------------------------------------------------------------------
    await page.goto("/oe/purchase-orders/new");
    await page.waitForSelector("h1:has-text('New purchase order')");
    await pickCombobox(page, "Supplier", SUPPLIER);
    await page.getByLabel("Description", { exact: true }).fill(`P7 purchase ${SUFFIX}`);
    await orderLine(page, 1, WINE, "40", "45000");
    await orderLine(page, 2, BOX, "10", "10000");
    await orderLine(page, 3, SERVICE, "1", "30000");
    await page.getByRole("button", { name: "Save order" }).click();
    // The save navigates to the order it just claimed a number for. Waited for by URL rather
    // than by the figure: on a cold dev server the detail route is compiled on this first
    // visit, and a figure that has not rendered yet because webpack is still running is not
    // the same failure as a figure that is wrong.
    await page.waitForURL(/\/oe\/purchase-orders\/\d+$/, { timeout: 60_000 });

    // 40 x 45 000, 10 x 10 000 and the 30 000 service line, before tax and with no tax code
    // on any of them.
    await expect(page.getByTestId("order-total")).toHaveText("FRw 1,930,000");
    const orderUrl = page.url();
    await expect(page.getByTestId("line-ordered").first()).toHaveText("40");
    await expect(page.getByTestId("line-received").first()).toHaveText("0");

    // --- the receipt, prepared from it ----------------------------------------------------
    await page.getByTestId("receive-order").click();
    await page.waitForSelector("h1:has-text('New goods receipt')");
    // The two stock lines and not the third: a service is received by its invoice, so the
    // order's service line is not on this document at all.
    await expect(page.getByTestId("grn-outstanding").locator("li")).toHaveCount(2);
    await expect(page.getByTestId("grn-outstanding")).toContainText("40 left");
    await expect(page.getByTestId("grn-outstanding")).toContainText("10 left");
    await expect(page.getByLabel("Quantity, row 1", { exact: true })).toHaveValue("40");

    // A short delivery: 25 of the 40 bottles, all 10 boxes, and the rest stays on the order.
    await page.getByLabel("Quantity, row 1", { exact: true }).fill("25");
    await page.getByLabel("Delivery note").fill(`DN-${SUFFIX}`);
    await page.getByRole("button", { name: "Post receipt" }).click();
    await page.waitForURL(/\/oe\/goods-received\/\d+$/, { timeout: 60_000 });

    await page.waitForSelector("[data-testid='grn-value']");
    // 25 x 45 000 and 10 x 10 000, valued at what the delivery note said.
    await expect(page.getByTestId("grn-value")).toHaveText("FRw 1,225,000");
    await expect(page.getByTestId("grn-line-quantity").first()).toHaveText("25");
    // Evolution's word for a receipt nothing has invoiced yet.
    await expect(page.getByText("Unprocessed").first()).toBeVisible();
    const grnUrl = page.url();
    const grnNumber = await page.locator("h1").first().innerText();

    // The listing agrees, and carries the figure the accrual account is reconciled against.
    await page.goto("/oe/goods-received");
    await page.waitForSelector("h1:has-text('Goods received')");
    const grnRow = page.locator("tbody tr", { hasText: grnNumber }).first();
    await expect(grnRow.getByTestId("grn-unmatched-value")).toHaveText("FRw 1,225,000");

    // --- the freight, landed on what arrived ----------------------------------------------
    await page.goto("/oe/landed-costs/new");
    await page.waitForSelector("h1:has-text('New landed cost')");
    await page.getByLabel("Amount", { exact: true }).fill("60000");
    await page.getByLabel("Description", { exact: true }).fill(`P7 freight ${SUFFIX}`);
    await pickCombobox(page, "Add a receipt", grnNumber);
    await page.getByRole("checkbox").first().check();
    await page.getByRole("button", { name: "Preview shares" }).click();

    // One target of the two on the receipt, so the whole cost lands on it — and the preview
    // says so before anything is written. The share and the amount are the same figure by
    // arithmetic, which is exactly what makes a wrong split visible here rather than in the
    // ledger.
    await expect(page.getByTestId("target-share").first()).toContainText("FRw 60,000");
    await expect(page.getByTestId("landed-cost-allocated")).toHaveText("FRw 60,000");
    await page.getByRole("button", { name: "Post", exact: true }).click();
    await page.waitForURL(/\/oe\/landed-costs\/\d+$/, { timeout: 60_000 });

    await page.waitForSelector("[data-testid='landed-cost-share']");
    await expect(page.getByTestId("landed-cost-share")).toHaveText("FRw 60,000");
    // The goods are still in the warehouse, so the cost went into their value rather than
    // straight to cost of sales.
    await expect(page.getByText("Stock").first()).toBeVisible();

    // --- the supplier invoice, matched to the receipt -------------------------------------
    await page.goto(grnUrl);
    await page.waitForSelector("[data-testid='grn-value']");
    await page.getByTestId("process-invoice").click();
    await page.waitForURL(/\/ap\/supplier-invoices\/new\?grn_id=\d+/, { timeout: 60_000 });
    await expect(page.getByTestId("document-outstanding")).toContainText("25 left");
    await expect(page.getByTestId("document-outstanding")).toContainText("10 left");
    // "Post (Ctrl+Enter)" — the AR/AP screens put the shortcut in the label itself.
    await page.getByRole("button", { name: /^Post \(/ }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 60_000 });

    // Back on the receipt: fully claimed, and Reverse says why it cannot be pressed rather
    // than being greyed out with no reason.
    //
    // `exact` on the chip, because "Unprocessed" **contains** "Processed": the substring match
    // this used to make was satisfied by the status it was meant to prove had changed, so the
    // assertion passed whether the invoice had claimed the receipt or not.
    await page.goto(grnUrl);
    await page.waitForSelector("[data-testid='grn-value']");
    await expect(page.getByText("Processed", { exact: true }).first()).toBeVisible();
    await expect(page.getByTestId("reverse-blocked")).toContainText("reverse that invoice first");
    await expect(page.getByTestId("reverse-grn")).toBeDisabled();

    // --- and the half a receipt cannot carry ----------------------------------------------
    await page.goto(orderUrl);
    await page.waitForSelector("[data-testid='order-total']");
    await expect(page.getByTestId("line-received").first()).toHaveText("25");
    await page.getByTestId("process-invoice").click();
    await page.waitForURL(/\/ap\/supplier-invoices\/new\?purchase_order_id=\d+/, {
      timeout: 60_000,
    });
    // The service line, and only it: the 15 bottles still owed are a receipt's business.
    // Thousand-separated, because the cell is not being edited — the grid swaps to raw digits
    // only while the caret is in it.
    await expect(page.getByLabel("Unit price, row 1", { exact: true })).toHaveValue("30,000");
    await expect(page.getByTestId("document-outstanding").locator("li")).toHaveCount(1);
    await page.getByRole("button", { name: /^Post \(/ }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 60_000 });
  });

  // ---------------------------------------------------------------------------------------
  // Order to cash, on the stock the first cycle received.
  // ---------------------------------------------------------------------------------------
  // PATH: /oe/sales-orders/new -> POST /oe/sales-orders -> Breakup -> the edit that would
  // reset it -> Invoice -> POST /subledger/ar/documents.
  // CANNOT SEE: the cost of the sale, which is the costing engine's and the tape's.
  test("order to cash: promise it, break the kit up, change your mind, invoice it", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);

    await page.goto("/oe/sales-orders/new");
    await page.waitForSelector("h1:has-text('New sales order')");
    await pickCombobox(page, "Customer", CUSTOMER);
    await page.getByLabel("Description", { exact: true }).fill(`P7 sale ${SUFFIX}`);
    // More bottles than the warehouse holds, on purpose: the default policy takes the order
    // and shows the shortfall rather than refusing the sale (decision 3).
    await orderLine(page, 1, WINE, "30");
    await orderLine(page, 2, KIT, "2");
    await page.getByRole("button", { name: "Save order" }).click();
    await page.waitForURL(/\/oe\/sales-orders\/\d+$/, { timeout: 60_000 });

    // 30 bottles at the catalogue's 2 000, two kits at 5 000.
    await expect(page.getByTestId("order-total")).toHaveText("FRw 70,000");
    const orderUrl = page.url();
    const orderNumber = await page.locator("h1").first().innerText();
    await expect(page.getByTestId("line-ordered").first()).toHaveText("30");

    // The kit is one line with its components under it — four bottles and two boxes for two
    // packs, which is what will leave the warehouse.
    await expect(page.getByText(BOX)).toBeVisible();

    // The listing's backorder column, and the figure it exists for. 25 bottles arrived; this
    // order promises 30 on its own line plus the 4 inside the two kits, so **two lines** are
    // short. The 2 gift boxes the kits explode into are covered by the 10 that came in on the
    // same receipt — which is the point of counting the **exploded** lines rather than the
    // line the customer sees: the warehouse has to find the components, not the kit.
    //
    // A count, not a quantity (step 9). The column used to read `9`: 5 short on the bottle
    // line plus 4 on the kit's bottle component, which happen to share a unit here and would
    // not on an order that mixed them. The 5 and the 4 are asserted on the enquiry below,
    // each against its line — that is where a shortfall has a unit to be in.
    await page.goto("/oe/sales-orders");
    await page.waitForSelector("h1:has-text('Sales orders')");
    const row = page.locator("tbody tr", { hasText: orderNumber }).first();
    await expect(row.getByTestId("order-backordered-lines")).toHaveText("2");
    await expect(row.getByTestId("order-total")).toHaveText("FRw 70,000");

    // The per-line half of the same fact, on the enquiry the listing sends you to. Three lines
    // after the explosion — 30 bottles, 4 bottles, 2 boxes — and the two that are short say by
    // how much, in the unit they are counted in. Their sum is the 9 the listing used to print;
    // that it is printable here and not there is the whole of the step-9 decision.
    await page.goto("/oe/enquiries/sales-orders");
    await page.waitForSelector("h1:has-text('Sales order enquiry')");
    await pickCombobox(page, "Sales order", orderNumber);
    await expect(page.getByTestId("enquiry-line-backordered")).toHaveText(["5", "4", "0"]);

    // The same fact from the other side, and the other sign. The listing shows the shortfall
    // as a positive number because that is what a person is short *by*; the item enquiry shows
    // `available` as the ledger computes it, quantity less committed, and it is **negative**
    // when more has been promised than is held. Both are rendered here, in one pass, because a
    // screen that quietly took the absolute value would look identical and mean something else.
    //
    // 25 bottles on the shelf, 34 promised (30 on the line and 4 inside the two kits): -9.
    // And 15 still owed by the supplier, which is the part-received purchase order.
    await page.goto("/inventory/enquiry");
    await page.waitForSelector("h1:has-text('Item enquiry')");
    await pickCombobox(page, "Item", WINE);
    await expect(page.getByTestId("location-committed").first()).toHaveText("34 EA");
    await expect(page.getByTestId("location-on-order").first()).toHaveText("15 EA");
    await expect(page.getByTestId("location-available").first()).toHaveText("-9 EA");

    // --- the breakup: this customer wants three bottles in the pack, not two --------------
    await page.goto(orderUrl);
    await page.waitForSelector("[data-testid='order-total']");
    await page.getByRole("button", { name: "Breakup" }).first().click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("Quantity").first().fill("6");
    await dialog.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Breakup saved").first()).toBeVisible();

    // The standalone screen sees it as a hand edit, which is the fact the next step protects.
    await page.goto("/oe/breakup");
    await page.waitForSelector("h1:has-text('Breakup')");
    await pickCombobox(page, "Sales order", orderNumber);
    await expect(page.getByText("Edited by hand")).toBeVisible();
    await expect(page.getByTestId("breakup-kit-quantity")).toHaveText("2");

    // --- the edit that would throw it away ------------------------------------------------
    //
    // **Wait for the order, not for the heading.** This used to wait on `h1:has-text('Sales
    // order')`, which is the shell: the workspace renders that heading — without a number in
    // it, and matching as a substring — before the order query has returned, and until step 9
    // it rendered three blank editable rows under it as well. A `fill` that landed in that
    // window was thrown away when the saved order seeded the form, the save then changed
    // nothing, and the reset dialog this test is about never appeared. That is what made these
    // two cycles intermittent (step 8's F-7); the fix is in `order-workspace.tsx`, which no
    // longer offers a grid over an order it has not loaded, and the wait below is what says so
    // from the outside: row 2's quantity holding the *saved* 2 means the seeding has happened.
    await page.goto(`${orderUrl}/edit`);
    await expect(page.getByLabel("Quantity, row 2", { exact: true })).toHaveValue("2");
    await page.getByLabel("Quantity, row 2", { exact: true }).fill("3");
    await page.getByRole("button", { name: "Save order" }).click();

    // Not a toast and not a refusal: a question, naming the line it is about.
    await expect(page.getByTestId("reset-breakup-items")).toContainText(KIT);
    await page.getByTestId("reset-breakup-confirm").click();
    await page.waitForURL(/\/oe\/sales-orders\/\d+$/, { timeout: 60_000 });
    await expect(page.getByTestId("order-total")).toHaveText("FRw 75,000");

    // --- the invoice ----------------------------------------------------------------------
    await page.getByTestId("invoice-order").click();
    await page.waitForURL(/\/ar\/invoices\/new\?sales_order_id=\d+/, { timeout: 60_000 });
    // Prepared at what the order still owes: 30 bottles, and the kit line with the explosion
    // behind it.
    await expect(page.getByTestId("document-outstanding")).toContainText("30 left");

    // Posting all of it takes out stock that is not there, and the refusal names the item and
    // the warehouse in the workspace banner — not in a toast that has gone by the time the
    // operator looks for the line it is about.
    await page.getByRole("button", { name: /^Post \(/ }).click();
    await expect(page.getByTestId("workspace-error")).toContainText(WINE);
    await expect(page.getByTestId("workspace-error")).toContainText("MAIN holds");

    // Lowered to what the shelf can cover — which is the one direction this screen allows.
    await page.getByLabel("Quantity, row 1", { exact: true }).fill("10");
    await page.getByRole("button", { name: /^Post \(/ }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 60_000 });

    await page.goto(orderUrl);
    await page.waitForSelector("[data-testid='order-total']");
    // Ten of the thirty invoiced, so the order is part invoiced and the rest still stands.
    await expect(page.getByText("Part invoiced").first()).toBeVisible();
    await expect(page.getByTestId("line-ordered").first()).toHaveText("30");
  });

  test.describe("accessibility", () => {
    // PATH: axe over the screens the nav sweep cannot reach — a document workspace has no nav
    // row, so nothing else in the suite opens one.
    // CANNOT SEE: the dialogs, each of which is covered where it is opened above.
    test("the order workspaces — light and dark", async ({ page }) => {
      await login(page, PRIMARY_EMAIL);
      for (const [path, heading] of [
        ["/oe/sales-orders/new", "New sales order"],
        ["/oe/purchase-orders/new", "New purchase order"],
        ["/oe/goods-received/new", "New goods receipt"],
        ["/oe/landed-costs/new", "New landed cost"],
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
