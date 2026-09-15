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
import { LandedCostStatus } from "@/lib/api-enums";
import { formatDate, formatMoney } from "@/lib/format";
import { useLandedCosts } from "./hooks";

export const LANDED_COST_STATUS_TONE: Record<
  string,
  "neutral" | "success" | "warning" | "danger" | "info"
> = {
  [LandedCostStatus.POSTED]: "success",
  [LandedCostStatus.REVERSED]: "neutral",
};

/**
 * Landed costs (P6 decision 9): every cost that has been spread into the value of goods.
 *
 * There is no "draft" state to filter for, because there is no such thing: a landed cost is
 * computed and posted in one step, and the only other state it can be in is reversed.
 */
export function LandedCostsScreen() {
  const t = useTranslations("orderEntry.landedCosts");
  const tc = useTranslations("orderEntry.common");
  const ts = useTranslations("orderEntry.landedCostStatus");
  const tb = useTranslations("orderEntry.landedCost");
  const canPost = useHasPermission()("oe:landed_cost_post");

  const [status, setStatus] = useState<string>("");
  const documents = useLandedCosts({ status: status || undefined });
  const currencies = useCurrencies();

  const base = (currencies.data ?? []).find((c) => c.is_base);
  const currency = {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };

  const rows = documents.data?.items ?? [];

  return (
    <MaintenancePage
      title={t("title")}
      description={t("subtitle")}
      actions={
        <div className="flex items-center gap-3">
          <Select
            options={[
              { value: "", label: t("allStatuses") },
              ...Object.values(LandedCostStatus).map((value) => ({ value, label: ts(value) })),
            ]}
            value={status}
            onValueChange={setStatus}
            ariaLabel={tc("status")}
            className="w-56"
          />
          {canPost ? (
            <Link href="/oe/landed-costs/new" className={buttonVariants({ variant: "primary" })}>
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
          {documents.isLoading ? tc("loading") : t("empty")}
        </p>
      ) : (
        <Table>
          <THead>
            <TR>
              <TH className="w-32">{tc("number")}</TH>
              <TH className="w-28">{tc("date")}</TH>
              <TH>{tc("description")}</TH>
              <TH className="w-28">{t("basis")}</TH>
              <TH className="w-36 text-right">{t("amount")}</TH>
              <TH className="w-32 text-right">{tc("status")}</TH>
            </TR>
          </THead>
          <TBody>
            {rows.map((row) => (
              <TR key={row.id}>
                <TD>
                  <Link
                    href={`/oe/landed-costs/${row.id}`}
                    className="font-mono text-xs font-semibold text-[var(--vinea-brand)] hover:underline"
                    data-testid="landed-cost-number"
                  >
                    {row.number}
                  </Link>
                </TD>
                <TD className="text-xs text-[var(--vinea-ink-muted)]">
                  {formatDate(row.cost_date)}
                </TD>
                <TD className="text-xs text-[var(--vinea-ink)]">{row.description}</TD>
                <TD className="text-xs text-[var(--vinea-ink-muted)]">{tb(`basis_${row.basis}`)}</TD>
                <TD
                  className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]"
                  data-testid="landed-cost-amount"
                >
                  {formatMoney(Number(row.amount), currency)}
                </TD>
                <TD className="text-right">
                  <StatusChip tone={LANDED_COST_STATUS_TONE[row.status] ?? "neutral"}>
                    {ts(row.status)}
                  </StatusChip>
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
      <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">{t("note")}</p>
    </MaintenancePage>
  );
}
