"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowLeft, CheckCircle2, Download, Printer } from "lucide-react";
import { Button } from "@/design/components/button";
import { DatePicker } from "@/design/components/date-picker";
import { Field } from "@/design/components/input";
import { Money } from "@/design/components/money";
import { StatusChip } from "@/design/components/status-chip";
import { Table, THead, TBody, TR, TH, TD } from "@/design/components/table";
import { ThemeToggle } from "@/design/components/theme-toggle";
import { useMe } from "@/features/auth/hooks";
import { useBranches, useCurrencies, useProjects, useTrialBalance } from "@/features/gl/hooks";
import { exportToCsv } from "@/lib/csv";
import { formatDate } from "@/lib/format";

export default function TrialBalanceReportPage() {
  const t = useTranslations("gl");
  const { data: me } = useMe();

  const [asOfDate, setAsOfDate] = useState<Date>(() => new Date());
  const [branchId, setBranchId] = useState("");
  const [projectId, setProjectId] = useState("");

  const branches = useBranches();
  const projects = useProjects();
  const currencies = useCurrencies();
  const baseCurrency = useMemo(() => currencies.data?.find((c) => c.is_base), [currencies.data]);

  const asOfStr = asOfDate.toISOString().slice(0, 10);
  const { data: tb, isLoading } = useTrialBalance({
    as_of: asOfStr,
    branch_id: branchId ? Number(branchId) : null,
    project_id: projectId ? Number(projectId) : null,
  });

  const currencyLike = baseCurrency
    ? { code: baseCurrency.code, decimalPlaces: baseCurrency.decimal_places, symbol: baseCurrency.symbol }
    : undefined;

  const totalDebit = Number(tb?.total_debit ?? 0);
  const totalCredit = Number(tb?.total_credit ?? 0);
  const difference = totalDebit - totalCredit;

  const isProjectFiltered = Boolean(projectId);
  const selectedBranch = branches.data?.find((b) => String(b.id) === branchId);
  const selectedProject = projects.data?.find((p) => String(p.id) === projectId);

  function handleExportCsv() {
    if (!tb) return;
    const headers = ["Account Code", "Account Name", "Class", "Debit", "Credit", "Net"];
    const rows = tb.rows.map((r) => [
      r.code,
      r.name,
      r.class,
      Number(r.debit),
      Number(r.credit),
      Number(r.net),
    ]);
    // Append totals row
    rows.push([
      "TOTALS",
      isProjectFiltered ? "Project Slice (Subset)" : "Total Footing",
      "",
      totalDebit,
      totalCredit,
      difference,
    ]);
    const fileSuffix = asOfStr;
    exportToCsv(`trial-balance-${fileSuffix}`, headers, rows);
  }

  return (
    <div className="flex min-h-screen flex-col print:bg-white print:text-black" data-density="dense">
      {/* Screen Header */}
      <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3 print:hidden">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]" aria-label="Back">
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <h1 className="font-display text-lg font-semibold">{t("trialBalanceReport")}</h1>
            <p className="text-xs text-[var(--vinea-ink-muted)]">
              {t("asOf")} {formatDate(asOfDate)}
            </p>
          </div>
          {tb && (
            isProjectFiltered ? (
              <StatusChip tone="neutral">{t("projectSubsetChip")}</StatusChip>
            ) : (
              <StatusChip tone={tb.foots ? "success" : "danger"}>
                {tb.foots ? (
                  <span className="inline-flex items-center gap-1">
                    <CheckCircle2 className="size-3" /> {t("foots")}
                  </span>
                ) : (
                  <span>{t("unbalanced")}</span>
                )}
              </StatusChip>
            )
          )}
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
          {/* Print-Only Formal Report Header */}
          <div className="hidden border-b-2 border-black pb-4 print:block">
            <div className="flex items-baseline justify-between">
              <div>
                <h1 className="text-2xl font-bold tracking-tight">{me?.company?.name ?? "Vinea ERP"}</h1>
                <p className="text-base font-semibold">{t("trialBalanceReport")}</p>
              </div>
              <div className="text-right text-xs">
                <p>
                  <strong>{t("asOf")}:</strong> {formatDate(asOfDate)}
                </p>
                <p>
                  <strong>Printed:</strong> {formatDate(new Date())}
                </p>
              </div>
            </div>
            <div className="mt-2 flex gap-4 text-xs text-gray-600">
              {selectedBranch && (
                <span>
                  <strong>Branch:</strong> {selectedBranch.code} - {selectedBranch.name}
                </span>
              )}
              {selectedProject && (
                <span>
                  <strong>Project:</strong> {selectedProject.code} - {selectedProject.name}
                </span>
              )}
              {isProjectFiltered && (
                <span className="italic font-medium">({t("projectSubsetNote")})</span>
              )}
            </div>
          </div>

          {/* Interactive Filter Bar (Hidden on print) */}
          <div className="grid grid-cols-1 gap-4 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-5 sm:grid-cols-3 print:hidden">
            <Field label={t("asOf")}>
              <DatePicker value={asOfDate} onValueChange={setAsOfDate} />
            </Field>
            <Field label={t("branch")}>
              <select
                value={branchId}
                onChange={(e) => setBranchId(e.target.value)}
                className="h-10 w-full rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] bg-[var(--vinea-surface-raised)] px-3 text-sm text-[var(--vinea-ink)]"
              >
                <option value="">{t("allBranches")}</option>
                {(branches.data ?? []).map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.code} · {b.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("project")}>
              <select
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
                className="h-10 w-full rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] bg-[var(--vinea-surface-raised)] px-3 text-sm text-[var(--vinea-ink)]"
              >
                <option value="">{t("allProjects")}</option>
                {(projects.data ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.code} · {p.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          {/* Table */}
          {isLoading ? (
            <div className="flex h-48 items-center justify-center text-sm text-[var(--vinea-ink-subtle)]">
              Loading report data…
            </div>
          ) : !tb || tb.rows.length === 0 ? (
            <div className="rounded-[var(--radius-card)] border border-dashed border-[var(--vinea-border)] p-12 text-center text-sm text-[var(--vinea-ink-subtle)]">
              No balances found as of {formatDate(asOfDate)}.
            </div>
          ) : (
            <div className="overflow-auto rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] print:border print:border-gray-300">
              <Table>
                <THead className="print:bg-gray-100">
                  <TR>
                    <TH className="w-24">Code</TH>
                    <TH>Account Name</TH>
                    <TH className="w-28">Class</TH>
                    <TH className="w-36 text-right">Debit</TH>
                    <TH className="w-36 text-right">Credit</TH>
                    <TH className="w-36 text-right">Net</TH>
                  </TR>
                </THead>
                <TBody className="divide-y divide-[var(--vinea-border)] print:divide-gray-300">
                  {tb.rows.map((row) => {
                    const debit = Number(row.debit);
                    const credit = Number(row.credit);
                    const net = Number(row.net);
                    return (
                      <TR key={row.gl_account_id} className="break-inside-avoid">
                        <TD className="font-mono text-xs font-medium text-[var(--vinea-brand)] print:text-black">
                          {row.code}
                        </TD>
                        <TD className="font-medium text-[var(--vinea-ink)] print:text-black">
                          {row.name}
                        </TD>
                        <TD>
                          <span className="rounded-full bg-[var(--vinea-surface-sunken)] px-2 py-0.5 text-[11px] capitalize text-[var(--vinea-ink-muted)] print:border print:border-gray-300 print:text-black">
                            {row.class}
                          </span>
                        </TD>
                        <TD className="text-right font-mono text-sm print:text-black">
                          {debit > 0 && currencyLike ? <Money amount={debit} currency={currencyLike} /> : "—"}
                        </TD>
                        <TD className="text-right font-mono text-sm print:text-black">
                          {credit > 0 && currencyLike ? <Money amount={credit} currency={currencyLike} /> : "—"}
                        </TD>
                        <TD className="text-right font-mono text-sm font-semibold print:text-black">
                          {net !== 0 && currencyLike ? <Money amount={net} currency={currencyLike} /> : "—"}
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>

              {/* Totals Footer */}
              <div className="flex items-center justify-between border-t-2 border-[var(--vinea-border)] bg-[var(--vinea-surface-sunken)]/60 px-6 py-4 font-mono text-sm print:border-t-2 print:border-black print:bg-gray-50">
                <div>
                  <span className="font-sans text-xs font-semibold uppercase tracking-wider text-[var(--vinea-ink-muted)] print:text-black">
                    {isProjectFiltered ? t("subsetDifference") : t("difference")}:
                  </span>{" "}
                  <span
                    className={`font-semibold ${
                      isProjectFiltered
                        ? "text-[var(--vinea-ink)]"
                        : difference === 0
                          ? "text-[var(--vinea-success)] print:text-black"
                          : "text-[var(--vinea-danger)] print:text-black"
                    }`}
                  >
                    {currencyLike && <Money amount={difference} currency={currencyLike} />}
                  </span>
                </div>
                <div className="flex gap-8">
                  <div>
                    <span className="font-sans text-xs text-[var(--vinea-ink-subtle)] print:text-black">
                      {isProjectFiltered ? t("subsetDebit") : t("totalDebit")}:
                    </span>{" "}
                    <span className="font-semibold">
                      {currencyLike && <Money amount={totalDebit} currency={currencyLike} />}
                    </span>
                  </div>
                  <div>
                    <span className="font-sans text-xs text-[var(--vinea-ink-subtle)] print:text-black">
                      {isProjectFiltered ? t("subsetCredit") : t("totalCredit")}:
                    </span>{" "}
                    <span className="font-semibold">
                      {currencyLike && <Money amount={totalCredit} currency={currencyLike} />}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
