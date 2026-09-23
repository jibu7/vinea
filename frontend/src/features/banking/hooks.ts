"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type {
  BankAccount,
  BankAccountRegisterPayload,
  BankAccountUpdatePayload,
  BankRule,
  BankRulePayload,
  StatementFormat,
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
