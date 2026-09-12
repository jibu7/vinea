/**
 * Captures the P5 step-6 maintenance screens for the review record: 1440x900, light and dark.
 * Run against the dev stack, the same prerequisites `npm run e2e` needs:
 *   OUT=../docs/screenshots/p5-step-6 npx tsx scripts/capture-p5-screens.ts
 *
 * Every shot has **rows in it**. Rule 13 is explicit that an empty-state screenshot proves the
 * route compiles and nothing else, so this script seeds what each screen needs before it
 * photographs it — through the real endpoints, as the signed-in owner, never by writing to the
 * database behind the app.
 */
import { chromium, type Page } from "@playwright/test";
import { PASSWORD, PRIMARY_EMAIL as OWNER } from "../e2e/support/fixtures";

const OUT = process.env.OUT ?? "screenshots";
const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const API = `${process.env.API_URL ?? "http://localhost:8000"}/api/v1`;

/** `ONLY=3-barcodes` re-captures just that one. Re-shooting all seven to change one
 * is how a review record ends up with fourteen files changed and one of them meaningful. */
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

/**
 * A catalogue worth photographing: three items across two unit categories, a case unit with a
 * real conversion factor, barcodes on two of them, and a second warehouse in a second branch.
 *
 * Idempotent by code, like `seed_e2e`: the script is run repeatedly while a shot is tuned, and
 * a fixture that duplicated itself each time would put six of everything on the screen.
 */
async function seedCatalogue(page: Page): Promise<void> {
  const categories = (await apiCall(page, "/inventory/uom-categories", undefined, "GET"))
    .json as Array<Named & { uoms: Named[] }>;
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

  const existing = (await apiCall(page, "/inventory/items?include_inactive=true", undefined, "GET"))
    .json as Named[];
  const byCode = new Map(existing.map((item) => [item.code, item]));

  async function item(
    code: string,
    name: string,
    categoryId: number,
    baseUomId: number,
    price: string,
    itemType = "stock",
  ): Promise<Named> {
    const found = byCode.get(code);
    if (found) return found;
    return (await apiOk(page, "/inventory/items", {
      code,
      name,
      uom_category_id: categoryId,
      base_uom_id: baseUomId,
      item_type: itemType,
      selling_price: price,
    })) as Named;
  }

  const red = await item("WINE-750", "Rugari Red 750ml", count.id, each.id, "8500");
  const white = await item("WINE-375", "Rugari White 375ml", count.id, each.id, "5200");
  await item(
    "COFFEE-KG",
    "Kivu Arabica, green",
    weight.id,
    weight.uoms.find((u) => u.code === "KG")!.id,
    "6400",
  );
  await item("DELIVERY", "Delivery within Kigali", count.id, each.id, "3000", "service");

  for (const [target, barcode, uomId, pack] of [
    [red, "5901234123457", each.id, "1"],
    [red, "5901234123464", box.id, "1"],
    [white, "4006381333931", each.id, "1"],
  ] as const) {
    const codes = (await apiCall(page, `/inventory/items/${target.id}/barcodes`, undefined, "GET"))
      .json as Array<{ barcode: string }>;
    if (codes.some((row) => row.barcode === barcode)) continue;
    await apiOk(page, `/inventory/items/${target.id}/barcodes`, {
      barcode,
      uom_id: uomId,
      pack_quantity: pack,
    });
  }

  const warehouses = (
    await apiCall(page, "/inventory/warehouses?include_in_transit=true", undefined, "GET")
  ).json as Named[];
  if (!warehouses.some((w) => w.code === "DEPOT")) {
    const branches = (await apiCall(page, "/gl/branches", undefined, "GET")).json as Array<
      Named & { is_main: boolean }
    >;
    const branch = branches.find((b) => !b.is_main) ?? branches[0];
    await apiOk(page, "/inventory/warehouses", {
      code: "DEPOT",
      name: "Musanze Depot",
      branch_id: branch.id,
    });
  }

  // A company-specific transaction type, so the kind column has something in it that the seed
  // did not put there — which is what the Maintenance screen exists for (decision 9).
  const types = (await apiCall(page, "/gl/transaction-types?module=inv", undefined, "GET"))
    .json as Named[];
  if (!types.some((t) => t.code === "DAMAGED")) {
    const accounts = (await apiCall(page, "/gl/accounts", undefined, "GET")).json as Array<
      Named & { is_postable: boolean; is_control: boolean }
    >;
    const contra = accounts.find((a) => a.code === "6800") ?? accounts.find((a) => a.is_postable && !a.is_control)!;
    await apiOk(page, "/gl/transaction-types", {
      module: "inv",
      code: "DAMAGED",
      name: "Damaged stock write-off",
      kind: "adjustment_out",
      default_gl_account_id: contra.id,
    });
  }
}

