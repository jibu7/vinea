import { expect, test, type Page } from "@playwright/test";
import { formatMoney, type CurrencyLike } from "../src/lib/format";
import {
  POSTER_EMAIL,
  PRIMARY_EMAIL,
  clearDrafts,
  login,
  pickCombobox,
  switchUser,
} from "./support/fixtures";

/**
 * P4 step 9 — the acceptance tape, run twice: once in USD (two decimals, two booking rates,
 * a realized exchange difference) and once in RWF (base currency, no FX, no decimals).
 *
 * PATH: the screens, in the order an operator would use them — customer master with terms and
 * a credit limit, invoice, part-payment receipt, allocation, the journal entry behind it, the
 * enquiry, the age analysis, the statement job, and finally the credit-limit block and the
 * override that clears it.
 *
 * CANNOT SEE: whether the *ledger* is internally consistent. `assert_subledger_invariants`
 * and the Hypothesis property tests own that and run in the backend job; this tape proves the
 * figures those services compute actually reach the screens, agree with each other across
 * three different reports, and that the permission gate is real rather than decorative.
 *
 * The two tapes share a `Tape` record instead of two copies of the same 200 lines: the
 * currency, the rates and the arithmetic are the *only* differences, and a copied tape is how
 * one currency's assertions quietly stop being run.
 */

const REVENUE = "4100"; // Sales Revenue
const BANK = "1120"; // Bank Account
const FX_LOSS = "6950"; // Foreign Exchange Loss — where an AR settlement below the booking rate lands
const TERMS = "NET30";

/** Seeded by `seed_rwanda`: RWF is base, 0 decimal places, rendered with its own symbol. */
const BASE: CurrencyLike = { code: "RWF", decimalPlaces: 0, symbol: "FRw" };

interface Tape {
  /** Test name, and the customer-code prefix, so a failure names the currency. */
  label: string;
  /** `null` means "leave the document on base currency" — the screen's default. */
  currency: string | null;
  /** Booking rate typed on the invoice, and the different one typed on the receipt. */
  invoiceRate: string | null;
  receiptRate: string | null;
  currencyLike: CurrencyLike;
  /** Credit limit, in base currency — that is the sense the server checks in. */
  creditLimit: number;
  /** Amounts in *document* currency. */
  invoiceAmount: number;
  receiptAmount: number;
  breachAmount: number;
  /** In base currency: the realized FX the allocation must post (0 = it must post nothing), and
   * what stays open on the invoice afterwards. */
  fxBase: number;
  openBase: number;
}

const TAPES: Tape[] = [
  {
    label: "USD",
    currency: "USD",
    invoiceRate: "1300",
    receiptRate: "1250",
    currencyLike: { code: "USD", decimalPlaces: 2, symbol: "$" },
    creditLimit: 5_000_000,
    invoiceAmount: 1_000, // booked at 1300 -> 1,300,000 base
    receiptAmount: 400, //   settled at 1250 ->   500,000 base
    breachAmount: 4_000, // 5,200,000 base against 4,220,000 of headroom
    // 400 x (1300 - 1250): the receivable was booked dearer than it settled, so AR takes a loss.
    fxBase: 20_000,
    openBase: 780_000, // 600 still open, at the invoice's own booking rate
  },
  {
    label: "RWF",
    currency: null,
    invoiceRate: null,
    receiptRate: null,
    currencyLike: BASE,
    creditLimit: 5_000_000,
    invoiceAmount: 1_300_000,
    receiptAmount: 500_000,
    breachAmount: 4_500_000, // 5,300,000 base against 4,200,000 of headroom
    fxBase: 0, // one currency, one rate of 1 — there is no difference to realize
    openBase: 800_000,
  },
];

function baseMoney(amount: number): string {
  return formatMoney(amount, BASE);
}

/** What the invoice is worth in base currency — its own booking rate, or 1 when it is base. */
function invoiceBase(tape: Tape): number {
  return tape.invoiceAmount * Number(tape.invoiceRate ?? 1);
}

/** As the report tables render it: grouped digits, no currency label. */
function bareBase(amount: number): string {
  return formatMoney(amount, BASE, { showCode: false });
}

async function fillLine(page: Page, account: string, unitPrice: number): Promise<void> {
  await page.getByRole("button", { name: "Account, row 1" }).click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(account);
  await page.locator(`[cmdk-item]:has-text("${account}")`).first().click();
  await page.getByLabel("Unit price, row 1").fill(String(unitPrice));
}

async function setCurrency(page: Page, tape: Tape, rate: string | null): Promise<void> {
  if (!tape.currency || !rate) return;
  await pickCombobox(page, "Currency", tape.currency);
  await page.getByLabel("Exchange rate").fill(rate);
}

