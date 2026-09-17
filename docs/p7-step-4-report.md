# P7 step 4 — the VAT return, filing, X/Z, FX revaluation

Step 4 is not a gate, but it retires ADR-05's last kernel-side stub and it posts to the general
ledger from two new places, so the posting maps and the decisions behind them are what a
reviewer needs in front of them rather than in a scratch file.

Written against `main` at `4573263` (PR #51, step 3, merged).

This step resumed a branch that already carried one commit (`f135983`) and a working note
saying, in as many words, that half of what it contained had never been executed. That note was
right, and **two of the five unexecuted functions were broken in ways only running them could
show**.

A third defect — the worst of the three — was in code that *was* executed, tested and reviewed,
and took a property test over random sequences to surface. All three are below, worst first.

## Three defects, and the third is the one to read

Two were found by running code the handover note had flagged as never executed. The third was
found by a **property test written because review asked for one**, and it is the serious one: a
correction posted into a closed month was re-declared on every subsequent VAT return, for the
life of the company.

### 3. A late entry was declared again, and again, and again

`_late()` selected entries dated inside a filed range whose id was above **that return's**
high-water mark. That condition is true the month after, and true forever. So:

```
post March 50, post March 50   →  the ledger holds 18 VAT
file March                      →  declares 18                          ✓
post March 50 again             →  a late entry of 9
file April                      →  declares 9 as a late entry           ✓
file May                        →  declares the same 9 again            ✗
file June                       →  and again                            ✗
```

The ledger holds 27; the returns declared 45. Hypothesis shrank it to those four steps.

The high-water rule was only half written. An entry is late if it arrived after its own period
was filed — and it is still *undeclared* only if no **later** return has swept it up already. A
return filed over a range after the one containing the entry has already looked into that
window, so if the entry existed when that return was filed, it was declared there:

```python
swept = max((later.high_water_entry_id
             for later in filed
             if later.period_from > ret.period_from), default=0)
# … JournalEntry.id > ret.high_water_entry_id, JournalEntry.id > swept
```

Proved sensitive the way step 9's pass will ask for — the guard removed, the test run, the
failure read:

```
AssertionError: April already declared it; May must not see it again
Left contains 2 more items, first extra item: LateEntry(entry_number='JE-000001',
  entry_date=2026-03-20, filed_return_number='VATR-000001', tax=180.000000)
```

**Two things about how this was found are worth more than the fix.** Every one of the eight
example tests around late entries passed throughout — the defect needs *three* filings
interleaved with a posting to appear, and an example pins one arrangement at a time. And the
property itself passed in CI, where Hypothesis runs at `max_examples=1`; it failed on the first
run at the `deep` profile. A property test that is never run deeply is a test that has not been
run. It is pinned as an example as well
(`test_a_late_entry_is_declared_once_and_not_by_every_return_after_it`), because the nightly is
where the property earns its keep and the pull request is where a regression has to fail.

### 1. Filing a return updated a posted journal entry

`file_return` posted the settlement entry, wrote the `vat_returns` row, and then did this:

```python
if entry is not None:
    entry.source_doc_id = filed.id
    db.flush()
```

An UPDATE on a posted journal entry. `kernel_block_posted_entry_mutation` raises `VN001` on it,
unconditionally and with no escape hatch, which is rule 3 working exactly as written. Every
call to `file_return` died there:

```
sqlalchemy.exc.DatabaseError: (psycopg.DatabaseError) journal entry 6 is posted and immutable
CONTEXT:  PL/pgSQL function kernel_block_posted_entry_mutation() line 4 at RAISE
```

The fix is the cycle-breaker `partner_documents` already uses for its companion stock entry:
take the id from the sequence before anything posts, and let the entry name it at birth.

```python
return_id = _reserve_return_id(db)    # SELECT nextval(...)
entry = posting.post(db, VatReturnPosted(..., source_doc_id=return_id), ...)
filed = VatReturn(id=return_id, ...)
```

A rolled-back filing burns an id, which is what a sequence is for. The return's *number* comes
from `document_sequences` and stays gapless.

### 2. Filing claimed a number that no row held

The return claimed a `VAT` number, and then `posting.post` claimed another for the settlement
entry, because that entry's doc type is the same run. Two numbers per filing, one of them held
by nothing.

The registry already said what was intended. `_NIL_VAT_RETURN` is narrowed to
`journal_entry_id IS NULL` with this comment, written in step 1:

> A return that *did* post shares its entry's number, which is why this claimant is narrowed
> rather than counting every row.

It did not share it. `assert_ledger_invariants` reads an unheld number as a hole in the run, so
the fix is one line of intent — `number = entry.number if entry is not None else claim_number(…)`
— and the test that matters is the invariant, not the string:

```python
vat.file_return(db, company_id, period_from=MARCH_FROM, period_to=MARCH_TO, actor=owner)
assert_ledger_invariants(db, company_id)
```

Defects 1 and 2 are the same shape: code that reads correctly, lints clean, and asserts nothing
about itself. The step-4 note called them unverified and it was right to. Defect 3 is a
different and harder shape — code that *was* asserted, by tests that were all individually
correct and collectively blind to a conjunction none of them constructed.

## What landed

**Decision 12 — filing.** `file_return` and `reverse_return` in `app/tax/vat.py`, now under
eleven tests (`tests/tax/test_vat_filing.py`). The settlement map, per code rather
than per side because several codes can share an account: Dr each output account for what
it declared, Cr each input account, the net to `gl_settings.vat_settlement_account_id`. The
fixture's month gives

| account | amount | why |
|---|---|---|
| `2200` | **+3 600** | output VAT was credited on declaration; settling it debits the same |
| `1400` | **−9 000** | input VAT was debited; settling it credits |
| `2250` | **+5 400** | the residue — a net *credit* position, sitting as a debit on the one account |

A settlement line carries the tax code on the tax account with `tax_amount = 0`, exactly like a
real tax line, so nothing about the amount distinguishes them. The module does
(`JournalEntry.module != 'tax'` in `_line_query`), and
`test_the_settlement_entry_is_not_tax_on_the_next_return` is what holds that: April's output
VAT is 0, and over a range containing the settlement it appears as an *untagged movement*
rather than as tax.

**Decision 12 — annexes.** `app/tax/annexes.py`, sales and purchases as RFC 4180 CSV. The
figures come from the same `journal_lines` the return reads, split into base and tax by the
account the line posted to — so the annex ties to the return by construction, and the tests
assert that equality rather than that the listing lists things. A document is joined through
its entry, **original or reversal**: a reversed invoice has two entries, the return counts both
because they cancel, and an annex showing only the original would tie to nothing.

**Decision 11 — X and Z.** `app/fiscal/daily.py`. An X is a question and a Z is an act: the
same computation, and only one of them is stored.

A Z states what the authority signed — both which documents are in the day and what they
declared — so it reads the receipts and the payloads they were issued against, through the
adapter's new `normalize_declared_totals()`. Not the ledger: see *The boundary, and the wrong
turn taken at it* below, which is the most instructive thing in this report after the two
defects. The §19.1 content is complete, discounts included. Pending queue rows are not receipts,
so a Z counts what was signed and records how many rows were still queued when it was taken.

**Decision 13 — FX revaluation.** `app/subledger/revaluation.py`, and `FxRevalued` is now a
posting somebody makes. Preview, post-with-mirror, reversal, the three refusals, and the
`fx_revaluation` job kind on `run_job`.

**Fifteen endpoints, five of them mutating** — `/tax/vat-returns` (preview, list, detail, file,
reverse, two annex downloads), `/gl/fx-revaluations` (preview, list, detail, post, reverse) and
the fiscal day (`x-report`, `z-reports`, `close-day`). Four of the five carry a
`GAP (P7, step 7)` line and `close-day` carries `GAP (P7, step 8)`; the previews, listings and
CSVs are reads and need no exemption, which is rule 14 working as written — it names mutating
endpoints.

## The FX posting map, and why it needs no rule per role

The difference is computed in **ledger sense**: `direction × open_amount`, so an AR invoice is
positive and an AP invoice negative, which is how the control accounts already hold them. Then:

* the revaluation account takes the difference as it stands;
* the P&L takes its negative, to `4410` when that is a credit and `6955` when it is a debit.

One rule, both roles, tested as the same rate movement in opposite directions:

| | open | booking | at date | carrying | revalued | difference | posts |
|---|---|---|---|---|---|---|---|
| AR invoice | USD 47.20 | 1 320 | 1 350 | 62 304 | 63 720 | **+1 416** | `1290` +1 416 / `4410` −1 416 |
| AP invoice | USD −100 | 1 320 | 1 350 | −132 000 | −135 000 | **−3 000** | `2190` −3 000 / `6955` +3 000 |

**Never `1200` or `2100`.** Their balance is Σ open items at booking rates — P4's invariant —
and a revaluation posting into them would break it on the first run.
`test_the_control_account_still_reconciles_after_a_revaluation` runs
`assert_subledger_invariants` after the pair, which is the assertion that would have caught it.

## Decisions worth review

### a. A late entry is *declared* on the current return, not merely listed — exactly once

The filed period is closed. A return that only listed a late entry would leave that tax
declared to nobody, so it counts in the figures, appears in the late-entry list with its own
date, and the tie carries its total separately (`AccountTie.late_total`) so the difference
against *this* range's account movement still adds up.

**Exactly once** is the half that was missing until the property test found it (defect 3). The
rule now has both clauses: an entry is declared by the first return filed after it arrived whose
range does not contain it, and by no other.

`test_an_entry_dated_in_the_month_but_posted_before_filing_is_not_late` is the other half: the
mark is about **arrival**, not about the date. An entry that existed when the return was filed
was reported by it, however late in the month it is dated.

### b. The tie is per VAT account, aggregating every code on that account

Carried from `f135983`. The Rwanda seed puts `VAT-IN-18` and `VAT-IN-IMP` both on `1400`; a tie
keyed one-code-per-account reported genuine input VAT as untagged.

### c. Reversing an FX run is a counter-pair, not a reversal

**The one most worth a second opinion.** A run posts its entry and its mirror the next day, and
the mirror is a `reverses_entry_id` reversal — decision 13 locks that. So the run's entry is
*already reversed*, and the kernel refuses to reverse it again (`entry_already_reversed`). It is
right to: the pair nets to zero across the two days, and undoing one half strands the other.

What has to be undone is the balance **at the revaluation date**, where the entry stands alone.
So `reverse_revaluation` posts the run again with every sign flipped and mirrors *that* the
following day. Four entries, cancelling at both dates:

```
Mar 31:  A  +1 416   then  −A  −1 416   → 0
Apr 01: −A  −1 416   then   A  +1 416   → 0
```

The test asserts the `1290` balance on both dates, which is the only way to see it. The
alternative — making the mirror a plain negated entry rather than a reversal, so the run's entry
stays reversible — would contradict decision 13's "(the P2 frozen-base reversal,
`reverses_entry_id` set)", so it is not taken here.

