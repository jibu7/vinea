# P7 step 9 — the tape through the screens, the certification runbook, and the phase close

Step 9 is the third **STOP** gate and the phase close. It builds one substantive thing —
membership by high-water mark, which is the defect step 8 found and could not fix inside its own
rule — and then proves the phase: the tape through the screens on a company of its own, a
sensitivity pass over eighteen guards on the tree that ships, the register at one P7 line, and
the runbook for a certification that cannot complete on this phase alone.

**Report and stop.** No PR until this is approved.

---

## A. A Z owns receipts by counter, not by clock

### The defect, restated

A Z's population was cut on `sdc_datetime` — RRA's clock, on RRA's server — while the close was
cut on `now()`, Vinea's. Let those two disagree by so much as a second and a receipt in flight
at the close comes back stamped *before* the close: it falls inside a Z whose figures are
already frozen, and the next Z opens exclusively at that same instant, so neither counts it. A
receipt in **no day at all**, on a report a revenue authority reads, on any slow day. Nothing
anywhere asserted otherwise.

It is the same shape as a VAT late entry, and decision 12 already answered that one:
**membership, not a timestamp range.**

### The key, and the argument for it

`fiscal_daily_reports.high_water_rcpt_no` — the device's **`tot_rcpt_no`** at the close. A Z owns
every receipt whose counter is above the previous Z's mark and at or below its own; the open X
owns everything above the last mark.

Why that key and not another:

* **It is strictly increasing per device across receipt types, and that is proven rather than
  assumed** — `assert_fiscal_invariants` clause 4 has asserted it since step 2.
* **It is clock-free.** Nothing about it can be moved by skew between two machines, which is the
  whole of the defect.
* **It is the number RRA keys the receipt by.** A Z reads "covers receipts 12–19" to an
  inspector holding the paper, and the screen prints exactly that.

The kickoff asks for a path where counter order is *not* insertion order — "Attach after a later
row was sent". **There is none, and the reason is structural rather than lucky.** A row in
`needs_receipt` is non-terminal and blocks the device's queue (decision 4, `outbox.BLOCKING`), so
no later row of that device can reach `sent` while an attach is outstanding; invariant 5 asserts
exactly that. The counter a person keys during an attach is therefore always above every counter
already stored and below every one still to come — and if it is not, invariant 4 refuses it as a
counter that went backwards. One key, and it holds.

### What the mark does not decide

`from_at` / `to_at` are still stored and still printed. §19.1 puts a span on the paper and an
inspector reads dates. They stopped deciding membership, and `_opened_at` says so where it
computes them:

> Display only since 0026 — what the day *contains* is `_open_from_key`'s question. The two are
> kept apart deliberately: the span is what §19.1 prints, and merging them again is how the clock
> would get back into deciding membership.

`close_day` reads the mark **after** `claim_number` takes its `SELECT … FOR UPDATE` on the
branch's `FZR` row. A device is unique per branch, so two closes of one device serialize there
and the second cannot take the same mark as the first.

### The legacy seam

`0026` is additive and nullable, and there is no alternative: `fiscal_daily_reports` is immutable
by trigger (`VN011`), so a migration that gave an existing Z a mark would be rewriting a legal
document (architecture rule 3). A NULL mark therefore means what it says — a Z closed before the
revision — and it keeps its `sdc_datetime` membership. The boundary such a Z left behind is
translated into a counter **once**, at the seam, by `_effective_mark`:

> everything the device signed at or before its `to_at` was closed by it or by a Z before it,
> because the closes tile the device's life. That translation reads the clock exactly once, at
> the seam between the old rule and the new one, and never again — the skew that motivates this
> revision is between a *receipt in flight* and a close, and a Z stored months ago has nothing
> in flight.

`backend/tests/test_p7_z_high_water_backfill.py` provisions a tenant at `0025` with a live
device, three signed receipts and a stored Z over the first two, upgrades to head, and asserts
all of it: the legacy Z's figures unchanged and its mark NULL, its membership still its clock
range and still holding receipts 1 and 2, the open X starting above counter 2 and holding only
the third, and the first Z closed after the upgrade taking mark 3 and owning `{3}`.

