/**
 * Prints every live route in the Appendix C navigation tree, one per line.
 *
 * CI warms `next dev`'s per-route compile cache with curl before Playwright runs, so that
 * a route's first-hit compile is paid outside any test's timeout. That list used to be
 * typed into `ci.yml` by hand and had drifted to about half the tree — the same drift
 * issue #9 found in the axe coverage. Generating it from `nav-tree.ts` means a screen
 * added to the nav is warmed, navigable, searchable and axe-swept by one edit.
 */
import { navIntents } from "../src/design/nav-tree";

const routes = new Set<string>(["/"]);
for (const intent of navIntents) {
  for (const item of intent.items) {
    if (item.href && !item.phase) routes.add(item.href);
  }
}
process.stdout.write([...routes].join("\n") + "\n");
