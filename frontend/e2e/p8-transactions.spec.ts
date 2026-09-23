import { expect, test, type Locator, type Page } from "@playwright/test";
import {
  PRIMARY_EMAIL,
  READONLY_EMAIL,
  accountIdByCode,
  login,
  pageFetch,
  pickCombobox,
  switchUser,
} from "./support/fixtures";
import { todayIso } from "../src/lib/format";

/**
 * P8 step 7, part 1 — **Bank statements** and **Bank reconciliation** (Appendix C.1.14).
 *
 * On Rugari Wines E2E, on a bank account of this file's own, made here with a suffixed code so
 * no other spec reads it and it reads nobody else's lines. The statement is **generated here
 * from the entries this file posts** — there is no committed sample and no fixed year: every
 * date is `todayIso()`, so the lines, the statement and the reconciliation all sit in the open
 * period the fixture always has.
 *
 * The walk, in the order the prompt gives it:
 *
 * 1. import previewed with one bad row → refused before the button; the fixed file imported,
 *    "5 new, 0 skipped, 1 matched" — Import chains the account's auto-match (step 7b), which
 *    finds the opening deposit by amount and date — and the same file again refused as already
 *    imported;
 * 2. the statement's detail, the one line matched and the rest unmatched;
 * 3. New reconciliation, its balance defaulted from the balance column; a receipt the ledger
 *    lacked is posted **after** the import, so *Auto-match* on demand has something the import's
 *    own pass could not have found, and reads it off the panes; a Tick, and its Unmatch;
 * 4. a manual n:m match — refused while it does not balance, the figure inline; accepted at two
 *    ledger lines to one bank line;
 * 5. the fee posted from its line, prefilled by the account's rule; the receipt posted from its
 *    line to a customer;
 * 6. Lock refused with the difference shown on a re-keyed balance; locked at zero; a line posted
 *    afterwards into the period flagged "dated inside BRC-n"; Unmatch refused on the locked one;
 * 7. the read-only member sees the listings and no button;
 * 8. Reopen with a reason; Void refused while the statement's lines are matched, and allowed once
 *    they are unmatched;
 * 9. a paper statement keyed line by line on a second account of this file's own.
 *
 * Every screen asserts one formatted money figure and one formatted quantity read off the page.
 * Every label is `{ exact: true }`.
 */

const SUFFIX = String(Date.now()).slice(-6);
const TODAY = todayIso();
const GL_CODE = `R${SUFFIX}`;
const GL_NAME = `Bank Account Reconcile ${SUFFIX}`;
const PAPER_GL_CODE = `P${SUFFIX}`;
const PAPER_GL_NAME = `Bank Account Paper ${SUFFIX}`;
const CUSTOMER_NAME = `Kigali Imports ${SUFFIX}`;
const CUSTOMER_CODE = `K${SUFFIX}`;
const DEPOSIT_REF = `DEP${SUFFIX}`;

/** Filled in by the first test, read by the rest — the file runs serially. */
const state = {
  bankAccountId: 0,
  paperAccountId: 0,
  statementId: 0,
  statementNumber: "",
  reconciliationId: 0,
  reconciliationNumber: "",
  opening: "",
  bulkA: "",
  bulkB: "",
  cheque: "",
  deposit: "",
};

/** The five lines the bank shows, in the generic layout (decision 3): `Date, Description,
 * Reference, Debit, Credit, Balance`. `badRow` breaks the third data row's date, which is what a
 * mis-set export looks like. */
function statementCsv(badRow = false): string {
  const rows = [
    ["OPENING DEPOSIT", "", "", "1000000", "1000000"],
    [`TRANSFER ${DEPOSIT_REF}`, "", "", "118000", "1118000"],
    ["CASH DEPOSIT BULK", "", "", "40000", "1158000"],
    ["MONTHLY ACCOUNT FEE", "", "2500", "", "1155500"],
    ["INWARD TRF KIGALI IMPORTS", "", "", "60000", "1215500"],
  ];
  const lines = rows.map((row, index) =>
    [badRow && index === 2 ? "not-a-date" : TODAY, ...row].join(","),
  );
  return ["Date,Description,Reference,Debit,Credit,Balance", ...lines].join("\n") + "\n";
}

