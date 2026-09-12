import { expect, test, type Page } from "@playwright/test";
import { PRIMARY_EMAIL, assertNoSeriousViolations, login, setTheme } from "./support/fixtures";

/**
 * P5 step 6 — the Maintenance → Inventory screens: items with their barcodes and units,
 * warehouses including the in-transit one, the module-filtered transaction types, the
 * company-wide barcode listing, unit-of-measure categories, the inventory defaults and the
 * rename screen with its history.
 *
 * Rule 13 is what shapes this file. Every screen below is *opened with data in it* and a
 * **figure** is asserted — a factor, a pack quantity, a code, a branch — not just a heading.
 * A spec that navigates to a route and checks an `<h1>` proves the route compiles and
 * nothing else, which is exactly how the six P4 defects got through review.
 *
 * **The standard set at the step-6 review, binding on steps 7 and 8:** every screen's e2e
 * asserts at least one *formatted money* value and one *formatted quantity* read off the page
 * — the rendered string, not the raw field. Both defects this step shipped were of exactly
 * that shape: a price printed at `NUMERIC(20,6)` scale instead of RWF's zero decimals, and a
 * unit factor printed as `1.0000000000`. Asserting `8,500` and `6` is what makes the
 * formatting layer load-bearing instead of decorative.
 */

const SUFFIX = String(Date.now()).slice(-6);
const ITEM_CODE = `E2EI${SUFFIX}`;
const RENAMED_CODE = `E2ER${SUFFIX}`;
const BARCODE = `590${SUFFIX}7777`;

/** Opens the "New …" dialog and returns it, so every fill is scoped to the dialog. */
function dialog(page: Page) {
  return page.getByRole("dialog");
}

async function pick(page: Page, buttonName: string | RegExp, needle: string) {
  await page.getByRole("button", { name: buttonName }).first().click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(needle);
  await page.locator(`[cmdk-item]:has-text("${needle}")`).first().click();
}

