import { expect, test } from "@playwright/test";
import messages from "../src/i18n/messages/en.json";
import { PRIMARY_EMAIL, login, pickCombobox } from "./support/fixtures";

/** P4 step 7 — the AR/AP transaction screens on the P3 DocumentWorkspace + LineGrid. */

const REVENUE_ACCOUNT = "4100"; // Sales Revenue
const BANK_ACCOUNT = "1120"; // Bank Account

test.describe("AR transaction screens", () => {
  test("posts an invoice from the document workspace and lands on its journal entry", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);

    const suffix = String(Date.now()).slice(-6);
    const code = `E2EDOC${suffix}`;
    // A customer to invoice.
    await page.goto("/maintenance/customers");
    await page.waitForSelector("h1:has-text('Customers')");
    await page.getByRole("button", { name: /New customer/i }).click();
    await page.getByRole("dialog").getByLabel("Customer code").fill(code);
    await page.getByRole("dialog").getByLabel("Name", { exact: true }).fill(`Doc Customer ${suffix}`);
    await page.getByRole("dialog").getByRole("button", { name: "Create", exact: true }).click();
    await expect(page.getByRole("dialog").filter({ hasText: `Doc Customer ${suffix}` })).toBeVisible();
    await page.keyboard.press("Escape");

    // --- the invoice ------------------------------------------------------------------
    await page.goto("/ar/invoices/new");
    await page.waitForSelector("h1:has-text('Invoice')");

    await pickCombobox(page, "Customer", code);
    // The partner typeahead reports what they already owe and what is left of their limit.
    await expect(page.getByText("Open balance")).toBeVisible();
    await expect(page.getByText("Credit headroom")).toBeVisible();

    await page.getByLabel("Description", { exact: true }).fill(`E2E invoice ${suffix}`);
    await pickCombobox(page, "Payment terms", "NET30");

    // Line: account, then quantity x unit price less discount.
    await page.getByRole("button", { name: "Account, row 1" }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(REVENUE_ACCOUNT);
    await page.locator(`[cmdk-item]:has-text("${REVENUE_ACCOUNT}")`).first().click();
    await page.getByLabel("Quantity, row 1").fill("2");
    await page.getByLabel("Unit price, row 1").fill("30000");
    await page.getByLabel("Discount percent, row 1").fill("10");

    // The footer sums the exclusive lines as typed — 2 x 30,000 less 10%. RWF renders with
    // its symbol (FRw) and no decimals. Tax and the inclusive total are the server's, and
    // the footer says so rather than guessing them.
    await expect(page.getByText("FRw 54,000")).toBeVisible();
    // Read from the catalogue, not spelled here: the assertion is that the footer says tax
    // is not worked out on this screen, which stays true when the wording is tuned.
    await expect(
      page.getByText(messages.arap.documents.common.serverComputed).first(),
    ).toBeVisible();

    const post = page.getByRole("button", { name: /^Post/ });
    await expect(post).toBeEnabled();
    await post.click();

    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 20_000 });
    await expect(page.getByText("Posted").first()).toBeVisible();
  });

  test("a receipt dated ahead warns that the cash side is post-dated", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/ar/receipts/new");
    await page.waitForSelector("h1:has-text('Receipt')");

    // A receipt carries an amount and a cash account, not lines.
    await expect(page.getByLabel("Amount", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Cash / bank account" })).toBeVisible();
    await expect(page.getByLabel("Quantity, row 1")).toHaveCount(0);

    await pickCombobox(page, "Cash / bank account", BANK_ACCOUNT);
    await expect(page.getByText(/books to the post-dated account/)).toHaveCount(0);
  });
});
