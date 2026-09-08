import { describe, expect, it } from "vitest";
import { RWF, USD, formatDate, formatMoney, roundHalfUp } from "./format";

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
