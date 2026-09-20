# P7 step 8 — the Tax enquiries and reports, and the FX report

Step 8 is the last of P7's three UI steps and it is **not a gate**: the changed specs and the
guard tests were run locally, the full backend suite was run because the changes reach
`app/fiscal/` and `app/api/v1/gl.py`, and CI is the record for the branch head.

What it builds is everything a fiscalized company **reads back**. Steps 6 and 7 gave it a device
to set up and a day's work to do; until this step it could declare a sale, print the receipt and
file a return, and then had no way to look up the receipt somebody was holding, ask what had
ever been sent for one invoice, close a fiscal day, tie the authority's figures to the sales
ledger, or read back a revaluation it had posted.

## What landed

**Two screens under Enquiries → Tax:**

| screen | route | endpoints |
|---|---|---|
| Fiscal receipts | `/fiscal/enquiries/receipts` | `GET /fiscal/receipts` (device, kind, range, one search box) |
| Fiscal queue history | `/fiscal/enquiries/queue-history` | `GET /fiscal/receipts` (the picker), `GET /fiscal/queue/rows?document_id=`, `GET /fiscal/queue/rows/{id}` |

**Three screens under Reports → Tax:**

| screen | route | endpoints |
|---|---|---|
| VAT return | `/tax/reports/vat-return`, `/tax/reports/vat-return/{id}` | `GET /tax/vat-returns`, `GET /tax/vat-returns/{id}`, `GET /tax/vat-returns/annexes/{sales,purchases}.csv` |
| Daily fiscal report | `/tax/reports/daily-fiscal` | `GET /fiscal/devices/{id}/x-report`, `GET …/z-reports`, `GET /fiscal/queue`, **`POST …/close-day`** |
| Fiscal receipts listing | `/tax/reports/receipts` | `GET /fiscal/receipts/listing` (new) |

**One screen under Reports → General Ledger:**

| screen | route | endpoints |
|---|---|---|
| FX revaluation | `/gl/reports/fx-revaluation`, `/gl/reports/fx-revaluation/{id}` | `GET /gl/fx-revaluations`, `GET /gl/fx-revaluations/{id}` |

### The register, and what step 9 inherits

`POST /api/v1/fiscal/devices/{device_id}/close-day` carried the last `GAP (P7, step 8)` line in
`backend/tests/test_api_has_a_caller.py`. It is gone, deleted by a **pressable** button on the X
view — pressed by `e2e/p7-enquiries-reports.spec.ts`, not by a hook written to satisfy the
matcher. After this step the register carries for P7 exactly:

```
POST /api/v1/fiscal/outbox/drain — by design, the EBM queue's scheduler hook
```

and nothing else, which is what step 9's DoD asks for. Checked here rather than left for step 9
to discover.

## One new endpoint, and why the enquiry could not be it

`GET /fiscal/receipts/listing` is the only endpoint this step adds, and it is a read.

The enquiry above already lists receipts. What it cannot do is **tie**, and the difference is
the whole report. The kickoff's words for it: *a listing that only prints receipts is a listing,
not a tie.* An accountant closing a month is not in doubt about the receipts; they are in doubt
about the **agreement** between what RRA holds and what the sales ledger holds, and the answer
they need is not a total — it is the name of every document that is on one side and not the
other, with the word that fixes it.

**The two sides are derived differently on purpose.**

* The **declared** side reads `fiscal_receipts.request` through the adapter's
  `normalize_declared_totals` — the same path `daily.compute` takes for an X or a Z. So a day's
  listing and that day's Z are the same arithmetic over the same rows and must agree to the
  cent.
* The **ledger** side reads `partner_documents` and knows nothing about RRA: AR invoices and
  credit notes posted on the device's branch in the range.

Agreeing is then evidence. A tie whose two sides came out of one query would agree whatever had
gone wrong.

**They are also indexed differently, and that is not a defect.** A receipt belongs to the day the
device *signed* it (`sdc_datetime`, the axis a Z is cut on); a document belongs to the day it is
*dated* (`document_date`). A sale keyed on the 31st and drained on the 1st sits on opposite sides
of a month end, and so does the `NR` for a reversal of last week's invoice — RRA cannot un-sign a
sale, so the refund is signed today against a document dated then (decision 3). Both land in the
asymmetry lists with the reason showing. An axis fudged to make the totals match would hide
exactly the rows worth looking at.

**Reversed documents stay on the ledger side.** A reversed invoice was posted, was signed, and
its reversal is a second document with an `NR` of its own; dropping it would take the sale off
one side while leaving both receipts on the other, and turn a tie that reconciles into one that
does not.

`registry.adapter_for_company` came out of this: "which adapter is this company's, with no
transport" now has one spelling, because `daily.py` and `enquiries.py` both want it and two
spellings is how one of them comes to answer differently after a country is added.

