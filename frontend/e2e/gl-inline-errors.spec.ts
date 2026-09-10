import { expect, test, type Page } from "@playwright/test";
import { CREDIT_ACCOUNT_CODE, DEBIT_ACCOUNT_CODE, PRIMARY_EMAIL, login, pageFetch, pickAccount } from "./support/fixtures";

/**
 * Closes the last two clauses of #8: `missing_exchange_rate` and `unbalanced_entry` reaching
 * the screen as inline errors rather than toasts.
 *
 * Both need a currency the rate table has never heard of. `XTS` is ISO 4217's code reserved
 * for testing, and it is created here with no rate ever written for it — which makes both
 * tests independent of the seed, of the order the suite runs in, and of any other spec that
 * happens to post an exchange rate for today.
 */

const TEST_CURRENCY = "XTS";

async function ensureRatelessCurrency(page: Page): Promise<void> {
  const existing = (await pageFetch(page, "/gl/currencies")).json as Array<{ code: string }>;
  if (existing.some((c) => c.code === TEST_CURRENCY)) return;
  const created = await pageFetch(page, "/gl/currencies", {
    method: "POST",
    body: { code: TEST_CURRENCY, name: "Testing currency", decimal_places: 2 },
    headers: { "Idempotency-Key": crypto.randomUUID() },
  });
  expect(created.ok, JSON.stringify(created.json)).toBeTruthy();
}

async function startJournal(page: Page, description: string): Promise<void> {
  await page.goto("/gl/journal-batches/new");
  await page.waitForSelector("text=Journal Batch");
  await page.fill('input[placeholder="September payroll accrual"]', description);
  await revealExtraColumns(page);
}

/** The journal grid hides branch, project, currency and tax behind a toggle, so a currency
 * cannot be set on a line until the columns are shown. */
async function revealExtraColumns(page: Page): Promise<void> {
  const toggle = page.getByRole("button", { name: /^More columns/ });
  if (await toggle.isVisible()) await toggle.click();
  await page.getByRole("button", { name: "Currency, row 1", exact: true }).waitFor();
}

async function setLineCurrency(page: Page, row: number, code: string): Promise<void> {
  await page.getByRole("button", { name: `Currency, row ${row + 1}`, exact: true }).click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(code);
  await page.locator(`[cmdk-item]:has-text("${code}")`).first().click();
}

test.describe("server-side posting refusals arrive inline", () => {
  // PATH: /gl/journal-batches/new → POST /gl/journal-entries → 422 with `field_errors` →
  // the screen's error mapping onto the row.
  //
  // The mapping was already right: `money.py` raises against `currency_id`, knowing nothing
  // about lines, and the kernel's per-line wrapper in `_resolve_lines` re-indexes any bare key
  // to `lines.<n>.<key>`, which is the shape `LineGrid` unpacks onto the row's currency cell.
  // What #8 was missing was this test — checked by reverting the mapping and watching it fail,
  // rather than assumed.
  //
  // CANNOT SEE: whether the *rate lookup itself* is right — `tests/kernel` owns that. What is
  // asserted here is that a refusal only the server can make is shown against the row that
  // caused it, and that nothing posted.
  test("missing_exchange_rate lands on the offending line's currency cell", async ({ page }) => {
    test.setTimeout(120_000);
    await login(page, PRIMARY_EMAIL);
    await ensureRatelessCurrency(page);

    await startJournal(page, `E2E no-rate journal ${Date.now()}`);

    // A line in a currency with no rate, and no rate typed either: only the server can know.
    await pickAccount(page, 0, DEBIT_ACCOUNT_CODE);
    await setLineCurrency(page, 0, TEST_CURRENCY);
    await page.locator("table tbody tr").nth(0).locator('input[placeholder="0"]').first().fill("100");

    await pickAccount(page, 1, CREDIT_ACCOUNT_CODE);
    await page.locator("table tbody tr").nth(1).locator('input[placeholder="0"]').nth(1).fill("100");

    // The client sums the amounts as typed, so as far as it knows this balances and Post is
    // offered. That is precisely the case the issue said was untested.
    await expect(page.getByText("Balanced", { exact: true })).toBeVisible();
    const post = page.getByRole("button", { name: "Post (Ctrl+Enter)" });
    await expect(post).toBeEnabled();
    await post.click();

    // Inline, and on the row that caused it — the whole point of line-indexing the error.
    const firstRow = page.locator("table tbody tr").nth(0);
    await expect(firstRow.getByText(/no exchange rate/i)).toBeVisible({ timeout: 30_000 });

    // Nothing posted, and the user is still on their entry with their work intact.
    await expect(page).toHaveURL(/\/gl\/journal-batches\/new/);
    await expect(page.locator("table tbody tr").nth(0).locator('input[placeholder="0"]').first()).toHaveValue("100");
  });

  // PATH: the same screen, but with a rate typed so the two sides balance *as typed* and
  // not once converted — the only route to `unbalanced_entry` from here, since Post is
  // otherwise disabled whenever the typed columns disagree.
  //
  // CANNOT SEE: whether the banner is the right home for every entry-level refusal. It
  // asserts this one lands there rather than in a toast, not that the rule generalises.
  test("unbalanced_entry from the server lands in the workspace, not a toast", async ({ page }) => {
    test.setTimeout(120_000);
    await login(page, PRIMARY_EMAIL);
    await ensureRatelessCurrency(page);

    await startJournal(page, `E2E base-unbalanced journal ${Date.now()}`);

    // 100 XTS at a rate of 50 is 5,000 in base; 100 in base is 100. The two sides are equal
    // as typed — which is all the client checks — and nowhere near equal once converted.
    // `unbalanced_entry` is otherwise unreachable from this screen, because Post is disabled
    // whenever the typed columns disagree.
    await pickAccount(page, 0, DEBIT_ACCOUNT_CODE);
    await setLineCurrency(page, 0, TEST_CURRENCY);
    await page.getByLabel("Exchange rate, row 1").fill("50");
    await page.locator("table tbody tr").nth(0).locator('input[placeholder="0"]').first().fill("100");

    await pickAccount(page, 1, CREDIT_ACCOUNT_CODE);
    await page.locator("table tbody tr").nth(1).locator('input[placeholder="0"]').nth(1).fill("100");

    await expect(page.getByText("Balanced", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Post (Ctrl+Enter)" }).click();

    // The refusal is entry-level — no single line is "the wrong one" — so it belongs in the
    // workspace banner rather than pinned to a row. It is still inline: same screen, above
    // the grid, `role="alert"`, and it survives until the next attempt.
    const banner = page.getByTestId("workspace-error");
    await expect(banner).toBeVisible({ timeout: 30_000 });
    await expect(banner).toContainText(/difference/i);
    await expect(page).toHaveURL(/\/gl\/journal-batches\/new/);
  });
});
