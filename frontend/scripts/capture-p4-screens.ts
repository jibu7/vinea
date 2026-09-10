/**
 * Captures the P4 maintenance screens for the review record: 1440x900, light and dark.
 * Run against the dev stack with `npm run e2e`'s prerequisites already up:
 *   OUT=../docs/screenshots/p4-step-6 npx tsx scripts/capture-p4-screens.ts
 * The read-only shot logs in as the seeded Clerk-role user, so the disabled Save and its
 * `gl:setup_manage` note are real permission state, not a styled mock.
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chromium, type Page } from "@playwright/test";
// The seeded logins and the one credential the seed and the suite share — no literal here
// to drift from `E2E_PASSWORD`.
import {
  PASSWORD,
  PRIMARY_EMAIL as OWNER,
  READONLY_EMAIL as READONLY,
} from "../e2e/support/fixtures";

const OUT = process.env.OUT ?? "screenshots";
const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const API = `${process.env.API_URL ?? "http://localhost:8000"}/api/v1`;
/** Seeded by `seed_e2e` with two overdue invoices — the only partner with anything to age. */
const AGED_CUSTOMER = "Gisenyi Hotel Group";

/** `ONLY=9-age-analysis,10b-statement-page1` re-captures just those, leaving the rest of the
 * directory untouched. Re-shooting all twelve to change one is how a review record ends up
 * with twelve files changed and one of them meaningful. Empty means everything. */
const ONLY = (process.env.ONLY ?? "")
  .split(",")
  .map((name) => name.trim())
  .filter(Boolean);

function wanted(...names: string[]): boolean {
  return ONLY.length === 0 || names.some((name) => ONLY.includes(name));
}

async function hydrated(page: Page, selector: string) {
  await page.waitForFunction((sel) => {
    const node = document.querySelector(sel);
    return (
      node !== null &&
      Object.keys(node).some((k) => k.startsWith("__reactFiber$") || k.startsWith("__reactProps$"))
    );
  }, selector);
}

async function login(page: Page, email: string) {
  await page.goto(`${BASE}/login`);
  await hydrated(page, "form");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL(`${BASE}/`);
  await page.waitForSelector("text=Good morning");
}

