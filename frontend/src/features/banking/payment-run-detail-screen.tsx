"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Download } from "lucide-react";
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
import { useCompanyDetails } from "@/features/gl/hooks";
import { downloadFromApi } from "@/lib/api";
import { PaymentRunStatus } from "@/lib/api-enums";
import { dotted, formatDate, formatQuantity } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { useAccountMoney } from "./account-picker";
import { useBankAccounts, usePaymentRun, useRemittances, useReversePaymentRun } from "./hooks";

/**
 * One payment run — `/ap/payment-runs/{id}` (P8 decision 7).
 *
 * **The lines**, each an invoice paid, with the `PMT-` and the `ALC-` the run produced for its
 * supplier — ordinary P4 documents, so each links to the AP document it is. **Instruction file**
 * downloads the run's CSV for the bank portal (a supplier without bank details is in it with the
 * account fields empty). **Remittance advices** are the run's `remittance_pdf` jobs, one per
 * supplier; they run after the post's response, so the list is polled until each has its PDF.
 *
 * **Reverse** asks for a reason and undoes the whole run in one transaction — every allocation,
 * then every settlement, then the match holding the bank's line. It is refused while that match
 * sits inside a locked reconciliation (`reconciliation_locked`), and the screen says which one
 * before the button rather than after the press.
 */
