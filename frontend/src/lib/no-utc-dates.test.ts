import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * `toISOString()` is banned under `src`, and this is the half of the ban that survives someone
 * switching a lint rule off.
 *
 * The other half is a `no-restricted-syntax` selector in `.eslintrc.json`, which is the one
 * that gives an author the message at the moment they write the line. Both exist deliberately —
 * the same redundancy `i18n-coverage.test.ts` documents for its attribute scan — because a
 * guard that lives only in lint config is one `eslint-disable` away from gone, and this one is
 * guarding a *posting date*.
 *
 * **What it is guarding.** `toISOString()` renders in UTC. East of Greenwich that is the
 * previous day for the first hours of every day: at 00:30 in Kigali (UTC+2) it is 22:30 UTC on
 * the day before, so a document defaults into yesterday — which at a month boundary is a
 * different accounting period, and at a year boundary a different fiscal year. West of
 * Greenwich the seam runs the other way and offers tomorrow. For a Rwanda-first product this
 * was a live defect across 23 files, not a cosmetic one (issue #30).
 *
 * The lint rule exempts `src/app/design/**`, which this scan does not: the design gallery is
 * exempt from the *i18n* rules for reasons of its own, and that exemption should not quietly
 * extend to dates.
 */
const ROOT = "src";

/** The one module allowed to reach for it — `nowIso()` returns a real UTC instant, which is
 * what draft `updatedAt` sorting wants. Its own tests describe the pattern in prose. */
const ALLOWED = ["src/lib/format.ts", "src/lib/format.test.ts"];

/** Prose describing the defect is not the defect. Only code is scanned, so a comment or a
 * docstring naming `toISOString()` is left alone — the alternative is a guard that forbids
 * explaining itself. */
function stripCommentsAndStrings(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1")
    .replace(/"(?:[^"\\\n]|\\.)*"/g, '""')
    .replace(/'(?:[^'\\\n]|\\.)*'/g, "''")
    .replace(/`(?:[^`\\]|\\.)*`/g, "``");
}

function sourceFilesUnder(path: string): string[] {
  const full = join(process.cwd(), path);
  if (statSync(full).isFile()) {
    return /\.tsx?$/.test(path) ? [path] : [];
  }
  return readdirSync(full).flatMap((entry) => sourceFilesUnder(join(path, entry)));
}

export function offendingLines(source: string): number[] {
  const code = stripCommentsAndStrings(source);
  return code
    .split("\n")
    .map((line, index) => (line.includes(".toISOString(") ? index + 1 : 0))
    .filter((lineNo) => lineNo > 0);
}

describe("no UTC dates under src", () => {
  it("finds no call to toISOString outside lib/format.ts", () => {
    const offenders: string[] = [];
    for (const file of sourceFilesUnder(ROOT)) {
      const rel = relative(process.cwd(), join(process.cwd(), file)).replace(/\\/g, "/");
      if (ALLOWED.includes(rel)) continue;
      for (const lineNo of offendingLines(readFileSync(join(process.cwd(), file), "utf8"))) {
        offenders.push(`${rel}:${lineNo}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("recognises the shape it forbids", () => {
    // Anti-vacuity. A scan that cannot fail is a scan that is not there.
    expect(offendingLines("const d = date.toISOString().slice(0, 10);")).toEqual([1]);
    expect(offendingLines("const now = new Date().toISOString();")).toEqual([1]);
  });

  it("leaves prose about the defect alone", () => {
    // Otherwise the guard forbids explaining itself, and the next author deletes the comment
    // rather than the call.
    expect(offendingLines("// never use date.toISOString() for a calendar date")).toEqual([]);
    expect(offendingLines("/** `toISOString()` renders in UTC. */")).toEqual([]);
    expect(offendingLines('const message = "call toISOString() and weep";')).toEqual([]);
  });
});
