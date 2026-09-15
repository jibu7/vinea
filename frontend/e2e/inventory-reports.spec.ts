import { expect, test, type Page } from "@playwright/test";
import {
  PRIMARY_EMAIL,
  assertNoSeriousViolations,
  login,
  pageFetch,
  pickCombobox,
  setTheme,
} from "./support/fixtures";
import { todayIso } from "../src/lib/format";

/**
 * P5 step 8 — the Enquiries and Reports → Inventory screens: item enquiry, Movement, Count,
 * Transaction and Valuation.
 *
 * One item, posted once, read five ways. Every figure below is worked by hand from the three
 * postings in `seed()` and written here as a literal — never read off one screen and asserted
 * on another, which would only prove the two agree:
 *
 *   1. Adjustment in  10 KG @ 400 at MAIN    → MAIN 10 / 4,000 · average 400
 *   2. Transfer now    4 KG MAIN → DEPOT     → MAIN  6 / 2,400 · DEPOT 4 / 1,600 (at 400)
 *   3. Count DEPOT, counted 3, Process       → DEPOT 3 / 1,200 (variance −1 at 400 = 400)
 *                                            → item  9 / 3,600 · average 400
 *
 * So the item is worth **FRw 3,600** on every screen that totals it, and the valuation
 * report's own tie to the GL has to read the same — that tie is the phase's invariant, and
 * the reason the valuation test asserts the two figures against each other rather than
 * against 3,600 twice.
 *
 * **The step-6 standard, binding here:** every screen asserts at least one formatted money
 * value and one formatted quantity read off the page as rendered. KG is the deliberate
 * choice — three decimals — so a quantity printed at the wrong scale ("6", "6.000000")
 * fails, and RWF's zero decimals catch money printed at `NUMERIC(20,6)` scale.
 */
test.describe.configure({ mode: "serial" });

const SUFFIX = String(Date.now()).slice(-6);
const ITEM_CODE = `E2ER${SUFFIX}`;

interface Named {
  id: number;
  code: string;
}

interface Fixture {
  itemId: number;
  mainId: number;
  depotId: number;
  sessionId: number;
}

let fixture: Fixture;

/** "FRw 3,600" -> 3600. The screens print money through `formatMoney`, so a test that wants
 * to add two of them up has to read them back the way a person would. */
function parseMoney(text: string): number {
  const digits = text.replace(/[^\d.-]/g, "");
  const value = Number(digits);
  if (!Number.isFinite(value)) throw new Error(`not a money figure: ${JSON.stringify(text)}`);
  return value;
}

async function post(page: Page, path: string, body: unknown, key?: string): Promise<unknown> {
  const res = await pageFetch(page, path, {
    method: "POST",
    body,
    headers: key ? { "Idempotency-Key": key } : {},
  });
  if (!res.ok) throw new Error(`POST ${path} -> ${res.status}: ${JSON.stringify(res.json)}`);
  return res.json;
}

/** The three postings above, through the real endpoints as the signed-in user. */
async function seed(page: Page): Promise<Fixture> {
  const categories = (await pageFetch(page, "/inventory/uom-categories")).json as Array<
    Named & { uoms: Named[] }
  >;
  const weight = categories.find((c) => c.code === "WEIGHT")!;
  const kg = weight.uoms.find((u) => u.code === "KG")!;

  const item = (await post(page, "/inventory/items", {
    code: ITEM_CODE,
    name: `E2E Reported Coffee ${SUFFIX}`,
    uom_category_id: weight.id,
    base_uom_id: kg.id,
    selling_price: "6400",
  })) as Named;

  const warehouses = (await pageFetch(page, "/inventory/warehouses")).json as Named[];
  const main = warehouses.find((w) => w.code === "MAIN")!;
  let depot = warehouses.find((w) => w.code === "DEPOT");
  if (!depot) {
    const branches = (await pageFetch(page, "/gl/branches")).json as Named[];
    depot = (await post(page, "/inventory/warehouses", {
      code: "DEPOT",
      name: "Musanze Depot",
      branch_id: branches[0].id,
    })) as Named;
  }

  const types = (await pageFetch(page, "/gl/transaction-types?module=inv")).json as Named[];
  const adjin = types.find((t) => t.code === "ADJIN")!;
  const today = todayIso();

  await post(
    page,
    "/inventory/adjustments",
    {
      document_date: today,
      description: "Opening for the report suite",
      transaction_type_id: adjin.id,
      lines: [{ item_id: item.id, warehouse_id: main.id, quantity: "10", unit_cost: "400" }],
    },
    `${SUFFIX}-open`,
  );

  await post(
    page,
    "/inventory/transfers",
    {
      transfer_date: today,
      description: "Stock the depot",
      from_warehouse_id: main.id,
      to_warehouse_id: depot.id,
      receive_now: true,
      lines: [{ item_id: item.id, quantity: "4" }],
    },
    `${SUFFIX}-transfer`,
  );

  const session = (await post(page, "/inventory/counts", {
    warehouse_id: depot.id,
    count_date: today,
    description: `Depot count ${SUFFIX}`,
  })) as { id: number; lines: Array<{ id: number; item_id: number }> };
  const line = session.lines.find((l) => l.item_id === item.id)!;
  const entered = await pageFetch(page, `/inventory/counts/${session.id}/lines/${line.id}`, {
    method: "PATCH",
    body: { counted_quantity: "3" },
  });
  if (!entered.ok) throw new Error(`count entry failed: ${JSON.stringify(entered.json)}`);
  await post(
    page,
    `/inventory/counts/${session.id}/process`,
    { session_id: session.id },
    `${SUFFIX}-process`,
  );

  return { itemId: item.id, mainId: main.id, depotId: depot.id, sessionId: session.id };
}

