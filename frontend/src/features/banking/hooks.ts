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
  BankAccountEnquiry,
  CashbookDetail,
  CashbookSummaryRow,
  EntryBankLine,
  LedgerLine,
  ManualStatementPayload,
  Match,
  PaymentRun,
  PaymentRunDetail,
  PaymentRunPayload,
  PaymentRunPreview,
  PostCashbookFromLinePayload,
  PostedFromStatement,
  PostSettlementFromLinePayload,
  Prefill,
  Reconciliation,
  ReconciliationDetail,
  ReconciliationReport,
  RemittanceJob,
  SelectableDocument,
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

/** A voided statement leaves every listing (decision 3) — so it is asked for by name. */
export function useStatements(bankAccountId: number | null, includeVoid = false) {
  return useQuery({
    queryKey: [ROOT, "statements", bankAccountId, { includeVoid }],
    queryFn: () =>
      api.get<Statement[]>(
        `/banking/statements?bank_account_id=${bankAccountId}&limit=200${includeVoid ? "&include_void=true" : ""}`,
      ),
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

// --- Payment runs (P8 decision 7, step 7b) -----------------------------------------------------

export function usePaymentRuns(bankAccountId: number | null) {
  return useQuery({
    queryKey: [ROOT, "payment-runs", bankAccountId],
    queryFn: () =>
      api.get<PaymentRun[]>(`/banking/payment-runs?bank_account_id=${bankAccountId}&limit=200`),
    enabled: bankAccountId !== null,
  });
}

export function usePaymentRun(runId: number) {
  return useQuery({
    queryKey: [ROOT, "payment-run", runId],
    queryFn: () => api.get<PaymentRunDetail>(`/banking/payment-runs/${runId}`),
  });
}

/** The selection grid's rows. `on` is the payment date, because `discount_available` is P4's
 * figure *at* that date — the same invoice offers a discount on the 10th and none on the 20th. */
export function useSelectableDocuments(
  bankAccountId: number | null,
  { dueBy, on }: { dueBy: string; on: string },
) {
  return useQuery({
    queryKey: [ROOT, "payment-runs", "selectable", bankAccountId, dueBy, on],
    queryFn: () => {
      const params = new URLSearchParams({ bank_account_id: String(bankAccountId), on });
      if (dueBy) params.set("due_by", dueBy);
      return api.get<SelectableDocument[]>(`/banking/payment-runs/selectable?${params}`);
    },
    enabled: bankAccountId !== null && on !== "",
  });
}

/** **Preview**: every refusal the post would raise, and nothing written. A mutation because the
 * endpoint is a POST (it takes the selection), not because it changes anything. */
export function usePreviewPaymentRun() {
  return useMutation({
    mutationFn: (payload: PaymentRunPayload) =>
      api.post<PaymentRunPreview>("/banking/payment-runs/preview", payload),
  });
}

/** **Post**: one settlement and one allocation per supplier, in one transaction, under the
 * draft's `Idempotency-Key` — a retried press replays the run it made. */
export function usePostPaymentRun() {
  const invalidate = useInvalidatePosting();
  return useMutation({
    mutationFn: ({ payload, idempotencyKey }: { payload: PaymentRunPayload; idempotencyKey: string }) =>
      api.post<PaymentRunDetail>("/banking/payment-runs", payload, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: invalidate,
  });
}

/** **Reverse**: unallocate everything, then reverse everything, and release the bank line's
 * match. No `Idempotency-Key`: a replayed reversal is refused `payment_run_already_reversed`,
 * which is the answer a retry wants. */
export function useReversePaymentRun() {
  const invalidate = useInvalidatePosting();
  return useMutation({
    mutationFn: ({ runId, reason, onDate }: { runId: number; reason: string; onDate: string | null }) =>
      api.post<PaymentRunDetail>(`/banking/payment-runs/${runId}/reverse`, {
        reason,
        on_date: onDate,
      }),
    onSuccess: invalidate,
  });
}

/** The run's advices. The jobs run after the post's response, so the list is polled while any
 * of them is still queued or running and left alone once all have an answer. */
export function useRemittances(runId: number, enabled: boolean) {
  return useQuery({
    queryKey: [ROOT, "payment-run", runId, "remittances"],
    queryFn: () => api.get<RemittanceJob[]>(`/banking/payment-runs/${runId}/remittances`),
    enabled,
    refetchInterval: (query) =>
      (query.state.data ?? []).some((job) => job.status === "queued" || job.status === "running")
        ? 1500
        : false,
  });
}

// --- Enquiries and reports (P8 step 8) ---------------------------------------------------------

/** Decision 10, per account and date. */
export function useBankAccountEnquiry(bankAccountId: number | null, asOf: string) {
  return useQuery({
    queryKey: [ROOT, "enquiry", bankAccountId, asOf],
    queryFn: () =>
      api.get<BankAccountEnquiry>(`/banking/enquiries/bank-account/${bankAccountId}?as_of=${asOf}`),
    enabled: bankAccountId !== null && asOf !== "",
  });
}

export function useCashbook(bankAccountId: number | null, dateFrom: string, dateTo: string) {
  return useQuery({
    queryKey: [ROOT, "cashbook", bankAccountId, dateFrom, dateTo],
    queryFn: () => {
      const params = new URLSearchParams({
        bank_account_id: String(bankAccountId),
        date_from: dateFrom,
        date_to: dateTo,
      });
      return api.get<CashbookDetail>(`/banking/reports/cashbook?${params}`);
    },
    enabled: bankAccountId !== null && dateFrom !== "" && dateTo !== "",
  });
}

export function useCashbookSummary(dateFrom: string, dateTo: string, enabled = true) {
  return useQuery({
    queryKey: [ROOT, "cashbook-summary", dateFrom, dateTo],
    queryFn: () =>
      api.get<CashbookSummaryRow[]>(
        `/banking/reports/cashbook-summary?date_from=${dateFrom}&date_to=${dateTo}`,
      ),
    enabled: enabled && dateFrom !== "" && dateTo !== "",
  });
}

export function useReconciliationReport(reconciliationId: number | null) {
  return useQuery({
    queryKey: [ROOT, "reconciliation-report", reconciliationId],
    queryFn: () =>
      api.get<ReconciliationReport>(`/banking/reports/reconciliation/${reconciliationId}`),
    enabled: reconciliationId !== null,
  });
}

/** The GL entry page's bank lines. Off without `bank:reports_view`: the page still renders, and
 * the column is simply not drawn. */
export function useEntryBankLines(entryId: number, enabled: boolean) {
  return useQuery({
    queryKey: [ROOT, "entry-bank-lines", entryId],
    queryFn: () => api.get<EntryBankLine[]>(`/banking/journal-entries/${entryId}/bank-lines`),
    enabled,
  });
}
