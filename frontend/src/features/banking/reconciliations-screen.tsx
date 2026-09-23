"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Plus } from "lucide-react";
import { Button } from "@/design/components/button";
import { Combobox } from "@/design/components/combobox";
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
import { ReconciliationStatus } from "@/lib/api-enums";
import { newDraftId } from "@/lib/drafts";
import { dotted, formatDate, formatQuantity, todayIso, trimDecimalString } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { BankAccountFilter, useAccountMoney, useBankAccountChoice } from "./account-picker";
import { useDefaultStatementBalance, useOpenReconciliation, useReconciliations } from "./hooks";

/**
 * **Bank reconciliation** — Transactions → General Ledger, the row after Bank statements (P8
 * step 7, Appendix C.1.14).
 *
 * The listing per account: every reconciliation with its date, its status and — once locked —
 * the four figures it was signed off at. An open one has no stored figures; they are live on its
 * workspace, and a column that showed them here would be a second computation of them.
 *
 * **New** asks for the account, the date and the statement balance, and fills the balance from
 * the latest statement line's balance column on or before the date — the function the server
 * falls back to, so what the dialog shows is what an empty field would get. Two of the service's
 * refusals are known before the button and said there: one open reconciliation per account
 * (`reconciliation_open_exists`), and a date after the last locked one
 * (`reconciliation_date_order`).
 */
