/**
 * Captures the P7 step-7 transaction screens for the review record: 1440x900, light and dark.
 *
 * Run against the e2e stack on a **reset database** — the `ebm-sandbox` service has to be up,
 * because every receipt in these shots was signed by it over HTTP:
 *
 *   make db-reset
 *   COMPOSE_FILE=docker-compose.yml:docker-compose.e2e.yml docker compose up -d --wait
 *   OUT=../docs/screenshots/p7-step-7 npx tsx scripts/capture-p7-transactions.ts
 *
 * Every shot has **rows in it**. Rule 13 is explicit that an empty-state screenshot proves the
 * route compiles and nothing else, so this script drives what each screen needs before it
 * photographs it — through the real endpoints, through the real authority, never by writing to
 * the database behind the app.
 *
 * The **receipt** shot is taken under `emulateMedia({ media: "print" })`, because that is the
 * only state the CIS layout exists in: it is `hidden print:block`, and the screen it sits on
 * hides its own panels when it is there. A screenshot of the document detail would photograph
 * everything except the thing this phase is about.
 *
 * The device is left **active**, as step 6's script leaves its own: a suspended device is a
 * picture of the setup not being finished. The e2e suite is what puts the fixture back.
 */
import { chromium, type Page } from "@playwright/test";
import { PASSWORD, PRIMARY_EMAIL as OWNER } from "../e2e/support/fixtures";

const OUT = process.env.OUT ?? "screenshots";
const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const API = `${process.env.API_URL ?? "http://localhost:8000"}/api/v1`;
/** Dialled by the **backend** container, not by this script. */
const EBM_URL = process.env.EBM_URL ?? "http://ebm-sandbox:8100";
/** Dialled by **this** script, to reset what the authority remembers between runs. */
const SANDBOX_ADMIN = process.env.EBM_ADMIN_URL ?? "http://localhost:8100";

const COMPANY_TIN = "999000099";
const CUSTOMER_TIN = "100000001";
const SUPPLIER_TIN = "100000002";
const PURCHASE_CODE = "AB12CD";

const ITEM_CODE = "FISCAL-TX";
const CUSTOMER_CODE = "FISCUSTX";
const SUPPLIER_CODE = "FISSUPPX";
const FX_CUSTOMER_CODE = "FISCUSFX";

const BOOKING_RATE = "1320";
const RATE_AT_MONTH_END = "1350";