async function shoot(page: Page, name: string, theme: "light" | "dark") {
  if (!wanted(name)) return;
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/${name}-${theme}.png` });
  console.log("captured", `${name}-${theme}`);
}

async function pick(page: Page, name: string, needle: string) {
  await page.getByRole("button", { name, exact: true }).click();
  await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
  await page.keyboard.type(needle);
  await page.locator(`[cmdk-item]:has-text("${needle}")`).first().click();
}

/** A USD invoice booked at one rate and a receipt at another, so the allocation preview has a
 * realized exchange difference to show. Seeded through the API rather than the document
 * screens: those have their own shots and their own e2e — what this fixture exists for is to
 * give the *allocation* screen something worth photographing. Every call is a real endpoint
 * with the signed-in user as actor. */
async function seedFxAllocation(page: Page, code: string): Promise<void> {
  const api = async (path: string, body?: unknown, method = "POST") =>
    page.evaluate(
      async ({ url, body, method }) => {
        const res = await fetch(url, {
          method,
          credentials: "include",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": crypto.randomUUID(),
          },
          body: body === undefined ? undefined : JSON.stringify(body),
        });
        return { status: res.status, json: await res.json().catch(() => null) };
      },
      { url: `${API}${path}`, body, method },
    );

  const currencies = (await api("/gl/currencies", undefined, "GET")).json as Array<{
    id: number;
    code: string;
  }>;
  const usd = currencies.find((c) => c.code === "USD")!.id;
  const accounts = (await api("/gl/accounts", undefined, "GET")).json as Array<{
    id: number;
    code: string;
  }>;
  const account = (c: string) => accounts.find((a) => a.code === c)!.id;

  const invoiceDate = "2026-08-10";
  const receiptDate = "2026-09-08";
  await api("/gl/exchange-rates", { currency_id: usd, valid_from: invoiceDate, rate: "1200" });
  await api("/gl/exchange-rates", { currency_id: usd, valid_from: receiptDate, rate: "1310" });

  const partner = (
    await api("/subledger/ar/partners", {
      name: `FX Customer ${code}`,
      customer_code: code,
      currency_id: usd,
    })
  ).json as { id: number };

  await api("/subledger/ar/documents", {
    kind: "invoice",
    partner_id: partner.id,
    document_date: invoiceDate,
    currency_id: usd,
    description: "Export consulting",
    lines: [{ unit_price: "100.00", gl_account_id: account("4100") }],
  });
  await api("/subledger/ar/documents", {
    kind: "settlement",
    partner_id: partner.id,
    document_date: receiptDate,
    currency_id: usd,
    description: "Settled in full",
    amount: "100.00",
    cash_account_id: account("1120"),
    instrument_type: "bank",
  });
}

async function main() {
  const browser = await chromium.launch();
  const maintenanceShots = [
    "1-customer-ar-settings",
    "2-supplier-master",
    "2b-supplier-ap-settings",
    "3-payment-terms",
    "4-bucket-set-editor",
  ];
  if (wanted(...maintenanceShots)) {
    const ownerCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ownerCtx.newPage();
    await login(page, OWNER);

    for (const theme of ["light", "dark"] as const) {
      await page.goto(`${BASE}/maintenance/customers`);
      await page.waitForSelector("h1:has-text('Customers')");
      await page.locator('button[aria-label^="Edit "]').first().click();
      await page.getByRole("tab", { name: "AR settings" }).click();
      await page.waitForTimeout(500);
      await shoot(page, "1-customer-ar-settings", theme);
      await page.keyboard.press("Escape");

      // The list with its seeded row, then the drawer open on the AP settings tab — the two
      // halves of the supplier master.
      await page.goto(`${BASE}/maintenance/suppliers`);
      await page.waitForSelector("h1:has-text('Suppliers')");
      await page.locator('button[aria-label^="Edit "]').first().waitFor({ state: "visible" });
      await shoot(page, "2-supplier-master", theme);
      await page.locator('button[aria-label^="Edit "]').first().click();
      await page.getByRole("tab", { name: "AP settings" }).click();
      await page.waitForTimeout(500);
      await shoot(page, "2b-supplier-ap-settings", theme);
      await page.keyboard.press("Escape");

      await page.goto(`${BASE}/maintenance/payment-terms`);
      await page.waitForSelector("h1:has-text('Payment terms')");
      await shoot(page, "3-payment-terms", theme);

      await page.goto(`${BASE}/maintenance/ageing-bucket-sets`);
      await page.waitForSelector("h1:has-text('Ageing bucket sets')");
      await page.locator('button[aria-label^="Edit "]').first().click();
      await page.waitForSelector("text=Buckets");
      await page.waitForTimeout(400);
      await shoot(page, "4-bucket-set-editor", theme);
      await page.keyboard.press("Escape");
    }
    await ownerCtx.close();
  }

  // The allocation screen, with the preview panel showing real postings. A same-currency
  // allocation posts nothing, so the fixture is a USD invoice settled at a different rate:
  // the panel then shows the control leg and the realized exchange difference.
  if (process.env.SKIP_ALLOCATION !== "1" && wanted("6-allocation-preview")) {
    const allocCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const alloc = await allocCtx.newPage();
    await login(alloc, OWNER);
    const suffix = String(Date.now()).slice(-6);
    const code = `SHOT${suffix}`;
    await seedFxAllocation(alloc, code);
    for (const theme of ["light", "dark"] as const) {
      await alloc.goto(`${BASE}/ar/allocations/new`);
      await alloc.waitForSelector("h1:has-text('Allocate')");
      await pick(alloc, "Partner", code);
      await alloc.getByRole("button", { name: /^Apply / }).first().click();
      await alloc.getByLabel(/^Allocate against /).first().fill("100");
      await alloc.getByRole("button", { name: "Preview", exact: true }).click();
      await alloc.getByTestId("allocation-preview").locator("table tbody tr").first().waitFor();
      await shoot(alloc, "6-allocation-preview", theme);
    }
    await allocCtx.close();
  }

  // The AR batch screen with lines entered, so the partner column and the atomicity note are
  // both visible.
  if (process.env.SKIP_BATCH !== "1" && wanted("7-ar-batch", "7-ap-batch")) {
    const batchCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const batch = await batchCtx.newPage();
    await login(batch, OWNER);
    // Both roles: the same component, and the record should show it working for each.
    const batchScreens = [
      {
        name: "7-ar-batch",
        path: "/ar/batches/new",
        heading: "Account receivable batches",
        partner: "E2EALC",
        narrative: "Interest on overdue account",
      },
      {
        name: "7-ap-batch",
        path: "/ap/batches/new",
        heading: "Account payable batches",
        partner: "E2ESUP001",
        narrative: "Rebate due from supplier",
      },
    ];
    for (const theme of ["light", "dark"] as const) {
      for (const screen of batchScreens) {
        // Drop any draft an earlier pass autosaved, from a page that has no draft of its own:
        // clearing while the batch screen is mounted loses the race with its autosave effect,
        // which re-saves before the reload and brings the "Draft restored" toast back over the
        // footer.
        await batch.goto(`${BASE}/`);
        await batch.evaluate(() => {
          try {
            for (const key of Object.keys(window.localStorage)) {
              if (key.startsWith("vinea.draft.")) window.localStorage.removeItem(key);
            }
          } catch {
            /* private windows make the accessor throw; nothing to clear there */
          }
        });
        await batch.goto(`${BASE}${screen.path}`);
        await batch.waitForSelector(`h1:has-text('${screen.heading}')`);
        await batch.waitForSelector(`h1:has-text('${screen.heading}')`);
        await batch.getByLabel("Reference").fill("Monthly interest run");
        await pick(batch, "Partner, row 1", screen.partner);
        await batch.getByLabel(/^Description, row 1/).fill(screen.narrative);
        await batch.getByLabel(/^Amount, row 1/).fill("1200");
        await batch.waitForTimeout(300);
        await shoot(batch, screen.name, theme);
      }
    }
    await batchCtx.close();
  }

  // Step 8: the customer enquiry with its drill-down open, the age analysis, and the statement
  // job at the point the PDF is downloadable.
  if (
    process.env.SKIP_REPORTS !== "1" &&
    wanted(
      "8-customer-enquiry-drilldown",
      "9-age-analysis",
      "10-statement-ready",
      "10b-statement-page1",
    )
  ) {
    const repCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const rep = await repCtx.newPage();
    await login(rep, OWNER);
    for (const theme of ["light", "dark"] as const) {
      if (wanted("8-customer-enquiry-drilldown")) {
        await rep.goto(`${BASE}/ar/enquiry`);
        await rep.waitForSelector("h1:has-text('Customer enquiry')");
        await pick(rep, "Customer", "E2E");
        const drill = rep.getByRole("button", { name: /^Open journal entry / }).first();
        await drill.waitFor();
        await drill.click();
        await rep.getByRole("dialog").waitFor();
        await rep.waitForTimeout(400);
        await shoot(rep, "8-customer-enquiry-drilldown", theme);
        await rep.keyboard.press("Escape");
      }

      if (wanted("9-age-analysis")) {
        await rep.goto(`${BASE}/ar/reports/age-analysis`);
        await rep.waitForSelector("h1:has-text('Age analysis')");
        await rep.waitForTimeout(500);
        await shoot(rep, "9-age-analysis", theme);
      }

      if (wanted("10-statement-ready")) {
        await rep.goto(`${BASE}/ar/reports/statements`);
        await rep.waitForSelector("h1:has-text('Customer statements')");
        await rep.getByRole("checkbox", { name: AGED_CUSTOMER }).check();
        await rep.getByRole("button", { name: /Queue statement/ }).click();
        await rep.getByTestId("statement-download").waitFor({ timeout: 30_000 });
        await rep.waitForTimeout(300);
        await shoot(rep, "10-statement-ready", theme);
      }
    }

    // 10b: the statement itself. Shot 10 proves the queue -> poll -> download plumbing
    // reaches a downloadable artifact; it cannot show what is *in* the artifact, and the PDF
    // is the thing a customer actually receives. So: take the download, rasterise page 1 and
    // commit that. No theme pair — WeasyPrint renders a print document, which has one look.
    if (wanted("10b-statement-page1")) {
      await rep.goto(`${BASE}/ar/reports/statements`);
      await rep.waitForSelector("h1:has-text('Customer statements')");
      await rep.getByRole("checkbox", { name: AGED_CUSTOMER }).check();
      await rep.getByRole("button", { name: /Queue statement/ }).click();
      const link = rep.getByTestId("statement-download");
      await link.waitFor({ timeout: 30_000 });
      const [download] = await Promise.all([
        rep.waitForEvent("download"),
        link.click(),
      ]);
      const pdf = join(mkdtempSync(join(tmpdir(), "vinea-stmt-")), "statement.pdf");
      await download.saveAs(pdf);
      // `-singlefile` so the output is exactly `<prefix>.png` rather than `<prefix>-1.png`;
      // `-scale-to-y -1` keeps the page's aspect ratio at the 1440 width the other shots use.
      execFileSync("pdftoppm", [
        "-png",
        "-f",
        "1",
        "-l",
        "1",
        "-singlefile",
        "-scale-to-x",
        "1440",
        "-scale-to-y",
        "-1",
        pdf,
        `${OUT}/10b-statement-page1-light`,
      ]);
      console.log("captured", "10b-statement-page1-light (from the PDF)");
    }
    await repCtx.close();
  }

  // The document workspace, and the journal entry it posts to. Deliberately a *foreign
  // currency* invoice: the workspace shows the currency and the booking rate, and the entry
  // behind it shows the base amounts — which is where P4 step 9 found the entry screen
  // formatting `base_amount` with the document's own currency, rendering a USD 1,000 invoice
  // as "$ 1,300,000.00". The pair is the review record for that fix.
  if (wanted("11-document-workspace", "12-fx-invoice-entry")) {
    const docCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const doc = await docCtx.newPage();
    await login(doc, OWNER);

    const partners = await doc.evaluate(async (url) => {
      const res = await fetch(url, { credentials: "include" });
      return (await res.json()) as Array<{ customer_code: string | null }>;
    }, `${API}/subledger/ar/partners`);
    const customer = partners.find((p) => p.customer_code)!.customer_code!;

    for (const theme of ["light", "dark"] as const) {
      // A restored draft would put someone else's half-typed invoice in the review record.
      await doc.goto(`${BASE}/`);
      await doc.evaluate(() => {
        for (const key of Object.keys(window.localStorage)) {
          if (key.startsWith("vinea.draft.")) window.localStorage.removeItem(key);
        }
      });
      await doc.goto(`${BASE}/ar/invoices/new`);
      await doc.waitForSelector("h1:has-text('Invoice')");
      await pick(doc, "Customer", customer);
      await doc.getByLabel("Description", { exact: true }).fill("Export consignment — 20 cases");
      await pick(doc, "Currency", "USD");
      await doc.getByLabel("Exchange rate").fill("1300");
      await doc.getByRole("button", { name: "Account, row 1" }).click();
      await doc.locator("[cmdk-item]").first().waitFor({ state: "visible" });
      await doc.keyboard.type("4100");
      await doc.locator('[cmdk-item]:has-text("4100")').first().click();
      await doc.getByLabel("Quantity, row 1").fill("20");
      await doc.getByLabel("Unit price, row 1").fill("50");
      await doc.waitForTimeout(400);
      await shoot(doc, "11-document-workspace", theme);
    }

    // Post once, from the state the dark shot left, and photograph the entry in both themes.
    if (wanted("12-fx-invoice-entry")) {
      await doc.getByRole("button", { name: /^Post/ }).click();
      await doc.waitForURL(/\/gl\/entries\/\d+/, { timeout: 30_000 });
      // Toasts sit over the footer totals and time out after a few seconds; wait them out
      // rather than photographing the entry through them.
      await doc.getByText(/posted$/).first().waitFor({ state: "hidden", timeout: 30_000 });
      for (const theme of ["light", "dark"] as const) {
        await doc.waitForTimeout(400);
        await shoot(doc, "12-fx-invoice-entry", theme);
      }
    }
    await docCtx.close();
  }

  if (wanted("5-ar-defaults-readonly")) {
    const clerkCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const clerk = await clerkCtx.newPage();
    await login(clerk, READONLY);
    for (const theme of ["light", "dark"] as const) {
      await clerk.goto(`${BASE}/maintenance/ar-ap-defaults`);
      await clerk.waitForSelector("h1:has-text('AR/AP defaults')");
      await clerk.getByRole("button", { name: "Save changes" }).scrollIntoViewIfNeeded();
      await clerk.waitForTimeout(400);
      await shoot(clerk, "5-ar-defaults-readonly", theme);
    }
    await clerkCtx.close();
  }
  await browser.close();
}
main();
