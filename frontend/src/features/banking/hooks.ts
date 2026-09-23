"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type {
  AutoMatchResult,
  BankAccount,
  BankAccountRegisterPayload,
  BankAccountUpdatePayload,
  BankRule,
  BankRulePayload,
  LedgerLine,
  ManualStatementPayload,
  Match,
  PostCashbookFromLinePayload,
  PostedFromStatement,
  PostSettlementFromLinePayload,
  Prefill,
  Reconciliation,
  ReconciliationDetail,
  Statement,
  StatementDetail,
  StatementFormat,
  StatementImportResult,
  StatementLineDetail,
  StatementPreview,
  UnregisteredAccount,
} from "./types";

/** One root key for the module, so a write invalidates every list derived from it — the same
 * shape `gl`, `fiscal` and `order-entry` use. */
const ROOT = "banking";

export function useBankAccounts(options: { includeInactive?: boolean; enabled?: boolean } = {}) {
  const includeInactive = options.includeInactive ?? false;
  return useQuery({
    queryKey: [ROOT, "accounts", { includeInactive }],
    queryFn: () =>
      api.get<BankAccount[]>(`/banking/accounts${includeInactive ? "?include_inactive=true" : ""}`),
    enabled: options.enabled ?? true,
  });
}

/** Bank/cash control accounts with no master row — empty on every healthy tenant, and the
 * screen's *Register* list when it is not. Gated on `bank:setup_manage`, so a reader who
 * lacks it simply is not asked. */
export function useUnregisteredBankAccounts(enabled: boolean) {
  return useQuery({
    queryKey: [ROOT, "accounts", "unregistered"],
    queryFn: () => api.get<UnregisteredAccount[]>("/banking/accounts/unregistered"),
    enabled,
  });
}

export function useRegisterBankAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: BankAccountRegisterPayload) =>
      api.post<BankAccount>("/banking/accounts", payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [ROOT] });
      // *New* makes a chart-of-accounts row as well as the master.
      queryClient.invalidateQueries({ queryKey: ["gl", "accounts"] });
    },
  });
}

export function useUpdateBankAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: BankAccountUpdatePayload }) =>
      api.patch<BankAccount>(`/banking/accounts/${id}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/**
 * *Test with a file* — the preview under the mapping on the screen, not the one stored.
 *
 * A mutation because the endpoint is a POST (it takes a file), but it writes nothing: no
 * statement, no line, not even the mapping it was asked to try. So there is nothing to
 * invalidate.
 */
export function usePreviewStatement() {
  return useMutation({
    mutationFn: ({
      bankAccountId,
      file,
      format,
    }: {
      bankAccountId: number;
      file: File;
      format?: StatementFormat;
    }) => {
      const form = new FormData();
      form.append("bank_account_id", String(bankAccountId));
      form.append("file", file);
      if (format) form.append("statement_format", JSON.stringify(format));
      return api.post<StatementPreview>("/banking/statements/preview", form);
    },
  });
}

export function useBankRules(bankAccountId: number | null) {
  return useQuery({
    queryKey: [ROOT, "rules", bankAccountId],
    queryFn: () => api.get<BankRule[]>(`/banking/accounts/${bankAccountId}/rules`),
    enabled: bankAccountId !== null,
  });
}

export function useCreateBankRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ bankAccountId, payload }: { bankAccountId: number; payload: BankRulePayload }) =>
      api.post<BankRule>(`/banking/accounts/${bankAccountId}/rules`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT, "rules"] }),
  });
}

export function useUpdateBankRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ ruleId, payload }: { ruleId: number; payload: BankRulePayload }) =>
      api.patch<BankRule>(`/banking/rules/${ruleId}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT, "rules"] }),
  });
}

// --- Statements (P8 step 7) ------------------------------------------------------------------

export function useStatements(bankAccountId: number | null) {
  return useQuery({
    queryKey: [ROOT, "statements", bankAccountId],
    queryFn: () => api.get<Statement[]>(`/banking/statements?bank_account_id=${bankAccountId}&limit=200`),
    enabled: bankAccountId !== null,
  });
}

export function useStatement(statementId: number) {
  return useQuery({
    queryKey: [ROOT, "statement", statementId],
    queryFn: () => api.get<StatementDetail>(`/banking/statements/${statementId}`),
  });
}

/**
 * **Import**'s confirm half. The file goes again as it went to the preview — the same bytes,
 * so `file_sha256` is over what the bank produced — with the two balances only where the
 * person keyed them (an empty field means "read them off the balance column").
 *
 * `Idempotency-Key` is the draft UUID the import dialog opened with, so a retried confirm
 * replays the statement it made rather than being refused as the same file twice.
 */
