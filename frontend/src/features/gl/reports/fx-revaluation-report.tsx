"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Field } from "@/design/components/input";
import { QueryState } from "@/design/components/query-state";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { FxRevaluationStatus } from "@/lib/api-enums";
import { exportToCsv } from "@/lib/csv";
import { formatDate, formatMoney, formatQuantity, trimDecimalString } from "@/lib/format";
import { lineHref, lineKey, lineLabel, lineName } from "../fx-revaluation-lines";
import { useCompanyDetails, useCurrencies, useFxRevaluation, useFxRevaluations } from "../hooks";

/**
 * Reports → General Ledger → **FX revaluation** (P7 step 8) — one run, and the lines in it.
 *
 * The screen under Transactions *posts* a run: an entry at the date and its mirror the day
 * after, in one transaction. This one reads one back, per line, which is the grain that makes
 * it answerable — "why did unrealized FX move by 1 416" is a question about documents, and a
 * per-run total cannot answer it. Each line names the document, what it was booked at, what it
 * is worth at the date, and the difference between them, and every one drills to the partner
 * document behind it.
 *
 * **The three entries are named on the page**, because the pair is not obvious and the page is
 * where somebody first meets it. A run posts, mirrors the next day, and — if it is reversed —
 * is undone by a *counter-entry of negated lines* rather than by a second mirror, since the
 * `reverses_entry_id` slot on the run's entry is already taken by its own mirror. That is the
 * asymmetry `_resolve_p7_document_pair` exists for on the entry page, and this is the screen
 * that shows why there are four `FXR-` numbers on the trial balance for one reversed run.
 *
 * The rates are quantities, not money: a rate is `NUMERIC(20,10)` and `formatMoney` would put
 * a currency code on a number that is not an amount of anything.
 *
 * **Bank lines** (P8 step 8, decision 8). A `bank` or `all` run revalues foreign-currency bank
 * balances as well as documents, and a bank line has no document and no partner: it is keyed by
 * the bank account and shows the account's code and name where a document line shows its number
 * and partner — the run screen's treatment at step 7b, from the same helpers. Its open amount is
 * the balance in the account's own currency, its booking rate is blank (a balance is the sum of
 * lines booked at many rates), and it drills to the account's Cashbooks detail at the run date.
 */