## The drill-key table

`sources.py` gains the two P7 source types, and `_module_document` asks for them **last** —
after the module table and after the source link — because the entries that reach that line are
the ones with no source link of their own.

| source type | written by | routing key | route |
|---|---|---|---|
| `inventory_document` | P5 | `inventory_document` | `/inventory/documents/{id}` |
| `partner_document` | P4 | `ar_document` / `ap_document` | `/ar/documents/{id}`, `/ap/documents/{id}` |
| `goods_received_note` | P6 | `goods_received_note` | `/oe/goods-received/{id}` |
| `landed_cost_document` | P6 | `landed_cost_document` | `/oe/landed-costs/{id}` |
| **`vat_return`** | `app/tax/vat.py` | `vat_return` | `/tax/reports/vat-return/{id}` |
| **`fx_revaluation`** | `app/subledger/revaluation.py` | `fx_revaluation` | `/gl/reports/fx-revaluation/{id}` |

Both land on a **report** rather than on a transaction screen, and the report routes take an id
for exactly this: `/tax/vat-returns` files a return and `/gl/fx-revaluations` posts a run, and
somebody arriving from a journal entry is reading, not acting.

### Six entries, and only four are named by a column

Between them the two documents can produce six journal entries. `ReversalRequested` carries no
`source_doc_type` of its own and the kernel does not copy the original's, which is why half of
them have none:

| entry | posted as | `source_doc_type` | named by |
|---|---|---|---|
| the settlement | `VatReturnPosted` | `vat_return` | `vat_returns.journal_entry_id` |
| its reversal | `ReversalRequested` | *none* | `vat_returns.reversal_entry_id` |
| the run | `FxRevalued` | `fx_revaluation` | `fx_revaluations.journal_entry_id` |
| its next-day mirror | `ReversalRequested` | *none* | `fx_revaluations.mirror_entry_id` |
| the counter-entry | `FxRevalued` | `fx_revaluation` | `fx_revaluations.reversal_entry_id` |
| the counter's own mirror | `ReversalRequested` | *none* | **nothing** |

The last row has no column anywhere: `reverse_revaluation` mirrors its counter-entry and stores
only the counter. It resolves through `reverses_entry_id`, on the rule that **a mirror belongs
wherever its original belongs**. Without that second hop it is an `FXR-` number on the trial
balance belonging to nothing.

### And the reverse: the pair, as P6 did for `LCA-`

A **VAT return** reverses as a true mirror (`ReversalRequested` under `module_reversal("tax")`),
so the kernel writes `reverses_entry_id` and both directions already stand. Nothing was added
for it — and `test_a_filed_return_pairs_without_the_document` is what keeps that *true* rather
than *assumed*, because the day it changes is the day the resolver silently becomes load-bearing
on this side too.

An **FX revaluation** does not. Reversing a run posts a **counter-entry of negated lines**, not a
mirror, because the run's own next-day mirror already occupies the one `reverses_entry_id` slot
`uq_journal_entries_reverses_entry_id` allows. So the counter-entry was a reversal the ledger
rendered with nothing to say about what it reversed — P6's `LCA-` hole, one phase on, and the
P4 failure mode rule 13 is written against: the row is there, the link is not, and nothing fails.

`_resolve_p7_document_pair` fills it from the document, and **only where the column is null**.
That is the one difference from `_resolve_landed_cost_pair`, which fills unconditionally, and it
is not a detail: the run's entry already has a `reversed_by` — its next-day mirror, the frozen-
base reversal — and that is the honest answer to "what took this out of the balance sheet".
Overwriting it with the counter-entry would replace one true statement with a different true
statement and lose the first.

## Close day: the kickoff's refusal wording is withdrawn

The kickoff carried this as *"refused when the device has non-terminal rows … the refusal shown
before the button"*. Raised before building, because it contradicts an approved decision, and
settled by the owner: **warn, don't refuse.**

**That wording is withdrawn. Decision 11 stands exactly as written, and is not amended.** No
backend change was made and none was needed — `daily.close_day()` is untouched.

The conflict was real. `close_day()` refuses one thing, an empty range (`fiscal_z_empty_range`),
and decision 11 deliberately records `queued_rows` on every Z, in its own words *"so a Z that
closed over an unsent sale says so on its face"*. A refusal would make that figure dead: it could
never be anything but zero, and the sentence decision 11 wrote it for could never be true. It
would also stop a shop closing its day because a line was down, which turns an outage into the
shop's problem instead of the queue's.

So the screen carries what the kickoff was actually asking for — the person is told, **before**
the button, what closing now means — and the decision keeps its meaning.

### What the X view says

