"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { AlertTriangle, ExternalLink, RefreshCw } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { DocumentWorkspaceShell, useDocumentShortcuts } from "@/design/components/document-workspace";
import { Field, Input } from "@/design/components/input";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { isApiError, useHasPermission } from "@/features/auth/hooks";
import { useCurrencies } from "@/features/gl/hooks";
import { StockCountStatus } from "@/lib/api-enums";
import { cn } from "@/lib/cn";
import { newDraftId } from "@/lib/drafts";
import { formatDate, formatMoney, formatQuantity } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { COUNT_STATUS_KEY, COUNT_STATUS_TONE } from "./counts-screen";
import {
  useAddCountLine,
  useCancelCount,
  useCountPreview,
  useCountSession,
  useEnterCount,
  useProcessCount,
  useResnapshotCountLine,
  useStockDocument,
} from "./hooks";
import { useInventoryLineSupport } from "./line-support";
import type { CountLine } from "./types";

/**
 * One count session: the sheet, the variances, and Process.
 *
 * **Keyboard-first.** One row per item, the counted cell is the only input, Enter saves the
 * row and moves to the next, Tab does the same without the save being tied to it (blur saves
 * too). An uncounted row is visibly different — muted, and it says so — because the worst
 * count is the one where a blank was read as zero. Clearing a counted cell puts the line back
 * to uncounted; it does not count it at zero.
 *
 * **The preview is the server's.** The variance panel is `GET /counts/{id}/preview`, exactly
 * the lines and values Process will post, costed at the item's current average (decision 7).
 * Nothing here re-derives a value from a quantity and a cost; the panel is what the ledger
 * will say, read before it says it.
 *
 * **Stale lines.** A location that has been posted to since the sheet froze it is flagged
 * per line, Process is refused with `count_line_stale`, and the refusal lands on that line —
 * not in a banner the operator has to map back to a row.
 */
