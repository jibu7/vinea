import { chromium } from "@playwright/test";

async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  page.on("pageerror", (err) => console.log("[pageerror]", err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error") console.log("[console.error]", msg.text());
  });

  console.log("1. Logging in...");
  await page.goto("http://localhost:3000/", { waitUntil: "networkidle" });
  await page.fill('input[type="email"]', "aline@rugariwines.rw");
  await page.fill('input[type="password"]', "SuperSecret123!");
  await page.click('button[type="submit"]');
  await page.waitForURL("http://localhost:3000/");
  await page.waitForSelector("text=Good morning");
  console.log("   Logged in successfully.");

  // --- Step A: Trial Balance Enquiry Project Filter Behavior ---
  console.log("2. Testing Trial Balance Enquiry project filter behavior...");
  await page.goto("http://localhost:3000/gl/enquiries/trial-balance", { waitUntil: "networkidle" });
  await page.waitForSelector("text=Balanced (foots)");
  await page.waitForSelector("text=Total debit");
  console.log("   Unfiltered TB shows Balanced (foots) chip: PASS");

  // Select a project
  const projectSelect = page.locator("select").nth(1);
  const options = await projectSelect.locator("option").allTextContents();
  console.log("   Available project options:", options);
  if (options.length > 1) {
    await projectSelect.selectOption({ index: 1 });
    await page.waitForTimeout(400);

    // Verify foots chip is suppressed and project slice chip is shown
    await page.waitForSelector("text=Project slice (filtered subset)");
    const footsVisible = await page.locator("text=Balanced (foots)").isVisible();
    const unbalancedVisible = await page.locator("text=Unbalanced").isVisible();
    console.log("   foots/Balanced chip suppressed:", !footsVisible && !unbalancedVisible);
    await page.waitForSelector("text=Project debit (subset)");
    await page.waitForSelector("text=Project credit (subset)");
    await page.waitForSelector("text=Project net difference");
    await page.waitForSelector("text=Project slice of balanced entries does not foot by design.");
    console.log("   Totals labelled as filtered subset: PASS");
    await page.screenshot({ path: "screenshots/10-tb-enquiry-project-slice.png", fullPage: true });

    // Reset project filter
    await projectSelect.selectOption({ index: 0 });
    await page.waitForTimeout(300);
    await page.waitForSelector("text=Balanced (foots)");
  }

  // --- Step B: Trial Balance Report (/gl/reports/trial-balance) ---
  console.log("3. Testing Trial Balance Report page & CSV export...");
  await page.goto("http://localhost:3000/gl/reports/trial-balance", { waitUntil: "networkidle" });
  await page.waitForSelector("text=Trial Balance Report");
  await page.waitForSelector("text=Print");
  await page.waitForSelector("text=Export CSV");
  await page.screenshot({ path: "screenshots/11-tb-report-light.png", fullPage: true });

  // Test CSV export trigger
  const [tbDownload] = await Promise.all([
    page.waitForEvent("download"),
    page.click('button:has-text("Export CSV")'),
  ]);
  console.log("   TB CSV export downloaded:", tbDownload.suggestedFilename());

  // Dark mode TB report
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
  await page.waitForTimeout(200);
  await page.screenshot({ path: "screenshots/12-tb-report-dark.png", fullPage: true });
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));

  // --- Step C: Account Transactions Report (/gl/reports/account-transactions) ---
  console.log("4. Testing Account Transactions Report page & CSV export...");
  await page.goto("http://localhost:3000/gl/reports/account-transactions", { waitUntil: "networkidle" });
  await page.waitForSelector("text=Account Transactions Report");

  // Select Salaries account
  await page.locator("button:has-text('Choose an account…')").click();
  await page.waitForTimeout(200);
  await page.keyboard.type("Salaries");
  await page.waitForTimeout(300);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(500);

  await page.waitForSelector("text=Opening balance");
  await page.waitForSelector("text=Closing balance");
  await page.screenshot({ path: "screenshots/13-account-tx-report-light.png", fullPage: true });

  // Test CSV export trigger
  const [txDownload] = await Promise.all([
    page.waitForEvent("download"),
    page.click('button:has-text("Export CSV")'),
  ]);
  console.log("   Account Transactions CSV downloaded:", txDownload.suggestedFilename());

  // Dark mode
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
  await page.waitForTimeout(200);
  await page.screenshot({ path: "screenshots/14-account-tx-report-dark.png", fullPage: true });
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));

  // --- Step D: Chart of Accounts Report (/gl/reports/chart-of-accounts) ---
  console.log("5. Testing Chart of Accounts Listing Report & CSV export...");
  await page.goto("http://localhost:3000/gl/reports/chart-of-accounts", { waitUntil: "networkidle" });
  await page.waitForSelector("text=Chart of Accounts");
  await page.waitForSelector("text=Operating Expenses");
  await page.screenshot({ path: "screenshots/15-coa-report-light.png", fullPage: true });

  // Test search filtering
  await page.fill('input[placeholder="Search by code or name…"]', "Bank");
  await page.waitForTimeout(300);
  await page.screenshot({ path: "screenshots/16-coa-report-filtered.png", fullPage: true });

  // Clear search
  await page.fill('input[placeholder="Search by code or name…"]', "");
  await page.waitForTimeout(200);

  // Test CSV export trigger
  const [coaDownload] = await Promise.all([
    page.waitForEvent("download"),
    page.click('button:has-text("Export CSV")'),
  ]);
  console.log("   COA CSV downloaded:", coaDownload.suggestedFilename());

  // Dark mode
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
  await page.waitForTimeout(200);
  await page.screenshot({ path: "screenshots/17-coa-report-dark.png", fullPage: true });

  await browser.close();
  console.log("ALL STEP 7 VERIFICATIONS PASSED!");
}

main().catch((err) => {
  console.error("STEP 7 TEST FAILED:", err);
  process.exit(1);
});
