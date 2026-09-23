"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Combobox } from "@/design/components/combobox";
import { Field } from "@/design/components/input";
import { QueryState } from "@/design/components/query-state";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useCompanyDetails } from "@/features/gl/hooks";
import { ReconciliationStatus } from "@/lib/api-enums";
import { exportToCsv } from "@/lib/csv";
import { dotted, formatDate, formatQuantity } from "@/lib/format";
import { useBankAccountChoice, useMoneyIn } from "./account-picker";
import { useReconciliationReport, useReconciliations } from "./hooks";
import type { Figures, OutstandingLine, ReconciliationReport as Report } from "./types";

/** Deposits in transit are the outstanding lines that put money in; unpresented payments the
 * ones that take it out. Their sum is `outstanding_total`, and the statement foots through both. */
function split(figures: Figures) {
  const deposits = figures.outstanding.filter((line) => Number(line.amount) > 0);
  const payments = figures.outstanding.filter((line) => Number(line.amount) < 0);
  return {
    deposits: deposits.reduce((sum, line) => sum + Number(line.amount), 0),
    payments: -payments.reduce((sum, line) => sum + Number(line.amount), 0),
  };
}

/**
 * Reports → General Ledger → **Bank reconciliation** (P8 step 8, decision 6) — the owner's own
 * row, and the statement an accountant signs. Transactions → General Ledger's row of the same
 * name is the workspace where the reconciliation is *done* (C.1.14); this is the paper it
 * produces, over one reconciliation, open or locked.
 *
 * It reads top to bottom as the classic statement: *Balance per bank statement*, *Add: deposits
 * in transit*, *Less: unpresented payments*, *= Adjusted bank balance*, *Balance per cashbook*,
 * *Difference* — then the outstanding items that make up the two middle lines.
 *
 * **An open one** is live: the figures are what its date computes now, and the bank's lines
 * nobody has matched yet are listed, because they are why it cannot lock.
 *
 * **A locked one** never changes (decision 5), and the report says both what it *said* — the
 * stored figures, reproduced from the lines that existed at the lock — and what the same date
 * computes *now*, side by side. They differ by exactly the lines under **Posted after lock**:
 * dated inside the reconciliation, posted after it was signed. Showing only the live reading
 * would silently restate a signed document; showing only the stored one could not explain a
 * difference anybody noticed.
 */
