"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Combobox } from "@/design/components/combobox";
import { Field } from "@/design/components/input";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useCompanyDetails, useCurrencies } from "@/features/gl/hooks";
import { exportToCsv } from "@/lib/csv";
import { formatDate, formatMoney } from "@/lib/format";
import { useAllocations, usePartners } from "../hooks";
import { partnerCode, type PartnerRole } from "../types";

/** Allocation report over `GET /{role}/allocations` — what was matched against what, and the
 * exchange difference each match realized. */
export function AllocationReport({ role }: { role: PartnerRole }) {
  const t = useTranslations("reports");
  const [partnerId, setPartnerId] = useState("");

  const partners = usePartners(role, { includeInactive: true });
  const allocations = useAllocations(role, {
    partnerId: partnerId ? Number(partnerId) : undefined,
  });
  const currencies = useCurrencies();
  const company = useCompanyDetails();
  const base = (currencies.data ?? []).find((c) => c.is_base);
  const baseLike = {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };
  const partnerNames = new Map((partners.data ?? []).map((p) => [p.id, p.name]));

  const rows = (allocations.data ?? []).flatMap((allocation) =>
    allocation.lines.map((line) => ({ allocation, line })),
  );

  function handleExport() {
    exportToCsv(
      `allocations-${role}`,
      [t("allocNumber"), t("date"), t("partner"), t("debit"), t("credit"), t("allocated"), t("discount"), t("fx")],
      rows.map(({ allocation, line }) => [
        allocation.number,
        allocation.allocation_date,
        partnerNames.get(allocation.partner_id) ?? allocation.partner_id,
        line.debit_document_id,
        line.credit_document_id,
        line.amount,
        line.discount_amount,
        line.fx_base_amount,
      ]),
    );
  }

  return (
    <ReportPage
      title={t(role === "ar" ? "allocTitleAr" : "allocTitleAp")}
      subtitle={t("allocSubtitle")}
      companyName={company.data?.name}
      onExportCsv={rows.length ? handleExport : undefined}
      filters={
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-3">
          <Field label={t("partner")}>
            <Combobox
              options={[
                { value: "", label: t("noRows") },
                ...(partners.data ?? []).map((p) => ({
                  value: String(p.id),
                  label: `${partnerCode(p, role)} · ${p.name}`,
                })),
              ]}
              value={partnerId}
              onValueChange={setPartnerId}
              placeholder={t("partner")}
            />
          </Field>
        </div>
      }
    >
      <ReportPanel>
        {rows.length === 0 ? (
          <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {allocations.isLoading ? t("loading") : t("noRows")}
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-28">{t("allocNumber")}</TH>
                <TH className="w-24">{t("date")}</TH>
                <TH>{t("partner")}</TH>
                <TH className="text-right">{t("allocated")}</TH>
                <TH className="text-right">{t("discount")}</TH>
                <TH className="text-right">{t("fx")}</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map(({ allocation, line }) => (
                <TR key={`${allocation.id}-${line.id}`}>
                  <TD className="font-mono text-xs text-[var(--vinea-brand)]">
                    {allocation.number}
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">
                    {formatDate(allocation.allocation_date)}
                  </TD>
                  <TD className="text-xs">{partnerNames.get(allocation.partner_id)}</TD>
                  <TD className="text-right font-mono text-xs tabular-nums">{line.amount}</TD>
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {line.discount_amount}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {formatMoney(Number(line.fx_base_amount), baseLike, { showCode: false })}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </ReportPanel>
    </ReportPage>
  );
}
