/**
 * Captures the P7 step-6 maintenance screens for the review record: 1440x900, light and dark.
 * Run against the e2e stack — the `ebm-sandbox` service has to be up, because shot 1 is of a
 * device that has actually been initialized:
 *
 *   COMPOSE_FILE=docker-compose.yml:docker-compose.e2e.yml docker compose up -d --wait
 *   OUT=../docs/screenshots/p7-step-6 npx tsx scripts/capture-p7-maintenance.ts
 *
 * Every shot has **rows in it**. Rule 13 is explicit that an empty-state screenshot proves the
 * route compiles and nothing else, so this script drives what each screen needs before it
 * photographs it — through the real endpoints and, for the device, through the real authority
 * call, never by writing to the database behind the app.
 *
 * The device is left **active** by this script and that is deliberate for the photograph: a
 * suspended device shows a grey chip and no identifiers, which is a picture of the setup not
 * being finished. The e2e suite is the one that has to put the fixture back, and does.
 */
import { chromium, type Page } from "@playwright/test";
import { PASSWORD, PRIMARY_EMAIL as OWNER } from "../e2e/support/fixtures";

const OUT = process.env.OUT ?? "screenshots";
const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const API = `${process.env.API_URL ?? "http://localhost:8000"}/api/v1`;
/** Dialled by the **backend** container, not by this script: the service name on the compose
 * network, not localhost. */
const EBM_URL = process.env.EBM_URL ?? "http://ebm-sandbox:8100";
/** The TIN the sandbox recognises, and the one a device is registered against. */
const COMPANY_TIN = "999000099";

const ITEM_CODE = "FISCAL-DEMO";
const UNIT_CODE = "CS6";

/** `ONLY=3-tax-types` re-captures just that one, rather than rewriting twelve files to change
 * one of them. */
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
 * A device, initialized against the sandbox, with its code tables synced.
 *
 * Idempotent, like `seed_e2e`: one device per branch means a second run would be refused, so
 * an existing row is re-initialized rather than duplicated — which is also what an operator
 * does when a device needs new keys.
 */
async function seedDevice(page: Page): Promise<void> {
  await apiOk(page, "/company", { tin: COMPANY_TIN }, "PATCH");

  const existing = (await apiCall(page, "/fiscal/devices", undefined, "GET")).json as Array<{
    id: number;
  }>;
  let deviceId = existing[0]?.id;
  if (deviceId === undefined) {
    const branches = (await apiCall(page, "/gl/branches", undefined, "GET")).json as Named[];
    const main = branches.find((b) => b.code === "MAIN")!;
    const device = (await apiOk(page, "/fiscal/devices", {
      branch_id: main.id,
      profile: "vsdc",
      environment: "test",
      base_url: EBM_URL,
      dvc_srl_no: "VINEA-DEMO-0001",
      bhf_id: "00",
    })) as { id: number };
    deviceId = device.id;
  }

  await apiOk(page, `/fiscal/devices/${deviceId}/initialize`);
  await apiOk(page, `/fiscal/devices/${deviceId}/sync-codes`);
  await apiOk(page, `/fiscal/devices/${deviceId}/sync-item-classes`);
}

/** An item carrying all four registered fields, and a unit carrying the authority's quantity
 * code — so the Fiscal section and the new column are photographed with something in them. */
async function seedCatalogue(page: Page): Promise<void> {
  const categories = (await apiCall(page, "/inventory/uom-categories", undefined, "GET"))
    .json as Array<Named & { uoms: Named[] }>;
  const count = categories.find((c) => c.code === "COUNT")!;
  const each = count.uoms.find((u) => u.code === "EA")!;

  if (!count.uoms.some((u) => u.code === UNIT_CODE)) {
    await apiOk(page, "/inventory/uoms", {
      category_id: count.id,
      code: UNIT_CODE,
      name: "Case of six",
      factor_to_base: "6",
      fiscal_quantity_unit: "BX",
    });
  }
  // The base unit needs one too, or the Fiscal section photographs an item that could not be
  // sold: `fiscal_uom_unmapped` is refused on the unit the line is priced in.
  await apiOk(page, `/inventory/uoms/${each.id}`, { fiscal_quantity_unit: "U" }, "PATCH");

  const classes = (await apiCall(page, "/fiscal/item-classes?limit=1", undefined, "GET"))
    .json as Array<{ item_cls_cd: string }>;
  const classCode = classes[0]?.item_cls_cd;
  if (!classCode) throw new Error("the classification is empty — did sync-item-classes run?");

  const items = (await apiCall(page, "/inventory/items?include_inactive=true", undefined, "GET"))
    .json as Named[];
  const existing = items.find((item) => item.code === ITEM_CODE);
  const fiscal = {
    fiscal_class_code: classCode,
    fiscal_origin_country: "RW",
    fiscal_package_unit: "BX",
    fiscal_item_type: "2",
  };
  if (existing) {
    await apiOk(page, `/inventory/items/${existing.id}`, fiscal, "PATCH");
  } else {
    await apiOk(page, "/inventory/items", {
      code: ITEM_CODE,
      name: "Rugari Red 750ml — fiscalized",
      uom_category_id: count.id,
      base_uom_id: each.id,
      item_type: "stock",
      selling_price: "2400",
      ...fiscal,
    });
  }
}

