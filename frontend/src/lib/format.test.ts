import { describe, expect, it } from "vitest";
import { RWF, USD, dotted, formatDate, formatMoney, formatQuantity, monthToDateIso, roundHalfUp, todayIso, trimDecimalString } from "./format";

describe("formatMoney", () => {
  it("shows RWF with no decimal places", () => {
    expect(formatMoney(1234.56, RWF)).toBe("RWF 1,235");
  });

  it("shows USD with two decimal places, using the currency symbol", () => {
    expect(formatMoney(1234.5, USD)).toBe("$ 1,234.50");
  });

  it("formats negative amounts", () => {
    expect(formatMoney(-500.4, RWF)).toBe("RWF -500");
  });

  it("can omit the currency label", () => {
    expect(formatMoney(1234.5, USD, { showCode: false })).toBe("1,234.50");
  });

  it("rounds half up, not to even, at the RWF whole-number boundary", () => {
    expect(formatMoney(1.5, RWF)).toBe("RWF 2");
    expect(formatMoney(2.5, RWF)).toBe("RWF 3");
  });

  it("rounds half up at the USD cent boundary", () => {
    expect(formatMoney(0.125, USD)).toBe("$ 0.13");
  });
});

describe("roundHalfUp", () => {
  it("rounds .5 away from zero regardless of sign", () => {
    expect(roundHalfUp(1.5, 0)).toBe(2);
    expect(roundHalfUp(-1.5, 0)).toBe(-2);
  });

  it("rounds to the requested number of decimal places", () => {
    expect(roundHalfUp(19.995, 2)).toBeCloseTo(20, 5);
    expect(roundHalfUp(1234.56, 0)).toBe(1235);
  });
});

describe("formatDate", () => {
  it("renders dd/MM/yyyy", () => {
    expect(formatDate("2026-01-05")).toBe("05/01/2026");
  });

  it("accepts a Date instance", () => {
    expect(formatDate(new Date(Date.UTC(2026, 8, 30)))).toBe("30/09/2026");
  });
});

describe("trimDecimalString", () => {
  it("drops insignificant trailing zeros", () => {
    expect(trimDecimalString("500000.000000")).toBe("500000");
    expect(trimDecimalString("2.5000000000")).toBe("2.5");
    expect(trimDecimalString("0.0500000000")).toBe("0.05");
  });

  it("leaves integers and already-trimmed values alone", () => {
    expect(trimDecimalString("0")).toBe("0");
    expect(trimDecimalString("1200")).toBe("1200");
    expect(trimDecimalString("1200.75")).toBe("1200.75");
  });
});

describe("dotted", () => {
  it("joins parts with the separator every picker label uses", () => {
    expect(dotted("1000", "Bank")).toBe("1000 · Bank");
  });

  it("skips absent parts rather than leaving a dangling separator", () => {
    expect(dotted("JE-0001", null)).toBe("JE-0001");
    expect(dotted(undefined, "Only")).toBe("Only");
    expect(dotted("", "Only")).toBe("Only");
  });

  it("takes numbers, which document numbers sometimes are", () => {
    expect(dotted(42, "Kigali")).toBe("42 · Kigali");
  });
});

describe("formatQuantity", () => {
  it("renders to the unit's decimal places, grouped, with no currency", () => {
    expect(formatQuantity(6, 0)).toBe("6");
    expect(formatQuantity(12, 0)).toBe("12");
    expect(formatQuantity(1234.5, 2)).toBe("1,234.50");
    expect(formatQuantity(0.0283168, 3)).toBe("0.028");
  });

  it("rounds half-up, the way the ledger does, not banker's", () => {
    expect(formatQuantity(2.5, 0)).toBe("3");
    expect(formatQuantity(0.125, 2)).toBe("0.13");
  });

  it("never prints NUMERIC scale", () => {
    // The wire value of a 12-unit line is "12.000000"; a screen must never show it.
    expect(formatQuantity(Number("12.000000"), 0)).toBe("12");
  });
});

describe("todayIso", () => {
  it("reads the viewer's own calendar, not UTC", () => {
    // 00:30 local. `toISOString().slice(0, 10)` — the shape these helpers replace — renders
    // this in UTC, which east of Greenwich is the *previous* day: in Kigali (UTC+2) that is
    // 22:30 on the 4th, so a screen defaulting to "today" would offer the 4th on the 5th.
    // Constructed from local parts, so the assertion holds in whatever zone CI runs in.
    const earlyMorning = new Date(2026, 2, 5, 0, 30);
    expect(todayIso(earlyMorning)).toBe("2026-03-05");
  });

  it("zero-pads month and day", () => {
    expect(todayIso(new Date(2026, 0, 9, 14, 0))).toBe("2026-01-09");
  });
});

describe("monthToDateIso", () => {
  it("runs from the first of the current month to the given day", () => {
    expect(monthToDateIso(new Date(2026, 8, 13, 9, 0))).toEqual({
      from: "2026-09-01",
      to: "2026-09-13",
    });
  });

  it("does not roll back into the previous month on the first, early", () => {
    // The same UTC seam, at the one date where it changes the *month* as well as the day.
    expect(monthToDateIso(new Date(2026, 8, 1, 0, 30))).toEqual({
      from: "2026-09-01",
      to: "2026-09-01",
    });
  });
});
