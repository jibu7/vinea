"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { Plus } from "lucide-react";
import { buttonVariants } from "@/design/components/button";
import { QueryState } from "@/design/components/query-state";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useHasPermission } from "@/features/auth/hooks";
import { useCompanyDetails } from "@/features/gl/hooks";
import { PaymentRunStatus } from "@/lib/api-enums";
import { cn } from "@/lib/cn";
import { formatDate, formatQuantity } from "@/lib/format";
import { BankAccountFilter, useAccountMoney, useBankAccountChoice } from "./account-picker";
import { usePaymentRuns } from "./hooks";

/**
 * **Payment runs** — Transactions → Accounts Payable, after Payment (P8 step 7, Appendix
 * C.1.15).
 *
 * A run pays many suppliers from one bank account in one press (decision 7): one ordinary P4
 * `PMT-` and one allocation per supplier, all in one transaction, and the bank shows the whole
 * run as a single debit carrying its number. This is the listing per account — number, date,
 * how many suppliers, the total that left the bank, and whether it stands.
 *
 * **Bank accounts only**, as on Bank statements: a run from a cash account is refused
 * `payment_run_needs_bank` (a bulk transfer is a banking act), so a till is not offered.
 */
export function PaymentRunsScreen({ requestedAccountId }: { requestedAccountId: number | null }) {
  const t = useTranslations("banking.paymentRuns");
  const tc = useTranslations("banking.common");
  const canPost = useHasPermission()("bank:payment_run_post");
  const company = useCompanyDetails();

  const { accounts, banks, selected } = useBankAccountChoice(requestedAccountId);
  const { money } = useAccountMoney(selected);
  const runs = usePaymentRuns(selected?.id ?? null);
  const rows = runs.data ?? [];

  return (
    <ReportPage
      title={t("title")}
      subtitle={t("subtitle")}
      companyName={company.data?.name}
      asOfLabel={selected ? `${selected.code} · ${selected.name}` : undefined}
      filters={
        <div className="flex flex-wrap items-end gap-3">
          <BankAccountFilter banks={banks} selected={selected} basePath="/ap/payment-runs" />
          {canPost && selected ? (
            <Link
              href={`/ap/payment-runs/new?account=${selected.id}`}
              className={cn(buttonVariants({ variant: "primary" }), "ml-auto gap-1.5")}
            >
              <Plus className="size-3.5" /> {t("new")}
            </Link>
          ) : null}
        </div>
      }
    >
      <ReportPanel>
        {rows.length === 0 ? (
          <QueryState
            query={selected ? runs : accounts}
            isEmpty
            empty={selected ? t("empty") : t("noBankAccount")}
            testId="payment-runs"
          />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-32">{t("number")}</TH>
                <TH className="w-28">{t("paymentDate")}</TH>
                <TH className="w-24 text-right">{t("suppliers")}</TH>
                <TH className="text-right">{t("total")}</TH>
                <TH className="w-28">{tc("status")}</TH>
                <TH>{t("reversalReason")}</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map((row) => (
                <TR key={row.id} data-payment-run={row.number}>
                  <TD className="font-mono text-xs font-semibold">
                    <Link href={`/ap/payment-runs/${row.id}`} className="text-[var(--vinea-brand)] underline">
                      {row.number}
                    </Link>
                  </TD>
                  <TD className="text-xs">{formatDate(row.payment_date)}</TD>
                  <TD className="text-right font-mono text-xs tabular-nums">
                    {formatQuantity(row.supplier_count, 0)}
                  </TD>
                  <TD className="text-right font-mono text-xs tabular-nums whitespace-nowrap">
                    {money(row.total)}
                  </TD>
                  <TD>
                    <StatusChip tone={row.status === PaymentRunStatus.POSTED ? "success" : "neutral"}>
                      {t(`statusLabel.${row.status}`)}
                    </StatusChip>
                  </TD>
                  <TD className="text-xs text-[var(--vinea-ink-muted)]">
                    {row.reversal_reason ?? tc("emptyValue")}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
        {rows.length > 0 ? (
          <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]" data-testid="payment-run-count">
            {t("count", {
              count: formatQuantity(rows.length, 0),
              posted: formatQuantity(rows.filter((row) => row.status === PaymentRunStatus.POSTED).length, 0),
            })}
          </p>
        ) : null}
      </ReportPanel>
    </ReportPage>
  );
}