/** A customer whose TIN the sandbox knows, so Verify TIN has a real answer to show. */
async function seedCustomer(page: Page): Promise<void> {
  const partners = (await apiCall(page, "/subledger/ar/partners", undefined, "GET")).json as Array<{
    customer_code: string | null;
  }>;
  if (partners.some((p) => p.customer_code === "FISCUST")) return;
  await apiOk(page, "/subledger/ar/partners", {
    name: "Customer C Ltd",
    customer_code: "FISCUST",
    tin: "100000001",
  });
}

async function main() {
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined,
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await login(page, OWNER);
  await seedDevice(page);
  await seedCatalogue(page);
  await seedCustomer(page);

  for (const theme of ["light", "dark"] as const) {
    // 1 — the device, active, with the identifiers the authority sent back and the keys shown
    // as *held* rather than shown.
    await page.goto(`${BASE}/maintenance/ebm-devices`);
    await page.waitForSelector("h1:has-text('EBM devices')");
    await page.locator("tbody tr").first().waitFor({ state: "visible" });
    await shoot(page, "1-ebm-devices", theme);

    // 2 — the register dialog, which is where the profile and the environment are chosen.
    await page.getByRole("button", { name: "Register device" }).click();
    await page.waitForTimeout(300);
    await shoot(page, "2-ebm-device-register", theme);
    await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();

    // 3 — Tax types, with the EBM class column beside the rate: A, B, C on the seeded codes.
    await page.goto(`${BASE}/maintenance/taxes`);
    await page.waitForSelector("h1:has-text('Tax types')");
    await page.locator("tbody tr").first().waitFor({ state: "visible" });
    await shoot(page, "3-tax-types-ebm-class", theme);

    // 4 — Units of measure, with the authority's quantity code on the case of six.
    await page.goto(`${BASE}/maintenance/uom-categories`);
    await page.waitForSelector("h1:has-text('Units of measure')");
    await page.locator("tbody tr").first().waitFor({ state: "visible" });
    await shoot(page, "4-uom-quantity-unit", theme);

    // 5 — the item dialog's Fiscal section: the four fields and what RRA holds back.
    await page.goto(`${BASE}/maintenance/inventory-items`);
    await page.waitForSelector("h1:has-text('Inventory items')");
    await page.getByLabel("Search").fill(ITEM_CODE);
    await page.locator(`tbody tr:has-text("${ITEM_CODE}")`).first().waitFor({ state: "visible" });
    await page
      .locator(`tbody tr:has-text("${ITEM_CODE}")`)
      .first()
      .getByRole("button", { name: /^Edit/ })
      .click();
    await page.waitForTimeout(400);
    // The section is below the fold of the dialog; scroll it into view before the shot.
    await page.getByRole("dialog").getByText("Fiscal", { exact: true }).scrollIntoViewIfNeeded();
    await page.waitForTimeout(200);
    await shoot(page, "5-item-fiscal-section", theme);
    await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();

    // 6 — Verify TIN on a customer, showing the authority's own name for the taxpayer.
    await page.goto(`${BASE}/maintenance/customers`);
    await page.waitForSelector("h1:has-text('Customers')");
    await page.getByLabel("Search customers").fill("FISCUST");
    await page
      .locator('tbody tr:has-text("FISCUST")')
      .first()
      .getByRole("button", { name: /^Edit/ })
      .click();
    await page.getByRole("dialog").getByRole("button", { name: "Verify TIN" }).click();
    await page.getByText("Customer C Ltd").last().waitFor({ state: "visible" });
    await page.waitForTimeout(300);
    await shoot(page, "6-verify-tin", theme);
    await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();

    // 7 — GL defaults, with the P7 block resolved to `code · name` rather than "Not set".
    await page.goto(`${BASE}/maintenance/defaults`);
    await page.waitForSelector("h1:has-text('Defaults')");
    await page.getByText("2250 · VAT Payable (RRA)").waitFor({ state: "visible" });
    await page.waitForTimeout(300);
    await shoot(page, "7-gl-defaults-tax-block", theme);
  }

  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
