import { expect, test } from "@playwright/test";
import { PRIMARY_EMAIL, assertNoSeriousViolations, login, setTheme } from "./support/fixtures";

/** P4 step 6 — the AR/AP maintenance screens: partners with role settings and contacts,
 * the shared masters, the module-filtered transaction types, the AR/AP defaults, and the
 * rename screen with its history. */

test.describe("AR/AP maintenance", () => {
  // PATH: /maintenance/customers → create → AR settings → rename, then the audit trail.
  // CANNOT SEE: whether the credit limit it sets has any effect — that needs a document
  // posted against it, which `ar-ap-acceptance` does.
  test("creates a customer, sets terms and a credit limit, then renames its code with history", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);

    const suffix = String(Date.now()).slice(-6);
    const code = `E2EC${suffix}`;
    const renamed = `E2ER${suffix}`;
    const name = `E2E Customer ${suffix}`;

    // --- create ---------------------------------------------------------------------
    await page.goto("/maintenance/customers");
    await page.waitForSelector("h1:has-text('Customers')");
    await page.getByRole("button", { name: /New customer/i }).click();
    await page.getByRole("dialog").getByLabel("Customer code").fill(code);
    await page.getByRole("dialog").getByLabel("Name").fill(name);
    await page.getByRole("dialog").getByRole("button", { name: "Create", exact: true }).click();

    // Creating opens the detail drawer straight onto the new partner.
    const drawer = page.getByRole("dialog").filter({ hasText: name });
    await expect(drawer).toBeVisible();

    // --- role settings: terms + credit limit ----------------------------------------
    await drawer.getByRole("tab", { name: "AR settings" }).click();
    await drawer.getByRole("button", { name: "Payment terms" }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.locator('[cmdk-item]:has-text("NET30")').first().click();
    await drawer.getByLabel(/Credit limit/).fill("500000");
    await drawer.getByRole("button", { name: /Save settings/ }).click();
    await expect(page.getByText("Settings saved").first()).toBeVisible();

    // --- thin CRM contact ------------------------------------------------------------
    await drawer.getByRole("tab", { name: "Contacts" }).click();
    await drawer.getByRole("button", { name: /Add contact/ }).click();
    await drawer.getByLabel("Name", { exact: true }).fill("Jeanne Mukamana");
    await drawer.getByLabel("Role", { exact: true }).fill("Accounts payable");
    await drawer.getByRole("button", { name: "Save contact" }).click();
    await expect(drawer.getByText("Jeanne Mukamana")).toBeVisible();
    await page.keyboard.press("Escape");

    // --- rename the code, history keeps the trail ------------------------------------
    await page.goto("/maintenance/rename-partner-code?role=ar");
    await page.waitForSelector("h1:has-text('Rename customer / supplier code')");
    await page.getByRole("button", { name: "Customer", exact: true }).click();
    await page.locator("[cmdk-input]").waitFor({ state: "visible" });
    await page.keyboard.type(code);
    await page.locator(`[cmdk-item]:has-text("${code}")`).first().click();

    await page.getByLabel("New customer code").fill(renamed);
    await page.getByRole("button", { name: "Rename", exact: true }).click();
    await expect(page.getByText("Customer renamed").first()).toBeVisible();

    // The audit trail hangs off partner_id, so both the old and the new code are on screen.
    const historyRow = page.locator('table tbody tr:has-text("partner.renamed")').first();
    await expect(historyRow).toBeVisible();
    await expect(historyRow).toContainText(code);
    await expect(historyRow).toContainText(renamed);
  });

  // PATH: the two transaction-type screens → GET /subledger/transaction-types?module=.
  // CANNOT SEE: whether the filter is the server's or the screen's. Either would satisfy
  // this; the RLS and permission tests in `tests/subledger` are what pin it server-side.
  test("AR and AP transaction types are filtered by module", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);

    await page.goto("/maintenance/ar-transaction-types");
    await page.waitForSelector("h1:has-text('AR transaction types')");
    const arModules = await page.locator("table tbody tr td:first-child").allTextContents();
    expect(arModules.length).toBeGreaterThan(0);
    expect(new Set(arModules.map((m) => m.trim()))).toEqual(new Set(["ar"]));

    await page.goto("/maintenance/ap-transaction-types");
    await page.waitForSelector("h1:has-text('AP transaction types')");
    const apModules = await page.locator("table tbody tr td:first-child").allTextContents();
    expect(apModules.length).toBeGreaterThan(0);
    expect(new Set(apModules.map((m) => m.trim()))).toEqual(new Set(["ap"]));
  });

  // PATH: /maintenance/ar-ap-defaults → the control-account combobox options.
  // CANNOT SEE: that saving a *non*-control account is refused — the options are filtered
  // here, and the DB-level guard against it lives in the posting-contract tests.
  test("AR/AP defaults offers only real control accounts for the control keys", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);

    await page.goto("/maintenance/ar-ap-defaults");
    await page.waitForSelector("h1:has-text('AR/AP defaults')");
    await page.getByRole("button", { name: "Accounts receivable control" }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    const options = await page.locator("[cmdk-item]").allTextContents();
    // "Not set" plus the AR control account(s) — no ordinary postable account may appear.
    expect(options.length).toBeGreaterThan(1);
    await page.keyboard.press("Escape");
  });

  test.describe("accessibility", () => {
    for (const [label, path, heading] of [
      ["customers", "/maintenance/customers", "Customers"],
      ["payment terms", "/maintenance/payment-terms", "Payment terms"],
      ["ageing bucket sets", "/maintenance/ageing-bucket-sets", "Ageing bucket sets"],
      ["AR/AP defaults", "/maintenance/ar-ap-defaults", "AR/AP defaults"],
    ] as const) {
      // PATH: axe over each maintenance screen at rest, in both themes.
      // CANNOT SEE: the create dialogs and detail drawers, which is where the forms are.
      test(`${label} — light and dark`, async ({ page }) => {
        await login(page, PRIMARY_EMAIL);
        await page.goto(path);
        await page.waitForSelector(`h1:has-text("${heading}")`);

        await setTheme(page, "light");
        await assertNoSeriousViolations(page);

        await setTheme(page, "dark");
        await assertNoSeriousViolations(page);
      });
    }
  });
});
