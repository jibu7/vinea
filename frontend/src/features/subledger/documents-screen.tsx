"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
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
import { byId } from "@/features/gl/lookups";
import { DocumentKind, DocumentStatus } from "@/lib/api-enums";
import { exportToCsv } from "@/lib/csv";
import { dotted, formatDate, formatMoney } from "@/lib/format";
import { useDocumentPage, usePartners } from "./hooks";
import type { PartnerRole } from "./types";

export const DOCUMENT_STATUS_TONE: Record<string, "neutral" | "success" | "warning" | "danger"> = {
  [DocumentStatus.POSTED]: "success",
  [DocumentStatus.REVERSED]: "neutral",
};

export const DOCUMENT_STATUS_KEY: Record<string, "statusPosted" | "statusReversed"> = {
  [DocumentStatus.POSTED]: "statusPosted",
  [DocumentStatus.REVERSED]: "statusReversed",
};

/**
 * Every posted customer or supplier document, with the way to correct one.
 *
 * **Why this screen exists.** P4 gave every partner document a reversal and every allocation
 * an undo, and shipped both as endpoints with no caller anywhere in the frontend: there was no
 * `/ar/documents/{id}` route to put the action on, because P4 built capture screens, an
 * enquiry and the reports and no document detail. So an invoice posted in error, or an
 * allocation made against the wrong invoice, was uncorrectable by anybody using the product.
 * The general ledger's own Reverse was not the way out either — it posts the reversing entry
 * and leaves the open item standing, which breaks `SUM(open items) == control balance`
 * silently, and the kernel now refuses it (`reverse_via_module_document`).
 *
 * Same hole as Appendix C.1.7's on the inventory side, one phase older and twice over, and
 * the same answer: the tree gains the screen that closes it, on the pattern
 * `/inventory/documents` set.
 */
export function PartnerDocumentsScreen({ role }: { role: PartnerRole }) {
  const t = useTranslations("arap.documentList");
  // Two namespaces rather than a dynamic key off one: the owner's menu names the AP
  // documents differently from AR's — a credit note is a "Return to supplier" on the
  // purchase side — and `useTranslations` resolves a namespace, not a computed path.
  const tKind = useTranslations(role === "ap" ? "arap.documentList.kindsAp" : "arap.documentList.kinds");
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const pager = useCursorPager();
  const page = useDocumentPage(role, {
    cursor: pager.cursor,
    kind: kind || undefined,
    status: status || undefined,
    dateFrom: dateFrom || undefined,
    dateTo: dateTo || undefined,
  });
  const partners = usePartners(role, { includeInactive: true });
  const partnerNames = new Map((partners.data ?? []).map((p) => [p.id, p.name]));
  const currencies = useCurrencies();
  const currencyById = byId(currencies.data);
  const company = useCompanyDetails();

  const rows = page.data?.items ?? [];

  /** A kind the catalogue does not name reads back as its own code rather than blank. */
  const kindLabel = (value: string) => (tKind.has(value) ? tKind(value) : value);

  function currencyLike(currencyId: number) {
    const currency = currencyById.get(currencyId);
    return {
      code: currency?.code ?? "",
      decimalPlaces: currency?.decimal_places ?? 0,
      symbol: currency?.symbol ?? null,
    };
  }

  function handleExport() {
    exportToCsv(
      `${role}-documents-${dateFrom || "all"}-${dateTo || "all"}`,
      [
        t("number"),
        t("date"),
        t("kind"),
        t("partner"),
        t("reference"),
        t("total"),
        t("open"),
        t("status"),
        t("entry"),
      ],
      rows.map((row) => [
        row.number,
        row.document_date,
        kindLabel(row.kind),
        partnerNames.get(row.partner_id) ?? row.partner_id,
        row.reference ?? "",
        row.total_amount,
        row.open_amount,
        row.status,
        row.id,
      ]),
    );
  }

  return (
    <ReportPage
      title={t(role === "ar" ? "titleAr" : "titleAp")}
      subtitle={t(role === "ar" ? "subtitleAr" : "subtitleAp")}
      companyName={company.data?.name}
      onExportCsv={rows.length ? handleExport : undefined}
      filters={
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-4">
          <Field label={t("kind")}>
            <Combobox
              options={[
                { value: "", label: t("allKinds") },
                ...Object.values(DocumentKind).map((value) => ({
                  value,
                  label: kindLabel(value),
                })),
              ]}
              value={kind}
              onValueChange={pager.filter(setKind)}
              placeholder={t("allKinds")}
            />
          </Field>
          <Field label={t("status")}>
            <Combobox
              options={[
                { value: "", label: t("allStatuses") },
                ...Object.values(DocumentStatus).map((value) => ({
                  value,
                  label: t(DOCUMENT_STATUS_KEY[value] ?? "statusPosted"),
                })),
              ]}
              value={status}
              onValueChange={pager.filter(setStatus)}
              placeholder={t("allStatuses")}
            />
          </Field>
          <Field label={t("from")}>
            <IsoDatePicker value={dateFrom} onValueChange={pager.filter(setDateFrom)} />
          </Field>
          <Field label={t("to")}>
            <IsoDatePicker value={dateTo} onValueChange={pager.filter(setDateTo)} />
          </Field>
        </div>
      }
    >
      <ReportPanel>
        {rows.length === 0 ? (
          <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {page.isLoading ? t("loading") : t("noRows")}
          </p>
        ) : (
          <>
            <Table>
              <THead>
                <TR>
                  <TH className="w-32">{t("number")}</TH>
                  <TH className="w-24">{t("date")}</TH>
                  <TH className="w-28">{t("kind")}</TH>
                  <TH>{t("partner")}</TH>
                  {/* Both money columns carry their currency. A listing that mixes an RWF
                      invoice with a USD one and marks neither is how P4 shipped base amounts
                      wearing a foreign currency's symbol — the reader cannot tell, and the
                      column is per-document currency, not base. */}
                  <TH className="text-right">{t("total")}</TH>
                  <TH className="text-right">{t("open")}</TH>
                  <TH className="w-24">{t("status")}</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((row) => (
                  <TR key={row.id}>
                    <TD>
                      <Link
                        href={`/${role}/documents/${row.id}`}
                        className="font-mono text-xs font-semibold text-[var(--vinea-brand)] underline"
                      >
                        {row.number}
                      </Link>
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {formatDate(row.document_date)}
                    </TD>
                    <TD className="text-xs">{kindLabel(row.kind)}</TD>
                    <TD className="text-xs">
                      {partnerNames.get(row.partner_id) ?? row.partner_id}
                    </TD>
                    <TD
                      data-testid="document-total"
                      className="text-right font-mono text-xs tabular-nums"
                    >
                      {formatMoney(Number(row.total_amount), currencyLike(row.currency_id))}
                    </TD>
                    <TD
                      data-testid="document-open"
                      className="text-right font-mono text-xs tabular-nums"
                    >
                      {formatMoney(Number(row.open_amount), currencyLike(row.currency_id))}
                    </TD>
                    <TD>
                      <StatusChip tone={DOCUMENT_STATUS_TONE[row.status] ?? "neutral"}>
                        {t(DOCUMENT_STATUS_KEY[row.status] ?? "statusPosted")}
                      </StatusChip>
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
      <p className="hidden text-xs print:block">
        {dotted(company.data?.name, t(role === "ar" ? "titleAr" : "titleAp"))}
      </p>
    </ReportPage>
  );
}
