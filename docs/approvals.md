# STOP-gate approvals

Phases carry mandatory STOP gates: the work pauses, a report goes to the owner, and nothing
continues until they say so. Those approvals happened in conversation and left no trace, which
is why P3's DoD clause "owner approved the step-2 prototypes" came back **unverifiable** in
`p3-dod-audit.md` — not because it did not happen, but because nothing recorded it.

One line per gate, from here on. An approval that is not written down cannot later be told
apart from one that was assumed.

| Phase | Step | Gate | Artefact approved | Commit | Approving message |
|---|---|---|---|---|---|
| P3 | 2 | Prototypes | `/design/prototypes/{dashboard,workspace,pos}` | not recorded | **Approved outside record, owner confirmed.** Backfilled at P4 step 7; the approval happened, the evidence was never committed. Closes the audit line as answered rather than open. |
| P4 | 2 | Posting contract and control-account guard | `partner_documents` schema, PostingEngine guard, `assert_subledger_invariants` | `ffffe52` | "Continue" — owner approved the step-2 report and the phase proceeded to documents. Recorded retrospectively at step 7 from this conversation; the message was not captured verbatim at the time. |
| P4 | 5 | Ageing, statements, listings, enquiries | ageing by bucket set, statement PDF jobs, transaction listings, partner enquiries | `ffffe52` | "Continue" — owner approved the step-5 report and the phase proceeded to UI. Recorded retrospectively at step 7; not captured verbatim. |
| P4 | 6 | Maintenance UI (review, not a plan gate) | eight AR/AP maintenance screens | `f65581e` | "Step 6 accepted with these closes" — accepted with seven follow-up items, all delivered. |
| P5 | 2 | Stock ledger, costing engine and posting contract | `stock_moves` + the two caches, `verify_stock_balances()`, the costing rules of decision 4, `receive_stock()` / `issue_stock()`, `assert_stock_invariants` | `fb7126d` | "Step 2 — approved on conditions. Land 1–4 on p5-step-2 before opening the PR." — six conditions, all delivered before the PR: the kernel/model line-by-line report; checker sensitivity with raw output for both provers; decision 1 restated so moves post regardless of value; decision 2 generalised to every negative→non-negative crossing; the primitive-level tape rows 1–9 with row 9 = Depot 2 / 300, avg 150, residue 34; and CI green. Row 9 supersedes the figure in the phase prompt (334 / 167) on the owner's direction — the step-5 document-level tape carries the same change. |
| P5 | 5 | Enquiry, reports and the costing tape | item enquiry, the Movement / Transaction / Valuation / Count reports, and the document-level costing tape (`backend/tests/inventory/test_costing_tape_documents.py`) | `8fcf814` | "Step 5 — approved. Before opening the PR: 1. The session created a `vinea` superuser role and database. State which role the harness connected as for the 700-run. Superusers bypass RLS entirely — FORCE has no effect on them — so if any test session ran as superuser, the RLS tests in that run could not have failed. CI will settle it either way; I want it stated. 2. Open the PR, CI green, approvals row with these words. Step 6 on green. Carry into step 8 (reports UI): 3. Any running 'average' shown on Movement or the enquiry is value ÷ quantity at that date, not the cost the issue was posted at — a backdated receipt makes the two differ in view. Either don't show one, or label it as the as-at ratio. No running average as a column named 'cost'." — conditions 1 and 3 answered below; step 6 proceeds on green CI. |
| P4 | 9 | Tests, CI and phase close (review, not a plan gate) | the acceptance tape (`frontend/e2e/ar-ap-acceptance.spec.ts`), the post-dated instruments screen, `docs/p4-final-report.md` | `474c9b1` | **Awaiting the owner.** The row is opened at submission, not filled in on the author's behalf: the point of this file is that an approval nobody wrote down cannot later be told apart from one that was assumed. Replace this cell with the owner's words when they arrive, and pin the commit they were looking at. The commit named is the one the report describes (`474c9b1`); this row is the commit after it, which is the closest a file can get to citing itself. |

## Conditions carried out of P5 step 5

**1 — which role the suite connected as.** Stated, with evidence. The `vinea` superuser that
session created is reachable only through `MIGRATION_DATABASE_URL`, which `tests/conftest.py`
uses for `CREATE DATABASE`, `CREATE ROLE` and Alembic. Line 45 of that file then overwrites
`DATABASE_URL` with the `vinea_app_test` role — created `NOSUPERUSER NOCREATEDB NOBYPASSRLS`,
and not the owner of any table, so RLS binds on it twice over. Read back from inside the
suite's own session, in the database the 700-run used:

```
current_user=vinea_app_test  session_user=vinea_app_test  rolsuper=False  rolbypassrls=False
```

No test session ran as superuser, so no RLS test in that run was vacuous. The suite already
pins this itself — `tests/test_tenancy_rls.py::test_the_app_role_cannot_bypass_rls` asserts
both flags from inside its own connection — which also covers the one soft spot noticed while
checking: `conftest._recreate_test_database` reuses a pre-existing `vinea_app_test` role
without inspecting its attributes, so a machine carrying a stale `BYPASSRLS` role of that name
is caught by that test rather than by the role creation.

**3 — no running average called "cost" (carries to step 8).** Recorded in the code the screens
will be built from, not only here. `MovementRow` and `enquiries.MoveRow` each carry a docstring
saying why they expose no average, and the valuation report's ratio was renamed from
`unit_cost` to `average_as_at` in the same change: value / quantity on the as-of date is not
the cost anything was posted at, because a backdated receipt moves the ratio while leaving
every earlier-posted issue at the value it was given. The cost a move was actually posted at
stays available per row as `StockMove.unit_cost`, on the transaction report and the enquiry.

## What a good row looks like

Everything except the first three: **the artefact named, the commit it was at, and the owner's
words**. The three backfilled rows are weaker on purpose — they record what is actually known
rather than reconstructing a quote, and say so.

P4 steps 2 and 5 share a commit because both reports were approved before the phase's work was
first pushed; the commit is where that work landed, not where each gate was cleared. Future
gates should be recorded when they happen, against the commit under review at that moment.

## Filling this in

At each STOP gate, before continuing:

1. `git rev-parse --short HEAD` — the commit the report describes.
2. Paste the owner's approving message verbatim, however short.
3. Name the artefact concretely enough that someone can go and look at it.
