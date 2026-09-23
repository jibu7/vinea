/**
 * Captures the P8 step-7b screens — Payment runs, the AP document's "Paid in run", the
 * workspace's 1:3 match and the FX revaluation screen's bank line — for the review record:
 * 1440x900 (taller where a shot needs both halves of a screen), light and dark. Run against the
 * dev stack on a reset database (`make db-reset`), after the dev server has compiled the routes:
 *
 *   OUT=../docs/screenshots/p8-step-7 npx tsx scripts/capture-p8-payment-runs.ts
 *
 * Every shot has **rows in it** (rule 13). The script makes a bank account of its own, terms of
 * 2/10 net 30, three suppliers (one on those terms, one without bank details, one with a payment
 * on account) and their invoices, then drives the screens through their own buttons — Preview,
 * Post, Import — never by writing behind the app. Each state is photographed in both themes
 * before the script moves on.
 */
import { chromium, type Page } from "@playwright/test";
import { PASSWORD, PRIMARY_EMAIL as OWNER, pickCombobox } from "../e2e/support/fixtures";
import { todayIso } from "../src/lib/format";

const OUT = process.env.OUT ?? "screenshots";
const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const API = `${process.env.API_URL ?? "http://localhost:8000"}/api/v1`;

const SUFFIX = String(Date.now()).slice(-5);
const TODAY = todayIso();
const GL_CODE = `11${SUFFIX}`;
const GL_NAME = "Bank of Kigali Payments";

const SUPPLIERS = [
  { key: "terms", name: "Nyanza Timber Ltd", code: `NT${SUFFIX}`, amount: "100000" },
  { key: "credit", name: "Rubavu Glassworks", code: `RG${SUFFIX}`, amount: "236000" },
  { key: "nobank", name: "Huye Cork Supplies", code: `HC${SUFFIX}`, amount: "50000" },
] as const;

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

