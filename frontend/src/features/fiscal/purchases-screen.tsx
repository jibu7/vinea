"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Download, ExternalLink } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field } from "@/design/components/input";
import { QueryState } from "@/design/components/query-state";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { useHasPermission } from "@/features/auth/hooks";
import { useCompanyDetails, useCurrencies } from "@/features/gl/hooks";
import { useDocumentPage } from "@/features/subledger/hooks";
import { DocumentKind, DocumentStatus, FiscalFeedDecision } from "@/lib/api-enums";
import { newDraftId } from "@/lib/drafts";
import { dotted, formatDate, formatMoney } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import {
  useAcceptPurchaseFeedRow,
  useFetchPurchaseFeed,
  useFiscalDevices,
  usePurchaseFeed,
  useRejectPurchaseFeedRow,
} from "./hooks";
import type { PurchaseFeedRow } from "./types";

const DECISION_TONE: Record<string, "neutral" | "success" | "danger"> = {
  [FiscalFeedDecision.PENDING]: "neutral",
  [FiscalFeedDecision.ACCEPTED]: "success",
  [FiscalFeedDecision.REJECTED]: "danger",
};

/**
 * **EBM purchases** — Transactions → Tax.
 *
 * What RRA is holding against this taxpayer: every sale another taxpayer declared *to* us. The
 * authority publishes it, this company says yes or no to it, and the answer goes back as a
 * `purchase_confirm` (decision 9).
 *
 * **Accept links a document, and the link is the whole no-double-registration rule.** A
 * purchase this company also keyed has already queued its own `purchase` row; confirming the
 * feed row as well would register the same supplier invoice twice. So the accept carries the AP
 * document it became, and the service decides which of the two registrations RRA keeps —
 * cancelling the document's own row in favour of the confirmation when it has not gone yet, and
 * declining to queue a confirmation when it has. The screen reports back which happened rather
 * than pretending the decision was simple.
 *
 * **Fetch** is on the screen for the same reason the device sync is: a feed nobody can refresh
 * is a feed that is always yesterday's. It pulls by the device's own watermark, so pressing it
 * twice is cheap and never re-reads what has already landed.
 */
