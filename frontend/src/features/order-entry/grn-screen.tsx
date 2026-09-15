"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { FileText, Undo2 } from "lucide-react";
import { Button, buttonVariants } from "@/design/components/button";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { DocumentWorkspaceShell } from "@/design/components/document-workspace";
import { Field, Input } from "@/design/components/input";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { isApiError, useHasPermission } from "@/features/auth/hooks";
import { useCurrencies } from "@/features/gl/hooks";
import { useInventoryLineSupport } from "@/features/inventory/line-support";
import { usePartners } from "@/features/subledger/hooks";
import { partnerCode } from "@/features/subledger/types";
import { GrnStatus } from "@/lib/api-enums";
import { newDraftId } from "@/lib/drafts";
import { dotted, formatDate, formatMoney, formatQuantity, todayIso } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { GRN_STATUS_TONE } from "./goods-received-screen";
import { useGrn, useReverseGrn } from "./hooks";

/**
 * One goods receipt: what arrived, what it was valued at, and how much of it an invoice has
 * since claimed.
 *
 * **Reverse is disabled with its reason, never silently.** A receipt any part of which has
 * been matched refuses reversal with `grn_matched` — the invoice that matched it is the
 * document that has to come back first, and saying so on the button is the difference between
 * a rule and a mystery. A receipt already reversed has nothing left to undo. Everything else
 * reverses, subject to the negative-stock policy at the moment it is tried, which is the
 * service's call and arrives in this dialog if it goes against.
 *
 * **Process invoice** hands the unmatched lines to the supplier invoice screen in matching
 * mode — the second half of the two-step. It prepares and posts nothing.
 */
