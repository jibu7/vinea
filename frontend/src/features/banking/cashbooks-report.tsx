"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Combobox } from "@/design/components/combobox";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Field } from "@/design/components/input";
import { QueryState } from "@/design/components/query-state";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useCompanyDetails } from "@/features/gl/hooks";
import { exportToCsv } from "@/lib/csv";
import { dotted, formatDate, formatQuantity, todayIso } from "@/lib/format";
import { useBaseCode, useMoneyIn } from "./account-picker";
import { useBankAccounts, useCashbook, useCashbookSummary } from "./hooks";
import type { CashbookDetail, CashbookRow, CashbookSummaryRow } from "./types";

export type CashbookMode = "detail" | "summary";

/** The reconciled column's third state. The wire says `"matched"`; the screen says it in words. */
const MATCHED = "matched";

/**
 * Reports → General Ledger → **Cashbooks** (P8 step 8, decision 6) — the owner's own row,
 * tagged P8 since the tree was written.
 *
 * **Detail** is the ledger for one bank or cash account over a range, in the account's own
 * currency: the opening balance, every line with its running balance, and a *Reconciled* column
 * carrying the `BRC-` its match was locked in, "Matched" for a match not locked yet, and blank
 * for outstanding — then the totals and the closing balance, which is also given in base
 * because that is the figure the trial balance publishes for the same account and date. The two
 * screens are asserted to agree (`base_closing_ties` on the server, the e2e on the page).
 *
 * **Summary** is one row per account: the range's movement, the closing in both currencies, and
 * the state of its reconciliation. Each account drills to its detail; the last reconciled date
 * drills to that reconciliation's report.
 *
 * The range, account and mode live in the URL, so a link from the enquiry lands on the page
 * a person would have built by hand.
 */
export function CashbooksReport({
  mode,
  requestedAccountId,
  dateFrom,
  dateTo,
}: {
  mode: CashbookMode;
  requestedAccountId: number | null;
  dateFrom: string | null;
  dateTo: string | null;
}) {
  const t = useTranslations("banking.cashbooks");
  const tc = useTranslations("banking.common");
  const router = useRouter();
  const company = useCompanyDetails();

  const accounts = useBankAccounts();
  const rows = accounts.data ?? [];
  const selected = rows.find((row) => row.id === requestedAccountId) ?? rows[0] ?? null;
  const to = dateTo ?? todayIso();
  const from = dateFrom ?? `${to.slice(0, 4)}-01-01`;

  const detail = useCashbook(mode === "detail" ? (selected?.id ?? null) : null, from, to);
  const summary = useCashbookSummary(from, to, mode === "summary");

  const go = (next: { mode?: CashbookMode; account?: number | null; from?: string; to?: string }) => {
    const params = new URLSearchParams({
      mode: next.mode ?? mode,
      from: next.from ?? from,
      to: next.to ?? to,
    });
    const account = next.account === undefined ? selected?.id : next.account;
    if (account) params.set("account", String(account));
    router.push(`/gl/reports/cashbooks?${params}`);
  };

  const handleExport =
    mode === "detail"
      ? detail.data
        ? () => exportDetail(detail.data!, t)
        : undefined
      : (summary.data ?? []).length > 0
        ? () => exportSummary(summary.data!, from, to, t)
        : undefined;

  return (
    <ReportPage
      title={t("title")}
      subtitle={mode === "detail" ? t("subtitleDetail") : t("subtitleSummary")}
      companyName={company.data?.name}
      asOfLabel={
        mode === "detail" && detail.data
          ? t("detailLabel", {
              account: dotted(detail.data.code, detail.data.name),
              from: formatDate(from),
              to: formatDate(to),
            })
          : t("rangeLabel", { from: formatDate(from), to: formatDate(to) })
      }
      onExportCsv={handleExport}
      filters={
        <div className="flex flex-wrap items-end gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4">
          <Field label={t("mode")} className="w-40">
            <Select
              options={[
                { value: "detail", label: t("modeDetail") },
                { value: "summary", label: t("modeSummary") },
              ]}
              value={mode}
              onValueChange={(value) => go({ mode: value as CashbookMode })}
              ariaLabel={t("mode")}
            />
          </Field>
          {mode === "detail" && (
            <Field label={tc("bankAccount")} className="w-72">
              <Combobox
                options={rows.map((row) => ({ value: String(row.id), label: dotted(row.code, row.name) }))}
                value={selected ? String(selected.id) : ""}
                onValueChange={(value) => go({ account: value ? Number(value) : null })}
                placeholder={tc("chooseBankAccount")}
              />
            </Field>
          )}
          <Field label={t("from")} className="w-44">
            <IsoDatePicker value={from} onValueChange={(value) => go({ from: value })} />
          </Field>
          <Field label={t("to")} className="w-44">
            <IsoDatePicker value={to} onValueChange={(value) => go({ to: value })} />
          </Field>
        </div>
      }
    >
      {mode === "detail" ? (
        detail.data ? (
          <DetailBody detail={detail.data} />
        ) : (
          <ReportPanel>
            <QueryState
              query={selected ? detail : accounts}
              isEmpty
              empty={rows.length === 0 ? t("noAccounts") : t("chooseAccount")}
              testId="cashbook"
            />
          </ReportPanel>
        )
      ) : (summary.data ?? []).length > 0 ? (
        <SummaryBody rows={summary.data!} from={from} to={to} />
      ) : (
        <ReportPanel>
          <QueryState query={summary} isEmpty empty={t("noAccounts")} testId="cashbook-summary" />
        </ReportPanel>
      )}
    </ReportPage>
  );
}