export function EbmPurchasesScreen() {
  const t = useTranslations("fiscal.purchases");
  const tc = useTranslations("fiscal.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canManage = useHasPermission()("fiscal:queue_manage");

  const [decision, setDecision] = useState<string>(FiscalFeedDecision.PENDING);
  const [accepting, setAccepting] = useState<PurchaseFeedRow | null>(null);
  const [apDocumentId, setApDocumentId] = useState("");

  const company = useCompanyDetails();
  const currencies = useCurrencies();
  const base = (currencies.data ?? []).find((c) => c.is_base);
  const baseLike = {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };

  const devices = useFiscalDevices();
  const feed = usePurchaseFeed({ decision: decision || undefined });
  const fetchFeed = useFetchPurchaseFeed();
  const accept = useAcceptPurchaseFeedRow();
  const reject = useRejectPurchaseFeedRow();

  /** Recent AP invoices, for the link. Not filtered to the feed row's supplier: the feed names
   * a TIN and Vinea names a partner, and the two are matched by the person who keyed the
   * invoice rather than by a join this screen would have to guess at. */
  const apDocuments = useDocumentPage("ap", {
    kind: DocumentKind.INVOICE,
    status: DocumentStatus.POSTED,
  });
  const apOptions = useMemo(
    () =>
      (apDocuments.data?.items ?? []).map((row) => ({
        value: String(row.id),
        label: dotted(row.number, row.document_date),
      })),
    [apDocuments.data],
  );

  const activeDevice = (devices.data ?? []).find((device) => device.sdc_id !== null);
  const rows = feed.data ?? [];

  async function handleFetch() {
    if (!activeDevice) return;
    try {
      const result = await fetchFeed.mutateAsync(activeDevice.id);
      toast.show({ title: t("fetched", { rows: result.rows }), tone: "success" });
    } catch (err) {
      showApiError(err, t("fetchFailed"));
    }
  }

  async function handleAccept() {
    if (!accepting) return;
    try {
      const result = await accept.mutateAsync({
        rowId: accepting.id,
        apDocumentId: apDocumentId ? Number(apDocumentId) : null,
        idempotencyKey: newDraftId(),
      });
      setAccepting(null);
      setApDocumentId("");
      // The service's own note, not a sentence composed here: which of the two registrations
      // RRA keeps is its decision, and paraphrasing it on the client is how the screen ends up
      // telling an operator something that is not true.
      toast.show({ title: t("accepted"), description: result.note || undefined, tone: "success" });
    } catch (err) {
      showApiError(err, t("acceptFailed"));
    }
  }

  async function handleReject(row: PurchaseFeedRow) {
    try {
      await reject.mutateAsync({ rowId: row.id, idempotencyKey: newDraftId() });
      toast.show({ title: t("rejected"), tone: "success" });
    } catch (err) {
      showApiError(err, t("rejectFailed"));
    }
  }

  return (
    <ReportPage
      title={t("title")}
      subtitle={t("subtitle")}
      companyName={company.data?.name}
      filters={
        <div className="flex flex-wrap items-end gap-3">
          <Field label={t("decision")} className="w-56">
            <Combobox
              options={[
                { value: "", label: t("allDecisions") },
                ...Object.values(FiscalFeedDecision).map((value) => ({
                  value,
                  label: t(`decisionLabel.${value}`),
                })),
              ]}
              value={decision}
              onValueChange={setDecision}
              placeholder={t("allDecisions")}
            />
          </Field>
          <Button
            variant="secondary"
            disabled={!canManage || !activeDevice || fetchFeed.isPending}
            onClick={handleFetch}
            data-testid="fetch-feed"
            className="gap-1.5 text-xs"
          >
            <Download className="size-3.5" /> {t("fetch")}
          </Button>
        </div>
      }
    >
      <ReportPanel>
        {rows.length === 0 ? (
          <QueryState query={feed} isEmpty empty={t("empty")} testId="purchase-feed" />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-36">{t("supplierTin")}</TH>
                <TH>{t("supplier")}</TH>
                <TH className="w-24 text-right">{t("invoiceNo")}</TH>
                <TH className="w-28">{t("salesDate")}</TH>
                <TH className="w-32 text-right">{t("taxable")}</TH>
                <TH className="w-28 text-right">{t("tax")}</TH>
                <TH className="w-32 text-right">{t("total")}</TH>
                <TH className="w-32">{t("decision")}</TH>
                <TH className="w-40 print:hidden" />
              </TR>
            </THead>
            <TBody>
              {rows.map((row) => (
                <TR key={row.id}>
                  <TD className="font-mono text-xs">{row.spplr_tin}</TD>
                  <TD className="text-xs">{row.spplr_nm ?? tc("emptyValue")}</TD>
                  <TD className="text-right font-mono text-xs tabular-nums">{row.spplr_invc_no}</TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">
                    {row.sales_dt ? formatDate(row.sales_dt) : tc("emptyValue")}
                  </TD>
                  <TD
                    className="text-right font-mono text-xs tabular-nums"
                    data-testid={`feed-taxable-${row.id}`}
                  >
                    {formatMoney(Number(row.total_taxable_amount), baseLike, { showCode: false })}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                    {formatMoney(Number(row.total_tax_amount), baseLike, { showCode: false })}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {formatMoney(Number(row.total_amount), baseLike, { showCode: false })}
                  </TD>
                  <TD>
                    <div className="flex flex-col items-start gap-1">
                      <StatusChip tone={DECISION_TONE[row.decision] ?? "neutral"}>
                        <span data-testid={`feed-decision-${row.id}`}>
                          {t(`decisionLabel.${row.decision}`)}
                        </span>
                      </StatusChip>
                      {row.ap_document_id !== null && (
                        <Link
                          href={`/ap/documents/${row.ap_document_id}`}
                          className="inline-flex items-center gap-1 font-mono text-[11px] text-[var(--vinea-brand)] underline"
                        >
                          {t("linkedDocument")}
                          <ExternalLink className="size-3" />
                        </Link>
                      )}
                    </div>
                  </TD>
                  <TD className="print:hidden">
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        disabled={!canManage || row.decision !== FiscalFeedDecision.PENDING}
                        data-testid={`accept-${row.id}`}
                        onClick={() => {
                          setApDocumentId("");
                          setAccepting(row);
                        }}
                      >
                        {t("accept")}
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={
                          !canManage ||
                          row.decision !== FiscalFeedDecision.PENDING ||
                          reject.isPending
                        }
                        data-testid={`reject-${row.id}`}
                        onClick={() => handleReject(row)}
                      >
                        {t("reject")}
                      </Button>
                    </div>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </ReportPanel>

      <Dialog open={accepting !== null} onOpenChange={(open) => !open && setAccepting(null)}>
        <DialogContent title={t("acceptTitle")} description={t("acceptDescription")}>
          <Field label={t("apDocument")}>
            <Combobox
              options={[{ value: "", label: t("noApDocument") }, ...apOptions]}
              value={apDocumentId}
              onValueChange={setApDocumentId}
              placeholder={t("noApDocument")}
            />
          </Field>
          <p className="pt-2 text-xs text-[var(--vinea-ink-subtle)]">{t("acceptNote")}</p>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setAccepting(null)}>
              {tc("cancel")}
            </Button>
            <Button
              variant="primary"
              disabled={accept.isPending}
              data-testid="confirm-accept"
              onClick={handleAccept}
            >
              {t("confirmAccept")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </ReportPage>
  );
}
