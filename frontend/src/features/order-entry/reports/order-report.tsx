"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Combobox } from "@/design/components/combobox";
import { QueryState } from "@/design/components/query-state";
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
import { useCompanyDetails } from "@/features/gl/hooks";
import { usePartners } from "@/features/subledger/hooks";
import { PurchaseOrderStatus, SalesOrderStatus } from "@/lib/api-enums";
import { cn } from "@/lib/cn";
import { exportToCsv } from "@/lib/csv";
import { dotted, formatDate, formatMoney, formatQuantity } from "@/lib/format";
import { usePurchaseOrderReport, useSalesOrderReport } from "../hooks";
import { useOrderLineSupport } from "../order-support";
import { PURCHASE_ORDER_STATUS_TONE } from "../purchase-orders-screen";
import { SALES_ORDER_STATUS_TONE } from "../sales-orders-screen";

type Role = "sales" | "purchase";

/**
 * Sales orders / Purchase orders report (P6 step 8) — order lines and what each still owes.
 *
 * **Per line, and that is the whole design.** The order listings under Transactions answer
 * "which orders"; this answers "what is outstanding", and an order-level quantity cannot: the
 * listing's `backordered` summed base quantities across lines counted in different units, so
 * 3 kg short and 2 crates short read 5. Step 8 recorded it and refused to repeat it here;
 * step 9 settled it by making that column a count of short lines. Every quantity here carries
 * the unit it is counted in.
 *
 * **Nothing in the footer is a grand total.** Quantities are subtotalled by unit and money by
 * currency, because there is no honest single number for either — an order's `exchange_rate` is
 * display only (decision 3), so no rate on this page could put RWF and USD on one line. The
 * count of lines is the total that always means something, and the server computes all three
 * over the whole filtered set rather than this page: a total that changed as you paged could be
 * reconciled against nothing.
 *
 * **Outstanding** is the default view and the one the plan's invariant is about. A line with a
 * backorder is here with its remaining quantity; after Close remaining it is not, because the
 * order leaves the open statuses and the row stops being selected. Nothing is written to make
 * that happen. Clearing the filter shows the history, closed and cancelled orders included.
 */
