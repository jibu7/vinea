import { expect, test, type Locator, type Page } from "@playwright/test";
import { formatMoney, type CurrencyLike } from "../src/lib/format";
import {
  POSTER_EMAIL,
  PRIMARY_EMAIL,
  clearDrafts,
  login,
  pickCombobox,
  pickDate,
  switchUser,
} from "./support/fixtures";

/**
 * P4 step 9 — the acceptance tape.
 *
 * PATH: the screens, in the order an operator uses them. Partner master with terms and a
 * credit limit → invoice → part-payment settlement at a second booking rate → allocation →
 * the Allocation report's drill-down into the journal entry → enquiry, age analysis and the
 * statement PDF → the credit-limit block and the override → a post-dated instrument, banked.
 * Run for AR in USD and RWF, for AP in USD, and once more for an AR journal batch.
 *
 * CANNOT SEE: whether the ledger is internally consistent. Every figure here is asserted
 * against another *surface* of the same system, so a posting-engine bug consistent across all
 * of them would pass. `assert_subledger_invariants` and the Hypothesis property tests hold
 * that line in the backend job; this proves the figures reach the screens, that three reports
 * agree, and that the permission gate and the maturity transfer are real rather than
 * decorative.
 *
 * The tapes share one `Tape` record rather than being copied per currency: the currency, the
 * rates and the arithmetic are the only differences, and a copied tape is how one currency's
 * assertions quietly stop being run.
 */

const REVENUE = "4100"; // Sales Revenue
const EXPENSE = "6990"; // Sundry Expenses
const BANK = "1120"; // Bank Account
const FX_LOSS = "6950"; // Foreign Exchange Loss
const FX_GAIN = "4400"; // Realized exchange gain
const POST_DATED_AR = "1250"; // Post-dated receivable
const TERMS = "NET30";
const TERMS_DAYS = 30;

/** Seeded by `seed_rwanda`: RWF is base, 0 decimal places, rendered with its own symbol. */
const BASE: CurrencyLike = { code: "RWF", decimalPlaces: 0, symbol: "FRw" };

type Role = "ar" | "ap";

/** Everything that differs between the two roles' screens, in one place — the phase built AR
 * and AP as one symmetric module, and the tape follows it rather than forking. */
const ROLE = {
  ar: {
    partner: "Customer",
    masterPath: "/maintenance/customers",
    masterHeading: "Customers",
    newPartner: /New customer/i,
    codeLabel: "Customer code",
    settingsTab: "AR settings",
    invoicePath: "/ar/invoices/new",
    invoiceHeading: "Invoice",
    settlementPath: "/ar/receipts/new",
    settlementHeading: "Receipt",
    lineAccount: REVENUE,
    allocationsPath: "/ar/allocations/new",
    allocationReportPath: "/ar/reports/allocations",
    enquiryPath: "/ar/enquiry",
    ageingPath: "/ar/reports/age-analysis",
    statementsPath: "/ar/reports/statements",
    statementsHeading: "Customer statements",
    /** AR books the receivable at the invoice's rate; settling below it realizes a loss. */
    fxAccount: FX_LOSS,
  },
  ap: {
    partner: "Supplier",
    masterPath: "/maintenance/suppliers",
    masterHeading: "Suppliers",
    newPartner: /New supplier/i,
    codeLabel: "Supplier code",
    settingsTab: "AP settings",
    invoicePath: "/ap/supplier-invoices/new",
    invoiceHeading: "Supplier invoice",
    settlementPath: "/ap/payments/new",
    settlementHeading: "Payment",
    lineAccount: EXPENSE,
    allocationsPath: "/ap/allocations/new",
    allocationReportPath: "/ap/reports/allocations",
    enquiryPath: "/ap/enquiry",
    ageingPath: "/ap/reports/age-analysis",
    statementsPath: "/ap/reports/statements",
    statementsHeading: "Supplier statements",
    /** AP is the mirror: owing at the higher rate and paying at the lower one is a gain. */
    fxAccount: FX_GAIN,
  },
} as const satisfies Record<Role, Record<string, unknown>>;

