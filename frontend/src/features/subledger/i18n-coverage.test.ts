import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import messages from "@/i18n/messages/en.json";

/**
 * Both lint rules in `.eslintrc.json` — `react/jsx-no-literals` for JSX children and the
 * `no-restricted-syntax` selector for user-visible attributes — now run over the whole app,
 * so this file is no longer the only thing standing between the AR/AP screens and a stray
 * `placeholder="Code"`. What lint still cannot do is tell whether `t("branchesSubtitle")`
 * names a key that exists: to ESLint every `t()` call looks the same. That is this file's
 * job, and it is why the scans below cover `src` rather than a hand-kept list.
 *
 * The attribute scan is kept as well. It is redundant with lint by design — cheap insurance
 * that survives someone switching a rule off.
 */
const SCREEN_FILES = ["src"];

/** The design gallery is exempt from the i18n rules and 404s outside development —
 * see `src/app/design/layout.tsx` and `.eslintrc.README.md`. */
const EXEMPT = ["src/app/design/"];

/** Props whose value reaches the screen or a screen reader. `label` covers `Field`, `title`
 * and `description` cover MaintenancePage / MaintenanceCard / Dialog / Drawer. */
const USER_VISIBLE_PROPS = [
  "placeholder",
  "aria-label",
  "label",
  "title",
  "description",
] as const;

function tsxFilesUnder(path: string): string[] {
  if (EXEMPT.some((prefix) => path.startsWith(prefix))) return [];
  const full = join(process.cwd(), path);
  if (statSync(full).isFile()) return full.endsWith(".tsx") ? [full] : [];
  return readdirSync(full)
    .flatMap((entry) => tsxFilesUnder(join(path, entry)))
    .filter((file) => file.endsWith(".tsx") && !file.endsWith(".test.tsx"));
}

const FILES = SCREEN_FILES.flatMap(tsxFilesUnder);

describe("every screen is fully externalised", () => {
  it("finds the screen files it claims to check", () => {
    // Anti-vacuity: a renamed directory must fail here, not silently check nothing.
    expect(FILES.length).toBeGreaterThanOrEqual(40);
  });

  it.each(USER_VISIBLE_PROPS)("has no literal %s= anywhere in them", (prop) => {
    const offenders: string[] = [];
    for (const file of FILES) {
      readFileSync(file, "utf8")
        .split("\n")
        .forEach((line, index) => {
          // `prop="..."` with a literal that has something in it. `prop={t("…")}` and
          // `prop={expr}` pass, and so does a whitespace-only value — `<Field label=" ">`
          // is a layout spacer, not copy, and the lint selector ignores it for the same
          // reason.
          const match = new RegExp(`\\b${prop}="([^"]+)"`).exec(line);
          if (match && match[1].trim() !== "") {
            offenders.push(`${file.replace(process.cwd() + "/", "")}:${index + 1} ${match[0]}`);
          }
        });
    }
    expect(offenders).toEqual([]);
  });
});

