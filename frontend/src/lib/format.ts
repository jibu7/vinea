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

/**
 * A quantity, to its unit's decimal places — grouped like money, but no code and no symbol,
 * because a count of bottles is not an amount of anything.
 *
 * `decimalPlaces` comes from the `uoms` master (`Uom.decimal_places`: EA = 0, KG = 3), never
 * hard-coded, for the same reason `formatMoney` takes the currency's. The step-6 review made
 * this a standard: every screen's e2e reads at least one quantity off the page *formatted*,
 * because the raw `NUMERIC(20,6)` string ("12.000000") is what the column holds and is not
 * what anyone should be shown.
 */
export function formatQuantity(
  amount: number,
  decimalPlaces: number,
  opts: { locale?: string } = {},
): string {
  const { locale = APP_LOCALE } = opts;
  const rounded = roundHalfUp(amount, decimalPlaces);
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: decimalPlaces,
    maximumFractionDigits: decimalPlaces,
  }).format(rounded);
}

/** dd/MM/yyyy, per the owner's correction — explicit digits, not locale dateStyle. */
export function formatDate(date: Date | string, opts: { locale?: string } = {}): string {
  const { locale = APP_LOCALE } = opts;
  const d = typeof date === "string" ? new Date(date) : date;
  return new Intl.DateTimeFormat(locale, { day: "2-digit", month: "2-digit", year: "numeric" }).format(d);
}


/** Drops a decimal string's insignificant trailing zeros for display in an editable field.
 * The API returns Postgres NUMERIC scale verbatim ("500000.000000", "2.5000000000"), which
 * is correct on the wire and unreadable in a text input. Never use this on money for
 * display — `formatMoney` owns that, with the currency's real decimal places. */
export function trimDecimalString(value: string): string {
  if (!value.includes(".")) return value;
  return value.replace(/0+$/, "").replace(/\.$/, "");
}

/** The " · " that separates a code from its name in every picker label, and a document
 * number from its date. Punctuation, not copy — there is nothing here to translate — but
 * it cannot sit in JSX as a literal or a template string without tripping
 * `react/jsx-no-literals`, and the AR/AP screens already build these labels in callbacks
 * rather than inline. This is the same thing, named once. */
export const DOT = " · ";

/** Joins with `DOT`, skipping parts that are absent — `dotted("1000", null)` is "1000". */
export function dotted(...parts: Array<string | number | null | undefined>): string {
  return parts.filter((p) => p !== null && p !== undefined && p !== "").join(DOT);
}

/**
 * Today as `YYYY-MM-DD`, in the **viewer's own calendar**.
 *
 * `new Date().toISOString().slice(0, 10)` — the shape used throughout the app before this
 * existed — renders the date in UTC, so east of Greenwich it reads *yesterday* for the first
 * hours of every day: at 00:30 in Kigali (UTC+2) it is 22:30 UTC on the day before, and a
 * document defaults to the wrong date. Local calendar parts have no such seam. New screens
 * use this; the existing local copies are a sweep of their own.
 */
export function todayIso(now: Date = new Date()): string {
  return [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("-");
}

/**
 * The range a report opens on: the first of the current month, to today.
 *
 * Not "everything ever" — the movement and transaction endpoints both require a range, and a
 * report that opens on an unbounded one asks the server to reconstruct every move a company
 * has ever posted before anyone has said what they wanted. Both ends are editable.
 */
export function monthToDateIso(now: Date = new Date()): { from: string; to: string } {
  return { from: todayIso(new Date(now.getFullYear(), now.getMonth(), 1)), to: todayIso(now) };
}
