/**
 * Captures the P6 step-7 order-entry screens for the review record: 1440x900, light and dark.
 * Run against the dev stack, the same prerequisites `npm run e2e` needs:
 *   OUT=../docs/screenshots/p6-step-7 npx tsx scripts/capture-p6-orders.ts
 *
 * Every shot has **rows in it**. Rule 13 is explicit that an empty-state screenshot proves the
 * route compiles and nothing else, so this script drives a whole cycle through the real
 * endpoints — as the signed-in owner, never by writing to the database behind the app — and
 * photographs the screens with that cycle's figures on them.
 *
 * The fixture is one consignment: 40 bottles and 10 gift boxes ordered, 25 bottles and all 10
 * boxes received, 60 000 of freight landed on the bottles, and a sales order that promises
 * more bottles than the shelf holds so the backorder column has something to say. A reviewer
 * can follow the same numbers from the purchase order to the receipt to the share.
 */
import { chromium, type Page } from "@playwright/test";
import { PASSWORD, PRIMARY_EMAIL as OWNER } from "../e2e/support/fixtures";

const OUT = process.env.OUT ?? "screenshots";
const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const API = `${process.env.API_URL ?? "http://localhost:8000"}/api/v1`;

/** `ONLY=5-goods-received` re-captures just that one, rather than rewriting twenty-six files
 * to change one of them. */
const ONLY = (process.env.ONLY ?? "")
  .split(",")
  .map((name) => name.trim())
  .filter(Boolean);

function wanted(...names: string[]): boolean {
  return ONLY.length === 0 || names.some((name) => ONLY.includes(name));
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
  await page.screenshot({ path: `${OUT}/${name}-${theme}.png` });
  console.log("captured", `${name}-${theme}`);
}

/** One authenticated request, made by the page itself. Declared at module scope rather than
 * inside a `page.evaluate` body: `tsx` compiles named arrow functions with an `__name` helper
 * that exists in Node and not in the browser. */
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

const TAG = "SHOT";
const WINE = `${TAG}-WINE`;
const BOX = `${TAG}-BOX`;
const SERVICE = `${TAG}-DELIVERY`;
const KIT = `${TAG}-GIFT2`;

interface Cycle {
  purchaseOrderId: number;
  grnId: number;
  grnNumber: string;
  landedCostId: number;
  salesOrderId: number;
  salesOrderNumber: string;
  kitLineId: number;
}

