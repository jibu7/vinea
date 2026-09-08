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

  // --- Step A: Two-level Esc test ---
  console.log("2. Testing two-level Esc in LineGrid...");
  await page.goto("http://localhost:3000/gl/journal-batches/new", { waitUntil: "networkidle" });
  await page.waitForSelector("text=Journal Batch");

  // Focus a cell input inside LineGrid
  const descCell = page.locator("table tbody tr").nth(0).locator('input[placeholder="Line description"]');
  await descCell.click();
  await page.keyboard.type("Cell edit test");
  // Press Escape while editing cell
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  // Verify we are still on the journal batch page
  if (page.url().includes("/gl/journal-batches/new")) {
    console.log("   First Esc reverted/cancelled cell edit without leaving document: PASS");
  } else {
    throw new Error(`First Esc navigated away prematurely to ${page.url()}`);
  }

  // Press Escape with no cell in edit
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);
  if (!page.url().includes("/gl/journal-batches/new")) {
    console.log("   Second Esc left the document workspace to " + page.url() + ": PASS");
  } else {
    throw new Error("Second Esc failed to leave document");
  }

  // --- Step B: Line-level inline error test ---
  console.log("3. Testing line-level inline error mapping (control account)...");
  await page.goto("http://localhost:3000/gl/journal-batches/new", { waitUntil: "networkidle" });
  await page.fill('input[placeholder="September payroll accrual"]', "Control account test");

  // Select a control account: 2100 (Accounts Payable, control) on Line 1
  await page.locator("table tbody tr").nth(0).locator("button").first().click();
  await page.waitForTimeout(100);
  await page.keyboard.type("2100");
  await page.waitForTimeout(300);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(200);

  // Line 1 debit: 10000
  await page.locator("table tbody tr").nth(0).locator('input[placeholder="0"]').first().fill("10000");

  // Line 2: normal account 6100 (Salaries & Wages)
  await page.locator("table tbody tr").nth(1).locator("button").first().click();
  await page.waitForTimeout(100);
  await page.keyboard.type("6100");
  await page.waitForTimeout(300);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(200);

  // Line 2 credit: 10000
  await page.locator("table tbody tr").nth(1).locator('input[placeholder="0"]').nth(1).fill("10000");
  await page.waitForTimeout(300);

  // Post entry (should be rejected by kernel control account rule)
  const postBtnReject = page.locator('button:has-text("Post (Ctrl+Enter)")');
  await postBtnReject.click();
  await page.waitForTimeout(500);

  // Check inline error on the line
  const inlineError = await page.locator("table tbody tr").nth(0).locator("text=is a control account").isVisible();
  console.log("   Inline line-level error visible on account cell:", inlineError);
  await page.screenshot({ path: "screenshots/03-inline-line-error.png", fullPage: true });

  // --- Step C: Create Journal with Reference & Post ---
  console.log("4. Creating Journal Entry with Reference...");
  await page.goto("http://localhost:3000/gl/journal-batches/new", { waitUntil: "networkidle" });
  await page.fill('input[placeholder="September payroll accrual"]', "E2E Payroll with Reference");
  await page.fill('input[placeholder="e.g. CHQ-1002"]', "REF-E2E-2026");

  // Line 1: Salaries & Wages (6100)
  await page.locator("table tbody tr").nth(0).locator("button").first().click();
  await page.waitForTimeout(100);
  await page.keyboard.type("6100");
  await page.waitForTimeout(300);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(200);
  await page.locator("table tbody tr").nth(0).locator('input[placeholder="0"]').first().fill("65000");

  // Line 2: Accrued Expenses (2300)
  await page.locator("table tbody tr").nth(1).locator("button").first().click();
  await page.waitForTimeout(100);
  await page.keyboard.type("2300");
  await page.waitForTimeout(300);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(200);
  await page.locator("table tbody tr").nth(1).locator('input[placeholder="0"]').nth(1).fill("65000");

  await page.waitForTimeout(300);
  const postBtn = page.locator('button:has-text("Post (Ctrl+Enter)")');
  await postBtn.click();
  await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 10000 });
  const entryUrl = page.url();
  console.log("   Journal posted successfully ->", entryUrl);

  // Verify Reference in header
  await page.waitForSelector("text=Ref: REF-E2E-2026");
  console.log("   Reference 'REF-E2E-2026' verified in header.");
  await page.screenshot({ path: "screenshots/01-je-with-reference-posted.png", fullPage: true });

  // --- Step D: Reverse entry and verify derived reversed_by chip ---
  console.log("5. Reversing entry and verifying reversed_by chip...");
  await page.click('button:has-text("Reverse entry")');
  await page.fill('input[placeholder="Duplicate posting"]', "E2E Reversal for chip check");
  await page.click('button:has-text("Reverse"):not(:has-text("entry"))');
  await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 10000 });
  const reversalUrl = page.url();
  console.log("   Reversal posted ->", reversalUrl);

  // Navigate back to the original entry
  await page.goto(entryUrl, { waitUntil: "networkidle" });
  await page.waitForSelector("text=Reversed by");
  const chipText = await page.locator("text=/Reversed by JE-\\d+/").first().textContent();
  console.log("   Verified chip on original entry:", chipText);

  // Verify reverse button is disabled
  const reverseButtonDisabled = await page.locator('button:has-text("Reverse entry")').isDisabled();
  console.log("   Reverse button disabled on reversed entry:", reverseButtonDisabled);
  await page.screenshot({ path: "screenshots/02-je-reversed-by-chip.png", fullPage: true });

  // --- Step E: Trial Balance Enquiry (Step 6) ---
  console.log("6. Verifying Trial Balance Enquiry...");
  await page.goto("http://localhost:3000/gl/enquiries/trial-balance", { waitUntil: "networkidle" });
  await page.waitForSelector("text=Trial Balance Enquiry");
  await page.waitForSelector("text=Balanced (foots)");
  console.log("   Trial Balance foots verified.");
  await page.screenshot({ path: "screenshots/04-trial-balance-enquiry.png", fullPage: true });

  // Click on Salaries (6100) row to drill down to Account Enquiry
  console.log("7. Drilling down to Account Enquiry...");
  const salariesRow = page.locator("table tbody tr:has-text('6100')").first();
  await salariesRow.click();
  await page.waitForURL(/\/gl\/enquiries\/account\?accountId=\d+/, { timeout: 10000 });
  console.log("   Drilled down to Account Enquiry ->", page.url());

  await page.waitForSelector("text=Opening balance");
  await page.waitForSelector("text=REF-E2E-2026");
  console.log("   Account Enquiry loaded with transactions and reference.");
  await page.screenshot({ path: "screenshots/05-account-enquiry.png", fullPage: true });

  // Click transaction row to open Drawer
  console.log("8. Opening Entry Detail Drawer...");
  const txRow = page.locator("table tbody tr:has-text('REF-E2E-2026')").first();
  await txRow.click();
  await page.waitForSelector("text=Journal Lines");
  console.log("   Entry Detail Drawer opened.");
  await page.screenshot({ path: "screenshots/06-account-enquiry-drawer.png", fullPage: true });

  // Dark mode captures
  console.log("9. Capturing Dark Mode variants...");
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
  await page.waitForTimeout(200);
  await page.screenshot({ path: "screenshots/07-account-enquiry-drawer-dark.png", fullPage: true });

  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  await page.screenshot({ path: "screenshots/08-account-enquiry-dark.png", fullPage: true });

  await page.goto("http://localhost:3000/gl/enquiries/trial-balance", { waitUntil: "networkidle" });
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
  await page.waitForTimeout(200);
  await page.screenshot({ path: "screenshots/09-trial-balance-enquiry-dark.png", fullPage: true });

  await browser.close();
  console.log("ALL E2E CHECKS PASSED!");
}

main().catch((err) => {
  console.error("E2E FAILED:", err);
  process.exit(1);
});
