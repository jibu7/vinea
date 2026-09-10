"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { CalendarClock, ExternalLink } from "lucide-react";
import { Button } from "@/design/components/button";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Field } from "@/design/components/input";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { useHasPermission } from "@/features/auth/hooks";
import { useAccounts, useCompanyDetails, useCurrencies } from "@/features/gl/hooks";
import { byId } from "@/features/gl/lookups";
import { isApiError } from "@/features/auth/hooks";
import { exportToCsv } from "@/lib/csv";
import { formatDate, formatMoney } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { useMatureInstruments, usePartners, usePendingInstruments } from "./hooks";
import { partnerCode, type PartnerRole } from "./types";

/**
 * Post-dated instruments, and the action that banks them.
 *
 * A receipt or payment dated ahead books its cash side to the post-dated account instead of
 * the bank (P4 decision 7), and `mature_instruments(as_of)` moves it across on or after
 * maturity. That service shipped as an endpoint and a scheduled job with no screen, which
 * left a document a person could raise and nothing a person could do about: the only way to
 * learn a cheque existed was to run maturity and watch what happened. This is that screen.
 *
 * The run is deliberately not per-row. `mature_instruments` takes a date and banks everything
 * matured by it, in one transaction through the PostingEngine — a per-row button would either
 * lie about that or need a second, narrower service to sit behind it.
 */
export function InstrumentsScreen({ role }: { role: PartnerRole }) {
  const t = useTranslations("arap.instruments");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const hasPermission = useHasPermission();
  const canPost = hasPermission(`${role}:transactions_post`);

  const [asOf, setAsOf] = useState(() => new Date().toISOString().slice(0, 10));
  const [banked, setBanked] = useState<number[]>([]);

  const instruments = usePendingInstruments(role, asOf);
  const mature = useMatureInstruments(role);
  const partners = usePartners(role, { includeInactive: true });
  const partnerById = new Map((partners.data ?? []).map((p) => [p.id, p]));
  const accounts = useAccounts();
  const accountById = byId(accounts.data);
  const currencies = useCurrencies();
  const currencyById = byId(currencies.data);
  const company = useCompanyDetails();

  const rows = instruments.data ?? [];
  const due = rows.filter((row) => row.is_due);

  /** Code and name, as every other AR/AP report identifies a partner. */
  function partnerLabel(partnerId: number): string {
    const partner = partnerById.get(partnerId);
    if (!partner) return String(partnerId);
    const code = partnerCode(partner, role);
    return code ? `${code} · ${partner.name}` : partner.name;
  }

  function currencyLike(currencyId: number) {
    const currency = currencyById.get(currencyId);
    return {
      code: currency?.code ?? "",
      decimalPlaces: currency?.decimal_places ?? 0,
      symbol: currency?.symbol ?? null,
    };
  }

  async function handleMature() {
    setBanked([]);
    try {
      const result = await mature.mutateAsync(asOf);
      if (result.matured_document_ids.length === 0) {
        toast.show({ title: t("maturedNone"), description: t("nothingDue"), tone: "neutral" });
        return;
      }
      setBanked(result.journal_entry_ids);
      toast.show({
        title: t("matured", { count: result.matured_document_ids.length }),
        tone: "success",
      });
    } catch (err) {
      if (isApiError(err)) showApiError(err, t("matureFailed"));
      else showApiError(err, t("matureFailed"));
    }
  }

  function handleExport() {
    exportToCsv(
      `post-dated-${role}`,
      [
        t("number"),
        t("partner"),
        t("documentDate"),
        t("maturityDate"),
        t("instrument"),
        t("amount"),
        t("open"),
        t("status"),
      ],
      rows.map((row) => [
        row.number,
        partnerLabel(row.partner_id),
        row.document_date,
        row.maturity_date,
        row.instrument_type ?? "",
        row.total_amount,
        row.open_amount,
        row.is_due ? t("due") : t("waiting"),
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
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-3">
          <Field label={t("asOfDate")}>
            <IsoDatePicker value={asOf} onValueChange={setAsOf} />
          </Field>
          <div className="flex items-end text-xs text-[var(--vinea-ink-muted)]">
            <span data-testid="instruments-due-count">
              {t("dueCount", { count: due.length, date: formatDate(asOf) })}
            </span>
          </div>
          <div className="flex items-end justify-end print:hidden">
            <Button
              variant="primary"
              disabled={!canPost || due.length === 0 || mature.isPending}
              onClick={handleMature}
            >
              {mature.isPending ? t("maturing") : t("markMatured")}
            </Button>
          </div>
          <p className="text-xs text-[var(--vinea-ink-subtle)] sm:col-span-3">{t("explainer")}</p>
        </div>
      }
    >
      {banked.length > 0 && (
        <div
          data-testid="instruments-banked"
          className="flex flex-wrap items-center gap-3 rounded-[var(--radius-control)] border border-[var(--vinea-success)] bg-[var(--vinea-success-soft)] px-4 py-2 text-xs text-[var(--vinea-success)] print:hidden"
        >
          <CalendarClock className="size-4" />
          {/* The transfer is a journal entry like any other, so the screen hands over the
              entries it just wrote rather than asking anyone to take its word for it. */}
          {banked.map((entryId) => (
            <Link
              key={entryId}
              href={`/gl/entries/${entryId}`}
              className="inline-flex items-center gap-1 font-medium underline"
            >
              {t("viewEntry", { number: entryId })} <ExternalLink className="size-3" />
            </Link>
          ))}
        </div>
      )}

      <ReportPanel>
        {rows.length === 0 ? (
          <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {instruments.isLoading ? t("loading") : t("none")}
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-32">{t("number")}</TH>
                <TH>{t("partner")}</TH>
                <TH className="w-24">{t("documentDate")}</TH>
                <TH className="w-24">{t("maturityDate")}</TH>
                <TH className="w-24">{t("instrument")}</TH>
                <TH className="w-36">{t("postedTo")}</TH>
                <TH className="text-right">{t("amount")}</TH>
                <TH className="text-right">{t("open")}</TH>
                <TH className="w-24 text-right">{t("status")}</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map((row) => {
                const account = row.cash_account_id
                  ? accountById.get(row.cash_account_id)
                  : undefined;
                return (
                  <TR key={row.id}>
                    <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">
                      {row.number}
                    </TD>
                    <TD className="text-xs">{partnerLabel(row.partner_id)}</TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {formatDate(row.document_date)}
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {formatDate(row.maturity_date)}
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {row.instrument_type}
                    </TD>
                    {/* Where the cash *will* go, named on the document itself — the
                        post-dated account is where it is now. */}
                    <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                      {account ? `${account.code} · ${account.name}` : null}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums">
                      {formatMoney(Number(row.total_amount), currencyLike(row.currency_id))}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums">
                      {formatMoney(Number(row.open_amount), currencyLike(row.currency_id))}
                    </TD>
                    <TD className="text-right">
                      <StatusChip tone={row.is_due ? "warning" : "neutral"}>
                        {row.is_due ? t("due") : t("waiting")}
                      </StatusChip>
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        )}
      </ReportPanel>
    </ReportPage>
  );
}
