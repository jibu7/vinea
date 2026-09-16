import { describeIntent } from "./support/axe-sweep";

/**
 * Every live **Maintenance** screen in the nav tree, one test per route, light and dark.
 *
 * Split out of the single `accessibility.spec.ts` at P6 step 9 so the a11y CI group can fan
 * out: Playwright parallelises tests, not the loops inside them, so the old one-test-per-intent
 * shape pinned the whole sweep to one worker. See `support/axe-sweep.ts` for the sweep itself
 * and for what these scans cannot see.
 */
describeIntent("Maintenance");
