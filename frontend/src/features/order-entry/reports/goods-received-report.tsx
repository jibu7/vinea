"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Combobox } from "@/design/components/combobox";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Field } from "@/design/components/input";
import {
  ReportPage,
  ReportPager,
  ReportPanel,
  useCursorPager,
} from "@/design/components/report-page";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useCompanyDetails, useCurrencies } from "@/features/gl/hooks";
import { usePartners } from "@/features/subledger/hooks";
import { GrnStatus } from "@/lib/api-enums";
import { exportToCsv } from "@/lib/csv";
import { dotted, formatDate, formatMoney, monthToDateIso } from "@/lib/format";
import { useGoodsReceived } from "../hooks";
import { GRN_STATUS_TONE } from "../goods-received-screen";
import { useOrderLineSupport } from "../order-support";

/**
 * Goods received report (P6 step 8) — receipts, and what each still has sitting in the accrual.
 *
 * **This report has one job the others do not: it is provable.** Decision 5 makes the GRN
 * accrual a control account whose balance is, at any date and per branch, Σ over GRN lines of
 * (received value − relieved value). `unmatched_total` is that same sum asked of the reporting
 * path, so the figure at the bottom of this page and the balance of *2350 Goods Received Not
 * Invoiced* on the trial balance for the same date are the same number — and an operator can
 * see it hold without running a test. The e2e reads both off two screens and compares the
 * **rendered strings**, because two figures that are equal as decimals and different as text
 * are still a report nobody can reconcile.
 *
 * The total is over the whole filtered set rather than this page. It has to be: a per-page
 * total would be a different number on every page and could be tied to nothing.
 *
 * Every value here is **base currency** — a GRN line's `value` is frozen at the rate on its
 * receipt date — so unlike the order reports this one can honestly carry a single money total.
 */