export function OrderReport({ role }: { role: Role }) {
  const t = useTranslations("orderEntry.orderReport");
  const tc = useTranslations("orderEntry.common");
  const tr = useTranslations("inventory.reports");
  const salesStatus = useTranslations("orderEntry.salesOrderStatus");
  const purchaseStatus = useTranslations("orderEntry.purchaseOrderStatus");
  const isSales = role === "sales";

  const [partnerId, setPartnerId] = useState("");
  const [status, setStatus] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [outstandingOnly, setOutstandingOnly] = useState(true);

  const company = useCompanyDetails();
  const support = useOrderLineSupport({ role });
  const partners = usePartners(isSales ? "ar" : "ap", {});

  const pager = useCursorPager();
  const params = {
    partnerId: partnerId ? Number(partnerId) : undefined,
    status: status || undefined,
    warehouseId: warehouseId ? Number(warehouseId) : undefined,
    dateFrom: dateFrom || undefined,
    dateTo: dateTo || undefined,
    outstandingOnly,
    cursor: pager.cursor,
  };
  const salesReport = useSalesOrderReport(params, { enabled: isSales });
  const purchaseReport = usePurchaseOrderReport(params, { enabled: !isSales });
  const report = isSales ? salesReport : purchaseReport;
  const data = report.data;
  const rows = data?.items ?? [];

  const statusLabel = (value: string) => (isSales ? salesStatus(value) : purchaseStatus(value));
  const statusTone = (value: string) =>
    (isSales ? SALES_ORDER_STATUS_TONE : PURCHASE_ORDER_STATUS_TONE)[value] ?? "neutral";
  const unitCode = (uomId: number) => support.uomById.get(uomId)?.code ?? "";
  const unitDecimals = (uomId: number) => support.uomById.get(uomId)?.decimal_places ?? 0;

  /** A quantity at the **item's** base unit, with the unit beside it. On a report that mixes
   * items the unit is not decoration: 2.5 of one and 2.500 of another are two different
   * scales, and the column is unreadable without saying which. */
  const quantity = (row: { base_uom_id: number }, value: string) =>
    `${formatQuantity(Number(value), unitDecimals(row.base_uom_id))} ${unitCode(row.base_uom_id)}`.trim();

  const currencyOf = (id: number) => {
    const currency = support.currencies.find((c) => c.id === id);
    return {
      code: currency?.code ?? "",
      decimalPlaces: currency?.decimal_places ?? 0,
      symbol: currency?.symbol ?? null,
    };
  };

  function handleExport() {
    exportToCsv(
      `${isSales ? "sales" : "purchase"}-orders-${dateFrom || "all"}-${dateTo || "all"}`,
      [
        t("order"),
        tr("date"),
        isSales ? tc("customer") : tc("supplier"),
        tc("status"),
        tc("item"),
        tr("name"),
        t("unit"),
        t("ordered"),
        isSales ? t("invoiced") : t("received"),
        t("remaining"),
        t("currency"),
        t("net"),
      ],
      rows.map((row) => [
        row.number,
        row.order_date,
        row.partner_name,
        row.status,
        row.item_code,
        row.item_name,
        unitCode(row.base_uom_id),
        row.ordered,
        row.fulfilled,
        row.remaining,
        currencyOf(row.currency_id).code,
        row.net_amount,
      ]),
    );
  }

  return (
    <ReportPage
      title={isSales ? t("salesTitle") : t("purchaseTitle")}
      subtitle={isSales ? t("salesSubtitle") : t("purchaseSubtitle")}
      companyName={company.data?.name}
      asOfLabel={dotted(
        outstandingOnly ? t("outstandingOnly") : t("everyOrder"),
        dateFrom && dateTo ? `${formatDate(dateFrom)} — ${formatDate(dateTo)}` : "",
      )}
      onExportCsv={rows.length > 0 ? handleExport : undefined}
      filters={
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-4">
          <Field label={isSales ? tc("customer") : tc("supplier")}>
            <Combobox
              options={[
                { value: "", label: t("everyPartner") },
                ...(partners.data ?? []).map((partner) => ({
                  value: String(partner.id),
                  label: partner.name,
                })),
              ]}
              value={partnerId}
              onValueChange={pager.filter(setPartnerId)}
              placeholder={t("everyPartner")}
            />
          </Field>
          <Field label={tc("status")}>
            <Select
              options={[
                { value: "", label: t("everyStatus") },
                ...Object.values(isSales ? SalesOrderStatus : PurchaseOrderStatus).map(
                  (value) => ({ value, label: statusLabel(value) }),
                ),
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
          <label className="flex items-center gap-2 text-xs text-[var(--vinea-ink-muted)] sm:col-span-3">
            <input
              type="checkbox"
              checked={outstandingOnly}
              onChange={(e) => pager.filter(setOutstandingOnly)(e.target.checked)}
              className="size-3.5"
            />
            {t("outstandingOnlyLabel")}
          </label>
        </div>
      }
    >
      <ReportPanel>
        {rows.length === 0 ? (
          <QueryState query={report} isEmpty empty={tr("noRows")} testId="query" />
        ) : (
          <>
            <Table>
              <THead>
                <TR>
                  <TH className="w-28">{t("order")}</TH>
                  <TH className="w-24">{tr("date")}</TH>
                  <TH>{isSales ? tc("customer") : tc("supplier")}</TH>
                  <TH className="w-28">{tc("item")}</TH>
                  <TH className="w-24 text-right">{t("ordered")}</TH>
                  <TH className="w-24 text-right">{isSales ? t("invoiced") : t("received")}</TH>
                  <TH className="w-24 text-right">{t("remaining")}</TH>
                  <TH className="w-32 text-right">{t("net")}</TH>
                  <TH className="w-32 text-right">{tc("status")}</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((row) => (
                  <TR key={row.line_id}>
                    <TD>
                      <Link
                        href={`/oe/${isSales ? "sales-orders" : "purchase-orders"}/${row.order_id}`}
                        data-testid="report-order-number"
                        className="font-mono text-xs font-semibold text-[var(--vinea-brand)] hover:underline"
                      >
                        {row.number}
                      </Link>
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {formatDate(row.order_date)}
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink)]">{row.partner_name}</TD>
                    <TD
                      className={cn(
                        "font-mono text-xs",
                        // Components indented under the kit line the customer saw.
                        row.kit_parent_line_id !== null && "pl-4 opacity-80",
                      )}
                    >
                      {row.item_code}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums">
                      {quantity(row, row.ordered)}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                      {quantity(row, row.fulfilled)}
                    </TD>
                    <TD
                      className="text-right font-mono text-xs tabular-nums"
                      data-testid="report-remaining"
                    >
                      {quantity(row, row.remaining)}
                    </TD>
                    <TD
                      className="text-right font-mono text-xs tabular-nums"
                      data-testid="report-net"
                    >
                      {formatMoney(Number(row.net_amount), currencyOf(row.currency_id))}
                    </TD>
                    <TD className="text-right">
                      <StatusChip tone={statusTone(row.status)}>
                        {statusLabel(row.status)}
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
      </ReportPanel>

      {data && rows.length > 0 && (
        <ReportPanel>
          <h2 className="pb-2 text-xs font-semibold uppercase tracking-wide text-[var(--vinea-ink-muted)]">
            {t("totals")}
          </h2>
          <dl className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div>
              <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("lineCount")}</dt>
              <dd
                data-testid="report-line-count"
                className="font-mono text-lg tabular-nums text-[var(--vinea-ink)]"
              >
                {data.line_count}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("remainingByUnit")}</dt>
              <dd className="space-y-0.5 pt-1" data-testid="report-remaining-by-unit">
                {data.by_unit.map((subtotal) => (
                  <p
                    key={subtotal.uom_id}
                    className="font-mono text-sm tabular-nums text-[var(--vinea-ink)]"
                  >
                    {formatQuantity(Number(subtotal.remaining), unitDecimals(subtotal.uom_id))}{" "}
                    {unitCode(subtotal.uom_id)}
                  </p>
                ))}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("netByCurrency")}</dt>
              <dd className="space-y-0.5 pt-1" data-testid="report-net-by-currency">
                {data.by_currency.map((subtotal) => (
                  <p
                    key={subtotal.currency_id}
                    className="font-mono text-sm tabular-nums text-[var(--vinea-ink)]"
                  >
                    {formatMoney(Number(subtotal.net_amount), currencyOf(subtotal.currency_id))}
                  </p>
                ))}
              </dd>
            </div>
          </dl>
          <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">{t("totalsNote")}</p>
        </ReportPanel>
      )}
    </ReportPage>
  );
}
