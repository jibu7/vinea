/**
 * Client-side draft autosave, keyed by module + company + user — nothing touches the ledger
 * until Post. Each draft carries a stable UUID used as the `Idempotency-Key`, so a retried
 * post replays the original entry instead of creating a duplicate.
 *
 * **localStorage, not IndexedDB.** P3's Definition of Done said IndexedDB and shipped this;
 * the correction is recorded rather than inherited quietly — see `docs/i18n-backfill-p3.md`
 * for the sibling case and the tracking issue. Keeping localStorage is a decision, not an
 * oversight: drafts are small, per-device and disposable, and the synchronous API is what
 * makes "save on every keystroke" trivial. What localStorage does *not* give us is a quota
 * we can rely on, hence the cap below.
 */

export interface Draft<T> {
  draftId: string;
  updatedAt: string;
  data: T;
}

const PREFIX = "vinea.draft.";

/** Browsers give localStorage ~5 MB for the whole origin, shared with everything else we
 * keep there. Drafts get a slice of it and evict their own oldest first, so a long-running
 * session cannot fill the origin's quota and start throwing on unrelated writes. */
export const MAX_TOTAL_DRAFT_BYTES = 512 * 1024;

/** The key carries both ids: a draft belongs to one user in one company, and switching
 * either must not surface the other's work. */
export function draftKey(module: string, companyId: number, userId: number): string {
  return `${PREFIX}${module}.${companyId}.${userId}`;
}

function safeStorage(): Storage | null {
  try {
    if (typeof window === "undefined") return null;
    return window.localStorage;
  } catch {
    // Private windows and "block site data" make the accessor itself throw.
    return null;
  }
}

export function loadDraft<T>(module: string, companyId: number, userId: number): Draft<T> | null {
  const storage = safeStorage();
  if (!storage) return null;
  const raw = storage.getItem(draftKey(module, companyId, userId));
  if (!raw) return null;
  try {
    return JSON.parse(raw) as Draft<T>;
  } catch {
    return null;
  }
}

interface StoredDraft {
  key: string;
  bytes: number;
  updatedAt: string;
}

function storedDrafts(storage: Storage): StoredDraft[] {
  const out: StoredDraft[] = [];
  for (let i = 0; i < storage.length; i += 1) {
    const key = storage.key(i);
    if (!key?.startsWith(PREFIX)) continue;
    const raw = storage.getItem(key) ?? "";
    let updatedAt = "";
    try {
      updatedAt = (JSON.parse(raw) as Draft<unknown>).updatedAt ?? "";
    } catch {
      // Unparseable drafts are evicted first: they cannot be restored anyway.
    }
    out.push({ key, bytes: key.length + raw.length, updatedAt });
  }
  return out;
}

/** Oldest first, unparseable before that — the draft you are typing in is the newest, so it
 * is the last thing to go. */
function evictUntilUnder(storage: Storage, budget: number, keep: string): void {
  const drafts = storedDrafts(storage).sort((a, b) => a.updatedAt.localeCompare(b.updatedAt));
  let total = drafts.reduce((sum, d) => sum + d.bytes, 0);
  for (const draft of drafts) {
    if (total <= budget) return;
    if (draft.key === keep) continue;
    storage.removeItem(draft.key);
    total -= draft.bytes;
  }
}

export function saveDraft<T>(
  module: string,
  companyId: number,
  userId: number,
  draft: Draft<T>,
): void {
  const storage = safeStorage();
  if (!storage) return;
  const key = draftKey(module, companyId, userId);
  const raw = JSON.stringify(draft);

  const others = storedDrafts(storage).filter((d) => d.key !== key);
  const total = others.reduce((sum, d) => sum + d.bytes, 0) + key.length + raw.length;
  if (total > MAX_TOTAL_DRAFT_BYTES) {
    evictUntilUnder(storage, MAX_TOTAL_DRAFT_BYTES - (key.length + raw.length), key);
  }

  try {
    storage.setItem(key, raw);
  } catch {
    // Quota exceeded even after eviction (something else owns the origin's space). Drop every
    // other draft and try once more; if it still fails, the draft is simply not saved —
    // losing autosave is acceptable, breaking the screen is not.
    for (const other of storedDrafts(storage)) {
      if (other.key !== key) storage.removeItem(other.key);
    }
    try {
      storage.setItem(key, raw);
    } catch {
      /* give up quietly */
    }
  }
}

export function clearDraft(module: string, companyId: number, userId: number): void {
  const storage = safeStorage();
  if (!storage) return;
  storage.removeItem(draftKey(module, companyId, userId));
}

export function newDraftId(): string {
  return crypto.randomUUID();
}
