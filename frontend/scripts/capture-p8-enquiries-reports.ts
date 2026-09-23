/**
 * Captures the P8 step-8 screens — the Bank account enquiry, Cashbooks (detail and summary), the
 * Bank reconciliation report, the FX revaluation report's bank line and the GL entry page's bank
 * line — for the review record: 1440x900 (taller where a shot needs the whole report), light and
 * dark. Run against the dev stack on a reset database (`make db-reset`), after the dev server has
 * compiled the routes:
 *
 *   OUT=../docs/screenshots/p8-step-8 npx tsx scripts/capture-p8-enquiries-reports.ts
 *
 * Every shot has **rows in it** (rule 13). The script makes a bank account of its own and posts
 * the ledger the e2e posts — an opening transfer, two receipts, a cheque — imports the bank's
 * statement of the three it has seen, matches them, locks the reconciliation at a zero difference,
 * and then posts a bank charge dated inside it. Then a USD receipt on `1121`, the month's two
 * dated rates, and a bank-role revaluation at the month end. Every screen here only reads, so the
 * setup is through the API; the screens are photographed as a person opens them.
 */
import { chromium, type Page } from "@playwright/test";
import { PASSWORD, PRIMARY_EMAIL as OWNER } from "../e2e/support/fixtures";
import { todayIso } from "../src/lib/format";

const OUT = process.env.OUT ?? "screenshots";
const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const API = `${process.env.API_URL ?? "http://localhost:8000"}/api/v1`;

const SUFFIX = String(Date.now()).slice(-5);
const TODAY = todayIso();
const GL_CODE = `11${SUFFIX}`;
const GL_NAME = "I&M Bank Operating";

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

/** Both themes over the one state on the screen — the step-7 scripts' `shoot`, unchanged. */
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

