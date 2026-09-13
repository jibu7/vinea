"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ExternalLink, Plus } from "lucide-react";
import { Button } from "@/design/components/button";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { MaintenancePage } from "@/design/components/maintenance-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { useHasPermission } from "@/features/auth/hooks";
import { StockTransferStatus } from "@/lib/api-enums";
import { newDraftId } from "@/lib/drafts";
import { dotted, formatDate, formatQuantity } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { useCancelTransfer, useReceiveTransfer, useTransfer, useTransfers } from "./hooks";
import { useInventoryLineSupport } from "./line-support";
import type { TransferSummary } from "./types";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

const STATUS_TONE: Record<string, "neutral" | "success" | "warning" | "danger" | "info"> = {
  [StockTransferStatus.IN_TRANSIT]: "info",
  [StockTransferStatus.COMPLETED]: "success",
  [StockTransferStatus.CANCELLED]: "neutral",
  [StockTransferStatus.REVERSED]: "danger",
};

const STATUS_KEY: Record<string, "statusDispatched" | "statusReceived" | "statusCancelled" | "statusReversed"> = {
  [StockTransferStatus.IN_TRANSIT]: "statusDispatched",
  [StockTransferStatus.COMPLETED]: "statusReceived",
  [StockTransferStatus.CANCELLED]: "statusCancelled",
  [StockTransferStatus.REVERSED]: "statusReversed",
};

/**
 * Warehouse transfers: the list, and the two things a person does to one after it is
 * dispatched — receive it at the destination, or cancel it while the stock is still in
 * transit. Both go through the transfer service with an idempotency key, the same way the
 * document was raised.
 *
 * A row opens its lines underneath rather than on a separate page: the quantity per item is
 * what the receiving clerk checks against the goods, and the two journal entries (dispatch
 * and receive) are linked from here because the value a transfer moved is on them, not on the
 * transfer — the arrival takes the dispatched value frozen.
 */
