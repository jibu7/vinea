import { expect, test } from "@playwright/test";
import { DEBIT_ACCOUNT_CODE, PRIMARY_EMAIL, login } from "./support/fixtures";

/** Closes the cashbook half of #8 — P3's DoD says a cashbook entry can be drafted, posted and
 * seen in enquiries, and nothing tested the screen. */

const BANK_ACCOUNT_CODE = "1120"; // Bank Account — the cash side of a receipt

test.describe("cashbook batch: post a receipt and see it in the enquiry", () => {
  // PATH: /gl/cashbook-batches/new → POST /gl/journal-entries → /gl/entries/{id} →
  // /gl/enquiries/account. The cashbook screen is a different composition of `LineGrid`
  // (`mode="cashbook"`: one signed Amount column and a receipt/payment direction, instead of
  // the journal's debit and credit pair), which is why the journal spec passing said nothing
  // about this one.
  //
  // CANNOT SEE: the draft autosave path. `saveDraft("cashbook", …)` runs on every edit here
  // and is asserted in the Vitest for the draft store, but a reload mid-entry is not
  // exercised — that is `e2e-regression-amount-overwrite.ts`'s territory on the journal screen
  // and the cashbook shares the implementation.
  test("posts a cashbook receipt and it lands in the account enquiry", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);

    const description = `E2E cashbook receipt ${Date.now()}`;
    await page.goto("/gl/cashbook-batches/new");
    await page.waitForSelector("text=Cashbook Batch");

    // The bank side is a document-level field, not a grid row — that is the shape of the
    // screen and the thing a journal-shaped test would get wrong.
    await page.getByRole("button", { name: "Bank / cash account", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(BANK_ACCOUNT_CODE);
    await page.locator(`[cmdk-item]:has-text("${BANK_ACCOUNT_CODE}")`).first().click();

    await page.getByLabel("Description", { exact: true }).fill(description);

    // The contra line: what the money was for.
    await page.locator("table tbody tr").nth(0).locator("button").first().click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(DEBIT_ACCOUNT_CODE);
    await page.locator(`[cmdk-item]:has-text("${DEBIT_ACCOUNT_CODE}")`).first().click();
    await page.getByLabel("Amount, row 1").fill("18000");

    const post = page.getByRole("button", { name: "Post (Ctrl+Enter)" });
    await expect(post).toBeEnabled();
    await post.click();

    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 30_000 });
    await expect(page.getByText("Posted").first()).toBeVisible();
    // Both sides are on the entry: the bank account the screen carried, and the contra line.
    await expect(page.getByText(new RegExp(`${BANK_ACCOUNT_CODE}\\s`)).first()).toBeVisible();
    await expect(page.getByText(new RegExp(`${DEBIT_ACCOUNT_CODE}\\s`)).first()).toBeVisible();

    // And it is enquirable, which is the clause of the DoD this closes.
    await page.goto("/gl/enquiries/account");
    await page.getByRole("button", { name: "Account", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(BANK_ACCOUNT_CODE);
    await page
      .locator(`[cmdk-item]:has-text("${BANK_ACCOUNT_CODE}")`)
      .first()
      .waitFor({ state: "visible" });
    await page.keyboard.press("Enter");
    await page.waitForSelector("text=Opening balance");

    // The enquiry pages at 100 rows and offers "Load more"; a busy bank account runs past that
    // long before a real one would. Page until the row appears rather than assuming it landed
    // on the first page — which is true of an empty database and of nothing else.
    const row = page.locator(`table tbody tr:has-text("${description}")`);
    const rows = page.locator("table tbody tr");
    for (let i = 0; i < 20 && (await row.count()) === 0; i += 1) {
      const more = page.getByRole("button", { name: /Load more/i });
      // The button is removed once the last page is in, so its absence is the end of the
      // list rather than a failure.
      if ((await more.count()) === 0) break;
      const before = await rows.count();
      await more.click();
      await expect
        .poll(() => rows.count(), { timeout: 20_000 })
        .toBeGreaterThan(before);
    }
    await expect(row).toBeVisible({ timeout: 20_000 });
  });
});
