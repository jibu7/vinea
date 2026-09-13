"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowLeft, Download, Printer } from "lucide-react";
import { Button } from "./button";
import { ThemeToggle } from "./theme-toggle";

/**
 * The chrome every report shares — the P3 pattern (print layout + CSV) factored out rather
 * than copied into twelve screens.
 *
 * The printed page is deliberately not the screen: filters, nav and buttons are `print:hidden`
 * and a masthead carrying the company, report name and as-of date is `hidden print:block`, so
 * a printout says what it is and when it was run without the reader having to be told.
 */
export function ReportPage({
  title,
  subtitle,
  companyName,
  asOfLabel,
  backHref = "/",
  filters,
  onExportCsv,
  children,
}: {
  title: string;
  subtitle?: string;
  companyName?: string;
  /** Printed under the title — the as-of date or range this report was run for. */
  asOfLabel?: string;
  backHref?: string;
  filters?: React.ReactNode;
  onExportCsv?: () => void;
  children: React.ReactNode;
}) {
  const t = useTranslations("reports");

  return (
    <div className="flex min-h-screen flex-col print:bg-white print:text-black" data-density="dense">
      <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3 print:hidden">
        <div className="flex items-center gap-3">
          <Link
            href={backHref}
            className="text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]"
            aria-label={t("back")}
          >
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <h1 className="font-display text-lg font-semibold">{title}</h1>
            {subtitle && <p className="text-xs text-[var(--vinea-ink-muted)]">{subtitle}</p>}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {onExportCsv && (
            <Button variant="secondary" onClick={onExportCsv} className="gap-1.5 text-xs">
              <Download className="size-3.5" /> {t("exportCsv")}
            </Button>
          )}
          <Button variant="primary" onClick={() => window.print()} className="gap-1.5 text-xs">
            <Printer className="size-3.5" /> {t("print")}
          </Button>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 overflow-auto px-6 py-6 print:overflow-visible print:px-0 print:py-0">
        <div className="mx-auto max-w-6xl space-y-4 print:max-w-none print:space-y-3">
          {/* Print-only masthead: a page that leaves the screen must carry its own context. */}
          <div className="hidden border-b-2 border-black pb-3 print:block">
            {companyName && <p className="text-lg font-semibold">{companyName}</p>}
            <p className="text-base">{title}</p>
            {asOfLabel && <p className="text-xs">{asOfLabel}</p>}
          </div>

          {filters && <div className="print:hidden">{filters}</div>}
          {children}
        </div>
      </main>
    </div>
  );
}

/** A report's data panel — bordered on screen, borderless in print so tables run edge to edge. */
export function ReportPanel({ children }: { children: React.ReactNode }) {
  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 print:rounded-none print:border-0 print:bg-white print:p-0">
      {children}
    </section>
  );
}

/**
 * Cursor paging state for a server-paged report.
 *
 * The cursor is the last row id of the page before, never an offset (ADR-11), so a row
 * posted while someone is paging cannot shift rows onto a page they have already seen. The
 * trade is that there is no page count and no jumping to page 7 — only forward and back,
 * which is what `ReportPager` renders.
 *
 * `filter` wraps a filter's state setter so changing it starts the chain again: page 3 of
 * the old filter is not page 3 of the new one, and a cursor from one filtered set means
 * nothing in another.
 */
export function useCursorPager() {
  const [cursors, setCursors] = useState<Array<number | null>>([null]);
  const filter = useCallback(
    <T,>(set: (value: T) => void) =>
      (value: T) => {
        set(value);
        setCursors([null]);
      },
    [],
  );
  return {
    cursor: cursors[cursors.length - 1],
    hasPrevious: cursors.length > 1,
    previous: useCallback(() => setCursors((c) => c.slice(0, -1)), []),
    next: useCallback((cursor: number | null) => setCursors((c) => [...c, cursor]), []),
    filter,
  };
}

/** Forward/back for a cursor-paged report, with the row count on this page. Hidden in print:
 * a printout is the page you were looking at, and "Next" means nothing on paper. */
export function ReportPager({
  count,
  hasPrevious,
  hasNext,
  onPrevious,
  onNext,
}: {
  count: number;
  hasPrevious: boolean;
  hasNext: boolean;
  onPrevious: () => void;
  onNext: () => void;
}) {
  const t = useTranslations("reports");
  return (
    <div className="flex items-center justify-between pt-3 print:hidden">
      <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("showing", { count })}</p>
      <div className="flex gap-2">
        <Button variant="secondary" disabled={!hasPrevious} onClick={onPrevious} className="text-xs">
          {t("previousPage")}
        </Button>
        <Button variant="secondary" disabled={!hasNext} onClick={onNext} className="text-xs">
          {t("nextPage")}
        </Button>
      </div>
    </div>
  );
}