function DetailBody({ detail }: { detail: CashbookDetail }) {
  const t = useTranslations("banking.cashbooks");
  const moneyIn = useMoneyIn();
  const baseCode = useBaseCode();
  const money = (value: string) => moneyIn(value, detail.currency_code);

  return (
    <>
      <ReportPanel>
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-5">
          <Stat label={t("opening")} testId="cashbook-opening">{money(detail.opening_balance)}</Stat>
          <Stat label={t("receipts")} testId="cashbook-receipts">{money(detail.receipts_total)}</Stat>
          <Stat label={t("payments")} testId="cashbook-payments">{money(detail.payments_total)}</Stat>
          <Stat label={t("closing")} testId="cashbook-closing">{money(detail.closing_balance)}</Stat>
          <Stat label={t("closingBase", { currency: baseCode })} testId="cashbook-closing-base">
            {moneyIn(detail.closing_base, baseCode)}
          </Stat>
        </dl>
        <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">{t("tieNote")}</p>
      </ReportPanel>

      <ReportPanel>
        <Table>
          <THead>
            <TR>
              <TH className="w-24">{t("date")}</TH>
              <TH className="w-28">{t("entry")}</TH>
              <TH className="w-16">{t("docType")}</TH>
              <TH className="w-28">{t("reference")}</TH>
              <TH>{t("description")}</TH>
              <TH className="w-36">{t("partner")}</TH>
              <TH className="w-32 text-right">{t("receipt")}</TH>
              <TH className="w-32 text-right">{t("payment")}</TH>
              <TH className="w-36 text-right">{t("balance")}</TH>
              <TH className="w-28">{t("reconciled")}</TH>
            </TR>
          </THead>
          <TBody>
            <TR data-testid="cashbook-opening-row">
              <TD className="font-mono text-xs">{formatDate(detail.date_from)}</TD>
              <TD colSpan={7} className="text-xs font-medium text-[var(--vinea-ink-muted)]">
                {t("openingRow")}
              </TD>
              <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">
                {money(detail.opening_balance)}
              </TD>
              <TD />
            </TR>
            {detail.rows.map((row) => (
              <DetailRow key={row.journal_line_id} row={row} money={money} />
            ))}
            <TR className="border-t-2 border-[var(--vinea-border-strong)] font-semibold">
              <TD />
              <TD colSpan={5} className="text-xs">
                {t("totalsRow", { count: formatQuantity(detail.rows.length, 0) })}
              </TD>
              <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">
                {money(detail.receipts_total)}
              </TD>
              <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">
                {money(detail.payments_total)}
              </TD>
              <TD
                className="text-right font-mono text-xs tabular-nums whitespace-nowrap"
                data-testid="cashbook-closing-row"
              >
                {money(detail.closing_balance)}
              </TD>
              <TD />
            </TR>
          </TBody>
        </Table>
        {detail.rows.length === 0 && (
          <p className="py-6 text-center text-xs text-[var(--vinea-ink-subtle)]">{t("noLines")}</p>
        )}
      </ReportPanel>
    </>
  );
}

