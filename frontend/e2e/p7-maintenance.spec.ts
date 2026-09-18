import { expect, test, type Page } from "@playwright/test";
import {
  PRIMARY_EMAIL,
  login,
  pageFetch,
  pickCombobox,
} from "./support/fixtures";

/**
 * P7 step 6 — the Maintenance screens fiscalization needs before anything can be declared.
 *
 * **EBM devices** is the new one; the other five are columns and sections on screens that
 * already existed and had nowhere to set what P7 step 1's migration added: the EBM tax class
 * on Tax types, the RRA quantity unit on Units of measure, the Fiscal section on Items, Verify
 * TIN on Customers and Suppliers, and the tax block on GL Defaults.
 *
 * **Initialize runs against the real `ebm-sandbox` container**, over HTTP, from the browser
 * through the app through the backend to a server answering as RRA does. Every other test of
 * that endpoint mounts the sandbox in process with a `TestClient` transport; this is the first
 * time it is reached the way a device in a shop would reach it — a URL, a network, a JSON
 * envelope — and a route table that only worked in-process would fail here and nowhere else.
 *
 * Rule 13 shapes the file as it shaped P5's and P6's. Every screen is opened with data in it
 * and a **figure** is asserted where one exists:
 *
 * * money — the fiscal item's selling price on the catalogue, `2,400`, where the column holds
 *   "2400.000000" and RWF has no decimals;
 * * quantity — the case of six's conversion factor, `6`, where the column holds
 *   "6.0000000000".
 *
 * On EBM devices there is no money and no quantity: what the screen holds is the authority's
 * own identifiers, so `SDC010000005` and `WIS01006230` are what it asserts — read off the page
 * after a real initialization, not from the fixture that seeded them.
 *
 * **The fixture is put back.** Activating a device makes the company *fiscalized*, and from
 * that moment every posting goes through the fiscal hook: an invoice without a purchase code
 * is refused, an item without a class code is refused. That would break the AR/AP tape and the
 * inventory specs from a distance, in whichever shard happened to run after this one. So the
 * last thing this file does is suspend the device — which is also the action the screen has to
 * demonstrate, and which leaves `is_fiscalized` false again.
 *
 * **What this file cannot see.** That a sale actually queues against this device, that the
 * receipt comes back, that the queue drains — none of that is reachable from a Maintenance
 * screen. It is step 7's, and proven for now by the backend acceptance tape.
 */

const SUFFIX = String(Date.now()).slice(-6);
const ITEM_CODE = `E2EFI${SUFFIX}`;
const UNIT_CODE = `BX${SUFFIX}`.slice(0, 20);
const CUSTOMER_CODE = `E2EFC${SUFFIX}`;

/** The company TIN the sandbox recognises (`KNOWN_TAXPAYERS` in `app/fiscal/rwanda/sandbox.py`).
 * A device is registered against a taxpayer number and `initialize_device` refuses a company
 * that has none (`company_tin_missing`), so this is a precondition rather than decoration. */
const COMPANY_TIN = "999000099";
/** A taxpayer the sandbox knows, and one it does not — the two answers Verify TIN has to be
 * able to show. `884` is the authority's own "no such taxpayer", which is an answer and not a
 * failure, and is the whole reason the button is worth having. */
const KNOWN_CUSTOMER_TIN = "100000001";
const UNKNOWN_TIN = "100999999";

/** Where the backend reaches the sandbox from **inside** the compose network. Not localhost:
 * the URL is dialled by the backend container, not by Playwright, so it is the service name. */
const EBM_URL = process.env.PLAYWRIGHT_EBM_URL ?? "http://ebm-sandbox:8100";

/** What the sandbox hands back on initialization. Fixed values, not random ones, so a tape can
 * assert them — and so this test can tell "the device answered" from "the screen rendered the
 * form it was given". */
const SDC_ID = "SDC010000005";
const MRC_NO = "WIS01006230";

interface Identified {
  id: number;
}

