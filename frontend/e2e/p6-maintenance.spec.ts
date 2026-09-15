import { expect, test, type Page } from "@playwright/test";
import {
  PRIMARY_EMAIL,
  READONLY_EMAIL,
  assertNoSeriousViolations,
  login,
  pageFetch,
  pickCombobox,
  setTheme,
  switchUser,
} from "./support/fixtures";

/**
 * P6 step 6 — the Maintenance screens order entry needs before it can be driven at all:
 * **Order defaults** (Maintenance → Order Entry) and the **Kit components** section the Items
 * screen grows for `item_type=kit`.
 *
 * Rule 13 shapes this file as it shaped P5's. Both screens are opened with data in them and a
 * **figure** is asserted, not a heading — and per the step-6 standard, at least one *formatted
 * money* value and one *formatted quantity* are read off the page as rendered strings:
 *
 * * money — the kit's selling price on the catalogue, `3,500`, where the column holds
 *   "3500.000000" and RWF has no decimals;
 * * quantity — a component of 0.75 KG per kit, rendered `0.750` to the **unit's** three
 *   decimal places, where the column holds "0.750000".
 *
 * Both are exactly the defect class P5 step 6 shipped and this standard exists to catch.
 *
 * **What this file cannot see.** That a kit explodes into these components on a sales-order
 * line, and that the accrual account these defaults name is the one a GRN credits: both are
 * step 7's screens, proven for now by the backend suite and the acceptance tape.
 */

const SUFFIX = String(Date.now()).slice(-6);
const KIT_CODE = `E2EKIT${SUFFIX}`;
const BOTTLE_CODE = `E2EKB${SUFFIX}`;
const COFFEE_CODE = `E2EKC${SUFFIX}`;

/** The accounts the Rwanda seed pack and the P6 back-fill leave on `gl_settings`, as the
 * pickers render them — `dotted(code, name)`. Asserting the resolved label rather than "not
 * empty" is the point: a picker comparing against the wrong wire value renders an empty
 * control and a correctly-mapped account as "Not set" (P5 step 6's defect, which is why
 * `@/lib/api-enums` is generated).
 *
 * Read as **text**, not as the control's accessible name: a `Combobox` takes its name from
 * its `Field` label ("Goods received not invoiced"), and the selected account is what it
 * shows. The name says which setting this is; the text says what it holds, and it is what
 * it holds that this test is about. */
const ACCRUAL = "2350 · Goods Received Not Invoiced";
const VARIANCE = "5300 · Purchase Price Variance";
const CLEARING = "1370 · Landed Cost Clearing";

interface SeededItem {
  id: number;
  code: string;
}

/** The two components, made through the API: this spec is about the **kit**, and typing two
 * ordinary items through the dialog would only re-prove what `inventory-maintenance.spec.ts`
 * already proves. The kit itself is created through the screen, because `kit` is the item
 * type this step adds to it. */
async function seedComponents(page: Page): Promise<{ bottle: SeededItem; coffee: SeededItem }> {
  const categories = await pageFetch(page, "/inventory/uom-categories");
  const rows = categories.json as Array<{ code: string; id: number; uoms: Array<{ id: number; code: string }> }>;
  const count = rows.find((c) => c.code === "COUNT")!;
  const weight = rows.find((c) => c.code === "WEIGHT")!;

  async function make(code: string, name: string, category: typeof count, uomCode: string) {
    const res = await pageFetch(page, "/inventory/items", {
      method: "POST",
      body: {
        code,
        name,
        uom_category_id: category.id,
        base_uom_id: category.uoms.find((u) => u.code === uomCode)!.id,
        item_type: "stock",
        selling_price: "1200",
      },
    });
    expect(res.ok, JSON.stringify(res.json)).toBe(true);
    return res.json as SeededItem;
  }

  return {
    bottle: await make(BOTTLE_CODE, `E2E Bottle ${SUFFIX}`, count, "EA"),
    coffee: await make(COFFEE_CODE, `E2E Coffee ${SUFFIX}`, weight, "KG"),
  };
}

function dialog(page: Page) {
  return page.getByRole("dialog");
}

/** An item's drawer, opened from the catalogue by code. The row is waited for rather than
 * clicked blind: the search refetches, so the previous filter's rows are on screen for a
 * moment after the box is filled. */
