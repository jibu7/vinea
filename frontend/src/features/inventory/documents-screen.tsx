"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ExternalLink } from "lucide-react";
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
import { useCompanyDetails, useCurrencies } from "@/features/gl/hooks";
import { InventoryDocumentStatus } from "@/lib/api-enums";
import { exportToCsv } from "@/lib/csv";
import { dotted, formatDate, formatMoney } from "@/lib/format";
import { useStockDocuments } from "./hooks";
import { useInventoryLineSupport } from "./line-support";

/** The document kinds this screen lists, by their `document_sequences` doc type. */
const DOC_TYPES = ["INAJ", "INJN", "INCT"] as const;

export const DOCUMENT_STATUS_TONE: Record<string, "neutral" | "success" | "warning" | "danger"> = {
  [InventoryDocumentStatus.POSTED]: "success",
  [InventoryDocumentStatus.REVERSED]: "neutral",
};

/** The catalogue already had these two labels for the capture screens; this is the map from
 * the wire value, so neither the label nor the enum is written out twice. */
export const DOCUMENT_STATUS_KEY: Record<string, "statusPosted" | "statusReversed"> = {
  [InventoryDocumentStatus.POSTED]: "statusPosted",
  [InventoryDocumentStatus.REVERSED]: "statusReversed",
};

/**
 * Every posted stock document — adjustments, journal batches and the documents processed
 * counts post — with the way to reverse one.
 *
 * **Why this screen exists.** Decision 11 gives every stock document a reversal, and until P5
 * step 9 nothing called it: `useReverseStockDocument` sat in the hooks unused, and the only
 * reversal button in the product was the general ledger's, which undoes the ledger half and
 * leaves the stock where it was. That button is now correctly refused for a module-owned
 * entry, so without this screen the product could post an adjustment and never undo it — the
 * same hole P4 found when `mature_instruments` shipped as an endpoint with no screen.
 *
 * It also gives the two things that had nowhere to land a home: a **valueless** document,
 * which has no journal entry to navigate to (a free-sample receipt posts moves and no ledger
 * side), and the item enquiry's source-document drill.
 */
