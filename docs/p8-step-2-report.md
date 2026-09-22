# P8 step 2 — matching, reconciliation, the invariants, the machine

The STOP-gate report. Committed rather than pasted into a conversation, because the substance
of a gate is the thing the gate is about: the figures identity, the refusal table, the
invariant statement and the census are what a reviewer reads and what step 5's acceptance tape
is checked against.

`main` at `ed8abe6`, which carries P8 step 1 (PR #63) and issue #54's audit index (PR #64).

## The schema

**None.** Step 1's `0027_p8_banking` created every table this step writes to, and nothing here
needed a column it did not already have. `alembic check` is clean without a new revision, which
is the outcome the step-1 schema was shaped for: the tables were designed against decisions 4
and 5 rather than against step 1's own needs, so the step that implements them adds nothing.

Two things about that shape earned their keep this step and are worth naming:

* **The member tables are unique on their line, in the database.** That single constraint is
  what makes `outstanding` a *partition* of the account's lines rather than a query that
  happens to agree with one. Every figure below depends on it.
* **`reconciliation_id` on the match, assigned at lock.** Membership is the assignment, not a
  date comparison — which is what lets a locked reconciliation stay reproducible while matches
  go on being made around it.

## The reconciled-amount rule, with the USD-on-RWF case worked through

One function, `app/banking/accounts.py::reconciled_amount`, and one SQL twin of it. It is the
figure the statement side compares against, and there is no second definition anywhere.

> `amount` on a foreign-currency account; `base_amount` on a base-currency one.

The case the rule exists for, worked end to end. Rugari banks with Bank of Kigali in RWF
(`1120`, base currency) and holds a USD account beside it (`1121`). A customer pays a USD
invoice into the **RWF** account — decision 2's rule is one-sided, so this is legal, and P4
values it at the rate keyed on the document:

| | |
|---|---|
| The receipt | USD 200.00 at 1 300 |
| The journal line on `1120` | `currency_id` = USD, `amount` = **200.00**, `base_amount` = **260 000** |
| What Bank of Kigali's statement shows | a credit of **260 000** francs |
| `reconciled_amount(line, BK-RWF, base=RWF)` | **260 000** — the `base_amount` |

Had the rule read `amount`, the matcher would have looked for a statement line of **200** and
found nothing; the receipt would have sat outstanding forever and the reconciliation would
have been out by 259 800 with nothing on the screen to explain it. On `1121` the same function
returns `amount`, because that account is *held* in USD, the `VN012` trigger refuses anything
else on it, and the statement it produces is denominated in dollars.

`tests/banking/test_accounts.py::test_the_sql_form_of_the_reconciled_amount_agrees_with_the_python_one`
asserts the two renderings agree, over both accounts, and works the 261 100 figure by hand.
`tests/banking/test_matching.py::test_the_usd_receipt_into_the_rwf_account_matches_on_its_base_amount`
is the same case seen by the matcher.

## The figures identity

Computed by one function — `reconciliation.py::figures` — read by the workspace strip, both
lock refusals, the report, and invariant clause 4.

```
ledger_balance      = Σ reconciled amounts of the account's lines dated <= the date
outstanding         = the same sum over lines not in a match *effective* at that date
unmatched_statement = the account's live statement lines dated <= the date, in no match
difference          = statement_balance − (ledger_balance − outstanding)
```

`ledger_balance − outstanding` is "what the bank should be showing": the ledger, less the money
the ledger knows about that the bank does not yet.

**Effective at a date** means every member — statement *and* ledger — is dated on or before it.
Both sides, deliberately: a receipt dated 30 September matched to a credit the bank booked on
2 October is a deposit in transit, and reading only the ledger side would reconcile it a month
early against a statement that never said so.

Checked against three of the acceptance tape's own rows, at this level, so step 5's literals
are already known to come out of this code:

| tape row | statement | ledger | outstanding | difference | test |
|---|---|---|---|---|---|
| 2, before the tick | 1 000 000 | 1 000 000 | 1 000 000 | **1 000 000** | `test_an_unticked_opening_entry_is_wholly_outstanding` |
| 2, after the tick | 1 000 000 | 1 000 000 | 0 | **0** | `test_ticking_the_opening_entry_closes_the_difference` |
| 8 | 1 053 000 | 983 000 | −70 000 | **0** | `test_an_unpresented_payment_is_outstanding_and_the_difference_shows_it` |

## The two lock refusals, and why the order matters

`statement_lines_unmatched` is checked **before** `reconciliation_difference`, and that is not
an implementation detail. A difference of zero with a statement line nobody has explained is
**two errors cancelling**: the ledger happens to foot to the bank's figure while the bank is
showing a movement nobody has accounted for. Refusing the unmatched lines first is what stops
that closing.

Both name their figure in the refusal, so the workspace can show them *before* the button
rather than after it.

## What landed

| | |
|---|---|
| `app/banking/matching.py` | the three auto rules in order, n:m with the balance rule, tick, unmatch, post-from-statement, rules as prefill |
| `app/banking/reconciliation.py` | the figures function, open, lock, reopen, late lines by high-water mark |
| `app/api/v1/banking.py` | nine workspace endpoints |
| `tests/banking/invariants.py` | `assert_bank_invariants`, clauses 1–7 and 9 |
| `tests/banking/test_boundary.py` | nothing under `app/banking/` writes a journal line |
| `tests/banking/test_property_banking.py` | the machine, 0-dp and 2-dp, plus one targeted property |
| `tests/banking/test_property_targeted_refusals.py` | the three conjunctions, constructed |
| `tests/banking/test_invariant_sensitivity.py` | every clause proven to catch its own failure |

### The three auto rules are an order of *evidence*

`reference` → `payment_run` → `amount_date`, each applied **only where the candidate is
unique**. A document number the bank printed is the bank telling you what the line is; a run
number is the same for a batch; an equal amount within three days is a guess that happens to be
a good one. The order decides which evidence wins when two rules both have something to say —
`test_the_reference_rule_wins_over_amount_and_date` is that case.

**Ambiguity is not a match.** Two receipts of the same amount in one week is an ordinary Tuesday
for a business with a price list; matching either would put a coin toss in the reconciliation as
fact. The workspace gets both candidates and a person chooses.

The windows differ on purpose: ±30 days for `reference` (a cheque presented six weeks after it
was written is the case it exists for) and ±3 for `amount_date` (nothing to go on but a figure).

### Post from a statement line is one transaction

The posting and its `posted` match are written together. A posted line is never left unmatched
— the user would post it twice — and a failed posting leaves no match, or the reconciliation
would claim a line that does not exist. Neither half is recoverable by a person looking at the
screen afterwards, which is why it is a transaction rather than two calls and a retry.
`test_a_failed_posting_leaves_no_match` is the half a refusal message cannot show.

The settlement path posts **unallocated**, deliberately: which invoices a receipt pays is the
P4 allocation screen's decision, and a reconciliation that allocated as a side effect would be
making a subledger decision from a bank statement.

## The refusal table

| code | where | test | proven sensitive by |
|---|---|---|---|
| `match_unbalanced` | `matching.create_match` | `test_a_match_that_does_not_balance_is_refused_with_the_difference` | clause 2's sensitivity test, which builds the state underneath the service |
| `match_across_accounts` | `matching._refuse_foreign_members` | `test_a_match_across_two_bank_accounts_is_refused` | clause 1's cross-account test |
| `match_is_empty` | `matching.create_match` | `test_an_empty_match_is_refused` | clause 1's no-members test |
| `statement_line_matched` | `matching._refuse_already_matched` | `test_a_line_cannot_be_in_two_matches`, `test_posting_from_an_already_matched_line_is_refused` | the DB unique constraint behind it |
| `journal_line_matched` | `matching._refuse_already_matched` | `test_a_line_cannot_be_in_two_matches` | as above |
| `reconciliation_locked` | `matching.unmatch` | `test_unmatching_inside_a_locked_reconciliation_is_refused` | clause 3 |
| `posting_is_not_one_bank_line` | `matching._bank_line_of` | — (asserted, not reachable today) | see below |
| `statement_line_void` | `matching.get_statement_line` | `test_a_voided_statements_lines_leave_the_matcher` | clause 9 |
| `reconciliation_needs_bank` | `reconciliation.open_reconciliation` | `test_a_cash_account_has_no_reconciliation` | — |
| `reconciliation_open_exists` | `reconciliation.open_reconciliation` | `test_only_one_reconciliation_may_be_open_per_account` | the partial unique index behind it |
| `reconciliation_date_order` | `reconciliation.open_reconciliation` | `test_the_next_reconciliation_must_be_dated_after_the_last_locked_one` | — |
| `statement_balance_required` | `reconciliation.open_reconciliation` | — (paper mode always keys one) | — |
| `statement_lines_unmatched` | `reconciliation.lock` | `test_a_lock_is_refused_while_a_statement_line_is_unmatched` | — |
| `reconciliation_difference` | `reconciliation.lock` | `test_a_lock_is_refused_at_a_non_zero_difference` | clause 4's zero-difference assertion |
| `reconciliation_already_locked` | `reconciliation.lock` | — | — |
| `reconciliation_not_latest` | `reconciliation.reopen` | `test_only_the_latest_locked_reconciliation_may_be_reopened` | — |
| `reconciliation_open_exists` (reopen's door) | `reconciliation.reopen` | `test_reopening_is_refused_while_a_later_reconciliation_is_open` | the partial unique index, which used to be the only thing catching it |
| `reconciliation_not_locked` | `reconciliation.reopen` | `test_an_open_reconciliation_cannot_be_reopened` | — |
| `reason_required` | `reconciliation.reopen` | `test_reopening_needs_a_reason` | — |

**`posting_is_not_one_bank_line` is asserted rather than exercised**, and it is the one entry
worth arguing about. A `CashbookEntry` derives exactly one bank line for the gross (P2), and a
P4 settlement posts one to its `cash_account_id`, so no path today can produce two. The check
is there because if one ever did, matching one of them would silently leave the other
outstanding forever — a defect with no symptom at the moment it is introduced. It is a refusal
against a future event rather than a current one, which is why it has no test.

One correction to the prompt's wording, recorded where it is asserted. Decision 4 says the
engine's `not_a_cash_account` and `control_account_manual_posting` refusals are what stop a
rule targeting a control account. The refusal that actually fires is
**`control_account_direct_posting`**: `manual_posting` is the `ManualJournal`-only branch, and
what a drawer posts is a `CashbookEntry`, so what refuses it is P4's `control_account_modules`
registry — `cb` is not among the modules paired with `ar`. That is the stronger of the two (it
is `VN007` in the database as well), so the guard decision 4 reached for is there and then
some.

## The invariant suite

Clauses **1–7 and 9**. Clause 8 is the payment-run clause and arrives with the runs at step 3;
it is declared by name in the suite's docstring and asserted absent by
`test_clause_8_is_not_written_yet`, so the gap is a fact a reader meets rather than one they
have to notice.

Every clause is proven to catch its own failure — 18 tests in
`tests/banking/test_invariant_sensitivity.py`, each breaking the state *underneath* the
services (direct SQL, or with a trigger dropped) and asserting the suite goes red. A clause
with a typo'd column or an always-true comparison would otherwise sit in the tape and the
machine reporting green about a question it never asked: P7 step 9's row 19, and the reason
that report says a census is a report until it is a gate.

Two clauses are worth the reader's attention.

**Clause 4** is the one the phase turns on. It recomputes a locked reconciliation's
`ledger_balance` over the account's lines with `id <= high_water_line_id` dated on or before its
date, and `outstanding` over those whose match does not carry **this** reconciliation's id, and
asserts the stored columns reproduce exactly — plus `statement_balance == ledger_balance −
outstanding_total`. Membership is the assignment made at lock, not a date test: a match created
afterwards may be perfectly effective at the locked date, and counting it would restate a figure
somebody signed.

**Clause 9 checks `is_void` against the statement's status in both directions**, which is the
carry-forward from step 1's approval. `VN013` makes a statement line immutable with exactly one
exemption — `is_void` — because a voided line has to leave the fingerprint uniqueness scope and
cannot be deleted. That exemption is a hole by design, and clause 9 is the only thing standing
in it. A line wrongly flagged void leaves every listing **and** stops blocking a re-import, so
the same movement is imported twice and the reconciliation is out by exactly one line nobody
can find.

## The boundary

`tests/banking/test_boundary.py` reads the import graph three ways: no module under
`app/banking/` constructs a journal row, writes the ledger tables in SQL, or imports the
engine's internals — with an anti-vacuity test per scan, a test that the scan actually reaches
the package, and one asserting from the ledger's side that no entry is ever posted under
`BANKING_MODULE`.

**One line, as the build order asks for it:** no new `module` string reaches
`journal_entries.module`. Every posting this phase causes is a `CashbookEntry` (`cb`), a P4
document (`ar` / `ap`) or a P7 revaluation (`gl`).

## What the machine found

**Two defects, neither of which a hand-written test would have contained.** Both are the shape
a random plan finds and a considered example does not, which is the whole argument for having
the machine at all.

### 1. A reconciliation locked on an empty account had no high-water mark

`max()` over an account with **no journal lines** is NULL, so a bank account opened and
reconciled before its first transaction — an ordinary thing to do — locked with a NULL
`high_water_line_id`.

NULL there means *unknown*, which is a lie: what is known about an empty account is that no
line existed, and the mark for that is **0**. The consequence was not cosmetic. `late_lines`
and the workspace's "dated inside BRC-n" both skip a NULL mark, so that reconciliation would
never flag a late line again however many were posted into its period; and clause 4 could not
reproduce its figures, because nothing said which lines it was struck over.

Fixed with `coalesce(max(id), 0)` and pinned by
`test_an_account_with_no_lines_locks_with_a_high_water_mark_of_zero`, which also asserts the
next line posted into the period *is* flagged late.

### 2. Reopening could put two open reconciliations on one account

One open reconciliation per account is the rule, and `open_reconciliation` enforces it —
`reconciliation_open_exists`. **Reopening is the back way into the same state**: lock `BRC-1`,
open `BRC-2` on top of it, then reopen `BRC-1`, and the account has two open.

The partial unique index `uq_bank_reconciliations_open_per_account` caught it, which is the
right last line of defence and the wrong first one: it reached the caller as an integrity error
rather than as something a screen can render. `reopen` now refuses it with the same code the
front door uses, because it is the same rule.

Nobody sets out to do this, which is why no hand-written test had. The machine reached it by
wandering into it on a two-decimal base at example ~200.

### 3 (the third, found earlier by a clause rather than the machine)

`reopen` read `latest_locked` *before* flushing the status change. On an `autoflush=False`
session — which this build requires, because the posting engine depends on it — the query still
saw the reconciliation being reopened as locked and handed it back as its own predecessor, so
`bank_accounts.last_reconciled_*` kept pointing at the reconciliation that had just been
withdrawn. **Invariant clause 7 caught it**, which is exactly why that clause recomputes the
cache instead of trusting it.

## The census, and what it cost to make it mean something

The census is a **gate**, not a report — P7 step 9's row 19 in one line — and it was red twice
before it was green. Both times it was the generator, and both times the reach counters said so.

### Pass 1

```
[property] refusals provoked: {'bank_account_currency_mismatch': 547, 'match_unbalanced': 2,
  'reconciliation_date_order': 5, 'reconciliation_open_exists': 69,
  'statement_lines_unmatched': 7}
[property] reach: {'lock: attempted': 105, 'lock: succeeded': 98, 'match: attempted manually': 48,
  'match: made manually': 46, 'posted from a statement line': 15, 'reconciliation: opened': 324,
  'reopen: succeeded': 7, 'statement: keyed': 169, 'tick: made': 199, 'unmatch: succeeded': 64,
  'usd line on the base-currency account': 445, …}

AssertionError: the deep pass did not provoke
  {'reconciliation_difference': 0, 'match_unbalanced': 2, 'reconciliation_locked': 0}
  at least 3 times each.
```

Three floors short with **every reach counter healthy** — 105 locks attempted, 98 succeeded, 48
manual matches attempted. That combination is the census saying *the generator cannot get
there*, not *the guard is broken*, and it is the distinction the reach counters exist to draw.

### Pass 2, after the generator fix

`reconciliation_difference` at zero turned out to be **my generator's fault, not a
conjunction**: `_lock` only ever keyed the figure the reconciliation needed to close, so the
machine never *tried* to lock at a difference. One draw in three now keys a deliberately wrong
balance, and it went 0 → **37**.

```
[property] refusals provoked: {'bank_account_currency_mismatch': 544, 'match_unbalanced': 1,
  'reconciliation_date_order': 2, 'reconciliation_difference': 37,
  'reconciliation_open_exists': 90, 'statement_lines_unmatched': 1}
[property] reach: {'lock: attempted': 77, 'lock: attempted at a wrong balance': 37,
  'lock: succeeded': 39, 'match: attempted manually': 16, 'posted from a statement line': 9,
  'reconciliation: opened': 379, 'statement: keyed': 258,
  'statement: perturbed with a line the ledger lacks': 33, …}
```

That is worth keeping in view as the counter-example: **a floor at zero is a question, not an
answer.** Had `reconciliation_difference` been moved out with the others it would have hidden a
generator that could not reach a case it should reach on every third lock.

### The floors moved to targeted properties

Three of decision 12's five, in `tests/banking/test_property_targeted_refusals.py`, each
drawing over the *shape* of its precondition while constructing the conjunction itself:

| refusal | why a random plan cannot reach it | the property |
|---|---|---|
| `match_unbalanced` | statements here are generated *from* the ledger, so a blindly drawn pair usually balances — the machine matched 46 of 48 successfully | draws both amounts and both signs, asserts the refusal names the difference |
| `reconciliation_locked` | needs a lock that succeeded, a match assigned to it, **and** that match drawn for unmatching | builds the lock with 1–4 assigned matches and unmatches each |
| `statement_lines_unmatched` | auto-match runs often enough that by lock time nothing is left unmatched; and the *interesting* case is a **zero difference** with an unexplained line | constructs exactly that — the difference is asserted zero before the lock is attempted |

The third is the one the move improved rather than merely rescued. The refusal's whole reason
for existing is the ordering — it is checked *before* the difference, because a zero difference
with an unexplained statement line is two errors cancelling — and the machine reached that
state never. The targeted property builds it every time.

Two of decision 12's seven are still owed: `payment_exceeds_open` needs payment runs (step 3),
and `statement_already_imported` needs the *file* path, which this machine does not take —
it keys statements line by line, since generating a CSV to express a date and an amount would
make every example a parser test. It is covered by construction in
`tests/banking/test_statements.py` and joins the census when the runs do.

### Pass 3 — the one this gate is reported on

`HYPOTHESIS_PROFILE=deep`, 300 examples, both property files:

```
7 passed, 1 warning in 504.79s (0:08:24)

[property] refusals provoked: {'bank_account_currency_mismatch': 532, 'match_unbalanced': 27,
  'reconciliation_date_order': 11, 'reconciliation_difference': 27,
  'reconciliation_open_exists': 74, 'statement_lines_unmatched': 4}

[property] reach: {'auto-match: found a tie and declined': 1, 'auto-match: matched something': 34,
  'auto-match: run': 536, 'currency rule: attempted': 532, 'lock: attempted': 76,
  'lock: attempted at a wrong balance': 27, 'lock: succeeded': 45,
  'match: attempted manually': 60, 'match: made manually': 33,
  'posted from a statement line': 10, 'reconciliation: opened': 330, 'reopen: succeeded': 3,
  'settlement: posted': 476, 'statement: keyed': 271,
  'statement: perturbed by dropping a line': 17,
  'statement: perturbed by shifting a value date': 71,
  'statement: perturbed with a line the ledger lacks': 66, 'tick: made': 230,
  'unmatch: succeeded': 43, 'usd line on the base-currency account': 587}
```

**Both enforced floors clear**: `reconciliation_difference` 27 and
`bank_account_currency_mismatch` 532, against a floor of 3. Every one of the seven reach floors
clears too, which is what makes those two numbers mean something.

The three moved out are still **counted here** — `match_unbalanced` 27,
`statement_lines_unmatched` 4, and `reconciliation_locked` provoked by its own property — so
the nightly prints one census rather than two. `match_unbalanced` went 2 → 27 as a side effect
of the generator being less lucky about balanced pairs once the lock draw changed; that is not
a reason to move it back, because the number that matters is the one that holds on *every*
seed, and a targeted property is what makes it so.

Two lines in the reach census are worth reading as findings rather than as numbers:

* **`auto-match: found a tie and declined` is 1.** The ambiguity path — two candidates of equal
  amount, both declined — is reached once in 300 examples. It is covered by
  `test_two_candidates_of_the_same_amount_are_left_for_a_person` and not by the machine, and it
  is the next candidate for a targeted property if step 3's additions push it to zero. Recorded
  now so a future zero has a cause attached rather than being a surprise.
* **`reopen: succeeded` is 3.** Low for the same reason: reopening needs a locked
  reconciliation and no later open one, which after the fix above is a narrower state than it
  was. `tests/banking/test_property_targeted_refusals.py::test_reopening_frees_exactly_the_matches_the_lock_had_taken`
  constructs it, so the coverage does not depend on this counter.

## Decisions worth review

1. **`REQUIRED_REACH` beside `REQUIRED_REFUSALS`.** Decision 12 asks for refusal floors. This
   step adds a second set of floors over the machine's *preconditions* — "lock: attempted",
   "lock: succeeded", "statement: perturbed with a line the ledger lacks" — asserted **first**,
   so that a refusal reading zero is diagnosed before it is argued about. P7 moved five of P6's
   eight floors to targeted properties precisely because the refusal census could not say
   whether the guard held or the generator never got near it; this is that lesson applied
   before the failure rather than after it.

2. **Two of decision 12's seven floors are not in this machine.** `payment_exceeds_open` needs
   payment runs (step 3). `statement_already_imported` needs the *file* path, and this machine
   keys its statements line by line — generating a CSV to express a date and an amount would
   make every example a parser test. It is covered by construction in
   `tests/banking/test_statements.py` and joins the census when the runs do. Named here rather
   than left as a silent gap.

3. **A targeted property beside the machine.** `test_a_locked_reconciliation_survives_everything_posted_after_it`
   constructs its precondition — lock, then three postings dated inside the locked period, then
   a recomputation after each — rather than hoping a random plan produces that conjunction.
   P7's rule, applied from the start rather than after a nightly fails.

4. **Unmatch deletes rather than flags.** A match is an assertion and withdrawing one leaves
   nothing to record; what the *reconciliation* said is the permanent record, and that is a
   stored snapshot this cannot reach. The `reconciliation_locked` refusal is what keeps the two
   consistent.

5. **Reopen releases the matches but does not withdraw them.** Somebody ticked those lines
   against a statement and that is still true. What is withdrawn is the sign-off, so the
   matches lose their `reconciliation_id` and are free to be unmatched or to join whatever
   locks next.

6. **Only the latest locked reconciliation may be reopened.** The figures are a chain — each
   one's outstanding items are what the one before it left behind — so reopening one in the
   middle would leave the ones after it proving something against a state that no longer
   stands.

7. **`prefill_for` matches on the raw description, not the normalised form.** A rule is written
   by a person looking at their own statement: they type `ACCOUNT FEE` because that is what the
   bank prints. Normalising both sides would make `ACCOUNTFEE` match too, which is a pattern
   nobody meant. The *fingerprint* and the `reference` auto-rule do normalise, because there
   the question is "is this the same event", not "did a person mean this".

8. **`MIN_REFERENCE_TOKEN = 4`.** Normalisation strips punctuation, so a two-character token
   would match inside almost anything — `C1` is a customer code and also the tail of
   `MOMO 0788 C1234`. Four is the shortest token the rule will look for.

9. **A `ReconciliationDetail` is built, not `model_validate`d.** `figures` is computed rather
   than a column, so validating the ORM row against a model that requires it fails before
   anything is filled in. Constructing it says which parts are stored and which are derived.

## The register

**Seventeen** `GAP (P8, step …)` lines in `tests/test_api_has_a_caller.py`: step 1's eight, and
nine added here for the workspace — all one screen, Transactions → General Ledger → Bank
reconciliation at step 7. Every one is driven over HTTP by `tests/banking/test_api.py` in the
meantime, for the reason the register exists.

The P1–P7 entries are whatever `main` carries; none was changed.

## Gates

```
$ docker compose exec -T backend uv run ruff check .
All checks passed!

$ make migrate-check
migrate-check: upgrade-from-zero, alembic check and downgrade-to-base all green

$ make be-test                 # docker compose exec -T backend uv run pytest -n 4 -q
1563 passed, 7 warnings in 1419.12s (0:23:39)
```

`migrate-check` is the one worth reading twice: `alembic check` comes back clean **without a
new revision**, which is this step's claim that it added no schema.

`main` at `ed8abe6` runs 1 467. The 96 this step adds:

| | |
|---|---|
| `tests/banking/test_matching.py` | 27 |
| `tests/banking/test_reconciliation.py` | 23 |
| `tests/banking/test_invariant_sensitivity.py` | 18 |
| `tests/banking/test_api.py` | 6 new (18 total) |
| `tests/banking/test_boundary.py` | 6 |
| `tests/banking/test_property_targeted_refusals.py` | 4 |
| `tests/banking/test_property_banking.py` | 3 |
| `tests/test_api_has_a_caller.py` — parametrized, one case per endpoint | 9 |
| | **96** |

Nothing deselected, nothing skipped; the seven warnings are the ones `main` already carries.

The **deep** profile is not part of `be-test` — the per-commit profile draws two examples, so
the census stays silent there and the nightly is where it is enforced. The deep run is quoted in
full above.

### Frontend

**Untouched.** `git diff main...HEAD -- frontend` is empty: this step builds services and
endpoints, and the screens arrive at step 7. No e2e to re-take and no screenshot to commit —
rule 13 applies from step 6, and step 2 builds nothing a person can open.

### The branch

```
$ git status --short
$ git log @{u}..
```

Both empty. `git diff --stat main...HEAD`: **15 files, 7 350 insertions, 2 deletions** — the
seven new files (two modules, the invariant suite and four test files), this report, and two
deletions where the defects above were fixed in place.

## What step 3 inherits

The matcher's `payment_run` rule is **written and finds nothing**, because `payment_runs` holds
no rows until step 3 puts them there. Step 3 makes it real: the run posts one settlement per
supplier through `post_document()` and one allocation per supplier through `allocate()`, the
`payment_run` rule matches the bank's single line to the run's N ledger lines, and invariant
clause 8 arrives with them — as does `payment_exceeds_open` in the census, and
`test_clause_8_is_not_written_yet` goes away.

**STOP.** Waiting for approval before payment runs.