export function GrnScreen({ grnId }: { grnId: number }) {
  const t = useTranslations("orderEntry.goodsReceipt");
  const tc = useTranslations("orderEntry.common");
  const ts = useTranslations("orderEntry.grnStatus");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canProcess = useHasPermission()("oe:grv_process");

  const grn = useGrn(grnId);
  const partners = usePartners("ap", {});
  const support = useInventoryLineSupport({ includeInactiveItems: true });
  const currencies = useCurrencies();
  const reverseGrn = useReverseGrn();

  const [reversing, setReversing] = useState(false);
  const [onDate, setOnDate] = useState(todayIso);
  const [reason, setReason] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState(newDraftId);
  const [banner, setBanner] = useState<string | null>(null);

  const data = grn.data;
  const currency = useMemo(() => {
    const found = (currencies.data ?? []).find((c) => c.id === data?.currency_id);
    return {
      code: found?.code ?? "",
      decimalPlaces: found?.decimal_places ?? 0,
      symbol: found?.symbol ?? null,
    };
  }, [currencies.data, data?.currency_id]);

  if (!data) {
    return (
      <DocumentWorkspaceShell
        backHref="/oe/goods-received"
        title={t("detailTitle")}
        footer={<span className="text-xs text-[var(--vinea-ink-subtle)]">{tc("loading")}</span>}
      >
        <p className="py-10 text-center text-xs text-[var(--vinea-ink-subtle)]">{tc("loading")}</p>
      </DocumentWorkspaceShell>
    );
  }

  const partner = partners.data?.find((p) => p.id === data.partner_id);
  const isReversed = data.status === GrnStatus.REVERSED;
  const anyMatched = data.lines.some((line) => Number(line.matched) > 0);
  const anyUnmatched = data.lines.some((line) => Number(line.unmatched) > 0);
  const receivedValue = data.lines.reduce((sum, line) => sum + Number(line.value), 0);

  /** Why Reverse cannot be pressed, in the service's own terms. Null means it can. */
  const reverseBlockedBy = isReversed
    ? t("alreadyReversed")
    : anyMatched
      ? t("matchedCannotReverse")
      : null;

  async function runReverse() {
    setBanner(null);
    try {
      await reverseGrn.mutateAsync({ grnId, payload: { on_date: onDate, reason }, idempotencyKey });
      toast.show({ title: t("reversed", { number: data!.number }), tone: "success" });
      setReversing(false);
      setIdempotencyKey(newDraftId());
    } catch (err) {
      if (isApiError(err)) setBanner(err.message);
      else showApiError(err, t("reverseFailed"));
    }
  }

  return (
    <DocumentWorkspaceShell
      backHref="/oe/goods-received"
      title={data.number}
      subtitle={dotted(partnerCode(partner!, "ap") ?? "", partner?.name ?? "")}
      statusChip={
        <StatusChip tone={GRN_STATUS_TONE[data.status] ?? "neutral"}>{ts(data.status)}</StatusChip>
      }
      errorBanner={banner}
      footer={
        <div className="flex items-center justify-between gap-6">
          <span className="text-sm text-[var(--vinea-ink-muted)]">
            {t("receivedValue")}{" "}
            <span
              className="font-mono tabular-nums text-[var(--vinea-ink)]"
              data-testid="grn-value"
            >
              {formatMoney(receivedValue, currency)}
            </span>
          </span>
          <div className="flex items-center gap-3">
            {reverseBlockedBy ? (
              <p className="text-xs text-[var(--vinea-ink-subtle)]" data-testid="reverse-blocked">
                {reverseBlockedBy}
              </p>
            ) : null}
            <Button
              variant="secondary"
              disabled={!canProcess || reverseBlockedBy !== null}
              onClick={() => setReversing(true)}
              data-testid="reverse-grn"
            >
              <Undo2 className="size-3.5" /> {tc("reverse")}
            </Button>
            {canProcess && !isReversed && anyUnmatched ? (
              <Link
                href={`/ap/supplier-invoices/new?grn_id=${data.id}`}
                className={buttonVariants({ variant: "primary" })}
                data-testid="process-invoice"
              >
                <FileText className="size-3.5" /> {t("processInvoice")}
              </Link>
            ) : (
              <Button variant="primary" disabled title={t("nothingToInvoice")}>
                <FileText className="size-3.5" /> {t("processInvoice")}
              </Button>
            )}
          </div>
        </div>
      }
    >
      <section className="grid grid-cols-2 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-5">
        {(
          [
            [tc("date"), formatDate(data.grn_date)],
            [
              tc("warehouse"),
              support.warehouses.find((w) => w.id === data.warehouse_id)?.code ?? tc("emptyValue"),
            ],
            [t("supplierReference"), data.supplier_reference ?? tc("emptyValue")],
            [tc("description"), data.description],
            [
              tc("reversed"),
              data.reversed_on ? formatDate(data.reversed_on) : tc("emptyValue"),
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
      </section>

      {data.journal_entry_id ? (
        <p className="text-xs text-[var(--vinea-ink-muted)]">
          {t("entryPrefix")}{" "}
          <Link
            href={`/gl/entries/${data.journal_entry_id}`}
            className="font-medium text-[var(--vinea-brand)] hover:underline"
            data-testid="grn-entry-link"
          >
            {t("viewEntry")}
          </Link>
          {data.reversal_entry_id ? (
            <>
              {" · "}
              <Link
                href={`/gl/entries/${data.reversal_entry_id}`}
                className="font-medium text-[var(--vinea-brand)] hover:underline"
              >
                {t("viewReversalEntry")}
              </Link>
            </>
          ) : null}
        </p>
      ) : null}

      <section className="space-y-2">
        <h2 className="text-xs font-medium uppercase tracking-wide text-[var(--vinea-ink-subtle)]">
          {t("lines")}
        </h2>
        <Table>
          <THead>
            <TR>
              <TH className="w-12">{t("lineNo")}</TH>
              <TH>{tc("item")}</TH>
              <TH className="w-24 text-right">{tc("quantity")}</TH>
              <TH className="w-28 text-right">{t("unitCost")}</TH>
              <TH className="w-32 text-right">{tc("value")}</TH>
              <TH className="w-28 text-right">{t("matched")}</TH>
              <TH className="w-28 text-right">{t("unmatched")}</TH>
            </TR>
          </THead>
          <TBody>
            {data.lines.map((line) => {
              const item = support.itemById.get(line.item_id);
              const decimals =
                support.uomById.get(item?.base_uom_id ?? 0)?.decimal_places ?? 0;
              return (
                <TR key={line.id}>
                  <TD className="font-mono text-xs text-[var(--vinea-ink-subtle)]">
                    {line.line_no}
                  </TD>
                  <TD className="text-xs font-medium text-[var(--vinea-ink)]">
                    {item ? dotted(item.code, item.name) : line.item_id}
                    {line.description ? (
                      <span className="block text-[var(--vinea-ink-subtle)]">
                        {line.description}
                      </span>
                    ) : null}
                  </TD>
                  <TD
                    className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]"
                    data-testid="grn-line-quantity"
                  >
                    {formatQuantity(Number(line.quantity), decimals)}
                  </TD>
                  <TD className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink-muted)]">
                    {formatMoney(Number(line.unit_cost), currency)}
                  </TD>
                  <TD
                    className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]"
                    data-testid="grn-line-value"
                  >
                    {formatMoney(Number(line.value), currency)}
                  </TD>
                  <TD className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink-muted)]">
                    {formatQuantity(Number(line.matched), decimals)}
                  </TD>
                  <TD className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]">
                    {formatQuantity(Number(line.unmatched), decimals)}
                  </TD>
                </TR>
              );
            })}
          </TBody>
        </Table>
        <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("linesNote")}</p>
      </section>

      <Dialog open={reversing} onOpenChange={(open) => !open && setReversing(false)}>
        <DialogContent title={t("reverseTitle")} description={t("reverseNote")}>
          <div className="space-y-3 pt-2">
            <Field label={t("onDate")}>
              <IsoDatePicker value={onDate} onValueChange={setOnDate} />
            </Field>
            <Field label={tc("reason")}>
              <Input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder={t("reasonPlaceholder")}
              />
            </Field>
            <div className="flex justify-end gap-2 pt-3">
              <Button variant="ghost" onClick={() => setReversing(false)}>
                {tc("cancel")}
              </Button>
              <Button
                variant="danger"
                onClick={runReverse}
                disabled={!reason || reverseGrn.isPending}
                data-testid="reverse-confirm"
              >
                {reverseGrn.isPending ? tc("saving") : t("reverseConfirm")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </DocumentWorkspaceShell>
  );
}
