/**
 * Captures the P8 step-7a screens — Bank statements and the reconciliation workspace — for the
 * review record: 1440x900, light and dark. Run against the dev stack on a reset database
 * (`make db-reset`), after the dev server has compiled the routes once:
 *
 *   OUT=../docs/screenshots/p8-step-7 npx tsx scripts/capture-p8-transactions.ts
 *
 * Every shot has **rows in it** (rule 13). The script makes a bank account of its own (suffixed,
 * in the generic format, with an `ACCOUNT FEE` rule), posts the ledger lines, generates the
 * statement from them, and drives the screens — import, auto-match, a match, the drawer, the
 * lock — through the product's own buttons and endpoints, never by writing behind the app.
 *
 * The screens are stateful (a lock cannot be taken twice), so each state is photographed in
 * both themes before the script moves on.
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
const GL_NAME = "Bank of Kigali Operating";
const CUSTOMER_NAME = "Nyamirambo Traders";
const DEPOSIT_REF = `DEP${SUFFIX}`;

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

/** Both themes over the one state on the screen. */
async function shoot(page: Page, name: string, opts: { height?: number } = {}) {
  // A success toast over the panes is not what the shot is of. Radix pauses a toast's timer
  // while the page has the pointer or focus, so waiting it out is not reliable: hide the layer.
  // And start from the top: ticking a checkbox low on the page scrolls it.
  await page.evaluate(() => {
    document.querySelectorAll<HTMLElement>("ol").forEach((node) => {
      if (getComputedStyle(node).position === "fixed") node.style.visibility = "hidden";
    });
    window.scrollTo(0, 0);
    document.querySelectorAll<HTMLElement>("main, [data-scroll-root]").forEach((node) => {
      node.scrollTop = 0;
    });
  });
  // The workspace's two panes are stacked at this width; a taller window holds both, where a
  // full-page shot would be as long as the sidebar.
  await page.setViewportSize({ width: 1440, height: opts.height ?? 900 });
  for (const theme of ["light", "dark"] as const) {
    await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/${name}-${theme}.png`, });
    console.log("captured", `${name}-${theme}`);
  }
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
  await page.setViewportSize({ width: 1440, height: 900 });
}

/** Module scope for the reason `capture-p7-maintenance.ts` gives (`tsx`'s `__name` helper does
 * not exist in the browser). */
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

function statementCsv(badRow = false): string {
  const rows = [
    ["OPENING DEPOSIT", "", "", "1000000", "1000000"],
    [`TRANSFER ${DEPOSIT_REF}`, "", "", "118000", "1118000"],
    ["CASH DEPOSIT BULK", "", "", "40000", "1158000"],
    ["MONTHLY ACCOUNT FEE", "", "2500", "", "1155500"],
    ["INWARD TRF NYAMIRAMBO TRADERS", "", "", "60000", "1215500"],
  ];
  // The bad row is today in the bank's own `DD/MM/YYYY` — the export a mis-set format misreads.
  const [year, month, day] = TODAY.split("-");
  const lines = rows.map((row, index) =>
    [badRow && index === 2 ? `${day}/${month}/${year}` : TODAY, ...row].join(","),
  );
  return ["Date,Description,Reference,Debit,Credit,Balance", ...lines].join("\n") + "\n";
}

function csvFile(name: string, badRow = false) {
  return { name, mimeType: "text/csv", buffer: Buffer.from(statementCsv(badRow)) };
}

async function main() {
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined,
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await login(page, OWNER);

  const accounts = (await apiOk(page, "/gl/accounts", undefined, "GET")) as Array<{ id: number; code: string }>;
  const id = (code: string) => accounts.find((account) => account.code === code)!.id;
  const bank = (await apiOk(page, "/banking/accounts", {
    new_account: { code: GL_CODE, name: GL_NAME, kind: "bank", parent_id: id("1100") },
    code: GL_CODE,
    name: GL_NAME,
    bank_name: "Bank of Kigali",
    account_number: `00040-00${SUFFIX}-17`,
    statement_format: { preset: "generic" },
  })) as { id: number; gl_account_id: number };
  await apiOk(page, `/banking/accounts/${bank.id}/rules`, {
    pattern: "ACCOUNT FEE",
    gl_account_id: id("6700"),
    description: "Monthly account fee",
    priority: 10,
  });
  await apiOk(page, "/subledger/ar/partners", { name: CUSTOMER_NAME, customer_code: `NYT${SUFFIX}` });
  const post = async (kind: string, amount: string, reference: string, description: string) =>
    ((await apiOk(page, "/gl/cashbook-entries", {
      entry_date: TODAY,
      description,
      reference,
      cash_account_id: bank.gl_account_id,
      kind,
      lines: [{ gl_account_id: id("3400"), amount }],
    })) as { number: string }).number;
  await post("receipt", "1000000", `OPEN${SUFFIX}`, "Opening balance");
  const bulkA = await post("receipt", "25000", `BNKA${SUFFIX}`, "Cash banked, till A");
  const bulkB = await post("receipt", "15000", `BNKB${SUFFIX}`, "Cash banked, till B");
  await post("payment", "70000", `CHQ${SUFFIX}`, "Cheque 000412 to Musanze Packaging");
  await post("receipt", "118000", DEPOSIT_REF, "Customer transfer");

  // 1 — Import's preview over a file with one bad row: the error by row, Import refused.
  await page.goto(`${BASE}/bank/statements?account=${bank.id}`);
  await page.getByRole("heading", { name: "Bank statements", exact: true }).waitFor();
  await page.getByRole("button", { name: "Import", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Statement file", { exact: true }).setInputFiles(csvFile("bk-statement.csv", true));
  await dialog.getByRole("button", { name: "Preview", exact: true }).click();
  await dialog.getByTestId("import-blocked").waitFor();
  await shoot(page, "7a-1-import-preview-error");
  await dialog.getByLabel("Statement file", { exact: true }).setInputFiles(csvFile("bk-statement-fixed.csv"));
  await dialog.getByRole("button", { name: "Preview", exact: true }).click();
  await dialog.getByText("Read cleanly", { exact: true }).waitFor();
  await dialog.getByRole("button", { name: "Import statement", exact: true }).click();
  await page.getByTestId("import-result").waitFor();

  // Open the reconciliation through the product's own New dialog.
  await page.goto(`${BASE}/bank/reconciliations?account=${bank.id}`);
  await page.getByRole("button", { name: "New reconciliation", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Open reconciliation", exact: true }).click();
  await page.waitForURL(/\/bank\/reconciliations\/\d+$/);
  const workspaceUrl = page.url();
  await page.locator("tr[data-statement-line-id]").first().waitFor();
  await page.getByRole("button", { name: "Auto-match", exact: true }).click();
  await page.getByTestId("auto-match-result").waitFor();

  // 3 — mid-match: one bank line against the first of the two ledger lines it covers, the
  // selection's imbalance beside the button.
  await page.getByRole("checkbox", { name: "Select statement line CASH DEPOSIT BULK", exact: true }).check();
  await page.getByRole("checkbox", { name: `Select ledger line ${bulkA}`, exact: true }).check();
  await page.getByTestId("selection-balance").waitFor();
  await shoot(page, "7a-3-workspace-mid-match", { height: 1500 });
  await page.getByRole("checkbox", { name: `Select ledger line ${bulkB}`, exact: true }).check();
  await page.getByRole("button", { name: "Match", exact: true }).click();
  await page
    .locator("tr[data-statement-line-id]", { hasText: "CASH DEPOSIT BULK" })
    .getByText("Matched · by hand")
    .waitFor();

  // 2 — the statement's detail: three of five lines matched, each by its rule.
  const statements = (await apiOk(page, `/banking/statements?bank_account_id=${bank.id}`, undefined, "GET")) as Array<{
    id: number;
  }>;
  await page.goto(`${BASE}/bank/statements/${statements[0].id}`);
  await page.getByTestId("statement-matched").waitFor();
  await shoot(page, "7a-2-statement-detail");

  // 4 — the fee's drawer, prefilled by the rule.
  await page.goto(workspaceUrl);
  await page.locator("tr[data-statement-line-id]").first().waitFor();
  await page
    .locator("tr[data-statement-line-id]", { hasText: "MONTHLY ACCOUNT FEE" })
    .getByRole("button", { name: "Post from line", exact: true })
    .click();
  const drawer = page.getByRole("dialog", { name: "Post from a statement line", exact: true });
  await drawer.getByTestId("prefill-note").waitFor();
  await drawer.getByText("6700 · Bank Charges").first().waitFor();
  await shoot(page, "7a-4-drawer-prefilled-by-rule");
  await drawer.getByRole("button", { name: "Post cashbook entry", exact: true }).click();
  await drawer.waitFor({ state: "hidden" });

  await page
    .locator("tr[data-statement-line-id]", { hasText: "INWARD TRF" })
    .getByRole("button", { name: "Post from line", exact: true })
    .click();
  const receipt = page.getByRole("dialog", { name: "Post from a statement line", exact: true });
  await receipt.getByRole("tab", { name: "Customer receipt", exact: true }).click();
  await pickCombobox(page, "Customer", CUSTOMER_NAME, { within: receipt });
  await receipt.getByRole("button", { name: "Post receipt", exact: true }).click();
  await receipt.waitFor({ state: "hidden" });

  // 5 — Lock refused, with the difference shown before the button: the statement balance
  // re-keyed 500 short, as it would be read off a smudged paper copy.
  await page.getByTestId("lock-ready").waitFor();
  await page.getByLabel("Statement balance", { exact: true }).fill("1215000");
  await page.getByTestId("lock-blocked").waitFor();
  await shoot(page, "7a-5-lock-refused-difference", { height: 1500 });

  // 6 — locked at zero.
  await page.getByLabel("Statement balance", { exact: true }).fill("1215500");
  await page.getByTestId("lock-ready").waitFor();
  await page.getByRole("button", { name: "Lock", exact: true }).click();
  await page.getByTestId("locked-note").waitFor();
  await shoot(page, "7a-6-locked-at-zero", { height: 1500 });

  // 7 — Bank accounts: the currency lock as a neutral hint, on the account just reconciled.
  await page.goto(`${BASE}/maintenance/bank-accounts`);
  await page.locator(`tr[data-bank-account="${GL_CODE}"]`).waitFor();
  await page
    .locator(`tr[data-bank-account="${GL_CODE}"]`)
    .getByRole("button", { name: `Edit ${GL_NAME}`, exact: true })
    .click();
  await page.getByRole("dialog").locator("[data-field-hint]").waitFor();
  await shoot(page, "7a-7-bank-accounts-currency-hint");

  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