### Invariant 12

> **The days tile the device.** Per device, every receipt belongs to exactly one closed Z or to
> the open X: no receipt is counted by two closes, and no receipt's counter falls in a gap
> between them. Each Z's stored `ns_count + nr_count` equals the number of receipts its
> membership window holds.

Read through `daily.membership_of` and `daily.receipts_of` rather than re-implemented, because a
second spelling of "what is in this day" would be a second answer — and two parts of the system
disagreeing about which day a receipt is in is the failure itself.

**Proven sensitive by breaking the membership query** (§C, row 9): `>` to `>=` on the lower
bound makes two consecutive Zs both claim the receipt on the seam.

### The wait that came out

`frontend/e2e/p7-enquiries-reports.spec.ts` waited 1 500 ms before draining, after a close, with
this comment:

> **Past the close's second before draining, deliberately.** … a receipt signed in the very
> second a Z was taken falls inside that Z's *range* while its figures are already frozen — and
> the next Z opens exclusively at the same instant, so neither counts it.

**It is gone, and every assertion under it is unchanged.** So is the 2.05-second sleep in
`test_a_second_z_covers_only_what_came_after_the_first`, and the 1 500 ms wait in
`frontend/scripts/capture-p7-enquiries-reports.ts`. That is the proof the fix is real rather than
a timing coincidence: the tests that needed the clock to cooperate no longer do. If any of them
ever needs a sleep again, the fix has come undone.

`test_a_second_z_covers_only_what_came_after_the_first` is re-purposed to counters and now also
asserts the window it owns (`(1, 2]`). A new test beside it,
`test_a_receipt_stamped_before_the_close_still_lands_on_the_next_day`, **is** the defect: the
first close is taken on a Vinea clock running a minute ahead, the sale that follows is stamped by
RRA's clock behind it, and the receipt is on the next day anyway. Under the old rule it was on
none. Nothing is mutated to arrange it — `fiscal_receipts` is immutable, which is why the skew
has to come from the clocks, which is also where it comes from in production.

### Rows 9 and 14, re-checked

Both hold unchanged, which is what the amendment predicts: the tape closes its days minutes of
simulated trading apart, so no receipt is anywhere near a boundary.

**Row 9** — the X after the close, all zero:

```
_expect("9", "X after the close — NS", 0, after.figures.ns_count)
_expect("9", "X after the close — NR", 0, after.figures.nr_count)
_expect("9", "X after the close — gross", D("0.00"), after.figures.ns_gross)
_expect("9", "X after the close — items", D("0.00"), after.figures.items_ns)
```

**Row 14** — Z-2 for that day, NR 1 / 11 800:

```
_expect("14", "Z-2 number", "Z-000002", zed2.number)
_expect("14", "Z-2 NR count", 1, zed2.figures["nr_count"])
_expect("14", "Z-2 NR gross", D("11800.00"), D(zed2.figures["nr_gross"]))
_expect("14", "Z-2 NS count", 0, zed2.figures["ns_count"])
```

Row 14 still passes its close an instant one second on. That is no longer about membership and
the comment beside it now says so: `close_day` refuses a zero-length *display* range
(`fiscal_z_empty_range`), which is about the span a Z prints.

### The listing's "this Z" filter

The step-8 tie compared a Z's totals to the receipts listing over the Z's **timestamp range**.
Under counter membership those can differ on exactly the receipt this revision is about, so the
listing gains a **Fiscal day (Z)** filter beside the date range. Asked for a Z, the receipts side
is that Z's members — through `daily.receipts_of`, the same function the close used, so the two
cannot disagree — and the dates become the Z's own span, which is what the ledger side and the
asymmetry lists are cut on. The heading states the Z's number and its counter window.

`p7-enquiries-reports.spec.ts` points its tie at the filter, and the new cycle tape asserts
the Z and the listing against one another over four receipts.

---

## B. The tape through the screens