export function ReconciliationsScreen({ requestedAccountId }: { requestedAccountId: number | null }) {
  const t = useTranslations("banking.reconciliations");
  const tc = useTranslations("banking.common");
  const router = useRouter();
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canLock = useHasPermission()("bank:reconcile_lock");
  const company = useCompanyDetails();

  const { accounts, banks, selected } = useBankAccountChoice(requestedAccountId);
  const { money } = useAccountMoney(selected);
  const reconciliations = useReconciliations(selected?.id ?? null);
  const rows = reconciliations.data ?? [];

  const [newOpen, setNewOpen] = useState(false);
  const [newAccountId, setNewAccountId] = useState<number | null>(null);
  const [newKey, setNewKey] = useState(() => newDraftId());
  const [newDate, setNewDate] = useState(todayIso);
  const [balance, setBalance] = useState("");
  const [balanceTouched, setBalanceTouched] = useState(false);
  const [newErrors, setNewErrors] = useState<Record<string, string[]>>({});
  const [newError, setNewError] = useState<string | null>(null);
  const openReconciliation = useOpenReconciliation();
  const defaultBalance = useDefaultStatementBalance(newOpen ? newAccountId : null, newDate);
  // The dialog's account may differ from the listing's; its refusals are about *its* rows.
  const newAccountRows = useReconciliations(newOpen ? newAccountId : null).data ?? [];

  // The default follows the date until the person types over it.
  useEffect(() => {
    if (!balanceTouched && defaultBalance.data) {
      const found = defaultBalance.data.statement_balance;
      setBalance(found === null ? "" : trimDecimalString(found));
    }
  }, [defaultBalance.data, balanceTouched]);

  const standing = newAccountRows.find((row) => row.status === ReconciliationStatus.OPEN) ?? null;
  const latestLocked = newAccountRows
    .filter((row) => row.status === ReconciliationStatus.LOCKED)
    .sort((a, b) => b.reconciliation_date.localeCompare(a.reconciliation_date))[0];

  function newBlockedReason(): string | null {
    if (newAccountId === null) return t("chooseAccount");
    if (standing) return t("openExists", { number: standing.number });
    if (latestLocked && newDate <= latestLocked.reconciliation_date) {
      return t("dateOrder", {
        number: latestLocked.number,
        date: formatDate(latestLocked.reconciliation_date),
      });
    }
    if (!balance.trim()) return t("balanceRequired");
    return null;
  }

  function startNew() {
    setNewAccountId(selected?.id ?? null);
    setNewDate(todayIso());
    setBalance("");
    setBalanceTouched(false);
    setNewErrors({});
    setNewError(null);
    setNewKey(newDraftId());
    setNewOpen(true);
  }

  async function handleOpen() {
    if (newAccountId === null || newBlockedReason()) return;
    setNewErrors({});
    setNewError(null);
    try {
      const opened = await openReconciliation.mutateAsync({
        payload: {
          bank_account_id: newAccountId,
          reconciliation_date: newDate,
          statement_balance: balance.trim(),
        },
        idempotencyKey: newKey,
      });
      setNewOpen(false);
      toast.show({ title: t("opened", { number: opened.number }), tone: "success" });
      router.push(`/bank/reconciliations/${opened.id}`);
    } catch (err) {
      if (isApiError(err)) {
        setNewErrors(err.fieldErrors);
        setNewError(err.message);
      } else showApiError(err, t("openFailed"));
    }
  }

  const blocked = newOpen ? newBlockedReason() : null;

  return (
    <ReportPage
      title={t("title")}
      subtitle={t("subtitle")}
      companyName={company.data?.name}
      asOfLabel={selected ? `${selected.code} · ${selected.name}` : undefined}
      filters={
        <div className="flex flex-wrap items-end gap-3">
          <BankAccountFilter banks={banks} selected={selected} basePath="/bank/reconciliations" />
          {selected ? (
            <p className="flex h-10 items-center text-xs text-[var(--vinea-ink-muted)]" data-testid="last-reconciled">
              {selected.last_reconciled_at
                ? t("lastReconciled", {
                    date: formatDate(selected.last_reconciled_at),
                    balance: money(selected.last_reconciled_balance),
                  })
                : t("neverReconciled")}
            </p>
          ) : null}
          {canLock && selected ? (
            <Button variant="primary" onClick={startNew} className="ml-auto gap-1.5">
              <Plus className="size-3.5" /> {t("new")}
            </Button>
          ) : null}
        </div>
      }
    >
      <ReportPanel>
        {rows.length === 0 ? (
          <QueryState
            query={selected ? reconciliations : accounts}
            isEmpty
            empty={selected ? t("empty") : t("noBankAccount")}
            testId="reconciliations"
          />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-32">{t("number")}</TH>
                <TH className="w-28">{t("date")}</TH>
                <TH className="w-24">{tc("status")}</TH>
                <TH className="text-right">{t("statementBalance")}</TH>
                <TH className="text-right">{t("ledgerBalance")}</TH>
                <TH className="text-right">{t("outstanding")}</TH>
                <TH className="text-right">{t("difference")}</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map((row) => {
                const locked = row.status === ReconciliationStatus.LOCKED;
                return (
                  <TR key={row.id} data-reconciliation={row.number}>
                    <TD className="font-mono text-xs font-semibold">
                      <Link
                        href={`/bank/reconciliations/${row.id}`}
                        className="text-[var(--vinea-brand)] underline"
                      >
                        {row.number}
                      </Link>
                    </TD>
                    <TD className="text-xs">{formatDate(row.reconciliation_date)}</TD>
                    <TD>
                      <StatusChip tone={locked ? "success" : "info"}>{t(`statusLabel.${row.status}`)}</StatusChip>
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums">{money(row.statement_balance)}</TD>
                    <TD className="text-right font-mono text-xs tabular-nums">
                      {locked ? money(row.ledger_balance) : t("liveOnWorkspace")}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums">
                      {locked ? money(row.outstanding_total) : tc("emptyValue")}
                    </TD>
                    <TD className="text-right font-mono text-xs tabular-nums">
                      {locked ? money(row.difference) : tc("emptyValue")}
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        )}
        {rows.length > 0 ? (
          <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]" data-testid="reconciliation-count">
            {t("count", {
              count: formatQuantity(rows.length, 0),
              locked: formatQuantity(rows.filter((row) => row.status === ReconciliationStatus.LOCKED).length, 0),
            })}
          </p>
        ) : null}
      </ReportPanel>

      <Dialog open={newOpen} onOpenChange={setNewOpen}>
        <DialogContent
          title={t("newTitle")}
          description={t("newDescription")}
        >
          <div className="space-y-3">
            <Field label={tc("bankAccount")} error={newErrors.bank_account_id?.[0]}>
              <Combobox
                options={banks.map((row) => ({ value: String(row.id), label: dotted(row.code, row.name) }))}
                value={newAccountId === null ? "" : String(newAccountId)}
                onValueChange={(value) => {
                  setNewAccountId(Number(value));
                  if (!balanceTouched) setBalance("");
                }}
                placeholder={tc("chooseBankAccount")}
              />
            </Field>
            <Field label={t("reconciliationDate")} error={newErrors.reconciliation_date?.[0]}>
              <IsoDatePicker
                value={newDate}
                onValueChange={(value) => {
                  setNewDate(value);
                  if (!balanceTouched) setBalance("");
                }}
              />
            </Field>
            <Field
              label={t("statementBalance")}
              error={newErrors.statement_balance?.[0]}
              hint={
                balanceTouched
                  ? undefined
                  : defaultBalance.data?.statement_balance
                    ? t("balanceDefaulted")
                    : t("balanceKeyed")
              }
            >
              <Input
                value={balance}
                inputMode="decimal"
                onChange={(e) => {
                  setBalanceTouched(true);
                  setBalance(e.target.value);
                }}
                className="font-mono"
              />
            </Field>
            {newError ? (
              <p
                data-testid="new-error"
                className="rounded-[var(--radius-control)] bg-[var(--vinea-danger-soft)] px-3 py-2 text-sm text-[var(--vinea-danger)]"
              >
                {newError}
              </p>
            ) : null}
          </div>
          <div className="mt-4 flex items-center justify-end gap-2">
            {blocked ? (
              <p className="mr-auto text-xs text-[var(--vinea-ink-muted)]" data-testid="new-blocked">
                {blocked}
              </p>
            ) : null}
            <Button variant="ghost" onClick={() => setNewOpen(false)}>
              {tc("cancel")}
            </Button>
            <Button
              variant="primary"
              disabled={blocked !== null || openReconciliation.isPending}
              onClick={handleOpen}
            >
              {t("open")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </ReportPage>
  );
}