function csvFile(name: string, badRow = false) {
  return { name, mimeType: "text/csv", buffer: Buffer.from(statementCsv(badRow)) };
}

async function apiOk(
  page: Page,
  path: string,
  init: { method?: string; body?: unknown; headers?: Record<string, string> } = {},
): Promise<unknown> {
  const res = await pageFetch(page, path, init);
  expect(res.ok, `${init.method ?? "GET"} ${path} -> ${res.status}: ${JSON.stringify(res.json)}`).toBe(true);
  return res.json;
}

/** A cashbook entry on this file's bank account against `3400`, returning its `CB-` number. */
async function cashbook(
  page: Page,
  kind: "receipt" | "payment",
  amount: string,
  reference: string,
  description: string,
): Promise<string> {
  const entry = (await apiOk(page, "/gl/cashbook-entries", {
    method: "POST",
    body: {
      entry_date: TODAY,
      description,
      reference,
      cash_account_id: await glIdOfBank(page),
      kind,
      lines: [{ gl_account_id: await accountIdByCode(page, "3400"), amount }],
    },
    headers: { "Idempotency-Key": `p8-7a-${reference}` },
  })) as { number: string };
  return entry.number;
}

async function glIdOfBank(page: Page): Promise<number> {
  return accountIdByCode(page, GL_CODE);
}

async function openWorkspace(page: Page) {
  await page.goto(`/bank/reconciliations/${state.reconciliationId}`);
  await expect(page.getByRole("heading", { name: `Reconciliation ${state.reconciliationNumber}`, exact: true })).toBeVisible();
  await page.locator("tr[data-statement-line-id]").first().waitFor({ state: "visible" });
}

function statementRow(page: Page, text: string): Locator {
  return page.locator("tr[data-statement-line-id]", { hasText: text });
}

function ledgerRow(page: Page, entry: string): Locator {
  return page.locator(`tr[data-entry="${entry}"]`);
}

async function figure(page: Page, name: string): Promise<Locator> {
  return page.getByTestId(`figure-${name}`);
}

test.describe.configure({ mode: "serial" });