export function TransfersScreen() {
  const t = useTranslations("inventory.transfers");
  const tc = useTranslations("inventory.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const hasPermission = useHasPermission();
  const canPost = hasPermission("inv:transactions_adjust");

  const transfers = useTransfers();
  const support = useInventoryLineSupport();
  const receive = useReceiveTransfer();
  const cancel = useCancelTransfer();

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const selected = useTransfer(selectedId);
  const [receiving, setReceiving] = useState<TransferSummary | null>(null);
  const [receiveDate, setReceiveDate] = useState(today);
  const [cancelling, setCancelling] = useState<TransferSummary | null>(null);
  const [reason, setReason] = useState("");
  // One key per dialog opening: a retry of the same click replays, a fresh opening is new.
  const [actionKey, setActionKey] = useState<string>(newDraftId);

  const warehouseName = (id: number) => {
    const w = support.warehouses.find((x) => x.id === id);
    return w ? w.code : String(id);
  };

  async function handleReceive() {
    if (!receiving) return;
    try {
      await receive.mutateAsync({ transferId: receiving.id, receiveDate, idempotencyKey: actionKey });
      toast.show({ title: t("received", { number: receiving.number }), tone: "success" });
      setReceiving(null);
      setActionKey(newDraftId());
    } catch (err) {
      showApiError(err, t("receiveFailed"));
    }
  }

  async function handleCancel() {
    if (!cancelling || !reason.trim()) return;
    try {
      await cancel.mutateAsync({ transferId: cancelling.id, reason: reason.trim(), idempotencyKey: actionKey });
      toast.show({ title: t("cancelled", { number: cancelling.number }), tone: "success" });
      setCancelling(null);
      setReason("");
      setActionKey(newDraftId());
    } catch (err) {
      showApiError(err, t("cancelFailed"));
    }
  }

  const rows = transfers.data?.items ?? [];

  return (
    <MaintenancePage
      title={t("title")}
      description={t("subtitle")}
      actions={
        canPost ? (
          <Link href="/inventory/transfers/new">
            <Button variant="primary">
              <Plus className="size-4" /> {t("new")}
            </Button>
          </Link>
        ) : undefined
      }
    >
      <Table>
        <THead>
          <TR>
            <TH>{t("number")}</TH>
            <TH>{t("date")}</TH>
            <TH>{t("from")}</TH>
            <TH>{t("to")}</TH>
            <TH>{t("reference")}</TH>
            <TH>{t("status")}</TH>
            <TH>{t("entry")}</TH>
            <TH className="text-right">{t("actions")}</TH>
          </TR>
        </THead>
        <TBody>
          {rows.length === 0 && (
            <TR>
              <TD colSpan={8} className="text-[var(--vinea-ink-muted)]">
                {transfers.isLoading ? tc("loading") : t("empty")}
              </TD>
            </TR>
          )}
          {rows.map((row) => (
            <TR
              key={row.id}
              data-transfer={row.number}
              className={selectedId === row.id ? "bg-[var(--vinea-brand-soft)]/30" : undefined}
            >
              <TD>
                <button
                  type="button"
                  className="font-mono text-[var(--vinea-brand)] hover:underline"
                  onClick={() => setSelectedId(selectedId === row.id ? null : row.id)}
                  aria-label={t("openLabel", { number: row.number })}
                >
                  {row.number}
                </button>
              </TD>
              <TD>{formatDate(row.transfer_date)}</TD>
              <TD className="font-mono">{warehouseName(row.from_warehouse_id)}</TD>
              <TD className="font-mono">{warehouseName(row.to_warehouse_id)}</TD>
              <TD className="text-[var(--vinea-ink-muted)]">{row.reference ?? tc("emptyValue")}</TD>
              <TD>
                <StatusChip tone={STATUS_TONE[row.status] ?? "neutral"}>
                  {t(STATUS_KEY[row.status] ?? "statusDispatched")}
                </StatusChip>
              </TD>
              <TD>
                <span className="flex items-center gap-2">
                  {row.dispatch_entry_id !== null && (
                    <Link
                      href={`/gl/entries/${row.dispatch_entry_id}`}
                      className="inline-flex items-center gap-1 text-[var(--vinea-brand)] hover:underline"
                      aria-label={`${t("dispatchEntry")} ${row.number}`}
                    >
                      <ExternalLink className="size-3.5" /> {t("dispatchEntry")}
                    </Link>
                  )}
                  {row.receive_entry_id !== null && (
                    <Link
                      href={`/gl/entries/${row.receive_entry_id}`}
                      className="inline-flex items-center gap-1 text-[var(--vinea-brand)] hover:underline"
                      aria-label={`${t("receiveEntry")} ${row.number}`}
                    >
                      <ExternalLink className="size-3.5" /> {t("receiveEntry")}
                    </Link>
                  )}
                </span>
              </TD>
              <TD className="text-right">
                {canPost && row.status === StockTransferStatus.IN_TRANSIT && (
                  <span className="inline-flex gap-2">
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => {
                        setReceiveDate(today());
                        setReceiving(row);
                      }}
                      aria-label={t("receiveLabel", { number: row.number })}
                    >
                      {t("receive")}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setCancelling(row)}
                      aria-label={t("cancelLabel", { number: row.number })}
                    >
                      {t("cancelTransfer")}
                    </Button>
                  </span>
                )}
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>

      {selectedId !== null && selected.data && (
        <section
          className="mt-4 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4"
          data-testid="transfer-lines"
        >
          <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--vinea-ink-subtle)]">
            {dotted(t("lines"), selected.data.number)}
          </h2>
          <Table>
            <THead>
              <TR>
                <TH>{tc("code")}</TH>
                <TH>{tc("name")}</TH>
                <TH className="text-right">{t("footerQuantity")}</TH>
                <TH>{t("unit")}</TH>
              </TR>
            </THead>
            <TBody>
              {selected.data.lines.map((line) => {
                const item = support.itemById.get(line.item_id);
                const uom = support.uomById.get(line.uom_id);
                return (
                  <TR key={line.id}>
                    <TD className="font-mono">{item?.code ?? line.item_id}</TD>
                    <TD>{item?.name ?? ""}</TD>
                    <TD className="text-right font-mono tabular-nums">
                      {uom ? formatQuantity(Number(line.quantity), uom.decimal_places) : line.quantity}
                    </TD>
                    <TD className="font-mono">{uom?.code ?? ""}</TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
          <p className="mt-2 text-xs text-[var(--vinea-ink-subtle)]">{t("inTransitNote")}</p>
        </section>
      )}

      <Dialog open={receiving !== null} onOpenChange={(open) => !open && setReceiving(null)}>
        {receiving && (
          <DialogContent title={t("receiveTitle", { number: receiving.number })} description={t("inTransitNote")}>
            <div className="space-y-4">
              <Field label={t("receiveDate")}>
                <IsoDatePicker value={receiveDate} onValueChange={setReceiveDate} />
              </Field>
              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setReceiving(null)}>
                  {tc("cancel")}
                </Button>
                <Button variant="primary" onClick={handleReceive} disabled={receive.isPending}>
                  {receive.isPending ? t("receiving") : t("receive")}
                </Button>
              </div>
            </div>
          </DialogContent>
        )}
      </Dialog>

      <Dialog open={cancelling !== null} onOpenChange={(open) => !open && setCancelling(null)}>
        {cancelling && (
          <DialogContent title={t("cancelTitle", { number: cancelling.number })}>
            <div className="space-y-4">
              <Field label={t("cancelReason")}>
                <Input value={reason} onChange={(e) => setReason(e.target.value)} />
              </Field>
              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setCancelling(null)}>
                  {tc("close")}
                </Button>
                <Button variant="primary" onClick={handleCancel} disabled={cancel.isPending || !reason.trim()}>
                  {t("confirmCancel")}
                </Button>
              </div>
            </div>
          </DialogContent>
        )}
      </Dialog>
    </MaintenancePage>
  );
}

