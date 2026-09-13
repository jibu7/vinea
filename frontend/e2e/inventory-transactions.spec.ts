import { expect, test, type Page } from "@playwright/test";
import {
  PRIMARY_EMAIL,
  assertNoSeriousViolations,
  clearDrafts,
  login,
  pageFetch,
  pickCombobox,
  setTheme,
} from "./support/fixtures";

/**
 * P5 step 7 — the Transactions → Inventory screens: an adjustment, a journal batch, a
 * transfer dispatched and then received, and a count session from start to Process.
 *
 * One serial flow rather than four independent tests, because each screen's figures come
 * from what the one before it posted: the adjustment puts 2.500 KG on hand, the batch reads
 * that figure back beside the item, the transfer takes 1.000 of it away, and the count finds
 * 1.500 on the sheet. That chain *is* the assertion — a screen that showed the right heading
 * over the wrong quantity would break the next step.
 *
 * **The step-6 standard, binding here:** every screen asserts at least one formatted money
 * value and one formatted quantity read off the page — "FRw 10,000" and "2.500 KG", the
 * rendered strings, never the `NUMERIC(20,6)` field. The KG item is the deliberate choice:
 * its unit has three decimals, so a quantity printed at the wrong scale ("2.5", "2.500000")
 * fails here.
 *
 * Two refusals are asserted where the prompt says they land: `insufficient_stock` on the
 * quantity cell of the line that asked for it, `count_line_stale` on the count line whose
 * location moved after it was counted.
 */
test.describe.configure({ mode: "serial" });

const SUFFIX = String(Date.now()).slice(-6);
const KG_CODE = `E2EK${SUFFIX}`;
const EA_CODE = `E2EC${SUFFIX}`;
const BARCODE = `777${SUFFIX}1`;

interface Named {
  id: number;
  code: string;
}

interface Fixture {
  kgItemId: number;
  eaItemId: number;
  mainId: number;
  depotId: number;
  adjinId: number;
}

let fixture: Fixture;

/** Everything the screens need, made through the real endpoints as the signed-in user. */
async function seed(page: Page): Promise<Fixture> {
  const categories = (await pageFetch(page, "/inventory/uom-categories")).json as Array<
    Named & { uoms: Named[] }
  >;
  const weight = categories.find((c) => c.code === "WEIGHT")!;
  const count = categories.find((c) => c.code === "COUNT")!;

  const kg = (
    await pageFetch(page, "/inventory/items", {
      method: "POST",
      body: {
        code: KG_CODE,
        name: `E2E Coffee ${SUFFIX}`,
        uom_category_id: weight.id,
        base_uom_id: weight.uoms.find((u) => u.code === "KG")!.id,
        selling_price: "6400",
      },
    })
  ).json as Named;
  await pageFetch(page, `/inventory/items/${kg.id}/barcodes`, {
    method: "POST",
    body: { barcode: BARCODE, uom_id: weight.uoms.find((u) => u.code === "KG")!.id, pack_quantity: "1" },
  });
  const ea = (
    await pageFetch(page, "/inventory/items", {
      method: "POST",
      body: {
        code: EA_CODE,
        name: `E2E Bottle ${SUFFIX}`,
        uom_category_id: count.id,
        base_uom_id: count.uoms.find((u) => u.code === "EA")!.id,
        selling_price: "8500",
      },
    })
  ).json as Named;

  const warehouses = (await pageFetch(page, "/inventory/warehouses")).json as Named[];
  const main = warehouses.find((w) => w.code === "MAIN")!;
  let depot = warehouses.find((w) => w.code === "DEPOT");
  if (!depot) {
    const branches = (await pageFetch(page, "/gl/branches")).json as Named[];
    depot = (
      await pageFetch(page, "/inventory/warehouses", {
        method: "POST",
        body: { code: "DEPOT", name: "Musanze Depot", branch_id: branches[0].id },
      })
    ).json as Named;
  }
  const types = (await pageFetch(page, "/gl/transaction-types?module=inv")).json as Named[];
  return {
    kgItemId: kg.id,
    eaItemId: ea.id,
    mainId: main.id,
    depotId: depot.id,
    adjinId: types.find((t) => t.code === "ADJIN")!.id,
  };
}