function DetailRow({ row, money }: { row: CashbookRow; money: (value: string) => string }) {
  const t = useTranslations("banking.cashbooks");
  const receipt = Number(row.receipt);
  const payment = Number(row.payment);
  return (
    <TR data-cashbook-line={row.entry_number}>
      <TD className="font-mono text-xs">{formatDate(row.entry_date)}</TD>
      <TD>
        <Link
          href={`/gl/entries/${row.entry_id}`}
          className="font-mono text-xs font-semibold text-[var(--vinea-brand)] hover:underline"
        >
          {row.entry_number}
        </Link>
      </TD>
      <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">{row.doc_type}</TD>
      <TD className="text-xs text-[var(--vinea-ink-muted)]">{row.reference ?? ""}</TD>
      <TD className="text-xs">{row.description ?? ""}</TD>
      <TD className="text-xs">{row.partner_name ?? ""}</TD>
      <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">
        {receipt !== 0 ? money(row.receipt) : ""}
      </TD>
      <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">
        {payment !== 0 ? money(row.payment) : ""}
      </TD>
      <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">
        {money(row.running_balance)}
      </TD>
      <TD data-testid="cashbook-reconciled">
        {row.reconciled === null ? null : row.reconciled === MATCHED ? (
          <StatusChip tone="success">{t("matched")}</StatusChip>
        ) : (
          <span className="whitespace-nowrap font-mono text-xs">{row.reconciled}</span>
        )}
      </TD>
    </TR>
  );
}

function SummaryBody({ rows, from, to }: { rows: CashbookSummaryRow[]; from: string; to: string }) {
  const t = useTranslations("banking.cashbooks");
  const tc = useTranslations("banking.common");
  const moneyIn = useMoneyIn();
  const baseCode = useBaseCode();
  const totalBase = rows.reduce((sum, row) => sum + Number(row.closing_base), 0);

  return (
    <ReportPanel>
      <Table>
        <THead>
          <TR>
            <TH>{t("account")}</TH>
            <TH className="w-16">{t("currency")}</TH>
            <TH className="w-32 text-right">{t("opening")}</TH>
            <TH className="w-32 text-right">{t("receipts")}</TH>
            <TH className="w-32 text-right">{t("payments")}</TH>
            <TH className="w-32 text-right">{t("closing")}</TH>
            <TH className="w-32 text-right">{t("closingBase", { currency: baseCode })}</TH>
            <TH className="w-40">{t("lastReconciled")}</TH>
            <TH className="w-24 text-right">{t("unmatchedLines")}</TH>
            <TH className="w-24 text-right">{t("outstandingLines")}</TH>
          </TR>
        </THead>
        <TBody>
          {rows.map((row) => (
            <TR key={row.bank_account_id} data-cashbook-account={row.code}>
              <TD>
                <Link
                  href={`/gl/reports/cashbooks?mode=detail&account=${row.bank_account_id}&from=${from}&to=${to}`}
                  className="text-xs font-medium text-[var(--vinea-brand)] hover:underline"
                >
                  {dotted(row.code, row.name)}
                </Link>
              </TD>
              <TD className="font-mono text-xs">{row.currency_code}</TD>
              <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">
                {moneyIn(row.opening_balance, row.currency_code)}
              </TD>
              <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">
                {moneyIn(row.receipts, row.currency_code)}
              </TD>
              <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">
                {moneyIn(row.payments, row.currency_code)}
              </TD>
              <TD
                className="text-right font-mono text-xs tabular-nums whitespace-nowrap"
                data-testid={`summary-closing-${row.code}`}
              >
                {moneyIn(row.closing_balance, row.currency_code)}
              </TD>
              <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">
                {moneyIn(row.closing_base, baseCode)}
              </TD>
              <TD className="text-xs">
                {row.last_reconciliation_id && row.last_reconciled_at ? (
                  <Link
                    href={`/gl/reports/bank-reconciliation?reconciliation=${row.last_reconciliation_id}`}
                    className="text-[var(--vinea-brand)] hover:underline"
                  >
                    {t("reconciledAt", {
                      date: formatDate(row.last_reconciled_at),
                      balance: moneyIn(row.last_reconciled_balance, row.currency_code),
                    })}
                  </Link>
                ) : (
                  <span className="text-[var(--vinea-ink-subtle)]">{tc("emptyValue")}</span>
                )}
              </TD>
              <TD
                className="text-right font-mono text-xs tabular-nums"
                data-testid={`summary-unmatched-${row.code}`}
              >
                {formatQuantity(row.unmatched_statement_lines, 0)}
              </TD>
              <TD
                className="text-right font-mono text-xs tabular-nums"
                data-testid={`summary-outstanding-${row.code}`}
              >
                {formatQuantity(row.outstanding_lines, 0)}
              </TD>
            </TR>
          ))}
          <TR className="border-t-2 border-[var(--vinea-border-strong)] font-semibold">
            <TD colSpan={6} className="text-xs">
              {t("summaryTotal", { count: formatQuantity(rows.length, 0) })}
            </TD>
            <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">
              {moneyIn(totalBase, baseCode)}
            </TD>
            <TD colSpan={3} />
          </TR>
        </TBody>
      </Table>
      <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">{t("summaryNote")}</p>
    </ReportPanel>
  );
}