### d. A fiscal day has a second's resolution, because a receipt does

`sdcDateTime` is `yyyyMMddHHmmss`. Both bounds of a day are therefore floored to the second, and
a device's **first** day opens *inclusively* at its activation second — otherwise the first sale
a device ever made, stamped in that same second, belongs to no day. Later days open exclusively
at the previous Z's `to_at`, so the closes tile exactly.

The consequence, stated rather than hidden: two closes inside one second are an empty range and
are refused (`fiscal_z_empty_range`). Two *receipts* inside one second cannot be split by any
boundary, which is why `test_a_second_z_covers_only_what_came_after_the_first` waits a real
second rather than pretending otherwise.

### e. The Z close has a permission of its own — a deviation from decision 15's list

Decision 15 enumerates the phase's new permissions and names none for **closing** the day: it
gives `fiscal:reports_view` the X/Z *view*, which is a reading, and the close is not one. Step 4
first put it under `fiscal:queue_manage`, and review was right that this is the wrong reading —
that permission is "retry / verify / attach / purchase feed / imports", so it would have handed
the close to whoever may press Retry on a queue row. A till supervisor closes the day; they do
not administer the device.

So `fiscal:close_day` exists, back-filled by `0025_p7_close_day_permission` to Administrator and
Accountant. **This is a deviation from a locked decision's list** and is recorded as one rather
than made quietly: decision 15 should gain the constant when the plan is next touched.

