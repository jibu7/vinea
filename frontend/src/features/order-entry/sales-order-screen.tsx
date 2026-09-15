"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Boxes, FileText, Pencil, XCircle } from "lucide-react";
import { Button, buttonVariants } from "@/design/components/button";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Dialog, DialogContent } from "@/design/components/dialog";
import {
  DocumentWorkspaceShell,
} from "@/design/components/document-workspace";
import { Field, Input } from "@/design/components/input";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { isApiError, useHasPermission } from "@/features/auth/hooks";
import { usePartners } from "@/features/subledger/hooks";
import { partnerCode } from "@/features/subledger/types";
import { SalesOrderStatus } from "@/lib/api-enums";
import { dotted, formatDate, formatMoney, formatQuantity, todayIso } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { BreakupDialog } from "./breakup-dialog";
import { useCancelSalesOrder, useCloseSalesOrder, useSalesOrder } from "./hooks";
import { SALES_ORDER_STATUS_TONE } from "./sales-orders-screen";
import { useOrderLineSupport } from "./order-support";
import type { SalesOrderLine } from "./types";

/**
 * One sales order: what was promised, what has been invoiced, what is left, and the three
 * things a person does to it.
 *
 * **Invoice** hands the order to `POST …/invoice`, which *prepares* an AR invoice and posts
 * nothing (decision 7). The prepared document is carried to the invoice screen, where the
 * quantities may be lowered and never raised, and where the over-fulfilment guard actually
 * lives. **Close remaining** gives up the rest of the order and keeps the history; **Cancel**
 * is only open while nothing has been fulfilled at all. Neither posts.
 *
 * A **kit** line shows its components indented underneath, because that is what will leave the
 * warehouse and what the cost of the sale will be made of — the parent line carries the price.
 * Its **Breakup** action edits the explosion for this order only (decision 8).
 */
