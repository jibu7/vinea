import { expect, test, type Page } from "@playwright/test";
import {
  PRIMARY_EMAIL,
  clearDrafts,
  login,
  pageFetch,
  pickCombobox,
  setTheme,
} from "./support/fixtures";

/**
 * P5 step 9 — the acceptance chain, driven through the screens.
 *
 * One catalogue, built from nothing, moved through every posting the phase ships, and read
 * back on every report: a unit, an item with a barcode, a second warehouse, an opening batch,
 * an adjustment, a transfer, a count with a variance, and finally a reversal — with the
 * valuation report tied to the inventory account in the GL at the end of it.
 *
 * Every figure below is worked by hand from the postings and written here as a literal.
 * Nothing is read off one screen and asserted on another, which would only prove the two
 * agree with each other:
 *
 *   opening batch   40 EA @ 500 at MAIN          → MAIN 40 / 20,000 · average 500
 *   adjustment in   10 EA @ 800 at MAIN          → MAIN 50 / 28,000 · average 560
 *   transfer now    20 EA MAIN → DEPOT           → MAIN 30 / 16,800 · DEPOT 20 / 11,200
 *   count DEPOT, counted 18, Process             → DEPOT 18 / 10,080 (variance −2 at 560)
 *                                                → item 48 / 26,880 · average 560
 *
 * So the company's stock is worth **FRw 26,880** more than it was, and the inventory account
 * has to say the same thing. That tie is the phase's invariant and the last link in the
 * chain: `valuation total == the inventory account on the GL report`.
 *
 * The step-6 formatting standard is binding: every screen asserts a formatted money value and
 * a formatted quantity read off the page as rendered, never the raw field.
 */
test.describe.configure({ mode: "serial" });

const SUFFIX = String(Date.now()).slice(-6);
const ITEM_CODE = `E2EA${SUFFIX}`;
const FREE_CODE = `E2EF${SUFFIX}`;
const BARCODE = `600${SUFFIX}1`;
const UNIT_CODE = `BX${SUFFIX}`;
const DEPOT = "DEPOT";

interface Chain {
  itemId: number;
  freeItemId: number;
  mainId: number;
  depotId: number;
  inventoryAccountCode: string;
  /** The inventory account's balance before this spec posted anything — every assertion
   * below is a *delta* on it, because the suite shares one company and other specs have
   * already put stock on the shelf. */
  openingAccountBalance: number;
}

let chain: Chain;

function dialog(page: Page) {
  return page.getByRole("dialog");
}

/** Escape until nothing is layered over the page.
 *
 * One press is not enough where a dialog was opened from inside a drawer — the barcode form
 * sits on top of the item drawer, so the first Escape closes the form and leaves the drawer
 * covering the screen underneath it. */
async function closeOverlays(page: Page) {
  for (let i = 0; i < 4; i += 1) {
    if ((await page.getByRole("dialog").count()) === 0) return;
    await page.keyboard.press("Escape");
    await page.waitForTimeout(250);
  }
  await expect(page.getByRole("dialog")).toHaveCount(0);
}

