"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Lock, Sparkles, Unlock } from "lucide-react";
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
import { ReconciliationStatus } from "@/lib/api-enums";
import { newDraftId } from "@/lib/drafts";
import { formatDate, formatQuantity, roundHalfUp } from "@/lib/format";
import { useApiErrorToast } from "@/lib/use-api-error-toast";
import { useAccountMoney } from "./account-picker";
import {
  useAccountStatementLines,
  useAutoMatch,
  useBankAccounts,
  useCreateMatch,
  useLedgerLines,
  useLockReconciliation,
  useReconciliation,
  useReconciliations,
  useReopenReconciliation,
  useTick,
  useUnmatch,
} from "./hooks";
import { MatchStateChip } from "./match-state";
import { PostFromLineDrawer, usePostFromLinePermissions } from "./post-from-line-drawer";
import type { AutoMatchResult, StatementLine } from "./types";

function toggle(set: Set<number>, id: number): Set<number> {
  const next = new Set(set);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

/**
 * The **reconciliation workspace** — `/bank/reconciliations/{id}` (P8 step 7, decisions 4 and 5).
 *
 * The bank's record on the left, the ledger on the right, the four figures across the top:
 *
 *     difference = statement balance − (ledger balance − outstanding)
 *
 * **Live** while the reconciliation is open — every match, tick and posting moves them, because
 * each invalidates the reconciliation it changed — and the **stored** figures once it is locked,
 * which never move again (a late line shows as "dated inside `BRC-n`" on the next one instead).
 *
 * Both panes put the work first: unmatched statement lines, then outstanding ledger lines, then
 * the matched rows with what each is matched *to*. A person selects on both sides and presses
 * **Match**; the balance of the selection is shown beside the button before it is pressed, and a
 * selection that does not balance comes back `match_unbalanced` with the difference, inline —
 * the difference is never stored, it is *posted*, which is what **Post from line** is for.
 *
 * **Lock** is refused while any statement line is unmatched or the difference is not zero, and
 * both are said beside the button before it is pressed, each with its figure. **Reopen** is on
 * the account's latest locked one only (`reconciliation_not_latest`), with a reason.
 *
 * The buttons are drawn for the permissions that press them and for nobody else: a read-only
 * member sees both panes and the figures, and no button at all.
 */
export function ReconciliationWorkspace({ reconciliationId }: { reconciliationId: number }) {
  const t = useTranslations("banking.workspace");
  const tr = useTranslations("banking.reconciliations");
  const tc = useTranslations("banking.common");
  const toast = useToast();
  const showApiError = useApiErrorToast();
  const has = useHasPermission();
  const canReconcile = has("bank:reconcile");
  const canLock = has("bank:reconcile_lock");
  const company = useCompanyDetails();

  const reconciliation = useReconciliation(reconciliationId);
  const rec = reconciliation.data;
  const accounts = useBankAccounts({ includeInactive: true });
  const account = (accounts.data ?? []).find((row) => row.id === rec?.bank_account_id) ?? null;
  const { currency, money } = useAccountMoney(account);
  const accountId = rec?.bank_account_id ?? null;
  const onDate = rec?.reconciliation_date ?? null;
  const statementLines = useAccountStatementLines(accountId, onDate);
  const ledgerLines = useLedgerLines(accountId, onDate);
  const siblings = useReconciliations(accountId);

  const autoMatch = useAutoMatch();
  const createMatch = useCreateMatch();
  const tick = useTick();
  const unmatch = useUnmatch();
  const lock = useLockReconciliation();
  const reopen = useReopenReconciliation();

  const [selectedStatement, setSelectedStatement] = useState<Set<number>>(new Set());
  const [selectedLedger, setSelectedLedger] = useState<Set<number>>(new Set());
  const [matchError, setMatchError] = useState<string | null>(null);
  const [autoResult, setAutoResult] = useState<AutoMatchResult | null>(null);
  const [keyedBalance, setKeyedBalance] = useState("");
  const [lockError, setLockError] = useState<string | null>(null);
  const [lockKey, setLockKey] = useState(() => newDraftId());
  const [reopenOpen, setReopenOpen] = useState(false);
  const [reopenReason, setReopenReason] = useState("");
  const [reopenError, setReopenError] = useState<string | null>(null);
  const [reopenKey, setReopenKey] = useState(() => newDraftId());
  const [postingLine, setPostingLine] = useState<StatementLine | null>(null);

  // The keyed balance follows the stored one until somebody types over it — and again after
  // every lock or reopen, which is when the stored one can change.
  useEffect(() => {
    if (rec) setKeyedBalance(String(Number(rec.statement_balance)));
  }, [rec?.statement_balance, rec?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const isOpen = rec?.status === ReconciliationStatus.OPEN;
  const decimals = currency?.decimal_places ?? 0;
  const statements = useMemo(() => statementLines.data ?? [], [statementLines.data]);
  const ledger = useMemo(() => ledgerLines.data ?? [], [ledgerLines.data]);

  /** What each match holds on the other side, so a matched row can say what it is matched to. */
  const ledgerByMatch = useMemo(() => {
    const map = new Map<number, string[]>();
    for (const line of ledger) {
      if (line.match_id === null) continue;
      map.set(line.match_id, [...(map.get(line.match_id) ?? []), line.entry_number]);
    }
    return map;
  }, [ledger]);
  const statementByMatch = useMemo(() => {
    const map = new Map<number, string[]>();
    for (const line of statements) {
      if (line.state.match_id === null) continue;
      map.set(line.state.match_id, [...(map.get(line.state.match_id) ?? []), line.description]);
    }
    return map;
  }, [statements]);

  const selectedStatementTotal = statements
    .filter((line) => selectedStatement.has(line.id))
    .reduce((sum, line) => sum + Number(line.amount), 0);
  const selectedLedgerTotal = ledger
    .filter((line) => selectedLedger.has(line.journal_line_id))
    .reduce((sum, line) => sum + Number(line.amount), 0);
  const selectionBalance = roundHalfUp(selectedStatementTotal - selectedLedgerTotal, decimals);

  if (!rec) {
    return (
      <ReportPage title={tr("title")} companyName={company.data?.name} backHref="/bank/reconciliations">
        <QueryState query={reconciliation} isEmpty empty={t("notFound")} testId="reconciliation" />
      </ReportPage>
    );
  }

  const figures = isOpen ? rec.figures : (rec.stored ?? rec.figures);
  const keyedChanged = isOpen && keyedBalance.trim() !== "" && Number(keyedBalance) !== Number(rec.statement_balance);
  /** The difference the lock will be judged at: the server's, or — while a re-keyed balance is
   * on the screen and not yet sent — the same identity over it, rounded to the currency. */
  const difference = keyedChanged
    ? roundHalfUp(
        Number(keyedBalance) - (Number(figures.ledger_balance) - Number(figures.outstanding_total)),
        decimals,
      )
    : Number(figures.difference);

  const lockReasons: string[] = [];
  if (isOpen) {
    if (figures.unmatched_statement_count > 0) {
      lockReasons.push(t("lockUnmatched", { count: formatQuantity(figures.unmatched_statement_count, 0) }));
    }
    if (difference !== 0) lockReasons.push(t("lockDifference", { difference: money(difference) }));
  }

  const lockedSiblings = (siblings.data ?? []).filter((row) => row.status === ReconciliationStatus.LOCKED);
  const latestLocked = [...lockedSiblings].sort((a, b) =>
    b.reconciliation_date.localeCompare(a.reconciliation_date),
  )[0];
  const standingOpen = (siblings.data ?? []).find(
    (row) => row.status === ReconciliationStatus.OPEN && row.id !== rec.id,
  );
  function reopenBlockedReason(): string | null {
    if (isOpen) return null;
    if (latestLocked && latestLocked.id !== rec!.id) return t("reopenNotLatest", { number: latestLocked.number });
    if (standingOpen) return t("reopenOpenExists", { number: standingOpen.number });
    return null;
  }
  const reopenBlocked = reopenBlockedReason();

  async function handleAutoMatch() {
    if (!accountId) return;
    setMatchError(null);
    try {
      setAutoResult(await autoMatch.mutateAsync({ bankAccountId: accountId }));
    } catch (err) {
      showApiError(err, t("autoMatchFailed"));
    }
  }

  async function handleMatch() {
    if (!accountId) return;
    setMatchError(null);
    try {
      await createMatch.mutateAsync({
        bank_account_id: accountId,
        statement_line_ids: [...selectedStatement],
        journal_line_ids: [...selectedLedger],
      });
      setSelectedStatement(new Set());
      setSelectedLedger(new Set());
      toast.show({ title: t("matched"), tone: "success" });
    } catch (err) {
      // `match_unbalanced` is said in the screen's own words, with the figure formatted in the
      // account's currency — the server's message carries it as a raw `NUMERIC(20,6)`.
      if (isApiError(err) && err.code === "match_unbalanced") {
        setMatchError(t("matchUnbalanced", { difference: money(selectionBalance) }));
      } else if (isApiError(err)) setMatchError(err.message);
      else showApiError(err, t("matchFailed"));
    }
  }

  async function handleTick(journalLineId: number) {
    if (!accountId) return;
    setMatchError(null);
    try {
      await tick.mutateAsync({ bank_account_id: accountId, journal_line_ids: [journalLineId] });
      setSelectedLedger((set) => {
        const next = new Set(set);
        next.delete(journalLineId);
        return next;
      });
      toast.show({ title: t("ticked"), tone: "success" });
    } catch (err) {
      if (isApiError(err)) setMatchError(err.message);
      else showApiError(err, t("matchFailed"));
    }
  }

  async function handleUnmatch(matchId: number) {
    setMatchError(null);
    try {
      await unmatch.mutateAsync({ matchId });
      toast.show({ title: t("unmatched"), tone: "success" });
    } catch (err) {
      if (isApiError(err)) setMatchError(err.message);
      else showApiError(err, t("unmatchFailed"));
    }
  }

  async function handleLock() {
    if (lockReasons.length > 0) return;
    setLockError(null);
    try {
      const locked = await lock.mutateAsync({
        reconciliationId: rec!.id,
        statementBalance: keyedChanged ? keyedBalance.trim() : null,
        idempotencyKey: lockKey,
      });
      setLockKey(newDraftId());
      toast.show({ title: t("locked", { number: locked.number }), tone: "success" });
    } catch (err) {
      if (isApiError(err)) setLockError(err.message);
      else showApiError(err, t("lockFailed"));
    }
  }

  async function handleReopen() {
    setReopenError(null);
    try {
      const reopened = await reopen.mutateAsync({
        reconciliationId: rec!.id,
        reason: reopenReason.trim(),
        idempotencyKey: reopenKey,
      });
      setReopenOpen(false);
      setReopenKey(newDraftId());
      toast.show({ title: t("reopened", { number: reopened.number }), tone: "success" });
    } catch (err) {
      if (isApiError(err)) setReopenError(err.message);
      else showApiError(err, t("reopenFailed"));
    }
  }

  /** The reason Unmatch cannot be pressed on this match, or `null` — `reconciliation_locked`,
   * said before the button rather than after it. */
  function unmatchBlockedReason(reconciliationNumber: string | null): string | null {
    return reconciliationNumber ? t("unmatchLocked", { number: reconciliationNumber }) : null;
  }

  const ambiguousCount = autoResult ? Object.keys(autoResult.ambiguous).length : 0;
  const showMatchBar = isOpen && canReconcile;

  return (
    <ReportPage
      title={t("heading", { number: rec.number })}
      subtitle={account ? `${account.code} · ${account.name}` : undefined}
      companyName={company.data?.name}
      asOfLabel={formatDate(rec.reconciliation_date)}
      backHref={`/bank/reconciliations?account=${rec.bank_account_id}`}
      filters={
        <div className="flex flex-wrap items-center gap-3">
          <StatusChip tone={isOpen ? "info" : "success"}>{tr(`statusLabel.${rec.status}`)}</StatusChip>
          {!isOpen && rec.locked_at ? (
            <span className="text-xs text-[var(--vinea-ink-muted)]">
              {t("lockedOn", { date: formatDate(rec.locked_at) })}
            </span>
          ) : null}
          {isOpen && rec.reopened_reason ? (
            <span className="text-xs text-[var(--vinea-ink-muted)]" data-testid="reopened-reason">
              {t("reopenedBecause", { reason: rec.reopened_reason })}
            </span>
          ) : null}
          <Link
            href={`/bank/statements?account=${rec.bank_account_id}`}
            className="text-xs text-[var(--vinea-brand)] underline"
          >
            {t("statementsLink")}
          </Link>
        </div>
      }
    >
      <ReportPanel>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6" data-testid="figures">
          <div>
            {isOpen && canLock ? (
              <Field label={t("statementBalance")}>
                <Input
                  value={keyedBalance}
                  inputMode="decimal"
                  onChange={(e) => setKeyedBalance(e.target.value)}
                  className="font-mono"
                />
              </Field>
            ) : (
              <>
                <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("statementBalance")}</p>
                <p className="font-mono text-sm tabular-nums text-[var(--vinea-ink)]" data-testid="figure-statement">
                  {money(figures.statement_balance)}
                </p>
              </>
            )}
          </div>
          <div>
            <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("ledgerBalance")}</p>
            <p className="font-mono text-sm tabular-nums text-[var(--vinea-ink)]" data-testid="figure-ledger">
              {money(figures.ledger_balance)}
            </p>
          </div>
          <div>
            <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("outstanding")}</p>
            <p className="font-mono text-sm tabular-nums text-[var(--vinea-ink)]" data-testid="figure-outstanding">
              {money(figures.outstanding_total)}
            </p>
            <p className="text-[11px] text-[var(--vinea-ink-subtle)]">
              {t("outstandingCount", { count: formatQuantity(figures.outstanding.length, 0) })}
            </p>
          </div>
          <div>
            <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("adjustedBank")}</p>
            <p className="font-mono text-sm tabular-nums text-[var(--vinea-ink)]" data-testid="figure-adjusted">
              {money(
                keyedChanged
                  ? roundHalfUp(Number(keyedBalance) + Number(figures.outstanding_total), decimals)
                  : figures.adjusted_bank_balance,
              )}
            </p>
          </div>
          <div>
            <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("unmatchedStatement")}</p>
            <p className="font-mono text-sm tabular-nums text-[var(--vinea-ink)]" data-testid="figure-unmatched">
              {formatQuantity(figures.unmatched_statement_count, 0)}
            </p>
          </div>
          <div>
            <p className="text-xs text-[var(--vinea-ink-subtle)]">{t("difference")}</p>
            <p
              className={`font-mono text-sm font-semibold tabular-nums ${
                difference === 0 ? "text-[var(--vinea-success)]" : "text-[var(--vinea-danger)]"
              }`}
              data-testid="figure-difference"
            >
              {money(difference)}
            </p>
          </div>
        </div>
        <p className="pt-3 text-xs text-[var(--vinea-ink-subtle)]">
          {isOpen ? t("figuresLive") : t("figuresStored")}
        </p>

        {isOpen && canLock ? (
          <div className="mt-3 flex flex-wrap items-center justify-end gap-3 border-t border-[var(--vinea-border)] pt-3">
            {lockReasons.length > 0 ? (
              <ul className="mr-auto space-y-0.5 text-xs text-[var(--vinea-danger)]" data-testid="lock-blocked">
                {lockReasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            ) : (
              <p className="mr-auto text-xs text-[var(--vinea-success)]" data-testid="lock-ready">
                {t("lockReady")}
              </p>
            )}
            <Button
              variant="primary"
              disabled={lockReasons.length > 0 || lock.isPending}
              onClick={handleLock}
              className="gap-1.5"
            >
              <Lock className="size-3.5" /> {t("lock")}
            </Button>
          </div>
        ) : null}
        {!isOpen && canLock ? (
          <div className="mt-3 flex flex-wrap items-center justify-end gap-3 border-t border-[var(--vinea-border)] pt-3">
            {reopenBlocked ? (
              <p className="mr-auto text-xs text-[var(--vinea-ink-muted)]" data-testid="reopen-blocked">
                {reopenBlocked}
              </p>
            ) : null}
            <Button
              variant="secondary"
              disabled={reopenBlocked !== null}
              onClick={() => {
                setReopenError(null);
                setReopenReason("");
                setReopenOpen(true);
              }}
              className="gap-1.5"
            >
              <Unlock className="size-3.5" /> {t("reopen")}
            </Button>
          </div>
        ) : null}
        {lockError ? (
          <p
            data-testid="lock-error"
            className="mt-3 rounded-[var(--radius-control)] bg-[var(--vinea-danger-soft)] px-3 py-2 text-sm text-[var(--vinea-danger)]"
          >
            {lockError}
          </p>
        ) : null}
      </ReportPanel>

      {!isOpen ? (
        <ReportPanel>
          <p className="text-xs text-[var(--vinea-ink-muted)]" data-testid="locked-note">
            {t("lockedNote", { number: rec.number })}
          </p>
        </ReportPanel>
      ) : null}

      {showMatchBar ? (
        <ReportPanel>
          <div className="flex flex-wrap items-center gap-4 text-xs" data-testid="match-bar">
            <span>
              {t("selectedStatement", {
                count: formatQuantity(selectedStatement.size, 0),
                total: money(selectedStatementTotal),
              })}
            </span>
            <span>
              {t("selectedLedger", {
                count: formatQuantity(selectedLedger.size, 0),
                total: money(selectedLedgerTotal),
              })}
            </span>
            <span data-testid="selection-balance" className="font-semibold">
              {selectionBalance === 0
                ? t("selectionBalances")
                : t("selectionOutBy", { difference: money(selectionBalance) })}
            </span>
            <div className="ml-auto flex gap-2">
              <Button variant="secondary" onClick={handleAutoMatch} disabled={autoMatch.isPending} className="gap-1.5">
                <Sparkles className="size-3.5" /> {t("autoMatch")}
              </Button>
              <Button
                variant="primary"
                disabled={selectedStatement.size === 0 || selectedLedger.size === 0 || createMatch.isPending}
                onClick={handleMatch}
              >
                {t("match")}
              </Button>
            </div>
          </div>
          {autoResult ? (
            <p className="pt-2 text-xs text-[var(--vinea-ink)]" data-testid="auto-match-result">
              {t("autoMatchResult", {
                matched: formatQuantity(autoResult.matched.length, 0),
                ambiguous: formatQuantity(ambiguousCount, 0),
              })}
            </p>
          ) : null}
          {matchError ? (
            <p
              data-testid="match-error"
              className="mt-2 rounded-[var(--radius-control)] bg-[var(--vinea-danger-soft)] px-3 py-2 text-sm text-[var(--vinea-danger)]"
            >
              {matchError}
            </p>
          ) : null}
        </ReportPanel>
      ) : null}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <ReportPanel>
          <h2 className="pb-2 text-xs font-semibold text-[var(--vinea-ink-muted)]">
            {t("statementPane", { count: formatQuantity(statements.length, 0) })}
          </h2>
          {statements.length === 0 ? (
            <QueryState query={statementLines} isEmpty empty={t("noStatementLines")} testId="statement-pane" />
          ) : (
            <Table>
              <THead>
                <TR>
                  {showMatchBar ? <TH className="w-8" /> : null}
                  <TH className="w-24">{t("date")}</TH>
                  <TH>{t("description")}</TH>
                  <TH className="w-32 text-right">{t("amount")}</TH>
                  <TH className="w-48">{t("state")}</TH>
                </TR>
              </THead>
              <TBody>
                {statements.map((line) => {
                  const matched = line.state.match_id !== null;
                  const blocked = unmatchBlockedReason(line.state.reconciliation_number);
                  return (
                    <TR key={line.id} data-statement-line-id={line.id} data-matched={matched ? "yes" : "no"}>
                      {showMatchBar ? (
                        <TD>
                          {!matched ? (
                            <input
                              type="checkbox"
                              aria-label={t("selectStatementLine", { text: line.description })}
                              checked={selectedStatement.has(line.id)}
                              onChange={() => setSelectedStatement((set) => toggle(set, line.id))}
                              className="size-3.5"
                            />
                          ) : null}
                        </TD>
                      ) : null}
                      <TD className="text-xs">{formatDate(line.value_date)}</TD>
                      <TD className="text-xs">
                        {line.description}
                        {line.reference ? (
                          <span className="ml-1 font-mono text-[11px] text-[var(--vinea-ink-subtle)]">
                            {line.reference}
                          </span>
                        ) : null}
                        {matched ? (
                          <p className="text-[11px] text-[var(--vinea-ink-subtle)]">
                            {t("matchedTo", {
                              entries: (ledgerByMatch.get(line.state.match_id!) ?? []).join(", ") || tc("emptyValue"),
                            })}
                          </p>
                        ) : null}
                      </TD>
                      <TD className="text-right font-mono text-xs tabular-nums">{money(line.amount)}</TD>
                      <TD className="text-xs">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <MatchStateChip
                            rule={line.state.match_rule}
                            reconciliationNumber={line.state.reconciliation_number}
                            ledgerLineCount={line.state.journal_line_count}
                          />
                          {canReconcile && matched ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={blocked !== null || unmatch.isPending}
                              title={blocked ?? undefined}
                              onClick={() => handleUnmatch(line.state.match_id!)}
                            >
                              {t("unmatch")}
                            </Button>
                          ) : null}
                          {showMatchBar && !matched ? <PostFromLineButton line={line} onPress={setPostingLine} /> : null}
                        </div>
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          )}
        </ReportPanel>

        <ReportPanel>
          <h2 className="pb-2 text-xs font-semibold text-[var(--vinea-ink-muted)]">
            {t("ledgerPane", { count: formatQuantity(ledger.length, 0) })}
          </h2>
          {ledger.length === 0 ? (
            <QueryState query={ledgerLines} isEmpty empty={t("noLedgerLines")} testId="ledger-pane" />
          ) : (
            <Table>
              <THead>
                <TR>
                  {showMatchBar ? <TH className="w-8" /> : null}
                  <TH className="w-24">{t("date")}</TH>
                  <TH className="w-28">{t("entry")}</TH>
                  <TH>{t("description")}</TH>
                  <TH className="w-32 text-right">{t("amount")}</TH>
                  <TH className="w-48">{t("state")}</TH>
                </TR>
              </THead>
              <TBody>
                {ledger.map((line) => {
                  const matched = line.match_id !== null;
                  const blocked = unmatchBlockedReason(line.reconciliation_number);
                  return (
                    <TR
                      key={line.journal_line_id}
                      data-ledger-line-id={line.journal_line_id}
                      data-entry={line.entry_number}
                      data-matched={matched ? "yes" : "no"}
                    >
                      {showMatchBar ? (
                        <TD>
                          {!matched ? (
                            <input
                              type="checkbox"
                              aria-label={t("selectLedgerLine", { entry: line.entry_number })}
                              checked={selectedLedger.has(line.journal_line_id)}
                              onChange={() => setSelectedLedger((set) => toggle(set, line.journal_line_id))}
                              className="size-3.5"
                            />
                          ) : null}
                        </TD>
                      ) : null}
                      <TD className="text-xs">{formatDate(line.entry_date)}</TD>
                      <TD className="font-mono text-xs">
                        <Link href={`/gl/entries/${line.entry_id}`} className="text-[var(--vinea-brand)] underline">
                          {line.entry_number}
                        </Link>
                      </TD>
                      <TD className="text-xs">
                        {line.description ?? tc("emptyValue")}
                        {line.dated_inside ? (
                          <StatusChip tone="warning" className="ml-2">
                            {t("datedInside", { number: line.dated_inside })}
                          </StatusChip>
                        ) : null}
                        {matched && statementByMatch.has(line.match_id!) ? (
                          <p className="text-[11px] text-[var(--vinea-ink-subtle)]">
                            {t("matchedTo", { entries: statementByMatch.get(line.match_id!)!.join(", ") })}
                          </p>
                        ) : null}
                      </TD>
                      <TD className="text-right font-mono text-xs tabular-nums">{money(line.amount)}</TD>
                      <TD className="text-xs">
                        <div className="flex flex-wrap items-center gap-1.5">
                          {matched ? (
                            <MatchStateChip
                              rule={line.match_rule}
                              reconciliationNumber={line.reconciliation_number}
                            />
                          ) : (
                            <StatusChip tone="warning">{t("outstandingChip")}</StatusChip>
                          )}
                          {canReconcile && matched ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={blocked !== null || unmatch.isPending}
                              title={blocked ?? undefined}
                              onClick={() => handleUnmatch(line.match_id!)}
                            >
                              {t("unmatch")}
                            </Button>
                          ) : null}
                          {showMatchBar && !matched ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={tick.isPending}
                              onClick={() => handleTick(line.journal_line_id)}
                            >
                              {t("tick")}
                            </Button>
                          ) : null}
                        </div>
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          )}
        </ReportPanel>
      </div>

      {postingLine && account ? (
        <PostFromLineDrawer line={postingLine} account={account} onClose={() => setPostingLine(null)} />
      ) : null}

      <Dialog open={reopenOpen} onOpenChange={setReopenOpen}>
        <DialogContent title={t("reopenTitle", { number: rec.number })} description={t("reopenDescription")}>
          <div className="space-y-3">
            <Field label={t("reopenReason")}>
              <Input value={reopenReason} onChange={(e) => setReopenReason(e.target.value)} />
            </Field>
            {reopenError ? (
              <p
                data-testid="reopen-error"
                className="rounded-[var(--radius-control)] bg-[var(--vinea-danger-soft)] px-3 py-2 text-sm text-[var(--vinea-danger)]"
              >
                {reopenError}
              </p>
            ) : null}
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setReopenOpen(false)}>
              {tc("cancel")}
            </Button>
            <Button
              variant="primary"
              disabled={!reopenReason.trim() || reopen.isPending}
              onClick={handleReopen}
            >
              {t("confirmReopen")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </ReportPage>
  );
}

/** *Post from line* on an unmatched statement line — drawn only when this user may post at least
 * one of the two things the drawer posts for a line of this sign. */
function PostFromLineButton({ line, onPress }: { line: StatementLine; onPress: (line: StatementLine) => void }) {
  const t = useTranslations("banking.workspace");
  const permitted = usePostFromLinePermissions(line);
  if (!permitted.any) return null;
  return (
    <Button variant="ghost" size="sm" onClick={() => onPress(line)}>
      {t("postFromLine")}
    </Button>
  );
}
