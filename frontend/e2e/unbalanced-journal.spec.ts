import { expect, test } from "@playwright/test";
import { DEBIT_ACCOUNT_CODE, PRIMARY_EMAIL, login, pickAccount } from "./support/fixtures";

test.describe("journal batch: unbalanced entries are blocked", () => {
  test("Post stays disabled while the two lines don't balance", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/gl/journal-batches/new", { waitUntil: "networkidle" });
    await page.waitForSelector("text=Journal Batch");
    await page.fill('input[placeholder="September payroll accrual"]', `E2E unbalanced ${Date.now()}`);

    const postButton = page.locator('button:has-text("Post (Ctrl+Enter)")');

    // The workspace starts with two empty rows; filling only one side leaves it both
    // unbalanced and short of two real lines.
    await pickAccount(page, 0, DEBIT_ACCOUNT_CODE);
    await page.locator("table tbody tr").nth(0).locator('input[placeholder="0"]').first().fill("10000");
    await expect(page.getByText("Unbalanced", { exact: true })).toBeVisible();
    await expect(postButton).toBeDisabled();

    // Two real lines now, but the amounts don't match: still unbalanced.
    await pickAccount(page, 1, "2300");
    await page.locator("table tbody tr").nth(1).locator('input[placeholder="0"]').nth(1).fill("9000");

    await expect(page.getByText("Unbalanced", { exact: true })).toBeVisible();
    await expect(postButton).toBeDisabled();
  });
});