async function main() {
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined,
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await login(page, OWNER);
  await seedCatalogue(page);

  for (const theme of ["light", "dark"] as const) {
    // 1 — the catalogue, with four items across two unit categories.
    await page.goto(`${BASE}/maintenance/inventory-items`);
    await page.waitForSelector("h1:has-text('Inventory items')");
    await page.locator("tbody tr").first().waitFor({ state: "visible" });
    await shoot(page, "1-inventory-items", theme);

    // 1b — the drawer on the barcodes tab: two codes, one of them a box of twelve.
    await page.locator('tbody tr:has-text("WINE-750") button').first().click();
    await page.getByRole("tab", { name: "Barcodes" }).click();
    await page.locator('[role="dialog"] tbody tr').first().waitFor({ state: "visible" });
    await page.waitForTimeout(300);
    await shoot(page, "1b-item-barcodes", theme);
    await page.keyboard.press("Escape");

    // 2 — warehouses, including the in-transit location and a second branch.
    await page.goto(`${BASE}/maintenance/warehouses`);
    await page.waitForSelector("h1:has-text('Warehouses')");
    await page.locator("tbody tr").first().waitFor({ state: "visible" });
    await shoot(page, "2-warehouses", theme);

    // 3 — the company-wide barcode listing, resolved to items and packs.
    await page.goto(`${BASE}/maintenance/barcodes`);
    await page.waitForSelector("h1:has-text('Barcodes')");
    await page.locator("tbody tr").first().waitFor({ state: "visible" });
    await shoot(page, "3-barcodes", theme);

    // 4 — the transaction types with their kinds, seeded six plus the company's own.
    await page.goto(`${BASE}/maintenance/inv-transaction-types`);
    await page.waitForSelector("h1:has-text('Inventory transaction types')");
    await page.locator("tbody tr").first().waitFor({ state: "visible" });
    await shoot(page, "4-inv-transaction-types", theme);

    // 5 — unit categories, each with its base unit at factor 1 and the box at 12.
    await page.goto(`${BASE}/maintenance/uom-categories`);
    await page.waitForSelector("h1:has-text('Units of measure')");
    await page.locator("tbody tr").first().waitFor({ state: "visible" });
    await shoot(page, "5-uom-categories", theme);

    // 6 — the defaults, with the seeded control accounts resolved rather than "Not set".
    await page.goto(`${BASE}/maintenance/inventory-defaults`);
    await page.waitForSelector("h1:has-text('Inventory defaults')");
    await page.waitForTimeout(600);
    await shoot(page, "6-inventory-defaults", theme);

    // 7 — rename, with an item selected so the form and its history are both on screen.
    await page.goto(`${BASE}/maintenance/rename-item-code`);
    await page.waitForSelector("h1:has-text('Rename item code')");
    await page.getByRole("button", { name: /Item/ }).first().click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type("WINE-750");
    await page.locator('[cmdk-item]:has-text("WINE-750")').first().click();
    await page.waitForTimeout(500);
    await shoot(page, "7-rename-item-code", theme);
  }

  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
