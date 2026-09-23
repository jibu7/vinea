"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/design/components/button";
import { Dialog, DialogContent } from "@/design/components/dialog";
import { Field, Input } from "@/design/components/input";
import { QueryState } from "@/design/components/query-state";
import { ReportPage, ReportPanel } from "@/design/components/report-page";
import { StatusChip } from "@/design/components/status-chip";
import { TBody, TD, TH, THead, TR, Table } from "@/design/components/table";
import { useToast } from "@/design/components/toast";
import { isApiError, useHasPermission } from "@/features/auth/hooks";
import { useCompanyDetails } from "@/features/gl/hooks";
import { StatementStatus } from "@/lib/api-enums";
import { formatDate, formatQuantity } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { useAccountMoney } from "./account-picker";
import { useBankAccounts, useStatement, useVoidStatement } from "./hooks";
import { MatchStateChip } from "./match-state";

/**
 * One statement and its lines, **each with its match state** — the screen a person works from
 * when the question is "which of the bank's lines have we explained?".
 *
 * **Void** is the only correction a statement has (decision 3): its lines are immutable, so a
 * mistaken import is voided as a whole and the file imported again. It is refused while any line
 * is in a match (`statement_has_matches`) — and that is said beside the button, with the count,
 * rather than discovered by pressing it. The matches are withdrawn on the reconciliation
 * workspace, which is where they were made.
 */
