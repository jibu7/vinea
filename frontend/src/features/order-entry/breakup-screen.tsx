"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Boxes } from "lucide-react";
import { Button } from "@/design/components/button";
import { QueryState } from "@/design/components/query-state";
import { Combobox } from "@/design/components/combobox";
import { Field } from "@/design/components/input";
import { MaintenancePage } from "@/design/components/maintenance-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useHasPermission } from "@/features/auth/hooks";
import { SalesOrderStatus } from "@/lib/api-enums";
import { dotted, formatQuantity } from "@/lib/format";
import { BreakupDialog } from "./breakup-dialog";
import { useSalesOrder, useSalesOrders } from "./hooks";
import { useOrderLineSupport } from "./order-support";
import type { SalesOrderLine } from "./types";

/**
 * Breakup, standing on its own (P6 decision 8, Appendix C's Transactions → Order Entry row).
 *
 * The same dialog the sales order opens, reached the other way round: an operator told "fix
 * the breakup on SO-14" starts from the order number rather than from a screen they have to
 * know owns it. One implementation of the editor — this screen finds the line and hands it
 * over; the arithmetic, the refusals and the whole-list PUT are the dialog's, as they are when
 * the order opens it.
 *
 * Only an **open** order is offered. Breakup changes what will ship, and what has already
 * been invoiced has shipped: the lines it went out on are posted rows, and no edit here
 * reaches them.
 */
export function BreakupScreen() {
  const t = useTranslations("orderEntry.breakupScreen");
  const tc = useTranslations("orderEntry.common");
  const canManage = useHasPermission()("oe:sales_orders_manage");

  const [orderId, setOrderId] = useState<string>("");
  const [line, setLine] = useState<SalesOrderLine | null>(null);

  const orders = useSalesOrders({ status: SalesOrderStatus.OPEN, limit: 200 });
  const order = useSalesOrder(orderId ? Number(orderId) : null);
  const support = useOrderLineSupport({ role: "sales" });

  const orderOptions = useMemo(
    () =>
      (orders.data?.items ?? []).map((row) => ({
        value: String(row.id),
        label: row.number,
      })),
    [orders.data],
  );

  const componentsOf = (parent: SalesOrderLine) =>
    (order.data?.lines ?? []).filter((other) => other.kit_parent_line_id === parent.id);

  /** A kit line is one that has components under it. Nothing else can be broken up — and a
   * line with none is not a kit whose explosion is empty, it is an ordinary item. */
  const kitLines = (order.data?.lines ?? []).filter(
    (candidate) => candidate.kit_parent_line_id === null && componentsOf(candidate).length > 0,
  );

  return (
    <MaintenancePage title={t("title")} description={t("subtitle")}>
      <div className="max-w-md">
        <Field label={t("order")}>
          <Combobox
            options={orderOptions}
            value={orderId}
            onValueChange={(v) => {
              setOrderId(v);
              setLine(null);
            }}
            placeholder={t("chooseOrder")}
          />
        </Field>
      </div>

      {!orderId ? (
        <p className="py-10 text-center text-xs text-[var(--vinea-ink-subtle)]">{t("chooseFirst")}</p>
      ) : kitLines.length === 0 ? (
        <QueryState query={order} isEmpty empty={t("noKits")} testId="query" />
      ) : (
        <Table>
          <THead>
            <TR>
              <TH className="w-12">{t("lineNo")}</TH>
              <TH>{tc("item")}</TH>
              <TH className="w-24 text-right">{tc("quantity")}</TH>
              <TH className="w-28 text-right">{t("components")}</TH>
              <TH className="w-32 text-right">{t("edited")}</TH>
              <TH className="w-28 text-right">{tc("actions")}</TH>
            </TR>
          </THead>
          <TBody>
            {kitLines.map((kit) => (
              <TR key={kit.id}>
                <TD className="font-mono text-xs text-[var(--vinea-ink-subtle)]">{kit.line_no}</TD>
                <TD className="text-xs font-medium text-[var(--vinea-ink)]">
                  {support.itemLabel(kit.item_id)}
                </TD>
                <TD
                  className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]"
                  data-testid="breakup-kit-quantity"
                >
                  {formatQuantity(Number(kit.quantity), support.quantityDecimals(kit.item_id))}
                </TD>
                <TD className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]">
                  {componentsOf(kit).length}
                </TD>
                <TD className="text-right">
                  {kit.kit_breakup_edited ? (
                    <StatusChip tone="warning">{t("editedYes")}</StatusChip>
                  ) : (
                    <StatusChip tone="neutral">{t("editedNo")}</StatusChip>
                  )}
                </TD>
                <TD className="text-right">
                  <Button
                    variant="ghost"
                    className="gap-1.5 text-xs"
                    disabled={!canManage}
                    onClick={() => setLine(kit)}
                    data-testid="open-breakup"
                  >
                    <Boxes className="size-3.5" /> {t("edit")}
                  </Button>
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}

      {orderId && order.data ? (
        <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">
          <Link
            href={`/oe/sales-orders/${order.data.id}`}
            className="font-medium text-[var(--vinea-brand)] hover:underline"
          >
            {t("openOrder", { number: order.data.number })}
          </Link>
        </p>
      ) : null}
      <p className="pt-1 text-xs text-[var(--vinea-ink-subtle)]">{t("note")}</p>

      {line && orderId ? (
        <BreakupDialog
          orderId={Number(orderId)}
          line={line}
          components={componentsOf(line)}
          onClose={() => setLine(null)}
        />
      ) : null}
    </MaintenancePage>
  );
}
