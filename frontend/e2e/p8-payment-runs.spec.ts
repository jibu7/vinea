import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { PRIMARY_EMAIL, accountIdByCode, login, pageFetch, pickCombobox, pickDate } from "./support/fixtures";
import { formatMoney, todayIso } from "../src/lib/format";

/**
 * P8 step 7, part 2 — **Payment runs** (Appendix C.1.15), the run's settlement on the AP document,
 * the chained auto-match on Import, and the FX revaluation screen's bank lines.
 *
 * On Rugari Wines E2E, with suppliers of this file's own (suffixed) and a bank account of its own,
 * so the run's account holds only what this file posts and the statement it imports is matched
 * against nothing else. Three suppliers: one on **2/10 net 30** terms (so today's payment is
 * inside the discount window), one **without bank details**, and one carrying a payment on
 * account (an open credit, named in the preview and never netted). Three supplier invoices are
 * posted through the API; the run pays all three.
 *
 * The walk, in the order the prompt gives it:
 *
 * 1. New: the discount read off the grid, `payment_exceeds_open` said on a row, Preview reading
 *    the discount taken and both warnings, Post → the run's page;
 * 2. the detail with its `PMT-` and `ALC-` links; the instruction CSV downloaded and read (three
 *    rows, one with empty account fields); a remittance advice read through `pdftotext`;
 * 3. the AP document: "Paid in run PYR-n", and Reverse refused before the button with
 *    `payment_run_member`;
 * 4. a one-line statement carrying the run's reference and total imported on the run's account —
 *    the chained auto-match matches it 1:3, read off the workspace;
 * 5. the run reversed with a reason: the open items back, the statement line unmatched;
 * 6. FX revaluation, role *Bank and cash accounts*: 1121's line, after a USD receipt on it and a
 *    dated rate.
 *
 * Every screen asserts one formatted money figure and one formatted quantity read off the page.
 * Every label is `{ exact: true }`.
 */

const SUFFIX = String(Date.now()).slice(-6);
const TODAY = todayIso();
const GL_CODE = `Y${SUFFIX}`;
const GL_NAME = `Bank Account Runs ${SUFFIX}`;
const TERMS_CODE = `T${SUFFIX}`;
const RWF = { code: "RWF", symbol: "FRw", decimalPlaces: 0 };
const USD = { code: "USD", symbol: "$", decimalPlaces: 2 };

/** The three suppliers: `terms` pays 2/10 net 30, `nobank` has no bank details, `credit` carries a
 * payment on account. */
const SUPPLIERS = {
  terms: { name: `Nyanza Timber ${SUFFIX}`, code: `NT${SUFFIX}`, amount: "100000" },
  credit: { name: `Rubavu Glass ${SUFFIX}`, code: `RG${SUFFIX}`, amount: "236000" },
  nobank: { name: `Huye Corks ${SUFFIX}`, code: `HC${SUFFIX}`, amount: "50000" },
} as const;
type SupplierKey = keyof typeof SUPPLIERS;

/** Filled in by the setup, read by the rest — the file runs serially. */
const state = {
  bankAccountId: 0,
  partners: {} as Record<SupplierKey, number>,
  invoices: {} as Record<SupplierKey, { id: number; number: string }>,
  onAccount: "",
  runId: 0,
  runNumber: "",
  settlements: {} as Record<SupplierKey, { id: number; number: string }>,
};

async function apiOk(
  page: Page,
  path: string,
  init: { method?: string; body?: unknown; headers?: Record<string, string> } = {},
): Promise<unknown> {
  const res = await pageFetch(page, path, init);
  expect(res.ok, `${init.method ?? "GET"} ${path} -> ${res.status}: ${JSON.stringify(res.json)}`).toBe(true);
  return res.json;
}

function monthStart(): string {
  return `${TODAY.slice(0, 8)}01`;
}

function monthEnd(): string {
  const [year, month] = TODAY.split("-").map(Number);
  const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return `${TODAY.slice(0, 8)}${String(last).padStart(2, "0")}`;
}

/** The bank's one line for the run: a bulk transfer, one debit, the run's number on it. */
function runStatementCsv(reference: string, total: string): string {
  const closing = String(1_000_000 - Number(total));
  return [
    "Date,Description,Reference,Debit,Credit,Balance",
    [TODAY, `BULK PAYMENT ${reference}`, reference, total, "", closing].join(","),
  ].join("\n") + "\n";
}