`frontend/e2e/p7-cycle-tape.spec.ts` — **ten tests, serial, 10 passed**, on the `ebm-sandbox`
container over HTTP.

**A tenant of its own**, signed up in `beforeAll`, which is what lets it assert the literals no
other P7 spec may: `1/1 NS`, `Z-000001`, `VATR-000001`, `FXR-000001`. On Rugari Wines E2E those
runs are consumed by whatever ran before, so steps 7 and 8 assert shape.

**And a branch of its own on the authority's side.** The sandbox keys its ledger by `tin:bhfId`
and knows four TINs, so a second company using `999000099` at `bhfId 00` would share receipt
counters with whatever ran before it. This device registers at **`bhfId 01`**, so its counters
start at one however the shards fall — which is the difference between a literal that means
something and one that passes today.

**Awkward prices, on purpose.** 1 499 exclusive is 1 768.82 inclusive: the ledger rounds each
document's tax half-up to the franc and the wire carries two decimals, so every declared figure
differs from its posted one.

| document | qty | net | tax | posted | declared | residue |
|---|---|---|---|---|---|---|
| INV-1 | 3 | 4 497 | 809.46 → 809 | **5 306** | 3 × 1 768.82 = **5 306.46** | +0.46 |
| CRN-1 | 1 | 1 499 | 269.82 → 270 | **1 769** | **1 768.82** | +0.18 back |
| INV-2 | 2 | 2 998 | 539.64 → 540 | **3 538** | **3 537.64** | −0.36 |
| INV-3 | 1 | 1 499 | 269.82 → 270 | **1 769** | **1 768.82** | −0.18 |

### The chain, and what each step asserts

| # | Through the screens | The figure |
|---|---|---|
| 0 | Register a device, **Initialize** against the container over HTTP, **Sync codes** | `SDC010000005`, `MRC WIS01006230`, `Active`, `Keys held`, read back after a reload |
| 1 | Key an invoice with a purchase code; the queue says it went | `pending 0`, `Flowing`, `Sent`; the document totals **5,306** |
| 1 | The receipt on the document | **`1/1 NS`**, `SDC010000005`, `copy_count 0` |
| 1 | **The printed sheet** (`pdftotext` on `page.pdf()`) | `SDC INFORMATION`, **`1/1 NS`**, the SDC id, the MRC, **`5,306`**, `AB12CD`; and **no** `THIS IS NOT AN OFFICIAL RECEIPT` |
| 3b | Copy print | `copy_count 1`; the paper carries **`COPY`**, **`THIS IS NOT AN OFFICIAL RECEIPT`**, and the **same** SDC block — one signature, reprinted |
| 3 | Credit note: the *Refund of* picker, the §4.16 reason | **`1/2 NR`** — its own run, sharing the total |
| 5 | Authority **down** under a posted sale; Print refused; **Retry now** pressed while it is still down; authority up; Retry again | Print disabled with `title=/queued/i`; pending > 0 after the first retry; **`2/3 NS`** after the second |
| 6 | `accept_then_timeout` → `Blocked`, Retry **disabled**, **Verify with device** → `Needs a receipt`, **Attach receipt** with the six fields off the authority's own ledger | **`3/4 NS`**, and equal to the sandbox's own `rcptNo/totRcptNo` |
| 7 | A supplier invoice registers on its own `FIP` run | the queue clears; a `Purchase` row is on it |
| 8 | **EBM purchases** → Fetch → **Accept** | the feed row's **11,800**, then gone from the undecided list |
| 9 | **Close day** | **`Z-000001`**, **`Covers receipts 1–4`**, NS 3 / **10,612.92**, NR 1 / **1,768.82**, net **8,844.10**, items 6 / 1, posted **8,844**, residue **0.10**, queued 0 |
| 9 | The listing, filtered to **this Z** | NS 3, NR 1, **10,612.92**, net **8,844.10**, and each receipt's own declaration on its row |
| 10 | The VAT return | output **1,349**, input **324**, net **1,025**, both tie chips *Reconciled* |
| 11 | File | **`VATR-000001`**, and the filed row carries the net the preview showed |
| 12 | A backdated entry into the filed month | the filed return **unchanged**; next month's return lists it, base **1,000** |
| 13 | Revaluation: preview, post, the mirror, reverse | difference **708**, **`FXR-000001`**, `mirror-FXR-000001` visible, then reversed |
| — | **The company with no device** | no purchase code demanded of a customer with a TIN, **no devices at all**, Print not gated; and on the paper: the total, **no** `SDC INFORMATION`, no SDC id, no `Internal Data` |

