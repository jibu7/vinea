"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { AlertTriangle } from "lucide-react";
import { Button } from "@/design/components/button";
import { Field } from "@/design/components/input";
import { QueryState } from "@/design/components/query-state";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { Tabs, TabsList, TabsTrigger } from "@/design/components/tabs";
import { useToast } from "@/design/components/toast";
import { isApiError, useHasPermission } from "@/features/auth/hooks";
import { useCompanyDetails, useCurrencies } from "@/features/gl/hooks";
import { exportToCsv } from "@/lib/csv";
import { newDraftId } from "@/lib/drafts";
import { dotted, formatDate, formatMoney, formatQuantity } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import {
  useCloseFiscalDay,
  useFiscalDevices,
  useFiscalQueue,
  useXReport,
  useZReports,
} from "../hooks";
import type { DailyFigures, DailyReport } from "../types";

/**
 * Reports → Tax → **Daily fiscal report** (P7 step 8): the X live, the Zs closed, and the act
 * of closing.
 *
 * **Every figure here is what the authority signed**, computed from `fiscal_receipts.request`
 * and not from the ledger. A Z reports what was *declared*; a VAT return reports what was
 * *posted*; on a discounted or fractionally priced line those differ, because the wire carries
 * a two-decimal inclusive price and RWF has no decimals at all. The residue is stated on this
 * page under its own name rather than left for a month-end reconciliation to discover.
 *
 * **Declared figures print at two decimals** even though the base currency has none. Rounding
 * them to the franc on screen would erase the very difference this report exists to state, and
 * the number on the paper in an inspector's hand has the centimes on it.
 *
 * **Close day warns; it does not refuse.** A device with rows still in flight can be closed,
 * and the Z records `queued_rows` on its face so that a day closed over an unsent sale is
 * evidence rather than a silent gap (decision 11). Refusing would make that figure dead and
 * would leave a shop unable to close because a line was down — the outage would become the
 * shop's problem instead of the queue's. So the screen says what closing now means, before the
 * button, and lets the person decide.
 */
