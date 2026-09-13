"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ExternalLink } from "lucide-react";
import { Combobox } from "@/design/components/combobox";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Drawer, DrawerContent } from "@/design/components/drawer";
import { Field } from "@/design/components/input";
import {
  ReportPage,
  ReportPager,
  ReportPanel,
  useCursorPager,
} from "@/design/components/report-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useAccounts, useCompanyDetails, useCurrencies, useJournalEntry } from "@/features/gl/hooks";
import { byId } from "@/features/gl/lookups";
import { exportToCsv } from "@/lib/csv";
import { dotted, formatDate, formatMoney, formatQuantity, todayIso } from "@/lib/format";
import { useItemEnquiry } from "./hooks";
import { useInventoryLineSupport } from "./line-support";

/**
 * Item enquiry over `GET /inventory/items/{id}/enquiry`.
 *
 * Two drill-downs, and they chain: a **location** row narrows the move list to that
 * warehouse, and a **move** opens the journal entry that posted it. That is the path a
 * question about a figure actually takes — "why does Depot say 695?" → the six moves that
 * made it → the entry one of them belongs to — and it ends at the ledger rather than at
 * another report.
 *
 * Quantities print at the item's **base unit** decimals and values in the base currency's,
 * never at the `NUMERIC(20,6)` scale the wire carries. Two figures on this screen are
 * deliberately *not* the same number and must not be labelled as if they were: `unit_cost`
 * is what a receipt was costed at, `average_cost` is Σvalue / Σquantity now, and a backdated
 * receipt makes them differ (decision 4).
 */
