import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import {
  PRIMARY_EMAIL,
  READONLY_EMAIL,
  SECONDARY_EMAIL,
  accountIdByCode,
  login,
  pageFetch,
  pickCombobox,
  switchUser,
} from "./support/fixtures";
import { todayIso } from "../src/lib/format";

/**
 * P8 step 6 — the Maintenance screens banking needs before a statement can be imported.
 *
 * **Bank accounts** is the new one (`/maintenance/bank-accounts`, Appendix C.1.13). The other
 * three are sections on screens that already existed: **Bank details** on Suppliers, the
 * **Banking** block on GL Defaults, and the notice **Chart of accounts** shows when a bank or
 * cash control account it created got its master row.
 *
 * The fixture is shaped for this (decision 9): Rugari Wines E2E holds `1121 Bank Account USD`
 * beside `1120`, and Kivu Traders only the seeded pair, so the list is read both ways.
 *
 * Rule 13 shapes the file. Every screen is opened with data in it and a **figure** is asserted
 * where one exists — on Bank accounts that is *Test with a file*, run over the committed USD
 * sample the acceptance tape imports:
 *
 * * money — the closing balance the preview derives, `$ 495.00`, where the file says `495.00`
 *   and the wire carries `"495.00"`;
 * * quantity — `2 lines · 2 new · 0 already held`, formatted counts read off the result.
 *
 * Suppliers and Chart of accounts hold no money and no quantity on the parts this step
 * touched; what they hold is identifiers, and those are what they assert — read back after a
 * reload, not from the form that typed them.
 *
 * **The fixture is put back** where another spec reads it: the Defaults key this file moves
 * is restored in the same test. Everything else it creates is its own, suffixed, and read by
 * nobody else.
 */

const SUFFIX = String(Date.now()).slice(-6);
const NEW_GL_CODE = `B${SUFFIX}`;
const NEW_GL_NAME = `Bank Account EUR ${SUFFIX}`;
const CASH_GL_CODE = `C${SUFFIX}`;
const CASH_GL_NAME = `Petty Cash ${SUFFIX}`;
const RULE_PATTERN = `ACCOUNT FEE ${SUFFIX}`;

/** The committed sample the acceptance tape imports into the USD account. */
const USD_SAMPLE = path.resolve(
  __dirname,
  "../../backend/tests/banking/samples/generic-bk-usd-sep.csv",
);

async function openBankAccounts(page: Page) {
  await page.goto("/maintenance/bank-accounts");
  await page.waitForSelector("h1:has-text('Bank accounts')");
  await page.locator("tr[data-bank-account]").first().waitFor({ state: "visible" });
}

async function openDrawer(page: Page, code: string, name: string) {
  await page
    .locator(`tr[data-bank-account="${code}"]`)
    .getByRole("button", { name: `Edit ${name}`, exact: true })
    .click();
  return page.getByRole("dialog");
}

test.describe.configure({ mode: "serial" });

