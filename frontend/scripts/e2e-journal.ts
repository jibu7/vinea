import { chromium } from "@playwright/test";

async function main() {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  page.on("pageerror", (err) => console.log("[pageerror]", err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error") console.log("[console.error]", msg.text());
  });

  // Login
  await page.goto("http://localhost:3000/", { waitUntil: "networkidle" });
  await page.fill('input[type="email"]', "aline@rugariwines.rw");
  await page.fill('input[type="password"]', "SuperSecret123!");
  await page.click('button[type="submit"]');
  await page.waitForURL("http://localhost:3000/");
  await page.waitForSelector("text=Good morning");
  console.log("login -> OK");

  // New journal entry
  await page.goto("http://localhost:3000/gl/journal-batches/new", { waitUntil: "networkidle" });
  await page.waitForSelector("text=Journal Batch");
  await page.screenshot({ path: "screenshots/e2e-je-empty.png", fullPage: true });

  await page.fill('input[placeholder="September payroll accrual"]', "E2E test payroll accrual");

  // Line 1: account combobox
  const accountButtons = page.locator('table button:has-text("Search accounts")').or(page.locator('table button', { hasText: "Account…" }));
  await page.locator("table tbody tr").nth(0).locator("button").first().click();
  await page.keyboard.type("Salaries");
  await page.waitForTimeout(300);
  await page.screenshot({ path: "screenshots/e2e-je-combobox.png" });
  await page.keyboard.press("Enter");
  await page.waitForTimeout(200);

  // Debit amount for line 1
  const row0Debit = page.locator("table tbody tr").nth(0).locator('input[placeholder="0"]').first();
  await row0Debit.fill("50000");

  // Line 2: click "+ Add line"
  await page.click("text=+ Add line");
  await page.waitForTimeout(200);
  await page.locator("table tbody tr").nth(1).locator("button").first().click();
  await page.keyboard.type("PAYE");
  await page.waitForTimeout(300);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(200);
  const row1Credit = page.locator("table tbody tr").nth(1).locator('input[placeholder="0"]').nth(1);
  await row1Credit.fill("50000");

  await page.waitForTimeout(300);
  await page.screenshot({ path: "screenshots/e2e-je-balanced.png", fullPage: true });

  const postBtn = page.locator('button:has-text("Post (Ctrl+Enter)")');
  console.log("post disabled?", await postBtn.isDisabled());
  await postBtn.click();
  await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 10000 });
  console.log("posted -> ", page.url());
  await page.waitForSelector("text=Posted");
  await page.screenshot({ path: "screenshots/e2e-je-posted.png", fullPage: true });

  // Reverse it
  await page.click('button:has-text("Reverse entry")');
  await page.fill('input[placeholder="Duplicate posting"]', "E2E test reversal");
  await page.screenshot({ path: "screenshots/e2e-je-reverse-dialog.png" });
  await page.click('button:has-text("Reverse"):not(:has-text("entry"))');
  await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 10000 });
  await page.waitForSelector("text=Reversal of entry");
  console.log("reversed -> ", page.url());
  await page.screenshot({ path: "screenshots/e2e-je-reversal-view.png", fullPage: true });

  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