/** A LineGrid cell's combobox, by its row-scoped accessible name. */
async function pickCell(page: Page, name: RegExp, needle: string) {
  await page.getByRole("button", { name }).first().click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(needle);
  await page.locator(`[cmdk-item]:has-text("${needle}")`).first().waitFor({ state: "visible" });
  await page.keyboard.press("Enter");
  await page.locator("[cmdk-input]").waitFor({ state: "hidden" });
}

test.describe("Inventory transactions", () => {
  test("adjustment: barcode finds the item, on hand and value are formatted, and it lands on its entry", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    fixture = await seed(page);
    await clearDrafts(page);

    await page.goto("/inventory/adjustments/new");
    await page.waitForSelector("h1:has-text('Inventory adjustment')");
    await pickCombobox(page, "Transaction type", "ADJIN");
    await pickCombobox(page, "Warehouse", "MAIN");

    // Typed the barcode, not the code: the option is found through its keywords and reads
    // back as the item — "E2EK… · E2E Coffee".
    await page.getByRole("button", { name: "Item, row 1" }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(BARCODE);
    const match = page.locator(`[cmdk-item]:has-text("${KG_CODE}")`).first();
    await expect(match).toBeVisible();
    await page.keyboard.press("Enter");
    await page.locator("[cmdk-input]").waitFor({ state: "hidden" });

    // Nothing on hand yet — and it says so at the unit's three decimals, not "0".
    await expect(page.getByLabel("On hand, row 1")).toHaveText("0.000 KG");

    await page.getByLabel("Quantity, row 1").fill("2.5");
    await page.getByLabel("Unit cost, row 1").fill("4000");
    // The keyed estimate: 2.5 × 4,000, in base currency at zero decimals.
    await expect(page.getByTestId("footer-value")).toHaveText("FRw 10,000");

    await setTheme(page, "dark");
    await assertNoSeriousViolations(page);
    await setTheme(page, "light");
    await assertNoSeriousViolations(page);

    await page.getByRole("button", { name: "Post", exact: true }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/);
    // The ledger's figure, on the entry the screen landed on: the same 10,000.
    await expect(page.getByText("FRw 10,000").first()).toBeVisible();
  });

  test("adjustment: an issue beyond what is on hand is refused on the quantity cell", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    await clearDrafts(page);
    await page.goto("/inventory/adjustments/new");
    await page.waitForSelector("h1:has-text('Inventory adjustment')");
    await pickCombobox(page, "Transaction type", "ADJOUT");
    await pickCombobox(page, "Warehouse", "MAIN");
    await pickCell(page, /^Item, row 1$/, KG_CODE);
    // The 2.500 the previous test put there, read back beside the item.
    await expect(page.getByLabel("On hand, row 1")).toHaveText("2.500 KG");
    // A decrease takes no unit cost — the cell is absent, not greyed.
    await expect(page.getByLabel("Unit cost, row 1")).toHaveText("—");

    await page.getByLabel("Quantity, row 1").fill("999");
    await page.getByRole("button", { name: "Post", exact: true }).click();

    await expect(page.getByTestId("workspace-error")).toBeVisible();
    // Inline, on the cell that asked for it: the quantity column of row 1.
    const quantityCell = page.locator("td", { has: page.getByLabel("Quantity, row 1") });
    await expect(quantityCell).toContainText("on hand at MAIN");
    await expect(page).toHaveURL(/\/inventory\/adjustments\/new/);
  });

  test("journal batch: many lines, one document, the toast names its number", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await clearDrafts(page);
    await page.goto("/inventory/journal-batches/new");
    await page.waitForSelector("h1:has-text('Inventory journal batch')");
    await pickCombobox(page, "Default type", "ADJIN");
    await pickCombobox(page, "Default warehouse", "MAIN");

    await pickCell(page, /^Item, row 1$/, EA_CODE);
    await expect(page.getByLabel("On hand, row 1")).toHaveText("0 EA");
    await page.getByLabel("Quantity, row 1").fill("12");
    await page.getByLabel("Unit cost, row 1").fill("500");

    await page.getByRole("button", { name: "+ Add line" }).click();
    await pickCell(page, /^Item, row 2$/, KG_CODE);
    // The other item, the other unit: 2.500 at three decimals beside 0 at none.
    await expect(page.getByLabel("On hand, row 2")).toHaveText("2.500 KG");
    await page.getByLabel("Quantity, row 2").fill("0.5");
    await page.getByLabel("Unit cost, row 2").fill("4000");

    await expect(page.getByTestId("footer-lines")).toHaveText("2");
    // 12 × 500 + 0.5 × 4,000.
    await expect(page.getByTestId("footer-value")).toHaveText("FRw 8,000");

    await assertNoSeriousViolations(page);

    await page.getByRole("button", { name: "Post", exact: true }).click();
    await expect(page.getByText(/INVJ?-?\S* posted|posted/).first()).toBeVisible();
    // A batch stays put with a fresh draft: the grid is empty again.
    await expect(page.getByTestId("footer-lines")).toHaveText("0");
  });

  test("transfer: dispatch only, then receive from the list, with the value on the dispatch entry", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    await clearDrafts(page);
    await page.goto("/inventory/transfers/new");
    await page.waitForSelector("h1:has-text('New transfer')");
    await pickCombobox(page, "From", "MAIN");
    await pickCombobox(page, "To", "DEPOT");
    await pickCell(page, /^Item, row 1$/, KG_CODE);
    // 2.500 + 0.500 from the batch.
    await expect(page.getByLabel("On hand, row 1")).toHaveText("3.000 KG");
    await page.getByLabel("Quantity, row 1").fill("1");
    await expect(page.getByTestId("footer-lines")).toHaveText("1");

    await page.getByRole("button", { name: "Dispatch only" }).click();
    await page.waitForURL(/\/inventory\/transfers$/);
    await page.waitForSelector("h1:has-text('Warehouse transfers')");

    const row = page.locator("tbody tr[data-transfer]").first();
    await expect(row).toContainText("In transit");
    await expect(row).toContainText("MAIN");
    await expect(row).toContainText("DEPOT");

    // Open the lines: 1.000 KG, at the unit's decimals.
    await row.getByRole("button", { name: /^Open / }).click();
    const lines = page.getByTestId("transfer-lines");
    await expect(lines).toContainText(KG_CODE);
    await expect(lines.locator("tbody tr").first().locator("td").nth(2)).toHaveText("1.000");
    await expect(lines.locator("tbody tr").first().locator("td").nth(3)).toHaveText("KG");

    await assertNoSeriousViolations(page);

    await row.getByRole("button", { name: /^Receive / }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Receive", exact: true }).click();
    await expect(row).toContainText("Received");
    await expect(row.getByRole("link", { name: /Receive entry/ })).toBeVisible();

    // The value a transfer moves is on its entry: 1.000 KG at the 4,000 average.
    await row.getByRole("link", { name: /Dispatch entry/ }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/);
    await expect(page.getByText("FRw 4,000").first()).toBeVisible();
  });

  test("count: sheet, a stale line refused on the line, re-snapshot, Process", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/inventory/counts");
    await page.waitForSelector("h1:has-text('Inventory counts')");
    await page.getByRole("button", { name: /New session/ }).click();
    const dialog = page.getByRole("dialog");
    await pickCombobox(page, "Warehouse", "MAIN", { within: dialog });
    await dialog.getByLabel("Description").fill(`E2E count ${SUFFIX}`);
    await dialog.getByRole("button", { name: "Start", exact: true }).click();
    await page.waitForURL(/\/inventory\/counts\/\d+/);
    await page.waitForSelector("h1:has-text('Count ')");

    // The snapshot: 3.000 − 1.000 dispatched, and the row is visibly uncounted.
    const kgRow = page.locator(`tr[data-count-line="${KG_CODE}"]`);
    await expect(kgRow).toHaveAttribute("data-counted", "false");
    await expect(kgRow.locator("td").nth(1)).toHaveText("2.000");
    await expect(kgRow).toContainText("Not counted");
    const eaRow = page.locator(`tr[data-count-line="${EA_CODE}"]`);
    await expect(eaRow.locator("td").nth(1)).toHaveText("12");

    // Keyboard-first: type, Enter saves and moves to the next row.
    const kgInput = kgRow.getByRole("textbox");
    await kgInput.fill("1.75");
    await kgInput.press("Enter");
    await expect(kgRow).toHaveAttribute("data-counted", "true");
    await expect(kgRow.locator("td").nth(2).getByRole("textbox")).toHaveValue("1.750");
    await expect(kgRow.locator("td").nth(4)).toHaveText("-0.250");
    // The server's posting preview: the loss at the item's average, 0.250 × 4,000.
    const preview = page.getByTestId("count-preview");
    const previewRow = preview.locator(`tr[data-preview-line="${KG_CODE}"]`);
    await expect(previewRow.locator("td").nth(4)).toHaveText("FRw 4,000");
    await expect(previewRow.locator("td").nth(5)).toHaveText("FRw -1,000");
    await expect(page.getByTestId("preview-total")).toHaveText("FRw -1,000");

    await assertNoSeriousViolations(page);

    // Stock moves behind the sheet's back: an adjustment on the counted item, after the
    // count. Process must refuse, and the refusal must land on that line.
    const moved = await pageFetch(page, "/inventory/adjustments", {
      method: "POST",
      headers: { "Idempotency-Key": `e2e-stale-${SUFFIX}` },
      body: {
        document_date: new Date().toISOString().slice(0, 10),
        description: "Moves behind the count",
        transaction_type_id: fixture.adjinId,
        lines: [{ item_id: fixture.kgItemId, warehouse_id: fixture.mainId, quantity: "1", unit_cost: "4000" }],
      },
    });
    expect(moved.status, JSON.stringify(moved.json)).toBe(201);

    await page.getByRole("button", { name: "Process", exact: true }).click();
    await expect(page.getByTestId("workspace-error")).toBeVisible();
    await expect(kgRow).toContainText("stock moved after the snapshot");

    // Re-snapshot picks up the new position (3.000) and clears the count; recount, Process.
    await kgRow.getByRole("button", { name: /Re-snapshot/ }).click();
    await expect(kgRow.locator("td").nth(1)).toHaveText("3.000");
    await expect(kgRow).toHaveAttribute("data-counted", "false");
    const recount = kgRow.getByRole("textbox");
    await recount.fill("2.75");
    await recount.press("Enter");
    await expect(kgRow.locator("td").nth(4)).toHaveText("-0.250");
    await expect(page.getByTestId("preview-total")).toHaveText("FRw -1,000");

    await page.getByRole("button", { name: "Process", exact: true }).click();
    await expect(page.getByText("Completed").first()).toBeVisible();
    await expect(page.getByText(/processed/).first()).toBeVisible();
    await expect(kgRow.getByRole("textbox")).toBeDisabled();

    // Back on the list, the session reads Completed.
    await page.goto("/inventory/counts");
    await page.waitForSelector("h1:has-text('Inventory counts')");
    await expect(page.locator("tbody tr[data-count-session]").first()).toContainText("Completed");
    await assertNoSeriousViolations(page);
  });
});
