import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { PRIMARY_EMAIL, login, pageFetch, pickCombobox } from "./support/fixtures";

/**
 * P7 step 8 — the Tax enquiries, the Tax reports, and the FX report.
 *
 * Six screens in one serial run, for the same reason step 7's nine are one file: activating a
 * device makes the company **fiscalized**, and from that moment every AR posting goes through
 * the fiscal hook. The device is turned on at the start and suspended at the end, leaving
 * `is_fiscalized` false for whatever runs next.
 *
 * **The prices are awkward, and that is the whole design.** Everything here is keyed at
 * **1 499 excl. at 18 %**, which the two systems round under different rules:
 *
 * * the **ledger** rounds per line to the currency's places, and RWF has none (rule 6) —
 *   4 497 net, 809.46 → **809** VAT, **5 306** gross for three of them;
 * * the **wire** carries a VAT-inclusive unit price at two decimals (decision 6) — 1 768.82,
 *   extended 1 768.82 × 3 = **5 306.46**.
 *
 * Forty-six centimes apart on one invoice, and neither figure is wrong. On 2 000 × 10 they
 * agree exactly and both fiscal reports have nothing to show — which is P7 step 2's census bias
 * in one line: round prices are precisely the ones that hide the residue. Step 7's spec used
 * 2 000 and could not have caught a report that printed the ledger's figure where the wire's
 * belongs. This one can.
 *
 * **The tie is asserted across two screens.** A Z's sales total is computed by the server by
 * absorbing every receipt in its range; the Fiscal receipts listing renders each receipt's own
 * declaration on its own row. The test sums the rows itself and asserts the total the Z shows
 * — two screens, one number, and the receipts side is the independent one. Each of the three
 * figures is also a hand-worked literal, so a bug that moved both sides together still fails.
 *
 * Rule 13: every screen is opened with data in it and a **figure** is read off the page, plus a
 * quantity where the screen has one — the receipts enquiry parses a counter in `n/m NS` form,
 * the daily report reads items sold, the FX report reads the count of documents revalued.
 *
 * **What this file cannot see.** The full tape end to end with a device going down and coming
 * back, the sensitivity pass, and the certification record: all step 9's.
 */

const SUFFIX = String(Date.now()).slice(-6);
const ITEM_CODE = `P8I${SUFFIX}`;
const CUSTOMER_CODE = `P8C${SUFFIX}`;
const SUPPLIER_CODE = `P8S${SUFFIX}`;

const COMPANY_TIN = "999000099";
const CUSTOMER_TIN = "100000001";
const SUPPLIER_TIN = "100000002";
const PURCHASE_CODE = "AB12CD";
/** §4.16 code 06, "Refund". */
const REFUND_REASON = "06";

const EBM_URL = process.env.PLAYWRIGHT_EBM_URL ?? "http://ebm-sandbox:8100";
const SANDBOX_ADMIN = process.env.PLAYWRIGHT_EBM_ADMIN_URL ?? "http://localhost:8100";
const SDC_ID = "SDC010000005";
const MRC_NO = "WIS01006230";

/** The price everything is keyed at — see the file docstring for why it is not 2 000. */
const PRICE = "1499";

/**
 * The declared figures, worked by hand from 1 768.82 inclusive and asserted as literals.
 *
 * Nothing here is read back from the service that computes it: these are the numbers that go on
 * the paper, and a test that took them from the code under test would pass over any arithmetic
 * that was wrong in the same way twice.
 */
const DECLARED = {
  /** 1 768.82 × 3 */
  invoiceA: "5,306.46",
  /** 1 768.82 × 7 */
  invoiceB: "12,381.74",
  /** 1 768.82 × 1 */
  creditNote: "1,768.82",
  /** 5 306.46 + 12 381.74 */
  salesTotal: "17,688.20",
  /** 17 688.20 − 1 768.82 */
  net: "15,919.38",
};

/** And the ledger's own, in whole francs — the other half of every residue on these screens. */
const POSTED = { invoiceA: "5,306", invoiceB: "12,382", creditNote: "1,769" };

/**
 * `yyyy-mm-dd` in **local** time, the way `lib/format`'s `todayIso()` does it. Not
 * `toISOString()`, which renders UTC: at 00:30 in Kigali that is the previous day.
 * `src/lib/no-utc-dates.test.ts` scans `e2e/` for exactly this.
 */
