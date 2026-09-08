import type { Page } from "@playwright/test";

/** Must match backend/app/scripts/seed_e2e.py — run once before the suite. `.example` (RFC
 * 2606), not `.test`: email-validator rejects `.test`/`.invalid`/`.localhost` as reserved,
 * which surfaced as every login POST 422ing even though the seeded user was real. */
export const PRIMARY_EMAIL = "e2e.primary@vinea.example";
export const PRIMARY_COMPANY = "Rugari Wines E2E";
export const SECONDARY_EMAIL = "e2e.secondary@vinea.example";
export const SECONDARY_COMPANY = "Kivu Traders E2E";
export const PASSWORD = "E2E-Sup3rSecret!1";

export const API_URL = process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000";
export const API_BASE = `${API_URL}/api/v1`;

/** Two ordinary, non-control postable accounts seeded by `seed_rwanda` — safe two-line pair. */
export const DEBIT_ACCOUNT_CODE = "6100"; // Salaries & Wages
export const CREDIT_ACCOUNT_CODE = "2300"; // Accrued Expenses

export async function login(page: Page, email: string = PRIMARY_EMAIL): Promise<void> {
  // `next dev`'s HMR websocket never idles, so `waitUntil: "networkidle"` here hangs to the
  // navigation timeout — go straight to /login (no client-side redirect to race) and let
  // page.fill's own auto-wait cover hydration instead.
  await page.goto("/login");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL("/");
  await page.waitForSelector("text=Good morning");
}

/** Fills the account combobox in a LineGrid row via its typeahead, by account code. `cmdk`
 * (the combobox's internal list) marks its own DOM nodes with `cmdk-input`/`cmdk-item`
 * attributes, so wait on those instead of sleeping — this is only one field, but combobox
 * cells throughout the app share the same underlying `Combobox` component and DOM shape. */
export async function pickAccount(page: Page, rowIndex: number, code: string): Promise<void> {
  await page.locator("table tbody tr").nth(rowIndex).locator("button").first().click();
  const searchInput = page.locator("[cmdk-input]");
  await searchInput.waitFor({ state: "visible" });
  await page.keyboard.type(code);
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.press("Enter");
  await searchInput.waitFor({ state: "hidden" });
}

/** Resolves a GL account id by code through the API — used where a test drives the API
 * directly (e.g. the idempotency replay check) rather than the LineGrid combobox. */
export async function accountIdByCode(page: Page, code: string): Promise<number> {
  const res = await page.request.get(`${API_BASE}/gl/accounts`);
  if (!res.ok()) throw new Error(`GET /gl/accounts failed: ${res.status()}`);
  const accounts: Array<{ id: number; code: string }> = await res.json();
  const account = accounts.find((a) => a.code === code);
  if (!account) throw new Error(`No seeded account with code ${code}`);
  return account.id;
}
