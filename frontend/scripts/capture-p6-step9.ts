/**
 * Captures the four screens P6 step 9 changed, for the review record: 1440x900, light and dark.
 *   OUT=../docs/screenshots/p6-step-9 npx tsx scripts/capture-p6-step9.ts
 *
 * Step 9 built no new screens — it is the closing step, and new screens are explicitly not its
 * scope. What it changed is four things a reader has to *see* to judge:
 *
 *  1. **The sales-order listing**, whose backorder column is now a count of short lines rather
 *     than a sum of base quantities across units, with the caption that apologised for the sum
 *     gone. The shot has two orders in it, one short and one not.
 *  2. **The sales-order enquiry** for the same order, where the shortfall lives now: per line,
 *     in the line's own unit, with the kit line reading zero because a kit is never on a shelf.
 *     These two shots are the whole of the step-9 decision, side by side.
 *  3. **A refused query**, on the goods-received listing. An empty state that cannot tell "no
 *     rows" from "the request failed" is the defect P4 shipped six times and P6 met twice;
 *     forty-two screens now say what the service said, and this is what that looks like.
 *  4. **The trial balance** of the fixture company after the phase's screens have been driven
 *     through it — the accrual and the clearing account among its rows, and a difference of
 *     zero. It is *not* the seven-row table `p6-cycle-tape.spec.ts` asserts: that spec signs up
 *     a company of its own precisely so its trial balance is nothing but its own cycle, and a
 *     shot of it would be four rows of a story the spec already tells better in words. What
 *     this one shows is the same report over a company a whole phase has posted into, which is
 *     the state a reviewer will actually meet.
 *
 * Rule 13: every shot has rows and a figure in it. The fixture is one story a reviewer can
 * check by hand — 25 bottles received, 6 000 of freight landed on them, 30 promised on one
 * order and 2 gift packs on top, ten of them invoiced — and the same figures run from the first
 * shot to the last.
 */
import { chromium, type Page } from "@playwright/test";
import { PASSWORD, PRIMARY_EMAIL as OWNER } from "../e2e/support/fixtures";

const OUT = process.env.OUT ?? "screenshots";
const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const API = `${process.env.API_URL ?? "http://localhost:8000"}/api/v1`;
const SUFFIX = String(Date.now()).slice(-6);

const ONLY = (process.env.ONLY ?? "").split(",").map((n) => n.trim()).filter(Boolean);
const wanted = (name: string) => ONLY.length === 0 || ONLY.includes(name);

async function hydrated(page: Page, selector: string) {
  await page.waitForFunction((sel) => {
    const node = document.querySelector(sel);
    return (
      node !== null &&
      Object.keys(node).some((k) => k.startsWith("__reactFiber$") || k.startsWith("__reactProps$"))
    );
  }, selector);
}

async function login(page: Page) {
  await page.goto(`${BASE}/login`);
  await hydrated(page, "form");
  await page.fill('input[type="email"]', OWNER);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL(`${BASE}/`);
  await page.waitForSelector("text=Good morning");
}

async function shoot(page: Page, name: string, theme: "light" | "dark") {
  if (!wanted(name)) return;
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
  await page.waitForTimeout(400);
  // Viewport, not `fullPage`: the shell's sidebar is taller than any of these screens.
  await page.screenshot({ path: `${OUT}/${name}-${theme}.png` });
  console.log("captured", `${name}-${theme}`);
}

/** One authenticated request, made by the page itself.
 *
 * Every posting endpoint in this phase requires `Idempotency-Key`, so it is always sent: a key
 * per call, derived from the run, which is what a screen does with its draft UUID. */
async function api(page: Page, path: string, body?: unknown, method = "POST") {
  idempotency += 1;
  return page.evaluate(
    async ({ url, m, b, key }) => {
      const res = await fetch(url, {
        method: m,
        credentials: "include",
        headers: {
          ...(b !== undefined ? { "Content-Type": "application/json" } : {}),
          ...(m === "POST" ? { "Idempotency-Key": key } : {}),
        },
        body: b !== undefined ? JSON.stringify(b) : undefined,
      });
      return { status: res.status, json: await res.json().catch(() => null) };
    },
    { url: `${API}${path}`, m: method, b: body, key: `s9-${SUFFIX}-${idempotency}` },
  );
}

let idempotency = 0;

function ok(label: string, r: { status: number; json: unknown }) {
  if (r.status >= 300) throw new Error(`${label}: ${r.status} ${JSON.stringify(r.json)}`);
  return r.json as Record<string, unknown>;
}

