import { execFileSync } from "node:child_process";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { API_BASE, pageFetch, pickCombobox, pickDate, waitForHydration } from "./support/fixtures";

/**
 * **The phase's tape, through the screens** (P7 step 9).
 *
 * `backend/tests/fiscal/test_acceptance_tape.py` drives the nineteen rows of decision 5's tape
 * through the *services* against the in-process sandbox, and asserts every figure against a
 * table worked by hand. This spec drives the same chain through the *screens*, against the
 * `ebm-sandbox` **container** over HTTP, and asserts the same kind of figures as **rendered
 * strings** and as text on printed paper. Neither reads the other's answer.
 *
 * The chain, in the prompt's order: initialize → register items → invoice with a purchase code
 * → the queue shows it sent → the document prints with its SDC block → a copy print → a credit
 * note with a §4.16 reason → the authority goes down, Retry fails, comes back, Retry succeeds →
 * an `accept_then_timeout` row verified and attached → a supplier invoice registered → the feed
 * accepted → the day closed and its Z tied to the receipts listing → the VAT return tied and
 * filed → a backdated entry landing as a late entry on the next return while the filed one does
 * not move → the revaluation previewed, posted, its mirror named, reversed → and a company with
 * no device posting an invoice that queues nothing and prints the P4 layout.
 *
 * **A tenant of its own**, signed up here rather than the seeded fixture company — which is
 * what lets this file assert the literals no other P7 spec may: `1/1 NS`, `Z-000001`,
 * `VATR-000001`, `FXR-000001`. On Rugari Wines E2E those runs are consumed by whatever ran
 * before, so steps 7 and 8 assert shape; here they are the first of their kind and the number
 * *is* the claim.
 *
 * **And a branch of its own on the authority's side.** The sandbox keys its ledger by
 * `tin:bhfId` and only four TINs exist, so a second company using `999000099` at `bhfId 00`
 * would share receipt counters with the specs that ran before it. This device registers at
 * `bhfId 01`, so its counters start at one and `1/1 NS` means what it says however the shards
 * fall.
 *
 * **Awkward prices, on purpose.** 1 499 exclusive is 1 768.82 inclusive, which is what the wire
 * carries at two decimals while the ledger rounds to the franc. Every declared figure below
 * therefore differs from its posted one, the Z names the residue, and a tape written on round
 * numbers would have proved the arithmetic only in the case where it cannot be wrong.
 *
 * CANNOT SEE: the `osdc` route profile (the backend tape runs rows 0–3 under it), the property
 * machine's random sequences, and a live device — the phase closes *code-complete,
 * certification pending*, and `docs/rra/certification.md` says what remains.
 */

const SUFFIX = String(Date.now()).slice(-6);
const OWNER_EMAIL = `e2e.fiscal.tape.${SUFFIX}@vinea.example`;
const OWNER_PASSWORD = "a fiscal tape passphrase for one company";
const COMPANY = `Fiscal Tape Co ${SUFFIX}`;
/** The company with no device: the other half of every screen in this phase. */
const PLAIN_EMAIL = `e2e.fiscal.plain.${SUFFIX}@vinea.example`;
const PLAIN_COMPANY = `Unfiscalized Co ${SUFFIX}`;

const ITEM_CODE = `TAPE${SUFFIX}`;
const CUSTOMER_CODE = `TC${SUFFIX}`;
const SUPPLIER_CODE = `TS${SUFFIX}`;
const FX_CUSTOMER_CODE = `TX${SUFFIX}`;
const FX_CURRENCY = `Z${SUFFIX.slice(-2)}`;

/** The taxpayers the sandbox knows (`KNOWN_TAXPAYERS` in `app/fiscal/rwanda/sandbox.py`). */
const COMPANY_TIN = "999000099";
const CUSTOMER_TIN = "100000001";
const SUPPLIER_TIN = "100000002";
const FEED_SUPPLIER_TIN = "100000003";

const PURCHASE_CODE = "AB12CD";
const REFUND_REASON = "06";

/** 1 499 exclusive; 1 768.82 inclusive at 18 %. */
const PRICE = "1499";
const COST = "900";

const EBM_URL = process.env.PLAYWRIGHT_EBM_URL ?? "http://ebm-sandbox:8100";
const SANDBOX_ADMIN = process.env.PLAYWRIGHT_EBM_ADMIN_URL ?? "http://localhost:8100";
const SDC_ID = "SDC010000005";
const MRC_NO = "WIS01006230";
/** This device's own branch on the authority's side — see the file header. */
const BHF_ID = "01";

/**
 * Every figure the tape asserts, worked by hand from 1 499 exclusive and 1 768.82 inclusive.
 *
 * Nothing here is read back from the code that computes it. The ledger rounds each document's
 * tax half-up to the franc (RWF has no minor unit); the wire carries the inclusive price at two
 * decimals and extends it by the quantity. The two differ per document, and the last column is
 * the whole reason decision 11 stores `declared_less_posted`.
 *
 *   document      qty   net     tax                 posted   declared            residue
 *   INV-1           3   4 497   809.46  ->     809   5 306    3 x 1 768.82 =  5 306.46   +0.46
 *   CRN-1           1   1 499   269.82  ->     270   1 769    1 x 1 768.82 =  1 768.82   +0.18 back
 *   INV-2           2   2 998   539.64  ->     540   3 538    2 x 1 768.82 =  3 537.64   -0.36
 *   INV-3           1   1 499   269.82  ->     270   1 769    1 x 1 768.82 =  1 768.82   -0.18
 *
 * The day: three sales and one refund.
 *   ns_gross  5 306.46 + 3 537.64 + 1 768.82 = 10 612.92
 *   nr_gross                                  =  1 768.82
 *   net                                       =  8 844.10
 *   posted    5 306 + 3 538 + 1 769 - 1 769   =  8 844
 *   residue                                   =      0.10
 */
const DECLARED = {
  invoice1: "5,306.46",
  creditNote: "1,768.82",
  invoice2: "3,537.64",
  invoice3: "1,768.82",
  salesTotal: "10,612.92",
  net: "8,844.10",
};
const POSTED = { invoice1: "5,306", dayNet: "8,844", residue: "0.10" };

/**
 * The VAT return, from the same four documents plus one supplier invoice.
 *
 *   output VAT   809 + 540 + 270 - 270   = 1,349   on base 4 497 + 2 998 + 1 499 - 1 499 = 7 495
 *   input VAT    2 x 900 = 1 800 @ 18 %  =   324
 *   net payable  1 349 - 324             = 1,025
 */
