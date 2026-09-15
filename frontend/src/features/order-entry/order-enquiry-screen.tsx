"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ExternalLink } from "lucide-react";
import { Combobox } from "@/design/components/combobox";
import { Field } from "@/design/components/input";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useCompanyDetails } from "@/features/gl/hooks";
import { usePartners } from "@/features/subledger/hooks";
import { cn } from "@/lib/cn";
import { exportToCsv } from "@/lib/csv";
import { dotted, formatDate, formatMoney, formatQuantity } from "@/lib/format";
import {
  usePurchaseOrderEnquiry,
  usePurchaseOrders,
  useSalesOrderEnquiry,
  useSalesOrders,
} from "./hooks";
import { PURCHASE_ORDER_STATUS_TONE } from "./purchase-orders-screen";
import { SALES_ORDER_STATUS_TONE } from "./sales-orders-screen";
import { formatOrderQuantity, useOrderLineSupport } from "./order-support";
import type { LinkedDocument, OrderEnquiry } from "./types";

type Role = "sales" | "purchase";

/**
 * Order enquiry — "what happened to this order", for either side (P6 step 8).
 *
 * One component for both because the question is the same one and the answer has the same
 * shape: the order, its lines with what each has done and what is left, and the documents
 * raised against it with the entries those posted. Only three things differ — which listing
 * fills the picker, which endpoint answers, and whether a fulfilling document is an invoice or
 * a receipt — and each is a lookup rather than a branch through the rendering.
 *
 * **The drill is the point of the screen.** Order → line → invoice or GRN → journal entry, in
 * one page, without a round trip per row: the endpoint carries the document links and the
 * entry numbers already. A stock-bearing invoice posts **two** entries (decision 2) and both
 * are offered, because an enquiry showing only the receivable would drill past the cost of the
 * sale — and `stock_entry_id` is a second link on one row rather than a second row, since one
 * document posted both and listing them apart would invite somebody to reverse one of them.
 *
 * **A reversed document stays in the list**, struck through and carrying its own status. What
 * happened to this order includes the invoice that was raised and taken back; dropping it
 * would leave a gap nobody can account for. Its quantity is what *that document* keyed, not a
 * net contribution — the netting lives in the line's `fulfilled`, which counts only posted,
 * unreversed documents. The two answer different questions, so they are rendered differently.
 */
