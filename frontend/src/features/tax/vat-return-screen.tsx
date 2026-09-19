"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ExternalLink } from "lucide-react";
import { Button } from "@/design/components/button";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { QueryState } from "@/design/components/query-state";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { isApiError, useHasPermission } from "@/features/auth/hooks";
import { useCompanyDetails, useCurrencies } from "@/features/gl/hooks";
import { VatReturnStatus } from "@/lib/api-enums";
import { newDraftId } from "@/lib/drafts";
import { formatDate, formatMoney, todayIso } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import {
  useFileVatReturn,
  useReverseVatReturn,
  useVatReturn,
  useVatReturnPreview,
  useVatReturns,
} from "./hooks";

/** The first and last day of the month `today` falls in — the range a VAT return is nearly
 * always for, offered so nobody keys two dates to get the obvious one. */
function monthOf(today: string): { from: string; to: string } {
  const [year, month] = today.split("-").map(Number);
  const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const mm = String(month).padStart(2, "0");
  return { from: `${year}-${mm}-01`, to: `${year}-${mm}-${String(last).padStart(2, "0")}` };
}

/**
 * **VAT return** — Transactions → Tax.
 *
 * The return over a range, the tie that makes it believable, and the act of filing it.
 *
 * **The tie is not decoration and is not collapsible.** Decision 12: the return is a
 * *reconciliation*, not a total. For each VAT account it shows the account's movement over the
 * range, the Σ of the tax lines the return declares, and every journal line making up the
 * difference — a VAT payment to RRA, a manual journal keyed without a tax code. A return that
 * showed only the sections would be the P4 defect again: a figure on a screen with nothing
 * behind it. So an unreconciled account is a warning chip and its untagged lines are listed
 * where the difference is shown, rather than being somewhere else for somebody to go and find.
 *
 * **Late entries** are the other half of "a filed return never changes". A filed return records
 * a high-water journal-entry id; an entry posted into its range afterwards has a higher id, so
 * it lands here, on the *next* return, under its own heading and with the number of the return
 * it was too late for. Nothing amends a filed return, which is why there is no amend button.
 *
 * **Filing** posts the settlement entry through the kernel (`VatReturnPosted`) and freezes the
 * figures. It is refused over a range that overlaps a posted return (`vat_period_filed`) —
 * reverse that one first, which is what Reverse on the detail is for.
 */
