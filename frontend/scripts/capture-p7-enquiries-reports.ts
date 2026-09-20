/**
 * Captures the P7 step-8 Tax enquiries and reports for the review record: 1440x900, light and
 * dark.
 *
 * Run against the e2e stack on a **reset database** — the `ebm-sandbox` service has to be up,
 * because every receipt in these shots was signed by it over HTTP:
 *
 *   make db-reset
 *   COMPOSE_FILE=docker-compose.yml:docker-compose.e2e.yml docker compose up -d --wait
 *   OUT=../docs/screenshots/p7-step-8 npx tsx scripts/capture-p7-enquiries-reports.ts
 *
 * Every shot has **rows in it**. Rule 13 is explicit that an empty-state screenshot proves the
 * route compiles and nothing else, so this script drives what each screen needs before it
 * photographs it — through the real endpoints, through the real authority, never by writing to
 * the database behind the app.
 *
 * **The prices are awkward on purpose.** Everything here is keyed at 1 499 excl. at 18 %, which
 * the ledger rounds to whole francs (RWF has no decimals) and the wire carries at two
 * (1 768.82 inclusive). Three of them is 5 306 in the ledger and 5 306.46 on the paper. Every
 * figure on these three fiscal screens is one of those two, and a shot taken at 2 000 × 10 —
 * where they agree exactly — would be a photograph of the reports with nothing to report.
 *
 * The **X view is photographed with a row still in flight**, which needs the authority down:
 * the worker drains every fifteen seconds, so a queue that RRA can answer is empty again before
 * any screenshot could catch it. That shot is the one that shows Close day warning rather than
 * refusing, which is decision 11 on the screen.
 *
 * The device is left **active**, as steps 6 and 7's scripts leave theirs: a suspended device is
 * a picture of the setup not being finished. The e2e suite is what puts the fixture back.
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

const ITEM_CODE = "FISCAL-R8";
const CUSTOMER_CODE = "FISCUSR8";
const SUPPLIER_CODE = "FISSUPR8";

/** See the file docstring: the price that makes the two rounding rules disagree. */
const PRICE = "1499";

/** `ONLY=4-daily-x` re-captures just that one. */
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
  await page.screenshot({ path: `${OUT}/${name}-${theme}.png`, fullPage: true });
  console.log("captured", `${name}-${theme}`);
}

/** One screen: drive it, then photograph it — or skip both when `ONLY` excludes it. The skip
 * wraps the **navigation**, so a subset re-capture does not walk the screens it was told to
 * leave alone and fail on one of their fixtures. */
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

