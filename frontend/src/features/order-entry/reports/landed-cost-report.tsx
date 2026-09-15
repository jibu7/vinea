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
import { LandedCostStatus } from "@/lib/api-enums";
import { exportToCsv } from "@/lib/csv";
import { dotted, formatDate, formatMoney, formatQuantity, monthToDateIso } from "@/lib/format";
import { useLandedCostAllocations } from "../hooks";
import { LANDED_COST_STATUS_TONE } from "../landed-costs-screen";
import { useOrderLineSupport } from "../order-support";

/**
 * Landed cost report (P6 step 8) — **per receipt line**, which is the grain the plan names.
 *
 * `/oe/landed-costs` under Transactions answers "what did we book"; this answers "what did
 * this consignment cost", and only the line grain can. A freight bill routinely covers
 * consignments from several suppliers, so a per-document listing tells an importer the total
 * and nothing about the goods.
 *
 * **Where the share went is a column, not a footnote.** A target whose location no longer holds
 * the item takes its share to cost of sales instead of into stock (decision 9), on the same
 * entry, so the clearing account clears whether or not the goods are still there. The two are
 * different facts about the same allocation and an operator reconciling stock value against the
 * inventory account needs to know which rows are which.
 *
 * Shares are **base currency** — the clearing account holds what was booked to it in base — so
 * the total is one honest number. Quantities are not totalled: `quantity_at_posting` is in each
 * item's own base unit, and rows across items are in different ones.
 */
export function LandedCostReport() {
  const t = useTranslations("orderEntry.landedCostReport");
  const tc = useTranslations("orderEntry.common");
  const tr = useTranslations("inventory.reports");
  const ts = useTranslations("orderEntry.landedCostStatus");

  const initial = monthToDateIso();
  const [status, setStatus] = useState("");
  const [itemId, setItemId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState(initial.to);

  const company = useCompanyDetails();
  const currencies = useCurrencies();
  const support = useOrderLineSupport({ role: "purchase" });

  const base = (currencies.data ?? []).find((c) => c.is_base);
  const baseLike = {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };

  const pager = useCursorPager();
  const listing = useLandedCostAllocations({
    status: status || undefined,
    itemId: itemId ? Number(itemId) : undefined,
    dateFrom: dateFrom || undefined,
    dateTo: dateTo || undefined,
    cursor: pager.cursor ?? undefined,
  });
  const data = listing.data;
  const rows = data?.items ?? [];

  const money = (value: string) => formatMoney(Number(value), baseLike);
  const quantity = (row: { item_id: number }, value: string) =>
    formatQuantity(Number(value), support.quantityDecimals(row.item_id));
  const warehouseCode = (id: number) =>
    support.warehouses.find((w) => w.id === id)?.code ?? String(id);

  function handleExport() {
    exportToCsv(
      `landed-cost-${dateFrom || "all"}-${dateTo || "all"}`,
      [
        t("allocation"),
        tr("date"),
        tr("description"),
        t("basis"),
        t("receipt"),
        tc("item"),
        tr("warehouse"),
        t("weight"),
        t("quantityAtPosting"),
        t("share"),
        t("destination"),
      ],
      rows.map((row) => [
        row.number,
        row.cost_date,
        row.description,
        row.basis,
        row.grn_number,
        support.itemLabel(row.item_id),
        warehouseCode(row.warehouse_id),
        row.weight,
        row.quantity_at_posting,
        row.share,
        row.went_to_cogs ? t("toCogs") : t("toStock"),
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
          <Field label={tc("status")}>
            <Select
              options={[
                { value: "", label: t("everyStatus") },
                ...Object.values(LandedCostStatus).map((value) => ({ value, label: ts(value) })),
              ]}
              value={status}
              onValueChange={pager.filter(setStatus)}
              ariaLabel={tc("status")}
            />
          </Field>
          <Field label={tc("item")}>
            <Combobox
              options={[{ value: "", label: t("everyItem") }, ...support.itemOptions]}
              value={itemId}
              onValueChange={pager.filter(setItemId)}
              placeholder={t("everyItem")}
            />
          </Field>
          <Field label={tr("from")}>
            <IsoDatePicker value={dateFrom} onValueChange={pager.filter(setDateFrom)} />
          </Field>
          <Field label={tr("to")}>
            <IsoDatePicker value={dateTo} onValueChange={pager.filter(setDateTo)} />
          </Field>
        </div>
      }
    >
      <ReportPanel>
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("totalAllocated")}</dt>
            <dd
              data-testid="report-total-allocated"
              className="font-mono text-2xl tabular-nums text-[var(--vinea-ink)]"
            >
              {money(data?.total_allocated ?? "0")}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("clearingAccount")}</dt>
            <dd className="pt-1 text-xs text-[var(--vinea-ink-muted)]">{t("clearingNote")}</dd>
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
                  <TH className="w-28">{t("allocation")}</TH>
                  <TH className="w-24">{tr("date")}</TH>
                  <TH className="w-28">{t("receipt")}</TH>
                  <TH>{tc("item")}</TH>
                  <TH className="w-20">{tr("warehouse")}</TH>
                  <TH className="w-24 text-right">{t("weight")}</TH>
                  <TH className="w-28 text-right">{t("quantityAtPosting")}</TH>
                  <TH className="w-32 text-right">{t("share")}</TH>
                  <TH className="w-28 text-right">{t("destination")}</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((row) => (
                  <TR key={row.line_id}>
                    <TD>
                      <Link
                        href={`/oe/landed-costs/${row.landed_cost_id}`}
                        data-testid="report-landed-cost-number"
                        className="font-mono text-xs font-semibold text-[var(--vinea-brand)] hover:underline"
                      >
                        {row.number}
                      </Link>
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {formatDate(row.cost_date)}
                    </TD>
                    <TD>
                      <Link
                        href={`/oe/goods-received/${row.grn_id}`}
                        className="font-mono text-xs text-[var(--vinea-brand)] hover:underline"
                      >
                        {row.grn_number}
                      </Link>
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink)]">
                      {support.itemLabel(row.item_id)}
                    </TD>
                    <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                      {warehouseCode(row.warehouse_id)}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                      {formatQuantity(Number(row.weight), 3)}
                    </TD>
                    <TD
                      className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]"
                      data-testid="report-quantity-at-posting"
                    >
                      {quantity(row, row.quantity_at_posting)}
                    </TD>
                    <TD
                      className="text-right font-mono text-xs tabular-nums"
                      data-testid="report-share"
                    >
                      {money(row.share)}
                    </TD>
                    <TD className="text-right">
                      {/* Not a status: where this share went. A line whose location held none
                          of the item has no move at all. */}
                      <StatusChip tone={row.went_to_cogs ? "warning" : "success"}>
                        {row.went_to_cogs ? t("toCogs") : t("toStock")}
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
        <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">{t("grainNote")}</p>
        <p className="pt-1 text-xs text-[var(--vinea-ink-subtle)]">{t("noQuantityTotalNote")}</p>
      </ReportPanel>
    </ReportPage>
  );
}
