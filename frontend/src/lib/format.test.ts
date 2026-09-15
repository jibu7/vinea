import { describe, expect, it } from "vitest";
import {
  RWF,
  USD,
  dotted,
  formatDate,
  formatMoney,
  formatQuantity,
  fromLocalIsoDate,
  monthToDateIso,
  nowIso,
  roundHalfUp,
  toLocalIsoDate,
  todayIso,
  trimDecimalString,
} from "./format";

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

describe("toLocalIsoDate against the UTC bug", () => {
  it("disagrees with the UTC rendering at the seam, and is the one that is right", () => {
    // The suite runs under TZ=Africa/Kigali (see vitest.setup.ts), which is UTC+2 all year.
    // 00:30 on the 5th of March, local. In UTC that instant is 22:30 on the **4th**, so the
    // expression this helper replaced would offer the 4th to somebody keying a document on
    // the 5th — a different accounting period at a month boundary, a different fiscal year
    // at a year boundary.
    //
    // Asserted against the old expression rather than only against the right answer, so the
    // test says what it is preventing. It fails on an implementation that returns the UTC
    // slice: that is what makes it worth having.
    const seam = new Date(2026, 2, 5, 0, 30);
    expect(seam.toISOString().slice(0, 10)).toBe("2026-03-04");
    expect(toLocalIsoDate(seam)).toBe("2026-03-05");
    expect(toLocalIsoDate(seam)).not.toBe(seam.toISOString().slice(0, 10));
  });

  it("agrees with UTC away from the seam", () => {
    // 23:30 local is 21:30 UTC — the same calendar day. Kigali is UTC+2, so local minus two
    // hours can only ever land on today or yesterday, never tomorrow; the "tomorrow"
    // direction of this defect belongs to zones west of Greenwich. Worth pinning, because a
    // reader who only saw the case above could reasonably think the two always differ.
    const evening = new Date(2026, 2, 5, 23, 30);
    expect(evening.toISOString().slice(0, 10)).toBe("2026-03-05");
    expect(toLocalIsoDate(evening)).toBe("2026-03-05");
  });

  it("round-trips a calendar date through fromLocalIsoDate", () => {
    // `new Date("2026-03-10")` parses as UTC midnight; west of Greenwich that is the 9th, so a
    // draft saved on the 10th would reopen on the 9th. The pair has to be each other's mirror.
    expect(toLocalIsoDate(fromLocalIsoDate("2026-03-10"))).toBe("2026-03-10");
    expect(fromLocalIsoDate("2026-03-10").getDate()).toBe(10);
    expect(fromLocalIsoDate("2026-03-10").getHours()).toBe(0);
  });

  it("reads a draft written before the helpers existed", () => {
    // Older drafts stored a full `toISOString()` instant. Its calendar day is not its first
    // ten characters in every zone, so it is parsed as an instant and then read locally.
    expect(toLocalIsoDate(fromLocalIsoDate("2026-03-09T22:00:00.000Z"))).toBe("2026-03-10");
  });
});

describe("nowIso", () => {
  it("is a sortable instant, not a calendar date", () => {
    // Drafts evict in `updatedAt` order, so this one is UTC on purpose — the distinction the
    // helper names exists so that banning toISOString does not push someone into using a date
    // where an instant belongs.
    const stamp = nowIso(new Date(2026, 2, 5, 0, 30));
    expect(stamp).toBe("2026-03-04T22:30:00.000Z");
    expect(stamp > nowIso(new Date(2026, 2, 5, 0, 29))).toBe(true);
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