export function GoodsReceivedReport() {
  const t = useTranslations("orderEntry.goodsReceivedReport");
  const tg = useTranslations("orderEntry.goodsReceived");
  const tc = useTranslations("orderEntry.common");
  const tr = useTranslations("inventory.reports");
  const ts = useTranslations("orderEntry.grnStatus");

  const initial = monthToDateIso();
  const [partnerId, setPartnerId] = useState("");
  const [status, setStatus] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState(initial.to);

  const company = useCompanyDetails();
  const currencies = useCurrencies();
  const support = useOrderLineSupport({ role: "purchase" });
  const partners = usePartners("ap", {});

  const base = (currencies.data ?? []).find((c) => c.is_base);
  const baseLike = {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };

  const pager = useCursorPager();
  const listing = useGoodsReceived({
    partnerId: partnerId ? Number(partnerId) : undefined,
    status: status || undefined,
    warehouseId: warehouseId ? Number(warehouseId) : undefined,
    dateFrom: dateFrom || undefined,
    dateTo: dateTo || undefined,
    cursor: pager.cursor ?? undefined,
  });
  const data = listing.data;
  const rows = data?.items ?? [];

  const money = (value: string) => formatMoney(Number(value), baseLike);
  const warehouseCode = (id: number) =>
    support.warehouses.find((w) => w.id === id)?.code ?? String(id);

  function handleExport() {
    exportToCsv(
      `goods-received-${dateFrom || "all"}-${dateTo || "all"}`,
      [
        t("receipt"),
        tr("date"),
        tc("supplier"),
        tr("warehouse"),
        t("reference"),
        tc("status"),
        tg("receivedValue"),
        tg("matchedValue"),
        tg("unmatchedValue"),
      ],
      rows.map((row) => [
        row.number,
        row.grn_date,
        row.partner_name,
        warehouseCode(row.warehouse_id),
        row.reference ?? "",
        row.status,
        row.received_value,
        row.matched_value,
        row.unmatched_value,
      ]),
    );
  }

  return (
    <ReportPage
      title={t("title")}
      subtitle={t("subtitle")}
      companyName={company.data?.name}
      asOfLabel={dotted(
        dateFrom ? formatDate(dateFrom) : t("fromTheBeginning"),
        dateTo ? formatDate(dateTo) : t("toToday"),
      )}
      onExportCsv={rows.length > 0 ? handleExport : undefined}
      filters={
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-4">
          <Field label={tc("supplier")}>
            <Combobox
              options={[
                { value: "", label: t("everySupplier") },
                ...(partners.data ?? []).map((partner) => ({
                  value: String(partner.id),
                  label: partner.name,
                })),
              ]}
              value={partnerId}
              onValueChange={pager.filter(setPartnerId)}
              placeholder={t("everySupplier")}
            />
          </Field>
          <Field label={tc("status")}>
            <Select
              options={[
                { value: "", label: tg("allStatuses") },
                ...Object.values(GrnStatus).map((value) => ({ value, label: ts(value) })),
              ]}
              value={status}
              onValueChange={pager.filter(setStatus)}
              ariaLabel={tc("status")}
            />
          </Field>
          <Field label={tr("from")}>
            <IsoDatePicker value={dateFrom} onValueChange={pager.filter(setDateFrom)} />
          </Field>
          <Field label={tr("to")}>
            <IsoDatePicker value={dateTo} onValueChange={pager.filter(setDateTo)} />
          </Field>
          <Field label={tr("warehouse")}>
            <Combobox
              options={[{ value: "", label: tr("allWarehouses") }, ...support.warehouseOptions]}
              value={warehouseId}
              onValueChange={pager.filter(setWarehouseId)}
              placeholder={tr("allWarehouses")}
            />
          </Field>
        </div>
      }
    >
      {/* The tie, above the rows rather than under them: it is what the page is for, and a
          reader comparing it with the trial balance should not have to page to the end to
          find it. */}
      <ReportPanel>
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <dt className="text-xs text-[var(--vinea-ink-muted)]">{tg("unmatchedTotal")}</dt>
            <dd
              data-testid="report-unmatched-total"
              className="font-mono text-2xl tabular-nums text-[var(--vinea-ink)]"
            >
              {money(data?.unmatched_total ?? "0")}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("accrualAccount")}</dt>
            <dd className="pt-1 text-xs text-[var(--vinea-ink-muted)]">
              {t("accrualTie")}{" "}
              <Link
                href="/gl/enquiries/trial-balance"
                className="font-medium text-[var(--vinea-brand)] underline"
              >
                {t("openTrialBalance")}
              </Link>
            </dd>
          </div>
        </dl>
      </ReportPanel>

      <ReportPanel>
        {rows.length === 0 ? (
          <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {listing.isLoading ? tr("loading") : tr("noRows")}
          </p>
        ) : (
          <>
            <Table>
              <THead>
                <TR>
                  <TH className="w-32">{t("receipt")}</TH>
                  <TH className="w-24">{tr("date")}</TH>
                  <TH>{tc("supplier")}</TH>
                  <TH className="w-20">{tr("warehouse")}</TH>
                  <TH className="w-32 text-right">{tg("receivedValue")}</TH>
                  <TH className="w-32 text-right">{tg("matchedValue")}</TH>
                  <TH className="w-32 text-right">{tg("unmatchedValue")}</TH>
                  <TH className="w-32 text-right">{tc("status")}</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((row) => (
                  <TR key={row.id}>
                    <TD>
                      <Link
                        href={`/oe/goods-received/${row.id}`}
                        data-testid="report-grn-number"
                        className="font-mono text-xs font-semibold text-[var(--vinea-brand)] hover:underline"
                      >
                        {row.number}
                      </Link>
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {formatDate(row.grn_date)}
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink)]">{row.partner_name}</TD>
                    <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                      {warehouseCode(row.warehouse_id)}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums">
                      {money(row.received_value)}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                      {money(row.matched_value)}
                    </TD>
                    <TD
                      className="text-right font-mono text-xs tabular-nums"
                      data-testid="report-grn-unmatched"
                    >
                      {money(row.unmatched_value)}
                    </TD>
                    <TD className="text-right">
                      <StatusChip tone={GRN_STATUS_TONE[row.status] ?? "neutral"}>
                        {ts(row.status)}
                      </StatusChip>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <ReportPager
              count={rows.length}
              hasPrevious={pager.hasPrevious}
              hasNext={data?.next_cursor !== null && data?.next_cursor !== undefined}
              onPrevious={pager.previous}
              onNext={() => pager.next(data?.next_cursor ?? null)}
            />
          </>
        )}
        <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">{tg("accrualNote")}</p>
        <p className="pt-1 text-xs text-[var(--vinea-ink-subtle)]">{tg("statusNote")}</p>
      </ReportPanel>
    </ReportPage>
  );
}
