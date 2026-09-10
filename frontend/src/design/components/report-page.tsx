"use client";

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
