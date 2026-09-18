"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type {
  DeviceCreatePayload,
  DeviceSyncResult,
  FiscalCode,
  FiscalDevice,
  FiscalItemClass,
  ItemRegistration,
  TinLookup,
} from "./types";

/** One root key for the module, so a device write invalidates every list derived from it —
 * the same shape `gl`, `inventory` and `order-entry` use. */
const ROOT = "fiscal";

export function useFiscalDevices() {
  return useQuery({
    queryKey: [ROOT, "devices"],
    queryFn: () => api.get<FiscalDevice[]>("/fiscal/devices"),
    staleTime: 30_000,
  });
}

export function useRegisterFiscalDevice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: DeviceCreatePayload) =>
      api.post<FiscalDevice>("/fiscal/devices", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/**
 * Initialize — and therefore **activate**, because there is no other way in.
 *
 * P7 step 1's decision: a device is activated by telling the authority about it and storing
 * what comes back, so a suspended device is brought back by re-initializing rather than by a
 * separate Activate button. `activate()` in `app/fiscal/devices.py` refuses a device with no
 * `sdc_id`, which is what makes that true rather than a convention; a screen offering
 * Activate would be offering a button that could only ever fail.
 *
 * `Idempotency-Key` is **required** by the endpoint, not optional politeness: this call is
 * not naturally idempotent from the caller's side, and a double-click while the authority is
 * slow would otherwise re-initialize a live device — which is how its keys get reissued.
 */
export function useInitializeFiscalDevice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ deviceId, idempotencyKey }: { deviceId: number; idempotencyKey: string }) =>
      api.post<FiscalDevice>(`/fiscal/devices/${deviceId}/initialize`, undefined, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useSuspendFiscalDevice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ deviceId, reason }: { deviceId: number; reason: string }) =>
      api.post<FiscalDevice>(`/fiscal/devices/${deviceId}/suspend`, { reason }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/**
 * Sync codes — both tables, in one press.
 *
 * Two endpoints and one button, deliberately. They are two calls to the authority because
 * they are two of its endpoints with two watermarks, but they are one *job*: an operator who
 * refreshed the code tables and not the classification would have a Tax-types screen offering
 * A–D and an Items screen whose class typeahead found nothing, and no way to tell why. The
 * classification is the slow one, so it runs second and the result carries both counts.
 */
export function useSyncFiscalCodes() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (deviceId: number) => {
      const codes = await api.post<DeviceSyncResult>(`/fiscal/devices/${deviceId}/sync-codes`);
      const classes = await api.post<DeviceSyncResult>(
        `/fiscal/devices/${deviceId}/sync-item-classes`,
      );
      return { codes, classes };
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}

/** The synced code tables, filtered by class. `enabled` on the class, so a screen that has
 * not decided which table it wants does not fetch all of them. */
export function useFiscalCodes(codeClass?: string) {
  return useQuery({
    queryKey: [ROOT, "codes", codeClass ?? "all"],
    queryFn: () =>
      api.get<FiscalCode[]>(
        codeClass ? `/fiscal/codes?code_class=${encodeURIComponent(codeClass)}` : "/fiscal/codes",
      ),
    staleTime: 5 * 60_000,
  });
}

/**
 * The authority's item classification, searched rather than listed.
 *
 * Tens of thousands of rows: a picker that loaded them all would be a picker nobody could
 * use. The search runs on the server and the page is bounded there too, so what a slow
 * connection carries is fifty rows and not a catalogue.
 *
 * **Debounced**, because the caller is a typeahead: without it, typing a ten-digit class code
 * is ten requests, nine of which are for prefixes nobody wanted. 250ms is under the threshold
 * where a picker feels slow and over the gap between keystrokes.
 */
export function useFiscalItemClasses(search: string) {
  const term = useDebounced(search.trim(), 250);
  return useQuery({
    queryKey: [ROOT, "item-classes", term],
    queryFn: () =>
      api.get<FiscalItemClass[]>(
        term ? `/fiscal/item-classes?search=${encodeURIComponent(term)}` : "/fiscal/item-classes",
      ),
    staleTime: 5 * 60_000,
  });
}

/** `value`, but only once it has stopped changing for `delay` ms. */
function useDebounced<T>(value: T, delay: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

/** Every item with its registration beside it — including the ones RRA has never heard of,
 * which are the ones that would refuse a fiscalized sale (`fiscal_class_missing`). */
export function useItemRegistrations() {
  return useQuery({
    queryKey: [ROOT, "items"],
    queryFn: () => api.get<ItemRegistration[]>("/fiscal/items"),
    staleTime: 30_000,
  });
}

/**
 * Verify TIN, from the Customers and Suppliers screens.
 *
 * A **mutation** over a GET, which looks wrong and is not: the authority's opinion of a TIN
 * is an answer at a moment rather than a fact about the partner, and nothing is stored. What
 * the screen needs is a button that asks *now* and shows what came back — a `useQuery` would
 * cache the answer and re-ask it on a window focus, which is the opposite of both.
 */
export function useVerifyTin() {
  return useMutation({
    mutationFn: ({ deviceId, tin }: { deviceId: number; tin: string }) =>
      api.get<TinLookup>(`/fiscal/devices/${deviceId}/lookup-tin?tin=${encodeURIComponent(tin)}`),
  });
}
