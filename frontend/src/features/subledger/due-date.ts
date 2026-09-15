import { fromLocalIsoDate, toLocalIsoDate } from "@/lib/format";
import type { DueBasis, PaymentTerms } from "./types";

/**
 * **One frame throughout, and it is the local one.**
 *
 * This module is pure calendar arithmetic — a `YYYY-MM-DD` in, a `YYYY-MM-DD` out — so it never
 * needs to know what time it is anywhere. What it does need is to build and render its dates in
 * the *same* frame. It used to do that in UTC end to end, parsing `T00:00:00Z` and rendering
 * with `toISOString()`, which was self-consistent and correct.
 *
 * Issue #30's sweep nearly broke it: swapping only the renderer for a local one left UTC
 * midnights being read with local calendar parts, which west of Greenwich is the previous day.
 * Kigali could not have caught it — at UTC+2 a UTC midnight is 02:00 on the same date — which
 * is the same reason the original defect survived a UTC-pinned test suite.
 *
 * So both ends moved instead. Local in, local arithmetic, local out; the results are identical
 * to the UTC version in every zone, and there is no seam left to get wrong.
 */
function lastDayOfMonth(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate();
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
  const parsed = fromLocalIsoDate(documentDate);
  if (Number.isNaN(parsed.getTime())) return documentDate;

  const basis: DueBasis = terms.due_basis;
  if (basis === "days_from_document_date") {
    parsed.setDate(parsed.getDate() + terms.due_days);
    return toLocalIsoDate(parsed);
  }
  if (basis === "days_from_end_of_month") {
    const end = new Date(
      parsed.getFullYear(),
      parsed.getMonth(),
      lastDayOfMonth(parsed.getFullYear(), parsed.getMonth()),
    );
    end.setDate(end.getDate() + terms.due_days);
    return toLocalIsoDate(end);
  }
  // Fixed day of month: this month if the document is on or before that day, else the next.
  const target = terms.due_day_of_month ?? 1;
  let year = parsed.getFullYear();
  let month = parsed.getMonth();
  if (parsed.getDate() > target) {
    month += 1;
    if (month > 11) {
      month = 0;
      year += 1;
    }
  }
  return toLocalIsoDate(new Date(year, month, Math.min(target, lastDayOfMonth(year, month))));
}
