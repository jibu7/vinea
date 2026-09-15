"use client";

import { useCallback, useMemo } from "react";
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

/** Permission check for screens that show read-only data but gate their write controls —
 * the server is still the authority, this only keeps disabled buttons out of the way. */
export function useHasPermission(): (permission: string) => boolean {
  const { data: me } = useMe();
  const granted = useMemo(() => new Set(me?.permissions ?? []), [me?.permissions]);
  return useCallback((permission: string) => granted.has(permission), [granted]);
}

export function isApiError(err: unknown): err is ApiError {
  return err instanceof ApiError;
}

// --- The P1 gap: the halves of auth that shipped without screens -----------------------------
//
// Every endpoint below has existed since P1 and had no caller, which is the failure rule 14
// exists to catch: a capability the product does not have, passing every test and appearing in
// the schema. These hooks and their screens delete six lines from the rule-14 register.
//
// None of them returns a token. The one-time tokens arrive by email and reach these hooks only
// as a `?token=` the user's own mail client handed them — see `app/services/email.py`.

/** Ask for a reset mail. **Always succeeds**, even for an address with no account: the server
 * refuses to reveal which addresses exist, so the screen must show the same thing either way. */
export function useRequestPasswordReset() {
  return useMutation({
    mutationFn: (email: string) => api.post<{ status: string }>("/auth/password-reset/request", { email }),
  });
}

/** Set a new password with a mailed token. The server clears the session cookies and revokes
 * every other session, so the user lands back at sign-in — which is the point of a reset. */
export function useConfirmPasswordReset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { token: string; new_password: string }) =>
      api.post<void>("/auth/password-reset/confirm", payload),
    onSuccess: () => queryClient.clear(),
  });
}

/** Send the verification mail again, for a signed-in user who never clicked the first one. */
export function useRequestEmailVerification() {
  return useMutation({
    mutationFn: () => api.post<{ status: string }>("/auth/email-verification/request"),
  });
}

/** The landing page for the link in that mail. */
export function useConfirmEmailVerification() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (token: string) => api.post<void>("/auth/email-verification/confirm", { token }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: meQueryKey }),
  });
}

/** Accept an invitation and sign in, in one step — the response is a session, so an invited
 * user goes straight into the tenancy rather than to a login form they have no password for. */
export function useAcceptInvitation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { token: string; full_name: string; password: string }) =>
      api.post<SessionResponse>("/invitations/accept", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: meQueryKey }),
  });
}
