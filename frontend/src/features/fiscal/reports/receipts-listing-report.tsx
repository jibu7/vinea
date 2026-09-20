"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Field } from "@/design/components/input";
import { QueryState } from "@/design/components/query-state";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useCompanyDetails, useCurrencies } from "@/features/gl/hooks";
import { exportToCsv } from "@/lib/csv";
import { dotted, formatDate, formatMoney, monthToDateIso } from "@/lib/format";
import { useFiscalDevices, useReceiptListing } from "../hooks";

/**
 * Reports → Tax → **Fiscal receipts listing** (P7 step 8) — the accountant's tie.
 *
 * Not a listing of receipts: that is the enquiry. This is the screen a month is closed on, and
 * its job is to put two independently-derived figures beside each other and then **name every
 * document that is on one side and not the other**. A report that only printed receipts would
 * answer a question nobody was asking — the receipts are not in doubt, the agreement is.
 *
 * **Reconciled, not balanced**, which is decision 12's word for the VAT return and the right
 * word here for the same reason. The two sides are cut on different axes: a receipt belongs to
 * the day the device *signed* it and a document to the day it is *dated*, so a sale keyed on
 * the 31st and drained on the 1st sits on opposite sides of a month end, and so does the `NR`
 * for a reversal of last week's invoice. Fudging an axis to make the totals match would hide
 * exactly the rows worth looking at.
 *
 * **Declared figures print at two decimals**, the ledger's at the currency's own — because
 * that difference *is* the residue, and rounding the wire's figure to the franc on screen would
 * erase the thing this report exists to show.
 */
