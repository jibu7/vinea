import { expect, test } from "@playwright/test";
import { navIntents } from "../src/design/nav-tree";
import {
  PRIMARY_EMAIL,
  assertNoSeriousViolations,
  login,
  seriousViolations,
  setTheme,
} from "./support/fixtures";

/**
 * Accessibility coverage is driven by the navigation tree, not by a list of screens someone
 * remembered to add (issue #9).
 *
 * Before this, `axe` ran over three screens — dashboard, journal workspace, chart of accounts
 * — and P3's Definition of Done read "light and dark both pass axe" as if that were a
 * statement about the application. It was a statement about three screens out of fifty. P4
 * step 6 then found a genuine serious-level contrast failure (`Button`'s blanket
 * `disabled:opacity-50`, measuring ~1.5:1) only because a newly built screen happened to
 * render a disabled primary button on load. The uncovered screens are where the next one is.
 *
 * So: every entry in `nav-tree.ts` with an `href` and no phase tag gets a light and a dark
 * pass, and a screen is covered the moment it joins the tree rather than when someone
 * remembers. One test per intent — the whole intent is swept and *every* violation collected
 * before it fails, because an accessibility run you have to repeat fifty times to see fifty
 * problems is one nobody finishes.
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

interface Screen {
  href: string;
  /** "Module › Label", for naming the offending screen in a failure. */
  where: string;
}

/** Live screens under one intent, deduplicated by href — Payment terms and Ageing bucket sets
 * are one shared master each, listed under both AR and AP so neither role has to borrow the
 * other's nav, and AR/AP Defaults are one screen behind two entries. */
function screensUnder(intentLabel: string): Screen[] {
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
async function sweepScreen(page: import("@playwright/test").Page, screen: Screen): Promise<string[]> {
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

test.describe("accessibility: no serious/critical axe violations", () => {
  // PATH: sign in, then axe over the dashboard in each theme. Kept as its own test because
  // the dashboard is the post-login landing page and is not an entry in the nav tree, so the
  // sweep below cannot reach it.
  test("dashboard — light and dark", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.waitForSelector("text=Good morning");

    await setTheme(page, "light");
    await assertNoSeriousViolations(page);

    await setTheme(page, "dark");
    await assertNoSeriousViolations(page);
  });

  for (const intent of navIntents) {
    const screens = screensUnder(intent.label);

    test(`${intent.label} — every screen in the nav tree, light and dark`, async ({ page }) => {
      // Each screen is a full document load plus two axe passes; `next dev` compiles a route
      // on its first hit, which CI front-loads with curl but a local run may not have.
      test.setTimeout(screens.length * 40_000 + 60_000);

      // A sweep that covers nothing passes. If the filter above ever stops matching — a
      // renamed field, a tree that moved — this says so instead of reporting green over an
      // empty loop, which is the failure mode this whole spec exists to remove.
      expect(screens.length, `no live screens under ${intent.label}`).toBeGreaterThan(0);

      await login(page, PRIMARY_EMAIL);

      const failures: string[] = [];
      for (const screen of screens) failures.push(...(await sweepScreen(page, screen)));

      expect(
        failures,
        `${failures.length} accessibility problem(s) across ${screens.length} ${intent.label} screen(s):\n\n` +
          failures.join("\n\n") +
          "\n",
      ).toEqual([]);
    });
  }
});