test.describe("P8 step 6 — banking maintenance", () => {
  // PATH: the list, read on the company with a foreign-currency account.
  // CANNOT SEE: a reconciled date. Nothing reconciles until step 7's workspace.
  test("Bank accounts lists the bank and cash accounts, with 1121 held in USD", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await openBankAccounts(page);

    const usd = page.locator('tr[data-bank-account="1121"]');
    await expect(usd).toContainText("Bank Account USD");
    await expect(usd).toContainText("1121 · Bank Account USD");
    await expect(usd.getByRole("cell", { name: "USD", exact: true })).toBeVisible();
    await expect(usd).toContainText("Bank of Kigali");
    await expect(usd).toContainText("00040-0000999-11");
    await expect(usd).toContainText("Never");

    const rwf = page.locator('tr[data-bank-account="1120"]');
    await expect(rwf.getByRole("cell", { name: "RWF", exact: true })).toBeVisible();
    await expect(page.locator('tr[data-bank-account="1110"]')).toContainText(
      "Not reconciled (cash)",
    );

    // The hook and the back-fill cover every path the product has, and the screen says so
    // rather than hiding the card.
    await expect(page.getByTestId("unregistered-empty")).toHaveText(
      "Every bank and cash control account in the chart has its row.",
    );
  });

  // PATH: the other company — the seeded pair and nothing else (decision 9).
  test("Kivu Traders holds only the seeded pair", async ({ page }) => {
    await login(page, SECONDARY_EMAIL);
    await openBankAccounts(page);
    await expect(page.locator("tr[data-bank-account]")).toHaveCount(2);
    await expect(page.locator('tr[data-bank-account="1121"]')).toHaveCount(0);
    await expect(page.locator('tr[data-bank-account="1120"]').getByRole("cell", { name: "RWF", exact: true })).toBeVisible();
  });

  // PATH: New — the GL account and its master in one call, in the currency asked for.
  test("New creates the GL account and its master together", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await openBankAccounts(page);

    await page.getByRole("button", { name: "New bank account", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("GL account code", { exact: true }).fill(NEW_GL_CODE);
    await dialog.getByLabel("GL account name", { exact: true }).fill(NEW_GL_NAME);
    await pickCombobox(page, "Held in currency", "USD", { within: dialog });
    await dialog.getByLabel("Bank name", { exact: true }).fill("Equity Bank Rwanda");
    await dialog.getByLabel("Bank account number", { exact: true }).fill(`4002-${SUFFIX}`);
    await dialog.getByRole("button", { name: "Create", exact: true }).click();
    await expect(page.getByText("Bank account created").first()).toBeVisible();

    // The drawer opens on the new account.
    const drawer = page.getByRole("dialog");
    await expect(drawer.getByRole("heading", { name: `${NEW_GL_CODE} · ${NEW_GL_NAME}` })).toBeVisible();
    await page.keyboard.press("Escape");

    // Read back from the list and from the chart, not from the form that typed it.
    await page.reload();
    await openBankAccounts(page);
    const row = page.locator(`tr[data-bank-account="${NEW_GL_CODE}"]`);
    await expect(row.getByRole("cell", { name: "USD", exact: true })).toBeVisible();
    await expect(row).toContainText(`4002-${SUFFIX}`);
    const chart = (await pageFetch(page, "/gl/accounts")).json as Array<{
      code: string;
      class: string;
      control_type: string | null;
      is_postable: boolean;
    }>;
    expect(chart.find((account) => account.code === NEW_GL_CODE)).toMatchObject({
      class: "asset",
      control_type: "bank",
      is_postable: true,
    });
  });

  // PATH: the currency lock. An account with a posting shows its currency as a fact, with the
  // reason, rather than a picker that would be refused.
  test("the currency is locked once the account has lines", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    const posted = await pageFetch(page, "/gl/cashbook-entries", {
      method: "POST",
      body: {
        entry_date: todayIso(),
        description: `P8 step 6 lock ${SUFFIX}`,
        cash_account_id: await accountIdByCode(page, "1120"),
        kind: "receipt",
        lines: [{ gl_account_id: await accountIdByCode(page, "3400"), amount: "1000" }],
      },
      headers: { "Idempotency-Key": `p8-6-lock-${SUFFIX}` },
    });
    expect(posted.status, JSON.stringify(posted.json)).toBe(201);

    await openBankAccounts(page);
    const drawer = await openDrawer(page, "1120", "Bank Account");
    await expect(drawer.getByTestId("currency-locked")).toHaveText("RWF · Rwandan Franc");
    await expect(
      drawer.getByText("Locked: the account already has postings, and each was valued in this currency."),
    ).toBeVisible();
    await page.keyboard.press("Escape");

    // The account New just made has none, so its picker is still a picker.
    const fresh = await openDrawer(page, NEW_GL_CODE, NEW_GL_NAME);
    await expect(fresh.getByTestId("currency-locked")).toHaveCount(0);
    await expect(fresh.getByRole("button", { name: "Held in currency", exact: true })).toBeVisible();
  });

  // PATH: the statement format — a custom mapping tried against a real file, its errors read
  // by row; the generic preset read cleanly, with the figures; the format saved.
  // CANNOT SEE: an import. The preview writes nothing, and Import is step 7's screen.
  test("Test with a file reads the sample under the mapping on the screen", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await openBankAccounts(page);
    const drawer = await openDrawer(page, "1121", "Bank Account USD");
    await drawer.getByRole("tab", { name: "Statement format", exact: true }).click();

    // A custom mapping with the wrong date format: every data row fails, each by its own row
    // number counted over the file (the header is row 1).
    await drawer.getByRole("combobox", { name: "Preset", exact: true }).click();
    await page.getByRole("option", { name: "Custom mapping", exact: true }).click();
    await drawer.getByRole("combobox", { name: "Date format", exact: true }).click();
    await page.getByRole("option", { name: "%d/%m/%Y", exact: true }).click();
    await drawer.getByLabel("Statement file", { exact: true }).setInputFiles(USD_SAMPLE);
    await drawer.getByRole("button", { name: "Test with a file", exact: true }).click();
    const result = drawer.getByTestId("format-test-result");
    await expect(result).toContainText("2 errors");
    const errorRows = result.locator("table").first().locator("tbody tr");
    await expect(errorRows).toHaveCount(2);
    await expect(errorRows.nth(0).locator("td").first()).toHaveText("2");
    await expect(errorRows.nth(1).locator("td").first()).toHaveText("3");
    await expect(errorRows.nth(0)).toContainText("date_column");

    // Back to the generic preset: the same file reads cleanly.
    await drawer.getByRole("combobox", { name: "Preset", exact: true }).click();
    await page.getByRole("option", { name: /^Generic/ }).click();
    await drawer.getByRole("button", { name: "Test with a file", exact: true }).click();
    await expect(result).toContainText("Read cleanly");
    // The quantity: formatted counts off the result.
    await expect(result).toContainText("2 lines · 2 new · 0 already held");
    // The money: the closing balance the preview derived from the balance column, in the
    // account's currency to its decimals — `495.00` in the file, `$ 495.00` on the screen.
    await expect(result.getByText("$ 495.00").first()).toBeVisible();
    await expect(result).toContainText("INWARD TRF C1 INV-3");
    await expect(result).toContainText("15/09/2026 to 15/09/2026");

    await drawer.getByRole("button", { name: "Save format", exact: true }).click();
    await expect(page.getByText("Statement format saved").first()).toBeVisible();
    const stored = await pageFetch(page, "/banking/accounts");
    const usd = (stored.json as Array<{ code: string; statement_format: { preset: string } | null }>).find(
      (row) => row.code === "1121",
    );
    expect(usd?.statement_format?.preset).toBe("generic");
  });

  // PATH: the rules list — create, edit, deactivate.
  // CANNOT SEE: the drawer a rule prefills. Step 7's workspace.
  test("a rule is created, edited and deactivated", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await openBankAccounts(page);
    const drawer = await openDrawer(page, "1121", "Bank Account USD");
    await drawer.getByRole("tab", { name: "Rules", exact: true }).click();

    await drawer.getByRole("button", { name: "New rule", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "New rule" });
    await dialog.getByLabel("Text contains", { exact: true }).fill(RULE_PATTERN);
    await dialog.getByLabel("Priority", { exact: true }).fill("10");
    await pickCombobox(page, "Post to account", "6700", { within: dialog });
    await dialog.getByLabel("Entry description", { exact: true }).fill("Monthly account fee");
    await dialog.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Rule created").first()).toBeVisible();

    const row = drawer.locator("tbody tr", { hasText: RULE_PATTERN });
    await expect(row).toContainText("6700 · Bank Charges");
    await expect(row).toContainText("Monthly account fee");
    await expect(row.locator("td").first()).toHaveText("10");

    await row.getByRole("button", { name: `Edit rule ${RULE_PATTERN}`, exact: true }).click();
    const edit = page.getByRole("dialog", { name: "Edit rule" });
    await edit.getByLabel("Priority", { exact: true }).fill("5");
    await edit.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Rule saved").first()).toBeVisible();
    await expect(row.locator("td").first()).toHaveText("5");

    await row.getByRole("button", { name: `Deactivate rule ${RULE_PATTERN}`, exact: true }).click();
    await expect(row).toContainText("Inactive");
  });

  // PATH: the three banking keys on GL Defaults, resolved to `code · name`, and the typeahead
  // assertion P6 and P7 pinned their pickers with.
  test("GL Defaults show the banking accounts and offer no control account", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/maintenance/defaults");
    await page.waitForSelector("h1:has-text('Defaults')");

    await expect(page.getByText("1130 · Bank Revaluation")).toBeVisible();
    await expect(page.getByText("6700 · Bank Charges")).toBeVisible();
    await expect(page.getByText("4300 · Other Income")).toBeVisible();

    // **The typeahead assertion.** Both halves: the account that must not be offered is
    // absent, and the one that belongs is present — an empty picker would satisfy the first on
    // its own. Bank revaluation offers assets, and every bank account is an asset *and* a
    // control account: 1120 and 1121 are exactly what decision 8 says the revaluation must
    // never touch, and 1200 is the AR control account.
    await page.getByRole("button", { name: "Bank revaluation", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await expect(page.locator('[cmdk-item]:has-text("1120 · Bank Account")')).toHaveCount(0);
    await expect(page.locator('[cmdk-item]:has-text("1121 · Bank Account USD")')).toHaveCount(0);
    await expect(page.locator('[cmdk-item]:has-text("1200 · Accounts Receivable")')).toHaveCount(0);
    await expect(page.locator('[cmdk-item]:has-text("1130 · Bank Revaluation")')).toHaveCount(1);
    await page.keyboard.press("Escape");

    // Bank charges offers income and expense only, so no bank account is on the menu at all.
    await page.getByRole("button", { name: "Bank charges", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await expect(page.locator('[cmdk-item]:has-text("1110 · Cash on Hand")')).toHaveCount(0);
    await expect(page.locator('[cmdk-item]:has-text("6700 · Bank Charges")')).toHaveCount(1);
    await page.keyboard.press("Escape");

    // One key changed, read back from the server — and put back, because step 7's drawer
    // reads it.
    await pickCombobox(page, "Bank interest", "4100");
    await page.getByRole("button", { name: "Save changes", exact: true }).click();
    await expect(page.getByText("Saved successfully").first()).toBeVisible();
    await page.reload();
    await page.waitForSelector("h1:has-text('Defaults')");
    await expect(page.getByText("4100 · Sales Revenue")).toBeVisible();

    await pickCombobox(page, "Bank interest", "4300");
    await page.getByRole("button", { name: "Save changes", exact: true }).click();
    await expect(page.getByText("Saved successfully").first()).toBeVisible();
    await page.reload();
    await page.waitForSelector("h1:has-text('Defaults')");
    await expect(page.getByText("4300 · Other Income")).toBeVisible();
  });

  // PATH: the supplier's bank details — written, read back after a reload, and one field
  // blanked on its own and read back blank.
  // CANNOT SEE: the instruction file that prints them. Step 7's payment run.
  test("Suppliers keep a bank details section", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/maintenance/suppliers");
    await page.waitForSelector("h1:has-text('Suppliers')");

    const openSupplier = async () => {
      await page
        .getByRole("button", { name: "Edit Musanze Packaging Ltd", exact: true })
        .click();
      return page.getByRole("dialog");
    };

    let drawer = await openSupplier();
    await expect(drawer.getByRole("heading", { name: "Bank details", exact: true })).toBeVisible();
    await drawer.getByLabel("Beneficiary bank", { exact: true }).fill("I&M Bank Rwanda");
    await drawer.getByLabel("Beneficiary account number", { exact: true }).fill(`2000-${SUFFIX}`);
    await drawer.getByLabel("Beneficiary name", { exact: true }).fill("Musanze Packaging Ltd");
    await drawer.getByRole("button", { name: "Save details", exact: true }).click();
    await expect(page.getByText("Supplier saved").first()).toBeVisible();

    await page.reload();
    await page.waitForSelector("h1:has-text('Suppliers')");
    drawer = await openSupplier();
    await expect(drawer.getByLabel("Beneficiary bank", { exact: true })).toHaveValue("I&M Bank Rwanda");
    await expect(drawer.getByLabel("Beneficiary account number", { exact: true })).toHaveValue(
      `2000-${SUFFIX}`,
    );

    // One field blanked on its own stays blank: the section sends the three as one fact.
    await drawer.getByLabel("Beneficiary name", { exact: true }).fill("");
    await drawer.getByRole("button", { name: "Save details", exact: true }).click();
    await expect(page.getByText("Supplier saved").first()).toBeVisible();
    await page.reload();
    await page.waitForSelector("h1:has-text('Suppliers')");
    drawer = await openSupplier();
    await expect(drawer.getByLabel("Beneficiary name", { exact: true })).toHaveValue("");
    await expect(drawer.getByLabel("Beneficiary bank", { exact: true })).toHaveValue("I&M Bank Rwanda");
  });

  // PATH: a cash control account created on the chart — the master row the same request
  // made, shown where the account was created.
  test("Chart of accounts shows the bank-account row a cash control account got", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/maintenance/chart-of-accounts");
    await page.waitForSelector("h1:has-text('Chart of accounts')");

    await page.getByRole("button", { name: "New account", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Code", { exact: true }).fill(CASH_GL_CODE);
    await dialog.getByLabel("Account class", { exact: true }).selectOption("asset");
    await dialog.getByLabel("Name", { exact: true }).fill(CASH_GL_NAME);
    await pickCombobox(page, "Parent account", "1100", { within: dialog });
    await dialog.getByLabel("Control account", { exact: true }).check();
    await dialog.getByLabel("Control type", { exact: true }).selectOption("cash");
    await dialog.getByRole("button", { name: "Save changes", exact: true }).click();

    const notice = page.getByTestId("created-bank-row");
    await expect(notice.getByRole("heading")).toHaveText(
      `${CASH_GL_CODE} · ${CASH_GL_NAME} is registered as a bank account`,
    );
    await expect(notice).toContainText(CASH_GL_CODE);
    await expect(notice).toContainText("Cash");
    await expect(notice).toContainText("RWF");

    await notice.getByRole("link", { name: "Open Bank accounts", exact: true }).click();
    await page.waitForSelector("h1:has-text('Bank accounts')");
    const row = page.locator(`tr[data-bank-account="${CASH_GL_CODE}"]`);
    await expect(row).toContainText(CASH_GL_NAME);
    await expect(row).toContainText("Not reconciled (cash)");
  });

  // PATH: the gate. A Clerk reads the accounts (`bank:reports_view`) and can change nothing.
  test("a read-only member sees the list and none of the writes", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await switchUser(page, READONLY_EMAIL);
    await openBankAccounts(page);
    await expect(page.locator('tr[data-bank-account="1121"]')).toBeVisible();
    await expect(page.getByRole("button", { name: "New bank account", exact: true })).toBeDisabled();
    await expect(page.getByText("Unregistered bank and cash accounts")).toHaveCount(0);
    const drawer = await openDrawer(page, "1121", "Bank Account USD");
    await expect(drawer.getByRole("button", { name: "Save details", exact: true })).toBeDisabled();
  });
});