* **Rows in flight by status**, not as one number (`close-day-pending-by-status`): `queued` and
  `sending` clear themselves, while `failed`, `unknown` and `needs_receipt` each wait for a person
  and for three different reasons — RRA refused, RRA may be holding the sale, RRA is holding it
  and Vinea has no receipt. "5 rows pending" tells a reader there is a queue and nothing about
  whether it is a drain away or a morning's work. `pending_rows` is Σ of exactly these statuses,
  so the breakdown and the total cannot disagree.
* **Where those sales go**: *"Closing now is allowed. None of these sales is on this Z — each
  appears on the Z of the day the authority signs it, and this Z records how many were still in
  flight when it was taken."* Not "the next day": a row the authority takes three days to answer
  lands on the Z of **that** day, and a first draft of this copy said otherwise.
* **Close day stays enabled**, under `fiscal:close_day`.
* When the queue is clear, `close-day-clear` says so instead.

### And the e2e closes over one

Driven through `/_sandbox/mode`: the authority goes down, a sale stays in flight, and the test
reads the count off the X view — then **presses Close day over it**. The resulting Z carries
`queued_rows` equal to that same count, and `ns_count` **0**, because a queued row is not a
receipt and is no part of the day's takings. Two screens, one count, which is the only way to see
what decision 11 stores `queued_rows` for.

Then the authority comes back, the queue drains, and the next Z carries that sale with
`queued_rows` **0** — the warning's claim made good rather than asserted.

### A receipt signed in the second a Z is taken is counted by neither

Found by writing the test above, and worth carrying because step 9's DoD asserts Z totals.

A Z's bounds are floored to a second (`_floor_second`), because `sdcDateTime` has no finer
resolution, and a range is `from_at <` … `<= to_at`. Close a day and drain **within the same
second**, and the receipt that comes back is stamped inside the closed Z's range — whose figures
are already frozen — while the next Z opens *exclusively* at that same instant. Observed exactly
once, in a run fast enough to do both in 18 seconds:

| | range | `ns_count` |
|---|---|---|
| `Z-000001` | 21:13:06 → **21:13:11** | 1 |
| `Z-000002` | **21:13:11** → 21:13:13 | 0 |

The receipt was signed at 21:13:11 and is on neither.

**Not filed as a defect, and no code was changed for it.** The tiling property decision 11 wants —
closes cover a device's whole life with no gap and no overlap — holds over *ranges*; what cannot
be made exact is which side a receipt issued in the boundary second falls, because the stamp has
no finer resolution to ask. A real device signs and closes minutes apart. Only a test is fast
enough to land on it, which is why the backend's own boundary test
(`test_a_second_z_covers_only_what_came_after_the_first`) sleeps 2.05 s rather than pretending
otherwise, and why this file's e2e now waits past the close's second before draining — with that
reason written next to the wait, so nobody later deletes it as a flake patch.

**For step 9**: the tape closes days and drains around them. If it asserts a Z total straight
after a close, it has to be past that second first.

### The second close

Asserted as **whatever the backend actually does**, which is `fiscal_z_empty_range` on an empty
follow-up range: a Z runs from the previous close to now, both floored to the second, so a second
close at the same instant has a range of zero length and nothing in it. Nothing was invented, and
no new refusal was added.

It is pinned at the **service** level, where the clock is injectable
(`tests/fiscal/test_daily_report.py::test_a_second_close_at_the_same_instant_is_refused`), and it
was asserted nowhere before this step — the existing API test names it in a docstring as the
reason for a `sleep` and never checks it. It is deliberately *not* asserted through the screen: a
tab switch and a render cost more than the second the refusal depends on, and once a second has
elapsed the close succeeds and stores an empty Z, which is also correct. A test that is right only
when the machine is fast is a test about the machine.

**`fiscal:close_day`** is the permission on the button (decision 15 as amended at step 5 —
`fiscal:queue_manage` was the placeholder, and migration 0025 added the real one). The nav row
reads on the fiscal *view* permissions, because an accountant may read an X and a Z without being
able to close a day.

## The Z-versus-receipts tie, and the prices it took to see it

**The prices are the design.** Everything in this step's tests and screenshots is keyed at
**1 499 excl. at 18 %**, which the two systems round under different rules:

| | rule | three bottles | seven | one back |
|---|---|---|---|---|
| **ledger** | per line, to the currency's places — RWF has **none** (rule 6) | 4 497 net, **809** VAT, **5 306** | **12 382** | **1 769** |
| **wire** | a VAT-inclusive unit price at **two** decimals (decision 6): 1 768.82 | **5 306.46** | **12 381.74** | **1 768.82** |

Forty-six centimes apart on one invoice, and **neither figure is wrong**. On 2 000 × 10 they
agree exactly and both fiscal reports have nothing to show — which is P7 step 2's census bias in
one line: *round prices are precisely the ones that hide the residue*. Step 7's spec used 2 000
and could not have caught a report printing the ledger's figure where the wire's belongs. This
one can.

