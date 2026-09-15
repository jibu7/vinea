/**
 * Captures the P6 step-6 maintenance screens for the review record: 1440x900, light and dark.
 * Run against the dev stack, the same prerequisites `npm run e2e` needs:
 *   OUT=../docs/screenshots/p6-step-6 npx tsx scripts/capture-p6-maintenance.ts
 *
 * Every shot has **rows in it**. Rule 13 is explicit that an empty-state screenshot proves the
 * route compiles and nothing else, so this script seeds what each screen needs before it
 * photographs it — through the real endpoints, as the signed-in owner, never by writing to the
 * database behind the app.
 *
 * The fixture is the acceptance tape's own catalogue, in miniature: a bottle, a gift box and
 * the kit that is two bottles in one box. A kit whose components are recognisably *parts of
 * it* is the point of the screenshot — a reviewer can see the definition is right, which a
 * kit of two arbitrary codes would not show.
 */
import { chromium, type Page } from "@playwright/test";
import { PASSWORD, PRIMARY_EMAIL as OWNER } from "../e2e/support/fixtures";

const OUT = process.env.OUT ?? "screenshots";
const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const API = `${process.env.API_URL ?? "http://localhost:8000"}/api/v1`;

/** `ONLY=2-kit-components` re-captures just that one, rather than rewriting eight files to
 * change one of them. */
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
 * The catalogue the kit needs: two stock items and the kit itself, with its definition.
 *
 * Idempotent by code, like `seed_e2e`: the script is run repeatedly while a shot is tuned,
 * and a fixture that duplicated itself each time would put six of everything on the screen.
 * The kit's definition is a whole-list PUT, so re-running it is the same fact again rather
 * than a second copy.
 */
async function seedKit(page: Page): Promise<void> {
  const categories = (await apiCall(page, "/inventory/uom-categories", undefined, "GET"))
    .json as Array<Named & { uoms: Named[] }>;
  const count = categories.find((c) => c.code === "COUNT")!;
  const weight = categories.find((c) => c.code === "WEIGHT")!;
  const each = count.uoms.find((u) => u.code === "EA")!;
  const kilo = weight.uoms.find((u) => u.code === "KG")!;

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
    extra: Record<string, unknown> = {},
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
      ...extra,
    })) as Named;
  }

  const bottle = await item("WINE-750", "Rugari Red 750ml", count.id, each.id, "2000", "stock", {
    // The weight a landed cost on the `weight` basis reads; a target item without one is
    // refused (`weight_missing`), so the catalogue is where it has to be settable.
    weight_per_base_unit: "1.2",
  });
  const box = await item("GIFTBOX", "Two-bottle gift box", count.id, each.id, "800");
  await item("COFFEE-KG", "Kivu Arabica, green", weight.id, kilo.id, "6400");
  const kit = await item("KIT-GIFT2", "Gift pack — two reds", count.id, each.id, "3500", "kit");

  await apiOk(
    page,
    `/inventory/items/${kit.id}/kit-components`,
    {
      components: [
        { component_item_id: bottle.id, quantity_per_kit: "2" },
        { component_item_id: box.id, quantity_per_kit: "1" },
      ],
    },
    "PUT",
  );
}

async function main() {
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined,
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await login(page, OWNER);
  await seedKit(page);

  for (const theme of ["light", "dark"] as const) {
    // 1 — the defaults, with the seeded accounts resolved rather than "Not set", and the
    // inventory default warehouse shown read-only beside the policy this screen does own.
    await page.goto(`${BASE}/maintenance/order-defaults`);
    await page.waitForSelector("h1:has-text('Order defaults')");
    await page.waitForTimeout(600);
    await shoot(page, "1-order-defaults", theme);

    // 2 — the catalogue with a kit in it: the type column reads Kit, and the price is RWF's
    // zero decimals rather than the column's six.
    await page.goto(`${BASE}/maintenance/inventory-items`);
    await page.waitForSelector("h1:has-text('Inventory items')");
    await page.locator("tbody tr").first().waitFor({ state: "visible" });
    await shoot(page, "2-inventory-items-kit", theme);

    // 3 — the definition as it reads: two bottles and a box, each in its own base unit.
    await page.locator('tbody tr:has-text("KIT-GIFT2") button').first().click();
    await page.getByRole("tab", { name: "Kit components" }).click();
    await page.locator('[role="dialog"] tbody tr').first().waitFor({ state: "visible" });
    await page.waitForTimeout(300);
    await shoot(page, "3-kit-components", theme);

    // 3b — and the editor over it, which is where the whole-list save is made.
    await page.getByRole("button", { name: /Edit definition/ }).click();
    await page.waitForTimeout(300);
    await shoot(page, "3b-kit-components-editor", theme);
    await page.getByRole("button", { name: "Cancel", exact: true }).click();

    // 4 — the item dialog, showing the two fields P6 added to it: the purchase account a
    // service line lands on, and the weight a landed-cost split reads.
    await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
    await page.locator('tbody tr:has-text("WINE-750")').first().getByRole("button", { name: /^Edit/ }).click();
    await page.waitForTimeout(300);
    await shoot(page, "4-item-purchase-and-weight", theme);
    await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
  }

  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
