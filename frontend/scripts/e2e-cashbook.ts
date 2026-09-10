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
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  page.on("pageerror", (err) => console.log("[pageerror]", err.message));

  await page.goto("http://localhost:3000/", { waitUntil: "networkidle" });
  await page.fill('input[type="email"]', EMAIL);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL("http://localhost:3000/");
  await page.waitForSelector("text=Good morning");

  await page.goto("http://localhost:3000/gl/cashbook-batches/new", { waitUntil: "networkidle" });
  await page.waitForSelector("text=Cashbook Batch");

  // Bank/cash account combobox
  await page.locator("text=Select account…").click();
  await page.keyboard.type("Bank");
  await page.waitForTimeout(300);
  await page.keyboard.press("Enter");

  await page.fill('input[placeholder="MTN MoMo settlement"]', "E2E cash sale");
  await page.screenshot({ path: "screenshots/e2e-cb-header.png", fullPage: true });

  // Line: account combobox + amount
  await page.locator("table tbody tr").nth(0).locator("button").first().click();
  await page.keyboard.type("Sales");
  await page.waitForTimeout(300);
  await page.keyboard.press("Enter");
  await page.locator("table tbody tr").nth(0).locator('input[placeholder="0"]').fill("25000");

  await page.waitForTimeout(300);
  await page.screenshot({ path: "screenshots/e2e-cb-filled.png", fullPage: true });

  const postBtn = page.locator('button:has-text("Post (Ctrl+Enter)")');
  console.log("cb post disabled?", await postBtn.isDisabled());
  await postBtn.click();
  await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 10000 });
  console.log("cb posted -> ", page.url());
  await page.waitForSelector("text=Posted");
  await page.screenshot({ path: "screenshots/e2e-cb-posted.png", fullPage: true });

  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