test.describe("Inventory maintenance", () => {
  // PATH: units → item → barcode → the company-wide listing, in the order an operator would
  // actually do it, because each screen needs what the one before it made.
  // CANNOT SEE: that the conversion factor is applied when a document is posted in a
  // non-base unit — that is step 7's grid, and the backend's `to_base_quantity` tests.
  test("creates a unit, an item and a barcode, and each screen shows them back", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);

    // --- units of measure: a case of six, in the seeded Count category --------------------
    await page.goto("/maintenance/uom-categories");
    await page.waitForSelector("h1:has-text('Units of measure')");

    // The Rwanda seed pack ships four categories with their base units — so this screen has
    // rows in it before the test writes anything, and the base unit reads 1.
    const countCard = page.locator("section", { hasText: "COUNT" }).first();
    await expect(countCard).toBeVisible();
    const baseRow = countCard.locator("tbody tr", { hasText: "EA" }).first();
    // The base unit is 1 of itself. Trimmed for display — the column holds NUMERIC(20,10),
    // and "1.0000000000" is a true statement nobody wants to read off a screen.
    await expect(baseRow.locator("td").nth(2)).toHaveText("1");

    await countCard.getByRole("button", { name: /Add unit/ }).click();
    await dialog(page).getByLabel("Code").fill(`CS${SUFFIX}`);
    await dialog(page).getByLabel("Name", { exact: true }).fill("Case of 6");
    await dialog(page).getByLabel("Units per base").fill("6");
    await dialog(page).getByRole("button", { name: "Save", exact: true }).click();

    const caseRow = countCard.locator("tbody tr", { hasText: `CS${SUFFIX}` }).first();
    await expect(caseRow).toBeVisible();
    // The figure this screen exists for: six base units to the case.
    await expect(caseRow.locator("td").nth(2)).toHaveText("6");

    // --- items: create one, and read its base unit and price off the list -----------------
    await page.goto("/maintenance/inventory-items");
    await page.waitForSelector("h1:has-text('Inventory items')");
    await page.getByRole("button", { name: /New item/i }).click();
    await dialog(page).getByLabel("Code").fill(ITEM_CODE);
    await dialog(page).getByLabel("Name", { exact: true }).fill(`E2E Wine ${SUFFIX}`);
    await pick(page, /Unit category/, "COUNT");
    await pick(page, /Base unit/, "EA");
    await dialog(page).getByLabel("Selling price").fill("8500");
    await dialog(page).getByRole("button", { name: "Create", exact: true }).click();

    // Creating drops straight into the detail drawer, on the item just made.
    const drawer = page.getByRole("dialog").filter({ hasText: `E2E Wine ${SUFFIX}` });
    await expect(drawer).toBeVisible();

    // --- barcodes: a case code on the new unit --------------------------------------------
    await drawer.getByRole("tab", { name: "Barcodes" }).click();
    await drawer.getByRole("button", { name: /Add barcode/ }).click();
    await dialog(page).getByLabel("Barcode").fill(BARCODE);
    await pick(page, /Unit/, `CS${SUFFIX}`);
    await dialog(page).getByLabel("Pack quantity").fill("2");
    await dialog(page).getByRole("button", { name: "Save", exact: true }).click();

    const barcodeRow = drawer.locator("tbody tr", { hasText: BARCODE }).first();
    await expect(barcodeRow).toBeVisible();
    await expect(barcodeRow).toContainText("2");
    await page.keyboard.press("Escape");

    // The list shows the item with the price and base unit it was given.
    const itemRow = page.locator("tbody tr", { hasText: ITEM_CODE }).first();
    // Grouped and to RWF's zero decimal places, not the raw "8500.000000" the column holds —
    // rendering a base-currency amount at NUMERIC scale is the P4 defect class this phase's
    // rule 13 exists to catch, and the first screenshot of this screen showed it.
    await expect(itemRow).toContainText("8,500");
    await expect(itemRow).toContainText("EA");
    await expect(itemRow).toContainText("Stock");

    // --- barcodes: the same code, company-wide, resolved to its item and pack -------------
    await page.goto("/maintenance/barcodes");
    await page.waitForSelector("h1:has-text('Barcodes')");
    await page.getByLabel("Search").fill(BARCODE);
    const listingRow = page.locator("tbody tr", { hasText: BARCODE }).first();
    await expect(listingRow).toBeVisible();
    // The three figures that make this screen worth having: which item, which unit, how many.
    await expect(listingRow).toContainText(ITEM_CODE);
    await expect(listingRow).toContainText(`CS${SUFFIX}`);
    await expect(listingRow).toContainText("2");
  });

  // PATH: /maintenance/warehouses with the seeded MAIN and the system in-transit location.
  // CANNOT SEE: that in-transit is unreachable on a *document* — that is step 7's pickers
  // and the backend's `in_transit_warehouse_not_selectable` refusal.
  test("warehouses list the seeded location and the in-transit one, with branches", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/maintenance/warehouses");
    await page.waitForSelector("h1:has-text('Warehouses')");

    const main = page.locator("tbody tr", { hasText: "MAIN" }).first();
    await expect(main).toBeVisible();
    await expect(main).toContainText("Default");

    // The in-transit location is on this screen and nowhere else, flagged and not editable.
    const transit = page.locator("tbody tr").filter({ hasText: "In transit" }).first();
    await expect(transit).toBeVisible();
    await expect(transit.getByRole("button", { name: /^Edit/ })).toHaveCount(0);

    // Every warehouse resolves to a branch — the branch its moves post to.
    const branchCell = main.locator("td").nth(2);
    await expect(branchCell).not.toBeEmpty();
  });

  // PATH: /maintenance/inv-transaction-types → GET /gl/transaction-types?module=inv.
  // CANNOT SEE: whether the filter is the server's or the screen's; the backend's
  // module-permission tests pin it server-side.
  test("inventory transaction types are filtered by module and show their kind", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/maintenance/inv-transaction-types");
    await page.waitForSelector("h1:has-text('Inventory transaction types')");

    const modules = await page.locator("table tbody tr td:first-child").allTextContents();
    expect(modules.length).toBeGreaterThan(0);
    expect(new Set(modules.map((m) => m.trim()))).toEqual(new Set(["inv"]));

    // The kind column is the thing this screen has that the AR/AP one does not: the six
    // seeded types cover the kinds P5 decision 9 names.
    const kinds = await page.locator("table tbody tr td:nth-child(4)").allTextContents();
    const seen = new Set(kinds.map((k) => k.trim()));
    expect(seen).toContain("Adjustment in");
    expect(seen).toContain("Adjustment out");
    expect(seen).toContain("Count variance");
    expect(seen).toContain("Opening balance");
  });

  // PATH: /maintenance/inventory-defaults → the control-account pickers and the policy.
  // CANNOT SEE: that saving a non-INV account is refused — the options are filtered here and
  // the DB-level guard lives in the posting-contract tests.
  test("inventory defaults show the seeded accounts and default to blocking", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/maintenance/inventory-defaults");
    await page.waitForSelector("h1:has-text('Inventory defaults')");

    // The seed maps both control keys, so these read an account rather than "Not set".
    const control = page.getByRole("button", { name: /Inventory control/ }).first();
    await expect(control).toBeVisible();
    await expect(control).not.toContainText("Not set");
    const inTransit = page.getByRole("button", { name: /Stock in transit/ }).first();
    await expect(inTransit).not.toContainText("Not set");

    // Decision 5's default, on screen rather than only in the settings row.
    await expect(page.getByRole("combobox", { name: /Negative stock/ }).first()).toContainText(
      "Block",
    );
  });

  // PATH: /maintenance/rename-item-code → PATCH /inventory/items/{id} → the audit trail.
  // CANNOT SEE: that a stock move keeps pointing at the item across the rename — that needs a
  // posted move, which the backend's rename tests already assert.
  test("renaming an item code leaves the old and new code in its history", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);

    // An item of its own, so the rename cannot collide with the one the first test made.
    const code = `E2EN${SUFFIX}`;
    await page.goto("/maintenance/inventory-items");
    await page.waitForSelector("h1:has-text('Inventory items')");
    await page.getByRole("button", { name: /New item/i }).click();
    await dialog(page).getByLabel("Code").fill(code);
    await dialog(page).getByLabel("Name", { exact: true }).fill(`E2E Rename ${SUFFIX}`);
    await pick(page, /Unit category/, "COUNT");
    await pick(page, /Base unit/, "EA");
    await dialog(page).getByRole("button", { name: "Create", exact: true }).click();
    await page.keyboard.press("Escape");

    await page.goto("/maintenance/rename-item-code");
    await page.waitForSelector("h1:has-text('Rename item code')");
    await pick(page, /Item/, code);

    await expect(page.getByLabel("Current code")).toHaveValue(code);
    await page.getByLabel("New code").fill(RENAMED_CODE);
    await page.getByRole("button", { name: "Rename", exact: true }).click();
    await expect(page.getByText("Item renamed").first()).toBeVisible();

    // The trail hangs off item_id, so both codes are on screen after the code has moved.
    const historyRow = page.locator("table tbody tr", { hasText: "item.renamed" }).first();
    await expect(historyRow).toBeVisible();
    await expect(historyRow).toContainText(code);
    await expect(historyRow).toContainText(RENAMED_CODE);
  });

  test.describe("accessibility", () => {
    for (const [label, path, heading] of [
      ["items", "/maintenance/inventory-items", "Inventory items"],
      ["warehouses", "/maintenance/warehouses", "Warehouses"],
      ["transaction types", "/maintenance/inv-transaction-types", "Inventory transaction types"],
      ["barcodes", "/maintenance/barcodes", "Barcodes"],
      ["units of measure", "/maintenance/uom-categories", "Units of measure"],
      ["inventory defaults", "/maintenance/inventory-defaults", "Inventory defaults"],
      ["rename item code", "/maintenance/rename-item-code", "Rename item code"],
    ] as const) {
      // PATH: axe over each screen at rest, in both themes.
      // CANNOT SEE: the create dialogs and the item drawer, which is where the forms are.
      test(`${label} — light and dark`, async ({ page }) => {
        await login(page, PRIMARY_EMAIL);
        await page.goto(path);
        await page.waitForSelector(`h1:has-text("${heading}")`);

        await setTheme(page, "light");
        await assertNoSeriousViolations(page);

        await setTheme(page, "dark");
        await assertNoSeriousViolations(page);
      });
    }
  });
});
