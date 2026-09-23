/**
 * Captures the P8 step-6 maintenance screens for the review record: 1440x900, light and dark.
 * Run against the dev stack on a reset database (`make db-reset`), after the dev server has
 * compiled the routes once:
 *
 *   OUT=../docs/screenshots/p8-step-6 npx tsx scripts/capture-p8-maintenance.ts
 *
 * Every shot has **rows in it** (rule 13). What a screen needs is driven through the real
 * endpoints first — a posting on `1120` so its currency is locked, a rule on `1121`, a
 * supplier of this script's own with bank details — never by writing to the database behind
 * the app. The seeded supplier is left alone: `e2e/p8-maintenance.spec.ts` reads it as having
 * no bank details, and step 7's payment run will too.
 */
import path from "node:path";
import { chromium, type Page } from "@playwright/test";
import { PASSWORD, PRIMARY_EMAIL as OWNER, pickCombobox } from "../e2e/support/fixtures";
import { todayIso } from "../src/lib/format";

const OUT = process.env.OUT ?? "screenshots";
const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const API = `${process.env.API_URL ?? "http://localhost:8000"}/api/v1`;

/** The committed sample the acceptance tape imports into the USD account. */
const USD_SAMPLE = path.resolve(__dirname, "../../backend/tests/banking/samples/generic-bk-usd-sep.csv");

const RULE_PATTERN = "ACCOUNT FEE";
const SUPPLIER_CODE = "KGLGLASS";
const SUPPLIER_NAME = "Kigali Glass Works Ltd";
/** Codes shot 8 may create a cash account under — the first one the chart does not hold. */
const CASH_CODES = ["1115", "1116", "1117", "1118", "1119"];

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

/** One authenticated request, made by the page itself — module scope for the reason
 * `capture-p7-maintenance.ts` gives (`tsx`'s `__name` helper does not exist in the browser). */
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

async function apiOk(page: Page, path: string, body?: unknown, method = "POST"): Promise<unknown> {
  const res = await apiCall(page, path, body, method);
  if (res.status >= 300) {
    throw new Error(`${method} ${path} -> ${res.status}: ${JSON.stringify(res.json)}`);
  }
  return res.json;
}

interface Account {
  id: number;
  code: string;
}

interface BankAccountRow {
  id: number;
  code: string;
  has_lines: boolean;
}

/** A receipt on `1120`, so shot 3 shows the currency as a locked fact — once, if it has none. */
async function seedPosting(page: Page, accounts: Account[]): Promise<void> {
  const banks = (await apiOk(page, "/banking/accounts", undefined, "GET")) as BankAccountRow[];
  if (banks.find((row) => row.code === "1120")?.has_lines) return;
  const id = (code: string) => accounts.find((account) => account.code === code)!.id;
  await apiOk(page, "/gl/cashbook-entries", {
    entry_date: todayIso(),
    description: "Owner's capital contribution",
    cash_account_id: id("1120"),
    kind: "receipt",
    lines: [{ gl_account_id: id("3400"), amount: "2500000" }],
  });
}

/** One rule on `1121`, so shot 5 is a list rather than its empty state. */
async function seedRule(page: Page, accounts: Account[]): Promise<void> {
  const banks = (await apiOk(page, "/banking/accounts", undefined, "GET")) as BankAccountRow[];
  const usd = banks.find((row) => row.code === "1121")!;
  const rules = (await apiOk(page, `/banking/accounts/${usd.id}/rules`, undefined, "GET")) as Array<{
    pattern: string;
  }>;
  if (rules.some((rule) => rule.pattern === RULE_PATTERN)) return;
  await apiOk(page, `/banking/accounts/${usd.id}/rules`, {
    pattern: RULE_PATTERN,
    priority: 10,
    gl_account_id: accounts.find((account) => account.code === "6700")!.id,
    description: "Monthly account fee",
  });
}

/** A supplier of this script's own, with the three bank details held. */
async function seedSupplier(page: Page): Promise<void> {
  const partners = (await apiOk(page, "/subledger/ap/partners", undefined, "GET")) as Array<{
    supplier_code: string | null;
  }>;
  if (partners.some((row) => row.supplier_code === SUPPLIER_CODE)) return;
  const created = (await apiOk(page, "/subledger/ap/partners", {
    name: SUPPLIER_NAME,
    supplier_code: SUPPLIER_CODE,
  })) as { id: number };
  await apiOk(
    page,
    `/subledger/ap/partners/${created.id}`,
    {
      clear_bank_details: true,
      bank_name: "I&M Bank Rwanda",
      bank_account_number: "2000-4471-0093",
      bank_account_holder: SUPPLIER_NAME,
    },
    "PATCH",
  );
}

async function openBankAccounts(page: Page) {
  await page.goto(`${BASE}/maintenance/bank-accounts`);
  await page.waitForSelector("h1:has-text('Bank accounts')");
  await page.locator("tr[data-bank-account]").first().waitFor({ state: "visible" });
}