export function useImportStatement() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      bankAccountId,
      file,
      openingBalance,
      closingBalance,
      idempotencyKey,
    }: {
      bankAccountId: number;
      file: File;
      openingBalance: string;
      closingBalance: string;
      idempotencyKey: string;
    }) => {
      const form = new FormData();
      form.append("bank_account_id", String(bankAccountId));
      form.append("file", file);
      if (openingBalance.trim()) form.append("opening_balance", openingBalance.trim());
      if (closingBalance.trim()) form.append("closing_balance", closingBalance.trim());
      return api.post<StatementImportResult>("/banking/statements", form, {
        "Idempotency-Key": idempotencyKey,
      });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/** A paper statement, keyed line by line — the same table and the same path as an import. */
export function useKeyManualStatement() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ payload, idempotencyKey }: { payload: ManualStatementPayload; idempotencyKey: string }) =>
      api.post<StatementImportResult>("/banking/statements/manual", payload, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useVoidStatement() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ statementId, reason }: { statementId: number; reason: string }) =>
      api.post<Statement>(`/banking/statements/${statementId}/void`, { reason: reason || null }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

// --- The workspace (P8 step 7) ---------------------------------------------------------------

export function useReconciliations(bankAccountId: number | null) {
  return useQuery({
    queryKey: [ROOT, "reconciliations", bankAccountId],
    queryFn: () =>
      api.get<Reconciliation[]>(`/banking/reconciliations?bank_account_id=${bankAccountId}&limit=200`),
    enabled: bankAccountId !== null,
  });
}

export function useReconciliation(reconciliationId: number) {
  return useQuery({
    queryKey: [ROOT, "reconciliation", reconciliationId],
    queryFn: () => api.get<ReconciliationDetail>(`/banking/reconciliations/${reconciliationId}`),
  });
}

/** What *New reconciliation* fills the statement balance with — the server's own fallback. */
export function useDefaultStatementBalance(bankAccountId: number | null, on: string) {
  return useQuery({
    queryKey: [ROOT, "default-statement-balance", bankAccountId, on],
    queryFn: () =>
      api.get<{ statement_balance: string | null }>(
        `/banking/accounts/${bankAccountId}/default-statement-balance?on=${on}`,
      ),
    enabled: bankAccountId !== null && on !== "",
  });
}

/** The left pane: statement lines on or before the date, unmatched first, with their match. */
export function useAccountStatementLines(bankAccountId: number | null, onOrBefore: string | null) {
  return useQuery({
    queryKey: [ROOT, "statement-lines", bankAccountId, onOrBefore],
    queryFn: () =>
      api.get<StatementLineDetail[]>(
        `/banking/accounts/${bankAccountId}/statement-lines${onOrBefore ? `?on_or_before=${onOrBefore}` : ""}`,
      ),
    enabled: bankAccountId !== null,
  });
}

/** The right pane: ledger lines on or before the date, outstanding first. */
export function useLedgerLines(bankAccountId: number | null, asOf: string | null) {
  return useQuery({
    queryKey: [ROOT, "ledger-lines", bankAccountId, asOf],
    queryFn: () =>
      api.get<LedgerLine[]>(
        `/banking/accounts/${bankAccountId}/ledger-lines${asOf ? `?as_of=${asOf}` : ""}`,
      ),
    enabled: bankAccountId !== null,
  });
}

export function useOpenReconciliation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      payload,
      idempotencyKey,
    }: {
      payload: { bank_account_id: number; reconciliation_date: string; statement_balance: string | null };
      idempotencyKey: string;
    }) =>
      api.post<ReconciliationDetail>("/banking/reconciliations", payload, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useLockReconciliation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      reconciliationId,
      statementBalance,
      idempotencyKey,
    }: {
      reconciliationId: number;
      statementBalance: string | null;
      idempotencyKey: string;
    }) =>
      api.post<ReconciliationDetail>(
        `/banking/reconciliations/${reconciliationId}/lock`,
        { statement_balance: statementBalance },
        { "Idempotency-Key": idempotencyKey },
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useReopenReconciliation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      reconciliationId,
      reason,
      idempotencyKey,
    }: {
      reconciliationId: number;
      reason: string;
      idempotencyKey: string;
    }) =>
      api.post<ReconciliationDetail>(
        `/banking/reconciliations/${reconciliationId}/reopen`,
        { reason },
        { "Idempotency-Key": idempotencyKey },
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useAutoMatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ bankAccountId }: { bankAccountId: number }) =>
      api.post<AutoMatchResult>(`/banking/accounts/${bankAccountId}/auto-match`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useCreateMatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      bank_account_id: number;
      statement_line_ids: number[];
      journal_line_ids: number[];
    }) => api.post<Match>("/banking/matches", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useTick() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { bank_account_id: number; journal_line_ids: number[] }) =>
      api.post<Match>("/banking/matches/tick", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useUnmatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ matchId }: { matchId: number }) => api.delete<void>(`/banking/matches/${matchId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function usePrefill(statementLineId: number | null) {
  return useQuery({
    queryKey: [ROOT, "prefill", statementLineId],
    queryFn: () => api.get<Prefill>(`/banking/statement-lines/${statementLineId}/prefill`),
    enabled: statementLineId !== null,
  });
}

/** Both drawers post through the kernel and write the match in the same transaction, so they
 * invalidate the ledger and the subledger as well as the module. */
function useInvalidatePosting() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: [ROOT] });
    queryClient.invalidateQueries({ queryKey: ["gl"] });
    queryClient.invalidateQueries({ queryKey: ["subledger"] });
  };
}

export function usePostCashbookFromLine() {
  const invalidate = useInvalidatePosting();
  return useMutation({
    mutationFn: ({
      statementLineId,
      payload,
      idempotencyKey,
    }: {
      statementLineId: number;
      payload: PostCashbookFromLinePayload;
      idempotencyKey: string;
    }) =>
      api.post<PostedFromStatement>(
        `/banking/statement-lines/${statementLineId}/post-cashbook`,
        payload,
        { "Idempotency-Key": idempotencyKey },
      ),
    onSuccess: invalidate,
  });
}

export function usePostSettlementFromLine() {
  const invalidate = useInvalidatePosting();
  return useMutation({
    mutationFn: ({
      statementLineId,
      payload,
      idempotencyKey,
    }: {
      statementLineId: number;
      payload: PostSettlementFromLinePayload;
      idempotencyKey: string;
    }) =>
      api.post<PostedFromStatement>(
        `/banking/statement-lines/${statementLineId}/post-settlement`,
        payload,
        { "Idempotency-Key": idempotencyKey },
      ),
    onSuccess: invalidate,
  });
}
