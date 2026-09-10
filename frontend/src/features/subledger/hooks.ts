"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type {
  AgeingBucketSet,
  Allocation,
  AllocationPayload,
  AllocationPreview,
  AutoAllocatePayload,
  BatchPayload,
  BatchResult,
  DocumentCreatePayload,
  PartnerDocument,
  PartnerEnquiry,
  AgeingBucketSetCreatePayload,
  AgeingBucketSetUpdatePayload,
  ArApDefaults,
  ArApDefaultsPayload,
  ContactPayload,
  Partner,
  PartnerAuditRecord,
  PartnerContact,
  PartnerCreatePayload,
  PartnerRole,
  PartnerUpdatePayload,
  PaymentTerms,
  PaymentTermsPayload,
  RoleSettings,
  RoleSettingsPayload,
  SalesRep,
} from "./types";

/** Every AR/AP query hangs off one root key so a role switch or a write invalidates cleanly. */
const ROOT = "subledger";

// --- Partners -------------------------------------------------------------------------------

export function usePartners(role: PartnerRole, opts: { includeInactive?: boolean } = {}) {
  const includeInactive = opts.includeInactive ?? false;
  return useQuery({
    queryKey: [ROOT, role, "partners", { includeInactive }],
    queryFn: () =>
      api.get<Partner[]>(
        `/subledger/${role}/partners${includeInactive ? "?include_inactive=true" : ""}`,
      ),
    staleTime: 60_000,
  });
}

export function useCreatePartner(role: PartnerRole) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: PartnerCreatePayload) =>
      api.post<Partner>(`/subledger/${role}/partners`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useUpdatePartner(role: PartnerRole) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ partnerId, payload }: { partnerId: number; payload: PartnerUpdatePayload }) =>
      api.patch<Partner>(`/subledger/${role}/partners/${partnerId}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/** `null` while no partner is selected — the settings row may not exist yet either. */
export function usePartnerSettings(role: PartnerRole, partnerId: number | null) {
  return useQuery({
    queryKey: [ROOT, role, "partners", partnerId, "settings"],
    queryFn: () =>
      api.get<RoleSettings | null>(`/subledger/${role}/partners/${partnerId}/settings`),
    enabled: partnerId !== null,
  });
}

export function useSavePartnerSettings(role: PartnerRole) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ partnerId, payload }: { partnerId: number; payload: RoleSettingsPayload }) =>
      api.put<RoleSettings>(`/subledger/${role}/partners/${partnerId}/settings`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function usePartnerContacts(role: PartnerRole, partnerId: number | null) {
  return useQuery({
    queryKey: [ROOT, role, "partners", partnerId, "contacts"],
    queryFn: () => api.get<PartnerContact[]>(`/subledger/${role}/partners/${partnerId}/contacts`),
    enabled: partnerId !== null,
  });
}

export function useCreateContact(role: PartnerRole) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ partnerId, payload }: { partnerId: number; payload: ContactPayload }) =>
      api.post<PartnerContact>(`/subledger/${role}/partners/${partnerId}/contacts`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useUpdateContact(role: PartnerRole) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ contactId, payload }: { contactId: number; payload: ContactPayload }) =>
      api.patch<PartnerContact>(`/subledger/${role}/contacts/${contactId}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/** Rename history: audit rows keyed on `partner_id`, so a code change never loses its trail. */
export function usePartnerHistory(role: PartnerRole, partnerId: number | null) {
  return useQuery({
    queryKey: [ROOT, role, "partners", partnerId, "history"],
    queryFn: () =>
      api.get<PartnerAuditRecord[]>(`/subledger/${role}/partners/${partnerId}/history`),
    enabled: partnerId !== null,
  });
}

// --- Sales reps -----------------------------------------------------------------------------

export function useSalesReps(opts: { includeInactive?: boolean } = {}) {
  const includeInactive = opts.includeInactive ?? false;
  return useQuery({
    queryKey: [ROOT, "sales-reps", { includeInactive }],
    queryFn: () =>
      api.get<SalesRep[]>(`/subledger/sales-reps${includeInactive ? "?include_inactive=true" : ""}`),
    staleTime: 60_000,
  });
}

export function useCreateSalesRep() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { code: string; name: string; email?: string | null }) =>
      api.post<SalesRep>("/subledger/sales-reps", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT, "sales-reps"] }),
  });
}

