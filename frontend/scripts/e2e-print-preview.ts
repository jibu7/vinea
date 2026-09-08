import { chromium } from "@playwright/test";

/**
 * Captures one print-preview screenshot per report (Trial Balance, Account Transactions,
 * Chart of Accounts) using Playwright's print media emulation, so the @media print layout
 * (company name, as-of date, filter summary, screen chrome hidden) can be evidenced without
 * an actual OS print dialog.
 */
async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  page.on("pageerror", (err) => console.log("[pageerror]", err.message));

  await page.goto("http://localhost:3000/", { waitUntil: "networkidle" });
  await page.fill('input[type="email"]', "aline@rugariwines.rw");
  await page.fill('input[type="password"]', "SuperSecret123!");
  await page.click('button[type="submit"]');
  await page.waitForURL("http://localhost:3000/");
  await page.waitForSelector("text=Good morning");
  console.log("login -> OK");

  // --- Trial Balance Report: filter by project so the print header shows a filter summary ---
  await page.goto("http://localhost:3000/gl/reports/trial-balance", { waitUntil: "networkidle" });
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
  await page.goto("http://localhost:3000/gl/reports/account-transactions", { waitUntil: "networkidle" });
  await page.waitForSelector("text=Account Transactions Report");
  await page.locator("button:has-text('Choose an account…')").click();
  await page.waitForTimeout(200);
  await page.keyboard.type("Salaries");
  await page.waitForTimeout(300);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(500);
  await page.waitForSelector("text=Opening balance");
  await page.emulateMedia({ media: "print" });
  await page.waitForTimeout(200);
  await page.screenshot({ path: "screenshots/print-02-account-transactions.png", fullPage: true });
  console.log("Account Transactions print preview captured");
  await page.emulateMedia({ media: "screen" });

  // --- Chart of Accounts Report: filter to a class so the summary/filter state is visible ---
  await page.goto("http://localhost:3000/gl/reports/chart-of-accounts", { waitUntil: "networkidle" });
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
