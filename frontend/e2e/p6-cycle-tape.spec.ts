import { expect, test, type Page } from "@playwright/test";
import { API_BASE, pageFetch, pickCombobox, waitForHydration } from "./support/fixtures";

/**
 * **The phase's proof that the screens post what the services post** (P6 step 9).
 *
 * `backend/tests/order_entry/test_cycle_trial_balance.py` drives procure-to-pay and
 * order-to-cash through the *services* and asserts the trial balance they leave, figure by
 * figure, against a table worked by hand. This spec drives the same sequence through the
 * *screens* and asserts the same table, read off the Trial balance report as **rendered
 * strings**. Neither reads the other's answer: both read the table.
 *
 *     procure to pay
 *       PO   40 x WINE @ 1 000, exclusive, no tax code
 *       GRN  receive 25 of them          Dr 1300  25 000   Cr 2350  25 000
 *       LCA  6 000 freight, by value     Dr 1300   6 000   Cr 1370   6 000
 *       INV  supplier invoice, matched   Dr 2350  25 000   Cr 2100  25 000
 *       INV  the freight bill itself     Dr 1370   6 000   Cr 2100   6 000
 *
 *     order to cash
 *       SO   10 x WINE @ 2 000
 *       INV  invoice all ten             Dr 1200  20 000   Cr 4100  20 000
 *            and the companion           Dr 5100  12 400   Cr 1300  12 400
 *
 * The 12 400 is the figure worth driving a browser for: 25 units arrived at 25 000, the
 * freight added 6 000 to those same 25, so the average is 1 240 and ten leave at 12 400. No
 * screen shows that average, and every screen depends on it.
 *
 * **A tenant of its own**, signed up here rather than the seeded fixture company. The trial
 * balance is a statement about *every* posting in a company, so a shared tenant would have the
 * rest of the suite in it and the comparison would be against a moving number. "On a reset
 * database" is what the step asks for; a company nobody else touches is the same guarantee
 * without serialising the suite behind it.
 *
 * CANNOT SEE: tax, foreign currency, reversals, the kit explosion — all of which the step-5
 * tape covers against the services, and three of which `p6-orders.spec.ts` drives through the
 * screens. What this adds is the *tie*: a rendered figure per account, against a hand-worked
 * one, for a cycle that began with somebody typing into a grid.
 */

const SUFFIX = String(Date.now()).slice(-6);
const OWNER_EMAIL = `e2e.tape.${SUFFIX}@vinea.example`;
const OWNER_PASSWORD = "a tape passphrase for one company";
const COMPANY = `Tape Co ${SUFFIX}`;
const WINE = `TAPEW${SUFFIX}`;
const SUPPLIER = `Tape Supplier ${SUFFIX}`;
const CUSTOMER = `Tape Customer ${SUFFIX}`;

/** account code -> [debit, credit] as the Trial balance report renders them (RWF, no minor
 * unit, thousands separated). The same seven rows as `EXPECTED_TRIAL_BALANCE` in the backend
 * test, formatted the way a person reads them. */
const EXPECTED: Array<[string, string, string]> = [
  ["1300", "FRw 31,000", "FRw 12,400"],
  ["2350", "FRw 25,000", "FRw 25,000"],
  ["1370", "FRw 6,000", "FRw 6,000"],
  ["2100", "—", "FRw 31,000"],
  ["1200", "FRw 20,000", "—"],
  ["4100", "—", "FRw 20,000"],
  ["5100", "FRw 12,400", "—"],
];

/** Signs the fresh owner in. Not `login()` from the fixtures: that one's failure message is
 * about the *seeded* credential and a drift that cannot apply to a company created a second
 * ago. */
