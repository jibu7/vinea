# P8 step 9 — tests, CI, phase close (STOP)

The STOP-gate report for the phase close. Branch `p8-step-9` from `main` at **`9eed27c`** (the
merge of PR #78; #76's KCB commits reached `main` through #77). The phase's own summary is
`docs/p8-final-report.md`. This file is the step's evidence.

## A. The tape through the screens

`frontend/e2e/p8-cycle-tape.spec.ts` has **17 tests**. It runs serially on a company it signs up
itself, so `BST-000001`, `BRC-000001`, `PYR-000001` and `FXR-000001` are hand-worked literals
that no other spec can consume.

- **Its own CI group, `p8-tape`.** `ci.yml` names it, the shard filter excludes it, and
  `ci-e2e-groups.test.ts` proves the partition (every spec in exactly one group). The brief said
  "like p6/p7's tapes". In fact those two ride the `rest` shards, and the group called `tape` is
  P4's `ar-ap-acceptance.spec.ts` alone. So `p8-tape` is new rather than a sibling. It is
  recorded in the final report's deviations.
- **The year is pinned.** `TAPE_YEAR = 2026`, because the three committed statements are dated
  2026. The fiscal year is created through `POST /gl/fiscal-years` if the signup's (wall-clock)
  year is another one, and August to November are opened. This is the step-5 backend tape's rule,
  carried as its report asked.
- **The walk**, each step on its screen: register the bank accounts (1120's details, 1121
  created in USD through *New bank account*, the generic format saved, *Test with a file* on the
  USD sample) → the two opening entries on the cashbook batch, read off the Cashbooks summary →
  paper-mode `BRC-000001`, with Lock refused at FRw 1,000,000 before the tick and locked after it
  → `RCT-1`/`RCT-2` on the receipt screen → `PYR-000001` previewed (S2's FRw 2,000, S3's *Bank
  details missing*, FRw 384,000) and posted, the instruction file downloaded and read (a header
  and three rows, S3's bank and account-number fields empty), S2's remittance advice through
  `pdftotext` (`SIN-000002`, 98,000, 2,000) → `RCT-3`/`RCT-4` in USD (RCT-4 at the keyed 1,300 is
  260,000 on 1120) and `PMT-000004` by cheque → the September CSV previewed (6 lines, opening
  FRw 1,000,000, closing FRw 1,090,500) and imported ("6 new, 0 skipped, 4 matched"), with each
  match's rule read off the statement's detail → `BRC-000002` defaulted to 1,090,500. On the
  strip: ledger 983,000, outstanding −70,000, 2 unmatched, difference 37,500. Lock refused for the
  two lines; line 6 against `PMT-000004` refused as out by 110,000; the fee posted from its line
  as prefilled by the rule (`CB-000003`); the deposit posted as C2's receipt (`RCT-000005`);
  1,090,000 keyed and Lock refused on "The difference is FRw -500"; locked at zero; Unmatch
  refused before the button → the USD statement, the fee posted in USD (`CB-000004`, 6,600 base)
  and `BRC-000003` at $ 495.00 → the revaluation preview at 30 Sep (1121's line: $ 495.00,
  +14,850; `SIN-000004`: −3,000; 2 lines), posted as `FXR-000001`, and a second bank run refused
  `fx_revaluation_exists` → `PMT-000005` dated 26 Sep, and the report on `BRC-000002`: stored
  1,020,500 beside live 1,000,500, the outstanding `PMT-000004`, and *Posted after lock: 1* for
  `PMT-000005` → the overlap import ("2 new, 1 skipped, 2 matched") → `BRC-000004` at 31 Oct at
  zero → `PYR-000002` with the discount declined, its `PMT-000006` refusing its own Reverse, and
  the run reversed (SIN-5 open FRw 30,000 again; "Runs: 2 · standing: 1") → Reopen refused on
  `BRC-000002` ("…and that is BRC-000004") and allowed on `BRC-000004`. The last-reconciled cache
  falls back to FRw 1,090,500 → Cashbooks for 1120 in September: opening 1,000,000, receipts
  477,000, payments 476,500, closing **1,000,500**, which is the same string the Trial balance
  screen shows for 1120 at 30 Sep.
- Every screen asserts one money figure and one quantity, and every label is `{ exact: true }`.
  `make db-reset` runs before the file, never inside it.

**The real exports**, precondition (d), in the same file on four accounts of their own. The
mapping comes from the committed `<bank>.format.json`, set through `PATCH /banking/accounts/{id}`,
and each file goes through the Import dialog:

| file | preview | result | statement detail |
|---|---|---|---|
| `bpr-2025-06.csv` | 45 lines · 45 new · 0 already held · closing FRw 4,274,862 | "45 new, 0 skipped" | — |
| `bpr-2025-05.csv` (same account) | opening FRw 1,110,776 | "32 new, 0 skipped" | 32 lines · closing FRw 2,408,456 |
| `kcb-2023-12.csv` | read cleanly · 281 lines · 281 new · "Skipped for no amount: 1" · opening FRw 0 · closing FRw 4,867 | "281 new, 0 skipped" + "Skipped for no amount: 1" | 281 lines · closing FRw 4,867 |
| `bk-2019-10.csv` | 249 lines · 249 new; opening 1,600 and closing 2,659 **keyed** (no balance column) | "249 new, 0 skipped" | 249 lines · closing FRw 2,659 |

The screen writes the brief's "281 new, 0 skipped, 1 skipped for no amount" as two phrases, and
the tape reads both. RWF has no minor unit, so the brief's "derived opening 0.00" renders FRw 0.

**What building it corrected, all in the spec and none in the product.** Five slips of mine
surfaced in the first runs, each read off a failure rather than guessed:
- the run posts in supplier-name order, so the suppliers are named to sort S1, S2, S3;
- RCT-2 and RCT-4 carry no reference, as in the backend tape, because a reference let the
  reference rule claim RCT-4 before amount-and-date could;
- rows 5 and 6 come after row 4, so the cheque is `PMT-000004`;
- an open reconciliation's statement balance is an input, not a figure;
- the statement listing is in date order, not import order.

## B. The sensitivity pass

One guard at a time. Each break is an exact-string edit asserted to match. The targeted tests ran
in the container (`-n 4`, `lock_timeout` 60 s). The file was restored with `git checkout`, its
SHA-256 compared with the committed tree, and `git status --short` checked empty. Every row
restored byte-for-byte. Twenty rows ran at **`bf444b3`**. 6a and 6b ran again at **`82aacc4`**,
which adds only the two tests below (`git diff --stat bf444b3 82aacc4` is one test file).

| # | guard | what was disabled | the test that failed | restored |
|---|---|---|---|---|
| 1a | currency rule — engine | `_check_bank_account_currency` returns before its check (`app/kernel/posting.py`) | `test_a_foreign_currency_account_refuses_a_base_currency_line`<br>`test_a_line_in_the_wrong_currency_is_refused_by_the_api`<br>`test_the_engine_still_refuses_when_the_trigger_is_dropped` — 3 failed, 50 passed, 6 warnings | yes, `eeae9e3dda56` at `bf444b3` |
| 1b | currency rule — trigger `VN012` | the trigger function returns `NEW` for every line (`alembic/versions/0027_p8_banking.py`) | `test_the_trigger_still_refuses_when_the_engine_check_is_disabled` — 1 failed, 37 passed, 5 warnings | yes, `bb5a2d7ee81e` at `bf444b3` |
| 2 | statement-line immutability `VN013` | the trigger function returns before both refusals (`alembic/versions/0027_p8_banking.py`) | `test_a_stored_line_cannot_be_edited`<br>`test_a_stored_line_cannot_be_deleted` — 2 failed, 30 passed, 5 warnings | yes, `bb5a2d7ee81e` at `bf444b3` |
| 3 | file-hash refusal | `statement_already_imported` never raised (`if duplicate is not None` → `if False`) (`app/banking/statements.py`) | `test_the_same_file_twice_is_refused`<br>`test_the_upload_previews_then_imports_then_refuses_the_same_file` — 2 failed, 55 passed, 7 warnings | yes, `341fbc433b88` at `bf444b3` |
| 4 | fingerprint skip | `_is_held` answers False for a held fingerprint (`app/banking/statements.py`) | `test_the_preview_counts_what_the_account_already_holds`<br>`test_an_overlapping_export_is_two_new_and_one_skipped` — 2 failed, 18 passed, 5 warnings | yes, `341fbc433b88` at `bf444b3` |
| 5 | match balance rule | `match_unbalanced` never raised (`if difference != ZERO` → `if False`) (`app/banking/matching.py`) | `test_a_refused_match_leaves_no_members`<br>`test_a_match_that_does_not_balance_is_refused_with_the_difference` — 2 failed, 25 passed, 5 warnings | yes, `77757c70d13a` at `bf444b3` |
| 6a | member uniqueness — `bank_match_statement_lines` | the `statement_line_id` unique constraint dropped from `0027` (`alembic/versions/0027_p8_banking.py`) | `test_the_database_refuses_a_statement_line_in_a_second_match` — 1 failed, 50 passed, 5 warnings | yes, `bb5a2d7ee81e` at `82aacc4` |
| 6b | member uniqueness — `bank_match_journal_lines` | the `journal_line_id` unique constraint dropped from `0027` (`alembic/versions/0027_p8_banking.py`) | `test_the_database_refuses_a_journal_line_in_a_second_match` — 1 failed, 50 passed, 5 warnings | yes, `bb5a2d7ee81e` at `82aacc4` |
| 7a | lock at zero — unmatched lines | `statement_lines_unmatched` never raised (`app/banking/reconciliation.py`) | `test_a_lock_is_refused_while_a_statement_line_is_unmatched` — 1 failed, 23 passed, 5 warnings | yes, `785fb0f5d3f4` at `bf444b3` |
| 7b | lock at zero — difference | `reconciliation_difference` never raised (`app/banking/reconciliation.py`) | `test_a_lock_is_refused_at_a_non_zero_difference` — 1 failed, 23 passed, 5 warnings | yes, `785fb0f5d3f4` at `bf444b3` |
| 8 | high-water late-line rule | both `id > high_water_line_id` tests (the workspace flag and *Posted after lock*) → `id < 0` (`app/banking/reconciliation.py`) | `test_a_line_posted_after_the_lock_is_late_and_moves_nothing`<br>`test_an_account_with_no_lines_locks_with_a_high_water_mark_of_zero`<br>`test_the_report_shows_the_stored_figures_beside_the_live_ones` — 3 failed, 38 passed, 5 warnings | yes, `785fb0f5d3f4` at `bf444b3` |
| 9 | reopen only the latest | `reconciliation_not_latest` never raised (`app/banking/reconciliation.py`) | `test_only_the_latest_locked_reconciliation_may_be_reopened` — 1 failed, 23 passed, 5 warnings | yes, `785fb0f5d3f4` at `bf444b3` |
| 10 | payment-run member refusal | `_refuse_a_payment_run_member` not called from `reverse_document` (`app/subledger/documents.py`) | `test_a_member_settlement_cannot_be_reversed_on_its_own` — 1 failed, 26 passed, 5 warnings | yes, `572f569a6701` at `bf444b3` |
| 11 | fallible leg first on run reversal | `reverse_document` for every settlement moved before `unallocate()` (`app/banking/payment_runs.py`) | `test_reversing_the_run_restores_every_open_item`<br>`test_reversing_the_run_releases_its_match`<br>`test_a_member_of_a_reversed_run_is_an_ordinary_document_again`<br>`test_reversing_a_run_twice_is_refused` — 4 failed, 23 passed, 5 warnings | yes, `946f327444be` at `bf444b3` |
| 12 | scope-overlap refusal | `fx_revaluation_exists` never raised (`if covered & scopes_of(run.role)` → `if False`) (`app/subledger/revaluation.py`) | `test_a_second_run_over_the_same_date_is_refused`<br>`test_a_second_bank_run_at_the_same_date_is_refused`<br>`test_a_bank_run_after_a_subledger_run_at_the_same_date_is_allowed` — 3 failed, 26 passed, 5 warnings | yes, `6a7ee8789923` at `bf444b3` |
| 13 | the bank line never touches the bank account | a bank group's revaluation side posted to the bank's own GL account instead of `1130` (`app/subledger/revaluation.py`) | `test_the_balance_sheet_reads_the_pair`<br>`test_the_run_posts_the_gain_to_1130_and_the_loss_to_2190`<br>`test_the_mirror_takes_the_adjustment_back_out_the_next_day`<br>`test_the_revaluation_never_writes_to_the_bank_account`<br>`test_a_second_bank_run_at_the_same_date_is_refused`<br>`test_a_bank_run_after_a_subledger_run_at_the_same_date_is_allowed`<br>`test_two_bank_accounts_in_one_currency_post_two_groups`<br>`test_the_stored_line_names_the_bank_account_and_no_document`<br>`test_reversing_a_bank_run_takes_both_entries_back_out` — 9 failed, 5 passed, 5 warnings | yes, `6a7ee8789923` at `bf444b3` |
| 14 | the Cashbooks tie | `closing_base` drops the opening base (`app/banking/reports.py`) | `test_the_base_closing_equals_the_trial_balance`<br>`test_a_range_with_no_lines_still_carries_its_opening` — 2 failed, 15 passed, 5 warnings | yes, `6aa0d302f62a` at `bf444b3` |
| 15a | invariant clause 4 — too narrow | membership reverted to this reconciliation's own matches (the `earlier` arm removed) (`app/banking/reconciliation.py`) | `test_clause_4_holds_when_a_later_lock_takes_a_line_dated_inside_an_earlier_one`<br>`test_a_line_reconciled_in_an_earlier_lock_is_not_outstanding_in_a_later_one`<br>`test_the_acceptance_tape` — 3 failed, 47 passed, 5 warnings | yes, `785fb0f5d3f4` at `bf444b3` |
| 15b | invariant clause 4 — too wide | the `earlier` arm's date bound dropped, so a *later* lock's matches count (`app/banking/reconciliation.py`) | `test_clause_4_holds_when_a_later_lock_takes_a_line_dated_inside_an_earlier_one`<br>`test_the_acceptance_tape` — 2 failed, 48 passed, 5 warnings | yes, `785fb0f5d3f4` at `bf444b3` |
| 16a | format option `empty_description` | the `reference` fallback never taken (`app/banking/formats.py`) | `test_empty_description_reference_still_refuses_a_row_with_neither`<br>`test_each_real_export_parses_through_its_mapping_with_no_errors[bpr-2025-05.csv]`<br>`test_each_real_export_parses_through_its_mapping_with_no_errors[bpr-2025-06.csv]`<br>`test_each_real_export_with_a_balance_column_ties_opening_to_closing[bpr-2025-06.csv]`<br>`test_each_real_export_parses_through_its_mapping_with_no_errors[bpr-2026-07.csv]`<br>`test_each_real_export_with_a_balance_column_ties_opening_to_closing[bpr-2025-05.csv]`<br>`test_each_real_export_with_a_balance_column_ties_opening_to_closing[bpr-2026-07.csv]` — 7 failed, 54 passed, 5 warnings | yes, `1346cf86fe72` at `bf444b3` |
| 16b | format option `zero_is_empty` | the filler zero kept as filled (`app/banking/formats.py`) | `test_zero_is_empty_reads_a_filler_zero_as_empty_but_not_both`<br>`test_each_real_export_parses_through_its_mapping_with_no_errors[bpr-2022-09.csv]`<br>`test_each_real_export_with_a_balance_column_ties_opening_to_closing[bpr-2022-09.csv]`<br>`test_four_identical_sme_fees_carry_occurrence_indexes_0_to_3`<br>`test_empty_amount_skip_reads_filler_zeros_in_both_columns_as_empty` — 5 failed, 56 passed, 5 warnings | yes, `1346cf86fe72` at `bf444b3` |
| 16c | format option `empty_amount` | the no-amount row never skipped (`app/banking/formats.py`) | `test_each_real_export_parses_through_its_mapping_with_no_errors[kcb-2023-12.csv]`<br>`test_empty_amount_skip_counts_the_row_and_refuses_what_it_cannot_read`<br>`test_empty_amount_skip_reads_a_dash_in_both_columns_as_empty[-]`<br>`test_empty_amount_skip_reads_a_dash_in_both_columns_as_empty[\u2014]`<br>`test_empty_amount_skip_reads_a_dash_in_both_columns_as_empty[\u2013]`<br>`test_empty_amount_skip_reads_filler_zeros_in_both_columns_as_empty`<br>`test_empty_amount_skip_applies_to_an_empty_signed_column`<br>`test_the_kcb_export_skips_its_brought_forward_row_and_derives_the_same_opening` — 8 failed, 53 passed, 5 warnings | yes, `1346cf86fe72` at `bf444b3` |

**The finding: member uniqueness had no test.** At `bf444b3`, dropping either constraint failed
nothing. With `tests/banking` whole (302 tests) plus `test_schema_invariants.py` and
`test_alembic_guards.py`, the result was **302 passed**. `_refuse_already_matched` answers first,
and every test goes through `create_match`, so the database half of "a line is in at most one
match" was untested. The two tests in `82aacc4` write a second member row directly and expect
Postgres to refuse it by the constraint's name. They were committed before the constraint was
broken again, and each fails alone when its constraint is dropped.

**Two harness incidents, neither the product's:**
- **1a (the engine check) first hung.** With the engine refusal off, a test's open transaction got
  a line into `journal_lines`, and the same test's superuser connection then waited on that
  lock for twenty minutes. I terminated the waiting session and the run reported its three
  failures. Every later run carried `PGOPTIONS='-c lock_timeout=60000'`.
- **6a/6b's first run errored `fixture 'banking' not found`** on 21 tests. That reproduces on the
  clean tree: xdist loses the `tests/banking` conftest when a root-level test file is listed
  *between* two banking files. Reordered, and the argument order is not repeated.

**The three format options, off, give the README's error counts exactly** (`/tmp/sens/counts.py`
parses each committed export under its committed mapping):

| option off | file | errors | clean |
|---|---|---:|---:|
| `empty_description` | `bpr-2025-05.csv` / `-06` / `2026-07` | **16 / 22 / 1** | 0 / 0 / 0 |
| `zero_is_empty` | `bpr-2022-09.csv` | **21** (0 lines read) | 0 |
| `empty_amount` | `kcb-2023-12.csv` | **1** (0 skipped) | 0 (1 skipped) |

## C. The register

**No P8 line.** `NO_UI` in `backend/tests/test_api_has_a_caller.py` carries five entries, none
changed by this phase's close:

| entry | kind | phase |
|---|---|---|
| `POST /api/v1/subledger/jobs/sweep` | by design — the retention reaper | P4 |
| `POST /api/v1/fiscal/outbox/drain` | by design — the EBM queue's scheduler hook | P7 |
| `POST /api/v1/operator/tenants/{company_id}/activate` | GAP (SaaS admin, C.2) | P1's operator console |
| `POST /api/v1/operator/tenants/{company_id}/suspend` | GAP (SaaS admin, C.2) | P1's operator console |
| `POST /api/v1/operator/tenants/{company_id}/impersonate` | GAP (SaaS admin, C.2) | P1's operator console |

The P8 blocks in its comments record the lines steps 6, 7a and 7b cleared: four, then twelve,
then three, and nothing left.

## D. Docs

- `docs/p8-final-report.md` in P7's form.
- The Master Plan's **P8 as built**, after P8's DoD.
- README: **Phase 8 complete**.
- `docs/approvals.md`: the **step-9 row** (awaiting the owner), and a **step-5 row**. The prompt
  cites "the P8 step 5 row" twice, and `main` had none. It records what is known and leaves the
  owner's words to the owner.
- Appendix C needed nothing. C.1.13–15 were written at steps 6–8, and the owner's *Cashbooks* and
  *Bank reconciliation* rows went live at step 8. `nav-tree.ts` carries no P8 tag.
- The clause-4 docstring in `tests/banking/invariants.py` still described the narrow membership
  step 5 corrected, and now says what the code does.

## E. Screenshots

`docs/screenshots/p8-step-9/`: five states, light and dark, taken **by the tape** when
`P8_CAPTURE_DIR` is set (CI never sets it). Its README says what each shows. The first cut had
success toasts covering rows. The four whose state is in the URL are now taken after a reload.

## F. Suites, against the final code

Run at **`c039ce7`**. The only commit after it is this report, so the code under test is the
final code. Local stack: `COMPOSE_FILE=docker-compose.yml:docker-compose.e2e.yml`, `make db-reset`,
`ebm-sandbox` and `worker` up, and 22 routes warmed on the Next dev server (`/` and `/login`
included) before the first spec.

| suite | main (`9eed27c`) | branch | result |
|---|---:|---:|---|
| `make be-lint` | — | — | `All checks passed!` |
| `make be-test` (`-n 4`, last and alone) | 1,679 collected | **1,681** collected | **1681 passed**, 8 warnings, 25:26 |
| deep profile (`HYPOTHESIS_PROFILE=deep`, `-m slow`, 300 examples) | — | 44 selected | **44 passed**, 42:08; census floors enforced |
| `npm run typecheck` | — | — | exit 0 |
| `npm run lint` | — | — | exit 0, 5 warnings (pre-existing `react-hooks/exhaustive-deps`) |
| `npm run test` (vitest) | 439 | 439 | **439 passed**, 16 files |
| Playwright `--list` | 296 | **313** | see groups below |

**Backend, per file.** `--collect-only` on both sides differs in one file only:
`tests/banking/test_matching.py`, 27 → 29 (the two uniqueness tests). `main` was collected from a
worktree mounted into a throwaway container, so the running stack was not touched.

**Playwright, per file.** `git diff --stat main -- frontend/e2e` is the new
`p8-cycle-tape.spec.ts` alone (17 tests). So `main`'s list is the branch's list with that file
excluded: 296 tests in 35 files, against 313 in 36. The worktree could not list directly: a
symlinked `node_modules` loads a second `@playwright/test`, which finds nothing.

**The full set in CI's six groups**, one after another on the one reset stack:

| group | result |
|---|---|
| `a11y` (the five axe sweeps, `--workers=4`) | first pass **105 passed, 1 failed**; re-run **106 passed** |
| `tape` (`ar-ap-acceptance`) | 6 passed |
| `p8-tape` | 17 passed |
| `rest-1` / `rest-2` / `rest-3` | 65 / 60 / 59 passed |
| extras (`e2e:regression`, `e2e:print-preview`) | both exit 0 |

That is 106 + 6 + 17 + 184 = 313, the whole list. **The one failure** was axe's `document-title`
on `/gl/reports/chart-of-accounts`, dark pass only; the light pass of the same screen was clean
seconds earlier. That is a P3 screen this branch does not touch, and a `<title>`-less document
fits `next dev` compiling a route mid-sweep under four workers (that route was not in the
warm-up). The five sweeps re-run as a group: 106 passed. It is recorded here rather than dropped.

**The deep census, quoted** (the P8 lines):

```
[property] refusals provoked: {'bank_account_currency_mismatch': 517, 'document_not_open': 13, 'fx_revaluation_exists': 43, 'fx_revaluation_reversed': 26, 'match_unbalanced': 21, 'payment_exceeds_open': 57, 'payment_run_before_invoice': 8, 'reconciliation_date_order': 4, 'reconciliation_difference': 14, 'reconciliation_open_exists': 105, 'statement_already_imported': 44, 'statement_lines_unmatched': 7}
[property] reach: {'auto-match: matched something': 54, 'auto-match: run': 647, 'currency rule: attempted': 517, 'lock: attempted': 43, 'lock: attempted at a wrong balance': 17, 'lock: succeeded': 22, 'match: attempted manually': 80, 'match: made manually': 59, 'payment run: attempted': 135, 'payment run: posted': 57, 'posted from a statement line': 24, 'reconciliation: opened': 427, 'reopen: succeeded': 4, 'revaluation: attempted': 504, 'revaluation: posted': 461, 'revaluation: reversed': 95, 'settlement: posted': 498, 'statement: imported from a file': 258, 'statement: keyed': 195, 'statement: perturbed by dropping a line': 29, 'statement: perturbed by shifting a value date': 73, 'statement: perturbed with a line the ledger lacks': 79, 'supplier invoice: posted': 563, 'tick: made': 302, 'unmatch: succeeded': 69, 'usd line on the base-currency account': 446}
```

`run reversal: succeeded` is **absent** from the reach line: the machine did not reverse a run in
this pass. That is the case step 5 withdrew its floor for. `reconciliation_locked` is not a key
in the refusals line either. Both are covered by construction in
`tests/banking/test_property_targeted_refusals.py` (step 2 moved `reconciliation_locked` there),
which passed as part of the 1681.

**`-n 4`, not 8.** At `-n 8` the per-worker migration runs together exhaust Postgres'
`max_locks_per_transaction` in the dev container ("out of shared memory"). The Makefile's
`PYTEST_WORKERS ?= 4` is that cap.

## G. The tree

```
$ git diff --stat main..HEAD -- . ':!docs/screenshots'     # at c039ce7, before this report
 .github/workflows/ci.yml               |   14 +-
 README.md                              |    2 +-
 backend/tests/banking/invariants.py    |   10 +-
 backend/tests/banking/test_matching.py |   77 +-
 docs/Vinea_ERP_Master_Plan_v5.md       |   66 ++
 docs/approvals.md                      |    2 +
 docs/p8-final-report.md                |  219 ++++++
 frontend/e2e/p8-cycle-tape.spec.ts     | 1242 ++++++++++++++++++++++++++++++++
 frontend/src/lib/ci-e2e-groups.test.ts |    8 +-
 9 files changed, 1626 insertions(+), 14 deletions(-)
```

plus `docs/screenshots/p8-step-9/` (ten PNGs and a README) and this report. **No product code
changed at step 9**: the diff is a spec, two backend tests, a docstring, CI and docs.

This report's own commit is the branch head. `git status --short` and `git log @{u}..` are
quoted empty after it is pushed, in the gate message, because a file cannot quote the state after
its own commit.

**STOP.** No PR until the owner approves.