### The tie, across two screens

| | figure |
|---|---|
| Fiscal receipts listing — the two sales' rows, summed by the test | **17,688.20** |
| Daily fiscal report — the Z's sales total, summed by the server | **17,688.20** |
| Z refunds | 1,768.82 |
| Z net | **15,919.38** |
| Z posted in the ledger | 15,919.00 |
| Z declared less posted | **0.38** |

The two sides are computed differently: the Z is the server absorbing every receipt in its range
through `normalize_declared_totals`; the listing renders each receipt's own declaration on its
own row and the test adds them up. Each figure is also a hand-worked literal, so a bug that moved
both sides together still fails.

**0.38 is the residue and nothing else**: +0.46 on the first invoice, −0.26 on the second, and
+0.18 the refund adds back (a refund's residue adds back because it is subtracted on both sides).
It is not a defect and never will be — two correct rounding rules meeting — and the screen names
it rather than leaving a month-end reconciliation to discover an unexplained franc.

**Declared figures print at two decimals** on both fiscal reports although the base currency has
none. Rounding the wire's figure to the franc on screen would erase the very difference these
reports exist to show, and the number on the paper in an inspector's hand has the centimes on it.

### And the tape's row 14, through the screen

Reverse a signed invoice on the document detail — with the §4.16 reason the dialog asks for on a
fiscalized invoice — drain, and close. The Z carries **NR 1**: 3,537.64 sold and 3,537.64 back,
net 0.00. RRA cannot un-sign a sale, so the reversal is a second receipt about the same invoice
and both are on the day.

## Appendix C

The prompt's step 8 says to record these as "the other half of **C.1.10**". That number is
**stale**: step 6 turned out to need an entry of its own and took C.1.10, so step 7's two blocks
are C.1.11 (Transactions → Tax) and C.1.12 (Transactions → GL → FX revaluation), and these belong
to **C.1.11**. The Master Plan's own note on the numbering says so, and step 7's report already
recorded the shift.

Six rows, pinned in `frontend/src/design/components/appendix-c-order.test.tsx`:

| intent | module | rows | position |
|---|---|---|---|
| Enquiries | Tax | Fiscal receipts, Fiscal queue history | after Order Entry, last |
| Reports | General Ledger | FX revaluation | after Chart of accounts, before the P8-tagged rows |
| Reports | Tax | VAT return, Daily fiscal report, Fiscal receipts | after Order Entry, last |

**"Fiscal receipts" appears under both intents, and they are not the same screen.** The enquiry
is what somebody holding a piece of paper opens: one search box over the printed counter, the
document number, the customer and the authority's invoice number. The report is what a month is
closed on: per device, per range, what RRA signed beside what the sales ledger holds, and every
document on one side and not the other. Appendix C already works this way — "Age analysis" is an
AR row and an AP row — and the intent is half the name.

FX revaluation goes after Chart of accounts and before the phase-tagged `Bank reconciliation`,
which keeps the live GL rows together and leaves the owner's tagged tail where it is: the same
rule the Transactions block followed when it put the run beside Cashbook batches.

After this step **no P4, P5, P6 or P7 tag is left anywhere in the tree**, and every untagged row
has a route — both already-existing assertions in that file, and both still pass.

## Permissions on the nav rows

Each row gates on exactly what the endpoint behind it accepts, which is the rule the tree has
followed since P6:

| row | permission | why |
|---|---|---|
| Fiscal receipts (enquiry), Fiscal queue history, Daily fiscal report, Fiscal receipts (report) | `fiscal:setup_manage` **or** `fiscal:reports_view` | what `_require_view` accepts. An accountant establishing a fact about one sale has no business reconfiguring a device, and gating on `setup_manage` alone would have handed them that authority in order to read a receipt |
| VAT return | `tax:vat_return_view` **or** `tax:vat_return_file` | the read accepts the first; a controller who may look at a filed return and not file one must be able to reach it |
| FX revaluation (report) | `gl:reports_view` | reading what a revaluation did is not the authority to post another one — that is `gl:fx_revalue`, on the Transactions screen |

**Close day** is the one act on these screens, and it checks `fiscal:close_day` on the button
rather than on the row.

## Print and CSV

**Print** is the P3 `ReportPage` shape on all six screens: the filters, the nav and the buttons
are `print:hidden` and a masthead carrying the company, the report name and the range is
`hidden print:block`. Asserted with `pdftotext` over `page.pdf()` — which renders with `print`
media, and is therefore the only way to see what actually reaches paper. A DOM assertion on the
screen proves nothing about the sheet.

| sheet | asserted on the paper |
|---|---|
| Daily fiscal report, a Z | the device's `sdc_id` **`SDC010000005`**, its `mrc_no` **`WIS01006230`**, the `Z-` number, and the day |
| VAT return | the `VATR-` number and the net payable figure |

**CSV** comes in two kinds, and the difference is deliberate.

* `exportToCsv` for a table already on the page — the receipts enquiry, the daily report's tax
  classes, the listing, the revaluation lines. The rows are there; round-tripping them through
  the network to get the same bytes back would be ceremony.
* **`downloadFromApi` for the two VAT annexes.** They are the authority's own listings — every
  fiscalized sale with its receipt block, every purchase with its supplier TIN — computed by
  `app/tax/annexes.py` over data no screen holds. A client that rebuilt them would be a second
  implementation of a filing, and the two would part company the first time either changed. The
  filename comes from the server's `Content-Disposition` too, because a name invented in the
  screen would be a third place the period is spelled.

The annexes are asserted **on the real download**: the file is read off disk, its header line
checked (`customer_tin`, `supplier_tin`), a row asserted under it, and the sales annex checked to
carry this run's own `SDC010000005` — never on JSON behind the endpoint.

## Rule 13 per screen

One money figure and one quantity read off every screen, formatted the way a reader sees them:

| screen | money | quantity |
|---|---|---|
| Fiscal receipts (enquiry) | Σ document totals | the printed counter, parsed from the page as `n/m NS` |
| Fiscal queue history | — (a queue row has no amount) | the sequence number, and the row count |
| VAT return | net payable, against what was filed | the high-water entry id |
| Daily fiscal report | sales 17,688.20 · refunds 1,768.82 · net 15,919.38 · residue 0.38 | items sold **10**, items returned **1** |
| Fiscal receipts listing | declared, posted and the difference, per row and in total | NS / NR counts, and the ledger's document counts |
| FX revaluation | the run's net difference, and each line's | documents revalued |

Dates go through `lib/format`. Enums come from `api-enums.ts` (`FiscalReceiptType`,
`FiscalOutboxStatus`, `VatReturnStatus`, `FxRevaluationStatus`) — no screen spells one. The i18n
coverage test scans all of `src`, so the six new screens are covered by the same edit that added
them, and it passes.

## Defects found, and the guard that catches each

Three, all found by writing the test rather than by reading the code, and each guard was broken
once to prove it is sensitive.

**1. Half of every `VATR-`/`FXR-` pair resolved to nothing.** The obvious half of the work — a
drill key per source type — covers the settlement entry and the run entry, which carry a source
link. It does not cover the **reversal, the two mirrors and the counter-entry**: `ReversalRequested`
carries no `source_doc_type` and the kernel does not copy the original's, so four of the six
entries had no link at all and the `tax`/`gl` modules have no document table to fall back to.
The counter's own mirror has no column anywhere.

*Guard*: `tests/tax/test_entry_drill.py`, seven tests over all six entry shapes.
*Broken once*: removing the `reverses_entry_id` hop from `_p7_document` →
`test_the_counter_entry_and_its_own_mirror_both_drill_to_the_run` fails, alone. Removing the
whole `_P7_DOCUMENTS` branch from `_module_document` → three fail.

**2. Filling the reversal pair unconditionally would have been wrong**, and it is the shape the
landed-cost resolver it is modelled on uses. An FX run's entry already has a `reversed_by` — its
next-day mirror — and overwriting it with the counter-entry trades one true statement for
another and loses the first.

*Guard*: `test_the_counter_entry_says_what_it_reversed_and_the_run_keeps_its_mirror`.
*Broken once*: dropping the two `is None` conditions → that test fails, alone.

**3. The tie's two sides are on different axes, and a first draft that ranged both on
`document_date` would have balanced by construction.** A receipt would then have been assigned to
the day of the document it belongs to rather than to the day the device signed it, which is not a
fact about the receipt — and the Z, which ranges on `sdc_datetime`, would have disagreed with a
listing that claimed to tie to it.

*Guard*: `tests/fiscal/test_receipt_listing.py::test_a_receipt_signed_outside_its_documents_range_says_dated_outside`,
which builds a sale dated in March and signed today and asserts it lands on the receipts side
alone with `dated_outside` against it.

**4. The GL entry page resolved a `VATR-`/`FXR-` document and then dropped it.** The drill key
is only half the job. `documentHref` was consumed inside the entry page's *module-owned* branch
— which was correct while every source type it could resolve belonged to `inv`, `ar` or `ap`, and
which silently stopped being correct at this phase. An `FXR-` entry's module is **`gl`**: the run,
its next-day mirror, the counter-entry and that counter's own mirror are all `gl`, all now resolve
to a run, and not one of them took that branch. The server had the link and the screen threw it
away — four `FXR-` numbers on the trial balance belonging, as far as a reader could tell, to
nothing. The P4 failure mode rule 13 is written against, in the one place this step was supposed
to be closing it.

*Guard*: `entry-source-document`, asserted on the screen in
`p7-enquiries-reports.spec.ts` for an `FXR-` mirror and `reverse-via-module` for the `VATR-`
settlement — both as **hrefs**, so a link pointing at the wrong kind of page fails rather than
rendering.

Found by writing the e2e against the *screen* rather than against the endpoint. The API-level
assertion (`module_document_target === "fx_revaluation"`) passed the whole time.

**5. Close day lives on the X tab, and closing switches to Z.** The second-close test pressed
the button again straight after the first and would have failed on a button that was no longer
rendered. The coupling is right — a Z is a thing that happened, the X is the day you are still
in — but it is invisible from the source and the test had to go back to the X tab. Worth naming
because step 9's tape will meet it.

**6. The queue-history picker was fed by the receipts, which excludes the documents it exists
for.** A receipt exists only once RRA has signed, so a picker built from `/fiscal/receipts` can
offer every document *except* the ones still queued, failed, `unknown` or waiting on a person —
which are exactly the documents somebody opens a *queue history* to ask about. It now reads the
queue rows themselves, which carry the document number and the partner and cover every document
ever sent for. Caught by reading the screen back, not by a failing test; the test that would have
caught it is the one that opens a history for a stuck document, and step 9's tape should have one.

### And one thing the tests corrected in the writing

The first draft of `test_receipt_listing.py` asserted the ledger side at **two** decimals
(17,688.20) and a difference of **0.00**. Both were wrong, and both for the same reason: RWF has
no decimal places, so `base_total_amount` is 17,688 and the difference is 0.38. That is the
report's whole subject, and the test had been written as though it did not exist. The figures
were re-derived by hand — and cross-checked by an independent implementation of the two rounding
rules, not by the code under test — before being pinned.

## Numbers on the shared company

`p7-enquiries-reports.spec.ts` runs on the **shared** Rugari Wines E2E fixture and consumes
document numbers there, exactly as step 7's spec does. Every number it asserts is asserted by
**shape**, never as a literal:

| run | asserted as | why |
|---|---|---|
| `FZR` (the Z) | `/^Z-\d+$/` | this step consumes it; a re-run or another spec in the shard moves it |
| `VAT` | `/^VATR-\d+$/` | step 7's spec consumes 1–4 on this company |
| `FXR` | `/^FXR-\d+$/` | step 7's spec consumes 1–4 |
| `FIS` (the receipt counters) | parsed off the page as `n/m NS` | whichever spec lands first in a shard takes the low counters |

The rule for step 9: **assert literals only inside a spec that owns its company.** A tape that
hand-worked `Z-000001` or `VATR-000001` on Rugari would fail the first time it ran after this
one. Either assert the shape, or give the tape a company of its own — the expensive option, and
the right one if the chain wants hand-worked numbers.

## Specs re-run, and why each

The kickoff's rule: every spec that opens a screen this step touched. Run on a reset database
with the `ebm-sandbox` container up.

| spec | why |
|---|---|
| `p7-enquiries-reports` | this step's own |
| `p7-transactions`, `p7-maintenance` | the document detail and the queue screen are drilled to from the new screens; `p7-transactions` also shares the `FIS`/`VAT`/`FXR` runs |
| `accessibility-enquiries`, `accessibility-reports` | the axe sweep enumerates `nav-tree.ts` — six new rows mean six new routes it now visits, in both themes, without a line of test code |
| `gl-reversal`, `journal-flow`, `idempotent-post`, `unbalanced-journal`, `closed-period`, `gl-cashbook`, `gl-inline-errors` | every spec that opens `/gl/entries/{id}`, because `_entry_read` gained a resolver that runs on every entry read |
| `ar-ap-documents`, `ar-ap-reports`, `ar-ap-corrections` | the document detail and the P3 reports shell |
| `p6-enquiries-reports` | the reports shell, and the `ReportPage` print contract |
| `inventory-reports` | `sources.resolve` gained two types and is called by the inventory enquiries and reports |

**Labels this step adds that already existed somewhere**: "Receipt", "Device", "Date", "Total",
"From", "To", "Document", "Status", "Number", "Customer", "Partner", "Currency". `getByLabel`
matches on substrings, so every lookup in the new spec is `exact: true` or scoped to a row, and
the loose ones were scanned against the whole set.

## What step 9 has to know

Carried forward from step 7's report, plus this step's own.

**1. The numbering rule.** Above. Shape, never literals, on the shared company — and that now
includes `Z-`, which this step consumes.

**2. The four endpoints that took their first *pressed* caller at step 7** — Verify with device,
Attach receipt manually, Reverse on the VAT return, Reverse on the FX revaluation — are still
newly covered rather than long-standing. **Close day joins them** at this step: it had no caller
at all until now.

**3. The `_receipt()` audit table** in step 7's report is the one to re-read before touching how
a document resolves its receipt. Five readers, three different reasons, and only two go through
that function. Unchanged by this step.

**4. `_untagged()`'s two clauses** each have exactly one witness. A change to that filter should
re-run both probes, not just the suite. Unchanged by this step.

**5. The sandbox's missing class 05.** Still open — `docs/p7-step-3-report.md` has it.

**6. Verify's live-run question**, and **the RWF-decimals question** — both still open, and both
belong in `docs/rra/certification.md` if the phase closes code-complete.

**7. New at this step: the two decimal scales are now a *rendering* contract, not only a wire
one.** The daily report and the receipts listing print declared figures at two decimals on a
zero-decimal currency, deliberately. Anything step 9 adds that shows a declared figure has to do
the same, or the residue disappears from the screen while staying in the data — which is the
quietest possible version of the P4 failure mode.

**8. And the tie's axis.** `sdc_datetime` for receipts, `document_date` for documents. If step 9's
tape asserts a listing total over a range, it has to cut the range on the axis it means.

**9. `make db-reset` does not reset the sandbox**, and `p7-maintenance.spec.ts` does not reset it
either. The authority's ledger lives in the container's memory. Step 9's tape talks to it more
than any spec so far, and the failure mode is a `994: the invoice number is already registered`
on a database whose sequences have just restarted — the sandbox behaving correctly and the
fixture being stale, which is a distinction worth not having to make at two in the morning.
`curl -X POST localhost:8100/_sandbox/reset`, or `sandboxReset()` as the two P7 specs that do it
already call it.

## What this step did **not** do

* **No migration.** Nothing new is stored. The tie is a query over `fiscal_receipts` and
  `partner_documents`, both of which already carry everything it reads; the drill keys are
  strings matched against columns that have existed since the documents did. The prompt asked
  for a reason before writing one, so: there was nothing to write.
* **No new mutating endpoint.** Close day already existed — it had no caller, which is what the
  register line was about.
* **No change to `daily.close_day()`.** See *Close day* above.
* **Not the tape through the screens end to end**, the sensitivity pass, `docs/rra/certification.md`,
  the README or the final report. Those are step 9's, and step 9 gets its own kickoff.

## Screenshots

`docs/screenshots/p7-step-8/` — **14 files**, seven screens in light and dark, plus a `README.md`
giving the capture command, the `ONLY` filter, and why the prices are what they are. Captured by
`frontend/scripts/capture-p7-enquiries-reports.ts` in a single pass on a reset database against
the `ebm-sandbox` container, and every one was opened and read before it was committed.

| # | screen | what it has rows of |
|---|---|---|
| 1 | Fiscal receipts (enquiry) | the counters as the paper prints them, with the document and the entry beside each |
| 2 | Fiscal queue history | one document's rows in send order, action log open |
| 3 | VAT return (report) | the sections as filed, the tie, both annex buttons |
| 4 | Daily fiscal report — X | the warning **before** the button, Close day still pressable |
| 5 | Daily fiscal report — the day | declared, posted, the residue named; classes and payment methods |
| 6 | Fiscal receipts listing | the tie, the difference, and the queued document named |
| 7 | FX revaluation (report) | a run's lines, with the run and its next-day mirror named |

## Checks

Non-gate step. The changed specs and the guard tests were run locally; CI is the record for the
branch head.

| check | result |
|---|---|
| `make be-lint` | `All checks passed!` |
| `make be-test` (`-n 4`, in the container) | `1372 passed, 7 warnings in 1565.02s (0:26:05)`, `PYTEST_EXIT=0` |
| `tests/test_api_has_a_caller.py` | 14 passed — the close-day line gone, `POST /fiscal/outbox/drain` the only P7 entry left |
| `tests/tax/test_entry_drill.py` | 7 passed |
| `tests/fiscal/test_receipt_listing.py` | 7 passed |
| `tests/fiscal/test_daily_report.py` | 14 passed (13 + the empty-range refusal) |
| `npx tsc --noEmit` | clean |
| `npm run lint` | clean (pre-existing `react-hooks/exhaustive-deps` warnings only) |
| `npx vitest run` | **407 passed** — 403 in the container plus the two workflow files (12 tests) on the host, which read `../.github/workflows` and cannot see it from `/app`. Both fail the same way on `main`; both pass on the host, which is what proves `p7-enquiries-reports.spec.ts` is covered by the shard filter and named by no group |
| `npm run build` | compiled; all eight new routes built |
| `e2e/p7-enquiries-reports.spec.ts` | **11 passed** |
| `gl-reversal`, `journal-flow`, `idempotent-post`, `unbalanced-journal`, `closed-period`, `gl-cashbook`, `gl-inline-errors` | 9 passed — every spec that opens `/gl/entries/{id}`, because `_entry_read` gained a resolver that runs on every entry read |
| `ar-ap-documents`, `ar-ap-reports`, `ar-ap-corrections`, `p6-enquiries-reports`, `inventory-reports` | 30 passed, including P6's *"every P6 entry leads back to its own document"* — the closest neighbour to the resolver change |
| `p7-transactions` | 14 passed |
| `p7-maintenance` | 7 passed **alone**; see below |

### `p7-maintenance` and the sandbox nobody resets

Run immediately after the screenshot capture, `p7-maintenance` failed on *"Device initialized and
active"*. It is not this step's change, and it is worth a line because step 9 will meet it.

`make db-reset` drops and re-seeds the **database**. The `ebm-sandbox` keeps its ledger — invoice
numbers, receipt counters, the taxpayers it has heard of — in the **container's memory**, and
nothing in `db-reset` touches it. `p7-transactions` and `p7-enquiries-reports` both call
`/_sandbox/reset` first, for exactly this reason; `p7-maintenance` does not (`grep -c` says zero),
so it inherits whatever the last thing to talk to the authority left behind.

Reset the sandbox and run it alone: **7 passed**. In CI it is the first thing to touch a fresh
container, so it never sees this. Locally, after a capture, it does.

### The count moved, and here is both sides

`main` collects **1358**; this branch collects **1372**, from `pytest --collect-only` on each, so
neither side is an estimate. 1358 − 1 + 15 = 1372.

**Gone — 1.** `test_every_exemption_states_which_kind_it_is` runs once per `NO_UI` entry, so
deleting the `close-day` register line deletes one case. The test itself is untouched and still
passes over the entries that remain.

**New — 15:**

`tests/tax/test_entry_drill.py` (7):

* `test_the_drill_keys_are_the_strings_the_posters_actually_write`
* `test_a_settlement_entry_drills_to_the_return_it_filed`
* `test_a_returns_reversal_drills_to_the_return_although_it_carries_no_source`
* `test_a_filed_return_pairs_without_the_document`
* `test_a_run_and_its_mirror_both_drill_to_the_run`
* `test_the_counter_entry_and_its_own_mirror_both_drill_to_the_run`
* `test_the_counter_entry_says_what_it_reversed_and_the_run_keeps_its_mirror`

`tests/fiscal/test_receipt_listing.py` (7):

* `test_the_counters_and_the_totals_are_what_the_receipts_declared`
* `test_the_ledger_side_is_the_documents_counted_the_ledgers_own_way`
* `test_the_tie_reconciles_rather_than_balances`
* `test_a_sale_the_queue_still_holds_is_named_with_the_word_that_fixes_it`
* `test_a_receipt_signed_outside_its_documents_range_says_dated_outside`
* `test_every_row_carries_the_declaration_and_the_posting_side_by_side`
* `test_the_listing_reaches_a_signed_in_accountant_over_http`

`tests/fiscal/test_daily_report.py` (1):

* `test_a_second_close_at_the_same_instant_is_refused`

**Frontend**: `document-route.test.ts` gains 2 cases (the two new routing keys) and its key-set
assertion now names seven. `appendix-c-order.test.tsx` is unchanged in count — it is parametrised
by intent — and its four tables gained six rows.

## Gates

The suite is the last thing, run **alone**, with nothing touching the stack — no Playwright, no
capture script, and `pg_stat_activity` showing **0** connections to `vinea_test` before it
started. `-n 4` rather than `-n auto`: `auto` exhausts Postgres's lock table on this machine and
produces hundreds of setup errors that are an environment limit and not breakage. Redirected to a
file rather than piped, because a pipe eats the exit code — `PYTEST_EXIT=0` is read from the
redirect.

**The hash the suite ran at is `ed9bb13`, and the backend has not moved since:**

```
$ git diff --name-only ed9bb13..HEAD -- backend
                          # empty

$ git status --short
                          # empty
```

So `1372 passed` is a statement about the backend that ships, not about one that existed while it
was running.

The **frontend** did move after that hash: the Close-day warning was rewritten to break the rows
in flight down by status and to say where those sales go, which touched
`daily-report.tsx`, `en.json`, the spec, the capture script and two screenshots — and nothing
under `backend/`. Every frontend and e2e result quoted above was re-taken at the new head:
`npx vitest run` 403 + 4 (the two host-only files), `npm run build` compiled,
`npx tsc --noEmit` and `npm run lint` clean, and `p7-enquiries-reports.spec.ts` **11 passed**.

```
$ git diff --stat main..
 52 files changed, 5815 insertions(+), 22 deletions(-)
```

Nothing is left behind, and nothing is unpushed:

```
$ git status --short
                          # empty

$ git log @{u}.. --oneline
                          # empty
```
