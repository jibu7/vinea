"use client";

import { useState } from "react";
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
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useCompanyDetails, useCurrencies } from "@/features/gl/hooks";
import { exportToCsv } from "@/lib/csv";
import { dotted, formatDate, formatMoney, monthToDateIso } from "@/lib/format";
import { useMovementReport } from "../hooks";
import { useInventoryLineSupport } from "../line-support";

/**
 * Movement report over `GET /inventory/reports/movement` — opening, in, out and closing per
 * item × warehouse across a date range, reconstructed from moves by **document date**.
 *
 * **The out columns are signed**, exactly as the endpoint sums them, so `opening + in + out =
 * closing` reads straight across a row. Rendering them as magnitudes would look tidier and
 * would be a rendering that can disagree with its data: `in` and `out` are split on the sign
 * of the *move*, not on a document being a receipt or an issue, so a reversal or a negative
 * revaluation can land on either side. A caption under the table says so rather than leaving
 * a reader to work out why a figure carries a minus.
 *
 * The panel's totals are over the whole filtered set rather than this page, which is why they
 * are read from the report's own fields and never summed in the browser: a page is not the
 * report.
 *
 * A row is per **location**, not per item. That is the level the invariant lives at — the
 * inventory account ties to Σ location values — and rolling warehouses up here would hide
 * the in-transit line that makes a dispatched, unreceived transfer add up (decision 6).
 */
