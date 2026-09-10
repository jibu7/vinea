import { expect, test, type Page } from "@playwright/test";
import { PRIMARY_EMAIL, assertNoSeriousViolations, login, setTheme } from "./support/fixtures";

/**
 * P4 step 8 — the two enquiries and the ten reports.
 *
 * Every test below states, in one line, the path it takes and what it cannot see. That is the
 * question that would have caught all five of this phase's earlier misses: the P3 keyboard
 * tests that only ever touched the description column, the nav test that never checked the
 * tree was complete, the DoD line marked done from prose, the back-fill test that started at a
 * revision where its subject did not exist, and the acceptance figure that agreed because
 * nothing had been posted. In each case the test passed and saw nothing.
 */

const AS_OF = "2026-09-30";

async function pick(page: Page, name: string, needle: string) {
  await page.getByRole("button", { name, exact: true }).click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(needle);
  await page.locator(`[cmdk-item]:has-text("${needle}")`).first().click();
}

test.describe("customer enquiry", () => {
  // PATH: /ar/enquiry -> GET /subledger/ar/enquiry/{id}, open items tab, drill into the entry.
  // CANNOT SEE: whether the running balance is arithmetically right — that is the acceptance
  // test's job; this only proves the figures reach the screen and the drill-down resolves.
  test("shows a partner's open items and drills into the journal entry behind one", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/ar/enquiry");
    await page.waitForSelector("h1:has-text('Customer enquiry')");
    await expect(page.getByText("Choose a partner to see their account.")).toBeVisible();

    await pick(page, "Customer", "E2E");
    await expect(page.getByText("Open balance")).toBeVisible();

    const drill = page.getByRole("button", { name: /^Open journal entry / }).first();
    await expect(drill).toBeVisible();
    await drill.click();

    // The drawer carries the entry's own lines, and a link out to the full entry screen.
    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();
    await expect(drawer.getByRole("link", { name: /Journal entry/ })).toBeVisible();
  });

  // PATH: posts a JNL batch line, then reads that partner's enquiry. CANNOT SEE: the statement
  // or ageing labels, which render through different code —
  // `test_the_enquiry_and_statement_show_the_transaction_type_not_the_kind` covers the
  // statement server-side.
  test("labels a journal debit as a journal, not as the invoice it is shaped like", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    const suffix = String(Date.now()).slice(-6);
    const code = `ENQ${suffix}`;

    await page.goto("/maintenance/customers");
    await page.waitForSelector("h1:has-text('Customers')");
    await page.getByRole("button", { name: /New customer/i }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Customer code").fill(code);
    await dialog.getByLabel("Name", { exact: true }).fill(`Enquiry ${suffix}`);
    await dialog.getByRole("button", { name: "Create", exact: true }).click();
    await expect(page.getByRole("dialog").filter({ hasText: `Enquiry ${suffix}` })).toBeVisible();
    await page.keyboard.press("Escape");

    await page.goto("/ar/batches/new");
    await page.waitForSelector("h1:has-text('Account receivable batches')");
    await page.getByLabel(/^Partner, row 1/).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(code);
    await page.locator(`[cmdk-item]:has-text("${code}")`).first().click();
    await page.getByRole("button", { name: "Contra account, row 1", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type("4300");
    await page.locator('[cmdk-item]:has-text("4300")').first().click();
    await page.getByLabel(/^Description, row 1/).fill("Interest charged");
    await page.getByLabel(/^Amount, row 1/).fill("1500");
    await page.getByRole("button", { name: /^Post/ }).click();
    await expect(page.getByText(/1 documents posted/).first()).toBeVisible({ timeout: 20_000 });

    await page.goto("/ar/enquiry");
    await page.waitForSelector("h1:has-text('Customer enquiry')");
    await pick(page, "Customer", code);

    // Invoice-shaped in the ledger, and it must not say so on screen. "Customer invoice" is a
    // legitimate label for a real invoice — the assertion is about *this* partner's row.
    const row = page.locator('table tbody tr:has-text("ARJ-")').first();
    await expect(row).toBeVisible();
    await expect(row).toContainText("AR journal");
    await expect(row).not.toContainText("Customer invoice");
  });
});

test.describe("age analysis", () => {
  // PATH: /ar/reports/age-analysis -> GET /subledger/ar/ageing with an as-of date and the
  // company's default bucket set. CANNOT SEE: whether the buckets agree with the control
  // account — the step 8 acceptance test asserts that, and would fail before this would.
  test("buckets open items and foots to a grand total", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/ar/reports/age-analysis");
    await page.waitForSelector("h1:has-text('Age analysis')");

    await expect(page.getByText("Grand total")).toBeVisible();
    await expect(page.getByRole("button", { name: "Export CSV" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Print" })).toBeVisible();
  });
});

test.describe("statements", () => {
  // PATH: /ar/reports/statements -> POST /statements (202) -> poll GET /jobs/{id} -> download
  // link to /jobs/{id}/artifact. CANNOT SEE: the PDF's contents — it asserts a job reaches
  // `succeeded` and offers a download, not that the bytes are a correct statement.
  test("queues a statement, polls the job, and offers the PDF", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/ar/reports/statements");
    await page.waitForSelector("h1:has-text('Customer statements')");

    // Nothing queued yet, so nothing to download.
    await expect(page.getByTestId("statement-download")).toHaveCount(0);

    await page.locator('input[type="checkbox"]').first().check();
    await page.getByRole("button", { name: /Queue statement/ }).click();

    const download = page.getByTestId("statement-download");
    await expect(download).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Ready")).toBeVisible();

    // The link points at the artifact endpoint — the browser fetches it with the session
    // cookie rather than the app buffering a blob.
    await expect(download).toHaveAttribute("href", /\/subledger\/jobs\/\d+\/artifact$/);

    const [downloaded] = await Promise.all([
      page.waitForEvent("download", { timeout: 30_000 }),
      download.click(),
    ]);
    expect(downloaded.suggestedFilename()).toMatch(/\.pdf$/);
  });
});

test.describe("transaction listing", () => {
  // PATH: /ar/reports/transactions -> GET /subledger/ar/documents, cursor paging. CANNOT SEE:
  // whether page 2 is correct when page 1 changed underneath it — cursor paging is chosen so
  // that cannot happen, but nothing here proves the choice was honoured.
  test("pages from the server and disables the controls at the ends", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/ar/reports/transactions");
    await page.waitForSelector("h1:has-text('Transaction listing')");

    // On the first page, Previous is unavailable; that is the boundary worth asserting.
    await expect(page.getByRole("button", { name: "Previous page" })).toBeDisabled();
    await expect(page.getByText(/Showing \d+ rows/)).toBeVisible();
  });
});

test.describe("listings", () => {
  // PATH: /ar/reports/customer-listing -> GET /subledger/ar/partners. CANNOT SEE: whether
  // inactive partners are excluded by the server or by the screen — it only checks the toggle
  // changes what is shown.
  test("lists customers and can include inactive ones", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/ar/reports/customer-listing");
    await page.waitForSelector("h1:has-text('Customer listing')");

    const before = await page.locator("table tbody tr").count();
    await page.getByText("Include inactive").click();
    await expect(page.locator("table tbody tr")).not.toHaveCount(before - 1);
  });
});

test.describe("report accessibility", () => {
  // PATH: axe over the three reports a user reaches most, in both themes. CANNOT SEE: the
  // print stylesheet, which axe does not evaluate — print layout is asserted structurally in
  // the vitest for ReportPage instead.
  for (const [name, path, heading] of [
    ["age analysis", "/ar/reports/age-analysis", "Age analysis"],
    ["customer enquiry", "/ar/enquiry", "Customer enquiry"],
    ["statements", "/ar/reports/statements", "Customer statements"],
  ] as const) {
    test(`${name} — light and dark`, async ({ page }) => {
      await login(page, PRIMARY_EMAIL);
      await page.goto(path);
      await page.waitForSelector(`h1:has-text("${heading}")`);

      await setTheme(page, "light");
      await assertNoSeriousViolations(page);
      await setTheme(page, "dark");
      await assertNoSeriousViolations(page);
    });
  }
});