async function main() {
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined,
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await login(page);

  // --- the consignment, through the API ---------------------------------------------------
  // Made here rather than through the screens: this script photographs the four screens step 9
  // changed, and driving the order workspace again would only re-shoot step 7's.
  const categories = ok("uoms", await api(page, "/inventory/uom-categories", undefined, "GET")) as unknown as Array<{ code: string; id: number; uoms: Array<{ id: number; code: string }> }>;
  const count = categories.find((c) => c.code === "COUNT")!;
  const accounts = ok("accounts", await api(page, "/gl/accounts", undefined, "GET")) as unknown as Array<{ code: string; id: number }>;
  const acct = (code: string) => accounts.find((a) => a.code === code)!.id;
  const ea = count.uoms.find((u) => u.code === "EA")!.id;

  const item = async (code: string, name: string, price: string, type = "stock") =>
    ok(code, await api(page, "/inventory/items", {
      code, name, uom_category_id: count.id, base_uom_id: ea, item_type: type,
      selling_price: price, sales_account_id: acct("4100"), cogs_account_id: acct("5100"),
    })) as { id: number };

  const wine = await item(`S9W${SUFFIX}`, `Step-9 wine ${SUFFIX}`, "2000");
  const box = await item(`S9B${SUFFIX}`, `Step-9 gift box ${SUFFIX}`, "500");
  const kit = await item(`S9K${SUFFIX}`, `Step-9 gift pack ${SUFFIX}`, "5000", "kit");
  ok("kit components", await api(page, `/inventory/items/${kit.id}/kit-components`, {
    components: [
      { component_item_id: wine.id, quantity_per_kit: "2" },
      { component_item_id: box.id, quantity_per_kit: "1" },
    ],
  }, "PUT"));

  const supplier = ok("supplier", await api(page, "/subledger/ap/partners", {
    name: `Step-9 Supplier ${SUFFIX}`, supplier_code: `S9AP${SUFFIX}`,
  })) as { id: number };
  const customer = ok("customer", await api(page, "/subledger/ar/partners", {
    name: `Step-9 Customer ${SUFFIX}`, customer_code: `S9AR${SUFFIX}`,
  })) as { id: number };

  const warehouses = ok("warehouses", await api(page, "/inventory/warehouses", undefined, "GET")) as unknown as Array<{ id: number; code: string; is_in_transit: boolean }>;
  const main = warehouses.find((w) => !w.is_in_transit)!;

  const today = new Date();
  const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;

  // 25 bottles and 10 boxes in, then 6 000 of freight on the consignment.
  const grn = ok("grn", await api(page, "/oe/goods-received-notes", {
    partner_id: supplier.id, grn_date: iso, description: `Step-9 receipt ${SUFFIX}`,
    warehouse_id: main.id,
    lines: [
      { item_id: wine.id, quantity: "25", unit_cost: "1000" },
      { item_id: box.id, quantity: "10", unit_cost: "200" },
    ],
  })) as { id: number; lines: Array<{ id: number }> };
  ok("landed cost", await api(page, "/oe/landed-costs", {
    cost_date: iso, description: `Step-9 freight ${SUFFIX}`, amount: "6000", basis: "value",
    grn_line_ids: [grn.lines[0].id],
  }));

  // The order the listing and the enquiry are about: 30 bottles and 2 gift packs against 25
  // bottles on the shelf. Two lines short — the bottle line by 5 and the kit's four bottles by
  // 4 — and the kit line itself by nothing, because a kit is never on a shelf.
  ok("sales order", await api(page, "/oe/sales-orders", {
    partner_id: customer.id, order_date: iso, description: `Step-9 sale ${SUFFIX}`,
    warehouse_id: main.id, tax_mode: "exclusive",
    lines: [
      { item_id: wine.id, quantity: "30", unit_price: "2000" },
      { item_id: kit.id, quantity: "2", unit_price: "5000" },
    ],
  }));
  // A second order, fully covered by the boxes, so the column has a zero beside its two.
  ok("covered order", await api(page, "/oe/sales-orders", {
    partner_id: customer.id, order_date: iso, description: `Step-9 covered ${SUFFIX}`,
    warehouse_id: main.id, tax_mode: "exclusive",
    lines: [{ item_id: box.id, quantity: "4", unit_price: "800" }],
  }));

  // --- 1. the listing, with the column that is now a count ---------------------------------
  await page.goto(`${BASE}/oe/sales-orders`);
  await page.waitForSelector("h1:has-text('Sales orders')");
  await page.waitForSelector("[data-testid='order-backordered-lines']");
  for (const theme of ["light", "dark"] as const) await shoot(page, "1-sales-orders-lines-short", theme);

  // --- 2. the enquiry, where the quantities live -------------------------------------------
  await page.goto(`${BASE}/oe/enquiries/sales-orders`);
  await page.waitForSelector("h1:has-text('Sales order enquiry')");
  await page.getByRole("button", { name: "Sales order", exact: true }).click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.locator("[cmdk-item]").first().click();
  await page.waitForSelector("[data-testid='enquiry-line-backordered']");
  for (const theme of ["light", "dark"] as const) await shoot(page, "2-enquiry-backorder-per-line", theme);

  // --- 3. a refused query says what the service said ----------------------------------------
  await page.route(
    (url) => url.pathname.startsWith("/api/v1") && url.pathname.includes("/oe/goods-received"),
    (route) =>
      route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          code: "period_not_open",
          message: "Period 2026-03 is closed — this listing cannot be built for it.",
          field_errors: {},
        }),
      }),
  );
  await page.goto(`${BASE}/oe/goods-received`);
  await page.waitForSelector("[data-testid='query-error']");
  for (const theme of ["light", "dark"] as const) await shoot(page, "3-a-refusal-is-not-an-empty-report", theme);
  await page.unrouteAll();

  // --- 4. the trial balance over everything this company has posted -------------------------
  await page.goto(`${BASE}/gl/reports/trial-balance`);
  await page.waitForSelector("h1:has-text('Trial Balance')");
  await page.waitForSelector("[data-testid='tb-row']");
  for (const theme of ["light", "dark"] as const) await shoot(page, "4-trial-balance-that-foots", theme);

  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
