"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type {
  AccountAuditRecord,
  AccountingPeriod,
  AccountTransactionsResponse,
  Branch,
  CashbookEntryCreatePayload,
  CompanyDetails,
  CompanyMember,
  Currency,
  ExchangeRate,
  FiscalYear,
  GLAccount,
  GLSettings,
  JournalEntry,
  JournalEntryCreatePayload,
  Project,
  ReversalPayload,
  Role,
  TaxCode,
  TransactionType,
  TrialBalanceResponse,
} from "./types";

export function useAccounts() {
  return useQuery({
    queryKey: ["gl", "accounts"],
    queryFn: () => api.get<GLAccount[]>("/gl/accounts"),
    staleTime: 60_000,
  });
}

export function useProjects() {
  return useQuery({
    queryKey: ["gl", "projects"],
    queryFn: () => api.get<Project[]>("/gl/projects"),
    staleTime: 60_000,
  });
}

export function useTransactionTypes(module = "gl") {
  return useQuery({
    queryKey: ["gl", "transaction-types", module],
    queryFn: () => api.get<TransactionType[]>(`/gl/transaction-types?module=${module}`),
    staleTime: 60_000,
  });
}

export function useBranches() {
  return useQuery({
    queryKey: ["gl", "branches"],
    queryFn: () => api.get<Branch[]>("/gl/branches"),
    staleTime: 60_000,
  });
}

export function useTaxCodes() {
  return useQuery({
    queryKey: ["gl", "tax-codes"],
    queryFn: () => api.get<TaxCode[]>("/gl/tax-codes"),
    staleTime: 60_000,
  });
}

export function useCurrencies() {
  return useQuery({
    queryKey: ["gl", "currencies"],
    queryFn: () => api.get<Currency[]>("/gl/currencies"),
    staleTime: 60_000,
  });
}

export function useExchangeRates(currencyId?: number) {
  return useQuery({
    queryKey: ["gl", "exchange-rates", currencyId],
    queryFn: () =>
      api.get<ExchangeRate[]>(`/gl/exchange-rates${currencyId ? `?currency_id=${currencyId}` : ""}`),
    staleTime: 60_000,
  });
}

export function useJournalEntry(id: number | null) {
  return useQuery({
    queryKey: ["gl", "entry", id],
    queryFn: () => api.get<JournalEntry>(`/gl/journal-entries/${id}`),
    enabled: id !== null,
  });
}

/** Idempotency-Key header makes a retried post replay the original entry, never duplicate it. */
export function useCreateJournalEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ payload, idempotencyKey }: { payload: JournalEntryCreatePayload; idempotencyKey: string }) =>
      api.post<JournalEntry>("/gl/journal-entries", payload, { "Idempotency-Key": idempotencyKey }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl"] }),
  });
}

export function useCreateCashbookEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ payload, idempotencyKey }: { payload: CashbookEntryCreatePayload; idempotencyKey: string }) =>
      api.post<JournalEntry>("/gl/cashbook-entries", payload, { "Idempotency-Key": idempotencyKey }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl"] }),
  });
}

export function useReverseEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      entryId,
      payload,
      idempotencyKey,
    }: {
      entryId: number;
      payload: ReversalPayload;
      idempotencyKey: string;
    }) =>
      api.post<JournalEntry>(`/gl/journal-entries/${entryId}/reverse`, payload, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl"] }),
  });
}

export function useTrialBalance(
  params: { as_of: string; branch_id?: number | null; project_id?: number | null },
  enabled = true,
) {
  const search = new URLSearchParams({ as_of: params.as_of });
  if (params.branch_id) search.set("branch_id", String(params.branch_id));
  if (params.project_id) search.set("project_id", String(params.project_id));

  return useQuery({
    queryKey: ["gl", "trial-balance", params],
    queryFn: () => api.get<TrialBalanceResponse>(`/gl/trial-balance?${search.toString()}`),
    enabled: enabled && !!params.as_of,
  });
}