export function MovementReport() {
  const t = useTranslations("inventory.reports");
  const initial = monthToDateIso();
  const [dateFrom, setDateFrom] = useState(initial.from);
  const [dateTo, setDateTo] = useState(initial.to);
  const [warehouseId, setWarehouseId] = useState("");
  const [itemId, setItemId] = useState("");
  const [includeZero, setIncludeZero] = useState(false);

  const support = useInventoryLineSupport({ includeInactiveItems: true });
  const company = useCompanyDetails();
  const currencies = useCurrencies();
  const base = (currencies.data ?? []).find((c) => c.is_base);
  const baseLike = {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };

  const pager = useCursorPager();
  const report = useMovementReport(
    dateFrom && dateTo
      ? {
          dateFrom,
          dateTo,
          warehouseId: warehouseId ? Number(warehouseId) : undefined,
          itemId: itemId ? Number(itemId) : undefined,
          includeZero,
          cursor: pager.cursor,
        }
      : null,
  );
  const data = report.data;
  const rows = data?.rows ?? [];

  const money = (value: string) => formatMoney(Number(value), baseLike, { showCode: false });
  const quantity = (row: { item_id: number }, value: string) =>
    support.formatBaseWithUnit(row.item_id, value);

  function handleExport() {
    exportToCsv(
      `inventory-movement-${dateFrom}-${dateTo}`,
      [
        t("code"),
        t("name"),
        t("warehouse"),
        t("openingQuantity"),
        t("openingValue"),
        t("quantityIn"),
        t("valueIn"),
        t("quantityOut"),
        t("valueOut"),
        t("closingQuantity"),
        t("closingValue"),
      ],
      rows.map((row) => [
        row.item_code,
        row.item_name,
        row.warehouse_code,
        row.opening_quantity,
        row.opening_value,
        row.quantity_in,
        row.value_in,
        row.quantity_out,
        row.value_out,
        row.closing_quantity,
        row.closing_value,
      ]),
    );
  }

  return (
    <ReportPage
      title={t("movementTitle")}
      subtitle={t("movementSubtitle")}
      companyName={company.data?.name}
      // The printed masthead carries the filter as well as the range: a page of item codes
      // that leaves the screen has to say which item, or whose codes they are, on its own.
      asOfLabel={[
        t("dateRange", { from: formatDate(dateFrom), to: formatDate(dateTo) }),
        itemId
          ? dotted(support.itemById.get(Number(itemId))?.code, support.itemById.get(Number(itemId))?.name)
          : t("allItems"),
        warehouseId
          ? (support.warehouses.find((w) => w.id === Number(warehouseId))?.code ?? "")
          : t("allWarehouses"),
      ].join(" · ")}
      onExportCsv={rows.length ? handleExport : undefined}
      filters={
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-4">
          <Field label={t("from")}>
            <IsoDatePicker value={dateFrom} onValueChange={pager.filter(setDateFrom)} />
          </Field>
          <Field label={t("to")}>
            <IsoDatePicker value={dateTo} onValueChange={pager.filter(setDateTo)} />
          </Field>
          <Field label={t("warehouse")}>
            <Combobox
              options={[{ value: "", label: t("allWarehouses") }, ...support.warehouseOptions]}
              value={warehouseId}
              onValueChange={pager.filter(setWarehouseId)}
              placeholder={t("allWarehouses")}
            />
          </Field>
          <Field label={t("item")}>
            <Combobox
              options={[{ value: "", label: t("allItems") }, ...support.itemOptions()]}
              value={itemId}
              onValueChange={pager.filter(setItemId)}
              placeholder={t("allItems")}
            />
          </Field>
          <label className="flex items-center gap-2 text-xs text-[var(--vinea-ink-muted)] sm:col-span-4">
            <input
              type="checkbox"
              checked={includeZero}
              onChange={(e) => pager.filter(setIncludeZero)(e.target.checked)}
              className="size-3.5"
            />
            {t("includeZeroRows")}
          </label>
        </div>
      }
    >
      <ReportPanel>
        {rows.length === 0 ? (
          <QueryState query={report} isEmpty empty={t("noRows")} testId="query" />
        ) : (
          <>
            <Table>
              <THead>
                <TR>
                  <TH className="w-28">{t("code")}</TH>
                  <TH>{t("name")}</TH>
                  <TH className="w-20">{t("warehouse")}</TH>
                  <TH className="text-right">{t("openingQuantity")}</TH>
                  <TH className="text-right">{t("openingValue")}</TH>
                  <TH className="text-right">{t("quantityIn")}</TH>
                  <TH className="text-right">{t("valueIn")}</TH>
                  <TH className="text-right">{t("quantityOut")}</TH>
                  <TH className="text-right">{t("valueOut")}</TH>
                  <TH className="text-right">{t("closingQuantity")}</TH>
                  <TH className="text-right">{t("closingValue")}</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((row) => (
                  <TR key={`${row.item_id}-${row.warehouse_id}`}>
                    <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">
                      {row.item_code}
                    </TD>
                    <TD className="text-xs">{row.item_name}</TD>
                    <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                      {row.warehouse_code}
                      {row.is_in_transit && (
                        <StatusChip tone="info" className="ml-2">
                          {t("inTransit")}
                        </StatusChip>
                      )}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                      {quantity(row, row.opening_quantity)}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                      {money(row.opening_value)}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums">
                      {quantity(row, row.quantity_in)}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums">
                      {money(row.value_in)}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums">
                      {quantity(row, row.quantity_out)}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums">
                      {money(row.value_out)}
                    </TD>
                    <TD className="text-right font-mono text-xs font-semibold tabular-nums">
                      {quantity(row, row.closing_quantity)}
                    </TD>
                    <TD
                      data-testid="movement-closing-value"
                      className="text-right font-mono text-xs font-semibold tabular-nums"
                    >
                      {money(row.closing_value)}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <p className="pt-2 text-xs text-[var(--vinea-ink-subtle)]">{t("signedOutNote")}</p>

            {/* The report's own totals, over the whole filtered set — never a sum of the page
                on screen, which would quietly disagree with itself from page two onwards. */}
            {data && (
              <dl className="mt-4 grid grid-cols-2 gap-4 border-t border-[var(--vinea-border)] pt-3 sm:grid-cols-4">
                {(
                  [
                    ["openingValue", data.opening_value],
                    ["valueIn", data.value_in],
                    ["valueOut", data.value_out],
                    ["closingValue", data.closing_value],
                  ] as const
                ).map(([key, value]) => (
                  <div key={key}>
                    <dt className="text-xs text-[var(--vinea-ink-muted)]">{t(key)}</dt>
                    <dd
                      data-testid={`movement-total-${key}`}
                      className="font-mono text-sm tabular-nums text-[var(--vinea-ink)]"
                    >
                      {formatMoney(Number(value), baseLike)}
                    </dd>
                  </div>
                ))}
              </dl>
            )}

            <ReportPager
              count={rows.length}
              hasPrevious={pager.hasPrevious}
              hasNext={(data?.next_cursor ?? null) !== null}
              onPrevious={pager.previous}
              onNext={() => pager.next(data?.next_cursor ?? null)}
            />
          </>
        )}
      </ReportPanel>
    </ReportPage>
  );
}
