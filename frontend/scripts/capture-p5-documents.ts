/**
 * Captures the P5 step-9 documents screens for the review record: 1440x900, light and dark.
 *   OUT=../docs/screenshots/p5-step-9 npx tsx scripts/capture-p5-documents.ts
 *
 * Rule 13: rows with figures in them. The seed posts through the real endpoints — an opening
 * batch, an adjustment, a **valueless** receipt (the case that has no journal entry to be
 * found through, and therefore no home but this screen) and a reversal, so the listing shows
 * every status and both kinds of entry cell.
 */
import { chromium, type Page } from "@playwright/test";
import { PASSWORD, PRIMARY_EMAIL as OWNER } from "../e2e/support/fixtures";

const OUT = process.env.OUT ?? "screenshots";
const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const API = `${process.env.API_URL ?? "http://localhost:8000"}/api/v1`;

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
  await page.evaluate((t) => {
    document.documentElement.setAttribute("data-theme", t);
    for (const el of document.querySelectorAll<HTMLElement>("*")) if (el.scrollLeft) el.scrollLeft = 0;
  }, theme);
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/${name}-${theme}.png` });
  console.log("captured", `${name}-${theme}`);
}

async function api(page: Page, path: string, body?: unknown, method = "POST", key?: string) {
  const res = await page.evaluate(
    async ({ url, body, method, key }) => {
      const r = await fetch(url, {
        method,
        credentials: "include",
        headers: { "Content-Type": "application/json", "Idempotency-Key": key ?? crypto.randomUUID() },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      return { status: r.status, json: await r.json().catch(() => null) };
    },
    { url: `${API}${path}`, body, method, key },
  );
  if (res.status >= 300) throw new Error(`${method} ${path} -> ${res.status}: ${JSON.stringify(res.json)}`);
  return res.json as never;
}

const TODAY = (() => {
  const n = new Date();
  return [n.getFullYear(), String(n.getMonth() + 1).padStart(2, "0"), String(n.getDate()).padStart(2, "0")].join("-");
})();

async function seed(page: Page) {
  const categories = (await api(page, "/inventory/uom-categories", undefined, "GET")) as Array<{
    id: number;
    code: string;
    uoms: Array<{ id: number; code: string }>;
  }>;
  const count = categories.find((c) => c.code === "COUNT")!;
  const each = count.uoms.find((u) => u.code === "EA")!;

  const existing = (await api(page, "/inventory/items?include_inactive=true", undefined, "GET")) as Array<{
    id: number;
    code: string;
  }>;
  const byCode = new Map(existing.map((i) => [i.code, i]));
  async function item(code: string, name: string, price: string) {
    const found = byCode.get(code);
    if (found) return found;
    return (await api(page, "/inventory/items", {
      code,
      name,
      uom_category_id: count.id,
      base_uom_id: each.id,
      item_type: "stock",
      selling_price: price,
    })) as { id: number; code: string };
  }
  const red = await item("WINE-750", "Rugari Red 750ml", "8500");
  const sample = await item("SAMPLE-01", "Tasting sample", "0");

  const warehouses = (await api(page, "/inventory/warehouses", undefined, "GET")) as Array<{
    id: number;
    code: string;
  }>;
  const main = warehouses.find((w) => w.code === "MAIN")!;
  const types = (await api(page, "/gl/transaction-types?module=inv", undefined, "GET")) as Array<{
    id: number;
    code: string;
  }>;
  const adjin = types.find((t) => t.code === "ADJIN")!;
  const open = types.find((t) => t.code === "OPEN")!;

  await api(
    page,
    "/inventory/journal-batches",
    {
      document_date: TODAY,
      description: "Opening stock for the review record",
      transaction_type_id: open.id,
      lines: [{ item_id: red.id, warehouse_id: main.id, quantity: "144", unit_cost: "4200" }],
    },
    "POST",
    "capture-p5-step9-opening",
  );
  // Priced, so the listing has a real figure in its Value column.
  const priced = (await api(
    page,
    "/inventory/adjustments",
    {
      document_date: TODAY,
      description: "Breakages replaced by supplier",
      reference: "GRN-0142",
      transaction_type_id: adjin.id,
      lines: [{ item_id: red.id, warehouse_id: main.id, quantity: "24", unit_cost: "4200" }],
    },
    "POST",
    "capture-p5-step9-priced",
  )) as { id: number };
  // Valueless: moves, and no journal entry to be found through.
  await api(
    page,
    "/inventory/adjustments",
    {
      document_date: TODAY,
      description: "Tasting samples received at no cost",
      transaction_type_id: adjin.id,
      lines: [{ item_id: sample.id, warehouse_id: main.id, quantity: "36", unit_cost: "0" }],
    },
    "POST",
    "capture-p5-step9-valueless",
  );
  // And one reversed, so the listing shows both statuses.
  const toReverse = (await api(
    page,
    "/inventory/adjustments",
    {
      document_date: TODAY,
      description: "Keyed against the wrong warehouse",
      transaction_type_id: adjin.id,
      lines: [{ item_id: red.id, warehouse_id: main.id, quantity: "6", unit_cost: "4200" }],
    },
    "POST",
    "capture-p5-step9-mistake",
  )) as { id: number };
  await api(
    page,
    `/inventory/documents/${toReverse.id}/reverse`,
    { reversal_date: TODAY, reason: "keyed against the wrong warehouse" },
    "POST",
    "capture-p5-step9-reversal",
  );
  return priced.id;
}

async function main() {
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined,
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await login(page);
  const documentId = await seed(page);

  for (const theme of ["light", "dark"] as const) {
    await page.goto(`${BASE}/inventory/documents`);
    await page.waitForSelector("h1:has-text('Inventory documents')");
    await page.waitForSelector('[data-testid="document-value"]');
    await shoot(page, "1-documents", theme);

    await page.goto(`${BASE}/inventory/documents/${documentId}`);
    await page.waitForSelector('[data-testid="document-total"]');
    await shoot(page, "2-document", theme);
  }
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
