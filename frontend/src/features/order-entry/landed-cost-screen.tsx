"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Undo2 } from "lucide-react";
import { Button } from "@/design/components/button";
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
import { LandedCostStatus } from "@/lib/api-enums";
import { newDraftId } from "@/lib/drafts";
import { dotted, formatDate, formatMoney, formatQuantity, todayIso } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { useLandedCost, useReverseLandedCost } from "./hooks";
import { LANDED_COST_STATUS_TONE } from "./landed-costs-screen";

/**
 * One landed cost: what was spread, over what, and how it fell.
 *
 * A line marked **to cost of sales** is one whose goods had already left the location by the
 * time the cost was posted. There was no stock left to add value to, so the share went
 * straight to cost of sales and that line has no stock move — the alternative, holding the
 * cost back until the goods come back, would mean holding it for ever.
 *
 * **Reverse** takes the allocation out at the values it went in at, never at today's average.
 * Under the `block` negative-stock policy a reversal that would strip value off a location the
 * goods have since left is refused, and that refusal arrives in this dialog rather than as a
 * toast, because it is an answer to what was just asked.
 */
export function LandedCostScreen({ documentId }: { documentId: number }) {
  const t = useTranslations("orderEntry.landedCost");
  const tc = useTranslations("orderEntry.common");
  const ts = useTranslations("orderEntry.landedCostStatus");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canPost = useHasPermission()("oe:landed_cost_post");

  const document = useLandedCost(documentId);
  const support = useInventoryLineSupport({ includeInactiveItems: true, includeInTransitWarehouses: true });
  const currencies = useCurrencies();
  const reverse = useReverseLandedCost();

  const [reversing, setReversing] = useState(false);
  const [onDate, setOnDate] = useState(todayIso);
  const [reason, setReason] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState(newDraftId);
  const [banner, setBanner] = useState<string | null>(null);

  const data = document.data;
  const currency = useMemo(() => {
    const base = (currencies.data ?? []).find((c) => c.is_base);
    return {
      code: base?.code ?? "",
      decimalPlaces: base?.decimal_places ?? 0,
      symbol: base?.symbol ?? null,
    };
  }, [currencies.data]);

  if (!data) {
    return (
      <DocumentWorkspaceShell
        backHref="/oe/landed-costs"
        title={t("detailTitle")}
        footer={<span className="text-xs text-[var(--vinea-ink-subtle)]">{tc("loading")}</span>}
      >
        <p className="py-10 text-center text-xs text-[var(--vinea-ink-subtle)]">{tc("loading")}</p>
      </DocumentWorkspaceShell>
    );
  }

  const isReversed = data.status === LandedCostStatus.REVERSED;

  async function runReverse() {
    setBanner(null);
    try {
      await reverse.mutateAsync({
        documentId,
        payload: { on_date: onDate, reason },
        idempotencyKey,
      });
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
      backHref="/oe/landed-costs"
      title={data.number}
      subtitle={data.description}
      statusChip={
        <StatusChip tone={LANDED_COST_STATUS_TONE[data.status] ?? "neutral"}>
          {ts(data.status)}
        </StatusChip>
      }
      errorBanner={banner}
      footer={
        <div className="flex items-center justify-between gap-6">
          <span className="text-sm text-[var(--vinea-ink-muted)]">
            {t("amount")}{" "}
            <span
              className="font-mono tabular-nums text-[var(--vinea-ink)]"
              data-testid="landed-cost-amount"
            >
              {formatMoney(Number(data.amount), currency)}
            </span>
          </span>
          <div className="flex items-center gap-3">
            {isReversed ? (
              <p className="text-xs text-[var(--vinea-ink-subtle)]" data-testid="reverse-blocked">
                {t("alreadyReversed")}
              </p>
            ) : null}
            <Button
              variant="secondary"
              disabled={!canPost || isReversed}
              onClick={() => setReversing(true)}
              data-testid="reverse-landed-cost"
            >
              <Undo2 className="size-3.5" /> {tc("reverse")}
            </Button>
          </div>
        </div>
      }
    >
      <section className="grid grid-cols-2 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-4">
        {(
          [
            [tc("date"), formatDate(data.cost_date)],
            [t("basis"), t(`basis_${data.basis}`)],
            [t("reference"), data.reference ?? tc("emptyValue")],
            [tc("reversed"), data.reversed_on ? formatDate(data.reversed_on) : tc("emptyValue")],
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
            data-testid="landed-cost-entry-link"
          >
            {t("viewEntry")}
          </Link>
          {data.reversal_entry_id ? (
            <>
              <span className="px-2 text-[var(--vinea-ink-subtle)]" aria-hidden>
                {tc("separator")}
              </span>
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
          {t("shares")}
        </h2>
        <Table>
          <THead>
            <TR>
              <TH className="w-12">{t("lineNo")}</TH>
              <TH>{tc("item")}</TH>
              <TH className="w-32">{tc("warehouse")}</TH>
              <TH className="w-28 text-right">{t("weight")}</TH>
              <TH className="w-36 text-right">{t("share")}</TH>
              <TH className="w-32 text-right">{t("where")}</TH>
            </TR>
          </THead>
          <TBody>
            {data.lines.map((line) => {
              const item = support.itemById.get(line.item_id);
              return (
                <TR key={line.id}>
                  <TD className="font-mono text-xs text-[var(--vinea-ink-subtle)]">
                    {line.line_no}
                  </TD>
                  <TD className="text-xs font-medium text-[var(--vinea-ink)]">
                    {item ? dotted(item.code, item.name) : line.item_id}
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">
                    {support.warehouses.find((w) => w.id === line.warehouse_id)?.code ??
                      tc("emptyValue")}
                  </TD>
                  <TD className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink-muted)]">
                    {formatQuantity(Number(line.weight), 4)}
                  </TD>
                  <TD
                    className="text-right font-mono tabular-nums text-xs text-[var(--vinea-ink)]"
                    data-testid="landed-cost-share"
                  >
                    {formatMoney(Number(line.share), currency)}
                  </TD>
                  <TD className="text-right">
                    <StatusChip tone={line.went_to_cogs ? "warning" : "neutral"}>
                      {line.went_to_cogs ? t("toCogs") : t("toStock")}
                    </StatusChip>
                  </TD>
                </TR>
              );
            })}
          </TBody>
        </Table>
        <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("cogsNote")}</p>
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
                disabled={!reason || reverse.isPending}
                data-testid="reverse-confirm"
              >
                {reverse.isPending ? tc("saving") : t("reverseConfirm")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </DocumentWorkspaceShell>
  );
}