export function useAccountTransactions(
  params: {
    account_id: number;
    date_from: string;
    date_to: string;
    branch_id?: number | null;
    project_id?: number | null;
    cursor?: number | null;
    limit?: number;
  },
  enabled = true,
) {
  const search = new URLSearchParams({
    date_from: params.date_from,
    date_to: params.date_to,
  });
  if (params.branch_id) search.set("branch_id", String(params.branch_id));
  if (params.project_id) search.set("project_id", String(params.project_id));
  if (params.cursor) search.set("cursor", String(params.cursor));
  if (params.limit) search.set("limit", String(params.limit));

  return useQuery({
    queryKey: ["gl", "account-transactions", params],
    queryFn: () =>
      api.get<AccountTransactionsResponse>(
        `/gl/accounts/${params.account_id}/transactions?${search.toString()}`,
      ),
    enabled: enabled && !!params.account_id && !!params.date_from && !!params.date_to,
  });
}

// --- Maintenance Hooks -------------------------------------------------------------------

export function useCompanyDetails() {
  return useQuery({
    queryKey: ["company"],
    queryFn: () => api.get<CompanyDetails>("/company"),
  });
}

export function useUpdateCompany() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: Partial<CompanyDetails>) =>
      api.patch<CompanyDetails>("/company", payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["company"] });
      queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
    },
  });
}

export function useGLSettings() {
  return useQuery({
    queryKey: ["gl", "settings"],
    queryFn: () => api.get<GLSettings>("/gl/settings"),
  });
}

export function useUpdateGLSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: GLSettings) => api.put<GLSettings>("/gl/settings", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl", "settings"] }),
  });
}

export function useFiscalYears() {
  return useQuery({
    queryKey: ["gl", "fiscal-years"],
    queryFn: () => api.get<FiscalYear[]>("/gl/fiscal-years"),
  });
}

export function useCreateFiscalYear() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { code: string; start_date: string; end_date: string; period_count?: number }) =>
      api.post<FiscalYear>("/gl/fiscal-years", payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["gl", "fiscal-years"] });
      queryClient.invalidateQueries({ queryKey: ["gl", "periods"] });
    },
  });
}

export function useCloseFiscalYear() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (yearId: number) => api.post<FiscalYear>(`/gl/fiscal-years/${yearId}/close`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["gl", "fiscal-years"] });
      queryClient.invalidateQueries({ queryKey: ["gl", "periods"] });
    },
  });
}

export function useReopenFiscalYear() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ yearId, reason }: { yearId: number; reason: string }) =>
      api.post<FiscalYear>(`/gl/fiscal-years/${yearId}/reopen`, { reason }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["gl", "fiscal-years"] });
      queryClient.invalidateQueries({ queryKey: ["gl", "periods"] });
    },
  });
}

export function usePeriods(fiscalYearId?: number) {
  return useQuery({
    queryKey: ["gl", "periods", fiscalYearId],
    queryFn: () =>
      api.get<AccountingPeriod[]>(
        `/gl/periods${fiscalYearId ? `?fiscal_year_id=${fiscalYearId}` : ""}`,
      ),
  });
}

export function useOpenPeriod() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (periodId: number) => api.post<AccountingPeriod>(`/gl/periods/${periodId}/open`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl", "periods"] }),
  });
}

export function useClosePeriod() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (periodId: number) => api.post<AccountingPeriod>(`/gl/periods/${periodId}/close`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl", "periods"] }),
  });
}

export function useLockPeriod() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (periodId: number) => api.post<AccountingPeriod>(`/gl/periods/${periodId}/lock`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl", "periods"] }),
  });
}

export function useReopenPeriod() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ periodId, reason }: { periodId: number; reason: string }) =>
      api.post<AccountingPeriod>(`/gl/periods/${periodId}/reopen`, { reason }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl", "periods"] }),
  });
}

export function useAccountHistory(accountId: number | null) {
  return useQuery({
    queryKey: ["gl", "accounts", accountId, "history"],
    queryFn: () => api.get<AccountAuditRecord[]>(`/gl/accounts/${accountId}/history`),
    enabled: accountId !== null,
  });
}

export function useCreateAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      code: string;
      name: string;
      class_: string;
      parent_id?: number | null;
      is_postable?: boolean;
      is_control?: boolean;
      control_type?: string | null;
    }) => api.post<GLAccount>("/gl/accounts", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl", "accounts"] }),
  });
}

