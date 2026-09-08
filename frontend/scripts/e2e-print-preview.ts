import { chromium } from "@playwright/test";
import { PASSWORD, PRIMARY_EMAIL } from "../e2e/support/fixtures";

/**
 * Captures one print-preview screenshot per report (Trial Balance, Account Transactions,
 * Chart of Accounts) using Playwright's print media emulation, so the @media print layout
 * (company name, as-of date, filter summary, screen chrome hidden) can be evidenced without
 * an actual OS print dialog.
 *
 * BASE_URL/EMAIL/PASSWORD default to the same `seed_e2e.py` fixture the rest of the e2e suite
 * uses, so this runs unattended in CI; override via env vars to point at a different stack.
 */
const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000";
const EMAIL = process.env.E2E_LOGIN_EMAIL ?? PRIMARY_EMAIL;
const PASSWORD_ENV = process.env.E2E_LOGIN_PASSWORD ?? PASSWORD;

async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, baseURL: BASE_URL });
  const page = await context.newPage();
  page.on("pageerror", (err) => console.log("[pageerror]", err.message));

  // `next dev`'s HMR websocket never idles, so `waitUntil: "networkidle"` hangs — go straight
  // to /login (no client-side redirect to race) instead of relying on root `/` to redirect.
  await page.goto("/login");
  await page.fill('input[type="email"]', EMAIL);
  await page.fill('input[type="password"]', PASSWORD_ENV);
  await page.click('button[type="submit"]');
  await page.waitForURL("/");
  await page.waitForSelector("text=Good morning");
  console.log("login -> OK");

  // --- Trial Balance Report: filter by project so the print header shows a filter summary ---
  await page.goto("/gl/reports/trial-balance");
  await page.waitForSelector("text=Trial Balance Report");
  const projectSelect = page.locator("select").nth(1);
  const projectOptions = await projectSelect.locator("option").allTextContents();
  if (projectOptions.length > 1) {
    await projectSelect.selectOption({ index: 1 });
    await page.waitForTimeout(300);
  }
  await page.emulateMedia({ media: "print" });
  await page.waitForTimeout(200);
  await page.screenshot({ path: "screenshots/print-01-trial-balance.png", fullPage: true });
  console.log("Trial Balance print preview captured");
  await page.emulateMedia({ media: "screen" });

  // --- Account Transactions Report: select an account so the print header shows the range ---
  await page.goto("/gl/reports/account-transactions");
  await page.waitForSelector("text=Account Transactions Report");
  await page.locator("button:has-text('Choose an account…')").click();
  // The account list loads async and cmdk renders zero [cmdk-item] nodes for a still-empty/
  // unmatched list — wait for at least one unfiltered item before typing, same as
  // e2e/support/fixtures.ts's pickAccount().
  const searchInput = page.locator("[cmdk-input]");
  await searchInput.waitFor({ state: "visible" });
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type("Salaries");
  await page.locator('[cmdk-item]:has-text("Salaries")').first().waitFor({ state: "visible" });
  await page.keyboard.press("Enter");
  await searchInput.waitFor({ state: "hidden" });
  await page.waitForSelector("text=Opening balance");
  await page.emulateMedia({ media: "print" });
  await page.waitForTimeout(200);
  await page.screenshot({ path: "screenshots/print-02-account-transactions.png", fullPage: true });
  console.log("Account Transactions print preview captured");
  await page.emulateMedia({ media: "screen" });

  // --- Chart of Accounts Report: filter to a class so the summary/filter state is visible ---
  await page.goto("/gl/reports/chart-of-accounts");
  await page.waitForSelector("text=Chart of Accounts");
  await page.locator("select").first().selectOption("asset");
  await page.waitForTimeout(200);
  await page.emulateMedia({ media: "print" });
  await page.waitForTimeout(200);
  await page.screenshot({ path: "screenshots/print-03-chart-of-accounts.png", fullPage: true });
  console.log("Chart of Accounts print preview captured");
  await page.emulateMedia({ media: "screen" });

  await browser.close();
  console.log("PRINT PREVIEW CAPTURE COMPLETE");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
