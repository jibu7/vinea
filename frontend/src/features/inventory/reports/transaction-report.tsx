"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ExternalLink } from "lucide-react";
import { Combobox } from "@/design/components/combobox";
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
import { documentHref } from "@/lib/document-route";
import { exportToCsv } from "@/lib/csv";
import { formatDate, formatMoney, formatQuantity, monthToDateIso } from "@/lib/format";
import { useTransactionReport } from "../hooks";
import { useInventoryLineSupport } from "../line-support";

/**
 * Transaction report over `GET /inventory/reports/transactions` — the move listing, filtered,
 * with a drill to the journal entry each move was posted with.
 *
 * This is the report an accountant reaches for when the inventory account and the stock
 * disagree, so every row carries the two keys that settle it: the posting `sequence_no`
 * (which is the order costing used, and differs from date order for a backdated document)
 * and the entry number.
 *
 * **Total quantity is shown only when one item is selected.** The endpoint returns it either
 * way, but a sum of kilograms and bottles is not a quantity of anything — printing it over a
 * mixed set would be a figure that looks like an answer and is not one. Total value has no
 * such problem: it is money, in one base currency, all the way down.
 */
export function TransactionReport() {
  const t = useTranslations("inventory.reports");
  const initial = monthToDateIso();
  const [dateFrom, setDateFrom] = useState(initial.from);
  const [dateTo, setDateTo] = useState(initial.to);
  const [itemId, setItemId] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const [transactionTypeId, setTransactionTypeId] = useState("");
  const [provisionalOnly, setProvisionalOnly] = useState(false);

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
  const report = useTransactionReport(
    dateFrom && dateTo
      ? {
          dateFrom,
          dateTo,
          itemId: itemId ? Number(itemId) : undefined,
          warehouseId: warehouseId ? Number(warehouseId) : undefined,
          transactionTypeId: transactionTypeId ? Number(transactionTypeId) : undefined,
          provisionalOnly,
          cursor: pager.cursor,
        }
      : null,
  );
  const data = report.data;
  const rows = data?.rows ?? [];

  const money = (value: string) => formatMoney(Number(value), baseLike, { showCode: false });
  const typeCode = (id: number | null) =>
    id === null ? t("emptyValue") : (support.typeById.get(id)?.code ?? String(id));

  function handleExport() {
    exportToCsv(
      `inventory-transactions-${dateFrom}-${dateTo}`,
      [
        t("date"),
        t("sequence"),
        t("code"),
        t("name"),
        t("warehouse"),
        t("transactionType"),
        t("quantity"),
        t("unitCost"),
        t("value"),
        t("provisional"),
        t("document"),
        t("entry"),
      ],
      rows.map((row) => [
        row.move_date,
        row.sequence_no,
        row.item_code,
        row.item_name,
        row.warehouse_code,
        typeCode(row.transaction_type_id),
        row.quantity,
        row.unit_cost ?? "",
        row.value,
        row.cost_provisional ? t("yes") : t("no"),
        row.source?.number ?? "",
        row.entry_number ?? "",
      ]),
    );
  }

  return (
    <ReportPage
      title={t("transactionTitle")}
      subtitle={t("transactionSubtitle")}
      companyName={company.data?.name}
      asOfLabel={t("dateRange", { from: formatDate(dateFrom), to: formatDate(dateTo) })}
      onExportCsv={rows.length ? handleExport : undefined}
      filters={
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-5">
          <Field label={t("from")}>
            <IsoDatePicker value={dateFrom} onValueChange={pager.filter(setDateFrom)} />
          </Field>
          <Field label={t("to")}>
            <IsoDatePicker value={dateTo} onValueChange={pager.filter(setDateTo)} />
          </Field>
          <Field label={t("item")}>
            <Combobox
              options={[{ value: "", label: t("allItems") }, ...support.itemOptions()]}
              value={itemId}
              onValueChange={pager.filter(setItemId)}
              placeholder={t("allItems")}
            />
          </Field>
          <Field label={t("warehouse")}>
            <Combobox
              options={[{ value: "", label: t("allWarehouses") }, ...support.warehouseOptions]}
              value={warehouseId}
              onValueChange={pager.filter(setWarehouseId)}
              placeholder={t("allWarehouses")}
            />
          </Field>
          <Field label={t("transactionType")}>
            <Combobox
              options={[{ value: "", label: t("allTypes") }, ...support.typeOptions]}
              value={transactionTypeId}
              onValueChange={pager.filter(setTransactionTypeId)}
              placeholder={t("allTypes")}
            />
          </Field>
          <label className="flex items-center gap-2 text-xs text-[var(--vinea-ink-muted)] sm:col-span-5">
            <input
              type="checkbox"
              checked={provisionalOnly}
              onChange={(e) => pager.filter(setProvisionalOnly)(e.target.checked)}
              className="size-3.5"
            />
            {t("provisionalOnly")}
          </label>
        </div>
      }
    >
      <ReportPanel>
        {rows.length === 0 ? (
          <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {report.isLoading ? t("loading") : t("noRows")}
          </p>
        ) : (
          <>
            <Table>
              <THead>
                <TR>
                  <TH className="w-24">{t("date")}</TH>
                  <TH className="w-14 text-right">{t("sequence")}</TH>
                  <TH className="w-28">{t("code")}</TH>
                  <TH>{t("name")}</TH>
                  <TH className="w-20">{t("warehouse")}</TH>
                  <TH className="w-24">{t("transactionType")}</TH>
                  <TH className="text-right">{t("quantity")}</TH>
                  <TH className="text-right">{t("unitCost")}</TH>
                  <TH className="text-right">{t("value")}</TH>
                  <TH className="w-28">{t("document")}</TH>
                  <TH className="w-28">{t("entry")}</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((row) => (
                  <TR key={row.move_id}>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {formatDate(row.move_date)}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-subtle)]">
                      {row.sequence_no}
                    </TD>
                    <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">
                      {row.item_code}
                    </TD>
                    <TD className="text-xs">
                      {row.item_name}
                      {row.cost_provisional && (
                        <StatusChip tone="warning" className="ml-2">
                          {t("provisional")}
                        </StatusChip>
                      )}
                    </TD>
                    <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                      {row.warehouse_code}
                    </TD>
                    <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                      {typeCode(row.transaction_type_id)}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums">
                      {support.formatBaseWithUnit(row.item_id, row.quantity)}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                      {row.unit_cost === null
                        ? t("emptyValue")
                        : formatQuantity(Number(row.unit_cost), 6)}
                    </TD>
                    <TD
                      data-testid="transaction-value"
                      className="text-right font-mono text-xs tabular-nums"
                    >
                      {money(row.value)}
                    </TD>
                    {/* The document, before the entry it posted. P6 gave a move four possible
                        sources and the server resolves all four; this column is where the
                        resolution lands, so a valuation figure leads back to the receipt or
                        the sale that made it and not only to a journal number. A move that
                        carried no value has no entry at all, and its document is the only way
                        to it. */}
                    <TD>
                      {(() => {
                        const href = documentHref(row.source?.target, row.source?.source_doc_id);
                        return href && row.source ? (
                          <Link
                            href={href}
                            data-testid="transaction-document"
                            className="inline-flex items-center gap-1 font-mono text-xs text-[var(--vinea-brand)] underline"
                          >
                            {row.source.number}
                            <ExternalLink className="size-3" />
                          </Link>
                        ) : (
                          <span className="text-xs text-[var(--vinea-ink-subtle)]">
                            {t("emptyValue")}
                          </span>
                        );
                      })()}
                    </TD>
                    <TD>
                      {row.journal_entry_id === null ? (
                        <span className="text-xs text-[var(--vinea-ink-subtle)]">
                          {t("emptyValue")}
                        </span>
                      ) : (
                        <Link
                          href={`/gl/entries/${row.journal_entry_id}`}
                          className="inline-flex items-center gap-1 font-mono text-xs font-semibold text-[var(--vinea-brand)] underline"
                        >
                          {row.entry_number ?? row.journal_entry_id}
                          <ExternalLink className="size-3" />
                        </Link>
                      )}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>

            {data && (
              <dl className="mt-4 grid grid-cols-2 gap-4 border-t border-[var(--vinea-border)] pt-3 sm:grid-cols-3">
                <div>
                  <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("moveCount")}</dt>
                  <dd className="font-mono text-sm tabular-nums text-[var(--vinea-ink)]">
                    {data.move_count}
                  </dd>
                </div>
                {itemId && (
                  <div>
                    <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("totalQuantity")}</dt>
                    <dd className="font-mono text-sm tabular-nums text-[var(--vinea-ink)]">
                      {support.formatBaseWithUnit(Number(itemId), data.total_quantity)}
                    </dd>
                  </div>
                )}
                <div>
                  <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("totalValue")}</dt>
                  <dd
                    data-testid="transaction-total-value"
                    className="font-mono text-sm tabular-nums text-[var(--vinea-ink)]"
                  >
                    {formatMoney(Number(data.total_value), baseLike)}
                  </dd>
                </div>
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
