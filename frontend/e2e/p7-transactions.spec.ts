import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { PRIMARY_EMAIL, login, pageFetch, pickCombobox, pickDate } from "./support/fixtures";

/**
 * P7 step 7 — the transaction screens a fiscalized company works through.
 *
 * Nine screens, one serial run: the two AR capture screens with their fiscal fields, the
 * document detail with its receipt and its Copy print, the fiscal queue, EBM purchases, import
 * declarations, the VAT return and the FX revaluation.
 *
 * **It has to be one file and it has to be serial.** Activating a device makes the company
 * *fiscalized*, and from that moment every AR posting goes through the fiscal hook: a sale to a
 * customer with a TIN and no purchase code is refused, an item with no class code is refused, a
 * credit note with no reason is refused. A second spec running concurrently against the same
 * fixture would meet those refusals for reasons that have nothing to do with it. So this file
 * turns the device on at the start, does its work, and — like `p7-maintenance.spec.ts` — turns
 * it off again at the end, leaving `is_fiscalized` false for whatever shard runs next.
 *
 * **The sandbox is reached over HTTP**, from the browser through the app through the backend to
 * the `ebm-sandbox` container. Every backend test mounts it in process; here it is a URL, a
 * network and a JSON envelope, which is the only place a route table that worked in-process and
 * nowhere else would fail.
 *
 * Rule 13: every screen is opened with data in it and a **figure** is read off the page —
 * `59,000` on the document, `1/1 NS` on the receipt, `11,800` on the feed, `240` on the import
 * declaration, `9,000` on the VAT return, and the revaluation's own difference. A heading and
 * an empty state would prove the route compiles and nothing else.
 *
 * **What this file cannot see.** The X and Z reports (step 8's screen), the two Tax enquiries
 * and the five Tax/GL reports (step 8), and the full tape with a device going down and coming
 * back (step 9). The backend acceptance tape covers the ledger side of all of it.
 */

const SUFFIX = String(Date.now()).slice(-6);
const ITEM_CODE = `P7T${SUFFIX}`;
const CUSTOMER_CODE = `P7C${SUFFIX}`;
const SUPPLIER_CODE = `P7S${SUFFIX}`;
const FX_CUSTOMER_CODE = `P7X${SUFFIX}`;

const COMPANY_TIN = "999000099";
/** A taxpayer the sandbox knows. A sale to a customer **with a TIN** is what makes a purchase
 * code mandatory (v1.0.5), which is the refusal this file has to be able to produce. */
const CUSTOMER_TIN = "100000001";
const SUPPLIER_TIN = "100000002";
/** Six characters, the customer's own EBM purchase code. */
const PURCHASE_CODE = "AB12CD";
/** §4.16 code 06, "Refund" — RRA's own name for the ordinary case. */
const REFUND_REASON = "06";

const EBM_URL = process.env.PLAYWRIGHT_EBM_URL ?? "http://ebm-sandbox:8100";
/** The same sandbox, reached from **this** process rather than from the backend container — the
 * mode switch is driven by the test, not by the app. */
const SANDBOX_ADMIN = process.env.PLAYWRIGHT_EBM_ADMIN_URL ?? "http://localhost:8100";

/** The one purchase the sandbox's feed fixture holds, and the one import declaration. Fixed
 * values, so the figures below are literals rather than whatever came back. */
const FEED_SUPPLIER_TIN = "100000003";
const FEED_TAXABLE = "11,800";
const IMPORT_QUANTITY = "240";
/** What the sandbox hands back on initialization — a fixed value, so a test can tell "the
 * authority answered" from "the screen rendered the form it was given". */
const SDC_ID = "SDC010000005";

/** The two USD rates the revaluation test posts, and the difference they produce on its one
 * invoice. Worked by hand in the test's own comment; nothing here is read back from the
 * service that computes it. */
const BOOKING_RATE = 1320;
const RATE_AT_MONTH_END = 1350;
const EXPECTED_DIFFERENCE = "708";

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

/** Drain the queue now rather than waiting for the worker's fifteen seconds.
 *
 * This is the one endpoint in the phase that is `by design` in the rule-14 register, and this
 * is the second of the two reasons it exists: a deployment with an external scheduler, and a
 * test that would otherwise be a sleep. */