export function ItemEnquiryScreen() {
  const t = useTranslations("inventory.enquiry");
  const tr = useTranslations("inventory.reports");
  const [itemId, setItemId] = useState("");
  const [asOf, setAsOf] = useState(todayIso);
  const [dateFrom, setDateFrom] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const [provisionalOnly, setProvisionalOnly] = useState(false);
  const [includeZeroLocations, setIncludeZeroLocations] = useState(false);
  const [drillEntryId, setDrillEntryId] = useState<number | null>(null);

  const support = useInventoryLineSupport();
  const company = useCompanyDetails();
  const currencies = useCurrencies();
  const accounts = useAccounts();
  const accountById = byId(accounts.data);
  const entry = useJournalEntry(drillEntryId);

  const base = (currencies.data ?? []).find((c) => c.is_base);
  const baseLike = {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };

  const pager = useCursorPager();
  const enquiry = useItemEnquiry(itemId ? Number(itemId) : null, {
    asOf,
    dateFrom: dateFrom || undefined,
    warehouseId: warehouseId ? Number(warehouseId) : undefined,
    provisionalOnly,
    includeZeroLocations,
    cursor: pager.cursor,
  });
  const data = enquiry.data;

  const baseUom = data ? support.uomById.get(data.base_uom_id) : undefined;
  const unitDecimals = baseUom?.decimal_places ?? 0;
  const unit = baseUom?.code ?? "";

  /** A quantity at the item's base-unit decimals, with its unit — "2.500 KG", never "2.5". */
  function quantity(value: string): string {
    return `${formatQuantity(Number(value), unitDecimals)} ${unit}`.trim();
  }
  function money(value: string): string {
    return formatMoney(Number(value), baseLike, { showCode: false });
  }

  function handleExport() {
    if (!data) return;
    exportToCsv(
      `item-enquiry-${data.item_code}-${asOf}`,
      [
        tr("date"),
        tr("sequence"),
        tr("transactionType"),
        tr("warehouse"),
        tr("quantity"),
        tr("unitCost"),
        tr("value"),
        tr("runningQuantity"),
        tr("runningValue"),
        tr("provisional"),
        tr("entry"),
      ],
      data.moves.map((move) => [
        move.move_date,
        move.sequence_no,
        move.transaction_type_code ?? "",
        move.warehouse_code,
        move.quantity,
        move.unit_cost ?? "",
        move.value,
        move.running_quantity,
        move.running_value,
        move.cost_provisional ? tr("yes") : tr("no"),
        move.entry_number ?? "",
      ]),
    );
  }

  return (
    <ReportPage
      title={t("title")}
      subtitle={t("subtitle")}
      companyName={company.data?.name}
      asOfLabel={data ? `${data.item_code} · ${data.item_name} — ${formatDate(asOf)}` : undefined}
      onExportCsv={data && data.moves.length > 0 ? handleExport : undefined}
      filters={
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-4">
          <Field label={t("item")}>
            <Combobox
              options={support.itemOptions()}
              value={itemId}
              onValueChange={pager.filter(setItemId)}
              placeholder={t("chooseItem")}
            />
          </Field>
          <Field label={tr("asOfDate")}>
            <IsoDatePicker value={asOf} onValueChange={pager.filter(setAsOf)} />
          </Field>
          <Field label={t("movesFrom")}>
            <IsoDatePicker value={dateFrom} onValueChange={pager.filter(setDateFrom)} />
          </Field>
          <Field label={tr("warehouse")}>
            <Combobox
              options={[{ value: "", label: tr("allWarehouses") }, ...support.warehouseOptions]}
              value={warehouseId}
              onValueChange={pager.filter(setWarehouseId)}
              placeholder={tr("allWarehouses")}
            />
          </Field>
          <label className="flex items-center gap-2 text-xs text-[var(--vinea-ink-muted)] sm:col-span-2">
            <input
              type="checkbox"
              checked={provisionalOnly}
              onChange={(e) => pager.filter(setProvisionalOnly)(e.target.checked)}
              className="size-3.5"
            />
            {t("provisionalOnly")}
          </label>
          <label className="flex items-center gap-2 text-xs text-[var(--vinea-ink-muted)] sm:col-span-2">
            <input
              type="checkbox"
              checked={includeZeroLocations}
              onChange={(e) => pager.filter(setIncludeZeroLocations)(e.target.checked)}
              className="size-3.5"
            />
            {t("includeEmptyLocations")}
          </label>
        </div>
      }
    >
      {!itemId ? (
        <p className="py-10 text-center text-sm text-[var(--vinea-ink-subtle)]">{t("selectItem")}</p>
      ) : !data ? (
        <p className="py-10 text-center text-sm text-[var(--vinea-ink-subtle)]">
          {enquiry.isLoading ? tr("loading") : tr("noRows")}
        </p>
      ) : (
        <>
          {/* The three figures the whole screen is about, before any table: what is held,
              what it is worth, and the average those two imply. */}
          <ReportPanel>
            <dl className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div>
                <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("onHand")}</dt>
                <dd
                  data-testid="enquiry-quantity"
                  className="font-mono text-lg tabular-nums text-[var(--vinea-ink)]"
                >
                  {quantity(data.total_quantity)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("stockValue")}</dt>
                <dd
                  data-testid="enquiry-value"
                  className="font-mono text-lg tabular-nums text-[var(--vinea-ink)]"
                >
                  {formatMoney(Number(data.total_value), baseLike)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("averageCost")}</dt>
                <dd
                  data-testid="enquiry-average"
                  className="font-mono text-lg tabular-nums text-[var(--vinea-ink)]"
                >
                  {formatQuantity(Number(data.average_cost), 6)}
                </dd>
              </div>
            </dl>
          </ReportPanel>

          <ReportPanel>
            <h2 className="pb-2 text-xs font-semibold uppercase tracking-wide text-[var(--vinea-ink-muted)]">
              {t("locations")}
            </h2>
            {warehouseId && (
              <p className="pb-2 text-xs text-[var(--vinea-ink-subtle)]">
                {t("oneLocationOnly")}
              </p>
            )}
            {data.locations.length === 0 ? (
              <p className="py-6 text-center text-xs text-[var(--vinea-ink-subtle)]">
                {t("noLocations")}
              </p>
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH className="w-32">{tr("warehouse")}</TH>
                    <TH>{tr("name")}</TH>
                    <TH className="text-right">{tr("quantity")}</TH>
                    <TH className="text-right">{tr("value")}</TH>
                  </TR>
                </THead>
                <TBody>
                  {data.locations.map((location) => {
                    const focused = warehouseId === String(location.warehouse_id);
                    return (
                      <TR key={location.warehouse_id}>
                        <TD>
                          {/* Drill one: the location narrows the enquiry to that warehouse —
                              both this table and the moves below it, which is what the endpoint
                              means by a warehouse filter. The row that is left is the way back,
                              so its name says so rather than repeating the way in. */}
                          <button
                            type="button"
                            onClick={() =>
                              pager.filter(setWarehouseId)(
                                focused ? "" : String(location.warehouse_id),
                              )
                            }
                            aria-label={
                              focused
                                ? t("showAllLocations")
                                : t("showMovesAt", { warehouse: location.warehouse_code })
                            }
                            className="font-mono text-xs font-semibold text-[var(--vinea-brand)] underline"
                          >
                            {location.warehouse_code}
                          </button>
                        </TD>
                        <TD className="text-xs text-[var(--vinea-ink-muted)]">
                          {location.warehouse_name}
                          {location.is_in_transit && (
                            <StatusChip tone="info" className="ml-2">
                              {tr("inTransit")}
                            </StatusChip>
                          )}
                        </TD>
                        <TD className="text-right font-mono text-xs tabular-nums">
                          {quantity(location.quantity)}
                        </TD>
                        <TD className="text-right font-mono text-xs tabular-nums">
                          {money(location.value)}
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
            )}
          </ReportPanel>

          <ReportPanel>
            <h2 className="pb-2 text-xs font-semibold uppercase tracking-wide text-[var(--vinea-ink-muted)]">
              {t("moves")}
            </h2>
            {data.moves.length === 0 ? (
              <p className="py-6 text-center text-xs text-[var(--vinea-ink-subtle)]">
                {tr("noRows")}
              </p>
            ) : (
              <>
                <Table>
                  <THead>
                    <TR>
                      <TH className="w-24">{tr("date")}</TH>
                      <TH className="w-16 text-right">{tr("sequence")}</TH>
                      <TH className="w-28">{tr("transactionType")}</TH>
                      <TH className="w-20">{tr("warehouse")}</TH>
                      <TH className="text-right">{tr("quantity")}</TH>
                      <TH className="text-right">{tr("unitCost")}</TH>
                      <TH className="text-right">{tr("value")}</TH>
                      <TH className="text-right">{tr("runningQuantity")}</TH>
                      <TH className="text-right">{tr("runningValue")}</TH>
                      <TH className="w-28">{tr("entry")}</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {/* The opening row is not a move, and is marked as one thing it is:
                        where the running columns start from. Shown only when the window
                        has a start — with no `from`, the first move *is* the opening. */}
                    {dateFrom && (
                      <TR>
                        <TD className="text-xs text-[var(--vinea-ink-muted)]" colSpan={7}>
                          {t("opening")}
                        </TD>
                        <TD className="text-right font-mono text-xs tabular-nums">
                          {quantity(data.opening_quantity)}
                        </TD>
                        <TD className="text-right font-mono text-xs tabular-nums">
                          {money(data.opening_value)}
                        </TD>
                        <TD />
                      </TR>
                    )}
                    {data.moves.map((move) => (
                      <TR key={move.move_id}>
                        <TD className="text-xs text-[var(--vinea-ink-muted)]">
                          {formatDate(move.move_date)}
                        </TD>
                        <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-subtle)]">
                          {move.sequence_no}
                        </TD>
                        <TD className="text-xs">
                          {move.transaction_type_code
                            ? dotted(move.transaction_type_code, move.transaction_type_name ?? "")
                            : tr("emptyValue")}
                          {move.cost_provisional && (
                            <StatusChip tone="warning" className="ml-2">
                              {tr("provisional")}
                            </StatusChip>
                          )}
                        </TD>
                        <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                          {move.warehouse_code}
                        </TD>
                        <TD className="text-right font-mono text-xs tabular-nums">
                          {quantity(move.quantity)}
                        </TD>
                        <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                          {move.unit_cost === null
                            ? tr("emptyValue")
                            : formatQuantity(Number(move.unit_cost), 6)}
                        </TD>
                        <TD className="text-right font-mono text-xs tabular-nums">
                          {money(move.value)}
                        </TD>
                        <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                          {quantity(move.running_quantity)}
                        </TD>
                        <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                          {money(move.running_value)}
                        </TD>
                        <TD>
                          {/* Drill two: the move's entry. A revaluation posts no entry of
                              its own on some paths, so the cell can legitimately be empty. */}
                          {move.journal_entry_id === null ? (
                            <span className="text-xs text-[var(--vinea-ink-subtle)]">
                              {tr("emptyValue")}
                            </span>
                          ) : (
                            <button
                              type="button"
                              onClick={() => setDrillEntryId(move.journal_entry_id)}
                              aria-label={t("drillDown", {
                                number: move.entry_number ?? String(move.journal_entry_id),
                              })}
                              className="inline-flex items-center gap-1 font-mono text-xs font-semibold text-[var(--vinea-brand)] underline"
                            >
                              {move.entry_number ?? move.journal_entry_id}
                              <ExternalLink className="size-3" />
                            </button>
                          )}
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
                <ReportPager
                  count={data.moves.length}
                  hasPrevious={pager.hasPrevious}
                  hasNext={data.next_cursor !== null}
                  onPrevious={pager.previous}
                  onNext={() => pager.next(data.next_cursor)}
                />
              </>
            )}
          </ReportPanel>
        </>
      )}

      <Drawer open={drillEntryId !== null} onOpenChange={(open) => !open && setDrillEntryId(null)}>
        {drillEntryId !== null && (
          <DrawerContent
            title={entry.data?.number ?? tr("entry")}
            description={entry.data ? formatDate(entry.data.entry_date) : undefined}
          >
            <div className="space-y-3">
              <Link
                href={`/gl/entries/${drillEntryId}`}
                className="inline-flex items-center gap-1 text-xs text-[var(--vinea-brand)] underline"
              >
                {t("viewEntry")} <ExternalLink className="size-3" />
              </Link>
              {entry.data && (
                <Table>
                  <THead>
                    <TR>
                      <TH>{tr("account")}</TH>
                      <TH>{tr("description")}</TH>
                      <TH className="text-right">{tr("value")}</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {entry.data.lines.map((line) => {
                      const account = accountById.get(line.gl_account_id);
                      return (
                        <TR key={line.id}>
                          <TD className="font-mono text-xs">
                            {account ? dotted(account.code, account.name) : line.gl_account_id}
                          </TD>
                          <TD className="text-xs text-[var(--vinea-ink-muted)]">
                            {line.description}
                          </TD>
                          <TD className="text-right font-mono text-xs tabular-nums">
                            {money(String(line.base_amount))}
                          </TD>
                        </TR>
                      );
                    })}
                  </TBody>
                </Table>
              )}
            </div>
          </DrawerContent>
        )}
      </Drawer>
    </ReportPage>
  );
}
