import { expect, test } from "@playwright/test";
import {
  CREDIT_ACCOUNT_CODE,
  DEBIT_ACCOUNT_CODE,
  PRIMARY_EMAIL,
  login,
  pickAccount,
} from "./support/fixtures";

/** Closes the reversal half of #8. Reversal was built in P3 (`gl/entries/[id]`) with a date,
 * a reason and the one-reversal rule surfaced, and tested at no level — so the rule in
 * particular was unverified from the client side. */

test.describe("reversing a posted entry from the UI", () => {
  // PATH: /gl/journal-batches/new → /gl/entries/{id} → the reverse dialog →
  // POST /gl/journal-entries/{id}/reverse → the reversing entry. Then back to the original,
  // where the rule is asserted as the screen states it.
  //
  // CANNOT SEE: that the reversing entry is the arithmetic inverse of the original. The
  // kernel's `test_reversal_*` suite owns that; this owns the screen — that a reason is
  // required, that the reversal is reachable, and that the second attempt is refused.
  test("posts an entry, reverses it with a date and reason, and refuses a second reversal", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    await login(page, PRIMARY_EMAIL);

    const description = `E2E reversible journal ${Date.now()}`;
    await page.goto("/gl/journal-batches/new");
    await page.waitForSelector("text=Journal Batch");
    await page.fill('input[placeholder="September payroll accrual"]', description);

    await pickAccount(page, 0, DEBIT_ACCOUNT_CODE);
    await page.locator("table tbody tr").nth(0).locator('input[placeholder="0"]').first().fill("31000");
    await pickAccount(page, 1, CREDIT_ACCOUNT_CODE);
    await page.locator("table tbody tr").nth(1).locator('input[placeholder="0"]').nth(1).fill("31000");

    await page.getByRole("button", { name: "Post (Ctrl+Enter)" }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 30_000 });
    const originalUrl = page.url();
    await expect(page.getByText("Posted").first()).toBeVisible();

    // --- reverse it -------------------------------------------------------------------
    await page.getByRole("button", { name: "Reverse entry" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    // A reason is not optional: the confirm stays disabled until one is given, which is the
    // screen enforcing "corrections are explained", not merely recorded.
    const confirm = dialog.getByRole("button", { name: "Reverse", exact: true });
    await expect(confirm).toBeDisabled();

    const reason = `Duplicate posting ${Date.now()}`;
    await dialog.getByLabel("Reason").fill(reason);
    await expect(confirm).toBeEnabled();
    await confirm.click();

    // Lands on the reversing entry, which says what it reverses and why.
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 30_000 });
    await expect(page.getByText(/Reversal of entry /)).toBeVisible({ timeout: 20_000 });
    // The reason is on the page twice — the kernel copies it into the reversing entry's own
    // description, and the banner repeats it. Either is the assertion; both is why this is
    // `.first()` rather than a strict match.
    await expect(page.getByText(reason).first()).toBeVisible();

    // A reversal cannot itself be reversed — the rule, on the screen.
    const reverseOnReversal = page.getByRole("button", { name: "Reverse entry" });
    await expect(reverseOnReversal).toBeDisabled();
    await expect(reverseOnReversal).toHaveAttribute("title", /reversal cannot be reversed/i);

    // --- and the original refuses a second reversal ------------------------------------
    await page.goto(originalUrl);
    await page.waitForSelector("h1");
    await expect(page.getByText("Reversed").first()).toBeVisible();
    // Twice again: once as the status chip, once in the banner body.
    await expect(page.getByText(/Reversed by /).first()).toBeVisible();
    const reverseAgain = page.getByRole("button", { name: "Reverse entry" });
    await expect(reverseAgain).toBeDisabled();
    await expect(reverseAgain).toHaveAttribute("title", /already been reversed/i);
  });
});
