import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Locator, type Page } from "@playwright/test";
import {
  API_BASE,
  pageFetch,
  pickCombobox,
  pickDate,
  setTheme,
  waitForHydration,
} from "./support/fixtures";

/**
 * **The phase's tape, through the screens** (P8 step 9).
 *
 * `backend/tests/banking/test_acceptance_tape.py` drives the nineteen rows of the step-5 tape
 * through the *services* and asserts every figure against a table worked by hand. This spec
 * drives the same chain through the *screens* and asserts the same figures as **rendered
 * strings** — on the workspace's strip, the import's result, the run's page, a downloaded
 * instruction file, a remittance advice through `pdftotext`, the reconciliation report, the
 * Cashbooks report and the Trial balance. Neither reads the other's answer.
 *
 * The walk, in the prompt's order: register the bank accounts → the opening entries → the
 * paper-mode `BRC-1` → the receipts → the payment run previewed and posted, its instruction file
 * read and a remittance advice read → the September CSV previewed and imported, the chained
 * auto-match read off the result → the fee posted from its line by the rule, the deposit posted
 * as a receipt → `BRC-2` off the strip, lock refused on a wrong balance, locked at zero → the USD
 * statement, its fee in USD, `BRC-3` → the revaluation previewed with the bank line and posted →
 * the backdated cheque under *Posted after lock* → the overlap import → `BRC-4` → the reversed
 * run → reopen refused on `BRC-2`, allowed on `BRC-4` → the Cashbooks closing balance against
 * the Trial balance screen. Then precondition (d): the owner's real exports, each on an account
 * of its own under its committed mapping.
 *
 * **A tenant of its own**, signed up here — which is what lets this file assert `BST-000001`,
 * `BRC-000001`, `PYR-000001`, `FXR-000001`: on Rugari Wines E2E those are consumed by whatever
 * ran before, here they are the first of their kind and the number *is* the claim.
 *
 * **The year is pinned, not read off the clock.** The three committed statements are dated
 * 2026, so every ledger date here is 2026 too, and the fiscal year is created if the signup's
 * (the wall clock's) is another one — the step-5 backend tape's `TAPE_YEAR`, for the same
 * reason: on 1 January a tape on `new Date().getFullYear()` would post its receipts into a year
 * the statement is not in, and every auto-match would silently stop matching.
 *
 * Setup that is not a screen in this phase — the sign-up, the dated rates, the partners, the
 * invoices, the allocations, the rule — goes through the API, as the P6 and P7 tapes did. Every
 * row the prompt names is pressed on its screen.
 *
 * CANNOT SEE: rows 17 and 18 of the backend tape (the void and re-import, and the refusal
 * sweep) — they are the backend tape's; the property machine's random sequences.
 */

const SUFFIX = String(Date.now()).slice(-6);
const OWNER_EMAIL = `e2e.bank.tape.${SUFFIX}@vinea.example`;
const OWNER_PASSWORD = "a banking tape passphrase for one company";
const COMPANY = `Bank Tape Co ${SUFFIX}`;