function iso(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

const today = () => iso(new Date());
const monthStart = () => iso(new Date(new Date().getFullYear(), new Date().getMonth(), 1));
const monthEnd = () => iso(new Date(new Date().getFullYear(), new Date().getMonth() + 1, 0));

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

async function drain(page: Page): Promise<void> {
  await apiOk(page, "/fiscal/outbox/drain", { method: "POST" });
}

async function sandboxReset(request: APIRequestContext): Promise<void> {
  const res = await request.post(`${SANDBOX_ADMIN}/_sandbox/reset`);
  expect(res.ok(), `sandbox reset -> ${res.status()}`).toBe(true);
}

/** Switch what the authority does next. `down` is what makes a queued row **deterministic**:
 * the worker drains every fifteen seconds, so "the row is still in flight" cannot be asserted
 * by being quick, and an unreachable authority keeps it queued however often the worker tries. */
async function sandboxMode(request: APIRequestContext, mode: string): Promise<void> {
  const res = await request.post(`${SANDBOX_ADMIN}/_sandbox/mode`, { data: { mode } });
  expect(res.ok(), `sandbox mode ${mode} -> ${res.status()}`).toBe(true);
}

async function pendingRows(page: Page): Promise<number> {
  const devices = (await apiOk(page, "/fiscal/queue")) as Array<{ pending_rows: number }>;
  return devices.reduce((total, device) => total + device.pending_rows, 0);
}

/**
 * Drain until the device is holding nothing, clearing the backoff on anything that is waiting.
 *
 * A drain alone is not enough, and the reason is the design working: a row the authority refused
 * or could not be reached for is due again in 1, then 5, then 15 minutes, and `drain_company`
 * picks up only what is **due**. So after the sandbox comes back, the rows it rejected while it
 * was down are still on a timer, and a helper that only drained would wait six minutes to find
 * out. Retry is what clears the timer.
 *
 * **Through the API here, deliberately, and unlike step 7's version of this helper.** That
 * button's caller is step 7's to own and `p7-transactions.spec.ts` presses it; this is fixture
 * hygiene. Driving it from the queue screen means a click loop over a list that refetches every
 * fifteen seconds — the row under the pointer can be replaced between `count()` and `click()`,
 * and Playwright then waits on a detached node until the test times out. That is a flake bought
 * for nothing, and it cost this file two runs before it was named.
 *
 * Bounded rather than `while`: a queue that never clears is a failure to report, not a loop to
 * spin in.
 */
async function drainUntilClear(page: Page): Promise<void> {
  for (let pass = 0; pass < 8; pass += 1) {
    if ((await pendingRows(page)) === 0) return;
    await drain(page);
    if ((await pendingRows(page)) === 0) return;
    const rows = (await apiOk(page, "/fiscal/queue/rows")) as Array<{
      row_id: number;
      status: string;
    }>;
    for (const row of rows) {
      if (row.status === "sent" || row.status === "cancelled") continue;
      await pageFetch(page, `/fiscal/queue/rows/${row.row_id}/retry`, { method: "POST" });
    }
    await drain(page);
  }
  expect(await pendingRows(page), "the queue never cleared").toBe(0);
}

/** "FRw 17,688.20" or "17,688.20" -> 17688.2. The screens render money through `formatMoney`,
 * so a test that wants the number has to undo the grouping the reader is shown. */
function amount(text: string): number {
  const match = /(-?[\d,]+(?:\.\d+)?)/.exec(text.replace(/−/g, "-"));
  expect(match, `no number in: ${text}`).not.toBeNull();
  return Number(match![1].replace(/,/g, ""));
}

/**
 * The current page, printed, as text.
 *
 * `page.pdf()` renders with `print` media, which is the only way to see what a `ReportPage`
 * actually puts on paper: the filters, the nav and the buttons are `print:hidden` and the
 * masthead is `hidden print:block`, so a DOM assertion on the screen proves nothing about the
 * sheet. `pdftotext` rather than a rendered comparison, because what is asserted is that a
 * figure reached the page and not how it is laid out. Poppler is in CI alongside the
 * WeasyPrint libraries the AR/AP statements need.
 */
async function printedText(page: Page, path: string): Promise<string> {
  await page.pdf({ path, format: "A4", printBackground: false });
  return execFileSync("pdftotext", ["-layout", path, "-"], { encoding: "utf8" });
}

/** A device this company holds, registered and initialized against the sandbox. */
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
  await apiOk(page, `/fiscal/devices/${device.id}/initialize`, {
    method: "POST",
    headers: { "Idempotency-Key": `p7-step8-init-${Date.now()}` },
  });
  await apiOk(page, `/fiscal/devices/${device.id}/sync-codes`, { method: "POST" });
  await apiOk(page, `/fiscal/devices/${device.id}/sync-item-classes`, { method: "POST" });
  return device;
}

test.describe.configure({ mode: "serial", timeout: 300_000 });

