# P8 step 1 — masters, schema, seed, formats, the parser

The schema the rest of Phase 8 keys against, the bank-account master over the accounts P2
already flagged, the one-sided currency rule enforced twice, and a CSV parser with the three
sample statements the acceptance tape will import. **Nothing here posts.** No journal line is
written by any module under `app/banking/`, no posting path changes shape, and the only edit
to `app/kernel/posting.py` is a refusal.

Not a gate step — the STOP gates are after steps 2 and 5 — but the report is committed anyway,
in the P7 form, because the preconditions this phase opens on have answers that belong in the
repository rather than in a conversation.

Written at `77c8212`, against `main` at `d5a9255`. Every figure below was read off that tree.

## Preconditions

**(a) P7 is closed on `main`.** PR #62 is merged; `main` is at `d5a9255`, README reads
"**Status:** Phase 7 complete — **code-complete, certification pending**". The owner *did*
tag the close: `p7-close` is the repository's only tag, and it is on the P7 head. The P7
prompt said a tag was not a blocker because the repository had never been tagged; it has been
now.

**(b) The four decisions are confirmed.** The phase prompt this step was built from locks
decisions **2** (the one-sided currency rule), **5** (lock at zero, reopen only the latest),
**7** (one `PMT-` per supplier, reconciled to the bank's single line) and **8** (bank
revaluation inside the P7 run) under "Decisions locked for this phase — implement exactly",
and the owner handed it over as the instruction for this step. Decision 2 is implemented here
exactly as written and is the only one of the four this step touches; 5, 7 and 8 land at steps
2, 3 and 4 and are unchanged from the prompt's wording.

