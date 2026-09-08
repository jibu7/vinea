import { expect, test } from "@playwright/test";
import {
  CREDIT_ACCOUNT_CODE,
  DEBIT_ACCOUNT_CODE,
  PRIMARY_EMAIL,
  SECONDARY_COMPANY,
  login,
  pickAccount,
} from "./support/fixtures";

test.describe("journal batch: login, switch company, post, enquire", () => {
  test("switches company, posts a balanced journal, and it foots the trial balance", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);

    // --- switch company -----------------------------------------------------------------
    // The primary e2e user has two memberships on purpose (for this test), so login doesn't
    // auto-select one (auth_service.select_membership only auto-picks when there's exactly
    // one) — the header shows "Select company" until the user picks. The switcher button is
    // the first button in the header regardless of which label it's currently showing.
    await page.locator("header button").first().click();
    await page.getByRole("button", { name: SECONDARY_COMPANY }).click();
    await expect(page.locator("header button").first()).toHaveText(SECONDARY_COMPANY);

    // --- create + post a balanced two-line journal ------------------------------------
    const description = `E2E balanced journal ${Date.now()}`;
    await page.goto("/gl/journal-batches/new");
    await page.waitForSelector("text=Journal Batch");
    await page.fill('input[placeholder="September payroll accrual"]', description);

    await pickAccount(page, 0, DEBIT_ACCOUNT_CODE);
    await page.locator("table tbody tr").nth(0).locator('input[placeholder="0"]').first().fill("42000");

    await pickAccount(page, 1, CREDIT_ACCOUNT_CODE);
    await page.locator("table tbody tr").nth(1).locator('input[placeholder="0"]').nth(1).fill("42000");

    await expect(page.getByText("Balanced", { exact: true })).toBeVisible();
    const postButton = page.locator('button:has-text("Post (Ctrl+Enter)")');
    await expect(postButton).toBeEnabled();
    await postButton.click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 10_000 });
    await page.waitForSelector("text=Posted");

    // --- entry visible in account enquiry ----------------------------------------------
    await page.goto("/gl/enquiries/account");
    await page.getByRole("button", { name: "Choose an account…" }).click();
    await page.keyboard.type(DEBIT_ACCOUNT_CODE);
    await page.waitForTimeout(250);
    await page.keyboard.press("Enter");
    await page.waitForSelector("text=Opening balance");
    await expect(page.locator(`table tbody tr:has-text("${description}")`)).toBeVisible({ timeout: 10_000 });

    // --- trial balance foots ------------------------------------------------------------
    await page.goto("/gl/enquiries/trial-balance");
    await page.waitForSelector("text=Trial Balance Enquiry");
    await expect(page.getByText("Balanced (foots)")).toBeVisible();
  });
});