test.describe("the tax enquiries and reports", () => {
  let deviceId = 0;
  let itemId = 0;
  let customerId = 0;
  let invoiceAId = 0;
  let reversibleId = 0;

  async function postInvoice(
    page: Page,
    quantity: string,
    tag: string,
  ): Promise<Identified & { number: string }> {
    return (await apiOk(page, "/subledger/ar/documents", {
      method: "POST",
      headers: { "Idempotency-Key": `p7-step8-${tag}-${SUFFIX}` },
      body: {
        kind: "invoice",
        partner_id: customerId,
        document_date: today(),
        description: `P8 ${tag} ${SUFFIX}`,
        purchase_code: PURCHASE_CODE,
        lines: [{ item_id: itemId, quantity, unit_price: PRICE }],
      },
    })) as Identified & { number: string };
  }

  test("the catalogue and the device are fiscal-ready", async ({ page, request }) => {
    await sandboxReset(request);
    await sandboxMode(request, "up");
    await login(page, PRIMARY_EMAIL);
    deviceId = (await activeDevice(page)).id;

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
    const taxCodes = (await apiOk(page, "/gl/tax-codes")) as Array<
      Identified & { code: string; fiscal_tax_type: string | null }
    >;
    const outputVat = taxCodes.find((code) => code.code === "VAT-OUT-18")!;

    const item = (await apiOk(page, "/inventory/items", {
      method: "POST",
      body: {
        code: ITEM_CODE,
        name: `Awkward-priced wine ${SUFFIX}`,
        uom_category_id: count.id,
        base_uom_id: each.id,
        item_type: "stock",
        selling_price: PRICE,
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
          name: `${role === "ar" ? "P8 customer" : "P8 supplier"} ${SUFFIX}`,
          [role === "ar" ? "customer_code" : "supplier_code"]: code,
          tin,
          phone: "+250788000009",
        },
      });
    }
    const customers = (await apiOk(page, "/subledger/ar/partners")) as Array<
      Identified & { customer_code: string | null }
    >;
    customerId = customers.find((p) => p.customer_code === CUSTOMER_CODE)!.id;

    // A fiscalized company's negative-stock policy is locked at `block` (CIS §7.30), so a sale
    // with nothing behind it is refused before it ever reaches the queue.
    const warehouses = (await apiOk(page, "/inventory/warehouses")) as Array<
      Identified & { code: string }
    >;
    const suppliers = (await apiOk(page, "/subledger/ap/partners")) as Array<
      Identified & { supplier_code: string | null }
    >;
    const supplierId = suppliers.find((s) => s.supplier_code === SUPPLIER_CODE)!.id;
    await apiOk(page, "/oe/goods-received-notes", {
      method: "POST",
      headers: { "Idempotency-Key": `p7-step8-grn-${SUFFIX}` },
      body: {
        partner_id: supplierId,
        grn_date: today(),
        description: `P8 opening stock ${SUFFIX}`,
        warehouse_id: warehouses[0].id,
        lines: [{ item_id: itemId, quantity: "200", unit_cost: "1000" }],
      },
    });

    // **A purchase, so the purchases annex has a row and not a header on its own.** The annex
    // is the authority's listing of what this taxpayer bought, and a test that asserted only a
    // header would pass over an annex that never found a document — which is the same shape of
    // pass the rule-14 register exists to prevent, one file over.
    await apiOk(page, "/subledger/ap/documents", {
      method: "POST",
      headers: { "Idempotency-Key": `p7-step8-sin-${SUFFIX}` },
      body: {
        kind: "invoice",
        partner_id: supplierId,
        document_date: today(),
        description: `P8 purchase ${SUFFIX}`,
        lines: [
          {
            item_id: itemId,
            quantity: "20",
            unit_price: "1000",
            tax_code_id: taxCodes.find((code) => code.code === "VAT-IN-18")!.id,
          },
        ],
      },
    });

    // --- and one open USD receivable, so the revaluation has something to revalue -----------
    //
    // The mirror posts the day **after** the revaluation date, so a month-end run reaches into
    // the next period and the seeded tenant marks that one `future`. Opening it is what an
    // accountant does by hand before closing a month, and the run is refused without it.
    const currencies = (await apiOk(page, "/gl/currencies")) as Array<
      Identified & { code: string; is_base: boolean }
    >;
    const usd = currencies.find((c) => !c.is_base)!;
    const rates = (await apiOk(page, `/gl/exchange-rates?currency_id=${usd.id}`)) as Array<{
      valid_from: string;
    }>;
    for (const [validFrom, rate] of [
      [monthStart(), "1320"],
      [monthEnd(), "1350"],
    ] as const) {
      if (rates.some((r) => r.valid_from === validFrom)) continue;
      await apiOk(page, "/gl/exchange-rates", {
        method: "POST",
        body: { currency_id: usd.id, valid_from: validFrom, rate },
      });
    }
    await apiOk(page, "/subledger/ar/documents", {
      method: "POST",
      headers: { "Idempotency-Key": `p7-step8-usd-${SUFFIX}` },
      body: {
        kind: "invoice",
        partner_id: customerId,
        document_date: today(),
        description: `P8 USD invoice ${SUFFIX}`,
        purchase_code: PURCHASE_CODE,
        currency_id: usd.id,
        lines: [{ item_id: itemId, quantity: "10", unit_price: "2" }],
      },
    });

    const periods = (await apiOk(page, "/gl/periods")) as Array<
      Identified & { start_date: string; status: string }
    >;
    const next = periods.find((p) => p.start_date > monthEnd() && p.status !== "open");
    if (next) await apiOk(page, `/gl/periods/${next.id}/open`, { method: "POST" });
    await apiOk(page, "/gl/fx-revaluations", {
      method: "POST",
      headers: { "Idempotency-Key": `p7-step8-fxr-${SUFFIX}` },
      body: { revaluation_date: monthEnd(), role: "both" },
    });

    await drainUntilClear(page);
  });

  // PATH: /tax/reports/daily-fiscal -> GET /fiscal/devices/{id}/x-report, GET /fiscal/queue.
  // CANNOT SEE: what a Z stores — that is the next test, after this one has closed the day.
  test("the X view warns before the button while a row is still in flight", async ({
    page,
    request,
  }) => {
    await login(page, PRIMARY_EMAIL);

    // **The authority goes down before this invoice is posted.** The worker drains every
    // fifteen seconds, so a row RRA can answer is signed before any test could look at it —
    // and this test's whole subject is a device holding something it has not sent.
    await sandboxMode(request, "down");
    await postInvoice(page, "1", "stuck");
    await drain(page);
    expect(await pendingRows(page), "the sale is stuck behind an unreachable authority")
      .toBeGreaterThan(0);

    await page.goto("/tax/reports/daily-fiscal");
    await page.waitForSelector("h1:has-text('Daily fiscal report')");

    // **The warning is shown before the button and never instead of it.** Decision 11 makes a
    // Z record `queued_rows` on its face precisely so a day closed over an unsent sale is
    // evidence rather than a silent gap — so the screen says what closing now means and lets
    // the person decide. Refusing would leave a shop unable to close because a line was down,
    // which turns the outage into the shop's problem instead of the queue's.
    await expect(page.getByTestId("close-day-warning")).toBeVisible();
    await expect(page.getByTestId("close-day-clear")).toHaveCount(0);
    await expect(page.getByTestId("close-day")).toBeEnabled();
    await expect(page.getByTestId("day-queued-rows")).not.toHaveText("0");

    // --- the authority comes back, and the warning goes with it ---------------------------
    await sandboxMode(request, "up");
    await drainUntilClear(page);
    await page.reload();
    await page.waitForSelector("h1:has-text('Daily fiscal report')");
    await expect(page.getByTestId("close-day-clear")).toBeVisible();
    await expect(page.getByTestId("close-day-warning")).toHaveCount(0);

    // Close what is standing so far — everything this fixture and any earlier spec left — so
    // the next test's Z covers its own three documents and nothing else.
    await page.getByTestId("close-day").click();
    await expect(page.getByText(/Z-\d+ stored/).first()).toBeVisible({ timeout: 30_000 });
  });

  // PATH: /tax/reports/daily-fiscal and /tax/reports/receipts -> GET x-report, z-reports,
  // receipts/listing. CANNOT SEE: the VAT return's own figures, which read the ledger rather
  // than the wire and therefore do not equal these — that is the next test and the reason the
  // residue has a name.
  test("the Z totals what the receipts listing shows, receipt by receipt", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);

    // 3 + 7 sold, 1 returned — at 1 499 excl., which the two systems round differently.
    const invoiceA = await postInvoice(page, "3", "inv-a");
    invoiceAId = invoiceA.id;
    await postInvoice(page, "7", "inv-b");
    await drainUntilClear(page);

    const lines = (await apiOk(page, `/subledger/ar/documents/${invoiceA.id}`)) as {
      lines: Array<Identified & { line_no: number }>;
    };
    await apiOk(page, "/subledger/ar/documents", {
      method: "POST",
      headers: { "Idempotency-Key": `p7-step8-crn-${SUFFIX}` },
      body: {
        kind: "credit_note",
        partner_id: customerId,
        document_date: today(),
        description: `P8 refund ${SUFFIX}`,
        purchase_code: PURCHASE_CODE,
        refund_reason: REFUND_REASON,
        refund_of_document_id: invoiceA.id,
        lines: [
          {
            item_id: itemId,
            quantity: "1",
            unit_price: PRICE,
            returns_line_id: lines.lines[0].id,
          },
        ],
      },
    });
    await drainUntilClear(page);

    // --- the receipts side, summed from the rows ------------------------------------------
    await page.goto("/tax/reports/receipts");
    await page.waitForSelector("h1:has-text('Fiscal receipts listing')");
    // The default range is month-to-date, which already covers everything signed today. Left
    // alone on purpose: the rows below are asserted individually, so a wider range is a fuller
    // screen rather than a different answer — and driving the two calendars to today would be
    // ceremony that could only break.

    // Each receipt's own declaration, on its own row. The wire's figures, at the wire's two
    // decimals — a screen that rounded them to the franc would be a screen that could not show
    // the residue this report exists for.
    for (const declared of [DECLARED.invoiceA, DECLARED.invoiceB, DECLARED.creditNote]) {
      await expect(
        page.getByTestId("listing-declared").filter({ hasText: declared }).first(),
      ).toBeVisible({ timeout: 30_000 });
    }
    // And the ledger's, beside them, in whole francs.
    for (const posted of [POSTED.invoiceA, POSTED.invoiceB, POSTED.creditNote]) {
      await expect(
        page.getByTestId("listing-posted").filter({ hasText: posted }).first(),
      ).toBeVisible();
    }

    const declaredSales =
      amount(DECLARED.invoiceA) + amount(DECLARED.invoiceB);
    expect(declaredSales, "the two sales, summed by the test from the rows it read").toBeCloseTo(
      amount(DECLARED.salesTotal),
      2,
    );

    // --- the Z side, totalled by the server -----------------------------------------------
    await page.goto("/tax/reports/daily-fiscal");
    await page.waitForSelector("h1:has-text('Daily fiscal report')");
    await expect(page.getByTestId("close-day-clear")).toBeVisible();
    await page.getByTestId("close-day").click();
    await expect(page.getByText(/Z-\d+ stored/).first()).toBeVisible({ timeout: 30_000 });

    // The number is asserted by **shape**, never as a literal: this spec runs on the shared
    // Rugari Wines E2E company and the `FZR` run is consumed by whatever ran before it.
    await expect(page.getByTestId("z-number").first()).toHaveText(/^Z-\d+$/);

    // **Two screens, one number.** The Z's sales total is the server absorbing every receipt in
    // its range; the figure above is the test adding up what the listing rendered per receipt.
    await expect(page.getByTestId("day-ns-gross")).toHaveText(DECLARED.salesTotal);
    await expect(page.getByTestId("day-nr-gross")).toHaveText(DECLARED.creditNote);
    await expect(page.getByTestId("day-net")).toHaveText(DECLARED.net);
    await expect(page.getByTestId("day-ns-count")).toHaveText("2");
    await expect(page.getByTestId("day-nr-count")).toHaveText("1");
    // Rule 13's quantity: how many **things** were sold, not how many lines. Ten bottles across
    // two invoices, one back.
    await expect(page.getByTestId("day-items-ns")).toHaveText("10");
    await expect(page.getByTestId("day-items-nr")).toHaveText("1");

    // **The residue, named.** Declared 15,919.38 against a ledger of 15,919 — the +0.46 on
    // INV-A, the −0.26 on INV-B and the +0.18 the refund adds back. Nobody's defect, and the
    // one figure on the page that an accountant would otherwise spend a morning on.
    await expect(page.getByTestId("day-posted")).toHaveText("15,919.00");
    await expect(page.getByTestId("day-residue")).toHaveText("0.38");

    // --- and the day is closed, so the X starts again from nothing -------------------------
    //
    // **Back to the X tab**, which is where Close day lives — a Z is a thing that happened, the
    // X is the day you are still in. Worth pressing back to because the coupling is invisible
    // from the source: the first close switched the screen to Z and took the button with it.
    //
    // What is *not* asserted here is the second close being refused. It is real — a Z's range
    // runs from the previous close to now, both floored to the second, so an immediate second
    // close has a range of zero length — but pressing the button twice is not a reliable way to
    // ask: the tab switch and the render cost more than the question's resolution, and once a
    // second has elapsed the close succeeds and stores an empty Z, which is also correct. A
    // test that is right only when the machine is fast is a test about the machine. The clock
    // is injectable at the service level, so the refusal is pinned there instead —
    // `tests/fiscal/test_daily_report.py::test_a_second_close_at_the_same_instant_is_refused`.
    await page.getByRole("tab", { name: /X —/ }).click();
    await expect(page.getByTestId("day-ns-count")).toHaveText("0");
  });

  // PATH: /ar/documents/{id} -> POST /subledger/ar/documents/{id}/reverse, then back to the
  // daily report. CANNOT SEE: the refund's own paper — step 7's spec prints it.
  test("reversing a signed sale puts an NR on the next Z", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);

    const invoice = await postInvoice(page, "2", "reversible");
    reversibleId = invoice.id;
    await drainUntilClear(page);

    // **Through the screen**, because this is the tape's row 14 and the point is that an
    // operator can do it: reversing a fiscalized invoice queues a full **refund** rather than
    // cancelling the sale (decision 7), and the §4.16 reason code is asked for on the dialog.
    await page.goto(`/ar/documents/${invoice.id}`);
    await page.waitForSelector("[data-testid='fiscal-receipt-number']");
    await page.getByRole("button", { name: "Reverse", exact: true }).click();
    await page.getByLabel("Reason", { exact: true }).fill(`P8 reversal ${SUFFIX}`);
    await pickCombobox(page, "Refund reason", REFUND_REASON);
    await page.getByTestId("confirm-reverse").click();
    // The dialog closes on success. **The `NR` does not exist yet** — reversing queues a
    // refund and RRA has not signed it, which is decision 7 working rather than a delay: there
    // is no draft form of a receipt (CIS §10). So drain first, then look.
    await expect(page.getByTestId("confirm-reverse")).toHaveCount(0, { timeout: 30_000 });
    await drainUntilClear(page);

    await page.reload();
    await expect(page.getByTestId("receipt-NR")).toBeVisible({ timeout: 30_000 });

    await page.goto("/tax/reports/daily-fiscal");
    await page.waitForSelector("h1:has-text('Daily fiscal report')");
    await page.getByTestId("close-day").click();
    await expect(page.getByText(/Z-\d+ stored/).first()).toBeVisible({ timeout: 30_000 });

    // One sale and one refund since the last close: the invoice that was reversed, and the
    // refund that reversed it. RRA cannot un-sign a sale, so both are on the day.
    await expect(page.getByTestId("day-ns-count")).toHaveText("1");
    await expect(page.getByTestId("day-nr-count")).toHaveText("1");
    // 1 768.82 × 2, both ways.
    await expect(page.getByTestId("day-ns-gross")).toHaveText("3,537.64");
    await expect(page.getByTestId("day-nr-gross")).toHaveText("3,537.64");
    await expect(page.getByTestId("day-net")).toHaveText("0.00");
  });

  // PATH: /tax/reports/daily-fiscal, the Z tab -> GET /fiscal/devices/{id}/z-reports, then
  // `page.pdf()`. CANNOT SEE: whether a printer would break the page where the browser does.
  test("a Z prints with the device block and the day on it", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/tax/reports/daily-fiscal");
    await page.waitForSelector("h1:has-text('Daily fiscal report')");

    await page.getByRole("tab", { name: /Z —/ }).click();
    await expect(page.getByTestId("z-number").first()).toHaveText(/^Z-\d+$/);
    await page.getByTestId("open-z").first().click();

    const text = await printedText(page, `test-results/p8-z-${SUFFIX}.pdf`);
    // The masthead a sheet needs to say what it is — and the device it is a day of. An
    // inspector reads the SDC and the MRC off the paper; a Z that carried the figures and not
    // the device would be a day of somebody's takings with no device against it.
    expect(text, "the Z prints the device's SDC").toContain(SDC_ID);
    expect(text, "the Z prints the device's MRC").toContain(MRC_NO);
    expect(text, "the Z prints its own number").toMatch(/Z-\d+/);
    expect(text, "the Z prints the day it covers").toContain(String(new Date().getFullYear()));
  });

  // PATH: /fiscal/enquiries/receipts -> GET /fiscal/receipts with the search box.
  // CANNOT SEE: the drill actually opening the entry — the link's href is asserted instead,
  // because the entry page is P2's and has its own specs.
  test("the receipts enquiry finds a receipt by its printed counter and drills three deep",
    async ({ page }) => {
      await login(page, PRIMARY_EMAIL);
      await page.goto("/fiscal/enquiries/receipts");
      await page.waitForSelector("h1:has-text('Fiscal receipts')");

      // Rule 13's quantity on this screen: the counter as the paper prints it, parsed from the
      // page rather than asserted as a literal — the `FIS` run is shared and whichever spec ran
      // first took the low numbers.
      const counter = page.getByTestId("receipt-counter").first();
      await expect(counter).toHaveText(/^\d+\/\d+ N[SR]$/, { timeout: 30_000 });
      const printed = (await counter.textContent())!.trim();

      await expect(page.getByTestId("receipts-count")).not.toHaveText("0");
      await expect(page.getByTestId("receipts-total")).toHaveText(/\d/);

      // Receipt → document → journal entry: the three links an inspector's question travels.
      const row = page.locator("tbody tr").filter({ hasText: printed }).first();
      await expect(row.getByTestId("receipt-document")).toHaveAttribute(
        "href",
        /\/ar\/documents\/\d+$/,
      );
      await expect(row.getByTestId("receipt-entry")).toHaveAttribute(
        "href",
        /\/gl\/entries\/\d+$/,
      );

      // --- the one box, over the printed counter --------------------------------------------
      await page.getByLabel("Find a receipt", { exact: true }).fill(printed);
      await expect(page.getByTestId("receipt-counter").first()).toHaveText(printed, {
        timeout: 30_000,
      });
      await expect(page.getByTestId("receipts-count")).toHaveText("1");
    });

  // PATH: /fiscal/enquiries/queue-history -> GET /fiscal/queue/rows?document_id=, then
  // GET /fiscal/queue/rows/{id} for the action log. CANNOT SEE: retry, verify or attach —
  // they are deliberately not here, and step 7's spec presses them on the queue screen.
  test("the queue history shows one document's rows, and nothing to press", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto("/fiscal/enquiries/queue-history");
    await page.waitForSelector("h1:has-text('Fiscal queue history')");

    // The picker is fed by the **queue**, not by the receipts: a receipt exists only once RRA
    // has signed, so a list built from receipts could not offer the documents somebody opens a
    // queue history to ask about.
    await page.getByLabel(/Filter by document number/, { exact: false }).first().fill("INV-");
    await page.getByRole("combobox", { name: "Document", exact: true }).click();
    await page.getByRole("option").filter({ hasText: "INV-" }).first().click();

    await expect(page.getByTestId("history-sequence").first()).toHaveText(/^\d+$/, {
      timeout: 30_000,
    });
    await expect(page.getByTestId("history-status").first()).toBeVisible();

    // Read-only: the acts live where the device's state is visible beside them.
    await expect(page.getByRole("button", { name: "Retry now" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Open the Fiscal queue" })).toBeVisible();

    await page.getByTestId("history-inspect").first().click();
    await expect(page.getByText("Action log")).toBeVisible();
  });

  // PATH: /tax/reports/vat-return -> GET /tax/vat-returns/{id}, the two annex CSVs, and
  // `page.pdf()`. CANNOT SEE: that the figures are right — `tests/tax/` proves the arithmetic;
  // this proves it reaches paper and a file.
  test("the VAT return prints its net payable and hands over both annexes", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);

    // File the month, so there is a filed return to report on. Through the API: the act is
    // step 7's screen and is pressed there; what this file is about is reading one back.
    const filed = (await apiOk(page, "/tax/vat-returns", {
      method: "POST",
      headers: { "Idempotency-Key": `p7-step8-file-${SUFFIX}` },
      body: { period_from: monthStart(), period_to: monthEnd() },
    })) as Identified & { number: string; net_payable: string };
    expect(filed.number, "shape, never a literal — the VAT run is shared").toMatch(/^VATR-\d+$/);

    await page.goto(`/tax/reports/vat-return/${filed.id}`);
    await page.waitForSelector("h1:has-text('VAT return')");
    await expect(page.getByTestId("report-return-number")).toHaveText(filed.number);

    const onScreen = amount((await page.getByTestId("report-net-payable").textContent())!);
    expect(onScreen, "the screen shows what was filed").toBeCloseTo(Number(filed.net_payable), 2);
    await expect(page.getByTestId("report-high-water")).toHaveText(/^\d+$/);
    await expect(page.getByTestId("report-settlement-entry")).toHaveAttribute(
      "href",
      /\/gl\/entries\/\d+$/,
    );

    // --- the sheet ------------------------------------------------------------------------
    const text = await printedText(page, `test-results/p8-vat-${SUFFIX}.pdf`);
    expect(text, "the return prints its number").toContain(filed.number);
    expect(text, "the return prints the net payable").toContain(
      new Intl.NumberFormat("en-GB").format(Math.round(Math.abs(Number(filed.net_payable)))),
    );

    // --- the annexes, downloaded rather than rebuilt ---------------------------------------
    // The bytes are the **server's**: these are the authority's own listings over data no
    // screen holds, and a client that assembled them would be a second implementation of a
    // filing. Asserted on the real download, never on JSON behind it.
    for (const [testId, header, stem] of [
      ["annex-sales", "customer_tin", "vat-sales"],
      ["annex-purchases", "supplier_tin", "vat-purchases"],
    ] as const) {
      const [download] = await Promise.all([
        page.waitForEvent("download", { timeout: 30_000 }),
        page.getByTestId(testId).click(),
      ]);
      expect(download.suggestedFilename()).toContain(stem);
      const body = readFileSync((await download.path())!, "utf8");
      const lines = body.trim().split(/\r?\n/);
      expect(lines[0], "the annex's own header").toContain(header);
      expect(lines.length, "a header with no rows under it is not an annex").toBeGreaterThan(1);
      if (testId === "annex-sales") {
        // One real row: this run's own invoice, with the receipt block against it.
        expect(body, "the sales annex carries a fiscalized sale of this run").toContain(SDC_ID);
      }
    }
  });

  // PATH: /gl/reports/fx-revaluation/{id} -> GET /gl/fx-revaluations/{id}. CANNOT SEE: the
  // posting map — `tests/tax/test_returns_api.py` and the backend tape assert the accounts.
  test("the FX revaluation report shows a run's lines and names its three entries", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);

    const runs = (await apiOk(page, "/gl/fx-revaluations")) as Array<
      Identified & { number: string; journal_entry_id: number | null }
    >;
    // Not `test.skip`: this file posts the run itself, in its own setup, precisely so that this
    // screen is opened **with data in it** (rule 13). A report that skipped when the fixture
    // happened to be empty would be a report nothing had ever opened.
    expect(runs.length, "this file's own setup posts a run").toBeGreaterThan(0);

    const run = runs[0];
    expect(run.number, "shape, never a literal — the FXR run is shared").toMatch(/^FXR-\d+$/);

    await page.goto(`/gl/reports/fx-revaluation/${run.id}`);
    await page.waitForSelector("h1:has-text('FX revaluation')");
    await expect(page.getByTestId("fx-run-number")).toHaveText(run.number);
    // Rule 13, both halves: a quantity (how many documents this run touched) and money.
    await expect(page.getByTestId("fx-line-count")).toHaveText(/^\d+$/);
    await expect(page.getByTestId("fx-total-difference")).toHaveText(/\d/);
    await expect(page.getByTestId("fx-line-difference").first()).toHaveText(/\d/);
    await expect(page.getByTestId("fx-line-document").first()).toHaveAttribute(
      "href",
      /\/a[rp]\/documents\/\d+$/,
    );

    // The run, and its next-day mirror. Two `FXR-` entries a day apart for one run, which is
    // the thing this screen exists to make obvious.
    await expect(page.getByTestId("fx-entry")).toHaveAttribute("href", /\/gl\/entries\/\d+$/);
    await expect(page.getByTestId("fx-mirror")).toHaveAttribute("href", /\/gl\/entries\/\d+$/);
  });

  // PATH: /gl/entries/{id} -> GET /gl/entries/{id}, the drill keys this step added.
  // CANNOT SEE: the counter-entry's pair when no run has been reversed on this fixture —
  // `tests/tax/test_entry_drill.py` builds that state directly and asserts both directions.
  test("a VATR- and an FXR- entry each name the document they belong to", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);

    const returns = (await apiOk(page, "/tax/vat-returns")) as Array<
      Identified & { number: string; journal_entry_id: number | null }
    >;
    const filed = returns.find((row) => row.journal_entry_id !== null)!;

    // The **endpoint** is `/gl/journal-entries/{id}`; `/gl/entries/{id}` is the screen in front
    // of it. Worth spelling out because this test wants both, and reaching for the page's path
    // in a `fetch` is a 404 that reads like a missing entry.

    // Before this step the settlement entry was a `VATR-` number on the trial balance with
    // nothing behind it: `tax` is in no module document table and the entry page had nowhere
    // to look. `sources.py` now carries the key.
    const entry = (await apiOk(page, `/gl/journal-entries/${filed.journal_entry_id}`)) as {
      module_document_id: number | null;
      module_document_number: string | null;
      module_document_target: string | null;
    };
    expect(entry.module_document_target).toBe("vat_return");
    expect(entry.module_document_id).toBe(filed.id);
    expect(entry.module_document_number).toBe(filed.number);

    // On the screen, not only in the payload. A `VATR-` entry is module-owned (`tax`), so it
    // carries the "reverse via the module's document" link the GL has offered since P6.
    await page.goto(`/gl/entries/${filed.journal_entry_id}`);
    await page.waitForSelector("h1");
    await expect(page.getByTestId("reverse-via-module")).toHaveAttribute(
      "href",
      `/tax/reports/vat-return/${filed.id}`,
    );

    const runs = (await apiOk(page, "/gl/fx-revaluations")) as Array<
      Identified & { number: string; journal_entry_id: number | null; mirror_entry_id: number | null }
    >;
    const run = runs.find((row) => row.mirror_entry_id !== null);
    expect(run, "the setup posts a run, and a run always mirrors").toBeDefined();

    // **The mirror is the one with nothing of its own.** Posted as a `ReversalRequested`, so no
    // `source_doc_type` at all — it resolves through `fx_revaluations.mirror_entry_id`, and
    // before this step it was an `FXR-` entry on the trial balance belonging to nothing.
    for (const entryId of [run!.journal_entry_id, run!.mirror_entry_id]) {
      const resolved = (await apiOk(page, `/gl/journal-entries/${entryId}`)) as {
        module_document_id: number | null;
        module_document_target: string | null;
      };
      expect(resolved.module_document_target).toBe("fx_revaluation");
      expect(resolved.module_document_id).toBe(run!.id);
    }

    // **And on the screen.** An `FXR-` entry's module is `gl`, so it takes neither the
    // module-owned branch nor its link — the server resolved the document and the page dropped
    // it, which is the P4 failure mode exactly. `entry-source-document` is what step 8 added.
    await page.goto(`/gl/entries/${run!.mirror_entry_id}`);
    await page.waitForSelector("h1");
    await expect(page.getByTestId("entry-source-document")).toHaveAttribute(
      "href",
      `/gl/reports/fx-revaluation/${run!.id}`,
    );
  });

  test("the device is suspended, and the company is not fiscalized again", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await drainUntilClear(page);
    // Suspending takes a **reason**: a device going out of service is an event somebody has to
    // be able to explain later, so the payload is required rather than defaulted.
    await apiOk(page, `/fiscal/devices/${deviceId}/suspend`, {
      method: "POST",
      body: { reason: `P7 step 8 e2e finished ${SUFFIX}` },
    });

    const context = (await apiOk(page, "/fiscal/document-context")) as { fiscalized: boolean };
    expect(context.fiscalized, "the fixture is put back for whatever runs next").toBe(false);
    // Referenced so the linter can see these are the run's own documents, and so a failure
    // names them.
    expect(invoiceAId, "the tie's first invoice").toBeGreaterThan(0);
    expect(reversibleId, "the reversed invoice").toBeGreaterThan(0);
  });
});
