"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ExternalLink } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { QueryState } from "@/design/components/query-state";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { isApiError, useHasPermission } from "@/features/auth/hooks";
import { useCompanyDetails } from "@/features/gl/hooks";
import { FiscalOutboxStatus } from "@/lib/api-enums";
import { dotted, formatDate } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { ATTACH_FIELDS, QUEUE_STATUS_TONE } from "./document-fiscal-panel";
import {
  useAttachQueueReceipt,
  useFiscalQueue,
  useFiscalQueueRow,
  useFiscalQueueRows,
  useRetryQueueRow,
  useVerifyQueueRow,
} from "./hooks";
import type { QueueRow } from "./types";

/**
 * **Fiscal queue** — Transactions → Tax.
 *
 * The outbox as an operator sees it: a card per device with what it is holding, the rows in
 * send order, and the three acts a person can perform on a row that will not move by itself.
 *
 * **Why the device cards come first.** The queue is per-device FIFO with one row in flight
 * (decision 4): a `failed`, `unknown` or `needs_receipt` row *blocks everything behind it*,
 * because the stock report must follow the sale that caused it and receipt counters are a
 * sequence. So the question a person opens this screen with is not "which rows are stuck" but
 * "which device has stopped", and the head row — the one everything else is waiting on — is
 * named on the card rather than left to be found in a list of two hundred.
 *
 * **`offline`** is VSDC §2.2 item 4: a device whose oldest unsent row is over 24 hours old.
 * The authority's own VSDC stops issuing at that point, so it is a warning about the device
 * rather than about the queue.
 *
 * The three actions are deliberately narrow, and the narrowness is the policy:
 *
 * - **Retry now** releases a `failed` row or a `queued` one that is backing off. It is never
 *   offered on `unknown`, because RRA may already hold that sale and a resend is the duplicate
 *   (`994`, which returns no receipt data) the policy exists to prevent.
 * - **Verify with device** is how an `unknown` is resolved: re-initialize, read
 *   `lastSaleInvcNo`, and let the counter decide — below ours means RRA never saw it, at or
 *   above means it did.
 * - **Attach receipt manually** is a person keying what MyRRA shows against a row that needs
 *   one, audited with their note. It is a human assertion about what a revenue authority is
 *   holding, which is why it carries a note and why the note is required.
 */
