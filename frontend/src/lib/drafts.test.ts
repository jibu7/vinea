import { beforeEach, describe, expect, it } from "vitest";
import {
  MAX_TOTAL_DRAFT_BYTES,
  clearDraft,
  draftKey,
  loadDraft,
  newDraftId,
  saveDraft,
} from "./drafts";

const MODULE = "subledger.ar.invoice";
const COMPANY_X = 1;
const COMPANY_Y = 2;
const USER_A = 10;
const USER_B = 20;

function draft(data: unknown, updatedAt = "2026-09-09T10:00:00.000Z") {
  return { draftId: "fixed-uuid", updatedAt, data };
}

beforeEach(() => {
  window.localStorage.clear();
});

describe("draft key isolation", () => {
  it("includes both the company id and the user id", () => {
    const key = draftKey(MODULE, COMPANY_X, USER_A);

    expect(key).toContain(String(COMPANY_X));
    expect(key).toContain(String(USER_A));
    expect(key).toBe(`vinea.draft.${MODULE}.${COMPANY_X}.${USER_A}`);
  });

  it("does not return user A's draft to user B in the same company", () => {
    saveDraft(MODULE, COMPANY_X, USER_A, draft({ secret: "A's half-typed invoice" }));

    expect(loadDraft(MODULE, COMPANY_X, USER_B)).toBeNull();
    expect(loadDraft(MODULE, COMPANY_X, USER_A)?.data).toEqual({
      secret: "A's half-typed invoice",
    });
  });

  it("does not return user A's company X draft when they switch to company Y", () => {
    saveDraft(MODULE, COMPANY_X, USER_A, draft({ partner: "Customer in X" }));

    expect(loadDraft(MODULE, COMPANY_Y, USER_A)).toBeNull();
  });

  it("keeps drafts for the same user in different companies apart", () => {
    saveDraft(MODULE, COMPANY_X, USER_A, draft({ where: "X" }));
    saveDraft(MODULE, COMPANY_Y, USER_A, draft({ where: "Y" }));

    expect(loadDraft(MODULE, COMPANY_X, USER_A)?.data).toEqual({ where: "X" });
    expect(loadDraft(MODULE, COMPANY_Y, USER_A)?.data).toEqual({ where: "Y" });
  });

  it("keeps different modules apart for the same user and company", () => {
    saveDraft("subledger.ar.invoice", COMPANY_X, USER_A, draft({ kind: "invoice" }));
    saveDraft("subledger.ar.receipt", COMPANY_X, USER_A, draft({ kind: "receipt" }));

    expect(loadDraft("subledger.ar.invoice", COMPANY_X, USER_A)?.data).toEqual({
      kind: "invoice",
    });
    expect(loadDraft("subledger.ar.receipt", COMPANY_X, USER_A)?.data).toEqual({
      kind: "receipt",
    });
  });

  it("clears only the draft it was asked to clear", () => {
    saveDraft(MODULE, COMPANY_X, USER_A, draft({ v: "A" }));
    saveDraft(MODULE, COMPANY_X, USER_B, draft({ v: "B" }));

    clearDraft(MODULE, COMPANY_X, USER_A);

    expect(loadDraft(MODULE, COMPANY_X, USER_A)).toBeNull();
    expect(loadDraft(MODULE, COMPANY_X, USER_B)?.data).toEqual({ v: "B" });
  });
});

describe("draft size cap", () => {
  const big = (n: number) => ({ blob: "x".repeat(n) });

  function totalDraftBytes(): number {
    let total = 0;
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const key = window.localStorage.key(i)!;
      if (!key.startsWith("vinea.draft.")) continue;
      total += key.length + (window.localStorage.getItem(key) ?? "").length;
    }
    return total;
  }

  it("stays under the cap by evicting, rather than growing without bound", () => {
    for (let i = 0; i < 12; i += 1) {
      saveDraft(`module.${i}`, COMPANY_X, USER_A, draft(big(60_000), `2026-09-0${i % 9}T00:00:00Z`));
    }

    expect(totalDraftBytes()).toBeLessThanOrEqual(MAX_TOTAL_DRAFT_BYTES);
  });

  it("evicts the oldest draft first and never the one being written", () => {
    saveDraft("module.old", COMPANY_X, USER_A, draft(big(200_000), "2026-01-01T00:00:00Z"));
    saveDraft("module.mid", COMPANY_X, USER_A, draft(big(200_000), "2026-06-01T00:00:00Z"));
    saveDraft("module.new", COMPANY_X, USER_A, draft(big(200_000), "2026-09-01T00:00:00Z"));

    // The one just written survives; the oldest is gone.
    expect(loadDraft("module.new", COMPANY_X, USER_A)).not.toBeNull();
    expect(loadDraft("module.old", COMPANY_X, USER_A)).toBeNull();
  });

  it("leaves a single oversized draft saved rather than evicting it to satisfy the cap", () => {
    // The draft you are typing in is the one you cannot afford to lose.
    saveDraft(MODULE, COMPANY_X, USER_A, draft(big(MAX_TOTAL_DRAFT_BYTES + 1000)));

    expect(loadDraft(MODULE, COMPANY_X, USER_A)).not.toBeNull();
  });
});

describe("draft ids", () => {
  it("mints a distinct id per draft, which becomes the Idempotency-Key", () => {
    expect(newDraftId()).not.toBe(newDraftId());
  });

  it("survives a round trip so a retried post replays rather than duplicates", () => {
    saveDraft(MODULE, COMPANY_X, USER_A, draft({ any: "thing" }));

    expect(loadDraft(MODULE, COMPANY_X, USER_A)?.draftId).toBe("fixed-uuid");
  });
});