export function SalesOrderScreen({ orderId }: { orderId: number }) {
  const t = useTranslations("orderEntry.salesOrder");
  const tc = useTranslations("orderEntry.common");
  const ts = useTranslations("orderEntry.salesOrderStatus");
  const router = useRouter();
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canManage = useHasPermission()("oe:sales_orders_manage");

  const order = useSalesOrder(orderId);
  const partners = usePartners("ar", {});
  const support = useOrderLineSupport({ role: "sales" });
  const closeOrder = useCloseSalesOrder();
  const cancelOrder = useCancelSalesOrder();

  const [transition, setTransition] = useState<"close" | "cancel" | null>(null);
  const [onDate, setOnDate] = useState(todayIso);
  const [banner, setBanner] = useState<string | null>(null);
  const [breakupLine, setBreakupLine] = useState<SalesOrderLine | null>(null);

  const data = order.data;
  const currency = useMemo(() => {
    const found = support.currencies.find((c) => c.id === data?.currency_id);
    return {
      code: found?.code ?? "",
      decimalPlaces: found?.decimal_places ?? 0,
      symbol: found?.symbol ?? null,
    };
  }, [support.currencies, data?.currency_id]);

  if (!data) {
    return (
      <DocumentWorkspaceShell
        backHref="/oe/sales-orders"
        title={t("title")}
        footer={<span className="text-xs text-[var(--vinea-ink-subtle)]">{tc("loading")}</span>}
      >
        <p className="py-10 text-center text-xs text-[var(--vinea-ink-subtle)]">{tc("loading")}</p>
      </DocumentWorkspaceShell>
    );
  }

  const partner = partners.data?.find((p) => p.id === data.partner_id);
  const parents = data.lines.filter((line) => line.kit_parent_line_id === null);
  const componentsOf = (line: SalesOrderLine) =>
    data.lines.filter((other) => other.kit_parent_line_id === line.id);

  const isOpen =
    data.status === SalesOrderStatus.OPEN || data.status === SalesOrderStatus.PARTIALLY_INVOICED;
  const nothingFulfilled = data.lines.every((line) => Number(line.invoiced) === 0);

  async function runTransition() {
    if (!transition) return;
    setBanner(null);
    const action = transition === "close" ? closeOrder : cancelOrder;
    try {
      await action.mutateAsync({ orderId, payload: { on_date: onDate } });
      toast.show({
        title: transition === "close" ? t("closed", { number: data!.number }) : t("cancelled", { number: data!.number }),
        tone: "success",
      });
      setTransition(null);
    } catch (err) {
      if (isApiError(err)) setBanner(err.message);
      else showApiError(err, t("transitionFailed"));
    }
  }

  return (
    <DocumentWorkspaceShell
      backHref="/oe/sales-orders"
      title={data.number}
      // The two queries are independent, so the document can arrive before the partner
      // list does. `partner!` crashed the whole screen when it did — an error boundary
      // over a document that had loaded perfectly well.
      subtitle={partner ? dotted(partnerCode(partner, "ar") ?? "", partner.name) : ""}
      statusChip={
        <StatusChip tone={SALES_ORDER_STATUS_TONE[data.status] ?? "neutral"}>
          {ts(data.status)}
        </StatusChip>
      }
      errorBanner={banner}
      footer={
        <div className="flex items-center justify-between gap-6">
          <div className="flex items-center gap-6 text-sm">
            <span className="text-[var(--vinea-ink-muted)]">
              {tc("net")}{" "}
              <span className="font-mono tabular-nums text-[var(--vinea-ink)]">
                {formatMoney(Number(data.net_amount), currency)}
              </span>
            </span>
            <span className="text-[var(--vinea-ink-muted)]">
              {tc("tax")}{" "}
              <span className="font-mono tabular-nums text-[var(--vinea-ink)]">
                {formatMoney(Number(data.tax_amount), currency)}
              </span>
            </span>
            <span className="text-[var(--vinea-ink-muted)]">
              {tc("total")}{" "}
              <span
                className="font-mono tabular-nums text-[var(--vinea-ink)]"
                data-testid="order-total"
              >
                {formatMoney(Number(data.total_amount), currency)}
              </span>
            </span>
          </div>
          <div className="flex items-center gap-3">
            <Button
              variant="secondary"
              disabled={!canManage || !isOpen || !nothingFulfilled}
              onClick={() => setTransition("cancel")}
            >
              <XCircle className="size-3.5" /> {t("cancel")}
            </Button>
            <Button
              variant="secondary"
              disabled={!canManage || !isOpen}
              onClick={() => setTransition("close")}
            >
              {t("closeRemaining")}
            </Button>
            {isOpen && canManage ? (
              <Link
                href={`/ar/invoices/new?sales_order_id=${data.id}`}
                className={buttonVariants({ variant: "primary" })}
                data-testid="invoice-order"
              >
                <FileText className="size-3.5" /> {t("invoice")}
              </Link>
            ) : (
              <Button variant="primary" disabled>
                <FileText className="size-3.5" /> {t("invoice")}
              </Button>
            )}
          </div>
        </div>
      }
    >
      <section className="grid grid-cols-2 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-5">
        {(
          [
            [t("orderDate"), formatDate(data.order_date)],
            [t("expectedDate"), data.expected_date ? formatDate(data.expected_date) : tc("emptyValue")],
            [t("reference"), data.reference ?? tc("emptyValue")],
            [t("warehouse"), support.warehouses.find((w) => w.id === data.warehouse_id)?.code ?? tc("emptyValue")],
          ] as Array<[string, string]>
        ).map(([label, value]) => (
          <div key={label}>
            <p className="text-xs uppercase tracking-wider text-[var(--vinea-ink-subtle)]">{label}</p>
            <p className="text-xs font-medium text-[var(--vinea-ink)]">{value}</p>
          </div>
        ))}
        <div className="flex items-end justify-end">
          {isOpen && canManage ? (
            <Link
              href={`/oe/sales-orders/${data.id}/edit`}
              className={buttonVariants({ variant: "secondary", size: "sm" })}
              data-testid="edit-order"
            >
              <Pencil className="size-3.5" /> {tc("edit")}
            </Link>
          ) : null}
        </div>
      </section>

      <section className="space-y-2">
        <h2 className="text-xs font-medium uppercase tracking-wide text-[var(--vinea-ink-subtle)]">
          {t("lines")}
        </h2>
        <Table>
          <THead>
            <TR>
              <TH className="w-12">{t("lineNo")}</TH>
              <TH>{t("item")}</TH>
              <TH className="w-24 text-right">{t("ordered")}</TH>
              <TH className="w-24 text-right">{t("invoiced")}</TH>
              <TH className="w-24 text-right">{t("remaining")}</TH>
              <TH className="w-32 text-right">{tc("net")}</TH>
              <TH className="w-28 text-right">{tc("actions")}</TH>
            </TR>
          </THead>
          <TBody>
            {parents.flatMap((line) => {
              const components = componentsOf(line);
              const decimals = support.quantityDecimals(line.item_id);
              const rows = [
                <TR key={line.id}>
                  <TD className="font-mono text-xs text-[var(--vinea-ink-subtle)]">{line.line_no}</TD>
                  <TD className="text-xs font-medium text-[var(--vinea-ink)]">
                    {support.itemLabel(line.item_id)}
                    {line.description ? (
                      <span className="block text-[var(--vinea-ink-subtle)]">{line.description}</span>
                    ) : null}
                  </TD>
                  <TD
                    className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]"
                    data-testid="line-ordered"
                  >
                    {formatQuantity(Number(line.quantity), decimals)}
                  </TD>
                  <TD className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]">
                    {formatQuantity(Number(line.invoiced), decimals)}
                  </TD>
                  <TD className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]">
                    {formatQuantity(Number(line.remaining), decimals)}
                  </TD>
                  <TD className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]">
                    {formatMoney(Number(line.net_amount), currency)}
                  </TD>
                  <TD className="text-right">
                    {components.length > 0 ? (
                      <Button
                        variant="ghost"
                        className="gap-1.5 text-xs"
                        disabled={!canManage || !isOpen}
                        onClick={() => setBreakupLine(line)}
                      >
                        <Boxes className="size-3.5" /> {t("breakup")}
                      </Button>
                    ) : null}
                  </TD>
                </TR>,
              ];
              for (const component of components) {
                const componentDecimals = support.quantityDecimals(component.item_id);
                rows.push(
                  <TR key={component.id} className="bg-[var(--vinea-surface-sunken)]/40">
                    <TD />
                    <TD className="pl-8 text-xs text-[var(--vinea-ink-muted)]">
                      {support.itemLabel(component.item_id)}
                    </TD>
                    <TD className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink-muted)]">
                      {formatQuantity(Number(component.quantity), componentDecimals)}
                    </TD>
                    <TD />
                    <TD />
                    <TD />
                    <TD />
                  </TR>,
                );
              }
              return rows;
            })}
          </TBody>
        </Table>
        <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("componentsNote")}</p>
      </section>

      <Dialog open={transition !== null} onOpenChange={(open) => !open && setTransition(null)}>
        <DialogContent
          title={transition === "cancel" ? t("cancelTitle") : t("closeTitle")}
          description={transition === "cancel" ? t("cancelNote") : t("closeNote")}
        >
          <div className="space-y-3 pt-2">
            <Field label={t("onDate")}>
              <IsoDatePicker value={onDate} onValueChange={setOnDate} />
            </Field>
            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setTransition(null)}>
                {tc("cancel")}
              </Button>
              <Button variant="primary" onClick={runTransition}>
                {transition === "cancel" ? t("cancelConfirm") : t("closeConfirm")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {breakupLine ? (
        <BreakupDialog
          orderId={orderId}
          line={breakupLine}
          components={componentsOf(breakupLine)}
          onClose={() => setBreakupLine(null)}
        />
      ) : null}
    </DocumentWorkspaceShell>
  );
}