export function useUpdateAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      accountId,
      payload,
    }: {
      accountId: number;
      payload: {
        code?: string;
        name?: string;
        parent_id?: number | null;
        clear_parent?: boolean;
        is_postable?: boolean;
        is_active?: boolean;
      };
    }) => api.patch<GLAccount>(`/gl/accounts/${accountId}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl", "accounts"] }),
  });
}

export function useCreateBranch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { code: string; name: string; is_main?: boolean }) =>
      api.post<Branch>("/gl/branches", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl", "branches"] }),
  });
}

export function useUpdateBranch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      branchId,
      payload,
    }: {
      branchId: number;
      payload: { name?: string; is_main?: boolean; is_active?: boolean };
    }) => api.patch<Branch>(`/gl/branches/${branchId}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl", "branches"] }),
  });
}

export function useCreateTaxCode() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      code: string;
      name: string;
      nature: string;
      rate_pct: string;
      gl_account_id?: number | null;
      valid_from: string;
      valid_to?: string | null;
    }) => api.post<TaxCode>("/gl/tax-codes", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl", "tax-codes"] }),
  });
}

export function useUpdateTaxCode() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      codeId,
      payload,
    }: {
      codeId: number;
      payload: {
        name?: string;
        rate_pct?: string;
        gl_account_id?: number | null;
        clear_gl_account?: boolean;
        valid_to?: string | null;
        is_active?: boolean;
      };
    }) => api.patch<TaxCode>(`/gl/tax-codes/${codeId}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl", "tax-codes"] }),
  });
}

export function useCreateCurrency() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      code: string;
      name: string;
      symbol?: string | null;
      decimal_places?: number;
    }) => api.post<Currency>("/gl/currencies", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl", "currencies"] }),
  });
}

export function useUpdateCurrency() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      currencyId,
      payload,
    }: {
      currencyId: number;
      payload: {
        name?: string;
        symbol?: string | null;
        decimal_places?: number;
        is_active?: boolean;
      };
    }) => api.patch<Currency>(`/gl/currencies/${currencyId}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl", "currencies"] }),
  });
}

export function useCreateExchangeRate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { currency_id: number; valid_from: string; rate: string }) =>
      api.post<ExchangeRate>("/gl/exchange-rates", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl", "exchange-rates"] }),
  });
}

export function useCreateProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { code: string; name: string }) =>
      api.post<Project>("/gl/projects", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl", "projects"] }),
  });
}

export function useUpdateProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      projectId,
      payload,
    }: {
      projectId: number;
      payload: { code?: string; name?: string; is_active?: boolean };
    }) => api.patch<Project>(`/gl/projects/${projectId}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl", "projects"] }),
  });
}

export function useCreateTransactionType() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      module: string;
      code: string;
      name: string;
      default_gl_account_id?: number | null;
    }) => api.post<TransactionType>("/gl/transaction-types", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl", "transaction-types"] }),
  });
}

export function useUpdateTransactionType() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      typeId,
      payload,
    }: {
      typeId: number;
      payload: { name?: string; default_gl_account_id?: number | null; is_active?: boolean };
    }) => api.patch<TransactionType>(`/gl/transaction-types/${typeId}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gl", "transaction-types"] }),
  });
}

export function useCompanyMembers() {
  return useQuery({
    queryKey: ["memberships"],
    queryFn: () => api.get<CompanyMember[]>("/memberships"),
  });
}

export function useCompanyRoles() {
  return useQuery({
    queryKey: ["memberships", "roles"],
    queryFn: () => api.get<Role[]>("/memberships/roles"),
  });
}

export function useInviteMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { email: string; role_ids: number[] }) =>
      api.post("/invitations", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["memberships"] }),
  });
}

export function useUpdateMemberRoles() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ membershipId, roleIds }: { membershipId: number; roleIds: number[] }) =>
      api.put(`/memberships/${membershipId}/roles`, { role_ids: roleIds }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["memberships"] }),
  });
}

export function useDeactivateMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (membershipId: number) =>
      api.post(`/memberships/${membershipId}/deactivate`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["memberships"] }),
  });
}

export function useActivateMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (membershipId: number) =>
      api.post(`/memberships/${membershipId}/activate`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["memberships"] }),
  });
}

