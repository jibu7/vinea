import { expect, test } from "@playwright/test";
import {
  CREDIT_ACCOUNT_CODE,
  DEBIT_ACCOUNT_CODE,
  PRIMARY_EMAIL,
  accountIdByCode,
  login,
  pageFetch,
} from "./support/fixtures";

/**
 * The document workspace autosaves a draft with a generated UUID and posts using that UUID
 * as the `Idempotency-Key` (frontend/src/app/(shell)/gl/journal-batches/new/page.tsx), so a
 * retried post — network hiccup, doubled click — replays instead of duplicating. Exercising
 * a *retry* means resending the exact same request, which the UI itself won't do once it has
 * navigated away on success — so this drives the API directly via the page's own `fetch`
 * (see `pageFetch`), reusing the authenticated session's cookies exactly as the app does.
 */
test.describe("journal posting is idempotent on retry", () => {
  // PATH: POST /gl/journal-entries twice with one key, through the page's own `fetch`.
  // CANNOT SEE: the UI's half of it — that the draft UUID is what the screens send as the
  // key. The document and allocation screens send theirs, and only `tests/subledger`
  // asserts a replay through them.
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

    const first = await pageFetch(page, "/gl/journal-entries", {
      method: "POST",
      body,
      headers: { "Idempotency-Key": idempotencyKey },
    });
    expect(first.status).toBe(201);
    const firstEntry = first.json as { id: number; number: string };

    const second = await pageFetch(page, "/gl/journal-entries", {
      method: "POST",
      body,
      headers: { "Idempotency-Key": idempotencyKey },
    });
    expect(second.status).toBe(200); // replay, not a new document
    const secondEntry = second.json as { id: number; number: string };

    expect(secondEntry.id).toBe(firstEntry.id);
    expect(secondEntry.number).toBe(firstEntry.number);

    // And it shows up exactly once, not twice, in that account's transactions.
    const list = await pageFetch(
      page,
      `/gl/accounts/${debitAccountId}/transactions?date_from=${body.entry_date}&date_to=${body.entry_date}`,
    );
    expect(list.ok).toBe(true);
    const { items } = list.json as { items: Array<{ description?: string }> };
    const matches = items.filter((item) => item.description === body.description);
    expect(matches).toHaveLength(1);
  });

  // PATH: the same key with a changed body → 409. CANNOT SEE: what the *screen* does with
  // that conflict, which is a toast path no spec drives.
  test("the same key with a different body is refused, not silently posted", async ({ page }) => {
    await login(page, PRIMARY_EMAIL);
    const debitAccountId = await accountIdByCode(page, DEBIT_ACCOUNT_CODE);
    const creditAccountId = await accountIdByCode(page, CREDIT_ACCOUNT_CODE);
    const idempotencyKey = `e2e-idempotency-conflict-${Date.now()}`;

    const first = await pageFetch(page, "/gl/journal-entries", {
      method: "POST",
      body: {
        entry_date: new Date().toISOString().slice(0, 10),
        description: `E2E idempotency conflict A ${idempotencyKey}`,
        lines: [
          { gl_account_id: debitAccountId, debit: "500", credit: "0" },
          { gl_account_id: creditAccountId, debit: "0", credit: "500" },
        ],
      },
      headers: { "Idempotency-Key": idempotencyKey },
    });
    expect(first.status).toBe(201);

    const second = await pageFetch(page, "/gl/journal-entries", {
      method: "POST",
      body: {
        entry_date: new Date().toISOString().slice(0, 10),
        description: `E2E idempotency conflict B ${idempotencyKey}`,
        lines: [
          { gl_account_id: debitAccountId, debit: "999", credit: "0" },
          { gl_account_id: creditAccountId, debit: "0", credit: "999" },
        ],
      },
      headers: { "Idempotency-Key": idempotencyKey },
    });
    expect(second.status).toBe(409);
    const error = second.json as { code: string };
    expect(error.code).toBe("idempotency_key_reused");
  });
});