function Stat({ label, testId, children }: { label: string; testId: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-[var(--vinea-ink-muted)]">{label}</dt>
      <dd className="font-mono text-sm font-semibold tabular-nums whitespace-nowrap" data-testid={testId}>
        {children}
      </dd>
    </div>
  );
}

type T = (key: string) => string;

/** The CSV carries the wire's decimals, not the screen's formatting: a spreadsheet wants a
 * number it can sum, as every P3–P7 export does. */
function exportDetail(detail: CashbookDetail, t: T) {
  exportToCsv(
    `cashbook-${detail.code}-${detail.date_from}-to-${detail.date_to}`,
    [
      t("date"),
      t("entry"),
      t("docType"),
      t("reference"),
      t("description"),
      t("partner"),
      t("receipt"),
      t("payment"),
      t("balance"),
      t("reconciled"),
    ],
    [
      [detail.date_from, "", "", "", t("openingRow"), "", "", "", detail.opening_balance, ""],
      ...detail.rows.map((row) => [
        row.entry_date,
        row.entry_number,
        row.doc_type,
        row.reference ?? "",
        row.description ?? "",
        row.partner_name ?? "",
        row.receipt,
        row.payment,
        row.running_balance,
        row.reconciled === MATCHED ? t("matched") : (row.reconciled ?? ""),
      ]),
      [
        detail.date_to,
        "",
        "",
        "",
        t("closingRow"),
        "",
        detail.receipts_total,
        detail.payments_total,
        detail.closing_balance,
        "",
      ],
    ],
  );
}

function exportSummary(rows: CashbookSummaryRow[], from: string, to: string, t: T) {
  exportToCsv(
    `cashbooks-summary-${from}-to-${to}`,
    [
      t("code"),
      t("name"),
      t("currency"),
      t("opening"),
      t("receipts"),
      t("payments"),
      t("closing"),
      t("closingBaseCsv"),
      t("lastReconciledDate"),
      t("lastReconciledBalance"),
      t("unmatchedLines"),
      t("outstandingLines"),
    ],
    rows.map((row) => [
      row.code,
      row.name,
      row.currency_code,
      row.opening_balance,
      row.receipts,
      row.payments,
      row.closing_balance,
      row.closing_base,
      row.last_reconciled_at ?? "",
      row.last_reconciled_balance ?? "",
      row.unmatched_statement_lines,
      row.outstanding_lines,
    ]),
  );
}