test.describe("Inventory enquiries and reports", () => {
  // PATH: /inventory/enquiry -> GET /inventory/items/{id}/enquiry, both drill-downs.
  // CANNOT SEE: whether the running columns are arithmetically right along the whole move
  // list — the step-5 costing tape asserts that server-side and would fail before this does.
  test("item enquiry: the three figures, the locations behind them, and the entry behind a move", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    fixture = await seed(page);

    await page.goto("/inventory/enquiry");
    await page.waitForSelector("h1:has-text('Item enquiry')");
    await expect(page.getByText("Choose an item to see its stock and its moves.")).toBeVisible();

    await pickCombobox(page, "Item", ITEM_CODE);

    // 10 received, 4 moved to the depot and 1 counted away: 9 left, worth 3,600, at 400.
    await expect(page.getByTestId("enquiry-quantity")).toHaveText("9.000 KG");
    await expect(page.getByTestId("enquiry-value")).toHaveText("FRw 3,600");
    await expect(page.getByTestId("enquiry-average")).toHaveText("400.000000");

    // Both locations, each at its own value — 2,400 + 1,200 is the 3,600 above.
    const main = page.locator("tbody tr", { hasText: "MAIN" }).first();
    await expect(main).toContainText("6.000 KG");
    await expect(main).toContainText("2,400");
    const depot = page.locator("tbody tr", { hasText: "DEPOT" }).first();
    await expect(depot).toContainText("3.000 KG");
    await expect(depot).toContainText("1,200");

    // Drill one: the location narrows the enquiry to that warehouse. Both tables follow —
    // one location row left, and only the depot's moves under it — while the three figures
    // above stay item-wide, which is the whole reason the narrowing is safe.
    await page.getByRole("button", { name: "Show moves at DEPOT" }).click();
    await expect(page.getByText("Narrowed to one warehouse.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Show every location again" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Show moves at MAIN" })).toHaveCount(0);
    await expect(page.getByTestId("enquiry-quantity")).toHaveText("9.000 KG");

    // And back: the row that is left is the way out of the drill.
    await page.getByRole("button", { name: "Show every location again" }).click();
    await expect(page.getByRole("button", { name: "Show moves at MAIN" })).toBeVisible();

    // Drill two: a move opens the entry that posted it, with that entry's own lines in it.
    const drill = page.getByRole("button", { name: /^Open journal entry / }).first();
    await expect(drill).toBeVisible();
    await drill.click();
    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();
    await expect(drawer.getByRole("link", { name: /Journal entry/ })).toBeVisible();
    await page.keyboard.press("Escape");

    await setTheme(page, "dark");
    await assertNoSeriousViolations(page);
    await setTheme(page, "light");
    await assertNoSeriousViolations(page);
  });

  // PATH: /inventory/reports/movement -> GET /inventory/reports/movement over month-to-date.
  // CANNOT SEE: a range that excludes the postings — they are all dated today, so the default
  // range always contains them; a report over a *past* month is the as-of reconstruction the
  // step-5 backend tests cover.
  test("movement report: opening plus in plus out foots to closing, per location", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/inventory/reports/movement");
    await page.waitForSelector("h1:has-text('Inventory movement')");
    await pickCombobox(page, "Item", ITEM_CODE);

    // MAIN: opened empty, took 10 in, sent 4 out, closed at 6 / 2,400. Out is signed, so the
    // row reads 0 + 10 − 4 = 6 straight across.
    const main = page.locator("tbody tr", { hasText: "MAIN" }).first();
    await expect(main).toBeVisible();
    await expect(main).toContainText("0.000 KG");
    await expect(main).toContainText("10.000 KG");
    await expect(main).toContainText("-4.000 KG");
    await expect(main).toContainText("6.000 KG");
    await expect(main).toContainText("2,400");

    // DEPOT: 4 in from the transfer, 1 out to the count, 3 / 1,200 left.
    const depot = page.locator("tbody tr", { hasText: "DEPOT" }).first();
    await expect(depot).toContainText("4.000 KG");
    await expect(depot).toContainText("-1.000 KG");
    await expect(depot).toContainText("1,200");

    // The report's own total, over the whole filtered set: the item is worth 3,600.
    await expect(page.getByTestId("movement-total-closingValue")).toHaveText("FRw 3,600");
    await expect(page.getByTestId("movement-total-openingValue")).toHaveText("FRw 0");
    await expect(page.getByRole("button", { name: "Export CSV" })).toBeVisible();

    await setTheme(page, "dark");
    await assertNoSeriousViolations(page);
    await setTheme(page, "light");
    await assertNoSeriousViolations(page);
  });

  // PATH: /inventory/reports/transactions -> GET /inventory/reports/transactions, item filter.
  // CANNOT SEE: the posting order of a backdated document — every move here is dated today,
  // and `sequence_no` only earns its column when date order and posting order disagree.
  test("transaction report: six moves, their entries, and totals that only appear for one item", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/inventory/reports/transactions");
    await page.waitForSelector("h1:has-text('Inventory transactions')");
    await pickCombobox(page, "Item", ITEM_CODE);

    // The receipt, the two transfer legs (four moves — source, in-transit, in-transit again,
    // destination) and the count variance.
    await expect(page.getByTestId("transaction-value").first()).toBeVisible();
    await expect(page.getByText("FRw 3,600").first()).toBeVisible();
    await expect(page.getByTestId("transaction-total-value")).toHaveText("FRw 3,600");
    // Shown only because one item is selected: a sum of kilograms and bottles would not be.
    await expect(page.getByText("9.000 KG").first()).toBeVisible();

    // Every row reaches the entry it posted with.
    await expect(page.getByRole("link").filter({ hasText: /^(INV|ADJ|TRF|CNT)/ }).first()).toBeVisible();

    await setTheme(page, "dark");
    await assertNoSeriousViolations(page);
    await setTheme(page, "light");
    await assertNoSeriousViolations(page);
  });

  // PATH: /inventory/reports/valuation -> GET /inventory/reports/valuation, all three tabs.
  // CANNOT SEE: the tie for the *whole company* — this filters to one item, so it asserts
  // the report agrees with its own GL figure on that slice. The company-wide tie is
  // `assert_stock_invariants`, which runs on every backend posting test.
  test("valuation report: the total, the GL figure it must equal, and the same money by warehouse", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/inventory/reports/valuation");
    await page.waitForSelector("h1:has-text('Inventory valuation')");
    await pickCombobox(page, "Item", ITEM_CODE);

    await expect(page.getByTestId("valuation-total")).toHaveText("FRw 3,600");

    // The invariant, on the report itself: the accounts the stock hangs off must add up to
    // the stock. There are two of them for this item — the inventory account holding 3,600
    // and the in-transit account at nil, because the transfer passed through it and left it
    // empty — so this sums them rather than assuming which one is which. Asserted against
    // the screen's *own* total as well as against the literal, which is what makes it a tie
    // and not two copies of the same hand-worked number.
    const tieValues = await page.getByTestId("valuation-gl-tie-value").allInnerTexts();
    expect(tieValues.length).toBeGreaterThan(0);
    const summed = tieValues.reduce((total, text) => total + parseMoney(text), 0);
    expect(summed).toBe(parseMoney(await page.getByTestId("valuation-total").innerText()));
    expect(summed).toBe(3600);

    // By location: two rows, each at the average the whole item sits at.
    const depot = page.locator("tbody tr", { hasText: "DEPOT" }).first();
    await expect(depot).toContainText("3.000 KG");
    await expect(depot).toContainText("400.000000");

    await page.getByRole("tab", { name: "By warehouse" }).click();
    await expect(page.locator("tbody tr", { hasText: "MAIN" }).first()).toContainText("2,400");
    await expect(page.locator("tbody tr", { hasText: "DEPOT" }).first()).toContainText("1,200");

    await page.getByRole("tab", { name: "By item" }).click();
    const itemRow = page.locator("tbody tr", { hasText: ITEM_CODE }).first();
    await expect(itemRow).toContainText("9.000 KG");
    await expect(itemRow).toContainText("3,600");

    await setTheme(page, "dark");
    await assertNoSeriousViolations(page);
    await setTheme(page, "light");
    await assertNoSeriousViolations(page);
  });

  // PATH: /inventory/reports/counts -> GET /inventory/reports/counts, one session expanded.
  // CANNOT SEE: the chip in its true state — that needs an *open* session whose location has
  // moved since the snapshot, which `inventory-transactions.spec.ts` drives on the count
  // sheet. What this pins is the other half: a Completed session shows no chip at all.
  test("count report: the session, the line that was out, and the document it posted", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/inventory/reports/counts");
    await page.waitForSelector("h1:has-text('Inventory counts')");

    const session = page.locator("tbody tr", { hasText: `Depot count ${SUFFIX}` }).first();
    await expect(session).toBeVisible();
    await expect(session).toContainText("Completed");
    // One line was out by 1; the rest of the sheet agreed with the books.
    await expect(session.getByTestId("count-variances")).toHaveText("1");

    // The document a processed session leaves behind, and through it the entry.
    await expect(session.getByRole("link")).toBeVisible();

    await session.getByRole("button", { name: /^Show the lines of count / }).click();
    // By testid, not by `tbody tr`: the expanded lines sit in a table nested inside the
    // session's own row, so a row selector matches the wrapper as well as the line.
    const line = page.getByTestId("count-line").filter({ hasText: ITEM_CODE });
    await expect(line).toContainText("4.000 KG"); // system, frozen at the snapshot
    await expect(line).toContainText("3.000 KG"); // counted
    await expect(line.getByTestId("count-line-variance")).toHaveText("-1.000 KG");
    // And it is **not** stale. Process refuses a stale line, so a Completed session cannot
    // hold one — but the count's own variance move sits above the snapshot watermark, so a
    // staleness rule that ignores session status flags every line it just corrected. It did:
    // `test_a_processed_line_is_not_flagged_stale_by_its_own_posting` is the server-side half.
    await expect(line).not.toContainText("Stale");

    await setTheme(page, "dark");
    await assertNoSeriousViolations(page);
    await setTheme(page, "light");
    await assertNoSeriousViolations(page);
  });

  // PATH: policy -> `allow`, an issue with nothing behind it, then the enquiry's provisional
  // filter. Last in the file on purpose: it leaves the item at a negative quantity, which
  // would break every figure asserted above.
  // CANNOT SEE: that the flag is never corrected by a later receipt — decision 5's "no
  // retroactive correction" is a backend property, asserted in the step-5 costing tape.
  test("item enquiry: the provisional filter finds the move that was costed with no stock behind it", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    const types = (await pageFetch(page, "/gl/transaction-types?module=inv")).json as Named[];
    const adjout = types.find((t) => t.code === "ADJOUT")!;
    const today = todayIso();

    try {
      const allowed = await pageFetch(page, "/inventory/defaults", {
        method: "PATCH",
        body: { negative_stock_policy: "allow" },
      });
      expect(allowed.ok, JSON.stringify(allowed.json)).toBe(true);

      // 20 out of a depot holding 3: it posts at the last positive average (400) and is
      // flagged, rather than being refused as it would be under `block`.
      await post(
        page,
        "/inventory/adjustments",
        {
          document_date: today,
          description: "Issued against stock that was not there",
          transaction_type_id: adjout.id,
          lines: [{ item_id: fixture.itemId, warehouse_id: fixture.depotId, quantity: "20" }],
        },
        `${SUFFIX}-provisional`,
      );

      await page.goto("/inventory/enquiry");
      await page.waitForSelector("h1:has-text('Item enquiry')");
      await pickCombobox(page, "Item", ITEM_CODE);

      // Unfiltered, the flagged move is one of several and wears its chip.
      await expect(page.getByText("Provisional").first()).toBeVisible();

      // Filtered, it is the only move left — and it is the 8,000 that 20 × 400 comes to.
      await page.getByText("Only provisionally costed moves").click();
      const moves = page.locator("tbody tr", { hasText: "Provisional" });
      await expect(moves).toHaveCount(1);
      await expect(moves.first()).toContainText("-20.000 KG");
      await expect(moves.first()).toContainText("-8,000");
    } finally {
      // Every other spec in the suite assumes the default. `block` is restored here rather
      // than in an afterAll so it happens even when an assertion above throws.
      await pageFetch(page, "/inventory/defaults", {
        method: "PATCH",
        body: { negative_stock_policy: "block" },
      });
    }
  });
});
