"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useCompanyDetails } from "@/features/gl/hooks";
import { exportToCsv } from "@/lib/csv";
import { usePartners } from "../hooks";
import { partnerCode, type PartnerRole } from "../types";

/** Customer / supplier listing over `GET /{role}/partners` — the master as it stands. */
export function PartnerListingReport({ role }: { role: PartnerRole }) {
  const t = useTranslations("reports");
  const [includeInactive, setIncludeInactive] = useState(false);
  const partners = usePartners(role, { includeInactive });
  const company = useCompanyDetails();
  const rows = partners.data ?? [];

  function handleExport() {
    exportToCsv(
      `${role === "ar" ? "customers" : "suppliers"}-listing`,
      [t("code"), t("name"), t("tin"), t("email"), t("phone"), t("status")],
      rows.map((p) => [
        partnerCode(p, role) ?? "",
        p.name,
        p.tin ?? "",
        p.email ?? "",
        p.phone ?? "",
        p.is_active ? t("active") : t("inactive"),
      ]),
    );
  }

  return (
    <ReportPage
      title={t(role === "ar" ? "listTitleAr" : "listTitleAp")}
      subtitle={t("listSubtitle")}
      companyName={company.data?.name}
      onExportCsv={rows.length ? handleExport : undefined}
      filters={
        <label className="flex items-center gap-2 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 text-xs text-[var(--vinea-ink-muted)]">
          <input
            type="checkbox"
            checked={includeInactive}
            onChange={(e) => setIncludeInactive(e.target.checked)}
            className="size-3.5"
          />
          {t("includeInactive")}
        </label>
      }
    >
      <ReportPanel>
        {rows.length === 0 ? (
          <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {partners.isLoading ? t("loading") : t("noRows")}
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-32">{t("code")}</TH>
                <TH>{t("name")}</TH>
                <TH className="w-28">{t("tin")}</TH>
                <TH>{t("email")}</TH>
                <TH className="w-32">{t("phone")}</TH>
                <TH className="w-24 text-right">{t("status")}</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map((partner) => (
                <TR key={partner.id}>
                  <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">
                    {partnerCode(partner, role)}
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink)]">{partner.name}</TD>
                  <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                    {partner.tin}
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">{partner.email}</TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">{partner.phone}</TD>
                  <TD className="text-right">
                    <StatusChip tone={partner.is_active ? "success" : "neutral"}>
                      {partner.is_active ? t("active") : t("inactive")}
                    </StatusChip>
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
