"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Combobox } from "@/design/components/combobox";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Field } from "@/design/components/input";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useCompanyDetails, useCurrencies } from "@/features/gl/hooks";
import { exportToCsv } from "@/lib/csv";
import { formatDate, formatMoney } from "@/lib/format";
import { useAgeing, useAgeingBucketSets } from "../hooks";
import type { PartnerRole } from "../types";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Age analysis over `GET /{role}/ageing` — the same figures the step 8 acceptance test
 * reconciles against the control account, so this screen adds presentation and nothing else. */
export function AgeAnalysisReport({ role }: { role: PartnerRole }) {
  const t = useTranslations("reports");
  const [asOf, setAsOf] = useState(today);
  const [bucketSetId, setBucketSetId] = useState("");
  // Off by default: a partner with nothing outstanding has nothing to age, and on a real
  // customer master those rows are most of the report. The filter is the server's, so the
  // CSV below and the printed page carry exactly the rows on screen.
  const [includeZeroBalances, setIncludeZeroBalances] = useState(false);

  const bucketSets = useAgeingBucketSets();
  // Land on the company's default set rather than an empty picker reading "Loading…" after the
  // data has arrived — the report already runs on the default, so the control should say so.
  useEffect(() => {
    if (bucketSetId || !bucketSets.data?.length) return;
    const preferred = bucketSets.data.find((set) => set.is_default) ?? bucketSets.data[0];
    setBucketSetId(String(preferred.id));
  }, [bucketSets.data, bucketSetId]);
  const ageing = useAgeing(role, {
    asOf,
    bucketSetId: bucketSetId ? Number(bucketSetId) : undefined,
    includeZeroBalances,
  });
  const currencies = useCurrencies();
  const company = useCompanyDetails();
  const base = (currencies.data ?? []).find((c) => c.is_base);
  const baseLike = {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };

  const report = ageing.data;
  const bucketLabels = useMemo(
    () => report?.totals.map((bucket) => bucket.label) ?? [],
    [report],
  );

  function handleExport() {
    if (!report) return;
    exportToCsv(
      `age-analysis-${role}-${asOf}`,
      [t("code"), t("name"), ...bucketLabels, t("total")],
      [
        ...report.rows.map((row) => [
          row.partner_code ?? "",
          row.partner_name,
          ...row.buckets.map((bucket) => bucket.amount),
          row.total,
        ]),
        ["", t("grandTotal"), ...report.totals.map((b) => b.amount), report.grand_total],
      ],
    );
  }

  return (
    <ReportPage
      title={t(role === "ar" ? "ageTitleAr" : "ageTitleAp")}
      subtitle={t("ageSubtitle")}
      companyName={company.data?.name}
      asOfLabel={t("asOf", { date: formatDate(asOf) })}
      onExportCsv={report ? handleExport : undefined}
      filters={
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-3">
          <Field label={t("asOfDate")}>
            <IsoDatePicker value={asOf} onValueChange={setAsOf} />
          </Field>
          <Field label={t("bucketSet")}>
            <Combobox
              options={(bucketSets.data ?? []).map((set) => ({
                value: String(set.id),
                label: `${set.code} · ${set.name}`,
              }))}
              value={bucketSetId}
              onValueChange={setBucketSetId}
              placeholder={t("loading")}
            />
          </Field>
          <label className="flex items-center gap-2 self-end pb-2.5 text-xs text-[var(--vinea-ink-muted)]">
            <input
              type="checkbox"
              checked={includeZeroBalances}
              onChange={(e) => setIncludeZeroBalances(e.target.checked)}
              className="size-3.5"
            />
            {t("includeZeroBalances")}
          </label>
        </div>
      }
    >
      <ReportPanel>
        {!report || report.rows.length === 0 ? (
          <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {ageing.isLoading ? t("loading") : t("noRows")}
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-28">{t("code")}</TH>
                <TH>{t("name")}</TH>
                {bucketLabels.map((label) => (
                  <TH key={label} className="text-right">
                    {label}
                  </TH>
                ))}
                <TH className="text-right">{t("total")}</TH>
              </TR>
            </THead>
            <TBody>
              {report.rows.map((row) => (
                <TR key={row.partner_id}>
                  <TD className="font-mono text-xs text-[var(--vinea-brand)]">
                    {row.partner_code}
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink)]">{row.partner_name}</TD>
                  {row.buckets.map((bucket) => (
                    <TD
                      key={bucket.label}
                      className="text-right font-mono text-xs tabular-nums"
                    >
                      {formatMoney(Number(bucket.amount), baseLike, { showCode: false })}
                    </TD>
                  ))}
                  <TD className="text-right font-mono text-xs font-semibold tabular-nums">
                    {formatMoney(Number(row.total), baseLike, { showCode: false })}
                  </TD>
                </TR>
              ))}
              <TR className="border-t-2 border-[var(--vinea-border-strong)]">
                <TD />
                <TD className="text-xs font-semibold">{t("grandTotal")}</TD>
                {report.totals.map((bucket) => (
                  <TD
                    key={bucket.label}
                    className="text-right font-mono text-xs font-semibold tabular-nums"
                  >
                    {formatMoney(Number(bucket.amount), baseLike, { showCode: false })}
                  </TD>
                ))}
                <TD className="text-right font-mono text-xs font-semibold tabular-nums">
                  {formatMoney(Number(report.grand_total), baseLike)}
                </TD>
              </TR>
            </TBody>
          </Table>
        )}
      </ReportPanel>
    </ReportPage>
  );
}
