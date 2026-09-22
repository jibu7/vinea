# P8 step 4 — bank revaluation, the reports and the enquiry

Not a gate step; step 5 is the STOP. `main` at `284d8e8`, which carries P8 step 3 (PR #66) and
the P4 allocation-date fix that step 3's property machine found (PR #67).

## The schema

**None, again.** Step 1's `0027_p8_banking` did every bit of decision 8's schema work: `bank` and
`all` on `FxRevaluationRole` and on the Postgres type, `fx_revaluation_lines.document_id` made
nullable, `bank_account_id` added, and the `(document_id IS NULL) <> (bank_account_id IS NULL)`
CHECK that keeps a line about exactly one thing. `1130 Bank Revaluation` and
`gl_settings.bank_revaluation_account_id` are seeded there too. `alembic check` is clean without a
new revision.

That is two steps running on a schema written three steps earlier against the decisions rather
than against its own needs, which is the thing step 1 was trying to buy.

## Decision 8 — bank revaluation as a scope

### The role map became a scope-set map

P7 mapped `FxRevaluationRole` to a tuple of `PartnerRole`. `bank` covers something that is not a
partner role at all, so the values had to change shape:

| role | scopes |
|---|---|
| `ar` | `{ar}` |
| `ap` | `{ap}` |
| `both` | `{ar, ap}` |
| `bank` | `{bank}` |
| `all` | `{ar, ap, bank}` |

**`fx_revaluation_exists` is now an intersection of those sets, not an equality of roles.** P7's
comparison gave the right answer while every role was a subset of `{ar, ap}` — it could not
afterwards. `bank` after `all` intersects and is refused; `bank` after `ar` does not and is
allowed, and that second case is the one that matters: an accountant who revalued the subledgers
on the 30th can still revalue the bank, and P7's equality would have refused it wrongly.
`test_a_bank_run_after_a_subledger_run_at_the_same_date_is_allowed` asserts the pair in both
directions.

### `fx_revaluation_role_unsupported` is deleted, and what replaced it

Step 1 put that refusal there deliberately — an enum value the API accepted and the service could
not compute needed to say so rather than fall off a map as a `KeyError`. Step 4 builds the scope,
so the refusal and the test that pinned it both go, exactly as step 1's own docstrings said they
would.

What stands in its place is **totality**: `test_every_role_has_a_scope` fails if a member of the
enum is ever added without a scope. Same protection, moved from a runtime refusal into the suite,
and it costs nothing to keep.

### The posting map was widened, not duplicated

The group key goes from `(PartnerRole, currency_id)` to `(scope, currency_id, bank_account_id)`.
AR and AP group per currency as they always have, with the account null; a **bank** group is per
*account*, because `1130`'s other side is per account.

Two consequences, both asserted:

* two USD bank accounts post **two** pairs of lines and never a net — `[6 000, 14 850]` on
  `1130`, not one line of 20 850;
* the P&L side is per group too, so a USD account gaining while a EUR account loses gives two
  P&L lines. That is the rule AR and AP already follow across currencies, applied to the new
  scope rather than excepted from it.

The alternative — a second grouping pass beside the first — was rejected at the gate for the
reason that makes this readable in hindsight: a second pass can drift from the first and nothing
would notice, whereas a wider key cannot.

**`_entry_lines`' key now has a test of its own** (`test_the_revaluation_group_key_is_scope_currency_and_account`
and its pair). P7's tests pin the *figures* the map produces, and those figures survived the
widening untouched — which is precisely why the key needs pinning separately: a future change back
to `(role, currency)` would keep every P7 test green while two bank accounts quietly netted into
one `1130` line.

### Never the bank account itself

`1130` takes the other side because a base-only line on a bank account — zero `amount`, non-zero
`base_amount` — would be a ledger line the statement can never show, and the reconciliation would
carry it as outstanding forever.

Asserted from the ledger's side rather than the map's: `1121` is unchanged at 653 400 across the
run **and** its mirror, and no line on the account carries a zero `amount`. The balance sheet
identity is asserted directly as `1121 + 1130 == 495 × 1 350`.

### The tape's row 12, reproduced

| figure | expected | actual |
|---|---|---|
| `BK-USD` `open_amount` | 495.00 | 495.00 |
| `carrying_base` | 653 400 | 653 400 |
| `revalued_base` at 1 350 | 668 250 | 668 250 |
| **gain** | 14 850 | 14 850 |
| `SIN-4` open | USD 100.00 | USD 100.00 |
| carrying / revalued | 132 000 / 135 000 | 132 000 / 135 000 |
| **loss** | 3 000 | 3 000 |
| `FXR-1` | Dr `1130` 14 850 · Cr `4410` 14 850 · Dr `6955` 3 000 · Cr `2190` 3 000 | as expected, four accounts |
| `FXR-2` | the mirror | `1130` flat on 1 Oct |
| `1121` | unchanged 653 400 | unchanged |
| balance sheet | `1121 + 1130 = 668 250 = 495 × 1 350` | equal |
| second run, role `bank` | `fx_revaluation_exists` | refused |

