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
 *
 * **`e2e/` is scanned too, and lint does not reach it at all** — `next lint` covers the app
 * directories and stops there, so for the specs this file is the only guard. They had the same
 * defect and it was invisible for a sharper reason than usual: CI runs on GitHub runners in
 * **UTC**, where a UTC rendering and a local one are the same string, so no CI run could ever
 * have told them apart. A developer running the suite in Kigali between 00:00 and 02:00 would
 * have had specs computing yesterday while the app under test computed today — a failure that
 * looks like a flake and is not one.
 */
const ROOTS = ["src", "e2e"];

/** The one module allowed to reach for it — `nowIso()` returns a real UTC instant, which is
 * what draft `updatedAt` sorting wants. Its own tests describe the pattern in prose. */
const ALLOWED = ["src/lib/format.ts", "src/lib/format.test.ts"];

/** Prose describing the defect is not the defect. Only code is scanned, so a comment or a
 * docstring naming `toISOString()` is left alone — the alternative is a guard that forbids
 * explaining itself.
 *
 * **Line numbers are preserved.** Block comments and template literals span lines, so deleting
 * them outright renumbers everything after the first one and the offender gets reported at a
 * line it is not on. Caught by reintroducing a real occurrence and watching the failure name
 * the wrong line: a guard that points somewhere else is one people learn to distrust. */
function blankOut(match: string): string {
  return match.replace(/[^\n]/g, " ");
}

function stripCommentsAndStrings(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, blankOut)
    .replace(/(^|[^:])\/\/[^\n]*/g, (_m, lead: string) => lead)
    .replace(/"(?:[^"\\\n]|\\.)*"/g, '""')
    .replace(/'(?:[^'\\\n]|\\.)*'/g, "''")
    .replace(/`(?:[^`\\]|\\.)*`/g, blankOut);
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

describe("no UTC dates under src or e2e", () => {
  it("finds no call to toISOString outside lib/format.ts", () => {
    const offenders: string[] = [];
    for (const file of ROOTS.flatMap(sourceFilesUnder)) {
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

  it("reports the line the call is actually on", () => {
    // Regression: stripping a block comment used to delete its lines rather than blank them,
    // so every offender after the first comment was reported several lines early. Found by
    // reintroducing a real occurrence at line 69 of an e2e spec and being told line 42.
    const source = ["/**", " * two", " * lines", " */", "const d = x.toISOString();"].join("\n");
    expect(offendingLines(source)).toEqual([5]);
  });

  it("leaves prose about the defect alone", () => {
    // Otherwise the guard forbids explaining itself, and the next author deletes the comment
    // rather than the call.
    expect(offendingLines("// never use date.toISOString() for a calendar date")).toEqual([]);
    expect(offendingLines("/** `toISOString()` renders in UTC. */")).toEqual([]);
    expect(offendingLines('const message = "call toISOString() and weep";')).toEqual([]);
  });
});
