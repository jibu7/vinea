/**
 * Captures the P6 step-8 enquiry and report screens for the review record: 1440x900, light and
 * dark, plus a **print preview** of each report. Run against the dev stack, the same
 * prerequisites `npm run e2e` needs:
 *   OUT=../docs/screenshots/p6-step-8 npx tsx scripts/capture-p6-enquiries.ts
 *
 * Every shot has rows in it. Rule 13 is explicit that an empty-state screenshot proves the
 * route compiles and nothing else, so this script drives a whole consignment through the real
 * endpoints — as the signed-in owner, never by writing behind the app — and photographs the
 * screens with that consignment's figures on them.
 *
 * **The fixture is one story a reviewer can check by hand.** 120 bottles ordered from Kivu
 * Vintners, 80 received at 1 000, 8 000 of freight landed on those 80, 100 promised to the
 * Mille Collines, 50 of them invoiced — so the purchase report owes 40, the sales report owes
 * 50, the accrual holds 80 000, and the enquiry shows a commitment the shelf cannot cover. The
 * same figures run from the first shot to the last.
 *
 * The print previews are captured through Chromium's print media emulation, which is what the
 * `@media print` layout these reports share with every P3–P5 report is for: the filter bar and
 * the screen chrome drop away and a masthead carrying the company, the report and its range
 * takes their place. There is no dark variant of a printed page.
 */
import { chromium, type Page } from "@playwright/test";
import { PASSWORD, PRIMARY_EMAIL as OWNER } from "../e2e/support/fixtures";
import { todayIso } from "../src/lib/format";

const OUT = process.env.OUT ?? "screenshots";
const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const API = `${process.env.API_URL ?? "http://localhost:8000"}/api/v1`;

/** `ONLY=3-goods-received-report` re-captures just that one, rather than rewriting sixteen
 * files to change one of them. */
const ONLY = (process.env.ONLY ?? "")
  .split(",")
  .map((name) => name.trim())
  .filter(Boolean);

function wanted(name: string): boolean {
  return ONLY.length === 0 || ONLY.includes(name);
}

async function hydrated(page: Page, selector: string) {
  await page.waitForFunction((sel) => {
    const node = document.querySelector(sel);
    return (
      node !== null &&
      Object.keys(node).some((k) => k.startsWith("__reactFiber$") || k.startsWith("__reactProps$"))
    );
  }, selector);
}

async function login(page: Page, email: string) {
  await page.goto(`${BASE}/login`);
  await hydrated(page, "form");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL(`${BASE}/`);
  await page.waitForSelector("text=Good morning");
}

async function shoot(page: Page, name: string, theme: "light" | "dark") {
  if (!wanted(name)) return;
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
  await page.waitForTimeout(400);
  // Viewport, not `fullPage`: the shell's sidebar is taller than any of these screens, so a
  // full-page shot is two thousand pixels of navigation with the report at the top of it.
  await page.screenshot({ path: `${OUT}/${name}-${theme}.png` });
  console.log("captured", `${name}-${theme}`);
}

/** The same screen as it leaves the printer. One variant, not two: WeasyPrint and a paper tray
 * both render on white, and a "dark" printout is not a thing that exists. */
