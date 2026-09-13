"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import { Combobox } from "@/design/components/combobox";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Field } from "@/design/components/input";
import {
  ReportPage,
  ReportPager,
  ReportPanel,
  useCursorPager,
} from "@/design/components/report-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useCompanyDetails } from "@/features/gl/hooks";
import { StockCountStatus } from "@/lib/api-enums";
import { exportToCsv } from "@/lib/csv";
import { formatDate } from "@/lib/format";
import { useCountReport } from "../hooks";
import { COUNT_STATUS_KEY, COUNT_STATUS_TONE } from "../counts-screen";
import { useInventoryLineSupport } from "../line-support";

/**
 * Count report over `GET /inventory/reports/counts` — the sessions, their lines, and the
 * document each processed session posted.
 *
 * A session's lines are what the report is for, so they expand in place rather than opening
 * a drawer: the question is "which lines were out, and by how much", and comparing two
 * sessions means having both open at once.
 *
 * The **stale** chip belongs to an open session and only to an open session. Staleness means
 * "Process would refuse this line" (decision 7), and Process refuses a completed session
 * outright — so a Completed row never carries the chip. Building this screen is what surfaced
 * that: the rule was measured without regard to session status, and since processing posts a
 * move for every non-zero variance, the count's *own* posting sat above the snapshot and every
 * line it had corrected read back as stale forever. The report flagged exactly the lines that
 * worked. Fixed in `counts.stale_lines`, not here — a screen that hid a wrong field would have
 * left the preview and the count sheet showing it.
 */