const VAT = { output: "1,349", input: "324", net: "1,025" };
/** The late entry: Cr 4100 1 000 with `VAT-OUT-18`, so 180 of output VAT, posted after filing. */
const LATE = { base: "1,000" };

/** The revaluation: 10 x 2.00 = 20.00 net, 18 % = 3.60, so 23.60 gross on the foreign side.
 * Carried at round(23.60 x 1 320) = 31 152 and revalued at round(23.60 x 1 350) = 31 860. */
const BOOKING_RATE = 1320;
const RATE_AT_MONTH_END = 1350;
const FX_DIFFERENCE = "708";

/** `yyyy-mm-dd` in **local** time, the way `lib/format`'s `todayIso()` does it — never
 * `toISOString()`, which renders UTC and is the previous day at 00:30 in Kigali.
 * `src/lib/no-utc-dates.test.ts` scans `e2e/` for exactly that. */
function iso(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

const today = () => iso(new Date());
const monthStart = () => iso(new Date(new Date().getFullYear(), new Date().getMonth(), 1));
const monthEnd = () => iso(new Date(new Date().getFullYear(), new Date().getMonth() + 1, 0));
const nextMonthStart = () => iso(new Date(new Date().getFullYear(), new Date().getMonth() + 1, 1));
const nextMonthEnd = () => iso(new Date(new Date().getFullYear(), new Date().getMonth() + 2, 0));

interface Identified {
  id: number;
}

async function apiOk(
  page: Page,
  path: string,
  init: { method?: string; body?: unknown; headers?: Record<string, string> } = {},
): Promise<unknown> {
  const res = await pageFetch(page, path, init);
  expect(res.ok, `${init.method ?? "GET"} ${path} -> ${res.status}: ${JSON.stringify(res.json)}`)
    .toBe(true);
  return res.json;
}

/** Sign in one of this file's two fresh owners. Not `login()` from the fixtures: that one's
 * failure message is about the seeded credential and a drift that cannot apply to a company
 * created a second ago. */
async function signIn(page: Page, email: string) {
  await page.goto("/login");
  await waitForHydration(page, "form");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', OWNER_PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL("/");
  await page.waitForSelector("text=Good morning");
}

/** Drain now rather than waiting on the worker's fifteen seconds. The one endpoint in the
 * phase that is `by design` in the rule-14 register, and this is the second reason it is. */
async function drain(page: Page): Promise<void> {
  await apiOk(page, "/fiscal/outbox/drain", { method: "POST" });
}

/**
 * Forget everything the authority is holding.
 *
 * The sandbox keeps its ledger in the **container's memory** and `make db-reset` does not touch
 * it, so a second run against a reset database would start Vinea's `FIS` run at 1 again while
 * the authority still remembers invoice 1 — `994: the invoice number is already registered`,
 * which is the sandbox behaving correctly and the fixture being stale.
 */
async function sandboxReset(request: APIRequestContext): Promise<void> {
  const res = await request.post(`${SANDBOX_ADMIN}/_sandbox/reset`);
  expect(res.ok(), `sandbox reset -> ${res.status()}`).toBe(true);
}

async function sandboxMode(request: APIRequestContext, mode: string): Promise<void> {
  const res = await request.post(`${SANDBOX_ADMIN}/_sandbox/mode`, { data: { mode } });
  expect(res.ok(), `sandbox mode ${mode} -> ${res.status()}`).toBe(true);
}

/**
 * The receipt the authority issued for an invoice number, off its own ledger — **the portal's
 * stand-in**. An operator resolving a `needs_receipt` row reads the six fields off MyRRA and
 * keys them; there is no endpoint that hands them over and there must not be, because the whole
 * point of `needs_receipt` is that Vinea does not know what RRA holds.
 */
async function sandboxReceipt(
  request: APIRequestContext,
  invoiceNo: number,
): Promise<Record<string, string>> {
  const res = await request.get(`${SANDBOX_ADMIN}/_sandbox/ledger`);
  expect(res.ok(), `sandbox ledger -> ${res.status()}`).toBe(true);
  const ledgers = (await res.json()) as Record<string, { sales: Record<string, unknown> }>;
  for (const ledger of Object.values(ledgers)) {
    const sale = ledger.sales?.[String(invoiceNo)];
    if (sale) return sale as Record<string, string>;
  }
  throw new Error(`the authority holds no sale ${invoiceNo}`);
}

/** How many rows this company's devices are still holding. */
async function pendingRows(page: Page): Promise<number> {
  const devices = (await apiOk(page, "/fiscal/queue")) as Array<{ pending_rows: number }>;
  return devices.reduce((total, device) => total + device.pending_rows, 0);
}

/**
 * Press **Retry now** on every waiting row until the queue is empty — through the screen, never
 * through a `fetch`. Bounded rather than `while`: a queue that never clears is a failure to
 * report, not a loop to spin in.
 */
async function releaseTheQueue(page: Page): Promise<void> {
  for (let pass = 0; pass < 8; pass += 1) {
    if ((await pendingRows(page)) === 0) return;
    await page.goto("/fiscal/queue");
    await page.waitForSelector("h1:has-text('Fiscal queue')");
    const buttons = page.getByRole("button", { name: "Retry now", exact: true });
    const count = await buttons.count();
    for (let index = 0; index < count; index += 1) {
      const button = buttons.nth(index);
      if (await button.isEnabled()) await button.click();
    }
    await drain(page);
  }
  expect(await pendingRows(page), "the queue never cleared").toBe(0);
}

/**
 * The current page, printed, as text.
 *
 * `page.pdf()` renders with `print` media, which is the only way to see what the document
 * screen actually puts on paper: the CIS receipt is `hidden print:block` and everything else is
 * `print:hidden`, so a DOM assertion on the screen proves nothing about the sheet.
 */
async function printedText(page: Page, path: string): Promise<string> {
  await page.pdf({ path, format: "A4", printBackground: false });
  return execFileSync("pdftotext", ["-layout", path, "-"], { encoding: "utf8" });
}

/** Post an AR invoice through the capture screen, and return the posted document's id. */
async function keyInvoice(
  page: Page,
  { customer, quantity, description }: { customer: string; quantity: string; description: string },
): Promise<number> {
  await page.goto("/ar/invoices/new");
  await page.waitForSelector("h1:has-text('Invoice')");
  await pickCombobox(page, "Customer", customer);
  await page.getByLabel("Description", { exact: true }).fill(description);
  await page.getByTestId("purchase-code").fill(PURCHASE_CODE);
  await page.getByRole("button", { name: "Item, row 1", exact: true }).click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(ITEM_CODE);
  await page.locator(`[cmdk-item]:has-text("${ITEM_CODE}")`).first().click();
  await page.getByLabel("Quantity, row 1", { exact: true }).fill(quantity);
  await page.getByLabel("Unit price, row 1", { exact: true }).fill(PRICE);
  await page.getByRole("button", { name: /^Post/ }).click();
  await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 60_000 });

  const documents = (await apiOk(page, "/subledger/ar/documents?kind=invoice")) as {
    items: Array<Identified & { description: string }>;
  };
  const posted = documents.items.find((row) => row.description === description);
  expect(posted, `no posted invoice described "${description}"`).toBeTruthy();
  return posted!.id;
}