test.describe("P8 step 7a — bank statements and the reconciliation workspace", () => {
  // PATH: the setup — this file's own bank account in the generic format, a rule on it, a
  // customer, and the four ledger lines the statement is generated from.
  test("setup: a bank account of this file's own, a rule, and the ledger it is reconciled to", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    const parent = await accountIdByCode(page, "1100");
    for (const [code, name] of [
      [GL_CODE, GL_NAME],
      [PAPER_GL_CODE, PAPER_GL_NAME],
    ]) {
      const created = (await apiOk(page, "/banking/accounts", {
        method: "POST",
        body: {
          new_account: { code, name, kind: "bank", parent_id: parent },
          code,
          name,
          statement_format: { preset: "generic" },
        },
      })) as { id: number };
      if (code === GL_CODE) state.bankAccountId = created.id;
      else state.paperAccountId = created.id;
    }
    await apiOk(page, `/banking/accounts/${state.bankAccountId}/rules`, {
      method: "POST",
      body: {
        pattern: "ACCOUNT FEE",
        gl_account_id: await accountIdByCode(page, "6700"),
        tax_code_id: null,
        description: "Monthly account fee",
        priority: 10,
      },
    });
    await apiOk(page, "/subledger/ar/partners", {
      method: "POST",
      body: { name: CUSTOMER_NAME, customer_code: CUSTOMER_CODE },
    });
    state.opening = await cashbook(page, "receipt", "1000000", `OPEN${SUFFIX}`, "Opening balance");
    state.bulkA = await cashbook(page, "receipt", "25000", `BNKA${SUFFIX}`, "Cash banked, till A");
    state.bulkB = await cashbook(page, "receipt", "15000", `BNKB${SUFFIX}`, "Cash banked, till B");
    state.cheque = await cashbook(page, "payment", "70000", `CHQ${SUFFIX}`, "Cheque to a supplier");
  });

  // PATH: Import — a file with one bad row is previewed and refused before the button; the fixed
  // file is previewed, imported, and listed; the same file again is refused as already held.
  test("Import previews the file, refuses a bad row, and imports the fixed one", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto(`/bank/statements?account=${state.bankAccountId}`);
    await expect(page.getByRole("heading", { name: "Bank statements", exact: true })).toBeVisible();
    await expect(page.getByTestId("statements-empty")).toBeVisible();

    await page.getByRole("button", { name: "Import", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Statement file", { exact: true }).setInputFiles(csvFile("bad.csv", true));
    await dialog.getByRole("button", { name: "Preview", exact: true }).click();
    const preview = dialog.getByTestId("import-preview");
    await expect(preview).toContainText("1 errors");
    const errorRow = preview.locator("table").first().locator("tbody tr");
    await expect(errorRow).toHaveCount(1);
    // Row 4 of the file: the header is row 1, so the third data row is row 4.
    await expect(errorRow.locator("td").first()).toHaveText("4");
    await expect(dialog.getByTestId("import-blocked")).toHaveText(
      "Rows that cannot be read: 1. Nothing is imported until the file reads cleanly.",
    );
    await expect(dialog.getByRole("button", { name: "Import statement", exact: true })).toBeDisabled();

    await dialog.getByLabel("Statement file", { exact: true }).setInputFiles(csvFile("fixed.csv"));
    await dialog.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(preview).toContainText("Read cleanly");
    // The quantity: formatted counts off the preview.
    await expect(preview).toContainText("5 lines · 5 new · 0 already held");
    // The money: the opening and closing the parser derived from the balance column.
    await expect(dialog.getByTestId("import-preview-opening")).toHaveText("FRw 0");
    await expect(dialog.getByTestId("import-preview-closing")).toHaveText("FRw 1,215,500");
    await expect(dialog.getByTestId("import-blocked")).toHaveCount(0);
    await dialog.getByRole("button", { name: "Import statement", exact: true }).click();

    const result = page.getByTestId("import-result");
    // The import chains the account's auto-match: the opening deposit is already in the ledger at
    // the same amount and date, so it is matched as it lands.
    await expect(result).toContainText("5 new, 0 skipped, 1 matched");
    const statements = (await apiOk(page, `/banking/statements?bank_account_id=${state.bankAccountId}`)) as Array<{
      id: number;
      number: string;
    }>;
    state.statementId = statements[0].id;
    state.statementNumber = statements[0].number;

    const row = page.locator(`tr[data-statement="${state.statementNumber}"]`);
    await expect(row).toContainText("Import");
    await expect(row).toContainText("FRw 1,215,500");
    await expect(row.locator("td").nth(6)).toHaveText("5");
    await expect(row).toContainText("Open");

    // The same file again: the preview says so, and the button says why it is not offered.
    await page.getByRole("button", { name: "Import", exact: true }).click();
    await dialog.getByLabel("Statement file", { exact: true }).setInputFiles(csvFile("fixed-again.csv"));
    await dialog.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(preview).toContainText("This file is already imported");
    await expect(preview).toContainText("5 lines · 0 new · 5 already held");
    await expect(dialog.getByRole("button", { name: "Import statement", exact: true })).toBeDisabled();
    await page.keyboard.press("Escape");
  });

  // PATH: the statement's detail — five lines, the one the import's auto-match found matched.
  test("the statement's detail lists its lines with their match state", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto(`/bank/statements?account=${state.bankAccountId}`);
    await page.getByRole("link", { name: state.statementNumber, exact: true }).click();
    await expect(page.getByRole("heading", { name: `Statement ${state.statementNumber}`, exact: true })).toBeVisible();
    await expect(page.getByTestId("statement-closing")).toHaveText("FRw 1,215,500");
    await expect(page.getByTestId("statement-line-count")).toHaveText("5");
    await expect(page.getByTestId("statement-matched")).toHaveText("1 of 5");
    await expect(page.locator("tr[data-statement-line]", { hasText: "OPENING DEPOSIT" })).toContainText(
      "Matched · amount and date",
    );
    const fee = page.locator("tr[data-statement-line]", { hasText: "MONTHLY ACCOUNT FEE" });
    await expect(fee).toContainText("FRw -2,500");
    await expect(fee).toContainText("Unmatched");
    await expect(page.locator("tr[data-statement-line]")).toHaveCount(5);
  });

  // PATH: New reconciliation, defaulted from the balance column; a receipt the ledger lacked is
  // posted; Auto-match; a Tick and its Unmatch.
  test("New opens the workspace; Auto-match, Tick and Unmatch move the live figures", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto(`/bank/reconciliations?account=${state.bankAccountId}`);
    await expect(page.getByRole("heading", { name: "Bank reconciliation", exact: true })).toBeVisible();
    await expect(page.getByTestId("last-reconciled")).toHaveText("Never reconciled");

    await page.getByRole("button", { name: "New reconciliation", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("button", { name: "Bank account", exact: true })).toHaveText(
      `${GL_CODE} · ${GL_NAME}`,
    );
    // Defaulted from the statement's balance column on or before the date — the figure the
    // server itself would have used for an empty field.
    await expect(dialog.getByLabel("Statement balance", { exact: true })).toHaveValue("1215500");
    await expect(dialog.getByText("From the statement's balance column on or before this date.", { exact: true })).toBeVisible();
    await dialog.getByRole("button", { name: "Open reconciliation", exact: true }).click();
    await page.waitForURL(/\/bank\/reconciliations\/\d+$/);
    state.reconciliationId = Number(page.url().split("/").pop());
    const detail = (await apiOk(page, `/banking/reconciliations/${state.reconciliationId}`)) as { number: string };
    state.reconciliationNumber = detail.number;
    await openWorkspace(page);

    // Only the opening deposit is matched (by the import's auto-match): the rest of the ledger
    // is outstanding — 25,000 + 15,000 − 70,000 — and four statement lines are unmatched.
    await expect(await figure(page, "ledger")).toHaveText("FRw 970,000");
    await expect(await figure(page, "outstanding")).toHaveText("FRw -30,000");
    await expect(await figure(page, "unmatched")).toHaveText("4");
    await expect(await figure(page, "difference")).toHaveText("FRw 215,500");

    // The receipt the bank showed on line 2 reaches the ledger after the import — the ordinary
    // order of things — so the import's own auto-match could not have found it, and Auto-match
    // on demand finds it by its reference.
    state.deposit = await cashbook(page, "receipt", "118000", DEPOSIT_REF, "Customer transfer");
    await page.reload();
    await openWorkspace(page);
    await page.getByRole("button", { name: "Auto-match", exact: true }).click();
    await expect(page.getByTestId("auto-match-result")).toHaveText(
      "Auto-match: 1 matched, 0 left for a person to choose",
    );
    // Read off the panes: which rule made each match, and what it is matched to.
    await expect(statementRow(page, "OPENING DEPOSIT")).toContainText("Matched · amount and date");
    await expect(statementRow(page, "OPENING DEPOSIT")).toContainText(`Matched to ${state.opening}`);
    await expect(statementRow(page, `TRANSFER ${DEPOSIT_REF}`)).toContainText("Matched · reference");
    await expect(ledgerRow(page, state.deposit)).toContainText(`Matched to TRANSFER ${DEPOSIT_REF}`);
    await expect(await figure(page, "ledger")).toHaveText("FRw 1,088,000");
    await expect(await figure(page, "outstanding")).toHaveText("FRw -30,000");
    await expect(await figure(page, "unmatched")).toHaveText("3");
    // 40,000 banked + 60,000 received − 2,500 fee: exactly what the ledger still lacks.
    await expect(await figure(page, "difference")).toHaveText("FRw 97,500");

    // Tick the cheque (paper mode): it leaves the outstanding items; Unmatch puts it back.
    await ledgerRow(page, state.cheque).getByRole("button", { name: "Tick", exact: true }).click();
    await expect(ledgerRow(page, state.cheque)).toContainText("Matched · ticked");
    await expect(await figure(page, "outstanding")).toHaveText("FRw 40,000");
    await expect(await figure(page, "difference")).toHaveText("FRw 167,500");
    await ledgerRow(page, state.cheque).getByRole("button", { name: "Unmatch", exact: true }).click();
    await expect(ledgerRow(page, state.cheque)).toContainText("Outstanding");
    await expect(await figure(page, "difference")).toHaveText("FRw 97,500");
  });

  // PATH: a manual n:m match — the balance beside the button, a refusal inline, then one bank
  // line against two ledger lines.
  test("a manual match is refused until it balances, then made n:m", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await openWorkspace(page);

    await page.getByRole("checkbox", { name: "Select statement line CASH DEPOSIT BULK", exact: true }).check();
    await page.getByRole("checkbox", { name: `Select ledger line ${state.bulkA}`, exact: true }).check();
    const bar = page.getByTestId("match-bar");
    await expect(bar).toContainText("Statement lines: 1 · FRw 40,000");
    await expect(bar).toContainText("Ledger lines: 1 · FRw 25,000");
    await expect(page.getByTestId("selection-balance")).toHaveText("Selection is out by FRw 15,000");
    await page.getByRole("button", { name: "Match", exact: true }).click();
    await expect(page.getByTestId("match-error")).toHaveText(
      "Refused: the selection is out by FRw 15,000. A match must balance; the difference is posted from its statement line, never matched.",
    );
    await expect(statementRow(page, "CASH DEPOSIT BULK")).toContainText("Unmatched");

    await page.getByRole("checkbox", { name: `Select ledger line ${state.bulkB}`, exact: true }).check();
    await expect(page.getByTestId("selection-balance")).toHaveText("Selection balances");
    await page.getByRole("button", { name: "Match", exact: true }).click();
    const bulk = statementRow(page, "CASH DEPOSIT BULK");
    await expect(bulk).toContainText("Matched · by hand");
    await expect(bulk).toContainText("Ledger lines: 2");
    await expect(bulk).toContainText(`Matched to ${state.bulkA}, ${state.bulkB}`);
    await expect(await figure(page, "difference")).toHaveText("FRw 57,500");
  });

  // PATH: Post from line — the fee through the cashbook drawer, prefilled by the account's rule;
  // the customer's transfer through the receipt drawer.
  test("the fee is posted from its line by the rule, the transfer as a receipt", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await openWorkspace(page);

    await statementRow(page, "MONTHLY ACCOUNT FEE").getByRole("button", { name: "Post from line", exact: true }).click();
    const drawer = page.getByRole("dialog", { name: "Post from a statement line", exact: true });
    await expect(drawer.getByTestId("post-from-line-amount")).toHaveText("FRw -2,500");
    await expect(drawer.getByTestId("prefill-note")).toHaveText(
      "Prefilled by a bank rule: 6700 · Bank Charges. Nothing posts until you press Post.",
    );
    // The P3 cashbook grid, one row: the rule's account and description, the line's amount —
    // shown, and not taken.
    const row = drawer.locator("table tbody tr").first();
    await expect(row.getByRole("button").first()).toHaveText("6700 · Bank Charges");
    await expect(row.getByLabel("Amount, row 1", { exact: true })).toHaveValue("2,500");
    await expect(row.getByLabel("Amount, row 1", { exact: true })).toHaveAttribute("readonly", "");
    await drawer.getByRole("button", { name: "Post cashbook entry", exact: true }).click();
    await expect(page.getByText(/^CB-\d+ posted and matched$/).first()).toBeVisible();
    await expect(statementRow(page, "MONTHLY ACCOUNT FEE")).toContainText("Matched · posted from the statement");

    await statementRow(page, "INWARD TRF KIGALI IMPORTS")
      .getByRole("button", { name: "Post from line", exact: true })
      .click();
    const receipt = page.getByRole("dialog", { name: "Post from a statement line", exact: true });
    await receipt.getByRole("tab", { name: "Customer receipt", exact: true }).click();
    await pickCombobox(page, "Customer", CUSTOMER_NAME, { within: receipt });
    await receipt.getByRole("button", { name: "Post receipt", exact: true }).click();
    await expect(page.getByText(/^RCT-\d+ posted and matched$/).first()).toBeVisible();
    await expect(statementRow(page, "INWARD TRF KIGALI IMPORTS")).toContainText("Matched · posted from the statement");

    await expect(await figure(page, "unmatched")).toHaveText("0");
    await expect(await figure(page, "ledger")).toHaveText("FRw 1,145,500");
    await expect(await figure(page, "outstanding")).toHaveText("FRw -70,000");
    await expect(await figure(page, "difference")).toHaveText("FRw 0");
  });

  // PATH: Lock — refused with the difference shown before the button on a re-keyed balance;
  // locked at zero; a late line flagged on the locked one; Unmatch refused there.
  test("Lock is refused with the difference shown, and locks at zero", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await openWorkspace(page);

    const keyed = page.getByLabel("Statement balance", { exact: true });
    await keyed.fill("1215000");
    await expect(await figure(page, "difference")).toHaveText("FRw -500");
    await expect(page.getByTestId("lock-blocked")).toHaveText(
      "The difference is FRw -500. A reconciliation locks at zero.",
    );
    await expect(page.getByRole("button", { name: "Lock", exact: true })).toBeDisabled();

    await keyed.fill("1215500");
    await expect(page.getByTestId("lock-ready")).toBeVisible();
    await page.getByRole("button", { name: "Lock", exact: true }).click();
    await expect(page.getByText(`${state.reconciliationNumber} locked`, { exact: true }).first()).toBeVisible();
    await expect(page.getByTestId("locked-note")).toBeVisible();
    await expect(await figure(page, "statement")).toHaveText("FRw 1,215,500");
    await expect(await figure(page, "difference")).toHaveText("FRw 0");
    await expect(await figure(page, "unmatched")).toHaveText("0");

    // Unmatch on a locked match is refused before the button: disabled, with the reason.
    const unmatch = statementRow(page, "OPENING DEPOSIT").getByRole("button", { name: "Unmatch", exact: true });
    await expect(unmatch).toBeDisabled();
    await expect(unmatch).toHaveAttribute("title", `Locked in ${state.reconciliationNumber}. Reopen it to unmatch.`);
    await expect(statementRow(page, "OPENING DEPOSIT")).toContainText(`Locked in ${state.reconciliationNumber}`);

    // A payment dated inside the locked period but posted after the lock is a late line: flagged
    // on the pane, and the stored figures do not move.
    const late = await cashbook(page, "payment", "1000", `LATE${SUFFIX}`, "Bank card, keyed late");
    await page.reload();
    await openWorkspace(page);
    await expect(ledgerRow(page, late)).toContainText(`dated inside ${state.reconciliationNumber}`);
    await expect(await figure(page, "ledger")).toHaveText("FRw 1,145,500");

    const listing = (await apiOk(page, `/banking/accounts`)) as Array<{
      id: number;
      last_reconciled_balance: string | null;
    }>;
    expect(Number(listing.find((row) => row.id === state.bankAccountId)?.last_reconciled_balance)).toBe(1215500);
  });

  // PATH: the gate. A Clerk reads both listings and the workspace, and presses nothing.
  test("a read-only member sees the listings and no button", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await switchUser(page, READONLY_EMAIL);

    await page.goto(`/bank/statements?account=${state.bankAccountId}`);
    const row = page.locator(`tr[data-statement="${state.statementNumber}"]`);
    await expect(row).toContainText("FRw 1,215,500");
    await expect(page.getByRole("button", { name: "Import", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Key a paper statement", exact: true })).toHaveCount(0);

    await page.goto(`/bank/reconciliations?account=${state.bankAccountId}`);
    await expect(page.locator(`tr[data-reconciliation="${state.reconciliationNumber}"]`)).toContainText("Locked");
    await expect(page.getByTestId("reconciliation-count")).toHaveText("Reconciliations: 1 · locked: 1");
    await expect(page.getByRole("button", { name: "New reconciliation", exact: true })).toHaveCount(0);

    await openWorkspace(page);
    await expect(await figure(page, "statement")).toHaveText("FRw 1,215,500");
    for (const name of ["Reopen", "Lock", "Unmatch", "Tick", "Post from line", "Auto-match", "Match"]) {
      await expect(page.getByRole("button", { name, exact: true })).toHaveCount(0);
    }
  });

  // PATH: Reopen with a reason; Void refused while matched, allowed once unmatched.
  test("Reopen, then Void is refused while matched and allowed once unmatched", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await openWorkspace(page);

    await page.getByRole("button", { name: "Reopen", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason for reopening", { exact: true }).fill("The bank restated a fee");
    await dialog.getByRole("button", { name: "Reopen reconciliation", exact: true }).click();
    await expect(page.getByTestId("reopened-reason")).toHaveText("Reopened: The bank restated a fee");
    // Open again: the late line is outstanding in it now, and the difference says so.
    await expect(await figure(page, "outstanding")).toHaveText("FRw -71,000");

    await page.goto(`/bank/statements/${state.statementId}`);
    await expect(page.getByTestId("statement-matched")).toHaveText("5 of 5");
    await expect(page.getByTestId("void-blocked")).toHaveText(
      "Lines in a match: 5. Unmatch them on the reconciliation before voiding.",
    );
    await expect(page.getByRole("button", { name: "Void statement", exact: true })).toBeDisabled();

    await openWorkspace(page);
    const matched = page.locator('tr[data-statement-line-id][data-matched="yes"]');
    for (let remaining = 5; remaining > 0; remaining -= 1) {
      await expect(matched).toHaveCount(remaining);
      await matched.first().getByRole("button", { name: "Unmatch", exact: true }).click();
    }
    await expect(matched).toHaveCount(0);
    await expect(await figure(page, "unmatched")).toHaveText("5");

    await page.goto(`/bank/statements/${state.statementId}`);
    await expect(page.getByTestId("statement-matched")).toHaveText("0 of 5");
    await page.getByRole("button", { name: "Void statement", exact: true }).click();
    const voidDialog = page.getByRole("dialog");
    await voidDialog.getByLabel("Reason for voiding", { exact: true }).fill("Imported to the wrong account");
    await voidDialog.getByRole("button", { name: "Void", exact: true }).click();
    await expect(page.getByText(`${state.statementNumber} voided`, { exact: true }).first()).toBeVisible();
    await expect(page.getByTestId("void-blocked")).toHaveText("This statement is void.");

    // Gone from the listing; back with *Show void statements*, marked Void.
    await page.goto(`/bank/statements?account=${state.bankAccountId}`);
    await expect(page.locator(`tr[data-statement="${state.statementNumber}"]`)).toHaveCount(0);
    await page.getByText("Show void statements", { exact: true }).click();
    await expect(page.locator(`tr[data-statement="${state.statementNumber}"]`)).toContainText("Void");
  });

  // PATH: a paper statement, keyed line by line on the second account.
  test("a paper statement is keyed line by line", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto(`/bank/statements?account=${state.paperAccountId}`);
    await expect(page.getByTestId("statements-empty")).toBeVisible();

    await page.getByRole("button", { name: "Key a paper statement", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Opening balance on the paper", { exact: true }).fill("0");
    await dialog.getByLabel("Closing balance on the paper", { exact: true }).fill("7500");
    await dialog.getByLabel("Description, line 1", { exact: true }).fill("PAPER DEPOSIT");
    await dialog.getByLabel("Money in, line 1", { exact: true }).fill("10000");
    await dialog.getByRole("button", { name: "Add line", exact: true }).click();
    await dialog.getByLabel("Description, line 2", { exact: true }).fill("PAPER FEE");
    await dialog.getByLabel("Money out, line 2", { exact: true }).fill("2500");
    await expect(dialog.getByTestId("manual-total")).toHaveText("Lines: 2 · net FRw 7,500");
    await dialog.getByRole("button", { name: "Save statement", exact: true }).click();

    await expect(page.getByTestId("import-result")).toContainText("2 new, 0 skipped");
    const row = page.locator("tr[data-statement]").first();
    await expect(row).toContainText("Paper");
    await expect(row).toContainText("FRw 7,500");
    await expect(row.locator("td").nth(6)).toHaveText("2");
  });
});
