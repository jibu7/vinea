"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Field, Input } from "@/design/components/input";
import { QueryState } from "@/design/components/query-state";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { Select } from "@/design/components/select";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useCompanyDetails } from "@/features/gl/hooks";
import { exportToCsv } from "@/lib/csv";
import { formatDate } from "@/lib/format";
import { QUEUE_STATUS_TONE } from "../document-fiscal-panel";
import { useFiscalQueueRow, useFiscalQueueRows, useFiscalReceipts } from "../hooks";

/**
 * Enquiries → Tax → **Fiscal queue history** (P7 step 8) — per document, and read-only.
 *
 * The Fiscal queue under Transactions is a **device** screen: what is stuck, and what has to
 * be done about it. This one answers the other question, which is somebody holding one invoice
 * and asking what has ever been sent for it. Those are different screens because they are
 * different jobs: the first is an operator clearing a blockage, the second is an accountant or
 * an auditor establishing a fact about one sale.
 *
 * **Read-only on purpose.** Retry, verify and attach stay on the queue screen, where the
 * device's state is visible beside them — attaching a receipt to a row without seeing whether
 * the device is blocked behind it is how somebody resolves the wrong row. The link out is
 * offered instead, which is the honest form of "not here".
 *
 * The action log is the audit trail rather than a second history table: retry, verify and
 * attach already write `audit_log` rows, and a row that recorded its own history would be a
 * second copy to keep honest.
 */
export function FiscalQueueHistoryEnquiry() {
  const t = useTranslations("fiscal.queueHistory");
  const tq = useTranslations("fiscal.queue");
  const tr = useTranslations("reports");

  const [search, setSearch] = useState("");
  const [documentId, setDocumentId] = useState("");

  const company = useCompanyDetails();
  /** The picker is fed by the receipts search, which is the same one box the Fiscal receipts
   * enquiry uses — a person looking up a document's history is holding the same paper. */
  const candidates = useFiscalReceipts({ search: search.trim() || undefined });
  const chosen = documentId ? Number(documentId) : null;
  const rows = useFiscalQueueRows({ documentId: chosen, watch: false });
  const queueRows = chosen === null ? [] : (rows.data ?? []);

  const [openRow, setOpenRow] = useState<number | null>(null);
  const detail = useFiscalQueueRow(openRow);

  const options = [
    { value: "", label: t("noDocument") },
    ...Array.from(
      new Map(
        (candidates.data ?? []).map((row) => [
          row.document_id,
          {
            value: String(row.document_id),
            label: t("documentLabel", {
              number: row.document_number,
              partner: row.partner_name,
            }),
          },
        ]),
      ).values(),
    ),
  ];

  function handleExport() {
    exportToCsv(
      `fiscal-queue-history-${documentId}`,
      [t("sequence"), t("kind"), t("rowStatus"), t("attempts"), t("queuedAt"), t("lastError")],
      queueRows.map((row) => [
        row.sequence_no,
        row.kind,
        row.status,
        row.attempts,
        row.created_at ?? "",
        row.last_error ?? "",
      ]),
    );
  }

  return (
    <ReportPage
      title={t("title")}
      subtitle={t("subtitle")}
      companyName={company.data?.name}
      onExportCsv={queueRows.length > 0 ? handleExport : undefined}
      filters={
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-2">
          <Field label={t("pickDocumentHint")}>
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t("pickDocumentHint")}
              aria-label={t("pickDocumentHint")}
            />
          </Field>
          <Field label={t("pickDocument")}>
            <Select
              options={options}
              value={documentId}
              onValueChange={(value) => {
                setDocumentId(value);
                setOpenRow(null);
              }}
              ariaLabel={t("pickDocument")}
            />
          </Field>
        </div>
      }
    >
      <ReportPanel>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("readOnly")}</p>
          <Link
            href="/fiscal/queue"
            className="text-xs font-medium text-[var(--vinea-brand)] hover:underline print:hidden"
          >
            {t("openQueue")}
          </Link>
        </div>
      </ReportPanel>

      <ReportPanel>
        <h2 className="pb-2 text-sm font-semibold text-[var(--vinea-ink)]">{t("rows")}</h2>
        {queueRows.length === 0 ? (
          <QueryState
            query={rows}
            isEmpty
            empty={chosen === null ? t("noDocument") : t("noRows")}
            testId="query"
          />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-16 text-right">{t("sequence")}</TH>
                <TH className="w-32">{t("kind")}</TH>
                <TH className="w-36">{t("rowStatus")}</TH>
                <TH className="w-20 text-right">{t("attempts")}</TH>
                <TH className="w-28">{t("queuedAt")}</TH>
                <TH>{t("lastError")}</TH>
                <TH className="w-20 print:hidden" />
              </TR>
            </THead>
            <TBody>
              {queueRows.map((row) => (
                <TR key={row.row_id}>
                  <TD
                    className="text-right font-mono text-xs tabular-nums"
                    data-testid="history-sequence"
                  >
                    {row.sequence_no}
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink)]">{tq(`kindLabel.${row.kind}`)}</TD>
                  <TD>
                    <StatusChip tone={QUEUE_STATUS_TONE[row.status] ?? "neutral"}>
                      <span data-testid="history-status">{tq(`statusLabel.${row.status}`)}</span>
                    </StatusChip>
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                    {row.attempts}
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">
                    {row.created_at ? formatDate(row.created_at) : tr("noRows")}
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">{row.last_error ?? ""}</TD>
                  <TD className="print:hidden">
                    <button
                      type="button"
                      onClick={() => setOpenRow(row.row_id === openRow ? null : row.row_id)}
                      data-testid="history-inspect"
                      className="text-xs font-medium text-[var(--vinea-brand)] hover:underline"
                    >
                      {tq("inspect")}
                    </button>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </ReportPanel>

      {openRow !== null && (
        <ReportPanel>
          <h2 className="pb-2 text-sm font-semibold text-[var(--vinea-ink)]">{t("actionLog")}</h2>
          {(detail.data?.actions ?? []).length === 0 ? (
            <QueryState query={detail} isEmpty empty={t("noActions")} testId="actions" />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH className="w-40">{t("action")}</TH>
                  <TH className="w-48">{t("actor")}</TH>
                  <TH className="w-32">{t("actedAt")}</TH>
                  <TH>{t("note")}</TH>
                </TR>
              </THead>
              <TBody>
                {(detail.data?.actions ?? []).map((action, index) => (
                  <TR key={`${action.at}-${index}`}>
                    <TD className="font-mono text-xs text-[var(--vinea-ink)]">{action.action}</TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {action.actor_email ?? ""}
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {formatDate(action.at)}
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {JSON.stringify(action.detail)}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </ReportPanel>
      )}
    </ReportPage>
  );
}