Every one passed on the first run of the file. That is worth one sentence rather than a
celebration: the literals were worked from the prompt by hand before the code was written, so a
first-run pass is the arithmetic agreeing, not the test being fitted to the output.

## Decisions 6 and 10 — the reports and the enquiry

`app/banking/reports.py` is a view over `journal_lines` throughout. Nothing in it stores a figure,
caches one or adjusts one.

### The Cashbooks tie, and the reading decision 6 needed

Decision 6 says the closing balance is asserted equal to the trial balance "in the account's
currency". That is exact on a base-currency account and **impossible** on a foreign one:
`reconciled_amount` is `base_amount` on the first, so the closing *is* the trial balance's figure;
on the second the closing is USD 495.00 while the trial balance — a base-currency report — says
653 400 francs. So there are two ties.

| tie | against | holds |
|---|---|---|
| `base_closing_ties` | `trial_balance`'s figure for the account | every account, **any** date |
| `currency_closing_ties` | `period_balances` in the account's own currency | foreign or base, **at a period end** |

The second returns **`None`**, not `False`, when the date is not a period end. The cache is keyed
by period and has no opinion about a Tuesday, and a caller treating `None` as a failure would be
asserting something unanswerable. A cashbook is closed at a period end, which is where the tie is
wanted. `test_the_currency_tie_declines_a_date_that_is_not_a_period_end` pins that distinction.

Both are **functions in the service**, not assertions living in a test, so the tape, the step-8
e2e and anything else ask the same code the same question. Two sums of the same thing in two
places is how a tie stops being a tie.

The tape's literal is the base one on `BK-RWF`: opening 1 000 000, receipts 477 000, payments
476 500, closing **1 000 500** == trial balance `1120` at 30 September.

`period_balances` was chosen over a second sum across `journal_lines` deliberately: the point is
that the derived report and the maintained cache agree, and `verify_period_balances` already
proves the cache against the lines (ADR-04). Summing the lines twice would prove nothing.

### The Reconciled column, and the summary

The column reads `matching.match_by_journal_line` — the same function the GL entry page reads — so
a line cannot say `BRC-000002` on the report and *outstanding* on the entry. All three states
(`BRC-` number, `matched`, blank) are asserted in one report.

The **summary is built from the detail** per account rather than from a second aggregate query. A
summary that recomputed could disagree with the page a user opens from it, which is the defect
shape P4 shipped when a report read one field and a screen read another.

### The reconciliation report carries both readings

`stored` — what the reconciliation *said*, reproduced from the lines that existed at the lock —
beside `live`, what the same date computes now. They differ by exactly the late lines, which are
listed as *Posted after lock*. Showing only the live figures would silently restate a signed
document; showing only the stored ones could not explain a difference anybody noticed.

### One defect the tests found

The enquiry passed the standing reconciliation's id into `figures()`. That asks clause 4's
narrower question — "is this line in a match carrying **this** reconciliation's id" — which is
right for reproducing a locked reconciliation and wrong for a live reading: a line already locked
into an earlier `BRC-` answers no and is counted outstanding all over again. The enquiry showed
two outstanding lines where the ledger has one.

Fixed by taking no id, which is what `live_figures` does and what the workspace strip shows. Found
by `test_the_enquiry_answers_every_figure_decision_10_names`, which asserts the count as a figure
rather than the presence of a key — the lesson P4's blank `partner_name` left.

## The register

**No line, because step 4 adds no mutating endpoint.** Its four routes — the Cashbooks detail and
summary, the reconciliation report and the bank-account enquiry — are GETs, which the rule-14
register has no opinion about.

Verified from `app.openapi()` rather than inferred from the register staying green: `app.routes`
is populated lazily and reported zero mutating endpoints, which would have been a comfortable and
entirely wrong answer. The schema lists 150, of which 20 are banking's (all step 1-3's, already
registered) and two are P7's FX endpoints, which have had callers since P7 step 7.

## The census

Pass 7 came back green on every floor with revaluation in the plan, and showed one thing worth
acting on: **`not_found` at 118** of roughly 680 run and revaluation attempts.

Step 3 pruned `state["invoices"]` of ids the session rollback had taken away and took that counter
from 24 to zero. Step 4 put it back, because `paid`, `runs` and the new `revaluations` list were
never pruned — the same defect wearing three more hats. The step-3 report named it as a step-4
item, so it is fixed here rather than carried a second time: one helper over every list, so the
next list added is pruned by construction rather than by somebody remembering.

### Pass 8 — the one this step is reported on

