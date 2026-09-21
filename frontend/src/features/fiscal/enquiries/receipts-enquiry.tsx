"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Field, Input } from "@/design/components/input";
import { QueryState } from "@/design/components/query-state";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useCompanyDetails, useCurrencies } from "@/features/gl/hooks";
import { FiscalReceiptType } from "@/lib/api-enums";
import { exportToCsv } from "@/lib/csv";
import { dotted, formatDate, formatMoney } from "@/lib/format";
import { useFiscalDevices, useFiscalReceipts } from "../hooks";

/**
 * Enquiries → Tax → **Fiscal receipts** (P7 step 8).
 *
 * The screen somebody opens holding a piece of paper. Everything on it is searchable by what
 * is *printed* on that paper — the counter `3/4 NS`, the document number, the customer, the
 * authority's invoice number — through one box rather than four fields, because a person with
 * a receipt in their hand does not know which of four fields their number is.
 *
 * **The drill is the point, and it is three deep.** An inspector's question travels receipt →
 * document → journal entry, so the row carries all three links rather than making somebody
 * search twice. The document link goes to `/ar/documents/{id}` — every fiscalized document is
 * an AR invoice or credit note (decision 3), so there is no role to resolve here.
 *
 * Refunds are not netted out of the total. A `NR` is a receipt RRA signed, it has a counter of
 * its own, and the sum on this screen is a sum of receipts rather than a day's takings — the
 * Daily fiscal report is where sales and refunds meet, and it keeps them apart there too.
 */
