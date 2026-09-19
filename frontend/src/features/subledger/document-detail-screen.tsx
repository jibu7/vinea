"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Copy, ExternalLink } from "lucide-react";
import { Button } from "@/design/components/button";
import { QueryState } from "@/design/components/query-state";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Combobox } from "@/design/components/combobox";
import { Dialog, DialogContent, DialogTrigger } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { isApiError, useHasPermission } from "@/features/auth/hooks";
import { DocumentFiscalPanel } from "@/features/fiscal/document-fiscal-panel";
import {
  useDocumentReceipt,
  useFiscalDocumentContext,
  usePrintReceiptCopy,
} from "@/features/fiscal/hooks";
import { CisReceiptLayout } from "@/features/fiscal/receipt-layout";
import { useAccounts, useCompanyDetails, useCurrencies } from "@/features/gl/hooks";
import { byId } from "@/features/gl/lookups";
import { DocumentKind, DocumentStatus, FiscalOutboxStatus } from "@/lib/api-enums";
import { ApiError } from "@/lib/api";
import { newDraftId } from "@/lib/drafts";
import { dotted, formatDate, formatMoney, formatQuantity, todayIso } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { DOCUMENT_STATUS_KEY, DOCUMENT_STATUS_TONE } from "./documents-screen";
import { useAllocations, useDocument, usePartners, useReverseDocument, useUnallocate } from "./hooks";
import type { PartnerRole } from "./types";

/**
 * One posted partner document: its header, its lines, the allocations against it — and the
 * two corrections the product had no way to make.
 *
 * **Reverse** is the P4 document reversal: a mirror entry *and* the open item withdrawn. Only
 * this service does both, which is why the general ledger's Reverse is refused for an `ar` /
 * `ap` entry (`reverse_via_module_document`) and links here instead — it would post the
 * reversing entry alone and leave `SUM(open items) == control balance` quietly broken.
 *
 * **Unallocate** is the allocation's mirror, with the open amount put back on both documents.
 * It lives beside the allocation it undoes rather than on a screen of its own, because the
 * question it answers ("this receipt went against the wrong invoice") is one somebody is
 * asking while looking at the document.
 *
 * Both endpoints shipped in P4 with no caller anywhere in the frontend. Appendix C.1.7.
 */