export function PaymentRunDetailScreen({ runId }: { runId: number }) {
  const t = useTranslations("banking.paymentRunDetail");
  const tr = useTranslations("banking.paymentRuns");
  const tc = useTranslations("banking.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canPost = useHasPermission()("bank:payment_run_post");
  const company = useCompanyDetails();

  const run = usePaymentRun(runId);
  const data = run.data;
  const accounts = useBankAccounts({ includeInactive: true });
  const account = (accounts.data ?? []).find((row) => row.id === data?.bank_account_id) ?? null;
  const { money } = useAccountMoney(account);
  const remittances = useRemittances(runId, canPost && data !== undefined);
  const reverse = useReversePaymentRun();

  const [reverseOpen, setReverseOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [reverseDate, setReverseDate] = useState("");
  const [reverseError, setReverseError] = useState<string | null>(null);

  if (!data) {
    return (
      <ReportPage title={tr("title")} companyName={company.data?.name} backHref="/ap/payment-runs">
        <QueryState query={run} isEmpty empty={t("notFound")} testId="payment-run" />
      </ReportPage>
    );
  }

  const reversed = data.status === PaymentRunStatus.REVERSED;
  const partnerName = new Map(data.lines.map((line) => [line.partner_id, line.partner_name]));

  /** Why Reverse cannot be pressed, or `null` — the service's refusals, in its order, read off
   * the run before the button is drawn. */
  function reverseBlockedReason(): string | null {
    if (!data) return null;
    if (reversed) return t("alreadyReversed");
    if (data.reconciliation_locked) return t("reconciliationLocked", { number: data.reconciliation_locked });
    return null;
  }

  async function handleReverse() {
    setReverseError(null);
    try {
      const result = await reverse.mutateAsync({ runId, reason, onDate: reverseDate || null });
      setReverseOpen(false);
      toast.show({ title: t("reversed", { number: result.number }), tone: "success" });
    } catch (err) {
      if (isApiError(err)) setReverseError(err.message);
      else showApiError(err, t("reverseFailed"));
    }
  }

  async function download(path: string, fallback: string) {
    try {
      await downloadFromApi(path, fallback);
    } catch (err) {
      showApiError(err, t("downloadFailed"));
    }
  }

  const blocked = reverseBlockedReason();
  const cashTotal = data.lines.reduce((sum, line) => sum + Number(line.amount) - Number(line.discount_amount), 0);

  return (
    <ReportPage
      title={t("heading", { number: data.number })}
      subtitle={account ? dotted(account.code, account.name) : undefined}
      companyName={company.data?.name}
      asOfLabel={formatDate(data.payment_date)}
      backHref={`/ap/payment-runs?account=${data.bank_account_id}`}
      filters={
        canPost ? (
          <div className="flex flex-wrap items-center justify-end gap-3">
            <Button
              variant="secondary"
              className="gap-1.5"
              onClick={() => download(`/banking/payment-runs/${runId}/instruction.csv`, `${data.number}-instruction.csv`)}
            >
              <Download className="size-3.5" /> {t("instructionFile")}
            </Button>
            {blocked ? (
              <p className="text-xs text-[var(--vinea-ink-muted)]" data-testid="reverse-run-blocked">
                {blocked}
              </p>
            ) : null}
            <Button
              variant="danger"
              disabled={blocked !== null}
              onClick={() => {
                setReverseError(null);
                setReason("");
                setReverseDate(data.payment_date);
                setReverseOpen(true);
              }}
            >
              {t("reverse")}
            </Button>
          </div>
        ) : undefined
      }
    >
      <ReportPanel>
        <dl className="grid grid-cols-2 gap-4 text-xs sm:grid-cols-3 lg:grid-cols-6" data-testid="payment-run-facts">
          <div>
            <dt className="text-[var(--vinea-ink-subtle)]">{tc("status")}</dt>
            <dd>
              <StatusChip tone={reversed ? "neutral" : "success"}>{tr(`statusLabel.${data.status}`)}</StatusChip>
            </dd>
          </div>
          <div>
            <dt className="text-[var(--vinea-ink-subtle)]">{tr("paymentDate")}</dt>
            <dd className="text-[var(--vinea-ink)]">{formatDate(data.payment_date)}</dd>
          </div>
          <div>
            <dt className="text-[var(--vinea-ink-subtle)]">{t("reference")}</dt>
            <dd className="whitespace-nowrap font-mono text-[var(--vinea-ink)]">{data.reference}</dd>
          </div>
          <div>
            <dt className="text-[var(--vinea-ink-subtle)]">{tr("suppliers")}</dt>
            <dd className="font-mono tabular-nums text-[var(--vinea-ink)]" data-testid="run-supplier-count">
              {formatQuantity(data.supplier_count, 0)}
            </dd>
          </div>
          <div>
            <dt className="text-[var(--vinea-ink-subtle)]">{t("invoices")}</dt>
            <dd className="font-mono tabular-nums text-[var(--vinea-ink)]" data-testid="run-invoice-count">
              {formatQuantity(data.lines.length, 0)}
            </dd>
          </div>
          <div>
            <dt className="text-[var(--vinea-ink-subtle)]">{tr("total")}</dt>
            <dd className="whitespace-nowrap font-mono tabular-nums text-[var(--vinea-ink)]" data-testid="run-total">
              {money(data.total)}
            </dd>
          </div>
        </dl>
        {reversed && data.reversal_reason ? (
          <p className="pt-3 text-xs text-[var(--vinea-ink-muted)]" data-testid="run-reversal-reason">
            {t("reversedBecause", { reason: data.reversal_reason })}
          </p>
        ) : null}
      </ReportPanel>

      <ReportPanel>
        <h2 className="pb-2 text-xs font-semibold text-[var(--vinea-ink-muted)]">{t("lines")}</h2>
        <Table>
          <THead>
            <TR>
              <TH>{t("supplier")}</TH>
              <TH className="w-32">{t("invoice")}</TH>
              <TH className="w-32">{t("payment")}</TH>
              <TH className="w-32">{t("allocation")}</TH>
              <TH className="text-right">{t("amount")}</TH>
              <TH className="text-right">{t("discount")}</TH>
              <TH className="text-right">{t("cash")}</TH>
            </TR>
          </THead>
          <TBody>
            {data.lines.map((line) => (
              <TR key={line.id} data-run-line={line.document_number}>
                <TD className="text-xs">{dotted(line.supplier_code, line.partner_name)}</TD>
                <TD className="font-mono text-xs">
                  <Link href={`/ap/documents/${line.document_id}`} className="whitespace-nowrap text-[var(--vinea-brand)] underline">
                    {line.document_number}
                  </Link>
                </TD>
                <TD className="font-mono text-xs">
                  {line.settlement_document_id !== null && line.settlement_number ? (
                    <Link
                      href={`/ap/documents/${line.settlement_document_id}`}
                      className="whitespace-nowrap text-[var(--vinea-brand)] underline"
                    >
                      {line.settlement_number}
                    </Link>
                  ) : (
                    tc("emptyValue")
                  )}
                </TD>
                <TD className="font-mono text-xs">
                  {/* An allocation has no page of its own; it is listed on both documents it
                      joins, so the link lands on the invoice it settled. */}
                  {line.allocation_number ? (
                    <Link href={`/ap/documents/${line.document_id}`} className="whitespace-nowrap text-[var(--vinea-brand)] underline">
                      {line.allocation_number}
                    </Link>
                  ) : (
                    tc("emptyValue")
                  )}
                </TD>
                <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">{money(line.amount)}</TD>
                <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">
                  {Number(line.discount_amount) === 0 ? tc("emptyValue") : money(line.discount_amount)}
                </TD>
                <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">
                  {money(Number(line.amount) - Number(line.discount_amount))}
                </TD>
              </TR>
            ))}
            <TR>
              <TD colSpan={6} className="text-right text-xs font-semibold">
                {t("cashTotal")}
              </TD>
              <TD className="text-right font-mono text-xs font-semibold tabular-nums whitespace-nowrap" data-testid="run-cash-total">
                {money(cashTotal)}
              </TD>
            </TR>
          </TBody>
        </Table>
      </ReportPanel>

      {canPost ? (
        <ReportPanel>
          <h2 className="pb-2 text-xs font-semibold text-[var(--vinea-ink-muted)]">{t("remittances")}</h2>
          <p className="pb-3 text-xs text-[var(--vinea-ink-subtle)]">{t("remittancesNote")}</p>
          {(remittances.data ?? []).length === 0 ? (
            <QueryState query={remittances} isEmpty empty={t("noRemittances")} testId="remittances" />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>{t("supplier")}</TH>
                  <TH className="w-28">{tc("status")}</TH>
                  <TH>{t("advice")}</TH>
                  <TH className="w-32" />
                </TR>
              </THead>
              <TBody>
                {(remittances.data ?? []).map((job) => {
                  const name = partnerName.get(job.params?.partner_id ?? 0) ?? tc("emptyValue");
                  return (
                    <TR key={job.id} data-remittance={name}>
                      <TD className="text-xs">{name}</TD>
                      <TD>
                        <StatusChip
                          tone={job.status === "succeeded" ? "success" : job.status === "failed" ? "danger" : "info"}
                        >
                          {t(`jobStatus.${job.status}`)}
                        </StatusChip>
                      </TD>
                      <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                        {job.status === "failed" ? job.error : (job.artifact_name ?? tc("emptyValue"))}
                      </TD>
                      <TD>
                        <Button
                          variant="ghost"
                          disabled={job.status !== "succeeded"}
                          aria-label={t("downloadAdvice", { supplier: name })}
                          onClick={() =>
                            download(
                              `/banking/payment-runs/${runId}/remittances/${job.id}`,
                              job.artifact_name ?? `${data.number}-remittance.pdf`,
                            )
                          }
                          className="gap-1.5"
                        >
                          <Download className="size-3.5" /> {t("download")}
                        </Button>
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          )}
        </ReportPanel>
      ) : null}

      <Dialog open={reverseOpen} onOpenChange={setReverseOpen}>
        <DialogContent title={t("reverseTitle", { number: data.number })} description={t("reverseDescription")}>
          <div className="space-y-3">
            <Field label={t("reversalDate")}>
              <IsoDatePicker value={reverseDate} onValueChange={setReverseDate} />
            </Field>
            <Field label={t("reason")}>
              <Input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder={t("reasonPlaceholder")}
              />
            </Field>
            {reverseError ? (
              <p
                data-testid="reverse-run-error"
                className="rounded-[var(--radius-control)] bg-[var(--vinea-danger-soft)] px-3 py-2 text-sm text-[var(--vinea-danger)]"
              >
                {reverseError}
              </p>
            ) : null}
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setReverseOpen(false)}>
              {tc("cancel")}
            </Button>
            <Button
              variant="danger"
              disabled={reason.trim().length < 3 || reverse.isPending}
              onClick={handleReverse}
            >
              {t("confirmReverse")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </ReportPage>
  );
}
