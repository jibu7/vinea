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
| P5 | 6 | Maintenance UI (review, not a plan gate) | the seven Maintenance → Inventory screens, `frontend/e2e/inventory-maintenance.spec.ts`, `docs/screenshots/p5-step-6/` | `6297d4c` | "Step 6 — approved with these before the PR: 1. Variable barcodes: rename the built screen's row to 'Barcodes' (what it is: per-item barcodes with UoM and pack quantity, listing + duplicate detector). Put 'Variable barcodes' back in the tree tagged P11 — it's the POS scale-label pattern (prefix, item-code digits, weight/price digits). Note in Appendix C's order test and in the P5 final report as a plan deviation: the prompt misread the label; that's on the owner side, not the build. 2. control_type 'INV' vs 'inventory': fix the class, not the instance. One source for enum string values on the frontend (generated from the API schema, or one constants module), and a test that those values equal the backend enum. No control-type or kind literal in any screen file. 3. PLAYWRIGHT_CHROMIUM_PATH: accepted, env-gated, comment says dev-only. The 12 green ran on a hand-assembled stack; CI's compose e2e is the authority. 4. Standard for steps 7 and 8: every screen's e2e asserts at least one formatted money value and one formatted quantity (UoM decimals) read off the page, the way 8,500 is asserted now." — all four delivered before the PR; see below. |
| P5 | 8 | Enquiry and reports UI | item enquiry, the Movement / Count / Transaction / Valuation screens, twelve screenshots under `docs/screenshots/p5-step-8` | `34abe7d` | Not a STOP gate — the phase prompt names steps 2 and 5 only. Recorded because the step-8 review set conditions that shaped step 9: rebase off the step-7 merge, state the valuation average column as rendered (**"Average as at"**), file the `toISOString` sweep as its own issue with a guard (#30), correct the prompt's tape row 9, and close every report with `git status --short` and `git log @{u}..` quoted. PR #29, both CI jobs green, merged as `387b3bd`. |
| P5 | 9 | Acceptance, sensitivity pass and the phase report | the acceptance chain through the screens (`frontend/e2e/inventory-acceptance.spec.ts`), the `inv:count_enter` split, the module-reversal rule, `/inventory/documents`, `docs/p5-final-report.md` | `26354f3` | Directed rather than approved: the review set step-9 scope (items 6–9 plus the three carried rules) and, on the reversal gap, "Build it, not the API-only path — a document that can be posted but not reversed from any screen is a product gap, not a test gap", with the server rule to live in the kernel behind a context flag, source links fixed both ways, count postings made uniform, and the chain to reverse through the new screen. All delivered; the back-fill half of the source-link instruction is recorded as impossible and why — see the final report. Pre-merge review added three checks: the AR/AP reversal gap (confirmed — two endpoints, no callers, named as the first work after P5), whether Docker caused the suite slowdown (measured: it did not — the host is 5% slower on the same subset), and the sweep issue (filed, #30). |
| P6 | 2 | Posting contract — partner documents with item lines, GRN and the match (**STOP**) | decision 2 in `app/subledger/documents.py`, the accrual guard and dimension rule, GRN create/post/reverse, the match on `grn_line_id`, and `assert_order_invariants` | PR #35 | **Approved (recorded after the fact)**, 2026-09-16. The owner's words were not captured at the time, so the cell says what it can rather than a reconstruction: the gate was cleared and the phase went on to build orders. |
| P6 | 5 | The acceptance tape (**STOP**) | the tape row by row in `backend/tests/order_entry/test_acceptance_tape.py`, the accrual and clearing proofs, and the four invariant suites after every row | `8f75e5e` / PR #38 | **Approved (recorded after the fact)**, 2026-09-16. As above — recorded, not quoted, and the phase went on to the UI. |
| P6 | 9 | Debt register, depth pass and phase close | the ten A-items cleared or carried with a reason, the tape through the screens (`frontend/e2e/p6-cycle-tape.spec.ts` + `backend/tests/order_entry/test_cycle_trial_balance.py`), Appendix C's Order Entry rows, `docs/p6-final-report.md` | this commit | **Awaiting the owner.** Opened at submission, not filled in on the author's behalf. The step-2 and step-5 gates above were missing when this row was written and the owner added them on 2026-09-16 — retroactively, and saying so, which is the honest form: an approval nobody wrote down at the time is not one the author may write down later. |
| P7 | 2 | Posting contract — the outbox in the posting transaction, item registration, sale and refund, receipts (**STOP**) | the hook in `post_document()` (`app/fiscal/sales.py`), the ten post-time refusals with a sensitivity test each, `app/fiscal/outbox.py` + `drainer.py` + `worker.py`, `assert_fiscal_invariants` (`backend/tests/fiscal/invariants.py`), the property machine with the sandbox switched between modes (`backend/tests/fiscal/test_property_fiscal.py`), and the per-line and per-document residue censuses in `docs/p7-step-2-report.md` | `2dc43a1` / PR #50 | "Step 2 approved. Invariant 1's skip was the hole it exists to find, and `activated_at` over `journal_entries.posted_at` is the right discriminator. The census-bias catch — round prices are exactly the ones with no residue — is the better piece of work. Approved with three conditions, all landed before merge: the document-level residue census including the FX path, the reversal refund receipt's route to the customer as a step-8 requirement, and the `totRcptNo` attribution corrected. — Owner, 2026-09-17, PR #50." The commit is the one `docs/p7-step-2-report.md` describes (`2dc43a1`); the three conditions landed on the branch after it and the approved head at merge was `2372c70`. |
| P7 | 5 | Enquiries, listings and the acceptance tape (**STOP**) | `app/fiscal/enquiries.py` (queue per device, row detail with the action log, receipts listing and search, the item registration enquiry), `app/fiscal/printing.py` (the print gate and the copy counter), eleven endpoints with their rule-14 lines, and the nineteen-row acceptance tape under both route profiles (`backend/tests/fiscal/test_acceptance_tape.py`, `backend/tests/fiscal/test_enquiries_api.py`) | `9cc94a1` / PR #57 | "Step 5 approved. Land it in this order: 1. Push claude/p7-step-4 as it stands (12 commits, head 1361e52). Open feat(fiscal): P7 step 4 — VAT return, filing, X/Z and FX revaluation, body = docs/p7-step-4-report.md plus the step-4 gap-status table from the step-5 report. Not a gate step, no approvals row. Owner arms auto-merge. 2. When it merges, rebase p7-step-5 onto main — that picks up PR #55 as well. Run the changed specs plus the guard tests locally (git diff --stat quoted); CI is the full run for the rebased head. The full-suite and deep numbers at 9cc94a1 stay in the report as the gate run, labelled with that hash. 3. In the step-5 PR, one docs commit corrects the prompt file's two literals — row 6 lastSaleInvcNo 6 (refunds share the FIS run) and row 5 drains at +0, +1, +6 (backoff waits) — with a pointer to the step-5 approvals row, the way P5 corrected its row 9. 4. Fill the placeholders (main baseline, git diff --stat, git status --short, git log @{u}.., the deep block), add the docs/approvals.md row, open feat(fiscal): P7 step 5 — enquiries, listings and the acceptance tape with the report as the body. Auto-merge armed. 5. Step 6 starts on green CI, fresh session, from pulled main." — Owner, 2026-09-19. All five instructions carried out; item 3's correction is the reason the prompt's rows 5 and 6 now read 6 rather than 5. **The hashes**: the gate run (1 360 passed, and the deep profile) was taken at `9cc94a1`, which the rebase onto `d54f684` rewrote — the same content is `1957479` afterwards, and the PR head is `6325b58`. `9cc94a1` is kept here and in the report because it is the tree the tape and the deep profile actually ran against; CI on the rebased head is the record for what merged. |
| P7 | 9 | Tests, CI, phase close (**STOP**) | the tape through the screens (`frontend/e2e/p7-cycle-tape.spec.ts`), revision `0026_p7_z_high_water` and `assert_fiscal_invariants` clause 12, the sensitivity pass over nineteen guards, the rule-14 register at one P7 line, `docs/rra/certification.md`, `docs/p7-final-report.md` and `docs/p7-step-9-report.md` | `4378ee5` | **Step 9 accepted**, 2026-09-21, with six conditions, all carried out before the PR was opened: (1) the full Playwright set green in **one pass** on a reset stack — `252 passed`, with the dev-server warm-up written into the report as a process rule, because a phase-close step runs its own suites and does not defer to CI; (2) issue #20's question answered outright — `fiscal/worker.py` orders nothing, and every queue ordering is `ORDER BY sequence_no` in `app/fiscal/outbox.py`; (3) every kickoff item confirmed with a file and line rather than a summary; (4) `unknown` given a **floor** in the queue-state census instead of only being printed, and proven sensitive; (5) the warehouse fix confirmed to resolve MAIN by code at all six sites, so the next spec to add a warehouse cannot move it; (6) `git status --short` and `git log @{u}..` quoted empty. — Owner, 2026-09-21. |
| P7 | — | **Decision 11 amended** — a Z owns receipts by high-water mark, not by a timestamp range | revision `0026_p7_z_high_water`, `app/fiscal/daily.py` (`Membership`, `receipts_of`, `membership_of`), `assert_fiscal_invariants` clause 12, `backend/tests/test_p7_z_high_water_backfill.py`, and the amendment written into `.github/prompts/phase-7-fiscalization.prompt.md` under decision 11 | `4378ee5` | **Amended, 2026-09-21.** Accepted with step 9. The decision as frozen cut a Z's population on `sdc_datetime` — RRA's clock — while the close was cut on `now()`, Vinea's; a receipt in flight across that skew belonged to no day at all, and nothing asserted otherwise. Step 8 found it (`docs/p7-step-8-report.md`, item 8) and left it as a backend item because its own rule was that the backend did not move. Membership by counter is the same answer decision 12 already gives late VAT entries. |
| P8 | 2 | Matching, reconciliation, the invariants, the machine (**STOP**) | `app/banking/matching.py` and `reconciliation.py`, the nine workspace endpoints with their rule-14 lines, `assert_bank_invariants` clauses 1-7 and 9 with a sensitivity test each (`backend/tests/banking/test_invariant_sensitivity.py`), `test_boundary.py`, the property machine and its three targeted refusals (`backend/tests/banking/test_property_banking.py`, `test_property_targeted_refusals.py`), and `docs/p8-step-2-report.md` | `c55635d` / PR #65 | "Step 2 approved 22 Sep 2026 on c55635d: the figures identity checked against tape rows 2, 8 and 9; clause 4 by assignment and high-water; census a gate with reach floors; decision 4's refusal name corrected to control_account_direct_posting." — Owner, 2026-09-22. Approved with two conditions, both landed in the same PR: the correction to decision 4 and tape row 18 in `.github/prompts/phase-8-banking.prompt.md` — the tape literal changes now, not at step 5 — and this row. `control_account_manual_posting` is a `ManualJournal`-only branch and a rule's drawer posts a `CashbookEntry`, so the refusal that fires is P4's `control_account_modules` registry, which is `VN007` in the database as well. The prompt itself was absent from `.github/prompts/` until this PR, where P1-P7's all were; it is added with the corrections applied. |
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

## Conditions carried out of P5 step 6

**1 — "Variable barcodes" was the wrong screen (plan deviation → `docs/p5-final-report.md`).**
Appendix C's "Variable barcodes" is the POS **scale-label** pattern: a prefix, then item-code
digits, then weight or price digits, decoded at the till. That is a P11 screen sitting with the
tills that read it. The step-6 prompt listed it among the maintenance screens to ship and the
build read it as "the barcode listing", so what landed was the plain per-item listing — a real
and needed screen, under a name that belongs to a different one. The appendix row keeps its
label and position and is now tagged `P11`; the built screen sits beside it as **"Barcodes"**
at `/maintenance/barcodes`. Recorded in `appendix-c-order.test.tsx` beside the assertion it
changes, and to be carried into the P5 final report at step 9 as a plan deviation on the
specification side, not the build.

**2 — one source for wire enum values.** `frontend/src/lib/api-enums.ts` is generated from the
Python enums by `backend/app/scripts/export_api_enums.py` (21 enums), with
`backend/tests/test_api_enums_export.py` as the drift gate — regenerate, compare, fail with the
command that fixes it, the same shape as `alembic check`. `frontend/src/lib/api-enums.test.ts`
keeps control-type, item-type, policy and kind literals out of every file under `src/app` and
`src/features`. Writing it surfaced **five pre-existing instances of the same defect** in P4
code (`control_type === "bank" | "cash" | "ar" | "ap"` hard-coded in the cashbook batch screen,
the AR/AP defaults screen and the subledger document screen); all now compare against
`ControlType`. The guard matches on *proximity to the field or the type*, not on the value
alone: a first cut that banned the values outright flagged twenty P4 files for `role="ap"`,
where `"ap"` is a `PartnerRole` and not a `ControlType`, and a guard that cries wolf is a guard
that gets switched off.

**3 — `PLAYWRIGHT_CHROMIUM_PATH`.** Env-gated, unset in CI, and its comment now says
DEVELOPMENT ONLY and names CI's `docker compose` e2e as the authority. The twelve green tests
reported at step 6 ran against a hand-assembled local stack (Postgres + uvicorn + `next dev`,
no Docker daemon in the sandbox) — a development signal, not evidence the suite passes.

**4 — the formatting standard, binding on steps 7 and 8.** Every screen's e2e asserts at least
one **formatted money** value and one **formatted quantity** read off the page as rendered, not
as the raw field. Both defects step 6 shipped were that shape: a price printed at
`NUMERIC(20,6)` scale instead of RWF's zero decimals, and a unit factor printed as
`1.0000000000`. Written into the header of `frontend/e2e/inventory-maintenance.spec.ts`, which
already meets it (`8,500` for money, `6` and `1` for factors).

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