export function PartnerDocumentDetailScreen({
  role,
  documentId,
}: {
  role: PartnerRole;
  documentId: number;
}) {
  const t = useTranslations("arap.documentList");
  const tf = useTranslations("fiscal.document");
  const tq = useTranslations("fiscal.queue");
  const tKind = useTranslations(role === "ap" ? "arap.documentList.kindsAp" : "arap.documentList.kinds");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const hasPermission = useHasPermission();
  const canPost = hasPermission(role === "ar" ? "ar:transactions_post" : "ap:transactions_post");

  const document = useDocument(role, documentId);
  const allocations = useAllocations(role, { documentId });
  const partners = usePartners(role, { includeInactive: true });
  const accounts = useAccounts();
  const accountById = byId(accounts.data);
  const currencies = useCurrencies();
  const currencyById = byId(currencies.data);
  const company = useCompanyDetails();
  const reverse = useReverseDocument(role);
  const unallocate = useUnallocate(role);

  // --- P7: the receipt this document prints, if RRA has signed one ------------------------
  //
  // Three answers, and the screen needs all three. A block is "print the CIS layout"; `null` is
  // "this company does not fiscalize, print the P4 layout"; and a `fiscal_receipt_pending`
  // refusal is "it does, and the authority has not signed yet" — which is why the query's error
  // is read rather than swallowed. `field_errors.fiscal_status` carries the queue row's own
  // status, so the disabled Print button says which kind of waiting this is.
  const receiptQuery = useDocumentReceipt(documentId);
  const printCopy = usePrintReceiptCopy();
  const receipt = receiptQuery.data ?? null;
  const receiptError = receiptQuery.error instanceof ApiError ? receiptQuery.error : null;
  const receiptPending = receiptError?.code === "fiscal_receipt_pending";
  /** The queue row's own status, carried on the refusal. `unknown` and `needs_receipt` are the
   * two that block a reversal (`fiscal_status_unresolved`): RRA may already hold the sale. */
  const fiscalStatus = receiptError?.fieldErrors?.fiscal_status?.[0] ?? null;
  const unresolvedFiscalStatus =
    fiscalStatus === FiscalOutboxStatus.UNKNOWN ||
    fiscalStatus === FiscalOutboxStatus.NEEDS_RECEIPT;
  const [copyOpen, setCopyOpen] = useState(false);
  const fiscalContext = useFiscalDocumentContext();

  const [reverseOpen, setReverseOpen] = useState(false);
  const [reversalDate, setReversalDate] = useState(todayIso);
  const [reason, setReason] = useState("");
  const [refundReason, setRefundReason] = useState("");
  const [reverseError, setReverseError] = useState<string | null>(null);

  const [unallocateId, setUnallocateId] = useState<number | null>(null);
  const [unallocationDate, setUnallocationDate] = useState(todayIso);
  const [unallocateReason, setUnallocateReason] = useState("");
  const [unallocateError, setUnallocateError] = useState<string | null>(null);
  const [unallocateKey, setUnallocateKey] = useState(() => newDraftId());

  const data = document.data;
  const isReversed = data?.status === DocumentStatus.REVERSED;

  /**
   * Why Reverse cannot be pressed, or `null` when it can.
   *
   * These are the service's own three refusals, in its order — `document_already_reversed`,
   * `document_allocated`, `instrument_matured` — read off the document before the button is
   * drawn rather than discovered by pressing it. A document that is allocated *can* be
   * reversed, but only after the allocation beside it is undone, and the reversal would
   * otherwise leave the counterparty's open item pointing at an entry that no longer
   * stands; saying "Unallocate first" beside a live Unallocate button is the whole answer.
   *
   * Amounts are compared as numbers: the wire carries `NUMERIC(20,6)` strings, so
   * "60000.000000" and "60000.0" are the same amount and different strings.
   */
  function reverseBlockedReason(): string | null {
    if (!canPost) return t("noReversePermission");
    if (!data) return null;
    if (isReversed) return t("alreadyReversed");
    if (Number(data.open_amount) !== Number(data.total_amount)) return t("mustUnallocateFirst");
    if (data.matured_entry_id !== null) return t("instrumentMatured");
    // P7 decision 7, said **before** the button rather than after it. A refund of a refund is
    // not in RRA's vocabulary — the correction is a new invoice — and a document whose queue
    // row is `unknown` or `needs_receipt` cannot be reversed at all until somebody has found
    // out what the authority holds, because cancelling a row RRA is holding would leave a sale
    // declared and unrefunded.
    if (data.kind === DocumentKind.CREDIT_NOTE && data.fiscal_receipt_id !== null) {
      return t("fiscalRefundIrreversible");
    }
    if (receiptPending && unresolvedFiscalStatus) return t("fiscalStatusUnresolved");
    return null;
  }

  async function handleReverse() {
    setReverseError(null);
    try {
      const result = await reverse.mutateAsync({
        documentId,
        // Sent only when the screen asked for it. Reversing a fiscalized invoice whose sale RRA
        // signed queues a full refund, and a refund carries a §4.16 reason (decision 7); an
        // ordinary reversal has none and must not invent one.
        payload: { on_date: reversalDate, reason, refund_reason: refundReason || null },
      });
      setReverseOpen(false);
      // No navigation: the reversal is a *state change on this document*, not a new one to
      // land on — unlike the inventory reversal, which creates a second document with its own
      // number. `useReverseDocument` invalidates the subledger root, so the header, the status
      // chip and the now-disabled Reverse button all re-read from the server in place.
      toast.show({ title: t("reversedToast", { number: result.number }), tone: "success" });
    } catch (err) {
      if (isApiError(err)) setReverseError(err.message);
      else showApiError(err, t("reverseFailed"));
    }
  }

  async function handleUnallocate() {
    if (unallocateId === null) return;
    setUnallocateError(null);
    try {
      const result = await unallocate.mutateAsync({
        allocationId: unallocateId,
        payload: { on_date: unallocationDate, reason: unallocateReason },
        idempotencyKey: unallocateKey,
      });
      setUnallocateId(null);
      setUnallocateKey(newDraftId());
      setUnallocateReason("");
      toast.show({ title: t("unallocatedToast", { number: result.number }), tone: "success" });
    } catch (err) {
      if (isApiError(err)) setUnallocateError(err.message);
      else showApiError(err, t("unallocateFailed"));
    }
  }

  if (!data) {
    return (
      <ReportPage title={t(role === "ar" ? "titleAr" : "titleAp")} companyName={company.data?.name}>
        <QueryState query={document} isEmpty empty={t("noRows")} testId="query" />
      </ReportPage>
    );
  }

  /** The company's own money. A fiscal receipt is declared and printed in it whatever the
   * document was keyed in (decision 3), so the CIS layout never sees the document's currency. */
  const base = (currencies.data ?? []).find((c) => c.is_base);
  const baseLike = {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };
  const currency = currencyById.get(data.currency_id);
  const currencyLike = {
    code: currency?.code ?? "",
    decimalPlaces: currency?.decimal_places ?? 0,
    symbol: currency?.symbol ?? null,
  };
  const partnerName =
    (partners.data ?? []).find((p) => p.id === data.partner_id)?.name ?? String(data.partner_id);
  const allocationRows = allocations.data ?? [];
  const reverseBlocked = reverseBlockedReason();
  /** A reversal that will queue a refund needs a reason code; every other reversal does not.
   * The service refuses without one, so asking here is the refusal said where it can be
   * answered rather than after the dialog has closed. */
  const needsRefundReason =
    data.kind === DocumentKind.INVOICE && data.fiscal_receipt_id !== null;
  const refundReasonSatisfied = !needsRefundReason || refundReason !== "";

  /**
   * Why this document may not be printed, or nothing when it may.
   *
   * CIS §10: a fiscalized document whose queue row RRA has not signed has nothing to print —
   * the paper carries a receipt number the authority issued, and there is no draft form of one.
   * A non-fiscalized company has no receipt and no refusal: `receipt_block` returns `null` and
   * the P4 layout prints, which is the last sentence of decision 11.
   */
  const printBlocked = receiptPending
    ? tf("printPending", { status: fiscalStatus ? tq(`statusLabel.${fiscalStatus}`) : "" })
    : undefined;

  async function handleCopyPrint() {
    try {
      await printCopy.mutateAsync(documentId);
      setCopyOpen(false);
      // Print *after* the counter has moved, so the sheet that comes out is the one the
      // authority's copy count describes. Nothing is sent to RRA — a copy is a print of a sale
      // already declared (§11, §15).
      window.print();
    } catch (err) {
      showApiError(err, tf("copyFailed"));
    }
  }

  return (
    <ReportPage
      title={data.number}
      subtitle={tKind.has(data.kind) ? tKind(data.kind) : data.kind}
      companyName={company.data?.name}
      backHref={`/${role}/documents`}
      asOfLabel={formatDate(data.document_date)}
      printDisabledReason={printBlocked}
      printMasthead={receipt === null}
      actions={
        receipt ? (
          <Button
            variant="secondary"
            onClick={() => setCopyOpen(true)}
            data-testid="copy-print"
            className="gap-1.5 text-xs"
          >
            <Copy className="size-3.5" /> {tf("copyPrint")}
          </Button>
        ) : undefined
      }
    >
      {/* The receipt, print-only and instead of everything else. A fiscal receipt is a
          prescribed layout an inspector reads, not a decorated document detail — so when there
          is one, the panels below are hidden at print time and this is what leaves the
          printer. */}
      {receipt && <CisReceiptLayout block={receipt} baseCurrency={baseLike} />}

      <div className={receipt ? "space-y-4 print:hidden" : "space-y-4"}>
      <ReportPanel>
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <div>
            <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("partner")}</dt>
            <dd className="text-sm" data-testid="document-partner">
              {partnerName}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("date")}</dt>
            <dd className="text-sm">{formatDate(data.document_date)}</dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("dueDate")}</dt>
            <dd className="text-sm">
              {data.due_date ? formatDate(data.due_date) : t("emptyValue")}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("status")}</dt>
            <dd>
              <StatusChip tone={DOCUMENT_STATUS_TONE[data.status] ?? "neutral"}>
                {t(DOCUMENT_STATUS_KEY[data.status] ?? "statusPosted")}
              </StatusChip>
            </dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("totalAmount")}</dt>
            <dd
              data-testid="document-total"
              className="font-mono text-sm tabular-nums text-[var(--vinea-ink)]"
            >
              {formatMoney(Number(data.total_amount), currencyLike)}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("openAmount")}</dt>
            <dd
              data-testid="document-open"
              className="font-mono text-sm tabular-nums text-[var(--vinea-ink)]"
            >
              {formatMoney(Number(data.open_amount), currencyLike)}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("transactionType")}</dt>
            <dd className="font-mono text-xs text-[var(--vinea-ink-muted)]">
              {data.transaction_type}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("entry")}</dt>
            <dd className="text-sm">
              <Link
                href={`/gl/entries/${data.journal_entry_id}`}
                className="inline-flex items-center gap-1 font-mono text-xs text-[var(--vinea-brand)] underline"
              >
                {t("viewEntry")}
                <ExternalLink className="size-3" />
              </Link>
            </dd>
          </div>
          {/* The companion (P6 decision 2). A stock-bearing document posts **two** entries and
              the screen showed one, so an invoice led to the receivable and nothing led to
              what the sale cost. The `STK-` entry links back here through the same pair, so
              the round trip closes from either end. Absent on a document with no valued stock
              line, which has no companion at all. */}
          {data.stock_entry_id !== null && (
            <div>
              <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("stockEntry")}</dt>
              <dd className="text-sm">
                <Link
                  href={`/gl/entries/${data.stock_entry_id}`}
                  data-testid="document-stock-entry"
                  className="inline-flex items-center gap-1 font-mono text-xs text-[var(--vinea-brand)] underline"
                >
                  {t("viewEntry")}
                  <ExternalLink className="size-3" />
                </Link>
              </dd>
            </div>
          )}
        </dl>
        <p className="pt-3 text-sm">{data.description}</p>
        {data.reference && (
          <p className="text-xs text-[var(--vinea-ink-muted)]">
            {dotted(t("reference"), data.reference)}
          </p>
        )}
        {isReversed && (
          <p className="pt-2 text-xs text-[var(--vinea-ink-muted)]" data-testid="document-reversed">
            {data.reversed_on
              ? t("reversedOn", { date: formatDate(data.reversed_on) })
              : t("wasReversed")}
          </p>
        )}
      </ReportPanel>

      <ReportPanel>
        <h2 className="pb-2 text-xs font-semibold text-[var(--vinea-ink-muted)]">{t("lines")}</h2>
        {data.lines.length === 0 ? (
          <p className="py-4 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {data.kind === DocumentKind.SETTLEMENT ? t("settlementHasNoLines") : t("noRows")}
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-10">{t("lineNo")}</TH>
                <TH className="w-28">{t("account")}</TH>
                <TH>{t("description")}</TH>
                <TH className="text-right">{t("quantity")}</TH>
                <TH className="text-right">{t("unitPrice")}</TH>
                <TH className="text-right">{t("net")}</TH>
                <TH className="text-right">{t("tax")}</TH>
                <TH className="text-right">{t("gross")}</TH>
              </TR>
            </THead>
            <TBody>
              {data.lines.map((line) => (
                <TR key={line.id}>
                  <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                    {line.line_no}
                  </TD>
                  <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">
                    {accountById.get(line.gl_account_id)?.code ?? line.gl_account_id}
                  </TD>
                  <TD className="text-xs">{line.description}</TD>
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {formatQuantity(Number(line.quantity), 2)}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                    {formatMoney(Number(line.unit_price), currencyLike, { showCode: false })}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {formatMoney(Number(line.net_amount), currencyLike, { showCode: false })}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                    {formatMoney(Number(line.tax_amount), currencyLike, { showCode: false })}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {formatMoney(Number(line.gross_amount), currencyLike, { showCode: false })}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </ReportPanel>

      <ReportPanel>
        <h2 className="pb-2 text-xs font-semibold text-[var(--vinea-ink-muted)]">
          {t("allocations")}
        </h2>
        {allocationRows.length === 0 ? (
          <p className="py-4 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {t("noAllocations")}
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-32">{t("allocationNumber")}</TH>
                <TH className="w-24">{t("allocationDate")}</TH>
                <TH>{t("against")}</TH>
                <TH className="text-right">{t("amount")}</TH>
                <TH className="text-right">{t("discountAmount")}</TH>
                <TH className="w-28 print:hidden" />
              </TR>
            </THead>
            <TBody>
              {allocationRows.map((row) => {
                // The other end of the line: on an invoice that is the receipt, on a receipt
                // the invoice. The row always names the document you are *not* looking at.
                const against =
                  row.debit_document_id === documentId ? row.credit_number : row.debit_number;
                const againstId =
                  row.debit_document_id === documentId
                    ? row.credit_document_id
                    : row.debit_document_id;
                const blocked = row.is_reversed
                  ? t("alreadyUnallocated")
                  : row.reverses_allocation_id !== null
                    ? t("isAnUnallocation")
                    : !canPost
                      ? t("noUnallocatePermission")
                      : null;
                return (
                  <TR key={`${row.allocation_id}-${row.debit_document_id}-${row.credit_document_id}`}>
                    <TD className="font-mono text-xs">{row.number}</TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">
                      {formatDate(row.allocation_date)}
                    </TD>
                    <TD>
                      <Link
                        href={`/${role}/documents/${againstId}`}
                        className="font-mono text-xs font-semibold text-[var(--vinea-brand)] underline"
                      >
                        {against}
                      </Link>
                    </TD>
                    <TD
                      data-testid="allocation-amount"
                      className="text-right font-mono text-xs tabular-nums"
                    >
                      {formatMoney(Number(row.amount), currencyLike, { showCode: false })}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                      {formatMoney(Number(row.discount_amount), currencyLike, { showCode: false })}
                    </TD>
                    <TD className="print:hidden">
                      <Button
                        variant="ghost"
                        disabled={blocked !== null}
                        title={blocked ?? undefined}
                        data-testid={`unallocate-${row.allocation_id}`}
                        onClick={() => {
                          setUnallocateError(null);
                          setUnallocateId(row.allocation_id);
                        }}
                      >
                        {t("unallocate")}
                      </Button>
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        )}
      </ReportPanel>

      <div className="flex justify-end print:hidden">
        {reverseBlocked !== null ? (
          <Button variant="danger" disabled title={reverseBlocked} data-testid="reverse-blocked">
            {t("reverse")}
          </Button>
        ) : (
          <Dialog open={reverseOpen} onOpenChange={setReverseOpen}>
            <DialogTrigger asChild>
              <Button variant="danger">{t("reverse")}</Button>
            </DialogTrigger>
            <DialogContent title={t("reverseTitle")} description={t("reverseDescription")}>
              {reverseError && (
                <p
                  data-testid="reverse-error"
                  className="mb-3 rounded-[var(--radius-control)] bg-[var(--vinea-danger-soft)] px-3 py-2 text-sm text-[var(--vinea-danger)]"
                >
                  {reverseError}
                </p>
              )}
              <div className="space-y-3">
                <Field label={t("reversalDate")}>
                  <IsoDatePicker value={reversalDate} onValueChange={setReversalDate} />
                </Field>
                <Field label={t("reason")}>
                  <Input
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder={t("reasonPlaceholder")}
                  />
                </Field>
                {/* Only on a fiscalized invoice RRA signed. Reversing one queues a **full
                    refund** rather than cancelling the sale, and a refund carries a §4.16 reason
                    code that only the person reversing can answer (decision 7). Every other
                    reversal leaves this unset and sends nothing. */}
                {data.kind === DocumentKind.INVOICE && data.fiscal_receipt_id !== null && (
                  <Field label={tf("refundReason")}>
                    <Combobox
                      options={(fiscalContext.data?.refund_reasons ?? []).map((item) => ({
                        value: item.code,
                        label: `${item.code} · ${item.name}`,
                      }))}
                      value={refundReason}
                      onValueChange={setRefundReason}
                      placeholder={tf("refundReasonPlaceholder")}
                    />
                  </Field>
                )}
              </div>
              <div className="mt-4 flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setReverseOpen(false)}>
                  {t("cancel")}
                </Button>
                <Button
                  variant="danger"
                  disabled={reason.length < 3 || !refundReasonSatisfied || reverse.isPending}
                  data-testid="confirm-reverse"
                  onClick={handleReverse}
                >
                  {t("confirmReverse")}
                </Button>
              </div>
            </DialogContent>
          </Dialog>
        )}
      </div>

      </div>

      {/* The fiscal half — what the authority was told, what it signed, and the three things a
          person can do when it has not answered. Renders nothing at all for a document that
          was never declared, which is every document on a company with no device. */}
      <div className="print:hidden">
        <DocumentFiscalPanel documentId={documentId} receipt={receipt} />
      </div>

      <Dialog open={copyOpen} onOpenChange={setCopyOpen}>
        <DialogContent title={tf("copyTitle")} description={tf("copyDescription")}>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setCopyOpen(false)}>
              {t("cancel")}
            </Button>
            <Button
              variant="primary"
              disabled={printCopy.isPending}
              data-testid="confirm-copy-print"
              onClick={handleCopyPrint}
            >
              {tf("confirmCopy")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={unallocateId !== null} onOpenChange={(open) => !open && setUnallocateId(null)}>
        <DialogContent title={t("unallocateTitle")} description={t("unallocateDescription")}>
          {unallocateError && (
            <p
              data-testid="unallocate-error"
              className="mb-3 rounded-[var(--radius-control)] bg-[var(--vinea-danger-soft)] px-3 py-2 text-sm text-[var(--vinea-danger)]"
            >
              {unallocateError}
            </p>
          )}
          <div className="space-y-3">
            <Field label={t("unallocationDate")}>
              <IsoDatePicker value={unallocationDate} onValueChange={setUnallocationDate} />
            </Field>
            <Field label={t("reason")}>
              <Input
                value={unallocateReason}
                onChange={(e) => setUnallocateReason(e.target.value)}
                placeholder={t("reasonPlaceholder")}
              />
            </Field>
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setUnallocateId(null)}>
              {t("cancel")}
            </Button>
            <Button
              variant="danger"
              disabled={unallocateReason.length < 3 || unallocate.isPending}
              onClick={handleUnallocate}
              data-testid="confirm-unallocate"
            >
              {t("confirmUnallocate")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </ReportPage>
  );
}
