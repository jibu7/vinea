import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { API_BASE, PRIMARY_EMAIL, accountIdByCode, login, pageFetch } from "./support/fixtures";
import { formatMoney, formatQuantity, todayIso } from "../src/lib/format";

/**
 * P8 step 8 — **Bank account enquiry** (Appendix C.1.14), the owner's **Cashbooks** and **Bank
 * reconciliation** reports, the FX revaluation report's bank lines and the GL entry page's.
 *
 * On Rugari Wines E2E, on a bank account of this file's own (suffixed code, so no other spec
 * reads it and it reads nobody else's lines), with a small ledger posted through the API and every
 * date `todayIso()` so it sits in the period the fixture always has open:
 *
 * | line          | amount     | on the statement | at the lock             |
 * |---------------|-----------:|------------------|-------------------------|
 * | opening       | +1 000 000 | yes              | matched, locked in BRC  |
 * | receipt A     |   +250 000 | yes              | matched, locked in BRC  |
 * | receipt B     |   +120 000 | yes              | matched, locked in BRC  |
 * | payment       |    −80 000 | no               | outstanding (unpresented)|
 * | late payment  |    −30 000 | no               | posted after the lock   |
 *
 * The statement shows 1 370 000. At the lock the cashbook is 1 290 000 and the one unpresented
 * payment of 80 000 adjusts the bank's figure down to it: difference 0. The late payment is dated
 * the reconciliation's own date and posted after it locked, so the report lists it under *Posted
 * after lock* and the stored figures do not move. The book balance afterwards is 1 260 000.
 *
 * The walk, in the order the prompt gives it: the enquiry's figures and each link → Cashbooks
 * detail (the *Reconciled* column, the closing balance against the Trial balance screen — two
 * screens, one figure) → Cashbooks summary → the reconciliation report on the locked BRC → its
 * print through `pdftotext` → its CSV, downloaded → the FX report on a posted bank-role run →
 * the GL entry page on a matched receipt.
 *
 * Every screen asserts one formatted money value and one formatted quantity read off the page.
 * Every label is `{ exact: true }`.
 */

const SUFFIX = String(Date.now()).slice(-6);
const TODAY = todayIso();
const GL_CODE = `E${SUFFIX}`;
const GL_NAME = `Bank Account Enquiry ${SUFFIX}`;
const RWF = { code: "RWF", symbol: "FRw", decimalPlaces: 0 };
const USD = { code: "USD", symbol: "$", decimalPlaces: 2 };
const rwf = (value: number) => formatMoney(value, RWF);

const STATEMENT_BALANCE = 1_370_000;
const LEDGER_AT_LOCK = 1_290_000;
const BOOK_BALANCE = 1_260_000;

