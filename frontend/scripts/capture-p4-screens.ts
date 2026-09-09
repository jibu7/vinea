/**
 * Captures the P4 maintenance screens for the review record: 1440x900, light and dark.
 * Run against the dev stack with `npm run e2e`'s prerequisites already up:
 *   OUT=../docs/screenshots/p4-step-6 npx tsx scripts/capture-p4-screens.ts
 * The read-only shot logs in as the seeded Clerk-role user, so the disabled Save and its
 * `gl:setup_manage` note are real permission state, not a styled mock.
 */
import { chromium, type Page } from "@playwright/test";

const OUT = process.env.OUT ?? "screenshots";
const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const PASSWORD = "E2E-Sup3rSecret!1";
const OWNER = "e2e.primary@vinea.example";
const READONLY = "e2e.readonly@vinea.example";

async function hydrated(page: Page, selector: string) {
  await page.waitForFunction((sel) => {
    const node = document.querySelector(sel);
    return (
      node !== null &&
      Object.keys(node).some((k) => k.startsWith("__reactFiber$") || k.startsWith("__reactProps$"))
    );
  }, selector);
}

async function login(page: Page, email: string) {
  await page.goto(`${BASE}/login`);
  await hydrated(page, "form");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL(`${BASE}/`);
  await page.waitForSelector("text=Good morning");
}

async function shoot(page: Page, name: string, theme: "light" | "dark") {
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/${name}-${theme}.png` });
  console.log("captured", `${name}-${theme}`);
}

async function main() {
  const browser = await chromium.launch();
  const ownerCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ownerCtx.newPage();
  await login(page, OWNER);

  for (const theme of ["light", "dark"] as const) {
    await page.goto(`${BASE}/maintenance/customers`);
    await page.waitForSelector("h1:has-text('Customers')");
    await page.locator('button[aria-label^="Edit "]').first().click();
    await page.getByRole("tab", { name: "AR settings" }).click();
    await page.waitForTimeout(500);
    await shoot(page, "1-customer-ar-settings", theme);
    await page.keyboard.press("Escape");

    await page.goto(`${BASE}/maintenance/suppliers`);
    await page.waitForSelector("h1:has-text('Suppliers')");
    await shoot(page, "2-supplier-master", theme);

    await page.goto(`${BASE}/maintenance/payment-terms`);
    await page.waitForSelector("h1:has-text('Payment terms')");
    await shoot(page, "3-payment-terms", theme);

    await page.goto(`${BASE}/maintenance/ageing-bucket-sets`);
    await page.waitForSelector("h1:has-text('Ageing bucket sets')");
    await page.locator('button[aria-label^="Edit "]').first().click();
    await page.waitForSelector("text=Buckets");
    await page.waitForTimeout(400);
    await shoot(page, "4-bucket-set-editor", theme);
    await page.keyboard.press("Escape");
  }
  await ownerCtx.close();

  const clerkCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const clerk = await clerkCtx.newPage();
  await login(clerk, READONLY);
  for (const theme of ["light", "dark"] as const) {
    await clerk.goto(`${BASE}/maintenance/ar-ap-defaults`);
    await clerk.waitForSelector("h1:has-text('AR/AP defaults')");
    await clerk.getByRole("button", { name: "Save changes" }).scrollIntoViewIfNeeded();
    await clerk.waitForTimeout(400);
    await shoot(clerk, "5-ar-defaults-readonly", theme);
  }
  await clerkCtx.close();
  await browser.close();
}
main();
