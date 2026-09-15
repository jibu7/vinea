"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Plus } from "lucide-react";
import { Button, buttonVariants } from "@/design/components/button";
import { MaintenancePage } from "@/design/components/maintenance-page";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useHasPermission } from "@/features/auth/hooks";
import { useCurrencies } from "@/features/gl/hooks";
import { GrnStatus } from "@/lib/api-enums";
import { formatDate, formatMoney } from "@/lib/format";
import { useGoodsReceived } from "./hooks";

export const GRN_STATUS_TONE: Record<
  string,
  "neutral" | "success" | "warning" | "danger" | "info"
> = {
  [GrnStatus.RECEIVED]: "info",
  [GrnStatus.PARTIALLY_MATCHED]: "warning",
  [GrnStatus.MATCHED]: "success",
  [GrnStatus.REVERSED]: "neutral",
};

/**
 * Goods received (P6 decision 6): every receipt, and what each one still owes an invoice.
 *
 * **The status words are Evolution's, the values are ours.** A receipt's stored status is
 * `received` → `partially_matched` → `matched`; a storeman who has used Evolution for fifteen
 * years knows the same three states as Unprocessed → Confirmed → Processed, and renaming them
 * on screen costs nothing while renaming them in the database would cost every query that
 * reads one. The mapping lives here, once, next to the listing that uses it.
 *
 * `unmatched_total` is over the whole filtered set rather than this page, deliberately: it
 * exists to be compared with the GRN accrual account's balance, and a per-page total could
 * not be.
 */
export function GoodsReceivedScreen() {
  const t = useTranslations("orderEntry.goodsReceived");
  const tc = useTranslations("orderEntry.common");
  const ts = useTranslations("orderEntry.grnStatus");
  const canProcess = useHasPermission()("oe:grv_process");

  const [status, setStatus] = useState<string>("");
  const listing = useGoodsReceived({ status: status || undefined });
  const currencies = useCurrencies();

  const base = (currencies.data ?? []).find((c) => c.is_base);
  const baseCurrency = {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };

  const rows = listing.data?.items ?? [];

  return (
    <MaintenancePage
      title={t("title")}
      description={t("subtitle")}
      actions={
        <div className="flex items-center gap-3">
          <Select
            options={[
              { value: "", label: t("allStatuses") },
              ...Object.values(GrnStatus).map((value) => ({ value, label: ts(value) })),
            ]}
            value={status}
            onValueChange={setStatus}
            ariaLabel={tc("status")}
            className="w-56"
          />
          {canProcess ? (
            <Link href="/oe/goods-received/new" className={buttonVariants({ variant: "primary" })}>
              <Plus className="size-3.5" /> {t("new")}
            </Link>
          ) : (
            <Button variant="primary" disabled className="gap-1.5 text-xs">
              <Plus className="size-3.5" /> {t("new")}
            </Button>
          )}
        </div>
      }
    >
      {rows.length === 0 ? (
        <p className="py-10 text-center text-xs text-[var(--vinea-ink-subtle)]">
          {listing.isLoading ? tc("loading") : t("empty")}
        </p>
      ) : (
        <>
          <Table>
            <THead>
              <TR>
                <TH className="w-32">{tc("number")}</TH>
                <TH>{tc("supplier")}</TH>
                <TH className="w-28">{tc("date")}</TH>
                <TH className="w-36 text-right">{t("receivedValue")}</TH>
                <TH className="w-36 text-right">{t("matchedValue")}</TH>
                <TH className="w-36 text-right">{t("unmatchedValue")}</TH>
                <TH className="w-36 text-right">{tc("status")}</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map((row) => (
                <TR key={row.id}>
                  <TD>
                    <Link
                      href={`/oe/goods-received/${row.id}`}
                      className="font-mono text-xs font-semibold text-[var(--vinea-brand)] hover:underline"
                      data-testid="grn-number"
                    >
                      {row.number}
                    </Link>
                  </TD>
                  <TD className="text-xs font-medium text-[var(--vinea-ink)]">{row.partner_name}</TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">
                    {formatDate(row.grn_date)}
                  </TD>
                  <TD
                    className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]"
                    data-testid="grn-received-value"
                  >
                    {formatMoney(Number(row.received_value), baseCurrency)}
                  </TD>
                  <TD className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink-muted)]">
                    {formatMoney(Number(row.matched_value), baseCurrency)}
                  </TD>
                  <TD
                    className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]"
                    data-testid="grn-unmatched-value"
                  >
                    {formatMoney(Number(row.unmatched_value), baseCurrency)}
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
          <div className="flex justify-end gap-2 pt-3 text-sm">
            <span className="text-[var(--vinea-ink-muted)]">{t("unmatchedTotal")}</span>
            <span
              className="font-mono tabular-nums font-semibold text-[var(--vinea-ink)]"
              data-testid="grn-unmatched-total"
            >
              {formatMoney(Number(listing.data?.unmatched_total ?? 0), baseCurrency)}
            </span>
          </div>
        </>
      )}
      <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">{t("statusNote")}</p>
      <p className="pt-1 text-xs text-[var(--vinea-ink-subtle)]">{t("accrualNote")}</p>
    </MaintenancePage>
  );
}