async function drain(page: Page): Promise<void> {
  await apiOk(page, "/fiscal/outbox/drain", { method: "POST" });
}

/**
 * Switch what the authority does next.
 *
 * `down` is how the print gate is made **deterministic**: the worker drains every fifteen
 * seconds, so "the row is still queued" cannot be asserted by being quick. With the authority
 * unreachable the row stays queued however many times the worker tries it, which is also
 * exactly the situation CIS §10 is about — the receipt number does not exist yet, so there is
 * nothing to print.
 */
/**
 * Forget everything the authority is holding.
 *
 * The sandbox keeps its ledger — invoice numbers, receipt counters, the items it has been told
 * about — in the **container's memory**, and `make db-reset` does not touch it. So a second run
 * against a reset database starts Vinea's `FIS` sequence at 1 again while the authority still
 * remembers invoice 1, and the sale comes back `994: the invoice number is already registered`.
 * That is the sandbox behaving correctly and the *fixture* being stale, which is a distinction
 * worth not having to make at two in the morning.
 *
 * Called once, first, so the run starts against an authority that has never heard of this
 * taxpayer — which is also the state a fresh CI stack is in.
 */
async function sandboxReset(request: APIRequestContext): Promise<void> {
  const res = await request.post(`${SANDBOX_ADMIN}/_sandbox/reset`);
  expect(res.ok(), `sandbox reset -> ${res.status()}`).toBe(true);
}

async function sandboxMode(request: APIRequestContext, mode: string): Promise<void> {
  const res = await request.post(`${SANDBOX_ADMIN}/_sandbox/mode`, { data: { mode } });
  expect(res.ok(), `sandbox mode ${mode} -> ${res.status()}`).toBe(true);
}

/** How many rows this company's devices are still holding. */
async function pendingRows(page: Page): Promise<number> {
  const devices = (await apiOk(page, "/fiscal/queue")) as Array<{ pending_rows: number }>;
  return devices.reduce((total, device) => total + device.pending_rows, 0);
}

/**
 * Press **Retry now** on every waiting row until the device's queue is empty.
 *
 * Through the screen, not through the API: this is the button that deletes one of the
 * fourteen `GAP (P7, step 7)` lines, and a test that released the queue with a `fetch` would
 * leave it a button nobody has ever pressed — which is the defect rule 14 exists to catch,
 * one level up.
 *
 * Bounded rather than `while (pending)`: a queue that never clears is a failure to report, not
 * a loop to spin in.
 */
async function releaseTheQueue(page: Page): Promise<void> {
  for (let pass = 0; pass < 8; pass += 1) {
    if ((await pendingRows(page)) === 0) return;
    await page.goto("/fiscal/queue");
    await page.waitForSelector("h1:has-text('Fiscal queue')");
    const buttons = page.getByRole("button", { name: "Retry now" });
    const count = await buttons.count();
    for (let index = 0; index < count; index += 1) {
      const button = buttons.nth(index);
      if (await button.isEnabled()) await button.click();
    }
    await drain(page);
  }
  expect(await pendingRows(page), "the queue never cleared").toBe(0);
}

/** The device this company holds, registered and initialized against the sandbox.
 *
 * One device per branch, so a second local run re-uses the row — which is what an operator does
 * too: a device is registered once and re-initialized whenever it needs new keys.
 */
async function activeDevice(page: Page): Promise<Identified> {
  await apiOk(page, "/company", { method: "PATCH", body: { tin: COMPANY_TIN } });
  const branches = (await apiOk(page, "/gl/branches")) as Array<Identified & { code: string }>;
  const main = branches.find((branch) => branch.code === "MAIN")!;
  const existing = (await apiOk(page, "/fiscal/devices")) as Array<Identified>;
  const device =
    existing[0] ??
    ((await apiOk(page, "/fiscal/devices", {
      method: "POST",
      body: {
        branch_id: main.id,
        profile: "vsdc",
        environment: "test",
        base_url: EBM_URL,
        dvc_srl_no: "VINEA-E2E-0001",
        bhf_id: "00",
      },
    })) as Identified);
  // The endpoint **requires** an Idempotency-Key: initialization is not naturally
  // idempotent from the caller's side, and re-initializing a live device is how its keys get
  // reissued. A fresh key per call, because two deliberate presses are two initializations.
  await apiOk(page, `/fiscal/devices/${device.id}/initialize`, {
    method: "POST",
    headers: { "Idempotency-Key": `p7-step7-init-${Date.now()}` },
  });
  await apiOk(page, `/fiscal/devices/${device.id}/sync-codes`, { method: "POST" });
  await apiOk(page, `/fiscal/devices/${device.id}/sync-item-classes`, { method: "POST" });
  return device;
}

