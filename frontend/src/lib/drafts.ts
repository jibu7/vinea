/**
 * Client-side draft autosave (localStorage), keyed by user + company — nothing touches the
 * ledger until Post. Each draft carries a stable UUID used as the Idempotency-Key, so a
 * retried post replays the original entry instead of creating a duplicate.
 */

export interface Draft<T> {
  draftId: string;
  updatedAt: string;
  data: T;
}

function storageKey(module: string, companyId: number, userId: number): string {
  return `vinea.draft.${module}.${companyId}.${userId}`;
}

export function loadDraft<T>(module: string, companyId: number, userId: number): Draft<T> | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(storageKey(module, companyId, userId));
  if (!raw) return null;
  try {
    return JSON.parse(raw) as Draft<T>;
  } catch {
    return null;
  }
}

export function saveDraft<T>(module: string, companyId: number, userId: number, draft: Draft<T>): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(storageKey(module, companyId, userId), JSON.stringify(draft));
}

export function clearDraft(module: string, companyId: number, userId: number): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(storageKey(module, companyId, userId));
}

export function newDraftId(): string {
  return crypto.randomUUID();
}