async function shootPrint(page: Page, name: string) {
  if (!wanted(name)) return;
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
  await page.emulateMedia({ media: "print" });
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${OUT}/${name}-print.png`, fullPage: true });
  await page.emulateMedia({ media: "screen" });
  console.log("captured", `${name}-print`);
}

async function apiCall(
  page: Page,
  path: string,
  body?: unknown,
  method = "POST",
): Promise<{ status: number; json: unknown }> {
  return page.evaluate(
    async ({ url, body, method }) => {
      const res = await fetch(url, {
        method,
        credentials: "include",
        headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      return { status: res.status, json: await res.json().catch(() => null) };
    },
    { url: `${API}${path}`, body, method },
  );
}

/** `apiCall` that refuses to fail quietly: a fixture that 4xx's and says nothing produces a
 * screenshot of the *previous* run's data, which is worse than no screenshot. */
async function apiOk(page: Page, path: string, body?: unknown, method = "POST"): Promise<unknown> {
  const res = await apiCall(page, path, body, method);
  if (res.status >= 300) {
    throw new Error(`${method} ${path} -> ${res.status}: ${JSON.stringify(res.json)}`);
  }
  return res.json;
}

interface Named {
  id: number;
  code: string;
}

interface Doc {
  id: number;
  number: string;
  lines: Array<{ id: number; item_id: number; kit_parent_line_id?: number | null }>;
}

const TAG = "SHOT8";
const WINE = `${TAG}-WINE`;
const KIT = `${TAG}-GIFT2`;
const HAULAGE = `${TAG}-HAULAGE`;

interface Cycle {
  purchaseOrderNumber: string;
  salesOrderNumber: string;
  grnId: number;
  invoiceId: number;
  stockEntryId: number;
}

/**
 * The consignment, **found or created**.
 *
 * Idempotent all the way through, including the documents — which the step-7 capture script
 * deliberately is not, and this one has to be. `ONLY=` exists so that changing one shot does
 * not rewrite twenty-five files, and a script that posted a second order every time it ran
 * would leave the re-captured shots showing two consignments and the rest showing one. The
 * record would disagree with itself and every figure in the README would be wrong for half
 * the images.
 *
 * Keyed on the supplier's and the customer's own documents rather than on a marker of ours:
 * these partners exist for this script alone, so "has this supplier an order already" is the
 * same question as "has this script run".
 */
async function drive(page: Page): Promise<Cycle> {
  const categories = (await apiCall(page, "/inventory/uom-categories", undefined, "GET"))
    .json as Array<Named & { uoms: Named[] }>;
  const count = categories.find((c) => c.code === "COUNT")!;
  const each = count.uoms.find((u) => u.code === "EA")!;

  const existing = (await apiCall(page, "/inventory/items?include_inactive=true", undefined, "GET"))
    .json as Named[];
  const byCode = new Map(existing.map((item) => [item.code, item]));

  async function item(code: string, name: string, price: string, itemType: string): Promise<Named> {
    const found = byCode.get(code);
    if (found) return found;
    return (await apiOk(page, "/inventory/items", {
      code,
      name,
      uom_category_id: count.id,
      base_uom_id: each.id,
      item_type: itemType,
      selling_price: price,
    })) as Named;
  }

  const wine = await item(WINE, "Rugari Red 750ml", "2000", "stock");
  const haulage = await item(HAULAGE, "Haulage to Kigali", "30000", "service");
  const kit = await item(KIT, "Gift pack — two reds", "5000", "kit");
  await apiOk(
    page,
    `/inventory/items/${kit.id}/kit-components`,
    { components: [{ component_item_id: wine.id, quantity_per_kit: "2" }] },
    "PUT",
  );

  const suppliers = (await apiCall(page, "/subledger/ap/partners", undefined, "GET")).json as Array<
    Named & { supplier_code: string | null }
  >;
  const supplier =
    suppliers.find((p) => p.supplier_code === `${TAG}-SUP`) ??
    ((await apiOk(page, "/subledger/ap/partners", {
      name: "Kivu Vintners Ltd",
      supplier_code: `${TAG}-SUP`,
    })) as Named);
  const customers = (await apiCall(page, "/subledger/ar/partners", undefined, "GET")).json as Array<
    Named & { customer_code: string | null }
  >;
  const customer =
    customers.find((p) => p.customer_code === `${TAG}-CUS`) ??
    ((await apiOk(page, "/subledger/ar/partners", {
      name: "Hôtel des Mille Collines",
      customer_code: `${TAG}-CUS`,
    })) as Named);

  const today = todayIso();

  // Already run against this database? Then everything below it exists too, and re-driving
  // would add a second consignment rather than refresh the first.
  const orders = (await apiCall(
    page,
    `/oe/purchase-orders?partner_id=${supplier.id}`,
    undefined,
    "GET",
  )).json as { items: Array<{ id: number }> };
  if (orders.items.length > 0) {
    return existing_(page, orders.items[0].id, customer.id);
  }

  // 120 ordered, and a haulage line a receipt cannot carry — a service is received by its
  // invoice, so the purchase report has one line that leaves it and one that stays.
  const po = (await apiOk(page, "/oe/purchase-orders", {
    partner_id: supplier.id,
    order_date: today,
    description: "September container — reds",
    warehouse_id: 1,
    lines: [
      { item_id: wine.id, quantity: "120", unit_price: "1000" },
      { item_id: haulage.id, quantity: "1", unit_price: "30000" },
    ],
  })) as Doc;

  const grn = (await apiOk(page, "/oe/goods-received-notes", {
    partner_id: supplier.id,
    grn_date: today,
    description: "Part delivery, September container",
    warehouse_id: 1,
    purchase_order_id: po.id,
    supplier_reference: "DN-51107",
    lines: [
      {
        item_id: wine.id,
        quantity: "80",
        unit_cost: "1000",
        purchase_order_line_id: po.lines[0].id,
      },
    ],
  })) as Doc;

  // The forwarder's bill **first**, booked to the clearing account, and the allocation second.
  // That is the order the phase's invariant is stated in — clearing == booked − allocated, and
  // zero once everything booked there has been spread — and a record that allocated a cost
  // nobody had booked would photograph the account sitting at minus eight thousand.
  const accounts = (await apiCall(page, "/gl/accounts", undefined, "GET")).json as Array<
    Named & { code: string }
  >;
  const clearing = accounts.find((a) => a.code === "1370")!;
  await apiOk(page, "/subledger/ap/documents", {
    kind: "invoice",
    partner_id: supplier.id,
    document_date: today,
    description: "Haulage invoice, September container",
    reference: "FWD-3308",
    lines: [
      {
        description: "Haulage to Kigali",
        quantity: "1",
        unit_price: "8000",
        gl_account_id: clearing.id,
      },
    ],
  });

  await apiOk(page, "/oe/landed-costs", {
    cost_date: today,
    description: "Haulage, September container",
    reference: "FWD-3308",
    amount: "8000",
    basis: "quantity",
    grn_line_ids: [grn.lines[0].id],
  });

  // More promised than the shelf can cover once half of it ships, so the enquiry has a signed
  // negative to show and the report has a backorder to owe.
  const so = (await apiOk(page, "/oe/sales-orders", {
    partner_id: customer.id,
    order_date: today,
    description: "Restaurant order — September",
    warehouse_id: 1,
    lines: [{ item_id: wine.id, quantity: "100", unit_price: "2000" }],
  })) as Doc;

  const invoice = (await apiOk(page, "/subledger/ar/documents", {
    kind: "invoice",
    partner_id: customer.id,
    document_date: today,
    description: "First delivery, September order",
    lines: [
      {
        item_id: wine.id,
        quantity: "50",
        unit_price: "2000",
        warehouse_id: 1,
        sales_order_line_id: so.lines[0].id,
      },
    ],
  })) as Doc & { stock_entry_id: number };

  return {
    purchaseOrderNumber: po.number,
    salesOrderNumber: so.number,
    grnId: grn.id,
    invoiceId: invoice.id,
    stockEntryId: invoice.stock_entry_id,
  };
}

/** The cycle this script already built, read back rather than posted again. */
async function existing_(page: Page, purchaseOrderId: number, customerId: number): Promise<Cycle> {
  const po = (await apiCall(page, `/oe/purchase-orders/${purchaseOrderId}`, undefined, "GET"))
    .json as Doc;
  const receipts = (await apiCall(page, "/oe/goods-received", undefined, "GET")).json as {
    items: Array<{ id: number }>;
  };
  const orders = (await apiCall(
    page,
    `/oe/sales-orders?partner_id=${customerId}`,
    undefined,
    "GET",
  )).json as { items: Array<{ id: number; number: string }> };
  const documents = (await apiCall(
    page,
    `/subledger/ar/documents?partner_id=${customerId}`,
    undefined,
    "GET",
  )).json as { items: Array<{ id: number }> };
  const invoice = (await apiCall(
    page,
    `/subledger/ar/documents/${documents.items[0].id}`,
    undefined,
    "GET",
  )).json as { id: number; stock_entry_id: number };
  return {
    purchaseOrderNumber: po.number,
    salesOrderNumber: orders.items[0].number,
    grnId: receipts.items[0].id,
    invoiceId: invoice.id,
    stockEntryId: invoice.stock_entry_id,
  };
}

/** Picks an option in a Combobox by the label on its trigger. */
async function pick(page: Page, label: string, needle: string) {
  await page.getByRole("button", { name: label, exact: true }).click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(needle);
  await page.locator(`[cmdk-item]:has-text("${needle}")`).first().click();
}

async function main() {
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined,
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await login(page, OWNER);
  const cycle = await drive(page);

  for (const theme of ["light", "dark"] as const) {
    // --- the enquiries ---------------------------------------------------------------------
    await page.goto(`${BASE}/oe/enquiries/sales-orders`);
    await page.waitForSelector("h1:has-text('Sales order enquiry')");
    await pick(page, "Sales order", cycle.salesOrderNumber);
    await page.waitForSelector("[data-testid='enquiry-order-total']");
    await shoot(page, "1-sales-order-enquiry", theme);

    await page.goto(`${BASE}/oe/enquiries/purchase-orders`);
    await page.waitForSelector("h1:has-text('Purchase order enquiry')");
    await pick(page, "Purchase order", cycle.purchaseOrderNumber);
    await page.waitForSelector("[data-testid='enquiry-order-total']");
    await shoot(page, "2-purchase-order-enquiry", theme);

    // --- the reports -----------------------------------------------------------------------
    await page.goto(`${BASE}/oe/reports/sales-orders`);
    await page.locator("tbody tr").first().waitFor({ state: "visible" });
    await shoot(page, "3-sales-orders-report", theme);

    await page.goto(`${BASE}/oe/reports/purchase-orders`);
    await page.locator("tbody tr").first().waitFor({ state: "visible" });
    await shoot(page, "4-purchase-orders-report", theme);

    await page.goto(`${BASE}/oe/reports/goods-received`);
    await page.waitForSelector("[data-testid='report-unmatched-total']");
    await page.locator("tbody tr").first().waitFor({ state: "visible" });
    await shoot(page, "5-goods-received-report", theme);

    // The other half of that tie, so the two figures are in the record side by side.
    await page.goto(`${BASE}/gl/enquiries/trial-balance`);
    await page.locator("tbody tr", { hasText: "2350" }).first().waitFor({ state: "visible" });
    await shoot(page, "6-trial-balance-accrual", theme);

    await page.goto(`${BASE}/oe/reports/landed-cost`);
    await page.locator("tbody tr").first().waitFor({ state: "visible" });
    await shoot(page, "7-landed-cost-report", theme);

    // --- the stock enquiry, with the three order columns and the totals row -----------------
    await page.goto(`${BASE}/inventory/enquiry`);
    await page.waitForSelector("h1:has-text('Item enquiry')");
    await pick(page, "Item", WINE);
    await page.waitForSelector("[data-testid='locations-total-quantity']");
    await shoot(page, "8-item-enquiry-committed", theme);

    // And what it says about a kit, which is not zero.
    await page.goto(`${BASE}/inventory/enquiry`);
    await page.waitForSelector("h1:has-text('Item enquiry')");
    await pick(page, "Item", KIT);
    await page.waitForSelector("text=A kit is a virtual bundle");
    await shoot(page, "9-item-enquiry-kit", theme);

    // --- the drill: a companion entry, and the document it leads back to --------------------
    await page.goto(`${BASE}/gl/entries/${cycle.stockEntryId}`);
    await page.waitForSelector("[data-testid='reverse-via-module']");
    await shoot(page, "10-companion-entry-drill", theme);

    await page.goto(`${BASE}/ar/documents/${cycle.invoiceId}`);
    await page.waitForSelector("[data-testid='document-stock-entry']");
    await shoot(page, "11-document-both-entries", theme);
  }

  // --- the print previews, once each -------------------------------------------------------
  for (const [name, path, ready] of [
    ["3-sales-orders-report", "/oe/reports/sales-orders", "tbody tr"],
    ["4-purchase-orders-report", "/oe/reports/purchase-orders", "tbody tr"],
    ["5-goods-received-report", "/oe/reports/goods-received", "tbody tr"],
    ["7-landed-cost-report", "/oe/reports/landed-cost", "tbody tr"],
  ] as const) {
    await page.goto(`${BASE}${path}`);
    await page.locator(ready).first().waitFor({ state: "visible" });
    await shootPrint(page, name);
  }

  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
