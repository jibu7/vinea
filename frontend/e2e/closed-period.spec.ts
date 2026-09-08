import { expect, test } from "@playwright/test";
import { CREDIT_ACCOUNT_CODE, DEBIT_ACCOUNT_CODE, PRIMARY_EMAIL, login, pickAccount } from "./support/fixtures";

/** `seed_e2e.py` closes the primary company's first accounting period (January of the
 * current year) so this test always has somewhere real to post into. */
async function openJanuary15th(page: import("@playwright/test").Page): Promise<void> {
  const today = new Date();
  await page.locator("button:has(svg.lucide-calendar)").first().click();
  for (let i = 0; i < today.getMonth(); i++) {
    await page.locator("button:has(svg.lucide-chevron-left)").click();
    await page.waitForTimeout(50);
  }
  await page.getByRole("button", { name: "15", exact: true }).click();
}

test.describe("posting into a closed period", () => {
  test("shows the inline period-closed error instead of posting", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/gl/journal-batches/new");
    await page.waitForSelector("text=Journal Batch");
    await page.fill('input[placeholder="September payroll accrual"]', `E2E closed period ${Date.now()}`);

    await pickAccount(page, 0, DEBIT_ACCOUNT_CODE);
    await page.locator("table tbody tr").nth(0).locator('input[placeholder="0"]').first().fill("7500");
    await pickAccount(page, 1, CREDIT_ACCOUNT_CODE);
    await page.locator("table tbody tr").nth(1).locator('input[placeholder="0"]').nth(1).fill("7500");
    await expect(page.getByText("Balanced", { exact: true })).toBeVisible();

    await openJanuary15th(page);

    const postButton = page.locator('button:has-text("Post (Ctrl+Enter)")');
    await expect(postButton).toBeEnabled();
    await postButton.click();

    await expect(page.getByText(/period is closed/i)).toBeVisible({ timeout: 10_000 });
    // No navigation to a posted entry happened.
    await expect(page).toHaveURL(/\/gl\/journal-batches\/new/);
  });
});
