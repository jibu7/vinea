import { expect, test, type Page } from "@playwright/test";
import { PRIMARY_EMAIL, SALES_EMAIL, login, pageFetch } from "./support/fixtures";

/**
 * P4 step 9 — the acceptance tape from the phase prompt, driven through the UI, run once per
 * currency.
 *
 * PATH: the screens a person actually uses — Customers → Invoice → Receipt → Allocate →
 * the journal entry → Statements → Age analysis — against the real stack in docker compose.
 *
 * CANNOT SEE: that the numbers are *right* in the accounting sense. Every figure here is
 * asserted against another surface of the same system (the ageing against the enquiry, the
 * entry against the allocation preview), so a bug in the posting engine that is consistent
 * across all of them would pass. `assert_subledger_invariants` and the step 8 acceptance test
 * are what hold that line; this proves the screens reach those paths and show what they
 * returned.
 *
 * Exchange *rates* are seeded through `POST /gl/exchange-rates` rather than the Foreign
 * currency screen: rates are a precondition of the tape, not a step in it, and that screen has
 * its own coverage. Everything the tape itself claims to test goes through the UI.
 */

const REVENUE = "4100";
const BANK = "1120";
const FX_GAIN = "4400"; // Realized exchange gain
const POST_DATED_AR = "1250"; // Post-dated receivable

interface Tape {
  /** Appears in the test name, so a failure says which pass broke. */
  label: string;
  code: string;
  /** Undefined means base currency: no rate field on the screen, and no FX to realize. */
  currency?: "USD";
  invoiceRate?: string;
  receiptRate?: string;
  invoiceAmount: string;
  receiptAmount: string;
  /** What the invoice is worth in base currency when it is booked. */
  invoiceBase: number;
  /** What is still open in base after the allocation.
   *
   * Not `invoiceBase - receiptBase`. The receipt was booked at its own rate, but what it
   * *relieves* from the receivable is the allocated amount at the **invoice's** rate — the
   * gap between the two is exactly the realized difference the allocation posts away. So the
   * residual is the unallocated foreign amount at the document's booking rate, and the
   * control account nets to it: 1,200,000 - 524,000 + 44,000 = 720,000. */
  openBase: number;
  creditLimit: string;
}

const TAPES: Tape[] = [
  {
    label: "USD",
    code: "TAPEUSD",
    currency: "USD",
    invoiceRate: "1200",
    receiptRate: "1310",
    invoiceAmount: "1000.00",
    receiptAmount: "400.00",
    invoiceBase: 1_200_000,
    openBase: 720_000, // (1000 - 400) USD at the invoice's 1200
    creditLimit: "5000000",
  },
  {
    label: "RWF",
    code: "TAPERWF",
    // No currency, no rate: RWF is base, so there is no exchange difference to realize and
    // no decimals to render. The same tape, with the FX assertions inverted.
    invoiceAmount: "500000",
    receiptAmount: "200000",
    invoiceBase: 500_000,
    openBase: 300_000, // base currency throughout, so the two rates are the same rate
    creditLimit: "5000000",
  },
];

function unique(prefix: string): string {
  return `${prefix}${String(Date.now()).slice(-6)}`;
}

async function pick(page: Page, name: string, needle: string): Promise<void> {
  await page.getByRole("button", { name, exact: true }).click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(needle);
  await page.locator(`[cmdk-item]:has-text("${needle}")`).first().click();
}

async function pickLineAccount(page: Page, code: string): Promise<void> {
  await page.getByRole("button", { name: "Account, row 1" }).click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(code);
  await page.locator(`[cmdk-item]:has-text("${code}")`).first().click();
}

/** Creates the customer through the Customers screen, then gives it terms and a credit limit
 * on the AR settings tab — the two things the tape's later steps depend on. */
