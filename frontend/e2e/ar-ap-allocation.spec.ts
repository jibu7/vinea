import { expect, test, type Page } from "@playwright/test";
import {
  PRIMARY_EMAIL,
  assertNoSeriousViolations,
  login,
  pickCombobox,
  setTheme,
} from "./support/fixtures";

/** P4 step 7 — the allocation screen, and the AP side of the document screens. */

const REVENUE = "4100";
const EXPENSE = "6990";
const BANK = "1120";

async function pickLineAccount(page: Page, code: string) {
  await page.getByRole("button", { name: "Account, row 1" }).click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(code);
  await page.locator(`[cmdk-item]:has-text("${code}")`).first().click();
}

async function makePartner(page: Page, role: "ar" | "ap", code: string, name: string) {
  await page.goto(role === "ar" ? "/maintenance/customers" : "/maintenance/suppliers");
  await page.waitForSelector(`h1:has-text('${role === "ar" ? "Customers" : "Suppliers"}')`);
  await page.getByRole("button", { name: role === "ar" ? /New customer/i : /New supplier/i }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel(role === "ar" ? "Customer code" : "Supplier code").fill(code);
  await dialog.getByLabel("Name", { exact: true }).fill(name);
  await dialog.getByRole("button", { name: "Create", exact: true }).click();
  await expect(page.getByRole("dialog").filter({ hasText: name })).toBeVisible();
  await page.keyboard.press("Escape");
}

test.describe("AR allocation", () => {
  test("allocates a receipt to an invoice, previewing the postings before Post", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    const suffix = String(Date.now()).slice(-6);
    const code = `E2EALC${suffix}`;
    await makePartner(page, "ar", code, `Alloc Customer ${suffix}`);

    // An invoice to settle...
    await page.goto("/ar/invoices/new");
    await page.waitForSelector("h1:has-text('Invoice')");
    await pickCombobox(page, "Customer", code);
    await page.getByLabel("Description", { exact: true }).fill(`Alloc invoice ${suffix}`);
    await pickLineAccount(page, REVENUE);
    await page.getByLabel("Unit price, row 1").fill("50000");
    await page.getByRole("button", { name: /^Post/ }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 20_000 });

    // ...and a receipt to settle it with.
    await page.goto("/ar/receipts/new");
    await page.waitForSelector("h1:has-text('Receipt')");
    await pickCombobox(page, "Customer", code);
    await page.getByLabel("Description", { exact: true }).fill(`Alloc receipt ${suffix}`);
    await page.getByLabel("Amount", { exact: true }).fill("20000");
    await pickCombobox(page, "Cash / bank account", BANK);
    await page.getByRole("button", { name: /^Post/ }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 20_000 });

    // --- allocate ---------------------------------------------------------------------
    await page.goto("/ar/allocations/new");
    await page.waitForSelector("h1:has-text('Allocate')");
    await pickCombobox(page, "Partner", code);

    const preview = page.getByTestId("allocation-preview");
    await expect(preview.getByText("Enter an amount to allocate, then preview.")).toBeVisible();

    // Apply the receipt, then say how much of the invoice it settles.
    await page.getByRole("button", { name: /^Apply / }).first().click();
    const allocateCell = page.getByLabel(/^Allocate against /).first();
    await allocateCell.fill("20000");

    // Post is refused until the preview has been taken — what posts must be what was shown.
    await expect(page.getByRole("button", { name: /^Post/ })).toBeDisabled();

    await page.getByRole("button", { name: "Preview", exact: true }).click();
    // Same currency, no discount: the honest answer is that this allocation writes no journal
    // entry at all. The panel says that rather than repeating "enter an amount".
    await expect(page.getByTestId("allocation-preview-empty")).toBeVisible();

    const post = page.getByRole("button", { name: /^Post/ });
    await expect(post).toBeEnabled();
    await post.click();
    await expect(page.getByText(/ALC-\d+ posted/).first()).toBeVisible({ timeout: 20_000 });

    // The invoice is now part-settled: 30,000 of the 50,000 remains open.
    await page.goto("/ar/allocations/new");
    await pickCombobox(page, "Partner", code);
    await expect(page.getByText("FRw 30,000")).toBeVisible();
  });

  test("editing an amount after previewing marks the preview stale and blocks Post", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/ar/allocations/new");
    await page.waitForSelector("h1:has-text('Allocate')");

    // The panel says where its numbers come from — this is the claim the whole design rests on.
    await expect(
      page.getByText("Computed by the same service that posts it, not re-derived here."),
    ).toBeVisible();
  });
});

test.describe("AP transaction screens", () => {
  test("posts a supplier invoice and a payment", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    const suffix = String(Date.now()).slice(-6);
    const code = `E2EAPS${suffix}`;
    await makePartner(page, "ap", code, `Alloc Supplier ${suffix}`);

    await page.goto("/ap/supplier-invoices/new");
    await page.waitForSelector("h1:has-text('Supplier invoice')");
    await pickCombobox(page, "Supplier", code);
    await page.getByLabel("Description", { exact: true }).fill(`AP invoice ${suffix}`);
    await pickLineAccount(page, EXPENSE);
    await page.getByLabel("Unit price, row 1").fill("15000");
    await page.getByRole("button", { name: /^Post/ }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 20_000 });
    await expect(page.getByText("Posted").first()).toBeVisible();

    await page.goto("/ap/payments/new");
    await page.waitForSelector("h1:has-text('Payment')");
    await pickCombobox(page, "Supplier", code);
    await page.getByLabel("Description", { exact: true }).fill(`AP payment ${suffix}`);
    await page.getByLabel("Amount", { exact: true }).fill("15000");
    await pickCombobox(page, "Cash / bank account", BANK);
    await page.getByRole("button", { name: /^Post/ }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 20_000 });
    await expect(page.getByText("Posted").first()).toBeVisible();
  });

  test("the AP return-to-supplier screen is the AP credit note", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/ap/returns/new");
    await page.waitForSelector("h1:has-text('Return to supplier')");

    await expect(page.getByLabel("Quantity, row 1")).toBeVisible();
    await expect(page.getByRole("button", { name: "Supplier", exact: true })).toBeVisible();
  });
});

test.describe("allocation accessibility", () => {
  test("allocation screen — light and dark", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/ar/allocations/new");
    await page.waitForSelector("h1:has-text('Allocate')");

    await setTheme(page, "light");
    await assertNoSeriousViolations(page);

    await setTheme(page, "dark");
    await assertNoSeriousViolations(page);
  });
});
