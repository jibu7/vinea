"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Download } from "lucide-react";
import { Button } from "@/design/components/button";
import { Field } from "@/design/components/input";
import { QueryState } from "@/design/components/query-state";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useCompanyDetails, useCurrencies } from "@/features/gl/hooks";
import { downloadFromApi } from "@/lib/api";
import { VatReturnStatus } from "@/lib/api-enums";
import { dotted, formatDate, formatMoney } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { useVatReturn, useVatReturns } from "../hooks";

/**
 * Reports → Tax → **VAT return** (P7 step 8) — a *filed* return, and its two annexes.
 *
 * The screen under Transactions runs a range and files it: it is an act. This one opens what
 * was filed, which is a different thing and is why it renders `vat_returns.figures` — the
 * JSONB snapshot taken at filing — and never a recomputation. That is what makes "a filed
 * return never changes" observable on the screen rather than merely asserted in a test: an
 * entry posted into a filed month appears on the *next* return under late entries, and this
 * page shows the same figures it showed before that entry existed.
 *
 * **The tie is on the page, not behind a link.** Decision 12's word is *reconciled*, not
 * balanced: a VAT payment to RRA and a manual journal without a tax code are real movements on
 * `2200` that no tax line explains, and the return's job is to list them by name. A screen that
 * printed the sections alone would be hiding the one thing an accountant opens it for.
 *
 * **The annexes come from the server.** They are the authority's own listings, computed over
 * data no screen holds; rebuilding them in TypeScript would be a second implementation of a
 * filing. `downloadFromApi` fetches the real CSV, with the filename the server chose.
 */
