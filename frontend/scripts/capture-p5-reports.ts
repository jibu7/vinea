/**
 * Captures the P5 step-8 enquiry and report screens for the review record: 1440x900, light
 * and dark. Run against the dev stack, the same prerequisites `npm run e2e` needs:
 *   OUT=../docs/screenshots/p5-step-8 npx tsx scripts/capture-p5-reports.ts
 *
 * Every shot has **rows in it** and a figure on them (rule 13). The script seeds through the
 * real endpoints as the signed-in owner — the same catalogue the step-7 script uses, so a
 * dev who has run both ends up with one shelf rather than two — then adds what these screens
 * need and step 7 had no reason to leave behind: stock in more than one warehouse, a
 * transfer still in transit (so the valuation report has an in-transit line to show), and a
 * count that has been **processed**, which is the only kind the count report can link to a
 * document.
 *
 * Idempotent by code and by idempotency key, so a re-run to tune one shot does not put a
 * second opening balance on the shelf.
 */
import { chromium, type Page } from "@playwright/test";
import { PASSWORD, PRIMARY_EMAIL as OWNER } from "../e2e/support/fixtures";

const OUT = process.env.OUT ?? "screenshots";
const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const API = `${process.env.API_URL ?? "http://localhost:8000"}/api/v1`;

