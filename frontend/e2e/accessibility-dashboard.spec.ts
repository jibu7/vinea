import { expect, test } from "@playwright/test";
import { PRIMARY_EMAIL, assertNoSeriousViolations, login, setTheme } from "./support/fixtures";
import { screensUnder } from "./support/axe-sweep";

/**
 * The dashboard, and the tree the other four files sweep.
 *
 * The dashboard is its own file because it is the post-login landing page and **not** an entry
 * in the nav tree, so no intent sweep can reach it. The census below is here for the same
 * reason the per-intent anti-vacuity checks are in `describeIntent`: the four files that sweep
 * are generated from `nav-tree.ts`, and a tree that collapsed would leave them declaring
 * nothing and passing. This says how many screens the run is supposed to cover.
 *
 * See `support/axe-sweep.ts` for what these scans can and cannot see.
 */

test.describe("accessibility: the dashboard and the tree", () => {
  test.describe.configure({ mode: "parallel" });

  // PATH: sign in, then axe over the dashboard in each theme.
  test("dashboard — light and dark", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.waitForSelector("text=Good morning");

    await setTheme(page, "light");
    await assertNoSeriousViolations(page);

    await setTheme(page, "dark");
    await assertNoSeriousViolations(page);
  });

  test("every intent has live screens to sweep", async () => {
    const counts = {
      Maintenance: screensUnder("Maintenance").length,
      Transactions: screensUnder("Transactions").length,
      Enquiries: screensUnder("Enquiries").length,
      Reports: screensUnder("Reports").length,
    };
    for (const [intent, count] of Object.entries(counts)) {
      expect(count, `no live screens under ${intent}`).toBeGreaterThan(0);
    }
    // A floor, not the exact number: the tree grows every phase and a test that had to be
    // edited on every addition would be edited without being read. What it refuses is the
    // tree quietly shrinking to a handful, which is the state this suite was built to end.
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    expect(total, `only ${total} screens in the nav tree`).toBeGreaterThanOrEqual(40);
  });
});