async function openItemDrawer(page: Page, code: string) {
  await page.goto("/maintenance/inventory-items");
  await page.waitForSelector("h1:has-text('Inventory items')");
  await page.getByLabel("Search").fill(code);
  const row = page.locator(`tbody tr:has-text("${code}")`).first();
  await expect(row).toBeVisible();
  await row.locator("button").first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
}

/** …on the Kit components tab. */
async function openKitComponents(page: Page, code: string) {
  await openItemDrawer(page, code);
  await page.getByRole("tab", { name: "Kit components" }).click();
}

/**
 * The kit, whoever made it.
 *
 * The test below it opens the kit editor, and the kit is created by the *catalogue* test
 * above — through the screen, which is what that test is about. Depending on it was a trap
 * with a sharp edge: Playwright retries in a **fresh worker**, so `SUFFIX` (a module-level
 * `Date.now()`) is re-evaluated and `KIT_CODE` becomes a code nothing has ever created. The
 * accessibility test could therefore only ever pass on its first attempt — a retry failed
 * looking for a row that did not exist, which is not the failure the retry was called to
 * re-examine and hid the one that was. CI showed exactly that: attempt one failed on an axe
 * violation, attempt two on `E2EKIT392488` not being in the table.
 *
 * So it makes its own when it has to, through the API — the screen path stays the catalogue
 * test's to prove, and this one stops depending on which worker it landed in.
 */
async function ensureKit(page: Page): Promise<void> {
  const existing = await pageFetch(page, "/inventory/items?include_inactive=true");
  const items = existing.json as Array<{ id: number; code: string }>;
  if (items.some((item) => item.code === KIT_CODE)) return;

  const { bottle, coffee } = await seedComponents(page);
  const categories = await pageFetch(page, "/inventory/uom-categories");
  const rows = categories.json as Array<{ code: string; id: number; uoms: Array<{ id: number; code: string }> }>;
  const count = rows.find((c) => c.code === "COUNT")!;
  const kit = await pageFetch(page, "/inventory/items", {
    method: "POST",
    body: {
      code: KIT_CODE,
      name: `E2E Gift kit ${SUFFIX}`,
      uom_category_id: count.id,
      base_uom_id: count.uoms.find((u) => u.code === "EA")!.id,
      item_type: "kit",
      selling_price: "3500",
    },
  });
  expect(kit.ok, JSON.stringify(kit.json)).toBe(true);
  const defined = await pageFetch(
    page,
    `/inventory/items/${(kit.json as { id: number }).id}/kit-components`,
    {
      method: "PUT",
      body: {
        components: [
          { component_item_id: bottle.id, quantity_per_kit: "2" },
          { component_item_id: coffee.id, quantity_per_kit: "0.75" },
        ],
      },
    },
  );
  expect(defined.ok, JSON.stringify(defined.json)).toBe(true);
}

/** Closes the drawer and waits for it to be gone. `Escape` is not enough on its own: while a
 * Radix dialog is open the page behind it is `aria-hidden`, so a `getByLabel` that follows
 * resolves against a tree the test cannot actually type into. */
async function closeDrawer(page: Page) {
  await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
}

