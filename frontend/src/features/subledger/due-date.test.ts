import { describe, expect, it } from "vitest";
import { dueDateFor } from "./due-date";
import type { PaymentTerms } from "./types";

function terms(patch: Partial<PaymentTerms>): PaymentTerms {
  return {
    id: 1,
    code: "T",
    name: "T",
    due_basis: "days_from_document_date",
    due_days: 0,
    due_day_of_month: null,
    discount_percent: "0",
    discount_days: 0,
    is_active: true,
    ...patch,
  };
}

/** These cases mirror `PaymentTerms.due_date` in backend/app/models/partner.py — if that
 * changes, this drifts and the form's default silently disagrees with the server's. */
describe("dueDateFor", () => {
  it("adds days to the document date", () => {
    expect(dueDateFor(terms({ due_days: 30 }), "2026-03-10")).toBe("2026-04-09");
  });

  it("adds days to the end of the document's month", () => {
    expect(dueDateFor(terms({ due_basis: "days_from_end_of_month", due_days: 30 }), "2026-03-10")).toBe(
      "2026-04-30",
    );
  });

  it("crosses a month boundary correctly from end of month", () => {
    expect(dueDateFor(terms({ due_basis: "days_from_end_of_month", due_days: 0 }), "2026-02-05")).toBe(
      "2026-02-28",
    );
  });

  it("takes the fixed day this month when the document is on or before it", () => {
    expect(
      dueDateFor(terms({ due_basis: "fixed_day_of_month", due_day_of_month: 25 }), "2026-03-10"),
    ).toBe("2026-03-25");
  });

  it("rolls to next month when the document is past the fixed day", () => {
    expect(
      dueDateFor(terms({ due_basis: "fixed_day_of_month", due_day_of_month: 5 }), "2026-03-10"),
    ).toBe("2026-04-05");
  });

  it("clamps a fixed day past the end of a short month", () => {
    expect(
      dueDateFor(terms({ due_basis: "fixed_day_of_month", due_day_of_month: 31 }), "2026-02-01"),
    ).toBe("2026-02-28");
  });

  it("rolls December into the next year", () => {
    expect(
      dueDateFor(terms({ due_basis: "fixed_day_of_month", due_day_of_month: 5 }), "2026-12-10"),
    ).toBe("2027-01-05");
  });

  it("falls back to the document date with no terms", () => {
    expect(dueDateFor(undefined, "2026-03-10")).toBe("2026-03-10");
  });
});