Two things the run taught, both now comments in the spec:

* **Retry now is offered for `queued` and `failed` rows only**, and the queue is in send order, so
  the first Retry button on the page belongs to an item registration signed long before. A bare
  `click()` on a disabled control waits for it to become enabled with **no timeout** — Playwright's
  config sets none for actions — so getting that wrong is a hang rather than a message. The spec
  scopes the button to the queued row and asserts it enabled first.
* **`getByLabel("To")` matches the shell's "Toggle theme"** as a substring and fails strict mode.
  Both range pickers use anchored regexes now.

---

## C. The sensitivity pass, on the tree that ships

Run at `74c1c0c`, one guard at a time: the guard is broken by a textual patch, the named test is
run in the backend container, and the file is restored with `git checkout --` — so the restore is
git's guarantee rather than this pass re-writing what it thinks the original was. The driver
printed a line per guard and `git status` came back clean of every patch afterwards.

| # | Guard | Broken by | Caught by | What failed |
|---|---|---|---|---|
| 1 | same-transaction enqueue | `enqueue()` returns before inserting | `test_posting_an_invoice_queues_its_sale_in_the_same_transaction` | `StopIteration` — there is no row to find |
| 2 | per-device ordering (FIFO) | the drainer stops refusing a `BLOCKING` head | `test_a_refused_row_blocks_the_queue_behind_it` | `a failed head is not retried by a timer, and nothing behind it may overtake it` |
| 3 | `fiscal_requires_block` | the refusal returns instead of raising | `test_allowing_negative_stock_is_refused_while_a_device_is_active` | `DID NOT RAISE AppError` |
| 4 | `purchase_code_required` | the TIN test is short-circuited | `test_a_sale_to_a_customer_with_a_tin_needs_a_purchase_code` | `DID NOT RAISE PostingError` |
| 5 | `refund_exceeds_original` | the quantity test is short-circuited | `test_a_credit_note_returning_more_than_was_invoiced_is_refused` | `DID NOT RAISE PostingError` |
| 6 | the high-water rule (VAT) | `id > ret.high_water_entry_id` → `id > 0` | `test_an_entry_dated_in_the_month_but_posted_before_filing_is_not_late` | `assert (LateEntry(...), ...) == ()` — an entry that was always in the return is declared late |
| 7 | the revaluation never touches a control account | the contra becomes the AR/AP control account | `test_a_gain_posts_to_the_revaluation_account_and_never_the_control` | `Account 1200 is the AR control account; the gl module may not post to it` |
| 8 | key redaction | `redact()` returns its argument | `test_redact_reaches_a_nested_key` | `assert 'secret' == '***'` — **see the note below** |
| 9 | the Z's membership by counter | `>` → `>=` on the window's lower bound | `test_a_second_z_covers_only_what_came_after_the_first` | `assert [1, 2] == [2]` — the seam receipt is in both days |
| 10 | `_untagged()` clause A | `module != TAX` → `module == TAX` | `test_a_reversed_ordinary_journal_still_lists_as_two_rows` | `assert [] == [Decimal('-500…')]` — an ordinary reversed journal vanishes from the tie |
| 11 | `_untagged()` clause B | both sub-clauses made vacuous | `test_filing_and_reversing_leaves_the_return_it_found` | `a settlement and its own reversal cancel in the totals and must not survive as two untagged rows` |
| 12 | `_receipt()`'s default | the latest receipt instead of the document's own | `test_a_reversed_sale_keeps_its_own_receipt_and_prints_the_refund_separately` | `assert 'NR' == NORMAL_SALE` — the invoice reports the refund's counters |
| 13 | the `reverses_entry_id` hop in the P7 drill | the second candidate is dropped | `test_the_counter_entry_and_its_own_mirror_both_drill_to_the_run` | `assert (None, None, None) == (1, 'FXR-0000…')` |
| 14 | the tie's two axes | `dated_outside` is never returned | `test_a_receipt_signed_outside_its_documents_range_says_dated_outside` | `assert […'other_branch'] == […'dated_outside']` |
| 15 | `registration_hash` over values, not representations | `default=canonical` → `default=str` | `tests/test_fingerprints.py` | `assert '{"amount":"0"}' == '{"amount":"0.0"}'` |
| 16 | invariant 1's activation window | `moment < since` dropped from the skip | `test_an_invoice_posted_before_the_device_went_live_is_not_a_hole` | `INV-000001 posted at … on a branch whose device has been live since … and it has no queue row` |
| 17 | `purchase_already_declared` | the refusal removed, not merely renamed — the duplicate check returns instead of raising | `test_an_unlinked_accept_of_an_invoice_already_declared_is_refused` | `DID NOT RAISE FiscalSetupError` |
| 18 | the three immutability triggers | `ALTER TABLE … DISABLE TRIGGER`, in a transaction that is rolled back | `tests/fiscal/test_immutability.py`, `test_a_filed_return_refuses_every_change_but_its_withdrawal` | see below |

