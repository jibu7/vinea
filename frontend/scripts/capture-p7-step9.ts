/**
 * The five screenshots P7's Definition of Done names, for the phase close: 1440x900, light and
 * dark.
 *
 * Run against the e2e stack on a **reset database**, with `ebm-sandbox` up — every receipt in
 * these shots was signed by it over HTTP, not written into a table:
 *
 *   make db-reset
 *   COMPOSE_FILE=docker-compose.yml:docker-compose.e2e.yml docker compose up -d --wait
 *   OUT=../docs/screenshots/p7-step-9 npx tsx scripts/capture-p7-step9.ts
 *
 * The DoD asks for five, and each is a *state* rather than a route — which is why this script
 * exists rather than a list of URLs:
 *
 *  1. **A fiscalized invoice printed with its SDC block.** Photographed under `print` media,
 *     because the CIS receipt is `hidden print:block`: a screenshot of the document screen
 *     shows the screen, and what the DoD asks for is the sheet a customer is handed.
 *  2. **The queue with a blocked device, and its recovery.** Two shots, because a queue screen
 *     with nothing wrong proves the route compiles: the authority is taken down under a posted
 *     sale, the device goes `Blocked`, and the second shot is the same screen after Retry now
 *     cleared it.
 *  3. **The VAT return with its tie.** Both VAT accounts carrying a movement, so the tie has
 *     something to reconcile rather than 0 against 0.
 *  4. **A Z.** With the counter window it owns on the row (revision `0026`) — a Z covers a run
 *     of receipts, and the dates beside it are what §19.1 prints.
 *  5. **The revaluation preview.** The lines, before anything is posted.
 *
 * **The prices are awkward on purpose.** 1 499 excl. at 18 % is 1 768.82 inclusive: the ledger
 * rounds to whole francs (RWF has no minor unit) and the wire carries two decimals, so every
 * figure on these screens differs between the two sides. A shot taken at 2 000 x 10 — where
 * they agree — would be a photograph of the reports with nothing to report.
 *
 * `ONLY=4-daily-z` re-captures one, navigation included.
 */
import { chromium, type Page } from "@playwright/test";
import { PASSWORD, PRIMARY_EMAIL as OWNER, pickDate } from "../e2e/support/fixtures";

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

const ITEM_CODE = "FISCAL-R9";
const CUSTOMER_CODE = "FISCUSR9";
const SUPPLIER_CODE = "FISSUPR9";
const PRICE = "1499";

const ONLY = (process.env.ONLY ?? "")
  .split(",")
  .map((name) => name.trim())
  .filter(Boolean);

const wanted = (name: string) => ONLY.length === 0 || ONLY.includes(name);

async function hydrated(page: Page, selector: string): Promise<void> {
  await page.waitForFunction((sel) => {
    const node = document.querySelector(sel);
    return (
      node !== null &&
      Object.keys(node).some((k) => k.startsWith("__reactFiber$") || k.startsWith("__reactProps$"))
    );
  }, selector);
}

async function login(page: Page): Promise<void> {
  await page.goto(`${BASE}/login`);
  await hydrated(page, "form");
  await page.fill('input[type="email"]', OWNER);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL(`${BASE}/`);
  await page.waitForSelector("text=Good morning");
}

