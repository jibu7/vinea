"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { ExternalLink } from "lucide-react";
import { Button } from "@/design/components/button";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Dialog, DialogContent, DialogTrigger } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { isApiError, useHasPermission } from "@/features/auth/hooks";
import { useCompanyDetails, useCurrencies } from "@/features/gl/hooks";
import { InventoryDocumentStatus } from "@/lib/api-enums";
import { dotted, formatDate, formatMoney, formatQuantity, todayIso } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { newDraftId } from "@/lib/drafts";
import { useReverseStockDocument, useStockDocument } from "./hooks";
import { DOCUMENT_STATUS_KEY, DOCUMENT_STATUS_TONE } from "./documents-screen";
import { useInventoryLineSupport } from "./line-support";

/**
 * One stock document: its header, its lines, its ledger entry — and Reverse.
 *
 * **Reverse lives here and only here.** Decision 11's reversal is a kernel reversal *plus*
 * reversing moves, and only the inventory service does both; the general ledger's own Reverse
 * is refused for a module-owned entry precisely because it would do the first half alone. So
 * this is the screen that action belongs to, and the GL entry page links here rather than
 * offering a button it cannot honour.
 *
 * The reversal is idempotent on a draft UUID like every other posting in the product, so a
 * retried click replays rather than reversing twice — and the document it produces links both
 * ways, which is what "a processed count stays Completed and links to the reversal" needs.
 */
