import type { Page } from "@playwright/test";

/** Must match backend/app/scripts/seed_e2e.py — run once before the suite. `.example` (RFC
 * 2606), not `.test`: email-validator rejects `.test`/`.invalid`/`.localhost` as reserved,
 * which surfaced as every login POST 422ing even though the seeded user was real. */
export const PRIMARY_EMAIL = "e2e.primary@vinea.example";
export const PRIMARY_COMPANY = "Rugari Wines E2E";
export const SECONDARY_EMAIL = "e2e.secondary@vinea.example";
export const SECONDARY_COMPANY = "Kivu Traders E2E";
/** Clerk role in PRIMARY_COMPANY: `*:reports_view` only, no setup or posting rights. */
export const READONLY_EMAIL = "e2e.readonly@vinea.example";
export const PASSWORD = "E2E-Sup3rSecret!1";

export const API_URL = process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000";
export const API_BASE = `${API_URL}/api/v1`;

/** Two ordinary, non-control postable accounts seeded by `seed_rwanda` — safe two-line pair. */
export const DEBIT_ACCOUNT_CODE = "6100"; // Salaries & Wages
export const CREDIT_ACCOUNT_CODE = "2300"; // Accrued Expenses

/** Resolves once React has hydrated the form — i.e. `onSubmit` is actually attached.
 *
 * `page.fill`/`page.click` auto-wait for the *element*, not for hydration, so a click that
 * lands first submits the form natively: the browser navigates to `/login?email=…&password=…`
 * and the page comes back with empty inputs and no session. React tags every hydrated DOM
 * node with `__reactFiber$…`/`__reactProps$…` keys, so their presence on the form is the
 * signal. (This race was always here; it only became deterministic when P4 step 6 grew the
 * next-intl message payload and, with it, the time to hydrate.) */
async function waitForHydration(page: Page, selector: string): Promise<void> {
  await page.waitForFunction((sel) => {
    const node = document.querySelector(sel);
    return (
      node !== null &&
      Object.keys(node).some((key) => key.startsWith("__reactFiber$") || key.startsWith("__reactProps$"))
    );
  }, selector);
}

export async function login(page: Page, email: string = PRIMARY_EMAIL): Promise<void> {
  // `next dev`'s HMR websocket never idles, so `waitUntil: "networkidle"` here hangs to the
  // navigation timeout — go straight to /login (no client-side redirect to race) and wait on
  // hydration explicitly instead.
  await page.goto("/login");
  await waitForHydration(page, "form");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL("/");
  await page.waitForSelector("text=Good morning");
}

/** Fills the account combobox in a LineGrid row via its typeahead, by account code. `cmdk`
 * (the combobox's internal list) marks its own DOM nodes with `cmdk-input`/`cmdk-item`
 * attributes, so wait on those instead of sleeping — this is only one field, but combobox
 * cells throughout the app share the same underlying `Combobox` component and DOM shape.
 * The account list itself loads async (a separate query from the page shell), so the popover
 * can open before it has any options — wait for at least one *unfiltered* item first, or
 * typing into a still-empty list can never produce a match. */
export async function pickAccount(page: Page, rowIndex: number, code: string): Promise<void> {
  await page.locator("table tbody tr").nth(rowIndex).locator("button").first().click();
  const searchInput = page.locator("[cmdk-input]");
  await searchInput.waitFor({ state: "visible" });
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(code);
  await page.locator(`[cmdk-item]:has-text("${code}")`).first().waitFor({ state: "visible" });
  await page.keyboard.press("Enter");
  await searchInput.waitFor({ state: "hidden" });
}

export interface FetchResult {
  status: number;
  ok: boolean;
  json: unknown;
}

/** Drives the API from *inside* the page via its own `fetch`, instead of `page.request` — the
 * latter has its own cookie jar synced from the browser context, but a real login redirect
 * (POST /auth/login sets cookies via the page's own `fetch`) didn't reliably show up there in
 * practice (GET /gl/accounts came back 401 immediately after a login that had already rendered
 * "Good morning"). Running fetch through `page.evaluate` uses the exact mechanism the app's own
 * code already relies on (`credentials: "include"`), so it can't diverge from it. */
export async function pageFetch(
  page: Page,
  path: string,
  init: { method?: string; body?: unknown; headers?: Record<string, string> } = {},
): Promise<FetchResult> {
  return page.evaluate(
    async ({ url, method, body, headers }) => {
      const res = await fetch(url, {
        method,
        credentials: "include",
        headers: body !== undefined ? { "Content-Type": "application/json", ...headers } : headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
      const json = await res.json().catch(() => null);
      return { status: res.status, ok: res.ok, json };
    },
    {
      url: `${API_BASE}${path}`,
      method: init.method ?? "GET",
      body: init.body,
      headers: init.headers ?? {},
    },
  );
}

/** Resolves a GL account id by code through the API — used where a test drives the API
 * directly (e.g. the idempotency replay check) rather than the LineGrid combobox. */
export async function accountIdByCode(page: Page, code: string): Promise<number> {
  const res = await pageFetch(page, "/gl/accounts");
  if (!res.ok) throw new Error(`GET /gl/accounts failed: ${res.status}`);
  const accounts = res.json as Array<{ id: number; code: string }>;
  const account = accounts.find((a) => a.code === code);
  if (!account) throw new Error(`No seeded account with code ${code}`);
  return account.id;
}

/** Flips the theme and waits out the `transition-colors` on every themed element. Without
 * the settle, axe samples mid-transition and reports contrast against interpolated colors
 * that are never actually painted at rest. */
export async function setTheme(page: Page, theme: "light" | "dark"): Promise<void> {
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
  await page.waitForTimeout(200);
}

/** Fails on any serious/critical axe violation, with the full violation JSON in the message. */
export async function assertNoSeriousViolations(page: Page): Promise<void> {
  const { default: AxeBuilder } = await import("@axe-core/playwright");
  const { expect } = await import("@playwright/test");
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
}