### Row 8 — the one worth reading twice

The pass first pointed `redact()`'s breakage at
`test_every_stored_json_column_is_free_of_key_material`, which is what the phase has always
called the key-redaction guard. **It passed with `redact()` disabled**, and so did the whole
`osdc` leg of the acceptance tape, which is the only profile that puts a key on the wire at all.

That is not a hole; it is the guard being in a different place from where the name suggests, and
it is worth writing down:

* **No response model has a field for a key** (`test_no_response_schema_has_a_field_for_a_key`),
  so nothing can be serialised out.
* **`cmcKey` is added at `_body()`, on the way to the wire**, and never reaches the rendered
  payload that `fiscal_outbox.payload` stores. The `redact()` in `render()` is a belt over a
  state that today cannot occur — its own docstring says so.
* **The keys are Fernet ciphertext at rest** on `fiscal_devices`, which is not a JSON column.

So `redact()` is load-bearing for one thing: the **initialization response**, which is the one
place the keys legitimately arrive, and `test_redact_reaches_a_nested_key` is what catches its
removal. The walk over stored JSON is anti-vacuity insurance — it asserts `examined > 0` for
exactly that reason — rather than the catcher. Both are kept; the report now says which is which.

### Row 18 — the triggers, and the tests that were missing

`fiscal_receipts`, `vat_returns` and `fiscal_daily_reports` have been immutable by trigger since
revision `0022`, and **nothing was asserting it.** The suite knew about the guards only sideways:
two sensitivity tests make their breakages on the ORM's copy *because* the row cannot be written,
and this step's own back-fill test met `VN011` by accident while trying to arrange a clock skew.
Each of those is evidence that the trigger fires; none would have failed if somebody had dropped
it. The Definition of Done names these two among the guards this pass has to revert — so the
pass could not be run until the tests existed.

They exist now (`backend/tests/fiscal/test_immutability.py` and one test in
`tests/tax/test_vat_filing.py`), and each asserts **both halves**: the refusal, and the one change
the trigger deliberately allows — because a trigger that refused everything would be wrong in a
way no "it raises" test could see. A reprint has to be countable; a filed return has to be
withdrawable.

The guards themselves are proven by dropping them, in a transaction that is rolled back:

```
fiscal_receipts       with the trigger:  ERROR:  fiscal receipt 1 is immutable; only copy_count may change
                      without it:        ALTER TABLE / UPDATE 1
vat_returns           with the trigger:  ERROR:  VAT return 1 is filed; only its reversal may be recorded
                      without it:        ALTER TABLE / UPDATE 1
fiscal_daily_reports  with the trigger:  ERROR:  fiscal daily report 1 is a close and is immutable
                      without it:        ALTER TABLE / UPDATE 1
```