export function InventoryDocumentsScreen() {
  const t = useTranslations("inventory.documents");
  const tr = useTranslations("inventory.reports");
  const tKind = useTranslations("inventory.documents.kinds");
  /** A doc type the catalogue does not name reads back as its own code rather than blank —
   * a new sequence added in P6 must not render an empty cell here. */
  const tk = (docType: string) => (tKind.has(docType) ? tKind(docType) : docType);
  const [docType, setDocType] = useState("");
  const [status, setStatus] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const support = useInventoryLineSupport({ includeInTransitWarehouses: true });
  const company = useCompanyDetails();
  const currencies = useCurrencies();
  const base = (currencies.data ?? []).find((c) => c.is_base);
  const baseLike = {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };

  const pager = useCursorPager();
  const page = useStockDocuments({
    docType: docType || undefined,
    status: status || undefined,
    dateFrom: dateFrom || undefined,
    dateTo: dateTo || undefined,
    cursor: pager.cursor,
  });
  const rows = page.data?.items ?? [];

  const warehouseCode = (id: number | null) =>
    id === null ? t("severalWarehouses") : (support.warehouses.find((w) => w.id === id)?.code ?? String(id));
  const typeCode = (id: number | null) =>
    id === null ? t("severalTypes") : (support.typeById.get(id)?.code ?? String(id));

  function handleExport() {
    exportToCsv(
      `inventory-documents-${dateFrom || "all"}-${dateTo || "all"}`,
      [
        t("number"),
        tr("date"),
        t("kind"),
        tr("warehouse"),
        tr("transactionType"),
        tr("description"),
        tr("value"),
        tr("status"),
        tr("entry"),
      ],
      rows.map((row) => [
        row.number,
        row.document_date,
        tk(row.doc_type),
        warehouseCode(row.warehouse_id),
        typeCode(row.transaction_type_id),
        row.description,
        row.total_value,
        row.status,
        row.journal_entry_id ?? "",
      ]),
    );
  }

  return (
    <ReportPage
      title={t("title")}
      subtitle={t("subtitle")}
      companyName={company.data?.name}
      onExportCsv={rows.length ? handleExport : undefined}
      filters={
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-4">
          <Field label={t("kind")}>
            <Combobox
              options={[
                { value: "", label: t("allKinds") },
                ...DOC_TYPES.map((code) => ({ value: code, label: tk(code) })),
              ]}
              value={docType}
              onValueChange={pager.filter(setDocType)}
              placeholder={t("allKinds")}
            />
          </Field>
          <Field label={tr("status")}>
            <Combobox
              options={[
                { value: "", label: tr("allStatuses") },
                ...Object.values(InventoryDocumentStatus).map((value) => ({
                  value,
                  label: t(DOCUMENT_STATUS_KEY[value] ?? "statusPosted"),
                })),
              ]}
              value={status}
              onValueChange={pager.filter(setStatus)}
              placeholder={tr("allStatuses")}
            />
          </Field>
          <Field label={tr("from")}>
            <IsoDatePicker value={dateFrom} onValueChange={pager.filter(setDateFrom)} />
          </Field>
          <Field label={tr("to")}>
            <IsoDatePicker value={dateTo} onValueChange={pager.filter(setDateTo)} />
          </Field>
        </div>
      }
    >
      <ReportPanel>
        {rows.length === 0 ? (
          <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {page.isLoading ? tr("loading") : tr("noRows")}
          </p>
        ) : (
          <>
            <Table>
              <THead>
                <TR>
                  <TH className="w-32">{t("number")}</TH>
                  <TH className="w-24">{tr("date")}</TH>
                  <TH className="w-28">{t("kind")}</TH>
                  <TH className="w-20">{tr("warehouse")}</TH>
                  <TH className="w-20">{tr("transactionType")}</TH>
                  <TH>{tr("description")}</TH>
                  <TH className="text-right">{tr("value")}</TH>
                  <TH className="w-28">{tr("status")}</TH>
                  <TH className="w-28">{tr("entry")}</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((row) => (
                  <TR key={row.id}>
                    <TD>
                      <Link
                        href={`/inventory/documents/${row.id}`}
                        className="font-mono text-xs font-semibold text-[var(--vinea-brand)] underline"
                      >
                        {row.number}
                      </Link>
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {formatDate(row.document_date)}
                    </TD>
                    <TD className="text-xs">{tk(row.doc_type)}</TD>
                    <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                      {warehouseCode(row.warehouse_id)}
                    </TD>
                    <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                      {typeCode(row.transaction_type_id)}
                    </TD>
                    <TD className="text-xs">{row.description}</TD>
                    <TD
                      data-testid="document-value"
                      className="text-right font-mono text-xs tabular-nums"
                    >
                      {formatMoney(Number(row.total_value), baseLike, { showCode: false })}
                    </TD>
                    <TD>
                      <StatusChip tone={DOCUMENT_STATUS_TONE[row.status] ?? "neutral"}>
                        {t(DOCUMENT_STATUS_KEY[row.status] ?? "statusPosted")}
                      </StatusChip>
                    </TD>
                    <TD>
                      {/* Null is not a missing link: a document that moved no value has no
                          ledger side to show, which is why this screen exists for it. */}
                      {row.journal_entry_id === null ? (
                        <span className="text-xs text-[var(--vinea-ink-subtle)]">
                          {t("noEntry")}
                        </span>
                      ) : (
                        <Link
                          href={`/gl/entries/${row.journal_entry_id}`}
                          className="inline-flex items-center gap-1 font-mono text-xs text-[var(--vinea-brand)] underline"
                        >
                          {t("viewEntry")}
                          <ExternalLink className="size-3" />
                        </Link>
                      )}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <ReportPager
              count={rows.length}
              hasPrevious={pager.hasPrevious}
              hasNext={(page.data?.next_cursor ?? null) !== null}
              onPrevious={pager.previous}
              onNext={() => pager.next(page.data?.next_cursor ?? null)}
            />
          </>
        )}
      </ReportPanel>
      <p className="hidden text-xs print:block">{dotted(company.data?.name, t("title"))}</p>
    </ReportPage>
  );
}
