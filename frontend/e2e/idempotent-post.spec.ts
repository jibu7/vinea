import { expect, test } from "@playwright/test";
import {
  API_BASE,
  CREDIT_ACCOUNT_CODE,
  DEBIT_ACCOUNT_CODE,
  PRIMARY_EMAIL,
  accountIdByCode,
  login,
} from "./support/fixtures";

/**
 * The document workspace autosaves a draft with a generated UUID and posts using that UUID
 * as the `Idempotency-Key` (frontend/src/app/(shell)/gl/journal-batches/new/page.tsx), so a
 * retried post — network hiccup, doubled click — replays instead of duplicating. Exercising
 * a *retry* means resending the exact same request, which the UI itself won't do once it has
 * navigated away on success — so this drives the API directly with page.request, which shares
 * the authenticated session's cookies with the browser context.
 */
test.describe("journal posting is idempotent on retry", () => {
  test("resending the same Idempotency-Key + body replays the original entry", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);

    const debitAccountId = await accountIdByCode(page, DEBIT_ACCOUNT_CODE);
    const creditAccountId = await accountIdByCode(page, CREDIT_ACCOUNT_CODE);
    const idempotencyKey = `e2e-idempotency-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const body = {
      entry_date: new Date().toISOString().slice(0, 10),
      description: `E2E idempotency check ${idempotencyKey}`,
      lines: [
        { gl_account_id: debitAccountId, debit: "12345", credit: "0" },
        { gl_account_id: creditAccountId, debit: "0", credit: "12345" },
      ],
    };

    const first = await page.request.post(`${API_BASE}/gl/journal-entries`, {
      data: body,
      headers: { "Idempotency-Key": idempotencyKey },
    });
    expect(first.status()).toBe(201);
    const firstEntry = await first.json();

    const second = await page.request.post(`${API_BASE}/gl/journal-entries`, {
      data: body,
      headers: { "Idempotency-Key": idempotencyKey },
    });
    expect(second.status()).toBe(200); // replay, not a new document
    const secondEntry = await second.json();

    expect(secondEntry.id).toBe(firstEntry.id);
    expect(secondEntry.number).toBe(firstEntry.number);

    // And it shows up exactly once, not twice, in that account's transactions.
    const list = await page.request.get(
      `${API_BASE}/gl/accounts/${debitAccountId}/transactions?date_from=${body.entry_date}&date_to=${body.entry_date}`,
    );
    expect(list.ok()).toBe(true);
    const { items } = await list.json();
    const matches = (items as Array<{ description?: string }>).filter(
      (item) => item.description === body.description,
    );
    expect(matches).toHaveLength(1);
  });

  test("the same key with a different body is refused, not silently posted", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    const debitAccountId = await accountIdByCode(page, DEBIT_ACCOUNT_CODE);
    const creditAccountId = await accountIdByCode(page, CREDIT_ACCOUNT_CODE);
    const idempotencyKey = `e2e-idempotency-conflict-${Date.now()}`;

    const first = await page.request.post(`${API_BASE}/gl/journal-entries`, {
      data: {
        entry_date: new Date().toISOString().slice(0, 10),
        description: `E2E idempotency conflict A ${idempotencyKey}`,
        lines: [
          { gl_account_id: debitAccountId, debit: "500", credit: "0" },
          { gl_account_id: creditAccountId, debit: "0", credit: "500" },
        ],
      },
      headers: { "Idempotency-Key": idempotencyKey },
    });
    expect(first.status()).toBe(201);

    const second = await page.request.post(`${API_BASE}/gl/journal-entries`, {
      data: {
        entry_date: new Date().toISOString().slice(0, 10),
        description: `E2E idempotency conflict B ${idempotencyKey}`,
        lines: [
          { gl_account_id: debitAccountId, debit: "999", credit: "0" },
          { gl_account_id: creditAccountId, debit: "0", credit: "999" },
        ],
      },
      headers: { "Idempotency-Key": idempotencyKey },
    });
    expect(second.status()).toBe(409);
    const error = await second.json();
    expect(error.code).toBe("idempotency_key_reused");
  });
});