export function InventoryDocumentScreen({ documentId }: { documentId: number }) {
  const t = useTranslations("inventory.documents");
  const tr = useTranslations("inventory.reports");
  const tKind = useTranslations("inventory.documents.kinds");
  const router = useRouter();
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const hasPermission = useHasPermission();
  const canReverse = hasPermission("inv:transactions_adjust");

  const document = useStockDocument(documentId);
  const support = useInventoryLineSupport({
    includeInactiveItems: true,
    includeInTransitWarehouses: true,
  });
  const company = useCompanyDetails();
  const currencies = useCurrencies();
  const reverse = useReverseStockDocument();

  const [open, setOpen] = useState(false);
  const [reversalDate, setReversalDate] = useState(todayIso);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState(() => newDraftId());

  const base = (currencies.data ?? []).find((c) => c.is_base);
  const baseLike = {
    code: base?.code ?? "",
    decimalPlaces: base?.decimal_places ?? 0,
    symbol: base?.symbol ?? null,
  };
  const data = document.data;
  const isReversed = data?.status === InventoryDocumentStatus.REVERSED;
  const isReversal = (data?.reverses_document_id ?? null) !== null;

  async function handleReverse() {
    setError(null);
    try {
      const reversal = await reverse.mutateAsync({
        documentId,
        payload: { reversal_date: reversalDate, reason },
        idempotencyKey,
      });
      setOpen(false);
      setIdempotencyKey(newDraftId());
      toast.show({ title: t("reversedToast", { number: reversal.number }), tone: "success" });
      router.push(`/inventory/documents/${reversal.id}`);
    } catch (err) {
      if (isApiError(err)) setError(err.message);
      else showApiError(err, t("reverseFailed"));
    }
  }

  if (!data) {
    return (
      <ReportPage title={t("title")} companyName={company.data?.name}>
        <p className="py-10 text-center text-sm text-[var(--vinea-ink-subtle)]">
          {document.isLoading ? tr("loading") : tr("noRows")}
        </p>
      </ReportPage>
    );
  }

  const warehouseCode = (id: number) =>
    support.warehouses.find((w) => w.id === id)?.code ?? String(id);

  return (
    <ReportPage
      title={data.number}
      subtitle={tKind.has(data.doc_type) ? tKind(data.doc_type) : data.doc_type}
      companyName={company.data?.name}
      backHref="/inventory/documents"
      asOfLabel={formatDate(data.document_date)}
    >
      <ReportPanel>
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <div>
            <dt className="text-xs text-[var(--vinea-ink-muted)]">{tr("date")}</dt>
            <dd className="text-sm">{formatDate(data.document_date)}</dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--vinea-ink-muted)]">{tr("status")}</dt>
            <dd>
              <StatusChip tone={DOCUMENT_STATUS_TONE[data.status] ?? "neutral"}>
                {t(DOCUMENT_STATUS_KEY[data.status] ?? "statusPosted")}
              </StatusChip>
            </dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--vinea-ink-muted)]">{tr("value")}</dt>
            <dd
              data-testid="document-total"
              className="font-mono text-sm tabular-nums text-[var(--vinea-ink)]"
            >
              {formatMoney(Number(data.total_value), baseLike)}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--vinea-ink-muted)]">{tr("entry")}</dt>
            <dd className="text-sm">
              {/* A document that moved no value has no ledger side. That is a real outcome
                  (a free-sample receipt), not a missing link, and it says so. */}
              {data.journal_entry_id === null ? (
                <span className="text-xs text-[var(--vinea-ink-subtle)]">{t("noEntry")}</span>
              ) : (
                <Link
                  href={`/gl/entries/${data.journal_entry_id}`}
                  className="inline-flex items-center gap-1 font-mono text-xs text-[var(--vinea-brand)] underline"
                >
                  {t("viewEntry")}
                  <ExternalLink className="size-3" />
                </Link>
              )}
            </dd>
          </div>
        </dl>
        <p className="pt-3 text-sm">{data.description}</p>
        {data.reference && (
          <p className="text-xs text-[var(--vinea-ink-muted)]">
            {dotted(t("reference"), data.reference)}
          </p>
        )}

        {/* Both directions of the reversal link, so either document leads to the other. */}
        {isReversed && (
          <p className="pt-2 text-xs text-[var(--vinea-ink-muted)]" data-testid="document-reversed">
            {t("wasReversed")}
          </p>
        )}
        {isReversal && (
          <p className="pt-2 text-xs text-[var(--vinea-ink-muted)]">
            <Link
              href={`/inventory/documents/${data.reverses_document_id}`}
              className="text-[var(--vinea-brand)] underline"
            >
              {t("reversesDocument")}
            </Link>
          </p>
        )}
      </ReportPanel>

      <ReportPanel>
        <Table>
          <THead>
            <TR>
              <TH className="w-28">{tr("code")}</TH>
              <TH>{tr("name")}</TH>
              <TH className="w-20">{tr("warehouse")}</TH>
              <TH className="text-right">{tr("quantity")}</TH>
              <TH className="text-right">{tr("unitCost")}</TH>
              <TH className="text-right">{tr("value")}</TH>
            </TR>
          </THead>
          <TBody>
            {data.lines.map((line) => {
              const item = support.itemById.get(line.item_id);
              return (
                <TR key={line.id}>
                  <TD className="font-mono text-xs font-semibold text-[var(--vinea-brand)]">
                    {item?.code ?? line.item_id}
                  </TD>
                  <TD className="text-xs">{item?.name}</TD>
                  <TD className="font-mono text-xs text-[var(--vinea-ink-muted)]">
                    {warehouseCode(line.warehouse_id)}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {support.formatBaseWithUnit(line.item_id, line.quantity_base)}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                    {line.unit_cost === null
                      ? tr("emptyValue")
                      : formatQuantity(Number(line.unit_cost), 6)}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {/* What the engine posted, not what was keyed: only a revaluation keys a
                        value, so `line.value` is null on almost every line. */}
                    {line.posted_value === null
                      ? tr("emptyValue")
                      : formatMoney(Number(line.posted_value), baseLike, { showCode: false })}
                  </TD>
                </TR>
              );
            })}
          </TBody>
        </Table>
      </ReportPanel>

      <div className="flex justify-end print:hidden">
        {isReversed || isReversal || !canReverse ? (
          <Button
            variant="danger"
            disabled
            title={
              isReversed
                ? t("alreadyReversed")
                : isReversal
                  ? t("cannotReverseAReversal")
                  : t("noReversePermission")
            }
          >
            {t("reverse")}
          </Button>
        ) : (
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button variant="danger">{t("reverse")}</Button>
            </DialogTrigger>
            <DialogContent title={t("reverseTitle")} description={t("reverseDescription")}>
              {error && (
                <p
                  data-testid="reverse-error"
                  className="mb-3 rounded-[var(--radius-control)] bg-[var(--vinea-danger-soft)] px-3 py-2 text-sm text-[var(--vinea-danger)]"
                >
                  {error}
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
              </div>
              <div className="mt-4 flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setOpen(false)}>
                  {t("cancel")}
                </Button>
                <Button
                  variant="danger"
                  disabled={!reason || reverse.isPending}
                  onClick={handleReverse}
                >
                  {t("confirmReverse")}
                </Button>
              </div>
            </DialogContent>
          </Dialog>
        )}
      </div>
    </ReportPage>
  );
}
