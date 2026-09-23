"use client";

import { Fragment, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Field, Input } from "@/design/components/input";
import { QueryState } from "@/design/components/query-state";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { isApiError, useHasPermission } from "@/features/auth/hooks";
import { useCompanyDetails } from "@/features/gl/hooks";
import { newDraftId } from "@/lib/drafts";
import { dotted, formatDate, formatQuantity, todayIso } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { useAccountMoney, useBankAccountChoice } from "./account-picker";
import { usePostPaymentRun, usePreviewPaymentRun, useSelectableDocuments } from "./hooks";
import type { PaymentRunPayload, PaymentRunPreview, PaymentRunPreviewSupplier, SelectableDocument } from "./types";

/** One selected invoice. `amount` empty is "all that is open" — the server's `null`. */
interface Pick {
  amount: string;
  takeDiscount: boolean;
}

/** `open_credits: PMT-000004, RTS-000001` → the numbers. The server writes the warning in that
 * shape (`payment_runs._warnings`); the screen says it in words and names the documents. */
function openCredits(warnings: string[]): string | null {
  const warning = warnings.find((w) => w.startsWith("open_credits:"));
  return warning ? warning.slice("open_credits:".length).trim() : null;
}

/**
 * **New payment run** — `/ap/payment-runs/new` (P8 decision 7).
 *
 * The bank account (bank kind only), the payment date and a due-by filter choose which open
 * supplier invoices are offered; the grid is **supplier × invoice**, each with its open amount,
 * `discount_available` at the payment date and a *take discount* toggle, and an amount for a
 * partial payment (empty pays all that is open). `payment_exceeds_open` is said on the row, before
 * the preview and again if the server refuses it there.
 *
 * **Preview → Post**, P7's shape: there are no draft runs. The preview writes nothing and shows
 * what each supplier will be paid, the discount taken, the total that leaves the bank and the two
 * warnings — `bank_details_missing` (the payment still posts; the instruction file carries the row
 * with the account fields empty) and **open credits named**, never netted: netting a credit into a
 * payment is P4's Allocate screen's job. Post is offered only for the selection that was
 * previewed, under an `Idempotency-Key` minted with that preview, so a retried press replays the
 * run it made rather than paying twice.
 */
