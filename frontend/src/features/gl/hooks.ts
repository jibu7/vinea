"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type {
  Branch,
  CashbookEntryCreatePayload,
  Currency,
  ExchangeRate,
  GLAccount,
  JournalEntry,
  JournalEntryCreatePayload,
  Project,
  ReversalPayload,
  TaxCode,
  TransactionType,
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
