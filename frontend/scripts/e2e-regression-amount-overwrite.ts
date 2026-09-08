import { chromium } from "@playwright/test";
import { PASSWORD, PRIMARY_EMAIL, pickAccount } from "../e2e/support/fixtures";

/**
 * Regression for the FRw 1,000,065,000 incident: a debit/credit/amount cell that already
 * held a value (e.g. a restored draft from an earlier rejected post) must have that value
 * fully REPLACED when focused and overwritten — never appended to. Before the fix, focusing
 * a cell holding "10000" and filling "65000" produced "1000065000" (a literal string
 * concatenation of the two values), because the cell did not select its existing content on
 * focus. This script proves the fix for both automated fill() and real keystroke typing, and
 * never posts anything (so it leaves no journal entries behind). It's also covered as a fast
 * jsdom unit test (src/design/components/line-grid.test.tsx) that runs on every `npm run
 * test`; this script additionally proves it end-to-end against a real browser and backend.
 *
 * BASE_URL/EMAIL/PASSWORD default to the same `seed_e2e.py` fixture the rest of the e2e suite
 * uses, so this runs unattended in CI; override via env vars to point at a different stack.
 */
const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000";
const EMAIL = process.env.E2E_LOGIN_EMAIL ?? PRIMARY_EMAIL;
const PASSWORD_ENV = process.env.E2E_LOGIN_PASSWORD ?? PASSWORD;

async function main() {
  const browser = await chromium.launch();
  const page = await (
    await browser.newContext({ viewport: { width: 1440, height: 900 }, baseURL: BASE_URL })
  ).newPage();
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

  await page.goto("/gl/journal-batches/new");
  await page.waitForSelector("text=Journal Batch");

  // Select an account on line 1 so the debit cell is a real, enabled input.
  await pickAccount(page, 0, "6100");

  const debitCell = page.locator("table tbody tr").nth(0).locator('input[placeholder="0"]').first();
  const descCell = page.locator("table tbody tr").nth(0).locator('input[placeholder="Line description"]');

  // --- Case 1: fill() into a cell that already holds a stale value ---
  await debitCell.fill("999999999");
  await page.waitForTimeout(100);
  await debitCell.fill("65000");
  await descCell.click(); // move focus to a different cell so the grid reformats with commas
  const afterFill = await debitCell.inputValue();
  console.log("Case 1 (fill over stale value) ->", afterFill);
  if (afterFill !== "65,000") {
    throw new Error(`REGRESSION: expected "65,000" after overwrite, got "${afterFill}"`);
  }
  console.log("Case 1: PASS — overwrite replaced the stale value, no concatenation");

  // --- Case 2: real keystroke typing into a cell that already holds a value. Focus must
  // move AWAY first so the next click is a genuine focus transition (a click on an
  // already-focused element fires no "focus" event, so the auto-select would never run). ---
  await debitCell.fill("42000");
  await descCell.click();
  await page.waitForTimeout(100);
  await debitCell.click(); // fresh focus transition — exercises the onFocus auto-select path
  await page.keyboard.type("65000");
  await descCell.click(); // move focus away again to force reformatting
  const afterType = await debitCell.inputValue();
  console.log("Case 2 (click + type over stale value) ->", afterType);
  if (afterType !== "65,000") {
    throw new Error(`REGRESSION: expected "65,000" after click+type overwrite, got "${afterType}"`);
  }
  console.log("Case 2: PASS — click-to-focus selected the stale value so typing replaced it");

  // Never post; leave no journal entry behind. Clear any autosaved draft too.
  await page.evaluate(() => localStorage.clear());
  await browser.close();
  console.log("REGRESSION SUITE PASSED — 65,000 in never becomes 1,000,065,000 out");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
