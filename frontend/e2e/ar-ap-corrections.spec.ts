import { expect, test, type Page } from "@playwright/test";
import { PRIMARY_EMAIL, login, pickCombobox } from "./support/fixtures";

/**
 * The AR/AP correction path — Appendix C.1.7's second half, closed before P6.
 *
 * P4 gave every partner document a reversal and every allocation an undo, and shipped both as
 * endpoints with no caller: there was no `/ar/documents/{id}` route to carry the action. So an
 * invoice posted in error, or a receipt allocated against the wrong invoice, was uncorrectable
 * by anybody using the product. These two tests drive both corrections through the screens and
 * read the consequence off the page — the open item withdrawn, the open amounts restored.
 */

const REVENUE = "4100"; // Sales Revenue
const EXPENSE = "6990"; // Sundry Expenses
const BANK = "1120"; // Bank Account

async function pickLineAccount(page: Page, code: string) {
  await page.getByRole("button", { name: "Account, row 1" }).click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(code);
  await page.locator(`[cmdk-item]:has-text("${code}")`).first().click();
}

async function makeCustomer(page: Page, code: string, name: string) {
  await page.goto("/maintenance/customers");
  await page.waitForSelector("h1:has-text('Customers')");
  await page.getByRole("button", { name: /New customer/i }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Customer code").fill(code);
  await dialog.getByLabel("Name", { exact: true }).fill(name);
  await dialog.getByRole("button", { name: "Create", exact: true }).click();
  await expect(page.getByRole("dialog").filter({ hasText: name })).toBeVisible();
  await page.keyboard.press("Escape");
}

async function makeSupplier(page: Page, code: string, name: string) {
  await page.goto("/maintenance/suppliers");
  await page.waitForSelector("h1:has-text('Suppliers')");
  await page.getByRole("button", { name: /New supplier/i }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Supplier code").fill(code);
  await dialog.getByLabel("Name", { exact: true }).fill(name);
  await dialog.getByRole("button", { name: "Create", exact: true }).click();
  await expect(page.getByRole("dialog").filter({ hasText: name })).toBeVisible();
  await page.keyboard.press("Escape");
}

/** Posts an invoice for `amount` and returns the id of the document it created. */
async function postInvoice(page: Page, code: string, description: string, amount: string) {
  await page.goto("/ar/invoices/new");
  await page.waitForSelector("h1:has-text('Invoice')");
  await pickCombobox(page, "Customer", code);
  await page.getByLabel("Description", { exact: true }).fill(description);
  await pickLineAccount(page, REVENUE);
  await page.getByLabel("Quantity, row 1").fill("4");
  await page.getByLabel("Unit price, row 1").fill(amount);
  await page.getByRole("button", { name: /^Post/ }).click();
  await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 20_000 });
}

/**
 * This customer's row on the age analysis — the report an accountant reconciles the AR
 * control account against, and therefore the screen where "the open item is gone" is
 * visible rather than merely asserted. Zero balances are hidden by default (the
 * "Include zero balances" convention), so a customer who owes nothing has no row at all.
 */
function ageAnalysisRow(page: Page, customerName: string) {
  return page.getByRole("row").filter({ hasText: customerName });
}

async function openAgeAnalysisFor(page: Page, role: "ar" | "ap") {
  await page.goto(`/${role}/reports/age-analysis`);
  await page.waitForSelector("h1:has-text('Age analysis')");
  // Wait for **either** ending: the grand-total row the table finishes with, or the empty
  // state. The comment here used to say the grand total meant "loaded, and an empty result is
  // an empty result" — which is not what the screen does. With no rows there is no table at
  // all, so the signal being waited for cannot arrive, and the wait ran to its timeout.
  //
  // It never showed because the suite ran as one file after another and some earlier *file*
  // had always left a balance on this side of the ledger by the time this one asked. Sharding
  // took that away, which is the reset-database rule doing its job: the dependency was always
  // there, and the run order was hiding it.
  await expect(
    page.getByText("Grand total").first().or(page.getByText("Nothing to report").first()),
  ).toBeVisible({ timeout: 20_000 });
}