export function StatementDetailScreen({ statementId }: { statementId: number }) {
  const t = useTranslations("banking.statementDetail");
  const ts = useTranslations("banking.statements");
  const tc = useTranslations("banking.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const canImport = useHasPermission()("bank:statement_import");
  const company = useCompanyDetails();

  const statement = useStatement(statementId);
  const accounts = useBankAccounts({ includeInactive: true });
  const data = statement.data;
  const account = (accounts.data ?? []).find((row) => row.id === data?.bank_account_id) ?? null;
  const { money } = useAccountMoney(account);
  const voidStatement = useVoidStatement();

  const [voidOpen, setVoidOpen] = useState(false);
  const [voidReason, setVoidReason] = useState("");
  const [voidError, setVoidError] = useState<string | null>(null);

  const matchedCount = (data?.lines ?? []).filter((line) => line.state.match_id !== null).length;

  /** Why Void cannot be pressed, or `null` when it can — the service's own refusal, read off
   * the lines before the button is drawn. */
  function voidBlockedReason(): string | null {
    if (!data) return null;
    if (data.status === StatementStatus.VOID) return t("alreadyVoid");
    if (matchedCount > 0) return t("hasMatches", { count: formatQuantity(matchedCount, 0) });
    return null;
  }

  async function handleVoid() {
    setVoidError(null);
    try {
      const result = await voidStatement.mutateAsync({ statementId, reason: voidReason });
      setVoidOpen(false);
      toast.show({ title: t("voided", { number: result.number }), tone: "success" });
    } catch (err) {
      if (isApiError(err)) setVoidError(err.message);
      else showApiError(err, t("voidFailed"));
    }
  }

  if (!data) {
    return (
      <ReportPage title={t("title")} companyName={company.data?.name} backHref="/bank/statements">
        <QueryState query={statement} isEmpty empty={t("notFound")} testId="statement" />
      </ReportPage>
    );
  }

  const blocked = voidBlockedReason();

  return (
    <ReportPage
      title={t("heading", { number: data.number })}
      subtitle={account ? `${account.code} · ${account.name}` : undefined}
      companyName={company.data?.name}
      asOfLabel={t("range", { from: formatDate(data.from_date), to: formatDate(data.to_date) })}
      backHref={`/bank/statements?account=${data.bank_account_id}`}
      filters={
        canImport ? (
          <div className="flex items-center justify-end gap-3">
            {blocked ? (
              <p className="text-xs text-[var(--vinea-ink-muted)]" data-testid="void-blocked">
                {blocked}
              </p>
            ) : null}
            <Button
              variant="danger"
              disabled={blocked !== null}
              onClick={() => {
                setVoidError(null);
                setVoidReason("");
                setVoidOpen(true);
              }}
            >
              {t("void")}
            </Button>
          </div>
        ) : undefined
      }
    >
      <ReportPanel>
        <dl className="grid grid-cols-2 gap-4 text-xs sm:grid-cols-4 lg:grid-cols-7" data-testid="statement-facts">
          <div>
            <dt className="text-[var(--vinea-ink-subtle)]">{tc("status")}</dt>
            <dd>
              <StatusChip tone={data.status === StatementStatus.OPEN ? "success" : "neutral"}>
                {ts(`statusLabel.${data.status}`)}
              </StatusChip>
            </dd>
          </div>
          <div>
            <dt className="text-[var(--vinea-ink-subtle)]">{ts("source")}</dt>
            <dd className="text-[var(--vinea-ink)]">{ts(`sourceLabel.${data.source}`)}</dd>
          </div>
          <div>
            <dt className="text-[var(--vinea-ink-subtle)]">{t("file")}</dt>
            <dd className="truncate font-mono text-[var(--vinea-ink)]">{data.file_name ?? tc("emptyValue")}</dd>
          </div>
          <div>
            <dt className="text-[var(--vinea-ink-subtle)]">{ts("opening")}</dt>
            <dd className="font-mono tabular-nums text-[var(--vinea-ink)]">{money(data.opening_balance)}</dd>
          </div>
          <div>
            <dt className="text-[var(--vinea-ink-subtle)]">{ts("closing")}</dt>
            <dd className="font-mono tabular-nums text-[var(--vinea-ink)]" data-testid="statement-closing">
              {money(data.closing_balance)}
            </dd>
          </div>
          <div>
            <dt className="text-[var(--vinea-ink-subtle)]">{ts("lines")}</dt>
            <dd className="font-mono tabular-nums text-[var(--vinea-ink)]" data-testid="statement-line-count">
              {formatQuantity(data.line_count, 0)}
            </dd>
          </div>
          <div>
            <dt className="text-[var(--vinea-ink-subtle)]">{t("matchedOf")}</dt>
            <dd className="font-mono tabular-nums text-[var(--vinea-ink)]" data-testid="statement-matched">
              {t("matchedCount", {
                matched: formatQuantity(matchedCount, 0),
                total: formatQuantity(data.lines.length, 0),
              })}
            </dd>
          </div>
        </dl>
      </ReportPanel>

      <ReportPanel>
        {data.lines.length === 0 ? (
          <QueryState query={statement} isEmpty empty={t("noLines")} testId="statement-lines" />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH className="w-12 text-right">{t("lineNo")}</TH>
                <TH className="w-28">{ts("valueDate")}</TH>
                <TH>{ts("lineDescription")}</TH>
                <TH className="w-32">{ts("lineReference")}</TH>
                <TH className="w-36 text-right">{t("amount")}</TH>
                <TH className="w-36 text-right">{t("balance")}</TH>
                <TH className="w-64">{t("match")}</TH>
              </TR>
            </THead>
            <TBody>
              {data.lines.map((line) => (
                <TR key={line.id} data-statement-line={line.line_no}>
                  <TD className="text-right font-mono text-xs">{line.line_no}</TD>
                  <TD className="text-xs">{formatDate(line.value_date)}</TD>
                  <TD className="text-xs">{line.description}</TD>
                  <TD className="font-mono text-xs">{line.reference ?? tc("emptyValue")}</TD>
                  <TD className="text-right font-mono text-xs tabular-nums">{money(line.amount)}</TD>
                  <TD className="text-right font-mono text-xs tabular-nums">{money(line.balance_after)}</TD>
                  <TD className="text-xs">
                    <MatchStateChip
                      rule={line.state.match_rule}
                      reconciliationNumber={line.state.reconciliation_number}
                      ledgerLineCount={line.state.journal_line_count}
                    />
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </ReportPanel>

      <Dialog open={voidOpen} onOpenChange={setVoidOpen}>
        <DialogContent title={t("voidTitle", { number: data.number })} description={t("voidDescription")}>
          <div className="space-y-3">
            <Field label={t("voidReason")}>
              <Input value={voidReason} onChange={(e) => setVoidReason(e.target.value)} />
            </Field>
            {voidError ? (
              <p
                data-testid="void-error"
                className="rounded-[var(--radius-control)] bg-[var(--vinea-danger-soft)] px-3 py-2 text-sm text-[var(--vinea-danger)]"
              >
                {voidError}
              </p>
            ) : null}
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setVoidOpen(false)}>
              {tc("cancel")}
            </Button>
            <Button variant="danger" disabled={voidStatement.isPending} onClick={handleVoid}>
              {t("confirmVoid")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </ReportPage>
  );
}
