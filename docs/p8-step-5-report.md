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

TAPE_TABLE

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
GATES
```

CENSUS

### Frontend

**No source changed.** `git diff main...HEAD -- frontend` is empty. The screens arrive at steps 6
to 8, so there is no e2e to re-take and no screenshot to commit — rule 13 applies from step 6, and
step 5 builds nothing a person can open.

FRONTEND_CHECKS

### The branch

```
$ git status --short
$ git log @{u}..
```

BRANCH_STAT

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