export function DailyFiscalReport() {
  const t = useTranslations("fiscal.daily");
  const tr = useTranslations("reports");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canClose = useHasPermission()("fiscal:close_day");

  const [deviceId, setDeviceId] = useState("");
  const [tab, setTab] = useState("x");
  const [openZ, setOpenZ] = useState<number | null>(null);

  const company = useCompanyDetails();
  const currencies = useCurrencies();
  const devices = useFiscalDevices();

  /** Default to the first device rather than making somebody choose on a one-device tenant,
   * which is nearly every tenant this phase serves. */
  const chosen = deviceId ? Number(deviceId) : (devices.data?.[0]?.id ?? null);
  const device = (devices.data ?? []).find((row) => row.id === chosen);

  const x = useXReport(chosen);
  const zs = useZReports(chosen);
  const queue = useFiscalQueue(chosen);
  const close = useCloseFiscalDay();

  const pending = queue.data?.[0]?.pending_rows ?? 0;

  const base = (currencies.data ?? []).find((c) => c.is_base);
  /** The **declared** scale: two decimals, whatever the currency's own places are. See the
   * class docstring — this is where the wire's precision is preserved on purpose. */
  const declaredLike = {
    code: base?.code ?? "",
    decimalPlaces: 2,
    symbol: base?.symbol ?? null,
  };
  const money = (value: string | number) =>
    formatMoney(Number(value), declaredLike, { showCode: false });

  const shown: DailyReport | undefined =
    tab === "x" ? x.data : (zs.data ?? []).find((z) => z.report_no === openZ) ?? zs.data?.[0];
  const figures = shown?.figures;

  async function handleClose() {
    if (chosen === null) return;
    try {
      const report = await close.mutateAsync({
        deviceId: chosen,
        idempotencyKey: newDraftId(),
      });
      toast.show({ title: t("closed", { number: report.number ?? "" }), tone: "success" });
      setTab("z");
      setOpenZ(report.report_no);
    } catch (err) {
      if (isApiError(err)) toast.show({ title: err.message, tone: "danger" });
      else showApiError(err, t("closeFailed"));
    }
  }

  function handleExport() {
    if (!figures) return;
    exportToCsv(
      `fiscal-day-${shown?.number ?? "x"}`,
      [t("taxClass"), t("rate"), t("taxableNs"), t("taxNs"), t("taxableNr"), t("taxNr")],
      Object.entries(figures.classes).map(([code, totals]) => [
        code,
        totals.rate,
        totals.taxable_ns,
        totals.tax_ns,
        totals.taxable_nr,
        totals.tax_nr,
      ]),
    );
  }

  return (
    <ReportPage
      title={t("title")}
      subtitle={t("subtitle")}
      companyName={company.data?.name}
      asOfLabel={
        shown
          ? dotted(
              shown.number ?? t("xHeading"),
              t("sdcBlock", {
                sdc: device?.sdc_id ?? "",
                mrc: device?.mrc_no ?? "",
              }),
              tr("dateRange", {
                from: formatDate(shown.from_at),
                to: formatDate(shown.to_at),
              }),
            )
          : undefined
      }
      onExportCsv={figures ? handleExport : undefined}
      filters={
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-3">
          <Field label={t("device")}>
            <Select
              options={(devices.data ?? []).map((row) => ({
                value: String(row.id),
                label: row.sdc_id ?? String(row.id),
              }))}
              value={String(chosen ?? "")}
              onValueChange={(value) => {
                setDeviceId(value);
                setOpenZ(null);
              }}
              ariaLabel={t("device")}
            />
          </Field>
          <div className="flex items-end sm:col-span-2">
            <Tabs value={tab} onValueChange={setTab}>
              <TabsList>
                <TabsTrigger value="x">{t("xTab")}</TabsTrigger>
                <TabsTrigger value="z">{t("zTab")}</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
        </div>
      }
    >
      {chosen === null ? (
        <ReportPanel>
          <p className="text-sm text-[var(--vinea-ink-muted)]">{t("noDevice")}</p>
        </ReportPanel>
      ) : (
        <>
          {tab === "x" && (
            <ReportPanel>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold text-[var(--vinea-ink)]">
                    {t("xHeading")}
                  </h2>
                  <p className="pt-1 text-xs text-[var(--vinea-ink-subtle)]">{t("xNote")}</p>
                  {x.data && (
                    <p className="pt-1 text-xs text-[var(--vinea-ink-muted)]">
                      {dotted(
                        `${t("openedAt")} ${formatDate(x.data.from_at)}`,
                        `${t("takenAt")} ${formatDate(x.data.to_at)}`,
                      )}
                    </p>
                  )}
                </div>

                {/* The refusal-shaped warning, shown *before* the button and never instead of
                    it. Closing over rows in flight is allowed and recorded; what is not
                    allowed is doing it without being told. */}
                <div className="flex flex-col items-end gap-2 print:hidden">
                  {pending > 0 ? (
                    <div
                      data-testid="close-day-warning"
                      className="flex max-w-sm items-start gap-2 rounded-[var(--radius-card)] border border-[var(--vinea-warning)] bg-[var(--vinea-warning-soft)] p-3 text-xs text-[var(--vinea-warning)]"
                    >
                      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                      <div>
                        <p className="font-semibold">{t("pendingWarningTitle")}</p>
                        <p>{t("pendingWarning", { count: pending })}</p>
                      </div>
                    </div>
                  ) : (
                    <p
                      data-testid="close-day-clear"
                      className="text-xs text-[var(--vinea-ink-subtle)]"
                    >
                      {t("queueClear")}
                    </p>
                  )}
                  {canClose && (
                    <Button
                      variant="primary"
                      onClick={handleClose}
                      disabled={close.isPending}
                      data-testid="close-day"
                      className="text-xs"
                    >
                      {close.isPending ? t("closing") : t("closeDay")}
                    </Button>
                  )}
                </div>
              </div>
            </ReportPanel>
          )}

          {tab === "z" && (
            <ReportPanel>
              {(zs.data ?? []).length === 0 ? (
                <QueryState query={zs} isEmpty empty={t("noZ")} testId="z-query" />
              ) : (
                <Table>
                  <THead>
                    <TR>
                      <TH className="w-28">{t("zNumber")}</TH>
                      <TH className="w-16 text-right">{t("reportNo")}</TH>
                      <TH className="w-56">{t("range")}</TH>
                      <TH className="w-20 text-right">{t("nsCount")}</TH>
                      <TH className="w-20 text-right">{t("nrCount")}</TH>
                      <TH className="w-32 text-right">{t("netGross")}</TH>
                      <TH className="w-20 print:hidden" />
                    </TR>
                  </THead>
                  <TBody>
                    {(zs.data ?? []).map((report) => (
                      <TR key={report.report_no}>
                        <TD>
                          <span
                            data-testid="z-number"
                            className="font-mono text-xs font-semibold text-[var(--vinea-ink)]"
                          >
                            {report.number}
                          </span>
                        </TD>
                        <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                          {report.report_no}
                        </TD>
                        <TD className="text-xs text-[var(--vinea-ink-muted)]">
                          {tr("dateRange", {
                            from: formatDate(report.from_at),
                            to: formatDate(report.to_at),
                          })}
                        </TD>
                        <TD
                          className="text-right font-mono text-xs tabular-nums"
                          data-testid="z-ns-count"
                        >
                          {report.figures.ns_count}
                        </TD>
                        <TD
                          className="text-right font-mono text-xs tabular-nums"
                          data-testid="z-nr-count"
                        >
                          {report.figures.nr_count}
                        </TD>
                        <TD
                          className="text-right font-mono text-xs tabular-nums"
                          data-testid="z-net"
                        >
                          {money(report.figures.net_gross)}
                        </TD>
                        <TD className="print:hidden">
                          <button
                            type="button"
                            onClick={() => setOpenZ(report.report_no)}
                            data-testid="open-z"
                            className="text-xs font-medium text-[var(--vinea-brand)] hover:underline"
                          >
                            {t("openZ")}
                          </button>
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              )}
            </ReportPanel>
          )}

          {figures ? (
            <DayFigures figures={figures} report={shown} money={money} />
          ) : (
            <ReportPanel>
              <QueryState query={tab === "x" ? x : zs} isEmpty empty={t("noZ")} testId="query" />
            </ReportPanel>
          )}
        </>
      )}
    </ReportPage>
  );
}

/** The §19.1 body of a day — the same layout for an X and a Z, because they are the same
 * computation and a reader comparing one to the other should not have to re-find anything. */
function DayFigures({
  figures,
  report,
  money,
}: {
  figures: DailyFigures;
  report: DailyReport | undefined;
  money: (value: string | number) => string;
}) {
  const t = useTranslations("fiscal.daily");

  return (
    <>
      <ReportPanel>
        <h2 className="pb-3 text-sm font-semibold text-[var(--vinea-ink)]">
          {report?.number ?? t("theDay")}
        </h2>
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Figure label={t("nsCount")} value={String(figures.ns_count)} testId="day-ns-count" />
          <Figure label={t("nsGross")} value={money(figures.ns_gross)} testId="day-ns-gross" />
          <Figure label={t("nrCount")} value={String(figures.nr_count)} testId="day-nr-count" />
          <Figure label={t("nrGross")} value={money(figures.nr_gross)} testId="day-nr-gross" />
          <Figure label={t("netGross")} value={money(figures.net_gross)} testId="day-net" />
          <Figure label={t("totalTax")} value={money(figures.total_tax)} testId="day-tax" />
          {/* Rule 13's quantity: how many *things* were sold, not how many lines. */}
          <Figure
            label={t("itemsNs")}
            value={formatQuantity(Number(figures.items_ns), 0)}
            testId="day-items-ns"
          />
          <Figure
            label={t("itemsNr")}
            value={formatQuantity(Number(figures.items_nr), 0)}
            testId="day-items-nr"
          />
          <Figure label={t("discounts")} value={money(figures.discounts)} />
          <Figure label={t("copies")} value={String(figures.copies_count)} testId="day-copies" />
          <Figure label={t("copiesGross")} value={money(figures.copies_gross)} />
          <Figure
            label={t("queuedRows")}
            value={String(figures.queued_rows)}
            testId="day-queued-rows"
          />
        </dl>
      </ReportPanel>

      <ReportPanel>
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Figure label={t("postedNet")} value={money(figures.posted_net)} testId="day-posted" />
          <Figure
            label={t("residue")}
            value={money(figures.declared_less_posted)}
            testId="day-residue"
          />
        </dl>
        <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">{t("residueNote")}</p>
      </ReportPanel>

      <ReportPanel>
        <h2 className="pb-2 text-sm font-semibold text-[var(--vinea-ink)]">{t("byClass")}</h2>
        {Object.keys(figures.classes).length === 0 ? (
          <p className="text-sm text-[var(--vinea-ink-muted)]">{t("noClasses")}</p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-20">{t("taxClass")}</TH>
                <TH className="w-20 text-right">{t("rate")}</TH>
                <TH className="text-right">{t("taxableNs")}</TH>
                <TH className="text-right">{t("taxNs")}</TH>
                <TH className="text-right">{t("taxableNr")}</TH>
                <TH className="text-right">{t("taxNr")}</TH>
              </TR>
            </THead>
            <TBody>
              {Object.entries(figures.classes).map(([code, totals]) => (
                <TR key={code}>
                  <TD className="font-mono text-xs font-semibold text-[var(--vinea-ink)]">
                    {code}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                    {totals.rate}
                  </TD>
                  <TD
                    className="text-right font-mono text-xs tabular-nums"
                    data-testid={`class-${code}-taxable-ns`}
                  >
                    {money(totals.taxable_ns)}
                  </TD>
                  <TD
                    className="text-right font-mono text-xs tabular-nums"
                    data-testid={`class-${code}-tax-ns`}
                  >
                    {money(totals.tax_ns)}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {money(totals.taxable_nr)}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {money(totals.tax_nr)}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </ReportPanel>

      <ReportPanel>
        <h2 className="pb-2 text-sm font-semibold text-[var(--vinea-ink)]">{t("byPayment")}</h2>
        {/* Sales and refunds are never netted here. The method on a refund is the credit
            note's own, so netting would take money out of whichever drawer the credit note
            names rather than the one the sale went into — and a reader could not take it
            apart again afterwards. */}
        <Table>
          <THead>
            <TR>
              <TH>{t("method")}</TH>
              <TH className="w-40 text-right">{t("sales")}</TH>
              <TH className="w-40 text-right">{t("refunds")}</TH>
            </TR>
          </THead>
          <TBody>
            {Array.from(
              new Set([
                ...Object.keys(figures.by_payment_method),
                ...Object.keys(figures.refunds_by_payment_method),
              ]),
            ).map((method) => (
              <TR key={method}>
                <TD className="text-xs text-[var(--vinea-ink)]">{method}</TD>
                <TD
                  className="text-right font-mono text-xs tabular-nums"
                  data-testid={`payment-${method}`}
                >
                  {money(figures.by_payment_method[method] ?? "0")}
                </TD>
                <TD className="text-right font-mono text-xs tabular-nums">
                  {money(figures.refunds_by_payment_method[method] ?? "0")}
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </ReportPanel>
    </>
  );
}

function Figure({
  label,
  value,
  testId,
}: {
  label: string;
  value: string;
  testId?: string;
}) {
  return (
    <div>
      <dt className="text-xs text-[var(--vinea-ink-muted)]">{label}</dt>
      <dd
        className="font-mono text-lg tabular-nums text-[var(--vinea-ink)]"
        data-testid={testId}
      >
        {value}
      </dd>
    </div>
  );
}