/** Fills and posts an AR invoice. Drafts are dropped *before* navigating, not after: the
 * screen restores one on mount, and the deliberately-rejected post later in this tape leaves
 * one behind that would otherwise reappear over the next invoice typed on this browser. */
async function postInvoice(
  page: Page,
  tape: Tape,
  { code, description, amount, rate }: { code: string; description: string; amount: number; rate: string | null },
): Promise<void> {
  await clearDrafts(page);
  await page.goto("/ar/invoices/new");
  await page.waitForSelector("h1:has-text('Invoice')");
  await pickCombobox(page, "Customer", code);
  await page.getByLabel("Description", { exact: true }).fill(description);
  await setCurrency(page, tape, rate);
  await fillLine(page, REVENUE, amount);
  await page.getByRole("button", { name: /^Post/ }).click();
}

test.describe("P4 acceptance tape", () => {
  for (const tape of TAPES) {
    test(`invoice, part payment, allocation, statement and the credit limit — ${tape.label}`, async ({
      page,
    }) => {
      // Nine screens and three logins; the per-test default of 45s buys a single navigation.
      test.setTimeout(300_000);

      const suffix = `${tape.label}${String(Date.now()).slice(-6)}`;
      const code = `E2EACC${suffix}`;
      const name = `Acceptance ${suffix}`;

      await login(page, PRIMARY_EMAIL);

      await test.step("a customer with payment terms and a credit limit", async () => {
        await page.goto("/maintenance/customers");
        await page.waitForSelector("h1:has-text('Customers')");
        await page.getByRole("button", { name: /New customer/i }).click();
        const dialog = page.getByRole("dialog");
        await dialog.getByLabel("Customer code").fill(code);
        await dialog.getByLabel("Name", { exact: true }).fill(name);
        await dialog.getByRole("button", { name: "Create", exact: true }).click();

        // Creating opens the new partner's drawer; the AR settings tab is where terms and the
        // limit live, because they belong to the *role*, not to the partner.
        const drawer = page.getByRole("dialog").filter({ hasText: name });
        await expect(drawer).toBeVisible();
        await drawer.getByRole("tab", { name: "AR settings" }).click();
        await pickCombobox(page, "Payment terms", TERMS, { within: drawer });
        await drawer.getByLabel(/^Credit limit/).fill(String(tape.creditLimit));
        await drawer.getByRole("button", { name: "Save settings" }).click();
        // `.first()`: a toast renders its title twice — the visible card, and a
        // `role="status"` region for screen readers — so an unqualified match is a
        // strict-mode violation from the moment both are mounted.
        await expect(page.getByText("Settings saved").first()).toBeVisible();
        await page.keyboard.press("Escape");
      });

      await test.step(`posts a ${tape.label} invoice`, async () => {
        await postInvoice(page, tape, {
          code,
          description: `Acceptance invoice ${suffix}`,
          amount: tape.invoiceAmount,
          rate: tape.invoiceRate,
        });
        await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 30_000 });
        await expect(page.getByText("Posted").first()).toBeVisible();

        // Debit and Credit on this screen are base amounts, so they must read in the base
        // currency whatever the document was billed in. Asserting the *label*, not just the
        // digits: formatting the base figure with the document's currency rendered a USD
        // 1,000 invoice as "$ 1,300,000.00", and only an FX document could ever show it.
        const control = page.locator("table tbody tr", { hasText: "1200" });
        await expect(control).toContainText(baseMoney(invoiceBase(tape)));
        if (tape.currency) await expect(control).not.toContainText("$");
      });

      await test.step("posts a part-payment receipt, at a different rate", async () => {
        await clearDrafts(page);
        await page.goto("/ar/receipts/new");
        await page.waitForSelector("h1:has-text('Receipt')");
        await pickCombobox(page, "Customer", code);
        await page.getByLabel("Description", { exact: true }).fill(`Acceptance receipt ${suffix}`);
        await setCurrency(page, tape, tape.receiptRate);
        await page.getByLabel("Amount", { exact: true }).fill(String(tape.receiptAmount));
        await pickCombobox(page, "Cash / bank account", BANK);
        await page.getByRole("button", { name: /^Post/ }).click();
        await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 30_000 });
      });

      let allocationEntryId: number | null = null;

      await test.step("allocates the receipt against the invoice", async () => {
        await page.goto("/ar/allocations/new");
        await page.waitForSelector("h1:has-text('Allocate')");
        await pickCombobox(page, "Partner", code);

        await page.getByRole("button", { name: /^Apply / }).first().click();
        await page.getByLabel(/^Allocate against /).first().fill(String(tape.receiptAmount));

        // Post stays refused until the preview has been taken: what posts must be what was
        // shown. The preview is the service's own answer, not a second computation.
        await expect(page.getByRole("button", { name: /^Post/ })).toBeDisabled();
        await page.getByRole("button", { name: "Preview", exact: true }).click();

        const preview = page.getByTestId("allocation-preview");
        if (tape.fxBase === 0) {
          await expect(page.getByTestId("allocation-preview-empty")).toBeVisible();
        } else {
          await expect(preview.getByText(FX_LOSS)).toBeVisible();
          await expect(page.getByText(`Realized FX ${baseMoney(tape.fxBase)}`)).toBeVisible();
        }

        // Read the allocation off the response the screen itself makes: the number is in the
        // toast, but the journal entry it wrote is not on screen anywhere.
        const [response] = await Promise.all([
          page.waitForResponse(
            (res) =>
              res.request().method() === "POST" &&
              /\/subledger\/ar\/allocations$/.test(new URL(res.url()).pathname) &&
              res.status() === 201,
          ),
          page.getByRole("button", { name: /^Post/ }).click(),
        ]);
        const allocation = (await response.json()) as { number: string; journal_entry_id: number | null };
        await expect(page.getByText(`${allocation.number} posted`).first()).toBeVisible({
          timeout: 30_000,
        });
        allocationEntryId = allocation.journal_entry_id;
      });

      await test.step("the realized exchange difference is in the journal entry", async () => {
        if (tape.fxBase === 0) {
          // Same currency, no discount: an allocation that moves no money must not invent an
          // entry to say so. This is the RWF half of "no FX".
          expect(allocationEntryId).toBeNull();
          return;
        }
        expect(allocationEntryId).not.toBeNull();
        await page.goto(`/gl/entries/${allocationEntryId}`);
        await expect(page.getByText("Posted").first()).toBeVisible();
        const fxRow = page.locator("table tbody tr", { hasText: FX_LOSS });
        await expect(fxRow).toHaveCount(1);
        await expect(fxRow).toContainText(bareBase(tape.fxBase));
      });

      await test.step("the enquiry, the age analysis and the statement agree", async () => {
        await page.goto("/ar/enquiry");
        await page.waitForSelector("h1:has-text('Customer enquiry')");
        await pickCombobox(page, "Customer", code);
        await expect(page.getByText(`Open balance ${baseMoney(tape.openBase)}`)).toBeVisible();

        await page.goto("/ar/reports/age-analysis");
        await page.waitForSelector("h1:has-text('Age analysis')");
        const row = page.locator("table tbody tr", { hasText: code });
        await expect(row).toHaveCount(1);
        // Columns: code, name, then one per bucket, then the row total. The invoice is due on
        // the terms, i.e. ahead of today, so the whole open item belongs in "Current" and the
        // row must foot to what the enquiry just reported.
        await expect(row.locator("td").nth(2)).toHaveText(bareBase(tape.openBase));
        await expect(row.locator("td").last()).toHaveText(bareBase(tape.openBase));
        if (tape.currencyLike.decimalPlaces === 0) {
          // RWF has no minor unit, and the report must not invent one.
          await expect(row.locator("td").last()).not.toContainText(".");
        }

        await page.goto("/ar/reports/statements");
        await page.waitForSelector("h1:has-text('Customer statements')");
        await page.getByLabel(name, { exact: true }).check();
        await page.getByRole("button", { name: /Queue statement/ }).click();
        const download = page.getByTestId("statement-download");
        await expect(download).toBeVisible({ timeout: 60_000 });
        const [file] = await Promise.all([
          page.waitForEvent("download", { timeout: 30_000 }),
          download.click(),
        ]);
        expect(file.suggestedFilename()).toMatch(/\.pdf$/);
      });

      await test.step("the credit limit blocks a poster, and the override clears it", async () => {
        // The Accountant role posts AR but holds no `ar:credit_limit_override`. The owner
        // holds every permission, so only a second user can show the block is real.
        await switchUser(page, POSTER_EMAIL);
        await postInvoice(page, tape, {
          code,
          description: `Over the limit ${suffix}`,
          amount: tape.breachAmount,
          rate: tape.invoiceRate,
        });
        await expect(page.getByText(/would exceed the credit limit/)).toBeVisible({
          timeout: 30_000,
        });
        await expect(page.getByText("credit limit exceeded")).toBeVisible();
        await expect(page).toHaveURL(/\/ar\/invoices\/new$/);

        await switchUser(page, PRIMARY_EMAIL);
        await postInvoice(page, tape, {
          code,
          description: `Over the limit, overridden ${suffix}`,
          amount: tape.breachAmount,
          rate: tape.invoiceRate,
        });
        await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 30_000 });
        await expect(page.getByText("Posted").first()).toBeVisible();
      });
    });
  }
});