export function FiscalQueueScreen() {
  const t = useTranslations("fiscal.queue");
  const tc = useTranslations("fiscal.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canManage = useHasPermission()("fiscal:queue_manage");

  const [deviceId, setDeviceId] = useState("");
  const [status, setStatus] = useState("");
  const [openRowId, setOpenRowId] = useState<number | null>(null);
  const [attaching, setAttaching] = useState<QueueRow | null>(null);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [attachError, setAttachError] = useState<string | null>(null);

  const company = useCompanyDetails();
  const devices = useFiscalQueue(deviceId ? Number(deviceId) : null);
  const rows = useFiscalQueueRows({
    deviceId: deviceId ? Number(deviceId) : null,
    status: status || undefined,
  });
  const detail = useFiscalQueueRow(openRowId);
  const retry = useRetryQueueRow();
  const verify = useVerifyQueueRow();
  const attach = useAttachQueueReceipt();

  const deviceCards = devices.data ?? [];
  const queueRows = rows.data ?? [];
  const allDevices = useFiscalQueue(null);
  const deviceOptions = useMemo(
    () =>
      (allDevices.data ?? []).map((device) => ({
        value: String(device.device_id),
        label: dotted(device.branch_code, device.branch_name),
      })),
    [allDevices.data],
  );

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

  function canRetryRow(row: QueueRow): boolean {
    return (
      row.status === FiscalOutboxStatus.FAILED || row.status === FiscalOutboxStatus.QUEUED
    );
  }

  return (
    <ReportPage
      title={t("title")}
      subtitle={t("subtitle")}
      companyName={company.data?.name}
      filters={
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
          <Field label={t("device")}>
            <Combobox
              options={[{ value: "", label: t("allDevices") }, ...deviceOptions]}
              value={deviceId}
              onValueChange={setDeviceId}
              placeholder={t("allDevices")}
            />
          </Field>
          <Field label={tc("status")}>
            <Combobox
              options={[
                { value: "", label: t("allStatuses") },
                ...Object.values(FiscalOutboxStatus).map((value) => ({
                  value,
                  label: t(`statusLabel.${value}`),
                })),
              ]}
              value={status}
              onValueChange={setStatus}
              placeholder={t("allStatuses")}
            />
          </Field>
        </div>
      }
    >
      <section className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {deviceCards.map((device) => (
          <ReportPanel key={device.device_id}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-[var(--vinea-ink)]">
                  {dotted(device.branch_code, device.branch_name)}
                </p>
                <p className="font-mono text-[11px] text-[var(--vinea-ink-subtle)]">
                  {device.sdc_id ?? tc("emptyValue")}
                </p>
              </div>
              <div className="flex flex-col items-end gap-1">
                {device.offline && (
                  <StatusChip tone="danger">
                    <span data-testid={`device-offline-${device.device_id}`}>{t("offline")}</span>
                  </StatusChip>
                )}
                {device.blocked && <StatusChip tone="warning">{t("blocked")}</StatusChip>}
                {!device.offline && !device.blocked && (
                  <StatusChip tone="success">{t("flowing")}</StatusChip>
                )}
              </div>
            </div>

            <dl className="grid grid-cols-3 gap-2 pt-3 text-xs">
              <div>
                <dt className="text-[var(--vinea-ink-muted)]">{t("pendingRows")}</dt>
                <dd
                  className="font-mono tabular-nums text-[var(--vinea-ink)]"
                  data-testid={`pending-${device.device_id}`}
                >
                  {device.pending_rows}
                </dd>
              </div>
              <div>
                <dt className="text-[var(--vinea-ink-muted)]">{t("oldestQueued")}</dt>
                <dd className="font-mono tabular-nums text-[var(--vinea-ink)]">
                  {device.oldest_queued_age_seconds === null
                    ? tc("emptyValue")
                    : t("ageMinutes", {
                        value: Math.floor(device.oldest_queued_age_seconds / 60),
                      })}
                </dd>
              </div>
              <div>
                <dt className="text-[var(--vinea-ink-muted)]">{t("lastSuccess")}</dt>
                <dd className="text-[var(--vinea-ink)]">
                  {device.last_success_at ? formatDate(device.last_success_at) : tc("emptyValue")}
                </dd>
              </div>
            </dl>

            <div className="flex flex-wrap gap-1.5 pt-3">
              {device.counts.map((count) => (
                <StatusChip key={count.status} tone={QUEUE_STATUS_TONE[count.status] ?? "neutral"}>
                  {t("countChip", {
                    status: t(`statusLabel.${count.status}`),
                    rows: count.rows,
                  })}
                </StatusChip>
              ))}
            </div>

            {/* The row everything behind it is waiting on. Named on the card because "which
                row is blocking this device" is the question, and a list of two hundred rows is
                not where it gets answered. */}
            {device.head && (
              <p className="pt-3 text-xs text-[var(--vinea-ink-muted)]">
                {t("headRow", {
                  sequence: device.head.sequence_no,
                  kind: t(`kindLabel.${device.head.kind}`),
                  status: t(`statusLabel.${device.head.status}`),
                })}
              </p>
            )}
            {device.last_error && (
              <p className="pt-1 text-xs text-[var(--vinea-danger)]">{device.last_error}</p>
            )}
          </ReportPanel>
        ))}
      </section>

      <ReportPanel>
        <h2 className="pb-2 text-xs font-semibold text-[var(--vinea-ink-muted)]">{t("rows")}</h2>
        {queueRows.length === 0 ? (
          <QueryState query={rows} isEmpty empty={t("noRows")} testId="queue-rows" />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-16">{t("sequence")}</TH>
                <TH className="w-28">{t("kind")}</TH>
                <TH className="w-32">{t("document")}</TH>
                <TH>{t("partner")}</TH>
                <TH className="w-20 text-right">{t("invcNo")}</TH>
                <TH className="w-32">{t("status")}</TH>
                <TH className="w-20 text-right">{t("attempts")}</TH>
                <TH className="w-64 print:hidden" />
              </TR>
            </THead>
            <TBody>
              {queueRows.map((row) => (
                <TR key={row.row_id}>
                  <TD className="font-mono text-xs">{row.sequence_no}</TD>
                  <TD className="text-xs">{t(`kindLabel.${row.kind}`)}</TD>
                  <TD className="font-mono text-xs">
                    {row.document_id && row.document_number ? (
                      <Link
                        href={`/ar/documents/${row.document_id}`}
                        className="inline-flex items-center gap-1 font-semibold text-[var(--vinea-brand)] underline"
                      >
                        {row.document_number}
                        <ExternalLink className="size-3" />
                      </Link>
                    ) : (
                      (row.document_number ?? tc("emptyValue"))
                    )}
                  </TD>
                  <TD className="text-xs">{row.partner_name ?? tc("emptyValue")}</TD>
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {row.invc_no ?? row.sar_no ?? tc("emptyValue")}
                  </TD>
                  <TD>
                    <StatusChip tone={QUEUE_STATUS_TONE[row.status] ?? "neutral"}>
                      <span data-testid={`row-status-${row.row_id}`}>
                        {t(`statusLabel.${row.status}`)}
                      </span>
                    </StatusChip>
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums">{row.attempts}</TD>
                  <TD className="print:hidden">
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        data-testid={`inspect-${row.row_id}`}
                        onClick={() => setOpenRowId(row.row_id)}
                      >
                        {t("inspect")}
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={!canManage || !canRetryRow(row) || retry.isPending}
                        data-testid={`retry-${row.row_id}`}
                        onClick={() =>
                          act(() => retry.mutateAsync(row.row_id), "retried", "retryFailed")
                        }
                      >
                        {t("retryNow")}
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={
                          !canManage || row.status !== FiscalOutboxStatus.UNKNOWN || verify.isPending
                        }
                        data-testid={`verify-${row.row_id}`}
                        onClick={() =>
                          act(() => verify.mutateAsync(row.row_id), "verified", "verifyFailed")
                        }
                      >
                        {t("verify")}
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={!canManage || row.status !== FiscalOutboxStatus.NEEDS_RECEIPT}
                        data-testid={`attach-${row.row_id}`}
                        onClick={() => {
                          setAttachError(null);
                          setFields({});
                          setNote("");
                          setAttaching(row);
                        }}
                      >
                        {t("attachReceipt")}
                      </Button>
                    </div>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </ReportPanel>

      {/* What was sent, what came back, and who has touched it. Both payloads are redacted at
          enqueue and again on the way out, so this is not a place a device key can escape. */}
      <Dialog open={openRowId !== null} onOpenChange={(open) => !open && setOpenRowId(null)}>
        <DialogContent title={t("rowTitle")} description={t("rowDescription")}>
          {detail.data && (
            <div className="max-h-[60vh] space-y-3 overflow-auto">
              <section>
                <h3 className="pb-1 text-xs font-semibold text-[var(--vinea-ink-muted)]">
                  {t("request")}
                </h3>
                <pre
                  data-testid="row-request"
                  className="overflow-auto rounded-[var(--radius-control)] bg-[var(--vinea-surface-sunken)] p-2 text-[11px]"
                >
                  {JSON.stringify(detail.data.request, null, 2)}
                </pre>
              </section>
              <section>
                <h3 className="pb-1 text-xs font-semibold text-[var(--vinea-ink-muted)]">
                  {t("response")}
                </h3>
                <pre
                  data-testid="row-response"
                  className="overflow-auto rounded-[var(--radius-control)] bg-[var(--vinea-surface-sunken)] p-2 text-[11px]"
                >
                  {detail.data.response
                    ? JSON.stringify(detail.data.response, null, 2)
                    : t("noResponse")}
                </pre>
              </section>
              <section>
                <h3 className="pb-1 text-xs font-semibold text-[var(--vinea-ink-muted)]">
                  {t("actionLog")}
                </h3>
                {detail.data.actions.length === 0 ? (
                  <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("noActions")}</p>
                ) : (
                  <ul className="space-y-1 text-xs">
                    {detail.data.actions.map((entry, index) => (
                      <li key={index} className="text-[var(--vinea-ink-muted)]">
                        {t("actionLine", {
                          at: formatDate(entry.at),
                          action: entry.action,
                          actor: entry.actor_email ?? t("systemActor"),
                        })}
                      </li>
                    ))}
                  </ul>
                )}
                {detail.data.resolution_note && (
                  <p className="pt-2 text-xs text-[var(--vinea-ink)]">
                    {t("resolutionNote", { note: detail.data.resolution_note })}
                  </p>
                )}
              </section>
            </div>
          )}
          <div className="mt-4 flex justify-end">
            <Button
              variant="ghost"
              data-testid="close-row"
              onClick={() => setOpenRowId(null)}
            >
              {t("close")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={attaching !== null} onOpenChange={(open) => !open && setAttaching(null)}>
        <DialogContent title={t("attachTitle")} description={t("attachDescription")}>
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
              <Field key={name} label={t(`attachField.${name}`)}>
                <Input
                  value={fields[name] ?? ""}
                  onChange={(e) => setFields((current) => ({ ...current, [name]: e.target.value }))}
                  data-testid={`attach-${name}`}
                  className="font-mono"
                />
              </Field>
            ))}
            <Field label={t("attachNote")} className="col-span-2">
              <Input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                data-testid="attach-note"
                placeholder={t("attachNotePlaceholder")}
              />
            </Field>
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setAttaching(null)}>
              {t("cancel")}
            </Button>
            <Button
              variant="primary"
              disabled={note.trim().length === 0 || attach.isPending}
              data-testid="confirm-attach"
              onClick={handleAttach}
            >
              {t("confirmAttach")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </ReportPage>
  );
}