async function openDrawer(page: Page, code: string, name: string) {
  await page
    .locator(`tr[data-bank-account="${code}"]`)
    .getByRole("button", { name: `Edit ${name}`, exact: true })
    .click();
  const drawer = page.getByRole("dialog");
  await drawer.waitFor({ state: "visible" });
  return drawer;
}

async function main() {
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined,
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await login(page, OWNER);
  const accounts = (await apiOk(page, "/gl/accounts", undefined, "GET")) as Account[];
  await seedPosting(page, accounts);
  await seedRule(page, accounts);
  await seedSupplier(page);

  for (const theme of ["light", "dark"] as const) {
    // 1 — the list: 1110 cash, 1120 in RWF, 1121 in USD with what the bank calls it.
    await openBankAccounts(page);
    await shoot(page, "1-bank-accounts", theme);

    // 2 — New: the GL account and its master in one form.
    await page.getByRole("button", { name: "New bank account", exact: true }).click();
    await page.getByRole("dialog").waitFor({ state: "visible" });
    await shoot(page, "2-bank-account-new", theme);
    await page.keyboard.press("Escape");

    // 3 — 1120's details, the currency shown as locked with the reason.
    let drawer = await openDrawer(page, "1120", "Bank Account");
    await drawer.getByTestId("currency-locked").waitFor({ state: "visible" });
    await shoot(page, "3-bank-account-currency-locked", theme);
    await page.keyboard.press("Escape");

    // 4 — 1121's statement format, Test with a file run over the tape's USD sample.
    drawer = await openDrawer(page, "1121", "Bank Account USD");
    await drawer.getByRole("tab", { name: "Statement format", exact: true }).click();
    await drawer.getByLabel("Statement file", { exact: true }).setInputFiles(USD_SAMPLE);
    await drawer.getByRole("button", { name: "Test with a file", exact: true }).click();
    const result = drawer.getByTestId("format-test-result");
    await result.getByText("$ 495.00").first().waitFor({ state: "visible" });
    await result.scrollIntoViewIfNeeded();
    await shoot(page, "4-statement-format-test", theme);

    // 5 — the rules tab on the same account.
    await drawer.getByRole("tab", { name: "Rules", exact: true }).click();
    await drawer.getByText(RULE_PATTERN).first().waitFor({ state: "visible" });
    await shoot(page, "5-bank-rules", theme);
    await page.keyboard.press("Escape");

    // 6 — a supplier's Bank details section, read back from the server.
    await page.goto(`${BASE}/maintenance/suppliers`);
    await page.waitForSelector("h1:has-text('Suppliers')");
    await page.getByRole("button", { name: `Edit ${SUPPLIER_NAME}`, exact: true }).click();
    const supplier = page.getByRole("dialog");
    await supplier.getByRole("heading", { name: "Bank details", exact: true }).waitFor();
    await supplier.evaluate((node) => {
      node.scrollTop = node.scrollHeight;
    });
    await shoot(page, "6-supplier-bank-details", theme);
    await page.keyboard.press("Escape");

    // 7 — GL Defaults' Banking block, each key resolved to `code · name`.
    await page.goto(`${BASE}/maintenance/defaults`);
    await page.waitForSelector("h1:has-text('Defaults')");
    await page.getByText("1130 · Bank Revaluation").waitFor({ state: "visible" });
    await page.getByText("Banking", { exact: true }).scrollIntoViewIfNeeded();
    await shoot(page, "7-gl-defaults-banking", theme);
  }

  // 8 — Chart of accounts, a cash control account just created and the row it got. Once, and
  // both themes over the same notice: it lives in the page's state, and creating a second
  // account for the dark shot would put two in the chart to photograph one.
  if (wanted("8-chart-bank-row")) {
    const held = new Set(((await apiOk(page, "/gl/accounts", undefined, "GET")) as Account[]).map((a) => a.code));
    const code = CASH_CODES.find((candidate) => !held.has(candidate));
    if (code === undefined) throw new Error(`every one of ${CASH_CODES.join(", ")} is taken`);
    await page.goto(`${BASE}/maintenance/chart-of-accounts`);
    await page.waitForSelector("h1:has-text('Chart of accounts')");
    await page.getByRole("button", { name: "New account", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Code", { exact: true }).fill(code);
    await dialog.getByLabel("Class", { exact: true }).selectOption("asset");
    await dialog.getByLabel("Name", { exact: true }).fill("Petty Cash Musanze");
    await pickCombobox(page, "Parent account", "1100", { within: dialog });
    await dialog.getByLabel("Control account", { exact: true }).check();
    await dialog.getByLabel("Control type", { exact: true }).selectOption("cash");
    await dialog.getByRole("button", { name: "Save changes", exact: true }).click();
    const notice = page.getByTestId("created-bank-row");
    await notice.getByText("RWF").waitFor({ state: "visible" });
    await page.waitForTimeout(600);
    await shoot(page, "8-chart-bank-row", "light");
    await shoot(page, "8-chart-bank-row", "dark");
  }

  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