interface Tape {
  label: string;
  role: Role;
  /** `null` leaves the document on base currency — the screen's default, and no rate field. */
  currency: string | null;
  invoiceRate: string | null;
  settlementRate: string | null;
  decimalPlaces: number;
  /** In base currency: the limit the server checks exposure against. */
  creditLimit: number;
  /** In *document* currency. */
  invoiceAmount: number;
  settlementAmount: number;
  breachAmount: number;
  /** In base currency: the realized FX the allocation must post (0 = it must post none), and
   * what stays open on the invoice afterwards. */
  fxBase: number;
  openBase: number;
}

const TAPES: Tape[] = [
  {
    label: "AR · USD",
    role: "ar",
    currency: "USD",
    invoiceRate: "1300",
    settlementRate: "1250",
    decimalPlaces: 2,
    creditLimit: 5_000_000,
    invoiceAmount: 1_000, // booked at 1300 -> 1,300,000 base
    settlementAmount: 400, // settled at 1250 ->   500,000 base
    breachAmount: 4_000, // 5,200,000 base against 4,220,000 of headroom
    // 400 x (1300 - 1250): the receivable was booked dearer than it settled — an AR loss.
    fxBase: 20_000,
    openBase: 780_000, // 600 still open, at the invoice's own booking rate
  },
  {
    label: "AR · RWF",
    role: "ar",
    currency: null,
    invoiceRate: null,
    settlementRate: null,
    decimalPlaces: 0,
    creditLimit: 5_000_000,
    invoiceAmount: 1_300_000,
    settlementAmount: 500_000,
    breachAmount: 4_500_000, // 5,300,000 base against 4,200,000 of headroom
    fxBase: 0, // one currency, one rate of 1 — there is no difference to realize
    openBase: 800_000,
  },
  {
    // The AP half of the symmetry, in USD so there is a difference to realize. The same
    // movement that costs AR money makes AP money: the payable was booked at 1300 and
    // settled at 1250, so 400 x 50 is a gain, and it lands in the gain account.
    label: "AP · USD",
    role: "ap",
    currency: "USD",
    invoiceRate: "1300",
    settlementRate: "1250",
    decimalPlaces: 2,
    creditLimit: 5_000_000,
    invoiceAmount: 1_000,
    settlementAmount: 400,
    breachAmount: 4_000,
    // Negative is the ledger's sign for a gain, and it is what the preview footer and the
    // Allocation report both render. The journal entry shows the magnitude in its Credit
    // column instead, which is why the entry assertion takes an absolute value.
    fxBase: -20_000,
    // Signed by the control account's side, the way the enquiry and the ageing both report
    // it: a payable is a credit, so what is still owed reads negative on both screens.
    openBase: -780_000,
  },
];

function baseMoney(amount: number): string {
  return formatMoney(amount, BASE);
}

/** As the report tables render it: grouped digits, no currency label. */
function bareBase(amount: number): string {
  return formatMoney(amount, BASE, { showCode: false });
}

