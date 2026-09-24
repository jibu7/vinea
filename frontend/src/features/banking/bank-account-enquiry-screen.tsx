"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Combobox } from "@/design/components/combobox";
import { IsoDatePicker } from "@/design/components/date-picker";
import { Field } from "@/design/components/input";
import { QueryState } from "@/design/components/query-state";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { useCompanyDetails } from "@/features/gl/hooks";
import { BankAccountKind } from "@/lib/api-enums";
import { dotted, formatDate, formatQuantity, todayIso } from "@/lib/format";
import { useBaseCode, useMoneyIn } from "./account-picker";
import { useBankAccountEnquiry, useBankAccounts } from "./hooks";

/**
 * Enquiries → General Ledger → **Bank account enquiry** (P8 step 8, decision 10; Appendix
 * C.1.14).
 *
 * One account at a date, and the six things a person asks of it before trusting its balance:
 * what the ledger says (in the account's currency and in base), when it was last proved against
 * the bank and at what balance, what the bank has shown that the ledger has not matched, what
 * the ledger holds that the bank has not shown, the latest statement imported, and whether a
 * reconciliation is standing open. **Every figure is a link** — to the workspace where the work
 * is, or the report where the proof is — because an enquiry that shows a count of unmatched
 * lines and no way to them is a number to worry about rather than a thing to do.
 *
 * A cash account is listed too: it has a book balance and a cashbook, and no statement or
 * reconciliation (`reconciliation_needs_bank`), which the reconciliation cards say rather than
 * showing zeros that look like a clean bank.
 */