export function CountReport() {
  const t = useTranslations("inventory.reports");
  const tc = useTranslations("inventory.counts");
  const [status, setStatus] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [variancesOnly, setVariancesOnly] = useState(false);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const support = useInventoryLineSupport({ includeInactiveItems: true });
  const company = useCompanyDetails();

  const pager = useCursorPager();
  const report = useCountReport({
    status: status || undefined,
    warehouseId: warehouseId ? Number(warehouseId) : undefined,
    dateFrom: dateFrom || undefined,
    dateTo: dateTo || undefined,
    variancesOnly,
    cursor: pager.cursor,
  });
  const rows = report.data?.rows ?? [];

  function toggle(sessionId: number) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(sessionId)) next.delete(sessionId);
      else next.add(sessionId);
      return next;
    });
  }

  const statusOptions = [
    { value: "", label: t("allStatuses") },
    ...Object.values(StockCountStatus).map((value) => ({
      value,
      label: tc(COUNT_STATUS_KEY[value]),
    })),
  ];

  function handleExport() {
    exportToCsv(
      `inventory-counts-${dateFrom || "all"}-${dateTo || "all"}`,
      [
        t("session"),
        t("countDate"),
        t("warehouse"),
        t("status"),
        t("code"),
        t("name"),
        t("systemQuantity"),
        t("countedQuantity"),
        t("variance"),
        t("stale"),
        t("document"),
      ],
      // One CSV row per *line*, not per session: a spreadsheet of sessions with a lines
      // column in it is not something anyone can sort or total.
      rows.flatMap((session) =>
        session.lines.map((line) => [
          session.number,
          session.count_date,
          session.warehouse_code,
          tc(COUNT_STATUS_KEY[session.status] ?? "statusCounting"),
          line.item_code,
          line.item_name,
          line.system_quantity,
          line.counted_quantity ?? "",
          line.variance ?? "",
          line.stale ? t("yes") : t("no"),
          session.document_number ?? "",
        ]),
      ),
    );
  }

  return (
    <ReportPage
      title={t("countTitle")}
      subtitle={t("countSubtitle")}
      companyName={company.data?.name}
      asOfLabel={
        dateFrom && dateTo
          ? t("dateRange", { from: formatDate(dateFrom), to: formatDate(dateTo) })
          : undefined
      }
      onExportCsv={rows.length ? handleExport : undefined}
      filters={
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-4">
          <Field label={t("from")}>
            <IsoDatePicker value={dateFrom} onValueChange={pager.filter(setDateFrom)} />
          </Field>
          <Field label={t("to")}>
            <IsoDatePicker value={dateTo} onValueChange={pager.filter(setDateTo)} />
          </Field>
          <Field label={t("warehouse")}>
            <Combobox
              options={[{ value: "", label: t("allWarehouses") }, ...support.warehouseOptions]}
              value={warehouseId}
              onValueChange={pager.filter(setWarehouseId)}
              placeholder={t("allWarehouses")}
            />
          </Field>
          <Field label={t("status")}>
            <Combobox
              options={statusOptions}
              value={status}
              onValueChange={pager.filter(setStatus)}
              placeholder={t("allStatuses")}
            />
          </Field>
          <label className="flex items-center gap-2 text-xs text-[var(--vinea-ink-muted)] sm:col-span-4">
            <input
              type="checkbox"
              checked={variancesOnly}
              onChange={(e) => pager.filter(setVariancesOnly)(e.target.checked)}
              className="size-3.5"
            />
            {t("variancesOnly")}
          </label>
        </div>
      }
    >
      <ReportPanel>
        {rows.length === 0 ? (
          <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {report.isLoading ? t("loading") : t("noRows")}
          </p>
        ) : (
          <>
            <Table>
              <THead>
                <TR>
                  <TH className="w-32">{t("session")}</TH>
                  <TH className="w-24">{t("countDate")}</TH>
                  <TH className="w-20">{t("warehouse")}</TH>
                  <TH>{t("description")}</TH>
                  <TH className="w-28">{t("status")}</TH>
                  <TH className="w-20 text-right">{t("lines")}</TH>
                  <TH className="w-24 text-right">{t("variances")}</TH>
                  <TH className="w-32">{t("document")}</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((session) => {
                  const open = expanded.has(session.session_id);
                  return [
                    <TR key={session.session_id}>
                      <TD>
                        <button
                          type="button"
                          onClick={() => toggle(session.session_id)}
                          aria-expanded={open}
                          aria-label={t("showLines", { number: session.number })}
                          className="inline-flex items-center gap-1 font-mono text-xs font-semibold text-[var(--vinea-brand)] underline"
                        >
                          {open ? (
                            <ChevronDown className="size-3" />
                          ) : (
                            <ChevronRight className="size-3" />
                          )}
                          {session.number}
                        </button>
                      </TD>
                      <TD className="text-xs text-[var(--vinea-ink-muted)]">
                        {formatDate(session.count_date)}
                      </TD>
                      <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                        {session.warehouse_code}
                      </TD>
                      <TD className="text-xs">{session.description}</TD>
                      <TD>
                        <StatusChip tone={COUNT_STATUS_TONE[session.status] ?? "neutral"}>
                          {tc(COUNT_STATUS_KEY[session.status] ?? "statusCounting")}
                        </StatusChip>
                      </TD>
                      <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                        {t("countedOfLines", {
                          counted: session.counted_count,
                          lines: session.line_count,
                        })}
                      </TD>
                      <TD
                        data-testid="count-variances"
                        className="text-right font-mono text-xs tabular-nums"
                      >
                        {session.variance_count}
                      </TD>
                      <TD>
                        {/* The link a processed session exists to leave: the variance
                            document, and through it the entry that moved the stock. */}
                        {session.journal_entry_id === null ? (
                          <span className="text-xs text-[var(--vinea-ink-subtle)]">
                            {t("emptyValue")}
                          </span>
                        ) : (
                          <Link
                            href={`/gl/entries/${session.journal_entry_id}`}
                            className="inline-flex items-center gap-1 font-mono text-xs font-semibold text-[var(--vinea-brand)] underline"
                          >
                            {session.document_number ?? session.journal_entry_id}
                            <ExternalLink className="size-3" />
                          </Link>
                        )}
                      </TD>
                    </TR>,
                    open && (
                      <TR key={`${session.session_id}-lines`}>
                        <TD colSpan={8} className="bg-[var(--vinea-surface-sunken)] p-0">
                          {session.lines.length === 0 ? (
                            <p className="py-4 text-center text-xs text-[var(--vinea-ink-subtle)]">
                              {t("noLines")}
                            </p>
                          ) : (
                            <Table>
                              <THead>
                                <TR>
                                  <TH className="w-28">{t("code")}</TH>
                                  <TH>{t("name")}</TH>
                                  <TH className="text-right">{t("systemQuantity")}</TH>
                                  <TH className="text-right">{t("countedQuantity")}</TH>
                                  <TH className="text-right">{t("variance")}</TH>
                                </TR>
                              </THead>
                              <TBody>
                                {session.lines.map((line) => (
                                  // A line row lives inside the session row that expands it,
                                  // so `tbody tr` matches the container too — the testid is
                                  // what lets a test name the line and not its wrapper.
                                  <TR key={line.line_id} data-testid="count-line">
                                    <TD className="font-mono text-xs text-[var(--vinea-brand)]">
                                      {line.item_code}
                                    </TD>
                                    <TD className="text-xs">
                                      {line.item_name}
                                      {line.stale && (
                                        <StatusChip tone="warning" className="ml-2">
                                          {t("stale")}
                                        </StatusChip>
                                      )}
                                    </TD>
                                    <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                                      {support.formatBaseWithUnit(
                                        line.item_id,
                                        line.system_quantity,
                                      )}
                                    </TD>
                                    <TD className="text-right font-mono text-xs tabular-nums">
                                      {line.counted_quantity === null
                                        ? t("uncounted")
                                        : support.formatBaseWithUnit(
                                            line.item_id,
                                            line.counted_quantity,
                                          )}
                                    </TD>
                                    <TD
                                      data-testid="count-line-variance"
                                      className="text-right font-mono text-xs font-semibold tabular-nums"
                                    >
                                      {line.variance === null
                                        ? t("emptyValue")
                                        : support.formatBaseWithUnit(line.item_id, line.variance)}
                                    </TD>
                                  </TR>
                                ))}
                              </TBody>
                            </Table>
                          )}
                        </TD>
                      </TR>
                    ),
                  ];
                })}
              </TBody>
            </Table>
            <ReportPager
              count={rows.length}
              hasPrevious={pager.hasPrevious}
              hasNext={(report.data?.next_cursor ?? null) !== null}
              onPrevious={pager.previous}
              onNext={() => pager.next(report.data?.next_cursor ?? null)}
            />
          </>
        )}
      </ReportPanel>
    </ReportPage>
  );
}
