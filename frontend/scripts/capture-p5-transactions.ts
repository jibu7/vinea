/**
 * Captures the P5 step-7 transaction screens for the review record: 1440x900, light and dark.
 * Run against the dev stack, the same prerequisites `npm run e2e` needs:
 *   OUT=../docs/screenshots/p5-step-7 npx tsx scripts/capture-p5-transactions.ts
 *
 * Every shot has **rows in it** and a figure on them (rule 13). The script seeds through the
 * real endpoints as the signed-in owner — a catalogue, opening stock, a transfer in transit
 * and a count session half filled in — then keys the document screens through the UI so the
 * grid is photographed with its on-hand, conversion and estimate cells populated.
 *
 * Idempotent by code and by idempotency key, so a re-run to tune one shot does not put a
 * second opening balance on the shelf.
 */
import { chromium, type Page } from "@playwright/test";
import { PASSWORD, PRIMARY_EMAIL as OWNER } from "../e2e/support/fixtures";

const OUT = process.env.OUT ?? "screenshots";
const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const API = `${process.env.API_URL ?? "http://localhost:8000"}/api/v1`;

/** `ONLY=5-count-sheet` re-captures just that one. */
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
    // A grid the script scrolled sideways while keying its last cell is shot from the left.
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
  adjin: Named;
}