export function CountSheetScreen({ sessionId }: { sessionId: number }) {
  const t = useTranslations("inventory.counts");
  const tc = useTranslations("inventory.common");
  const router = useRouter();
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const hasPermission = useHasPermission();
  const canCount = hasPermission("inv:transactions_adjust");
  const canProcess = hasPermission("inv:count_process");

  const session = useCountSession(sessionId);
  const preview = useCountPreview(sessionId);
  const support = useInventoryLineSupport();
  const currencies = useCurrencies();
  const enterCount = useEnterCount();
  const addLine = useAddCountLine();
  const resnapshot = useResnapshotCountLine();
  const processCount = useProcessCount();
  const cancelCount = useCancelCount();
  const document = useStockDocument(session.data?.document_id ?? null);

  // Process is idempotent on this key for the life of the page: a retried click after a
  // dropped connection replays, a reload starts a fresh key against a session the server
  // will refuse to process twice anyway.
  const [processKey] = useState<string>(newDraftId);
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [lineErrors, setLineErrors] = useState<Record<number, string>>({});
  const [banner, setBanner] = useState<string | null>(null);
  const [addItemId, setAddItemId] = useState("");
  const [cancelOpen, setCancelOpen] = useState(false);
  const [reason, setReason] = useState("");
  const inputs = useRef<Map<number, HTMLInputElement>>(new Map());

  const baseCurrency = (currencies.data ?? []).find((c) => c.is_base);
  const baseLike = {
    code: baseCurrency?.code ?? "",
    decimalPlaces: baseCurrency?.decimal_places ?? 0,
    symbol: baseCurrency?.symbol ?? null,
  };

  const lines = useMemo(() => session.data?.lines ?? [], [session.data]);
  const counting = session.data?.status === StockCountStatus.COUNTING;
  const editable = counting && canCount;

  // The first uncounted cell takes focus when the sheet opens: the operator arrives with the
  // clipboard, not the mouse.
  const focused = useRef(false);
  useEffect(() => {
    if (focused.current || !editable || lines.length === 0) return;
    const first = lines.find((line) => line.counted_quantity === null) ?? lines[0];
    inputs.current.get(first.id)?.focus();
    focused.current = true;
  }, [lines, editable]);

  const uomOf = (line: CountLine) => support.uomById.get(line.uom_id);
  const decimalsOf = (line: CountLine) => uomOf(line)?.decimal_places ?? 0;

  function draftValue(line: CountLine): string {
    if (line.id in drafts) return drafts[line.id];
    return line.counted_quantity === null ? "" : formatQuantity(Number(line.counted_quantity), decimalsOf(line));
  }

  async function commit(line: CountLine) {
    if (!(line.id in drafts)) return;
    const raw = drafts[line.id].trim().replace(/,/g, "");
    const current = line.counted_quantity === null ? "" : String(Number(line.counted_quantity));
    if (raw === current || (raw !== "" && !Number.isFinite(Number(raw)))) {
      setDrafts(({ [line.id]: _dropped, ...rest }) => rest);
      return;
    }
    try {
      await enterCount.mutateAsync({
        sessionId,
        lineId: line.id,
        payload: { counted_quantity: raw === "" ? null : raw },
      });
      setDrafts(({ [line.id]: _dropped, ...rest }) => rest);
      setLineErrors(({ [line.id]: _dropped, ...rest }) => rest);
    } catch (err) {
      if (isApiError(err)) {
        setLineErrors((prev) => ({ ...prev, [line.id]: err.fieldErrors.counted_quantity?.[0] ?? err.message }));
      } else {
        showApiError(err, t("saveFailed"));
      }
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>, index: number, line: CountLine) {
    if (e.key === "Enter") {
      e.preventDefault();
      void commit(line);
      const next = lines[index + 1];
      if (next) inputs.current.get(next.id)?.focus();
    } else if (e.key === "ArrowDown" && lines[index + 1]) {
      e.preventDefault();
      inputs.current.get(lines[index + 1].id)?.focus();
    } else if (e.key === "ArrowUp" && lines[index - 1]) {
      e.preventDefault();
      inputs.current.get(lines[index - 1].id)?.focus();
    }
  }

  async function handleAdd() {
    if (!addItemId) return;
    try {
      const line = await addLine.mutateAsync({ sessionId, itemId: Number(addItemId) });
      toast.show({ title: t("itemAdded", { code: support.itemById.get(line.item_id)?.code ?? "" }), tone: "success" });
      setAddItemId("");
    } catch (err) {
      showApiError(err, t("saveFailed"));
    }
  }

  async function handleResnapshot(line: CountLine) {
    try {
      await resnapshot.mutateAsync({ sessionId, lineId: line.id });
      setLineErrors(({ [line.id]: _dropped, ...rest }) => rest);
      toast.show({ title: t("resnapshotted"), tone: "neutral" });
    } catch (err) {
      showApiError(err, t("saveFailed"));
    }
  }

  async function handleProcess() {
    if (!canProcessNow) return;
    setBanner(null);
    try {
      const result = await processCount.mutateAsync({ sessionId, idempotencyKey: processKey });
      toast.show({
        title: result.document ? t("processed", { number: result.document.number }) : t("processedNoDocument"),
        tone: "success",
      });
    } catch (err) {
      if (isApiError(err)) {
        // `count_line_stale` keys its refusal by line *id*: put it on the line.
        const perLine: Record<number, string> = {};
        for (const [key, messages] of Object.entries(err.fieldErrors)) {
          const match = /^lines\.(\d+)$/.exec(key);
          if (match) perLine[Number(match[1])] = messages[0];
        }
        setLineErrors((prev) => ({ ...prev, ...perLine }));
        setBanner(err.message);
      } else {
        showApiError(err, t("processFailed"));
      }
    }
  }

  async function handleCancel() {
    if (!reason.trim()) return;
    try {
      const cancelled = await cancelCount.mutateAsync({ sessionId, reason: reason.trim() });
      toast.show({ title: t("cancelled", { number: cancelled.number }), tone: "success" });
      setCancelOpen(false);
    } catch (err) {
      showApiError(err, t("cancelFailed"));
    }
  }

  const canProcessNow = Boolean(counting && canProcess && preview.data?.can_process && !processCount.isPending);
  useDocumentShortcuts({ onPost: handleProcess, onCancel: () => router.push("/inventory/counts"), canPost: canProcessNow });

  const onSheet = new Set(lines.map((line) => line.item_id));
  const addOptions = support.itemOptions().filter((o) => !onSheet.has(Number(o.value)));
  const warehouse = support.warehouses.find((w) => w.id === session.data?.warehouse_id);
  const counted = lines.filter((line) => line.counted_quantity !== null).length;
  const status = session.data?.status ?? StockCountStatus.COUNTING;

  return (
    <DocumentWorkspaceShell
      backHref="/inventory/counts"
      title={session.data ? t("sheetTitle", { number: session.data.number }) : t("title")}
      subtitle={
        session.data
          ? t("sheetSubtitle", { warehouse: warehouse?.code ?? "", date: formatDate(session.data.snapshot_at) })
          : undefined
      }
      statusChip={
        <StatusChip tone={COUNT_STATUS_TONE[status] ?? "neutral"}>{t(COUNT_STATUS_KEY[status] ?? "statusCounting")}</StatusChip>
      }
      errorBanner={banner}
      footer={
        <div className="flex items-center justify-between gap-6">
          <div className="flex items-center gap-6 text-sm">
            <span className="text-[var(--vinea-ink-muted)]" data-testid="counted-lines">
              {t("countedLines", { counted, total: lines.length })}
            </span>
            {preview.data && (
              <span className="text-[var(--vinea-ink-muted)]">
                {t("totalValue")}{" "}
                <span className="font-mono tabular-nums text-[var(--vinea-ink)]" data-testid="preview-total">
                  {formatMoney(Number(preview.data.total_value), baseLike)}
                </span>
              </span>
            )}
          </div>
          <div className="flex items-center gap-3">
            {counting && canCount && (
              <Button variant="ghost" onClick={() => setCancelOpen(true)}>
                {t("cancelSession")}
              </Button>
            )}
            {counting && (
              <Button variant="primary" disabled={!canProcessNow} onClick={handleProcess}>
                {processCount.isPending ? t("processing") : t("process")}
              </Button>
            )}
            {!counting && session.data?.document_id !== null && document.data?.journal_entry_id != null && (
              <Link href={`/gl/entries/${document.data.journal_entry_id}`}>
                <Button variant="secondary">
                  <ExternalLink className="size-3.5" /> {t("viewDocument")}
                </Button>
              </Link>
            )}
          </div>
        </div>
      }
    >
      {status === StockCountStatus.COMPLETED && (
        <p className="text-sm text-[var(--vinea-ink-muted)]">{t("completedNote")}</p>
      )}
      {status === StockCountStatus.CANCELLED && (
        <p className="text-sm text-[var(--vinea-ink-muted)]">{t("cancelledNote")}</p>
      )}

      <section className="space-y-2">
        <h2 className="text-xs font-medium uppercase tracking-wide text-[var(--vinea-ink-subtle)]">{t("lines")}</h2>
        <div className="overflow-x-auto rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)]">
          <Table>
            <THead>
              <TR>
                <TH>{t("item")}</TH>
                <TH className="text-right">{t("systemQuantity")}</TH>
                <TH className="text-right">{t("counted")}</TH>
                <TH>{t("uom")}</TH>
                <TH className="text-right">{t("variance")}</TH>
                <TH>{t("status")}</TH>
              </TR>
            </THead>
            <TBody>
              {lines.length === 0 && (
                <TR>
                  <TD colSpan={6} className="text-[var(--vinea-ink-muted)]">
                    {session.isLoading ? tc("loading") : t("emptyLines")}
                  </TD>
                </TR>
              )}
              {lines.map((line, index) => {
                const item = support.itemById.get(line.item_id);
                const uom = uomOf(line);
                const decimals = decimalsOf(line);
                const uncounted = line.counted_quantity === null;
                const error = lineErrors[line.id];
                return (
                  <TR
                    key={line.id}
                    data-count-line={item?.code ?? line.item_id}
                    data-counted={uncounted ? "false" : "true"}
                    className={cn(
                      uncounted && "bg-[var(--vinea-surface-sunken)] text-[var(--vinea-ink-muted)]",
                      error && "bg-[var(--vinea-danger-soft)]",
                    )}
                  >
                    <TD>
                      <span className="font-mono">{item?.code ?? line.item_id}</span>
                      <span className="ml-2 text-[var(--vinea-ink-muted)]">{item?.name ?? ""}</span>
                    </TD>
                    <TD className="text-right font-mono tabular-nums">
                      {formatQuantity(Number(line.system_quantity), decimals)}
                    </TD>
                    <TD className="w-36 text-right">
                      <input
                        ref={(node) => {
                          if (node) inputs.current.set(line.id, node);
                          else inputs.current.delete(line.id);
                        }}
                        value={draftValue(line)}
                        onChange={(e) => setDrafts((prev) => ({ ...prev, [line.id]: e.target.value }))}
                        onBlur={() => void commit(line)}
                        onKeyDown={(e) => onKeyDown(e, index, line)}
                        disabled={!editable}
                        inputMode="decimal"
                        aria-label={t("countedAria", { item: item?.code ?? String(line.item_id) })}
                        className={cn(
                          "h-8 w-full rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] bg-[var(--vinea-surface-raised)] px-2 text-right font-mono tabular-nums text-[var(--vinea-ink)] focus:border-[var(--vinea-brand)] disabled:opacity-60",
                          error && "border-[var(--vinea-danger)]",
                        )}
                        placeholder={t("uncounted")}
                      />
                      {error && <p className="mt-0.5 text-right text-xs text-[var(--vinea-danger)]">{error}</p>}
                    </TD>
                    <TD className="font-mono">{uom?.code ?? ""}</TD>
                    <TD className="text-right font-mono tabular-nums">
                      {line.variance === null ? tc("emptyValue") : formatQuantity(Number(line.variance), decimals)}
                    </TD>
                    <TD>
                      <span className="flex items-center gap-2">
                        {uncounted ? (
                          <StatusChip tone="neutral">{t("uncounted")}</StatusChip>
                        ) : (
                          <StatusChip tone="success">{t("counted")}</StatusChip>
                        )}
                        {line.stale && (
                          <>
                            <StatusChip tone="warning">
                              <AlertTriangle className="size-3" /> {t("stale")}
                            </StatusChip>
                            {editable && (
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => handleResnapshot(line)}
                                disabled={resnapshot.isPending}
                                title={t("staleHint")}
                              >
                                <RefreshCw className="size-3" /> {t("resnapshot")}
                              </Button>
                            )}
                          </>
                        )}
                      </span>
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        </div>
        {editable && (
          <div className="flex items-end gap-2">
            <Field label={t("addItem")} className="w-96">
              <Combobox
                options={addOptions}
                value={addItemId}
                onValueChange={setAddItemId}
                placeholder={t("addItemPlaceholder")}
              />
            </Field>
            <Button variant="secondary" onClick={handleAdd} disabled={!addItemId || addLine.isPending}>
              {t("addLine")}
            </Button>
          </div>
        )}
      </section>

      <section className="space-y-2" data-testid="count-preview">
        <div className="flex items-baseline justify-between">
          <h2 className="text-xs font-medium uppercase tracking-wide text-[var(--vinea-ink-subtle)]">
            {t("reviewTitle")}
          </h2>
          <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("reviewSubtitle")}</p>
        </div>
        {preview.isError && <p className="text-sm text-[var(--vinea-danger)]">{t("previewFailed")}</p>}
        {preview.data && preview.data.stale_lines.length > 0 && (
          <p className="text-sm text-[var(--vinea-warning)]">{t("previewStale")}</p>
        )}
        <div className="overflow-x-auto rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)]">
          <Table>
            <THead>
              <TR>
                <TH>{t("item")}</TH>
                <TH className="text-right">{t("systemQuantity")}</TH>
                <TH className="text-right">{t("counted")}</TH>
                <TH className="text-right">{t("variance")}</TH>
                <TH className="text-right">{t("unitCost")}</TH>
                <TH className="text-right">{t("value")}</TH>
              </TR>
            </THead>
            <TBody>
              {(preview.data?.lines ?? []).filter((line) => line.counted && line.variance !== null && Number(line.variance) !== 0).length === 0 && (
                <TR>
                  <TD colSpan={6} className="text-[var(--vinea-ink-muted)]">
                    {preview.isLoading ? tc("loading") : t("previewEmpty")}
                  </TD>
                </TR>
              )}
              {(preview.data?.lines ?? [])
                .filter((line) => line.counted && line.variance !== null && Number(line.variance) !== 0)
                .map((line) => {
                  const decimals = support.uomById.get(support.itemById.get(line.item_id)?.base_uom_id ?? -1)?.decimal_places ?? 0;
                  return (
                    <TR key={line.line_id} data-preview-line={line.item_code}>
                      <TD>
                        <span className="font-mono">{line.item_code}</span>
                        <span className="ml-2 text-[var(--vinea-ink-muted)]">{line.item_name}</span>
                      </TD>
                      <TD className="text-right font-mono tabular-nums">{formatQuantity(Number(line.system_quantity), decimals)}</TD>
                      <TD className="text-right font-mono tabular-nums">
                        {line.counted_quantity === null ? "" : formatQuantity(Number(line.counted_quantity), decimals)}
                      </TD>
                      <TD className="text-right font-mono tabular-nums">{formatQuantity(Number(line.variance), decimals)}</TD>
                      <TD className="text-right font-mono tabular-nums">{formatMoney(Number(line.unit_cost), baseLike)}</TD>
                      <TD className="text-right font-mono tabular-nums">{formatMoney(Number(line.value), baseLike)}</TD>
                    </TR>
                  );
                })}
            </TBody>
          </Table>
        </div>
      </section>

      <Dialog open={cancelOpen} onOpenChange={setCancelOpen}>
        <DialogContent title={t("cancelTitle", { number: session.data?.number ?? "" })}>
          <div className="space-y-4">
            <Field label={t("cancelReason")}>
              <Input value={reason} onChange={(e) => setReason(e.target.value)} />
            </Field>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setCancelOpen(false)}>
                {tc("close")}
              </Button>
              <Button variant="primary" onClick={handleCancel} disabled={!reason.trim() || cancelCount.isPending}>
                {t("confirmCancel")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </DocumentWorkspaceShell>
  );
}