### f. The imports section is keyed on the seeded code string

Carried from `f135983`. `IMPORT_TAX_CODE = "VAT-IN-IMP"`; everything else in the module keys on
`nature`, which is neutral. The schema that would carry this properly was step 1's to add.

### g. A nil return and a valueless run hold their own numbers

Same shape as P5's valueless stock document. Without the claimant, the gapless check reads them
as holes.

## The boundary, and the wrong turn taken at it

The Z's figures were first read straight off `fiscal_receipts.request`, by the authority's own
field names. `tests/fiscal/test_boundary.py` refused it:

```
AssertionError: these modules outside app/fiscal/rwanda use a revenue authority's field names
in code: {'app/fiscal/daily.py': ['taxblAmt']}
```

**The second draft over-corrected, and was worse.** It moved the arithmetic onto `journal_lines`
— neutral, tying to the VAT return, and wrong. A Z reports what was *declared*, and on a
discounted line the wire's taxable amount is `splyAmt − dcAmt`, extended from the two-decimal
**inclusive** price, not the posted gross. `rwanda/builders.py` says so where it computes them:

> The wire's taxable amount for one line — `splyAmt - dcAmt`, **not** the posted gross.

The two coincide on round, undiscounted prices and part company everywhere else. So the
ledger-based Z passed its test only because the fixture sold whole thousands — the same trap
step 2 found in its residue census, walked into a second time one step further along.