async function importStatement(page: Page, bankAccountId: number, csv: string) {
  const res = await page.evaluate(
    async ({ url, bankAccountId, csv }) => {
      const form = new FormData();
      form.set("bank_account_id", String(bankAccountId));
      form.set("file", new Blob([csv], { type: "text/csv" }), "im-bank-statement.csv");
      const res = await fetch(url, {
        method: "POST",
        credentials: "include",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: form,
      });
      return { status: res.status, json: await res.json().catch(() => null) };
    },
    { url: `${API}/banking/statements`, bankAccountId, csv },
  );
  if (res.status >= 300) throw new Error(`import -> ${res.status}: ${JSON.stringify(res.json)}`);
  return (res.json as { statement: { id: number } }).statement;
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
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await login(page, OWNER);

  const accounts = (await apiOk(page, "/gl/accounts", undefined, "GET")) as Array<{ id: number; code: string }>;
  const idOf = (code: string) => accounts.find((a) => a.code === code)!.id;
  const bank = (await apiOk(page, "/banking/accounts", {
    new_account: { code: GL_CODE, name: GL_NAME, kind: "bank", parent_id: idOf("1100") },
    code: GL_CODE,
    name: GL_NAME,
    bank_name: "I&M Bank Rwanda",
    account_number: "20014-0098812-01",
    statement_format: { preset: "generic" },
  })) as { id: number; gl_account_id: number };

  const cashbook = async (kind: "receipt" | "payment", amount: string, reference: string, description: string) =>
    (await apiOk(page, "/gl/cashbook-entries", {
      entry_date: TODAY,
      description,
      reference,
      cash_account_id: bank.gl_account_id,
      kind,
      lines: [{ gl_account_id: idOf("3400"), amount }],
    })) as { id: number };
  const opening = await cashbook("receipt", "1000000", `TRF${SUFFIX}`, "Transfer from the savings account");
  const deposit = await cashbook("receipt", "250000", `DEP${SUFFIX}`, "Cash banked, Remera shop");
  const second = await cashbook("receipt", "120000", `DEP${SUFFIX}B`, "Cash banked, Kimihurura shop");
  await cashbook("payment", "80000", `CHQ${SUFFIX}`, "Cheque 004417, cleaning contractor");

  const csv =
    [
      "Date,Description,Reference,Debit,Credit,Balance",
      `${TODAY},TRANSFER FROM SAVINGS,,,1000000,1000000`,
      `${TODAY},CASH DEPOSIT REMERA,,,250000,1250000`,
      `${TODAY},CASH DEPOSIT KIMIHURURA,,,120000,1370000`,
    ].join("\n") + "\n";
  const statement = await importStatement(page, bank.id, csv);
  const detail = (await apiOk(page, `/banking/statements/${statement.id}`, undefined, "GET")) as {
    lines: Array<{ id: number; amount: string }>;
  };
  const ledger = (await apiOk(page, `/banking/accounts/${bank.id}/ledger-lines`, undefined, "GET")) as Array<{
    journal_line_id: number;
    entry_id: number;
  }>;
  for (const [amount, entry] of [
    ["1000000", opening],
    ["250000", deposit],
    ["120000", second],
  ] as const) {
    await apiOk(page, "/banking/matches", {
      bank_account_id: bank.id,
      statement_line_ids: [detail.lines.find((line) => Number(line.amount) === Number(amount))!.id],
      journal_line_ids: [ledger.find((line) => line.entry_id === entry.id)!.journal_line_id],
    });
  }
  const reconciliation = (await apiOk(page, "/banking/reconciliations", {
    bank_account_id: bank.id,
    reconciliation_date: TODAY,
    statement_balance: "1370000",
  })) as { id: number };
  await apiOk(page, `/banking/reconciliations/${reconciliation.id}/lock`, {});
  await cashbook("payment", "30000", `FEE${SUFFIX}`, "Bank charges keyed after the lock");

  // 1121: a USD balance, the month's two rates, and a bank-role run at the month end.
  const [first, last] = monthEdges();
  const currencies = (await apiOk(page, "/gl/currencies", undefined, "GET")) as Array<{ id: number; code: string }>;
  const usd = currencies.find((c) => c.code === "USD")!;
  const rates = (await apiOk(page, `/gl/exchange-rates?currency_id=${usd.id}`, undefined, "GET")) as Array<{
    valid_from: string;
  }>;
  for (const [validFrom, rate] of [
    [first, "1320"],
    [last, "1350"],
  ] as const) {
    if (!rates.some((r) => r.valid_from === validFrom)) {
      await apiOk(page, "/gl/exchange-rates", { currency_id: usd.id, valid_from: validFrom, rate });
    }
  }
  await apiOk(page, "/gl/cashbook-entries", {
    entry_date: TODAY,
    description: "Export proceeds, Kampala buyer",
    reference: `USD${SUFFIX}`,
    cash_account_id: idOf("1121"),
    kind: "receipt",
    currency_id: usd.id,
    exchange_rate: "1320",
    lines: [{ gl_account_id: idOf("3400"), amount: "500" }],
  });
  const periods = (await apiOk(page, "/gl/periods", undefined, "GET")) as Array<{
    id: number;
    start_date: string;
    status: string;
  }>;
  const next = periods.find((p) => p.start_date > last && p.status !== "open");
  if (next) await apiOk(page, `/gl/periods/${next.id}/open`);
  const run = (await apiOk(page, "/gl/fx-revaluations", { revaluation_date: last, role: "bank" })) as {
    number: string;
  };

  // 1. The enquiry.
  await page.goto(`${BASE}/gl/enquiries/bank-account?account=${bank.id}&as_of=${TODAY}`);
  await page.getByTestId("enquiry-book-balance").waitFor();
  await shoot(page, "8-1-bank-account-enquiry");

  // 2. Cashbooks detail, with the Reconciled column.
  await page.goto(`${BASE}/gl/reports/cashbooks?mode=detail&account=${bank.id}&from=${first}&to=${TODAY}`);
  await page.locator("tr[data-cashbook-line]").first().waitFor();
  await shoot(page, "8-2-cashbooks-detail-reconciled-column");

  // 3. Cashbooks summary.
  await page.goto(`${BASE}/gl/reports/cashbooks?mode=summary&from=${first}&to=${TODAY}`);
  await page.locator("tr[data-cashbook-account]").first().waitFor();
  await shoot(page, "8-3-cashbooks-summary");

  // 4. The reconciliation report: stored beside live, one outstanding item, one late line.
  await page.goto(`${BASE}/gl/reports/bank-reconciliation?reconciliation=${reconciliation.id}`);
  await page.getByTestId("posted-after-lock").waitFor();
  await shoot(page, "8-4-reconciliation-report-outstanding-and-late", { height: 1400 });

  // 5. The FX revaluation report on the bank-role run.
  await page.goto(`${BASE}/gl/reports/fx-revaluation`);
  await page.getByTestId("fx-run-number").waitFor();
  await page.getByRole("combobox", { name: "Revaluation run", exact: true }).click();
  await page.getByRole("option", { name: new RegExp(`^${run.number} — `) }).click();
  await page.locator('tr[data-revaluation-line="1121"]').waitFor();
  await shoot(page, "8-5-fx-report-bank-line");

  // 6. The GL entry page on a matched receipt: its bank line, locked in the BRC.
  await page.goto(`${BASE}/gl/entries/${deposit.id}`);
  await page.getByTestId("entry-bank-brc").waitFor();
  await shoot(page, "8-6-gl-entry-bank-line");

  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
