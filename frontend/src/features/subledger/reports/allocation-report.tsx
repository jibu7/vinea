"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ExternalLink } from "lucide-react";
import { Combobox } from "@/design/components/combobox";
import { Field } from "@/design/components/input";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useCompanyDetails, useCurrencies } from "@/features/gl/hooks";
import { byId } from "@/features/gl/lookups";
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
  const currencyById = byId(currencies.data ?? []);

  function currencyLike(currencyId: number) {
    const currency = currencyById.get(currencyId);
    return {
      code: currency?.code ?? "",
      decimalPlaces: currency?.decimal_places ?? 0,
      symbol: currency?.symbol ?? null,
    };
  }

  // The endpoint already returns one row per pairing; nothing to flatten.
  const rows = allocations.data ?? [];

  function handleExport() {
    exportToCsv(
      `allocations-${role}`,
      [t("allocNumber"), t("date"), t("partner"), t("debit"), t("credit"), t("allocated"), t("discount"), t("fx")],
      rows.map((row) => [
        row.number,
        row.allocation_date,
        partnerNames.get(row.partner_id) ?? row.partner_id,
        row.debit_number,
        row.credit_number,
        row.amount,
        row.discount_amount,
        row.fx_base_amount,
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
                <TH className="w-32">{t("allocNumber")}</TH>
                <TH className="w-24">{t("date")}</TH>
                <TH>{t("partner")}</TH>
                <TH className="w-32">{t("debit")}</TH>
                <TH className="w-32">{t("credit")}</TH>
                <TH className="text-right">{t("allocated")}</TH>
                <TH className="text-right">{t("discount")}</TH>
                <TH className="text-right">{t("fx")}</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map((row) => (
                <TR key={`${row.allocation_id}-${row.debit_document_id}-${row.credit_document_id}`}>
                  <TD className="font-mono text-xs">
                    {/* The drill-down: an exchange difference on this report leads to the
                        entry that carries it, the same way an open item leads to its
                        document on the enquiry. An allocation that posted nothing has no
                        entry to open, and says so rather than offering a dead link. */}
                    {row.journal_entry_id === null ? (
                      <span className="text-[var(--vinea-ink-muted)]">
                        {row.number}{" "}
                        <span className="text-[10px] text-[var(--vinea-ink-subtle)]">
                          {t("allocNoEntry")}
                        </span>
                      </span>
                    ) : (
                      <Link
                        href={`/gl/entries/${row.journal_entry_id}`}
                        aria-label={t("allocDrillDown", { number: row.number })}
                        className="inline-flex items-center gap-1 font-semibold text-[var(--vinea-brand)] underline"
                      >
                        {row.number}
                        <ExternalLink className="size-3" />
                      </Link>
                    )}
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">
                    {formatDate(row.allocation_date)}
                  </TD>
                  <TD className="text-xs">{partnerNames.get(row.partner_id)}</TD>
                  <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                    {row.debit_number}
                  </TD>
                  <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                    {row.credit_number}
                  </TD>
                  {/* Allocated and discount are in the *allocation's* currency; only the
                      realized difference is in base. Printed raw, they came out at the
                      NUMERIC scale the wire carries ("200000.000000"). */}
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {formatMoney(Number(row.amount), currencyLike(row.currency_id), {
                      showCode: false,
                    })}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {formatMoney(Number(row.discount_amount), currencyLike(row.currency_id), {
                      showCode: false,
                    })}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {formatMoney(Number(row.fx_base_amount), baseLike, { showCode: false })}
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
