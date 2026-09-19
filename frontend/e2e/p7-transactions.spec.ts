import { expect, test, type Page } from "@playwright/test";
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

/** The one purchase the sandbox's feed fixture holds, and the one import declaration. Fixed
 * values, so the figures below are literals rather than whatever came back. */
const FEED_SUPPLIER_TIN = "100000003";
const FEED_TAXABLE = "11,800";
const IMPORT_QUANTITY = "240";

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

/** Drain the queue now rather than waiting for the worker's fifteen seconds.
 *
 * This is the one endpoint in the phase that is `by design` in the rule-14 register, and this
 * is the second of the two reasons it exists: a deployment with an external scheduler, and a
 * test that would otherwise be a sleep. */
async function drain(page: Page): Promise<void> {
  await apiOk(page, "/fiscal/outbox/drain", { method: "POST" });
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
  await apiOk(page, `/fiscal/devices/${device.id}/initialize`, { method: "POST" });
  await apiOk(page, `/fiscal/devices/${device.id}/sync-codes`, { method: "POST" });
  await apiOk(page, `/fiscal/devices/${device.id}/sync-item-classes`, { method: "POST" });
  return device;
}

test.describe.configure({ mode: "serial", timeout: 300_000 });

test.describe("the fiscalized transaction screens", () => {
  let deviceId = 0;
  let itemId = 0;
  let invoiceDocumentId = 0;

  test("the catalogue and the device are fiscal-ready", async ({ page }) => {
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
    await apiOk(page, "/oe/goods-received", {
      method: "POST",
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
  test("an invoice to a customer with a TIN demands a purchase code, inline", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
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
  }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto(`/ar/documents/${invoiceDocumentId}`);
    await page.waitForSelector("[data-testid='document-total']");

    // The figure, off the page: RWF has no decimals, so 59 000 renders without any.
    await expect(page.getByTestId("document-total")).toContainText("59,000");

    // Before the drain the row is queued and there is nothing to print — CIS §10. The button
    // is disabled and carries the queue row's own status as its reason.
    await expect(page.getByTestId("fiscal-status")).toContainText("Queued");
    await expect(page.getByTestId("report-print")).toBeDisabled();

    await drain(page);
    await page.reload();
    await page.waitForSelector("[data-testid='fiscal-receipt-number']");

    // The authority's counters, read off the page: first sale on a fresh device.
    await expect(page.getByTestId("fiscal-status")).toContainText("Sent");
    await expect(page.getByTestId("fiscal-receipt-number")).toContainText("1/1 NS");
    await expect(page.getByTestId("report-print")).toBeEnabled();
    await expect(page.getByTestId("fiscal-copy-count")).toHaveText("0");

    // No key is on this page, and there is no field on the wire that could put one here.
    const body = await page.locator("body").innerText();
    expect(body).not.toContain("sandbox-cmc-key");
    expect(body).not.toContain("sandbox-sign-key");

    // --- the copy print -------------------------------------------------------------------
    await page.getByTestId("copy-print").click();
    await page.getByTestId("confirm-copy-print").click();
    await page.waitForTimeout(500);
    await page.reload();
    await page.waitForSelector("[data-testid='fiscal-copy-count']");
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
    await page.getByRole("button", { name: "Close" }).click();
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

    // The invoice declared 9 000 of output VAT and the credit note took 360 back
    // (2 × 2 000 = 4 000 net, 18 % = 720 — halved by nothing; the credit is 720). Rather than
    // restate the arithmetic of two documents keyed above, what is asserted is the tie: the
    // account's movement and what the return declares of it agree, which is the one claim this
    // screen exists to make.
    await expect(page.getByTestId("tie-2200")).toHaveText("Reconciled");
    await expect(page.getByTestId("tie-1400")).toHaveText("Reconciled");
    // The figure, off the page: input VAT is the 9 000 on the opening-stock receipt.
    await expect(page.getByTestId("vat-input")).toContainText("9,000");

    await page.getByTestId("file-return").click();
    await page.getByTestId("confirm-file").click();
    await expect(page.getByText(/VATR-\d+ filed/).first()).toBeVisible();

    await page.reload();
    await page.waitForSelector("h1:has-text('VAT return')");
    // The filed return, with the settlement entry behind it.
    const filed = page.locator("tbody tr").filter({ hasText: "VATR-" }).first();
    await expect(filed).toBeVisible();
    await expect(filed).toContainText("Filed");
  });

  // PATH: /gl/fx-revaluation — preview a run over an open foreign-currency invoice, post it,
  // and reverse it.
  test("the revaluation previews an open USD invoice, posts and reverses", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);

    // A customer, a rate and an invoice in a currency that is not the base — the only thing a
    // revaluation has anything to say about.
    const currencies = (await apiOk(page, "/gl/currencies")) as Array<
      Identified & { code: string; is_base: boolean }
    >;
    const foreign = currencies.find((c) => !c.is_base);
    test.skip(foreign === undefined, "the fixture has no foreign currency to revalue");

    await apiOk(page, "/subledger/ar/partners", {
      method: "POST",
      body: {
        name: `Fiscal FX customer ${SUFFIX}`,
        customer_code: FX_CUSTOMER_CODE,
        tin: CUSTOMER_TIN,
      },
    });

    const today = new Date();
    const monthEnd = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() + 1, 0))
      .toISOString()
      .slice(0, 10);

    await page.goto("/ar/invoices/new");
    await page.waitForSelector("h1:has-text('Invoice')");
    await pickCombobox(page, "Customer", FX_CUSTOMER_CODE);
    await page.getByLabel("Description", { exact: true }).fill(`Fiscal FX invoice ${SUFFIX}`);
    await page.getByTestId("purchase-code").fill(PURCHASE_CODE);
    await pickCombobox(page, "Currency", foreign!.code);
    await page.getByRole("button", { name: "Item, row 1", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(ITEM_CODE);
    await page.locator(`[cmdk-item]:has-text("${ITEM_CODE}")`).first().click();
    await page.getByLabel("Quantity, row 1", { exact: true }).fill("10");
    await page.getByLabel("Unit price, row 1", { exact: true }).fill("2");
    await page.getByRole("button", { name: /^Post/ }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 60_000 });
    await drain(page);

    await page.goto("/gl/fx-revaluation");
    await page.waitForSelector("h1:has-text('FX revaluation')");
    await pickDate(page, "Revaluation date", monthEnd);

    // The preview names the document and the difference the entry would post. A line is what
    // makes this screen worth opening — a figure, not a heading.
    const line = page.locator("tbody tr").first();
    await expect(line).toBeVisible();
    await expect(page.getByTestId("revaluation-total")).toBeVisible();

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