async function pick(page: Page, buttonName: string | RegExp, needle: string) {
  await page.getByRole("button", { name: buttonName }).first().click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(needle);
  await page.locator(`[cmdk-item]:has-text("${needle}")`).first().click();
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

/** "FRw 26,880" -> 26880, so two rendered figures can be compared as numbers. */
function parseMoney(text: string): number {
  const value = Number(text.replace(/[^\d.-]/g, ""));
  if (!Number.isFinite(value)) throw new Error(`not a money figure: ${JSON.stringify(text)}`);
  return value;
}

/** The inventory account's balance as at today, from the GL trial balance. */
async function inventoryAccountBalance(page: Page, code: string): Promise<number> {
  const today = new Date();
  const iso = [
    today.getFullYear(),
    String(today.getMonth() + 1).padStart(2, "0"),
    String(today.getDate()).padStart(2, "0"),
  ].join("-");
  const res = await pageFetch(page, `/gl/trial-balance?as_of=${iso}`);
  if (!res.ok) throw new Error(`trial balance failed: ${JSON.stringify(res.json)}`);
  const rows = (res.json as { rows: Array<{ code: string; debit: string; credit: string }> }).rows;
  const row = rows.find((r) => r.code === code);
  if (!row) throw new Error(`no trial-balance row for account ${code}`);
  return Number(row.debit) - Number(row.credit);
}

test.describe("P5 acceptance", () => {
  // PATH: the three maintenance screens, in the order an operator would use them — each one
  // needs what the last one made. CANNOT SEE: the conversion factor being *applied* to a
  // posting, which the batch below does with the box unit this step creates.
  test("a unit, an item with a barcode, and a second warehouse", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);

    await page.goto("/maintenance/uom-categories");
    await page.waitForSelector("h1:has-text('Units of measure')");
    const countCard = page.locator("section", { hasText: "COUNT" }).first();
    await countCard.getByRole("button", { name: /Add unit/ }).click();
    await dialog(page).getByLabel("Code").fill(UNIT_CODE);
    await dialog(page).getByLabel("Name", { exact: true }).fill("Box of 10");
    await dialog(page).getByLabel("Units per base").fill("10");
    await dialog(page).getByRole("button", { name: "Save", exact: true }).click();
    const unitRow = countCard.locator("tbody tr", { hasText: UNIT_CODE }).first();
    await expect(unitRow.locator("td").nth(2)).toHaveText("10");

    await page.goto("/maintenance/inventory-items");
    await page.waitForSelector("h1:has-text('Inventory items')");
    await page.getByRole("button", { name: /New item/i }).click();
    await dialog(page).getByLabel("Code").fill(ITEM_CODE);
    await dialog(page).getByLabel("Name", { exact: true }).fill(`E2E Acceptance ${SUFFIX}`);
    await pick(page, /Unit category/, "COUNT");
    await pick(page, /Base unit/, "EA");
    await dialog(page).getByLabel("Selling price").fill("1200");
    await dialog(page).getByRole("button", { name: "Create", exact: true }).click();
    const drawer = page.getByRole("dialog").filter({ hasText: `E2E Acceptance ${SUFFIX}` });
    await expect(drawer).toBeVisible();
    await drawer.getByRole("tab", { name: "Barcodes" }).click();
    await drawer.getByRole("button", { name: /Add barcode/ }).click();
    await dialog(page).getByLabel("Barcode").fill(BARCODE);
    await pick(page, /Unit/, "EA");
    await dialog(page).getByLabel("Pack quantity").fill("1");
    await dialog(page).getByRole("button", { name: "Save", exact: true }).click();
    await expect(drawer.locator("tbody tr", { hasText: BARCODE }).first()).toBeVisible();
    await closeOverlays(page);

    // A second stock item that will be received at no cost — step 9's zero-value case.
    await page.getByRole("button", { name: /New item/i }).click();
    await dialog(page).getByLabel("Code").fill(FREE_CODE);
    await dialog(page).getByLabel("Name", { exact: true }).fill(`E2E Sample ${SUFFIX}`);
    await pick(page, /Unit category/, "COUNT");
    await pick(page, /Base unit/, "EA");
    await dialog(page).getByLabel("Selling price").fill("0");
    await dialog(page).getByRole("button", { name: "Create", exact: true }).click();
    await expect(page.getByRole("dialog").filter({ hasText: `E2E Sample ${SUFFIX}` })).toBeVisible();
    await closeOverlays(page);

    await page.goto("/maintenance/warehouses");
    await page.waitForSelector("h1:has-text('Warehouses')");
    if ((await page.locator("tbody tr", { hasText: DEPOT }).count()) === 0) {
      await page.getByRole("button", { name: /New warehouse/i }).click();
      await dialog(page).getByLabel("Code").fill(DEPOT);
      await dialog(page).getByLabel("Name", { exact: true }).fill("Musanze Depot");
      await dialog(page).getByRole("button", { name: "Create", exact: true }).click();
    }
    await expect(page.locator("tbody tr", { hasText: DEPOT }).first()).toBeVisible();

    // Ids and the inventory account, for the assertions the rest of the chain makes.
    const items = (await pageFetch(page, "/inventory/items")).json as Array<{
      id: number;
      code: string;
    }>;
    const warehouses = (await pageFetch(page, "/inventory/warehouses")).json as Array<{
      id: number;
      code: string;
    }>;
    const defaults = (await pageFetch(page, "/inventory/defaults")).json as {
      inventory_account_id: number;
    };
    const accounts = (await pageFetch(page, "/gl/accounts")).json as Array<{
      id: number;
      code: string;
    }>;
    const inventoryAccountCode = accounts.find((a) => a.id === defaults.inventory_account_id)!.code;
    chain = {
      itemId: items.find((i) => i.code === ITEM_CODE)!.id,
      freeItemId: items.find((i) => i.code === FREE_CODE)!.id,
      mainId: warehouses.find((w) => w.code === "MAIN")!.id,
      depotId: warehouses.find((w) => w.code === DEPOT)!.id,
      inventoryAccountCode,
      openingAccountBalance: await inventoryAccountBalance(page, inventoryAccountCode),
    };
  });

  // PATH: the journal batch grid, keyed in the box unit created above, so the conversion is
  // exercised on a posting rather than only shown. CANNOT SEE: the average after the second
  // receipt — that is the next test, which is the point of doing them in order.
  test("the opening batch posts 40 in boxes of ten", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await clearDrafts(page);

    await page.goto("/inventory/journal-batches/new");
    await page.waitForSelector("h1:has-text('Inventory journal batch')");
    await pickCombobox(page, "Default type", "OPEN");
    await pickCombobox(page, "Default warehouse", "MAIN");
    await pickCell(page, /^Item, row 1$/, ITEM_CODE);
    await page.getByLabel("Quantity, row 1").fill("4");
    await pickCell(page, /^Unit of measure, row 1$/, UNIT_CODE);
    // Four boxes of ten, keyed in boxes and posted in the base unit.
    await expect(page.getByLabel(/^Unit conversion, row 1/)).toContainText("40 EA");
    // Priced **per box**, the unit the line is keyed in: 5,000 a box is 500 an each, and the
    // four boxes come to 20,000. Getting this wrong is the mistake the conversion column
    // exists to make visible.
    await page.getByLabel("Unit cost, row 1").fill("5000");

    await page.getByRole("button", { name: /^Post/ }).click();
    // The batch resets to a blank draft on success — the surest signal the post went through,
    // and one that outlives the toast.
    await expect(page.getByLabel("Quantity, row 1")).toHaveValue("1", { timeout: 20_000 });

    // 4 boxes at 5,000 = 20,000 over 40 each, so the average is 500.
    await page.goto("/inventory/enquiry");
    await page.waitForSelector("h1:has-text('Item enquiry')");
    await pickCombobox(page, "Item", ITEM_CODE);
    await expect(page.getByTestId("enquiry-quantity")).toHaveText("40 EA");
    await expect(page.getByTestId("enquiry-value")).toHaveText("FRw 20,000");
    await expect(page.getByTestId("enquiry-average")).toHaveText("500.000000");
  });

  // PATH: the adjustment workspace, then the enquiry. CANNOT SEE: that the weighted average
  // is right in general — the step-5 costing tape asserts that to the minor unit; this asserts
  // the one number this chain depends on.
  test("an adjustment in moves the average to 560", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await clearDrafts(page);

    await page.goto("/inventory/adjustments/new");
    await page.waitForSelector("h1:has-text('Inventory adjustment')");
    await pickCombobox(page, "Transaction type", "ADJIN");
    await pickCombobox(page, "Warehouse", "MAIN");
    await pickCell(page, /^Item, row 1$/, ITEM_CODE);
    await expect(page.getByLabel("On hand, row 1")).toHaveText("40 EA");
    await page.getByLabel("Quantity, row 1").fill("10");
    await page.getByLabel("Unit cost, row 1").fill("800");
    await expect(page.getByTestId("footer-value")).toHaveText("FRw 8,000");
    await page.getByRole("button", { name: "Post", exact: true }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/);

    // (20,000 + 8,000) / 50 = 560.
    await page.goto("/inventory/enquiry");
    await page.waitForSelector("h1:has-text('Item enquiry')");
    await pickCombobox(page, "Item", ITEM_CODE);
    await expect(page.getByTestId("enquiry-quantity")).toHaveText("50 EA");
    await expect(page.getByTestId("enquiry-value")).toHaveText("FRw 28,000");
    await expect(page.getByTestId("enquiry-average")).toHaveText("560.000000");
  });

  // PATH: a zero-cost receipt through the adjustment screen. CANNOT SEE: anything about the
  // ledger, because there is deliberately nothing there — which is the whole assertion.
  test("a receipt at no cost posts moves, no entry, and does not navigate to nowhere", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    await clearDrafts(page);

    await page.goto("/inventory/adjustments/new");
    await page.waitForSelector("h1:has-text('Inventory adjustment')");
    await pickCombobox(page, "Transaction type", "ADJIN");
    await pickCombobox(page, "Warehouse", "MAIN");
    await pickCell(page, /^Item, row 1$/, FREE_CODE);
    await page.getByLabel("Quantity, row 1").fill("12");
    await page.getByLabel("Unit cost, row 1").fill("0");
    await page.getByRole("button", { name: "Post", exact: true }).click();

    // The document posted — and the screen stayed put, because there is no entry to land on.
    // A screen that navigated regardless would be sitting on `/gl/entries/undefined`.
    await expect(page.getByText(/posted/i).first()).toBeVisible({ timeout: 20_000 });
    await expect(page).toHaveURL(/\/inventory\/adjustments\/new/);

    // The stock moved all the same: twelve on the shelf, worth nothing.
    await page.goto("/inventory/enquiry");
    await page.waitForSelector("h1:has-text('Item enquiry')");
    await pickCombobox(page, "Item", FREE_CODE);
    await expect(page.getByTestId("enquiry-quantity")).toHaveText("12 EA");
    await expect(page.getByTestId("enquiry-value")).toHaveText("FRw 0");

    // And the document is reachable, which is the other half of why a valueless posting
    // needed a screen: there is no journal entry to find it through.
    await page.goto("/inventory/documents");
    await page.waitForSelector("h1:has-text('Inventory documents')");
    // The valueless document is here, and its entry cell says why it has no link rather than
    // showing an empty space — this screen is the only way to reach it.
    const valueless = page.locator("tbody tr").filter({ hasText: "No journal entry" }).first();
    await expect(valueless).toBeVisible();
    await expect(valueless).toContainText("Adjustment");
  });

  // PATH: transfer now, both legs in one posting. CANNOT SEE: a dispatch left in transit —
  // `inventory-transactions.spec.ts` drives that, and the valuation report's in-transit line
  // is asserted in its own step-8 spec.
  test("a transfer moves twenty to the depot at the average", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await clearDrafts(page);

    await page.goto("/inventory/transfers/new");
    await page.waitForSelector("h1:has-text('New transfer')");
    await pickCombobox(page, "From", "MAIN");
    await pickCombobox(page, "To", DEPOT);
    await pickCell(page, /^Item, row 1$/, ITEM_CODE);
    await page.getByLabel("Quantity, row 1").fill("20");
    await page.getByRole("button", { name: "Transfer now" }).click();
    await page.waitForURL(/\/inventory\/transfers$/, { timeout: 20_000 });
    await expect(page.locator("tbody tr[data-transfer]").first()).toContainText("Received");

    // 20 × 560 = 11,200 moves; the item is worth the same in total as before.
    await page.goto("/inventory/enquiry");
    await page.waitForSelector("h1:has-text('Item enquiry')");
    await pickCombobox(page, "Item", ITEM_CODE);
    await expect(page.getByTestId("enquiry-quantity")).toHaveText("50 EA");
    await expect(page.getByTestId("enquiry-value")).toHaveText("FRw 28,000");
    const depot = page.locator("tbody tr", { hasText: DEPOT }).first();
    await expect(depot).toContainText("20 EA");
    await expect(depot).toContainText("11,200");
  });

  // PATH: the count sheet from start to Process, including an item the warehouse holds none
  // of. CANNOT SEE: a stale line refused — `inventory-transactions.spec.ts` drives that.
  test("a count finds two short, and an item the books say is not there", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);

    await page.goto("/inventory/counts");
    await page.waitForSelector("h1:has-text('Inventory counts')");
    await page.getByRole("button", { name: /New session/ }).click();
    await pickCombobox(page, "Warehouse", DEPOT, { within: dialog(page) });
    await dialog(page).getByLabel("Description").fill(`Acceptance ${SUFFIX}`);
    await dialog(page).getByRole("button", { name: "Start", exact: true }).click();
    await page.waitForURL(/\/inventory\/counts\/\d+/);
    await page.waitForSelector("h1:has-text('Count ')");

    // The sheet holds what the depot has: the transferred item, and not the free sample,
    // which was received at MAIN. An item found on a shelf the books say is empty has to be
    // countable all the same — that is what Add item is for.
    const sheetRow = page.locator(`tr[data-count-line="${ITEM_CODE}"]`);
    await expect(sheetRow).toBeVisible();
    await expect(sheetRow.locator("td").nth(1)).toHaveText("20");
    await expect(page.locator(`tr[data-count-line="${FREE_CODE}"]`)).toHaveCount(0);

    await pickCombobox(page, "Add item", FREE_CODE);
    await page.getByRole("button", { name: "Add", exact: true }).click();
    const addedRow = page.locator(`tr[data-count-line="${FREE_CODE}"]`);
    await expect(addedRow).toBeVisible();
    // The books say none here, and the sheet says so before anybody counts.
    await expect(addedRow.locator("td").nth(1)).toHaveText("0");

    // Two short on the transferred item; the added line is counted at zero, which agrees.
    const countInput = sheetRow.getByRole("textbox");
    await countInput.fill("18");
    await countInput.press("Enter");
    await expect(sheetRow.locator("td").nth(4)).toHaveText("-2");
    const addedInput = addedRow.getByRole("textbox");
    await addedInput.fill("0");
    await addedInput.press("Enter");
    await expect(addedRow).toHaveAttribute("data-counted", "true");

    // The server's posting preview, before Process. Column 4 is the **average** the variance
    // will be costed at, not the amount — the two short come off at 560 each, and the 1,120
    // that makes is asserted after Process, on the enquiry, where it has actually moved.
    const preview = page.getByTestId("count-preview");
    await expect(
      preview.locator(`tr[data-preview-line="${ITEM_CODE}"]`).locator("td").nth(4),
    ).toHaveText("FRw 560");

    await page.getByRole("button", { name: "Process", exact: true }).click();
    await expect(page.getByText(/processed/i).first()).toBeVisible({ timeout: 20_000 });

    // 28,000 − 1,120 = 26,880 across 48.
    await page.goto("/inventory/enquiry");
    await page.waitForSelector("h1:has-text('Item enquiry')");
    await pickCombobox(page, "Item", ITEM_CODE);
    await expect(page.getByTestId("enquiry-quantity")).toHaveText("48 EA");
    await expect(page.getByTestId("enquiry-value")).toHaveText("FRw 26,880");
  });

  // PATH: the valuation report against the GL's own trial balance. This is the phase
  // invariant, read off two screens that share no code. CANNOT SEE: the per-branch split,
  // which `assert_stock_invariants` asserts server-side on every posting test.
  test("the valuation total equals the inventory account in the GL", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);

    await page.goto("/inventory/reports/valuation");
    await page.waitForSelector("h1:has-text('Inventory valuation')");
    await pickCombobox(page, "Item", ITEM_CODE);
    await expect(page.getByTestId("valuation-total")).toHaveText("FRw 26,880");

    // What the whole company's stock is worth, against what the account says it is worth.
    await page.goto("/inventory/reports/valuation");
    await page.waitForSelector("h1:has-text('Inventory valuation')");
    const total = parseMoney(await page.getByTestId("valuation-total").innerText());
    const tieValues = await page.getByTestId("valuation-gl-tie-value").allInnerTexts();
    expect(tieValues.length).toBeGreaterThan(0);
    expect(tieValues.reduce((sum, text) => sum + parseMoney(text), 0)).toBe(total);

    // And against the ledger itself, not the report's own statement of it.
    const balance = await inventoryAccountBalance(page, chain.inventoryAccountCode);
    expect(balance - chain.openingAccountBalance).toBe(26880);
  });

  // PATH: the movement report over the range this chain posted in. CANNOT SEE: an as-of
  // reconstruction over a past period — the step-5 backend tests cover that.
  test("the movement report foots opening plus in plus out to closing", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);

    await page.goto("/inventory/reports/movement");
    await page.waitForSelector("h1:has-text('Inventory movement')");
    await pickCombobox(page, "Item", ITEM_CODE);

    // MAIN took 50 in and sent 20 out, closing at 30 / 16,800.
    const main = page.locator("tbody tr", { hasText: "MAIN" }).first();
    await expect(main).toContainText("50 EA");
    await expect(main).toContainText("-20 EA");
    await expect(main).toContainText("30 EA");
    await expect(main).toContainText("16,800");

    // The report's own closing total is the same 26,880 the valuation report showed.
    await expect(page.getByTestId("movement-total-closingValue")).toHaveText("FRw 26,880");
  });

  // PATH: policy `block`, then `allow`, then the enquiry's provisional filter. Last of the
  // costing steps because it leaves the item negative.
  test("negative stock is blocked, then allowed and flagged", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await clearDrafts(page);

    await page.goto("/inventory/adjustments/new");
    await page.waitForSelector("h1:has-text('Inventory adjustment')");
    await pickCombobox(page, "Transaction type", "ADJOUT");
    await pickCombobox(page, "Warehouse", DEPOT);
    await pickCell(page, /^Item, row 1$/, ITEM_CODE);
    await page.getByLabel("Quantity, row 1").fill("99");
    await page.getByRole("button", { name: "Post", exact: true }).click();

    // Refused, on the cell that asked for it.
    await expect(page.getByTestId("workspace-error")).toBeVisible();
    const quantityCell = page.locator("td", { has: page.getByLabel("Quantity, row 1") });
    await expect(quantityCell).toContainText("on hand");

    try {
      await page.goto("/maintenance/inventory-defaults");
      await page.waitForSelector("h1:has-text('Inventory defaults')");
      // A Radix Select, not the typeahead the rest of these screens use: open the trigger by
      // its field name and click the option.
      await page.getByRole("combobox", { name: "Negative stock" }).click();
      await page.getByRole("option", { name: /^Allow/ }).click();
      await page.getByRole("button", { name: "Save", exact: true }).click();
      await expect(page.getByText(/defaults saved/i).first()).toBeVisible({ timeout: 20_000 });

      await clearDrafts(page);
      await page.goto("/inventory/adjustments/new");
      await page.waitForSelector("h1:has-text('Inventory adjustment')");
      await pickCombobox(page, "Transaction type", "ADJOUT");
      await pickCombobox(page, "Warehouse", DEPOT);
      await pickCell(page, /^Item, row 1$/, ITEM_CODE);
      await page.getByLabel("Quantity, row 1").fill("20");
      await page.getByRole("button", { name: "Post", exact: true }).click();
      await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 20_000 });

      // Two of the twenty had nothing behind them, so the move is flagged for review — and
      // the enquiry's filter finds exactly it.
      await page.goto("/inventory/enquiry");
      await page.waitForSelector("h1:has-text('Item enquiry')");
      await pickCombobox(page, "Item", ITEM_CODE);
      await page.getByText("Only provisionally costed moves").click();
      const provisional = page.locator("tbody tr", { hasText: "Provisional" });
      await expect(provisional).toHaveCount(1);
    } finally {
      // Every other spec assumes the default.
      await pageFetch(page, "/inventory/defaults", {
        method: "PATCH",
        body: { negative_stock_policy: "block" },
      });
    }
  });

  // PATH: the documents screen, and the reversal that only lives there. CANNOT SEE: the GL
  // page refusing the same reversal — `test_the_gl_reversal_endpoint_refuses_an_inventory_entry`
  // is the server-side half, and the link is asserted below.
  test("reversing the adjustment through its document returns the value", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);

    const before = await inventoryAccountBalance(page, chain.inventoryAccountCode);

    await page.goto("/inventory/documents");
    await page.waitForSelector("h1:has-text('Inventory documents')");
    // The 8,000 adjustment from earlier in the chain.
    const row = page.locator("tbody tr", { hasText: "8,000" }).first();
    await expect(row).toBeVisible();
    await row.getByRole("link").first().click();
    await page.waitForURL(/\/inventory\/documents\/\d+/);
    await expect(page.getByTestId("document-total")).toHaveText("FRw 8,000");

    await page.getByRole("button", { name: "Reverse", exact: true }).click();
    await dialog(page).getByLabel("Reason").fill("keyed twice");
    await dialog(page).getByRole("button", { name: /Reverse document/ }).click();
    await page.waitForURL(/\/inventory\/documents\/\d+/, { timeout: 20_000 });

    // The mirror is worth the negative of the original, and the account has given the 8,000
    // back — the value returned, on the ledger and not only on the screen.
    await expect(page.getByTestId("document-total")).toHaveText("FRw -8,000");
    const after = await inventoryAccountBalance(page, chain.inventoryAccountCode);
    expect(after - before).toBe(-8000);

    // And the entry that reversal posted sends a reader back to this document rather than
    // offering a Reverse button the kernel would refuse.
    await page.goto("/inventory/documents");
    await page.waitForSelector("h1:has-text('Inventory documents')");
    const reversed = page.locator("tbody tr", { hasText: "-8,000" }).first();
    await reversed.getByRole("link", { name: /entry/i }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/);
    await expect(page.getByTestId("reverse-via-module")).toBeVisible();
    await expect(page.getByRole("button", { name: "Reverse", exact: true })).toHaveCount(0);

    await setTheme(page, "dark");
    await setTheme(page, "light");
  });
});