**What the test actually forbids is a field name above the adapter, not reading the payload.**
The adapter already owns `normalize_receipt()`, which turns the authority's *answer* into a
neutral `FiscalReceiptData`. The third draft adds its read half, `normalize_declared_totals()`,
turning a stored *request* into a neutral `DeclaredTotals` — on the Protocol, implemented in
`rwanda/`, returning `NULL` from `NullAdapter`. `daily.py` now names no field of anybody's, and
the Z states what RRA signed, which is what decision 11 requires. Rule 12 intact, decision 11
satisfied; the two were never in tension, and the second draft only looked like they were.

The test that holds it is `test_the_day_reports_discounts_and_follows_the_receipt_not_the_ledger`
— a 12.5 % line at 333.33, asserted against the stored payload, which is a case where the two
sources disagree and a round fixture could not have told them apart.

## Request or response: whose figures a Z prints

Raised in review, and the payload models settle it. `SalesResponseData` carries exactly three
money fields — `totTaxblAmt`, `totTaxAmt`, `totAmt` — and nothing else: no per-class buckets, no
rates, no `totItemCnt`, no `itemList` and therefore no `dcAmt`.

So `normalize_declared_totals(request, response)` splits on what the authority actually sends
back:

| figure | source | why |
|---|---|---|
| gross, taxable, tax | the **response**, falling back to the request | these are countersigned; "what RRA signed" means its own answer, and the two are equal exactly until the day they are not — which is the day a Z has to be right |
| per-class split, rates | the **request** | the response does not echo them |
| item count, discounts | the **request** | likewise; `dcAmt` exists only on the request's lines |

`DeclaredTotals.countersigned` records which happened, so a Z can say whose figures it prints.
The asymmetry is the authority's, not a shortcut: an adapter whose response carries more may
prefer more of it, and the Protocol is written so it can.

