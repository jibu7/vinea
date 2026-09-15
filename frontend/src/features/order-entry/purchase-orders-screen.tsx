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
import { usePartners } from "@/features/subledger/hooks";
import { PurchaseOrderStatus } from "@/lib/api-enums";
import { formatDate, formatMoney } from "@/lib/format";
import { usePurchaseOrders } from "./hooks";
import { useOrderLineSupport } from "./order-support";

export const PURCHASE_ORDER_STATUS_TONE: Record<
  string,
  "neutral" | "success" | "warning" | "danger" | "info"
> = {
  [PurchaseOrderStatus.OPEN]: "info",
  [PurchaseOrderStatus.PARTIALLY_RECEIVED]: "warning",
  [PurchaseOrderStatus.RECEIVED]: "success",
  [PurchaseOrderStatus.CLOSED]: "neutral",
  [PurchaseOrderStatus.CANCELLED]: "neutral",
};

/**
 * Purchase orders: what has been ordered from suppliers and how much of it has arrived.
 *
 * No backorder column, and its absence is the point rather than an omission: a backorder is
 * what a *customer* was promised that the shelf cannot cover. What a purchase order is short
 * of is simply what has not been delivered yet, which the status and the per-line remaining
 * already say.
 */
export function PurchaseOrdersScreen() {
  const t = useTranslations("orderEntry.purchaseOrders");
  const tc = useTranslations("orderEntry.common");
  const ts = useTranslations("orderEntry.purchaseOrderStatus");
  const canManage = useHasPermission()("oe:purchase_orders_manage");

  const [status, setStatus] = useState<string>("");
  const orders = usePurchaseOrders({ status: status || undefined });
  const partners = usePartners("ap", {});
  const support = useOrderLineSupport({ role: "purchase" });

  const partnerName = (id: number) =>
    partners.data?.find((partner) => partner.id === id)?.name ?? tc("emptyValue");

  const currencyOf = (id: number) => {
    const currency = support.currencies.find((c) => c.id === id);
    return {
      code: currency?.code ?? "",
      decimalPlaces: currency?.decimal_places ?? 0,
      symbol: currency?.symbol ?? null,
    };
  };

  const rows = orders.data?.items ?? [];

  return (
    <MaintenancePage
      title={t("title")}
      description={t("subtitle")}
      actions={
        <div className="flex items-center gap-3">
          <Select
            options={[
              { value: "", label: t("allStatuses") },
              ...Object.values(PurchaseOrderStatus).map((value) => ({ value, label: ts(value) })),
            ]}
            value={status}
            onValueChange={setStatus}
            ariaLabel={tc("status")}
            className="w-56"
          />
          {canManage ? (
            <Link href="/oe/purchase-orders/new" className={buttonVariants({ variant: "primary" })}>
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
          {orders.isLoading ? tc("loading") : t("empty")}
        </p>
      ) : (
        <Table>
          <THead>
            <TR>
              <TH className="w-32">{t("number")}</TH>
              <TH>{tc("supplier")}</TH>
              <TH className="w-28">{t("orderDate")}</TH>
              <TH className="w-28">{t("expectedDate")}</TH>
              <TH className="w-36 text-right">{tc("total")}</TH>
              <TH className="w-44 text-right">{tc("status")}</TH>
            </TR>
          </THead>
          <TBody>
            {rows.map((order) => (
              <TR key={order.id}>
                <TD>
                  <Link
                    href={`/oe/purchase-orders/${order.id}`}
                    className="font-mono text-xs font-semibold text-[var(--vinea-brand)] hover:underline"
                    data-testid="order-number"
                  >
                    {order.number}
                  </Link>
                </TD>
                <TD className="text-xs font-medium text-[var(--vinea-ink)]">
                  {partnerName(order.partner_id)}
                </TD>
                <TD className="text-xs text-[var(--vinea-ink-muted)]">
                  {formatDate(order.order_date)}
                </TD>
                <TD className="text-xs text-[var(--vinea-ink-muted)]">
                  {order.expected_date ? formatDate(order.expected_date) : tc("emptyValue")}
                </TD>
                <TD
                  className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]"
                  data-testid="order-total"
                >
                  {formatMoney(Number(order.total_amount), currencyOf(order.currency_id))}
                </TD>
                <TD className="text-right">
                  <StatusChip tone={PURCHASE_ORDER_STATUS_TONE[order.status] ?? "neutral"}>
                    {ts(order.status)}
                  </StatusChip>
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
      <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">{t("noBackorderNote")}</p>
    </MaintenancePage>
  );
}
