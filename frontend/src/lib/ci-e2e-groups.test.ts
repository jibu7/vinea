import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Every e2e spec file belongs to exactly one CI group.
 *
 * The e2e job is five parallel groups: two heavy files named one by one, and everything else
 * `--shard`ed three ways behind a path filter that excludes those two. That is a **partition
 * kept by a regex**, which is fine until somebody adds a spec file — and the failure mode is
 * the worst kind there is: a file matched by the exclusion but named by no group would be
 * collected by nothing and run by nothing, and CI would go green without it. A test that
 * never runs is the rule-14 defect one level up, and this is the test that looks.
 *
 * What is checked is the partition itself, not the run: each spec file must be either named
 * explicitly by a group **or** left in by the filter — exactly one of the two, for all of
 * them. No browser, no stack, no Playwright; it is string work over `ci.yml` and a directory
 * listing, so it runs in the unit suite where a mistake is cheap to see.
 */
const WORKFLOW = join(process.cwd(), "..", ".github", "workflows", "ci.yml");
const E2E_DIR = join(process.cwd(), "e2e");

/** The `args:` values of the e2e matrix, as the shell will receive them.
 *
 * A **line** regex, which is the whole reason for the assertion below. P6 step 9 wrote the
 * a11y group's five file names as a YAML block scalar (`args: >-`) and this function dutifully
 * returned `">-"`: the five files then looked named by no group and matched by the shard
 * filter, so they were scheduled twice, and this test reported green over it. A partition
 * checker that cannot read one of the partitions is worse than none, so an `args:` value that
 * hands over a block-scalar indicator instead of arguments fails here by name. */
function matrixArgs(): string[] {
  const workflow = readFileSync(WORKFLOW, "utf8");
  const values = [...workflow.matchAll(/^\s*args:\s*(.+)$/gm)].map(([, value]) => value.trim());
  const folded = values.filter((value) => /^[>|][-+]?\d*$/.test(value));
  expect(folded, "args: written as a YAML block scalar — this reader cannot see it").toEqual([]);
  return values;
}

function specFiles(): string[] {
  return readdirSync(E2E_DIR).filter((name) => name.endsWith(".spec.ts"));
}

/** The path filter the sharded groups carry, as a regex. `\\.` in the YAML is `\.` by the
 * time bash has read it out of the double quotes, which is what this undoes. */
function exclusionPattern(args: string[]): RegExp {
  const sharded = args.filter((value) => value.includes("--shard="));
  expect(sharded.length, "the sharded groups").toBeGreaterThan(0);
  const filters = new Set(
    sharded.map((value) => {
      const match = /"(.+)"\s*$/.exec(value);
      expect(match, `no quoted path filter in: ${value}`).not.toBeNull();
      return match![1].replace(/\\\\/g, "\\");
    }),
  );
  // One filter, not three that drifted apart: a shard with a different exclusion would leave
  // a file in two groups or in none.
  expect([...filters], "every sharded group uses the same path filter").toHaveLength(1);
  return new RegExp([...filters][0]);
}

describe("the e2e matrix covers every spec exactly once", () => {
  it("finds the workflow and the specs it claims to check", () => {
    // Anti-vacuity: a moved workflow or a renamed directory must fail here, not check nothing.
    expect(matrixArgs().length).toBeGreaterThanOrEqual(3);
    expect(specFiles().length).toBeGreaterThanOrEqual(15);
  });

  it("names or shards each spec file, never both and never neither", () => {
    const args = matrixArgs();
    const named = new Set(
      args
        .flatMap((value) => value.split(/\s+/))
        .filter((token) => token.endsWith(".spec.ts"))
        .map((token) => token.replace(/^e2e\//, "")),
    );
    const excluded = exclusionPattern(args);

    const orphaned: string[] = [];
    const doubled: string[] = [];
    for (const file of specFiles()) {
      const isNamed = named.has(file);
      // The filter is applied by Playwright to the path it reports, `e2e/<file>`.
      const isSharded = excluded.test(`e2e/${file}`);
      if (!isNamed && !isSharded) orphaned.push(file);
      if (isNamed && isSharded) doubled.push(file);
    }

    expect(orphaned, "spec files in no CI group — they would never run").toEqual([]);
    expect(doubled, "spec files in two CI groups — they would run twice").toEqual([]);
  });

  it("catches a new spec that the exclusion swallows", () => {
    // Anti-vacuity for the rule above, written as the case that would be silent. The filter
    // matches on how a path *ends*, so an ordinary new spec is sharded — but one named
    // `…-accessibility.spec.ts` is excluded by a rule written for a different file, and
    // nothing names it. That file would be collected by no group and run by nobody, and the
    // only thing standing between it and a green CI is the assertion above.
    const excluded = new RegExp("^(?!.*(?:accessibility|ar-ap-acceptance)\\.spec\\.ts).*$");
    expect(excluded.test("e2e/p6-orders.spec.ts"), "an ordinary new spec is sharded").toBe(true);
    expect(
      excluded.test("e2e/order-entry-accessibility.spec.ts"),
      "a spec the exclusion swallows",
    ).toBe(false);
  });
});