## Two figures the first pass simply omitted

**Discounts.** Decision 11 lists the §19.1 content as "copies count and gross, items count,
**discounts**". `DailyFigures` had no discount total, so a Z could not have been reconciled
against a checkpoint sheet. It is `Σ dcAmt` over the receipt's lines, reaching the neutral side
through `DeclaredTotals.discount`.

**The partner on a revaluation line.** Decision 13: the lines carry `partner_type/partner_id`
**so the report drills**. The detail endpoint loaded the documents and the currencies and then
rendered `partner_name=""` — a screen of blank cells that satisfies the schema and nothing else.
Fixed, and now asserted by name rather than by key.

That second one is the more interesting failure, because of *how* it survived: step 4 shipped
fifteen endpoints and not one test that opened any of them. Rule 13 is about screens, but its
reasoning is about routes — "a report that read the wrong field and showed Nothing to report
over a full subledger". `tests/tax/test_returns_api.py` is the answer: it asks the endpoints for
data and asserts figures that came back, including the partner's name, the annex headers, and a
second tenant getting a 404.

It found a third defect on its first run, and a worse one than the blank name:

```
pydantic_core.ValidationError: 1 validation error for FxRevaluationDetail
lines — Field required
```

`GET /gl/fx-revaluations/{id}` called `model_validate(run)` on the ORM row and *then* assigned
`detail.lines`. `lines` is required, so validation raised first: **every call to that endpoint
was a 500**, and the blank partner name was a bug inside a route that had never once returned
200. Three defects in this step now share one shape — code that reads correctly, lints clean,
and had nothing asserting it.

## Where the wire-versus-ledger residue is named, and where it is not

Review asked for the step-2 residue to get its own line on the tie, beside `late_total`. It has
a line, but not there, and the reason is worth recording because it looks like the obvious place.

**It cannot reach the tie.** The tie compares a VAT account's *ledger* movement with the tax
lines declared on it — both sides are `journal_lines`. No wire figure ever enters the ledger:
nothing under `app/fiscal/` calls `posting.post`, the outbox payload is built *from* the posted
document, and `sales.py::_posted_base` uses "the same function, on the same inputs, that
produced the `base_amount` on every journal line the document posted". A residue row on
`AccountTie` would read zero in every month and would explain nothing.

**Where it does appear is Z versus return.** A Z reports what was *declared* and a return reports
what was *posted*, and on a discounted or fractionally priced line those differ by construction
(decision 6). An accountant reconciling a month of Zs against that month's return meets the
franc there — so that is where it is named: `DailyFigures.posted_net`, the same documents'
ledger value, and `declared_less_posted`, the difference, on the document that introduces it.

If there is a path by which a wire figure reaches `journal_lines` that this missed, the line
belongs on the tie after all and this section is the thing to correct.

## One defect fixed outside step 4's scope

**The sandbox stamped receipts in UTC and the adapter read them as Kigali.**
`sandbox.py::_now()` formatted `datetime.now(UTC)` into `yyyyMMddHHmmss`; `parse_stamp` reads
that format as `Africa/Kigali`, correctly, because the format carries no zone and the device is
in Kigali. Every sandbox receipt therefore landed **two hours before the moment it was issued**.

Invisible to every test that reads counters, and fatal to the first one that asks which day a
receipt falls in — which is X/Z, this step. A real VSDC stamps local time, so the sandbox was
the wrong side of it. One line, and decision 16's "answers as RRA would" is true again.

## The build order's required tests, by name

Listed because the first draft of this report gave file-level counts only, and a count does not
show that a named requirement is met. Each clause of *"Tests with literals worked by hand"*:

