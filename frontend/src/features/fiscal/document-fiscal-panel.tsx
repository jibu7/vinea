"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/design/components/button";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { ReportPanel } from "@/design/components/report-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { isApiError, useHasPermission } from "@/features/auth/hooks";
import { FiscalOutboxKind, FiscalOutboxStatus } from "@/lib/api-enums";
import { formatDate } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import {
  useAttachQueueReceipt,
  useFiscalQueueRows,
  useRetryQueueRow,
  useVerifyQueueRow,
} from "./hooks";
import type { QueueRow, ReceiptBlock } from "./types";
import { dashed } from "./receipt-layout";

/**
 * How a queue row reads as a chip. One map, used by the document detail and the queue screen,
 * because a row that is `danger` on one page and `warning` on the other is two screens
 * disagreeing about how much trouble a device is in.
 *
 * `unknown` and `needs_receipt` are **warnings, not failures**: RRA may be holding the sale,
 * and the thing to do is ask rather than to treat it as lost. `failed` is a danger because a
 * `9xx` refusal will not retry itself and the document has to be reversed and re-posted.
 */
export const QUEUE_STATUS_TONE: Record<string, "success" | "warning" | "danger" | "neutral"> = {
  [FiscalOutboxStatus.QUEUED]: "warning",
  [FiscalOutboxStatus.SENDING]: "warning",
  [FiscalOutboxStatus.SENT]: "success",
  [FiscalOutboxStatus.FAILED]: "danger",
  [FiscalOutboxStatus.UNKNOWN]: "warning",
  [FiscalOutboxStatus.NEEDS_RECEIPT]: "warning",
  [FiscalOutboxStatus.CANCELLED]: "neutral",
};

/** The six fields of a sales response, as MyRRA shows them. Spelled here rather than derived
 * because they are what a person is copying off a portal page, in that order. */
export const ATTACH_FIELDS = [
  "rcptNo",
  "totRcptNo",
  "intrlData",
  "rcptSign",
  "sdcId",
  "vsdcRcptPbctDate",
] as const;

/**
 * The fiscal half of a partner document: what the authority has been told, what it signed, and
 * the three things a person can do when it has not answered.
 *
 * **The chip is the row, not the document.** A posted invoice on a fiscalized company is a
 * fact in Vinea's ledger whatever RRA thinks of it; what is in doubt is the declaration, and
 * conflating the two is how an operator ends up believing a sale was never made because a
 * queue is stuck.
 *
 * The three actions are decision 4's, and their narrowness is the point: **Retry now** is
 * offered on `failed` and on a backing-off `queued` row and never on `unknown`, because a
 * retry on a row RRA may already hold is the duplicate the policy exists to prevent; **Verify
 * with device** is how an `unknown` is resolved, by asking the device what its counters say;
 * and **Attach receipt manually** is a person asserting what MyRRA shows, audited with their
 * note. The endpoints refuse the combinations this screen does not offer, so the buttons are
 * the rule said where it can be acted on rather than a second copy of it.
 */