export function VatReturnReport() {
  const t = useTranslations("tax.vatReturnReport");
  const tv = useTranslations("tax.vatReturn");
  const tr = useTranslations("reports");
  const showApiError = useApiErrorToast();

  const returns = useVatReturns();
  const [chosenId, setChosenId] = useState<number | null>(null);
  const effective = chosenId ?? returns.data?.[0]?.id ?? null;
  const detail = useVatReturn(effective);
  const filed = detail.data;

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

  async function annex(kind: "sales" | "purchases") {
    if (!filed) return;
    const query = `period_from=${filed.period_from}&period_to=${filed.period_to}`;
    try {
      await downloadFromApi(
        `/tax/vat-returns/annexes/${kind}.csv?${query}`,
        `vat-${kind}-${filed.period_from}-${filed.period_to}.csv`,
      );
    } catch (err) {
      showApiError(err, kind === "sales" ? t("salesAnnex") : t("purchasesAnnex"));
    }
  }

  const sections = filed?.figures.sections;
  const rows: Array<[string, string]> = sections
    ? [
        [tv("salesStandardBase"), money(sections.sales_standard_base)],
        [tv("salesStandardVat"), money(sections.sales_standard_vat)],
        [tv("salesZeroRated"), money(sections.sales_zero_rated_base)],
        [tv("salesExempt"), money(sections.sales_exempt_base)],
        [tv("purchasesStandardBase"), money(sections.purchases_standard_base)],
        [tv("purchasesStandardVat"), money(sections.purchases_standard_vat)],
        [tv("purchasesImportsBase"), money(sections.purchases_imports_base)],
        [tv("purchasesImportsVat"), money(sections.purchases_imports_vat)],
        [tv("purchasesZeroRated"), money(sections.purchases_zero_rated_base)],
        [tv("purchasesExempt"), money(sections.purchases_exempt_base)],
      ]
    : [];

  return (
    <ReportPage
      title={t("title")}
      subtitle={t("subtitle")}
      companyName={company.data?.name}
      asOfLabel={
        filed
          ? t("periodLabel", {
              from: formatDate(filed.period_from),
              to: formatDate(filed.period_to),
            })
          : undefined
      }
      actions={
        filed ? (
          <>
            <Button
              variant="secondary"
              onClick={() => annex("sales")}
              data-testid="annex-sales"
              className="gap-1.5 text-xs"
            >
              <Download className="size-3.5" /> {t("salesAnnex")}
            </Button>
            <Button
              variant="secondary"
              onClick={() => annex("purchases")}
              data-testid="annex-purchases"
              className="gap-1.5 text-xs"
            >
              <Download className="size-3.5" /> {t("purchasesAnnex")}
            </Button>
          </>
        ) : undefined
      }
      filters={
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-2">
          <Field label={t("pickReturn")}>
            <Select
              options={(returns.data ?? []).map((row) => ({
                value: String(row.id),
                label: `${row.number} — ${row.period_from} · ${row.period_to}`,
              }))}
              value={String(effective ?? "")}
              onValueChange={(value) => setChosenId(value ? Number(value) : null)}
              ariaLabel={t("pickReturn")}
            />
          </Field>
          <p className="self-end pb-2 text-xs text-[var(--vinea-ink-subtle)]">
            {t("pickReturnHint")}
          </p>
        </div>
      }
    >
      {!filed ? (
        <ReportPanel>
          <QueryState
            query={returns}
            isEmpty
            empty={(returns.data ?? []).length === 0 ? t("noReturns") : t("noneChosen")}
            testId="query"
          />
        </ReportPanel>
      ) : (
        <>
          <ReportPanel>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p
                  data-testid="report-return-number"
                  className="font-mono text-lg font-semibold text-[var(--vinea-ink)]"
                >
                  {filed.number}
                </p>
                <p className="text-xs text-[var(--vinea-ink-muted)]">
                  {t("filedOn", { date: formatDate(filed.filed_at) })}
                </p>
              </div>
              <StatusChip
                tone={filed.status === VatReturnStatus.REVERSED ? "warning" : "success"}
              >
                {filed.status === VatReturnStatus.REVERSED
                  ? t("reversedBadge")
                  : t("postedBadge")}
              </StatusChip>
            </div>
          </ReportPanel>

          <ReportPanel>
            <h2 className="pb-2 text-sm font-semibold text-[var(--vinea-ink)]">
              {t("sectionsHeading")}
            </h2>
            <Table>
              <THead>
                <TR>
                  <TH>{t("section")}</TH>
                  <TH className="w-40 text-right">{tr("amount")}</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map(([label, value]) => (
                  <TR key={label}>
                    <TD className="text-xs text-[var(--vinea-ink)]">{label}</TD>
                    <TD className="text-right font-mono text-xs tabular-nums">{value}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>

            <dl className="grid grid-cols-1 gap-4 pt-4 sm:grid-cols-3">
              <div>
                <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("outputVat")}</dt>
                <dd
                  className="font-mono text-lg tabular-nums text-[var(--vinea-ink)]"
                  data-testid="report-output-vat"
                >
                  {money(filed.output_vat)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("inputVat")}</dt>
                <dd
                  className="font-mono text-lg tabular-nums text-[var(--vinea-ink)]"
                  data-testid="report-input-vat"
                >
                  {money(filed.input_vat)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-[var(--vinea-ink-muted)]">
                  {Number(filed.net_payable) < 0 ? t("netCredit") : t("netPayable")}
                </dt>
                <dd
                  className="font-mono text-2xl tabular-nums text-[var(--vinea-ink)]"
                  data-testid="report-net-payable"
                >
                  {money(filed.net_payable)}
                </dd>
              </div>
            </dl>
          </ReportPanel>

          <ReportPanel>
            <h2 className="text-sm font-semibold text-[var(--vinea-ink)]">{t("tieHeading")}</h2>
            <p className="pb-2 pt-1 text-xs text-[var(--vinea-ink-subtle)]">{t("tieNote")}</p>
            <Table>
              <THead>
                <TR>
                  <TH className="w-24">{t("account")}</TH>
                  <TH className="w-36 text-right">{t("movement")}</TH>
                  <TH className="w-36 text-right">{t("declared")}</TH>
                  <TH className="w-36 text-right">{tr("total")}</TH>
                  <TH>{t("untagged")}</TH>
                </TR>
              </THead>
              <TBody>
                {filed.figures.ties.map((tie) => (
                  <TR key={tie.account_code}>
                    <TD className="font-mono text-xs font-semibold text-[var(--vinea-ink)]">
                      {tie.account_code}
                    </TD>
                    <TD
                      className="text-right font-mono text-xs tabular-nums"
                      data-testid={`report-tie-movement-${tie.account_code}`}
                    >
                      {money(tie.movement)}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums">
                      {money(tie.declared_in_range)}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums">
                      {money(tie.difference)}
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {tie.untagged.length === 0 ? (
                        <StatusChip tone="success">{t("reconciled")}</StatusChip>
                      ) : (
                        <ul className="space-y-0.5">
                          {tie.untagged.map((line, index) => (
                            <li key={`${line.entry_number}-${index}`}>
                              <span className="font-mono">{line.entry_number}</span>
                              {dotted(line.description, money(line.base_amount))}
                            </li>
                          ))}
                        </ul>
                      )}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </ReportPanel>

          <ReportPanel>
            <h2 className="text-sm font-semibold text-[var(--vinea-ink)]">
              {t("settlementHeading")}
            </h2>
            <p className="pb-2 pt-1 text-xs text-[var(--vinea-ink-subtle)]">
              {t("settlementNote")}
            </p>
            <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("highWater")}</dt>
                <dd
                  className="font-mono text-sm tabular-nums text-[var(--vinea-ink)]"
                  data-testid="report-high-water"
                >
                  {filed.high_water_entry_id}
                </dd>
                <dd className="pt-1 text-xs text-[var(--vinea-ink-subtle)]">
                  {t("highWaterNote")}
                </dd>
              </div>
              {filed.journal_entry_id !== null && (
                <div className="print:hidden">
                  <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("entryNumber")}</dt>
                  <dd>
                    <Link
                      href={`/gl/entries/${filed.journal_entry_id}`}
                      data-testid="report-settlement-entry"
                      className="text-xs font-medium text-[var(--vinea-brand)] hover:underline"
                    >
                      {t("openEntry")}
                    </Link>
                  </dd>
                </div>
              )}
            </dl>
            <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">{t("annexNote")}</p>
          </ReportPanel>
        </>
      )}
    </ReportPage>
  );
}