| the brief asks for | the test |
|---|---|
| a month whose return ties to `2200`/`1400` to the franc | `tax/test_vat_return.py::test_the_month_ties_to_the_vat_accounts_to_the_franc` |
| …with one untagged payment listed | `tax/test_vat_return.py::test_an_untagged_movement_is_listed_rather_than_absorbed` — a cashbook payment to RRA, tagged with nothing |
| a late entry that changes the next return | `tax/test_vat_filing.py::test_a_late_entry_is_declared_on_the_next_return_and_the_filed_one_never_moves` |
| …and not the filed one | the same test: `filed.figures == as_filed` and `filed.output_vat == 3 600` after the backdated entry |
| a return that cannot overlap | `tax/test_vat_filing.py::test_a_range_may_not_overlap_a_filed_return` — refused at the one-day boundary, then allowed the day after |
| a revaluation whose gain equals `open × (rate_at − booking)` | `subledger/test_revaluation.py::test_the_gain_is_the_rate_movement_times_what_is_open` |
| …whose mirror nets to zero the next day | `subledger/test_revaluation.py::test_the_mirror_reverses_it_the_following_day` |
| …after which `assert_subledger_invariants` holds | `subledger/test_revaluation.py::test_the_control_account_still_reconciles_after_a_revaluation` |
| a Z whose totals equal Σ of its receipts | `fiscal/test_daily_report.py::test_a_z_totals_equal_the_sum_of_its_own_receipts` |
| *(added on review)* the late-entry rule **as a property over sequences** | `tax/test_property_vat.py::test_every_taxed_line_is_declared_exactly_once_across_a_run_of_returns` |
| *(added on review)* its regression, as an example | `tax/test_vat_filing.py::test_a_late_entry_is_declared_once_and_not_by_every_return_after_it` |

The first two are `f135983`'s and were already green; the rest were written in this step. The
overlap refusal was on the never-executed list at the start of it, which is why it is called out
rather than assumed.

**Every one of those is an example, and review was right that examples are not enough for the
late-entry rule.** Each pins one arrangement; none can reach a *conjunction* — two filings
interleaved with postings into both the open month and an already-closed one, where a line could
be declared twice (by the range that contains it and again as a late entry) or by neither (it
arrived after the first return's mark, and the second's sweep looked in the wrong window). So
`tests/tax/test_property_vat.py` drives a random sequence of **post** and **file** across three
months and asserts, after every step:

* **no filed return ever moves** — `figures` compared against the snapshot taken when it was
  filed, not against a recomputation, which is the whole reason they are stored; and
* **every taxed line is declared exactly once** — the run ends by filing a sweep range after all
  the others, which picks up every late entry from every filed month, and then Σ of what all the
  returns declared must equal the output VAT the ledger holds.

The second is the invariant the high-water rule exists to produce, stated over a whole run
instead of over one arrangement of it. It carries `@pytest.mark.slow`, so the nightly deep
profile is where it earns its keep.

Beyond the brief, and prompted by review: `per (role, currency)` is confirmed by
`test_each_currency_gets_its_own_pair_of_lines` — a USD gain of +1 416 beside a EUR loss of
−2 000 posts four lines to two P&L accounts, rather than one netted pair of −584 that is true of
nothing.

## Endpoint coverage, and the systemic finding behind it

Step 4 shipped fifteen endpoints and, when it first proposed itself done, **seven of them had a
test and eight did not**. That is the finding worth carrying out of this step, and it is bigger
than any of the three defects:

> The rule-14 register proves an endpoint has a **caller**. It never proves the endpoint
> **answers**.

`GET /gl/fx-revaluations/{revaluation_id}` satisfied the register, carried a `GAP (P7, step 7)`
line naming the screen that would call it, passed review, and returned **500 to every request**
— `FxRevaluationDetail.model_validate(run)` raised `lines — Field required` before the code that
filled `lines` could run. The blank `partner_name` review had spotted was a bug *inside a route
that had never once returned 200*. Rule 13 says a screen is not done until a test has opened it
with data in it; the same sentence is true of a route, and nothing in the build enforces it.

All fifteen now answer at least once, with a figure asserted rather than a status code:

| endpoint | test |
|---|---|
| `GET /tax/vat-returns/preview` | `test_the_vat_return_preview_renders_its_sections_and_its_tie` |
| `GET /tax/vat-returns` | `test_the_vat_return_listing_and_detail_answer` |
| `GET /tax/vat-returns/{id}` | the same, plus a 404 for a missing one |
| `POST /tax/vat-returns` | `test_filing_a_return_needs_the_filing_permission` |
| `POST /tax/vat-returns/{id}/reverse` | `test_reversing_a_return_over_the_api_reopens_the_range` |
| `GET …/annexes/sales.csv`, `…/purchases.csv` | `test_the_annexes_come_back_as_csv_with_their_headers` |
| `GET /gl/fx-revaluations/preview` | `test_the_revaluation_preview_renders_a_figure` |
| `GET /gl/fx-revaluations` | `test_the_revaluation_listing_and_reversal_answer` |
| `GET /gl/fx-revaluations/{id}` | `test_the_revaluation_detail_names_the_partner_each_line_drills_to` |
| `POST /gl/fx-revaluations` | `test_the_settings_the_revaluation_needs_are_named_when_missing` |
| `POST /gl/fx-revaluations/{id}/reverse` | `test_the_revaluation_listing_and_reversal_answer` |
| `GET /fiscal/devices/{id}/x-report` | `test_the_x_report_endpoint_renders_the_day` |
| `GET /fiscal/devices/{id}/z-reports` | `test_closing_the_day_over_the_api_stores_a_z_and_lists_it` |
| `POST /fiscal/devices/{id}/close-day` | the same |

## Carried to step 9

1. **"Every endpoint answers 200 at least once", as a build-wide check.** Step 4 closed its own
   fifteen by hand, which fixes this step and not the next one. What the build wants is the
   register's sibling: a test that enumerates every route the app serves — as
   `test_api_has_a_caller.py` already does — and fails on any that no test has ever exercised.
   Step 9 owns the sensitivity pass and the register, so it is the right place to add it, and it
   is listed here as its own item rather than folded into "more tests".
2. **Decision 15 should gain `fiscal:close_day`** when the plan is next touched (decision *e*).
3. **The two sandbox questions of decision 6** remain step 5's, unchanged by this step.

## Checks

Run in the backend container, which is where this project's checks run.

```
uv run ruff check .                                       →  All checks passed
uv run pytest tests/ -n 4                                 → 1329 passed in 17:24
HYPOTHESIS_PROFILE=deep uv run pytest tests/tax/test_property_vat.py
                                                          →    1 passed (300 examples)
make migrate-check                                        →  upgrade-from-zero, alembic check
                                                             and downgrade-to-base all green
```

The deep profile is listed separately and deliberately. CI runs Hypothesis at `max_examples=1`,
where this step's property **passed while the code was wrong**; it failed on the first run at
300. A property test recorded as green without saying which profile produced that green is a
line of evidence for nothing.

What this step added or repaired:

| file | tests |
|---|---|
| `tests/tax/test_vat_filing.py` | 12 |
| `tests/tax/test_annexes.py` | 5 |
| `tests/tax/test_returns_api.py` | 10 |
| `tests/tax/test_property_vat.py` | 1 property |
| `tests/subledger/test_revaluation.py` | 13 |
| `tests/fiscal/test_daily_report.py` | 13 |
| `alembic/versions/0025_p7_close_day_permission.py` | the `fiscal:close_day` back-fill |

`tests/tax/test_vat_return.py` (2) is `f135983`'s and still passes unchanged.

## What step 4 does not include

Nothing in the brief's step-4 list is outstanding. For the reader's orientation, the parts of
decisions 11–13 that belong to later steps and are deliberately absent here:

* the CIS **print layout** and the copy watermark — decision 11's first half, which is a
  document-detail concern and arrives with the screens;
* every **screen** named in the `GAP` lines above (steps 7 and 8);
* the **acceptance tape**, which is step 5 and exercises rows 10–13 of it against this code.