```
refusals: {'bank_account_currency_mismatch': 598, 'document_not_open': 13,
           'fx_revaluation_exists': 50, 'fx_revaluation_reversed': 17,
           'match_unbalanced': 35, 'payment_exceeds_open': 60,
           'payment_run_before_invoice': 24, 'reconciliation_date_order': 3,
           'reconciliation_difference': 29, 'reconciliation_open_exists': 75,
           'statement_already_imported': 64, 'statement_lines_unmatched': 8}
reach:    {'auto-match: found a tie and declined': 5, 'auto-match: matched something': 88,
           'auto-match: run': 586, 'currency rule: attempted': 598, 'lock: attempted': 74,
           'lock: attempted at a wrong balance': 36, 'lock: succeeded': 37,
           'match: attempted manually': 95, 'match: made manually': 60,
           'payment run: attempted': 145, 'payment run: posted': 48,
           'posted from a statement line': 41, 'reconciliation: opened': 433,
           'reopen: succeeded': 10, 'revaluation: attempted': 617,
           'revaluation: posted': 567, 'revaluation: reversed': 141,
           'run reversal: attempted': 8, 'run reversal: succeeded': 8,
           'settlement: posted': 685, 'statement: imported from a file': 290,
           'statement: keyed': 275, 'statement: perturbed by dropping a line': 48,
           'statement: perturbed by shifting a value date': 143,
           'statement: perturbed with a line the ledger lacks': 83,
           'supplier invoice: posted': 626, 'tick: made': 288, 'unmatch: succeeded': 65,
           'usd line on the base-currency account': 530}

10 passed, 1 warning in 729.79s (0:12:09)
```

Every floor clear, and every invariant suite — ledger, subledger and bank — held after every step
of 300 examples at both a 0-dp and a 2-dp base with revaluation drawn into the plan. Three things
in it worth naming:

| | pass 7 | pass 8 |
|---|---|---|
| `not_found` (wasted draws) | 118 | **absent** |
| `document_not_open` | 21 | 13 |
| `payment_exceeds_open` | 41 | 60 |

* **`not_found` is gone**, which is the prune above doing what step 3's did for `invoices` alone.
* **`fx_revaluation_exists` at 50 and `fx_revaluation_reversed` at 17** — the scope overlap and the
  double-reversal are provoked by the machine rather than only by their unit tests, which is what
  puts decision 8's new refusal inside the compounding-error property instead of beside it.
* **`revaluation: posted` at 567** against 617 attempts. The date is taken from the tenant's own
  period ends rather than drawn freely, for the reason step 3's payment-run date taught: a free
  offset would have spent nearly every attempt on `fx_revaluation_not_period_end` and never
  reached the posting, and the census would have read "the guard held" when it meant "the
  generator never got there".

## Gates

```
$ docker compose exec -T backend uv run ruff check .
All checks passed!

$ make migrate-check
migrate-check: upgrade-from-zero, alembic check and downgrade-to-base all green

$ make be-test                 # docker compose exec -T backend uv run pytest -n 4 -q
1637 passed, 8 warnings in 1483.60s (0:24:43)
```

`alembic check` clean **without a new revision** is this step's claim that it added no schema —
the same reading as steps 2 and 3.

`main` at `284d8e8` runs 1 606. The 31 this step adds:

| | |
|---|---|
| `tests/banking/test_bank_revaluation.py` | 14 |
| `tests/banking/test_reports.py` | 16 |
| `tests/subledger/test_revaluation.py` | 2 new for the scopes, 1 scaffold deleted → +1 (15 total) |
| | **31** |

Nothing deselected, nothing skipped; the eight warnings are the ones `main` already carries.

The **deep** profile is not part of `be-test` — the per-commit profile draws two examples, so the
census stays silent there and the nightly enforces it. Pass 8 is quoted in full above.

### Frontend

**No source changed.** `git diff main...HEAD -- frontend` is empty. The screens arrive at step 8
(Reports → General Ledger → Cashbooks and Bank reconciliation, Enquiries → General Ledger → Bank
account enquiry, and the FX revaluation screen's new roles), so there is no e2e to re-take and no
screenshot to commit.

The wire shape of an FX revaluation line **widened** — `document_number`, `partner_name`, `role`
and `booking_rate` are nullable now, and `scope`, `bank_account_id` and `bank_account_code` are
new. No frontend file reads those fields yet; the screen that does is step 8's, and it will be
written against the widened shape rather than adapted to it.

```
$ npm --prefix frontend run test          # vitest run
 Test Files  16 passed (16)
      Tests  407 passed (407)
```

Run because the AP partner wire shape and the FX revaluation line shape both moved; `next build`
and the Playwright set are CI's.

### The branch

```
$ git status --short
$ git log @{u}..
```

Both empty at the gate. `git diff --stat main...HEAD`: **11 files, 2 878 insertions, 105
deletions** — `app/banking/reports.py` and its test suite, the revaluation-scope test file, this
report, and the rest edited in place.

## What step 5 inherits

Step 5 is the **STOP gate**: the listings, then the acceptance tape end to end with every literal
worked by hand.

* **The tape's rows 12 and 13 are already proven at the unit level.** Row 12's revaluation figures
  are in `tests/banking/test_bank_revaluation.py` and row 13's Cashbooks tie in
  `tests/banking/test_reports.py`, both with the literals the tape will use. Step 5 re-derives them
  in sequence rather than for the first time.
* **The Cashbooks tie is a function**, so the tape calls `base_closing_ties` rather than writing
  its own comparison — which is the point of it being in the service.
* **The currency tie is period-end only.** The tape's row 13 asks about 30 September, which is one;
  a row asking mid-month would get `None` and must not read that as a failure.
* Nothing is withheld and nothing is open.