async function signIn(page: Page) {
  await page.goto("/login");
  await waitForHydration(page, "form");
  await page.fill('input[type="email"]', OWNER_EMAIL);
  await page.fill('input[type="password"]', OWNER_PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL("/");
  await page.waitForSelector("text=Good morning");
}

/** One line of an order grid — the same shape `p6-orders.spec.ts` uses. */
async function orderLine(page: Page, row: number, code: string, quantity: string, price: string) {
  await page.getByRole("button", { name: `Item, row ${row}`, exact: true }).click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(code);
  await page.locator(`[cmdk-item]:has-text("${code}")`).first().click();
  await page.getByLabel(`Quantity, row ${row}`, { exact: true }).fill(quantity);
  await page.getByLabel(`Unit price, row ${row}`, { exact: true }).fill(price);
}

test.describe.configure({ mode: "serial", timeout: 300_000 });

test.describe("the two cycles, through the screens, tied to a trial balance", () => {
  test.beforeAll(async ({ request }) => {
    const signup = await request.post(`${API_BASE}/auth/signup`, {
      data: {
        email: OWNER_EMAIL,
        password: OWNER_PASSWORD,
        full_name: "Tape Owner",
        company_name: COMPANY,
      },
    });
    expect(signup.ok(), await signup.text()).toBe(true);
  });

  // PATH: /oe/purchase-orders/new -> Receive -> Allocate landed cost -> Process invoice ->
  //       /ap/supplier-invoices/new -> /oe/sales-orders/new -> Invoice ->
  //       /gl/reports/trial-balance.
  // CANNOT SEE: the average behind the 12 400 — no screen shows it, which is the point.
  test("procure to pay and order to cash leave the trial balance the services leave", async ({
    page,
  }) => {
    await signIn(page);

    // --- the catalogue, through the API ---------------------------------------------------
    // This spec is about the *cycle*; typing an item and two partners through their dialogs
    // would re-prove what `inventory-maintenance.spec.ts` and the AR/AP specs already prove,
    // and would put three more screens between a failure and its cause.
    const categories = await pageFetch(page, "/inventory/uom-categories");
    const count = (
      categories.json as Array<{ code: string; id: number; uoms: Array<{ id: number; code: string }> }>
    ).find((c) => c.code === "COUNT")!;
    const accounts = await pageFetch(page, "/gl/accounts");
    const byCode = new Map(
      (accounts.json as Array<{ code: string; id: number }>).map((a) => [a.code, a.id]),
    );

    const item = await pageFetch(page, "/inventory/items", {
      method: "POST",
      body: {
        code: WINE,
        name: `Tape wine ${SUFFIX}`,
        uom_category_id: count.id,
        base_uom_id: count.uoms.find((u) => u.code === "EA")!.id,
        item_type: "stock",
        selling_price: "2000",
        sales_account_id: byCode.get("4100"),
        cogs_account_id: byCode.get("5100"),
      },
    });
    expect(item.ok, JSON.stringify(item.json)).toBe(true);

    for (const [role, name, field] of [
      ["ap", SUPPLIER, "supplier_code"],
      ["ar", CUSTOMER, "customer_code"],
    ] as const) {
      const made = await pageFetch(page, `/subledger/${role}/partners`, {
        method: "POST",
        body: { name, [field]: `${role.toUpperCase()}${SUFFIX}` },
      });
      expect(made.ok, JSON.stringify(made.json)).toBe(true);
    }

    // --- procure to pay -------------------------------------------------------------------
    await page.goto("/oe/purchase-orders/new");
    await page.waitForSelector("h1:has-text('New purchase order')");
    await pickCombobox(page, "Supplier", SUPPLIER);
    await page.getByLabel("Description", { exact: true }).fill(`Tape purchase ${SUFFIX}`);
    await orderLine(page, 1, WINE, "40", "1000");
    await page.getByRole("button", { name: "Save order" }).click();
    await page.waitForURL(/\/oe\/purchase-orders\/\d+$/, { timeout: 60_000 });
    await expect(page.getByTestId("order-total")).toHaveText("FRw 40,000");

    // Receive 25 of the 40 — the rest stays on the order and out of this trial balance, which
    // is itself a claim: a promise posts nothing (decision 3).
    await page.getByTestId("receive-order").click();
    await page.waitForSelector("h1:has-text('New goods receipt')");
    await page.getByLabel("Quantity, row 1", { exact: true }).fill("25");
    await page.getByRole("button", { name: "Post receipt" }).click();
    await page.waitForURL(/\/oe\/goods-received\/\d+$/, { timeout: 60_000 });
    await page.waitForSelector("[data-testid='grn-value']");
    await expect(page.getByTestId("grn-value")).toHaveText("FRw 25,000");
    const grnUrl = page.url();
    const grnNumber = await page.locator("h1").first().innerText();

    // The freight, spread into the goods it belongs to.
    await page.goto("/oe/landed-costs/new");
    await page.waitForSelector("h1:has-text('New landed cost')");
    await page.getByLabel("Amount", { exact: true }).fill("6000");
    await page.getByLabel("Description", { exact: true }).fill(`Tape freight ${SUFFIX}`);
    await pickCombobox(page, "Add a receipt", grnNumber);
    await page.getByRole("checkbox").first().check();
    await page.getByRole("button", { name: "Preview shares" }).click();
    await expect(page.getByTestId("landed-cost-allocated")).toHaveText("FRw 6,000");
    await page.getByRole("button", { name: "Post", exact: true }).click();
    await page.waitForURL(/\/oe\/landed-costs\/\d+$/, { timeout: 60_000 });

    // The supplier's invoice, matched to the receipt: the accrual comes back off.
    await page.goto(grnUrl);
    await page.waitForSelector("[data-testid='grn-value']");
    await page.getByTestId("process-invoice").click();
    await page.waitForURL(/\/ap\/supplier-invoices\/new\?grn_id=\d+/, { timeout: 60_000 });
    await page.getByRole("button", { name: /^Post \(/ }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 60_000 });

    // And the forwarder's own bill — a plain GL line to the clearing account, which is exactly
    // why 1370 is not a control account.
    await page.goto("/ap/supplier-invoices/new");
    await page.waitForSelector("h1:has-text('Supplier invoice')");
    await pickCombobox(page, "Supplier", SUPPLIER);
    await page.getByLabel("Description", { exact: true }).fill(`Tape freight bill ${SUFFIX}`);
    await pickCombobox(page, "Account, row 1", "1370");
    await page.getByLabel("Quantity, row 1", { exact: true }).fill("1");
    await page.getByLabel("Unit price, row 1", { exact: true }).fill("6000");
    await page.getByRole("button", { name: /^Post \(/ }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 60_000 });

    // --- order to cash --------------------------------------------------------------------
    await page.goto("/oe/sales-orders/new");
    await page.waitForSelector("h1:has-text('New sales order')");
    await pickCombobox(page, "Customer", CUSTOMER);
    await page.getByLabel("Description", { exact: true }).fill(`Tape sale ${SUFFIX}`);
    await orderLine(page, 1, WINE, "10", "2000");
    await page.getByRole("button", { name: "Save order" }).click();
    await page.waitForURL(/\/oe\/sales-orders\/\d+$/, { timeout: 60_000 });
    await expect(page.getByTestId("order-total")).toHaveText("FRw 20,000");

    await page.getByTestId("invoice-order").click();
    await page.waitForURL(/\/ar\/invoices\/new\?sales_order_id=\d+/, { timeout: 60_000 });
    await page.getByRole("button", { name: /^Post \(/ }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 60_000 });

    // --- the tie ---------------------------------------------------------------------------
    await page.goto("/gl/reports/trial-balance");
    await page.waitForSelector("h1:has-text('Trial Balance')");

    for (const [code, debit, credit] of EXPECTED) {
      const row = page.locator(`[data-account="${code}"]`);
      await expect(row, `no trial-balance row for ${code}`).toHaveCount(1);
      // **Rendered strings**, not numbers parsed back out of them. A column that printed a
      // base amount with a foreign currency's symbol reads correctly to `Number()` and is the
      // P4 defect rule 13 was written against; only the string catches it.
      await expect(row.locator("td").nth(3), `${code} debit`).toHaveText(debit);
      await expect(row.locator("td").nth(4), `${code} credit`).toHaveText(credit);
    }

    // Nothing else posted in this company, so the seven rows are the whole trial balance —
    // which is what makes the comparison a comparison rather than a spot check.
    await expect(page.getByTestId("tb-row")).toHaveCount(EXPECTED.length);
  });
});
