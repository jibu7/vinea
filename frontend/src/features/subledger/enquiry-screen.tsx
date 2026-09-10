"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ExternalLink, Search } from "lucide-react";
import { Combobox } from "@/design/components/combobox";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Drawer, DrawerContent } from "@/design/components/drawer";
import { Field } from "@/design/components/input";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/design/components/tabs";
import { useAccounts, useCurrencies, useJournalEntry } from "@/features/gl/hooks";
import { byId } from "@/features/gl/lookups";
import { exportToCsv } from "@/lib/csv";
import { formatDate, formatMoney } from "@/lib/format";
import { usePartnerEnquiry, usePartners } from "./hooks";
import { partnerCode, type PartnerRole } from "./types";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/**
 * Customer / supplier enquiry over `GET /{role}/enquiry/{partner_id}`.
 *
 * The drill-down is the point: an open item opens the journal entry that created it, so a
 * question about a figure ends at the ledger rather than at another report. Documents are
 * labelled by transaction type, never by kind — a journal debit reads "AR journal".
 */
export function EnquiryScreen({ role }: { role: PartnerRole }) {
  const t = useTranslations("arap.enquiry");
  const tr = useTranslations(`arap.role.${role}`);
  const [partnerId, setPartnerId] = useState("");
  const [asOf, setAsOf] = useState(today);
  const [drillEntryId, setDrillEntryId] = useState<number | null>(null);

  const partners = usePartners(role, { includeInactive: true });
  const enquiry = usePartnerEnquiry(role, partnerId ? Number(partnerId) : null, asOf);
  const currencies = useCurrencies();
  const currencyById = byId(currencies.data);
  const accounts = useAccounts();
  const accountById = byId(accounts.data);
  const entry = useJournalEntry(drillEntryId);

  const base = (currencies.data ?? []).find((c) => c.is_base);
  const baseLike = {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };
  function currencyLike(currencyId: number) {
    const currency = currencyById.get(currencyId);
    return {
      code: currency?.code ?? "",
      decimalPlaces: currency?.decimal_places ?? 0,
      symbol: currency?.symbol ?? null,
    };
  }

  const data = enquiry.data;
  const headroom = data?.credit_available;

  function handleExport() {
    if (!data) return;
    exportToCsv(
      `enquiry-${data.partner_code ?? data.partner_id}-${asOf}`,
      [t("number"), t("type"), t("date"), t("due"), t("amount"), t("open"), t("running")],
      data.entries.map((row) => [
        row.number,
        row.transaction_type_name,
        row.document_date,
        row.due_date ?? "",
        row.total_amount,
        row.open_amount,
        row.running_base,
      ]),
    );
  }

  return (
    <ReportPage
      title={t(role === "ar" ? "titleAr" : "titleAp")}
      subtitle={t("subtitle")}
      companyName={data?.partner_name}
      asOfLabel={formatDate(asOf)}
      onExportCsv={data ? handleExport : undefined}
      filters={
        <div className="grid grid-cols-1 gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4 sm:grid-cols-4">
          <Field label={tr("partner")}>
            <Combobox
              options={(partners.data ?? []).map((p) => ({
                value: String(p.id),
                label: `${partnerCode(p, role)} · ${p.name}`,
              }))}
              value={partnerId}
              onValueChange={setPartnerId}
              placeholder={t("choosePartner")}
            />
          </Field>
          <Field label={t("asOfDate")}>
            <IsoDatePicker value={asOf} onValueChange={setAsOf} />
          </Field>
          {data && (
            <div className="sm:col-span-2 flex items-end gap-4 text-xs">
              <span className="text-[var(--vinea-ink-muted)]">
                {t("openBalance")}{" "}
                <span className="font-mono tabular-nums text-[var(--vinea-ink)]">
                  {formatMoney(Number(data.balance_base), baseLike)}
                </span>
              </span>
              <span className="text-[var(--vinea-ink-muted)]">
                {t("headroom")}{" "}
                {headroom === null || headroom === undefined ? (
                  <span className="text-[var(--vinea-ink-subtle)]">{t("noLimit")}</span>
                ) : (
                  <StatusChip tone={Number(headroom) < 0 ? "danger" : "success"}>
                    {Number(headroom) < 0
                      ? t("overLimit")
                      : formatMoney(Number(headroom), baseLike)}
                  </StatusChip>
                )}
              </span>
            </div>
          )}
        </div>
      }
    >
      {!partnerId ? (
        <p className="py-10 text-center text-sm text-[var(--vinea-ink-subtle)]">
          {t("selectPartner")}
        </p>
      ) : (
        <Tabs defaultValue="open">
          <TabsList className="mb-3 print:hidden">
            <TabsTrigger value="open">{t("openItems")}</TabsTrigger>
            <TabsTrigger value="activity">{t("activity")}</TabsTrigger>
          </TabsList>

          <TabsContent value="open">
            <ReportPanel>
              {!data || data.open_items.length === 0 ? (
                <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
                  {t("noneOpen")}
                </p>
              ) : (
                <Table>
                  <THead>
                    <TR>
                      <TH className="w-32">{t("number")}</TH>
                      <TH className="w-28">{t("type")}</TH>
                      <TH className="w-24">{t("date")}</TH>
                      <TH className="w-24">{t("due")}</TH>
                      <TH className="text-right">{t("amount")}</TH>
                      <TH className="text-right">{t("open")}</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {data.open_items.map((item) => {
                      const entryRow = data.entries.find(
                        (row) => row.document_id === item.document_id,
                      );
                      return (
                        <TR key={item.document_id}>
                          <TD>
                            {/* The drill-down: an open item leads to the journal entry that
                                created it, so a question about a figure ends at the ledger. */}
                            <button
                              type="button"
                              onClick={() =>
                                setDrillEntryId(entryRow?.journal_entry_id ?? null)
                              }
                              aria-label={t("drillDown", { number: item.number })}
                              className="inline-flex items-center gap-1 font-mono text-xs font-semibold text-[var(--vinea-brand)] underline"
                            >
                              {item.number}
                              <ExternalLink className="size-3" />
                            </button>
                          </TD>
                          <TD className="text-xs text-[var(--vinea-ink-muted)]">
                            {item.transaction_type_name}
                          </TD>
                          <TD className="text-xs text-[var(--vinea-ink-muted)]">
                            {formatDate(item.document_date)}
                          </TD>
                          <TD className="text-xs text-[var(--vinea-ink-muted)]">
                            {item.due_date ? formatDate(item.due_date) : null}
                            {item.days_overdue > 0 && (
                              <StatusChip tone="warning">
                                {t("overdue", { days: item.days_overdue })}
                              </StatusChip>
                            )}
                          </TD>
                          <TD className="text-right font-mono text-xs tabular-nums">
                            {formatMoney(
                              Number(item.total_amount),
                              currencyLike(item.currency_id),
                            )}
                          </TD>
                          <TD className="text-right font-mono text-xs tabular-nums">
                            {formatMoney(
                              Number(item.open_amount),
                              currencyLike(item.currency_id),
                            )}
                          </TD>
                        </TR>
                      );
                    })}
                  </TBody>
                </Table>
              )}
            </ReportPanel>
          </TabsContent>

          <TabsContent value="activity">
            <ReportPanel>
              {!data || data.entries.length === 0 ? (
                <p className="py-8 text-center text-xs text-[var(--vinea-ink-subtle)]">
                  {t("noActivity")}
                </p>
              ) : (
                <Table>
                  <THead>
                    <TR>
                      <TH className="w-32">{t("number")}</TH>
                      <TH className="w-28">{t("type")}</TH>
                      <TH className="w-24">{t("date")}</TH>
                      <TH className="text-right">{t("amount")}</TH>
                      <TH className="text-right">{t("running")}</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {data.entries.map((row) => (
                      <TR key={row.document_id}>
                        <TD>
                          <button
                            type="button"
                            onClick={() => setDrillEntryId(row.journal_entry_id)}
                            aria-label={t("drillDown", { number: row.number })}
                            className="font-mono text-xs font-semibold text-[var(--vinea-brand)] underline"
                          >
                            {row.number}
                          </button>
                        </TD>
                        <TD className="text-xs text-[var(--vinea-ink-muted)]">
                          {row.transaction_type_name}
                        </TD>
                        <TD className="text-xs text-[var(--vinea-ink-muted)]">
                          {formatDate(row.document_date)}
                        </TD>
                        <TD className="text-right font-mono text-xs tabular-nums">
                          {formatMoney(Number(row.total_amount), currencyLike(row.currency_id))}
                        </TD>
                        <TD className="text-right font-mono text-xs tabular-nums">
                          {formatMoney(Number(row.running_base), baseLike, { showCode: false })}
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              )}
            </ReportPanel>
          </TabsContent>
        </Tabs>
      )}

      <Drawer open={drillEntryId !== null} onOpenChange={(open) => !open && setDrillEntryId(null)}>
        {drillEntryId !== null && (
          <DrawerContent
            title={entry.data?.number ?? t("viewEntry")}
            description={entry.data ? formatDate(entry.data.entry_date) : undefined}
          >
            <div className="space-y-3">
              <Link
                href={`/gl/entries/${drillEntryId}`}
                className="inline-flex items-center gap-1 text-xs text-[var(--vinea-brand)] underline"
              >
                {t("viewEntry")} <ExternalLink className="size-3" />
              </Link>
              {entry.data && (
                <Table>
                  <THead>
                    <TR>
                      <TH>{t("account")}</TH>
                      <TH>{t("narrative")}</TH>
                      <TH className="text-right">{t("amount")}</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {entry.data.lines.map((line) => (
                      <TR key={line.id}>
                        {/* Code and name, not the row id: the point of drilling in is to see
                            which accounts moved. */}
                        <TD className="font-mono text-xs">
                          {(() => {
                            const account = accountById.get(line.gl_account_id);
                            return account
                              ? `${account.code} · ${account.name}`
                              : line.gl_account_id;
                          })()}
                        </TD>
                        <TD className="text-xs text-[var(--vinea-ink-muted)]">
                          {line.description}
                        </TD>
                        <TD className="text-right font-mono text-xs tabular-nums">
                          {formatMoney(Number(line.base_amount), baseLike, { showCode: false })}
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              )}
            </div>
          </DrawerContent>
        )}
      </Drawer>
    </ReportPage>
  );
}