export function NewPaymentRunScreen({ requestedAccountId }: { requestedAccountId: number | null }) {
  const t = useTranslations("banking.newPaymentRun");
  const tr = useTranslations("banking.paymentRuns");
  const tc = useTranslations("banking.common");
  const router = useRouter();
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canPost = useHasPermission()("bank:payment_run_post");
  const company = useCompanyDetails();

  const { accounts, banks, selected: requested } = useBankAccountChoice(requestedAccountId);
  const [accountId, setAccountId] = useState<number | null>(null);
  const account = banks.find((row) => row.id === accountId) ?? requested;
  const { money } = useAccountMoney(account);

  const [paymentDate, setPaymentDate] = useState(todayIso);
  const [dueBy, setDueBy] = useState("");
  const selectable = useSelectableDocuments(canPost ? (account?.id ?? null) : null, {
    dueBy,
    on: paymentDate,
  });
  const rows = useMemo(() => selectable.data ?? [], [selectable.data]);

  const [picks, setPicks] = useState<Map<number, Pick>>(new Map());
  const preview = usePreviewPaymentRun();
  const post = usePostPaymentRun();
  const [result, setResult] = useState<PaymentRunPreview | null>(null);
  const [previewedPayload, setPreviewedPayload] = useState<string | null>(null);
  const [postKey, setPostKey] = useState(() => newDraftId());
  const [error, setError] = useState<string | null>(null);
  /** The server's refusal on a line, by document — `lines.N.amount` mapped back to its row. */
  const [lineErrors, setLineErrors] = useState<Map<number, string>>(new Map());

  const bySupplier = useMemo(() => {
    const groups = new Map<number, SelectableDocument[]>();
    for (const row of rows) groups.set(row.partner_id, [...(groups.get(row.partner_id) ?? []), row]);
    return [...groups.values()];
  }, [rows]);

  function reset() {
    setPicks(new Map());
    setResult(null);
    setPreviewedPayload(null);
    setError(null);
    setLineErrors(new Map());
  }

  function toggle(row: SelectableDocument) {
    setPicks((current) => {
      const next = new Map(current);
      if (next.has(row.document_id)) next.delete(row.document_id);
      else next.set(row.document_id, { amount: "", takeDiscount: Number(row.discount_available) > 0 });
      return next;
    });
  }

  function update(documentId: number, patch: Partial<Pick>) {
    setPicks((current) => {
      const next = new Map(current);
      const pick = next.get(documentId);
      if (pick) next.set(documentId, { ...pick, ...patch });
      return next;
    });
    setLineErrors((current) => {
      const next = new Map(current);
      next.delete(documentId);
      return next;
    });
  }

  /** In the grid's order, so `lines.N` on a refusal is the Nth selected row. */
  const selectedRows = rows.filter((row) => picks.has(row.document_id));
  const payload: PaymentRunPayload | null = account
    ? {
        bank_account_id: account.id,
        payment_date: paymentDate,
        lines: selectedRows.map((row) => {
          const pick = picks.get(row.document_id)!;
          return {
            document_id: row.document_id,
            amount: pick.amount.trim() === "" ? null : pick.amount.trim(),
            take_discount: pick.takeDiscount,
          };
        }),
      }
    : null;
  const payloadKey = payload ? JSON.stringify(payload) : null;

  /** `payment_exceeds_open`, said on the row as it is typed — the service's refusal known
   * before any press. Compared as numbers: the wire's `NUMERIC(20,6)` strings are not. */
  function exceedsOpen(row: SelectableDocument): boolean {
    const amount = picks.get(row.document_id)?.amount.trim() ?? "";
    return amount !== "" && Number(amount) > Number(row.open_amount);
  }

  function previewBlockedReason(): string | null {
    if (!account) return t("chooseAccount");
    if (selectedRows.length === 0) return t("selectFirst");
    return null;
  }

  function postBlockedReason(): string | null {
    if (!canPost) return t("noPermission");
    if (!result || previewedPayload === null) return t("previewFirst");
    if (previewedPayload !== payloadKey) return t("selectionChanged");
    return null;
  }

  function takeLineErrors(fieldErrors: Record<string, string[]>) {
    const mapped = new Map<number, string>();
    for (const [key, messages] of Object.entries(fieldErrors)) {
      const match = /^lines\.(\d+)\./.exec(key);
      const row = match ? selectedRows[Number(match[1])] : undefined;
      if (row) mapped.set(row.document_id, messages[0]);
    }
    setLineErrors(mapped);
  }

  async function handlePreview() {
    if (!payload || previewBlockedReason()) return;
    setError(null);
    setLineErrors(new Map());
    try {
      const previewed = await preview.mutateAsync(payload);
      setResult(previewed);
      setPreviewedPayload(payloadKey);
      // A fresh key per preview: the post it offers is *this* selection, and a key is never
      // offered for a second request body (`idempotency_key_reused`).
      setPostKey(newDraftId());
    } catch (err) {
      setResult(null);
      setPreviewedPayload(null);
      if (isApiError(err)) {
        takeLineErrors(err.fieldErrors);
        setError(err.message);
      } else showApiError(err, t("previewFailed"));
    }
  }

  async function handlePost() {
    if (!payload || postBlockedReason()) return;
    setError(null);
    try {
      const run = await post.mutateAsync({ payload, idempotencyKey: postKey });
      toast.show({ title: t("posted", { number: run.number }), tone: "success" });
      router.push(`/ap/payment-runs/${run.id}`);
    } catch (err) {
      if (isApiError(err)) {
        takeLineErrors(err.fieldErrors);
        setError(err.message);
      } else showApiError(err, t("postFailed"));
    }
  }

  const previewBlocked = previewBlockedReason();
  const postBlocked = postBlockedReason();
  const invoiceCount = result ? result.suppliers.reduce((sum, s) => sum + s.lines.length, 0) : 0;

  return (
    <ReportPage
      title={t("title")}
      subtitle={t("subtitle")}
      companyName={company.data?.name}
      backHref={`/ap/payment-runs${account ? `?account=${account.id}` : ""}`}
      asOfLabel={formatDate(paymentDate)}
      filters={
        <div className="flex flex-wrap items-end gap-3">
          <Field label={tc("bankAccount")} className="w-72">
            <Combobox
              options={banks.map((row) => ({ value: String(row.id), label: dotted(row.code, row.name) }))}
              value={account ? String(account.id) : ""}
              onValueChange={(value) => {
                setAccountId(Number(value));
                reset();
              }}
              placeholder={tc("chooseBankAccount")}
            />
          </Field>
          <Field label={t("paymentDate")} className="w-44">
            <IsoDatePicker value={paymentDate} onValueChange={setPaymentDate} />
          </Field>
          <Field label={t("dueBy")} className="w-44">
            <IsoDatePicker value={dueBy} onValueChange={setDueBy} placeholder={t("anyDueDate")} />
          </Field>
          {dueBy ? (
            <Button variant="ghost" onClick={() => setDueBy("")}>
              {t("clearDueBy")}
            </Button>
          ) : null}
        </div>
      }
    >
      {!canPost ? (
        <ReportPanel>
          <p className="text-sm text-[var(--vinea-ink-muted)]" data-testid="payment-run-no-permission">
            {t("noPermission")}
          </p>
        </ReportPanel>
      ) : (
        <ReportPanel>
          <div className="flex items-center justify-between pb-2">
            <h2 className="text-xs font-semibold text-[var(--vinea-ink-muted)]">{t("selection")}</h2>
            <p className="text-xs text-[var(--vinea-ink-muted)]" data-testid="selection-count">
              {t("selectedCount", {
                selected: formatQuantity(selectedRows.length, 0),
                offered: formatQuantity(rows.length, 0),
              })}
            </p>
          </div>
          {rows.length === 0 ? (
            <QueryState
              query={account ? selectable : accounts}
              isEmpty
              empty={account ? t("nothingOpen") : tr("noBankAccount")}
              testId="selectable"
            />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH className="w-10" />
                  <TH className="w-32">{t("invoice")}</TH>
                  <TH className="w-28">{t("dueDate")}</TH>
                  <TH className="text-right">{t("openAmount")}</TH>
                  <TH className="text-right">{t("discountAvailable")}</TH>
                  <TH className="w-32">{t("takeDiscount")}</TH>
                  <TH className="w-48">{t("amount")}</TH>
                </TR>
              </THead>
              <TBody>
                {bySupplier.map((group) => (
                  <Fragment key={group[0].partner_id}>
                    <TR data-supplier={group[0].partner_name}>
                      <TD colSpan={7} className="bg-[var(--vinea-surface-sunken)] text-xs font-semibold">
                        {dotted(group[0].supplier_code, group[0].partner_name)}
                      </TD>
                    </TR>
                    {group.map((row) => {
                      const pick = picks.get(row.document_id);
                      const noDiscount = Number(row.discount_available) === 0;
                      const rowError = lineErrors.get(row.document_id) ??
                        (exceedsOpen(row) ? t("exceedsOpen", { open: money(row.open_amount) }) : null);
                      return (
                        <TR key={row.document_id} data-invoice={row.number}>
                          <TD>
                            <input
                              type="checkbox"
                              aria-label={t("payInvoice", { number: row.number })}
                              checked={pick !== undefined}
                              onChange={() => toggle(row)}
                              className="size-3.5"
                            />
                          </TD>
                          <TD className="font-mono text-xs font-semibold">
                            <Link href={`/ap/documents/${row.document_id}`} className="text-[var(--vinea-brand)] underline">
                              {row.number}
                            </Link>
                          </TD>
                          <TD className="text-xs">{row.due_date ? formatDate(row.due_date) : tc("emptyValue")}</TD>
                          <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">
                            {money(row.open_amount)}
                          </TD>
                          <TD
                            className="text-right font-mono text-xs tabular-nums whitespace-nowrap"
                            data-testid={`discount-available-${row.number}`}
                          >
                            {noDiscount ? tc("emptyValue") : money(row.discount_available)}
                          </TD>
                          <TD>
                            <label className="flex items-center gap-1.5 text-xs text-[var(--vinea-ink-muted)]">
                              <input
                                type="checkbox"
                                aria-label={t("takeDiscountOn", { number: row.number })}
                                checked={pick?.takeDiscount ?? false}
                                disabled={pick === undefined || noDiscount}
                                onChange={(e) => update(row.document_id, { takeDiscount: e.target.checked })}
                                className="size-3.5"
                              />
                              {t("take")}
                            </label>
                          </TD>
                          <TD>
                            <Input
                              aria-label={t("amountOn", { number: row.number })}
                              value={pick?.amount ?? ""}
                              disabled={pick === undefined}
                              inputMode="decimal"
                              placeholder={t("allOpen")}
                              onChange={(e) => update(row.document_id, { amount: e.target.value })}
                              className="h-8 font-mono"
                            />
                            {rowError ? (
                              <p
                                className="pt-1 text-xs text-[var(--vinea-danger)]"
                                data-testid={`line-error-${row.number}`}
                              >
                                {rowError}
                              </p>
                            ) : null}
                          </TD>
                        </TR>
                      );
                    })}
                  </Fragment>
                ))}
              </TBody>
            </Table>
          )}
          <div className="flex items-center justify-end gap-3 pt-3">
            {previewBlocked ? (
              <p className="text-xs text-[var(--vinea-ink-muted)]" data-testid="preview-blocked">
                {previewBlocked}
              </p>
            ) : null}
            <Button
              variant="secondary"
              disabled={previewBlocked !== null || preview.isPending}
              onClick={handlePreview}
            >
              {t("preview")}
            </Button>
          </div>
        </ReportPanel>
      )}

      {error ? (
        <p
          data-testid="payment-run-error"
          className="rounded-[var(--radius-control)] bg-[var(--vinea-danger-soft)] px-3 py-2 text-sm text-[var(--vinea-danger)]"
        >
          {error}
        </p>
      ) : null}

      {result ? (
        <ReportPanel>
          <div className="flex flex-wrap items-center justify-between gap-3 pb-3" data-testid="payment-run-preview">
            <h2 className="text-xs font-semibold text-[var(--vinea-ink-muted)]">{t("previewTitle")}</h2>
            <p className="text-xs text-[var(--vinea-ink-muted)]" data-testid="preview-counts">
              {t("previewCounts", {
                suppliers: formatQuantity(result.suppliers.length, 0),
                invoices: formatQuantity(invoiceCount, 0),
              })}
            </p>
          </div>
          <div className="space-y-4">
            {result.suppliers.map((supplier) => (
              <PreviewSupplierBlock key={supplier.partner_id} supplier={supplier} money={money} />
            ))}
          </div>
          <dl className="mt-4 flex flex-wrap justify-end gap-6 border-t border-[var(--vinea-border)] pt-3 text-sm">
            <div className="text-right">
              <dt className="text-xs text-[var(--vinea-ink-subtle)]">{t("discountTotal")}</dt>
              <dd className="font-mono tabular-nums" data-testid="preview-discount-total">
                {money(result.discount_total)}
              </dd>
            </div>
            <div className="text-right">
              <dt className="text-xs text-[var(--vinea-ink-subtle)]">{t("runTotal")}</dt>
              <dd className="font-mono font-semibold tabular-nums" data-testid="preview-total">
                {money(result.total)}
              </dd>
            </div>
          </dl>
          <div className="flex items-center justify-end gap-3 pt-3">
            {postBlocked ? (
              <p className="text-xs text-[var(--vinea-ink-muted)]" data-testid="post-blocked">
                {postBlocked}
              </p>
            ) : null}
            <Button variant="primary" disabled={postBlocked !== null || post.isPending} onClick={handlePost}>
              {t("post")}
            </Button>
          </div>
        </ReportPanel>
      ) : null}
    </ReportPage>
  );
}

