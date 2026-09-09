/**
 * Captures the P4 maintenance screens for the review record: 1440x900, light and dark.
 * Run against the dev stack with `npm run e2e`'s prerequisites already up:
 *   OUT=../docs/screenshots/p4-step-6 npx tsx scripts/capture-p4-screens.ts
 * The read-only shot logs in as the seeded Clerk-role user, so the disabled Save and its
 * `gl:setup_manage` note are real permission state, not a styled mock.
 */
import { chromium, type Page } from "@playwright/test";

const OUT = process.env.OUT ?? "screenshots";
const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const API = `${process.env.API_URL ?? "http://localhost:8000"}/api/v1`;
const PASSWORD = "E2E-Sup3rSecret!1";
const OWNER = "e2e.primary@vinea.example";
const READONLY = "e2e.readonly@vinea.example";

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

  // The allocation screen, with the preview panel showing real postings. A same-currency
  // allocation posts nothing, so the fixture is a USD invoice settled at a different rate:
  // the panel then shows the control leg and the realized exchange difference.
  if (process.env.SKIP_ALLOCATION !== "1") {
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
  await browser.close();
}
main();