async function sandboxMode(mode: string): Promise<void> {
  const res = await fetch(`${SANDBOX_ADMIN}/_sandbox/mode`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
  if (!res.ok) throw new Error(`sandbox mode ${mode} -> ${res.status}`);
}

interface Named {
  id: number;
  code: string;
}

/** `yyyy-mm-dd` in **local** time, the way `lib/format`'s `todayIso()` does it. */
function iso(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

const today = () => iso(new Date());
const monthStart = () => iso(new Date(new Date().getFullYear(), new Date().getMonth(), 1));
const monthEnd = () => iso(new Date(new Date().getFullYear(), new Date().getMonth() + 1, 0));

/**
 * Everything these seven screens need, driven through the product.
 *
 * The shape is chosen so the **tie has something to say**: three documents that RRA signed, one
 * left in the queue behind a device that went down, and a run of the revaluation. A listing
 * whose two sides agreed and whose asymmetry lists were both empty would be a correct
 * screenshot of a report doing nothing.
 */
async function seed(page: Page): Promise<{ deviceId: number; documentId: number }> {
  await apiOk(page, "/company", { tin: COMPANY_TIN }, "PATCH");

  // The authority forgets, so a repeated run does not meet `994` on an invoice number Vinea's
  // own sequence has just restarted. From **Node**, not from the page: the sandbox sends no
  // CORS headers, and it is this script's business rather than the app's.
  const reset = await fetch(`${SANDBOX_ADMIN}/_sandbox/reset`, { method: "POST" });
  if (!reset.ok) throw new Error(`sandbox reset -> ${reset.status}`);
  await sandboxMode("up");

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
        dvc_srl_no: "VINEA-DEMO-R801",
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
        name: "Awkward-priced wine",
        uom_category_id: count.id,
        base_uom_id: each.id,
        item_type: "stock",
        selling_price: PRICE,
        sales_account_id: byCode.get("4100"),
        cogs_account_id: byCode.get("5100"),
        default_sales_tax_code_id: outputVat.id,
        fiscal_class_code: "5059020800",
      })) as { id: number }
    ).id;

  for (const [role, code, tin, name] of [
    ["ar", CUSTOMER_CODE, CUSTOMER_TIN, "Umucyo Traders"],
    ["ap", SUPPLIER_CODE, SUPPLIER_TIN, "Kivu Supplies"],
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
  const suppliers = (await get(page, "/subledger/ap/partners")) as Array<{
    id: number;
    supplier_code: string | null;
  }>;
  const supplier = suppliers.find((p) => p.supplier_code === SUPPLIER_CODE)!;

  const warehouses = (await get(page, "/inventory/warehouses")) as Named[];
  await apiOk(page, "/oe/goods-received-notes", {
    partner_id: supplier.id,
    grn_date: today(),
    description: "Fiscal demo opening stock",
    warehouse_id: warehouses[0].id,
    lines: [{ item_id: itemId, quantity: "200", unit_cost: "1000" }],
  });

  // **A purchase.** Without one the VAT return's input side is zero and its tie shows `1400`
  // reconciling 0 against 0 — which is true, and a picture of a report with nothing to
  // reconcile. The screen is worth photographing with both accounts carrying a movement.
  const inputVat = taxCodes.find((t) => t.code === "VAT-IN-18")!;
  await apiOk(page, "/subledger/ap/documents", {
    kind: "invoice",
    partner_id: supplier.id,
    document_date: today(),
    description: "Fiscal demo purchase",
    lines: [
      { item_id: itemId, quantity: "20", unit_price: "1000", tax_code_id: inputVat.id },
    ],
  });

  // --- the three signed documents ---------------------------------------------------------
  const invoice = (await apiOk(page, "/subledger/ar/documents", {
    kind: "invoice",
    partner_id: customer.id,
    document_date: today(),
    description: "Fiscal demo invoice A",
    purchase_code: PURCHASE_CODE,
    payment_method: "credit",
    lines: [{ item_id: itemId, quantity: "3", unit_price: PRICE, tax_code_id: outputVat.id }],
  })) as { id: number; lines: Array<{ id: number }> };

  await apiOk(page, "/subledger/ar/documents", {
    kind: "invoice",
    partner_id: customer.id,
    document_date: today(),
    description: "Fiscal demo invoice B",
    purchase_code: PURCHASE_CODE,
    payment_method: "cash",
    lines: [{ item_id: itemId, quantity: "7", unit_price: PRICE, tax_code_id: outputVat.id }],
  });
  await apiOk(page, "/fiscal/outbox/drain");

  await apiOk(page, "/subledger/ar/documents", {
    kind: "credit_note",
    partner_id: customer.id,
    document_date: today(),
    description: "Fiscal demo refund",
    purchase_code: PURCHASE_CODE,
    refund_of_document_id: invoice.id,
    refund_reason: "06",
    lines: [
      {
        item_id: itemId,
        quantity: "1",
        unit_price: PRICE,
        returns_line_id: invoice.lines[0].id,
      },
    ],
  });
  await apiOk(page, "/fiscal/outbox/drain");

  // --- the day is closed here, between the two groups --------------------------------------
  //
  // Not at the end, and the position is the whole difference between two useful screenshots and
  // one. A Z holds what was signed since the previous close and an X holds what has been signed
  // since the last one — so closing after *everything* leaves the X with nothing but the stuck
  // row, and shot 4 becomes a picture of a warning over a column of zeros. Closing here gives
  // the Z the three documents above (shot 5) and leaves the USD sale below to fill the X, which
  // then carries figures **and** the warning (shot 4).
  await apiOk(page, `/fiscal/devices/${deviceId}/close-day`);

  // **Past the close's second before anything else is signed.** A Z's bounds are floored to a
  // second (`sdcDateTime` has no finer resolution) and its range is `from_at <` … `<= to_at`, so
  // a receipt issued in the very second the Z was taken lands inside a range whose figures are
  // already frozen — and the next range opens exclusively at the same instant, so neither Z
  // counts it. This script does a day's trading in under a second, which is the only way to
  // reach that boundary; a real device is minutes apart. Without this wait shot 4's X came out
  // a warning over a column of zeros, because the sale below had been swallowed by the Z above.
  await page.waitForTimeout(1500);

  // --- a posted revaluation, so the FX report has a run to open ---------------------------
  //
  // The mirror posts the day after the revaluation date, so a month-end run reaches into the
  // month after it and the seeded tenant marks that one `future`. Opening it is what an
  // accountant does by hand before closing a month.
  const periods = (await get(page, "/gl/periods")) as Array<
    Named & { start_date: string; status: string }
  >;
  const next = periods.find((period) => period.start_date > monthEnd() && period.status !== "open");
  if (next) await apiOk(page, `/gl/periods/${next.id}/open`);

  const currencies = (await get(page, "/gl/currencies")) as Array<Named & { is_base: boolean }>;
  const usd = currencies.find((c) => !c.is_base)!;
  const rates = (await get(page, `/gl/exchange-rates?currency_id=${usd.id}`)) as Array<{
    valid_from: string;
  }>;
  for (const [validFrom, rate] of [
    [monthStart(), "1320"],
    [monthEnd(), "1350"],
  ] as const) {
    if (rates.some((r) => r.valid_from === validFrom)) continue;
    await apiOk(page, "/gl/exchange-rates", { currency_id: usd.id, valid_from: validFrom, rate });
  }
  await apiOk(page, "/subledger/ar/documents", {
    kind: "invoice",
    partner_id: customer.id,
    document_date: today(),
    description: "Fiscal demo USD invoice",
    purchase_code: PURCHASE_CODE,
    currency_id: usd.id,
    lines: [{ item_id: itemId, quantity: "10", unit_price: "2", tax_code_id: outputVat.id }],
  });
  await apiOk(page, "/fiscal/outbox/drain");

  const runs = (await get(page, "/gl/fx-revaluations")) as unknown[];
  if (runs.length === 0) {
    await apiOk(page, "/gl/fx-revaluations", { revaluation_date: monthEnd(), role: "both" });
  }

  // --- a filed return, so the report has one to open ---------------------------------------
  const filed = (await get(page, "/tax/vat-returns")) as unknown[];
  if (filed.length === 0) {
    await apiOk(page, "/tax/vat-returns", {
      period_from: monthStart(),
      period_to: monthEnd(),
    });
  }

  // --- and one sale the authority never got ------------------------------------------------
  //
  // Last, and with the authority **down**, because the worker drains every fifteen seconds: a
  // row RRA can answer is signed again before any screenshot could catch it. This is what puts
  // a document in the ledger and not on the receipts, which is the row the tie exists to name —
  // and what makes the X view's Close day warning real rather than staged.
  await sandboxMode("down");
  await apiOk(page, "/subledger/ar/documents", {
    kind: "invoice",
    partner_id: customer.id,
    document_date: today(),
    description: "Fiscal demo invoice, still queued",
    purchase_code: PURCHASE_CODE,
    lines: [{ item_id: itemId, quantity: "5", unit_price: PRICE, tax_code_id: outputVat.id }],
  });
  await apiOk(page, "/fiscal/outbox/drain");

  return { deviceId, documentId: invoice.id };
}

async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  await login(page, OWNER);
  const { documentId } = await seed(page);

  for (const theme of ["light", "dark"] as const) {
    // 1 — the receipts enquiry: the counters as the paper prints them, and the three drills.
    await shot("1-receipts-enquiry", async () => {
      await page.goto(`${BASE}/fiscal/enquiries/receipts`);
      await page.waitForSelector("h1:has-text('Fiscal receipts')");
      await page.getByTestId("receipt-counter").first().waitFor({ state: "visible" });
      await page.waitForTimeout(300);
      await shoot(page, "1-receipts-enquiry", theme);
    });

    // 2 — the queue history for one document, with its action log open.
    await shot("2-queue-history", async () => {
      await page.goto(`${BASE}/fiscal/enquiries/queue-history`);
      await page.waitForSelector("h1:has-text('Fiscal queue history')");
      // The **last** option is the sale the authority never got — posted after the drain, with
      // the sandbox down. It is the document somebody opens a queue history to ask about, and
      // it is only in this list at all because the picker reads the queue rather than the
      // receipts: a receipt exists once RRA has signed, so a receipts-fed list offers every
      // document except the ones still in flight.
      await page.getByRole("combobox", { name: "Document", exact: true }).click();
      await page.getByRole("option").last().click();
      await page.getByTestId("history-sequence").first().waitFor({ state: "visible" });
      await page.getByTestId("history-inspect").first().click();
      await page.waitForTimeout(400);
      await shoot(page, "2-queue-history", theme);
    });

    // 3 — the VAT return as filed, with its tie and both annex buttons.
    await shot("3-vat-return-report", async () => {
      await page.goto(`${BASE}/tax/reports/vat-return`);
      await page.waitForSelector("h1:has-text('VAT return')");
      await page.getByTestId("report-net-payable").waitFor({ state: "visible" });
      await page.waitForTimeout(300);
      await shoot(page, "3-vat-return-report", theme);
    });

    // 4 — the X, with a row still in flight: the warning **before** the button, and Close day
    // still pressable. Decision 11 on the screen.
    await shot("4-daily-x-close-day", async () => {
      await page.goto(`${BASE}/tax/reports/daily-fiscal`);
      await page.waitForSelector("h1:has-text('Daily fiscal report')");
      await page.getByTestId("close-day-warning").waitFor({ state: "visible" });
      await page.waitForTimeout(300);
      await shoot(page, "4-daily-x-close-day", theme);
    });

    // 5 — the **Z** tab: the closed days, and the §19.1 figures of the one that is open.
    //
    // Not a second shot of the X. `fullPage` already puts the day's figures in shot 4, so a
    // second full-page capture of the same screen produced the same image to the byte — which
    // is the sort of thing a screenshot pass exists to catch and only catches if somebody
    // looks.
    await shot("5-daily-z-figures", async () => {
      await page.goto(`${BASE}/tax/reports/daily-fiscal`);
      await page.waitForSelector("h1:has-text('Daily fiscal report')");
      await page.getByRole("tab", { name: /Z —/ }).click();
      await page.getByTestId("z-number").first().waitFor({ state: "visible" });
      await page.getByTestId("open-z").first().click();
      await page.getByTestId("day-residue").waitFor({ state: "visible" });
      await page.waitForTimeout(400);
      await shoot(page, "5-daily-z-figures", theme);
    });

    // 6 — the tie. Declared against the ledger, the difference, and the one document that is
    // on one side and not the other with the word that fixes it.
    await shot("6-receipts-listing-tie", async () => {
      await page.goto(`${BASE}/tax/reports/receipts`);
      await page.waitForSelector("h1:has-text('Fiscal receipts listing')");
      await page.getByTestId("tie-difference").waitFor({ state: "visible" });
      await page.waitForTimeout(300);
      await shoot(page, "6-receipts-listing-tie", theme);
    });

    // 7 — the revaluation, per line, with the run and its next-day mirror named.
    await shot("7-fx-revaluation-report", async () => {
      await page.goto(`${BASE}/gl/reports/fx-revaluation`);
      await page.waitForSelector("h1:has-text('FX revaluation')");
      await page.getByTestId("fx-total-difference").waitFor({ state: "visible" });
      await page.waitForTimeout(300);
      await shoot(page, "7-fx-revaluation-report", theme);
    });
  }

  console.log("document used for the queue history:", documentId);
  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
