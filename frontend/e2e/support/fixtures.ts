import type { Locator, Page } from "@playwright/test";

/** Must match backend/app/scripts/seed_e2e.py — run once before the suite. `.example` (RFC
 * 2606), not `.test`: email-validator rejects `.test`/`.invalid`/`.localhost` as reserved,
 * which surfaced as every login POST 422ing even though the seeded user was real. */
export const PRIMARY_EMAIL = "e2e.primary@vinea.example";
export const PRIMARY_COMPANY = "Rugari Wines E2E";
export const SECONDARY_EMAIL = "e2e.secondary@vinea.example";
export const SECONDARY_COMPANY = "Kivu Traders E2E";
/** Clerk role in PRIMARY_COMPANY: `*:reports_view` only, no setup or posting rights. */
export const READONLY_EMAIL = "e2e.readonly@vinea.example";
/** Accountant role in PRIMARY_COMPANY: can post AR/AP, holds no `*:credit_limit_override`. */
export const POSTER_EMAIL = "e2e.poster@vinea.example";

/** The fixture password comes from the environment, and there is no literal to fall back to.
 * `seed_e2e.py` hashes whatever `E2E_PASSWORD` holds when it runs and these specs log in with
 * the same value, so one variable is the single source and CI can generate a fresh credential
 * per run (P4 step 9). Missing means the seed and the suite would disagree silently — a wall
 * of `invalid_credentials` — so fail here, naming the variable. */
export const PASSWORD = ((): string => {
  const value = process.env.E2E_PASSWORD;
  if (!value) {
    throw new Error(
      "E2E_PASSWORD is not set. Seed and suite share it, e.g.\n" +
        '  export E2E_PASSWORD="$(openssl rand -base64 24)"\n' +
        "  docker compose exec -e E2E_PASSWORD -T backend uv run python -m app.scripts.seed_e2e",
    );
  }
  return value;
})();

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

/** Signs `email` in on a page that may already hold a session. `/login` redirects to `/` as
 * soon as `useMe()` resolves, so a second `login()` on the same context lands on the
 * dashboard as the *previous* user and every later assertion reads their data. Dropping the
 * cookies first is what makes the next navigation an actual sign-in — the session lives in
 * httpOnly cookies, so this is the only handle the test has on it. */
export async function switchUser(page: Page, email: string): Promise<void> {
  await page.context().clearCookies();
  await login(page, email);
}

/** Drops every autosaved document draft for this origin. Drafts survive a failed post (that
 * is their whole point), so a test that deliberately posts a *rejected* document leaves one
 * behind, and the next visit to that screen restores it over whatever the next test types. */
export async function clearDrafts(page: Page): Promise<void> {
  await page.evaluate(() => {
    for (const key of Object.keys(window.localStorage)) {
      if (key.startsWith("vinea.draft.")) window.localStorage.removeItem(key);
    }
  });
}

/** Picks an option in a `Combobox` by its field label, typing `needle` into the typeahead.
 * `within` scopes the *trigger* only: the popover renders through a Radix portal at the top
 * of the document, so its `[cmdk-item]`s are never inside the drawer or dialog that opened
 * it. Waiting for one unfiltered item first matters — the option lists load asynchronously,
 * and typing into a list that is still empty can never produce a match. */
export async function pickCombobox(
  page: Page,
  label: string,
  needle: string,
  opts: { within?: Locator } = {},
): Promise<void> {
  await (opts.within ?? page).getByRole("button", { name: label, exact: true }).click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(needle);
  await page.locator(`[cmdk-item]:has-text("${needle}")`).first().click();
}

/** Sets an `IsoDatePicker` by clicking through its calendar.
 *
 * There is no text input to type into: the app replaced `<input type="date">` everywhere with
 * a button that opens a month grid, so `fill()` fails on it with "Element is not an <input>".
 * This walks the month header to the target month and clicks the day, which is also the only
 * way a person can set one of these — a test that reached past the calendar would not be
 * exercising the control the product ships. */
export async function pickDate(
  page: Page,
  label: string | RegExp,
  iso: string,
  opts: { within?: Locator } = {},
): Promise<void> {
  const [year, month, day] = iso.split("-").map(Number);
  const target = new Date(year, month - 1, day);
  const wanted = target.toLocaleString("en-GB", { month: "long" }) + " " + year;

  await (opts.within ?? page).getByLabel(label).click();
  const popover = page.locator("[data-radix-popper-content-wrapper]").last();
  await popover.waitFor({ state: "visible" });

  // The header is the only place the grid says which month it is showing; step towards the
  // target rather than assuming a starting point, and bound the walk so a mismatch fails as
  // a clear error instead of spinning to the test timeout.
  for (let step = 0; step < 60; step += 1) {
    const heading = (await popover.locator("span.font-medium").first().innerText()).trim();
    if (heading === wanted) break;
    const shown = new Date(`${heading} 1`);
    const forward = shown.getTime() < new Date(year, month - 1, 1).getTime();
    await popover.getByRole("button", { name: forward ? "Next month" : "Previous month" }).click();
    if (step === 59) throw new Error(`calendar never reached ${wanted} (stuck on ${heading})`);
  }
  await popover.getByRole("button", { name: String(day), exact: true }).click();
  await popover.waitFor({ state: "hidden" });
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

export interface SeriousViolation {
  id: string;
  impact: string;
  help: string;
  /** One CSS selector per offending node, so a report can name the element without the JSON. */
  targets: string[];
}

/** Runs axe over the page and returns the serious/critical violations, worst first.
 *
 * Split from the assertion below because the nav-wide sweep visits every screen in the tree
 * and has to *collect* across all of them: throwing on the first screen would report one
 * failure and hide the rest, and an accessibility pass you have to run fifty times to see
 * fifty problems is one nobody finishes. */
export async function seriousViolations(page: Page): Promise<SeriousViolation[]> {
  const { default: AxeBuilder } = await import("@axe-core/playwright");
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  return results.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => ({
      id: v.id,
      impact: v.impact ?? "serious",
      help: v.help,
      targets: v.nodes.map((n) => n.target.join(" ")),
    }));
}

/** Fails on any serious/critical axe violation, with the offending rules and nodes in the message. */
export async function assertNoSeriousViolations(page: Page): Promise<void> {
  const { expect } = await import("@playwright/test");
  const serious = await seriousViolations(page);
  expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
}
