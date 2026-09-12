import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import * as apiEnums from "@/lib/api-enums";

/**
 * No screen compares a control type, an item type, a stock policy or a transaction kind
 * against a wire value it typed out itself.
 *
 * P5 step 6 shipped two screens filtering on `control_type === "INV"` while the enum
 * serialises `"inventory"`. The pickers were empty and a correctly-mapped account displayed as
 * "Not set" — a screen that rendered perfectly and told the operator something untrue. An e2e
 * caught it, but only because one existed to open that screen with data in it.
 *
 * Fixing that instance would have left the class alone. `src/lib/api-enums.ts` is generated
 * from the Python enums (`backend/app/scripts/export_api_enums.py`, with
 * `backend/tests/test_api_enums_export.py` as the drift gate), and this file keeps the
 * literals out of the screens so there is nothing left to be wrong.
 *
 * **Why it matches on the field rather than on the value.** The first cut of this test banned
 * every guarded enum value anywhere in a screen file, and immediately flagged twenty P4 files
 * for `"ap"` — which in those files is a `PartnerRole` in `role="ap"`, not a `ControlType`.
 * Two enums genuinely share that string, and no amount of scanning the value alone can tell
 * them apart. So the rule is *proximity to the field or the type*: a quoted string next to
 * `control_type`, `item_type`, `negative_stock_policy`, `kind`, or one of those type names, is
 * a wire value being spelled out and is banned. A guard that cried wolf on `role="ap"` would
 * have been switched off in a week, and a guard that gets switched off guards nothing.
 */
const SCAN_ROOTS = ["src/app", "src/features"];

/** The generated module is where these values are *supposed* to appear. */
const EXEMPT_FILES = ["src/lib/api-enums.ts"];

/** Field names as they arrive on the wire, and the enum each one carries. */
const GUARDED_FIELDS: ReadonlyArray<readonly [field: string, enumName: string]> = [
  ["control_type", "ControlType"],
  ["item_type", "ItemType"],
  ["negative_stock_policy", "NegativeStockPolicy"],
  // `kind` is deliberately absent: four modules use that field name for four different enums
  // (a cashbook line's receipt/payment, a subledger `DocumentKind`, an inventory transaction
  // kind), so matching on it would flag `kind: "receipt"` as a bad InventoryTransactionKind.
  // The type-name rule below catches the inventory one precisely.
];

/** Type names, to catch `useState<ItemType>("stock")` and `ItemType[] = ["stock", …]`. */
const GUARDED_TYPES = ["ControlType", "ItemType", "NegativeStockPolicy", "InventoryTransactionKind"] as const;

function filesUnder(path: string): string[] {
  const full = join(process.cwd(), path);
  if (statSync(full).isFile()) {
    return /\.tsx?$/.test(full) && !/\.test\.tsx?$/.test(full) ? [full] : [];
  }
  return readdirSync(full).flatMap((entry) => filesUnder(join(path, entry)));
}

const FILES = SCAN_ROOTS.flatMap(filesUnder).filter(
  (file) => !EXEMPT_FILES.some((exempt) => file.endsWith(exempt)),
);

/**
 * A quoted string standing in for `anchor`'s value on this line, or `null`.
 *
 * Two shapes, both precise rather than "a quote somewhere nearby". A plain proximity window
 * flagged `{ value: NegativeStockPolicy.BLOCK, label: t("policyBlock") }` — the translation
 * key happened to sit close to the type name — and a guard with false positives is a guard
 * that gets deleted.
 *
 *   1. compared or assigned:  `control_type === "bank"`, `item_type: "stock"`
 *   2. a generic or an array: `useState<ItemType>("stock")`, `ItemType[] = ["stock", …]`
 */
function literalNear(line: string, anchor: string): string | null {
  const escaped = anchor.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const patterns = [
    new RegExp(`${escaped}\\s*(?:\\?\\.)?\\s*(?:===|!==|==|!=|:|=)\\s*["']([A-Za-z_][A-Za-z0-9_]*)["']`),
    new RegExp(`<${escaped}>\\s*\\(\\s*["']([A-Za-z_][A-Za-z0-9_]*)["']`),
    new RegExp(`${escaped}\\[\\]\\s*=\\s*\\[\\s*["']([A-Za-z_][A-Za-z0-9_]*)["']`),
  ];
  for (const pattern of patterns) {
    const match = pattern.exec(line);
    if (match) return match[1];
  }
  return null;
}

function keyOf(enumName: string, value: string): string | null {
  const members = apiEnums[enumName as keyof typeof apiEnums] as Record<string, string> | undefined;
  if (!members) return null;
  return Object.entries(members).find(([, v]) => v === value)?.[0] ?? null;
}

describe("wire enum values live in one generated module", () => {
  it("finds the files it claims to scan", () => {
    expect(FILES.length).toBeGreaterThan(20);
  });

  it("exports a value and a type for every guarded enum", () => {
    for (const enumName of GUARDED_TYPES) {
      const members = apiEnums[enumName] as Record<string, string>;
      expect(Object.keys(members).length).toBeGreaterThan(0);
      for (const value of Object.values(members)) expect(typeof value).toBe("string");
    }
  });

  it("is actually capable of catching the defect it was written for", () => {
    // The exact line P5 step 6 shipped. A guard nobody has watched fail is an assertion about
    // itself, so this one is shown failing on the real thing before it is trusted.
    const shipped = `() => usable.filter((a) => a.control_type === "INV"),`;
    expect(literalNear(shipped, "control_type")).toBe("INV");
    expect(keyOf("ControlType", "INV")).toBeNull(); // …and "INV" is not a value at all
    expect(keyOf("ControlType", "inventory")).toBe("INVENTORY");
  });

  it.each(FILES.map((file) => [file.replace(`${process.cwd()}/`, ""), file]))(
    "%s spells no control type, item type, policy or kind by hand",
    (relative, absolute) => {
      const offences: string[] = [];
      readFileSync(absolute, "utf8")
        .split("\n")
        .forEach((line, index) => {
          for (const [field, enumName] of GUARDED_FIELDS) {
            const found = literalNear(line, field);
            if (found === null) continue;
            const key = keyOf(enumName, found);
            offences.push(
              `line ${index + 1}: "${found}" beside \`${field}\` — use ` +
                (key ? `${enumName}.${key}` : `a ${enumName} member ("${found}" is not one)`),
            );
          }
          for (const typeName of GUARDED_TYPES) {
            const found = literalNear(line, typeName);
            if (found === null) continue;
            const key = keyOf(typeName, found);
            offences.push(
              `line ${index + 1}: "${found}" beside \`${typeName}\` — use ` +
                (key ? `${typeName}.${key}` : `a ${typeName} member ("${found}" is not one)`),
            );
          }
        });
      expect(
        offences,
        `${relative} spells a wire value by hand:\n  ${offences.join("\n  ")}\n` +
          "Import it from @/lib/api-enums instead.",
      ).toEqual([]);
    },
  );
});