const openAgeAnalysis = (page: Page) => openAgeAnalysisFor(page, "ar");
const openApAgeAnalysis = (page: Page) => openAgeAnalysisFor(page, "ap");

test.describe("AR corrections", () => {
  // PATH: customer → invoice → /ar/documents (listing, with figures) → /ar/documents/{id} →
  // Reverse → the status chip, the open amount and the control account afterwards.
  // CANNOT SEE: a reversal into a closed period, which the server refuses — that refusal is
  // asserted in the backend suite, not through a screen that cannot reach a closed month.
  test("reverses a posted invoice from the document detail and withdraws its open item", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    const suffix = String(Date.now()).slice(-6);
    const code = `E2EREV${suffix}`;
    await makeCustomer(page, code, `Reversal Customer ${suffix}`);

    // Nothing owed yet, so this customer has no row on the age analysis at all.
    await openAgeAnalysis(page);
    await expect(ageAnalysisRow(page, `Reversal Customer ${suffix}`)).toHaveCount(0);

    // 4 x 15,000 = 60,000, no tax code on the line.
    await postInvoice(page, code, `Reversal invoice ${suffix}`, "15000");

    // --- the listing, with rows and figures in them ------------------------------------
    await page.goto("/ar/documents");
    await page.waitForSelector("h1:has-text('Customer documents')");
    const row = page.getByRole("row").filter({ hasText: `Reversal Customer ${suffix}` }).first();
    await expect(row).toBeVisible({ timeout: 20_000 });
    // A formatted money value read off the page — RWF renders as FRw with no decimals.
    await expect(row.getByTestId("document-total")).toHaveText("FRw 60,000");
    await expect(row.getByTestId("document-open")).toHaveText("FRw 60,000");

    // --- the detail --------------------------------------------------------------------
    await row.getByRole("link").first().click();
    await page.waitForURL(/\/ar\/documents\/\d+/);
    await expect(page.getByTestId("document-total")).toHaveText("FRw 60,000");
    await expect(page.getByTestId("document-open")).toHaveText("FRw 60,000");
    // A formatted quantity read off the page (approvals condition 4): the line's 4.00.
    await expect(page.getByRole("cell", { name: "4.00", exact: true }).first()).toBeVisible();

    // The control account carries the invoice: the customer is now on the age analysis for
    // the whole 60,000.
    await openAgeAnalysis(page);
    await expect(
      // The figure appears twice on the row — in the current bucket and in the total — and
      // both are the same 60,000, which is the point.
      ageAnalysisRow(page, `Reversal Customer ${suffix}`).getByText("60,000").first(),
    ).toBeVisible();
    await page.goBack();
    await page.waitForURL(/\/ar\/documents\/\d+/);

    // --- Reverse -----------------------------------------------------------------------
    await page.getByRole("button", { name: "Reverse", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason").fill("Keyed against the wrong customer");
    await dialog.getByRole("button", { name: "Reverse document", exact: true }).click();
    await expect(page.getByText(/INV-\d+ reversed/).first()).toBeVisible({ timeout: 20_000 });

    // The open item is gone and the document says why it no longer counts.
    await expect(page.getByTestId("document-reversed")).toBeVisible();
    await expect(page.getByTestId("document-open")).toHaveText("FRw 0");
    // Reverse is not offered twice.
    await expect(page.getByRole("button", { name: "Reverse", exact: true })).toBeDisabled();

    // And the control account is back where it started: the customer has dropped off the age
    // analysis entirely, because there is no open item left to bucket. That is the half the
    // GL's own Reverse could never do — it posts the reversing entry and leaves the open item
    // standing — which is why that button is refused for a module-owned entry.
    await openAgeAnalysis(page);
    await expect(ageAnalysisRow(page, `Reversal Customer ${suffix}`)).toHaveCount(0);
  });

  // PATH: customer → invoice → receipt → allocate → /ar/documents/{id} → Unallocate → the
  // open amounts on both documents. CANNOT SEE: an unallocation that posts realized FX;
  // both documents are in base currency. `ar-ap-acceptance` carries the FX case.
  test("unallocates a receipt from the document detail and restores both open amounts", async ({
    page,
  }) => {
    await login(page, PRIMARY_EMAIL);
    const suffix = String(Date.now()).slice(-6);
    const code = `E2EUNA${suffix}`;
    await makeCustomer(page, code, `Unallocate Customer ${suffix}`);

    // 4 x 15,000 = 60,000 invoiced...
    await postInvoice(page, code, `Unallocate invoice ${suffix}`, "15000");

    // ...settled by 20,000.
    await page.goto("/ar/receipts/new");
    await page.waitForSelector("h1:has-text('Receipt')");
    await pickCombobox(page, "Customer", code);
    await page.getByLabel("Description", { exact: true }).fill(`Unallocate receipt ${suffix}`);
    await page.getByLabel("Amount", { exact: true }).fill("20000");
    await pickCombobox(page, "Cash / bank account", BANK);
    await page.getByRole("button", { name: /^Post/ }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 20_000 });

    await page.goto("/ar/allocations/new");
    await page.waitForSelector("h1:has-text('Allocate')");
    await pickCombobox(page, "Partner", code);
    await page.getByRole("button", { name: /^Apply / }).first().click();
    await page.getByLabel(/^Allocate against /).first().fill("20000");
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await page.getByRole("button", { name: /^Post/ }).click();
    await expect(page.getByText(/ALC-\d+ posted/).first()).toBeVisible({ timeout: 20_000 });

    // --- the invoice, part-settled -----------------------------------------------------
    await page.goto("/ar/documents");
    await page.waitForSelector("h1:has-text('Customer documents')");
    const row = page
      .getByRole("row")
      .filter({ hasText: `Unallocate Customer ${suffix}` })
      .filter({ hasText: "FRw 60,000" })
      .first();
    await row.getByRole("link").first().click();
    await page.waitForURL(/\/ar\/documents\/\d+/);
    await expect(page.getByTestId("document-open")).toHaveText("FRw 40,000");

    // The allocation that settled it is on the page, naming the receipt it came from. The
    // allocations table is single-currency like the lines table — an allocation is per
    // partner per currency — so the amounts carry no code; the header totals above do.
    const allocation = page.getByTestId("allocation-amount").first();
    await expect(allocation).toHaveText("20,000");

    // Reverse is refused while the document is allocated — `document_allocated` — and the
    // button says so rather than raising it when pressed. The two corrections are ordered,
    // and the screen states the order beside the action that satisfies it.
    const blocked = page.getByTestId("reverse-blocked");
    await expect(blocked).toBeVisible();
    await expect(blocked).toBeDisabled();
    await expect(blocked).toHaveAttribute("title", "Unallocate first");

    // --- Unallocate --------------------------------------------------------------------
    await page.getByRole("button", { name: "Unallocate", exact: true }).first().click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason").fill("Allocated against the wrong invoice");
    await page.getByTestId("confirm-unallocate").click();
    await expect(page.getByText(/ALC-\d+ unallocated/).first()).toBeVisible({ timeout: 20_000 });

    // Both open amounts are back: the invoice owes its full 60,000 again...
    await expect(page.getByTestId("document-open")).toHaveText("FRw 60,000", { timeout: 20_000 });
    // ...and the original allocation can no longer be undone a second time.
    await expect(
      page.getByRole("button", { name: "Unallocate", exact: true }).first(),
    ).toBeDisabled();

    // With nothing allocated against it, Reverse is live: the order the screen stated is
    // the order the service enforces, and satisfying it releases the action.
    await expect(page.getByTestId("reverse-blocked")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Reverse", exact: true })).toBeEnabled();

    // ...and the receipt has its 20,000 available to allocate again.
    await page.goto("/ar/allocations/new");
    await page.waitForSelector("h1:has-text('Allocate')");
    await pickCombobox(page, "Partner", code);
    await expect(page.getByText("FRw 60,000").first()).toBeVisible({ timeout: 20_000 });
  });
});

test.describe("AP corrections", () => {
  // PATH: supplier → /ap/supplier-invoices/new → /ap/documents → /ap/documents/{id} →
  // Reverse → the AP age analysis afterwards. The AP screens are the same two components
  // with `role="ap"`, so what this covers that the AR tests do not is the role wiring:
  // the supplier partner list, the AP document kinds under their own names, the AP
  // permission on Reverse, and the payable side of the control account unwinding.
  // CANNOT SEE: an AP allocation being undone — that path is identical to AR's and is
  // covered there; this test is about the role, not a second copy of the mechanism.
  test("reverses a supplier invoice from the AP document detail", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    const suffix = String(Date.now()).slice(-6);
    const code = `E2EAPR${suffix}`;
    const name = `AP Reversal Supplier ${suffix}`;
    await makeSupplier(page, code, name);

    // Nothing owed yet, so the supplier has no row on the AP age analysis.
    await openApAgeAnalysis(page);
    await expect(ageAnalysisRow(page, name)).toHaveCount(0);

    // 3 x 25,000 = 75,000.
    await page.goto("/ap/supplier-invoices/new");
    await page.waitForSelector("h1:has-text('Supplier invoice')");
    await pickCombobox(page, "Supplier", code);
    await page.getByLabel("Description", { exact: true }).fill(`AP reversal invoice ${suffix}`);
    await pickLineAccount(page, EXPENSE);
    await page.getByLabel("Quantity, row 1").fill("3");
    await page.getByLabel("Unit price, row 1").fill("25000");
    await page.getByRole("button", { name: /^Post/ }).click();
    await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 20_000 });

    // --- the AP listing ----------------------------------------------------------------
    await page.goto("/ap/documents");
    await page.waitForSelector("h1:has-text('Supplier documents')");
    const row = page.getByRole("row").filter({ hasText: name }).first();
    await expect(row).toBeVisible({ timeout: 20_000 });
    // The owner's menu names this document "Supplier invoice", not "Invoice" — the AP
    // catalogue, not AR's, which is the role wiring this test exists for.
    await expect(row).toContainText("Supplier invoice");
    await expect(row.getByTestId("document-total")).toHaveText("FRw 75,000");

    // --- the AP detail -----------------------------------------------------------------
    await row.getByRole("link").first().click();
    await page.waitForURL(/\/ap\/documents\/\d+/);
    // One formatted money value and one formatted quantity, read off the page.
    await expect(page.getByTestId("document-total")).toHaveText("FRw 75,000");
    await expect(page.getByTestId("document-open")).toHaveText("FRw 75,000");
    await expect(page.getByRole("cell", { name: "3.00", exact: true }).first()).toBeVisible();

    // The payable is on the age analysis.
    await openApAgeAnalysis(page);
    await expect(ageAnalysisRow(page, name).getByText("75,000").first()).toBeVisible();
    await page.goBack();
    await page.waitForURL(/\/ap\/documents\/\d+/);

    // --- Reverse ------------------------------------------------------------------------
    await page.getByRole("button", { name: "Reverse", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason").fill("Supplier billed the wrong company");
    await dialog.getByRole("button", { name: "Reverse document", exact: true }).click();
    await expect(page.getByText(/SIN-\d+ reversed/).first()).toBeVisible({ timeout: 20_000 });

    await expect(page.getByTestId("document-reversed")).toBeVisible();
    await expect(page.getByTestId("document-open")).toHaveText("FRw 0");

    // And the supplier drops off the AP age analysis: no open item left to bucket, so the
    // payable control is back where it started.
    await openApAgeAnalysis(page);
    await expect(ageAnalysisRow(page, name)).toHaveCount(0);
  });
});
