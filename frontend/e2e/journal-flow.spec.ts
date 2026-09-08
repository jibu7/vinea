import { expect, test } from "@playwright/test";
import {
  CREDIT_ACCOUNT_CODE,
  DEBIT_ACCOUNT_CODE,
  PRIMARY_COMPANY,
  SECONDARY_EMAIL,
  login,
  pickAccount,
} from "./support/fixtures";

test.describe("journal batch: login, switch company, post, enquire", () => {
  test("switches company, posts a balanced journal, and it foots the trial balance", async ({ page }) => {
    // SECONDARY_EMAIL — not PRIMARY_EMAIL — is the user with two memberships (seed_e2e.py),
    // so it's the one whose login leaves no company auto-selected (select_membership only
    // auto-picks when there's exactly one membership) and needs the switcher. Every other
    // spec logs in as PRIMARY_EMAIL, which has exactly one membership and auto-selects.
    await login(page, SECONDARY_EMAIL);

    // --- switch company -----------------------------------------------------------------
    // The header shows "Select company" until a company is picked. The switcher button is
    // the first button in the header regardless of which label it's currently showing.
    await page.locator("header button").first().click();
    await page.getByRole("button", { name: PRIMARY_COMPANY }).click();
    await expect(page.locator("header button").first()).toHaveText(PRIMARY_COMPANY);

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
    // The combobox's accessible name comes from its wrapping Field ("Account"), not its
    // visible placeholder text ("Choose an account…") — Field/Combobox wire aria-labelledby
    // to the Field's label, which wins over the trigger's own text for name computation.
    await page.goto("/gl/enquiries/account");
    await page.getByRole("button", { name: "Account", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(DEBIT_ACCOUNT_CODE);
    await page.locator(`[cmdk-item]:has-text("${DEBIT_ACCOUNT_CODE}")`).first().waitFor({ state: "visible" });
    await page.keyboard.press("Enter");
    await page.waitForSelector("text=Opening balance");
    await expect(page.locator(`table tbody tr:has-text("${description}")`)).toBeVisible({ timeout: 10_000 });

    // --- trial balance foots ------------------------------------------------------------
    await page.goto("/gl/enquiries/trial-balance");
    await page.waitForSelector("text=Trial Balance Enquiry");
    await expect(page.getByText("Balanced (foots)")).toBeVisible();
  });
});