/** Both themes over the one state on the screen — the 7a script's `shoot`, unchanged. */
async function shoot(page: Page, name: string, opts: { height?: number } = {}) {
  await page.evaluate(() => {
    document.querySelectorAll<HTMLElement>("ol").forEach((node) => {
      if (getComputedStyle(node).position === "fixed") node.style.visibility = "hidden";
    });
    window.scrollTo(0, 0);
    document.querySelectorAll<HTMLElement>("main, [data-scroll-root]").forEach((node) => {
      node.scrollTop = 0;
    });
  });
  await page.setViewportSize({ width: 1440, height: opts.height ?? 900 });
  for (const theme of ["light", "dark"] as const) {
    await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/${name}-${theme}.png` });
    console.log("captured", `${name}-${theme}`);
  }
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
  await page.setViewportSize({ width: 1440, height: 900 });
}

async function apiOk(page: Page, path: string, body?: unknown, method = "POST"): Promise<unknown> {
  const res = await page.evaluate(
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
  if (res.status >= 300) throw new Error(`${method} ${path} -> ${res.status}: ${JSON.stringify(res.json)}`);
  return res.json;
}

function monthEdges(): [string, string] {
  const [year, month] = TODAY.split("-").map(Number);
  const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return [`${TODAY.slice(0, 8)}01`, `${TODAY.slice(0, 8)}${String(last).padStart(2, "0")}`];
}

async function main() {
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined,
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
  const page = await context.newPage();
  await login(page, OWNER);

  const accounts = (await apiOk(page, "/gl/accounts", undefined, "GET")) as Array<{ id: number; code: string }>;
  const idOf = (code: string) => accounts.find((a) => a.code === code)!.id;
  const bank = (await apiOk(page, "/banking/accounts", {
    new_account: { code: GL_CODE, name: GL_NAME, kind: "bank", parent_id: idOf("1100") },
    code: GL_CODE,
    name: GL_NAME,
    bank_name: "Bank of Kigali",
    account_number: "00040-0071234-01",
    statement_format: { preset: "generic" },
  })) as { id: number; gl_account_id: number };
  await apiOk(page, "/gl/cashbook-entries", {
    entry_date: TODAY,
    description: "Transfer from the operating account",
    reference: `OPEN${SUFFIX}`,
    cash_account_id: bank.gl_account_id,
    kind: "receipt",
    lines: [{ gl_account_id: idOf("3400"), amount: "1000000" }],
  });
  const terms = (await apiOk(page, "/subledger/payment-terms", {
    code: `T${SUFFIX}`,
    name: "2% 10 days, net 30",
    due_days: 30,
    discount_percent: "2",
    discount_days: 10,
  })) as { id: number };

  const invoices: Record<string, { id: number; number: string }> = {};
  const partners: Record<string, number> = {};
  for (const supplier of SUPPLIERS) {
    const partner = (await apiOk(page, "/subledger/ap/partners", {
      name: supplier.name,
      supplier_code: supplier.code,
      ...(supplier.key === "terms" ? { payment_terms_id: terms.id } : {}),
      ...(supplier.key === "nobank"
        ? {}
        : { bank_name: "Bank of Kigali", bank_account_number: `00040-${supplier.code}-01`, bank_account_holder: supplier.name }),
    })) as { id: number };
    partners[supplier.key] = partner.id;
    invoices[supplier.key] = (await apiOk(page, "/subledger/ap/documents", {
      kind: "invoice",
      partner_id: partner.id,
      document_date: TODAY,
      reference: `INV-${supplier.code}`,
      description: `Supplies from ${supplier.name}`,
      ...(supplier.key === "terms" ? { payment_terms_id: terms.id } : {}),
      lines: [{ unit_price: supplier.amount, gl_account_id: idOf("6990") }],
    })) as { id: number; number: string };
  }
  await apiOk(page, "/subledger/ap/documents", {
    kind: "settlement",
    partner_id: partners.credit,
    document_date: TODAY,
    description: "Payment on account",
    amount: "20000",
    cash_account_id: bank.gl_account_id,
    instrument_type: "bank",
  });

  // 1 — New: the selection, the discount available, and the preview with both warnings.
  await page.goto(`${BASE}/ap/payment-runs/new?account=${bank.id}`);
  for (const supplier of SUPPLIERS) {
    await page.getByRole("checkbox", { name: `Pay ${invoices[supplier.key].number}`, exact: true }).check();
  }
  await page.getByRole("button", { name: "Preview", exact: true }).click();
  await page.getByTestId("warning-open-credits").waitFor();
  await shoot(page, "7b-1-new-run-preview-discount-and-warnings", { height: 1700 });

  // 2 — Post, and the run's page once the advices are rendered.
  await page.getByRole("button", { name: "Post payment run", exact: true }).click();
  await page.waitForURL(/\/ap\/payment-runs\/\d+$/);
  const runId = Number(page.url().split("/").pop());
  const run = (await apiOk(page, `/banking/payment-runs/${runId}`, undefined, "GET")) as {
    number: string;
    lines: Array<{ document_id: number; settlement_document_id: number }>;
  };
  await page.locator('tr[data-remittance] >> text="Ready"').nth(2).waitFor({ timeout: 60_000 });
  await shoot(page, "7b-2-run-detail-instruction-file", { height: 1100 });

  // 3 — the AP document a run posted: "Paid in run", and its Reverse refused before the button.
  const settlement = run.lines.find((line) => line.document_id === invoices.terms.id)!.settlement_document_id;
  await page.goto(`${BASE}/ap/documents/${settlement}`);
  await page.getByTestId("reverse-blocked-reason").waitFor();
  await shoot(page, "7b-3-ap-document-paid-in-run");

  // 4 — the bank's one line for the run, imported; the chained auto-match, 1:3, on the workspace.
  await page.goto(`${BASE}/bank/statements?account=${bank.id}`);
  await page.getByRole("button", { name: "Import", exact: true }).click();
  const dialog = page.getByRole("dialog");
  const csv = [
    "Date,Description,Reference,Debit,Credit,Balance",
    `${TODAY},BULK PAYMENT ${run.number},${run.number},384000,,596000`,
  ].join("\n");
  await dialog.getByLabel("Statement file", { exact: true }).setInputFiles({
    name: "bk-bulk.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(`${csv}\n`),
  });
  await dialog.getByRole("button", { name: "Preview", exact: true }).click();
  await dialog.getByRole("button", { name: "Import statement", exact: true }).click();
  await page.getByTestId("import-result").getByText("1 matched", { exact: false }).waitFor();
  const reconciliation = (await apiOk(page, "/banking/reconciliations", {
    bank_account_id: bank.id,
    reconciliation_date: TODAY,
    statement_balance: null,
  })) as { id: number };
  await page.goto(`${BASE}/bank/reconciliations/${reconciliation.id}`);
  await page.locator("tr[data-statement-line-id]").first().waitFor();
  await shoot(page, "7b-4-workspace-run-matched-one-to-three", { height: 1500 });

  // 5 — FX revaluation, role bank: 1121's balance after a USD receipt at a dated rate.
  const currencies = (await apiOk(page, "/gl/currencies", undefined, "GET")) as Array<{ id: number; code: string }>;
  const usd = currencies.find((c) => c.code === "USD")!;
  const rates = (await apiOk(page, `/gl/exchange-rates?currency_id=${usd.id}`, undefined, "GET")) as Array<{
    valid_from: string;
  }>;
  const [start, end] = monthEdges();
  for (const [validFrom, rate] of [
    [start, "1320"],
    [end, "1350"],
  ] as const) {
    if (!rates.some((r) => r.valid_from === validFrom)) {
      await apiOk(page, "/gl/exchange-rates", { currency_id: usd.id, valid_from: validFrom, rate });
    }
  }
  await apiOk(page, "/gl/cashbook-entries", {
    entry_date: TODAY,
    description: "Inward transfer, export sale",
    reference: `USD${SUFFIX}`,
    cash_account_id: idOf("1121"),
    kind: "receipt",
    currency_id: usd.id,
    exchange_rate: "1320",
    lines: [{ gl_account_id: idOf("3400"), amount: "500" }],
  });
  await page.goto(`${BASE}/gl/fx-revaluations`);
  await pickCombobox(page, "Side", "Bank and cash accounts");
  await page.locator('tr[data-revaluation-line="1121"]').waitFor();
  await shoot(page, "7b-5-fx-preview-bank-line");

  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