/** `ONLY=5-valuation` re-captures just that one. */
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
  await page.evaluate((t) => {
    document.documentElement.setAttribute("data-theme", t);
    for (const el of document.querySelectorAll<HTMLElement>("*")) if (el.scrollLeft) el.scrollLeft = 0;
  }, theme);
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/${name}-${theme}.png` });
  console.log("captured", `${name}-${theme}`);
}

async function apiCall(
  page: Page,
  path: string,
  body?: unknown,
  method = "POST",
  idempotencyKey?: string,
): Promise<{ status: number; json: unknown }> {
  return page.evaluate(
    async ({ url, body, method, key }) => {
      const res = await fetch(url, {
        method,
        credentials: "include",
        headers: { "Content-Type": "application/json", "Idempotency-Key": key ?? crypto.randomUUID() },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      return { status: res.status, json: await res.json().catch(() => null) };
    },
    { url: `${API}${path}`, body, method, key: idempotencyKey },
  );
}

async function apiOk(page: Page, path: string, body?: unknown, method = "POST", key?: string): Promise<unknown> {
  const res = await apiCall(page, path, body, method, key);
  if (res.status >= 300) {
    throw new Error(`${method} ${path} -> ${res.status}: ${JSON.stringify(res.json)}`);
  }
  return res.json;
}

interface Named {
  id: number;
  code: string;
}

interface Seeded {
  red: Named;
  white: Named;
  coffee: Named;
  main: Named;
  depot: Named;
}

const TODAY = (() => {
  const now = new Date();
  return [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("-");
})();

async function seed(page: Page): Promise<Seeded> {
  const categories = (await apiCall(page, "/inventory/uom-categories", undefined, "GET")).json as Array<
    Named & { uoms: Named[] }
  >;
  const count = categories.find((c) => c.code === "COUNT")!;
  const weight = categories.find((c) => c.code === "WEIGHT")!;
  const each = count.uoms.find((u) => u.code === "EA")!;
  const kg = weight.uoms.find((u) => u.code === "KG")!;

  const existing = (await apiCall(page, "/inventory/items?include_inactive=true", undefined, "GET")).json as Named[];
  const byCode = new Map(existing.map((item) => [item.code, item]));
  async function item(code: string, name: string, categoryId: number, baseUomId: number, price: string): Promise<Named> {
    const found = byCode.get(code);
    if (found) return found;
    return (await apiOk(page, "/inventory/items", {
      code,
      name,
      uom_category_id: categoryId,
      base_uom_id: baseUomId,
      item_type: "stock",
      selling_price: price,
    })) as Named;
  }
  const red = await item("WINE-750", "Rugari Red 750ml", count.id, each.id, "8500");
  const white = await item("WINE-375", "Rugari White 375ml", count.id, each.id, "5200");
  const coffee = await item("COFFEE-KG", "Kivu Arabica, green", weight.id, kg.id, "6400");

  const warehouses = (await apiCall(page, "/inventory/warehouses", undefined, "GET")).json as Named[];
  const main = warehouses.find((w) => w.code === "MAIN")!;
  let depot = warehouses.find((w) => w.code === "DEPOT");
  if (!depot) {
    const branches = (await apiCall(page, "/gl/branches", undefined, "GET")).json as Array<Named & { is_main: boolean }>;
    const branch = branches.find((b) => !b.is_main) ?? branches[0];
    depot = (await apiOk(page, "/inventory/warehouses", { code: "DEPOT", name: "Musanze Depot", branch_id: branch.id })) as Named;
  }

  const types = (await apiCall(page, "/gl/transaction-types?module=inv", undefined, "GET")).json as Named[];
  const adjin = types.find((t) => t.code === "ADJIN")!;

  // Opening stock, once. Shared key with the step-7 script's opening batch on purpose: if
  // that ran first this replays instead of doubling the shelf.
  await apiOk(
    page,
    "/inventory/journal-batches",
    {
      document_date: TODAY,
      description: "Opening stock for the review record",
      transaction_type_id: adjin.id,
      lines: [
        { item_id: red.id, warehouse_id: main.id, quantity: "144", unit_cost: "4200" },
        { item_id: white.id, warehouse_id: main.id, quantity: "60", unit_cost: "2600" },
        { item_id: coffee.id, warehouse_id: main.id, quantity: "250.5", unit_cost: "3800" },
      ],
    },
    "POST",
    "capture-p5-step7-opening-stock",
  );

  // Received, so the depot holds something the valuation report can put a value on.
  await apiOk(
    page,
    "/inventory/transfers",
    {
      transfer_date: TODAY,
      description: "Depot opening",
      reference: "VAN-11",
      from_warehouse_id: main.id,
      to_warehouse_id: depot.id,
      receive_now: true,
      lines: [
        { item_id: white.id, quantity: "12" },
        { item_id: coffee.id, quantity: "60.25" },
      ],
    },
    "POST",
    "capture-p5-step8-transfer-received",
  );

  // And one left in transit, so the valuation report has the in-transit line that makes a
  // dispatched, unreceived transfer add up (decision 6) rather than going missing.
  await apiOk(
    page,
    "/inventory/transfers",
    {
      transfer_date: TODAY,
      description: "Restock the depot",
      reference: "VAN-12",
      from_warehouse_id: main.id,
      to_warehouse_id: depot.id,
      receive_now: false,
      lines: [{ item_id: red.id, quantity: "24" }],
    },
    "POST",
    "capture-p5-step8-transfer-in-transit",
  );

  return { red, white, coffee, main, depot };
}

/** A **processed** count on the depot, one line short — the only kind the count report can
 * show a document for. Idempotent on its description. */
async function seedProcessedCount(page: Page, seeded: Seeded): Promise<void> {
  const description = "Depot spot check";
  const listed = (await apiCall(page, "/inventory/counts", undefined, "GET")).json as {
    items: Array<{ id: number; description: string; status: string }>;
  };
  if (listed.items.some((s) => s.description === description)) return;

  const session = (await apiOk(page, "/inventory/counts", {
    warehouse_id: seeded.depot.id,
    count_date: TODAY,
    description,
    reference: "SPOT-09",
  })) as { id: number; lines: Array<{ id: number; item_id: number }> };

  // The white wine is two short; the coffee agrees with the books. A count where everything
  // agreed would show a report with nothing on it to look at.
  const short = session.lines.find((l) => l.item_id === seeded.white.id);
  const agreed = session.lines.find((l) => l.item_id === seeded.coffee.id);
  if (short) {
    await apiOk(page, `/inventory/counts/${session.id}/lines/${short.id}`, { counted_quantity: "10" }, "PATCH");
  }
  if (agreed) {
    await apiOk(page, `/inventory/counts/${session.id}/lines/${agreed.id}`, { counted_quantity: "60.25" }, "PATCH");
  }
  await apiOk(
    page,
    `/inventory/counts/${session.id}/process`,
    { session_id: session.id },
    "POST",
    "capture-p5-step8-count-process",
  );
}

async function pick(page: Page, name: string | RegExp, needle: string) {
  await page.getByRole("button", { name, exact: typeof name === "string" }).first().click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(needle);
  await page.locator(`[cmdk-item]:has-text("${needle}")`).first().waitFor({ state: "visible" });
  await page.keyboard.press("Enter");
  await page.locator("[cmdk-input]").waitFor({ state: "hidden" });
}

async function main() {
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined,
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await login(page, OWNER);
  const seeded = await seed(page);
  await seedProcessedCount(page, seeded);

  for (const theme of ["light", "dark"] as const) {
    // 1 — the item enquiry, on the item that is in two warehouses and in transit between
    // them: the three headline figures, both locations, and the moves that made them.
    await page.goto(`${BASE}/inventory/enquiry`);
    await page.waitForSelector("h1:has-text('Item enquiry')");
    await pick(page, "Item", "COFFEE-KG");
    await page.waitForSelector('[data-testid="enquiry-value"]');
    await shoot(page, "1-item-enquiry", theme);

    // 2 — movement, over the default month to date, for everything.
    await page.goto(`${BASE}/inventory/reports/movement`);
    await page.waitForSelector("h1:has-text('Inventory movement')");
    await page.waitForSelector('[data-testid="movement-total-closingValue"]');
    await shoot(page, "2-movement", theme);

    // 3 — counts, with the processed session expanded so its variance line is in the shot.
    await page.goto(`${BASE}/inventory/reports/counts`);
    await page.waitForSelector("h1:has-text('Inventory counts')");
    await page
      .locator("tbody tr", { hasText: "Depot spot check" })
      .first()
      .getByRole("button", { name: /^Show the lines of count / })
      .click();
    await page.waitForSelector('[data-testid="count-line"]');
    await shoot(page, "3-counts", theme);

    // 4 — transactions, every move in the month with the entry behind each one.
    await page.goto(`${BASE}/inventory/reports/transactions`);
    await page.waitForSelector("h1:has-text('Inventory transactions')");
    await page.waitForSelector('[data-testid="transaction-total-value"]');
    await shoot(page, "4-transactions", theme);

    // 5 — valuation: the stock total beside the GL figure it has to equal, and the location
    // rows under it including the in-transit one.
    await page.goto(`${BASE}/inventory/reports/valuation`);
    await page.waitForSelector("h1:has-text('Inventory valuation')");
    await page.waitForSelector('[data-testid="valuation-gl-tie-value"]');
    await shoot(page, "5-valuation", theme);

    // 5b — the same report by warehouse, which is where the per-branch half of the
    // invariant is legible: each warehouse's value, over the whole filtered set.
    await page.getByRole("tab", { name: "By warehouse" }).click();
    await page.waitForSelector('[data-testid="valuation-warehouse-value"]');
    await shoot(page, "5b-valuation-by-warehouse", theme);
  }

  await browser.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
