# P8 step 3 — payment runs and supplier bank details

The STOP-gate report. `main` at `33c8470`, which carries P8 step 2 (PR #65).

Step 2 handed over three things and this step closes all three: the `payment_run` auto-match
rule was written and could find nothing, invariant clause 8 was declared and absent, and
`payment_exceeds_open` stood in the census with no runs to provoke it. The runs are here, the
rule matches one bank line to three ledger lines, clause 8 asserts, and
`test_clause_8_is_not_written_yet` is deleted.

## The schema

**None.** Step 1's `0027_p8_banking` created `payment_runs` and `payment_run_lines` with every
column decision 7 names, and added `partners.bank_name`, `bank_account_number` and
`bank_account_holder`. Nothing here needed a column it did not already have; `alembic check` is
clean without a new revision.

The three `partners` columns had **existed since step 1 and nothing could write them** —
`PartnerInput` did not carry them and neither did the create or the patch. That is closed here
(see *Supplier bank details* below), which is the difference between a column and a field.

## The reading of "batch supplier payments → single bank line"

Decision 7 asks for this to be recorded in the report, so here it is in full.

The run posts **one settlement per supplier** through `post_document()` (kind settlement,
`cash_account_id` = the bank account's GL account, `instrument_type = bank`, `reference` = the
run's number, amount = Σ of that supplier's lines net of discount) and **one allocation per
supplier** through `allocate()` against the selected invoices, all in one transaction.

So every payment is an ordinary P4 document. It has its own `PMT-` number, its own journal
entry, its own open item, its own reversal, and the AP subledger sees nothing it has not seen
since P4. Nothing in `app/banking/` writes a journal line, and
`tests/banking/test_boundary.py` reads the import graph to say so rather than trusting this
paragraph.

The **single bank line is the statement's**, not the ledger's. A bulk transfer shows at the bank
as one debit carrying the run's reference, and decision 4's `payment_run` rule matches that one
line to the run's N ledger lines — one match with N journal members, which balances by
construction because the run's total is Σ of its settlements.

**The clearing account was considered and rejected.** Post the N settlements to a clearing
account, then one bank entry for the total, and the ledger would hold a literal single line on
the bank account. The price is an account, a control type and a kernel event, bought to restate
a fact the bank already states — and it is not even always the right restatement: a bank that
shows one line per beneficiary (some do, for domestic transfers) would then need the reverse
mapping, from one clearing entry back to N statement lines, which is the harder direction. The
statement is the bank's record of the ledger; making the ledger imitate the statement's shape is
the wrong way round.

## What landed

| | |
|---|---|
| `app/banking/payment_runs.py` | the selection, `plan()`, `post_run()`, `reverse_run()`, the instruction file |
| `app/banking/remittance.py` | the `remittance_pdf` job, one advice per supplier |
| `app/api/v1/banking.py` | nine routes — selectable, list, detail, preview, post, reverse, the instruction file, and the advices (list + download) |
| `app/subledger/documents.py` | `payment_run_member` on the document path, and `in_payment_run` |
| `app/subledger/masters.py` + schemas + API | supplier bank details |
| `app/banking/matching.py` | `assert_unmatchable`, extracted from `unmatch` |
| `app/kernel/events.py` | the "P8 adds no event, and that is the decision" note decision 11 asks for |
| `tests/banking/invariants.py` | clause 8 |
| `tests/banking/test_payment_runs.py` | 27 tests on the tape's row 4, with its literals |
| `tests/banking/test_property_banking.py` | three operations, three census floors, two generator fixes |
| `tests/banking/test_property_targeted_refusals.py` | the auto-match tie |

### `plan()` is separate from `post_run()`, and that is the whole design

A run posts N settlements and N allocations. A refusal discovered on the third supplier leaves
two suppliers paid and a `PYR-` number claimed for a run that does not exist — and an auditor
following that series finds a hole nobody can explain. So `plan()` raises **every** refusal the
run is going to raise, reads nothing it will not read again, and writes nothing; `post_run()`
calls it first and claims the number afterwards. `test_paying_more_than_is_open_is_refused_and_claims_no_number`
asserts the second half by posting the run successfully afterwards and getting `PYR-000001`.

`plan()` is also the preview the screen shows, which is deliberate: a preview computed by a
different function from the posting is a preview that can be wrong.

### The discount is P4's, taken at P4's word

`discount_available` per line is `allocations.max_discount(document, on=payment_date, currency)`
— P4's own computation, unchanged and not reimplemented. It is taken by default and may be
declined per line, which is all `take_discount` does; the preview keeps reporting what was
*available* either way, so the screen can show what a declined discount gave up.

The discount is a fact of the **payment date**, so a selection listed for the 10th and posted on
the 30th offers nothing. `test_the_discount_window_is_a_fact_of_the_payment_date` is that.

It is taken only on a line that settles the invoice's **whole remaining amount** — decision 5
below has the reasoning, and the short version is that the other rule can post a settlement of
zero cash after the number is claimed.

**`RunLineInput.amount` is gross**, not the cash. A line settling 100 000 with a 2 000 discount
pays 98 000 and closes the invoice; keeping the input gross is what makes `payment_exceeds_open`
a statement about the invoice rather than about the discount, and `None` means "the document's
whole open amount" — the figure the screen put on the row.

### Reversal is fallible-leg-first, and the ordering is proven at the service level

`unallocate()` every allocation, then `reverse_document()` every settlement, then release the
match, then mark the run `reversed`. P4's `reverse_document` refuses `document_allocated` while
any allocation survives, so the other order posts N reversals and then refuses — leaving
settlements reversed in the ledger and their allocations standing, by exactly the run's value.
That is the same defect P6's property machine found on the stock companion and the same rule
P4's own `reverse_document` follows for it.

P6's rule is that an ordering which matters is **proven**, not described. Two tests:

* `test_reversing_a_settlement_before_unallocating_it_is_refused` does the other order at the
  service level and gets `document_allocated`.
* `test_the_reversal_refuses_before_it_writes` refuses a reversal on a locked reconciliation and
  then asserts every invoice still closed, the bank balance unmoved and the match still standing.

The refusals that belong to the **run** come before both legs. A run whose bank line sits inside
a locked reconciliation is not reversible (`reconciliation_locked` — reopen it first), and
finding that out after N unallocations would leave the run half undone. `matching.unmatch`'s
single guard is extracted as `assert_unmatchable` for exactly this: the question is asked for
every match the reversal will release before the first write.

A run whose statement line is matched and **not** locked is reversible, and the reversal releases
the match — the statement line is a line nobody has explained again, and the reversing entries
are outstanding until the bank returns the money or the accountant posts what happened.

### `payment_run_member`, on the document path

A member settlement reversed on its own from `/ap/documents/{id}` is refused: a bulk transfer is
one banking act, the bank shows one line either way, and reversing one beneficiary out of it
leaves a run whose total no longer equals its members (clause 8) and a statement line that can
never be matched again.

Two choices in it worth naming:

* **It lives in `reverse_document`, not in the endpoint.** The register's whole lesson is that a
  guard on one caller is a guard somebody else walks round; the service is the document path.
* **It sits above `document_allocated`.** Every member of a posted run is allocated, so
  underneath it the generic refusal would always win and "reverse the run" would never be said.
* It reads `app.models.banking` rather than `app.banking`. The question is "is there a row", and
  a service import would tie the subledger to the banking package for one `SELECT`.

The run's own reversal passes `in_payment_run=True`. A keyword rather than a state test, because
the run flips to `reversed` **last** — after the settlements it is reversing — so a guard keyed
on the run's status would block the run itself.

### Supplier bank details

`bank_name`, `bank_account_number`, `bank_account_holder` on `partners` rather than on the AP
role settings: a partner who is both a customer and a supplier banks at one bank, and a refund
to a customer will want the same three fields when one is ever built.

Cleared by **one** `clear_bank_details` flag rather than three. Bank details are one fact — a
supplier who changed banks and whose new account number has not arrived yet has no bank details,
not a stale bank name and a blank number — and the instruction file would print that pair
without noticing.

### The instruction file

`beneficiary, bank, account number, amount, currency, reference, supplier code`, one row per
**beneficiary**, RFC 4180 endings, `Decimal` written by `str` — the same rules as the VAT
annexes.

A supplier with no bank details still gets a row, with the account fields empty. That is
decision 7's instruction and it is right: the payment was posted, and hiding the row would hide
the one beneficiary the accountant has to key into the portal by hand.

Amounts are quantised to the **currency's** decimal places, not the column's. `NUMERIC(20,6)`
renders a franc as `236000.000000`, which some bank portals reject and others read as cents.

## The refusal table

| code | where | test |
|---|---|---|
| `payment_run_needs_bank` | `payment_runs.plan` | `test_a_run_from_a_cash_account_is_refused` |
| `payment_run_empty` | `payment_runs.plan` | — (the schema's `min_length=1` catches it over HTTP) |
| `payment_run_not_an_invoice` | `payment_runs.plan` | — |
| `payment_run_currency_mismatch` | `payment_runs.plan` | `test_an_invoice_in_another_currency_is_not_selectable` |
| `payment_run_before_invoice` | `payment_runs.plan` | `test_a_run_cannot_be_dated_before_an_invoice_it_pays` |
| `document_not_open` | `payment_runs.plan` | `test_an_invoice_settled_meanwhile_fails_the_run` |
| `payment_exceeds_open` | `payment_runs.plan` | `test_paying_more_than_is_open_is_refused_and_claims_no_number`, `test_a_run_naming_the_same_invoice_twice_is_refused_before_it_writes` |
| `invalid_amount` | `payment_runs.plan` | — |
| `reconciliation_locked` | `payment_runs.reverse_run` via `matching.assert_unmatchable` | `test_a_run_inside_a_locked_reconciliation_is_not_reversible`, `test_the_reversal_refuses_before_it_writes` |
| `payment_run_already_reversed` | `payment_runs.reverse_run` | `test_reversing_a_run_twice_is_refused` |
| `payment_run_member` | `documents.reverse_document` | `test_a_member_settlement_cannot_be_reversed_on_its_own`, `test_reversing_a_member_settlement_over_http_is_refused` |
| `document_allocated` | `documents.reverse_document` (P4's, unchanged) | `test_reversing_a_settlement_before_unallocating_it_is_refused` — the ordering proof |

`bank_details_missing` and `open_credits: …` are **warnings, not refusals**, and appear on the
preview per supplier. The payment is owed either way, and netting an open credit into a payment
is P4's allocation screen's job, not this one's.

## What the machine found

Two things, and one of them is not ours.

### 1. A payment dated before the invoice it pays breaks the subledger invariant — and it is P4's

The first deep pass failed with:

```
AP control account 15736 is 1000.000000 as of 2026-09-01 but open items total 0
```

The plan posted a supplier invoice dated 19 September and a payment run dated 1 September that
paid it. Allocating a payment dated before its invoice puts the payment on the AP control
account on a date the invoice is not on it yet, so the control account and the open items
disagree at every date between the two — clause 1 of `assert_subledger_invariants`.

**This is not a payment-run defect.** Reduced to P4 alone — `post_document` a settlement dated
1 September, `allocate` it to an invoice dated 19 September, no banking code anywhere — the same
clause fails identically. It is reachable today through the AR/AP screens: post a receipt dated
last month and allocate it to this month's invoice.

What step 3 did: **refuse it on the path step 3 built.** `payment_run_before_invoice` — a run
cannot pay an invoice before it is raised. What step 3 did **not** do: add the same guard to
`allocate()`. That would change AR and AP behaviour this step was not asked to touch.

### Ruled at the gate, and the diagnosis was sharpened

The owner ruled on this, and corrected the framing. **A payment dated before the invoice is
ordinary business** — it is a deposit, and it must stay legal. What is illegal is *allocating* it
on a date the invoice was not yet posted, which is what makes the control account and the open
items disagree in between. So:

* **The P4 fix is `allocate()` refusing an `allocation_date` earlier than either document's
  `document_date`** — `allocation_before_document`, on both the `allocate()` and the
  auto-allocate paths, with the reduced P4-only reproduction above as its sensitivity test. It
  ships as **its own PR off `main`**, touching no P8 file, before step 4.
* **`payment_run_before_invoice` stays.** It is a different rule at a different level — a run
  pays invoices that exist at its payment date — and the two are kept because neither implies the
  other: the P4 guard would not stop a run being *dated* before an invoice it does not pay, and
  the run guard says nothing about a hand-keyed allocation.

### 2. `reconciliation_difference` and `lock: attempted at a wrong balance` fell below their floors

The first deep pass read `lock: attempted at a wrong balance` at **2** against a floor of 3 —
over **116** lock attempts, roughly a third of which should have keyed a wrong balance.

The cause was step 2's `wrong_by=amount if pick % 3 == 0 else ZERO`. Deriving one decision from
another draw's arithmetic makes its frequency a property of Hypothesis's shrinking rather than of
the generator, and the census cannot tell that from a guard that stopped firing. The plan tuple
gained its own boolean draw. This is the same shape as step 2's own finding — where keying only
the closing figure made `reconciliation_difference` read zero — one level further in.

## The census

### Pass 1 — the one that found both defects above

```
refusals: {'bank_account_currency_mismatch': 79, 'match_unbalanced': 6, 'not_found': 8,
           'payment_exceeds_open': 19, 'reconciliation_difference': 2,
           'reconciliation_open_exists': 14, 'statement_already_imported': 9,
           'statement_lines_unmatched': 2}
reach:    {'auto-match: run': 248, 'currency rule: attempted': 79, 'lock: attempted': 116,
           'lock: attempted at a wrong balance': 2, 'lock: succeeded': 112,
           'match: attempted manually': 10, 'match: made manually': 4,
           'payment run: attempted': 329, 'payment run: posted': 302,
           'posted from a statement line': 3, 'reconciliation: opened': 159,
           'settlement: posted': 113, 'statement: imported from a file': 29,
           'statement: keyed': 30, 'statement: perturbed by dropping a line': 3,
           'statement: perturbed by shifting a value date': 8,
           'statement: perturbed with a line the ledger lacks': 10,
           'supplier invoice: posted': 449, 'tick: made': 48, 'unmatch: succeeded': 3,
           'usd line on the base-currency account': 83}
```

Four readings off this, before the fixes:

* **`statement_already_imported` reached 9.** Step 2's note said the file-hash refusal was
  unreachable because the machine keys its statements line by line. That was true of
  `import_manual` and is no longer true: `import_statement_file` writes the generated statement
  out as a `generic` CSV and imports *those bytes*, and one draw in three re-offers the previous
  run's bytes — a user finding last month's export in their downloads folder, which is the whole
  of what the refusal is for. The parser-test objection still stands for the dates and the
  amounts, which are generated from the ledger rather than drawn; what needs a file is a hash.
* **`payment_exceeds_open` reached 19**, comfortably. It is one drawn operation away, which is
  what this machine is good at.
* **`document_not_open` reached 0**, with 302 runs posted — so it is not the guard. It needs two
  runs in one plan colliding on one invoice, which is a conjunction; 449 invoices to 302 runs
  means a plan usually holds one run and never revisits an invoice. The generator now names an
  already-paid invoice one draw in three, which is the same fix `_lock`'s wrong balance had at
  step 2 and for the same reason: the machine should *try* the thing rather than wait to stumble
  into it.
* **`auto-match: found a tie and declined` is absent entirely** — 7 at step 2, 0 here. The plan
  gained three operations, so every other one is drawn less often and the narrow window in which
  two equal unmatched ledger lines sit inside one statement line's ±3 days closed. Nothing about
  the matcher changed, which is exactly the case P7's rule is written for.

### The tie moved to a targeted property

`test_auto_match_declines_a_tie_and_leaves_both_candidates` builds the conjunction — two ledger
lines of the same amount within the window, one statement line naming neither — and draws over
its shape (the amount, the date, the 0-to-3-day gap). It asserts the line is left alone and both
candidates reported.

It is the most important property in that file and the only one that is a **decline** rather
than a refusal: a matcher that guessed between two candidates would put a fact in the
reconciliation nobody checked, and unlike a refusal there is no error message whose absence
anybody would notice. So it carries an explicit **anti-vacuity control**: the same construction
with one of the two lines removed must *match*. Without that, the property would pass just as
happily against a matcher that matched nothing at all.

It is deliberately **not** added to `REQUIRED_REACH`. The census fixture is module-scoped on
`test_property_banking.py`, which sorts before the targeted file, so a counter only the targeted
file increments would be read before it was written. The property failing is the gate; the
counter is reporting.

### Pass 2 — the fixes worked, and one of them broke something else

```
refusals: {'bank_account_currency_mismatch': 404, 'document_not_open': 1,
           'match_unbalanced': 14, 'not_found': 41, 'payment_exceeds_open': 44,
           'payment_run_before_invoice': 40, 'reconciliation_date_order': 8,
           'reconciliation_difference': 27, 'reconciliation_open_exists': 73,
           'statement_already_imported': 24, 'statement_lines_unmatched': 10}
reach:    {'auto-match: found a tie and declined': 3, 'auto-match: matched something': 27,
           'auto-match: run': 368, 'currency rule: attempted': 404, 'lock: attempted': 70,
           'lock: attempted at a wrong balance': 32, 'lock: succeeded': 33,
           'match: attempted manually': 47, 'match: made manually': 33,
           'payment run: attempted': 134, 'payment run: posted': 8,
           'posted from a statement line': 8, 'reconciliation: opened': 322,
           'reopen: succeeded': 7, 'settlement: posted': 391,
           'statement: imported from a file': 161, 'statement: keyed': 152,
           'statement: perturbed by dropping a line': 27,
           'statement: perturbed by shifting a value date': 38,
           'statement: perturbed with a line the ledger lacks': 50,
           'supplier invoice: posted': 405, 'tick: made': 155, 'unmatch: succeeded': 36,
           'usd line on the base-currency account': 381}

the deep pass did not provoke {'document_not_open': 1} at least 3 times each.
```

Every invariant held over 300 examples at both scales. The two generator fixes did what they
were meant to — `lock: attempted at a wrong balance` went 2 → **32** and
`reconciliation_difference` 2 → **27** — and `auto-match: found a tie and declined` is back at 3
from the targeted property.

**And `payment_run_before_invoice` — the refusal this step added — starved the generator.** 134
run attempts, 40 of them refused for the date, and **eight** runs posted. Two things follow from
eight:

* `document_not_open` needs a *posted* run to have closed an invoice, so it reached 1. The
  one-in-three already-paid draw added after pass 1 had almost nothing to draw from.
* **`reverse_run` never ran once in 300 examples.** `run reversal: attempted` is absent from the
  reach table entirely — `state["runs"]` only fills on a posted run. The most important new path
  in the step, and the machine had not touched it.

That is a generator defect and not a guard: the screen defaults the payment date to today and
the invoices are already raised, so a run dated *before* one of them is the unusual case. The
generator now dates the run at or after the latest invoice it pays, three draws in four; the
fourth stays deliberately backdated so the refusal keeps appearing in the census rather than
going quiet the moment it stopped being an accident.

`run reversal: succeeded` is added to `REQUIRED_REACH` at the same time. It is two preconditions
deep — a run must post, then be drawn for reversal — and it is the path that unallocates N
allocations and reverses N settlements, so a zero there means the invariant suite never saw the
state between those legs. Reading the number was what found this; gating it is what stops the
next step losing it silently.

### Pass 3 — the new floor catches the thing it was added for

```
refusals: {'bank_account_currency_mismatch': 381, 'document_not_open': 5,
           'match_unbalanced': 8, 'not_found': 24, 'payment_exceeds_open': 9,
           'payment_run_before_invoice': 8, 'reconciliation_difference': 21,
           'reconciliation_open_exists': 66, 'statement_already_imported': 7}
reach:    {'auto-match: found a tie and declined': 3, 'auto-match: matched something': 29,
           'auto-match: run': 442, 'currency rule: attempted': 381, 'lock: attempted': 46,
           'lock: attempted at a wrong balance': 21, 'lock: succeeded': 25,
           'match: attempted manually': 44, 'match: made manually': 36,
           'payment run: attempted': 70, 'payment run: posted': 24,
           'posted from a statement line': 10, 'reconciliation: opened': 267,
           'reopen: succeeded': 10, 'settlement: posted': 366,
           'statement: imported from a file': 111, 'statement: keyed': 61,
           'statement: perturbed by dropping a line': 8,
           'statement: perturbed by shifting a value date': 65,
           'statement: perturbed with a line the ledger lacks': 35,
           'supplier invoice: posted': 316, 'tick: made': 98, 'unmatch: succeeded': 12,
           'usd line on the base-currency account': 403}

the deep pass did not reach {'run reversal: succeeded': 0} at least 3 times each.
```

**Every refusal floor cleared** — `document_not_open` 1 → 5, so the date fix worked, and
`payment_run_before_invoice` settled at 8, so the deliberate backdated draw keeps it counted
without starving the runs.

**And the floor added at pass 2 failed on its first outing, which is the best thing that happened
to this step's census.** `run reversal: succeeded` is 0 — and `run reversal: attempted` is absent
from the reach table *altogether*, so the operation was never even entered. `state["runs"]` is
empty unless a run posted earlier in the same plan, and 24 runs posted across 300 examples.

A run reversal is therefore a conjunction **two operations deep**, and P7's rule sends that to a
targeted property rather than to a bigger `max_examples`. Had the counter not been gated at pass
2, this step would have shipped with its most important new path — N unallocations and N
document reversals — never once exercised by the machine, and the census would have looked green.

Three changes followed, and **the first diagnosis of the three was wrong** — pass 4 is what
corrected it, which is the part of this worth reading:

* **`not_found` was 24 of 70 run attempts** — ids the session rollback had taken away, still
  named in `state["invoices"]`. Pruned before the draw. A screen only ever offers documents that
  exist, so this makes the generator more like the product and not less.
* **`test_reversing_a_payment_run_holds_every_invariant`** in the targeted file: it builds the
  run and asserts all three invariant suites **between** the reversal's legs, which is what the
  machine would have contributed rather than what the service tests already do, and draws over
  both invoice amounts, the date, and whether the bank line was matched first — the case where
  the reversal also has to release the match.
* **The tie property was building two full company seeds per draw.** Its anti-vacuity control
  does not vary with the amount or the date, so drawing it 300 times bought nothing and made the
  property the slowest thing in the deep run. It is one example now, and deliberately *not*
  marked `slow`, so the per-commit suite runs it: a control only the nightly sees is a control
  that can rot for a day.

`run reversal: succeeded` was taken *out* of `REQUIRED_REACH` at this point, on the reading that a
reversal is a conjunction the generator cannot reach. **Pass 4 showed that reading was wrong.**
With the stale ids pruned, posted runs went 24 → 41 and the machine reached the reversal **7**
times — so the cause of the zero was the generator failing to post the thing, not the conjunction
being out of range, and that is precisely the mistake step 2's report warns against making in the
other direction about `reconciliation_difference`. The floor is restored, and the targeted
property is kept: seven against three is a thinner margin than the rest of the list, and the
property is what makes a dip below it a question about the generator rather than a hole in the
coverage.

### Pass 4 — the floor was wrongly removed, and this is what said so

```
refusals: {'bank_account_currency_mismatch': 463, 'document_not_open': 6,
           'match_unbalanced': 14, 'payment_exceeds_open': 18,
           'payment_run_before_invoice': 3, 'reconciliation_date_order': 1,
           'reconciliation_difference': 28, 'reconciliation_open_exists': 47,
           'statement_already_imported': 13, 'statement_lines_unmatched': 22}
reach:    {'auto-match: found a tie and declined': 1, 'auto-match: matched something': 49,
           'auto-match: run': 442, 'currency rule: attempted': 463, 'lock: attempted': 80,
           'lock: attempted at a wrong balance': 43, 'lock: succeeded': 30,
           'match: attempted manually': 22, 'match: made manually': 8,
           'payment run: attempted': 68, 'payment run: posted': 41,
           'posted from a statement line': 29, 'reconciliation: opened': 310,
           'run reversal: attempted': 7, 'run reversal: succeeded': 7,
           'settlement: posted': 350, 'statement: imported from a file': 171,
           'statement: keyed': 191, 'statement: perturbed by dropping a line': 51,
           'statement: perturbed by shifting a value date': 49,
           'statement: perturbed with a line the ledger lacks': 83,
           'supplier invoice: posted': 374, 'tick: made': 171, 'unmatch: succeeded': 49,
           'usd line on the base-currency account': 437}

10 passed, 1 warning in 632.63s (0:10:32)
```

Green, and two numbers in it are the interesting ones.

**`not_found` is absent from the refusal census entirely** — it was 24. Every one of those was a
wasted draw, and giving them back is why `payment_exceeds_open` roughly doubled (9 → 18) and
`statement_lines_unmatched` went 10 → 22.

**`run reversal: attempted` and `succeeded` both read 7.** The machine gets there now, so the
floor removed after pass 3 goes back — see above. Note these 7 are the *machine's*: the census
prints at the machine module's teardown, which is before the targeted file runs, so the targeted
property's own count is not in this table.

`auto-match: found a tie and declined` reads 1 here for the same ordering reason and is not a
gated floor; the tie's cover is its property, which either passes or fails.

### Pass 5 — a *step-2* floor reads zero, and that is the real finding

```
refusals: {'bank_account_currency_mismatch': 427, 'match_unbalanced': 12,
           'payment_exceeds_open': 13, 'payment_run_before_invoice': 17,
           'reconciliation_date_order': 3, 'reconciliation_difference': 27,
           'reconciliation_open_exists': 102, 'statement_already_imported': 11,
           'statement_lines_unmatched': 1}
reach:    {..., 'payment run: posted': 13, 'posted from a statement line': 0,
           'run reversal: attempted': 4, 'run reversal: succeeded': 4, ...}

the deep pass did not reach {'posted from a statement line': 0} at least 3 times each.
```

`run reversal: succeeded` held at 4, so restoring the floor was right. But
`document_not_open` is **absent** — it read 5 and 6 on the two previous passes — and
`posted from a statement line`, which is a **step-2** floor with step-2 code behind it, read
**0** against 3, 8, 10 and 29 on the four passes before it.

Put the five passes side by side and the cause stops being any one guard:

| counter | pass 1 | pass 2 | pass 3 | pass 4 | pass 5 |
|---|---|---|---|---|---|
| `posted from a statement line` | 3 | 8 | 10 | 29 | **0** |
| `document_not_open` | 0 | 1 | 5 | 6 | **0** |
| `run reversal: succeeded` | — | 0 | 0 | 7 | 4 |
| `payment run: posted` | 302 | 8 | 24 | 41 | 13 |

Same guards, same code; what changed is how often a plan reaches them. **And the cause is this
step.** `OPERATIONS` went from 14 to 18 — a supplier invoice, a payment run, its reversal, and
the file-import path splitting off — while the plan kept step 2's 6-22 draws. Every operation is
drawn proportionally less often, and the marginal counters started flipping between passes. A
floor that flips is worse than no floor: it trains the reader to re-run the nightly rather than to
read it, which is the failure step 2's report spent a page on.

Three changes, root cause first: the plan draws **10-30** steps rather than 6-22; **both**
machines close the fee loop after the plan rather than only the 0-dp one (and a fee at a
two-decimal base rounds where the 0-dp one cannot, so it earns its place rather than only doubling
a count); and the already-paid-invoice draw goes from one in three to one in two, which is
`document_not_open`'s only route.

### Pass 6 — the one this gate is reported on

```
refusals: {'bank_account_currency_mismatch': 658, 'document_not_open': 13,
           'match_unbalanced': 21, 'not_found': 56, 'payment_exceeds_open': 57,
           'payment_run_already_reversed': 7, 'payment_run_before_invoice': 24,
           'reconciliation_difference': 51, 'reconciliation_open_exists': 150,
           'statement_already_imported': 37, 'statement_lines_unmatched': 9}
reach:    {'auto-match: found a tie and declined': 1, 'auto-match: matched something': 63,
           'auto-match: run': 664, 'currency rule: attempted': 658, 'lock: attempted': 132,
           'lock: attempted at a wrong balance': 55, 'lock: succeeded': 72,
           'match: attempted manually': 72, 'match: made manually': 51,
           'payment run: attempted': 173, 'payment run: posted': 64,
           'posted from a statement line': 25, 'reconciliation: opened': 553,
           'reopen: succeeded': 29, 'run reversal: attempted': 75,
           'run reversal: succeeded': 27, 'settlement: posted': 576,
           'statement: imported from a file': 280, 'statement: keyed': 303,
           'statement: perturbed by dropping a line': 20,
           'statement: perturbed by shifting a value date': 155,
           'statement: perturbed with a line the ledger lacks': 102,
           'supplier invoice: posted': 613, 'tick: made': 281, 'unmatch: succeeded': 70,
           'usd line on the base-currency account': 594}

10 passed, 1 warning in 979.39s (0:16:19)
```

Green, and — the point of the five passes before it — green **with a margin** rather than green
by luck:

| floor (3 at the deep profile) | pass 5 | pass 6 |
|---|---|---|
| `document_not_open` | 0 | **13** |
| `payment_exceeds_open` | 13 | **57** |
| `statement_already_imported` | 11 | **37** |
| `reconciliation_difference` | 27 | **51** |
| `bank_account_currency_mismatch` | 427 | **658** |
| `posted from a statement line` (reach) | 0 | **25** |
| `run reversal: succeeded` (reach) | 4 | **27** |
| `lock: attempted at a wrong balance` (reach) | 27 | **55** |

The thinnest margin in the table is now about eight times its floor. Sixteen minutes rather than
ten, which is what a longer plan costs the nightly and is the right trade for a census that means
something.

Two entries in it worth naming:

* **`payment_run_already_reversed` at 7** — the machine draws `reverse_run` on a run it has
  already reversed, so `reverse_run`'s own front-door refusal is exercised rather than assumed.
* **`not_found` at 56 of 173 run attempts.** `state["invoices"]` is pruned of ids the session
  rollback took away; `state["paid"]` is not, so a stale paid id is a skipped step instead of a
  `document_not_open`. Harmless — it is a counted skip, and the floors clear with margin either
  way — and pruning it the same way is a small generator improvement left named for step 4 rather
  than churned in at a gate.

## Decisions worth review

1. **The clearing account was rejected.** Argued in full above. It is the one structural choice
   in decision 7 and the report records it because the final report has to.
2. **`payment_run_before_invoice` stays, and the P4 defect is fixed separately — ruled.** This
   refusal is not in the prompt; it is here because the state it prevents breaks
   `assert_subledger_invariants`. The owner ruled at this gate that it keeps its place as a rule
   of its own — *a run pays invoices that exist at its payment date* — and that the underlying
   P4 defect is a different rule in a different place. See *What the machine found*.
3. **`Idempotency-Key` on `reverse`: not implemented — accepted as a plan deviation.** Decision
   11 lists it. `payment_runs` carries one `idempotency_key` pair and it belongs to the post; a
   second would be a migration, and step 1 owned the schema. A retried reverse cannot double-post
   — it is refused `payment_run_already_reversed` — so what a key would buy is a 200 with the
   original result instead of a 409.

   Step 2 shipped `reconciliation.reopen` the same way against the same decision, unrecorded.
   **Ruled at this gate: no column for either.** Both are refused on a second press by state —
   `payment_run_already_reversed`, and `reconciliation_not_latest` / already-open for reopen — so
   a double-click is harmless. Carried to `docs/p8-final-report.md`'s deviations list.
4. **The remittance advices are read back through the banking router**, not through
   `/subledger/jobs`. Decision 11 puts them under `bank:payment_run_post`, and the generic jobs
   endpoints are gated on `ar:`/`ap:reports_view` — so a person who may post a run could not
   download its advices. The scoped read also checks the job belongs to the run being looked at.
   Two GETs, no duplication of the job machinery.
5. **The discount is taken only on a line that settles the invoice's whole remaining amount —
   ruled, and this is the reading of decision 7.** Decision 7 says to take the discount P4
   computes and does not say what a *partial* line should do, so the choice had to be made.
   Accepted at this gate as the phase's reading, the owner noting it is also Evolution's
   behaviour. A settlement discount buys prompt settlement of the
   account, not a percentage off an instalment — and the other candidate, capping P4's figure at
   the line's amount, is wrong twice over: 1 000 paid against a 100 000 invoice at 2/10 would
   claim a 1 000 discount and post a settlement of **zero cash**, which `post_document` refuses
   after the `PYR-` number was claimed. That is the one thing `plan()` exists to prevent.
   `discount_available` is still reported on every line, so the screen can say "pay it in full
   and save 2 000" rather than going quiet. "In full" is the invoice's **remaining** amount, so
   an invoice part paid outside the run still qualifies —
   `test_settling_the_last_of_a_part_paid_invoice_takes_the_discount`.
6. **`plan()` tracks what is left within the run.** A selection naming one invoice twice — two
   rows open on it, a retry that appended rather than replaced — passes the plain reading, and
   P4 then refuses halfway through the posting with settlements already written. The refusal
   belongs with the others, before the first write.

## The register

**Twenty** `GAP (P8, step …)` lines: step 1's eight, step 2's nine for the workspace, and three
here for the payment runs — preview, post and reverse, all one screen (Transactions → Accounts
Payable → Payment runs at step 7). Every one is driven over HTTP by `tests/banking/test_api.py`
in the meantime, for the reason the register exists.

The instruction file and the remittance advices are GETs and so are not the register's business,
but they are the same screen's buttons and are driven over HTTP too.

The P1–P7 entries are whatever `main` carries; none was changed.

## Gates

```
$ docker compose exec -T backend uv run ruff check .
All checks passed!

$ make migrate-check
migrate-check: upgrade-from-zero, alembic check and downgrade-to-base all green

$ make be-test                 # docker compose exec -T backend uv run pytest -n 4 -q
1603 passed, 8 warnings in 1444.71s (0:24:04)
```

`migrate-check` is worth reading twice for the same reason it was at step 2: `alembic check`
comes back clean **without a new revision**, which is this step's claim that it added no schema.

`main` at `33c8470` runs 1 563. The 40 this step adds:

| | |
|---|---|
| `tests/banking/test_payment_runs.py` | 27 |
| `tests/banking/test_api.py` | 4 new (22 total) |
| `tests/banking/test_invariant_sensitivity.py` | 4 new for clause 8, 1 deleted → +3 (21 total) |
| `tests/test_api_has_a_caller.py` — parametrized, one case per endpoint | 3 |
| `tests/banking/test_property_targeted_refusals.py` | 3 new — the tie, its control, the run reversal (7 total) |
| | **40** |

Nothing deselected, nothing skipped.

**Eight warnings rather than `main`'s seven**, and the extra one is worth a line: it is
WeasyPrint's `HarfBuzz-Subset` deprecation, raised by
`tests/banking/test_api.py::test_a_payment_run_is_previewed_posted_and_reversed_over_http`. That
is the remittance job **actually rendering a PDF** — `TestClient` runs the background task the
endpoint queues — so the advice path is exercised end to end over HTTP, WeasyPrint included,
rather than only as HTML. The other seven are the ones `main` already carries.

The **deep** profile is not part of `be-test`: the per-commit profile draws two examples, so the
census stays silent there and the nightly is where it is enforced. Both deep runs are quoted in
full above.

### Frontend

**No source changed.** `git diff main...HEAD -- frontend` is empty: this step builds services and
endpoints, and the screens arrive at step 7. Rule 13 applies from step 6, so there is no e2e to
re-take and no screenshot to commit — step 3 builds nothing a person can open.

`PaymentRunStatus` was already in the generated `frontend/src/lib/api-enums.ts` from step 1 and
no new enum crosses the API this step, so nothing needed regenerating;
`tests/test_api_enums_export.py` is green in the run above, which is what proves it.

```
$ cd frontend && npm run test          # vitest run
 Test Files  16 passed (16)
      Tests  407 passed (407)
```

Run locally because the guards are cheap and this step changes the AP partner wire shape;
`next build` and the Playwright set are CI's, and there is nothing this step could have broken in
either — no frontend file changed.

### The branch

```
$ git status --short
$ git log @{u}..
```

Both empty at the gate. `git diff --stat main...HEAD`: **20 files, 3 931 insertions, 75
deletions** — three new source files (the payment-run service, the remittance job, its test
suite), this report, and the rest edited in place.

## What step 4 inherits

Nothing withheld, and nothing open. All three questions this report raised were ruled at the
gate:

* **The P4 backdated-allocation defect** ships as its own PR off `main` before step 4 —
  `allocation_before_document` on both allocate paths. It touches no P8 file, so step 4 starts
  from a `main` that already carries it.
* **No idempotency column** for `reverse_run` or `reconciliation.reopen`; state refusals suffice.
  Recorded as a deviation, carried to the final report.
* **The discount on full settlement only** is the phase's reading of decision 7.

One small generator improvement is named rather than done: `state["paid"]` is not pruned of ids
the session rollback took away, so 56 of pass 6's 173 run attempts were counted skips rather than
real draws. Every floor clears with margin either way.

Step 4's own inheritance is ordinary: clause 8 is asserted after every machine step, so a
revaluation that touched a bank account's own lines would now break it as well as clause 4.