test.describe("Order entry maintenance", () => {
  // PATH: the settings screen with the seeded accounts resolved on it, and the one key it
  // owns changed and read back.
  // CANNOT SEE: that a GRN actually credits this account — step 7's screen, and the tape's.
  test("order defaults show the seeded accounts, and the backorder policy round-trips", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/maintenance/order-defaults");
    await page.waitForSelector("h1:has-text('Order defaults')");

    // The three accounts, resolved to code · name rather than "Not set". The accrual picker
    // offers only `grn_accrual` control accounts and the other two only ordinary postable
    // ones, so these labels also say the filters kept the right rows.
    await expect(page.getByText(ACCRUAL)).toBeVisible();
    await expect(page.getByText(VARIANCE)).toBeVisible();
    await expect(page.getByText(CLEARING)).toBeVisible();

    // Read-only, and named: sales and purchase orders default their lines from the inventory
    // default, and this screen shows it rather than offering a second way to write it.
    await expect(page.getByText("MAIN · Main Warehouse")).toBeVisible();

    const policy = page.getByRole("combobox", { name: "Backorders" });
    await expect(policy).toContainText("Allow");

    await policy.click();
    await page.getByRole("option", { name: /^Block/ }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Order defaults saved").first()).toBeVisible();

    // Read back from the server, not from the form that just sent it.
    await page.reload();
    await page.waitForSelector("h1:has-text('Order defaults')");
    await expect(page.getByRole("combobox", { name: "Backorders" })).toContainText("Block");

    // Put it back: `allow` is what the Rwanda seed ships and what the rest of the suite and
    // the acceptance tape assume, so a spec that left `block` behind would break them from a
    // distance.
    await page.getByRole("combobox", { name: "Backorders" }).click();
    await page.getByRole("option", { name: /^Allow/ }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Order defaults saved").first()).toBeVisible();
    await page.reload();
    await expect(page.getByRole("combobox", { name: "Backorders" })).toContainText("Allow");
  });

  // PATH: a role holding none of the five order-entry permissions types the URL in.
  // The seeded Clerk is that role — no fixture user holds an order-entry permission without
  // also being the owner, so this is the only side of the split a test can reach today.
  // CANNOT SEE: the readable-but-not-writable middle (a Sales Manager, say), which the
  // disabled Save covers in code and no seeded role can demonstrate.
  test("a role with no order-entry permission is told, not shown three empty pickers", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    await switchUser(page, READONLY_EMAIL);

    // The sidebar does not offer it — the same five permissions the endpoint accepts.
    await expect(page.getByRole("link", { name: "Order defaults" })).toHaveCount(0);

    await page.goto("/maintenance/order-defaults");
    await page.waitForSelector("h1:has-text('Order defaults')");
    // The figure that matters here is the one that is *not* on the screen: no picker reads
    // "Not set" over a company whose accounts are all configured.
    await expect(page.getByText("could not be read", { exact: false })).toBeVisible();
    await expect(page.getByText("Not set")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Save", exact: true })).toHaveCount(0);
  });

  // PATH: create a kit through the Items dialog, define what it is made of, and read the
  // definition back off the screen.
  // CANNOT SEE: the explosion itself — a kit line on a sales order is step 7.
  test("a kit is created and its components are defined, saved as one definition", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    const { bottle, coffee } = await seedComponents(page);
    expect(bottle.code).toBe(BOTTLE_CODE);
    expect(coffee.code).toBe(COFFEE_CODE);

    await page.goto("/maintenance/inventory-items");
    await page.waitForSelector("h1:has-text('Inventory items')");
    await page.getByRole("button", { name: /New item/i }).click();
    await dialog(page).getByLabel("Code").fill(KIT_CODE);
    await dialog(page).getByLabel("Name", { exact: true }).fill(`E2E Gift kit ${SUFFIX}`);
    // `kit` is the item type this step adds to the dialog. Before it, a kit could be defined
    // by the API and by nothing a person could press.
    await dialog(page).getByRole("combobox", { name: "Type" }).click();
    await page.getByRole("option", { name: "Kit", exact: true }).click();
    await pickCombobox(page, "Unit category", "COUNT", { within: dialog(page) });
    await pickCombobox(page, "Base unit", "EA", { within: dialog(page) });
    await dialog(page).getByLabel("Selling price").fill("3500");

    // The purchase account offers ordinary postable accounts and no control account of any
    // kind — the same predicate the Order defaults screen applies to variance and clearing.
    // An AP line for a service item landing on 2350, or on the AR or AP control, is refused
    // at posting, so a picker that offers one is a 409 waiting to be earned. Asserted by the
    // typeahead: the accrual's own code finds nothing, an ordinary expense account does.
    await dialog(page).getByRole("button", { name: "Purchase account", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type("2350");
    await expect(page.locator("[cmdk-item]")).toHaveCount(0);
    for (let i = 0; i < 4; i += 1) await page.keyboard.press("Backspace");
    await page.keyboard.type("6100");
    await expect(page.locator('[cmdk-item]:has-text("6100")').first()).toBeVisible();
    await page.keyboard.press("Escape");

    await dialog(page).getByRole("button", { name: "Create", exact: true }).click();

    // The drawer opens on the new item. Close it and read the catalogue row: the **money**
    // assertion, formatted to RWF's zero decimals from a column holding "3500.000000".
    await closeDrawer(page);
    await page.getByLabel("Search").fill(KIT_CODE);
    const kitRow = page.locator(`tbody tr:has-text("${KIT_CODE}")`).first();
    await expect(kitRow).toBeVisible();
    await expect(kitRow.locator("td").nth(2)).toHaveText("Kit");
    await expect(kitRow.locator("td").nth(4)).toHaveText("3,500");

    // --- the definition: two bottles and three-quarters of a kilo of coffee ---------------
    await openKitComponents(page, KIT_CODE);
    await expect(page.getByText("No components yet.", { exact: false })).toBeVisible();

    await page.getByRole("button", { name: /Edit definition/ }).click();
    await page.getByRole("button", { name: /Add component/ }).click();
    await pickCombobox(page, "Component", BOTTLE_CODE);
    await page.getByLabel("Per kit").fill("2");
    await page.getByRole("button", { name: /Add component/ }).click();
    await page.getByLabel("Component").last().click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(COFFEE_CODE);
    await page.locator(`[cmdk-item]:has-text("${COFFEE_CODE}")`).first().click();
    await page.getByLabel("Per kit").last().fill("0.75");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Kit components saved").first()).toBeVisible();

    // The saved definition, read back. The **quantity** assertion: 0.75 of a kilo renders
    // `0.750` — the unit's three decimal places, not the column's six and not a bare "0.75".
    const drawer = page.getByRole("dialog");
    const bottleRow = drawer.locator("tbody tr", { hasText: BOTTLE_CODE }).first();
    await expect(bottleRow.locator("td").nth(1)).toHaveText("EA");
    await expect(bottleRow.locator("td").nth(2)).toHaveText("2");
    const coffeeRow = drawer.locator("tbody tr", { hasText: COFFEE_CODE }).first();
    await expect(coffeeRow.locator("td").nth(1)).toHaveText("KG");
    await expect(coffeeRow.locator("td").nth(2)).toHaveText("0.750");
    await expect(drawer.getByText("One kit explodes into 2 lines.")).toBeVisible();

    // --- the refusal lands on the row that caused it --------------------------------------
    // A component may appear once; the service names `components.2.component_item_id` and the
    // message belongs on that cell, not in a toast that leaves the operator hunting.
    await page.getByRole("button", { name: /Edit definition/ }).click();
    await page.getByRole("button", { name: /Add component/ }).click();
    await page.getByLabel("Component").last().click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(BOTTLE_CODE);
    await page.locator(`[cmdk-item]:has-text("${BOTTLE_CODE}")`).first().click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("already on this kit")).toBeVisible();

    // And nothing was half-saved: the definition is still the two rows it was. A whole-list
    // PUT is what makes that true — a row-at-a-time editor would have written the first two
    // again and left the third to fail.
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(drawer.locator("tbody tr")).toHaveCount(2);

    // The other refusal an operator can actually provoke from this editor, and it comes from
    // the schema rather than the service: a quantity of zero. It arrives keyed the same way
    // (`components.0.quantity_per_kit`), so it lands on the cell too — one mapping for both.
    await page.getByRole("button", { name: /Edit definition/ }).click();
    await page.getByLabel("Per kit").first().fill("0");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Input should be greater than 0")).toBeVisible();
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(drawer.locator("tbody tr")).toHaveCount(2);

    // A kit has this section and nothing else does: on a stock item the tab is not there at
    // all, rather than opening onto a section whose save the service refuses with `not_a_kit`.
    await closeDrawer(page);
    await openItemDrawer(page, BOTTLE_CODE);
    await expect(page.getByRole("tab", { name: "Kit components" })).toHaveCount(0);
    await expect(page.getByRole("tab", { name: "Barcodes" })).toBeVisible();
  });

  test.describe("accessibility", () => {
    for (const [label, path, heading] of [
      ["order defaults", "/maintenance/order-defaults", "Order defaults"],
      ["items", "/maintenance/inventory-items", "Inventory items"],
    ] as const) {
      // PATH: axe over each screen at rest, in both themes.
      // CANNOT SEE: the kit editor itself, which lives inside the drawer.
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

    // The editor is the part with the controls, so it gets its own pass with the drawer open.
    test("the kit components editor — light and dark", async ({ page }) => {
      await login(page, PRIMARY_EMAIL);
      await ensureKit(page);
      await openKitComponents(page, KIT_CODE);
      await page.getByRole("button", { name: /Edit definition/ }).click();

      await setTheme(page, "light");
      await assertNoSeriousViolations(page);

      await setTheme(page, "dark");
      await assertNoSeriousViolations(page);
    });
  });
});
