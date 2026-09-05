/**
 * Locale-aware money/date formatting. `decimalPlaces` must come from the
 * `currencies` master (RWF = 0, USD = 2) — never assume 2dp.
 */

export type CurrencyLike = {
  code: string;
  decimalPlaces: number;
};

export const RWF: CurrencyLike = { code: "RWF", decimalPlaces: 0 };
export const USD: CurrencyLike = { code: "USD", decimalPlaces: 2 };

const symbolByCode: Record<string, string> = {
  RWF: "FRW",
  USD: "$",
  EUR: "\u20ac",
};

/** Half-up rounding to `decimalPlaces`, avoiding binary float banker's rounding. */
export function roundHalfUp(amount: number, decimalPlaces: number): number {
  const factor = 10 ** decimalPlaces;
  return Math.sign(amount) * Math.round(Math.abs(amount) * factor) / factor;
}

export function formatMoney(
  amount: number,
  currency: CurrencyLike,
  opts: { locale?: string; showCode?: boolean } = {},
): string {
  const { locale = "en-RW", showCode = true } = opts;
  const rounded = roundHalfUp(amount, currency.decimalPlaces);
  const formatted = new Intl.NumberFormat(locale, {
    minimumFractionDigits: currency.decimalPlaces,
    maximumFractionDigits: currency.decimalPlaces,
  }).format(rounded);
  const symbol = symbolByCode[currency.code] ?? currency.code;
  return showCode ? `${symbol} ${formatted}` : formatted;
}

export function formatDate(
  date: Date | string,
  opts: { locale?: string; dateStyle?: Intl.DateTimeFormatOptions["dateStyle"] } = {},
): string {
  const { locale = "en-RW", dateStyle = "medium" } = opts;
  const d = typeof date === "string" ? new Date(date) : date;
  return new Intl.DateTimeFormat(locale, { dateStyle }).format(d);
}
