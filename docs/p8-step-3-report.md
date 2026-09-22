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
cannot pay an invoice before it is raised, which is true on its own terms and not only as an
invariant defence. What step 3 did **not** do: add the same guard to `allocate()`. That would
change AR and AP behaviour this step was not asked to touch, and the decision is the owner's.

**Recommendation for the owner.** `allocations.prepare` should refuse an `allocation_date`
earlier than the latest document date in the pairs. It is a three-line guard in one place, and
the state it prevents is one the P4 suite does not currently catch. It is out of step 3's scope
and is recorded here rather than carried silently.

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

Three readings off this, before the fixes:

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

It is the most important of the four targeted properties and the only one that is a **decline**
rather than a refusal: a matcher that guessed between two candidates would put a fact in the
reconciliation nobody checked, and unlike a refusal there is no error message whose absence
anybody would notice. So it carries an explicit **anti-vacuity control**: the same construction
with one of the two lines removed must *match*. Without that, the property would pass just as
happily against a matcher that matched nothing at all.

It is deliberately **not** added to `REQUIRED_REACH`. The census fixture is module-scoped on
`test_property_banking.py`, which sorts before the targeted file, so a counter only the targeted
file increments would be read before it was written. The property failing is the gate; the
counter is reporting.

### Pass 2 — the one this gate is reported on

```
PASS_2_CENSUS
```

## Decisions worth review

1. **The clearing account was rejected.** Argued in full above. It is the one structural choice
   in decision 7 and the report records it because the final report has to.
2. **`payment_run_before_invoice` is a refusal this phase added.** Not in the prompt. It is here
   because the state it prevents breaks `assert_subledger_invariants`, which the Definition of
   Done requires green after every tape row. The deeper fix belongs to `allocate()` and is the
   owner's call — see *What the machine found*.
3. **`Idempotency-Key` on `reverse`: not implemented, and this is a deviation.** Decision 11
   lists it. `payment_runs` carries one `idempotency_key` pair and it belongs to the post; a
   second would be a migration, and step 1 owned the schema. A retried reverse cannot double-post
   — it is refused `payment_run_already_reversed` — so what a key would buy is a 200 with the
   original result instead of a 409. Step 2 shipped `reconciliation.reopen` the same way against
   the same decision, unrecorded; both are recorded here. **The owner's call** whether step 5
   adds a `*_reversal_idempotency_key` pair to both tables or the refusal is accepted.
4. **The remittance advices are read back through the banking router**, not through
   `/subledger/jobs`. Decision 11 puts them under `bank:payment_run_post`, and the generic jobs
   endpoints are gated on `ar:`/`ap:reports_view` — so a person who may post a run could not
   download its advices. The scoped read also checks the job belongs to the run being looked at.
   Two GETs, no duplication of the job machinery.
5. **The discount is taken only on a line that settles the invoice's whole remaining amount.**
   Decision 7 says to take the discount P4 computes and does not say what a *partial* line
   should do, so the choice had to be made. A settlement discount buys prompt settlement of the
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
1601 passed, 8 warnings in 1436.81s (0:23:56)
```

`migrate-check` is worth reading twice for the same reason it was at step 2: `alembic check`
comes back clean **without a new revision**, which is this step's claim that it added no schema.

`main` at `33c8470` runs 1 563. The 38 this step adds:

| | |
|---|---|
| `tests/banking/test_payment_runs.py` | 27 |
| `tests/banking/test_api.py` | 4 new (22 total) |
| `tests/banking/test_invariant_sensitivity.py` | 4 new for clause 8, 1 deleted → +3 (21 total) |
| `tests/test_api_has_a_caller.py` — parametrized, one case per endpoint | 3 |
| `tests/banking/test_property_targeted_refusals.py` | 1 new (5 total) |
| | **38** |

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

BRANCH_STAT

## What step 4 inherits

Nothing withheld. The two open questions are for the owner rather than for step 4:

* **`allocate()` and a backdated allocation** — finding 1 above. Step 4 touches revaluation and
  will not go near it, but it is live in AR and AP today.
* **`Idempotency-Key` on the two reversal/withdrawal paths** — decision 3 above, `reverse_run`
  and `reconciliation.reopen` together.

Step 4's own inheritance is ordinary: clause 8 is asserted after every machine step, so a
revaluation that touched a bank account's own lines would now break it as well as clause 4.
