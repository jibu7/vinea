import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Every native `<select>` in the app carries its own accessible name.
 *
 * `Field` publishes a label id through `FieldLabelContext` and the design-system controls
 * (`Input`, `Combobox`, `DatePicker`, `Select`) read it, so they are named without each call
 * site wiring anything. A `<select>` written by hand is the one control that silently opts
 * out: it has no visible label as far as a screen reader is concerned, and axe scores that
 * `select-name`, *critical*.
 *
 * Eight of the twelve in the app were in that state. The four that were not were on the three
 * screens the old axe spec happened to cover — each named by hand, one at a time, when a test
 * finally looked at it. The nav-wide sweep in `e2e/accessibility.spec.ts` now looks at every
 * screen, which is what found the other eight; this test is the faster and wider half of the
 * same guard, because it also sees the selects inside dialogs and drawers that the sweep
 * cannot open.
 */
const ROOT = "src";

/** The design gallery is exempt from the app's lint and i18n rules and 404s outside
 * development — see `src/app/design/layout.tsx` and `.eslintrc.README.md`. */
const EXEMPT = ["src/app/design/"];

function tsxFilesUnder(path: string): string[] {
  if (EXEMPT.some((prefix) => path.startsWith(prefix))) return [];
  const full = join(process.cwd(), path);
  if (statSync(full).isFile()) return path.endsWith(".tsx") ? [full] : [];
  return readdirSync(full).flatMap((entry) => tsxFilesUnder(join(path, entry)));
}

const FILES = tsxFilesUnder(ROOT).filter((file) => !file.endsWith(".test.tsx"));

/** The text of each `<select …>` opening tag in `source`.
 *
 * A `>` inside a JSX expression container — `onChange={(e) => setBranchId(…)}`, which every
 * one of these has — is not the end of the tag, so track brace depth and stop at the first
 * `>` outside one. */
function selectOpeningTags(source: string): string[] {
  const tags: string[] = [];
  const opener = /<select[\s>]/g;
  for (let match = opener.exec(source); match !== null; match = opener.exec(source)) {
    let depth = 0;
    for (let i = match.index; i < source.length; i += 1) {
      const char = source[i];
      if (char === "{") depth += 1;
      else if (char === "}") depth -= 1;
      else if (char === ">" && depth === 0) {
        tags.push(source.slice(match.index, i + 1));
        break;
      }
    }
  }
  return tags;
}

describe("native selects are named", () => {
  it("finds the source files it claims to scan", () => {
    expect(FILES.length).toBeGreaterThan(50);
  });

  it("sees a <select> whose onChange arrow contains a '>'", () => {
    const tags = selectOpeningTags(
      '<select value={x} onChange={(e) => setX(e.target.value)} className="h-10">\n<option/>',
    );
    expect(tags).toHaveLength(1);
    expect(tags[0]).toContain("className");
    expect(tags[0]).not.toContain("option");
  });

  it("gives every native <select> an aria-label or aria-labelledby", () => {
    const unnamed = FILES.flatMap((file) =>
      selectOpeningTags(readFileSync(file, "utf8"))
        .filter((tag) => !/\saria-label(ledby)?[=\s]/.test(tag))
        .map(() => relative(process.cwd(), file)),
    );

    expect(
      unnamed,
      "a hand-written <select> does not read Field's label context, so it needs its own " +
        "aria-label — otherwise a screen reader announces an unnamed combo box (axe " +
        "select-name, critical):\n" +
        unnamed.join("\n"),
    ).toEqual([]);
  });
});
