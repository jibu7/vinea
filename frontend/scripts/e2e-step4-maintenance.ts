import { chromium } from "@playwright/test";

// Credentials from the environment, never written down here. This script signs in to a
// hand-made dev tenant rather than the `seed_e2e` fixtures, so it takes both from env:
//   E2E_LOGIN_EMAIL=... E2E_PASSWORD=... npx tsx <this file>
const EMAIL = requireEnv("E2E_LOGIN_EMAIL");
const PASSWORD = requireEnv("E2E_PASSWORD");

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set — this script signs in as a real user.`);
  return value;
}

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
  await page.fill('input[type="email"]', EMAIL);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL("http://localhost:3000/");
  await page.waitForSelector("text=Good morning");
  console.log("   Logged in successfully.");

  // --- Step 4.1: Chart of Accounts Tree ---
  console.log("2. Verifying Chart of Accounts tree...");
  await page.goto("http://localhost:3000/maintenance/chart-of-accounts", { waitUntil: "networkidle" });
  await page.waitForSelector("text=Chart of accounts");
  await page.waitForSelector("text=Current Assets");
  await page.waitForSelector("text=Salaries & Wages");
  console.log("   COA tree nodes loaded.");
  await page.screenshot({ path: "screenshots/20-coa-tree-light.png", fullPage: true });

  // Class filtering
  await page.click('button:has-text("Expense")');
  await page.waitForTimeout(300);
  await page.waitForSelector("text=Operating Expenses");
  await page.screenshot({ path: "screenshots/21-coa-tree-expense-filter.png", fullPage: true });

  // Reset filter
  await page.click('button:has-text("All")');
  await page.waitForTimeout(200);

  // Attempt to deactivate a control account (1120 Bank Account) -> inline refusal
  console.log("   Testing inline refusal on deactivating control account...");
  const bankRow = page.locator('[data-account-code="1120"]').locator("button:has-text('Active')");
  await bankRow.click();
  await page.waitForTimeout(600);
  await page.waitForSelector("text=Control accounts cannot be deactivated");
  console.log("   Inline error on deactivating control account: PASS");
  await page.screenshot({ path: "screenshots/21b-coa-deactivate-inline-error.png", fullPage: true });

  // --- Step 4.2: Rename Account & History Notes ---
  console.log("3. Verifying Rename Account & History notes...");
  await page.goto("http://localhost:3000/maintenance/rename-account", { waitUntil: "networkidle" });
  await page.waitForSelector("text=Rename account");

  // Select an account from combobox: Office Supplies (6500)
  await page.locator("button:has-text('Choose account…')").click();
  await page.waitForTimeout(200);
  await page.keyboard.type("Office Supplies");
  await page.waitForTimeout(300);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(400);

  await page.waitForSelector("text=Office Supplies");
  console.log("   Account selected in Rename screen.");
  await page.screenshot({ path: "screenshots/22-rename-account-before.png", fullPage: true });

  // Rename code to a new unique code and then restore through the API so both directions are audited
  const currentVal = await page.locator('input[disabled]').inputValue();
  const targetCode = currentVal === "6500" ? "6505" : "6500";
  const newCodeInput = page.locator('input[placeholder="e.g. 6150"]');
  await newCodeInput.fill(targetCode);
  await page.waitForTimeout(200);

  const renameBtn = page.locator('button:has-text("Rename")');
  await renameBtn.waitFor({ state: "visible" });
  await renameBtn.click();
  await page.waitForTimeout(600);
  console.log("   Rename submitted.");

  // Verify history section shows rename note
  await page.waitForSelector("text=gl_account.renamed");
  console.log("   gl_account.renamed history note rendered in audit table.");
  await page.screenshot({ path: "screenshots/23-rename-account-after.png", fullPage: true });

  // Restore back to 6500 through the API if not already 6500
  const afterVal = await page.locator('input[disabled]').inputValue();
  if (afterVal !== "6500") {
    await newCodeInput.fill("6500");
    await page.waitForTimeout(200);
    await renameBtn.click();
    await page.waitForTimeout(600);
    console.log("   Restored to 6500 through the API.");
  }

  // --- Step 4.3: Company Details, Periods Timeline, & Defaults ---
  console.log("4. Verifying Company details, Periods timeline, and Defaults...");
  await page.goto("http://localhost:3000/maintenance/company-details", { waitUntil: "networkidle" });
  await page.waitForSelector("text=Company Information");
  await page.screenshot({ path: "screenshots/24-company-details.png", fullPage: true });

  // Switch to Fiscal Years & Periods tab
  await page.click('button:has-text("Fiscal Years & Periods")');
  await page.waitForTimeout(300);
  await page.waitForSelector("text=Fiscal Years");
  await page.waitForSelector("text=Monthly posting control");
  console.log("   Fiscal years and accounting periods timeline verified.");
  await page.screenshot({ path: "screenshots/25-periods-timeline.png", fullPage: true });

  // Switch to General Settings (Defaults) tab
  await page.click('button:has-text("General Settings")');
  await page.waitForTimeout(300);
  await page.waitForSelector("text=Retained earnings account");
  await page.waitForSelector("text=Rounding difference account");
  console.log("   GL defaults verified.");
  await page.screenshot({ path: "screenshots/26-company-defaults.png", fullPage: true });

  // --- Step 4.4: Foreign Currency & Exchange Rates ---
  console.log("5. Verifying Foreign Currency & Exchange rates...");
  await page.goto("http://localhost:3000/maintenance/currencies", { waitUntil: "networkidle" });
  await page.waitForSelector("text=Currencies");
  await page.waitForSelector("text=Exchange Rates");
  await page.waitForSelector("text=Base currency");
  console.log("   Currencies and exchange rates verified.");
  await page.screenshot({ path: "screenshots/27-currencies.png", fullPage: true });

  // --- Step 4.5: Tax Types ---
  console.log("6. Verifying Tax types (with corrected labels)...");
  await page.goto("http://localhost:3000/maintenance/taxes", { waitUntil: "networkidle" });
  await page.waitForSelector("text=Tax types");
  await page.waitForSelector("text=VAT-OUT-18");
  await page.waitForSelector("text=VAT-IN-18");
  await page.waitForSelector("text=Output VAT (sales)");
  await page.waitForSelector("text=Input VAT (purchases)");
  console.log("   Tax types with corrected labels verified.");
  await page.screenshot({ path: "screenshots/28-taxes.png", fullPage: true });

  // --- Step 4.6: Branches ---
  console.log("7. Verifying Branches...");
  await page.goto("http://localhost:3000/maintenance/branches", { waitUntil: "networkidle" });
  await page.waitForSelector("text=Branches");
  await page.waitForSelector("text=Main branch");
  console.log("   Branches verified.");
  await page.screenshot({ path: "screenshots/29-branches.png", fullPage: true });

  // --- Step 4.7: Transaction Types ---
  console.log("8. Verifying Transaction types...");
  await page.goto("http://localhost:3000/maintenance/transaction-types", { waitUntil: "networkidle" });
  await page.waitForSelector("text=Transaction types");
  console.log("   Transaction types verified.");
  await page.screenshot({ path: "screenshots/30-transaction-types.png", fullPage: true });

  // --- Step 4.8: Projects Master ---
  console.log("9. Verifying Projects master...");
  await page.goto("http://localhost:3000/maintenance/projects", { waitUntil: "networkidle" });
  await page.waitForSelector("text=Costing Projects");
  console.log("   Projects master verified.");
  await page.screenshot({ path: "screenshots/31-projects.png", fullPage: true });

  // --- Step 4.9: Administration -> Users & Memberships ---
  console.log("10. Verifying Administration -> Users & memberships...");
  await page.goto("http://localhost:3000/administration/users", { waitUntil: "networkidle" });
  await page.waitForSelector("text=Team Members & Invitations");
  await page.waitForSelector(`text=${EMAIL}`);
  await page.waitForSelector("text=Owner");
  console.log("    Users and memberships verified.");
  await page.screenshot({ path: "screenshots/32-users-and-memberships.png", fullPage: true });

  // Dark mode capture
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
  await page.waitForTimeout(200);
  await page.screenshot({ path: "screenshots/33-users-dark.png", fullPage: true });

  await browser.close();
  console.log("ALL STEP 4 VERIFICATIONS PASSED!");
}

main().catch((err) => {
  console.error("STEP 4 E2E FAILED:", err);
  process.exit(1);
});