/** `ONLY=5-fiscal-queue` re-captures just that one. */
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
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/${name}-${theme}.png` });
  console.log("captured", `${name}-${theme}`);
}

/**
 * One screen: drive it, then photograph it — or skip both when `ONLY` excludes it.
 *
 * The skip has to wrap the **navigation**, not just the screenshot. A filter that only skipped
 * the `screenshot()` call still walked every screen, so `ONLY=1-invoice` could fail on shot
 * seven's fixture — which is the opposite of what a subset re-capture is for.
 */
async function shot(name: string, drive: () => Promise<void>): Promise<void> {
  if (!wanted(name)) return;
  await drive();
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

const get = (page: Page, path: string) => apiCall(page, path, undefined, "GET").then((r) => r.json);

interface Named {
  id: number;
  code: string;
}

/** `yyyy-mm-dd` in **local** time, the way `lib/format`'s `todayIso()` does it. `toISOString()`
 * renders UTC, which east of Greenwich is the previous day for the first hours of every one —
 * so a seeded document would be dated into the month before at a boundary. */
function iso(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

const today = () => iso(new Date());
const monthStart = () => iso(new Date(new Date().getFullYear(), new Date().getMonth(), 1));
const monthEnd = () => iso(new Date(new Date().getFullYear(), new Date().getMonth() + 1, 0));

/**
 * Everything these nine screens need, driven through the product.
 *
 * Idempotent in the places it can be — a device is re-initialized rather than duplicated — and
 * deliberately *not* in the places it cannot: a second run against the same database posts a
 * second invoice, which is a fuller screenshot rather than a wrong one.
 */
async function seed(page: Page): Promise<{ invoiceId: number; deviceId: number }> {
  await apiOk(page, "/company", { tin: COMPANY_TIN }, "PATCH");

  // The authority forgets, so a repeated run does not meet `994` on an invoice number Vinea's
  // own sequence has just restarted. From **Node**, not from the page: the sandbox sends no
  // CORS headers, and it is this script's business rather than the app's.
  const reset = await fetch(`${SANDBOX_ADMIN}/_sandbox/reset`, { method: "POST" });
  if (!reset.ok) throw new Error(`sandbox reset -> ${reset.status}`);

  const branches = (await get(page, "/gl/branches")) as Named[];
  const main = branches.find((b) => b.code === "MAIN")!;
  const devices = (await get(page, "/fiscal/devices")) as Array<{ id: number }>;
  const deviceId =
    devices[0]?.id ??
    (
      (await apiOk(page, "/fiscal/devices", {
        branch_id: main.id,
        profile: "vsdc",
        environment: "test",
        base_url: EBM_URL,
        dvc_srl_no: "VINEA-DEMO-TX01",
        bhf_id: "00",
      })) as { id: number }
    ).id;
  await apiOk(page, `/fiscal/devices/${deviceId}/initialize`);
  await apiOk(page, `/fiscal/devices/${deviceId}/sync-codes`);
  await apiOk(page, `/fiscal/devices/${deviceId}/sync-item-classes`);

  // --- the catalogue ----------------------------------------------------------------------
  const categories = (await get(page, "/inventory/uom-categories")) as Array<
    Named & { uoms: Named[] }
  >;
  const count = categories.find((c) => c.code === "COUNT")!;
  const each = count.uoms.find((u) => u.code === "EA")!;
  await apiOk(page, `/inventory/uoms/${each.id}`, { fiscal_quantity_unit: "U" }, "PATCH");

  const accounts = (await get(page, "/gl/accounts")) as Named[];
  const byCode = new Map(accounts.map((a) => [a.code, a.id]));
  const taxCodes = (await get(page, "/gl/tax-codes")) as Named[];
  const outputVat = taxCodes.find((t) => t.code === "VAT-OUT-18")!;

  const items = (await get(page, `/inventory/items?search=${ITEM_CODE}`)) as Named[];
  const itemId =
    items.find((i) => i.code === ITEM_CODE)?.id ??
    (
      (await apiOk(page, "/inventory/items", {
        code: ITEM_CODE,
        name: "Fiscal demo wine",
        uom_category_id: count.id,
        base_uom_id: each.id,
        item_type: "stock",
        selling_price: "2000",
        sales_account_id: byCode.get("4100"),
        cogs_account_id: byCode.get("5100"),
        default_sales_tax_code_id: outputVat.id,
        fiscal_class_code: "5059020800",
      })) as { id: number }
    ).id;

  // --- the partners -----------------------------------------------------------------------
  for (const [role, code, tin, name] of [
    ["ar", CUSTOMER_CODE, CUSTOMER_TIN, "Umucyo Traders"],
    ["ap", SUPPLIER_CODE, SUPPLIER_TIN, "Kivu Supplies"],
    ["ar", FX_CUSTOMER_CODE, CUSTOMER_TIN, "Cape Exports"],
  ] as const) {
    const existing = (await get(page, `/subledger/${role}/partners`)) as Array<{
      customer_code?: string | null;
      supplier_code?: string | null;
    }>;
    const field = role === "ar" ? "customer_code" : "supplier_code";
    if (existing.some((p) => p[field] === code)) continue;
    await apiOk(page, `/subledger/${role}/partners`, { name, [field]: code, tin });
  }
  const customers = (await get(page, "/subledger/ar/partners")) as Array<{
    id: number;
    customer_code: string | null;
  }>;
  const customer = customers.find((p) => p.customer_code === CUSTOMER_CODE)!;
  const fxCustomer = customers.find((p) => p.customer_code === FX_CUSTOMER_CODE)!;
  const suppliers = (await get(page, "/subledger/ap/partners")) as Array<{
    id: number;
    supplier_code: string | null;
  }>;
  const supplier = suppliers.find((p) => p.supplier_code === SUPPLIER_CODE)!;

  // --- stock, so a fiscalized sale is not refused by the `block` policy -------------------
  const warehouses = (await get(page, "/inventory/warehouses")) as Named[];
  await apiOk(page, "/oe/goods-received-notes", {
    partner_id: supplier.id,
    grn_date: today(),
    description: "Fiscal demo opening stock",
    warehouse_id: warehouses[0].id,
    lines: [{ item_id: itemId, quantity: "100", unit_cost: "1000" }],
  });

  // --- the two rates the revaluation needs ------------------------------------------------
  const currencies = (await get(page, "/gl/currencies")) as Array<Named & { is_base: boolean }>;
  const usd = currencies.find((c) => !c.is_base)!;
  const rates = (await get(page, `/gl/exchange-rates?currency_id=${usd.id}`)) as Array<{
    valid_from: string;
  }>;
  for (const [validFrom, rate] of [
    [monthStart(), BOOKING_RATE],
    [monthEnd(), RATE_AT_MONTH_END],
  ] as const) {
    if (rates.some((r) => r.valid_from === validFrom)) continue;
    await apiOk(page, "/gl/exchange-rates", {
      currency_id: usd.id,
      valid_from: validFrom,
      rate,
    });
  }

  // --- the documents ----------------------------------------------------------------------
  const invoice = (await apiOk(page, "/subledger/ar/documents", {
    kind: "invoice",
    partner_id: customer.id,
    document_date: today(),
    description: "Fiscal demo invoice",
    purchase_code: PURCHASE_CODE,
    payment_method: "credit",
    lines: [{ item_id: itemId, quantity: "25", unit_price: "2000", tax_code_id: outputVat.id }],
  })) as { id: number };

  await apiOk(page, "/subledger/ar/documents", {
    kind: "invoice",
    partner_id: fxCustomer.id,
    document_date: today(),
    description: "Fiscal demo USD invoice",
    purchase_code: PURCHASE_CODE,
    currency_id: usd.id,
    lines: [{ item_id: itemId, quantity: "10", unit_price: "2", tax_code_id: outputVat.id }],
  });

  await apiOk(page, "/subledger/ar/documents", {
    kind: "credit_note",
    partner_id: customer.id,
    document_date: today(),
    description: "Fiscal demo credit note",
    purchase_code: PURCHASE_CODE,
    refund_of_document_id: invoice.id,
    refund_reason: "06",
    lines: [{ item_id: itemId, quantity: "2", unit_price: "2000", tax_code_id: outputVat.id }],
  });

  // Signed, so the document detail photographs a receipt rather than a queue row.
  await apiOk(page, "/fiscal/outbox/drain");

  // --- what the authority is holding ------------------------------------------------------
  await apiOk(page, `/fiscal/devices/${deviceId}/fetch-purchase-feed`);
  await apiOk(page, `/fiscal/devices/${deviceId}/fetch-imports`);

  return { invoiceId: invoice.id, deviceId };
}

async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  await login(page, OWNER);
  const { invoiceId } = await seed(page);

  for (const theme of ["light", "dark"] as const) {
    // 1 — the Invoice capture screen with its Fiscalization section, on a customer with a TIN.
    await shot("1-invoice-fiscal-section", async () => {
      await page.goto(`${BASE}/ar/invoices/new`);
      await page.waitForSelector("h1:has-text('Invoice')");
      await page.getByRole("button", { name: "Customer", exact: true }).click();
      await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
      await page.keyboard.type(CUSTOMER_CODE);
      await page.locator(`[cmdk-item]:has-text("${CUSTOMER_CODE}")`).first().click();
      await page.getByTestId("purchase-code").waitFor({ state: "visible" });
      await page.getByTestId("purchase-code").fill(PURCHASE_CODE);
      await page.waitForTimeout(300);
      await shoot(page, "1-invoice-fiscal-section", theme);
    });

    // 2 — the Credit note's Refund of picker, offering the partner's fiscalized invoices.
    await shot("2-credit-note-refund-of", async () => {
      await page.goto(`${BASE}/ar/credit-notes/new`);
      await page.waitForSelector("h1:has-text('Credit note')");
      await page.getByRole("button", { name: "Customer", exact: true }).click();
      await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
      await page.keyboard.type(CUSTOMER_CODE);
      await page.locator(`[cmdk-item]:has-text("${CUSTOMER_CODE}")`).first().click();
      const fiscal = page.getByTestId("document-fiscal");
      await fiscal.getByRole("button", { name: "Refund of", exact: true }).click();
      await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
      await page.waitForTimeout(300);
      await shoot(page, "2-credit-note-refund-of", theme);
      await page.keyboard.press("Escape");
    });

    // 3 — the document detail: the fiscal panel, the receipt, Copy print, Print enabled.
    await shot("3-document-fiscal-panel", async () => {
      await page.goto(`${BASE}/ar/documents/${invoiceId}`);
      await page.getByTestId("fiscal-receipt-number").waitFor({ state: "visible" });
      await page.waitForTimeout(300);
      await shoot(page, "3-document-fiscal-panel", theme);
    });

    // 4 — the CIS receipt itself. Print media, because that is the only state it exists in.
    await shot("4-cis-receipt-print", async () => {
      await page.emulateMedia({ media: "print" });
      await page.getByTestId("cis-receipt").waitFor({ state: "visible" });
      await page.waitForTimeout(400);
      await shoot(page, "4-cis-receipt-print", theme);
      await page.emulateMedia({ media: "screen" });
    });

    // 5 — the fiscal queue: the device card and the rows behind it.
    await shot("5-fiscal-queue", async () => {
      await page.goto(`${BASE}/fiscal/queue`);
      await page.waitForSelector("h1:has-text('Fiscal queue')");
      await page.locator("tbody tr").first().waitFor({ state: "visible" });
      await page.waitForTimeout(300);
      await shoot(page, "5-fiscal-queue", theme);
    });

    // 6 — a row's request and response, redacted, with its action log.
    await shot("6-queue-row-payload", async () => {
      await page.locator("tbody tr").first().getByRole("button", { name: "Inspect" }).click();
      await page.getByTestId("row-request").waitFor({ state: "visible" });
      await page.waitForTimeout(300);
      await shoot(page, "6-queue-row-payload", theme);
      await page.getByTestId("close-row").click();
    });

    // 7 — the purchase feed, with one undecided purchase.
    await shot("7-ebm-purchases", async () => {
      await page.goto(`${BASE}/fiscal/purchases`);
      await page.waitForSelector("h1:has-text('EBM purchases')");
      await page.locator("tbody tr").first().waitFor({ state: "visible" });
      await page.waitForTimeout(300);
      await shoot(page, "7-ebm-purchases", theme);
    });

    // 8 — the import declarations, waiting to be matched to a Vinea item.
    await shot("8-import-declarations", async () => {
      await page.goto(`${BASE}/fiscal/imports`);
      await page.waitForSelector("h1:has-text('Import declarations')");
      await page.locator("tbody tr").first().waitFor({ state: "visible" });
      await page.waitForTimeout(300);
      await shoot(page, "8-import-declarations", theme);
    });

    // 9 — the VAT return, with the tie under it.
    await shot("9-vat-return", async () => {
      await page.goto(`${BASE}/tax/vat-return`);
      await page.waitForSelector("h1:has-text('VAT return')");
      await page.getByTestId("vat-net").waitFor({ state: "visible" });
      await page.waitForTimeout(300);
      await shoot(page, "9-vat-return", theme);
    });

    // 10 — the revaluation preview, per open document.
    await shot("10-fx-revaluation", async () => {
      await page.goto(`${BASE}/gl/fx-revaluation`);
      await page.waitForSelector("h1:has-text('FX revaluation')");
      await page.getByTestId("revaluation-total").waitFor({ state: "visible" });
      await page.locator("tbody tr").first().waitFor({ state: "visible" });
      await page.waitForTimeout(300);
      await shoot(page, "10-fx-revaluation", theme);
    });
  }

  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
