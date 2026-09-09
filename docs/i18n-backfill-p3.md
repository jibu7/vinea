# P3 Definition-of-Done corrections

P3 was accepted on a DoD whose claims did not all hold. They are recorded here rather than
inherited quietly by later phases; the full clause-by-clause check is in
[`p3-dod-audit.md`](./p3-dod-audit.md).

| Claim in P3's DoD | What shipped | Decision |
|---|---|---|
| "next-intl with every string externalised" | 28 files, 327 violations of `react/jsx-no-literals` | Backfill — the rest of this document, tracked as [issue #6](https://github.com/jibu7/vinea/issues/6) |
| "IndexedDB drafts keyed by draft UUID as `Idempotency-Key`" — **P4 step 7's wording, not P3's** | `localStorage` autosave; the draft UUID *is* the `Idempotency-Key`, so only the storage engine differs | **Keep localStorage.** Decided at P4 step 7 |

> **Correction.** An earlier version of this file, and a comment on issue #6, said P3's DoD
> claimed IndexedDB. It did not: P3's prompt says "IndexedDB/**localStorage** keyed by user +
> company", so localStorage was compliant there. The IndexedDB wording is **P4 step 7's**. The
> deviation is real and the decision below stands; the phase it belongs to was wrong.

## The drafts decision

Drafts are small, per-device and disposable, and localStorage's synchronous API is what makes
"save on every keystroke" trivial — IndexedDB would buy asynchrony and a larger quota that
nothing here needs. What localStorage does not give is a quota we can rely on, so
`frontend/src/lib/drafts.ts` caps the total at 512 KB and evicts oldest-first, and the key
carries module + company + user so a draft cannot leak across a company switch or between two
users on one browser. Both are covered in `frontend/src/lib/drafts.test.ts`.

This is a decision, not an oversight. If a later phase needs drafts that survive a quota
squeeze, outlive a device, or hold something large, that is the point to revisit it — and the
DoD claim should be corrected rather than the storage silently swapped.

---

# P3 i18n backfill — the screens that are not externalised

P3's Definition of Done said "next-intl with every string externalised". That was not true
when P3 was accepted, and P4 step 6 did not inherit the claim quietly: the AR/AP screens were
fully externalised and `react/jsx-no-literals` was scoped to them in `frontend/.eslintrc.json`
so they cannot regress. Everything below still fails that same rule.

Tracked as [issue #6](https://github.com/jibu7/vinea/issues/6). This file is the snapshot taken when P4 step 6 was accepted (2026-09-09). It is the checklist
for the backfill, not a description of current state once work starts — the authoritative
count is always the rule itself.

## Reproducing this list

```sh
cd frontend
cat > .eslintrc.p3scan.json <<'JSON'
{
  "extends": "next/core-web-vitals",
  "rules": { "react/jsx-no-literals": ["error", { "noStrings": true, "ignoreProps": true }] }
}
JSON
npx eslint -c .eslintrc.p3scan.json --no-eslintrc --ext .tsx src
rm .eslintrc.p3scan.json
```

## Scope of the debt

**28 files, 327 violations** — JSX children only. Attribute copy
(`placeholder`, `title`, `aria-label`, `label`, `description`) is *not* counted here; the
rule cannot see it without also flagging every `className`. The real number is higher.

| Violations | File |
|---:|---|
| 54 | `frontend/src/app/design/page.tsx` |
| 26 | `frontend/src/app/(shell)/gl/reports/account-transactions/page.tsx` |
| 22 | `frontend/src/app/(shell)/gl/reports/trial-balance/page.tsx` |
| 20 | `frontend/src/app/(shell)/gl/enquiries/account/page.tsx` |
| 19 | `frontend/src/app/(shell)/maintenance/chart-of-accounts/page.tsx` |
| 18 | `frontend/src/app/(shell)/gl/reports/chart-of-accounts/page.tsx` |
| 17 | `frontend/src/app/(shell)/maintenance/currencies/page.tsx` |
| 15 | `frontend/src/app/design/prototypes/dashboard/page.tsx` |
| 14 | `frontend/src/app/(shell)/maintenance/company-details/page.tsx` |
| 11 | `frontend/src/app/(shell)/administration/users/page.tsx` |
| 11 | `frontend/src/app/(shell)/gl/enquiries/trial-balance/page.tsx` |
| 11 | `frontend/src/app/(shell)/gl/entries/[id]/page.tsx` |
| 11 | `frontend/src/app/(shell)/maintenance/rename-account/page.tsx` |
| 11 | `frontend/src/design/components/line-grid.tsx` |
| 10 | `frontend/src/app/design/prototypes/pos/page.tsx` |
| 9 | `frontend/src/app/(shell)/maintenance/taxes/page.tsx` |
| 9 | `frontend/src/app/(shell)/page.tsx` |
| 9 | `frontend/src/app/design/prototypes/workspace/page.tsx` |
| 8 | `frontend/src/app/(shell)/maintenance/branches/page.tsx` |
| 6 | `frontend/src/app/(shell)/maintenance/projects/page.tsx` |
| 3 | `frontend/src/app/(shell)/app-shell.tsx` |
| 3 | `frontend/src/app/error.tsx` |
| 3 | `frontend/src/design/components/command-palette.tsx` |
| 2 | `frontend/src/app/(shell)/gl/journal-batches/new/page.tsx` |
| 2 | `frontend/src/design/components/document-workspace.tsx` |
| 1 | `frontend/src/app/(shell)/layout.tsx` |
| 1 | `frontend/src/app/(shell)/maintenance/defaults/page.tsx` |
| 1 | `frontend/src/design/components/combobox.tsx` |

## Suggested order

1. **`src/app/(shell)/**` screens users actually reach** — the GL maintenance, enquiry and
   report pages, the dashboard, the shell and the users screen. That is 219
   of the 327.
2. **`src/design/components/**`** — `line-grid`, `command-palette`, `combobox`,
   `document-workspace`. Shared primitives, so each fix pays off across every screen.
3. **`src/app/design/**` prototypes** — design-system demo pages, not product surface.
   Lowest value; consider excluding them from the rule permanently instead.

## Definition of done for the backfill

Delete the `overrides` block in `frontend/.eslintrc.json` and move
`react/jsx-no-literals` into the top-level `rules`, so the whole app is held to what the
AR/AP screens already meet. The companion attribute check in
`frontend/src/features/subledger/i18n-coverage.test.ts` should widen its file list at the
same time.