export function OrderEnquiryScreen({ role }: { role: Role }) {
  const t = useTranslations("orderEntry.orderEnquiry");
  const tc = useTranslations("orderEntry.common");
  const tr = useTranslations("inventory.reports");
  const salesStatus = useTranslations("orderEntry.salesOrderStatus");
  const purchaseStatus = useTranslations("orderEntry.purchaseOrderStatus");
  const kinds = useTranslations("orderEntry.documentKind");

  const [orderId, setOrderId] = useState("");
  const isSales = role === "sales";

  const company = useCompanyDetails();
  const support = useOrderLineSupport({ role });
  const partners = usePartners(isSales ? "ar" : "ap", {});
  const salesOrders = useSalesOrders({ limit: 200 }, { enabled: isSales });
  const purchaseOrders = usePurchaseOrders({ limit: 200 }, { enabled: !isSales });
  const salesEnquiry = useSalesOrderEnquiry(isSales && orderId ? Number(orderId) : null);
  const purchaseEnquiry = usePurchaseOrderEnquiry(!isSales && orderId ? Number(orderId) : null);

  const listing = isSales ? salesOrders : purchaseOrders;
  const enquiry = isSales ? salesEnquiry : purchaseEnquiry;
  const data: OrderEnquiry | undefined = enquiry.data;

  const orderOptions = useMemo(
    () =>
      (listing.data?.items ?? []).map((row) => ({
        value: String(row.id),
        label: row.number,
        keywords: [partners.data?.find((p) => p.id === row.partner_id)?.name ?? ""],
      })),
    [listing.data, partners.data],
  );

  const currency = useMemo(() => {
    const found = support.currencies.find((c) => c.id === data?.currency_id);
    return {
      code: found?.code ?? "",
      decimalPlaces: found?.decimal_places ?? 0,
      symbol: found?.symbol ?? null,
    };
  }, [support.currencies, data?.currency_id]);

  const statusLabel = (value: string) =>
    isSales ? salesStatus(value) : purchaseStatus(value);
  const statusTone = (value: string) =>
    (isSales ? SALES_ORDER_STATUS_TONE : PURCHASE_ORDER_STATUS_TONE)[value] ?? "neutral";

  /** A quantity at the **item's** base-unit decimals — never one scale for the whole table.
   * `ordered`, `fulfilled` and `remaining` are all in that unit, which is what decision 4
   * counts in, so the unit travels with the figure. */
  const quantity = (itemId: number, value: string) =>
    formatQuantity(Number(value), support.quantityDecimals(itemId));
  const money = (value: string) => formatMoney(Number(value), currency);

  /** Where a fulfilling document opens. A goods receipt is its own screen; everything else is
   * a partner document, and which subledger it belongs to is settled by which enquiry this is
   * — a sales order is only ever fulfilled by AR documents and a purchase order by AP. */
  function documentPath(document: LinkedDocument): string {
    if (document.kind === "goods_received_note") return `/oe/goods-received/${document.document_id}`;
    return `/${isSales ? "ar" : "ap"}/documents/${document.document_id}`;
  }

  function handleExport() {
    if (!data) return;
    exportToCsv(
      `order-enquiry-${data.number}`,
      [
        t("lineNo"),
        tc("item"),
        tr("name"),
        t("unit"),
        t("ordered"),
        isSales ? t("invoiced") : t("received"),
        t("remaining"),
        t("backordered"),
        tc("total"),
      ],
      data.lines.map((line) => [
        line.line_no,
        line.item_code,
        line.item_name,
        support.uomById.get(line.uom_id)?.code ?? "",
        line.ordered,
        line.fulfilled,
        line.remaining,
        line.backordered,
        line.gross_amount,
      ]),
    );
  }

  return (
    <ReportPage
      title={isSales ? t("salesTitle") : t("purchaseTitle")}
      subtitle={isSales ? t("salesSubtitle") : t("purchaseSubtitle")}
      companyName={company.data?.name}
      asOfLabel={data ? dotted(data.number, data.partner_name, formatDate(data.order_date)) : undefined}
      onExportCsv={data && data.lines.length > 0 ? handleExport : undefined}
      filters={
        <div className="max-w-md rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4">
          <Field label={isSales ? t("salesOrder") : t("purchaseOrder")}>
            <Combobox
              options={orderOptions}
              value={orderId}
              onValueChange={setOrderId}
              placeholder={t("chooseOrder")}
            />
          </Field>
        </div>
      }
    >
      {!orderId ? (
        <p className="py-10 text-center text-sm text-[var(--vinea-ink-subtle)]">
          {t("chooseFirst")}
        </p>
      ) : enquiry.isError ? (
        /* **An error is not an empty report.** Rendering "nothing to report" over a request
           that failed is the P4 defect rule 13 is written against, and it is exactly what hid
           this endpoint's 500 for three steps: the screen looked like an order nothing had
           been raised against. Say what the service said, and say that it is a failure. */
        <p
          className="py-10 text-center text-sm text-[var(--vinea-danger)]"
          data-testid="enquiry-error"
        >
          {(enquiry.error as { message?: string })?.message ?? t("enquiryFailed")}
        </p>
      ) : !data ? (
        <p className="py-10 text-center text-sm text-[var(--vinea-ink-subtle)]">
          {enquiry.isLoading ? tc("loading") : tr("noRows")}
        </p>
      ) : (
        <>
          <ReportPanel>
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <div>
                <dt className="text-xs text-[var(--vinea-ink-muted)]">{tc("status")}</dt>
                <dd className="pt-1">
                  <StatusChip tone={statusTone(data.status)}>
                    {statusLabel(data.status)}
                  </StatusChip>
                </dd>
              </div>
              <div>
                <dt className="text-xs text-[var(--vinea-ink-muted)]">
                  {isSales ? tc("customer") : tc("supplier")}
                </dt>
                <dd className="pt-1 text-sm text-[var(--vinea-ink)]">{data.partner_name}</dd>
              </div>
              <div>
                <dt className="text-xs text-[var(--vinea-ink-muted)]">{tc("total")}</dt>
                <dd
                  data-testid="enquiry-order-total"
                  className="pt-1 font-mono text-lg tabular-nums text-[var(--vinea-ink)]"
                >
                  {money(data.total_amount)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-[var(--vinea-ink-muted)]">{t("expectedDate")}</dt>
                <dd className="pt-1 text-sm text-[var(--vinea-ink)]">
                  {data.expected_date ? formatDate(data.expected_date) : tc("emptyValue")}
                </dd>
              </div>
            </dl>
            <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">{data.description}</p>
            <p className="pt-2 text-xs">
              <Link
                href={`/oe/${isSales ? "sales-orders" : "purchase-orders"}/${data.order_id}`}
                className="font-medium text-[var(--vinea-brand)] hover:underline"
              >
                {t("openOrder", { number: data.number })}
              </Link>
            </p>
          </ReportPanel>

          <ReportPanel>
            <h2 className="pb-2 text-xs font-semibold uppercase tracking-wide text-[var(--vinea-ink-muted)]">
              {t("lines")}
            </h2>
            <Table>
              <THead>
                <TR>
                  <TH className="w-10">{t("lineNo")}</TH>
                  <TH className="w-28">{tc("item")}</TH>
                  <TH>{tr("name")}</TH>
                  <TH className="w-16">{t("unit")}</TH>
                  <TH className="w-24 text-right">{t("ordered")}</TH>
                  <TH className="w-24 text-right">{isSales ? t("invoiced") : t("received")}</TH>
                  <TH className="w-24 text-right">{t("remaining")}</TH>
                  {isSales && <TH className="w-24 text-right">{t("backordered")}</TH>}
                  <TH className="w-32 text-right">{tc("total")}</TH>
                </TR>
              </THead>
              <TBody>
                {data.lines.map((line) => (
                  <TR key={line.line_id}>
                    <TD className="font-mono text-xs text-[var(--vinea-ink-subtle)]">
                      {line.line_no}
                    </TD>
                    <TD
                      className={cn(
                        "font-mono text-xs font-semibold text-[var(--vinea-brand)]",
                        // A kit's components are indented under the line the customer saw, so
                        // the explosion reads as one promise rather than several.
                        line.kit_parent_line_id !== null && "pl-6 font-normal opacity-80",
                      )}
                    >
                      {line.item_code}
                    </TD>
                    <TD className="text-xs text-[var(--vinea-ink-muted)]">{line.item_name}</TD>
                    <TD className="font-mono text-xs text-[var(--vinea-ink-subtle)]">
                      {support.uomById.get(line.uom_id)?.code ?? ""}
                    </TD>
                    <TD
                      className="text-right font-mono text-xs tabular-nums"
                      data-testid="enquiry-line-ordered"
                    >
                      {quantity(line.item_id, line.ordered)}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]">
                      {quantity(line.item_id, line.fulfilled)}
                    </TD>
                    <TD
                      className="text-right font-mono text-xs tabular-nums"
                      data-testid="enquiry-line-remaining"
                    >
                      {quantity(line.item_id, line.remaining)}
                    </TD>
                    {isSales && (
                      <TD
                        className={cn(
                          "text-right font-mono text-xs tabular-nums",
                          Number(line.backordered) > 0 && "text-[var(--vinea-warning)]",
                        )}
                        data-testid="enquiry-line-backordered"
                      >
                        {quantity(line.item_id, line.backordered)}
                      </TD>
                    )}
                    <TD className="text-right font-mono text-xs tabular-nums">
                      {money(line.gross_amount)}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <p className="pt-2 text-xs text-[var(--vinea-ink-subtle)]">{t("linesNote")}</p>
          </ReportPanel>

          <ReportPanel>
            <h2 className="pb-2 text-xs font-semibold uppercase tracking-wide text-[var(--vinea-ink-muted)]">
              {t("documents")}
            </h2>
            {data.documents.length === 0 ? (
              <p className="py-6 text-center text-xs text-[var(--vinea-ink-subtle)]">
                {t("noDocuments")}
              </p>
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH className="w-32">{t("document")}</TH>
                    <TH className="w-32">{t("kind")}</TH>
                    <TH className="w-28">{tr("date")}</TH>
                    <TH className="w-28">{tc("status")}</TH>
                    <TH className="w-24 text-right">{tc("quantity")}</TH>
                    <TH className="w-32">{tr("entry")}</TH>
                    <TH className="w-32">{t("stockEntry")}</TH>
                  </TR>
                </THead>
                <TBody>
                  {data.documents.map((document) => {
                    const reversed = document.status === "reversed";
                    return (
                      <TR key={`${document.kind}-${document.document_id}`}>
                        <TD>
                          <Link
                            href={documentPath(document)}
                            data-testid="enquiry-document"
                            className={cn(
                              "inline-flex items-center gap-1 font-mono text-xs font-semibold text-[var(--vinea-brand)] underline",
                              reversed && "line-through opacity-70",
                            )}
                          >
                            {document.number}
                            <ExternalLink className="size-3" />
                          </Link>
                        </TD>
                        <TD className="text-xs text-[var(--vinea-ink-muted)]">
                          {kinds.has(document.kind) ? kinds(document.kind) : document.kind}
                        </TD>
                        <TD className="text-xs text-[var(--vinea-ink-muted)]">
                          {formatDate(document.document_date)}
                        </TD>
                        <TD>
                          <StatusChip tone={reversed ? "neutral" : "success"}>
                            {document.status}
                          </StatusChip>
                        </TD>
                        <TD
                          className={cn(
                            "text-right font-mono text-xs tabular-nums",
                            reversed && "line-through opacity-70",
                          )}
                        >
                          {formatOrderQuantity(document.quantity)}
                        </TD>
                        <TD>
                          <EntryLink
                            id={document.journal_entry_id}
                            number={document.journal_entry_number}
                            empty={tc("emptyValue")}
                          />
                        </TD>
                        <TD>
                          {/* The companion (decision 2). Blank is a real answer: a document
                              with no valued stock line has no companion and claims no `STK-`
                              number at all. */}
                          <EntryLink
                            id={document.stock_entry_id}
                            number={document.stock_entry_number}
                            empty={tc("emptyValue")}
                          />
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
            )}
            <p className="pt-2 text-xs text-[var(--vinea-ink-subtle)]">{t("documentsNote")}</p>
          </ReportPanel>
        </>
      )}
    </ReportPage>
  );
}

function EntryLink({
  id,
  number,
  empty,
}: {
  id: number | null;
  number: string | null;
  empty: string;
}) {
  if (id === null) {
    return <span className="text-xs text-[var(--vinea-ink-subtle)]">{empty}</span>;
  }
  return (
    <Link
      href={`/gl/entries/${id}`}
      data-testid="enquiry-entry"
      className="inline-flex items-center gap-1 font-mono text-xs text-[var(--vinea-brand)] underline"
    >
      {number ?? id}
      <ExternalLink className="size-3" />
    </Link>
  );
}