async function apiOk(
  page: Page,
  path: string,
  init: { method?: string; body?: unknown } = {},
): Promise<unknown> {
  const res = await pageFetch(page, path, init);
  expect(res.ok, `${init.method ?? "GET"} ${path} -> ${res.status}: ${JSON.stringify(res.json)}`)
    .toBe(true);
  return res.json;
}

/** The company's TIN, set through the real endpoint. `seed_e2e` provisions a tenant without
 * one — a company that has never fiscalized does not need one — so the first thing this flow
 * does is what an operator would do first: Company details, TIN. */
async function ensureCompanyTin(page: Page): Promise<void> {
  await apiOk(page, "/company", { method: "PUT", body: { tin: COMPANY_TIN } });
}

/** The device this company already holds, if any.
 *
 * One device per branch, suspended or not — so a second local run of this file would hit
 * `fiscal_device_exists` if it registered blindly. It re-uses the row instead, which is also
 * what an operator does: a device is registered once and then initialized again whenever it
 * needs new keys. */
async function existingDevice(page: Page): Promise<Identified | null> {
  const devices = (await apiOk(page, "/fiscal/devices")) as Array<Identified & { status: string }>;
  return devices[0] ?? null;
}

test.describe.configure({ mode: "serial" });

test.describe("EBM devices", () => {
  // PATH: register a device against a branch, initialize it against the sandbox over HTTP,
  // sync the code tables, and suspend it — the four things the screen does, in the order an
  // operator does them.
  // CANNOT SEE: that a sale queues against the device. Step 7's screen.
  test("a device is registered, initialized against the sandbox, synced and suspended", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    await ensureCompanyTin(page);

    await page.goto("/maintenance/ebm-devices");
    await page.waitForSelector("h1:has-text('EBM devices')");

    if ((await existingDevice(page)) === null) {
      await page.getByRole("button", { name: "Register device" }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();
      await pickCombobox(page, "Branch", "MAIN", { within: dialog });
      await dialog.getByLabel("Base URL").fill(EBM_URL);
      await dialog.getByLabel("Device serial").fill("VINEA-E2E-0001");
      await dialog.getByRole("button", { name: "Register", exact: true }).click();
      await expect(page.getByText("Device registered").first()).toBeVisible();
    } else {
      await page.reload();
      await page.waitForSelector("h1:has-text('EBM devices')");
    }

    const row = page.locator("tbody tr").first();
    await expect(row).toBeVisible();
    // Registered is not active: nothing has been said to the authority yet, and the screen
    // says exactly that rather than showing a device that looks ready to invoice.
    await expect(row).toContainText("MAIN · Head Office");

    // --- Initialize, over HTTP, against the container -------------------------------------
    await row.getByRole("button", { name: /^(Initialize|Re-initialize)$/ }).click();
    await expect(page.getByText("Device initialized and active").first()).toBeVisible();

    // Read back from the server, not from the response that just rendered.
    await page.reload();
    await page.waitForSelector("h1:has-text('EBM devices')");
    const live = page.locator("tbody tr").first();
    // The authority's own identifiers — this is the figure this screen has.
    await expect(live).toContainText(SDC_ID);
    await expect(live).toContainText(`MRC ${MRC_NO}`);
    await expect(live).toContainText("Active");
    // The keys arrived and were encrypted on the way in. What the screen can say about them
    // is that they are held; there is no field on the wire that could say more.
    await expect(live).toContainText("Keys held");

    // …and nothing on this page is a key. The three the device holds are the only secrets
    // this phase has, and `DeviceRead` has no field for one.
    const body = await page.locator("body").innerText();
    expect(body).not.toContain("sandbox-cmc-key");
    expect(body).not.toContain("sandbox-intrl-key");
    expect(body).not.toContain("sandbox-sign-key");

    // --- Sync codes: both tables, one press ------------------------------------------------
    await live.getByRole("button", { name: "Sync codes" }).click();
    await expect(page.getByText("Code tables synced").first()).toBeVisible();
    // The counts are the authority's, not ours: a sync that stored nothing would say "0
    // codes" and look identical to one that worked, which is why the toast carries them.
    await expect(page.getByText(/\d+ codes, \d+ item classes/).first()).toBeVisible();

    // The synced rows are what the other screens' pickers read, so prove they landed.
    const taxClasses = (await apiOk(page, "/fiscal/codes?code_class=04")) as Array<{
      code: string;
    }>;
    expect(taxClasses.map((row) => row.code).sort()).toEqual(["A", "B", "C", "D"]);
    const units = (await apiOk(page, "/fiscal/codes?code_class=10")) as Array<{ code: string }>;
    expect(units.length).toBeGreaterThan(0);
  });

  // PATH: the four fields the authority registers an item by, set on the catalogue and read
  // back, with the class picked from the synced classification.
  // CANNOT SEE: the registration itself — an item registers on its first fiscal use, in the
  // posting's own transaction, which is the backend tape's to prove.
  test("an item carries its fiscal class, and the catalogue shows RWF money", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);

    const categories = (await apiOk(page, "/inventory/uom-categories")) as Array<
      Identified & { code: string; uoms: Array<Identified & { code: string }> }
    >;
    const count = categories.find((c) => c.code === "COUNT")!;
    await apiOk(page, "/inventory/items", {
      method: "POST",
      body: {
        code: ITEM_CODE,
        name: `E2E Fiscal item ${SUFFIX}`,
        uom_category_id: count.id,
        base_uom_id: count.uoms.find((u) => u.code === "EA")!.id,
        item_type: "stock",
        selling_price: "2400",
      },
    });

    await page.goto("/maintenance/inventory-items");
    await page.waitForSelector("h1:has-text('Inventory items')");
    await page.getByLabel("Search").fill(ITEM_CODE);
    const row = page.locator(`tbody tr:has-text("${ITEM_CODE}")`).first();
    await expect(row).toBeVisible();

    // **The money figure.** The column holds "2400.000000"; RWF has no decimals, so the
    // screen reads 2,400. A screen printing the column verbatim renders perfectly and says
    // something untrue, which is the defect class rule 13 exists for.
    await expect(row).toContainText("2,400");

    await row.getByRole("button", { name: /^Edit/ }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    // The class comes from the authority's synced classification, searched on the server.
    const firstClass = (
      (await apiOk(page, "/fiscal/item-classes?limit=1")) as Array<{
        item_cls_cd: string;
        item_cls_nm: string;
      }>
    )[0];
    expect(firstClass, "sync codes ran in the test above and stored a classification").toBeTruthy();
    await pickCombobox(page, "Class code", firstClass.item_cls_cd, { within: dialog });
    await pickCombobox(page, "Product type", "Finished product", { within: dialog });
    await dialog.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Item updated").first()).toBeVisible();

    // Read back from the server. An item with no class is the one a fiscalized sale refuses,
    // so "the picker accepted a click" is not the thing worth asserting.
    const items = (await apiOk(page, `/inventory/items?search=${ITEM_CODE}`)) as Array<{
      code: string;
      fiscal_class_code: string | null;
      fiscal_item_type: string | null;
    }>;
    const saved = items.find((item) => item.code === ITEM_CODE)!;
    expect(saved.fiscal_class_code).toBe(firstClass.item_cls_cd);
    expect(saved.fiscal_item_type).toBe("2");

    // …and the registration pair reads what RRA holds, which is nothing yet: this item has
    // never been sold, so it has never been registered.
    await page.reload();
    await page.waitForSelector("h1:has-text('Inventory items')");
    await page.getByLabel("Search").fill(ITEM_CODE);
    await page.locator(`tbody tr:has-text("${ITEM_CODE}")`).first()
      .getByRole("button", { name: /^Edit/ })
      .click();
    await expect(page.getByRole("dialog").getByText("Not registered")).toBeVisible();
  });

  // PATH: the EBM tax class on a seeded VAT code, and the rate beside it.
  // CANNOT SEE: that a receipt reports under this class — the tape's.
  test("tax types show and edit the EBM class", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/maintenance/taxes");
    await page.waitForSelector("h1:has-text('Tax types')");

    // Seeded by the Rwanda pack at P7 step 1, and this is the first screen that shows it.
    const standard = page.locator('tbody tr:has-text("VAT-OUT-18")').first();
    await expect(standard).toBeVisible();
    await expect(standard).toContainText("18%");
    await expect(standard).toContainText("B");
    await expect(page.locator('tbody tr:has-text("VAT-EXEMPT")').first()).toContainText("A");
    await expect(page.locator('tbody tr:has-text("VAT-ZERO")').first()).toContainText("C");

    // Editable, and deliberately not locked by postings the way the rate is: the class only
    // decides which bucket of RRA's report a line lands in, and a company that mapped the
    // wrong one has to be able to correct it.
    await standard.getByRole("button", { name: /^Edit/ }).click();
    const dialog = page.getByRole("dialog");
    await pickCombobox(page, "EBM class", "D", { within: dialog });
    await dialog.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Tax code updated").first()).toBeVisible();
    await page.reload();
    await page.waitForSelector("h1:has-text('Tax types')");
    await expect(page.locator('tbody tr:has-text("VAT-OUT-18")').first()).toContainText("D");

    // Put it back: B is what the seed ships and what the acceptance tape and every other spec
    // assume, so a run that left D behind would break them from a distance.
    await page.locator('tbody tr:has-text("VAT-OUT-18")').first()
      .getByRole("button", { name: /^Edit/ })
      .click();
    await pickCombobox(page, "EBM class", "B", { within: page.getByRole("dialog") });
    await page.getByRole("dialog").getByRole("button", { name: "Save", exact: true }).click();
    await page.reload();
    await expect(page.locator('tbody tr:has-text("VAT-OUT-18")').first()).toContainText("B");
  });

  // PATH: a new unit with the authority's quantity code on it, and the factor rendered.
  // CANNOT SEE: `fiscal_uom_unmapped` on a posting — the backend suite's.
  test("a unit of measure carries the RRA quantity code, beside its factor", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/maintenance/uom-categories");
    await page.waitForSelector("h1:has-text('Units of measure')");

    const card = page.locator("section", { hasText: "COUNT · Count" }).first();
    await card.getByRole("button", { name: "Add unit" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Code").fill(UNIT_CODE);
    await dialog.getByLabel("Name").fill(`Case of six ${SUFFIX}`);
    await dialog.getByLabel("Units per base").fill("6");
    // The authority's §4.6 list, synced by the device in the first test — not typed.
    await pickCombobox(page, "RRA quantity unit", "BX", { within: dialog });
    await dialog.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Unit created").first()).toBeVisible();

    await page.reload();
    await page.waitForSelector("h1:has-text('Unit of measure categories')");
    const row = page.locator(`tbody tr:has-text("${UNIT_CODE}")`).first();
    await expect(row).toBeVisible();
    // **The quantity figure.** The column holds "6.0000000000"; the screen reads 6.
    await expect(row).toContainText("6");
    await expect(row).toContainText("BX");
  });

  // PATH: Verify TIN on a customer, both answers.
  // CANNOT SEE: what a real RRA does with a TIN it half-knows. The sandbox has two states.
  test("verify TIN answers for a taxpayer the authority knows and one it does not", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    await apiOk(page, "/subledger/ar/partners", {
      method: "POST",
      body: {
        name: `E2E Fiscal customer ${SUFFIX}`,
        customer_code: CUSTOMER_CODE,
        tin: KNOWN_CUSTOMER_TIN,
      },
    });

    await page.goto("/maintenance/customers");
    await page.waitForSelector("h1:has-text('Customers')");
    await page.getByLabel("Search customers").fill(CUSTOMER_CODE);
    await page
      .locator(`tbody tr:has-text("${CUSTOMER_CODE}")`)
      .first()
      .getByRole("button", { name: /^Edit/ })
      .click();
    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();

    await drawer.getByRole("button", { name: "Verify TIN" }).click();
    // The authority's own name for the taxpayer, not ours — which is the point of asking.
    await expect(page.getByText("Customer C Ltd")).toBeVisible();

    // …and the answer that matters most before invoicing: RRA has never heard of this one.
    await drawer.getByLabel("TIN").fill(UNKNOWN_TIN);
    await drawer.getByRole("button", { name: "Verify TIN" }).click();
    await expect(
      page.getByText("The authority holds no taxpayer with this number"),
    ).toBeVisible();
  });

  // PATH: the six settings the Defaults screen gained, resolved to `code · name` rather than
  // "Not set", and one of them changed and read back.
  // CANNOT SEE: the VAT filing that uses them. Step 7's screen.
  test("GL defaults show the seeded P7 accounts and offer no control account", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/maintenance/defaults");
    await page.waitForSelector("h1:has-text('Defaults')");

    // Seeded by the Rwanda pack, resolved to the label a picker renders. An empty picker or a
    // "Not set" over a configured company is exactly the defect `api-enums.ts` was generated
    // to make impossible, so these are the resolved labels and not "is something there".
    await expect(page.getByText("2250 · VAT Payable (RRA)")).toBeVisible();
    await expect(page.getByText("1290 · AR Revaluation")).toBeVisible();
    await expect(page.getByText("2190 · AP Revaluation")).toBeVisible();
    await expect(page.getByText("4410 · Unrealized Foreign Exchange Gain")).toBeVisible();
    await expect(page.getByText("6955 · Unrealized Foreign Exchange Loss")).toBeVisible();

    // **The typeahead assertion**, the way P6 step 6 pinned its three. Each picker offers a
    // different list and none of them offers a control account: the AR revaluation contra is
    // an asset and must not be 1200, whose balance is the sum of open items at their booking
    // rates — the invariant a revaluation posted there would break.
    await page.getByRole("button", { name: "AR revaluation", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type("1200");
    await expect(page.locator("[cmdk-item]")).toHaveCount(0);
    await page.keyboard.press("Escape");

    // The VAT settlement account offers liabilities and nothing else, so an asset is not on
    // the menu at all — `invalid_gl_setting_account_class` is the same rule server-side.
    await page.getByRole("button", { name: "VAT settlement", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type("1110");
    await expect(page.locator("[cmdk-item]")).toHaveCount(0);
    await page.keyboard.press("Escape");

    // One key changed and read back from the server.
    await pickCombobox(page, "Unrealized FX loss", "6955");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Saved successfully").first()).toBeVisible();
    await page.reload();
    await page.waitForSelector("h1:has-text('Defaults')");
    await expect(page.getByText("6955 · Unrealized Foreign Exchange Loss")).toBeVisible();
  });

  // PATH: suspend, and leave the fixture unfiscalized.
  // This is last in a `serial` describe deliberately: an active device changes what every
  // other spec's postings are allowed to do, and a shard that ran this file and then the
  // AR/AP tape would find the tape refused for reasons that have nothing to do with it.
  test("the device is suspended, and the company is unfiscalized again", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/maintenance/ebm-devices");
    await page.waitForSelector("h1:has-text('EBM devices')");

    const row = page.locator("tbody tr").first();
    await row.getByRole("button", { name: "Suspend" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason").fill("End of the P7 step 6 e2e run");
    await dialog.getByRole("button", { name: "Suspend", exact: true }).click();
    await expect(page.getByText("Device suspended").first()).toBeVisible();

    await page.reload();
    await page.waitForSelector("h1:has-text('EBM devices')");
    const suspended = page.locator("tbody tr").first();
    await expect(suspended).toContainText("Suspended");
    // Initialize is the only way back: the button is there and it says so, and there is no
    // Activate anywhere on the screen.
    await expect(suspended.getByRole("button", { name: "Re-initialize" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Activate" })).toHaveCount(0);
  });
});
