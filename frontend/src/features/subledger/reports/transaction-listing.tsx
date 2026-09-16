"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { IsoDatePicker } from "@/design/components/date-picker";
import { QueryState } from "@/design/components/query-state";
import { Field } from "@/design/components/input";
import {
  ReportPage,
  ReportPager,
  ReportPanel,
  useCursorPager,
} from "@/design/components/report-page";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useCompanyDetails, useCurrencies } from "@/features/gl/hooks";
import { byId } from "@/features/gl/lookups";
import { exportToCsv } from "@/lib/csv";
import { formatDate, formatMoney } from "@/lib/format";
import { useDocumentPage, usePartners } from "../hooks";
import type { PartnerRole } from "../types";

/**
 * Transaction listing over `GET /{role}/documents` — paged **by the server**, cursor-style
 * through `useCursorPager` / `ReportPager`.
 *
 * That pair was factored out of this screen at P5 step 8, when four more reports needed the
 * same forward-and-back and the alternative was five copies of it. The reasoning it carries
 * is the one that was written here: the cursor is the last id of the page before, never an
 * offset, so a document posted while someone is paging cannot shift rows onto a page they
 * have already seen — and there is therefore no page count and no jumping to page 7, which
 * is the trade the kernel's ADR-11 made.
 */
export function TransactionListingReport({ role }: { role: PartnerRole }) {
  const t = useTranslations("reports");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const pager = useCursorPager();

  const page = useDocumentPage(role, {
    cursor: pager.cursor,
    dateFrom: dateFrom || undefined,
    dateTo: dateTo || undefined,
  });
  const partners = usePartners(role, { includeInactive: true });
  const partnerNames = new Map((partners.data ?? []).map((p) => [p.id, p.name]));
  const currencies = useCurrencies();
  const currencyById = byId(currencies.data);
  const company = useCompanyDetails();

  const rows = page.data?.items ?? [];

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
      `transactions-${role}`,
      [t("number"), t("type"), t("date"), t("dueDate"), t("partner"), t("currency"), t("amount"), t("open")],
      rows.map((d) => [
        d.number,
        d.transaction_type,
        d.document_date,
        d.due_date ?? "",
        partnerNames.get(d.partner_id) ?? d.partner_id,
        currencyById.get(d.currency_id)?.code ?? "",
        d.total_amount,
        d.open_amount,
      ]),
    );
  }

  return (
    <ReportPage
      title={t(role === "ar" ? "txTitleAr" : "txTitleAp")}
      subtitle={t("txSubtitle")}
      companyName={company.data?.name}
      asOfLabel={dateFrom && dateTo ? t("dateRange", { from: formatDate(dateFrom), to: formatDate(dateTo) }) : undefined}
      onExportCsv={rows.length ? handleExport : undefined}
      filters={
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-3">
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
          <QueryState query={page} isEmpty empty={t("noRows")} testId="query" />
        ) : (
          <>
            <Table>
              <THead>
                <TR>
                  <TH className="w-32">{t("number")}</TH>
                  <TH className="w-20">{t("type")}</TH>
                  <TH className="w-24">{t("date")}</TH>
                  <TH>{t("partner")}</TH>
                  <TH className="text-right">{t("amount")}</TH>
                  <TH className="text-right">{t("open")}</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((document) => (
                  <TR key={document.id}>
                    <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">
                      {document.number}
                    </TD>
                    <TD className="font-mono text-xs uppercase text-[var(--vinea-ink-subtle)]">
                      {document.transaction_type}
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {formatDate(document.document_date)}
                    </TD>
                    <TD className="text-xs">{partnerNames.get(document.partner_id)}</TD>
                    <TD className="text-right font-mono text-xs tabular-nums">
                      {formatMoney(Number(document.total_amount), currencyLike(document.currency_id))}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums">
                      {formatMoney(Number(document.open_amount), currencyLike(document.currency_id))}
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
    </ReportPage>
  );
}
