"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { OrderDefaults, OrderDefaultsPayload } from "./types";

/** One root key for the module, so a write invalidates everything order entry derives from
 * these keys — the same shape `inventory` and `gl` use. */
const ROOT = "order-entry";

// --- Defaults (P6 decision 10) -------------------------------------------------------------

export function useOrderDefaults() {
  return useQuery({
    queryKey: [ROOT, "defaults"],
    queryFn: () => api.get<OrderDefaults>("/oe/defaults"),
    staleTime: 60_000,
  });
}

/** A **PUT**, not a PATCH: `/oe/defaults` takes the settings the screen is showing and the
 * service writes only the keys present in the body. */
export function useSaveOrderDefaults() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: OrderDefaultsPayload) =>
      api.put<OrderDefaults>("/oe/defaults", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [ROOT] }),
  });
}