export function BankAccountEnquiryScreen({
  requestedAccountId,
  requestedAsOf,
}: {
  requestedAccountId: number | null;
  requestedAsOf: string | null;
}) {
  const t = useTranslations("banking.enquiry");
  const tc = useTranslations("banking.common");
  const router = useRouter();
  const company = useCompanyDetails();
  const moneyIn = useMoneyIn();
  const baseCode = useBaseCode();

  const accounts = useBankAccounts();
  const rows = accounts.data ?? [];
  const selected = rows.find((row) => row.id === requestedAccountId) ?? rows[0] ?? null;
  const asOf = requestedAsOf ?? todayIso();
  const enquiry = useBankAccountEnquiry(selected?.id ?? null, asOf);
  const data = enquiry.data;
  const isBank = data?.kind === BankAccountKind.BANK;

  const go = (accountId: number | null, date: string) =>
    router.push(`/gl/enquiries/bank-account?account=${accountId ?? ""}&as_of=${date}`);

  const workspace = data?.open_reconciliation_id
    ? `/bank/reconciliations/${data.open_reconciliation_id}`
    : `/bank/reconciliations?account=${data?.bank_account_id ?? ""}`;
  const cashbook = `/gl/reports/cashbooks?account=${data?.bank_account_id ?? ""}&to=${asOf}`;

  return (
    <ReportPage
      title={t("title")}
      subtitle={t("subtitle")}
      companyName={company.data?.name}
      asOfLabel={data ? t("asOfLabel", { account: dotted(data.code, data.name), date: formatDate(asOf) }) : undefined}
      filters={
        <div className="flex flex-wrap items-end gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-4">
          <Field label={tc("bankAccount")} className="w-72">
            <Combobox
              options={rows.map((row) => ({ value: String(row.id), label: dotted(row.code, row.name) }))}
              value={selected ? String(selected.id) : ""}
              onValueChange={(value) => go(value ? Number(value) : null, asOf)}
              placeholder={tc("chooseBankAccount")}
            />
          </Field>
          <Field label={t("asOf")} className="w-44">
            <IsoDatePicker value={asOf} onValueChange={(value) => go(selected?.id ?? null, value)} />
          </Field>
        </div>
      }
    >
      {!data ? (
        <ReportPanel>
          <QueryState
            query={selected ? enquiry : accounts}
            isEmpty
            empty={rows.length === 0 ? t("noAccounts") : t("chooseAccount")}
            testId="enquiry"
          />
        </ReportPanel>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          <Figure label={t("bookBalance")}>
            <Link href={cashbook} data-testid="enquiry-book-balance" className={LINK_BIG}>
              {moneyIn(data.book_balance, data.currency_code)}
            </Link>
            {data.currency_code !== baseCode && (
              <p className="pt-1 font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]" data-testid="enquiry-book-balance-base">
                {t("inBase", { amount: moneyIn(data.book_balance_base, baseCode) })}
              </p>
            )}
            <p className="pt-1 text-xs text-[var(--vinea-ink-subtle)]">{t("bookBalanceNote")}</p>
          </Figure>

          <Figure label={t("lastReconciliation")}>
            {!isBank ? (
              <p className="text-sm text-[var(--vinea-ink-muted)]">{t("cashNotReconciled")}</p>
            ) : data.last_reconciliation_id ? (
              <>
                <Link
                  href={`/gl/reports/bank-reconciliation?reconciliation=${data.last_reconciliation_id}`}
                  data-testid="enquiry-last-reconciliation"
                  className={LINK_BIG}
                >
                  {data.last_reconciliation_number}
                </Link>
                <p className="pt-1 text-xs text-[var(--vinea-ink-muted)]" data-testid="enquiry-last-reconciled">
                  {t("reconciledAt", {
                    date: formatDate(data.last_reconciled_at ?? ""),
                    balance: moneyIn(data.last_reconciled_balance, data.currency_code),
                  })}
                </p>
              </>
            ) : (
              <p className="text-sm text-[var(--vinea-ink-muted)]">{t("neverReconciled")}</p>
            )}
          </Figure>

          <Figure label={t("openReconciliation")}>
            {!isBank ? (
              <p className="text-sm text-[var(--vinea-ink-muted)]">{t("cashNotReconciled")}</p>
            ) : data.open_reconciliation_id ? (
              <Link
                href={`/bank/reconciliations/${data.open_reconciliation_id}`}
                data-testid="enquiry-open-reconciliation"
                className={LINK_BIG}
              >
                {data.open_reconciliation_number}
              </Link>
            ) : (
              <Link
                href={`/bank/reconciliations?account=${data.bank_account_id}`}
                data-testid="enquiry-open-reconciliation"
                className="text-sm font-medium text-[var(--vinea-brand)] hover:underline"
              >
                {t("noneOpen")}
              </Link>
            )}
          </Figure>

          <Figure label={t("unmatchedStatement")}>
            {!isBank ? (
              <p className="text-sm text-[var(--vinea-ink-muted)]">{t("cashNoStatement")}</p>
            ) : (
              <Link href={workspace} data-testid="enquiry-unmatched" className="block hover:underline">
                <span className={COUNT} data-testid="enquiry-unmatched-count">
                  {formatQuantity(data.unmatched_statement_count, 0)}
                </span>
                <span className={SUM} data-testid="enquiry-unmatched-total">
                  {moneyIn(data.unmatched_statement_total, data.currency_code)}
                </span>
              </Link>
            )}
          </Figure>

          <Figure label={t("outstanding")}>
            <Link
              href={isBank ? workspace : cashbook}
              data-testid="enquiry-outstanding"
              className="block hover:underline"
            >
              <span className={COUNT} data-testid="enquiry-outstanding-count">
                {formatQuantity(data.outstanding_count, 0)}
              </span>
              <span className={SUM} data-testid="enquiry-outstanding-total">
                {moneyIn(data.outstanding_total, data.currency_code)}
              </span>
            </Link>
            <p className="pt-1 text-xs text-[var(--vinea-ink-subtle)]">{t("outstandingNote")}</p>
          </Figure>

          <Figure label={t("latestStatement")}>
            {!isBank ? (
              <p className="text-sm text-[var(--vinea-ink-muted)]">{t("cashNoStatement")}</p>
            ) : data.latest_statement_id ? (
              <>
                <Link
                  href={`/bank/statements/${data.latest_statement_id}`}
                  data-testid="enquiry-latest-statement"
                  className={LINK_BIG}
                >
                  {data.latest_statement_number}
                </Link>
                <p className="pt-1 text-xs text-[var(--vinea-ink-muted)]">
                  {t("statementTo", { date: formatDate(data.latest_statement_to ?? "") })}
                </p>
              </>
            ) : (
              <Link
                href={`/bank/statements?account=${data.bank_account_id}`}
                data-testid="enquiry-latest-statement"
                className="text-sm font-medium text-[var(--vinea-brand)] hover:underline"
              >
                {t("noStatement")}
              </Link>
            )}
          </Figure>
        </div>
      )}
    </ReportPage>
  );
}

const LINK_BIG =
  "font-mono text-xl font-semibold tabular-nums text-[var(--vinea-brand)] hover:underline";
const COUNT = "block font-mono text-xl font-semibold tabular-nums text-[var(--vinea-brand)]";
const SUM = "block pt-1 font-mono text-xs tabular-nums text-[var(--vinea-ink-muted)]";

function Figure({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <ReportPanel>
      <p className="pb-2 text-xs font-medium text-[var(--vinea-ink-muted)]">{label}</p>
      {children}
    </ReportPanel>
  );
}
