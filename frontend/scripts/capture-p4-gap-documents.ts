/**
 * Captures the AR/AP document screens for the review record: 1440x900, light and dark.
 *   OUT=../docs/screenshots/p4-gap npx tsx scripts/capture-p4-gap-documents.ts
 *
 * Rule 13: rows with figures in them. The seed posts through the real endpoints — two
 * invoices, a receipt, the allocation that part-settles one of them, and a reversal — so the
 * listing shows both statuses and the detail shows an allocation with a live Unallocate
 * beside it. An empty-state screenshot would prove the route compiles and nothing else.
 */
import { chromium, type Page } from "@playwright/test";
import { PASSWORD, PRIMARY_EMAIL as OWNER } from "../e2e/support/fixtures";

const OUT = process.env.OUT ?? "screenshots";
const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const API = `${process.env.API_URL ?? "http://localhost:8000"}/api/v1`;

async function hydrated(page: Page, selector: string) {
  await page.waitForFunction((sel) => {
    const node = document.querySelector(sel);
    return (
      node !== null &&
      Object.keys(node).some((k) => k.startsWith("__reactFiber$") || k.startsWith("__reactProps$"))
    );
  }, selector);
}

async function login(page: Page) {
  await page.goto(`${BASE}/login`);
  await hydrated(page, "form");
  await page.fill('input[type="email"]', OWNER);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL(`${BASE}/`);
  await page.waitForSelector("text=Good morning");
}