/** One supplier of the preview: its bank details or the warning that it has none, its open
 * credits named, and what each invoice is paid. */
function PreviewSupplierBlock({
  supplier,
  money,
}: {
  supplier: PaymentRunPreviewSupplier;
  money: (value: string) => string;
}) {
  const t = useTranslations("banking.newPaymentRun");
  const tc = useTranslations("banking.common");
  const missing = supplier.warnings.includes("bank_details_missing");
  const credits = openCredits(supplier.warnings);
  return (
    <section data-preview-supplier={supplier.partner_name} className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-sm font-semibold text-[var(--vinea-ink)]">
          {dotted(supplier.supplier_code, supplier.partner_name)}
        </h3>
        {missing ? (
          <StatusChip tone="warning">{t("bankDetailsMissingChip")}</StatusChip>
        ) : (
          <span className="text-xs text-[var(--vinea-ink-muted)]">
            {dotted(supplier.bank_name, supplier.bank_account_number)}
          </span>
        )}
        <span className="ml-auto font-mono text-sm tabular-nums" data-testid={`supplier-total-${supplier.partner_name}`}>
          {money(supplier.total)}
        </span>
      </div>
      {missing ? (
        <p className="rounded-[var(--radius-control)] bg-[var(--vinea-warning-soft)] px-3 py-2 text-xs text-[var(--vinea-warning)]" data-testid="warning-bank-details-missing">
          {t("bankDetailsMissing")}
        </p>
      ) : null}
      {credits ? (
        <p className="rounded-[var(--radius-control)] bg-[var(--vinea-warning-soft)] px-3 py-2 text-xs text-[var(--vinea-warning)]" data-testid="warning-open-credits">
          {t("openCredits", { documents: credits })}
        </p>
      ) : null}
      <Table>
        <THead>
          <TR>
            <TH className="w-32">{t("invoice")}</TH>
            <TH className="text-right">{t("openAmount")}</TH>
            <TH className="text-right">{t("paying")}</TH>
            <TH className="text-right">{t("discountTaken")}</TH>
            <TH className="text-right">{t("cash")}</TH>
          </TR>
        </THead>
        <TBody>
          {supplier.lines.map((line) => (
            <TR key={line.document_id} data-preview-invoice={line.document_number}>
              <TD className="font-mono text-xs">{line.document_number}</TD>
              <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">{money(line.open_amount)}</TD>
              <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">{money(line.amount)}</TD>
              <TD
                className="text-right font-mono text-xs tabular-nums whitespace-nowrap"
                data-testid={`discount-taken-${line.document_number}`}
              >
                {Number(line.discount_amount) === 0 ? tc("emptyValue") : money(line.discount_amount)}
              </TD>
              <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">{money(line.cash_amount)}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </section>
  );
}
