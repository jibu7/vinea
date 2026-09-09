import { test } from "@playwright/test";
import { PRIMARY_EMAIL, assertNoSeriousViolations, login, setTheme } from "./support/fixtures";

test.describe("accessibility: no serious/critical axe violations", () => {
  test("dashboard — light and dark", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.waitForSelector("text=Good morning");

    await setTheme(page, "light");
    await assertNoSeriousViolations(page);

    await setTheme(page, "dark");
    await assertNoSeriousViolations(page);
  });

  test("journal batch workspace — light and dark", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/gl/journal-batches/new");
    await page.waitForSelector("text=Journal Batch");

    await setTheme(page, "light");
    await assertNoSeriousViolations(page);

    await setTheme(page, "dark");
    await assertNoSeriousViolations(page);
  });

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
