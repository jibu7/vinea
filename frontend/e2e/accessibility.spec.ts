import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { PRIMARY_EMAIL, login } from "./support/fixtures";

async function setTheme(page: Page, theme: "light" | "dark"): Promise<void> {
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
  await page.waitForTimeout(100);
}

async function assertNoSeriousViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
}

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
