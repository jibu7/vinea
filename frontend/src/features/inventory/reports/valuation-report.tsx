"use client";

import { useState } from "react";
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
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/design/components/tabs";
import { useBranches, useCompanyDetails, useCurrencies } from "@/features/gl/hooks";
import { exportToCsv } from "@/lib/csv";
import { dotted, formatDate, formatMoney, formatQuantity, todayIso } from "@/lib/format";
import { useValuationReport } from "../hooks";
import { useInventoryLineSupport } from "../line-support";

/**
 * Valuation report over `GET /inventory/reports/valuation` — what stock is worth on a date,
 * reconstructed from moves, with the **GL tie stated on the report itself**.
 *
 * That tie is the phase's invariant (§6.3: stock valuation == inventory GL balance, at any
 * date, per branch), and a report that only shows the stock side leaves a reader to go and
 * find the other half in a different screen and compare by eye. The endpoint returns
 * `account_totals` — what each inventory account should read on `as_of` — so the two sit
 * beside each other here and disagreement is visible rather than discoverable.
 *
 * Zero-quantity locations are off by default (the P3 "Include zero balances" convention): a
 * location holding nothing is worth nothing, and the totals are the same either way.
 *
 * The per-location average is labelled **"Average as at"**, never "cost". Value / quantity on
 * a date is not what anything was posted at — a backdated receipt makes the two differ
 * (decision 4) — and a column headed "cost" would be read as the second thing.
 *
 * **What each figure is a total of**, because the three tabs do not share a scope and a
 * reader who assumes they do will find them disagreeing: the headline, the GL tie and the
 * warehouse tab are over the **whole filtered set**, computed by the server before paging.
 * Paging is by *item*, so the location and item tabs show a page of items with all of each
 * one's locations — every item's own total is whole, and only the set of items is a page.
 */