**(c) Issue #54 has had no PR of its own.** It is still open — "`ix_audit_log_company_at` does
not discriminate the entity, so a history read scans the tenant". PR #55 (`fix(audit): history
endpoints break ties on id, not on the plan`) is closed and addressed a different defect in
the same area: the ordering, not the index. So the first item of the post-P7 queue is still
owed, and per the prompt it belongs in a gap **between** steps rather than inside one — it is
not in this branch.

**(d) No real bank statement export is held.** `docs/banking/samples/` does not exist and no
anonymised CSV from BK, I&M, Equity, Access, Ecobank or BPR has been supplied. The three
generic samples are committed and steps 1–4 are written to them, exactly as the prompt
provides for. This is not a blocker now; if it is still outstanding at step 9 the final report
names it a plan deviation.

**The nightly.** Run #13 of `nightly-property.yml` on `main` at `7d8521b` (2026-09-21,
02:17 UTC schedule plus the post-merge run) is **green**: `deep property run` succeeded, `open
an issue on failure` skipped, and the standing `nightly-property` issue is unopened. The last
five nightlies are all green. The census and reach lines themselves live in that run's job
summary and its `deep-property-log` artifact, both of which need an authenticated GitHub
session this agent's shell does not have; the figures as they stood at the P7 close are quoted
verbatim in `docs/p7-step-9-report.md` §"The deep property profile" — every P6 refusal floor
reached (`exceeds_available` 66 … `receipt_exceeds_order` 12), 23 reach counters all non-zero,
and P7's queue-state census reaching `unknown` seven times, which step 9 turned from a printed
number into `REQUIRED_STATES`/`STATE_FLOOR`. **The phase inherits no red nightly.** P8's own
counters join that summary at step 2, not at step 9 — P7's rule, applied from the start.

## What landed

### Migration `0027_p8_banking`

Ten tables, all tenant-scoped with `ENABLE` + `FORCE ROW LEVEL SECURITY` and a `FOR ALL`
`tenant_isolation` policy carrying `WITH CHECK`:

| table | what it is |
|---|---|
| `bank_accounts` | the master over a flagged GL account — currency, bank details, the column mapping, the last-reconciled cache |
| `bank_rules` | a prefill for a recurring statement line; never posts |
| `bank_statements` | one import, with the mapping it was read with snapshotted onto it |
| `bank_statement_lines` | the bank's record, immutable by trigger |
| `bank_reconciliations` | the dated proof and, once locked, its snapshot |
| `bank_matches` | the assertion that a set of statement lines and a set of ledger lines are one event |
| `bank_match_statement_lines` | members, **unique on the line** |
| `bank_match_journal_lines` | members, **unique on the line** |
| `payment_runs` | a batch supplier payment; its postings are ordinary P4 documents |
| `payment_run_lines` | one invoice paid, with the `PMT-` and `ALC-` it produced |

Plus: `partners.bank_name` / `.bank_account_number` / `.bank_account_holder`; three
`gl_settings` keys (`bank_revaluation_account_id`, `bank_charges_account_id`,
`bank_interest_account_id`) with their composite FKs; `fx_revaluation_lines.document_id` and
`.booking_rate` widened to nullable, `bank_account_id` added with a FK and the CHECK that
exactly one of the two id columns is set; and `fx_revaluation_role` **rebuilt** with `bank`
and `all` (rename / recreate / retype — never `ALTER TYPE … ADD VALUE`, P6 step 1's finding).

`make migrate-check` is green end to end: `alembic upgrade head` from zero, `alembic check`
clean, `alembic downgrade base` all the way out.

### The two database guards

| SQLSTATE | trigger | what it refuses |
|---|---|---|
| `VN012` | `trg_journal_lines_needs_bank_currency` | a line whose currency is not a **foreign-currency** bank account's own. One-sided: a base-currency account may carry a USD receipt. |
| `VN013` | `trg_bank_statement_lines_immutable` | any UPDATE or DELETE of a statement line, except the single `is_void` column. |

Both are registered in `app/kernel/errors.py` so they surface in the ADR-11 envelope rather
than as 500s: `bank_account_currency_mismatch` and `statement_line_immutable`.

The `is_void` exemption is the one design decision in the trigger, and it is there for a
concrete reason. Decision 3 requires that a voided statement's line fingerprints block
nothing, so the fingerprint uniqueness scope has to be "the lines that still count". A partial
unique index cannot reach `bank_statements.status`, and the lines cannot be deleted — so the
status is denormalised onto the line and the trigger exempts exactly that column, naming why.
It is P7's `copy_count` exemption on a fiscal receipt, one domain along: countable without
being editable.

### Numbering and permissions

`BST` / `BRC` / `PYR` enter `DocType`, `DEFAULT_PREFIXES` and `SEQUENCE_CLAIMANTS`. Each
registers **its own table** as its only claimant, because none of the three numbers a posting:
a statement records what the bank said, a reconciliation records a proof, and a payment run's
postings are ordinary `PMT-` documents with numbers of their own. A voided statement, a
reopened reconciliation and a reversed run all keep their numbers, the way a cancelled order
keeps its `SO-`.

Six permissions — `bank:setup_manage`, `bank:statement_import`, `bank:reconcile`,
`bank:reconcile_lock`, `bank:payment_run_post`, `bank:reports_view` — with the role back-fill
decision 11 names: Administrator all six (as part of the whole union, so a tenant several
phases old catches up), Accountant all but `bank:setup_manage`, Clerk `bank:reports_view`.

### `app/banking/`

`accounts.py` (the master, the `ensure_row` hook, the currency rule's data, the reconciled
amount, and the rules' CRUD), `formats.py` (the mapping model, the `generic` preset, the
parser, the fingerprint) and `statements.py` (preview, import, dedup, void). The API is
`app/api/v1/banking.py` under `/api/v1/banking/…`.

**Nothing under `app/banking/` imports `PostingEngine` internals or writes to
`journal_entries` / `journal_lines`**, and no new value reaches `journal_entries.module`. The
`tests/banking/test_boundary.py` that *proves* this by reading the import graph arrives at
step 2, as the build order schedules it; at this step the claim is true by inspection and by
the fact that neither module imports `app.kernel.posting` at all.

### The hook, and where it is called from

`ensure_row(db, account)` is called by `POST /gl/accounts` and by `seed_rwanda.seed_company`,
in the same transaction as the account. It is **not** called from `app/kernel/accounts.py`:
the kernel may not import the banking package (P5's rule about `events.py` importing upward).
A path that forgets is caught by `assert_bank_invariants` clause 6 at step 2, and
`unregistered_control_accounts` is the listing that shows it to a person.

It is deliberately safe to call unconditionally — it returns `None` on anything that is not a
`bank` / `cash` control account — so the call site is one line and not a branch somebody
forgets to extend.

### The samples

`backend/tests/banking/samples/generic-bk-rwf-sep.csv`, `generic-bk-usd-sep.csv` and
`generic-bk-rwf-overlap-oct.csv`, in the `generic` layout, holding exactly the rows step 5's
tape reads. The step-5 literals are already asserted off these files here rather than hoped
for: September parses to six lines opening at **1 000 000** and closing at **1 090 500**, and
October is **2 new, 1 skipped** against it.

## Decisions worth review

1. **Column references are header names, falling back to 0-based positions.** A column in a
   mapping is matched against the header (case-insensitively, trimmed) and, failing that,
   parsed as an index. Names rather than positions because a header name survives a bank
   adding a column in the middle, which is the change these files actually undergo; positions
   as a fallback because a headerless export exists and would otherwise need a synthetic
   header invented for it. `header_rows` is how many rows precede the data and the **last** of
   them is the header.

2. **`bank_rules` CRUD lives in `accounts.py`, not `matching.py`.** Decision 1 lists the rules
   under `matching.py`. A rule is two things: a *master* maintained beside the account it
   belongs to under `bank:setup_manage`, and an *input to matching*. The master half is here
   so the Bank accounts screen can edit rules without importing the matcher; reading them to
   prefill the drawer is matching's and arrives with it at step 2. Nothing about the storage
   or the refusals changes.

3. **`bank_statement_lines.is_void`, and the one trigger exemption.** Set out above. The
   alternative — deleting a voided statement's lines — was rejected because they are the
   bank's record of something that was genuinely read off a file, and because the trigger
   would then need a DELETE hole that nothing else could be stopped from using.

4. **The import is multipart, and `python-multipart` is a new dependency.** `file_sha256` is
   the refusal that catches the same export twice, so it must be over the bytes the bank
   produced; the preview and the import have to agree about what those bytes are, and a
   base64 field between them is exactly where a BOM or a line ending is lost. The two keyed
   balances arrive as form **strings** and are parsed as `Decimal`, never through `float`
   (ADR-06), because a multipart form has no types.

5. **A row with both a debit and a credit is refused, not netted.** A bank writes one or the
   other; a row with both is a mapping pointed at the wrong pair of columns, and netting it
   would import a plausible wrong number. Relatedly, an explicit `0` in a column is treated as
   *filled* and refused as a zero amount naming the row — not as "no debit and no credit",
   which would send the reader to the mapping instead of to the row.

6. **A `CR`/`DR` marker is dropped in a debit/credit pair and refused in a signed column.**
   Some Rwandan exports sign a single column with a trailing marker rather than a minus. In
   `debit_credit` mode the column the value sits in already says which way the money went, so
   the marker is redundant and is stripped. In `signed` mode the marker **is** the sign, and
   stripping it would turn a withdrawal into a deposit of the same size — a plausible wrong
   number, which is the one outcome worse than a refusal. The mapping cannot express that
   layout today and says so with a row number; a bank that needs it gets a `sign_marker` mode,
   not a guess here.

7. **The back-fill registers every existing bank/cash account in the base currency, and
   guesses nothing.** A tenant whose bank account is really held in USD says so afterwards,
   which it can do for as long as that GL account has no journal line. Had the migration
   inferred USD from an existing foreign-currency line, `VN012` would then forbid every RWF
   line on that account and the tenant's own cashbook would stop working. `tests/test_p8_backfill.py`
   provisions exactly that case — a USD receipt into the RWF bank account — and asserts it
   survives.

8. **The three settings keys are exposed on `GET`/`PUT /gl/settings` now**, with the class rule
   the other keys have (`1130` must be an asset; the two drawer defaults may be income or
   expense, because a bank refunding charges and a penalty are both real). The Defaults
   *screen* is step 6's; the keys being settable now is what makes them testable now, and
   `PUT /gl/settings` already has a caller so the register gains no line for it.

9. **`test_no_mutable_balance_columns_exist` gains a register.** ADR-04's guard matched any
   column with "balance" in its name and P8 adds six that are not stored balances of a Vinea
   account: four are the **bank's** own figures (which Vinea does not derive at all), one is
   the snapshot a locked reconciliation stores, and one caches a stored row. Each is listed in
   `ALLOWED_BALANCE_COLUMNS` with its reason, and the assertion is now **equality** rather than
   emptiness — so a new `balance` column still fails, and so does deleting one of these
   without deleting its line.

10. **A `statement_needs_bank` refusal, which the prompt does not name.** A cash account has no
   statement to import: decision 5 gives it no reconciliation
   (`reconciliation_needs_bank`) and decision 2 gives it no format, and this is the third
   member of that family.

11. **`bank` and `all` are refused by name until step 4.** The build order rebuilds
   `fx_revaluation_role` here and builds decision 8's bank scope at step 4, which leaves a
   window where the API accepts an enum value `app/subledger/revaluation.py` cannot compute.
   Left alone, `_ROLES[role]` raises `KeyError` and the caller gets a 500 with nothing in it
   to act on. `_partner_roles()` turns that into `fx_revaluation_role_unsupported` with a
   field error on `role`, and `tests/subledger/test_revaluation.py` asserts it — one test that
   step 4 deletes when it makes the roles real.

## The refusal table

Every refusal this step adds, and the test that fires it.

| code | where | test |
|---|---|---|
| `not_a_cash_account` | `accounts.register` | `test_registering_a_plain_account_is_refused` |
| `bank_account_code_taken` | `accounts.update` | `test_two_bank_accounts_cannot_share_a_code` |
| `bank_account_has_lines` | `accounts.update` | `test_the_currency_may_not_change_once_the_account_has_lines` |
| `cash_account_has_no_format` | `accounts.update` | `test_a_cash_account_cannot_carry_a_statement_format` |
| `statement_format_invalid` | `formats.validate_format` | `test_a_mode_without_its_columns_is_refused_when_the_format_is_saved`, `test_separators_that_agree_are_refused` |
| `bank_account_currency_mismatch` (engine) | `posting._check_bank_account_currency` | `test_a_foreign_currency_account_refuses_a_base_currency_line` |
| `bank_account_currency_mismatch` (`VN012`) | `kernel_check_bank_account_currency` | `test_the_trigger_still_refuses_when_the_engine_check_is_disabled` |
| `statement_needs_bank` | `statements._bank_account` | `test_a_cash_account_has_no_statement_to_import` |
| `statement_already_imported` | `statements.import_statement` | `test_the_same_file_twice_is_refused` |
| `statement_parse_error` | `statements.import_statement` | `test_a_parse_error_imports_nothing` |
| row-numbered `ParseError`s (inside `statement_parse_error`) | `formats.parse` | `test_a_wrong_date_format_is_a_row_numbered_error_and_not_an_exception`, `test_a_mapping_aimed_at_a_missing_column_says_so_once`, `test_a_row_with_both_a_debit_and_a_credit_is_refused`, `test_a_zero_amount_row_is_refused_with_its_row_number`, `test_a_cr_dr_marker_in_a_signed_column_is_refused_rather_than_dropped` |
| `statement_empty` | `statements.import_statement` | `test_a_file_with_no_lines_at_all_is_refused` |
| `statement_balances_required` | `statements.import_statement` | `test_a_format_with_no_balance_column_needs_the_two_balances_keyed` |
| `statement_has_matches` | `statements.void` | `test_voiding_is_refused_while_a_line_is_matched` |
| `statement_already_void` | `statements.void` | `test_a_statement_cannot_be_voided_twice` |
| `statement_line_immutable` (`VN013`) | `bank_block_statement_line_mutation` | `test_a_stored_line_cannot_be_edited`, `test_a_stored_line_cannot_be_deleted` |
| `idempotency_key_reused` | `statements._replay` | `test_a_key_reused_for_a_different_file_is_refused` |
| `invalid_gl_setting_account_class` | `PUT /gl/settings` | `test_the_defaults_screen_can_move_the_bank_revaluation_account` |
| `fx_revaluation_role_unsupported` | `revaluation._partner_roles` | `test_the_bank_and_all_scopes_are_refused_by_name_until_p8_step_4` |

### The currency rule, proven sensitive both ways

The build order asks for each half to be proven on its own, and both tests are in
`tests/banking/test_accounts.py`:

* **Disable the engine check** (`monkeypatch` over `posting._check_bank_account_currency`) →
  the posting still fails, and the test asserts the `VN012` SQLSTATE on the exception's
  `__cause__`, not merely the error code. Asserting the code alone would pass with the engine
  doing the work, which is the thing this test exists to rule out.
* **Drop the trigger** (from the admin connection, since the app role does not own the table;
  recreated in a `finally`) → the engine still refuses, with the field error a screen shows.

## The register

Eight `GAP (P8, step 6)` / `GAP (P8, step 7)` lines in `tests/test_api_has_a_caller.py`, each
naming the screen that deletes it: four for the Bank accounts screen (register, edit, rule
create, rule edit) and four for the Bank statements screen (preview, import, manual, void).
`POST /banking/statements/preview` is on the list and is not a mutation — it stores nothing —
because the register goes by HTTP method, correctly, and because a preview nobody can reach is
an import with no way to see what it is about to write.

The P1–P7 entries are whatever `main` carries; none was changed.

## Gates

```
$ make migrate-check
migrate-check: upgrade-from-zero, alembic check and downgrade-to-base all green
```

```
$ docker compose exec -T backend uv run ruff check .
All checks passed!
```

```
$ make be-test                 # docker compose exec -T backend uv run pytest -n 4 -q
1461 passed, 7 warnings in 1458.96s (0:24:18)
```

`main` ran **1 379** at the P7 close and still does. The 82 this step adds break down as:

| | |
|---|---|
| `tests/banking/test_formats.py` | 21 |
| `tests/banking/test_statements.py` | 20 |
| `tests/banking/test_accounts.py` | 16 |
| `tests/banking/test_api.py` | 12 |
| `tests/test_p8_backfill.py` | 4 |
| `tests/subledger/test_revaluation.py` — the two roles not built yet | 1 |
| `tests/test_api_has_a_caller.py` — parametrized, one case per endpoint | 8 |
| | **82** |

Nothing was deselected and nothing skipped. The seven warnings are the five `TestClient`
deprecations and the two WeasyPrint ones `main` already carries.

Frontend, for the one generated file this step touches (`src/lib/api-enums.ts`, ten new enums
from the Python ones — regenerated, never typed):

```
$ npx tsc --noEmit
(clean)
$ npx vitest run
Test Files  16 passed (16)
     Tests  407 passed (407)