export function VatReturnScreen() {
  const t = useTranslations("tax.vatReturn");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canFile = useHasPermission()("tax:vat_return_file");

  const defaults = monthOf(todayIso());
  const [periodFrom, setPeriodFrom] = useState(defaults.from);
  const [periodTo, setPeriodTo] = useState(defaults.to);
  const [fileOpen, setFileOpen] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const [openReturnId, setOpenReturnId] = useState<number | null>(null);
  const [reverseReason, setReverseReason] = useState("");
  const [reverseError, setReverseError] = useState<string | null>(null);

  const company = useCompanyDetails();
  const currencies = useCurrencies();
  const base = (currencies.data ?? []).find((c) => c.is_base);
  const baseLike = {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };
  const money = (value: string | number) => formatMoney(Number(value), baseLike, { showCode: false });

  const preview = useVatReturnPreview(periodFrom, periodTo);
  const returns = useVatReturns();
  const detail = useVatReturn(openReturnId);
  const file = useFileVatReturn();
  const reverse = useReverseVatReturn();

  async function handleFile() {
    setFileError(null);
    try {
      const filed = await file.mutateAsync({
        periodFrom,
        periodTo,
        idempotencyKey: newDraftId(),
      });
      setFileOpen(false);
      toast.show({ title: t("filed", { number: filed.number }), tone: "success" });
    } catch (err) {
      if (isApiError(err)) setFileError(err.message);
      else showApiError(err, t("fileFailed"));
    }
  }

  async function handleReverse() {
    if (openReturnId === null) return;
    setReverseError(null);
    try {
      const reversed = await reverse.mutateAsync({
        returnId: openReturnId,
        reason: reverseReason,
      });
      setOpenReturnId(null);
      setReverseReason("");
      toast.show({ title: t("reversed", { number: reversed.number }), tone: "success" });
    } catch (err) {
      if (isApiError(err)) setReverseError(err.message);
      else showApiError(err, t("reverseFailed"));
    }
  }

  const sections = preview.data?.sections;
  const rows: Array<[string, string]> = sections
    ? [
        [t("salesStandardBase"), money(sections.sales_standard_base)],
        [t("salesStandardVat"), money(sections.sales_standard_vat)],
        [t("salesZeroRated"), money(sections.sales_zero_rated_base)],
        [t("salesExempt"), money(sections.sales_exempt_base)],
        [t("purchasesStandardBase"), money(sections.purchases_standard_base)],
        [t("purchasesStandardVat"), money(sections.purchases_standard_vat)],
        [t("purchasesImportsBase"), money(sections.purchases_imports_base)],
        [t("purchasesImportsVat"), money(sections.purchases_imports_vat)],
        [t("purchasesZeroRated"), money(sections.purchases_zero_rated_base)],
        [t("purchasesExempt"), money(sections.purchases_exempt_base)],
      ]
    : [];

  return (
    <ReportPage
      title={t("title")}
      subtitle={t("subtitle")}
      companyName={company.data?.name}
      asOfLabel={t("range", { from: formatDate(periodFrom), to: formatDate(periodTo) })}
      filters={
        <div className="flex flex-wrap items-end gap-3">
          <Field label={t("periodFrom")} className="w-44">
            <IsoDatePicker value={periodFrom} onValueChange={setPeriodFrom} />
          </Field>
          <Field label={t("periodTo")} className="w-44">
            <IsoDatePicker value={periodTo} onValueChange={setPeriodTo} />
          </Field>
          <Button
            variant="primary"
            disabled={!canFile || !preview.data}
            data-testid="file-return"
            onClick={() => {
              setFileError(null);
              setFileOpen(true);
            }}
          >
            {t("file")}
          </Button>
        </div>
      }
    >
      <ReportPanel>
        <h2 className="pb-2 text-xs font-semibold text-[var(--vinea-ink-muted)]">
          {t("sections")}
        </h2>
        {!sections ? (
          <QueryState query={preview} isEmpty empty={t("noFigures")} testId="vat-sections" />
        ) : (
          <>
            <Table>
              <THead>
                <TR>
                  <TH>{t("line")}</TH>
                  <TH className="w-40 text-right">{t("amount")}</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map(([label, value]) => (
                  <TR key={label}>
                    <TD className="text-xs">{label}</TD>
                    <TD className="text-right font-mono text-xs tabular-nums">{value}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <dl className="grid grid-cols-3 gap-4 pt-4">
              <div>
                <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("outputVat")}</dt>
                <dd
                  className="font-mono text-sm tabular-nums text-[var(--vinea-ink)]"
                  data-testid="vat-output"
                >
                  {money(sections.output_vat)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("inputVat")}</dt>
                <dd
                  className="font-mono text-sm tabular-nums text-[var(--vinea-ink)]"
                  data-testid="vat-input"
                >
                  {money(sections.input_vat)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-[var(--vinea-ink-muted)]">
                  {Number(sections.net_payable) < 0 ? t("netCredit") : t("netPayable")}
                </dt>
                <dd
                  className="font-mono text-sm font-semibold tabular-nums text-[var(--vinea-ink)]"
                  data-testid="vat-net"
                >
                  {money(sections.net_payable)}
                </dd>
              </div>
            </dl>
          </>
        )}
      </ReportPanel>

      {/* The tie. Not "balanced" — *reconciled*, and it says which. */}
      <ReportPanel>
        <h2 className="pb-2 text-xs font-semibold text-[var(--vinea-ink-muted)]">{t("tie")}</h2>
        <p className="pb-3 text-xs text-[var(--vinea-ink-subtle)]">{t("tieNote")}</p>
        {(preview.data?.ties ?? []).map((tie) => (
          <div key={tie.account_id} className="pb-4">
            <div className="flex items-center justify-between">
              <p className="text-sm text-[var(--vinea-ink)]">
                {t("account", { code: tie.code, name: tie.name })}
              </p>
              <StatusChip tone={tie.reconciled ? "success" : "warning"}>
                <span data-testid={`tie-${tie.code}`}>
                  {tie.reconciled ? t("reconciled") : t("unreconciled")}
                </span>
              </StatusChip>
            </div>
            <dl className="grid grid-cols-4 gap-3 pt-2 text-xs">
              <Figure label={t("movement")} value={money(tie.movement)} testId={`movement-${tie.code}`} />
              <Figure label={t("declared")} value={money(tie.declared_in_range)} />
              <Figure label={t("lateTotal")} value={money(tie.late_total)} />
              <Figure label={t("difference")} value={money(tie.difference)} />
            </dl>
            {tie.untagged.length > 0 && (
              <div className="pt-2">
                <p className="pb-1 text-xs text-[var(--vinea-ink-muted)]">{t("untagged")}</p>
                <Table>
                  <THead>
                    <TR>
                      <TH className="w-32">{t("entry")}</TH>
                      <TH className="w-28">{t("entryDate")}</TH>
                      <TH>{t("description")}</TH>
                      <TH className="w-32 text-right">{t("amount")}</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {tie.untagged.map((line) => (
                      <TR key={line.line_id}>
                        <TD className="font-mono text-xs">
                          <Link
                            href={`/gl/entries/${line.entry_id}`}
                            className="inline-flex items-center gap-1 font-semibold text-[var(--vinea-brand)] underline"
                          >
                            {line.entry_number}
                            <ExternalLink className="size-3" />
                          </Link>
                        </TD>
                        <TD className="text-xs text-[var(--vinea-ink-muted)]">
                          {formatDate(line.entry_date)}
                        </TD>
                        <TD className="text-xs">{line.description ?? ""}</TD>
                        <TD className="text-right font-mono text-xs tabular-nums">
                          {money(line.base_amount)}
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </div>
            )}
          </div>
        ))}
      </ReportPanel>

      {(preview.data?.late_entries.length ?? 0) > 0 && (
        <ReportPanel>
          <h2 className="pb-2 text-xs font-semibold text-[var(--vinea-ink-muted)]">
            {t("lateEntries")}
          </h2>
          <p className="pb-3 text-xs text-[var(--vinea-ink-subtle)]">{t("lateEntriesNote")}</p>
          <Table>
            <THead>
              <TR>
                <TH className="w-32">{t("entry")}</TH>
                <TH className="w-28">{t("entryDate")}</TH>
                <TH className="w-32">{t("filedReturn")}</TH>
                <TH className="w-28">{t("taxCode")}</TH>
                <TH className="w-32 text-right">{t("base")}</TH>
                <TH className="w-32 text-right">{t("tax")}</TH>
              </TR>
            </THead>
            <TBody>
              {(preview.data?.late_entries ?? []).map((late) => (
                <TR key={`${late.entry_id}-${late.code}-${late.side}`}>
                  <TD className="font-mono text-xs">{late.entry_number}</TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">
                    {formatDate(late.entry_date)}
                  </TD>
                  <TD className="font-mono text-xs">{late.filed_return_number}</TD>
                  <TD className="font-mono text-xs">{late.code}</TD>
                  <TD className="text-right font-mono text-xs tabular-nums" data-testid="late-base">
                    {money(late.base)}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums">{money(late.tax)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </ReportPanel>
      )}

      <ReportPanel>
        <h2 className="pb-2 text-xs font-semibold text-[var(--vinea-ink-muted)]">{t("filedReturns")}</h2>
        {(returns.data ?? []).length === 0 ? (
          <QueryState query={returns} isEmpty empty={t("noneFiled")} testId="filed-returns" />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-32">{t("number")}</TH>
                <TH className="w-48">{t("period")}</TH>
                <TH className="w-32 text-right">{t("outputVat")}</TH>
                <TH className="w-32 text-right">{t("inputVat")}</TH>
                <TH className="w-32 text-right">{t("netPayable")}</TH>
                <TH className="w-28">{t("status")}</TH>
                <TH className="w-28 print:hidden" />
              </TR>
            </THead>
            <TBody>
              {(returns.data ?? []).map((row) => (
                <TR key={row.id}>
                  <TD className="font-mono text-xs font-semibold">{row.number}</TD>
                  <TD className="text-xs">
                    {t("range", {
                      from: formatDate(row.period_from),
                      to: formatDate(row.period_to),
                    })}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {money(row.output_vat)}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {money(row.input_vat)}
                  </TD>
                  <TD
                    className="text-right font-mono text-xs tabular-nums"
                    data-testid={`filed-net-${row.number}`}
                  >
                    {money(row.net_payable)}
                  </TD>
                  <TD>
                    <StatusChip
                      tone={row.status === VatReturnStatus.POSTED ? "success" : "neutral"}
                    >
                      {t(`statusLabel.${row.status}`)}
                    </StatusChip>
                  </TD>
                  <TD className="print:hidden">
                    <Button
                      variant="ghost"
                      data-testid={`open-return-${row.id}`}
                      onClick={() => {
                        setReverseError(null);
                        setReverseReason("");
                        setOpenReturnId(row.id);
                      }}
                    >
                      {t("open")}
                    </Button>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </ReportPanel>

      <Dialog open={fileOpen} onOpenChange={setFileOpen}>
        <DialogContent title={t("fileTitle")} description={t("fileDescription")}>
          {fileError && (
            <p
              data-testid="file-error"
              className="mb-3 rounded-[var(--radius-control)] bg-[var(--vinea-danger-soft)] px-3 py-2 text-sm text-[var(--vinea-danger)]"
            >
              {fileError}
            </p>
          )}
          <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("fileNote")}</p>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setFileOpen(false)}>
              {t("cancel")}
            </Button>
            <Button
              variant="primary"
              disabled={file.isPending}
              data-testid="confirm-file"
              onClick={handleFile}
            >
              {t("confirmFile")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* The filed return, rendered from the snapshot it was filed on rather than from a
          recomputation — which is what makes "a filed return never changes" something an
          accountant can see rather than something the tests assert. */}
      <Dialog open={openReturnId !== null} onOpenChange={(open) => !open && setOpenReturnId(null)}>
        <DialogContent title={t("detailTitle")} description={t("detailDescription")}>
          {detail.data && (
            <div className="max-h-[60vh] space-y-3 overflow-auto">
              <dl className="grid grid-cols-3 gap-3 text-xs">
                <Figure
                  label={t("outputVat")}
                  value={money(detail.data.figures.sections.output_vat)}
                  testId="detail-output"
                />
                <Figure
                  label={t("inputVat")}
                  value={money(detail.data.figures.sections.input_vat)}
                />
                <Figure
                  label={t("netPayable")}
                  value={money(detail.data.figures.sections.net_payable)}
                />
              </dl>
              <p className="text-xs text-[var(--vinea-ink-muted)]">
                {t("highWater", { value: detail.data.high_water_entry_id })}
              </p>
              {detail.data.journal_entry_id !== null && (
                <Link
                  href={`/gl/entries/${detail.data.journal_entry_id}`}
                  className="inline-flex items-center gap-1 font-mono text-xs text-[var(--vinea-brand)] underline"
                >
                  {t("settlementEntry")}
                  <ExternalLink className="size-3" />
                </Link>
              )}
              {reverseError && (
                <p
                  data-testid="reverse-error"
                  className="rounded-[var(--radius-control)] bg-[var(--vinea-danger-soft)] px-3 py-2 text-sm text-[var(--vinea-danger)]"
                >
                  {reverseError}
                </p>
              )}
              {detail.data.status === VatReturnStatus.POSTED && (
                <Field label={t("reverseReason")}>
                  <Input
                    value={reverseReason}
                    onChange={(e) => setReverseReason(e.target.value)}
                    data-testid="vat-reverse-reason"
                    placeholder={t("reverseReasonPlaceholder")}
                  />
                </Field>
              )}
            </div>
          )}
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpenReturnId(null)}>
              {t("close")}
            </Button>
            {detail.data?.status === VatReturnStatus.POSTED && (
              <Button
                variant="danger"
                disabled={!canFile || reverseReason.length < 3 || reverse.isPending}
                data-testid="confirm-vat-reverse"
                onClick={handleReverse}
              >
                {t("reverse")}
              </Button>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </ReportPage>
  );
}

function Figure({ label, value, testId }: { label: string; value: string; testId?: string }) {
  return (
    <div>
      <dt className="text-[var(--vinea-ink-muted)]">{label}</dt>
      <dd className="font-mono tabular-nums text-[var(--vinea-ink)]" data-testid={testId}>
        {value}
      </dd>
    </div>
  );
}