---

## D. The register

`backend/tests/test_api_has_a_caller.py` — **14 passed**. The P7 section carries exactly one
entry, and the comment above it is the register's own account of how it got there:

> ```python
> "POST /api/v1/fiscal/outbox/drain": (
>     "by design — the EBM queue's scheduler hook. The queue drains by itself: a worker "
>     "loop every fifteen seconds (`python -m app.fiscal.worker`, a service in "
>     "docker-compose) and an after-response kick from the posting that filled it. This "
>     "endpoint exists for a deployment that runs no worker and drives an external "
>     "scheduler instead, and for the e2e stack, which drives it rather than waiting. The "
>     "same shape as `jobs/sweep`: there is no moment at which a person wants to press it."
> ),
> ```

> **Step 8 closed the last P7 gap.** `POST /fiscal/devices/{device_id}/close-day` carried a
> `GAP (P7, step 8)` line here until Reports → Tax → Daily fiscal report shipped with a **Close
> day** button on the X view, under `fiscal:close_day` and pressed by
> `e2e/p7-enquiries-reports.spec.ts`. After this step the register carries exactly one P7 entry —
> the drain — and step 9 inherits no surprise.

**The rest of the register, unchanged by this step** — listed because the kickoff asks for them
and because "changed none" is a claim worth being able to check:

| Entry | Kind |
|---|---|
| `POST /api/v1/subledger/jobs/sweep` | `by design` — the retention reaper (P4) |
| `POST /api/v1/fiscal/outbox/drain` | `by design` — the scheduler's hook (P7) |
| `POST /api/v1/operator/tenants/{company_id}/activate` | `GAP (SaaS admin, Appendix C.2)` |
| `POST /api/v1/operator/tenants/{company_id}/suspend` | `GAP (SaaS admin, C.2)` |
| `POST /api/v1/operator/tenants/{company_id}/impersonate` | `GAP (SaaS admin, C.2)` |

Five entries: two `by design`, three `GAP`, none of them P1's, P4's, P5's or P6's debt. P6 carries
no entry at all, which is what its own Definition of Done required.

---

## E. `docs/rra/certification.md`

The runbook, and it opens with the thing a reader most needs and would otherwise find in a
footnote:

> **Read this first: P7 alone cannot pass certification, and that is by design.** The checkpoint
> sheet requires **training receipts (TS/TR, rows 11, 41, 42)**, a **proforma receipt (PS, rows
> 12, 43)** and a **PLU report (row 24)**. All three are explicitly out of scope for Phase 7 …
> they are work that has not been scheduled.

What is in it:

* **The application** — where it goes (`cis_sdc_certification@rra.gov.rw`) and the ten
  enclosures, four of which are the company's and **six of which do not exist**: brochure,
  warranty statement, user manual, installation guide, programming manual, support SLA. The list
  is marked **as of 16 September 2026** with an owner item on top of it, because nothing in this
  repository can re-check RRA's page and a list transcribed from memory is worse than one with a
  date on it.
* **The MRC scheme** `BBBCCNNNNNN` and where Vinea keeps each part: it keeps **none** of the
  first five characters, because they are facts about the vendor's certification that RRA issues,
  and it stores whole what the authority returns in `fiscal_devices.mrc_no`. The last six are
  `dvc_srl_no`. A build that generated an MRC would be inventing an identifier RRA owns.
* **The test environment** — `myrratest.rra.gov.rw`, the approved TIN / branch id / serial,
  `sdcsandbox.rra.gov.rw` — with the run written out as four screens, and the instruction to
  record it as `docs/rra/sandbox-run-<date>.md` with the six answers in it.
* **The checkpoint mapping: all 75 rows**, each to the screen, test or document that shows it, or
  to **not in P7** with what it would take. **Twelve** are not in P7: rows 1–5 (the owner's
  documents), 11, 12, 41, 42, 43 (training and proforma), 24 (the PLU report), and 21 — a
  verifiable software version number on the receipt, which is one line in the layout and a build
  stamp to put in it, and the only one of the twelve that is small.
