import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import messages from "@/i18n/messages/en.json";

/**
 * `react/jsx-no-literals` (see .eslintrc.json) covers JSX *children* on the P4 AR/AP screens.
 * It cannot cover attributes: its `noAttributeStrings` mode flags every `className` too,
 * which is unusable in a Tailwind codebase. So the props a user actually reads are checked
 * here instead, over the same file list the lint rule is scoped to.
 */
const SCREEN_FILES = [
  "src/app/(shell)/maintenance/customers",
  "src/app/(shell)/maintenance/suppliers",
  "src/app/(shell)/maintenance/sales-reps",
  "src/app/(shell)/maintenance/payment-terms",
  "src/app/(shell)/maintenance/ageing-bucket-sets",
  "src/app/(shell)/maintenance/ar-ap-defaults",
  "src/app/(shell)/maintenance/ar-transaction-types",
  "src/app/(shell)/maintenance/ap-transaction-types",
  "src/app/(shell)/maintenance/rename-partner-code",
  "src/features/subledger",
  "src/features/gl/transaction-types-screen.tsx",
];

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
  const full = join(process.cwd(), path);
  if (statSync(full).isFile()) return full.endsWith(".tsx") ? [full] : [];
  return readdirSync(full)
    .flatMap((entry) => tsxFilesUnder(join(path, entry)))
    .filter((file) => file.endsWith(".tsx") && !file.endsWith(".test.tsx"));
}

const FILES = SCREEN_FILES.flatMap(tsxFilesUnder);

describe("P4 AR/AP screens are fully externalised", () => {
  it("finds the screen files it claims to check", () => {
    // Anti-vacuity: a renamed directory must fail here, not silently check nothing.
    expect(FILES.length).toBeGreaterThanOrEqual(11);
  });

  it.each(USER_VISIBLE_PROPS)("has no literal %s= anywhere in them", (prop) => {
    const offenders: string[] = [];
    for (const file of FILES) {
      readFileSync(file, "utf8")
        .split("\n")
        .forEach((line, index) => {
          // `prop="..."` with a non-empty literal. `prop={t("…")}` and `prop={expr}` pass.
          const match = new RegExp(`\\b${prop}="([^"]+)"`).exec(line);
          if (match) {
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
        ...source.matchAll(/useTranslations\("((?:arap|maintenance)[a-zA-Z.]*)"\)/g),
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
      for (const [, key] of source.matchAll(/\bt[a-z]?(?:\.rich)?\("([a-zA-Z][\w]*)"/g)) {
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