$ npx next lint
(the same four pre-existing react-hooks/exhaustive-deps warnings as on main; no new output)
```

No screen exists yet, so there is no e2e to re-take and no screenshot to commit — rule 13
applies from step 6, and step 1 builds nothing a person can open.

The branch, pushed at `53d47d6`:

```
$ git status --short
$ git log @{u}..
```

Both empty. `git diff --stat main...HEAD`: **40 files, 7 045 insertions, 24 deletions** —
of which 6 025 lines are the eleven new files (the migration, the six modules and the four
test files), 356 are this report, and the 24 deletions are the lines this step replaced in
`test_schema_invariants.py`, `revaluation.py` and `models/__init__.py`.

## Deferred out of step 1, deliberately

**The e2e seed's second bank account.** Decision 9 gives Rugari Wines E2E a `1121 Bank Account
USD` beside `1120`, and Kivu Traders only the seeded pair, "so every screen is exercised both
ways". Nothing is exercised both ways until there is a screen: the build order for this step
names the three settings keys, `1130` and the back-fill and does not name the e2e seed, and
adding a chart row to the shared fixture company now would perturb every existing spec for no
reading gained. It lands with the Bank accounts screen at step 6, which is the first thing
that needs it.

**`tests/banking/test_boundary.py`.** Scheduled for step 2 by the build order. The claim it
proves is already true — neither `app/banking` module imports `app.kernel.posting` at all —
but a claim is not a guard, and the guard arrives when the module it has most to say about
(`matching.py`, which posts through the kernel) does.

## What step 2 inherits

The schema, the master and the parser, and nothing that matches or reconciles. Step 2 adds
`matching.py` (the three auto rules in order, manual n:m with the balance rule, tick, unmatch
and its lock refusal, post-from-statement in the posting's transaction, the rules as prefill),
`reconciliation.py` (the figures function, open, lock with its two refusals and the snapshot,
reopen, late lines by `high_water_line_id`), `assert_bank_invariants` clauses 1–7 and 9 each
proven sensitive, `tests/banking/test_boundary.py`, and the Hypothesis machine without payment
runs and revaluation — with its census floors and reach counters going into the nightly
summary at that step rather than at step 9.

Step 2 is a **STOP** gate.
