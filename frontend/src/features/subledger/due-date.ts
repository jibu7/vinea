import type { DueBasis, PaymentTerms } from "./types";

function lastDayOfMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
}

function iso(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/**
 * Mirrors `PaymentTerms.due_date` in backend/app/models/partner.py, for the *default* the
 * document form shows as soon as terms are chosen.
 *
 * It is a display default and nothing more: the form sends `due_date` only when the operator
 * edits it, so an untouched document is dated by the server's own derivation. Keeping the
 * client out of the record is the same rule the allocation preview follows — this exists so
 * the field is not blank until Post, not so the browser decides when money is owed.
 */
export function dueDateFor(terms: PaymentTerms | undefined, documentDate: string): string {
  if (!terms || !documentDate) return documentDate;
  const parsed = new Date(`${documentDate}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return documentDate;

  const basis: DueBasis = terms.due_basis;
  if (basis === "days_from_document_date") {
    parsed.setUTCDate(parsed.getUTCDate() + terms.due_days);
    return iso(parsed);
  }
  if (basis === "days_from_end_of_month") {
    const end = new Date(
      Date.UTC(parsed.getUTCFullYear(), parsed.getUTCMonth(), lastDayOfMonth(parsed.getUTCFullYear(), parsed.getUTCMonth())),
    );
    end.setUTCDate(end.getUTCDate() + terms.due_days);
    return iso(end);
  }
  // Fixed day of month: this month if the document is on or before that day, else the next.
  const target = terms.due_day_of_month ?? 1;
  let year = parsed.getUTCFullYear();
  let month = parsed.getUTCMonth();
  if (parsed.getUTCDate() > target) {
    month += 1;
    if (month > 11) {
      month = 0;
      year += 1;
    }
  }
  return iso(new Date(Date.UTC(year, month, Math.min(target, lastDayOfMonth(year, month)))));
}