test.describe.configure({ mode: "serial", timeout: 600_000 });

test.describe("the fiscal cycle, through the screens, tied to the tape", () => {
  let deviceId = 0;
  let itemId = 0;
  let customerId = 0;
  let supplierId = 0;
  let invoice1Id = 0;
  let invoice3Id = 0;

  test.beforeAll(async ({ request }) => {
    for (const [email, company, name] of [
      [OWNER_EMAIL, COMPANY, "Fiscal Tape Owner"],
      [PLAIN_EMAIL, PLAIN_COMPANY, "Unfiscalized Owner"],
    ] as const) {
      const signup = await request.post(`${API_BASE}/auth/signup`, {
        data: { email, password: OWNER_PASSWORD, full_name: name, company_name: company },
      });
      expect(signup.ok(), await signup.text()).toBe(true);
    }
  });

  // PATH: /maintenance/ebm-devices -> register, Initialize (over HTTP, against the container),
  // Sync codes; then the catalogue the first receipt line needs.
  // CANNOT SEE: that anything queues yet. Nothing has been posted.
  test("row 0 — the device is registered, initialized and synced, and the catalogue is ready", async ({
    page,
    request,
  }) => {
    await sandboxReset(request);
    await signIn(page, OWNER_EMAIL);

    // A device is registered against a taxpayer number: the company's TIN is a precondition,
    // not decoration (`company_tin_missing`).
    await apiOk(page, "/company", { method: "PATCH", body: { tin: COMPANY_TIN } });

    await page.goto("/maintenance/ebm-devices");
    await page.waitForSelector("h1:has-text('EBM devices')");
    await page.getByRole("button", { name: "Register device" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await pickCombobox(page, "Branch", "MAIN", { within: dialog });
    await dialog.getByLabel("Base URL").fill(EBM_URL);
    await dialog.getByLabel("Device serial").fill(`VINEA-TAPE-${SUFFIX}`);
    await dialog.getByLabel("Authority branch id").fill(BHF_ID);
    await dialog.getByRole("button", { name: "Register", exact: true }).click();
    await expect(page.getByText("Device registered").first()).toBeVisible();

    const row = page.locator("tbody tr").first();
    await row.getByRole("button", { name: /^(Initialize|Re-initialize)$/ }).click();
    await expect(page.getByText("Device initialized and active").first()).toBeVisible();

    await page.reload();
    await page.waitForSelector("h1:has-text('EBM devices')");
    const live = page.locator("tbody tr").first();
    // The authority's own identifiers, read off the page after a real initialization.
    await expect(live).toContainText(SDC_ID);
    await expect(live).toContainText(`MRC ${MRC_NO}`);
    await expect(live).toContainText("Active");
    await expect(live).toContainText("Keys held");

    await live.getByRole("button", { name: "Sync codes" }).click();
    await expect(page.getByText(/\d+ codes, \d+ item classes/).first()).toBeVisible();

    deviceId = ((await apiOk(page, "/fiscal/devices")) as Identified[])[0].id;

    // --- the four things a receipt line needs (decision 8) --------------------------------
    const categories = (await apiOk(page, "/inventory/uom-categories")) as Array<
      Identified & { code: string; uoms: Array<Identified & { code: string }> }
    >;
    const count = categories.find((c) => c.code === "COUNT")!;
    const each = count.uoms.find((u) => u.code === "EA")!;
    await apiOk(page, `/inventory/uoms/${each.id}`, {
      method: "PATCH",
      body: { fiscal_quantity_unit: "U" },
    });

    const taxCodes = (await apiOk(page, "/gl/tax-codes")) as Array<
      Identified & { code: string; fiscal_tax_type: string | null }
    >;
    const outputVat = taxCodes.find((code) => code.code === "VAT-OUT-18")!;
    const inputVat = taxCodes.find((code) => code.code === "VAT-IN-18")!;
    // The seed pack gives a new company its EBM classes; a sale line whose code has none is
    // refused `tax_class_unmapped`, because RRA reports every line under one of A–D.
    expect(outputVat.fiscal_tax_type).toBe("B");

    const accounts = (await apiOk(page, "/gl/accounts")) as Array<Identified & { code: string }>;
    const byCode = new Map(accounts.map((a) => [a.code, a.id]));
    itemId = (
      (await apiOk(page, "/inventory/items", {
        method: "POST",
        body: {
          code: ITEM_CODE,
          name: `Tape wine ${SUFFIX}`,
          uom_category_id: count.id,
          base_uom_id: each.id,
          item_type: "stock",
          selling_price: PRICE,
          sales_account_id: byCode.get("4100"),
          cogs_account_id: byCode.get("5100"),
          default_sales_tax_code_id: outputVat.id,
          default_purchase_tax_code_id: inputVat.id,
          fiscal_class_code: "5059020800",
        },
      })) as Identified
    ).id;

    for (const [role, code, tin] of [
      ["ar", CUSTOMER_CODE, CUSTOMER_TIN],
      ["ap", SUPPLIER_CODE, SUPPLIER_TIN],
    ] as const) {
      await apiOk(page, `/subledger/${role}/partners`, {
        method: "POST",
        body: {
          name: `Tape ${role === "ar" ? "customer" : "supplier"} ${SUFFIX}`,
          [role === "ar" ? "customer_code" : "supplier_code"]: code,
          tin,
          phone: "+250788000001",
        },
      });
    }
    customerId = (
      (await apiOk(page, "/subledger/ar/partners")) as Array<
        Identified & { customer_code: string | null }
      >
    ).find((p) => p.customer_code === CUSTOMER_CODE)!.id;
    supplierId = (
      (await apiOk(page, "/subledger/ap/partners")) as Array<
        Identified & { supplier_code: string | null }
      >
    ).find((p) => p.supplier_code === SUPPLIER_CODE)!.id;

    // Opening stock. A fiscalized company's negative-stock policy is locked at `block`
    // (CIS §7.30 — no receipt for goods the stock does not hold), so a sale with nothing behind
    // it would be refused before it ever reached the queue.
    const warehouses = (await apiOk(page, "/inventory/warehouses")) as Array<
      Identified & { code: string }
    >;
    const mainWarehouse = warehouses.find((w) => w.code === "MAIN") ?? warehouses[0];
    await apiOk(page, "/oe/goods-received-notes", {
      method: "POST",
      headers: { "Idempotency-Key": `p7-tape-grn-${SUFFIX}` },
      body: {
        partner_id: supplierId,
        grn_date: today(),
        description: `Tape opening stock ${SUFFIX}`,
        // **MAIN by code, never `warehouses[0]`.** The listing is ordered by code and
        // `inventory-reports.spec.ts` creates a `DEPOT`, which sorts *before* `MAIN` — so index 0
        // seeds the stock somewhere the sale will not look, and the sale is refused
        // `insufficient_stock` on a company holding plenty. It passes alone and fails behind that
        // spec, which makes it a test about the suite's order rather than about the product.
        warehouse_id: mainWarehouse.id,
        lines: [{ item_id: itemId, quantity: "100", unit_cost: COST }],
      },
    });
    await drain(page);
  });

  // PATH: /ar/invoices/new -> /fiscal/queue -> /ar/documents/{id} -> Print -> Copy print.
  // CANNOT SEE: the Z these receipts land on. Two tests along.
  test("rows 1 and 3b — the invoice is signed, prints its SDC block, and reprints as a COPY", async ({
    page,
  }) => {
    await signIn(page, OWNER_EMAIL);

    invoice1Id = await keyInvoice(page, {
      customer: CUSTOMER_CODE,
      quantity: "3",
      description: `Tape invoice one ${SUFFIX}`,
    });
    await drain(page);

    // --- the queue says it went ------------------------------------------------------------
    await page.goto("/fiscal/queue");
    await page.waitForSelector("h1:has-text('Fiscal queue')");
    await expect(page.getByTestId(`pending-${deviceId}`)).toHaveText("0");
    await expect(page.getByText("Flowing").first()).toBeVisible();
    await expect(page.getByText("Sent").first()).toBeVisible();

    // --- the document, and the counter that is this company's first ------------------------
    await page.goto(`/ar/documents/${invoice1Id}`);
    await page.waitForSelector("[data-testid='fiscal-receipt-number']");
    await expect(page.getByTestId("document-total")).toContainText(POSTED.invoice1);
    await expect(page.getByTestId("fiscal-status")).toContainText("Sent");
    // **The literal the rest of the phase may not assert.** This company's device is the first
    // thing to speak to `999000099:01`, so the authority's counters start here.
    await expect(page.getByTestId("fiscal-receipt-number")).toHaveText("1/1 NS");
    await expect(page.getByTestId("fiscal-sdc-id")).toHaveText(SDC_ID);
    await expect(page.getByTestId("fiscal-copy-count")).toHaveText("0");

    // --- and what is actually on the paper --------------------------------------------------
    const paper = await printedText(page, `test-results/p7-tape-original-${SUFFIX}.pdf`);
    expect(paper, "the SDC block, §7.24").toContain("SDC INFORMATION");
    expect(paper, "the receipt counter and its label, §7.25").toContain("1/1 NS");
    expect(paper, "the device the authority signed with").toContain(SDC_ID);
    expect(paper, "the MRC, §3.2.1.ii").toContain(MRC_NO);
    // The money figure, off the sheet: RWF has no minor unit, so the declared 5 306.46 prints
    // as whole francs on a receipt a customer is handed.
    expect(paper, "the total").toContain(POSTED.invoice1);
    expect(paper, "the purchase code the business customer was invoiced under").toContain(
      PURCHASE_CODE,
    );
    // An original is not a copy, and says nothing about not being one.
    expect(paper).not.toContain("THIS IS NOT AN OFFICIAL RECEIPT");

    // --- the copy: §11 and §15 --------------------------------------------------------------
    await page.getByTestId("copy-print").click();
    await page.getByTestId("confirm-copy-print").click();
    await expect(page.getByTestId("fiscal-copy-count")).toHaveText("1");

    const copy = await printedText(page, `test-results/p7-tape-copy-${SUFFIX}.pdf`);
    expect(copy, "the watermark, §11").toContain("COPY");
    expect(copy, "the warning under the totals, §15").toContain(
      "THIS IS NOT AN OFFICIAL RECEIPT",
    );
    // **The same SDC block**: a copy is a reprint of one signature, not a second sale. Nothing
    // went to RRA for it (v1.0.5 sends `N` only), which is why the counter is unchanged.
    expect(copy).toContain("1/1 NS");
    expect(copy).toContain(SDC_ID);
  });

  // PATH: /ar/credit-notes/new -> the Refund of picker and the §4.16 reason -> the NR receipt.
  test("row 3 — the credit note names the invoice it refunds and why", async ({ page }) => {
    await signIn(page, OWNER_EMAIL);
    await page.goto("/ar/credit-notes/new");
    await page.waitForSelector("h1:has-text('Credit note')");

    await pickCombobox(page, "Customer", CUSTOMER_CODE);
    await page.getByLabel("Description", { exact: true }).fill(`Tape refund ${SUFFIX}`);
    await page.getByTestId("purchase-code").fill(PURCHASE_CODE);

    const fiscal = page.getByTestId("document-fiscal");
    await fiscal.getByRole("button", { name: "Refund of", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.locator("[cmdk-item]").filter({ hasText: "INV-" }).first().click();
    // Thirteen reasons, the authority's own names, synced rather than typed.
    await pickCombobox(page, "Refund reason", REFUND_REASON, { within: fiscal });

    await page.getByRole("button", { name: "Item, row 1", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(ITEM_CODE);
    await page.locator(`[cmdk-item]:has-text("${ITEM_CODE}")`).first().click();
    await page.getByLabel("Quantity, row 1", { exact: true }).fill("1");
    await page.getByLabel("Unit price, row 1", { exact: true }).fill(PRICE);
    await page.getByRole("button", { name: /^Post/ }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 60_000 });
    await drain(page);

    const documents = (await apiOk(page, "/subledger/ar/documents?kind=credit_note")) as {
      items: Array<Identified & { description: string }>;
    };
    const creditNote = documents.items.find((row) => row.description === `Tape refund ${SUFFIX}`)!;
    await page.goto(`/ar/documents/${creditNote.id}`);
    await page.waitForSelector("[data-testid='fiscal-receipt-number']");
    // The refund run has its own counter and shares the total: `1/2 NR` (decision 5).
    await expect(page.getByTestId("fiscal-receipt-number")).toHaveText("1/2 NR");
  });

  // PATH: the authority goes down under a posted invoice, Retry now fails while it is down, and
  // succeeds when it comes back — decision 4's backoff and the button that exists for it.
  test("row 5 — the sale survives the authority being down, and lands when it returns", async ({
    page,
    request,
  }) => {
    await signIn(page, OWNER_EMAIL);
    await sandboxMode(request, "down");

    const invoice2Id = await keyInvoice(page, {
      customer: CUSTOMER_CODE,
      quantity: "2",
      description: `Tape invoice two ${SUFFIX}`,
    });
    await drain(page);

    // Print is refused while the receipt does not exist — CIS §10, and the reason on the button
    // is the row's own status rather than a generic refusal.
    await page.goto(`/ar/documents/${invoice2Id}`);
    await page.waitForSelector("[data-testid='document-total']");
    await expect(page.getByTestId("fiscal-status")).toContainText("Queued");
    await expect(page.getByTestId("report-print")).toBeDisabled();
    await expect(page.getByTestId("report-print")).toHaveAttribute("title", /queued/i);

    // **Retry now, while it is still down**: the row is tried and stays queued. This is the
    // press that proves the button reaches the authority rather than marking the row by hand.
    await page.goto("/fiscal/queue");
    await page.waitForSelector("h1:has-text('Fiscal queue')");
    // **The queued row's own button**, not the first one on the page. Retry is offered for
    // `queued` and `failed` rows only, and the rows are in send order — so the first button in
    // the table belongs to an item registration that was signed hours of tape ago and is
    // disabled. A bare `click()` on a disabled control waits for it to become enabled with **no
    // timeout** (`playwright.config.ts` sets none for actions), so getting this wrong is a hang
    // rather than a message.
    const queuedRow = page.locator("tbody tr").filter({ hasText: "Queued" }).first();
    await expect(queuedRow).toBeVisible();
    const retry = queuedRow.getByRole("button", { name: "Retry now", exact: true });
    await expect(retry).toBeEnabled();
    await retry.click();
    await drain(page);
    expect(await pendingRows(page), "an unreachable authority signs nothing").toBeGreaterThan(0);

    await sandboxMode(request, "up");
    await releaseTheQueue(page);

    await page.goto(`/ar/documents/${invoice2Id}`);
    await page.waitForSelector("[data-testid='fiscal-receipt-number']");
    await expect(page.getByTestId("fiscal-receipt-number")).toHaveText("2/3 NS");
    await expect(page.getByTestId("report-print")).toBeEnabled();
  });

  // PATH: /fiscal/queue — an `unknown` row verified with the device and its receipt attached by
  // hand. Tape rows 6 and 6b, through the screens.
  test("row 6 — an unanswered sale is verified and its receipt attached", async ({
    page,
    request,
  }) => {
    await signIn(page, OWNER_EMAIL);
    // The state `unknown` exists for: RRA registers the sale and the answer never arrives. A
    // retry would be the duplicate the policy is written to prevent — `994` returns no receipt
    // data — which is why the screen does not offer one.
    await sandboxMode(request, "accept_then_timeout");

    invoice3Id = ((await apiOk(page, "/subledger/ar/documents", {
      method: "POST",
      headers: { "Idempotency-Key": `p7-tape-unknown-${SUFFIX}` },
      body: {
        kind: "invoice",
        partner_id: customerId,
        document_date: today(),
        description: `Tape invoice three ${SUFFIX}`,
        purchase_code: PURCHASE_CODE,
        lines: [{ item_id: itemId, quantity: "1", unit_price: PRICE }],
      },
    })) as Identified).id;
    await drain(page);
    await sandboxMode(request, "up");

    await page.goto("/fiscal/queue");
    await page.waitForSelector("h1:has-text('Fiscal queue')");
    await expect(page.getByText("Blocked").first()).toBeVisible();
    const unknownRow = page.locator("tbody tr").filter({ hasText: "No answer" }).first();
    await expect(unknownRow).toBeVisible();
    await expect(unknownRow.getByRole("button", { name: "Retry now", exact: true })).toBeDisabled();

    await unknownRow.getByRole("button", { name: "Verify with device", exact: true }).click();
    await expect(page.getByText("The device was asked what it holds").first()).toBeVisible();
    await page.reload();
    await page.waitForSelector("h1:has-text('Fiscal queue')");
    const needsReceipt = page.locator("tbody tr").filter({ hasText: "Needs a receipt" }).first();
    await expect(needsReceipt).toBeVisible();

    const invcNo = Number(await needsReceipt.locator("td").nth(4).innerText());
    const issued = await sandboxReceipt(request, invcNo);
    await needsReceipt.getByRole("button", { name: "Attach receipt", exact: true }).click();
    for (const field of ["rcptNo", "totRcptNo", "intrlData", "rcptSign", "sdcId"] as const) {
      await page.getByTestId(`attach-${field}`).fill(String(issued[field]));
    }
    await page.getByTestId("attach-vsdcRcptPbctDate").fill(String(issued.vsdcRcptPbctDate));
    await page.getByTestId("attach-note").fill(`Read off MyRRA, P7 tape ${SUFFIX}`);
    await page.getByTestId("confirm-attach").click();
    await expect(page.getByText("Receipt attached").first()).toBeVisible();

    await releaseTheQueue(page);
    await page.goto(`/ar/documents/${invoice3Id}`);
    await page.waitForSelector("[data-testid='fiscal-receipt-number']");
    // The counters are the **authority's**, keyed by a person off its own ledger.
    await expect(page.getByTestId("fiscal-receipt-number")).toHaveText(
      `${issued.rcptNo}/${issued.totRcptNo} NS`,
    );
    await expect(page.getByTestId("fiscal-receipt-number")).toHaveText("3/4 NS");
  });

  // PATH: /ap/supplier-invoices/new -> the purchase run; /fiscal/purchases -> Accept.
  // CANNOT SEE: what RRA does with a confirmation. The sandbox records it; the feed screen
  // shows the row leaving the undecided list, which is the operator's whole interaction.
  test("rows 7 and 8 — a supplier invoice is registered, and a feed row is confirmed", async ({
    page,
  }) => {
    await signIn(page, OWNER_EMAIL);

    await apiOk(page, "/subledger/ap/documents", {
      method: "POST",
      headers: { "Idempotency-Key": `p7-tape-sin-${SUFFIX}` },
      body: {
        kind: "invoice",
        partner_id: supplierId,
        document_date: today(),
        description: `Tape supplier invoice ${SUFFIX}`,
        reference: "77",
        lines: [{ item_id: itemId, quantity: "2", unit_price: COST }],
      },
    });
    await drain(page);

    // The purchase run is its own (`FIP`), and the row is on the queue beside the sales.
    await page.goto("/fiscal/queue");
    await page.waitForSelector("h1:has-text('Fiscal queue')");
    await expect(page.getByTestId(`pending-${deviceId}`)).toHaveText("0");
    await expect(page.locator("tbody tr").filter({ hasText: "Purchase" }).first()).toBeVisible();

    // --- the feed: the other side of somebody else's sale ---------------------------------
    await page.goto("/fiscal/purchases");
    await page.waitForSelector("h1:has-text('EBM purchases')");
    await page.getByTestId("fetch-feed").click();
    await expect(page.getByText(/\d+ purchases fetched/).first()).toBeVisible();
    await page.reload();
    await page.waitForSelector("h1:has-text('EBM purchases')");

    const row = page.locator("tbody tr").filter({ hasText: FEED_SUPPLIER_TIN }).first();
    await expect(row).toBeVisible();
    await expect(row).toContainText("11,800");
    await row.getByRole("button", { name: "Accept", exact: true }).click();
    await page.getByTestId("confirm-accept").click();
    await expect(page.getByText("Purchase confirmed").first()).toBeVisible();

    await page.goto("/fiscal/purchases");
    await page.waitForSelector("h1:has-text('EBM purchases')");
    // Undecided is the default filter, so the row it just decided is gone from it.
    await expect(page.locator("tbody tr").filter({ hasText: FEED_SUPPLIER_TIN })).toHaveCount(0);
    await drain(page);
  });

  // PATH: /tax/reports/daily-fiscal -> Close day, then /tax/reports/receipts with the Z chosen.
  // CANNOT SEE: the VAT return's own figures — they read the ledger rather than the wire, and
  // that is the next test and the reason the residue has a name.
  test("row 9 — the day closes as Z-000001, and the receipts listing ties to it", async ({
    page,
  }) => {
    await signIn(page, OWNER_EMAIL);
    await page.goto("/tax/reports/daily-fiscal");
    await page.waitForSelector("h1:has-text('Daily fiscal report')");

    // Nothing is in flight, so the day closes over a clear queue and says so.
    await expect(page.getByTestId("close-day-clear")).toBeVisible();
    await page.getByTestId("close-day").click();
    await expect(page.getByText(/Z-\d+ stored/).first()).toBeVisible({ timeout: 30_000 });

    // **The first Z this company has ever taken** — the literal no other P7 spec may assert.
    await expect(page.getByTestId("z-number").first()).toHaveText("Z-000001");
    // What it owns, as counters: everything up to the fourth receipt (0026). The dates are on
    // the paper; this is what decides membership.
    await expect(page.getByTestId("z-window").first()).toHaveText("Covers receipts 1–4");

    await expect(page.getByTestId("day-ns-count")).toHaveText("3");
    await expect(page.getByTestId("day-nr-count")).toHaveText("1");
    await expect(page.getByTestId("day-ns-gross")).toHaveText(DECLARED.salesTotal);
    await expect(page.getByTestId("day-nr-gross")).toHaveText(DECLARED.creditNote);
    await expect(page.getByTestId("day-net")).toHaveText(DECLARED.net);
    // Quantities: six bottles out, one back — rule 13's figure on this screen.
    await expect(page.getByTestId("day-items-ns")).toHaveText("6");
    await expect(page.getByTestId("day-items-nr")).toHaveText("1");
    // **The residue, named.** Declared at the wire's two decimals, posted at the franc, and the
    // ten centimes between them is the one figure an accountant would otherwise spend a morning
    // on. The copy printed in the first test is counted too, and is no part of the takings.
    await expect(page.getByTestId("day-posted")).toHaveText(POSTED.dayNet);
    await expect(page.getByTestId("day-residue")).toHaveText(POSTED.residue);
    await expect(page.getByTestId("day-queued-rows")).toHaveText("0");

    // --- the tie, pointed at this Z --------------------------------------------------------
    //
    // Two independently-derived figures over one set of receipts: the close absorbing them one
    // at a time, and the listing summing what it rendered. Asked for **the Z** rather than for
    // a date range, because a Z owns a run of counters and no date range can express that
    // (0026).
    await page.goto("/tax/reports/receipts");
    await page.waitForSelector("h1:has-text('Fiscal receipts listing')");
    await page.getByRole("combobox", { name: "Fiscal day (Z)", exact: true }).click();
    await page.getByRole("option", { name: "Z-000001", exact: true }).click();
    await expect(page.getByTestId("listing-z-note")).toBeVisible();

    await expect(page.getByTestId("tie-ns-count")).toHaveText("3", { timeout: 30_000 });
    await expect(page.getByTestId("tie-nr-count")).toHaveText("1");
    await expect(page.getByTestId("tie-ns-gross")).toHaveText(DECLARED.salesTotal);
    await expect(page.getByTestId("tie-declared-net")).toHaveText(DECLARED.net);

    // Each receipt's own declaration on its own row, at the wire's two decimals — a screen that
    // rounded these to the franc could not show the residue this report exists for.
    for (const declared of [DECLARED.invoice1, DECLARED.invoice2, DECLARED.invoice3]) {
      await expect(
        page.getByTestId("listing-declared").filter({ hasText: declared }).first(),
      ).toBeVisible();
    }
    await expect(
      page.getByTestId("listing-posted").filter({ hasText: POSTED.invoice1 }).first(),
    ).toBeVisible();
  });

  // PATH: /tax/vat-returns -> the tie, File; then a backdated entry and the next month's return.
  // CANNOT SEE: the settlement's own journal entry — the GL entry screen is P2's.
  test("rows 10 to 12 — the return ties, files as VATR-000001, and a late entry lands next month", async ({
    page,
  }) => {
    await signIn(page, OWNER_EMAIL);
    await page.goto("/tax/vat-returns");
    await page.waitForSelector("h1:has-text('VAT return')");
    await page.waitForSelector("[data-testid='vat-output']");

    // **The whole return as literals**, which only a company of its own allows: three sales and
    // a refund against one supplier invoice, and nothing else has ever posted here.
    await expect(page.getByTestId("vat-output")).toHaveText(VAT.output);
    await expect(page.getByTestId("vat-input")).toHaveText(VAT.input);
    await expect(page.getByTestId("vat-net")).toHaveText(VAT.net);
    // Reconciled, not balanced: every franc of movement on each VAT account is either declared
    // by the return or named by a line under it. With nothing untagged, the lists are empty.
    await expect(page.getByTestId("tie-2200")).toHaveText("Reconciled");
    await expect(page.getByTestId("tie-1400")).toHaveText("Reconciled");

    await page.getByTestId("file-return").click();
    await page.getByTestId("confirm-file").click();
    await expect(page.getByText("VATR-000001 filed").first()).toBeVisible();

    await page.reload();
    await page.waitForSelector("h1:has-text('VAT return')");
    // Frozen, not recomputed: the filed row carries the net the preview showed.
    await expect(page.getByTestId("filed-net-VATR-000001")).toHaveText(VAT.net);

    // --- the late entry --------------------------------------------------------------------
    //
    // Backdated **into the filed month**, after the filing. Decision 12: a filed return never
    // changes, and the entry lands on the next one under *Late entries from filed periods* with
    // its own date beside it.
    const accounts = (await apiOk(page, "/gl/accounts")) as Array<Identified & { code: string }>;
    const byCode = new Map(accounts.map((a) => [a.code, a.id]));
    const outputVat = (
      (await apiOk(page, "/gl/tax-codes")) as Array<Identified & { code: string }>
    ).find((code) => code.code === "VAT-OUT-18")!;
    await apiOk(page, "/gl/journal-entries", {
      method: "POST",
      headers: { "Idempotency-Key": `p7-tape-late-${SUFFIX}` },
      body: {
        entry_date: today(),
        description: `Tape late sale ${SUFFIX}`,
        lines: [
          { gl_account_id: byCode.get("1500"), debit: "1180" },
          {
            gl_account_id: byCode.get("4100"),
            credit: "1000",
            tax_code_id: outputVat.id,
            tax_amount: "-180",
          },
          { gl_account_id: byCode.get("2200"), credit: "180", tax_code_id: outputVat.id },
        ],
      },
    });

    await page.reload();
    await page.waitForSelector("h1:has-text('VAT return')");
    // **The filed return has not moved**, which is the claim worth the round trip.
    await expect(page.getByTestId("filed-net-VATR-000001")).toHaveText(VAT.net);

    // …and next month's return carries it, dated to the month it was keyed into.
    // **Anchored regexes, not bare strings.** `getByLabel("To")` matches the header's "Toggle
    // theme" as a substring and fails strict mode — which is a thing worth knowing about every
    // two-letter label on a screen inside the app shell.
    await pickDate(page, /^From$/, nextMonthStart());
    await pickDate(page, /^To$/, nextMonthEnd());
    // **Over the rows, not over the first one.** `_late` emits one row per journal *line* that
    // carries the code — the base on the revenue line and the tax on the tax-account line — so
    // a three-line correction is two rows, and which of them renders first is the query's
    // ordering rather than a claim this test should make.
    await expect(page.getByTestId("late-base").first()).toBeVisible({ timeout: 30_000 });
    const lateBases = await page.getByTestId("late-base").allInnerTexts();
    expect(lateBases.map((text) => text.trim()), "the backdated sale, on the next return").toContain(
      LATE.base,
    );
  });

  // PATH: /gl/fx-revaluations — preview an open foreign-currency invoice, post, read the mirror
  // on the run, reverse.
  test("row 13 — the revaluation posts as FXR-000001 with its mirror, and reverses", async ({
    page,
  }) => {
    await signIn(page, OWNER_EMAIL);

    // **Its own currency and its own rates.** `exchange_rates` rows are append-only per date and
    // there is no update endpoint, so a spec that borrowed USD could not guarantee what USD is
    // worth on a given day once another spec had seeded its own.
    const currency = (await apiOk(page, "/gl/currencies", {
      method: "POST",
      body: {
        code: FX_CURRENCY,
        name: `Tape revaluation currency ${SUFFIX}`,
        decimal_places: 2,
      },
    })) as Identified;
    for (const [validFrom, rate] of [
      [monthStart(), BOOKING_RATE],
      [monthEnd(), RATE_AT_MONTH_END],
    ] as const) {
      await apiOk(page, "/gl/exchange-rates", {
        method: "POST",
        body: { currency_id: currency.id, valid_from: validFrom, rate: String(rate) },
      });
    }
    await apiOk(page, "/subledger/ar/partners", {
      method: "POST",
      body: { name: `Tape FX customer ${SUFFIX}`, customer_code: FX_CUSTOMER_CODE, tin: CUSTOMER_TIN },
    });

    // **The next period has to be open**, and that is the run's shape rather than a fixture
    // detail: decision 13 posts the entry at the revaluation date *and its mirror the following
    // day*, in one transaction, so a month-end run reaches into the month after it.
    const periods = (await apiOk(page, "/gl/periods")) as Array<
      Identified & { start_date: string; status: string }
    >;
    const next = periods.find((p) => p.start_date > monthEnd() && p.status !== "open");
    if (next) await apiOk(page, `/gl/periods/${next.id}/open`, { method: "POST" });

    await page.goto("/ar/invoices/new");
    await page.waitForSelector("h1:has-text('Invoice')");
    await pickCombobox(page, "Customer", FX_CUSTOMER_CODE);
    await page.getByLabel("Description", { exact: true }).fill(`Tape FX invoice ${SUFFIX}`);
    await page.getByTestId("purchase-code").fill(PURCHASE_CODE);
    await pickDate(page, "Document date", monthStart());
    await pickCombobox(page, "Currency", FX_CURRENCY);
    // Nothing is typed into Exchange rate: the dated lookup is what carries the booking.
    await page.getByRole("button", { name: "Item, row 1", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(ITEM_CODE);
    await page.locator(`[cmdk-item]:has-text("${ITEM_CODE}")`).first().click();
    await page.getByLabel("Quantity, row 1", { exact: true }).fill("10");
    await page.getByLabel("Unit price, row 1", { exact: true }).fill("2");
    await page.getByRole("button", { name: /^Post/ }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 60_000 });
    await drain(page);

    const fxNumber = (
      (await apiOk(page, "/subledger/ar/documents?kind=invoice")) as {
        items: Array<{ number: string; description: string }>;
      }
    ).items.find((row) => row.description === `Tape FX invoice ${SUFFIX}`)!.number;

    await page.goto("/gl/fx-revaluations");
    await page.waitForSelector("h1:has-text('FX revaluation')");
    await pickDate(page, "Revaluation date", monthEnd());
    // The figure, worked by hand: 23.60 gross carries at 31 152 and revalues at 31 860.
    await expect(page.getByTestId(`difference-${fxNumber}`)).toHaveText(FX_DIFFERENCE);

    await page.getByTestId("post-revaluation").click();
    await page.getByTestId("confirm-post").click();
    await expect(page.getByText("FXR-000001 posted").first()).toBeVisible();

    await page.reload();
    await page.waitForSelector("h1:has-text('FX revaluation')");
    const run = page.locator("tbody tr").filter({ hasText: "FXR-000001" }).first();
    await expect(run).toBeVisible();
    // Two entries, one transaction: the run at the date and its mirror the day after — the
    // balance sheet at the date carries the revaluation and the next period does not.
    await expect(run.getByTestId("mirror-FXR-000001")).toBeVisible();

    await run.getByRole("button", { name: "Open", exact: true }).click();
    await page.getByTestId("fx-reverse-reason").fill(`Reversed by the P7 tape ${SUFFIX}`);
    await page.getByTestId("confirm-fx-reverse").click();
    await expect(page.getByText(/FXR-\d+ reversed/).first()).toBeVisible();
  });

  // PATH: the other company entirely — /ar/invoices/new, then the printed sheet.
  // CANNOT SEE: a queue. That is the assertion.
  test("the company with no device declares nothing and prints the P4 layout", async ({
    page,
  }) => {
    await signIn(page, PLAIN_EMAIL);

    const categories = (await apiOk(page, "/inventory/uom-categories")) as Array<
      Identified & { code: string; uoms: Array<Identified & { code: string }> }
    >;
    const count = categories.find((c) => c.code === "COUNT")!;
    const accounts = (await apiOk(page, "/gl/accounts")) as Array<Identified & { code: string }>;
    const byCode = new Map(accounts.map((a) => [a.code, a.id]));
    // **The same tax code as the fiscalized company's item**, so the two invoices are the same
    // sale keyed twice and the only difference between them is the device.
    const outputVat = (
      (await apiOk(page, "/gl/tax-codes")) as Array<Identified & { code: string }>
    ).find((code) => code.code === "VAT-OUT-18")!;
    await apiOk(page, "/inventory/items", {
      method: "POST",
      body: {
        code: `PLAIN${SUFFIX}`,
        name: `Plain wine ${SUFFIX}`,
        uom_category_id: count.id,
        base_uom_id: count.uoms.find((u) => u.code === "EA")!.id,
        // A **service**: no device means no stock policy to satisfy, and nothing here is about
        // inventory.
        item_type: "service",
        selling_price: PRICE,
        sales_account_id: byCode.get("4100"),
        default_sales_tax_code_id: outputVat.id,
      },
    });
    await apiOk(page, "/subledger/ar/partners", {
      method: "POST",
      body: { name: `Plain customer ${SUFFIX}`, customer_code: `PC${SUFFIX}`, tin: CUSTOMER_TIN },
    });

    await page.goto("/ar/invoices/new");
    await page.waitForSelector("h1:has-text('Invoice')");
    await pickCombobox(page, "Customer", `PC${SUFFIX}`);
    await page.getByLabel("Description", { exact: true }).fill(`Plain invoice ${SUFFIX}`);
    // **The block is there and asks for nothing the authority would.** Payment method is a
    // property of every document (decision 7, both roles), so the fiscal block renders on an
    // unfiscalized company too — what is absent is the *requirement*: the customer has a TIN
    // and no purchase code is demanded, because a purchase code is a requirement of
    // fiscalization rather than of having a TIN.
    await expect(page.getByTestId("document-fiscal")).toBeVisible();
    await expect(page.getByLabel("Purchase code (required)")).toHaveCount(0);

    await page.getByRole("button", { name: "Item, row 1", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(`PLAIN${SUFFIX}`);
    await page.locator(`[cmdk-item]:has-text("PLAIN${SUFFIX}")`).first().click();
    await page.getByLabel("Quantity, row 1", { exact: true }).fill("3");
    await page.getByLabel("Unit price, row 1", { exact: true }).fill(PRICE);
    await page.getByRole("button", { name: /^Post/ }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 60_000 });

    const documents = (await apiOk(page, "/subledger/ar/documents?kind=invoice")) as {
      items: Array<Identified & { description: string }>;
    };
    const posted = documents.items.find((row) => row.description === `Plain invoice ${SUFFIX}`)!;
    // Nothing was enqueued, and nothing could have been: there is no device.
    expect((await apiOk(page, "/fiscal/devices")) as unknown[]).toEqual([]);

    await page.goto(`/ar/documents/${posted.id}`);
    await page.waitForSelector("[data-testid='document-total']");
    await expect(page.getByTestId("document-total")).toContainText(POSTED.invoice1);
    // No status chip, no receipt block, and Print is not gated behind a signature that will
    // never come.
    await expect(page.getByTestId("fiscal-status")).toHaveCount(0);
    await expect(page.getByTestId("report-print")).toBeEnabled();

    const paper = await printedText(page, `test-results/p7-tape-plain-${SUFFIX}.pdf`);
    // **The P4 layout, on the paper**: the document's own figures and no SDC block. This is the
    // assertion the whole "non-fiscalized companies keep the P4 layout" clause rests on, and it
    // cannot be made from the DOM — the receipt is `hidden print:block`.
    expect(paper).toContain(POSTED.invoice1);
    expect(paper, "no SDC block on a document nobody declared").not.toContain("SDC INFORMATION");
    expect(paper).not.toContain(SDC_ID);
    expect(paper).not.toContain("Internal Data");
  });
});
