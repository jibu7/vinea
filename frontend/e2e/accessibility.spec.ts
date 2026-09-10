import { test } from "@playwright/test";
import { PRIMARY_EMAIL, assertNoSeriousViolations, login, setTheme } from "./support/fixtures";

test.describe("accessibility: no serious/critical axe violations", () => {
  // PATH: sign in, then axe over the dashboard in each theme.
  // CANNOT SEE: anything below serious/critical, and anything axe cannot test
  // automatically — focus order, and whether a label actually describes its control.
  test("dashboard — light and dark", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.waitForSelector("text=Good morning");

    await setTheme(page, "light");
    await assertNoSeriousViolations(page);

    await setTheme(page, "dark");
    await assertNoSeriousViolations(page);
  });

  // PATH: /gl/journal-batches/new, axe over the LineGrid in each theme.
  // CANNOT SEE: the grid mid-interaction — the popovers, the combobox listbox and the
  // inline row errors are all closed here, and each is its own accessibility surface.
  test("journal batch workspace — light and dark", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/gl/journal-batches/new");
    await page.waitForSelector("text=Journal Batch");

    await setTheme(page, "light");
    await assertNoSeriousViolations(page);

    await setTheme(page, "dark");
    await assertNoSeriousViolations(page);
  });

  // PATH: /maintenance/chart-of-accounts, axe over the tree in each theme.
  // CANNOT SEE: the account editor dialog, which is where this screen's form controls
  // actually live.
  test("chart of accounts — light and dark", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/maintenance/chart-of-accounts");
    await page.waitForSelector("text=Chart of accounts");

    await setTheme(page, "light");
    await assertNoSeriousViolations(page);

    await setTheme(page, "dark");
    await assertNoSeriousViolations(page);
  });
});