export function ReconciliationReportScreen({
  requestedAccountId,
  requestedReconciliationId,
}: {
  requestedAccountId: number | null;
  requestedReconciliationId: number | null;
}) {
  const t = useTranslations("banking.reconciliationReport");
  const tc = useTranslations("banking.common");
  const tr = useTranslations("banking.reconciliations");
  const router = useRouter();
  const company = useCompanyDetails();
  const moneyIn = useMoneyIn();

  const direct = useReconciliationReport(requestedReconciliationId);
  const accountId = direct.data?.bank_account_id ?? requestedAccountId;
  const { accounts, banks, selected } = useBankAccountChoice(accountId);
  const reconciliations = useReconciliations(selected?.id ?? null);
  const chosenId =
    requestedReconciliationId ?? (reconciliations.data ?? [])[0]?.id ?? null;
  const report = useReconciliationReport(chosenId);
  const data = report.data;
  const locked = data?.status === ReconciliationStatus.LOCKED;
  const money = (value: string | number | null | undefined) =>
    moneyIn(value, data?.currency_code ?? "");

  return (
    <ReportPage
      title={t("title")}
      subtitle={t("subtitle")}
      companyName={company.data?.name}
      asOfLabel={
        data
          ? t("printLabel", {
              number: data.number,
              account: dotted(data.bank_account_code, data.bank_account_name),
              date: formatDate(data.reconciliation_date),
            })
          : undefined
      }
      onExportCsv={data ? () => exportReport(data, t) : undefined}
      filters={
        <div className="flex flex-wrap items-end gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4">
          <Field label={tc("bankAccount")} className="w-72">
            <Combobox
              options={banks.map((row) => ({ value: String(row.id), label: dotted(row.code, row.name) }))}
              value={selected ? String(selected.id) : ""}
              onValueChange={(value) => router.push(`/gl/reports/bank-reconciliation?account=${value}`)}
              placeholder={tc("chooseBankAccount")}
            />
          </Field>
          <Field label={t("reconciliation")} className="w-72">
            <Select
              options={(reconciliations.data ?? []).map((row) => ({
                value: String(row.id),
                label: t("reconciliationOption", {
                  number: row.number,
                  date: formatDate(row.reconciliation_date),
                  status: tr(`statusLabel.${row.status}`),
                }),
              }))}
              value={chosenId ? String(chosenId) : ""}
              onValueChange={(value) =>
                router.push(`/gl/reports/bank-reconciliation?reconciliation=${value}`)
              }
              ariaLabel={t("reconciliation")}
              placeholder={t("chooseReconciliation")}
            />
          </Field>
        </div>
      }
    >
      {!data ? (
        <ReportPanel>
          <QueryState
            query={chosenId ? report : selected ? reconciliations : accounts}
            isEmpty
            empty={
              banks.length === 0
                ? tr("noBankAccount")
                : (reconciliations.data ?? []).length === 0
                  ? t("noReconciliations")
                  : t("chooseReconciliation")
            }
            testId="reconciliation-report"
          />
        </ReportPanel>
      ) : (
        <>
          <ReportPanel>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                <div>
                  <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("account")}</dt>
                  <dd className="text-sm font-medium" data-testid="report-account">
                    {dotted(data.bank_account_code, data.bank_account_name)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("number")}</dt>
                  <dd className="font-mono text-sm font-semibold" data-testid="report-number">
                    {data.number}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("date")}</dt>
                  <dd className="font-mono text-sm">{formatDate(data.reconciliation_date)}</dd>
                </div>
              </dl>
              <div className="flex items-center gap-3">
                <StatusChip tone={locked ? "neutral" : "warning"}>{tr(`statusLabel.${data.status}`)}</StatusChip>
                <Link
                  href={`/bank/reconciliations/${data.reconciliation_id}`}
                  className="text-xs font-medium text-[var(--vinea-brand)] hover:underline print:hidden"
                >
                  {t("openWorkspace")}
                </Link>
              </div>
            </div>
          </ReportPanel>

          <ReportPanel>
            <StatementTable report={data} money={money} />
            <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">
              {locked ? t("lockedNote") : t("openNote")}
            </p>
          </ReportPanel>

          <ReportPanel>
            <h2 className="pb-2 text-sm font-semibold">
              {t("outstandingHeading", {
                count: formatQuantity((data.stored ?? data.live).outstanding.length, 0),
              })}
            </h2>
            <LineTable
              lines={(data.stored ?? data.live).outstanding}
              money={money}
              testId="outstanding-item"
              empty={t("noOutstanding")}
            />
          </ReportPanel>

          {!locked && (
            <ReportPanel>
              <h2 className="pb-2 text-sm font-semibold">
                {t("unmatchedHeading", { count: formatQuantity(data.live.unmatched_statement_count, 0) })}
              </h2>
              {data.live.unmatched_statement.length === 0 ? (
                <p className="py-4 text-xs text-[var(--vinea-ink-subtle)]">{t("noUnmatched")}</p>
              ) : (
                <Table>
                  <THead>
                    <TR>
                      <TH className="w-28">{t("date")}</TH>
                      <TH>{t("description")}</TH>
                      <TH className="w-36">{t("reference")}</TH>
                      <TH className="w-36 text-right">{t("amount")}</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {data.live.unmatched_statement.map((line) => (
                      <TR key={line.id} data-testid="unmatched-statement-line">
                        <TD className="font-mono text-xs">{formatDate(line.value_date)}</TD>
                        <TD className="text-xs">{line.description}</TD>
                        <TD className="text-xs text-[var(--vinea-ink-muted)]">{line.reference ?? ""}</TD>
                        <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">
                          {money(line.amount)}
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              )}
            </ReportPanel>
          )}

          {locked && (
            <ReportPanel>
              <h2 className="pb-1 text-sm font-semibold">
                {t("postedAfterLockHeading", { count: formatQuantity(data.posted_after_lock.length, 0) })}
              </h2>
              <p className="pb-2 text-xs text-[var(--vinea-ink-subtle)]">{t("postedAfterLockNote")}</p>
              <LineTable
                lines={data.posted_after_lock}
                money={money}
                testId="posted-after-lock"
                empty={t("noneAfterLock")}
              />
            </ReportPanel>
          )}
        </>
      )}
    </ReportPage>
  );
}

function StatementTable({ report, money }: { report: Report; money: (value: string | number) => string }) {
  const t = useTranslations("banking.reconciliationReport");
  const columns: Array<{ key: string; label: string; figures: Figures }> = report.stored
    ? [
        { key: "stored", label: t("atLock"), figures: report.stored },
        { key: "live", label: t("now"), figures: report.live },
      ]
    : [{ key: "live", label: t("live"), figures: report.live }];

  const rows: Array<{ key: string; label: string; value: (f: Figures) => string | number; strong?: boolean }> = [
    { key: "statement", label: t("statementBalance"), value: (f) => f.statement_balance },
    { key: "deposits", label: t("depositsInTransit"), value: (f) => split(f).deposits },
    { key: "payments", label: t("unpresentedPayments"), value: (f) => split(f).payments },
    { key: "adjusted", label: t("adjustedBankBalance"), value: (f) => f.adjusted_bank_balance, strong: true },
    { key: "cashbook", label: t("cashbookBalance"), value: (f) => f.ledger_balance },
    { key: "difference", label: t("difference"), value: (f) => f.difference, strong: true },
  ];

  return (
    <Table>
      <THead>
        <TR>
          <TH />
          {columns.map((column) => (
            <TH key={column.key} className="w-44 text-right">
              {column.label}
            </TH>
          ))}
        </TR>
      </THead>
      <TBody>
        {rows.map((row) => (
          <TR key={row.key} className={row.strong ? "font-semibold" : undefined}>
            <TD className="text-sm">{row.label}</TD>
            {columns.map((column) => (
              <TD
                key={column.key}
                className="text-right font-mono text-sm tabular-nums whitespace-nowrap"
                data-testid={`figure-${row.key}-${column.key}`}
              >
                {money(row.value(column.figures))}
              </TD>
            ))}
          </TR>
        ))}
      </TBody>
    </Table>
  );
}

function LineTable({
  lines,
  money,
  testId,
  empty,
}: {
  lines: OutstandingLine[];
  money: (value: string) => string;
  testId: string;
  empty: string;
}) {
  const t = useTranslations("banking.reconciliationReport");
  if (lines.length === 0) {
    return <p className="py-4 text-xs text-[var(--vinea-ink-subtle)]">{empty}</p>;
  }
  return (
    <Table>
      <THead>
        <TR>
          <TH className="w-28">{t("date")}</TH>
          <TH className="w-28">{t("entry")}</TH>
          <TH className="w-16">{t("docType")}</TH>
          <TH>{t("description")}</TH>
          <TH className="w-36 text-right">{t("amount")}</TH>
        </TR>
      </THead>
      <TBody>
        {lines.map((line) => (
          <TR key={line.journal_line_id} data-testid={testId} data-entry={line.entry_number}>
            <TD className="font-mono text-xs">{formatDate(line.entry_date)}</TD>
            <TD>
              <Link
                href={`/gl/entries/${line.entry_id}`}
                className="font-mono text-xs font-semibold text-[var(--vinea-brand)] hover:underline"
              >
                {line.entry_number}
              </Link>
            </TD>
            <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">{line.doc_type}</TD>
            <TD className="text-xs">
              {line.description ?? ""}
              {line.dated_inside && (
                <StatusChip tone="warning" className="ml-2 whitespace-nowrap">
                  {t("datedInside", { number: line.dated_inside })}
                </StatusChip>
              )}
            </TD>
            <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">{money(line.amount)}</TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}

/** The statement as rows of label and figure, then the outstanding items and the late lines —
 * the printed page's order, with the wire's decimals for a spreadsheet to sum. */
function exportReport(report: Report, t: (key: string) => string) {
  const figures = report.stored ?? report.live;
  const { deposits, payments } = split(figures);
  const lines = (heading: string, items: OutstandingLine[]) =>
    items.map((line) => [heading, line.entry_date, line.entry_number, line.doc_type, line.description ?? "", line.amount]);
  exportToCsv(
    `bank-reconciliation-${report.number}`,
    [t("section"), t("date"), t("entry"), t("docType"), t("description"), t("amount")],
    [
      [t("statementBalance"), report.reconciliation_date, "", "", "", figures.statement_balance],
      [t("depositsInTransit"), "", "", "", "", deposits],
      [t("unpresentedPayments"), "", "", "", "", payments],
      [t("adjustedBankBalance"), "", "", "", "", figures.adjusted_bank_balance],
      [t("cashbookBalance"), "", "", "", "", figures.ledger_balance],
      [t("difference"), "", "", "", "", figures.difference],
      ...lines(t("outstandingSection"), figures.outstanding),
      ...lines(t("postedAfterLockSection"), report.posted_after_lock),
    ],
  );
}
