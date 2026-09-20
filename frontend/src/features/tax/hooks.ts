"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { VatReturn, VatReturnDetail, VatReturnPreview } from "./types";

/** One root key for the module, so filing a return invalidates the listing and the preview
 * behind it — the preview *changes* when a return is filed, because the next one's late
 * entries are computed from the high-water mark this one just set. */
const ROOT = "tax";

/**
 * The return over a range as it stands right now — nothing stored.
 *
 * `enabled` on both dates, so the screen does not ask for a return of nothing before anybody
 * has chosen a range.
 */
export function useVatReturnPreview(periodFrom: string, periodTo: string) {
  return useQuery({
    queryKey: [ROOT, "vat-return-preview", periodFrom, periodTo],
    queryFn: () =>
      api.get<VatReturnPreview>(
        `/tax/vat-returns/preview?period_from=${periodFrom}&period_to=${periodTo}`,
      ),
    enabled: Boolean(periodFrom && periodTo),
  });
}

export function useVatReturns() {
  return useQuery({
    queryKey: [ROOT, "vat-returns"],
    queryFn: () => api.get<VatReturn[]>("/tax/vat-returns"),
    staleTime: 30_000,
  });
}

export function useVatReturn(returnId: number | null) {
  return useQuery({
    queryKey: [ROOT, "vat-return", returnId],
    queryFn: () => api.get<VatReturnDetail>(`/tax/vat-returns/${returnId}`),
    enabled: returnId !== null,
  });
}

/**
 * File the return: freeze the figures, record the high-water mark, post the settlement entry.
 *
 * `Idempotency-Key` because filing claims a `VATR-` number and posts through the kernel; a
 * double-click while the ledger is busy would otherwise file the same range twice, and the
 * second would be refused `vat_period_filed` only after spending a number.
 */
export function useFileVatReturn() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      periodFrom,
      periodTo,
      idempotencyKey,
    }: {
      periodFrom: string;
      periodTo: string;
      idempotencyKey: string;
    }) =>
      api.post<VatReturn>(
        "/tax/vat-returns",
        { period_from: periodFrom, period_to: periodTo },
        { "Idempotency-Key": idempotencyKey },
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/** Reverse a filed return, which reopens its range so it can be filed again. The settlement
 * entry reverses **only** this way (`module_reversal("tax")`, the P5 kernel rule) — the general
 * ledger's own Reverse is refused for a `VAT` entry. */
export function useReverseVatReturn() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ returnId, reason }: { returnId: number; reason: string }) =>
      api.post<VatReturn>(`/tax/vat-returns/${returnId}/reverse`, { reason }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}