/**
 * One consignment, bought and sold.
 *
 * Idempotent by code for the catalogue, like `seed_e2e`, because the script is run repeatedly
 * while a shot is tuned. The **documents** are not: an order claims a number every time, and
 * that is correct — a screenshot of a listing with four purchase orders on it is a screenshot
 * of a listing, which is what it is meant to show.
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
      // The weight a landed cost on the `weight` basis reads.
      weight_per_base_unit: itemType === "stock" ? "1.2" : null,
    })) as Named;
  }

  const wine = await item(WINE, "Rugari Red 750ml", "2000", "stock");
  const box = await item(BOX, "Two-bottle gift box", "800", "stock");
  const service = await item(SERVICE, "Delivery to Kigali", "30000", "service");
  const kit = await item(KIT, "Gift pack — two reds", "5000", "kit");
  await apiOk(
    page,
    `/inventory/items/${kit.id}/kit-components`,
    {
      components: [
        { component_item_id: wine.id, quantity_per_kit: "2" },
        { component_item_id: box.id, quantity_per_kit: "1" },
      ],
    },
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

  const today = new Date().toISOString().slice(0, 10);

  const po = (await apiOk(page, "/oe/purchase-orders", {
    partner_id: supplier.id,
    order_date: today,
    description: "March container — reds and gift boxes",
    warehouse_id: 1,
    lines: [
      { item_id: wine.id, quantity: "40", unit_price: "45000" },
      { item_id: box.id, quantity: "10", unit_price: "10000" },
      { item_id: service.id, quantity: "1", unit_price: "30000" },
    ],
  })) as Doc;

  const grn = (await apiOk(page, "/oe/goods-received-notes", {
    partner_id: supplier.id,
    grn_date: today,
    description: "Part delivery, March container",
    warehouse_id: 1,
    purchase_order_id: po.id,
    supplier_reference: "DN-44812",
    lines: [
      {
        item_id: wine.id,
        quantity: "25",
        unit_cost: "45000",
        purchase_order_line_id: po.lines[0].id,
      },
      {
        item_id: box.id,
        quantity: "10",
        unit_cost: "10000",
        purchase_order_line_id: po.lines[1].id,
      },
    ],
  })) as Doc;

  const landed = (await apiOk(page, "/oe/landed-costs", {
    cost_date: today,
    description: "Freight and clearing, March container",
    reference: "FWD-2291",
    amount: "60000",
    basis: "value",
    grn_line_ids: [grn.lines[0].id, grn.lines[1].id],
  })) as Doc;

  const so = (await apiOk(page, "/oe/sales-orders", {
    partner_id: customer.id,
    order_date: today,
    description: "Restaurant order — reds and two gift packs",
    warehouse_id: 1,
    lines: [
      { item_id: wine.id, quantity: "30", unit_price: "2000" },
      { item_id: kit.id, quantity: "2", unit_price: "5000" },
    ],
  })) as Doc;
  const kitLine = so.lines.find(
    (line) => line.item_id === kit.id && line.kit_parent_line_id == null,
  )!;

  return {
    purchaseOrderId: po.id,
    grnId: grn.id,
    grnNumber: grn.number,
    landedCostId: landed.id,
    salesOrderId: so.id,
    salesOrderNumber: so.number,
    kitLineId: kitLine.id,
  };
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
    // --- the purchase side ---------------------------------------------------------------
    await page.goto(`${BASE}/oe/purchase-orders`);
    await page.locator("tbody tr").first().waitFor({ state: "visible" });
    await shoot(page, "1-purchase-orders", theme);

    await page.goto(`${BASE}/oe/purchase-orders/${cycle.purchaseOrderId}`);
    await page.waitForSelector("[data-testid='order-total']");
    await shoot(page, "2-purchase-order", theme);

    // The receipt the order prepares, with what is still outstanding beside the lines.
    await page.goto(
      `${BASE}/oe/goods-received/new?purchase_order_id=${cycle.purchaseOrderId}`,
    );
    await page.waitForSelector("[data-testid='grn-outstanding']");
    await shoot(page, "3-goods-receipt-from-order", theme);

    await page.goto(`${BASE}/oe/goods-received`);
    await page.locator("tbody tr").first().waitFor({ state: "visible" });
    await shoot(page, "4-goods-received", theme);

    await page.goto(`${BASE}/oe/goods-received/${cycle.grnId}`);
    await page.waitForSelector("[data-testid='grn-value']");
    await shoot(page, "5-goods-receipt", theme);

    // --- the cost that lands on it ---------------------------------------------------------
    await page.goto(`${BASE}/oe/landed-costs`);
    await page.locator("tbody tr").first().waitFor({ state: "visible" });
    await shoot(page, "6-landed-costs", theme);

    await page.goto(`${BASE}/oe/landed-costs/${cycle.landedCostId}`);
    await page.waitForSelector("[data-testid='landed-cost-share']");
    await shoot(page, "7-landed-cost", theme);

    // The preview, which is the screen's whole argument: the shares before anything posts.
    await page.goto(`${BASE}/oe/landed-costs/new`);
    await page.waitForSelector("h1:has-text('New landed cost')");
    await page.getByLabel("Amount", { exact: true }).fill("60000");
    await page.getByLabel("Description", { exact: true }).fill("Freight, April container");
    await page.getByRole("button", { name: "Add a receipt", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(cycle.grnNumber);
    await page.locator(`[cmdk-item]:has-text("${cycle.grnNumber}")`).first().click();
    await page.getByRole("checkbox").first().check();
    await page.getByRole("checkbox").nth(1).check();
    await page.getByRole("button", { name: "Preview shares" }).click();
    await page.waitForSelector("[data-testid='target-share']");
    await shoot(page, "8-landed-cost-preview", theme);

    // --- the supplier invoice, in matching mode --------------------------------------------
    await page.goto(`${BASE}/ap/supplier-invoices/new?grn_id=${cycle.grnId}`);
    await page.waitForSelector("[data-testid='document-outstanding']");
    await shoot(page, "9-supplier-invoice-matching", theme);

    // --- the sales side ---------------------------------------------------------------------
    await page.goto(`${BASE}/oe/sales-orders`);
    await page.locator("tbody tr").first().waitFor({ state: "visible" });
    await shoot(page, "10-sales-orders", theme);

    await page.goto(`${BASE}/oe/sales-orders/${cycle.salesOrderId}`);
    await page.waitForSelector("[data-testid='order-total']");
    await shoot(page, "11-sales-order", theme);

    // The workspace, with the kit line on the grid it is keyed on.
    await page.goto(`${BASE}/oe/sales-orders/${cycle.salesOrderId}/edit`);
    await page.waitForSelector("grid, [role='grid']");
    await page.waitForTimeout(600);
    await shoot(page, "12-sales-order-edit", theme);

    // Breakup, from the order and from its own screen.
    await page.goto(`${BASE}/oe/sales-orders/${cycle.salesOrderId}`);
    await page.waitForSelector("[data-testid='order-total']");
    await page.getByRole("button", { name: "Breakup" }).first().click();
    await page.waitForSelector("[role='dialog']");
    await page.waitForTimeout(300);
    await shoot(page, "13-breakup-dialog", theme);
    await page.keyboard.press("Escape");

    await page.goto(`${BASE}/oe/breakup`);
    await page.waitForSelector("h1:has-text('Breakup')");
    await page.getByRole("button", { name: "Sales order", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(cycle.salesOrderNumber);
    await page.locator(`[cmdk-item]:has-text("${cycle.salesOrderNumber}")`).first().click();
    await page.locator("tbody tr").first().waitFor({ state: "visible" });
    await shoot(page, "14-breakup", theme);

    // And the customer invoice the order prepares, with the item lines decision 1 added.
    await page.goto(`${BASE}/ar/invoices/new?sales_order_id=${cycle.salesOrderId}`);
    await page.waitForSelector("[data-testid='document-outstanding']");
    await shoot(page, "15-customer-invoice-from-order", theme);
  }

  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
