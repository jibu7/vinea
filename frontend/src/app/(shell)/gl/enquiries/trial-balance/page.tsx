"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { ArrowLeft, CheckCircle2, AlertTriangle, ArrowUpRight } from "lucide-react";
import { DatePicker } from "@/design/components/date-picker";
import { Field } from "@/design/components/input";
import { Money } from "@/design/components/money";
import { StatusChip } from "@/design/components/status-chip";
import { Table, THead, TBody, TR, TH, TD } from "@/design/components/table";
import { ThemeToggle } from "@/design/components/theme-toggle";
import { useBranches, useCurrencies, useProjects, useTrialBalance } from "@/features/gl/hooks";
import { formatDate } from "@/lib/format";

export default function TrialBalanceEnquiryPage() {
  const t = useTranslations("gl");
  const router = useRouter();

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
  // TODO(P5): Inter-branch transfers will need the same treatment (suppressing foots and labelling as filtered subset). Leave branch as-is for now.

  return (
    <div className="flex min-h-screen flex-col" data-density="dense">
      <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]" aria-label="Back">
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <h1 className="font-display text-lg font-semibold">{t("trialBalanceEnquiry")}</h1>
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
                  <span className="inline-flex items-center gap-1">
                    <AlertTriangle className="size-3" /> {t("unbalanced")}
                  </span>
                )}
              </StatusChip>
            )
          )}
        </div>
        <div className="flex items-center gap-3">
          <Link
            href="/gl/enquiries/account"
            className="flex items-center gap-1 rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] px-3 py-1.5 text-xs font-medium text-[var(--vinea-ink)] hover:bg-[var(--vinea-surface-sunken)]"
          >
            {t("accountEnquiry")} <ArrowUpRight className="size-3" />
          </Link>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 overflow-auto px-6 py-6">
        <div className="mx-auto max-w-5xl space-y-6">
          {/* Filter Bar */}
          <div className="grid grid-cols-1 gap-4 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-5 sm:grid-cols-3">
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

          {/* KPI Summary Cards */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4">
              <p className="text-xs font-medium uppercase tracking-wider text-[var(--vinea-ink-subtle)]">
                {isProjectFiltered ? t("subsetDebit") : t("totalDebit")}
              </p>
              <div className="mt-1 font-mono text-xl font-semibold">
                {currencyLike && <Money amount={totalDebit} currency={currencyLike} />}
              </div>
            </div>
            <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4">
              <p className="text-xs font-medium uppercase tracking-wider text-[var(--vinea-ink-subtle)]">
                {isProjectFiltered ? t("subsetCredit") : t("totalCredit")}
              </p>
              <div className="mt-1 font-mono text-xl font-semibold">
                {currencyLike && <Money amount={totalCredit} currency={currencyLike} />}
              </div>
            </div>
            <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4">
              <p className="text-xs font-medium uppercase tracking-wider text-[var(--vinea-ink-subtle)]">
                {isProjectFiltered ? t("subsetDifference") : t("difference")}
              </p>
              <div
                className={`mt-1 font-mono text-xl font-semibold ${
                  isProjectFiltered
                    ? "text-[var(--vinea-ink)]"
                    : difference === 0
                      ? "text-[var(--vinea-success)]"
                      : "text-[var(--vinea-danger)]"
                }`}
              >
                {currencyLike && <Money amount={difference} currency={currencyLike} />}
              </div>
              {isProjectFiltered && (
                <p className="mt-1 text-[11px] text-[var(--vinea-ink-subtle)]">{t("projectSubsetNote")}</p>
              )}
            </div>
          </div>

          {/* Trial Balance Table */}
          {isLoading ? (
            <div className="flex h-48 items-center justify-center text-sm text-[var(--vinea-ink-subtle)]">
              Loading trial balance…
            </div>
          ) : !tb || tb.rows.length === 0 ? (
            <div className="rounded-[var(--radius-card)] border border-dashed border-[var(--vinea-border)] p-12 text-center text-sm text-[var(--vinea-ink-subtle)]">
              No balances found as of {formatDate(asOfDate)}.
            </div>
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH className="w-24">Code</TH>
                  <TH>Account Name</TH>
                  <TH className="w-28">Class</TH>
                  <TH className="w-36 text-right">Debit</TH>
                  <TH className="w-36 text-right">Credit</TH>
                  <TH className="w-36 text-right">Net</TH>
                </TR>
              </THead>
              <TBody>
                {tb.rows.map((row) => {
                  const debit = Number(row.debit);
                  const credit = Number(row.credit);
                  const net = Number(row.net);
                  return (
                    <TR
                      key={row.gl_account_id}
                      onClick={() => router.push(`/gl/enquiries/account?accountId=${row.gl_account_id}`)}
                      className="cursor-pointer hover:bg-[var(--vinea-surface-sunken)]/60"
                    >
                      <TD className="font-mono text-xs font-medium text-[var(--vinea-brand)]">
                        {row.code}
                      </TD>
                      <TD className="font-medium text-[var(--vinea-ink)]">
                        {row.name}
                      </TD>
                      <TD>
                        <span className="rounded-full bg-[var(--vinea-surface-sunken)] px-2 py-0.5 text-[11px] capitalize text-[var(--vinea-ink-muted)]">
                          {row.class}
                        </span>
                      </TD>
                      <TD className="text-right font-mono text-sm">
                        {debit > 0 && currencyLike ? <Money amount={debit} currency={currencyLike} /> : "—"}
                      </TD>
                      <TD className="text-right font-mono text-sm">
                        {credit > 0 && currencyLike ? <Money amount={credit} currency={currencyLike} /> : "—"}
                      </TD>
                      <TD className="text-right font-mono text-sm font-semibold">
                        {net !== 0 && currencyLike ? <Money amount={net} currency={currencyLike} /> : "—"}
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          )}
        </div>
      </main>
    </div>
  );
}