* **Ten open questions**, each with the *evidence* that closes it rather than the opinion that
  would.

---

## F. The Definition of Done, as a checklist with no third state

Each clause is either met, with the artefact that shows it, or **not met**, with why. Nothing is
"partly".

| Clause | Status |
|---|---|
| The acceptance tape reproduces every expected value to the rounding rule | **met** — `tests/fiscal/test_acceptance_tape.py`, nineteen rows, every figure a literal worked by hand |
| `assert_fiscal_invariants` passes: one row per posted document, one receipt per sent row, `invc_no` gapless, counters strictly increasing | **met** — twelve clauses, asserted after every row of the tape and after every step of the property machine; each proven sensitive (§C) |
| A queued invoice survives the sandbox down through three backoff steps and completes in order, with the stock report behind it | **met** — tape row 5; and through the screens in `p7-cycle-tape.spec.ts` row 5, where the authority is down under a posted sale and **Retry now** is pressed while it still is |
| A response that never arrived is resolved by verification and manual attachment, never by a blind resend | **met** — tape row 6; through the screens in the cycle tape, ending at `3/4 NS` equal to the authority's own counters |
| The receipt prints in the CIS layout with the SDC block and QR, refuses to print before the receipt exists, and reprints as a COPY | **met** — asserted on the **paper** by `pdftotext`: `SDC INFORMATION`, `1/1 NS`, the SDC id, the MRC, the total, the purchase code; the copy carries `COPY`, the §15 warning and the same SDC block; Print is disabled with the row's own status while the sale is queued |
| A fiscalized company cannot sell what it does not hold and cannot invoice a TIN without a purchase code | **met** — `fiscal_requires_block` and `purchase_code_required`, both proven sensitive (§C rows 3 and 4) |
| The VAT return ties with every untagged movement listed; a filed return never changes; a late entry lands in the next one | **met** — `tests/tax/`, and through the screens: output 1,349 / input 324 / net 1,025, both chips *Reconciled*, `VATR-000001` filed and unchanged by a backdated entry that lands on the next return |
| The settlement entry posts through the kernel and reverses only through the return | **met** — `VatReturnPosted`, `module_reversal("tax")` |
| The FX revaluation moves the revaluation accounts and never the control accounts, and its mirror nets it out the next day | **met** — §C row 7; `FXR-000001` with `mirror-FXR-000001` on the run, through the screens |
| Nothing outside `app/fiscal/` knows an RRA field name | **met** — `tests/fiscal/test_boundary.py`, including the read side (`normalize_declared_totals`) |
| No device key is readable through any endpoint or present in any stored payload | **met** — and §C row 8 says precisely which guard does the work and which is insurance |
| Every P7 row is live, untagged, opened by a test with data and shown in a committed screenshot | **met** — steps 6, 7 and 8 shipped the screens with their screenshots; this step adds the five the DoD names in `docs/screenshots/p7-step-9` |
| The tape runs under both route profiles against the sandbox | **met** — `test_the_tape_runs_under_the_osdc_profile`, rows 0–3 |
| The live run against `sdcsandbox.rra.gov.rw` is recorded, **or** the phase closes code-complete, certification pending with the runbook written and the deviation named | **met, second branch** — precondition (d) was never held; `docs/rra/certification.md` is written and the deviation is named in `docs/p7-final-report.md` and in the README |
| Nothing writes to the ledger outside the PostingEngine | **met** — `kernel_require_posting_engine`, unchanged by this phase |
| Every audited call passes a real actor | **met** — P1's audited-service rule, unchanged |
| Every mutating endpoint has a caller or the one `by design` line | **met** — §D: one P7 entry, `by design` |
| CI green on backend and frontend | see §G |

**One clause reads differently after this step**, and it is worth naming rather than leaving to
be noticed: "the receipt counters strictly increase" was always asserted; what was *not* asserted
until now is that the **days tile the device**, which is invariant 12 and the reason for §A.

---

## G. The gates

