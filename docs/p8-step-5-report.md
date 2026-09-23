# P8 step 5 — the listings and the acceptance tape (STOP)

The STOP-gate report. `main` at `0300736`, which carries P8 step 4 (PR #68).

Step 5 is the gate the phase turns on: every figure the prompt names, reproduced in one sequence
by code that was written before the figures were read off it. What follows is the tape's own output
table, the defect it found, and the readings it forced.

## The listings

Two of the six the prompt names did not exist, and the prompt's own text is where that was found
rather than in a review:

| listing | state before step 5 |
|---|---|
| statement listing per account, counts and status | existed |
| **statement detail with lines and their match state** | lines existed; **match state did not** |
| **ledger-line listing per account with match state** (the workspace's right pane) | **missing entirely** |
| reconciliation listing and detail | existed |
| payment-run listing and detail | existed (step 3) |
| rule listing | existed |

`GET /banking/accounts/{id}/ledger-lines` is the right pane: every line on the account with its
match, its `BRC-` if it was locked into one, and its "dated inside BRC-n" flag, **outstanding
first**. The left pane's `unmatched-statement-lines` had no counterpart, so the workspace could
show what the bank said and not what the ledger holds — half a reconciliation.

`StatementLineDetailRead` adds the match state to the statement detail. `journal_line_count` is on
it because a payment run's single debit matches **three** ledger lines, and "matched" alone would
hide the one-to-many decision 7 exists to produce. `StatementLineRead` is unchanged for the
preview, which has no matches yet by definition.

**No register line: step 5 adds no mutating endpoint.** Both are GETs.

## The tape

18 rows, one sequence, **236 expected-vs-actual assertions**, and
`assert_ledger_invariants` + `assert_subledger_invariants` + `assert_bank_invariants` after every
row. Not `assert_fiscal_invariants`: this company has **no EBM device**, so nothing fiscalizes and
that suite has nothing to say — the prompt asks for that to be stated rather than silently omitted.

| row | subject | assertions | result |
|---|---|---|---|
| 0 | three `bank_accounts` rows, `1121` by the hook, the currency rule engine **and** trigger (`VN012`) | 16 | **all ok** |
| 1 | `CB-1` / `CB-2`, and the Cashbooks summary on all three accounts | 6 | **all ok** |
| 2 | `BRC-1` in paper mode — lock refused at 1 000 000, then locked at zero | 8 | **all ok** |
| 3 | `RCT-1` / `RCT-2` allocated; `1120` 1 177 000; both receipts outstanding | 6 | **all ok** |
| 4 | `PYR-1`: discount 2 000, total **384 000**, `PMT-2` 98 000, `ALJ-1`, the instruction file, three jobs | 21 | **all ok** |
| 5 | `RCT-3` at 1 320 and `RCT-4` at the bank's **1 300**; `ALJ-2` realized loss 4 000 | 12 | **all ok** |
| 6 | `PMT-4` on account; `1120` 983 000; S3 open −70 000 | 3 | **all ok** |
| 7 | `BST-000001`; auto-match **1:3 on the run**; re-import refused | 17 | **all ok** |
| 8 | ledger 983 000, outstanding −70 000, difference **+37 500**; both refusals | 9 | **all ok** |
| 9 | the fee and the deposit posted from their lines; `BRC-000002` at zero; snapshot and high-water | 22 | **all ok** |
| 10 | unmatch inside a locked reconciliation refused | 3 | **all ok** |
| 11 | the USD statement; `CB-4` base **6 600**; `BRC-000003` at 495.00 | 11 | **all ok** |
| 12 | gain **14 850**, loss **3 000**, `1121` unchanged, `1121 + 1130 = 495 × 1 350` | 23 | **all ok** |
| 13 | the late line; stored figures unmoved; Cashbooks **1 000 500** tied to the trial balance | 18 | **all ok** |
| 14 | the overlap — **2 new, 1 skipped**; `BRC-000004`; the history unchanged | 12 | **all ok** |
| 15 | `PMT-6` 30 000 with the discount declined; `payment_run_member`; the reversal; three refusals | 15 | **all ok** |
| 16 | reopen refused then allowed; the matches stand; the cache falls back to `BRC-2` | 10 | **all ok** |
| 17 | `statement_has_matches`; the void; re-import **not** refused; the same `BRC-000004` | 13 | **all ok** |
| 18 | all six remaining refusals | 11 | **all ok** |
| | | **236** | **0 mismatches** |

Printed by the tape itself — `_expect` collects every pair as it goes and a module fixture prints
them, P7's shape — so this table **is** the run rather than a transcription of it. Every literal is
the prompt's, unchanged: no expectation was edited to meet an output, which is the rule the prompt
sets (a build override is a finding, not a correction).

## What the tape found

### Clause 4 was wrong over two successive locks

Reproducing `BRC-000002` gave **930 000** outstanding against a stored **−70 000**.

`_lines_in_this_reconciliation` read only the matches carrying *this* reconciliation's id.
`_assign_effective_matches` deliberately leaves an already-assigned match with its earlier
reconciliation — correctly, because that is what keeps the earlier one reproducible from its own
membership — so August's float kept `BRC-000001`, and September's reproduction counted it
outstanding all over again. **The stored figure was right and the recomputation was wrong**, so
clause 4 failed a lock that was correct.

930 000 is August's 1 000 000 float less September's unpresented cheque, which is the arithmetic
saying exactly what had happened.

Membership is now this reconciliation's matches **and those of every locked one before it on the
same account**. That does not weaken what the narrow reading protected — a later reconciliation is
dated later, so its matches are never in the set.

**Every step-2 test had a single lock**, which is why the narrow reading passed all of them. The
tape is the first thing in the build that could reach it, and this is what an acceptance tape is
for.

### Proven sensitive both ways, and a gap in the sensitivity suite

| direction | what was broken | what went red |
|---|---|---|
| the fix reverted | membership narrowed to this reconciliation only | the two-lock regression test **and** the tape — `BRC-000002` reproduces 930 000 against a stored −70 000 |
| the fix over-widened | the **date bound** dropped, so a *later* lock's match counts | **only the tape** — `BRC-000002` reproduces 0 |

The second row is the finding. Nothing in the sensitivity suite covered the direction the narrow
reading existed to protect, so a future change that dropped the date bound would have been caught
by an 18-row sequence saying "something broke" rather than by a test saying which rule.

`test_clause_4_holds_when_a_later_lock_takes_a_line_dated_inside_an_earlier_one` closes it, and is
red with the bound dropped. It builds the state without a raw UPDATE — the posted-entry trigger
refuses to move an entry's date, rightly — by posting the line inside August, leaving it unticked
when August locks, and ticking it into September's lock afterwards.

Both experiments restored the source byte-for-byte; `git diff` on
`app/banking/reconciliation.py` was empty after each.

## The readings

Six places the tape and the prompt disagreed. **In every one the tape was wrong and the prompt was
right** — which is worth saying plainly, because the alternative reading of a tape mismatch is to
edit the expectation, and a build override is a finding rather than a correction.

### 1. Row 15's discount is declined, and the prompt says so by omission

`PMT-6` is **30 000**, not 29 400. S2's terms are `2/10 net 30`, `SIN-5` is dated 2 November and
`PYR-2` pays it on the 3rd — one day in — so P4 offers 2 % of 30 000.

Row 4 says "with the discount taken" **in as many words, because it is a choice**. Row 15 says
nothing about a discount and gives the undiscounted figure. So this run declines it, which is
exactly what the per-line toggle is for.

The tape asserts the offer at **600** as well as the payment at 30 000, so the toggle is shown
doing something rather than silently agreeing with a zero.

### 2. The rule carries no description

`prefill_for` returns `rule.description or line.description`, so a rule that names its own
narrative shadows the bank's text. Row 9 expects the drawer to open on `MONTHLY ACCOUNT FEE` —
what the bank printed — and the prompt's setup gives the rule a pattern and an account and nothing
else. The first fixture invented a description and made the tape disagree with its own prompt.

### 3. `RCT-3` carries reference `INV-3`

Row 5 gives the receipt no reference; row 11 expects the bank's `INWARD TRF C1 INV-3` to match it
by **`reference`** rather than `amount_date`. So the receipt has to hold a token that line
contains. `INV-3` is the invoice number the customer quoted, which `_reference_tokens` documents as
exactly what a receipt's reference is for.

The first draft used `INV-3 C1` by analogy with `RCT-1` and got `amount_date`: normalised, `INV3C1`
is not inside `INWARDTRFC1INV3`, because the bank prints the two tokens the other way round. The
same match, on weaker evidence — and the rule is what the row asserts.

### 4. Row 15's refusals come *after* the reversal

The prompt scopes "before the reversal" to the `payment_run_member` refusal alone and lists `PYR-3`
and the `CASH` run next. That is the only order that works: while `PYR-2` stands, `SIN-5` is fully
paid, so a run over it is refused `document_not_open` rather than `payment_exceeds_open`. Probing
early produced exactly that.

### 5. The engine's field key is `lines.1.currency_id`

Not `currency_id`. The line index is what puts an inline error on the right row, and row 0 asserts
the real shape.

### 6. `_partner_open` reads `exposure_direction(role)`

`signed_base_amount` is signed by the **control account's** side, so AP's is negative when a
supplier is owed. Row 6's "S3 open −70 000" is the **role's** sense — an overpayment. Rather than
write a minus sign, the tape uses the product's own function, the same turn the AR/AP partner
enquiry makes, so the tape and the screen cannot drift about which way round the number goes.

### Two probes that were green while proving nothing

Both in row 18, and both the same class of defect:

* the parse-error probe re-offered a committed sample, and the **file-hash** refusal is checked
  before the parse — so it was refused `statement_already_imported` and the parser never ran. It
  builds fresh content now.
* the control-account probe hunted for an unmatched `CASH DEPOSIT` line and found none, because by
  row 18 every line on the account is matched or voided. An `if` skipped it silently and the row
  still read green. It keys its own statement line now.

A probe that can vanish is worse than no probe.

## The year is pinned to the samples, not the clock

`TAPE_YEAR = 2026`, because `tests/banking/samples/*.csv` are dated 2026, and `_ensure_tape_year`
creates a fiscal year for it rather than taking `date.today().year` the way
`tests/kernel/conftest.py` does.

Without that, on 1 January the ledger entries would move to the new year while the statement files
stayed in 2026, every auto-match in rows 7 to 14 would quietly stop matching, and a tape of
hand-worked literals would report a different answer depending on the day it ran. **That is the one
thing an acceptance tape may not do.**

`create_fiscal_year` makes it safe either way: when the wall clock *is* the tape year the seeded
periods only need opening, and when it is not the year is created and cannot overlap the seeded one.

**Carried to step 9:** `frontend/e2e/p8-cycle-tape.spec.ts` must pin its year the same way.

## The prompt corrections

Two, on the owner's direction at this gate, both pointing at the approvals row:

* **decision 5, clause 4** — "in no match carrying **its** `reconciliation_id`" becomes "in no
  match assigned to this reconciliation **or to any earlier locked reconciliation on the
  account**";
* **decision 6, the tie** — one tie becomes two, base for every account at any date and currency
  against `period_balances` at a period end, the second declining rather than failing where the
  date is not one.

Row 18's refusal name needed nothing: `control_account_direct_posting` was already corrected at the
step-2 gate.

## Precondition (d) — the real bank export

**Still not held.** `docs/banking/samples/` does not exist and no anonymised CSV from BK, I&M,
Equity, Access, Ecobank or BPR has been supplied. Step 1 said the same; nothing has changed since.

The three committed generic samples carry the tape, exactly as the prompt provides for — and the
tape is written to them in full, including the overlap and the void-and-re-import. If (d) is still
outstanding at step 9 the final report names it a plan deviation.

## Gates

```
$ docker compose exec -T backend uv run ruff check .
All checks passed!

$ make migrate-check
migrate-check: upgrade-from-zero, alembic check and downgrade-to-base all green

$ make be-test                 # docker compose exec -T backend uv run pytest -n 4 -q
1643 passed, 8 warnings in 1466.91s (0:24:26)
```

`alembic check` clean **without a new revision** — the third step running on step 1's schema.

### The count, both sides, per file

`--collect-only` on each branch rather than arithmetic, which is the rule from the step-4 gate:

```
$ git checkout main        && pytest --collect-only -q | tail -1     # 0300736
1637 tests collected in 0.76s

$ git checkout p8-step-5   && pytest --collect-only -q | tail -1     # this branch
1643 tests collected in 0.64s
```

**1643 − 1637 = 6**, and the six are named:

| file | main | branch | delta |
|---|---|---|---|
| `tests/banking/test_acceptance_tape.py` | — (did not exist) | 4 | **+4** |
| `tests/banking/test_reconciliation.py` | 23 | 24 | **+1** — the two-lock regression |
| `tests/banking/test_invariant_sensitivity.py` | 21 | 22 | **+1** — the later-lock direction |
| `tests/banking/test_api.py` | 22 | 22 | 0 |
| | | | **+6** |

Six test *functions* for 236 tape assertions, which is the shape a tape has: the sequence is one
test because its interesting values depend on its history, and the row number in the assertion
message is what localises a failure. The other three functions in the tape file are row 0's
three — the registration check and the currency rule's two halves, which are separate because a
refusal proven inside a sequence is a refusal whose precondition nobody can see.

### The deep census, and a floor removed

The gate's first deep pass came back red on **`run reversal: succeeded`: 2** against a floor of 3.

That floor was restored at the **step-4** gate, on pass 4's evidence of 7, with a note in the
source that "seven against a floor of three is a thinner margin than the rest of this list". Five
passes now give the full picture:

| pass | `run reversal: succeeded` | note |
|---|---|---|
| step 3, pass 1 | 0 | `state["invoices"]` held ids the rollback had taken away |
| step 3, pass 2 | 0 | same defect; floor removed on the reading that it was unreachable |
| step 4, pass 1 | 7 | ids pruned; **floor restored on this evidence** |
| step 4, pass 2 | 8 | green |
| step 5 (this gate) | **2** | red |

**The step-4 restoration was wrong, and this is the correction.** The diagnosis then — "the cause
of the zero was the generator failing to post the thing, not the conjunction being out of range" —
was true about the zeros and insufficient about the margin. A run reversal needs a run posted
*earlier in the same plan* and then drawn for reversal: a conjunction two deep over eighteen
operations. Reachable, but not reliably, and a floor that passes four times in five is a floor that
teaches the reader to re-run the nightly rather than read it — the failure step 2's report spent a
page on and step 3 met again.

P7's rule sends such a floor to a targeted property, and it already had one:
`test_reversing_a_payment_run_holds_every_invariant` constructs the run and asserts all three
invariant suites **between** the reversal's legs, which is more than the machine checks even when
it does get there. The counter stays in the reach table as reporting, and the five-pass history is
recorded beside it in the source so the next reader does not have to rediscover it.

refusals: {'bank_account_currency_mismatch': 476, 'document_not_open': 10,
           'fx_revaluation_exists': 41, 'fx_revaluation_reversed': 7,
           'match_unbalanced': 28, 'payment_exceeds_open': 43,
           'payment_run_before_invoice': 3, 'reconciliation_date_order': 14,
           'reconciliation_difference': 67, 'reconciliation_open_exists': 99,
           'statement_already_imported': 40, 'statement_lines_unmatched': 1}
reach:    {'auto-match: found a tie and declined': 24, 'auto-match: matched something': 108,
           'auto-match: run': 628, 'currency rule: attempted': 476, 'lock: attempted': 121,
           'lock: attempted at a wrong balance': 67, 'lock: succeeded': 53,
           'match: attempted manually': 83, 'match: made manually': 55,
           'payment run: attempted': 124, 'payment run: posted': 68,
           'posted from a statement line': 39, 'reconciliation: opened': 467,
           'reopen: succeeded': 5, 'revaluation: attempted': 585,
           'revaluation: posted': 544, 'revaluation: reversed': 62,
           'settlement: posted': 620, 'statement: imported from a file': 263,
           'statement: keyed': 248, 'statement: perturbed by dropping a line': 46,
           'statement: perturbed by shifting a value date': 137,
           'statement: perturbed with a line the ledger lacks': 137,
           'supplier invoice: posted': 551, 'tick: made': 270,
           'unmatch: succeeded': 100, 'usd line on the base-currency account': 629}

10 passed, 1 warning in 741.11s (0:12:21)
```

Every floor clear, and every invariant suite — ledger, subledger and bank — green after every step
of 300 examples at both a 0-dp and a 2-dp base.

**And this pass settles the floor question rather than merely passing it.** `run reversal` is absent
from the reach table *altogether* — the machine did not enter the operation once — which makes the
history **0, 0, 7, 8, 2, 0**. A counter that reads 8 on one pass and nothing on the next is not a
gate, and the targeted property is what makes that a reporting fact rather than a coverage hole.

Two other readings worth naming:

* **`auto-match: found a tie and declined` at 24.** It read 1 on the previous pass and 0 at step 3,
  which is why it has a targeted property of its own. The variance is the same shape as the run
  reversal's; the difference is that its property was written at step 3 rather than argued about.
* **`statement_lines_unmatched` at 1.** Not a floor — step 2 moved it to a targeted property for
  exactly this reason, and that property is what covers it.

### Frontend

**No source changed.** `git diff main...HEAD -- frontend` is empty. The screens arrive at steps 6
to 8, so there is no e2e to re-take and no screenshot to commit — rule 13 applies from step 6, and
step 5 builds nothing a person can open.

```
$ npm --prefix frontend run test          # vitest run
 Test Files  16 passed (16)
      Tests  407 passed (407)
```

### The branch

```
$ git status --short
$ git log @{u}..
```

Both empty at the gate. `git diff --stat main...HEAD`: **10 files changed, 2864 insertions(+), 26 deletions(-)** across 10 files —
the acceptance tape and its two listings, the clause-4 fix with its regression and sensitivity
tests, the two prompt corrections, and this report.

## What step 6 inherits

Step 6 is the first UI step: Maintenance → General Ledger → **Bank accounts**, the statement-format
editor, the rules list, the Suppliers screen's *Bank details* section, and the three Defaults
accounts.

* **Every figure it renders is already proven.** The tape exercises the services end to end, so a
  screen that disagrees with it is the screen's defect and not the service's.
* **Rule 13 begins here.** From step 6 a screen is not done until a test has opened it with data
  and a committed screenshot shows rows.
* **The register carries twenty P8 lines**, eight of which are step 6's to delete — the four setup
  endpoints plus the four statement ones are steps 6 and 7's respectively.
* Nothing is withheld and nothing is open.