async function seed(page: Page): Promise<Seeded> {
  const categories = (await apiCall(page, "/inventory/uom-categories", undefined, "GET")).json as Array<
    Named & { uoms: Named[] }
  >;
  const count = categories.find((c) => c.code === "COUNT")!;
  const weight = categories.find((c) => c.code === "WEIGHT")!;
  const each = count.uoms.find((u) => u.code === "EA")!;
  let box = count.uoms.find((u) => u.code === "BOX12");
  if (!box) {
    box = (await apiOk(page, "/inventory/uoms", {
      category_id: count.id,
      code: "BOX12",
      name: "Box of 12",
      factor_to_base: "12",
    })) as Named;
  }

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
  const coffee = await item("COFFEE-KG", "Kivu Arabica, green", weight.id, weight.uoms.find((u) => u.code === "KG")!.id, "6400");
  for (const [target, barcode, uomId] of [
    [red, "5901234123457", each.id],
    [red, "5901234123464", box.id],
    [white, "4006381333931", each.id],
  ] as const) {
    const codes = (await apiCall(page, `/inventory/items/${target.id}/barcodes`, undefined, "GET")).json as Array<{ barcode: string }>;
    if (codes.some((row) => row.barcode === barcode)) continue;
    await apiOk(page, `/inventory/items/${target.id}/barcodes`, { barcode, uom_id: uomId, pack_quantity: "1" });
  }

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

  // Opening stock, once: the key makes a re-run a replay rather than a second shelf-full.
  const today = new Date().toISOString().slice(0, 10);
  await apiOk(
    page,
    "/inventory/journal-batches",
    {
      document_date: today,
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
  // One transfer left in transit, so the list has a row with Receive on it.
  await apiOk(
    page,
    "/inventory/transfers",
    {
      transfer_date: today,
      description: "Restock the depot",
      reference: "VAN-12",
      from_warehouse_id: main.id,
      to_warehouse_id: depot.id,
      receive_now: false,
      lines: [
        { item_id: red.id, quantity: "24" },
        { item_id: coffee.id, quantity: "40" },
      ],
    },
    "POST",
    "capture-p5-step7-transfer-in-transit",
  );
  // And one received, so both entry links show.
  await apiOk(
    page,
    "/inventory/transfers",
    {
      transfer_date: today,
      description: "Depot opening",
      reference: "VAN-11",
      from_warehouse_id: main.id,
      to_warehouse_id: depot.id,
      receive_now: true,
      lines: [{ item_id: white.id, quantity: "12" }],
    },
    "POST",
    "capture-p5-step7-transfer-received",
  );
  return { red, white, coffee, main, depot, adjin };
}

/** A count session on MAIN with two of three lines counted, one of them short. */
async function seedCount(page: Page, seeded: Seeded): Promise<number> {
  const sessions = (await apiCall(page, "/inventory/counts?status=counting", undefined, "GET")).json as {
    items: Array<{ id: number; warehouse_id: number; description: string }>;
  };
  const open = sessions.items.find((s) => s.description === "Month-end count, main store");
  if (open) return open.id;
  const session = (await apiOk(page, "/inventory/counts", {
    warehouse_id: seeded.main.id,
    count_date: new Date().toISOString().slice(0, 10),
    description: "Month-end count, main store",
    reference: "CNT-SEP",
  })) as { id: number; lines: Array<{ id: number; item_id: number }> };
  const line = (itemId: number) => session.lines.find((l) => l.item_id === itemId)!;
  await apiOk(page, `/inventory/counts/${session.id}/lines/${line(seeded.red.id).id}`, { counted_quantity: "118" }, "PATCH");
  await apiOk(page, `/inventory/counts/${session.id}/lines/${line(seeded.coffee.id).id}`, { counted_quantity: "210.25" }, "PATCH");
  return session.id;
}

async function pick(page: Page, name: string | RegExp, needle: string) {
  // Exact for a string: "To" must not resolve to "Toggle theme".
  await page.getByRole("button", { name, exact: typeof name === "string" }).first().click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(needle);
  await page.locator(`[cmdk-item]:has-text("${needle}")`).first().waitFor({ state: "visible" });
  await page.keyboard.press("Enter");
  await page.locator("[cmdk-input]").waitFor({ state: "hidden" });
}

async function clearDrafts(page: Page) {
  await page.evaluate(() => {
    for (const key of Object.keys(window.localStorage)) {
      if (key.startsWith("vinea.draft.")) window.localStorage.removeItem(key);
    }
  });
}

async function main() {
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined,
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await login(page, OWNER);
  const seeded = await seed(page);
  const sessionId = await seedCount(page, seeded);

  for (const theme of ["light", "dark"] as const) {
    // 1 — an adjustment keyed but not posted: its one line, on hand beside the item, a box
    // unit with its conversion, unit cost on the increase, the estimate in the footer.
    await clearDrafts(page);
    await page.goto(`${BASE}/inventory/adjustments/new`);
    await page.waitForSelector("h1:has-text('Inventory adjustment')");
    await pick(page, "Transaction type", "ADJIN");
    await pick(page, "Warehouse", "MAIN");
    await page.getByLabel("Reference").fill("GRN-0142");
    await page.getByLabel("Description", { exact: true }).fill("Breakages replaced by supplier");
    await pick(page, /^Item, row 1$/, "WINE-750");
    await page.getByLabel("Quantity, row 1").fill("2");
    await pick(page, /^Unit of measure, row 1$/, "BOX12");
    await page.getByLabel("Unit cost, row 1").fill("50400");
    await page.getByLabel("Description", { exact: true }).click();
    await page.waitForTimeout(300);
    await shoot(page, "1-adjustment", theme);

    // 2 — a journal batch with each line its own type: an increase priced, a decrease
    // costed at the average (its unit-cost cell absent), a revaluation stating a value.
    await clearDrafts(page);
    await page.goto(`${BASE}/inventory/journal-batches/new`);
    await page.waitForSelector("h1:has-text('Inventory journal batch')");
    await pick(page, "Default type", "ADJIN");
    await pick(page, "Default warehouse", "MAIN");
    await page.getByLabel("Description", { exact: true }).fill("September stock corrections");
    await pick(page, /^Item, row 1$/, "WINE-375");
    await page.getByLabel("Quantity, row 1").fill("6");
    await page.getByLabel("Unit cost, row 1").fill("2600");
    await page.getByRole("button", { name: "+ Add line" }).click();
    await pick(page, /^Item, row 2$/, "WINE-750");
    await pick(page, /^Transaction type, row 2$/, "ADJOUT");
    await page.getByLabel("Quantity, row 2").fill("3");
    await page.getByRole("button", { name: "+ Add line" }).click();
    await pick(page, /^Item, row 3$/, "COFFEE-KG");
    await pick(page, /^Transaction type, row 3$/, "REVAL");
    await page.getByLabel("Value, row 3").fill("-15000");
    await page.getByLabel("Description", { exact: true }).click();
    await page.waitForTimeout(300);
    await shoot(page, "2-journal-batch", theme);

    // 3 — the transfers list, the in-transit one opened to its lines.
    await page.goto(`${BASE}/inventory/transfers`);
    await page.waitForSelector("h1:has-text('Warehouse transfers')");
    await page.locator("tbody tr[data-transfer]").first().waitFor({ state: "visible" });
    const inTransit = page.locator("tbody tr[data-transfer]", { hasText: "In transit" }).first();
    await inTransit.getByRole("button", { name: /^Open / }).click();
    await page.getByTestId("transfer-lines").locator("tbody tr").first().waitFor({ state: "visible" });
    await page.waitForTimeout(300);
    await shoot(page, "3-transfers", theme);

    // 3b — a new transfer keyed: on hand at the source beside each line.
    await clearDrafts(page);
    await page.goto(`${BASE}/inventory/transfers/new`);
    await page.waitForSelector("h1:has-text('New transfer')");
    await pick(page, "From", "MAIN");
    await pick(page, "To", "DEPOT");
    await page.getByLabel("Reference").fill("VAN-13");
    await page.getByLabel("Description", { exact: true }).fill("Weekend restock");
    await pick(page, /^Item, row 1$/, "WINE-750");
    await page.getByLabel("Quantity, row 1").fill("1");
    await pick(page, /^Unit of measure, row 1$/, "BOX12");
    await page.getByRole("button", { name: "+ Add line" }).click();
    await pick(page, /^Item, row 2$/, "COFFEE-KG");
    await page.getByLabel("Quantity, row 2").fill("20");
    await page.getByLabel("Description", { exact: true }).click();
    await page.waitForTimeout(300);
    await shoot(page, "3b-transfer-new", theme);

    // 4 — the count sessions list.
    await page.goto(`${BASE}/inventory/counts`);
    await page.waitForSelector("h1:has-text('Inventory counts')");
    await page.locator("tbody tr[data-count-session]").first().waitFor({ state: "visible" });
    await shoot(page, "4-counts", theme);

    // 5 — the sheet: two counted, one not, and the preview costing the variances.
    await page.goto(`${BASE}/inventory/counts/${sessionId}`);
    await page.waitForSelector("h1:has-text('Count ')");
    await page.locator("tr[data-count-line]").first().waitFor({ state: "visible" });
    await page.locator("tr[data-preview-line]").first().waitFor({ state: "visible" });
    await page.waitForTimeout(300);
    await shoot(page, "5-count-sheet", theme);
  }

  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