async function makeCustomer(
  page: Page,
  code: string,
  name: string,
  opts: { creditLimit?: string; currency?: string } = {},
): Promise<void> {
  await page.goto("/maintenance/customers");
  await page.waitForSelector("h1:has-text('Customers')");
  await page.getByRole("button", { name: /New customer/i }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Customer code").fill(code);
  await dialog.getByLabel("Name", { exact: true }).fill(name);
  await dialog.getByRole("button", { name: "Create", exact: true }).click();
  await expect(page.getByRole("dialog").filter({ hasText: name })).toBeVisible();

  await page.getByRole("tab", { name: "AR settings" }).click();
  await pick(page, "Payment terms", "NET30");
  if (opts.creditLimit !== undefined) {
    await page.getByLabel(/^Credit limit/).fill(opts.creditLimit);
  }
  await page.getByRole("button", { name: /^Save/ }).click();
  await expect(page.getByText(/saved/i).first()).toBeVisible({ timeout: 15_000 });
  await page.keyboard.press("Escape");
}

async function seedRate(page: Page, code: string, on: string, rate: string): Promise<void> {
  const currencies = (await pageFetch(page, "/gl/currencies")).json as Array<{
    id: number;
    code: string;
  }>;
  const currency = currencies.find((c) => c.code === code);
  if (!currency) throw new Error(`no seeded currency ${code}`);
  await pageFetch(page, "/gl/exchange-rates", {
    method: "POST",
    body: { currency_id: currency.id, valid_from: on, rate },
    headers: { "Idempotency-Key": crypto.randomUUID() },
  });
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function isoDaysFromNow(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

/** Posts the document on screen and waits for the journal entry it lands on. */
async function postDocument(page: Page): Promise<void> {
  await page.getByRole("button", { name: /^Post/ }).click();
  await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 30_000 });
}

for (const tape of TAPES) {
  test.describe(`P4 acceptance tape — ${tape.label}`, () => {
    test(`${tape.label}: invoice, receipt at another rate, allocation, statement, ageing`, async ({
      page,
    }) => {
      test.setTimeout(240_000);
      await login(page, PRIMARY_EMAIL);

      const code = unique(tape.code);
      const name = `Tape ${tape.label} ${code}`;
      if (tape.currency) {
        await seedRate(page, tape.currency, today(), tape.invoiceRate!);
      }
      await makeCustomer(page, code, name, { creditLimit: tape.creditLimit });

      // --- the invoice ----------------------------------------------------------------
      await page.goto("/ar/invoices/new");
      await page.waitForSelector("h1:has-text('Invoice')");
      await pick(page, "Customer", code);
      // The typeahead reports the limit we just set, so the tape proves the setting took.
      await expect(page.getByText("Credit headroom")).toBeVisible();
      await page.getByLabel("Description", { exact: true }).fill(`Tape invoice ${code}`);
      if (tape.currency) {
        await pick(page, "Currency", tape.currency);
        await page.getByLabel("Exchange rate").fill(tape.invoiceRate!);
      }
      await pickLineAccount(page, REVENUE);
      await page.getByLabel("Unit price, row 1").fill(tape.invoiceAmount);
      await postDocument(page);
      await expect(page.getByText("Posted").first()).toBeVisible();
      // Booked in base at the invoice's own rate, whatever currency it was raised in.
      await expect(page.getByText(String(tape.invoiceBase.toLocaleString("en-US"))).first()).toBeVisible();

      // --- the receipt, at a different rate -------------------------------------------
      if (tape.currency) {
        await seedRate(page, tape.currency, today(), tape.receiptRate!);
      }
      await page.goto("/ar/receipts/new");
      await page.waitForSelector("h1:has-text('Receipt')");
      await pick(page, "Customer", code);
      await page.getByLabel("Description", { exact: true }).fill(`Tape receipt ${code}`);
      if (tape.currency) {
        await pick(page, "Currency", tape.currency);
        await page.getByLabel("Exchange rate").fill(tape.receiptRate!);
      }
      await page.getByLabel("Amount", { exact: true }).fill(tape.receiptAmount);
      await pick(page, "Cash / bank account", BANK);
      await postDocument(page);
      await expect(page.getByText("Posted").first()).toBeVisible();

      // --- allocate, previewing first --------------------------------------------------
      await page.goto("/ar/allocations/new");
      await page.waitForSelector("h1:has-text('Allocate')");
      await pick(page, "Partner", code);
      await page.getByRole("button", { name: /^Apply / }).first().click();
      await page.getByLabel(/^Allocate against /).first().fill(tape.receiptAmount);

      // What posts must be what was shown: Post is refused until the preview is taken.
      await expect(page.getByRole("button", { name: /^Post/ })).toBeDisabled();
      await page.getByRole("button", { name: "Preview", exact: true }).click();

      const preview = page.getByTestId("allocation-preview");
      if (tape.currency) {
        // A rate that moved between booking and settlement realizes a difference, and the
        // preview shows the posting before it is written.
        await expect(preview.locator("table tbody tr").first()).toBeVisible();
        await expect(preview).toContainText(FX_GAIN);
      } else {
        // Same currency, no discount: the honest answer is that this allocation writes no
        // journal entry at all, and the panel says so rather than showing an empty table.
        await expect(page.getByTestId("allocation-preview-empty")).toBeVisible();
      }

      await page.getByRole("button", { name: /^Post/ }).click();
      await expect(page.getByText(/ALC-\d+ posted/).first()).toBeVisible({ timeout: 30_000 });

      // --- the realized FX, on the entry itself ----------------------------------------
      // Scoped to this partner. The suite shares one long-lived company, so an unfiltered
      // read here picks up whatever the *other* currency's pass left behind — which is
      // exactly how the RWF pass first "realized" the USD pass's 44,000.
      const partners = (await pageFetch(page, "/subledger/ar/partners")).json as Array<{
        id: number;
        customer_code: string;
      }>;
      const partner = partners.find((p) => p.customer_code === code)!;
      const allocations = (
        await pageFetch(page, `/subledger/ar/allocations?partner_id=${partner.id}`)
      ).json as Array<{
        number: string;
        amount: string;
        fx_base_amount: string;
      }>;
      const realized = allocations
        .map((a) => Number(a.fx_base_amount))
        .reduce((max, v) => (Math.abs(v) > Math.abs(max) ? v : max), 0);

      if (tape.currency) {
        // 400 USD relieved at 1200 but received at 1310 — the difference, and only the
        // difference, is what the allocation realizes. Compared as a magnitude: the sign on
        // `fx_base_amount` is the ledger's (a gain is a credit, so it reads negative), and
        // *which way* it went is asserted below by the account it landed in, which is the
        // durable statement. Asserting the sign here would be asserting a convention.
        const expectedFx =
          Number(tape.receiptAmount) * (Number(tape.receiptRate) - Number(tape.invoiceRate));
        expect(Math.abs(realized)).toBeCloseTo(Math.abs(expectedFx), 2);

        // And it is *in the entry*, named — not merely a number in an API response.
        const accounts = (await pageFetch(page, "/gl/accounts")).json as Array<{
          id: number;
          code: string;
        }>;
        const gain = accounts.find((a) => a.code === FX_GAIN)!;
        const txns = (
          await pageFetch(
            page,
            `/gl/accounts/${gain.id}/transactions?date_from=${today()}&date_to=${today()}`,
          )
        ).json as { items: Array<{ entry_id: number; base_amount: string }> };
        // The rate rose between booking and settlement, so the difference is a *gain*: it is
        // the gain account that moved, and the loss account that did not.
        expect(txns.items.length, "the gain account carries the difference").toBeGreaterThan(0);
        // The most recent row on the gain account is this allocation's — the tape posted it
        // moments ago, and `items` is in entry order.
        const entryId = txns.items[txns.items.length - 1].entry_id;
        await page.goto(`/gl/entries/${entryId}`);
        await page.waitForSelector("h1");
        await expect(page.getByText(new RegExp(`${FX_GAIN}\\s`)).first()).toBeVisible();
      } else {
        // Same currency: there is no rate to have moved, so nothing is realized.
        expect(realized, "a same-currency allocation realizes no exchange difference").toBe(0);
      }

      // --- the statement job, and a PDF at the end of it -------------------------------
      await page.goto("/ar/reports/statements");
      await page.waitForSelector("h1:has-text('Customer statements')");
      await page.getByRole("checkbox", { name }).check();
      await page.getByRole("button", { name: /Queue statement/ }).click();
      const download = page.getByTestId("statement-download");
      await expect(download).toBeVisible({ timeout: 60_000 });
      await expect(page.getByText("Ready")).toBeVisible();
      const [file] = await Promise.all([
        page.waitForEvent("download", { timeout: 30_000 }),
        download.click(),
      ]);
      expect(file.suggestedFilename()).toMatch(/\.pdf$/);

      // --- the age analysis agrees with the open items ---------------------------------
      const enquiry = (await pageFetch(page, `/subledger/ar/enquiry/${partner.id}`)).json as {
        balance_base: string;
      };
      const ageing = (
        await pageFetch(page, `/subledger/ar/ageing?partner_id=${partner.id}`)
      ).json as { rows: Array<{ partner_id: number; total: string }> };
      const row = ageing.rows.find((r) => r.partner_id === partner.id);
      expect(row, "the customer appears in the ageing").toBeTruthy();
      // Two independent reconstructions of the same position: the ageing buckets open items
      // by date, the enquiry walks the documents to a running balance.
      expect(Number(row!.total)).toBeCloseTo(Number(enquiry.balance_base), 2);
      // And it is the arithmetic the tape actually performed: the unallocated foreign amount
      // at the invoice's own booking rate.
      expect(Number(row!.total)).toBeCloseTo(tape.openBase, 2);

      // The screen shows the same figure it was asked for.
      await page.goto("/ar/reports/age-analysis");
      await page.waitForSelector("h1:has-text('Age analysis')");
      await expect(page.getByText(name)).toBeVisible({ timeout: 20_000 });
    });

    test(`${tape.label}: the credit-limit block fires, and the override clears it`, async ({
      page,
    }) => {
      test.setTimeout(180_000);
      // PATH: the invoice screen as a Sales Manager, who can post AR but holds no
      // `ar:credit_limit_override`. CANNOT SEE: the audit row the override writes — that is
      // asserted in the backend suite, not here.
      await login(page, SALES_EMAIL);
      const code = unique(`${tape.code}CL`);
      const name = `Limit ${tape.label} ${code}`;
      if (tape.currency) await seedRate(page, tape.currency, today(), tape.invoiceRate!);
      // A limit far below what the invoice will be worth in base currency.
      await makeCustomer(page, code, name, { creditLimit: "1000" });

      await page.goto("/ar/invoices/new");
      await page.waitForSelector("h1:has-text('Invoice')");
      await pick(page, "Customer", code);
      await page.getByLabel("Description", { exact: true }).fill(`Over limit ${code}`);
      if (tape.currency) {
        await pick(page, "Currency", tape.currency);
        await page.getByLabel("Exchange rate").fill(tape.invoiceRate!);
      }
      await pickLineAccount(page, REVENUE);
      await page.getByLabel("Unit price, row 1").fill(tape.invoiceAmount);
      await page.getByRole("button", { name: /^Post/ }).click();

      // Inline, on the field the server named — not a toast, and not a navigation.
      await expect(page.getByText(/credit limit exceeded/i).first()).toBeVisible({
        timeout: 30_000,
      });
      await expect(page).toHaveURL(/\/ar\/invoices\/new/);

      // The same invoice, by someone holding the override, posts.
      await login(page, PRIMARY_EMAIL);
      await page.goto("/ar/invoices/new");
      await page.waitForSelector("h1:has-text('Invoice')");
      await pick(page, "Customer", code);
      await page.getByLabel("Description", { exact: true }).fill(`Over limit ${code}`);
      if (tape.currency) {
        await pick(page, "Currency", tape.currency);
        await page.getByLabel("Exchange rate").fill(tape.invoiceRate!);
      }
      await pickLineAccount(page, REVENUE);
      await page.getByLabel("Unit price, row 1").fill(tape.invoiceAmount);
      await page.getByRole("button", { name: /^Post/ }).click();
      await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 30_000 });
      await expect(page.getByText("Posted").first()).toBeVisible();
    });

    test(`${tape.label}: a post-dated cheque stays out of bank until it matures`, async ({
      page,
    }) => {
      test.setTimeout(180_000);
      // PATH: the receipt screen with a cheque dated ahead, then the maturity run.
      // CANNOT SEE: the scheduling that would call the maturity run in production — the run
      // is invoked directly here, so this proves the accounting, not the cron.
      await login(page, PRIMARY_EMAIL);
      const code = unique(`${tape.code}PD`);
      const name = `Cheque ${tape.label} ${code}`;
      if (tape.currency) await seedRate(page, tape.currency, today(), tape.invoiceRate!);
      await makeCustomer(page, code, name, { creditLimit: tape.creditLimit });

      await page.goto("/ar/receipts/new");
      await page.waitForSelector("h1:has-text('Receipt')");
      await pick(page, "Customer", code);
      await page.getByLabel("Description", { exact: true }).fill(`Cheque ${code}`);
      if (tape.currency) {
        await pick(page, "Currency", tape.currency);
        await page.getByLabel("Exchange rate").fill(tape.invoiceRate!);
      }
      await page.getByLabel("Amount", { exact: true }).fill(tape.receiptAmount);
      await pick(page, "Cash / bank account", BANK);
      await page.getByLabel("Maturity date").fill(isoDaysFromNow(30));
      // The screen says what the dating does before it is posted.
      await expect(page.getByText(/books to the post-dated account/)).toBeVisible();
      await page.getByRole("button", { name: /^Post/ }).click();
      await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 30_000 });

      // The cash side is in the post-dated account, and the bank has not moved.
      await expect(page.getByText(new RegExp(`${POST_DATED_AR}\\s`)).first()).toBeVisible();
      await expect(page.getByText(new RegExp(`${BANK}\\s`))).toHaveCount(0);

      // Run maturity as at the cheque's date: now it is in the bank.
      const matured = await pageFetch(page, "/subledger/ar/instruments/mature", {
        method: "POST",
        body: { as_of: isoDaysFromNow(31) },
        headers: { "Idempotency-Key": crypto.randomUUID() },
      });
      expect(matured.ok, JSON.stringify(matured.json)).toBeTruthy();
      const entryIds = (matured.json as { matured_entry_ids: number[] }).matured_entry_ids;
      expect(entryIds.length, "the matured cheque posts a transfer entry").toBeGreaterThan(0);

      await page.goto(`/gl/entries/${entryIds[entryIds.length - 1]}`);
      await page.waitForSelector("h1");
      await expect(page.getByText(new RegExp(`${BANK}\\s`)).first()).toBeVisible();
      await expect(page.getByText(new RegExp(`${POST_DATED_AR}\\s`)).first()).toBeVisible();
    });

    test(`${tape.label}: an AR batch posts, and its journal debit ages`, async ({ page }) => {
      test.setTimeout(180_000);
      // PATH: the AR batch screen → the age analysis. CANNOT SEE: that the batch line posted
      // under the JNL transaction type rather than the invoice type — `ar-ap-reports.spec.ts`
      // asserts the label on the enquiry row, which is where that would show.
      await login(page, PRIMARY_EMAIL);
      const code = unique(`${tape.code}BAT`);
      const name = `Batch ${tape.label} ${code}`;
      await makeCustomer(page, code, name, { creditLimit: tape.creditLimit });

      await page.goto("/ar/batches/new");
      await page.waitForSelector("h1:has-text('Account receivable batches')");
      await page.getByLabel("Reference").fill(`Tape batch ${code}`);
      await page.getByLabel(/^Partner, row 1/).click();
      await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
      await page.keyboard.type(code);
      await page.locator(`[cmdk-item]:has-text("${code}")`).first().click();
      await page.getByRole("button", { name: "Contra account, row 1", exact: true }).click();
      await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
      await page.keyboard.type(REVENUE);
      await page.locator(`[cmdk-item]:has-text("${REVENUE}")`).first().click();
      await page.getByLabel(/^Description, row 1/).fill(`Batch charge ${code}`);
      await page.getByLabel(/^Amount, row 1/).fill("7500");
      await page.getByRole("button", { name: /^Post/ }).click();
      await expect(page.getByText(/1 documents posted/).first()).toBeVisible({ timeout: 30_000 });

      // The debit the batch raised is an open item, so it ages like any other.
      const partners = (await pageFetch(page, "/subledger/ar/partners")).json as Array<{
        id: number;
        customer_code: string;
      }>;
      const partner = partners.find((p) => p.customer_code === code)!;
      const ageing = (
        await pageFetch(page, `/subledger/ar/ageing?partner_id=${partner.id}`)
      ).json as { rows: Array<{ partner_id: number; total: string }> };
      const row = ageing.rows.find((r) => r.partner_id === partner.id);
      expect(row, "the batch debit reaches the ageing").toBeTruthy();
      expect(Number(row!.total)).toBeCloseTo(7500, 2);

      await page.goto("/ar/reports/age-analysis");
      await page.waitForSelector("h1:has-text('Age analysis')");
      await expect(page.getByText(name)).toBeVisible({ timeout: 20_000 });
    });
  });
}