async function openRun(page: Page) {
  await page.goto(`/ap/payment-runs/${state.runId}`);
  await expect(page.getByRole("heading", { name: `Payment run ${state.runNumber}`, exact: true })).toBeVisible();
}

function runLine(page: Page, invoice: string): Locator {
  return page.locator(`tr[data-run-line="${invoice}"]`);
}

test.describe.configure({ mode: "serial" });

test.describe("P8 step 7b — payment runs, the AP document, auto-match on import, and the FX bank line", () => {
  // PATH: the setup — the run's bank account, the terms, three suppliers, their invoices, and a
  // payment on account for one of them.
  test("setup: a bank account, 2/10 net 30 terms, three suppliers and their invoices", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    const created = (await apiOk(page, "/banking/accounts", {
      method: "POST",
      body: {
        new_account: { code: GL_CODE, name: GL_NAME, kind: "bank", parent_id: await accountIdByCode(page, "1100") },
        code: GL_CODE,
        name: GL_NAME,
        statement_format: { preset: "generic" },
      },
    })) as { id: number };
    state.bankAccountId = created.id;
    const bankGl = await accountIdByCode(page, GL_CODE);

    // Funds for the run: the money has to be in the account before a person pays from it.
    await apiOk(page, "/gl/cashbook-entries", {
      method: "POST",
      body: {
        entry_date: TODAY,
        description: "Opening deposit",
        reference: `OPEN${SUFFIX}`,
        cash_account_id: bankGl,
        kind: "receipt",
        lines: [{ gl_account_id: await accountIdByCode(page, "3400"), amount: "1000000" }],
      },
      headers: { "Idempotency-Key": `p8-7b-open-${SUFFIX}` },
    });

    const terms = (await apiOk(page, "/subledger/payment-terms", {
      method: "POST",
      body: { code: TERMS_CODE, name: `2/10 net 30 ${SUFFIX}`, due_days: 30, discount_percent: "2", discount_days: 10 },
    })) as { id: number };

    for (const key of Object.keys(SUPPLIERS) as SupplierKey[]) {
      const supplier = SUPPLIERS[key];
      const partner = (await apiOk(page, "/subledger/ap/partners", {
        method: "POST",
        body: {
          name: supplier.name,
          supplier_code: supplier.code,
          ...(key === "terms" ? { payment_terms_id: terms.id } : {}),
          ...(key === "nobank"
            ? {}
            : { bank_name: "Bank of Kigali", bank_account_number: `00040-${supplier.code}`, bank_account_holder: supplier.name }),
        },
      })) as { id: number };
      state.partners[key] = partner.id;
      const invoice = (await apiOk(page, "/subledger/ap/documents", {
        method: "POST",
        body: {
          kind: "invoice",
          partner_id: partner.id,
          document_date: TODAY,
          description: `Supplies from ${supplier.name}`,
          ...(key === "terms" ? { payment_terms_id: terms.id } : {}),
          lines: [{ unit_price: supplier.amount, gl_account_id: await accountIdByCode(page, "6990") }],
        },
        headers: { "Idempotency-Key": `p8-7b-sin-${key}-${SUFFIX}` },
      })) as { id: number; number: string };
      state.invoices[key] = { id: invoice.id, number: invoice.number };
    }

    // An open credit: a payment on account, unallocated. The preview names it and never nets it.
    const onAccount = (await apiOk(page, "/subledger/ap/documents", {
      method: "POST",
      body: {
        kind: "settlement",
        partner_id: state.partners.credit,
        document_date: TODAY,
        description: "Payment on account",
        amount: "20000",
        cash_account_id: bankGl,
        instrument_type: "bank",
      },
      headers: { "Idempotency-Key": `p8-7b-on-account-${SUFFIX}` },
    })) as { number: string };
    state.onAccount = onAccount.number;
  });

  // PATH: /ap/payment-runs/new — the discount, a row refused inline, Preview with the two
  // warnings, Post.
  test("New previews the discount and both warnings, then posts the run", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto(`/ap/payment-runs?account=${state.bankAccountId}`);
    await expect(page.getByRole("heading", { name: "Payment runs", exact: true })).toBeVisible();
    await expect(page.getByTestId("payment-runs-empty")).toBeVisible();
    await page.getByRole("link", { name: "New payment run", exact: true }).click();
    await page.waitForURL(/\/ap\/payment-runs\/new/);
    await expect(page.getByRole("heading", { name: "New payment run", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Bank account", exact: true })).toHaveText(`${GL_CODE} · ${GL_NAME}`);

    // The due-by filter: net 30 from today puts all three inside a date 30 days out.
    const dueBy = new Date(`${TODAY}T00:00:00`);
    dueBy.setDate(dueBy.getDate() + 30);
    const dueByIso = `${dueBy.getFullYear()}-${String(dueBy.getMonth() + 1).padStart(2, "0")}-${String(dueBy.getDate()).padStart(2, "0")}`;
    await pickDate(page, "Due by", dueByIso);

    const invoiceRow = (key: SupplierKey) => page.locator(`tr[data-invoice="${state.invoices[key].number}"]`);
    for (const key of Object.keys(SUPPLIERS) as SupplierKey[]) await expect(invoiceRow(key)).toBeVisible();
    // The money: P4's own discount at today's date, 2 % of 100,000, on the terms supplier only.
    await expect(page.getByTestId(`discount-available-${state.invoices.terms.number}`)).toHaveText("FRw 2,000");
    await expect(page.getByTestId(`discount-available-${state.invoices.nobank.number}`)).toHaveText("—");

    for (const key of Object.keys(SUPPLIERS) as SupplierKey[]) {
      await page.getByRole("checkbox", { name: `Pay ${state.invoices[key].number}`, exact: true }).check();
    }
    // Taken by default where there is one; declined per line by the toggle.
    await expect(
      page.getByRole("checkbox", { name: `Take the discount on ${state.invoices.terms.number}`, exact: true }),
    ).toBeChecked();
    // The quantity: the selection counted.
    await expect(page.getByTestId("selection-count")).toContainText("Selected: 3 of");

    // `payment_exceeds_open`, said on the row before any press — and gone once the amount is.
    const amount = page.getByLabel(`Amount to pay on ${state.invoices.nobank.number}`, { exact: true });
    await amount.fill("60000");
    await expect(page.getByTestId(`line-error-${state.invoices.nobank.number}`)).toHaveText(
      "More than is open. The most this invoice takes is FRw 50,000.",
    );
    await amount.fill("");
    await expect(page.getByTestId(`line-error-${state.invoices.nobank.number}`)).toHaveCount(0);

    await expect(page.getByTestId("post-blocked")).toHaveCount(0);
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    const preview = page.getByTestId("payment-run-preview");
    await expect(preview).toBeVisible();
    await expect(page.getByTestId("preview-counts")).toHaveText("Suppliers: 3 · invoices: 3");
    await expect(page.getByTestId(`discount-taken-${state.invoices.terms.number}`)).toHaveText("FRw 2,000");
    await expect(page.getByTestId("preview-discount-total")).toHaveText("FRw 2,000");
    // 98,000 + 236,000 + 50,000.
    await expect(page.getByTestId("preview-total")).toHaveText("FRw 384,000");
    await expect(page.getByTestId(`supplier-total-${SUPPLIERS.terms.name}`)).toHaveText("FRw 98,000");

    const nobank = page.locator(`[data-preview-supplier="${SUPPLIERS.nobank.name}"]`);
    await expect(nobank.getByText("Bank details missing", { exact: true })).toBeVisible();
    await expect(nobank.getByTestId("warning-bank-details-missing")).toContainText("key this transfer by hand");
    const credit = page.locator(`[data-preview-supplier="${SUPPLIERS.credit.name}"]`);
    await expect(credit.getByTestId("warning-open-credits")).toHaveText(
      `Open credits not netted: ${state.onAccount}. The run pays the invoices in full; allocate the credits on the Allocate screen.`,
    );
    // Never netted: the supplier with the credit is paid the invoice in full.
    await expect(page.getByTestId(`supplier-total-${SUPPLIERS.credit.name}`)).toHaveText("FRw 236,000");

    // A change after the preview withdraws Post until the selection is previewed again.
    await page.getByRole("checkbox", { name: `Take the discount on ${state.invoices.terms.number}`, exact: true }).uncheck();
    await expect(page.getByTestId("post-blocked")).toHaveText("The selection changed after the preview. Preview again.");
    await expect(page.getByRole("button", { name: "Post payment run", exact: true })).toBeDisabled();
    await page.getByRole("checkbox", { name: `Take the discount on ${state.invoices.terms.number}`, exact: true }).check();
    await expect(page.getByTestId("post-blocked")).toHaveCount(0);

    await page.getByRole("button", { name: "Post payment run", exact: true }).click();
    await page.waitForURL(/\/ap\/payment-runs\/\d+$/);
    state.runId = Number(page.url().split("/").pop());
    const run = (await apiOk(page, `/banking/payment-runs/${state.runId}`)) as {
      number: string;
      lines: Array<{ document_id: number; settlement_document_id: number; settlement_number: string }>;
    };
    state.runNumber = run.number;
    for (const key of Object.keys(SUPPLIERS) as SupplierKey[]) {
      const line = run.lines.find((row) => row.document_id === state.invoices[key].id)!;
      state.settlements[key] = { id: line.settlement_document_id, number: line.settlement_number };
    }
  });

  // PATH: /ap/payment-runs/{id} — the lines and their links, the instruction file, the advices.
  test("the run's detail links its payments, downloads its instruction file and its advices", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await openRun(page);
    await expect(page.getByTestId("run-total")).toHaveText("FRw 384,000");
    await expect(page.getByTestId("run-supplier-count")).toHaveText("3");
    await expect(page.getByTestId("run-invoice-count")).toHaveText("3");
    await expect(page.getByTestId("run-cash-total")).toHaveText("FRw 384,000");

    const terms = runLine(page, state.invoices.terms.number);
    // Settled 100,000; 2,000 discount; 98,000 left the bank.
    await expect(terms).toContainText("FRw 100,000");
    await expect(terms).toContainText("FRw 98,000");
    // One `PMT-` per supplier, each a link to its AP document; the `ALC-` beside it.
    for (const key of Object.keys(SUPPLIERS) as SupplierKey[]) {
      const link = runLine(page, state.invoices[key].number).getByRole("link", {
        name: state.settlements[key].number,
        exact: true,
      });
      await expect(link).toHaveAttribute("href", `/ap/documents/${state.settlements[key].id}`);
      await expect(runLine(page, state.invoices[key].number).getByRole("link", { name: /^ALC-/ })).toBeVisible();
    }
    await expect(new Set(Object.values(state.settlements).map((s) => s.number)).size).toBe(3);

    // The instruction file: three beneficiaries, the one without details with its account fields empty.
    const [instruction] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: "Instruction file", exact: true }).click(),
    ]);
    expect(instruction.suggestedFilename()).toBe(`${state.runNumber}-instruction.csv`);
    const csv = readFileSync(await instruction.path(), "utf8").trim().split("\r\n");
    expect(csv[0]).toBe("beneficiary,bank,account number,amount,currency,reference,supplier code");
    expect(csv).toHaveLength(4);
    expect(csv).toContain(`${SUPPLIERS.nobank.name},,,50000,RWF,${state.runNumber},${SUPPLIERS.nobank.code}`);
    expect(csv).toContain(
      `${SUPPLIERS.terms.name},Bank of Kigali,00040-${SUPPLIERS.terms.code},98000,RWF,${state.runNumber},${SUPPLIERS.terms.code}`,
    );

    // The advices run after the post's response; the list is polled until each is ready.
    const advice = page.locator(`tr[data-remittance="${SUPPLIERS.terms.name}"]`);
    await expect(advice).toContainText("Ready", { timeout: 60_000 });
    await expect(page.locator("tr[data-remittance]")).toHaveCount(3);
    const [pdf] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: `Download the advice for ${SUPPLIERS.terms.name}`, exact: true }).click(),
    ]);
    const text = execFileSync("pdftotext", ["-layout", await pdf.path(), "-"], { encoding: "utf8" });
    expect(text).toContain(state.invoices.terms.number);
    expect(text).toContain("98,000");
    expect(text).toContain("2,000");
  });

  // PATH: /ap/documents/{id} on a run's settlement — "Paid in run", and Reverse refused first.
  test("the AP document says Paid in run and refuses its own Reverse before the button", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto(`/ap/documents/${state.settlements.terms.id}`);
    await expect(page.getByRole("heading", { name: state.settlements.terms.number, exact: true })).toBeVisible();
    const paidIn = page.getByTestId("document-payment-run");
    await expect(paidIn).toHaveText(`Paid in run ${state.runNumber}`);
    await expect(paidIn.getByRole("link", { name: state.runNumber, exact: true })).toHaveAttribute(
      "href",
      `/ap/payment-runs/${state.runId}`,
    );
    await expect(page.getByTestId("document-total")).toHaveText("FRw 98,000");
    await expect(page.getByTestId("reverse-blocked-reason")).toHaveText(
      `Paid in ${state.runNumber}: a payment run's payment is reversed with its run. Open ${state.runNumber} and reverse the run.`,
    );
    await expect(page.getByTestId("reverse-blocked")).toBeDisabled();
    // The allocation the run made against the invoice, listed on the payment: 98,000 of cash,
    // with the 2,000 discount beside it closing the 100,000 invoice.
    const allocation = page.locator("tr", { has: page.getByTestId("allocation-amount") });
    await expect(page.getByTestId("allocation-amount")).toHaveText("98,000");
    await expect(allocation).toContainText("2,000");
    await expect(allocation.getByRole("link", { name: state.invoices.terms.number, exact: true })).toBeVisible();
  });

  // PATH: /bank/statements Import → the chained auto-match → the workspace, 1:3.
  test("a one-line statement for the run is imported and auto-matched to its three payments", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto(`/bank/statements?account=${state.bankAccountId}`);
    await expect(page.getByRole("heading", { name: "Bank statements", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Import", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Statement file", { exact: true }).setInputFiles({
      name: "bulk.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(runStatementCsv(state.runNumber, "384000")),
    });
    await dialog.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(dialog.getByTestId("import-preview")).toContainText("1 lines · 1 new · 0 already held");
    await dialog.getByRole("button", { name: "Import statement", exact: true }).click();
    // The quantities: what was new, what was skipped, and what the chained auto-match matched.
    await expect(page.getByTestId("import-result")).toContainText("1 new, 0 skipped, 1 matched");

    const reconciliation = (await apiOk(page, "/banking/reconciliations", {
      method: "POST",
      body: { bank_account_id: state.bankAccountId, reconciliation_date: TODAY, statement_balance: null },
      headers: { "Idempotency-Key": `p8-7b-brc-${SUFFIX}` },
    })) as { id: number; number: string };
    await page.goto(`/bank/reconciliations/${reconciliation.id}`);
    await expect(page.getByRole("heading", { name: `Reconciliation ${reconciliation.number}`, exact: true })).toBeVisible();
    const line = page.locator("tr[data-statement-line-id]", { hasText: `BULK PAYMENT ${state.runNumber}` });
    // One bank line, three ledger lines: the payment run rule.
    await expect(line).toContainText("Matched · payment run");
    await expect(line).toContainText("Ledger lines: 3");
    await expect(line).toContainText("FRw -384,000");
    await expect(page.getByTestId("figure-unmatched")).toHaveText("0");
    // The three ledger lines are the run's settlements, each carrying the run's number as its
    // reference; each reads what it is matched to.
    const ledger = (await apiOk(page, `/banking/accounts/${state.bankAccountId}/ledger-lines`)) as Array<{
      entry_number: string;
      reference: string | null;
    }>;
    const runEntries = ledger.filter((row) => row.reference === state.runNumber).map((row) => row.entry_number);
    expect(runEntries).toHaveLength(3);
    for (const entry of runEntries) {
      await expect(page.locator(`tr[data-entry="${entry}"]`)).toContainText(`Matched to BULK PAYMENT ${state.runNumber}`);
    }
  });

  // PATH: Reverse on /ap/payment-runs/{id} — the reason, the open items back, the match released.
  test("the run is reversed with a reason: the open items come back and the bank line is unmatched", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await openRun(page);
    await expect(page.getByTestId("reverse-run-blocked")).toHaveCount(0);
    await page.getByRole("button", { name: "Reverse run", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: `Reverse ${state.runNumber}`, exact: true });
    await expect(dialog.getByRole("button", { name: "Reverse the run", exact: true })).toBeDisabled();
    await dialog.getByLabel("Reason", { exact: true }).fill("The bulk transfer was recalled by the bank");
    await dialog.getByRole("button", { name: "Reverse the run", exact: true }).click();
    await expect(page.getByTestId("reverse-run-blocked")).toHaveText("This run has already been reversed.");
    await expect(page.getByTestId("run-reversal-reason")).toHaveText(
      "Reversed: The bulk transfer was recalled by the bank",
    );
    await expect(page.getByTestId("run-total")).toHaveText("FRw 384,000");

    // The open items are back — the invoice open in full, its line quantity as posted.
    await page.goto(`/ap/documents/${state.invoices.terms.id}`);
    await expect(page.getByTestId("document-open")).toHaveText("FRw 100,000");
    await expect(page.locator("table").first()).toContainText("1.00");
    // The payment is reversed and still says which run it was paid in.
    await page.goto(`/ap/documents/${state.settlements.terms.id}`);
    await expect(page.getByTestId("document-payment-run")).toHaveText(`Paid in run ${state.runNumber} · the run was reversed`);

    // The listing reads the run as reversed, with its reason.
    await page.goto(`/ap/payment-runs?account=${state.bankAccountId}`);
    const row = page.locator(`tr[data-payment-run="${state.runNumber}"]`);
    await expect(row).toContainText("Reversed");
    await expect(row).toContainText("FRw 384,000");
    await expect(page.getByTestId("payment-run-count")).toHaveText("Runs: 1 · standing: 0");

    // The bank's line is a line nobody has explained again.
    const reconciliations = (await apiOk(page, `/banking/reconciliations?bank_account_id=${state.bankAccountId}`)) as Array<{
      id: number;
    }>;
    await page.goto(`/bank/reconciliations/${reconciliations[0].id}`);
    const line = page.locator("tr[data-statement-line-id]", { hasText: `BULK PAYMENT ${state.runNumber}` });
    await expect(line).toContainText("Unmatched");
    await expect(page.getByTestId("figure-unmatched")).toHaveText("1");
  });

  // PATH: /gl/fx-revaluations, role bank — 1121's line keyed by the account, its balance in USD.
  test("FX revaluation previews 1121's balance under the bank role", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    const currencies = (await apiOk(page, "/gl/currencies")) as Array<{ id: number; code: string }>;
    const usd = currencies.find((c) => c.code === "USD")!;
    const rates = (await apiOk(page, `/gl/exchange-rates?currency_id=${usd.id}`)) as Array<{ valid_from: string }>;
    // The same two dated rates the P7 specs use, added only where they are not already held.
    for (const [validFrom, rate] of [
      [monthStart(), "1320"],
      [monthEnd(), "1350"],
    ] as const) {
      if (rates.some((r) => r.valid_from === validFrom)) continue;
      await apiOk(page, "/gl/exchange-rates", { method: "POST", body: { currency_id: usd.id, valid_from: validFrom, rate } });
    }

    type BankLine = { bank_account_code: string | null; open_amount: string; difference: string };
    const bankLines = async () =>
      ((await apiOk(page, `/gl/fx-revaluations/preview?revaluation_date=${monthEnd()}&role=bank`)) as {
        lines: BankLine[];
      }).lines;
    const previewOf = async () => (await bankLines()).find((line) => line.bank_account_code === "1121");
    const before = await previewOf();
    await apiOk(page, "/gl/cashbook-entries", {
      method: "POST",
      body: {
        entry_date: TODAY,
        description: `USD receipt ${SUFFIX}`,
        reference: `USD${SUFFIX}`,
        cash_account_id: await accountIdByCode(page, "1121"),
        kind: "receipt",
        currency_id: usd.id,
        exchange_rate: "1320",
        lines: [{ gl_account_id: await accountIdByCode(page, "3400"), amount: "250" }],
      },
      headers: { "Idempotency-Key": `p8-7b-usd-${SUFFIX}` },
    });
    const after = (await previewOf())!;
    // On a fresh database `before` is absent: USD 250.00, carried at 330,000, revalued at 337,500.
    expect(Number(after.open_amount)).toBeCloseTo(Number(before?.open_amount ?? 0) + 250, 6);

    await page.goto("/gl/fx-revaluations");
    await expect(page.getByRole("heading", { name: "FX revaluation", exact: true })).toBeVisible();
    await pickCombobox(page, "Side", "Bank and cash accounts");
    const line = page.locator('tr[data-revaluation-line="1121"]');
    await expect(line).toContainText("Bank Account USD");
    // The money, in the account's own currency: the balance, not a document's open item.
    await expect(page.getByTestId("open-1121")).toHaveText(formatMoney(Number(after.open_amount), USD));
    await expect(page.getByTestId("difference-1121")).toHaveText(
      formatMoney(Number(after.difference), RWF, { showCode: false }),
    );
    // The quantity: one line per foreign-currency bank account with a balance — on a fresh
    // Rugari, 1121 alone.
    await expect(page.getByTestId("revaluation-line-count")).toHaveText(`Lines: ${(await bankLines()).length}`);
  });
});