async function shoot(page: Page, name: string, theme: "light" | "dark") {
  await page.evaluate((t) => {
    document.documentElement.setAttribute("data-theme", t);
    for (const el of document.querySelectorAll<HTMLElement>("*")) if (el.scrollLeft) el.scrollLeft = 0;
  }, theme);
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/${name}-${theme}.png` });
  console.log("captured", `${name}-${theme}`);
}

async function api(page: Page, path: string, body?: unknown, method = "POST", key?: string) {
  const res = await page.evaluate(
    async ({ url, body, method, key }) => {
      const r = await fetch(url, {
        method,
        credentials: "include",
        headers: { "Content-Type": "application/json", "Idempotency-Key": key ?? crypto.randomUUID() },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      return { status: r.status, json: await r.json().catch(() => null) };
    },
    { url: `${API}${path}`, body, method, key },
  );
  if (res.status >= 300) throw new Error(`${method} ${path} -> ${res.status}: ${JSON.stringify(res.json)}`);
  return res.json as never;
}

const TODAY = (() => {
  const n = new Date();
  return [n.getFullYear(), String(n.getMonth() + 1).padStart(2, "0"), String(n.getDate()).padStart(2, "0")].join("-");
})();

async function seed(page: Page) {
  const accounts = (await api(page, "/gl/accounts", undefined, "GET")) as Array<{
    id: number;
    code: string;
  }>;
  const revenue = accounts.find((a) => a.code === "4100")!;
  const bank = accounts.find((a) => a.code === "1120")!;

  const existing = (await api(
    page,
    "/subledger/ar/partners?include_inactive=true",
    undefined,
    "GET",
  )) as Array<{ id: number; customer_code: string | null }>;
  const found = existing.find((p) => p.customer_code === "SHOTCUST");
  const customer =
    found ??
    ((await api(page, "/subledger/ar/partners", {
      name: "Bralirwa Distributors",
      customer_code: "SHOTCUST",
    })) as { id: number });

  async function invoice(description: string, quantity: string, price: string, key: string) {
    return (await api(
      page,
      "/subledger/ar/documents",
      {
        kind: "invoice",
        partner_id: customer.id,
        document_date: TODAY,
        description,
        lines: [
          { description, quantity, unit_price: price, gl_account_id: revenue.id },
        ],
      },
      "POST",
      key,
    )) as { id: number };
  }

  const settled = await invoice("Rugari Red 750ml — March order", "48", "8500", "shot-gap-inv-1");
  await invoice("Rugari White 750ml — March order", "24", "7800", "shot-gap-inv-2");

  const receipt = (await api(
    page,
    "/subledger/ar/documents",
    {
      kind: "settlement",
      partner_id: customer.id,
      document_date: TODAY,
      description: "Part payment on account",
      amount: "150000",
      cash_account_id: bank.id,
      instrument_type: "bank",
    },
    "POST",
    "shot-gap-rct-1",
  )) as { id: number };

  // The allocation the detail screen shows, with Unallocate live beside it.
  await api(
    page,
    "/subledger/ar/allocations",
    {
      partner_id: customer.id,
      allocation_date: TODAY,
      pairs: [
        { debit_document_id: settled.id, credit_document_id: receipt.id, amount: "150000" },
      ],
    },
    "POST",
    "shot-gap-alc-1",
  );

  // And one reversed, so the listing shows both statuses.
  const mistake = await invoice("Keyed against the wrong customer", "6", "8500", "shot-gap-inv-3");
  await api(
    page,
    `/subledger/ar/documents/${mistake.id}/reverse`,
    { on_date: TODAY, reason: "Keyed against the wrong customer" },
    "POST",
    "shot-gap-rev-1",
  );

  // --- the AP side ---------------------------------------------------------------------
  // Seeded too, because a screenshot of an empty supplier listing proves the route compiles
  // and nothing else (rule 13). The AP screen is the same component with `role="ap"`, and
  // the thing worth seeing on it is that the owner's names for these documents differ from
  // AR's — a credit note is a "Return to supplier" on the purchase side.
  const expense = accounts.find((a) => a.code === "6990")!;
  const suppliers = (await api(
    page,
    "/subledger/ap/partners?include_inactive=true",
    undefined,
    "GET",
  )) as Array<{ id: number; supplier_code: string | null }>;
  const supplierFound = suppliers.find((p) => p.supplier_code === "SHOTSUPP");
  const supplier =
    supplierFound ??
    ((await api(page, "/subledger/ap/partners", {
      name: "Kigali Glass & Packaging",
      supplier_code: "SHOTSUPP",
    })) as { id: number });

  async function apDocument(
    kind: string,
    description: string,
    quantity: string,
    price: string,
    key: string,
  ) {
    return (await api(
      page,
      "/subledger/ap/documents",
      {
        kind,
        partner_id: supplier.id,
        document_date: TODAY,
        description,
        lines: [{ description, quantity, unit_price: price, gl_account_id: expense.id }],
      },
      "POST",
      key,
    )) as { id: number };
  }

  const supplierInvoice = await apDocument(
    "invoice",
    "Bottles and closures — March delivery",
    "5000",
    "42",
    "shot-gap-apin-1",
  );
  await apDocument("credit_note", "Cracked bottles returned", "120", "42", "shot-gap-apcn-1");
  const payment = (await api(
    page,
    "/subledger/ap/documents",
    {
      kind: "settlement",
      partner_id: supplier.id,
      document_date: TODAY,
      description: "Payment on account",
      amount: "100000",
      cash_account_id: bank.id,
      instrument_type: "bank",
    },
    "POST",
    "shot-gap-appy-1",
  )) as { id: number };
  await api(
    page,
    "/subledger/ap/allocations",
    {
      partner_id: supplier.id,
      allocation_date: TODAY,
      pairs: [
        {
          debit_document_id: payment.id,
          credit_document_id: supplierInvoice.id,
          amount: "100000",
        },
      ],
    },
    "POST",
    "shot-gap-apalc-1",
  );

  return { arDocumentId: settled.id, apDocumentId: supplierInvoice.id };
}

async function main() {
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined,
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await login(page);
  const { arDocumentId, apDocumentId } = await seed(page);

  for (const theme of ["light", "dark"] as const) {
    await page.goto(`${BASE}/ar/documents`);
    await page.waitForSelector("h1:has-text('Customer documents')");
    await page.waitForSelector('[data-testid="document-total"]');
    await shoot(page, "1-ar-documents", theme);

    await page.goto(`${BASE}/ar/documents/${arDocumentId}`);
    await page.waitForSelector('[data-testid="allocation-amount"]');
    await shoot(page, "2-ar-document-detail", theme);

    await page.goto(`${BASE}/ap/documents`);
    await page.waitForSelector("h1:has-text('Supplier documents')");
    await page.waitForSelector('[data-testid="document-total"]');
    await shoot(page, "3-ap-documents", theme);

    await page.goto(`${BASE}/ap/documents/${apDocumentId}`);
    await page.waitForSelector('[data-testid="allocation-amount"]');
    await shoot(page, "4-ap-document-detail", theme);
  }
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
