/**
 * Locale-aware money/date formatting. `decimalPlaces` and `symbol` must come
 * from the `currencies` master (RWF = 0dp, USD = 2dp) — never hard-code either.
 * Locale is "en-GB" throughout (day/month/year ordering matches Rwanda convention);
 * a per-company locale override can replace this default once that setting exists.
 */

export const APP_LOCALE = "en-GB";

export type CurrencyLike = {
  code: string;
  decimalPlaces: number;
  symbol?: string | null;
};

export const RWF: CurrencyLike = { code: "RWF", decimalPlaces: 0, symbol: null };
export const USD: CurrencyLike = { code: "USD", decimalPlaces: 2, symbol: "$" };

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
  const { locale = APP_LOCALE, showCode = true } = opts;
  const rounded = roundHalfUp(amount, currency.decimalPlaces);
  const formatted = new Intl.NumberFormat(locale, {
    minimumFractionDigits: currency.decimalPlaces,
    maximumFractionDigits: currency.decimalPlaces,
  }).format(rounded);
  const label = currency.symbol || currency.code;
  return showCode ? `${label} ${formatted}` : formatted;
}

/** dd/MM/yyyy, per the owner's correction — explicit digits, not locale dateStyle. */
export function formatDate(date: Date | string, opts: { locale?: string } = {}): string {
  const { locale = APP_LOCALE } = opts;
  const d = typeof date === "string" ? new Date(date) : date;
  return new Intl.DateTimeFormat(locale, { day: "2-digit", month: "2-digit", year: "numeric" }).format(d);
}