---

## H. What this step changed, and what it deliberately did not

### Changed

* **`0026_p7_z_high_water`** — one nullable column, no UPDATE, no back-fill. §A.
* **`app/fiscal/daily.py`** — `Membership`, `receipts_of`, `membership_of`, `open_membership`,
  and `close_day` storing the mark under the lock it already takes. `compute()` no longer takes
  a date range; it takes a membership.
* **`assert_fiscal_invariants` clause 12**, and two sensitivity tests for it.
* **The receipts listing's "this Z" filter** — backend, schema, hook, screen — and the counter
  window on the Z listing and the X.
* **`frontend/e2e/p7-cycle-tape.spec.ts`** — new, ten tests. §B.
* **`backend/tests/fiscal/test_immutability.py`** and one test in `tests/tax/test_vat_filing.py`
  — the guards the sensitivity pass could not otherwise run. §C row 18.
* **`backend/tests/test_p7_z_high_water_backfill.py`** — the rule-10 proof `make migrate-check`
  cannot reach.
* **Three waits removed** — the e2e's, the unit test's, and the capture script's. §A.
* **Docs** — the prompt's decision 11 and decision 15 amended and its stale C.1.10 corrected,
  with both approvals rows opened; `docs/rra/certification.md`; `docs/p7-final-report.md`; the
  README; Appendix C's three missing rows and the Master Plan's "P7 as built"; the step-9
  screenshots.

### Not done, deliberately

* **No live run.** Precondition (d) is not held and no application is outstanding. The runbook
  says what it would take and the phase closes on the DoD's second branch.
* **No training or proforma receipts, and no PLU report.** Out of scope by the phase prompt, and
  named in the runbook as the reason certification cannot complete on P7 alone.
* **Nothing outside `app/fiscal/` learned an RRA field name.** `tests/fiscal/test_boundary.py` is
  green, and the new membership code is neutral by construction: `tot_rcpt_no` is a column of
  Vinea's own table.
* **No change to the queue, the drainer, the payload map or the receipt layout.** The one
  backend change this step makes is the one step 8 handed it.

### Plan deviations, as they will appear in the final report

Seventeen of them, with reasons, in `docs/p7-final-report.md` — the route-profile reading, the
VAT filing entry, the purchase-registration rule, decision 6's Sage/Ishyiga provenance, decision
11's membership amendment, decision 15's seventh permission, the C.1.10/11/12 renumbering, tape
rows 5 and 6, the `_receipt()` default, `_untagged()`'s asymmetry, the reversal's `NR` on the
invoice detail, Defaults' five accounts, the fourteen-line register, the missing "Period end"
group, the movement reported by the document rather than by the stock service, the RRA logo
placeholder, and the code-complete close.

---

## I. What the next reader should know

1. **The mark is read after the sequence claim, and that ordering is the lock.** Moving the
   `claim_number` call after the counter read would put two closes of one device back in a race.
   `close_day` says so where it does it.
2. **A legacy Z's membership is its clock range, forever.** There is no back-fill and there
   cannot be one: the table is immutable. Anything that reads a Z must go through
   `membership_of`, which is the only place that knows both forms.
3. **The `bhfId` trick in the cycle tape is load-bearing.** The sandbox keys its ledger by
   `tin:bhfId` and knows four TINs. Any future spec that wants literal receipt counters needs a
   branch id no other spec is using.
4. **Retry now is enabled for `queued` and `failed` only**, and a Playwright action has no
   timeout. `.first()` on a button that is usually enabled is a hang waiting to happen.
5. **The key-redaction guard is not where its name suggests** — §C row 8. Before changing
   `redact()`, read that section: the test that catches it is the unit test, and the walk over
   stored columns is insurance.
6. **`_late` emits one row per journal line**, so a three-line correction is two rows on the next
   return — the base on the revenue line and the tax on the tax-account line. A test that asserts
   `.first()` is asserting the query's ordering.
7. **The certification runbook has an owner item at the top of it** and the enclosure list carries
   a date. If the date is old, the list is a historical record and not a requirement.