export function FiscalReceiptsListingReport() {
  const t = useTranslations("fiscal.receiptsListing");
  const tr = useTranslations("reports");

  const initial = monthToDateIso();
  const [deviceId, setDeviceId] = useState("");
  const [dateFrom, setDateFrom] = useState(initial.from);
  const [dateTo, setDateTo] = useState(initial.to);

  const company = useCompanyDetails();
  const currencies = useCurrencies();
  const devices = useFiscalDevices();
  const chosen = deviceId ? Number(deviceId) : (devices.data?.[0]?.id ?? null);
  const listing = useReceiptListing(chosen, dateFrom, dateTo);
  const data = listing.data;

  const base = (currencies.data ?? []).find((c) => c.is_base);
  const ledgerLike = {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };
  const declaredLike = { ...ledgerLike, decimalPlaces: 2 };
  const posted = (value: string | number) =>
    formatMoney(Number(value), ledgerLike, { showCode: false });
  const declared = (value: string | number) =>
    formatMoney(Number(value), declaredLike, { showCode: false });

  function handleExport() {
    if (!data) return;
    exportToCsv(
      `fiscal-receipts-listing-${dateFrom}-${dateTo}`,
      [
        t("counter"),
        t("documentColumn"),
        t("documentDate"),
        t("customer"),
        t("declaredGross"),
        t("declaredTax"),
        t("postedTotal"),
        t("signedAt"),
        t("straddle"),
      ],
      data.receipts.map((row) => [
        row.receipt_number,
        row.document_number,
        row.document_date,
        row.partner_name,
        row.declared_gross,
        row.declared_tax,
        row.posted_base_total ?? "",
        row.sdc_datetime,
        row.outside_range ?? "",
      ]),
    );
  }

  return (
    <ReportPage
      title={t("title")}
      subtitle={t("subtitle")}
      companyName={company.data?.name}
      asOfLabel={dotted(
        data?.device_label,
        data?.sdc_id,
        tr("dateRange", { from: formatDate(dateFrom), to: formatDate(dateTo) }),
      )}
      onExportCsv={data && data.receipts.length > 0 ? handleExport : undefined}
      filters={
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-3">
          <Field label={t("device")}>
            <Select
              options={(devices.data ?? []).map((row) => ({
                value: String(row.id),
                label: row.sdc_id ?? String(row.id),
              }))}
              value={String(chosen ?? "")}
              onValueChange={setDeviceId}
              ariaLabel={t("device")}
            />
          </Field>
          <Field label={tr("from")}>
            <IsoDatePicker value={dateFrom} onValueChange={setDateFrom} />
          </Field>
          <Field label={tr("to")}>
            <IsoDatePicker value={dateTo} onValueChange={setDateTo} />
          </Field>
        </div>
      }
    >
      {chosen === null ? (
        <ReportPanel>
          <p className="text-sm text-[var(--vinea-ink-muted)]">{t("noDevice")}</p>
        </ReportPanel>
      ) : !data ? (
        <ReportPanel>
          <QueryState query={listing} isEmpty empty={t("noReceipts")} testId="query" />
        </ReportPanel>
      ) : (
        <>
          <ReportPanel>
            <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
              <div>
                <h2 className="pb-2 text-sm font-semibold text-[var(--vinea-ink)]">
                  {t("declaredHeading")}
                </h2>
                <dl className="grid grid-cols-2 gap-3">
                  <Figure label={t("nsCount")} value={String(data.ns_count)} testId="tie-ns-count" />
                  <Figure label={t("nrCount")} value={String(data.nr_count)} testId="tie-nr-count" />
                  <Figure
                    label={t("nsGross")}
                    value={declared(data.ns_gross)}
                    testId="tie-ns-gross"
                  />
                  <Figure label={t("nsTax")} value={declared(data.ns_tax)} testId="tie-ns-tax" />
                  <Figure label={t("nrGross")} value={declared(data.nr_gross)} />
                  <Figure label={t("nrTax")} value={declared(data.nr_tax)} />
                </dl>
                <dl className="pt-3">
                  <Figure
                    label={t("declaredNet")}
                    value={declared(data.declared_net)}
                    testId="tie-declared-net"
                    large
                  />
                </dl>
              </div>
              <div>
                <h2 className="pb-2 text-sm font-semibold text-[var(--vinea-ink)]">
                  {t("ledgerHeading")}
                </h2>
                <dl className="grid grid-cols-2 gap-3">
                  <Figure
                    label={t("invoiceCount")}
                    value={String(data.ledger_invoice_count)}
                    testId="tie-invoice-count"
                  />
                  <Figure
                    label={t("creditNoteCount")}
                    value={String(data.ledger_credit_note_count)}
                    testId="tie-credit-note-count"
                  />
                  <Figure
                    label={t("invoiceTotal")}
                    value={posted(data.ledger_invoice_total)}
                    testId="tie-invoice-total"
                  />
                  <Figure
                    label={t("creditNoteTotal")}
                    value={posted(data.ledger_credit_note_total)}
                  />
                </dl>
                <dl className="pt-3">
                  <Figure
                    label={t("ledgerNet")}
                    value={posted(data.ledger_net)}
                    testId="tie-ledger-net"
                    large
                  />
                </dl>
              </div>
            </div>

            <div className="mt-4 border-t border-[var(--vinea-border)] pt-4">
              <dl>
                <Figure
                  label={t("difference")}
                  value={declared(data.difference)}
                  testId="tie-difference"
                  large
                />
              </dl>
              <p className="pt-2 text-xs text-[var(--vinea-ink-subtle)]">{t("reconciledNote")}</p>
              <p className="pt-1 text-xs text-[var(--vinea-ink-subtle)]">{t("axisNote")}</p>
            </div>
          </ReportPanel>

          <ReportPanel>
            <h2 className="text-sm font-semibold text-[var(--vinea-ink)]">{t("onlyInLedger")}</h2>
            <p className="pb-2 pt-1 text-xs text-[var(--vinea-ink-subtle)]">
              {t("onlyInLedgerNote")}
            </p>
            {data.only_in_ledger.length === 0 ? (
              <p className="text-sm text-[var(--vinea-ink-muted)]">{t("noneOnlyInLedger")}</p>
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH className="w-28">{t("documentColumn")}</TH>
                    <TH className="w-24">{t("documentDate")}</TH>
                    <TH>{t("customer")}</TH>
                    <TH className="w-28">{t("kind")}</TH>
                    <TH className="w-32 text-right">{t("amount")}</TH>
                    <TH className="w-64">{t("waitingOn")}</TH>
                  </TR>
                </THead>
                <TBody>
                  {data.only_in_ledger.map((row) => (
                    <TR key={row.document_id}>
                      <TD>
                        <Link
                          href={`/ar/documents/${row.document_id}`}
                          data-testid="only-ledger-document"
                          className="font-mono text-xs font-semibold text-[var(--vinea-brand)] hover:underline"
                        >
                          {row.number}
                        </Link>
                      </TD>
                      <TD className="text-xs text-[var(--vinea-ink-muted)]">
                        {formatDate(row.document_date)}
                      </TD>
                      <TD className="text-xs text-[var(--vinea-ink)]">{row.partner_name}</TD>
                      <TD className="text-xs text-[var(--vinea-ink-muted)]">{row.kind}</TD>
                      <TD
                        className="text-right font-mono text-xs tabular-nums"
                        data-testid="only-ledger-amount"
                      >
                        {posted(row.base_total_amount)}
                      </TD>
                      <TD>
                        <StatusChip tone="warning">
                          <span data-testid="only-ledger-reason">
                            {t(`reason.${row.reason}` as never)}
                          </span>
                        </StatusChip>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </ReportPanel>

          <ReportPanel>
            <h2 className="text-sm font-semibold text-[var(--vinea-ink)]">{t("onlyOnReceipts")}</h2>
            <p className="pb-2 pt-1 text-xs text-[var(--vinea-ink-subtle)]">
              {t("onlyOnReceiptsNote")}
            </p>
            {data.only_on_receipts.length === 0 ? (
              <p className="text-sm text-[var(--vinea-ink-muted)]">{t("noneOnlyOnReceipts")}</p>
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH className="w-24">{t("counter")}</TH>
                    <TH className="w-28">{t("documentColumn")}</TH>
                    <TH className="w-24">{t("documentDate")}</TH>
                    <TH>{t("customer")}</TH>
                    <TH className="w-32 text-right">{t("declaredGross")}</TH>
                    <TH className="w-56">{t("straddle")}</TH>
                  </TR>
                </THead>
                <TBody>
                  {data.only_on_receipts.map((row) => (
                    <TR key={row.receipt_id}>
                      <TD className="font-mono text-xs">{row.receipt_number}</TD>
                      <TD>
                        <Link
                          href={`/ar/documents/${row.document_id}`}
                          className="font-mono text-xs font-semibold text-[var(--vinea-brand)] hover:underline"
                        >
                          {row.document_number}
                        </Link>
                      </TD>
                      <TD className="text-xs text-[var(--vinea-ink-muted)]">
                        {formatDate(row.document_date)}
                      </TD>
                      <TD className="text-xs text-[var(--vinea-ink)]">{row.partner_name}</TD>
                      <TD
                        className="text-right font-mono text-xs tabular-nums"
                        data-testid="only-receipt-declared"
                      >
                        {declared(row.declared_gross)}
                      </TD>
                      <TD>
                        <StatusChip tone="info">
                          <span data-testid="only-receipt-reason">
                            {t(`straddleReason.${row.outside_range}` as never)}
                          </span>
                        </StatusChip>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </ReportPanel>

          <ReportPanel>
            {data.receipts.length === 0 ? (
              <QueryState query={listing} isEmpty empty={t("noReceipts")} testId="rows-query" />
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH className="w-24">{t("counter")}</TH>
                    <TH className="w-28">{t("documentColumn")}</TH>
                    <TH className="w-24">{t("documentDate")}</TH>
                    <TH>{t("customer")}</TH>
                    <TH className="w-32 text-right">{t("declaredGross")}</TH>
                    <TH className="w-28 text-right">{t("declaredTax")}</TH>
                    <TH className="w-32 text-right">{t("postedTotal")}</TH>
                  </TR>
                </THead>
                <TBody>
                  {data.receipts.map((row) => (
                    <TR key={row.receipt_id}>
                      <TD className="font-mono text-xs" data-testid="listing-counter">
                        {row.receipt_number}
                      </TD>
                      <TD>
                        <Link
                          href={`/ar/documents/${row.document_id}`}
                          className="font-mono text-xs font-semibold text-[var(--vinea-brand)] hover:underline"
                        >
                          {row.document_number}
                        </Link>
                      </TD>
                      <TD className="text-xs text-[var(--vinea-ink-muted)]">
                        {formatDate(row.document_date)}
                      </TD>
                      <TD className="text-xs text-[var(--vinea-ink)]">{row.partner_name}</TD>
                      <TD
                        className="text-right font-mono text-xs tabular-nums"
                        data-testid="listing-declared"
                      >
                        {declared(row.declared_gross)}
                      </TD>
                      <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                        {declared(row.declared_tax)}
                      </TD>
                      <TD
                        className="text-right font-mono text-xs tabular-nums"
                        data-testid="listing-posted"
                      >
                        {row.posted_base_total === null ? "" : posted(row.posted_base_total)}
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

function Figure({
  label,
  value,
  testId,
  large,
}: {
  label: string;
  value: string;
  testId?: string;
  large?: boolean;
}) {
  return (
    <div>
      <dt className="text-xs text-[var(--vinea-ink-muted)]">{label}</dt>
      <dd
        className={`font-mono tabular-nums text-[var(--vinea-ink)] ${large ? "text-2xl" : "text-lg"}`}
        data-testid={testId}
      >
        {value}
      </dd>
    </div>
  );
}