export function FxRevaluationReport() {
  const t = useTranslations("gl.fxRevaluationReport");
  const tf = useTranslations("gl.fxRevaluation");
  const tr = useTranslations("reports");

  const runs = useFxRevaluations();
  const [chosenId, setChosenId] = useState<number | null>(null);
  const effective = chosenId ?? runs.data?.[0]?.id ?? null;
  const detail = useFxRevaluation(effective);
  const run = detail.data;

  const company = useCompanyDetails();
  const currencies = useCurrencies();
  const base = (currencies.data ?? []).find((c) => c.is_base);
  const baseLike = {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };
  const money = (value: string | number) =>
    formatMoney(Number(value), baseLike, { showCode: false });
  const currencyById = new Map((currencies.data ?? []).map((c) => [c.id, c]));
  /** The open amount in the line's own currency — `$ 495.00`, not the wire's `495.000000`. */
  const inCurrency = (value: string, currencyId: number) => {
    const currency = currencyById.get(currencyId);
    return formatMoney(Number(value), {
      code: currency?.code ?? "",
      decimalPlaces: currency?.decimal_places ?? 2,
      symbol: currency?.symbol ?? null,
    });
  };

  const lines = run?.lines ?? [];
  const total = lines.reduce((sum, line) => sum + Number(line.difference), 0);

  function handleExport() {
    if (!run) return;
    exportToCsv(
      `fx-revaluation-${run.number}`,
      [
        t("documentColumn"),
        t("partner"),
        t("currency"),
        t("openAmount"),
        t("bookingRate"),
        t("carryingBase"),
        t("rateAtDate"),
        t("revaluedBase"),
        t("difference"),
      ],
      lines.map((line) => [
        lineLabel(line),
        lineName(line),
        line.currency_code,
        line.open_amount,
        line.booking_rate ?? "",
        line.carrying_base,
        line.rate_at_date,
        line.revalued_base,
        line.difference,
      ]),
    );
  }

  return (
    <ReportPage
      title={t("title")}
      subtitle={t("subtitle")}
      companyName={company.data?.name}
      asOfLabel={
        run ? t("runLabel", { number: run.number, date: formatDate(run.revaluation_date) }) : undefined
      }
      onExportCsv={lines.length > 0 ? handleExport : undefined}
      filters={
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-2">
          <Field label={t("pickRun")}>
            <Select
              options={(runs.data ?? []).map((row) => ({
                value: String(row.id),
                label: t("runOption", {
                  number: row.number,
                  date: formatDate(row.revaluation_date),
                  role: tf(`roleLabel.${row.role}`),
                }),
              }))}
              value={String(effective ?? "")}
              onValueChange={(value) => setChosenId(value ? Number(value) : null)}
              ariaLabel={t("pickRun")}
            />
          </Field>
        </div>
      }
    >
      {!run ? (
        <ReportPanel>
          <QueryState
            query={runs}
            isEmpty
            empty={(runs.data ?? []).length === 0 ? t("noRuns") : t("noneChosen")}
            testId="query"
          />
        </ReportPanel>
      ) : (
        <>
          <ReportPanel>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <div>
                  <dt className="text-xs text-[var(--vinea-ink-muted)]">{tr("number")}</dt>
                  <dd
                    className="font-mono text-lg font-semibold text-[var(--vinea-ink)]"
                    data-testid="fx-run-number"
                  >
                    {run.number}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("runDate")}</dt>
                  <dd className="font-mono text-sm text-[var(--vinea-ink)]">
                    {formatDate(run.revaluation_date)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("role")}</dt>
                  <dd className="text-sm text-[var(--vinea-ink)]">{tf(`roleLabel.${run.role}`)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("lineCount")}</dt>
                  <dd
                    className="font-mono text-lg tabular-nums text-[var(--vinea-ink)]"
                    data-testid="fx-line-count"
                  >
                    {formatQuantity(lines.length, 0)}
                  </dd>
                </div>
              </dl>
              <StatusChip
                tone={run.status === FxRevaluationStatus.REVERSED ? "warning" : "success"}
              >
                {tf(`statusLabel.${run.status}`)}
              </StatusChip>
            </div>

            {/* The entries, named. Three of them on a reversed run, and the third is not a
                mirror — see the module docstring. */}
            <div className="flex flex-wrap gap-4 pt-4 print:hidden">
              {run.journal_entry_id !== null && (
                <Link
                  href={`/gl/entries/${run.journal_entry_id}`}
                  data-testid="fx-entry"
                  className="text-xs font-medium text-[var(--vinea-brand)] hover:underline"
                >
                  {t("entry")}
                </Link>
              )}
              {run.mirror_entry_id !== null && (
                <Link
                  href={`/gl/entries/${run.mirror_entry_id}`}
                  data-testid="fx-mirror"
                  className="text-xs font-medium text-[var(--vinea-brand)] hover:underline"
                >
                  {t("mirror")}
                </Link>
              )}
              {run.reversal_entry_id !== null && (
                <Link
                  href={`/gl/entries/${run.reversal_entry_id}`}
                  data-testid="fx-reversal"
                  className="text-xs font-medium text-[var(--vinea-brand)] hover:underline"
                >
                  {t("reversal")}
                </Link>
              )}
            </div>
            <p className="pt-2 text-xs text-[var(--vinea-ink-subtle)]">{t("mirrorNote")}</p>
            <p className="pt-1 text-xs text-[var(--vinea-ink-subtle)]">{t("controlNote")}</p>
          </ReportPanel>

          <ReportPanel>
            <dl>
              <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("totalDifference")}</dt>
              <dd
                className="font-mono text-2xl tabular-nums text-[var(--vinea-ink)]"
                data-testid="fx-total-difference"
              >
                {money(total)}
              </dd>
            </dl>
          </ReportPanel>

          <ReportPanel>
            {lines.length === 0 ? (
              <QueryState query={detail} isEmpty empty={t("noLines")} testId="lines-query" />
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH className="w-28">{t("documentColumn")}</TH>
                    <TH>{t("partner")}</TH>
                    <TH className="w-20">{t("currency")}</TH>
                    <TH className="w-28 text-right">{t("openAmount")}</TH>
                    <TH className="w-28 text-right">{t("bookingRate")}</TH>
                    <TH className="w-32 text-right">{t("carryingBase")}</TH>
                    <TH className="w-28 text-right">{t("rateAtDate")}</TH>
                    <TH className="w-32 text-right">{t("revaluedBase")}</TH>
                    <TH className="w-32 text-right">{t("difference")}</TH>
                  </TR>
                </THead>
                <TBody>
                  {lines.map((line) => (
                    <TR key={lineKey(line)} data-revaluation-line={lineLabel(line)}>
                      <TD>
                        <Link
                          href={lineHref(line, run.revaluation_date)}
                          data-testid={line.document_id !== null ? "fx-line-document" : "fx-line-bank"}
                          className="font-mono text-xs font-semibold text-[var(--vinea-brand)] hover:underline"
                        >
                          {lineLabel(line)}
                        </Link>
                      </TD>
                      <TD className="text-xs text-[var(--vinea-ink)]">{lineName(line)}</TD>
                      <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                        {line.currency_code}
                      </TD>
                      <TD
                        className="text-right font-mono text-xs tabular-nums whitespace-nowrap"
                        data-testid="fx-line-open"
                      >
                        {inCurrency(line.open_amount, line.currency_id)}
                      </TD>
                      <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                        {line.booking_rate !== null ? trimDecimalString(line.booking_rate) : tf("emptyValue")}
                      </TD>
                      <TD className="text-right font-mono text-xs tabular-nums">
                        {money(line.carrying_base)}
                      </TD>
                      <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                        {trimDecimalString(line.rate_at_date)}
                      </TD>
                      <TD
                        className="text-right font-mono text-xs tabular-nums"
                        data-testid="fx-line-revalued"
                      >
                        {money(line.revalued_base)}
                      </TD>
                      <TD
                        className="text-right font-mono text-xs tabular-nums"
                        data-testid="fx-line-difference"
                      >
                        {money(line.difference)}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </ReportPanel>
        </>
      )}
    </ReportPage>
  );
}
