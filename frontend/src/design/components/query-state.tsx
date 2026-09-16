"use client";

import { useTranslations } from "next-intl";
import { cn } from "@/lib/cn";

/**
 * What a listing, report or enquiry shows when it has no rows to show — and the distinction
 * this component exists to make.
 *
 * **"No rows" and "the request failed" are different facts, and a screen that renders the same
 * sentence for both is lying about one of them.** P4 shipped six defects of this shape and
 * rule 13 is written against them; P6 found two more — step 6's F5 and step 8's F-3 were the
 * same defect twice, an empty state that could not tell a full subledger behind a broken query
 * from an empty one. The reader's next move is opposite in the two cases: "nothing to report"
 * ends the investigation, "the server refused this" begins it.
 *
 * So a failed query says **what the service said**. Not a generic apology: the API's own
 * `message` is the one sentence that names the cause — a closed period, a missing permission,
 * a 500 — and it is the difference between a bug report and a shrug. A fallback is offered for
 * the case where the failure never reached the API layer (a dropped connection has no body).
 *
 * `null` when there is something to render, so the caller keeps its table:
 *
 *     <QueryState query={orders} isEmpty={rows.length === 0} /> ?? <Table>…</Table>
 *
 * is not how JSX reads, so in practice:
 *
 *     {state ? state : <Table>…</Table>}
 *
 * Loading is here too rather than in the caller, because the three states are one decision:
 * a screen that checks `isError` after it has already returned the loading line shows
 * "Loading…" for ever on a query that failed.
 */
export interface QueryLike {
  isError: boolean;
  isLoading: boolean;
  error?: unknown;
}

export function queryErrorMessage(error: unknown, fallback: string): string {
  const message = (error as { message?: string } | null | undefined)?.message;
  return message && message.trim() !== "" ? message : fallback;
}

export function QueryState({
  query,
  isEmpty,
  empty,
  loading,
  testId = "query-state",
  className,
}: {
  query: QueryLike;
  /** Whether the *successful* result has no rows. */
  isEmpty: boolean;
  /** The no-rows line, when this screen has a better one than "Nothing to report". */
  empty?: string;
  /** The loading line, when this screen has a better one than "Loading…". */
  loading?: string;
  /** Prefix for the two testids: `<testId>-error` and `<testId>-empty`. */
  testId?: string;
  className?: string;
}): React.ReactElement | null {
  const t = useTranslations("common.queryState");

  if (query.isError) {
    return (
      <p
        className={cn("py-8 text-center text-xs text-[var(--vinea-danger)]", className)}
        data-testid={`${testId}-error`}
        role="status"
      >
        {queryErrorMessage(query.error, t("failed"))}
      </p>
    );
  }
  if (query.isLoading) {
    return (
      <p
        className={cn("py-8 text-center text-xs text-[var(--vinea-ink-subtle)]", className)}
        data-testid={`${testId}-loading`}
      >
        {loading ?? t("loading")}
      </p>
    );
  }
  if (isEmpty) {
    return (
      <p
        className={cn("py-8 text-center text-xs text-[var(--vinea-ink-subtle)]", className)}
        data-testid={`${testId}-empty`}
      >
        {empty ?? t("noRows")}
      </p>
    );
  }
  return null;
}
