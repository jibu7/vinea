"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowLeft, Download, Printer, Search } from "lucide-react";
import { Button } from "@/design/components/button";
import { Table, THead, TBody, TR, TH, TD } from "@/design/components/table";
import { ThemeToggle } from "@/design/components/theme-toggle";
import { useMe } from "@/features/auth/hooks";
import { useAccounts } from "@/features/gl/hooks";
import { controlTypeLabel, type GLAccount } from "@/features/gl/types";
import { exportToCsv } from "@/lib/csv";
import { formatDate } from "@/lib/format";

export default function ChartOfAccountsReportPage() {
  const t = useTranslations("gl");
  const { data: me } = useMe();
  const { data: accounts, isLoading } = useAccounts();

  const [query, setQuery] = useState("");
  const [classFilter, setClassFilter] = useState<string>("all");
  const [postableOnly, setPostableOnly] = useState(false);
  const [activeOnly, setActiveOnly] = useState(true);

  const filtered = useMemo(() => {
    return (accounts ?? []).filter((acc) => {
      if (activeOnly && !acc.is_active) return false;
      if (postableOnly && !acc.is_postable) return false;
      if (classFilter !== "all" && acc.class !== classFilter) return false;
      if (query.trim()) {
        const q = query.toLowerCase();
        return acc.code.toLowerCase().includes(q) || acc.name.toLowerCase().includes(q);
      }
      return true;
    });
  }, [accounts, query, classFilter, postableOnly, activeOnly]);

  // Counts must reflect the active filter (search/class/postable/active-only), not the
  // unfiltered account list — otherwise the header and the table disagree on "how many".
  const summary = useMemo(() => {
    const total = filtered.length;
    const postable = filtered.filter((a) => a.is_postable).length;
    const control = filtered.filter((a) => a.is_control).length;
    return { total, postable, control };
  }, [filtered]);

  function handleExportCsv() {
    const headers = ["Account Code", "Account Name", "Class", "Type", "Control Type", "Status"];
    const rows = filtered.map((a: GLAccount) => [
      a.code,
      a.name,
      a.class,
      a.is_postable ? "Postable" : "Header",
      controlTypeLabel(a.control_type),
      a.is_active ? "Active" : "Inactive",
    ]);
    exportToCsv(`chart-of-accounts-${new Date().toISOString().slice(0, 10)}`, headers, rows);
  }

  return (
    <div className="flex min-h-screen flex-col print:bg-white print:text-black" data-density="dense">
      {/* Screen Header */}
      <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3 print:hidden">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]">
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <h1 className="font-display text-lg font-semibold">{t("chartOfAccountsReport")}</h1>
            <p className="text-xs text-[var(--vinea-ink-muted)]">
              {filtered.length} accounts ({summary.postable} postable, {summary.control} control)
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={handleExportCsv} className="gap-1.5 text-xs">
            <Download className="size-3.5" /> {t("exportCsv")}
          </Button>
          <Button variant="primary" onClick={() => window.print()} className="gap-1.5 text-xs">
            <Printer className="size-3.5" /> {t("printReport")}
          </Button>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 overflow-auto px-6 py-6 print:overflow-visible print:px-0 print:py-0">
        <div className="mx-auto max-w-5xl space-y-6 print:max-w-none print:space-y-4">
          {/* Formal Print-Only Report Header */}
          <div className="hidden border-b-2 border-black pb-4 print:block">
            <div className="flex items-baseline justify-between">
              <div>
                <h1 className="text-2xl font-bold tracking-tight">{me?.company?.name ?? "Vinea ERP"}</h1>
                <p className="text-base font-semibold">{t("chartOfAccountsReport")}</p>
              </div>
              <div className="text-right text-xs">
                <p>
                  <strong>Total Accounts:</strong> {filtered.length}
                </p>
                <p>
                  <strong>Printed:</strong> {formatDate(new Date())}
                </p>
              </div>
            </div>
          </div>

          {/* Filters Bar (print:hidden) */}
          <div className="flex flex-wrap items-center justify-between gap-4 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 print:hidden">
            <div className="flex flex-1 items-center gap-3 min-w-64">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--vinea-ink-subtle)]" />
                <input
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search by code or name…"
                  className="h-10 w-full rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] bg-[var(--vinea-surface-raised)] pl-9 pr-3 text-sm text-[var(--vinea-ink)]"
                />
              </div>
              <select
                value={classFilter}
                onChange={(e) => setClassFilter(e.target.value)}
                className="h-10 rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] bg-[var(--vinea-surface-raised)] px-3 text-sm text-[var(--vinea-ink)]"
              >
                <option value="all">{t("allClasses")}</option>
                <option value="asset">Asset</option>
                <option value="liability">Liability</option>
                <option value="equity">Equity</option>
                <option value="income">Income</option>
                <option value="expense">Expense</option>
              </select>
            </div>
            <div className="flex items-center gap-4 text-xs">
              <label className="flex items-center gap-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={postableOnly}
                  onChange={(e) => setPostableOnly(e.target.checked)}
                  className="size-4 accent-[var(--vinea-brand)]"
                />
                {t("postableOnly")}
              </label>
              <label className="flex items-center gap-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={activeOnly}
                  onChange={(e) => setActiveOnly(e.target.checked)}
                  className="size-4 accent-[var(--vinea-brand)]"
                />
                {t("activeAccountsOnly")}
              </label>
            </div>
          </div>

          {/* Listing Table */}
          {isLoading ? (
            <div className="flex h-48 items-center justify-center text-sm text-[var(--vinea-ink-subtle)]">
              Loading chart of accounts…
            </div>
          ) : filtered.length === 0 ? (
            <div className="rounded-[var(--radius-card)] border border-dashed border-[var(--vinea-border)] p-12 text-center text-sm text-[var(--vinea-ink-subtle)]">
              No accounts match the current filter.
            </div>
          ) : (
            <div className="overflow-auto rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] print:border print:border-gray-300">
              <Table>
                <THead className="print:bg-gray-100">
                  <TR>
                    <TH className="w-24">Code</TH>
                    <TH>Account Name</TH>
                    <TH className="w-28">Class</TH>
                    <TH className="w-28">Type</TH>
                    <TH className="w-32">Control Type</TH>
                    <TH className="w-24 text-right">Status</TH>
                  </TR>
                </THead>
                <TBody className="divide-y divide-[var(--vinea-border)] print:divide-gray-300">
                  {filtered.map((acc) => (
                    <TR key={acc.id} className="break-inside-avoid">
                      <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)] print:text-black">
                        {acc.code}
                      </TD>
                      <TD className={`font-medium ${acc.is_postable ? "text-[var(--vinea-ink)]" : "font-semibold text-[var(--vinea-ink)]"} print:text-black`}>
                        {acc.name}
                      </TD>
                      <TD>
                        <span className="rounded-full bg-[var(--vinea-surface-sunken)] px-2 py-0.5 text-[11px] capitalize text-[var(--vinea-ink-muted)] print:border print:border-gray-300 print:text-black">
                          {acc.class}
                        </span>
                      </TD>
                      <TD className="text-xs text-[var(--vinea-ink-muted)] print:text-black">
                        {acc.is_postable ? "Postable" : "Header"}
                      </TD>
                      <TD className="text-xs text-[var(--vinea-ink-subtle)] print:text-black">
                        {acc.control_type ? (
                          <span className="rounded bg-[var(--vinea-surface-sunken)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--vinea-ink)]">
                            {controlTypeLabel(acc.control_type)}
                          </span>
                        ) : (
                          "—"
                        )}
                      </TD>
                      <TD className="text-right text-xs">
                        <span
                          className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                            acc.is_active
                              ? "bg-[var(--vinea-success-soft)] text-[var(--vinea-success)] print:text-black"
                              : "bg-[var(--vinea-danger-soft)] text-[var(--vinea-danger)] print:text-black"
                          }`}
                        >
                          {acc.is_active ? "Active" : "Inactive"}
                        </span>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
