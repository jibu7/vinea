import { type Page, expect, test } from "@playwright/test";
import {
  PRIMARY_EMAIL,
  clearDrafts,
  login,
  pageFetch,
  pickCombobox,
  pickDate,
} from "./support/fixtures";

/**
 * A document with no rate typed on it books at the rate the `exchange_rates` table holds for
 * its **own date**.
 *
 * `document-screen.tsx` sends `exchange_rate: form.exchangeRate || null`, so leaving the field
 * empty is not a validation error — it hands the decision to the server, and `rate_for`
 * (`app/kernel/money.py`) takes "the greatest `valid_from <= date`". That is the path a real
 * operator uses: rates are maintained once on the Foreign currency screen and nobody retypes
 * one per invoice.
 *
 * No e2e covered it. Every foreign-currency document in the acceptance tape types its booking
 * rate on the screen, which is the stronger *UI* assertion but leaves the lookup to
 * `tests/kernel`; `gl-inline-errors.spec.ts` covers only the refusal when no rate exists at
 * all. Recorded as a known gap in `docs/p4-final-report.md`.
 *
 * Two rates are seeded and the document is posted twice, once either side of the later one. A
 * test that seeds a single rate and finds it proves only that some rate was reachable — it
 * passes just as well against an implementation that takes the newest row and ignores the
 * date. Moving only the *document's* date and requiring the booked figure to follow is what
 * makes `valid_from <= date` the thing under test.
 *
 * CANNOT SEE: a rate dated *after* the document (the table is append-only by `valid_from`, so
 * a later correction is a new row and the old documents keep their booked rate — that is
 * kernel-level and tested there), and the rounding-difference line, which needs a rate that
 * does not divide evenly.
 */

const CURRENCY = "USD";
const LINE_ACCOUNT = "4100"; // Sales Revenue — ordinary income, non-control
const UNIT_PRICE = 1_000;

/** `valid_from` dates either side of the document's, and the rates that go with them. */
const EARLIER = { daysAgo: 20, rate: 1_100 };
const DOCUMENT = { daysAgo: 10 };
const LATER = { daysAgo: 5, rate: 1_400 };

/** Same invoice, same empty rate field, two document dates — the booked base follows the date.
 * `wrong` is the other seeded rate, asserted absent so a figure that happens to appear
 * elsewhere on the screen cannot carry the test. */
const CASES = [
  {
    label: "dated between the two, it books at the earlier",
    daysAgo: DOCUMENT.daysAgo,
    rate: EARLIER.rate,
    wrong: LATER.rate,
  },
  {
    label: "dated after both, it books at the later",
    daysAgo: 2,
    rate: LATER.rate,
    wrong: EARLIER.rate,
  },
] as const;

function isoDaysAgo(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString().slice(0, 10);
}

async function seedRate(
  page: Page,
  code: string,
  validFrom: string,
  rate: number,
): Promise<void> {
  const currencies = (await pageFetch(page, "/gl/currencies")).json as Array<{
    id: number;
    code: string;
  }>;
  const currency = currencies.find((c) => c.code === code);
  if (!currency) throw new Error(`no seeded currency ${code}`);
  const res = await pageFetch(page, "/gl/exchange-rates", {
    method: "POST",
    body: {
      currency_id: currency.id,
      valid_from: validFrom,
      rate: String(rate),
    },
  });
  // A rate already on that date is fine — the row we need exists either way. Anything else is
  // a real failure and must not be swallowed into a confusing assertion later.
  if (!res.ok && res.status !== 409) {
    throw new Error(
      `seeding ${code} @ ${validFrom} failed: ${res.status} ${JSON.stringify(res.json)}`,
    );
  }
}

test.describe("a document with no rate typed books at its own date's rate", () => {
  for (const scenario of CASES) {
    test(scenario.label, async ({ page }) => {
      // Three screens, two API seeds and a post; the 45s default does not stretch to it.
      test.setTimeout(120_000);

      await login(page, PRIMARY_EMAIL);
      const suffix = String(Date.now()).slice(-6);
      const code = `RATE${suffix}`;

      await test.step("two rates, either side of the document's date", async () => {
        await seedRate(
          page,
          CURRENCY,
          isoDaysAgo(EARLIER.daysAgo),
          EARLIER.rate,
        );
        await seedRate(page, CURRENCY, isoDaysAgo(LATER.daysAgo), LATER.rate);
      });

      await test.step("a customer to bill", async () => {
        await page.goto("/maintenance/customers");
        await page.waitForSelector("h1:has-text('Customers')");
        await page.getByRole("button", { name: "New customer" }).click();
        const dialog = page.getByRole("dialog");
        await dialog.getByLabel("Customer code").fill(code);
        await dialog
          .getByLabel("Name", { exact: true })
          .fill(`Dated rate ${suffix}`);
        await dialog
          .getByRole("button", { name: "Create", exact: true })
          .click();
        await expect(
          page.getByRole("dialog").filter({ hasText: `Dated rate ${suffix}` }),
        ).toBeVisible();
        await page.keyboard.press("Escape");
      });

      await test.step("an invoice in USD with the exchange rate left empty", async () => {
        await clearDrafts(page);
        await page.goto("/ar/invoices/new");
        await page.waitForSelector("h1:has-text('Invoice')");
        await pickCombobox(page, "Customer", code);
        await page
          .getByLabel("Description", { exact: true })
          .fill(`Dated rate ${suffix}`);
        await pickDate(page, "Document date", isoDaysAgo(scenario.daysAgo));
        await pickCombobox(page, "Currency", CURRENCY);

        // The point of the test: nothing is typed here, so the server has to date the lookup.
        await expect(page.getByLabel("Exchange rate")).toHaveValue("");

        await page.getByRole("button", { name: "Account, row 1" }).click();
        await page.locator("[cmdk-item]").first().waitFor({ state: "visible" });
        await page.keyboard.type(LINE_ACCOUNT);
        await page
          .locator(`[cmdk-item]:has-text("${LINE_ACCOUNT}")`)
          .first()
          .click();
        await page.getByLabel("Unit price, row 1").fill(String(UNIT_PRICE));
        await page.getByRole("button", { name: /^Post/ }).click();
        await page.waitForURL(/\/gl\/entries\/\d+/, { timeout: 30_000 });
      });

      await test.step("the entry carries the rate effective on that date", async () => {
        // Debit and Credit on this screen are base amounts (RWF, no decimals), so the figure is
        // the booked rate made visible. Asserting the number, not just that a rate was found.
        const expected = (UNIT_PRICE * scenario.rate).toLocaleString("en-US");
        await expect(page.getByText(expected).first()).toBeVisible({
          timeout: 15_000,
        });

        const wrong = (UNIT_PRICE * scenario.wrong).toLocaleString("en-US");
        await expect(page.getByText(wrong)).toHaveCount(0);
      });
    });
  }
});
