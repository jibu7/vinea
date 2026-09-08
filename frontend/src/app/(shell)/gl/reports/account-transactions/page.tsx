"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { ArrowLeft, Download, Printer } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { DatePicker } from "@/design/components/date-picker";
import { Field } from "@/design/components/input";
import { Money } from "@/design/components/money";
import { Table, THead, TBody, TR, TH, TD } from "@/design/components/table";
import { ThemeToggle } from "@/design/components/theme-toggle";
import { useMe } from "@/features/auth/hooks";
import { useAccounts, useBranches, useCurrencies, useProjects } from "@/features/gl/hooks";
import { byId, toOptions } from "@/features/gl/lookups";
import type { AccountTransaction, AccountTransactionsResponse } from "@/features/gl/types";
import { api } from "@/lib/api";
import { exportToCsv } from "@/lib/csv";
import { formatDate } from "@/lib/format";

function AccountTransactionsReportView() {
  const t = useTranslations("gl");
  const searchParams = useSearchParams();
  const { data: me } = useMe();
  const initialAccountId = searchParams.get("accountId") ?? "";

  const [accountId, setAccountId] = useState(initialAccountId);
  const [dateFrom, setDateFrom] = useState<Date>(() => {
    const d = new Date();
    return new Date(d.getFullYear(), 0, 1);
  });
  const [dateTo, setDateTo] = useState<Date>(() => new Date());
  const [branchId, setBranchId] = useState("");
  const [projectId, setProjectId] = useState("");

  const accounts = useAccounts();
  const branches = useBranches();
  const projects = useProjects();
  const currencies = useCurrencies();

  const accountById = byId(accounts.data);
  const branchById = byId(branches.data);
  const projectById = byId(projects.data);
  const baseCurrency = useMemo(() => currencies.data?.find((c) => c.is_base), [currencies.data]);

  const currencyLike = baseCurrency
    ? { code: baseCurrency.code, decimalPlaces: baseCurrency.decimal_places, symbol: baseCurrency.symbol }
    : undefined;

  const [items, setItems] = useState<AccountTransaction[]>([]);
  const [openingBase, setOpeningBase] = useState<string>("0");
  const [loading, setLoading] = useState(false);

  const selectedAccount = accountById.get(Number(accountId));
  const selectedBranch = branches.data?.find((b) => String(b.id) === branchId);
  const selectedProject = projects.data?.find((p) => String(p.id) === projectId);

  // Load all transactions for the report (limit 500)
  useEffect(() => {
    if (!accountId) {
      setItems([]);
      setOpeningBase("0");
      return;
    }

    let active = true;
    setLoading(true);

    const search = new URLSearchParams({
      date_from: dateFrom.toISOString().slice(0, 10),
      date_to: dateTo.toISOString().slice(0, 10),
      limit: "500",
    });
    if (branchId) search.set("branch_id", branchId);
    if (projectId) search.set("project_id", projectId);

    api
      .get<AccountTransactionsResponse>(`/gl/accounts/${accountId}/transactions?${search.toString()}`)
      .then((res) => {
        if (!active) return;
        setOpeningBase(res.opening_base);
        setItems(res.items);
      })
      .catch((err) => {
        console.error("Failed to load transactions", err);
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [accountId, dateFrom, dateTo, branchId, projectId]);

  const totalDebit = items
    .filter((l) => Number(l.base_amount) > 0)
    .reduce((s, l) => s + Number(l.base_amount), 0);
  const totalCredit = items
    .filter((l) => Number(l.base_amount) < 0)
    .reduce((s, l) => s - Number(l.base_amount), 0);
  const closingBalance =
    items.length > 0 ? Number(items[items.length - 1].running_base) : Number(openingBase);

  function handleExportCsv() {
    if (!selectedAccount) return;
    const headers = [
      "Date",
      "Document Number",
      "Reference",
      "Description",
      "Branch",
      "Project",
      "Debit",
      "Credit",
      "Running Balance",
    ];

    const rows: (string | number)[][] = [];

    // Opening row
    rows.push([
      formatDate(dateFrom),
      "OPENING",
      "",
      "Opening Balance brought forward",
      "",
      "",
      "",
      "",
      Number(openingBase),
    ]);

    for (const item of items) {
      const baseAmt = Number(item.base_amount);
      const b = branchById.get(item.branch_id);
      const p = item.project_id ? projectById.get(item.project_id) : undefined;
      rows.push([
        formatDate(item.entry_date),
        item.entry_number,
        item.reference ?? "",
        item.description ?? "",
        b?.code ?? "",
        p?.code ?? "",
        baseAmt > 0 ? baseAmt : "",
        baseAmt < 0 ? -baseAmt : "",
        Number(item.running_base),
      ]);
    }

    // Closing row
    rows.push([
      formatDate(dateTo),
      "CLOSING",
      "",
      "Closing Balance",
      "",
      "",
      totalDebit,
      totalCredit,
      closingBalance,
    ]);

    const fileSuffix = `${selectedAccount.code}_${dateFrom.toISOString().slice(0, 10)}_to_${dateTo.toISOString().slice(0, 10)}`;
    exportToCsv(`account-transactions-${fileSuffix}`, headers, rows);
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
            <h1 className="font-display text-lg font-semibold">{t("accountTransactionsReport")}</h1>
            <p className="text-xs text-[var(--vinea-ink-muted)]">
              {selectedAccount ? `${selectedAccount.code} · ${selectedAccount.name}` : t("selectAccountToBegin")}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            onClick={handleExportCsv}
            disabled={!selectedAccount || items.length === 0}
            className="gap-1.5 text-xs"
          >
            <Download className="size-3.5" /> {t("exportCsv")}
          </Button>
          <Button
            variant="primary"
            onClick={() => window.print()}
            disabled={!selectedAccount}
            className="gap-1.5 text-xs"
          >
            <Printer className="size-3.5" /> {t("printReport")}
          </Button>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 overflow-auto px-6 py-6 print:overflow-visible print:px-0 print:py-0">
        <div className="mx-auto max-w-6xl space-y-6 print:max-w-none print:space-y-4">
          {/* Formal Print-Only Report Header */}
          <div className="hidden border-b-2 border-black pb-4 print:block">
            <div className="flex items-baseline justify-between">
              <div>
                <h1 className="text-2xl font-bold tracking-tight">{me?.company?.name ?? "Vinea ERP"}</h1>
                <p className="text-base font-semibold">{t("accountTransactionsReport")}</p>
                {selectedAccount && (
                  <p className="text-sm font-medium">
                    Account: {selectedAccount.code} — {selectedAccount.name} ({selectedAccount.class})
                  </p>
                )}
              </div>
              <div className="text-right text-xs">
                <p>
                  <strong>Period:</strong> {formatDate(dateFrom)} – {formatDate(dateTo)}
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
            </div>
          </div>

          {/* Interactive Filters (print:hidden) */}
          <div className="grid grid-cols-1 gap-4 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-5 sm:grid-cols-2 lg:grid-cols-5 print:hidden">
            <Field label={t("account")} className="sm:col-span-2 lg:col-span-2">
              <Combobox
                options={toOptions(accounts.data ?? [], (a) => `${a.code} · ${a.name}`)}
                value={accountId}
                onValueChange={setAccountId}
                placeholder="Choose an account…"
              />
            </Field>
            <Field label={t("dateFrom")}>
              <DatePicker value={dateFrom} onValueChange={setDateFrom} />
            </Field>
            <Field label={t("dateTo")}>
              <DatePicker value={dateTo} onValueChange={setDateTo} />
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
          </div>

          {!accountId ? (
            <div className="rounded-[var(--radius-card)] border border-dashed border-[var(--vinea-border)] p-16 text-center print:hidden">
              <p className="text-sm font-medium text-[var(--vinea-ink)]">{t("selectAccountToBegin")}</p>
            </div>
          ) : loading ? (
            <div className="flex h-48 items-center justify-center text-sm text-[var(--vinea-ink-subtle)]">
              Loading account transactions…
            </div>
          ) : (
            <div className="overflow-auto rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] print:border print:border-gray-300">
              {/* Opening Balance Bar */}
              <div className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-sunken)]/60 px-4 py-3 print:bg-gray-100">
                <span className="text-xs font-semibold uppercase tracking-wider text-[var(--vinea-ink-muted)] print:text-black">
                  {t("openingBalance")} (as of {formatDate(dateFrom)})
                </span>
                <span className="font-mono text-sm font-semibold print:text-black">
                  {currencyLike && <Money amount={Number(openingBase)} currency={currencyLike} />}
                </span>
              </div>

              {items.length === 0 ? (
                <div className="p-12 text-center text-sm text-[var(--vinea-ink-subtle)]">
                  {t("noTransactions")}
                </div>
              ) : (
                <Table>
                  <THead className="print:bg-gray-100">
                    <TR>
                      <TH className="w-24">{t("date")}</TH>
                      <TH className="w-28">Doc #</TH>
                      <TH className="w-32">{t("reference")}</TH>
                      <TH>Description</TH>
                      <TH className="w-20">Branch</TH>
                      <TH className="w-20">Project</TH>
                      <TH className="w-32 text-right">Debit</TH>
                      <TH className="w-32 text-right">Credit</TH>
                      <TH className="w-36 text-right">{t("runningBalance")}</TH>
                    </TR>
                  </THead>
                  <TBody className="divide-y divide-[var(--vinea-border)] print:divide-gray-300">
                    {items.map((tx) => {
                      const baseAmt = Number(tx.base_amount);
                      const runningAmt = Number(tx.running_base);
                      const branch = branchById.get(tx.branch_id);
                      const project = tx.project_id ? projectById.get(tx.project_id) : undefined;
                      return (
                        <TR key={tx.line_id} className="break-inside-avoid">
                          <TD className="text-xs text-[var(--vinea-ink-muted)] print:text-black">
                            {formatDate(tx.entry_date)}
                          </TD>
                          <TD className="font-mono text-xs font-medium text-[var(--vinea-brand)] print:text-black">
                            {tx.entry_number}
                          </TD>
                          <TD className="text-xs text-[var(--vinea-ink-muted)] print:text-black">
                            {tx.reference || "—"}
                          </TD>
                          <TD className="max-w-xs truncate text-xs text-[var(--vinea-ink)] print:text-black">
                            {tx.description || "—"}
                          </TD>
                          <TD className="text-xs text-[var(--vinea-ink-subtle)] print:text-black">
                            {branch?.code ?? "—"}
                          </TD>
                          <TD className="text-xs text-[var(--vinea-ink-subtle)] print:text-black">
                            {project?.code ?? "—"}
                          </TD>
                          <TD className="text-right font-mono text-xs print:text-black">
                            {baseAmt > 0 && currencyLike ? <Money amount={baseAmt} currency={currencyLike} /> : "—"}
                          </TD>
                          <TD className="text-right font-mono text-xs print:text-black">
                            {baseAmt < 0 && currencyLike ? <Money amount={-baseAmt} currency={currencyLike} /> : "—"}
                          </TD>
                          <TD className="text-right font-mono text-xs font-semibold print:text-black">
                            {currencyLike && <Money amount={runningAmt} currency={currencyLike} />}
                          </TD>
                        </TR>
                      );
                    })}
                  </TBody>
                </Table>
              )}

              {/* Closing Summary Footer */}
              <div className="flex items-center justify-between border-t-2 border-[var(--vinea-border)] bg-[var(--vinea-surface-sunken)]/60 px-6 py-4 font-mono text-sm print:border-t-2 print:border-black print:bg-gray-50">
                <div>
                  <span className="font-sans text-xs font-semibold uppercase tracking-wider text-[var(--vinea-ink-muted)] print:text-black">
                    {t("closingBalance")} (as of {formatDate(dateTo)}):
                  </span>{" "}
                  <span className="font-semibold print:text-black">
                    {currencyLike && <Money amount={closingBalance} currency={currencyLike} />}
                  </span>
                </div>
                <div className="flex gap-8">
                  <div>
                    <span className="font-sans text-xs text-[var(--vinea-ink-subtle)] print:text-black">
                      {t("totalDebit")}:
                    </span>{" "}
                    <span className="font-semibold">
                      {currencyLike && <Money amount={totalDebit} currency={currencyLike} />}
                    </span>
                  </div>
                  <div>
                    <span className="font-sans text-xs text-[var(--vinea-ink-subtle)] print:text-black">
                      {t("totalCredit")}:
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

export default function AccountTransactionsReportPage() {
  return (
    <Suspense fallback={<div className="flex min-h-screen items-center justify-center text-sm">Loading…</div>}>
      <AccountTransactionsReportView />
    </Suspense>
  );
}
