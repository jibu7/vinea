# P3 Definition-of-Done audit

Taken at P4 step 7, after four separate P3 claims turned out to be untrue. Rather than keep
discovering them one at a time, every clause of P3's DoD — and the step-5 and step-8
requirements the DoD leans on — is checked here with evidence.

Source: `.github/prompts/phase-3-frontend-foundation.prompt.md`.

**Verdict counts: 6 true, 4 false, 4 partial, 2 unverifiable from the repository.**

## The DoD clauses

| # | Clause | Verdict | Evidence |
|---|---|---|---|
| 1 | Owner approved the step-2 prototypes | **closed** → see below | An owner action outside the repository. No approval record is committed. |
| 2 | The sidebar renders the **complete** Appendix C tree in the owner's order, later phases tagged | **false** | Eighteen entries were missing: Receipt, Supplier invoice, Payment, AP Allocate, both batch screens, the Allocation report, both partner listings, the Transaction listings and every AP report. Order and tagging were correct for what was there. Fixed on this PR; the whole tree is now pinned in `appendix-c-order.test.tsx`. |
| 3 | Every string is externalised; one locale (`en`) | **false** | 28 files, 327 `react/jsx-no-literals` violations. One locale is correct. Tracked as [#6](https://github.com/jibu7/vinea/issues/6); the P4 AR/AP screens are clean and lint-enforced. |
| 4 | RWF renders with no decimals everywhere money is shown | **true** | `formatMoney` honours `decimal_places`, covered in `format.test.ts`. The only other numeric formatter in the app is LineGrid's editing display (`line-grid.tsx:60`), which formats raw entry digits, not a currency amount. |
| 5 | A **journal** entry can be drafted, autosaved, posted, seen in enquiries and reversed from the UI | **partial** | Draft → post → enquiry → trial balance is covered by `journal-flow.spec.ts`. **Reversal is built** (`gl/entries/[id]/page.tsx`, date + reason + one-reversal rule) but has no test at any level. |
| 6 | A **cashbook** entry can be drafted, autosaved, posted, seen in enquiries and reversed from the UI | **partial** | The screen exists (`gl/cashbook-batches/new`) and the backend path is tested, but no frontend test or e2e touches it. `grep -rl "cashbook" e2e/` returns nothing. |
| 7 | Light and dark both pass `axe` | **partial** | True for the three screens the spec named (dashboard, workspace, chart of accounts) in both themes. Not a statement about the app: eight P3 screens have no axe coverage. P4 added four more covered screens. |
| 8 | Frontend CI jobs green (`lint · typecheck · test · build · e2e`) | **true** | All five jobs exist in `ci.yml` and pass. |
| 9 | Backend suite unchanged and green | **true** | Green throughout; P4 has only added to it. |
| 10 | Final report with screenshots of the six main screens | **unverifiable** → see below | No screenshots were committed by P3. P4 step 6 committed its own under `docs/screenshots/`, which is the practice going forward. |

## Step-5 requirements the DoD depends on

| # | Requirement | Verdict | Evidence |
|---|---|---|---|
| 11 | LineGrid keyboard model — arrows navigate rows, Enter on the last row adds one | **false** | Arrow and Enter navigation was broken in every amount column (debit, credit, cashbook amount) from P3 step 5 (`58f0176`) until `c80f992` — the cell's `data-col` was a sequential counter while its handlers passed ids in the 100s, so the lookup found nothing and focus stayed put. The P3 tests asserted only on the description column, whose `data-col` coincidentally equals its handler id. Fixed, and the tests are now parameterised across all six navigable columns; they produce 16 failures against the pre-fix grid. |
| 12 | Drafts autosave client-side, **IndexedDB/localStorage** keyed by user + company, UUID as `Idempotency-Key` | **true** | P3 allowed either engine; localStorage keyed by module + company + user is compliant, and the draft UUID is the `Idempotency-Key`. *(P4 step 7's prompt asks specifically for IndexedDB — that deviation is P4's, not P3's, and is recorded in `i18n-backfill-p3.md`.)* |
| 13 | `is_rounding_line` highlighted as engine-generated on the posted entry | **true** | `gl/entries/[id]/page.tsx:155` rows highlight, and rounding lines are excluded from the debit/credit foots. |
| 14 | Error codes mapped to inline field errors, not toasts | **partial** | `period_closed` is covered by `closed-period.spec.ts` and `idempotency_key_reused` by `idempotent-post.spec.ts`. `unbalanced_entry` is prevented client-side rather than mapped; `missing_exchange_rate` has no test. |
| 15 | Post disabled while unbalanced | **true** | `unbalanced-journal.spec.ts`. |
| 16 | Vitest for `formatMoney`, the LineGrid keyboard model, and permission-filtered nav | **true** | All three exist — though see #11 for what the keyboard tests were actually asserting on. |

## The two unverifiable clauses, and what would settle each

"Unverifiable" is a status for this audit, not somewhere to leave a clause. Both are
unverifiable because the evidence was never written down, not because it cannot be — and both
have a cheap fix that makes the *next* phase's equivalent claim checkable.

### #1 — "Owner approved the step-2 prototypes"

**Why it cannot be checked:** approval happened in conversation. Nothing in the repository, the
git history or the PR record names the prototypes that were approved, or when.

**What would make it verifiable:** a line in the phase's final report naming the approved
artefact and the date — for P3, the three `/design/prototypes/*` routes as they stood at a given
commit. A STOP-gate approval that is not written down cannot be distinguished later from one
that was assumed. P4's two gates (after steps 2 and 5) should record the approving message and
the commit it approved, in the phase report; that costs one line and settles the clause for good.

**Retroactively:** done — `approvals.md` now carries a row for it reading "approved outside
record, owner confirmed", which closes this line as answered rather than open. Future gates are
recorded when they happen.

### #10 — "Final report with screenshots of the six main screens"

**Why it cannot be checked:** P3's report, if written, lives in conversation. No screenshots were
committed, so there is no way to tell whether six screens were shown, or which.

**What would make it verifiable:** screenshots committed to the repository, as P4 step 6 now does
under `docs/screenshots/p4-step-6/` with a README naming each shot and the script that takes
them. A reviewer can then re-run the script and diff.

**Retroactively:** cheap and worth doing — `frontend/scripts/capture-p4-screens.ts` already takes
a themed, sized set against the dev stack, and pointing it at P3's six screens would produce the
missing evidence in one run. Filed with the other P3 gaps rather than done here, because it is
P3 surface and this PR is already large.

## What was fixed on this PR

#2 (tree completeness), #11 (LineGrid keyboard model, plus its tests) — both cheap and both
already regressions in the making.

## What becomes an issue

| Clause | Issue |
|---|---|
| #3 i18n backfill | [#6](https://github.com/jibu7/vinea/issues/6), already open |
| #5 reversal untested, #6 cashbook untested, #14 error-mapping gaps | one issue: "P3 frontend test gaps" |
| #7 axe coverage is three screens, not the app | one issue: "extend axe coverage to every shipped screen" |

## Why this audit exists

Four P3 claims failed in four separate rounds of review, each found by someone asking rather
than by a check. The pattern is that a DoD written as prose gets marked done as prose. The
counter-measure is the one applied to the tree and the keyboard model here: turn the claim into
a test that fails when the claim stops being true. Where that is not yet done, this table says
so plainly instead of leaving the clause looking satisfied.
