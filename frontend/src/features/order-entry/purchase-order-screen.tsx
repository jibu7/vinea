"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { FileText, PackageCheck, Pencil, XCircle } from "lucide-react";
import { Button, buttonVariants } from "@/design/components/button";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { DocumentWorkspaceShell } from "@/design/components/document-workspace";
import { Field } from "@/design/components/input";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { isApiError, useHasPermission } from "@/features/auth/hooks";
import { usePartners } from "@/features/subledger/hooks";
import { partnerCode } from "@/features/subledger/types";
import { PurchaseOrderStatus } from "@/lib/api-enums";
import { dotted, formatDate, formatMoney, formatQuantity, todayIso } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { useCancelPurchaseOrder, useClosePurchaseOrder, usePurchaseOrder } from "./hooks";
import { useOrderLineSupport } from "./order-support";
import { PURCHASE_ORDER_STATUS_TONE } from "./purchase-orders-screen";

/**
 * One purchase order: what was ordered, what has arrived, what is left, and the ways out.
 *
 * **Two ways out, because the order has two kinds of line** (decision 4). A stock line is
 * received — *Receive* prepares a goods receipt from what is still open, and the receipt is
 * what posts the accrual. A service line has nothing to receive: a consultant's day is
 * "delivered" by the invoice for it, so *Process invoice* prepares a supplier invoice from the
 * open service lines instead. Offering either against an order that has none of that kind of
 * line would be offering a document that would come back empty, so each is disabled with the
 * reason.
 *
 * Neither posts. Both prepare a document the next screen shows, the operator may lower and
 * never raise, and that screen's own guards then apply (decision 7).
 */
export function PurchaseOrderScreen({ orderId }: { orderId: number }) {
  const t = useTranslations("orderEntry.purchaseOrder");
  const tc = useTranslations("orderEntry.common");
  const ts = useTranslations("orderEntry.purchaseOrderStatus");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canManage = useHasPermission()("oe:purchase_orders_manage");

  const order = usePurchaseOrder(orderId);
  const partners = usePartners("ap", {});
  const support = useOrderLineSupport({ role: "purchase" });
  const closeOrder = useClosePurchaseOrder();
  const cancelOrder = useCancelPurchaseOrder();

  const [transition, setTransition] = useState<"close" | "cancel" | null>(null);
  const [onDate, setOnDate] = useState(todayIso);
  const [banner, setBanner] = useState<string | null>(null);

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
        backHref="/oe/purchase-orders"
        title={t("title")}
        footer={<span className="text-xs text-[var(--vinea-ink-subtle)]">{tc("loading")}</span>}
      >
        <p className="py-10 text-center text-xs text-[var(--vinea-ink-subtle)]">{tc("loading")}</p>
      </DocumentWorkspaceShell>
    );
  }

  const partner = partners.data?.find((p) => p.id === data.partner_id);
  const isOpen =
    data.status === PurchaseOrderStatus.OPEN ||
    data.status === PurchaseOrderStatus.PARTIALLY_RECEIVED;
  const nothingReceived = data.lines.every((line) => Number(line.received) === 0);
  const openLines = data.lines.filter((line) => Number(line.remaining) > 0);
  const canReceive =
    isOpen && canManage && openLines.some((line) => support.isReceivable(line.item_id));
  const canProcessInvoice =
    isOpen && canManage && openLines.some((line) => !support.isReceivable(line.item_id));

  async function runTransition() {
    if (!transition) return;
    setBanner(null);
    const action = transition === "close" ? closeOrder : cancelOrder;
    try {
      await action.mutateAsync({ orderId, payload: { on_date: onDate } });
      toast.show({
        title:
          transition === "close"
            ? t("closed", { number: data!.number })
            : t("cancelled", { number: data!.number }),
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
      backHref="/oe/purchase-orders"
      title={data.number}
      // The two queries are independent, so the document can arrive before the partner
      // list does. `partner!` crashed the whole screen when it did — an error boundary
      // over a document that had loaded perfectly well.
      subtitle={partner ? dotted(partnerCode(partner, "ap") ?? "", partner.name) : ""}
      statusChip={
        <StatusChip tone={PURCHASE_ORDER_STATUS_TONE[data.status] ?? "neutral"}>
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
              disabled={!canManage || !isOpen || !nothingReceived}
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
            {canProcessInvoice ? (
              <Link
                href={`/ap/supplier-invoices/new?purchase_order_id=${data.id}`}
                className={buttonVariants({ variant: "secondary" })}
                data-testid="process-invoice"
              >
                <FileText className="size-3.5" /> {t("processInvoice")}
              </Link>
            ) : (
              <Button variant="secondary" disabled title={t("noServiceLines")}>
                <FileText className="size-3.5" /> {t("processInvoice")}
              </Button>
            )}
            {canReceive ? (
              <Link
                href={`/oe/goods-received/new?purchase_order_id=${data.id}`}
                className={buttonVariants({ variant: "primary" })}
                data-testid="receive-order"
              >
                <PackageCheck className="size-3.5" /> {t("receive")}
              </Link>
            ) : (
              <Button variant="primary" disabled title={t("noStockLines")}>
                <PackageCheck className="size-3.5" /> {t("receive")}
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
            [
              t("expectedDate"),
              data.expected_date ? formatDate(data.expected_date) : tc("emptyValue"),
            ],
            [t("reference"), data.reference ?? tc("emptyValue")],
            [
              t("warehouse"),
              support.warehouses.find((w) => w.id === data.warehouse_id)?.code ?? tc("emptyValue"),
            ],
          ] as Array<[string, string]>
        ).map(([label, value]) => (
          <div key={label}>
            <p className="text-xs uppercase tracking-wider text-[var(--vinea-ink-subtle)]">
              {label}
            </p>
            <p className="text-xs font-medium text-[var(--vinea-ink)]">{value}</p>
          </div>
        ))}
        <div className="flex items-end justify-end">
          {isOpen && canManage ? (
            <Link
              href={`/oe/purchase-orders/${data.id}/edit`}
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
              <TH className="w-24 text-right">{t("received")}</TH>
              <TH className="w-24 text-right">{t("remaining")}</TH>
              <TH className="w-32 text-right">{tc("net")}</TH>
            </TR>
          </THead>
          <TBody>
            {data.lines.map((line) => {
              const decimals = support.quantityDecimals(line.item_id);
              return (
                <TR key={line.id}>
                  <TD className="font-mono text-xs text-[var(--vinea-ink-subtle)]">
                    {line.line_no}
                  </TD>
                  <TD className="text-xs font-medium text-[var(--vinea-ink)]">
                    {support.itemLabel(line.item_id)}
                    {line.description ? (
                      <span className="block text-[var(--vinea-ink-subtle)]">
                        {line.description}
                      </span>
                    ) : null}
                  </TD>
                  <TD
                    className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]"
                    data-testid="line-ordered"
                  >
                    {formatQuantity(Number(line.quantity), decimals)}
                  </TD>
                  <TD
                    className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]"
                    data-testid="line-received"
                  >
                    {formatQuantity(Number(line.received), decimals)}
                  </TD>
                  <TD className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]">
                    {formatQuantity(Number(line.remaining), decimals)}
                  </TD>
                  <TD className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]">
                    {formatMoney(Number(line.net_amount), currency)}
                  </TD>
                </TR>
              );
            })}
          </TBody>
        </Table>
        <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("linesNote")}</p>
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
    </DocumentWorkspaceShell>
  );
}
