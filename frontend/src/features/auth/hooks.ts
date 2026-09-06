"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { LoginPayload, MeResponse, SessionResponse, SignupPayload } from "./types";

export const meQueryKey = ["auth", "me"] as const;

/** 401 here means "not signed in" — callers redirect to /login, never throw a page error. */
export function useMe() {
  return useQuery({
    queryKey: meQueryKey,
    queryFn: () => api.get<MeResponse>("/auth/me"),
    retry: false,
    staleTime: 60_000,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: LoginPayload) => api.post<SessionResponse>("/auth/login", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: meQueryKey }),
  });
}

export function useSignup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: SignupPayload) => api.post<SessionResponse>("/auth/signup", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: meQueryKey }),
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<void>("/auth/logout"),
    onSuccess: () => queryClient.clear(),
  });
}

export function useSwitchCompany() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (companyId: number) => api.post<SessionResponse>("/auth/switch-company", { company_id: companyId }),
    onSuccess: () => queryClient.invalidateQueries(), // switching tenants invalidates every cached query
  });
}

export function isApiError(err: unknown): err is ApiError {
  return err instanceof ApiError;
}
