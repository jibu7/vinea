import { expect, test, type Page } from "@playwright/test";
import { PRIMARY_EMAIL, assertNoSeriousViolations, login, setTheme } from "./support/fixtures";

/** P4 step 7 — AR/AP journal batches on the journal grid, restricted to the module. */

async function pickCell(page: Page, label: RegExp, needle: string) {
  await page.getByLabel(label).first().click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(needle);
  await page.locator(`[cmdk-item]:has-text("${needle}")`).first().click();
}

async function newCustomer(page: Page, code: string, name: string) {
  await page.goto("/maintenance/customers");
  await page.waitForSelector("h1:has-text('Customers')");
  await page.getByRole("button", { name: /New customer/i }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Customer code").fill(code);
  await dialog.getByLabel("Name", { exact: true }).fill(name);
  await dialog.getByRole("button", { name: "Create", exact: true }).click();
  await expect(page.getByRole("dialog").filter({ hasText: name })).toBeVisible();
  await page.keyboard.press("Escape");
}

test.describe("AR journal batches", () => {
  // PATH: /ar/batches/new → POST /subledger/ar/batches → the AR journal number series.
  // CANNOT SEE: that a refused line refuses the whole batch. The service is keyed as one
  // unit and `tests/subledger` asserts the all-or-nothing; nothing here posts a bad line.
  test("charges two customers in one batch, on the journal number series", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    const suffix = String(Date.now()).slice(-6);
    await newCustomer(page, `BAT1${suffix}`, `Batch One ${suffix}`);
    await newCustomer(page, `BAT2${suffix}`, `Batch Two ${suffix}`);

    await page.goto("/ar/batches/new");
    await page.waitForSelector("h1:has-text('Account receivable batches')");
    await page.getByLabel("Reference").fill(`Interest run ${suffix}`);

    // Line 1 — the grid carries a partner column, which the journal grid does not.
    await pickCell(page, /^Partner, row 1/, `BAT1${suffix}`);
    await pickCell(page, /^Contra account, row 1/, "4300");
    await page.getByLabel(/^Description, row 1/).fill("Interest on overdue account");
    await page.getByLabel(/^Amount, row 1/).fill("1200");

    // Enter on the last row adds another — the shared keyboard model.
    await page.getByLabel(/^Amount, row 1/).press("Enter");
    await expect(page.getByLabel(/^Partner, row 2/)).toBeVisible();

    await pickCell(page, /^Partner, row 2/, `BAT2${suffix}`);
    await pickCell(page, /^Contra account, row 2/, "4300");
    await page.getByLabel(/^Description, row 2/).fill("Interest on overdue account");
    await page.getByLabel(/^Amount, row 2/).fill("800");

    await expect(page.getByText("FRw 2,000")).toBeVisible();

    const post = page.getByRole("button", { name: /^Post/ });
    await expect(post).toBeEnabled();
    await post.click();
    await expect(page.getByText(/2 documents posted/).first()).toBeVisible({ timeout: 20_000 });

    // Each line became its own open item, shown by transaction type — not "Customer invoice".
    await page.goto("/ar/allocations/new");
    await page.waitForSelector("h1:has-text('Allocate')");
    await page.getByRole("button", { name: "Partner", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(`BAT1${suffix}`);
    await page.locator(`[cmdk-item]:has-text("BAT1${suffix}")`).first().click();
    await expect(page.getByText("FRw 1,200")).toBeVisible();
    await expect(page.locator('td:has-text("ARJ-")').first()).toBeVisible();
  });

  // PATH: /ap/batches/new, rendered from the same component with the role flipped.
  // CANNOT SEE: an AP batch actually posting — this asserts the composition, and the AR
  // test above asserts the posting they share.
  test("the AP batch screen is the same grid for suppliers", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/ap/batches/new");
    await page.waitForSelector("h1:has-text('Account payable batches')");

    await expect(page.getByLabel(/^Partner, row 1/)).toBeVisible();
    await expect(page.getByLabel(/^Amount, row 1/)).toBeVisible();
    // Journal-grid columns that do not belong on a batch line.
    await expect(page.getByLabel(/^Debit, row 1/)).toHaveCount(0);
    await expect(page.getByLabel(/^Quantity, row 1/)).toHaveCount(0);
  });

  // PATH: axe over the batch grid in both themes.
  // CANNOT SEE: the grid with rows filled and a server error pinned to one of them.
  test("batch screen — light and dark", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/ar/batches/new");
    await page.waitForSelector("h1:has-text('Account receivable batches')");

    await setTheme(page, "light");
    await assertNoSeriousViolations(page);
    await setTheme(page, "dark");
    await assertNoSeriousViolations(page);
  });
});