export function DocumentFiscalPanel({
  documentId,
  receipt,
}: {
  documentId: number;
  receipt: ReceiptBlock | null;
}) {
  const t = useTranslations("fiscal.document");
  const tq = useTranslations("fiscal.queue");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canManage = useHasPermission()("fiscal:queue_manage");

  const rows = useFiscalQueueRows({ documentId });
  const retry = useRetryQueueRow();
  const verify = useVerifyQueueRow();
  const attach = useAttachQueueReceipt();

  const [attaching, setAttaching] = useState<QueueRow | null>(null);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [attachError, setAttachError] = useState<string | null>(null);

  const queueRows = rows.data ?? [];
  // The sale or the refund — the row that decides whether there is a receipt. Stock and item
  // rows sit in the same queue and are no part of what this document prints.
  const declaration = queueRows.find(
    (row) => row.kind === FiscalOutboxKind.SALE || row.kind === FiscalOutboxKind.REFUND,
  );

  if (queueRows.length === 0 && receipt === null) return null;

  async function act(
    run: () => Promise<unknown>,
    successKey: "retried" | "verified",
    failureKey: "retryFailed" | "verifyFailed",
  ) {
    try {
      await run();
      toast.show({ title: t(successKey), tone: "success" });
    } catch (err) {
      showApiError(err, t(failureKey));
    }
  }

  async function handleAttach() {
    if (!attaching) return;
    setAttachError(null);
    try {
      await attach.mutateAsync({
        rowId: attaching.row_id,
        payload: { fields, note: note.trim() },
      });
      toast.show({ title: t("attached"), tone: "success" });
      setAttaching(null);
      setFields({});
      setNote("");
    } catch (err) {
      if (isApiError(err)) setAttachError(err.message);
      else showApiError(err, t("attachFailed"));
    }
  }

  const canRetry =
    declaration !== undefined &&
    (declaration.status === FiscalOutboxStatus.FAILED ||
      declaration.status === FiscalOutboxStatus.QUEUED);
  const canVerify =
    declaration !== undefined && declaration.status === FiscalOutboxStatus.UNKNOWN;
  const canAttach =
    declaration !== undefined && declaration.status === FiscalOutboxStatus.NEEDS_RECEIPT;

  return (
    <ReportPanel>
      <div className="flex items-center justify-between pb-2">
        <h2 className="text-xs font-semibold text-[var(--vinea-ink-muted)]">{t("title")}</h2>
        {declaration && (
          <StatusChip tone={QUEUE_STATUS_TONE[declaration.status] ?? "neutral"}>
            <span data-testid="fiscal-status">{tq(`statusLabel.${declaration.status}`)}</span>
          </StatusChip>
        )}
      </div>

      {receipt ? (
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Fact label={t("receiptNumber")} testId="fiscal-receipt-number">
            {receipt.receipt_number}
          </Fact>
          <Fact label={t("invcNo")}>{String(receipt.invc_no)}</Fact>
          <Fact label={t("sdcId")}>{receipt.sdc_id}</Fact>
          <Fact label={t("sdcDateTime")}>{formatDate(receipt.sdc_datetime)}</Fact>
          <Fact label={t("internalData")} className="sm:col-span-2">
            {dashed(receipt.intrl_data)}
          </Fact>
          <Fact label={t("receiptSignature")} className="sm:col-span-2">
            {dashed(receipt.rcpt_sign)}
          </Fact>
          <Fact label={t("copyCount")} testId="fiscal-copy-count">
            {String(receipt.copy_count)}
          </Fact>
        </dl>
      ) : (
        <p className="py-2 text-xs text-[var(--vinea-ink-subtle)]">{t("noReceiptYet")}</p>
      )}

      {declaration?.last_error && (
        <p className="pt-2 text-xs text-[var(--vinea-danger)]" data-testid="fiscal-error">
          {declaration.last_result_cd
            ? t("lastErrorWithCode", {
                code: declaration.last_result_cd,
                message: declaration.last_error,
              })
            : declaration.last_error}
        </p>
      )}

      <div className="flex flex-wrap gap-2 pt-3 print:hidden">
        <Button
          variant="secondary"
          disabled={!canManage || !canRetry || retry.isPending}
          data-testid="fiscal-retry"
          onClick={() =>
            declaration && act(() => retry.mutateAsync(declaration.row_id), "retried", "retryFailed")
          }
        >
          {tq("retryNow")}
        </Button>
        <Button
          variant="secondary"
          disabled={!canManage || !canVerify || verify.isPending}
          data-testid="fiscal-verify"
          onClick={() =>
            declaration &&
            act(() => verify.mutateAsync(declaration.row_id), "verified", "verifyFailed")
          }
        >
          {tq("verify")}
        </Button>
        <Button
          variant="secondary"
          disabled={!canManage || !canAttach}
          data-testid="fiscal-attach"
          onClick={() => {
            setAttachError(null);
            setFields({});
            setNote("");
            setAttaching(declaration ?? null);
          }}
        >
          {tq("attachReceipt")}
        </Button>
      </div>

      {/* The whole queue history for this document, read-only — the item registration that had
          to go first, the sale, the stock report that followed it. Order is send order. */}
      {queueRows.length > 1 && (
        <div className="pt-4">
          <h3 className="pb-1 text-xs font-semibold text-[var(--vinea-ink-muted)]">
            {t("queueHistory")}
          </h3>
          <Table>
            <THead>
              <TR>
                <TH className="w-16">{tq("sequence")}</TH>
                <TH className="w-32">{tq("kind")}</TH>
                <TH className="w-32">{tq("status")}</TH>
                <TH className="w-24 text-right">{tq("attempts")}</TH>
                <TH>{tq("lastError")}</TH>
              </TR>
            </THead>
            <TBody>
              {queueRows.map((row) => (
                <TR key={row.row_id}>
                  <TD className="font-mono text-xs">{row.sequence_no}</TD>
                  <TD className="text-xs">{tq(`kindLabel.${row.kind}`)}</TD>
                  <TD>
                    <StatusChip tone={QUEUE_STATUS_TONE[row.status] ?? "neutral"}>
                      {tq(`statusLabel.${row.status}`)}
                    </StatusChip>
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums">{row.attempts}</TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">{row.last_error ?? ""}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </div>
      )}

      <Dialog open={attaching !== null} onOpenChange={(open) => !open && setAttaching(null)}>
        <DialogContent title={tq("attachTitle")} description={tq("attachDescription")}>
          {attachError && (
            <p
              data-testid="attach-error"
              className="mb-3 rounded-[var(--radius-control)] bg-[var(--vinea-danger-soft)] px-3 py-2 text-sm text-[var(--vinea-danger)]"
            >
              {attachError}
            </p>
          )}
          <div className="grid grid-cols-2 gap-3">
            {ATTACH_FIELDS.map((name) => (
              <Field key={name} label={tq(`attachField.${name}`)}>
                <Input
                  value={fields[name] ?? ""}
                  onChange={(e) => setFields((current) => ({ ...current, [name]: e.target.value }))}
                  data-testid={`attach-${name}`}
                  className="font-mono"
                />
              </Field>
            ))}
            <Field label={tq("attachNote")} className="col-span-2">
              <Input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                data-testid="attach-note"
                placeholder={tq("attachNotePlaceholder")}
              />
            </Field>
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setAttaching(null)}>
              {tq("cancel")}
            </Button>
            <Button
              variant="primary"
              disabled={note.trim().length === 0 || attach.isPending}
              data-testid="confirm-attach"
              onClick={handleAttach}
            >
              {tq("confirmAttach")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </ReportPanel>
  );
}

function Fact({
  label,
  children,
  className,
  testId,
}: {
  label: string;
  children: string;
  className?: string;
  testId?: string;
}) {
  return (
    <div className={className}>
      <dt className="text-xs text-[var(--vinea-ink-muted)]">{label}</dt>
      <dd className="break-all font-mono text-xs text-[var(--vinea-ink)]" data-testid={testId}>
        {children}
      </dd>
    </div>
  );
}