/** Filled in by the setup, read by the rest — the file runs serially. */
const state = {
  bankAccountId: 0,
  statementId: 0,
  statementNumber: "",
  reconciliationId: 0,
  reconciliationNumber: "",
  entries: {} as Record<"opening" | "receiptA" | "receiptB" | "payment" | "late", { id: number; number: string }>,
  fxRunId: 0,
  fxRunNumber: "",
  fxRunByThisSpec: 0,
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

/** A cashbook entry on this file's bank account against `3400`. */
async function cashbook(
  page: Page,
  kind: "receipt" | "payment",
  amount: string,
  reference: string,
  description: string,
): Promise<{ id: number; number: string }> {
  return (await apiOk(page, "/gl/cashbook-entries", {
    method: "POST",
    body: {
      entry_date: TODAY,
      description,
      reference,
      cash_account_id: await accountIdByCode(page, GL_CODE),
      kind,
      lines: [{ gl_account_id: await accountIdByCode(page, "3400"), amount }],
    },
    headers: { "Idempotency-Key": `p8-8-${reference}` },
  })) as { id: number; number: string };
}

/** The bank's three lines, in the generic layout: `Date, Description, Reference, Debit, Credit,
 * Balance`. The payment is not on it — that is what makes it outstanding. */
function statementCsv(): string {
  const rows = [
    ["OPENING DEPOSIT", "", "", "1000000", "1000000"],
    [`TRANSFER A${SUFFIX}`, "", "", "250000", "1250000"],
    [`TRANSFER B${SUFFIX}`, "", "", "120000", "1370000"],
  ];
  return ["Date,Description,Reference,Debit,Credit,Balance", ...rows.map((row) => [TODAY, ...row].join(","))].join(
    "\n",
  ) + "\n";
}

/** The import endpoint is multipart, which `pageFetch` does not speak; the same page-side
 * `fetch` with the session's cookies, and a `FormData` body. */
async function importStatement(page: Page): Promise<{ id: number; number: string }> {
  const result = await page.evaluate(
    async ({ url, accountId, csv, key }) => {
      const form = new FormData();
      form.set("bank_account_id", String(accountId));
      form.set("file", new Blob([csv], { type: "text/csv" }), "p8-step-8.csv");
      const res = await fetch(url, {
        method: "POST",
        credentials: "include",
        headers: { "Idempotency-Key": key },
        body: form,
      });
      return { ok: res.ok, status: res.status, json: await res.json().catch(() => null) };
    },
    {
      url: `${API_BASE}/banking/statements`,
      accountId: state.bankAccountId,
      csv: statementCsv(),
      key: `p8-8-import-${SUFFIX}`,
    },
  );
  expect(result.ok, `import -> ${result.status}: ${JSON.stringify(result.json)}`).toBe(true);
  return (result.json as { statement: { id: number; number: string } }).statement;
}

function cashbookLine(page: Page, entryNumber: string): Locator {
  return page.locator(`tr[data-cashbook-line="${entryNumber}"]`);
}

test.describe.configure({ mode: "serial", timeout: 180_000 });

test.describe("P8 step 8 — the bank enquiry and reports", () => {
  // PATH: the setup — an account of this file's own, five ledger lines, one statement imported
  // and matched 1:1 by hand, one reconciliation locked, one line posted after the lock.
  test("setup: a small ledger reconciled and locked, and one line posted after", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    const parent = await accountIdByCode(page, "1100");
    const created = (await apiOk(page, "/banking/accounts", {
      method: "POST",
      body: {
        new_account: { code: GL_CODE, name: GL_NAME, kind: "bank", parent_id: parent },
        code: GL_CODE,
        name: GL_NAME,
        statement_format: { preset: "generic" },
      },
    })) as { id: number };
    state.bankAccountId = created.id;

    state.entries.opening = await cashbook(page, "receipt", "1000000", `OPEN${SUFFIX}`, "Opening balance");
    state.entries.receiptA = await cashbook(page, "receipt", "250000", `A${SUFFIX}`, "Transfer in, A");
    state.entries.receiptB = await cashbook(page, "receipt", "120000", `B${SUFFIX}`, "Transfer in, B");
    state.entries.payment = await cashbook(page, "payment", "80000", `CHQ${SUFFIX}`, "Cheque to a supplier");

    const statement = await importStatement(page);
    state.statementId = statement.id;
    state.statementNumber = statement.number;

    // Matched by hand, one to one, so the test does not depend on which rule would find them.
    const detail = (await apiOk(page, `/banking/statements/${state.statementId}`)) as {
      lines: Array<{ id: number; amount: string }>;
    };
    const ledger = (await apiOk(page, `/banking/accounts/${state.bankAccountId}/ledger-lines`)) as Array<{
      journal_line_id: number;
      entry_id: number;
    }>;
    const lineOf = (entryId: number) => ledger.find((line) => line.entry_id === entryId)!.journal_line_id;
    for (const [amount, entry] of [
      ["1000000", state.entries.opening],
      ["250000", state.entries.receiptA],
      ["120000", state.entries.receiptB],
    ] as const) {
      const statementLine = detail.lines.find((line) => Number(line.amount) === Number(amount))!;
      await apiOk(page, "/banking/matches", {
        method: "POST",
        body: {
          bank_account_id: state.bankAccountId,
          statement_line_ids: [statementLine.id],
          journal_line_ids: [lineOf(entry.id)],
        },
      });
    }

    const opened = (await apiOk(page, "/banking/reconciliations", {
      method: "POST",
      body: {
        bank_account_id: state.bankAccountId,
        reconciliation_date: TODAY,
        statement_balance: String(STATEMENT_BALANCE),
      },
      headers: { "Idempotency-Key": `p8-8-open-${SUFFIX}` },
    })) as { id: number; number: string };
    state.reconciliationId = opened.id;
    state.reconciliationNumber = opened.number;
    const locked = (await apiOk(page, `/banking/reconciliations/${opened.id}/lock`, {
      method: "POST",
      body: {},
      headers: { "Idempotency-Key": `p8-8-lock-${SUFFIX}` },
    })) as { status: string };
    expect(locked.status).toBe("locked");

    // Dated the reconciliation's own date, posted after it locked: decision 5's late line.
    state.entries.late = await cashbook(page, "payment", "30000", `LATE${SUFFIX}`, "Bank charge keyed late");
  });

  // PATH: /gl/enquiries/bank-account -> GET /banking/enquiries/bank-account/{id}. Each figure,
  // then each link followed to where it says it goes.
  test("the enquiry shows decision 10's figures, and each one links", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto(`/gl/enquiries/bank-account?account=${state.bankAccountId}&as_of=${TODAY}`);
    await expect(page.getByRole("heading", { name: "Bank account enquiry", exact: true })).toBeVisible();

    await expect(page.getByTestId("enquiry-book-balance")).toHaveText(rwf(BOOK_BALANCE));
    await expect(page.getByTestId("enquiry-last-reconciliation")).toHaveText(state.reconciliationNumber);
    await expect(page.getByTestId("enquiry-last-reconciled")).toContainText(rwf(STATEMENT_BALANCE));
    // The quantities: nothing on the statement unmatched, two ledger lines outstanding (the
    // unpresented payment and the late one), 110 000 between them.
    await expect(page.getByTestId("enquiry-unmatched-count")).toHaveText(formatQuantity(0, 0));
    await expect(page.getByTestId("enquiry-outstanding-count")).toHaveText(formatQuantity(2, 0));
    await expect(page.getByTestId("enquiry-outstanding-total")).toHaveText(rwf(-110_000));
    await expect(page.getByTestId("enquiry-latest-statement")).toHaveText(state.statementNumber);

    // Each link, by its href and then by following it.
    await expect(page.getByTestId("enquiry-book-balance")).toHaveAttribute(
      "href",
      `/gl/reports/cashbooks?account=${state.bankAccountId}&to=${TODAY}`,
    );
    await expect(page.getByTestId("enquiry-last-reconciliation")).toHaveAttribute(
      "href",
      `/gl/reports/bank-reconciliation?reconciliation=${state.reconciliationId}`,
    );
    await expect(page.getByTestId("enquiry-latest-statement")).toHaveAttribute(
      "href",
      `/bank/statements/${state.statementId}`,
    );
    // No reconciliation is open, so "in progress" and both open-work figures point at the listing
    // where one is started.
    await expect(page.getByTestId("enquiry-open-reconciliation")).toHaveText("None open. Start one");
    for (const id of ["enquiry-open-reconciliation", "enquiry-unmatched", "enquiry-outstanding"]) {
      await expect(page.getByTestId(id)).toHaveAttribute("href", `/bank/reconciliations?account=${state.bankAccountId}`);
    }

    await page.getByTestId("enquiry-latest-statement").click();
    await expect(page).toHaveURL(new RegExp(`/bank/statements/${state.statementId}$`));
    await expect(page.getByRole("heading", { name: `Statement ${state.statementNumber}`, exact: true })).toBeVisible();

    await page.goBack();
    await page.getByTestId("enquiry-open-reconciliation").click();
    await expect(page).toHaveURL(new RegExp(`/bank/reconciliations\\?account=${state.bankAccountId}$`));
    await expect(page.getByRole("heading", { name: "Bank reconciliation", exact: true })).toBeVisible();

    await page.goBack();
    await page.getByTestId("enquiry-last-reconciliation").click();
    await expect(page).toHaveURL(/\/gl\/reports\/bank-reconciliation\?reconciliation=\d+$/);
    await expect(page.getByTestId("report-number")).toHaveText(state.reconciliationNumber);

    await page.goBack();
    await page.getByTestId("enquiry-book-balance").click();
    await expect(page).toHaveURL(/\/gl\/reports\/cashbooks\?/);
    await expect(page.getByTestId("cashbook-closing")).toHaveText(rwf(BOOK_BALANCE));
  });

  // PATH: /gl/reports/cashbooks (detail) -> GET /banking/reports/cashbook, and the Trial balance
  // enquiry for the same account and date: two screens, one figure.
  test("Cashbooks detail reads BRC-n, matched or blank, and closes on the trial balance", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto(`/gl/reports/cashbooks?mode=detail&account=${state.bankAccountId}&to=${TODAY}`);
    await expect(page.getByRole("heading", { name: "Cashbooks", exact: true })).toBeVisible();

    await expect(cashbookLine(page, state.entries.receiptA.number).getByTestId("cashbook-reconciled")).toHaveText(
      state.reconciliationNumber,
    );
    await expect(cashbookLine(page, state.entries.payment.number).getByTestId("cashbook-reconciled")).toHaveText("");
    await expect(cashbookLine(page, state.entries.late.number).getByTestId("cashbook-reconciled")).toHaveText("");
    await expect(cashbookLine(page, state.entries.payment.number)).toContainText(rwf(80_000));
    await expect(page.locator("tr[data-cashbook-line]")).toHaveCount(5);
    await expect(page.getByText(`Totals over ${formatQuantity(5, 0)} lines`, { exact: true })).toBeVisible();
    await expect(page.getByTestId("cashbook-receipts")).toHaveText(rwf(1_370_000));
    await expect(page.getByTestId("cashbook-closing-row")).toHaveText(rwf(BOOK_BALANCE));

    const closingBase = (await page.getByTestId("cashbook-closing-base").textContent())!.trim();
    expect(closingBase).toBe(rwf(BOOK_BALANCE));

    // The other screen. The trial balance is a base-currency report and this account is RWF, so
    // its net is the cashbook's closing in base, to the franc.
    await page.goto("/gl/enquiries/trial-balance");
    await expect(page.getByRole("heading", { name: "Trial Balance Enquiry", exact: true })).toBeVisible();
    const tbRow = page.locator("tbody tr", { hasText: GL_CODE });
    await expect(tbRow.locator("td").last()).toHaveText(closingBase);
  });

  // PATH: /gl/reports/cashbooks (summary) -> GET /banking/reports/cashbook-summary.
  test("Cashbooks summary gives the account one row that agrees with its detail", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto(`/gl/reports/cashbooks?mode=summary&from=${monthStart()}&to=${TODAY}`);
    await expect(page.getByRole("heading", { name: "Cashbooks", exact: true })).toBeVisible();
    await expect(page.getByTestId(`summary-closing-${GL_CODE}`)).toHaveText(rwf(BOOK_BALANCE));
    await expect(page.getByTestId(`summary-unmatched-${GL_CODE}`)).toHaveText(formatQuantity(0, 0));
    await expect(page.getByTestId(`summary-outstanding-${GL_CODE}`)).toHaveText(formatQuantity(2, 0));

    const row = page.locator(`tr[data-cashbook-account="${GL_CODE}"]`);
    await row.getByRole("link", { name: `${GL_CODE} · ${GL_NAME}`, exact: true }).click();
    await expect(page).toHaveURL(/mode=detail/);
    await expect(page.getByTestId("cashbook-closing")).toHaveText(rwf(BOOK_BALANCE));
  });

  // PATH: /gl/reports/bank-reconciliation -> GET /banking/reports/reconciliation/{id}; the print
  // through `pdftotext`, and the CSV through a real download.
  test("the reconciliation report shows the statement, an outstanding item and the late line", async ({ page }, testInfo) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto(`/gl/reports/bank-reconciliation?reconciliation=${state.reconciliationId}`);
    await expect(page.getByRole("heading", { name: "Bank reconciliation", exact: true })).toBeVisible();
    await expect(page.getByTestId("report-account")).toHaveText(`${GL_CODE} · ${GL_NAME}`);

    // What it said at the lock, and what the date computes now: they differ by the late line.
    await expect(page.getByTestId("figure-statement-stored")).toHaveText(rwf(STATEMENT_BALANCE));
    await expect(page.getByTestId("figure-payments-stored")).toHaveText(rwf(80_000));
    await expect(page.getByTestId("figure-adjusted-stored")).toHaveText(rwf(LEDGER_AT_LOCK));
    await expect(page.getByTestId("figure-cashbook-stored")).toHaveText(rwf(LEDGER_AT_LOCK));
    await expect(page.getByTestId("figure-difference-stored")).toHaveText(rwf(0));
    await expect(page.getByTestId("figure-cashbook-live")).toHaveText(rwf(BOOK_BALANCE));
    await expect(page.getByTestId("figure-payments-live")).toHaveText(rwf(110_000));

    await expect(page.getByText(`Outstanding items: ${formatQuantity(1, 0)}`, { exact: true })).toBeVisible();
    await expect(page.getByTestId("outstanding-item")).toHaveCount(1);
    await expect(page.getByTestId("outstanding-item")).toHaveAttribute("data-entry", state.entries.payment.number);
    await expect(page.getByText(`Posted after lock: ${formatQuantity(1, 0)}`, { exact: true })).toBeVisible();
    const late = page.getByTestId("posted-after-lock");
    await expect(late).toHaveAttribute("data-entry", state.entries.late.number);
    await expect(late).toContainText(`Dated inside ${state.reconciliationNumber}`);
    await expect(late).toContainText(rwf(-30_000));

    // The paper. The filters and the workspace link are print:hidden; the statement is not.
    const pdfPath = testInfo.outputPath("reconciliation.pdf");
    await page.pdf({ path: pdfPath, format: "A4", printBackground: false });
    const printed = execFileSync("pdftotext", ["-layout", pdfPath, "-"], { encoding: "utf8" });
    expect(printed).toMatch(new RegExp(`= Adjusted bank balance\\s+${escape(rwf(LEDGER_AT_LOCK))}`));
    expect(printed).toContain(`Outstanding items: ${formatQuantity(1, 0)}`);
    expect(printed).toContain(state.entries.payment.number);
    expect(printed).not.toContain("Open the workspace");

    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: "Export CSV", exact: true }).click(),
    ]);
    expect(download.suggestedFilename()).toBe(`bank-reconciliation-${state.reconciliationNumber}.csv`);
    const rows = readFileSync(await download.path(), "utf8")
      .replace(/^﻿/, "")
      .trim()
      .split("\r\n")
      .map((line) => line.split(","));
    const cell = (label: string) => Number(rows.find((row) => row[0] === label)![5]);
    expect(cell("= Adjusted bank balance")).toBe(LEDGER_AT_LOCK);
    expect(cell("Less: unpresented payments")).toBe(80_000);
    const outstanding = rows.filter((row) => row[0] === "Outstanding");
    expect(outstanding.map((row) => row[2])).toEqual([state.entries.payment.number]);
    expect(Number(outstanding[0][5])).toBe(-80_000);
    expect(rows.filter((row) => row[0] === "Posted after lock").map((row) => row[2])).toEqual([
      state.entries.late.number,
    ]);
  });

  // PATH: /gl/reports/fx-revaluation on a posted bank-role run -> GET /gl/fx-revaluations/{id}.
  // The run is this file's own unless one covering the bank already stands at the month end.
  test("the FX revaluation report shows 1121's bank line on a bank-role run", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    const currencies = (await apiOk(page, "/gl/currencies")) as Array<{ id: number; code: string }>;
    const usd = currencies.find((c) => c.code === "USD")!;
    const rates = (await apiOk(page, `/gl/exchange-rates?currency_id=${usd.id}`)) as Array<{ valid_from: string }>;
    for (const [validFrom, rate] of [
      [monthStart(), "1320"],
      [monthEnd(), "1350"],
    ] as const) {
      if (rates.some((r) => r.valid_from === validFrom)) continue;
      await apiOk(page, "/gl/exchange-rates", { method: "POST", body: { currency_id: usd.id, valid_from: validFrom, rate } });
    }

    type Run = { id: number; number: string; role: string; status: string; revaluation_date: string };
    const standing = ((await apiOk(page, "/gl/fx-revaluations")) as Run[]).find(
      (run) => run.status === "posted" && run.revaluation_date === monthEnd() && ["bank", "all"].includes(run.role),
    );
    if (standing) {
      state.fxRunId = standing.id;
      state.fxRunNumber = standing.number;
    } else {
      // A balance on 1121 for the run to revalue, booked at 1 320 and revalued at 1 350.
      await apiOk(page, "/gl/cashbook-entries", {
        method: "POST",
        body: {
          entry_date: TODAY,
          description: `USD receipt ${SUFFIX}`,
          reference: `USD8${SUFFIX}`,
          cash_account_id: await accountIdByCode(page, "1121"),
          kind: "receipt",
          currency_id: usd.id,
          exchange_rate: "1320",
          lines: [{ gl_account_id: await accountIdByCode(page, "3400"), amount: "100" }],
        },
        headers: { "Idempotency-Key": `p8-8-usd-${SUFFIX}` },
      });
      // The mirror posts the day after the month end, so the next period must be open.
      const periods = (await apiOk(page, "/gl/periods")) as Array<{ id: number; start_date: string; status: string }>;
      const next = periods.find((p) => p.start_date > monthEnd() && p.status !== "open");
      if (next) await apiOk(page, `/gl/periods/${next.id}/open`, { method: "POST" });
      const run = (await apiOk(page, "/gl/fx-revaluations", {
        method: "POST",
        headers: { "Idempotency-Key": `p8-8-fxr-${SUFFIX}` },
        body: { revaluation_date: monthEnd(), role: "bank" },
      })) as Run;
      state.fxRunId = run.id;
      state.fxRunNumber = run.number;
      state.fxRunByThisSpec = run.id;
    }
    const detail = (await apiOk(page, `/gl/fx-revaluations/${state.fxRunId}`)) as {
      lines: Array<{ bank_account_code: string | null; open_amount: string; difference: string }>;
    };
    const line1121 = detail.lines.find((line) => line.bank_account_code === "1121")!;
    expect(line1121, "a bank-role run carries 1121's line").toBeDefined();

    await page.goto("/gl/reports/fx-revaluation");
    await expect(page.getByRole("heading", { name: "FX revaluation", exact: true })).toBeVisible();
    await page.getByRole("combobox", { name: "Revaluation run", exact: true }).click();
    await page.getByRole("option", { name: new RegExp(`^${state.fxRunNumber} — `) }).click();
    await expect(page.getByTestId("fx-run-number")).toHaveText(state.fxRunNumber);

    const row = page.locator('tr[data-revaluation-line="1121"]');
    await expect(row).toContainText("Bank Account USD");
    await expect(row.getByTestId("fx-line-bank")).toHaveAttribute(
      "href",
      new RegExp(`^/gl/reports/cashbooks\\?mode=detail&account=\\d+&to=${monthEnd()}$`),
    );
    await expect(row.getByTestId("fx-line-open")).toHaveText(formatMoney(Number(line1121.open_amount), USD));
    await expect(row.getByTestId("fx-line-difference")).toHaveText(
      formatMoney(Number(line1121.difference), RWF, { showCode: false }),
    );
    await expect(page.getByTestId("fx-line-count")).toHaveText(formatQuantity(detail.lines.length, 0));
  });

  // PATH: /gl/entries/{id} -> GET /banking/journal-entries/{id}/bank-lines. The matched receipt
  // reads the BRC it was locked in; the late payment reads outstanding, dated inside it.
  test("the GL entry page reads a bank line's BRC-n, or outstanding", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    await page.goto(`/gl/entries/${state.entries.receiptA.id}`);
    await expect(page.getByRole("heading", { name: "Cashbook Batch", exact: true })).toBeVisible();
    const bankLine = page.getByTestId("entry-bank-line");
    await expect(bankLine).toHaveCount(1);
    await expect(bankLine).toContainText(`Locked in ${state.reconciliationNumber}`);
    await expect(bankLine.getByTestId("entry-bank-brc")).toHaveAttribute(
      "href",
      `/gl/reports/bank-reconciliation?reconciliation=${state.reconciliationId}`,
    );
    await expect(page.locator("tbody tr", { has: bankLine })).toContainText(rwf(250_000));

    await page.goto(`/gl/entries/${state.entries.late.id}`);
    const lateLine = page.getByTestId("entry-bank-line");
    await expect(lateLine).toContainText("Outstanding");
    await expect(lateLine).toContainText(`Dated inside ${state.reconciliationNumber}`);
    await expect(page.locator("tbody tr", { has: lateLine })).toContainText(rwf(30_000));
    // The contra line on 3400 is not a bank line and carries nothing.
    await expect(page.locator("tbody tr")).toHaveCount(2);
  });

  test("teardown: the FX run this file posted is reversed", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    if (state.fxRunByThisSpec) {
      await apiOk(page, `/gl/fx-revaluations/${state.fxRunByThisSpec}/reverse`, {
        method: "POST",
        body: { reason: `P8 step 8 e2e finished ${SUFFIX}` },
      });
    }
    expect(state.bankAccountId).toBeGreaterThan(0);
  });
});

function escape(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