describe("the arap message catalogue", () => {
  const arap = (messages as Record<string, unknown>).arap as Record<string, unknown>;

  it("carries the same key set for both roles, so neither can drift", () => {
    const role = arap.role as Record<string, Record<string, string>>;
    expect(Object.keys(role.ar).sort()).toEqual(Object.keys(role.ap).sort());
  });

  it("resolves every message key the screens reference", () => {
    const referenced = new Set<string>();
    for (const file of FILES) {
      const source = readFileSync(file, "utf8");
      // Namespaces are declared as useTranslations("arap.x"); keys as t("y").
      // A file declares one or more namespaces; a key it uses must resolve under at least
      // one of them. Which alias maps to which namespace is the typechecker's job — this
      // catches the case the typechecker cannot see: a key that exists in no catalogue at all.
      const namespaces = [
        ...source.matchAll(/useTranslations\("([a-zA-Z][a-zA-Z.]*)"\)/g),
      ].map((m) => m[1]);
      // Some namespaces are built from a template literal — `arap.role.${role}`,
      // `arap.documents.${spec.messages}`. Expand the literal prefix to every child it has
      // in the catalogue, so a key is accepted if it resolves under any of them. (The
      // role-parity test above keeps the two role blocks in lockstep; the document blocks
      // are checked the same way below.)
      for (const [, prefix] of source.matchAll(/useTranslations\(`([a-zA-Z.]+)\.\$\{/g)) {
        let node: unknown = messages;
        for (const part of prefix.split(".")) {
          node = typeof node === "object" && node !== null ? (node as Record<string, unknown>)[part] : undefined;
        }
        if (typeof node === "object" && node !== null) {
          namespaces.push(...Object.keys(node).map((child) => `${prefix}.${child}`));
        }
      }
      if (namespaces.length === 0) continue;
      // Any alias, not just `t` and `tc` — screens that pull two or three namespaces name
      // them `tGl`, `tApp`, `tCommon`. Dotted keys ("controlTypes.bank") are checked too;
      // keys built from a template literal cannot be, and are simply not matched.
      for (const [, key] of source.matchAll(/\bt[A-Za-z]*(?:\.rich)?\("([a-zA-Z][\w.]*)"/g)) {
        referenced.add(namespaces.map((ns) => `${ns}.${key}`).join("|"));
      }
    }
    expect(referenced.size).toBeGreaterThan(0);
    const resolves = (path: string): boolean => {
      let node: unknown = messages;
      for (const part of path.split(".")) {
        if (typeof node !== "object" || node === null) return false;
        node = (node as Record<string, unknown>)[part];
      }
      return typeof node === "string";
    };
    const missing = [...referenced].filter(
      (candidates) => !candidates.split("|").some(resolves),
    );
    expect(missing).toEqual([]);
  });

  it("gives every document screen the same key set, so no kind is missing copy", () => {
    const documents = (arap.documents as Record<string, Record<string, string>>);
    const perScreen = Object.entries(documents).filter(([name]) => name !== "common");
    expect(perScreen.length).toBe(6);
    const [, first] = perScreen[0];
    for (const [name, block] of perScreen) {
      expect(Object.keys(block).sort(), name).toEqual(Object.keys(first).sort());
    }
  });
});

/**
 * The scan above matches `t("literal")`. A key assembled from a template literal is
 * invisible to it, and equally invisible to lint — so the four in the app are pinned by
 * hand. Each list is the exact set of values its call site can produce; adding a fifth
 * `mode` to LineGrid, or a ControlType to the backend enum, should fail here.
 */
describe("keys built from a template literal still resolve", () => {
  const resolveKey = (path: string): unknown =>
    path
      .split(".")
      .reduce<unknown>(
        (node, part) =>
          typeof node === "object" && node !== null ? (node as Record<string, unknown>)[part] : undefined,
        messages,
      );

  const dynamic: Array<[string, string[]]> = [
    // date-picker.tsx: t(`weekday.${d}`) over WEEKDAY_KEYS
    ["datePicker.weekday", ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]],
    // (shell)/page.tsx: t(`kpi.${kpi.key}`) over the dashboard KPI list
    ["dashboard.kpi", ["cashPosition", "receivables", "payables", "netIncome"]],
    // features/gl/types.ts: controlTypeLabel() over CONTROL_TYPE_KEYS, plus the null case
    ["gl.controlTypeShort", ["none", "bank", "cash", "ar", "ap", "inventory"]],
    // line-grid.tsx: t(`${mode}Lines`) over the grid's four modes
    ["lineGrid", ["journalLines", "cashbookLines", "documentLines", "batchLines"]],
  ];

  it.each(dynamic)("%s resolves for every value the call site can produce", (prefix, keys) => {
    const missing = keys.filter((key) => typeof resolveKey(`${prefix}.${key}`) !== "string");
    expect(missing).toEqual([]);
  });
});
