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

