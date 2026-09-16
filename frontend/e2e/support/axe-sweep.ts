import { expect, test, type Page } from "@playwright/test";
import { navIntents, type IntentLabel } from "../../src/design/nav-tree";
import { PRIMARY_EMAIL, login, seriousViolations, setTheme } from "./fixtures";

/**
 * The accessibility sweep, shared by the five `accessibility-*.spec.ts` files.
 *
 * Coverage is driven by the navigation tree, not by a list of screens someone remembered to
 * add (issue #9). Before this, `axe` ran over three screens — dashboard, journal workspace,
 * chart of accounts — and P3's Definition of Done read "light and dark both pass axe" as if
 * that were a statement about the application. It was a statement about three screens out of
 * fifty. P4 step 6 then found a genuine serious-level contrast failure (`Button`'s blanket
 * `disabled:opacity-50`, measuring ~1.5:1) only because a newly built screen happened to
 * render a disabled primary button on load. The uncovered screens are where the next one is.
 *
 * So: every entry in `nav-tree.ts` with an `href` and no phase tag gets a light and a dark
 * pass, and a screen is covered the moment it joins the tree rather than when someone
 * remembers.
 *
 * **One test per route** (P6 step 9). It used to be one test per intent, sweeping thirty-odd
 * screens in sequence inside a single test, which is why the a11y group was 8.9 of the
 * suite's 21.0 minutes: Playwright's unit of parallelism is the test, so a sweep written as
 * one test can only ever run on one worker however many are free. A test per route lets the
 * group fan out — each file declares `mode: "parallel"`, so the tests inside it run
 * concurrently without turning parallelism on for specs that are not written for it.
 *
 * It also reads better when it fails. The old shape collected every violation under an intent
 * and reported them in one message, because an accessibility run you have to repeat fifty
 * times to see fifty problems is one nobody finishes; per route, each screen reports its own
 * and every screen still reports in the same run. What is no longer possible is one screen's
 * failure hiding the twenty after it in the same test.
 *
 * CANNOT SEE, and these are the same blind spots the three-screen version had:
 *   - anything below serious/critical, and anything axe cannot test automatically — focus
 *     order, and whether a label actually describes its control;
 *   - every screen at rest only. The LineGrid's popovers, combobox listboxes and inline row
 *     errors are closed here, the account editor dialog on Chart of accounts is shut, and
 *     each of those is its own accessibility surface;
 *   - screens are visited signed in as the seeded primary owner, so anything gated behind a
 *     permission that user lacks is not reached.
 */

export interface Screen {
  href: string;
  /** "Module › Label", for naming the offending screen in a failure. */
  where: string;
}

/** Live screens under one intent, deduplicated by href — Payment terms and Ageing bucket sets
 * are one shared master each, listed under both AR and AP so neither role has to borrow the
 * other's nav, and AR/AP Defaults are one screen behind two entries. */
export function screensUnder(intentLabel: IntentLabel): Screen[] {
  const intent = navIntents.find((i) => i.label === intentLabel);
  if (!intent) throw new Error(`no intent "${intentLabel}" in the nav tree`);
  const seen = new Set<string>();
  const screens: Screen[] = [];
  for (const item of intent.items) {
    if (item.phase || !item.href || seen.has(item.href)) continue;
    seen.add(item.href);
    screens.push({ href: item.href, where: `${item.module} › ${item.label}` });
  }
  return screens;
}

/** Sweeps one screen in both themes, appending a line per problem rather than throwing. */
export async function sweepScreen(page: Page, screen: Screen): Promise<string[]> {
  const found: string[] = [];
  await page.goto(screen.href, { waitUntil: "domcontentloaded" });

  // The screen has to be *on* the page before a clean axe result means anything: a route that
  // 404s, redirects, or falls into the error boundary passes an accessibility scan trivially.
  // Every screen in the tree renders an <h1>, directly or through `DocumentWorkspace` /
  // `ReportPage` / `MaintenancePage`, so its absence means this route never became a screen.
  try {
    await page.locator("h1").first().waitFor({ state: "visible", timeout: 30_000 });
  } catch {
    return [`${screen.href}  (${screen.where})\n    never rendered — no <h1> after 30s`];
  }

  for (const theme of ["light", "dark"] as const) {
    await setTheme(page, theme);
    for (const violation of await seriousViolations(page)) {
      const nodes = violation.targets.slice(0, 3);
      const more = violation.targets.length - nodes.length;
      found.push(
        `${screen.href} [${theme}]  (${screen.where})\n` +
          `    ${violation.id} (${violation.impact}) — ${violation.help}\n` +
          nodes.map((t) => `      ${t}`).join("\n") +
          (more > 0 ? `\n      …and ${more} more node(s)` : ""),
      );
    }
  }
  return found;
}

/**
 * Declares one test per live screen under an intent, plus the anti-vacuity check.
 *
 * The count assertion is not decoration. A sweep that covers nothing passes, so if the filter
 * in `screensUnder` ever stops matching — a renamed field, a tree that moved — this says so
 * instead of reporting green over an empty loop, which is the failure mode this whole spec
 * exists to remove. It is its own test rather than a line inside each sweep, because with a
 * test per route an empty intent declares **no tests at all** and there is nowhere left for
 * the assertion to live.
 */
export function describeIntent(intentLabel: IntentLabel): void {
  const screens = screensUnder(intentLabel);

  test.describe(`accessibility: ${intentLabel} — no serious/critical axe violations`, () => {
    test.describe.configure({ mode: "parallel" });

    test(`${intentLabel} has live screens in the nav tree`, async () => {
      expect(screens.length, `no live screens under ${intentLabel}`).toBeGreaterThan(0);
    });

    for (const screen of screens) {
      test(`${screen.where} (${screen.href}) — light and dark`, async ({ page }) => {
        // A full document load plus two axe passes — and, on a cold `next dev`, a webpack
        // compile of the route itself. CI front-loads that with curl; a local run does not, and
        // a single cold route on a loaded machine has been measured past 100 seconds.
        //
        // Worth stating because **the split made this tighter**, not looser: the old
        // one-test-per-intent shape budgeted `screens.length * 40_000 + 60_000` for the whole
        // sweep, so a single slow route borrowed slack from the thirty around it. A test per
        // route has no one to borrow from, so it gets the whole allowance — still a fifth of
        // what the Maintenance sweep used to hold, and the trade is worth it: the group now
        // runs four routes at once instead of one.
        test.setTimeout(240_000);
        await login(page, PRIMARY_EMAIL);

        const failures = await sweepScreen(page, screen);
        expect(
          failures,
          `${failures.length} accessibility problem(s) on ${screen.href}:\n\n` +
            failures.join("\n\n") +
            "\n",
        ).toEqual([]);
      });
    }
  });
}