export function ValuationReport() {
  const t = useTranslations("inventory.reports");
  const [asOf, setAsOf] = useState(todayIso);
  const [warehouseId, setWarehouseId] = useState("");
  const [branchId, setBranchId] = useState("");
  const [itemId, setItemId] = useState("");
  const [includeZero, setIncludeZero] = useState(false);

  const support = useInventoryLineSupport({
    includeInactiveItems: true,
    includeInTransitWarehouses: true,
  });
  const company = useCompanyDetails();
  const branches = useBranches();
  const currencies = useCurrencies();
  const base = (currencies.data ?? []).find((c) => c.is_base);
  const baseLike = {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };

  const pager = useCursorPager();
  const report = useValuationReport({
    asOf,
    warehouseId: warehouseId ? Number(warehouseId) : undefined,
    branchId: branchId ? Number(branchId) : undefined,
    itemId: itemId ? Number(itemId) : undefined,
    includeZero,
    cursor: pager.cursor,
  });
  const data = report.data;
  const rows = data?.rows ?? [];

  const money = (value: string) => formatMoney(Number(value), baseLike, { showCode: false });

  function handleExport() {
    exportToCsv(
      `inventory-valuation-${asOf}`,
      [t("code"), t("name"), t("warehouse"), t("quantity"), t("averageAsAt"), t("value")],
      rows.map((row) => [
        row.item_code,
        row.item_name,
        row.warehouse_code,
        row.quantity,
        row.average_as_at ?? "",
        row.value,
      ]),
    );
  }

  return (
    <ReportPage
      title={t("valuationTitle")}
      subtitle={t("valuationSubtitle")}
      companyName={company.data?.name}
      asOfLabel={formatDate(asOf)}
      onExportCsv={rows.length ? handleExport : undefined}
      filters={
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-4">
          <Field label={t("asOfDate")}>
            <IsoDatePicker value={asOf} onValueChange={pager.filter(setAsOf)} />
          </Field>
          <Field label={t("warehouse")}>
            <Combobox
              options={[{ value: "", label: t("allWarehouses") }, ...support.warehouseOptions]}
              value={warehouseId}
              onValueChange={pager.filter(setWarehouseId)}
              placeholder={t("allWarehouses")}
            />
          </Field>
          <Field label={t("branch")}>
            <Combobox
              options={[
                { value: "", label: t("allBranches") },
                ...(branches.data ?? []).map((branch) => ({
                  value: String(branch.id),
                  label: dotted(branch.code, branch.name),
                })),
              ]}
              value={branchId}
              onValueChange={pager.filter(setBranchId)}
              placeholder={t("allBranches")}
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
            {t("includeZeroQuantities")}
          </label>
        </div>
      }
    >
      {/* The headline and its tie, before the detail: what the stock is worth, and what the
          inventory account it hangs off should read on the same date. */}
      <ReportPanel>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <p className="text-xs text-[var(--vinea-ink-muted)]">{t("stockValue")}</p>
            <p
              data-testid="valuation-total"
              className="font-mono text-lg tabular-nums text-[var(--vinea-ink)]"
            >
              {formatMoney(Number(data?.total_value ?? 0), baseLike)}
            </p>
          </div>
          <div>
            <p className="text-xs text-[var(--vinea-ink-muted)]">{t("glTie")}</p>
            {(data?.account_totals ?? []).length === 0 ? (
              <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("emptyValue")}</p>
            ) : (
              <ul className="space-y-0.5">
                {(data?.account_totals ?? []).map((account) => (
                  <li
                    key={account.gl_account_id ?? account.code ?? "none"}
                    data-testid="valuation-gl-tie"
                    className="flex items-baseline justify-between gap-3 text-sm"
                  >
                    <span className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                      {account.code ? dotted(account.code, account.name ?? "") : t("noAccount")}
                    </span>
                    <span
                      data-testid="valuation-gl-tie-value"
                      className="font-mono tabular-nums text-[var(--vinea-ink)]"
                    >
                      {formatMoney(Number(account.value), baseLike)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </ReportPanel>

      <Tabs defaultValue="locations">
        <TabsList className="mb-3 print:hidden">
          <TabsTrigger value="locations">{t("byLocation")}</TabsTrigger>
          <TabsTrigger value="items">{t("byItem")}</TabsTrigger>
          <TabsTrigger value="warehouses">{t("byWarehouse")}</TabsTrigger>
        </TabsList>

        <TabsContent value="locations">
          <ReportPanel>
            {rows.length === 0 ? (
              <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
                {report.isLoading ? t("loading") : t("noRows")}
              </p>
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH className="w-28">{t("code")}</TH>
                    <TH>{t("name")}</TH>
                    <TH className="w-32">{t("warehouse")}</TH>
                    <TH className="text-right">{t("quantity")}</TH>
                    <TH className="text-right">{t("averageAsAt")}</TH>
                    <TH className="text-right">{t("value")}</TH>
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
                      <TD className="text-right font-mono text-xs tabular-nums">
                        {support.formatBaseWithUnit(row.item_id, row.quantity)}
                      </TD>
                      <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                        {row.average_as_at === null
                          ? t("emptyValue")
                          : formatQuantity(Number(row.average_as_at), 6)}
                      </TD>
                      <TD
                        data-testid="valuation-row-value"
                        className="text-right font-mono text-xs tabular-nums"
                      >
                        {money(row.value)}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </ReportPanel>
        </TabsContent>

        <TabsContent value="items">
          <ReportPanel>
            {(data?.item_totals ?? []).length === 0 ? (
              <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
                {report.isLoading ? t("loading") : t("noRows")}
              </p>
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH className="w-28">{t("code")}</TH>
                    <TH>{t("name")}</TH>
                    <TH className="text-right">{t("quantity")}</TH>
                    <TH className="text-right">{t("value")}</TH>
                  </TR>
                </THead>
                <TBody>
                  {(data?.item_totals ?? []).map((total) => (
                    <TR key={total.item_id}>
                      <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">
                        {total.item_code}
                      </TD>
                      <TD className="text-xs">{total.item_name}</TD>
                      <TD className="text-right font-mono text-xs tabular-nums">
                        {support.formatBaseWithUnit(total.item_id, total.quantity)}
                      </TD>
                      <TD className="text-right font-mono text-xs tabular-nums">
                        {money(total.value)}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </ReportPanel>
        </TabsContent>

        {/* The per-branch half of the invariant, as far as a screen can show it: every
            warehouse's value, over the whole filtered set rather than this page, so these
            foot to the headline above and to the branch balances in the GL. */}
        <TabsContent value="warehouses">
          <ReportPanel>
            {(data?.warehouse_totals ?? []).length === 0 ? (
              <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
                {report.isLoading ? t("loading") : t("noRows")}
              </p>
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH className="w-32">{t("warehouse")}</TH>
                    <TH>{t("name")}</TH>
                    <TH className="text-right">{t("value")}</TH>
                  </TR>
                </THead>
                <TBody>
                  {(data?.warehouse_totals ?? []).map((total) => {
                    const warehouse = support.warehouses.find(
                      (w) => w.id === total.warehouse_id,
                    );
                    return (
                      <TR key={total.warehouse_id}>
                        <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">
                          {warehouse?.code ?? total.warehouse_id}
                        </TD>
                        <TD className="text-xs">
                          {warehouse?.name ?? t("emptyValue")}
                          {warehouse?.is_in_transit && (
                            <StatusChip tone="info" className="ml-2">
                              {t("inTransit")}
                            </StatusChip>
                          )}
                        </TD>
                        <TD
                          data-testid="valuation-warehouse-value"
                          className="text-right font-mono text-xs tabular-nums"
                        >
                          {money(total.value)}
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
            )}
          </ReportPanel>
        </TabsContent>
      </Tabs>

      {/* Outside the tabs: paging is by item, so it moves the location and item tabs
          together — they are two views of one page, not two pages. */}
      {rows.length > 0 && (
        <ReportPager
          count={rows.length}
          hasPrevious={pager.hasPrevious}
          hasNext={(data?.next_cursor ?? null) !== null}
          onPrevious={pager.previous}
          onNext={() => pager.next(data?.next_cursor ?? null)}
        />
      )}
    </ReportPage>
  );
}