export function useUpdateSalesRep() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      repId,
      payload,
    }: {
      repId: number;
      payload: { name?: string; email?: string | null; is_active?: boolean };
    }) => api.patch<SalesRep>(`/subledger/sales-reps/${repId}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT, "sales-reps"] }),
  });
}

// --- Payment terms --------------------------------------------------------------------------

export function usePaymentTerms(opts: { includeInactive?: boolean } = {}) {
  const includeInactive = opts.includeInactive ?? false;
  return useQuery({
    queryKey: [ROOT, "payment-terms", { includeInactive }],
    queryFn: () =>
      api.get<PaymentTerms[]>(
        `/subledger/payment-terms${includeInactive ? "?include_inactive=true" : ""}`,
      ),
    staleTime: 60_000,
  });
}

export function useCreatePaymentTerms() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: PaymentTermsPayload) =>
      api.post<PaymentTerms>("/subledger/payment-terms", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT, "payment-terms"] }),
  });
}

/** PUT, not PATCH — the backend replaces the row, so the form always sends every field. */
export function useUpdatePaymentTerms() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ termsId, payload }: { termsId: number; payload: PaymentTermsPayload }) =>
      api.put<PaymentTerms>(`/subledger/payment-terms/${termsId}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT, "payment-terms"] }),
  });
}

// --- Ageing bucket sets ---------------------------------------------------------------------

export function useAgeingBucketSets(opts: { includeInactive?: boolean } = {}) {
  const includeInactive = opts.includeInactive ?? false;
  return useQuery({
    queryKey: [ROOT, "ageing-bucket-sets", { includeInactive }],
    queryFn: () =>
      api.get<AgeingBucketSet[]>(
        `/subledger/ageing-bucket-sets${includeInactive ? "?include_inactive=true" : ""}`,
      ),
    staleTime: 60_000,
  });
}

export function useCreateAgeingBucketSet() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: AgeingBucketSetCreatePayload) =>
      api.post<AgeingBucketSet>("/subledger/ageing-bucket-sets", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT, "ageing-bucket-sets"] }),
  });
}

export function useUpdateAgeingBucketSet() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ setId, payload }: { setId: number; payload: AgeingBucketSetUpdatePayload }) =>
      api.patch<AgeingBucketSet>(`/subledger/ageing-bucket-sets/${setId}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT, "ageing-bucket-sets"] }),
  });
}

// --- AR/AP defaults -------------------------------------------------------------------------

export function useArApDefaults() {
  return useQuery({
    queryKey: [ROOT, "defaults"],
    queryFn: () => api.get<ArApDefaults>("/subledger/defaults"),
    staleTime: 60_000,
  });
}

export function useUpdateArApDefaults() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: ArApDefaultsPayload) =>
      api.patch<ArApDefaults>("/subledger/defaults", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT, "defaults"] }),
  });
}

// --- Documents ------------------------------------------------------------------------------

/** `Idempotency-Key` is the draft's UUID, so a retried post replays instead of duplicating. */
export function usePostDocument(role: PartnerRole) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      payload,
      idempotencyKey,
    }: {
      payload: DocumentCreatePayload;
      idempotencyKey: string;
    }) =>
      api.post<PartnerDocument>(`/subledger/${role}/documents`, payload, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/** Open balance, credit limit and headroom for the partner typeahead, plus the open items
 * the allocation screen works from. */
export function usePartnerEnquiry(role: PartnerRole, partnerId: number | null, asOf?: string) {
  return useQuery({
    queryKey: [ROOT, role, "enquiry", partnerId, asOf ?? null],
    queryFn: () =>
      api.get<PartnerEnquiry>(
        `/subledger/${role}/enquiry/${partnerId}${asOf ? `?as_of=${asOf}` : ""}`,
      ),
    enabled: partnerId !== null,
  });
}

// --- Allocation -----------------------------------------------------------------------------

/** The preview is the backend's own dry run — same service, no commit — never client-side
 * arithmetic, so what the screen shows before Post is what Post writes. */
export function useAllocationPreview(role: PartnerRole) {
  return useMutation({
    mutationFn: (payload: AllocationPayload) =>
      api.post<AllocationPreview>(`/subledger/${role}/allocations/preview`, payload),
  });
}

export function useAutoAllocate(role: PartnerRole) {
  return useMutation({
    mutationFn: (payload: AutoAllocatePayload) =>
      api.post<AllocationPreview>(`/subledger/${role}/allocations/auto`, payload),
  });
}

export function usePostAllocation(role: PartnerRole) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      payload,
      idempotencyKey,
    }: {
      payload: AllocationPayload;
      idempotencyKey: string;
    }) =>
      api.post<Allocation>(`/subledger/${role}/allocations`, payload, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/** A batch is one unit of work: the key covers the whole thing, and a refused line refuses
 * all of them, so a retry replays rather than posting the good lines twice. */
export function usePostBatch(role: PartnerRole) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      payload,
      idempotencyKey,
    }: {
      payload: BatchPayload;
      idempotencyKey: string;
    }) =>
      api.post<BatchResult>(`/subledger/${role}/batches`, payload, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}
