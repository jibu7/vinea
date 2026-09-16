"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Plus } from "lucide-react";
import { Button, buttonVariants } from "@/design/components/button";
import { QueryState } from "@/design/components/query-state";
import { MaintenancePage } from "@/design/components/maintenance-page";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useHasPermission } from "@/features/auth/hooks";
import { usePartners } from "@/features/subledger/hooks";
import { SalesOrderStatus } from "@/lib/api-enums";
import { formatDate, formatMoney } from "@/lib/format";
import { useSalesOrders } from "./hooks";
import { useOrderLineSupport } from "./order-support";

export const SALES_ORDER_STATUS_TONE: Record<
  string,
  "neutral" | "success" | "warning" | "danger" | "info"
> = {
  [SalesOrderStatus.OPEN]: "info",
  [SalesOrderStatus.PARTIALLY_INVOICED]: "warning",
  [SalesOrderStatus.INVOICED]: "success",
  [SalesOrderStatus.CLOSED]: "neutral",
  [SalesOrderStatus.CANCELLED]: "neutral",
};

/**
 * Sales orders (P6 decision 3): the listing, with what each order still owes the customer.
 *
 * **The backorder column is the listing's half of decision 7** — the plan asks for it on the
 * order, the enquiry *and* the listing, and the endpoint computes it by the same rule the
 * enquiry uses per line, so the two screens cannot disagree about which lines are short.
 *
 * It is a **count of short lines**, not a quantity (step 9). It was a quantity: the sum of the
 * per-line base quantities, each in its own item's unit, so an order 3 kg short of coffee and
 * 2 crates short of wine read `5` — a number in no unit at all, and one this screen had to
 * caption an apology under. There is no honest order-level backorder quantity, so the order
 * level carries the count and the quantities stay where they have a unit: on the order, per
 * line, where the enquiry shows them.
 *
 * An order posts nothing (decision 3), so there is no journal entry to drill to from here —
 * the documents raised against an order hang off the order itself.
 */
export function SalesOrdersScreen() {
  const t = useTranslations("orderEntry.salesOrders");
  const tc = useTranslations("orderEntry.common");
  const ts = useTranslations("orderEntry.salesOrderStatus");
  const canManage = useHasPermission()("oe:sales_orders_manage");

  const [status, setStatus] = useState<string>("");
  const orders = useSalesOrders({ status: status || undefined });
  const partners = usePartners("ar", {});
  const support = useOrderLineSupport();

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
              ...Object.values(SalesOrderStatus).map((value) => ({
                value,
                label: ts(value),
              })),
            ]}
            value={status}
            onValueChange={setStatus}
            ariaLabel={tc("status")}
            className="w-56"
          />
          {canManage ? (
            <Link href="/oe/sales-orders/new" className={buttonVariants({ variant: "primary" })}>
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
        <QueryState query={orders} isEmpty empty={t("empty")} testId="query" />
      ) : (
        <Table>
          <THead>
            <TR>
              <TH className="w-32">{t("number")}</TH>
              <TH>{tc("customer")}</TH>
              <TH className="w-28">{t("orderDate")}</TH>
              <TH className="w-28">{t("expectedDate")}</TH>
              <TH className="w-32 text-right">{t("backorderedLines")}</TH>
              <TH className="w-36 text-right">{tc("total")}</TH>
              <TH className="w-40 text-right">{tc("status")}</TH>
            </TR>
          </THead>
          <TBody>
            {rows.map((order) => (
              <TR key={order.id}>
                <TD>
                  <Link
                    href={`/oe/sales-orders/${order.id}`}
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
                  data-testid="order-backordered-lines"
                >
                  {order.backordered_lines}
                </TD>
                <TD
                  className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]"
                  data-testid="order-total"
                >
                  {formatMoney(Number(order.total_amount), currencyOf(order.currency_id))}
                </TD>
                <TD className="text-right">
                  <StatusChip tone={SALES_ORDER_STATUS_TONE[order.status] ?? "neutral"}>
                    {ts(order.status)}
                  </StatusChip>
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
      <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">{t("backorderNote")}</p>
    </MaintenancePage>
  );
}