export function FiscalReceiptsEnquiry() {
  const t = useTranslations("fiscal.receiptsEnquiry");
  const tr = useTranslations("reports");

  const [deviceId, setDeviceId] = useState("");
  const [receiptType, setReceiptType] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [search, setSearch] = useState("");

  const company = useCompanyDetails();
  const currencies = useCurrencies();
  const devices = useFiscalDevices();
  const listing = useFiscalReceipts({
    deviceId: deviceId ? Number(deviceId) : undefined,
    receiptType: receiptType || undefined,
    dateFrom: dateFrom || undefined,
    dateTo: dateTo || undefined,
    search: search.trim() || undefined,
  });
  const rows = listing.data ?? [];

  const base = (currencies.data ?? []).find((c) => c.is_base);
  const baseLike = {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };
  const money = (value: string) => formatMoney(Number(value), baseLike);

  /** Σ of the documents' base totals. The ledger's figure, not the wire's: this screen lists
   * receipts and their documents, and the wire-versus-ledger residue belongs on the report
   * that exists to show it (Reports → Tax → Fiscal receipts listing). */
  const total = rows.reduce((sum, row) => sum + Number(row.base_total_amount), 0);

  function handleExport() {
    exportToCsv(
      `fiscal-receipts-${dateFrom || "all"}-${dateTo || "all"}`,
      [
        t("counter"),
        t("receiptKind"),
        tr("number"),
        tr("date"),
        t("customer"),
        t("invcNo"),
        t("sdcId"),
        t("signedAt"),
        t("documentTotal"),
        t("copies"),
      ],
      rows.map((row) => [
        row.receipt_number,
        row.receipt_type,
        row.document_number,
        row.document_date,
        row.partner_name,
        row.invc_no,
        row.sdc_id,
        row.sdc_datetime,
        row.base_total_amount,
        row.copy_count,
      ]),
    );
  }

  return (
    <ReportPage
      title={t("title")}
      subtitle={t("subtitle")}
      companyName={company.data?.name}
      asOfLabel={dotted(
        dateFrom ? formatDate(dateFrom) : tr("from"),
        dateTo ? formatDate(dateTo) : tr("to"),
      )}
      onExportCsv={rows.length > 0 ? handleExport : undefined}
      filters={
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-5">
          <Field label={t("search")} className="sm:col-span-2">
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t("searchPlaceholder")}
              aria-label={t("search")}
            />
          </Field>
          <Field label={t("device")}>
            <Select
              options={[
                { value: "", label: t("everyDevice") },
                ...(devices.data ?? []).map((device) => ({
                  value: String(device.id),
                  label: device.sdc_id ?? String(device.id),
                })),
              ]}
              value={deviceId}
              onValueChange={setDeviceId}
              ariaLabel={t("device")}
            />
          </Field>
          <Field label={t("receiptKind")}>
            <Select
              options={[
                { value: "", label: t("everyKind") },
                { value: FiscalReceiptType.NORMAL_SALE, label: t("sale") },
                { value: FiscalReceiptType.NORMAL_REFUND, label: t("refund") },
              ]}
              value={receiptType}
              onValueChange={setReceiptType}
              ariaLabel={t("receiptKind")}
            />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label={tr("from")}>
              <IsoDatePicker value={dateFrom} onValueChange={setDateFrom} />
            </Field>
            <Field label={tr("to")}>
              <IsoDatePicker value={dateTo} onValueChange={setDateTo} />
            </Field>
          </div>
        </div>
      }
    >
      <ReportPanel>
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div>
            <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("signedCount")}</dt>
            <dd
              data-testid="receipts-count"
              className="font-mono text-2xl tabular-nums text-[var(--vinea-ink)]"
            >
              {rows.length}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("declaredTotal")}</dt>
            <dd
              data-testid="receipts-total"
              className="font-mono text-2xl tabular-nums text-[var(--vinea-ink)]"
            >
              {money(String(total))}
            </dd>
          </div>
          <div className="sm:col-span-1">
            <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("searchHint")}</dt>
          </div>
        </dl>
      </ReportPanel>

      <ReportPanel>
        {rows.length === 0 ? (
          <QueryState query={listing} isEmpty empty={t("noReceipts")} testId="query" />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-24">{t("counter")}</TH>
                <TH className="w-28">{tr("number")}</TH>
                <TH className="w-24">{tr("date")}</TH>
                <TH>{t("customer")}</TH>
                <TH className="w-20 text-right">{t("invcNo")}</TH>
                <TH className="w-32">{t("sdcId")}</TH>
                <TH className="w-32 text-right">{t("documentTotal")}</TH>
                <TH className="w-24 print:hidden">{t("entry")}</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map((row) => (
                <TR key={row.receipt_id}>
                  <TD>
                    {/* The counter as the paper prints it — `3/4 NS`. The tone splits sales
                        from refunds, because on a screen of numbers the one thing a reader
                        scans for is which rows went the other way. */}
                    <StatusChip
                      tone={
                        row.receipt_type === FiscalReceiptType.NORMAL_REFUND
                          ? "warning"
                          : "success"
                      }
                    >
                      <span data-testid="receipt-counter" className="font-mono">
                        {row.receipt_number}
                      </span>
                    </StatusChip>
                  </TD>
                  <TD>
                    <Link
                      href={`/ar/documents/${row.document_id}`}
                      data-testid="receipt-document"
                      className="font-mono text-xs font-semibold text-[var(--vinea-brand)] hover:underline"
                    >
                      {row.document_number}
                    </Link>
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">
                    {formatDate(row.document_date)}
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink)]">{row.partner_name}</TD>
                  <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                    {row.invc_no}
                  </TD>
                  <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                    {row.sdc_id}
                  </TD>
                  <TD
                    className="text-right font-mono text-xs tabular-nums"
                    data-testid="receipt-total"
                  >
                    {money(row.base_total_amount)}
                  </TD>
                  <TD className="print:hidden">
                    {row.journal_entry_id === null ? null : (
                      <Link
                        href={`/gl/entries/${row.journal_entry_id}`}
                        data-testid="receipt-entry"
                        className="font-mono text-xs text-[var(--vinea-brand)] hover:underline"
                      >
                        {t("openEntry")}
                      </Link>
                    )}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
        <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">{t("drillNote")}</p>
      </ReportPanel>
    </ReportPage>
  );
}