test.describe.configure({ mode: "serial", timeout: 300_000 });

test.describe("the fiscalized transaction screens", () => {
  let deviceId = 0;
  let itemId = 0;
  let invoiceDocumentId = 0;

  test("the catalogue and the device are fiscal-ready", async ({ page, request }) => {
    await sandboxReset(request);
    await login(page, PRIMARY_EMAIL);
    deviceId = (await activeDevice(page)).id;

    // The four things a line needs before it can be put on a receipt (decision 8): an item
    // with a class, a unit with an RRA quantity unit, a tax code with an EBM class, and a
    // customer whose TIN decides whether a purchase code is required.
    const categories = (await apiOk(page, "/inventory/uom-categories")) as Array<
      Identified & { code: string; uoms: Array<Identified & { code: string }> }
    >;
    const count = categories.find((c) => c.code === "COUNT")!;
    const each = count.uoms.find((u) => u.code === "EA")!;
    await apiOk(page, `/inventory/uoms/${each.id}`, {
      method: "PATCH",
      body: { fiscal_quantity_unit: "U" },
    });

    const accounts = (await apiOk(page, "/gl/accounts")) as Array<Identified & { code: string }>;
    const byCode = new Map(accounts.map((a) => [a.code, a.id]));
    // The item's own tax code, and the EBM class on it. Decision 8: a sale line whose code has
    // no class is refused `tax_class_unmapped`, because RRA reports every line under one of
    // A-D and there is no fifth answer.
    const taxCodes = (await apiOk(page, "/gl/tax-codes")) as Array<
      Identified & { code: string; fiscal_tax_type: string | null }
    >;
    const outputVat = taxCodes.find((code) => code.code === "VAT-OUT-18")!;
    expect(outputVat.fiscal_tax_type, "the P7 migration seeds the four codes' EBM class").toBe(
      "B",
    );
    const item = (await apiOk(page, "/inventory/items", {
      method: "POST",
      body: {
        code: ITEM_CODE,
        name: `Fiscal tape wine ${SUFFIX}`,
        uom_category_id: count.id,
        base_uom_id: each.id,
        item_type: "stock",
        selling_price: "2000",
        sales_account_id: byCode.get("4100"),
        cogs_account_id: byCode.get("5100"),
        default_sales_tax_code_id: outputVat.id,
        default_purchase_tax_code_id: taxCodes.find((code) => code.code === "VAT-IN-18")?.id,
        fiscal_class_code: "5059020800",
      },
    })) as Identified;
    itemId = item.id;

    for (const [role, code, tin] of [
      ["ar", CUSTOMER_CODE, CUSTOMER_TIN],
      ["ap", SUPPLIER_CODE, SUPPLIER_TIN],
    ] as const) {
      await apiOk(page, `/subledger/${role}/partners`, {
        method: "POST",
        body: {
          name: `${role === "ar" ? "Fiscal customer" : "Fiscal supplier"} ${SUFFIX}`,
          [role === "ar" ? "customer_code" : "supplier_code"]: code,
          tin,
          phone: "+250788000001",
        },
      });
    }

    // Opening stock: a fiscalized company's negative-stock policy is locked at `block`
    // (CIS §7.30 — no receipt for goods the stock does not hold), so a sale with nothing
    // behind it would be refused before it ever reached the queue.
    const warehouses = (await apiOk(page, "/inventory/warehouses")) as Array<
      Identified & { code: string }
    >;
    const suppliers = (await apiOk(page, "/subledger/ap/partners")) as Array<
      Identified & { supplier_code: string | null }
    >;
    await apiOk(page, "/oe/goods-received-notes", {
      method: "POST",
      headers: { "Idempotency-Key": `p7-step7-grn-${SUFFIX}` },
      body: {
        partner_id: suppliers.find((s) => s.supplier_code === SUPPLIER_CODE)!.id,
        grn_date: new Date().toISOString().slice(0, 10),
        description: `Fiscal opening stock ${SUFFIX}`,
        warehouse_id: warehouses[0].id,
        lines: [{ item_id: itemId, quantity: "100", unit_cost: "1000" }],
      },
    });
    await drain(page);
  });

  // PATH: /ar/invoices/new — the purchase code is demanded, refused inline when missing, and
  // accepted; the sale queues in the posting's own transaction.
  // CANNOT SEE: the payload that went to RRA. The queue screen's row detail shows it.
  test("an invoice to a customer with a TIN demands a purchase code, inline", async ({
    page,
    request,
  }) => {
    await login(page, PRIMARY_EMAIL);

    // **The authority goes down before this invoice is posted.** The worker drains every
    // fifteen seconds, so a row that RRA can answer is signed before any test could look at it
    // — and the next test's whole subject is a document whose receipt does not exist yet. An
    // unreachable authority keeps the row queued however many times the worker tries it, which
    // is the real case CIS §10 is about rather than a contrivance.
    await sandboxMode(request, "down");

    await page.goto("/ar/invoices/new");
    await page.waitForSelector("h1:has-text('Invoice')");

    await pickCombobox(page, "Customer", CUSTOMER_CODE);
    await page.getByLabel("Description", { exact: true }).fill(`Fiscal invoice ${SUFFIX}`);

    // The field appears because the customer has a TIN, and it says "required" because this
    // company fiscalizes. Neither is a guess: `/fiscal/document-context` answers both, and it
    // takes no fiscal permission to ask.
    const fiscal = page.getByTestId("document-fiscal");
    await expect(fiscal).toBeVisible();
    await expect(fiscal.getByLabel("Purchase code (required)")).toBeVisible();

    await page.getByRole("button", { name: "Item, row 1", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(ITEM_CODE);
    await page.locator(`[cmdk-item]:has-text("${ITEM_CODE}")`).first().click();
    await page.getByLabel("Quantity, row 1", { exact: true }).fill("25");
    await page.getByLabel("Unit price, row 1", { exact: true }).fill("2000");

    // --- posted without a code, and refused on the field that names it --------------------
    await page.getByRole("button", { name: /^Post/ }).click();
    await expect(page.getByTestId("purchase-code")).toBeVisible();
    await expect(fiscal.getByText(/required/i).first()).toBeVisible();
    // Still on the capture screen: a refusal is not a navigation.
    await expect(page).toHaveURL(/\/ar\/invoices\/new/);

    await page.getByTestId("purchase-code").fill(PURCHASE_CODE);
    await page.getByRole("button", { name: /^Post/ }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 60_000 });

    const documents = (await apiOk(page, "/subledger/ar/documents?kind=invoice")) as {
      items: Array<Identified & { description: string; total_amount: string }>;
    };
    const posted = documents.items.find((row) => row.description.includes(SUFFIX))!;
    invoiceDocumentId = posted.id;
    // 25 × 2 000 = 50 000 net, 18 % = 9 000 tax, 59 000 gross. Worked by hand, not read back.
    expect(Number(posted.total_amount)).toBe(59000);
  });

  // PATH: /ar/documents/{id} — Print refused before the authority signs, allowed after, and a
  // second print is a COPY.
  // CANNOT SEE: the printed sheet itself. `pdftotext` on the print is step 9's.
  test("the document refuses to print until RRA signs it, then prints and copies", async ({
    page,
    request,
  }) => {
    await login(page, PRIMARY_EMAIL);

    await page.goto(`/ar/documents/${invoiceDocumentId}`);
    await page.waitForSelector("[data-testid='document-total']");

    // The figure, off the page: RWF has no decimals, so 59 000 renders without any.
    await expect(page.getByTestId("document-total")).toContainText("59,000");

    await expect(page.getByTestId("fiscal-status")).toContainText("Queued");
    await expect(page.getByTestId("report-print")).toBeDisabled();
    // The reason is the row's own status, not a generic "cannot print".
    await expect(page.getByTestId("report-print")).toHaveAttribute(
      "title",
      /queued/i,
    );

    // --- the authority comes back --------------------------------------------------------
    //
    // Bringing it back is not enough, and that is decision 4 working rather than an
    // inconvenience: every failed attempt pushed the row's `next_attempt_at` out along the
    // 1 → 5 → 15 minute backoff, so the drainer will not look at it again for minutes. **Retry
    // now** is the button that exists for exactly this moment, and it is pressed here on the
    // queue screen, once per waiting row — because the queue is per-device FIFO and releasing
    // the sale while the item registration ahead of it is still backing off releases nothing.
    await sandboxMode(request, "up");
    await releaseTheQueue(page);

    await page.goto(`/ar/documents/${invoiceDocumentId}`);
    await page.waitForSelector("[data-testid='fiscal-receipt-number']");

    await expect(page.getByTestId("fiscal-status")).toContainText("Sent");
    // The counters and the identity are the **authority's**, read off the page. The counter is
    // asserted by its CIS §7.25 shape rather than as `1/1`: the sandbox keeps its counters in
    // the container's memory, so a fresh stack starts at one and a second local run against the
    // same container does not, and a literal here would be a test that passes in CI and fails
    // on the machine it was written on. The SDC id is a fixture constant, so it is a literal —
    // and it is the thing that proves these figures came back from a server rather than from
    // the form that was submitted.
    await expect(page.getByTestId("fiscal-receipt-number")).toHaveText(/^\d+\/\d+ NS$/);
    await expect(page.getByTestId("fiscal-sdc-id")).toHaveText(SDC_ID);
    await expect(page.getByTestId("report-print")).toBeEnabled();
    await expect(page.getByTestId("fiscal-copy-count")).toHaveText("0");

    // No key is on this page, and there is no field on the wire that could put one here.
    const body = await page.locator("body").innerText();
    expect(body).not.toContain("sandbox-cmc-key");
    expect(body).not.toContain("sandbox-sign-key");

    // --- the copy print -------------------------------------------------------------------
    await page.getByTestId("copy-print").click();
    await page.getByTestId("confirm-copy-print").click();
    await expect(page.getByTestId("fiscal-copy-count")).toHaveText("1");
  });

  // PATH: /ar/credit-notes/new — the Refund of picker offers the partner's fiscalized
  // invoices, and the reason code comes from the synced §4.16 table.
  test("a credit note names the invoice it refunds and why", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/ar/credit-notes/new");
    await page.waitForSelector("h1:has-text('Credit note')");

    await pickCombobox(page, "Customer", CUSTOMER_CODE);
    await page.getByLabel("Description", { exact: true }).fill(`Fiscal credit ${SUFFIX}`);
    await page.getByTestId("purchase-code").fill(PURCHASE_CODE);

    // The picker is fed by the document listing filtered to rows that hold a receipt — which
    // is why an AR clerk can use it without `fiscal:reports_view`.
    const fiscal = page.getByTestId("document-fiscal");
    await fiscal.getByRole("button", { name: "Refund of", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await expect(page.locator("[cmdk-item]").filter({ hasText: "INV-" }).first()).toBeVisible();
    await page.locator("[cmdk-item]").filter({ hasText: "INV-" }).first().click();

    // Thirteen reasons, RRA's own names, synced rather than typed.
    await pickCombobox(page, "Refund reason", REFUND_REASON, { within: fiscal });

    await page.getByRole("button", { name: "Item, row 1", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(ITEM_CODE);
    await page.locator(`[cmdk-item]:has-text("${ITEM_CODE}")`).first().click();
    await page.getByLabel("Quantity, row 1", { exact: true }).fill("2");
    await page.getByLabel("Unit price, row 1", { exact: true }).fill("2000");

    await page.getByRole("button", { name: /^Post/ }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 60_000 });
    await drain(page);
  });

  // PATH: /fiscal/queue — the device card, the rows in send order, and a row's payload.
  test("the queue shows the device, its rows and what was sent", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/fiscal/queue");
    await page.waitForSelector("h1:has-text('Fiscal queue')");

    // Everything has been signed, so the device is flowing and nothing is waiting — which is
    // the figure this card has.
    await expect(page.getByTestId(`pending-${deviceId}`)).toHaveText("0");
    await expect(page.getByText("Flowing").first()).toBeVisible();

    const rows = page.locator("tbody tr");
    await expect(rows.first()).toBeVisible();
    // Sale, refund, item registration and the stock reports behind them: more than one row,
    // in send order, and the sale among them is sent.
    await expect(page.getByText("Sent").first()).toBeVisible();

    // The payload, redacted twice — at enqueue and again on the way out.
    await rows.first().getByRole("button", { name: "Inspect" }).click();
    const request = page.getByTestId("row-request");
    await expect(request).toBeVisible();
    const payload = await request.innerText();
    expect(payload).not.toContain("sandbox-cmc-key");
    expect(payload).not.toContain("sandbox-sign-key");
    // By test id: the dialog's own dismiss control is also named "Close".
    await page.getByTestId("close-row").click();
  });

  // PATH: /fiscal/purchases — fetch what RRA is holding, and confirm one.
  test("the purchase feed is fetched and a purchase is confirmed", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/fiscal/purchases");
    await page.waitForSelector("h1:has-text('EBM purchases')");

    await page.getByTestId("fetch-feed").click();
    await expect(page.getByText(/\d+ purchases fetched/).first()).toBeVisible();
    await page.reload();
    await page.waitForSelector("h1:has-text('EBM purchases')");

    const row = page.locator("tbody tr").filter({ hasText: FEED_SUPPLIER_TIN }).first();
    await expect(row).toBeVisible();
    // The figure: the fixture's one purchase, 11 800 taxable.
    await expect(row).toContainText(FEED_TAXABLE);

    await row.getByRole("button", { name: "Accept" }).click();
    await page.getByTestId("confirm-accept").click();
    await expect(page.getByText("Purchase confirmed").first()).toBeVisible();

    await page.goto("/fiscal/purchases");
    await page.waitForSelector("h1:has-text('EBM purchases')");
    // Undecided is the default filter, so the row it just decided is gone from it.
    await expect(page.locator("tbody tr").filter({ hasText: FEED_SUPPLIER_TIN })).toHaveCount(0);
  });

  // PATH: /fiscal/imports — fetch the declarations and acknowledge one against a Vinea item.
  test("an import declaration is acknowledged against the item it became", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/fiscal/imports");
    await page.waitForSelector("h1:has-text('Import declarations')");

    await page.getByTestId("fetch-imports").click();
    await expect(page.getByText(/\d+ declarations fetched/).first()).toBeVisible();
    await page.reload();
    await page.waitForSelector("h1:has-text('Import declarations')");

    const row = page.locator("tbody tr").first();
    await expect(row).toBeVisible();
    // The figure: the fixture's declared quantity.
    await expect(row).toContainText(IMPORT_QUANTITY);

    await row.getByRole("button", { name: "Approve" }).click();
    const dialog = page.getByRole("dialog");
    await pickCombobox(page, "Vinea item", ITEM_CODE, { within: dialog });
    await page.getByTestId("confirm-approve").click();
    await expect(page.getByText("Declaration approved").first()).toBeVisible();
    await drain(page);
  });

  // PATH: /tax/vat-return — the figures, the tie, and filing.
  // CANNOT SEE: a late entry landing on the next return. The backend tape's row 12.
  test("the VAT return ties to the VAT accounts and is filed", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/tax/vat-return");
    await page.waitForSelector("h1:has-text('VAT return')");
    await page.waitForSelector("[data-testid='vat-output']");

    // **The figures asserted here are the tie itself**, not a total.
    //
    // A return's sections are an aggregate over everything posted in the range, and this
    // fixture is shared: another spec in the same shard posting a taxed AR document this month
    // would move them. What is *not* movable is the claim this screen exists to make — that
    // every franc of movement on a VAT account is accounted for by the return. So the two
    // figures read off the page are the account's own movement and what the return declares of
    // it, and they are asserted to be the same number.
    //
    // 2200 is VAT output and 1400 VAT input; `difference` is what is left over, which is where
    // an untagged movement — a VAT payment to the authority, a journal keyed without a code —
    // would show up rather than being swallowed.
    await expect(page.getByTestId("tie-2200")).toHaveText("Reconciled");
    await expect(page.getByTestId("tie-1400")).toHaveText("Reconciled");
    await expect(page.getByTestId("difference-2200")).toHaveText("0");
    const movement = await page.getByTestId("movement-2200").innerText();
    const declared = await page.getByTestId("declared-2200").innerText();
    expect(declared).toBe(movement);
    // …and it is a real, formatted money figure rather than a zero that ties trivially: the
    // invoice above put 9 000 of output VAT on this account and the credit note took 720 back,
    // so it reads in thousands with a group separator.
    expect(movement).toMatch(/\d{1,3},\d{3}/);

    const netBeforeFiling = await page.getByTestId("vat-net").innerText();

    await page.getByTestId("file-return").click();
    await page.getByTestId("confirm-file").click();
    await expect(page.getByText(/VATR-\d+ filed/).first()).toBeVisible();

    await page.reload();
    await page.waitForSelector("h1:has-text('VAT return')");

    // The filed return carries the net the preview showed — frozen, not recomputed.
    const filedNet = page.locator('[data-testid^="filed-net-"]').first();
    await expect(filedNet).toBeVisible();
    await expect(filedNet).toHaveText(netBeforeFiling);
    await expect(page.locator("tr").filter({ has: filedNet })).toContainText("Filed");

    // …and the settlement entry it posted is now an **untagged movement** on this range's own
    // tie, named and listed rather than absorbed. That is decision 12 working: the settlement's
    // lines carry the tax codes with `tax_amount 0`, so they move the VAT accounts and declare
    // nothing, and the account no longer moves by the amount the return declares.
    //
    // The chip still reads "Reconciled", and that is the word doing its work. A tie is not
    // *balanced* — a VAT payment to the authority and a journal keyed without a code are real
    // movements that no tax line explains — it is reconciled when every franc of the difference
    // is accounted for by a line the report can name. Here the difference is 8 280 and the one
    // line naming it is `VATR-000001`, for exactly 8 280.
    await expect(page.getByTestId("tie-2200")).toHaveText("Reconciled");
    await expect(page.getByTestId("difference-2200")).toHaveText(netBeforeFiling);
    await expect(
      page.locator("tbody tr").filter({ hasText: "settled" }).first(),
    ).toContainText(netBeforeFiling);
  });

  // PATH: /gl/fx-revaluation — preview a run over an open foreign-currency invoice, post it,
  // and reverse it.
  test("the revaluation previews an open USD invoice, posts and reverses", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);

    // A customer, two rates and an invoice in a currency that is not the base — the only thing a
    // revaluation has anything to say about.
    const currencies = (await apiOk(page, "/gl/currencies")) as Array<
      Identified & { code: string; is_base: boolean }
    >;
    const foreign = currencies.find((c) => !c.is_base)!;

    await apiOk(page, "/subledger/ar/partners", {
      method: "POST",
      body: {
        name: `Fiscal FX customer ${SUFFIX}`,
        customer_code: FX_CUSTOMER_CODE,
        tin: CUSTOMER_TIN,
      },
    });

    const today = new Date();
    const monthStart = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 1))
      .toISOString()
      .slice(0, 10);
    const monthEnd = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() + 1, 0))
      .toISOString()
      .slice(0, 10);

    // **Two rates, and the gap between them is the whole subject.** The invoice books at
    // BOOKING_RATE and the month-end revaluation reads RATE_AT_MONTH_END, so the difference the
    // preview shows is a figure that can be worked out by hand rather than whatever the fixture
    // happened to hold.
    for (const [validFrom, rate] of [
      [monthStart, BOOKING_RATE],
      [monthEnd, RATE_AT_MONTH_END],
    ] as const) {
      await apiOk(page, "/gl/exchange-rates", {
        method: "POST",
        body: { currency_id: foreign.id, valid_from: validFrom, rate: String(rate) },
      });
    }

    await page.goto("/ar/invoices/new");
    await page.waitForSelector("h1:has-text('Invoice')");
    await pickCombobox(page, "Customer", FX_CUSTOMER_CODE);
    await page.getByLabel("Description", { exact: true }).fill(`Fiscal FX invoice ${SUFFIX}`);
    await page.getByTestId("purchase-code").fill(PURCHASE_CODE);
    await pickCombobox(page, "Currency", foreign.code);
    await page.getByRole("button", { name: "Item, row 1", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(ITEM_CODE);
    await page.locator(`[cmdk-item]:has-text("${ITEM_CODE}")`).first().click();
    await page.getByLabel("Quantity, row 1", { exact: true }).fill("10");
    await page.getByLabel("Unit price, row 1", { exact: true }).fill("2");
    await page.getByRole("button", { name: /^Post/ }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 60_000 });
    await drain(page);

    const fxDocuments = (await apiOk(page, "/subledger/ar/documents?kind=invoice")) as {
      items: Array<{ number: string; description: string }>;
    };
    const fxInvoiceNumber = fxDocuments.items.find((row) =>
      row.description.startsWith("Fiscal FX invoice"),
    )!.number;

    // **The next period has to be open**, and that is the run's shape rather than a fixture
    // detail: decision 13 posts the entry at the revaluation date *and its mirror the following
    // day*, in one transaction, so a month-end run reaches into the month after it. The seeded
    // tenant marks periods after today `future`, and the kernel refuses a posting into one —
    // which is the same thing an accountant does by hand before closing a month.
    const periods = (await apiOk(page, "/gl/periods")) as Array<
      Identified & { start_date: string; status: string }
    >;
    const nextPeriod = periods.find(
      (period) => period.start_date > monthEnd && period.status !== "open",
    );
    if (nextPeriod) {
      await apiOk(page, `/gl/periods/${nextPeriod.id}/open`, { method: "POST" });
    }

    await page.goto("/gl/fx-revaluation");
    await page.waitForSelector("h1:has-text('FX revaluation')");
    await pickDate(page, "Revaluation date", monthEnd);

    // **The figure, worked by hand.** 10 x USD 2.00 = 20.00 net, 18 % = 3.60, so the invoice is
    // USD 23.60 gross. RWF has no decimals, so it carries at round(23.60 x 1 320) = 31 152 and
    // revalues at round(23.60 x 1 350) = 31 860 — a difference of 708, which is what this
    // document's line has to show.
    const line = page.getByTestId(`difference-${fxInvoiceNumber}`);
    await expect(line).toHaveText(EXPECTED_DIFFERENCE);

    await page.getByTestId("post-revaluation").click();
    await page.getByTestId("confirm-post").click();
    await expect(page.getByText(/FXR-\d+ posted/).first()).toBeVisible();

    await page.reload();
    await page.waitForSelector("h1:has-text('FX revaluation')");
    const run = page.locator("tbody tr").filter({ hasText: "FXR-" }).first();
    await expect(run).toBeVisible();
    // Two entries, one transaction: the run at the date and its mirror the day after.
    await expect(run.getByTestId(/^mirror-FXR-/)).toBeVisible();

    await run.getByRole("button", { name: "Open" }).click();
    await page.getByTestId("fx-reverse-reason").fill("Reversed by the P7 step 7 e2e run");
    await page.getByTestId("confirm-fx-reverse").click();
    await expect(page.getByText(/FXR-\d+ reversed/).first()).toBeVisible();
  });

  // Last in a `serial` describe deliberately: an active device changes what every other spec's
  // postings are allowed to do, and a shard that ran this file and then the AR/AP tape would
  // find the tape refused for reasons that have nothing to do with it.
  test("the device is suspended, and the company is unfiscalized again", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await apiOk(page, `/fiscal/devices/${deviceId}/suspend`, {
      method: "POST",
      body: { reason: "End of the P7 step 7 e2e run" },
    });
    const context = (await apiOk(page, "/fiscal/document-context")) as { fiscalized: boolean };
    expect(context.fiscalized).toBe(false);
  });
});