/** Pinned to `backend/tests/banking/samples/*.csv`, which are dated 2026. */
const TAPE_YEAR = 2026;
const d = (month: number, day: number) =>
  `${TAPE_YEAR}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
const AUG_31 = d(8, 31);
const SEP_1 = d(9, 1);
const SEP_3 = d(9, 3);
const SEP_5 = d(9, 5);
const SEP_10 = d(9, 10);
const SEP_15 = d(9, 15);
const SEP_26 = d(9, 26);
const SEP_28 = d(9, 28);
const SEP_30 = d(9, 30);
const OCT_31 = d(10, 31);
const NOV_2 = d(11, 2);
const NOV_3 = d(11, 3);

const SAMPLES = path.resolve(__dirname, "../../backend/tests/banking/samples");
const RWF_SEP = path.join(SAMPLES, "generic-bk-rwf-sep.csv");
const USD_SEP = path.join(SAMPLES, "generic-bk-usd-sep.csv");
const RWF_OVERLAP = path.join(SAMPLES, "generic-bk-rwf-overlap-oct.csv");
const REAL = path.resolve(__dirname, "../../docs/banking/samples");

/**
 * Every figure below is worked by hand from the tape's table (Master Plan / phase prompt, P8
 * step 5), never read back from the code that computes it. Amounts on bank lines are
 * **reconciled amounts** — RWF on `1120`, USD on `1121`.
 *
 *   1120 after row 1      1 000 000                             (CB-1 from 3400)
 *   row 3  + 118 000 + 59 000                     = 1 177 000
 *   row 4  − 236 000 − 98 000 − 50 000 = −384 000 =   793 000   (S2's 2 % of 100 000 taken)
 *   row 5  + 260 000 (USD 200.00 @ 1 300, keyed)  = 1 053 000
 *   row 6  −  70 000 (PMT-4, CHQ 101)             =   983 000
 *   row 8  statement 1 090 500 · outstanding −70 000 (PMT-4) · 2 lines unmatched
 *          difference 1 090 500 − (983 000 + 70 000) = +37 500 = 40 000 deposit − 2 500 fee
 *          line 6 (+40 000) against PMT-4 (−70 000) is out by 110 000
 *   row 9  − 2 500 (CB-3) + 40 000 (RCT-5)        = 1 020 500 → difference 0; keyed at
 *          1 090 000 the difference is −500
 *   row 11 1121: USD 500.00 − 5.00 = 495.00; the fee at 1 320 is 6 600 base
 *   row 12 USD 495.00 carried at 660 000 − 6 600 = 653 400, revalued 495 × 1 350 = 668 250:
 *          gain 14 850 · SIN-4 USD 100.00 carried 132 000, revalued 135 000: loss 3 000
 *   row 13 PMT-5 −20 000 dated 26 Sep posted after BRC-2 locked → live ledger 1 000 500,
 *          live outstanding −90 000
 *          September on 1120: receipts 118 000 + 59 000 + 260 000 + 40 000 = 477 000;
 *          payments 384 000 + 70 000 + 2 500 + 20 000 = 476 500; closing 1 000 500
 *   row 14 BST-000003: 2 new, 1 skipped; BRC-4 at 31 Oct: 1 000 500, outstanding 0
 *   row 15 PMT-6 30 000 → 970 500; reversed → 1 000 500
 */
const LEDGER = {
  opening: "FRw 1,000,000",
  row8: "FRw 983,000",
  row9: "FRw 1,020,500",
  closingSep: "FRw 1,000,500",
};

/**
 * The five screenshots the phase's Definition of Done names, taken **from this tape's company at
 * the moment each state exists** — a locked reconciliation at zero, a report with an outstanding
 * item and a late line, the Reconciled column, a run with its instruction file, a revaluation
 * preview with a bank line. A separate capture script would have to build the same nineteen rows
 * a second time to reach them, and would drift from the tape it was meant to photograph.
 *
 * Off unless `P8_CAPTURE_DIR` is set, so CI never writes a file:
 *
 *   P8_CAPTURE_DIR=../docs/screenshots/p8-step-9 npx playwright test e2e/p8-cycle-tape.spec.ts
 */
const CAPTURE_DIR = process.env.P8_CAPTURE_DIR;

async function capture(page: Page, name: string, { reload = true } = {}): Promise<void> {
  if (!CAPTURE_DIR) return;
  await page.setViewportSize({ width: 1440, height: 900 });
  // A success toast sits over the rows it announces. Every state photographed here but the
  // revaluation preview lives in the URL, so a reload shows it without the notice.
  if (reload) {
    await page.reload();
    await page.waitForLoadState("networkidle");
  }
  for (const theme of ["light", "dark"] as const) {
    await setTheme(page, theme);
    await page.screenshot({ path: path.join(CAPTURE_DIR, `${name}-${theme}.png`), fullPage: true });
  }
  await setTheme(page, "light");
}

interface Identified {
  id: number;
}
interface Numbered extends Identified {
  number: string;
}

/** Filled in as the file goes, read by the tests after — the file runs serially. */
const state = {
  ids: {} as Record<string, number>,
  bank: { rwf: 0, usd: 0, cash: 0 },
  partners: {} as Record<"C1" | "C2" | "S1" | "S2" | "S3", number>,
  docs: {} as Record<string, Numbered>,
  entries: {} as Record<string, string>,
  brc: {} as Record<"1" | "2" | "3" | "4", Numbered>,
  run1: { id: 0, number: "" },
  run2: { id: 0, number: "" },
  termsId: 0,
};

async function apiOk(
  page: Page,
  pathname: string,
  init: { method?: string; body?: unknown; headers?: Record<string, string> } = {},
): Promise<unknown> {
  const res = await pageFetch(page, pathname, init);
  expect(res.ok, `${init.method ?? "GET"} ${pathname} -> ${res.status}: ${JSON.stringify(res.json)}`).toBe(
    true,
  );
  return res.json;
}

async function signIn(page: Page) {
  await page.goto("/login");
  await waitForHydration(page, "form");
  await page.fill('input[type="email"]', OWNER_EMAIL);
  await page.fill('input[type="password"]', OWNER_PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL("/");
  await page.waitForSelector("text=Good morning");
}

async function acct(page: Page, code: string): Promise<number> {
  if (state.ids[code]) return state.ids[code];
  const accounts = (await apiOk(page, "/gl/accounts")) as Array<Identified & { code: string }>;
  for (const account of accounts) state.ids[account.code] = account.id;
  expect(state.ids[code], `no account ${code}`).toBeTruthy();
  return state.ids[code];
}

/** A posted document's number and id, found by its description on the role's listing. */
async function documentByDescription(
  page: Page,
  role: "ar" | "ap",
  description: string,
): Promise<Numbered> {
  const listing = (await apiOk(page, `/subledger/${role}/documents?limit=200`)) as {
    items: Array<Numbered & { description: string }>;
  };
  const found = listing.items.find((row) => row.description === description);
  expect(found, `no ${role} document described "${description}"`).toBeTruthy();
  return { id: found!.id, number: found!.number };
}

/** The bank line on `1120`/`1121` a document or entry put there, by its entry number. */
async function ledgerLines(page: Page, bankAccountId: number) {
  return (await apiOk(page, `/banking/accounts/${bankAccountId}/ledger-lines`)) as Array<{
    journal_line_id: number;
    entry_id: number;
    entry_number: string;
    reference: string | null;
    entry_date: string;
  }>;
}

async function openWorkspace(page: Page, brc: Numbered) {
  await page.goto(`/bank/reconciliations/${brc.id}`);
  await expect(
    page.getByRole("heading", { name: `Reconciliation ${brc.number}`, exact: true }),
  ).toBeVisible();
}

function statementRow(page: Page, text: string): Locator {
  return page.locator("tr[data-statement-line-id]", { hasText: text });
}

function ledgerRow(page: Page, entry: string): Locator {
  return page.locator(`tr[data-entry="${entry}"]`);
}

function figure(page: Page, name: string): Locator {
  return page.getByTestId(`figure-${name}`);
}

/** Import a file on `/bank/statements` through the dialog, previewing it first. Returns the
 * preview panel so the caller can read it before the confirm. */
async function importThroughDialog(
  page: Page,
  bankAccountId: number,
  file: string,
  check: (preview: Locator, dialog: Locator) => Promise<void>,
): Promise<void> {
  await page.goto(`/bank/statements?account=${bankAccountId}`);
  await expect(page.getByRole("heading", { name: "Bank statements", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Import", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Statement file", { exact: true }).setInputFiles(file);
  await dialog.getByRole("button", { name: "Preview", exact: true }).click();
  const preview = dialog.getByTestId("import-preview");
  await expect(preview).toBeVisible();
  await check(preview, dialog);
  await dialog.getByRole("button", { name: "Import statement", exact: true }).click();
  await expect(page.getByTestId("import-result")).toBeVisible();
}

/** New reconciliation on the listing: account preselected by `?account`, the date picked, the
 * balance keyed when given (paper mode) or left as defaulted. */
async function openReconciliation(
  page: Page,
  bankAccountId: number,
  date: string,
  keyed: string | null,
  expectDefault?: string,
): Promise<Numbered> {
  await page.goto(`/bank/reconciliations?account=${bankAccountId}`);
  await expect(page.getByRole("heading", { name: "Bank reconciliation", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "New reconciliation", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await pickDate(page, "Reconciliation date", date, { within: dialog });
  const balance = dialog.getByLabel("Statement balance", { exact: true });
  if (expectDefault !== undefined) await expect(balance).toHaveValue(expectDefault);
  if (keyed !== null) await balance.fill(keyed);
  await dialog.getByRole("button", { name: "Open reconciliation", exact: true }).click();
  await page.waitForURL(/\/bank\/reconciliations\/\d+$/);
  const id = Number(page.url().split("/").pop());
  const detail = (await apiOk(page, `/banking/reconciliations/${id}`)) as { number: string };
  return { id, number: detail.number };
}

/** A settlement keyed on `/ar/receipts/new` or `/ap/payments/new`. Returns the posted entry's
 * number, read off the GL entry page the post lands on. */
async function keySettlement(
  page: Page,
  role: "ar" | "ap",
  opts: {
    partner: string;
    date: string;
    description: string;
    reference?: string;
    amount: string;
    account: string;
    instrument?: "Bank" | "Cheque" | "Mobile money";
    currency?: { code: string; rate: string };
  },
): Promise<Numbered> {
  await page.goto(role === "ar" ? "/ar/receipts/new" : "/ap/payments/new");
  await page.waitForSelector(`h1:has-text('${role === "ar" ? "Receipt" : "Payment"}')`);
  await pickCombobox(page, role === "ar" ? "Customer" : "Supplier", opts.partner);
  await pickDate(page, "Document date", opts.date);
  if (opts.currency) {
    await pickCombobox(page, "Currency", opts.currency.code);
    await page.getByLabel("Exchange rate", { exact: true }).fill(opts.currency.rate);
  }
  if (opts.reference) await page.getByLabel("Reference", { exact: true }).fill(opts.reference);
  await page.getByLabel("Description", { exact: true }).fill(opts.description);
  await page.getByLabel("Amount", { exact: true }).fill(opts.amount);
  await pickCombobox(page, "Cash / bank account", opts.account);
  if (opts.instrument) {
    await page.getByRole("combobox", { name: "Instrument", exact: true }).click();
    await page.getByRole("option", { name: opts.instrument, exact: true }).click();
  }
  await page.getByRole("button", { name: /^Post/ }).click();
  await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 60_000 });
  await expect(page.getByText("Posted").first()).toBeVisible();
  return documentByDescription(page, role, opts.description);
}

/** Allocate a settlement to one invoice, P4's way — setup, not a screen of this phase. */
async function allocate(
  page: Page,
  role: "ar" | "ap",
  partner: number,
  date: string,
  debit: number,
  credit: number,
  amount: string,
) {
  const [debitId, creditId] = role === "ar" ? [debit, credit] : [credit, debit];
  return (await apiOk(page, `/subledger/${role}/allocations`, {
    method: "POST",
    headers: { "Idempotency-Key": `p8-tape-alc-${debit}-${credit}-${SUFFIX}` },
    body: {
      partner_id: partner,
      allocation_date: date,
      pairs: [{ debit_document_id: debitId, credit_document_id: creditId, amount }],
    },
  })) as Numbered;
}

async function keyCashbook(
  page: Page,
  opts: { date: string; account: string; contra: string; amount: string; description: string; reference: string },
): Promise<string> {
  await page.goto("/gl/cashbook-batches/new");
  await page.waitForSelector("text=Cashbook Batch");
  await pickDate(page, "Date", opts.date);
  await page.getByRole("button", { name: "Bank / cash account", exact: true }).click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(opts.account);
  await page.locator(`[cmdk-item]:has-text("${opts.account}")`).first().click();
  await page.getByLabel("Reference", { exact: true }).fill(opts.reference);
  await page.getByLabel("Description", { exact: true }).fill(opts.description);
  await page.locator("table tbody tr").nth(0).locator("button").first().click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(opts.contra);
  await page.locator(`[cmdk-item]:has-text("${opts.contra}")`).first().click();
  await page.getByLabel("Amount, row 1").fill(opts.amount);
  await page.getByRole("button", { name: "Post (Ctrl+Enter)" }).click();
  await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 30_000 });
  const entryId = Number(page.url().split("/").pop());
  const entry = (await apiOk(page, `/gl/journal-entries/${entryId}`)) as { number: string };
  return entry.number;
}

test.describe.configure({ mode: "serial", timeout: 300_000 });

test.describe("the banking cycle, through the screens, tied to the tape", () => {
  test.beforeAll(async ({ request }) => {
    const signup = await request.post(`${API_BASE}/auth/signup`, {
      data: {
        email: OWNER_EMAIL,
        password: OWNER_PASSWORD,
        full_name: "Bank Tape Owner",
        company_name: COMPANY,
      },
    });
    expect(signup.ok(), await signup.text()).toBe(true);
  });

  // PATH: setup through the API — the year pinned, Aug–Nov open, the two dated rates, the
  // partners with S2 on 2/10 net 30 and S3 without bank details, the eight invoices of 1 Sep.
  test("setup: the tape's year, rates, partners and the invoices of 1 September", async ({ page }) => {
    await signIn(page);

    const years = (await apiOk(page, "/gl/fiscal-years")) as Array<
      Identified & { start_date: string; end_date: string }
    >;
    if (!years.some((year) => year.start_date <= SEP_1 && year.end_date >= SEP_1)) {
      await apiOk(page, "/gl/fiscal-years", {
        method: "POST",
        body: {
          name: String(TAPE_YEAR),
          start_date: d(1, 1),
          end_date: d(12, 31),
          open_through: d(12, 1),
        },
      });
    }
    const periods = (await apiOk(page, "/gl/periods")) as Array<
      Identified & { start_date: string; end_date: string; status: string }
    >;
    for (const period of periods) {
      if (period.start_date >= d(8, 1) && period.end_date <= d(11, 30) && period.status !== "open") {
        await apiOk(page, `/gl/periods/${period.id}/open`, { method: "POST" });
      }
    }

    const currencies = (await apiOk(page, "/gl/currencies")) as Array<Identified & { code: string }>;
    const usd = currencies.find((c) => c.code === "USD")!;
    state.ids.USD = usd.id;
    for (const [validFrom, rate] of [
      [SEP_1, "1320"],
      [SEP_30, "1350"],
    ] as const) {
      await apiOk(page, "/gl/exchange-rates", {
        method: "POST",
        body: { currency_id: usd.id, valid_from: validFrom, rate },
      });
    }

    const terms = (await apiOk(page, "/subledger/payment-terms", {
      method: "POST",
      body: { code: "2-10-N30", name: "2/10 net 30", due_days: 30, discount_percent: "2", discount_days: 10 },
    })) as Identified;
    state.termsId = terms.id;

    for (const [key, name] of [
      ["C1", "Customer One"],
      ["C2", "Customer Two"],
    ] as const) {
      const partner = (await apiOk(page, "/subledger/ar/partners", {
        method: "POST",
        body: { name: `${name} ${SUFFIX}`, customer_code: key },
      })) as Identified;
      state.partners[key] = partner.id;
    }
    for (const [key, name, bank, withTerms] of [
      // Named so they sort S1, S2, S3: the run posts one payment per supplier in name order,
      // which is what gives S2's the tape's `PMT-2`.
      ["S1", "Akagera Supplies", true, false],
      ["S2", "Bugesera Traders", true, true],
      ["S3", "Cyangugu Corks", false, false],
    ] as const) {
      const partner = (await apiOk(page, "/subledger/ap/partners", {
        method: "POST",
        body: {
          name: `${name} ${SUFFIX}`,
          supplier_code: key,
          ...(withTerms ? { payment_terms_id: terms.id } : {}),
          ...(bank
            ? {
                bank_name: "Bank of Kigali",
                bank_account_number: `00040-${key}-01`,
                bank_account_holder: `${name} ${SUFFIX}`,
              }
            : {}),
        },
      })) as Identified;
      state.partners[key] = partner.id;
    }

    const invoices: Array<[string, "ar" | "ap", keyof typeof state.partners, string, string, boolean]> = [
      ["INV-1", "ar", "C1", "118000", "4100", false],
      ["INV-2", "ar", "C2", "59000", "4100", false],
      ["INV-3", "ar", "C1", "500", "4100", true],
      ["INV-4", "ar", "C2", "200", "4100", true],
      ["SIN-1", "ap", "S1", "236000", "6990", false],
      ["SIN-2", "ap", "S2", "100000", "6990", false],
      ["SIN-3", "ap", "S3", "50000", "6990", false],
      ["SIN-4", "ap", "S1", "100", "6990", true],
    ];
    for (const [key, role, partner, amount, account, foreign] of invoices) {
      const posted = (await apiOk(page, `/subledger/${role}/documents`, {
        method: "POST",
        headers: { "Idempotency-Key": `p8-tape-${key}-${SUFFIX}` },
        body: {
          kind: "invoice",
          partner_id: state.partners[partner],
          document_date: SEP_1,
          description: `Tape ${key} ${SUFFIX}`,
          ...(key === "SIN-2" ? { payment_terms_id: terms.id } : {}),
          ...(foreign ? { currency_id: usd.id, exchange_rate: "1320" } : {}),
          lines: [{ unit_price: amount, gl_account_id: await acct(page, account) }],
        },
      })) as Numbered;
      state.docs[key] = { id: posted.id, number: posted.number };
    }
    // The tape's own numbers: this company's first of each kind.
    expect(state.docs["INV-1"].number).toBe("INV-000001");
    expect(state.docs["SIN-4"].number).toBe("SIN-000004");
  });

  // PATH: /maintenance/bank-accounts — 1120's details edited, 1121 created in USD through
  // New (its master row by the hook), the generic format saved on both, the USD sample tried.
  test("row 0 — the bank accounts are registered, and 1121 holds only USD", async ({ page }) => {
    await signIn(page);
    await page.goto("/maintenance/bank-accounts");
    await page.waitForSelector("h1:has-text('Bank accounts')");
    await page.locator("tr[data-bank-account]").first().waitFor({ state: "visible" });

    // The seed pack's pair, each with its row: bank and cash.
    await expect(page.locator("tr[data-bank-account]")).toHaveCount(2);

    // 1120: the bank's name and number, on the drawer.
    await page
      .locator('tr[data-bank-account="1120"]')
      .getByRole("button", { name: "Edit Bank Account", exact: true })
      .click();
    let drawer = page.getByRole("dialog");
    await drawer.getByLabel("Bank name", { exact: true }).fill("Bank of Kigali");
    await drawer.getByLabel("Bank account number", { exact: true }).fill("00040-0000123-45");
    await drawer.getByRole("button", { name: "Save details", exact: true }).click();
    await expect(page.getByText("Bank account saved").first()).toBeVisible();
    await drawer.getByRole("tab", { name: "Statement format", exact: true }).click();
    await drawer.getByRole("combobox", { name: "Preset", exact: true }).click();
    await page.getByRole("option", { name: /^Generic/ }).click();
    await drawer.getByRole("button", { name: "Save format", exact: true }).click();
    await expect(page.getByText("Statement format saved").first()).toBeVisible();
    await drawer.getByRole("button", { name: "Close", exact: true }).click();
    await expect(drawer).toHaveCount(0);

    // 1121: the GL account and its master in one call, in USD.
    await page.getByRole("button", { name: "New bank account", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("GL account code", { exact: true }).fill("1121");
    await dialog.getByLabel("GL account name", { exact: true }).fill("Bank Account USD");
    await pickCombobox(page, "Held in currency", "USD", { within: dialog });
    await dialog.getByLabel("Bank name", { exact: true }).fill("Bank of Kigali");
    await dialog.getByRole("button", { name: "Create", exact: true }).click();
    await expect(page.getByText("Bank account created").first()).toBeVisible();
    drawer = page.getByRole("dialog");
    await drawer.getByRole("tab", { name: "Statement format", exact: true }).click();
    await drawer.getByRole("combobox", { name: "Preset", exact: true }).click();
    await page.getByRole("option", { name: /^Generic/ }).click();
    await drawer.getByLabel("Statement file", { exact: true }).setInputFiles(USD_SEP);
    await drawer.getByRole("button", { name: "Test with a file", exact: true }).click();
    const tried = drawer.getByTestId("format-test-result");
    // The money: the closing the preview derived, in the account's currency.
    await expect(tried.getByText("$ 495.00").first()).toBeVisible();
    await expect(tried).toContainText("2 lines · 2 new · 0 already held");
    await drawer.getByRole("button", { name: "Save format", exact: true }).click();
    await expect(page.getByText("Statement format saved").first()).toBeVisible();
    await drawer.getByRole("button", { name: "Close", exact: true }).click();
    await expect(drawer).toHaveCount(0);

    await page.reload();
    await page.waitForSelector("h1:has-text('Bank accounts')");
    // The quantity: three rows, and 1121 in USD.
    await expect(page.locator("tr[data-bank-account]")).toHaveCount(3);
    await expect(
      page.locator('tr[data-bank-account="1121"]').getByRole("cell", { name: "USD", exact: true }),
    ).toBeVisible();
    await expect(page.locator('tr[data-bank-account="1120"]')).toContainText("00040-0000123-45");
    await expect(page.locator('tr[data-bank-account="1110"]')).toContainText("Not reconciled (cash)");

    const banks = (await apiOk(page, "/banking/accounts")) as Array<
      Identified & { code: string; kind: string; gl_account_id: number }
    >;
    state.bank.rwf = banks.find((b) => b.code === "1120")!.id;
    state.bank.usd = banks.find((b) => b.code === "1121")!.id;
    state.bank.cash = banks.find((b) => b.code === "1110")!.id;
    expect(banks.find((b) => b.code === "1110")!.kind).toBe("cash");

    // The one-sided currency rule: a RWF line on the USD account is refused by name.
    const refused = await pageFetch(page, "/gl/cashbook-entries", {
      method: "POST",
      headers: { "Idempotency-Key": `p8-tape-mismatch-${SUFFIX}` },
      body: {
        entry_date: SEP_1,
        description: "RWF into the USD account",
        cash_account_id: await acct(page, "1121"),
        kind: "receipt",
        lines: [{ gl_account_id: await acct(page, "3400"), amount: "1000" }],
      },
    });
    expect(refused.status).toBe(422);
    expect(JSON.stringify(refused.json)).toContain("bank_account_currency_mismatch");

    // The rule decision 4 prefills the fee from.
    await apiOk(page, `/banking/accounts/${state.bank.rwf}/rules`, {
      method: "POST",
      body: {
        pattern: "ACCOUNT FEE",
        gl_account_id: await acct(page, "6700"),
        tax_code_id: null,
        description: "MONTHLY ACCOUNT FEE",
        priority: 10,
      },
    });
  });

  // PATH: /gl/cashbook-batches/new twice → /gl/reports/cashbooks (summary) at 31 August.
  test("row 1 — the opening entries, read off the Cashbooks summary", async ({ page }) => {
    await signIn(page);
    state.entries.cb1 = await keyCashbook(page, {
      date: AUG_31,
      account: "1120",
      contra: "3400",
      amount: "1000000",
      description: "Opening balance, bank",
      reference: "OPEN BK",
    });
    state.entries.cb2 = await keyCashbook(page, {
      date: AUG_31,
      account: "1110",
      contra: "3400",
      amount: "50000",
      description: "Opening balance, cash",
      reference: "OPEN CASH",
    });
    expect(state.entries.cb1).toBe("CB-000001");

    await page.goto(`/gl/reports/cashbooks?mode=summary&from=${d(8, 1)}&to=${AUG_31}`);
    await expect(page.getByRole("heading", { name: "Cashbooks", exact: true })).toBeVisible();
    await expect(page.getByTestId("summary-closing-1120")).toHaveText(LEDGER.opening);
    await expect(page.getByTestId("summary-closing-1110")).toHaveText("FRw 50,000");
    await expect(page.getByTestId("summary-closing-1121")).toHaveText("$ 0.00");
    await expect(page.locator("tr[data-cashbook-account]")).toHaveCount(3);
  });

  // PATH: /bank/reconciliations → New (keyed, no statement) → the workspace: Lock refused at the
  // full balance, Tick CB-1, Lock → BRC-000001.
  test("row 2 — paper-mode BRC-1: refused before the tick, locked after it", async ({ page }) => {
    await signIn(page);
    state.brc["1"] = await openReconciliation(page, state.bank.rwf, AUG_31, "1000000");
    expect(state.brc["1"].number).toBe("BRC-000001");
    await openWorkspace(page, state.brc["1"]);

    await expect(figure(page, "ledger")).toHaveText(LEDGER.opening);
    await expect(figure(page, "outstanding")).toHaveText(LEDGER.opening);
    await expect(figure(page, "unmatched")).toHaveText("0");
    await expect(figure(page, "difference")).toHaveText(LEDGER.opening);
    await expect(page.getByTestId("lock-blocked")).toHaveText(
      "The difference is FRw 1,000,000. A reconciliation locks at zero.",
    );
    await expect(page.getByRole("button", { name: "Lock", exact: true })).toBeDisabled();

    await ledgerRow(page, state.entries.cb1).getByRole("button", { name: "Tick", exact: true }).click();
    await expect(ledgerRow(page, state.entries.cb1)).toContainText("Matched · ticked");
    await expect(figure(page, "outstanding")).toHaveText("FRw 0");
    await expect(figure(page, "difference")).toHaveText("FRw 0");
    await page.getByRole("button", { name: "Lock", exact: true }).click();
    await expect(page.getByText("BRC-000001 locked", { exact: true }).first()).toBeVisible();

    await page.goto(`/bank/reconciliations?account=${state.bank.rwf}`);
    await expect(page.getByTestId("reconciliation-count")).toHaveText("Reconciliations: 1 · locked: 1");
    await expect(page.getByTestId("last-reconciled")).toContainText("FRw 1,000,000");
  });

  // PATH: /ar/receipts/new ×2 — row 3 on its screen; the allocations are P4's and go through
  // the API.
  test("row 3 — the two receipts, keyed on their screen", async ({ page }) => {
    await signIn(page);
    state.docs["RCT-1"] = await keySettlement(page, "ar", {
      partner: "C1",
      date: SEP_3,
      description: `Tape RCT-1 ${SUFFIX}`,
      reference: "INV-1 C1",
      amount: "118000",
      account: "1120",
    });
    await expect(page.locator("tbody tr", { hasText: "1120" })).toContainText("118,000");
    state.docs["RCT-2"] = await keySettlement(page, "ar", {
      partner: "C2",
      date: SEP_5,
      description: `Tape RCT-2 ${SUFFIX}`,
      amount: "59000",
      account: "1120",
      instrument: "Mobile money",
    });
    expect(state.docs["RCT-2"].number).toBe("RCT-000002");
    await allocate(page, "ar", state.partners.C1, SEP_3, state.docs["INV-1"].id, state.docs["RCT-1"].id, "118000");
    await allocate(page, "ar", state.partners.C2, SEP_5, state.docs["INV-2"].id, state.docs["RCT-2"].id, "59000");

    // Both receipts are outstanding: nothing from the bank yet.
    await page.goto(`/gl/enquiries/bank-account?account=${state.bank.rwf}&as_of=${SEP_5}`);
    await expect(page.getByTestId("enquiry-book-balance")).toHaveText("FRw 1,177,000");
    await expect(page.getByTestId("enquiry-outstanding-count")).toHaveText("2");
  });

  // PATH: /ap/payment-runs/new → Preview → Post → /ap/payment-runs/{id}: the lines, the
  // instruction file downloaded and read, a remittance advice through pdftotext.
  test("row 4 — the payment run previewed and posted, its file and an advice read", async ({ page }) => {
    await signIn(page);
    await page.goto(`/ap/payment-runs/new?account=${state.bank.rwf}`);
    await expect(page.getByRole("heading", { name: "New payment run", exact: true })).toBeVisible();
    await pickDate(page, "Payment date", SEP_10);

    const pay = ["SIN-1", "SIN-2", "SIN-3"].map((key) => state.docs[key].number);
    for (const number of pay) await expect(page.locator(`tr[data-invoice="${number}"]`)).toBeVisible();
    // P4's discount at 10 September: 2 % of 100 000, inside S2's ten days.
    await expect(page.getByTestId(`discount-available-${state.docs["SIN-2"].number}`)).toHaveText("FRw 2,000");
    for (const number of pay) {
      await page.getByRole("checkbox", { name: `Pay ${number}`, exact: true }).check();
    }
    await expect(page.getByTestId("selection-count")).toContainText("Selected: 3 of");

    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(page.getByTestId("payment-run-preview")).toBeVisible();
    await expect(page.getByTestId("preview-counts")).toHaveText("Suppliers: 3 · invoices: 3");
    await expect(page.getByTestId("preview-discount-total")).toHaveText("FRw 2,000");
    await expect(page.getByTestId("preview-total")).toHaveText("FRw 384,000");
    await expect(
      page
        .locator(`[data-preview-supplier="Cyangugu Corks ${SUFFIX}"]`)
        .getByText("Bank details missing", { exact: true }),
    ).toBeVisible();

    await page.getByRole("button", { name: "Post payment run", exact: true }).click();
    await page.waitForURL(/\/ap\/payment-runs\/\d+$/);
    state.run1.id = Number(page.url().split("/").pop());
    await expect(page.getByRole("heading", { name: "Payment run PYR-000001", exact: true })).toBeVisible();
    state.run1.number = "PYR-000001";
    await expect(page.getByTestId("run-total")).toHaveText("FRw 384,000");
    await expect(page.getByTestId("run-supplier-count")).toHaveText("3");
    await expect(page.locator(`tr[data-run-line="${state.docs["SIN-2"].number}"]`)).toContainText("FRw 98,000");

    const run = (await apiOk(page, `/banking/payment-runs/${state.run1.id}`)) as {
      lines: Array<{ document_id: number; settlement_document_id: number; settlement_number: string }>;
    };
    for (const [key, pmt] of [
      ["SIN-1", "PMT-1"],
      ["SIN-2", "PMT-2"],
      ["SIN-3", "PMT-3"],
    ] as const) {
      const line = run.lines.find((row) => row.document_id === state.docs[key].id)!;
      state.docs[pmt] = { id: line.settlement_document_id, number: line.settlement_number };
    }
    expect(state.docs["PMT-2"].number).toBe("PMT-000002");

    const [instruction] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: "Instruction file", exact: true }).click(),
    ]);
    expect(instruction.suggestedFilename()).toBe("PYR-000001-instruction.csv");
    const csv = readFileSync(await instruction.path(), "utf8").trim().split("\r\n");
    expect(csv[0]).toBe("beneficiary,bank,account number,amount,currency,reference,supplier code");
    expect(csv).toHaveLength(4);
    expect(csv).toContain(`Cyangugu Corks ${SUFFIX},,,50000,RWF,PYR-000001,S3`);
    expect(csv).toContain(`Bugesera Traders ${SUFFIX},Bank of Kigali,00040-S2-01,98000,RWF,PYR-000001,S2`);

    await expect(page.locator(`tr[data-remittance="Bugesera Traders ${SUFFIX}"]`)).toContainText("Ready", {
      timeout: 60_000,
    });
    await expect(page.locator("tr[data-remittance]")).toHaveCount(3);
    await capture(page, "9-4-payment-run-instruction-file");
    const [pdf] = await Promise.all([
      page.waitForEvent("download"),
      page
        .getByRole("button", { name: `Download the advice for Bugesera Traders ${SUFFIX}`, exact: true })
        .click(),
    ]);
    const advice = execFileSync("pdftotext", ["-layout", await pdf.path(), "-"], { encoding: "utf8" });
    expect(advice).toContain(state.docs["SIN-2"].number);
    expect(advice).toContain("98,000");
    expect(advice).toContain("2,000");
  });

  // PATH: /ar/receipts/new ×2 (USD) and /ap/payments/new — rows 5 and 6 on their screens.
  test("rows 5 and 6 — the USD receipts and the cheque, keyed on their screens", async ({ page }) => {
    await signIn(page);
    state.docs["RCT-3"] = await keySettlement(page, "ar", {
      partner: "C1",
      date: SEP_15,
      description: `Tape RCT-3 ${SUFFIX}`,
      reference: "INV-3",
      amount: "500",
      account: "1121",
      currency: { code: "USD", rate: "1320" },
    });
    // The bank's own rate, keyed: USD 200.00 at 1 300 is 260 000 on the RWF account. RCT-2 and
    // RCT-4 carry no reference, as in the backend tape: the statement finds them by amount and
    // date, and a reference would let the reference rule find them first.
    state.docs["RCT-4"] = await keySettlement(page, "ar", {
      partner: "C2",
      date: SEP_15,
      description: `Tape RCT-4 ${SUFFIX}`,
      amount: "200",
      account: "1120",
      currency: { code: "USD", rate: "1300" },
    });
    // The line on 1120 carries the base amount, which is what the statement will show.
    await expect(page.locator("tbody tr", { hasText: "1120" })).toContainText("260,000");
    await allocate(page, "ar", state.partners.C1, SEP_15, state.docs["INV-3"].id, state.docs["RCT-3"].id, "500");
    await allocate(page, "ar", state.partners.C2, SEP_15, state.docs["INV-4"].id, state.docs["RCT-4"].id, "200");

    state.docs["PMT-4"] = await keySettlement(page, "ap", {
      partner: "S3",
      date: SEP_28,
      description: `Tape PMT-4 ${SUFFIX}`,
      reference: "CHQ 101",
      amount: "70000",
      account: "1120",
      instrument: "Cheque",
    });
    expect(state.docs["PMT-4"].number).toBe("PMT-000004");

    // The AP document screen: S3's payment on account, open in full.
    await page.goto(`/ap/documents/${state.docs["PMT-4"].id}`);
    await expect(page.getByTestId("document-total")).toHaveText("FRw 70,000");
    await expect(page.getByTestId("document-open")).toHaveText("FRw 70,000");
    // 1 177 000 − 384 000 + 260 000 − 70 000.
    await page.goto(`/gl/enquiries/bank-account?account=${state.bank.rwf}&as_of=${SEP_30}`);
    await expect(page.getByTestId("enquiry-book-balance")).toHaveText("FRw 983,000");
  });

  // PATH: /bank/statements → Import generic-bk-rwf-sep.csv → the result, the chained
  // auto-match → the statement's detail → the same file again refused.
  test("row 7 — the September statement previewed, imported and auto-matched", async ({ page }) => {
    await signIn(page);
    await importThroughDialog(page, state.bank.rwf, RWF_SEP, async (preview, dialog) => {
      await expect(preview).toContainText("Read cleanly");
      await expect(preview).toContainText("6 lines · 6 new · 0 already held");
      await expect(dialog.getByTestId("import-preview-opening")).toHaveText("FRw 1,000,000");
      await expect(dialog.getByTestId("import-preview-closing")).toHaveText("FRw 1,090,500");
    });
    // Four matched by the chained auto-match: RCT-1 by reference, RCT-2 and RCT-4 by amount
    // and date, the three run payments by the run's number. The fee and the deposit are not.
    await expect(page.getByTestId("import-result")).toContainText("BST-000001");
    await expect(page.getByTestId("import-result")).toContainText("6 new, 0 skipped, 4 matched");

    const statements = (await apiOk(page, `/banking/statements?bank_account_id=${state.bank.rwf}`)) as Numbered[];
    const statement = statements.find((row) => row.number === "BST-000001")!;
    await page.goto(`/bank/statements/${statement.id}`);
    await expect(page.getByTestId("statement-closing")).toHaveText("FRw 1,090,500");
    await expect(page.getByTestId("statement-line-count")).toHaveText("6");
    await expect(page.getByTestId("statement-matched")).toHaveText("4 of 6");
    const line = (text: string) => page.locator("tr[data-statement-line]", { hasText: text });
    await expect(line("INV-1 C1")).toContainText("Matched · reference");
    await expect(line("MOMO DEPOSIT 0788")).toContainText("Matched · amount and date");
    await expect(line("BULK PAYMENT PYR-000001")).toContainText("Matched · payment run");
    await expect(line("INWARD TRF USD 200")).toContainText("Matched · amount and date");
    await expect(line("MONTHLY ACCOUNT FEE")).toContainText("Unmatched");
    await expect(line("CASH DEPOSIT DEP 4471")).toContainText("Unmatched");

    // The same file again: the preview says so and the button is withheld.
    await page.goto(`/bank/statements?account=${state.bank.rwf}`);
    await page.getByRole("button", { name: "Import", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Statement file", { exact: true }).setInputFiles(RWF_SEP);
    await dialog.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(dialog.getByTestId("import-preview")).toContainText("This file is already imported");
    await expect(dialog.getByRole("button", { name: "Import statement", exact: true })).toBeDisabled();
    await page.keyboard.press("Escape");
  });

  // PATH: New BRC-2 at 30 Sep (defaulted from the balance column) → the strip → Lock refused
  // for the two lines → the unbalanced manual match refused → Post from line ×2 → the wrong
  // balance refused with the difference → locked at zero.
  test("rows 8 to 10 — BRC-2 off the strip, the fee and the deposit posted, locked at zero", async ({ page }) => {
    await signIn(page);
    state.brc["2"] = await openReconciliation(page, state.bank.rwf, SEP_30, null, "1090500");
    expect(state.brc["2"].number).toBe("BRC-000002");
    await openWorkspace(page, state.brc["2"]);

    // Open, so the statement balance is the field it can still be re-keyed in.
    await expect(page.getByLabel("Statement balance", { exact: true })).toHaveValue("1090500");
    await expect(figure(page, "ledger")).toHaveText(LEDGER.row8);
    await expect(figure(page, "outstanding")).toHaveText("FRw -70,000");
    await expect(figure(page, "unmatched")).toHaveText("2");
    await expect(figure(page, "difference")).toHaveText("FRw 37,500");
    await expect(page.getByTestId("lock-blocked")).toContainText("Statement lines in no match: 2.");

    // Line 6 against PMT-4: +40 000 against −70 000 is out by 110 000.
    const pmt4 = (await ledgerLines(page, state.bank.rwf)).find((row) => row.reference === "CHQ 101")!;
    state.entries.pmt4 = pmt4.entry_number;
    await page
      .getByRole("checkbox", { name: "Select statement line CASH DEPOSIT DEP 4471", exact: true })
      .check();
    await page.getByRole("checkbox", { name: `Select ledger line ${pmt4.entry_number}`, exact: true }).check();
    await expect(page.getByTestId("selection-balance")).toHaveText("Selection is out by FRw 110,000");
    await page.getByRole("button", { name: "Match", exact: true }).click();
    await expect(page.getByTestId("match-error")).toContainText("out by FRw 110,000");
    await page
      .getByRole("checkbox", { name: "Select statement line CASH DEPOSIT DEP 4471", exact: true })
      .uncheck();
    await page.getByRole("checkbox", { name: `Select ledger line ${pmt4.entry_number}`, exact: true }).uncheck();

    // The fee, from its line, prefilled by the rule.
    await statementRow(page, "MONTHLY ACCOUNT FEE").getByRole("button", { name: "Post from line", exact: true }).click();
    const drawer = page.getByRole("dialog", { name: "Post from a statement line", exact: true });
    await expect(drawer.getByTestId("post-from-line-amount")).toHaveText("FRw -2,500");
    await expect(drawer.getByTestId("prefill-note")).toHaveText(
      "Prefilled by a bank rule: 6700 · Bank Charges. Nothing posts until you press Post.",
    );
    await drawer.getByRole("button", { name: "Post cashbook entry", exact: true }).click();
    await expect(page.getByText("CB-000003 posted and matched", { exact: true }).first()).toBeVisible();
    await expect(statementRow(page, "MONTHLY ACCOUNT FEE")).toContainText("Matched · posted from the statement");

    // The deposit, as a customer receipt from C2, unallocated.
    await statementRow(page, "CASH DEPOSIT DEP 4471").getByRole("button", { name: "Post from line", exact: true }).click();
    const receipt = page.getByRole("dialog", { name: "Post from a statement line", exact: true });
    await receipt.getByRole("tab", { name: "Customer receipt", exact: true }).click();
    await pickCombobox(page, "Customer", "C2", { within: receipt });
    await receipt.getByRole("button", { name: "Post receipt", exact: true }).click();
    await expect(page.getByText("RCT-000005 posted and matched", { exact: true }).first()).toBeVisible();

    await expect(figure(page, "ledger")).toHaveText(LEDGER.row9);
    await expect(figure(page, "outstanding")).toHaveText("FRw -70,000");
    await expect(figure(page, "unmatched")).toHaveText("0");
    await expect(figure(page, "difference")).toHaveText("FRw 0");

    // A wrong balance: the lock is withheld with the difference named.
    const keyed = page.getByLabel("Statement balance", { exact: true });
    await keyed.fill("1090000");
    await expect(figure(page, "difference")).toHaveText("FRw -500");
    await expect(page.getByTestId("lock-blocked")).toHaveText(
      "The difference is FRw -500. A reconciliation locks at zero.",
    );
    await expect(page.getByRole("button", { name: "Lock", exact: true })).toBeDisabled();
    await keyed.fill("1090500");
    await expect(page.getByTestId("lock-ready")).toBeVisible();
    await page.getByRole("button", { name: "Lock", exact: true }).click();
    await expect(page.getByText("BRC-000002 locked", { exact: true }).first()).toBeVisible();
    await expect(figure(page, "difference")).toHaveText("FRw 0");
    await capture(page, "9-1-workspace-locked-at-zero");

    // Row 10: unmatching inside a locked reconciliation is refused before the button.
    const unmatch = statementRow(page, "INV-1 C1").getByRole("button", { name: "Unmatch", exact: true });
    await expect(unmatch).toBeDisabled();
    await expect(unmatch).toHaveAttribute("title", "Locked in BRC-000002. Reopen it to unmatch.");

    // The listing reads the stored figures: locked, and the cache is BRC-2's balance.
    await page.goto(`/bank/reconciliations?account=${state.bank.rwf}`);
    await expect(page.locator('tr[data-reconciliation="BRC-000002"]')).toContainText("Locked");
    await expect(page.getByTestId("last-reconciled")).toContainText("FRw 1,090,500");
  });

  // PATH: Import generic-bk-usd-sep.csv on 1121 → New BRC-3 → the fee posted from its line in
  // USD → Lock at zero.
  test("row 11 — the USD statement, its fee in USD, BRC-3", async ({ page }) => {
    await signIn(page);
    await importThroughDialog(page, state.bank.usd, USD_SEP, async (preview, dialog) => {
      await expect(preview).toContainText("2 lines · 2 new · 0 already held");
      await expect(dialog.getByTestId("import-preview-closing")).toHaveText("$ 495.00");
    });
    await expect(page.getByTestId("import-result")).toContainText("BST-000002");
    await expect(page.getByTestId("import-result")).toContainText("2 new, 0 skipped, 1 matched");

    state.brc["3"] = await openReconciliation(page, state.bank.usd, SEP_30, null, "495");
    expect(state.brc["3"].number).toBe("BRC-000003");
    await openWorkspace(page, state.brc["3"]);
    await expect(statementRow(page, "INWARD TRF C1 INV-3")).toContainText("Matched · reference");
    await expect(figure(page, "unmatched")).toHaveText("1");

    await statementRow(page, "TRF FEE").getByRole("button", { name: "Post from line", exact: true }).click();
    const drawer = page.getByRole("dialog", { name: "Post from a statement line", exact: true });
    await expect(drawer.getByTestId("post-from-line-amount")).toHaveText("$ -5.00");
    const row = drawer.locator("table tbody tr").first();
    await row.getByRole("button").first().click();
    await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
    await page.keyboard.type("6700");
    await page.locator('[cmdk-item]:has-text("6700")').first().click();
    await drawer.getByRole("button", { name: "Post cashbook entry", exact: true }).click();
    await expect(page.getByText("CB-000004 posted and matched", { exact: true }).first()).toBeVisible();

    await expect(page.getByLabel("Statement balance", { exact: true })).toHaveValue("495");
    await expect(figure(page, "ledger")).toHaveText("$ 495.00");
    await expect(figure(page, "unmatched")).toHaveText("0");
    await expect(figure(page, "difference")).toHaveText("$ 0.00");
    await page.getByRole("button", { name: "Lock", exact: true }).click();
    await expect(page.getByText("BRC-000003 locked", { exact: true }).first()).toBeVisible();

    // CB-4's base amount: USD 5.00 at the 15 September rate of 1 320.
    const cb4 = (await ledgerLines(page, state.bank.usd)).find((l) => l.entry_number === "CB-000004")!;
    await page.goto(`/gl/entries/${cb4.entry_id}`);
    await expect(page.locator("tbody tr", { hasText: "6700" })).toContainText("6,600");
  });

  // PATH: /gl/fx-revaluations at 30 Sep, side "Customers, suppliers and bank" → the bank line
  // and the AP line in the preview → Post → FXR-000001; a second bank run refused.
  test("row 12 — the revaluation previewed with the bank line and posted", async ({ page }) => {
    await signIn(page);
    await page.goto("/gl/fx-revaluations");
    await expect(page.getByRole("heading", { name: "FX revaluation", exact: true })).toBeVisible();
    await pickDate(page, "Revaluation date", SEP_30);
    await pickCombobox(page, "Side", "Customers, suppliers and bank");

    const bankLine = page.locator('tr[data-revaluation-line="1121"]');
    await expect(bankLine).toContainText("Bank Account USD");
    await expect(page.getByTestId("open-1121")).toHaveText("$ 495.00");
    await expect(page.getByTestId("difference-1121")).toHaveText("14,850");
    await expect(page.getByTestId(`difference-${state.docs["SIN-4"].number}`)).toHaveText("-3,000");
    // The quantity: the bank line and SIN-4's; AR has nothing open in USD.
    await expect(page.getByTestId("revaluation-line-count")).toHaveText("Lines: 2");
    await capture(page, "9-5-revaluation-preview-bank-line", { reload: false });

    await page.getByTestId("post-revaluation").click();
    await page.getByTestId("confirm-post").click();
    await expect(page.getByText("FXR-000001 posted", { exact: true }).first()).toBeVisible();

    // The bank line never touches the bank account: 1121 still carries 653 400.
    const refused = await pageFetch(page, "/gl/fx-revaluations", {
      method: "POST",
      headers: { "Idempotency-Key": `p8-tape-fxr-bank-${SUFFIX}` },
      body: { revaluation_date: SEP_30, role: "bank" },
    });
    expect(refused.status).toBeGreaterThanOrEqual(400);
    expect(JSON.stringify(refused.json)).toContain("fx_revaluation_exists");
  });

  // PATH: /ap/payments/new (dated 26 Sep, posted now) → /gl/reports/bank-reconciliation on
  // BRC-2: the stored figures unmoved, the cheque under Posted after lock.
  test("row 13 — the backdated cheque, and Posted after lock on the report", async ({ page }) => {
    await signIn(page);
    state.docs["PMT-5"] = await keySettlement(page, "ap", {
      partner: "S1",
      date: SEP_26,
      description: `Tape PMT-5 ${SUFFIX}`,
      reference: "CHQ 102",
      amount: "20000",
      account: "1120",
      instrument: "Cheque",
    });
    state.entries.pmt5 = (await ledgerLines(page, state.bank.rwf)).find((l) => l.reference === "CHQ 102")!.entry_number;

    await page.goto(`/gl/reports/bank-reconciliation?reconciliation=${state.brc["2"].id}`);
    await expect(page.getByTestId("report-number")).toHaveText("BRC-000002");
    await expect(page.getByTestId("figure-statement-stored")).toHaveText("FRw 1,090,500");
    await expect(page.getByTestId("figure-cashbook-stored")).toHaveText(LEDGER.row9);
    await expect(page.getByTestId("figure-difference-stored")).toHaveText("FRw 0");
    await expect(page.getByTestId("figure-cashbook-live")).toHaveText(LEDGER.closingSep);
    await expect(page.getByTestId("figure-payments-live")).toHaveText("FRw 90,000");
    await expect(page.getByText("Outstanding items: 1", { exact: true })).toBeVisible();
    await expect(page.getByTestId("outstanding-item")).toHaveAttribute("data-entry", state.entries.pmt4);
    await expect(page.getByText("Posted after lock: 1", { exact: true })).toBeVisible();
    const late = page.getByTestId("posted-after-lock");
    await expect(late).toHaveAttribute("data-entry", state.entries.pmt5);
    await expect(late).toContainText("Dated inside BRC-000002");
    await expect(late).toContainText("FRw -20,000");
    await capture(page, "9-2-reconciliation-report-outstanding-and-late");
  });

  // PATH: Import generic-bk-rwf-overlap-oct.csv on 1120 → "2 new, 1 skipped" → New BRC-4 at
  // 31 Oct → Lock at zero.
  test("row 14 — the overlap import, and BRC-4", async ({ page }) => {
    await signIn(page);
    await importThroughDialog(page, state.bank.rwf, RWF_OVERLAP, async (preview) => {
      await expect(preview).toContainText("3 lines · 2 new · 1 already held");
    });
    await expect(page.getByTestId("import-result")).toContainText("BST-000003");
    await expect(page.getByTestId("import-result")).toContainText("2 new, 1 skipped, 2 matched");

    state.brc["4"] = await openReconciliation(page, state.bank.rwf, OCT_31, null, "1000500");
    expect(state.brc["4"].number).toBe("BRC-000004");
    await openWorkspace(page, state.brc["4"]);
    await expect(statementRow(page, "CHQ 101")).toContainText("Matched · reference");
    await expect(statementRow(page, "CHQ 102")).toContainText("Matched · reference");
    await expect(page.getByLabel("Statement balance", { exact: true })).toHaveValue("1000500");
    await expect(figure(page, "ledger")).toHaveText(LEDGER.closingSep);
    await expect(figure(page, "outstanding")).toHaveText("FRw 0");
    await expect(figure(page, "unmatched")).toHaveText("0");
    await expect(figure(page, "difference")).toHaveText("FRw 0");
    await page.getByRole("button", { name: "Lock", exact: true }).click();
    await expect(page.getByText("BRC-000004 locked", { exact: true }).first()).toBeVisible();

    // BRC-2's report still lists PMT-5 under Posted after lock: the history does not move.
    await page.goto(`/gl/reports/bank-reconciliation?reconciliation=${state.brc["2"].id}`);
    await expect(page.getByTestId("posted-after-lock")).toHaveAttribute("data-entry", state.entries.pmt5);
  });

  // PATH: /ap/payment-runs/new (3 Nov, SIN-5, discount declined) → the AP document refuses its
  // own Reverse → Reverse run with a reason → SIN-5 open again.
  test("row 15 — the second run posted and reversed as a unit", async ({ page }) => {
    await signIn(page);
    state.docs["SIN-5"] = (await apiOk(page, "/subledger/ap/documents", {
      method: "POST",
      headers: { "Idempotency-Key": `p8-tape-SIN-5-${SUFFIX}` },
      body: {
        kind: "invoice",
        partner_id: state.partners.S2,
        document_date: NOV_2,
        description: `Tape SIN-5 ${SUFFIX}`,
        payment_terms_id: state.termsId,
        lines: [{ unit_price: "30000", gl_account_id: await acct(page, "6990") }],
      },
    })) as Numbered;

    await page.goto(`/ap/payment-runs/new?account=${state.bank.rwf}`);
    await expect(page.getByRole("heading", { name: "New payment run", exact: true })).toBeVisible();
    await pickDate(page, "Payment date", NOV_3);
    const number = state.docs["SIN-5"].number;
    await page.getByRole("checkbox", { name: `Pay ${number}`, exact: true }).check();
    // The tape pays it in full: the discount is offered and declined.
    await page.getByRole("checkbox", { name: `Take the discount on ${number}`, exact: true }).uncheck();
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(page.getByTestId("preview-counts")).toHaveText("Suppliers: 1 · invoices: 1");
    await expect(page.getByTestId("preview-total")).toHaveText("FRw 30,000");
    await page.getByRole("button", { name: "Post payment run", exact: true }).click();
    await page.waitForURL(/\/ap\/payment-runs\/\d+$/);
    state.run2.id = Number(page.url().split("/").pop());
    await expect(page.getByRole("heading", { name: "Payment run PYR-000002", exact: true })).toBeVisible();

    const run = (await apiOk(page, `/banking/payment-runs/${state.run2.id}`)) as {
      lines: Array<{ settlement_document_id: number; settlement_number: string }>;
    };
    const pmt6 = run.lines[0];
    expect(pmt6.settlement_number).toBe("PMT-000006");
    await page.goto(`/ap/documents/${pmt6.settlement_document_id}`);
    await expect(page.getByTestId("document-total")).toHaveText("FRw 30,000");
    await expect(page.getByTestId("reverse-blocked")).toBeDisabled();
    await expect(page.getByTestId("reverse-blocked-reason")).toContainText("PYR-000002");

    await page.goto(`/ap/payment-runs/${state.run2.id}`);
    await page.getByRole("button", { name: "Reverse run", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Reverse PYR-000002", exact: true });
    await dialog.getByLabel("Reason", { exact: true }).fill("The transfer was recalled");
    await dialog.getByRole("button", { name: "Reverse the run", exact: true }).click();
    await expect(page.getByTestId("run-reversal-reason")).toHaveText("Reversed: The transfer was recalled");
    await expect(page.getByTestId("run-total")).toHaveText("FRw 30,000");

    await page.goto(`/ap/documents/${state.docs["SIN-5"].id}`);
    await expect(page.getByTestId("document-open")).toHaveText("FRw 30,000");
    await page.goto(`/ap/payment-runs?account=${state.bank.rwf}`);
    await expect(page.getByTestId("payment-run-count")).toHaveText("Runs: 2 · standing: 1");
  });

  // PATH: BRC-2's workspace (not the latest: no Reopen) → BRC-4's Reopen with a reason → the
  // listing's last-reconciled back to BRC-2's.
  test("row 16 — reopen refused on BRC-2, allowed on BRC-4", async ({ page }) => {
    await signIn(page);
    await openWorkspace(page, state.brc["2"]);
    await expect(page.getByTestId("reopen-blocked")).toHaveText(
      "Only the latest locked reconciliation on this account can be reopened, and that is BRC-000004.",
    );
    await expect(figure(page, "statement")).toHaveText("FRw 1,090,500");

    await openWorkspace(page, state.brc["4"]);
    await page.getByRole("button", { name: "Reopen", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason for reopening", { exact: true }).fill("The bank restated October");
    await dialog.getByRole("button", { name: "Reopen reconciliation", exact: true }).click();
    await expect(page.getByTestId("reopened-reason")).toHaveText("Reopened: The bank restated October");
    // Its two matches stand: nothing is unmatched, nothing outstanding.
    await expect(figure(page, "unmatched")).toHaveText("0");
    await expect(figure(page, "difference")).toHaveText("FRw 0");

    await page.goto(`/bank/reconciliations?account=${state.bank.rwf}`);
    await expect(page.getByTestId("last-reconciled")).toContainText("FRw 1,090,500");
    await expect(page.getByTestId("reconciliation-count")).toHaveText("Reconciliations: 3 · locked: 2");
  });

  // PATH: /gl/reports/cashbooks (detail, 1120, September) → /gl/enquiries/trial-balance at
  // 30 September: two screens, one figure.
  test("the Cashbooks closing balance is the Trial balance's figure for 1120", async ({ page }) => {
    await signIn(page);
    await page.goto(`/gl/reports/cashbooks?mode=detail&account=${state.bank.rwf}&from=${SEP_1}&to=${SEP_30}`);
    await expect(page.getByRole("heading", { name: "Cashbooks", exact: true })).toBeVisible();
    await expect(page.getByTestId("cashbook-opening")).toHaveText(LEDGER.opening);
    await expect(page.getByTestId("cashbook-receipts")).toHaveText("FRw 477,000");
    await expect(page.getByTestId("cashbook-payments")).toHaveText("FRw 476,500");
    await expect(page.getByTestId("cashbook-closing-row")).toHaveText(LEDGER.closingSep);
    await expect(page.locator("tr[data-cashbook-line]")).toHaveCount(10);
    const reconciled = (entry: string) =>
      page.locator(`tr[data-cashbook-line="${entry}"]`).getByTestId("cashbook-reconciled");
    await expect(reconciled(state.docs["RCT-1"].number)).toHaveText("BRC-000002");
    // BRC-4 is open again, so its two cheques are matched and not yet locked.
    await expect(reconciled(state.entries.pmt4)).toHaveText("Matched");
    await expect(reconciled(state.entries.pmt5)).toHaveText("Matched");
    const closingBase = (await page.getByTestId("cashbook-closing-base").textContent())!.trim();
    expect(closingBase).toBe(LEDGER.closingSep);
    await capture(page, "9-3-cashbooks-reconciled-column");

    await page.goto(`/gl/enquiries/trial-balance`);
    await expect(page.getByRole("heading", { name: "Trial Balance Enquiry", exact: true })).toBeVisible();
    await pickDate(page, "As of", SEP_30);
    const tbRow = page.locator("tbody tr", { hasText: "1120" }).first();
    await expect(tbRow.locator("td").last()).toHaveText(closingBase);
  });

  // PATH: precondition (d) — the owner's real exports, each on an account of its own, the
  // mapping set through the API from the committed `<bank>.format.json`, the file imported
  // through the screen and read off the result and the statement's detail.
  test("the real exports import under their committed mappings", async ({ page }) => {
    await signIn(page);
    const parent = await acct(page, "1100");
    const mapped = async (code: string, name: string, format: string) => {
      const created = (await apiOk(page, "/banking/accounts", {
        method: "POST",
        body: { new_account: { code, name, kind: "bank", parent_id: parent }, code, name },
      })) as Identified;
      const mapping = JSON.parse(readFileSync(path.join(REAL, format), "utf8"));
      await apiOk(page, `/banking/accounts/${created.id}`, {
        method: "PATCH",
        body: { statement_format: mapping },
      });
      return created.id;
    };
    // The one imported last, by id: the listing is in statement-date order, and May's export
    // was imported after June's.
    const latestStatement = async (bankAccountId: number) =>
      ((await apiOk(page, `/banking/statements?bank_account_id=${bankAccountId}`)) as Numbered[]).reduce(
        (latest, row) => (row.id > latest.id ? row : latest),
      );

    // BPR 2025: June, then May on the same account — consecutive months, nothing overlapping.
    const bpr = await mapped("1150", "BPR Current Account", "bpr.format.json");
    await importThroughDialog(page, bpr, path.join(REAL, "bpr-2025-06.csv"), async (preview, dialog) => {
      await expect(preview).toContainText("45 lines · 45 new · 0 already held");
      await expect(dialog.getByTestId("import-preview-closing")).toHaveText("FRw 4,274,862");
    });
    await expect(page.getByTestId("import-result")).toContainText("45 new, 0 skipped");
    await importThroughDialog(page, bpr, path.join(REAL, "bpr-2025-05.csv"), async (preview, dialog) => {
      await expect(dialog.getByTestId("import-preview-opening")).toHaveText("FRw 1,110,776");
    });
    await expect(page.getByTestId("import-result")).toContainText("32 new, 0 skipped");
    const may = await latestStatement(bpr);
    await page.goto(`/bank/statements/${may.id}`);
    await expect(page.getByTestId("statement-line-count")).toHaveText("32");
    await expect(page.getByTestId("statement-closing")).toHaveText("FRw 2,408,456");

    // KCB 2023: the balance-brought-forward row skipped for no amount, the opening derived.
    const kcb = await mapped("1151", "KCB Current Account", "kcb.format.json");
    await importThroughDialog(page, kcb, path.join(REAL, "kcb-2023-12.csv"), async (preview, dialog) => {
      await expect(preview).toContainText("Read cleanly");
      await expect(preview).toContainText("281 lines · 281 new · 0 already held");
      await expect(dialog.getByTestId("import-preview-no-amount")).toHaveText("Skipped for no amount: 1");
      await expect(dialog.getByTestId("import-preview-opening")).toHaveText("FRw 0");
      await expect(dialog.getByTestId("import-preview-closing")).toHaveText("FRw 4,867");
    });
    await expect(page.getByTestId("import-result")).toContainText("281 new, 0 skipped");
    await expect(page.getByTestId("import-result")).toContainText("Skipped for no amount: 1");
    const kcbStatement = await latestStatement(kcb);
    await page.goto(`/bank/statements/${kcbStatement.id}`);
    await expect(page.getByTestId("statement-line-count")).toHaveText("281");
    await expect(page.getByTestId("statement-closing")).toHaveText("FRw 4,867");

    // BK 2019: no balance column, so the opening and closing are keyed in the preview.
    const bk = await mapped("1152", "BK Current Account", "bk.format.json");
    await importThroughDialog(page, bk, path.join(REAL, "bk-2019-10.csv"), async (preview, dialog) => {
      await expect(preview).toContainText("249 lines · 249 new · 0 already held");
      await dialog.getByLabel("Statement opening balance", { exact: true }).fill("1600");
      await dialog.getByLabel("Statement closing balance", { exact: true }).fill("2659");
    });
    await expect(page.getByTestId("import-result")).toContainText("249 new, 0 skipped");
    const bkStatement = await latestStatement(bk);
    await page.goto(`/bank/statements/${bkStatement.id}`);
    await expect(page.getByTestId("statement-line-count")).toHaveText("249");
    await expect(page.getByTestId("statement-closing")).toHaveText("FRw 2,659");
  });
});
