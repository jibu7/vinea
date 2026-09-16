import { expect, test, type Page } from "@playwright/test";
import { PRIMARY_EMAIL, login } from "./support/fixtures";

/**
 * A failed query says so, on the screen, in the service's own words (P6 step 9).
 *
 * **The defect this is written against has now been shipped three times.** P4 lost six screens
 * to it and rule 13 was written; P6 found step 6's F5 and step 8's F-3, which were the same
 * defect twice — a listing that rendered "Nothing to report" over a request the server had
 * refused. The reader's next move is opposite in the two cases: "nothing to report" ends the
 * investigation, "the server refused this" begins it, and a screen that cannot tell them apart
 * sends every reader down the wrong one.
 *
 * Step 9 swept every listing, report and enquiry onto one `QueryState` component.
 * `query-state.test.tsx` proves the component and bans the old idiom by name across `src/`.
 * What neither of those can see is a **real screen, wired to a real query, with a real failure
 * coming back** — which is exactly the gap that let the defect ship: the component was never
 * the problem, the wiring was. So this drives it through the browser.
 *
 * The failure is injected rather than provoked: making the server 500 on demand would mean a
 * fixture that breaks the database, and what is being tested is the screen's reaction to a
 * refusal, not the server's ability to produce one. The body is the shape the API really
 * sends (`app/kernel/errors.py` → `{code, message, field_errors}`), so the message the screen
 * renders is the message a real refusal would carry.
 */

const MESSAGE = "Period 2026-03 is closed — this report cannot be built for it.";

/** Refuses every **API** call whose path contains `fragment`.
 *
 * `/api/v1` in the predicate is not decoration. The app's page routes are named after the
 * endpoints behind them — `/oe/sales-orders` is both a screen and an endpoint — so a predicate
 * on the fragment alone fulfils the *document* request with a JSON body, and the screen never
 * renders at all. Which is a failure that looks exactly like the one this spec is about. */
async function refuse(page: Page, fragment: string) {
  await page.route(
    (url) => url.pathname.startsWith("/api/v1") && url.pathname.includes(fragment),
    (route) =>
      route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({ code: "period_not_open", message: MESSAGE, field_errors: {} }),
      }),
  );
}

const SCREENS: Array<{ name: string; path: string; heading: string; endpoint: string }> = [
  {
    name: "the sales-order listing",
    path: "/oe/sales-orders",
    heading: "Sales orders",
    endpoint: "/oe/sales-orders",
  },
  {
    name: "the sales-order report",
    path: "/oe/reports/sales-orders",
    heading: "Sales orders",
    endpoint: "/oe/reports/sales-orders",
  },
  {
    name: "the goods-received listing",
    path: "/oe/goods-received",
    heading: "Goods received",
    endpoint: "/oe/goods-received",
  },
  {
    name: "the inventory documents listing",
    path: "/inventory/documents",
    heading: "Inventory documents",
    endpoint: "/inventory/documents",
  },
  {
    name: "the AR age analysis",
    path: "/ar/reports/age-analysis",
    heading: "Age analysis",
    endpoint: "/ageing",
  },
];

test.describe("a refused query is not an empty report", () => {
  for (const screen of SCREENS) {
    // PATH: open the screen with its query refused → the error line, carrying the API's message.
    // CANNOT SEE: that the screen renders correctly when the query *succeeds* — that is every
    // other spec's job, and this one deliberately never lets the request through.
    test(`${screen.name} shows the service's message, not "nothing to report"`, async ({
      page,
    }) => {
      await login(page, PRIMARY_EMAIL);
      await refuse(page, screen.endpoint);
      await page.goto(screen.path);
      await page.waitForSelector(`h1:has-text("${screen.heading}")`);

      const error = page.getByTestId("query-error");
      await expect(error).toBeVisible();
      // **The service's words**, not a generic apology: the message is the one sentence that
      // names the cause, and it is the difference between a bug report and a shrug.
      await expect(error).toHaveText(MESSAGE);
      // And the empty line is *not* on the screen, which is the distinction itself.
      await expect(page.getByTestId("query-empty")).toHaveCount(0);
    });
  }
});