function isoDaysFromNow(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

function invoiceBase(tape: Tape): number {
  return tape.invoiceAmount * Number(tape.invoiceRate ?? 1);
}

/** Creates the partner through its own master screen, then gives it payment terms and a
 * credit limit on the role's settings tab — the two things the later steps depend on. */
async function makePartner(
  page: Page,
  role: Role,
  { code, name, creditLimit }: { code: string; name: string; creditLimit?: number },
): Promise<void> {
  const r = ROLE[role];
  await page.goto(r.masterPath);
  await page.waitForSelector(`h1:has-text('${r.masterHeading}')`);
  await page.getByRole("button", { name: r.newPartner }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel(r.codeLabel).fill(code);
  await dialog.getByLabel("Name", { exact: true }).fill(name);
  await dialog.getByRole("button", { name: "Create", exact: true }).click();

  // Creating opens the new partner's drawer; terms and the limit live on the settings tab,
  // because they belong to the *role*, not to the partner.
  const drawer = page.getByRole("dialog").filter({ hasText: name });
  await expect(drawer).toBeVisible();
  await drawer.getByRole("tab", { name: r.settingsTab }).click();
  await pickCombobox(page, "Payment terms", TERMS, { within: drawer });
  if (creditLimit !== undefined) {
    await drawer.getByLabel(/^Credit limit/).fill(String(creditLimit));
  }
  await drawer.getByRole("button", { name: "Save settings" }).click();
  // `.first()`: a toast renders its title twice — the visible card, and a `role="status"`
  // region for screen readers — so an unqualified match is a strict-mode violation from the
  // moment both are mounted.
  await expect(page.getByText("Settings saved").first()).toBeVisible();
  await page.keyboard.press("Escape");
}

async function setCurrency(page: Page, tape: Tape, rate: string | null): Promise<void> {
  if (!tape.currency || !rate) return;
  await pickCombobox(page, "Currency", tape.currency);
  await page.getByLabel("Exchange rate").fill(rate);
}

/** Fills and posts an invoice (or supplier invoice). Drafts are dropped *before* navigating,
 * not after: the screen restores one on mount, and the deliberately-rejected post later in
 * this tape leaves one behind that would otherwise reappear over the next invoice typed. */
async function postInvoice(
  page: Page,
  tape: Tape,
  { code, description, amount }: { code: string; description: string; amount: number },
): Promise<void> {
  const r = ROLE[tape.role];
  await clearDrafts(page);
  await page.goto(r.invoicePath);
  await page.waitForSelector(`h1:has-text('${r.invoiceHeading}')`);
  await pickCombobox(page, r.partner, code);
  await page.getByLabel("Description", { exact: true }).fill(description);
  await setCurrency(page, tape, tape.invoiceRate);
  await page.getByRole("button", { name: "Account, row 1" }).click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(r.lineAccount);
  await page.locator(`[cmdk-item]:has-text("${r.lineAccount}")`).first().click();
  await page.getByLabel("Unit price, row 1").fill(String(amount));
  await page.getByRole("button", { name: /^Post/ }).click();
}

/** Fills and posts a receipt (or payment). A maturity date ahead of today makes it
 * post-dated: the cash side books to the post-dated account until it is banked. */
async function postSettlement(
  page: Page,
  tape: Tape,
  { code, description, amount, documentDate, maturityDate }: {
    code: string;
    description: string;
    amount: number;
    documentDate?: string;
    maturityDate?: string;
  },
): Promise<void> {
  const r = ROLE[tape.role];
  await clearDrafts(page);
  await page.goto(r.settlementPath);
  await page.waitForSelector(`h1:has-text('${r.settlementHeading}')`);
  await pickCombobox(page, r.partner, code);
  await page.getByLabel("Description", { exact: true }).fill(description);
  if (documentDate) await pickDate(page, "Document date", documentDate);
  await setCurrency(page, tape, tape.settlementRate);
  await page.getByLabel("Amount", { exact: true }).fill(String(amount));
  await pickCombobox(page, "Cash / bank account", BANK);
  if (maturityDate) await pickDate(page, "Maturity date", maturityDate);
  await page.getByRole("button", { name: /^Post/ }).click();
}

/** Allocates the whole of the open credit against the open debit, previewing first, and
 * returns the allocation's number as the toast reported it. */
async function allocate(page: Page, tape: Tape, code: string, amount: number): Promise<string> {
  const r = ROLE[tape.role];
  await page.goto(r.allocationsPath);
  await page.waitForSelector("h1:has-text('Allocate')");
  await pickCombobox(page, "Partner", code);

  await page.getByRole("button", { name: /^Apply / }).first().click();
  await page.getByLabel(/^Allocate against /).first().fill(String(amount));

  // Post stays refused until the preview has been taken: what posts must be what was shown.
  // The preview is the service's own answer, not a second computation on the client.
  await expect(page.getByRole("button", { name: /^Post/ })).toBeDisabled();
  await page.getByRole("button", { name: "Preview", exact: true }).click();

  if (tape.fxBase === 0) {
    await expect(page.getByTestId("allocation-preview-empty")).toBeVisible();
  } else {
    await expect(page.getByTestId("allocation-preview").getByText(r.fxAccount)).toBeVisible();
    await expect(page.getByText(`Realized FX ${baseMoney(tape.fxBase)}`)).toBeVisible();
  }

  await page.getByRole("button", { name: /^Post/ }).click();
  const toast = page.getByText(/ALC-\d+ posted/).first();
  await expect(toast).toBeVisible({ timeout: 30_000 });
  const text = await toast.innerText();
  return text.replace(/\s*posted\s*$/, "").trim();
}

/** Finds the allocation on the Allocation report and follows its drill-down to the journal
 * entry. This is the whole point of that link: an exchange difference on a report has to lead
 * to the ledger behind it, without anyone reading a network response to find the entry id. */
async function drillFromAllocationReport(
  page: Page,
  role: Role,
  code: string,
  number: string,
): Promise<Locator> {
  await page.goto(ROLE[role].allocationReportPath);
  await page.waitForSelector("h1:has-text('Allocation report')");
  await pickCombobox(page, "Partner", code);
  const row = page.locator("table tbody tr", { hasText: number });
  await expect(row).toHaveCount(1);
  return row;
}

async function queueStatement(page: Page, role: Role, name: string): Promise<void> {
  const r = ROLE[role];
  await page.goto(r.statementsPath);
  await page.waitForSelector(`h1:has-text('${r.statementsHeading}')`);
  await page.getByLabel(name, { exact: true }).check();
  await page.getByRole("button", { name: /Queue statement/ }).click();
  const download = page.getByTestId("statement-download");
  await expect(download).toBeVisible({ timeout: 60_000 });
  const [file] = await Promise.all([
    page.waitForEvent("download", { timeout: 30_000 }),
    download.click(),
  ]);
  expect(file.suggestedFilename()).toMatch(/\.pdf$/);
}

test.describe("P4 acceptance tape", () => {
  for (const tape of TAPES) {
    test(`${tape.label} — invoice, part settlement at a second rate, allocation, statement, credit limit`, async ({
      page,
    }) => {
      // Eleven screens and three sign-ins; the 45s per-test default buys one navigation.
      test.setTimeout(420_000);

      const r = ROLE[tape.role];
      const suffix = `${tape.role.toUpperCase()}${String(Date.now()).slice(-6)}`;
      const code = `E2EACC${suffix}`;
      const name = `Acceptance ${suffix}`;

      await login(page, PRIMARY_EMAIL);

      await test.step("a partner with payment terms and a credit limit", async () => {
        await makePartner(page, tape.role, { code, name, creditLimit: tape.creditLimit });
      });

      await test.step(`posts the ${tape.currency ?? "base-currency"} invoice`, async () => {
        await postInvoice(page, tape, {
          code,
          description: `Acceptance invoice ${suffix}`,
          amount: tape.invoiceAmount,
        });
        await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 30_000 });
        await expect(page.getByText("Posted").first()).toBeVisible();

        // Debit and Credit on this screen are base amounts, so they must read in the base
        // currency whatever the document was billed in. Asserting the *label*, not just the
        // digits: formatting the base figure with the document's currency rendered a USD
        // 1,000 invoice as "$ 1,300,000.00", and only an FX document could ever show it.
        const control = page.locator("table tbody tr", { hasText: r.lineAccount });
        await expect(control).toContainText(baseMoney(invoiceBase(tape)));
        if (tape.currency) await expect(control).not.toContainText("$");
      });

      await test.step("posts a part settlement, at a different rate", async () => {
        await postSettlement(page, tape, {
          code,
          description: `Acceptance settlement ${suffix}`,
          amount: tape.settlementAmount,
        });
        await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 30_000 });
      });

      let allocationNumber = "";

      await test.step("allocates the settlement against the invoice", async () => {
        allocationNumber = await allocate(page, tape, code, tape.settlementAmount);
      });

      await test.step("the Allocation report drills into the entry behind the difference", async () => {
        const row = await drillFromAllocationReport(page, tape.role, code, allocationNumber);
        await expect(row).toContainText(bareBase(tape.fxBase));

        if (tape.fxBase === 0) {
          // Same currency, no discount: an allocation that moves no money must not invent an
          // entry to say so. The report says the allocation posts nothing, and offers no link
          // to a journal entry that does not exist.
          await expect(row).toContainText("posts nothing");
          await expect(row.getByRole("link")).toHaveCount(0);
          return;
        }

        await row.getByRole("link", { name: /^Open journal entry/ }).click();
        await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 30_000 });
        await expect(page.getByText("Posted").first()).toBeVisible();
        const fxRow = page.locator("table tbody tr", { hasText: r.fxAccount });
        await expect(fxRow).toHaveCount(1);
        // Debit and Credit are separate columns, so the entry shows the magnitude and the
        // column carries the sign — the account it landed in is the durable statement about
        // which way it went, and that is what `hasText` above pins.
        await expect(fxRow).toContainText(bareBase(Math.abs(tape.fxBase)));
      });

      await test.step("the enquiry, the age analysis and the statement agree", async () => {
        await page.goto(r.enquiryPath);
        await page.waitForSelector("h1:has-text('enquiry')");
        await pickCombobox(page, r.partner, code);
        await expect(page.getByText(`Open balance ${baseMoney(tape.openBase)}`)).toBeVisible();

        await page.goto(r.ageingPath);
        await page.waitForSelector("h1:has-text('Age analysis')");
        const row = page.locator("table tbody tr", { hasText: code });
        await expect(row).toHaveCount(1);
        // Columns: code, name, then one per bucket, then the row total. The invoice is due on
        // the terms, i.e. ahead of today, so the whole open item belongs in "Current" and the
        // row must foot to what the enquiry just reported.
        await expect(row.locator("td").nth(2)).toHaveText(bareBase(tape.openBase));
        await expect(row.locator("td").last()).toHaveText(bareBase(tape.openBase));
        if (tape.decimalPlaces === 0) {
          // RWF has no minor unit, and the report must not invent one.
          await expect(row.locator("td").last()).not.toContainText(".");
        }

        await queueStatement(page, tape.role, name);
      });

      await test.step("the credit limit blocks a poster, and the override clears it", async () => {
        // The Accountant role posts AR and AP but holds no `*:credit_limit_override`. The
        // owner holds every permission, so only a second user can show the block is real.
        await switchUser(page, POSTER_EMAIL);
        await postInvoice(page, tape, {
          code,
          description: `Over the limit ${suffix}`,
          amount: tape.breachAmount,
        });
        await expect(page.getByText(/would exceed the credit limit/)).toBeVisible({
          timeout: 30_000,
        });
        await expect(page.getByText("credit limit exceeded")).toBeVisible();
        await expect(page).toHaveURL(new RegExp(`${r.invoicePath}$`));

        await switchUser(page, PRIMARY_EMAIL);
        await postInvoice(page, tape, {
          code,
          description: `Over the limit, overridden ${suffix}`,
          amount: tape.breachAmount,
        });
        await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 30_000 });
        await expect(page.getByText("Posted").first()).toBeVisible();
      });

      // Last, deliberately: banking a post-dated instrument allocates against the open
      // balance, and every assertion above is arithmetic on that balance.
      if (tape.role === "ar") {
        await test.step("a post-dated cheque is allocatable, stays out of bank, and banks on maturity", async () => {
          // Dated back, maturing **today**, rather than dated today and maturing next month.
          // The transfer posts on the instrument's own maturity date, and the kernel refuses
          // a period that is still `future` — a cheque maturing in October genuinely cannot
          // be banked until October is opened, which is correct and not what this step is
          // about. Both dates here sit inside the open period whatever day of the month the
          // suite runs on.
          const documentDate = isoDaysFromNow(-5);
          const maturity = isoDaysFromNow(0);

          await postSettlement(page, tape, {
            code,
            description: `Post-dated cheque ${suffix}`,
            amount: tape.settlementAmount,
            documentDate,
            maturityDate: maturity,
          });
          await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 30_000 });

          // The cash side is in the post-dated account, and the bank has not moved.
          await expect(
            page.locator("table tbody tr", { hasText: POST_DATED_AR }),
          ).toHaveCount(1);
          await expect(page.locator("table tbody tr", { hasText: BANK })).toHaveCount(0);

          // It is a real claim while it waits: allocatable before a shilling has landed.
          await allocate(page, tape, code, tape.settlementAmount);

          await page.goto("/ar/post-dated");
          await page.waitForSelector("h1:has-text('Post-dated receipts')");
          const row = page.locator("table tbody tr", { hasText: code });
          await expect(row).toHaveCount(1);

          // As at yesterday it has not matured, and there is nothing to bank.
          await pickDate(page, "Mature up to", isoDaysFromNow(-1));
          await expect(row).toContainText("Waiting");
          await expect(page.getByRole("button", { name: "Mark matured" })).toBeDisabled();

          // As at its maturity date it is due, and banking it posts the transfer out of the
          // post-dated account and into the bank.
          await pickDate(page, "Mature up to", maturity);
          await expect(row).toContainText("Due");
          const mature = page.getByRole("button", { name: "Mark matured" });
          await expect(mature).toBeEnabled();
          await mature.click();

          const banked = page.getByTestId("instruments-banked");
          await expect(banked).toBeVisible({ timeout: 30_000 });
          await banked.getByRole("link").first().click();
          await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 30_000 });
          await expect(page.locator("table tbody tr", { hasText: BANK })).toHaveCount(1);
          await expect(
            page.locator("table tbody tr", { hasText: POST_DATED_AR }),
          ).toHaveCount(1);

          // And it has left the waiting list: the cash has landed.
          await page.goto("/ar/post-dated");
          await page.waitForSelector("h1:has-text('Post-dated receipts')");
          await expect(page.locator("table tbody tr", { hasText: code })).toHaveCount(0);
        });
      }
    });
  }

  test("an AR journal batch raises a debit that ages on the partner's terms", async ({ page }) => {
    test.setTimeout(240_000);
    // PATH: the AR batch screen → the age analysis at a date past the terms. CANNOT SEE: that
    // the batch line posted under the JNL transaction type rather than the invoice type —
    // `ar-ap-reports.spec.ts` asserts that label on the enquiry row, which is where it shows.
    await login(page, PRIMARY_EMAIL);
    const suffix = `BAT${String(Date.now()).slice(-6)}`;
    const code = `E2EBAT${suffix}`;
    const name = `Batch ${suffix}`;
    const amount = 7_500;

    await makePartner(page, "ar", { code, name });

    await page.goto("/ar/batches/new");
    await page.waitForSelector("h1:has-text('Account receivable batches')");
    await page.getByLabel(/^Partner, row 1/).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(code);
    await page.locator(`[cmdk-item]:has-text("${code}")`).first().click();
    await page.getByRole("button", { name: "Contra account, row 1", exact: true }).click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type(REVENUE);
    await page.locator(`[cmdk-item]:has-text("${REVENUE}")`).first().click();
    await page.getByLabel(/^Description, row 1/).fill(`Interest charged ${suffix}`);
    await page.getByLabel(/^Amount, row 1/).fill(String(amount));
    await page.getByRole("button", { name: /^Post/ }).click();
    await expect(page.getByText(/1 documents posted/).first()).toBeVisible({ timeout: 30_000 });

    // The batch screen has no due-date column at all, so the partner's payment terms are the
    // only thing that can set one. Age at 75 days: on NET30 the debit is 45 days past due and
    // belongs in "31 - 60". Read off the *document* date instead and it would be 75 days old
    // and sit in "61 - 90" — so the bucket is what distinguishes the two, and asserting the
    // row total alone would not have.
    await page.goto("/ar/reports/age-analysis");
    await page.waitForSelector("h1:has-text('Age analysis')");
    await pickDate(page, "As-of date", isoDaysFromNow(TERMS_DAYS + 45));
    const row = page.locator("table tbody tr", { hasText: code });
    await expect(row).toHaveCount(1);
    // Columns: code, name, Current, 31-60, 61-90, 91-120, 120+, total.
    await expect(row.locator("td").nth(3)).toHaveText(bareBase(amount));
    await expect(row.locator("td").nth(4)).toHaveText(bareBase(0));
    await expect(row.locator("td").last()).toHaveText(bareBase(amount));
  });
});
