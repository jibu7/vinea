"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { ArrowLeft, ArrowUpRight, ExternalLink } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { DatePicker } from "@/design/components/date-picker";
import { Drawer, DrawerContent } from "@/design/components/drawer";
import { Field } from "@/design/components/input";
import { Money } from "@/design/components/money";
import { StatusChip } from "@/design/components/status-chip";
import { Table, THead, TBody, TR, TH, TD } from "@/design/components/table";
import { ThemeToggle } from "@/design/components/theme-toggle";
import {
  useAccounts,
  useBranches,
  useCurrencies,
  useJournalEntry,
  useProjects,
} from "@/features/gl/hooks";
import { byId, toOptions } from "@/features/gl/lookups";
import type { AccountTransaction, AccountTransactionsResponse } from "@/features/gl/types";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";

function AccountEnquiryView() {
  const t = useTranslations("gl");
  const searchParams = useSearchParams();
  const initialAccountId = searchParams.get("accountId") ?? "";

  const [accountId, setAccountId] = useState(initialAccountId);
  const [dateFrom, setDateFrom] = useState<Date>(() => {
    const d = new Date();
    return new Date(d.getFullYear(), 0, 1); // Jan 1 of current year
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

  // Accumulated transactions for server pagination / streaming
  const [items, setItems] = useState<AccountTransaction[]>([]);
  const [openingBase, setOpeningBase] = useState<string>("0");
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  // Drawer state for clicked transaction
  const [selectedEntryId, setSelectedEntryId] = useState<number | null>(null);
  const { data: drawerEntry, isLoading: drawerLoading } = useJournalEntry(selectedEntryId);

  // When account or filters change, fetch page 1
  useEffect(() => {
    if (!accountId) {
      setItems([]);
      setOpeningBase("0");
      setNextCursor(null);
      return;
    }

    let active = true;
    setLoading(true);

    const search = new URLSearchParams({
      date_from: dateFrom.toISOString().slice(0, 10),
      date_to: dateTo.toISOString().slice(0, 10),
      limit: "100",
    });
    if (branchId) search.set("branch_id", branchId);
    if (projectId) search.set("project_id", projectId);

    api
      .get<AccountTransactionsResponse>(`/gl/accounts/${accountId}/transactions?${search.toString()}`)
      .then((res) => {
        if (!active) return;
        setOpeningBase(res.opening_base);
        setItems(res.items);
        setNextCursor(res.next_cursor);
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

  // Load next page
  function handleLoadMore() {
    if (!accountId || !nextCursor || loadingMore) return;
    setLoadingMore(true);

    const search = new URLSearchParams({
      date_from: dateFrom.toISOString().slice(0, 10),
      date_to: dateTo.toISOString().slice(0, 10),
      cursor: String(nextCursor),
      limit: "100",
    });
    if (branchId) search.set("branch_id", branchId);
    if (projectId) search.set("project_id", projectId);

    api
      .get<AccountTransactionsResponse>(`/gl/accounts/${accountId}/transactions?${search.toString()}`)
      .then((res) => {
        setItems((prev) => [...prev, ...res.items]);
        setNextCursor(res.next_cursor);
      })
      .catch((err) => {
        console.error("Failed to load more transactions", err);
      })
      .finally(() => {
        setLoadingMore(false);
      });
  }

  const selectedAccount = accountById.get(Number(accountId));

  return (
    <div className="flex min-h-screen flex-col" data-density="dense">
      <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]">
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <h1 className="font-display text-lg font-semibold">{t("accountEnquiry")}</h1>
            <p className="text-xs text-[var(--vinea-ink-muted)]">
              {selectedAccount ? `${selectedAccount.code} · ${selectedAccount.name}` : t("selectAccountToBegin")}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <Link
            href="/gl/enquiries/trial-balance"
            className="flex items-center gap-1 rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] px-3 py-1.5 text-xs font-medium text-[var(--vinea-ink)] hover:bg-[var(--vinea-surface-sunken)]"
          >
            {t("trialBalanceEnquiry")} <ArrowUpRight className="size-3" />
          </Link>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 overflow-auto px-6 py-6">
        <div className="mx-auto max-w-6xl space-y-6">
          {/* Filters */}
          <div className="grid grid-cols-1 gap-4 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-5 sm:grid-cols-2 lg:grid-cols-5">
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
            <div className="rounded-[var(--radius-card)] border border-dashed border-[var(--vinea-border)] p-16 text-center">
              <p className="text-sm font-medium text-[var(--vinea-ink)]">{t("selectAccountToBegin")}</p>
              <p className="mt-1 text-xs text-[var(--vinea-ink-subtle)]">
                Select an account above to inspect all posted ledger movements and running balance.
              </p>
            </div>
          ) : loading ? (
            <div className="flex h-48 items-center justify-center text-sm text-[var(--vinea-ink-subtle)]">
              Loading account transactions…
            </div>
          ) : (
            <div className="space-y-4">
              {/* Opening balance bar */}
              <div className="flex items-center justify-between rounded-[var(--radius-control)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-sunken)]/60 px-4 py-3">
                <span className="text-xs font-semibold uppercase tracking-wider text-[var(--vinea-ink-muted)]">
                  {t("openingBalance")} (as of {formatDate(dateFrom)})
                </span>
                <span className="font-mono text-sm font-semibold">
                  {currencyLike && <Money amount={Number(openingBase)} currency={currencyLike} />}
                </span>
              </div>

              {/* Transactions Table */}
              {items.length === 0 ? (
                <div className="rounded-[var(--radius-card)] border border-dashed border-[var(--vinea-border)] p-12 text-center text-sm text-[var(--vinea-ink-subtle)]">
                  {t("noTransactions")}
                </div>
              ) : (
                <Table>
                  <THead>
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
                  <TBody>
                    {items.map((tx) => {
                      const baseAmt = Number(tx.base_amount);
                      const runningAmt = Number(tx.running_base);
                      const branch = branchById.get(tx.branch_id);
                      const project = tx.project_id ? projectById.get(tx.project_id) : undefined;
                      return (
                        <TR
                          key={tx.line_id}
                          onClick={() => setSelectedEntryId(tx.entry_id)}
                          className="cursor-pointer hover:bg-[var(--vinea-surface-sunken)]/60"
                        >
                          <TD className="text-xs text-[var(--vinea-ink-muted)]">
                            {formatDate(tx.entry_date)}
                          </TD>
                          <TD className="font-mono text-xs font-medium text-[var(--vinea-brand)]">
                            {tx.entry_number}
                          </TD>
                          <TD className="text-xs text-[var(--vinea-ink-muted)]">
                            {tx.reference || "—"}
                          </TD>
                          <TD className="max-w-xs truncate text-xs text-[var(--vinea-ink)]">
                            {tx.description || "—"}
                          </TD>
                          <TD className="text-xs text-[var(--vinea-ink-subtle)]">
                            {branch?.code ?? "—"}
                          </TD>
                          <TD className="text-xs text-[var(--vinea-ink-subtle)]">
                            {project?.code ?? "—"}
                          </TD>
                          <TD className="text-right font-mono text-xs">
                            {baseAmt > 0 && currencyLike ? <Money amount={baseAmt} currency={currencyLike} /> : "—"}
                          </TD>
                          <TD className="text-right font-mono text-xs">
                            {baseAmt < 0 && currencyLike ? <Money amount={-baseAmt} currency={currencyLike} /> : "—"}
                          </TD>
                          <TD className="text-right font-mono text-xs font-semibold">
                            {currencyLike && <Money amount={runningAmt} currency={currencyLike} />}
                          </TD>
                        </TR>
                      );
                    })}
                  </TBody>
                </Table>
              )}

              {/* Streaming pagination button */}
              {nextCursor && (
                <div className="flex justify-center pt-2">
                  <Button
                    variant="secondary"
                    onClick={handleLoadMore}
                    disabled={loadingMore}
                    className="text-xs"
                  >
                    {loadingMore ? "Loading more…" : t("loadMore")}
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>
      </main>

      {/* Entry Detail Drawer */}
      <Drawer open={selectedEntryId !== null} onOpenChange={(open) => !open && setSelectedEntryId(null)}>
        <DrawerContent
          title={drawerEntry ? `${drawerEntry.number}` : t("entryDetails")}
          description={
            drawerEntry
              ? `${formatDate(drawerEntry.entry_date)} · ${drawerEntry.doc_type === "CB" ? t("cashbookBatch") : t("journalBatch")}`
              : undefined
          }
        >
          {drawerLoading || !drawerEntry ? (
            <div className="flex h-48 items-center justify-center text-sm text-[var(--vinea-ink-subtle)]">
              Loading entry details…
            </div>
          ) : (
            <div className="space-y-6 pt-2">
              <div className="flex items-center justify-between">
                <StatusChip tone={drawerEntry.reversed_by_entry_id ? "neutral" : "success"}>
                  {drawerEntry.reversed_by_entry_id ? t("reversed") : t("posted")}
                </StatusChip>
                <Link
                  href={`/gl/entries/${drawerEntry.id}`}
                  className="flex items-center gap-1 text-xs font-medium text-[var(--vinea-brand)] hover:underline"
                >
                  {t("viewEntry")} <ExternalLink className="size-3" />
                </Link>
              </div>

              {drawerEntry.reversed_by_entry_id && (
                <div className="rounded-[var(--radius-control)] border border-[var(--vinea-warning)] bg-[var(--vinea-warning-soft)] p-3 text-xs text-[var(--vinea-warning)]">
                  {t("reversedBy", { number: drawerEntry.reversed_by_number ?? drawerEntry.reversed_by_entry_id })}
                </div>
              )}

              {drawerEntry.reverses_entry_id && (
                <div className="rounded-[var(--radius-control)] border border-[var(--vinea-info)] bg-[var(--vinea-info-soft)] p-3 text-xs text-[var(--vinea-info)]">
                  {t("reversalOf", { number: drawerEntry.reverses_entry_number ?? drawerEntry.reverses_entry_id })}
                  {drawerEntry.reversal_reason && ` · ${drawerEntry.reversal_reason}`}
                </div>
              )}

              <div className="rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-sunken)]/40 p-4 space-y-2">
                <p className="text-xs font-medium text-[var(--vinea-ink-muted)]">Description</p>
                <p className="text-sm text-[var(--vinea-ink)]">{drawerEntry.description}</p>
                {drawerEntry.reference && (
                  <p className="text-xs text-[var(--vinea-ink-muted)]">
                    <span className="font-medium">Ref:</span> {drawerEntry.reference}
                  </p>
                )}
              </div>

              <div>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--vinea-ink-subtle)]">
                  Journal Lines ({drawerEntry.lines.length})
                </p>
                <div className="overflow-hidden rounded-[var(--radius-control)] border border-[var(--vinea-border)]">
                  <table className="w-full text-xs">
                    <thead className="bg-[var(--vinea-surface-sunken)] text-[var(--vinea-ink-subtle)]">
                      <tr>
                        <th className="px-3 py-2 text-left">Account</th>
                        <th className="px-3 py-2 text-right">Debit</th>
                        <th className="px-3 py-2 text-right">Credit</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[var(--vinea-border)]">
                      {drawerEntry.lines.map((line) => {
                        const acc = accountById.get(line.gl_account_id);
                        const baseAmt = Number(line.base_amount);
                        return (
                          <tr key={line.id}>
                            <td className="px-3 py-2 text-[var(--vinea-ink)]">
                              <p className="font-medium">{acc ? `${acc.code} · ${acc.name}` : line.gl_account_id}</p>
                              {line.description && <p className="text-[11px] text-[var(--vinea-ink-subtle)]">{line.description}</p>}
                            </td>
                            <td className="px-3 py-2 text-right font-mono">
                              {baseAmt > 0 && currencyLike ? <Money amount={baseAmt} currency={currencyLike} /> : "—"}
                            </td>
                            <td className="px-3 py-2 text-right font-mono">
                              {baseAmt < 0 && currencyLike ? <Money amount={-baseAmt} currency={currencyLike} /> : "—"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </DrawerContent>
      </Drawer>
    </div>
  );
}

export default function AccountEnquiryPage() {
  return (
    <Suspense fallback={<div className="flex min-h-screen items-center justify-center text-sm">Loading…</div>}>
      <AccountEnquiryView />
    </Suspense>
  );
}