async function shoot(
  page: Page,
  name: string,
  theme: "light" | "dark",
  media: "screen" | "print" = "screen",
): Promise<void> {
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
  await page.emulateMedia({ media });
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/${name}-${theme}.png`, fullPage: true });
  await page.emulateMedia({ media: "screen" });
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

function iso(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

const today = () => iso(new Date());
const monthStart = () => iso(new Date(new Date().getFullYear(), new Date().getMonth(), 1));
const monthEnd = () => iso(new Date(new Date().getFullYear(), new Date().getMonth() + 1, 0));

/** Everything the five shots need, driven through the product. */
async function seed(page: Page): Promise<{ deviceId: number; invoiceId: number }> {
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
        dvc_srl_no: "VINEA-DEMO-R901",
        bhf_id: "00",
      })) as { id: number }
    ).id;
  await apiOk(page, `/fiscal/devices/${deviceId}/initialize`);
  await apiOk(page, `/fiscal/devices/${deviceId}/sync-codes`);
  await apiOk(page, `/fiscal/devices/${deviceId}/sync-item-classes`);

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
  const inputVat = taxCodes.find((t) => t.code === "VAT-IN-18")!;

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
  const customer = ((await get(page, "/subledger/ar/partners")) as Array<{
    id: number;
    customer_code: string | null;
  }>).find((p) => p.customer_code === CUSTOMER_CODE)!;
  const supplier = ((await get(page, "/subledger/ap/partners")) as Array<{
    id: number;
    supplier_code: string | null;
  }>).find((p) => p.supplier_code === SUPPLIER_CODE)!;

  const warehouses = (await get(page, "/inventory/warehouses")) as Named[];
  await apiOk(page, "/oe/goods-received-notes", {
    partner_id: supplier.id,
    grn_date: today(),
    description: "Fiscal close opening stock",
    warehouse_id: warehouses[0].id,
    lines: [{ item_id: itemId, quantity: "200", unit_cost: "1000" }],
  });

  // **A purchase**, so the return's input side is not zero and its tie has two accounts to
  // reconcile rather than one.
  await apiOk(page, "/subledger/ap/documents", {
    kind: "invoice",
    partner_id: supplier.id,
    document_date: today(),
    description: "Fiscal close purchase",
    lines: [{ item_id: itemId, quantity: "20", unit_price: "1000", tax_code_id: inputVat.id }],
  });

  const invoice = (await apiOk(page, "/subledger/ar/documents", {
    kind: "invoice",
    partner_id: customer.id,
    document_date: today(),
    description: "Fiscal close invoice A",
    purchase_code: PURCHASE_CODE,
    payment_method: "credit",
    lines: [{ item_id: itemId, quantity: "3", unit_price: PRICE, tax_code_id: outputVat.id }],
  })) as { id: number; lines: Array<{ id: number }> };
  await apiOk(page, "/subledger/ar/documents", {
    kind: "invoice",
    partner_id: customer.id,
    document_date: today(),
    description: "Fiscal close invoice B",
    purchase_code: PURCHASE_CODE,
    payment_method: "cash",
    lines: [{ item_id: itemId, quantity: "7", unit_price: PRICE, tax_code_id: outputVat.id }],
  });
  await apiOk(page, "/fiscal/outbox/drain");

  await apiOk(page, "/subledger/ar/documents", {
    kind: "credit_note",
    partner_id: customer.id,
    document_date: today(),
    description: "Fiscal close refund",
    purchase_code: PURCHASE_CODE,
    refund_of_document_id: invoice.id,
    refund_reason: "06",
    lines: [
      { item_id: itemId, quantity: "1", unit_price: PRICE, returns_line_id: invoice.lines[0].id },
    ],
  });
  await apiOk(page, "/fiscal/outbox/drain");

  // **The day closes here, and nothing waits for the second to turn over.** Since revision
  // `0026` a Z owns a run of receipt counters rather than a stretch of clock, so the sale below
  // lands on the next day however fast this script trades. The earlier capture scripts waited
  // 1.5 s at exactly this point, and the wait is gone.
  await apiOk(page, `/fiscal/devices/${deviceId}/close-day`);

  // --- the revaluation's ingredients ------------------------------------------------------
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
    description: "Fiscal close USD invoice",
    purchase_code: PURCHASE_CODE,
    currency_id: usd.id,
    lines: [{ item_id: itemId, quantity: "10", unit_price: "2", tax_code_id: outputVat.id }],
  });
  await apiOk(page, "/fiscal/outbox/drain");

  return { deviceId, invoiceId: invoice.id };
}

async function main(): Promise<void> {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await login(page);
  const { deviceId, invoiceId } = await seed(page);

  // 1 — the receipt, on paper.
  await shot("1-invoice-receipt", async () => {
    await page.goto(`${BASE}/ar/documents/${invoiceId}`);
    await page.waitForSelector("[data-testid='fiscal-receipt-number']");
    for (const theme of ["light", "dark"] as const) {
      await shoot(page, "1-invoice-receipt", theme, "print");
    }
  });

  // 2 — the queue, blocked and then flowing again. The authority goes down under a posted sale,
  // which is the only way to photograph a queue with something in it: the worker drains every
  // fifteen seconds.
  await shot("2-queue-blocked", async () => {
    await sandboxMode("reject:884");
    const customer = ((await get(page, "/subledger/ar/partners")) as Array<{
      id: number;
      customer_code: string | null;
    }>).find((p) => p.customer_code === CUSTOMER_CODE)!;
    const item = ((await get(page, `/inventory/items?search=${ITEM_CODE}`)) as Named[]).find(
      (i) => i.code === ITEM_CODE,
    )!;
    await apiOk(page, "/subledger/ar/documents", {
      kind: "invoice",
      partner_id: customer.id,
      document_date: today(),
      description: "Fiscal close, refused by the authority",
      purchase_code: PURCHASE_CODE,
      lines: [{ item_id: item.id, quantity: "5", unit_price: PRICE }],
    });
    await apiOk(page, "/fiscal/outbox/drain");

    await page.goto(`${BASE}/fiscal/queue`);
    await page.waitForSelector("h1:has-text('Fiscal queue')");
    await page.waitForSelector("text=Blocked");
    for (const theme of ["light", "dark"] as const) {
      await shoot(page, "2-queue-blocked", theme);
    }

    // …and the recovery: the authority comes back and Retry now releases the device.
    await sandboxMode("up");
    await page.goto(`${BASE}/fiscal/queue`);
    await page.waitForSelector("h1:has-text('Fiscal queue')");
    for (let pass = 0; pass < 6; pass += 1) {
      const buttons = page.getByRole("button", { name: "Retry now", exact: true });
      const total = await buttons.count();
      for (let index = 0; index < total; index += 1) {
        const button = buttons.nth(index);
        if (await button.isEnabled()) await button.click();
      }
      await apiOk(page, "/fiscal/outbox/drain");
      await page.reload();
      await page.waitForSelector("h1:has-text('Fiscal queue')");
      if ((await page.getByText("Blocked").count()) === 0) break;
    }
    await page.waitForSelector("text=Flowing");
    for (const theme of ["light", "dark"] as const) {
      await shoot(page, "2-queue-recovered", theme);
    }
  });

  // 3 — the VAT return, both accounts reconciled.
  await shot("3-vat-return-tie", async () => {
    await page.goto(`${BASE}/tax/vat-returns`);
    await page.waitForSelector("h1:has-text('VAT return')");
    await page.waitForSelector("[data-testid='vat-output']");
    for (const theme of ["light", "dark"] as const) {
      await shoot(page, "3-vat-return-tie", theme);
    }
  });

  // 4 — the Z, with the counter window it owns.
  await shot("4-daily-z", async () => {
    await page.goto(`${BASE}/tax/reports/daily-fiscal`);
    await page.waitForSelector("h1:has-text('Daily fiscal report')");
    await page.getByRole("button", { name: /Z —/ }).click();
    await page.waitForSelector("[data-testid='z-number']");
    for (const theme of ["light", "dark"] as const) {
      await shoot(page, "4-daily-z", theme);
    }
  });

  // 5 — the revaluation preview: the lines, before anything is posted.
  await shot("5-revaluation-preview", async () => {
    await page.goto(`${BASE}/gl/fx-revaluations`);
    await page.waitForSelector("h1:has-text('FX revaluation')");
    // A calendar, not an input: `IsoDatePicker` is a button that opens a month grid.
    await pickDate(page, /^Revaluation date$/, monthEnd());
    await page.waitForSelector("[data-testid='post-revaluation']");
    for (const theme of ["light", "dark"] as const) {
      await shoot(page, "5-revaluation-preview", theme);
    }
  });

  console.log("device", deviceId, "left active — the e2e suite is what puts the fixture back");
  await browser.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
