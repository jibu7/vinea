# P7 step 4 — the VAT return, filing, X/Z, FX revaluation

Step 4 is not a gate, but it retires ADR-05's last kernel-side stub and it posts to the general
ledger from two new places, so the posting maps and the decisions behind them are what a
reviewer needs in front of them rather than in a scratch file.

Written against `main` at `4573263` (PR #51, step 3, merged).

This step resumed a branch that already carried one commit (`f135983`) and a working note
saying, in as many words, that half of what it contained had never been executed. That note was
right, and it is the most useful thing in this report: **two of the five unexecuted functions
were broken in ways only running them could show.** They are the first section below.

## Two defects in code that lints, reviews well, and cannot work

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

Both defects are the same shape: code that reads correctly, lints clean, and asserts nothing
about itself. The step-4 note called them unverified and it was right to.

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

A receipt decides *what is in* the day; the ledger decides *what it is worth*. The population is
`fiscal_receipts` and nothing else — a sale with no receipt is no part of the day, however
posted it is — and the money is read off the documents those receipts were issued against, as
decision 11 words it ("computed from `fiscal_receipts` **and their documents**"). The first
draft read the stored request payload instead, and
`test_no_rra_field_name_appears_in_code_outside_the_rwanda_package` refused it; see *One
boundary the first draft crossed* below. Pending queue rows are not receipts, so a Z counts what
was signed and records how many rows were still queued when it was taken.

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

### a. A late entry is *declared* on the current return, not merely listed

The filed period is closed. A return that only listed a late entry would leave that tax
declared to nobody, so it counts in the figures, appears in the late-entry list with its own
date, and the tie carries its total separately (`AccountTie.late_total`) so the difference
against *this* range's account movement still adds up.

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

### e. The Z close sits under `fiscal:queue_manage`

Decision 15 gives `fiscal:reports_view` to "queue, receipts, X/Z, the fiscal enquiries" — which
is viewing. A close is an act, and no permission is named for it. `fiscal:queue_manage` is the
closest ("retry / verify / attach / purchase feed / imports"), so the X and the Z listing are
`reports_view` and the close is `queue_manage`. **If the intent was a permission of its own,
this is the line to change.**

### f. The imports section is keyed on the seeded code string

Carried from `f135983`. `IMPORT_TAX_CODE = "VAT-IN-IMP"`; everything else in the module keys on
`nature`, which is neutral. The schema that would carry this properly was step 1's to add.

### g. A nil return and a valueless run hold their own numbers

Same shape as P5's valueless stock document. Without the claimant, the gapless check reads them
as holes.

## One boundary the first draft crossed

The Z's figures were first read straight off `fiscal_receipts.request` — the stored payload, by
its own field names (`taxblAmtB`, `totTaxAmt`, `pmtTyCd`). It was a defensible instinct: a Z
should say what the authority signed, and the payload is what the authority got.

`tests/fiscal/test_boundary.py::test_no_rra_field_name_appears_in_code_outside_the_rwanda_package`
refused it:

```
AssertionError: these modules outside app/fiscal/rwanda use a revenue authority's field names
in code: {'app/fiscal/daily.py': ['taxblAmt']}. The DTOs in app/fiscal/mapping.py are the
vocabulary above the adapter.
```

The test is right and the instinct was half right. What a Z must take from the receipts is
**which documents are in the day and when** — that is the part the authority decides, and it
stays exactly as it was. What it takes from the ledger is **what they were worth**, which is how
every other figure in this phase is computed and what makes a Z tie to the VAT return that
covers the same days. The four tax classes reach the computation through
`tax_codes.fiscal_tax_type`, Vinea's own mapping, not through a wire field.

Rule 12's point, concretely: this same `compute()` has to serve a second country whose payload
spells none of it alike. Reading the ledger, it already does.

One nuance the rewrite surfaced and the code now names: `totItemCnt` on the wire is the *DTO's*
line count, and a kit travels as its parent with its components suppressed (decision 3), so a
document carrying a kit counts more lines in the day's `items_count` than its receipt printed.
Step 5's tape is where the two are read side by side against a real kit.

## One defect fixed outside step 4's scope

**The sandbox stamped receipts in UTC and the adapter read them as Kigali.**
`sandbox.py::_now()` formatted `datetime.now(UTC)` into `yyyyMMddHHmmss`; `parse_stamp` reads
that format as `Africa/Kigali`, correctly, because the format carries no zone and the device is
in Kigali. Every sandbox receipt therefore landed **two hours before the moment it was issued**.

Invisible to every test that reads counters, and fatal to the first one that asks which day a
receipt falls in — which is X/Z, this step. A real VSDC stamps local time, so the sandbox was
the wrong side of it. One line, and decision 16's "answers as RRA would" is true again.

## Checks

Run in the backend container, which is where this project's checks run.

```
uv run ruff check .                                       → All checks passed
uv run pytest tests/test_api_has_a_caller.py              →  30 passed
uv run pytest tests/fiscal/ tests/subledger/ tests/tax/ -n 4
                                                          → 464 passed in 5:45
```

Of those 464, the ones this step added or repaired:

| file | tests |
|---|---|
| `tests/tax/test_vat_filing.py` | 11 |
| `tests/tax/test_annexes.py` | 5 |
| `tests/subledger/test_revaluation.py` | 12 |
| `tests/fiscal/test_daily_report.py` | 9 |

`tests/tax/test_vat_return.py` (2) is `f135983`'s and still passes unchanged.

## What step 4 does not include

Nothing in the brief's step-4 list is outstanding. For the reader's orientation, the parts of
decisions 11–13 that belong to later steps and are deliberately absent here:

* the CIS **print layout** and the copy watermark — decision 11's first half, which is a
  document-detail concern and arrives with the screens;
* every **screen** named in the `GAP` lines above (steps 7 and 8);
* the **acceptance tape**, which is step 5 and exercises rows 10–13 of it against this code.
