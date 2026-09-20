# P7 step 7 — the transaction UI

Step 7 is the second of P7's three UI steps and it is **not a gate**: the changed specs and the
guard tests were run locally, the full backend suite was run because two of the changes are in
`app/fiscal/`, and CI is the record for the branch head.

What it builds is everything a fiscalized company *does*, as opposed to everything it is set up
to do. Steps 1–5 built a posting hook, an outbox, a drainer, a receipt store, a purchase feed,
an import register, a VAT return and an FX revaluation, and shipped **fourteen mutating
endpoints with no caller**. A company could be fiscalized by step 6's screens and then had no
way to see its own queue, decide a purchase RRA was holding, print a receipt, file a return or
revalue a currency. Those fourteen lines are gone.

## What landed

**Four new screens under Transactions → Tax** (Appendix C.1.11, the C.2 promise made good):

| screen | route | what it does |
|---|---|---|
| Fiscal queue | `/fiscal/queue` | a card per device with status counts, the oldest queued age, the offline flag and the head row; the rows in send order; **Retry now**, **Verify with device**, **Attach receipt manually**, and a row's request, response and action log |
| EBM purchases | `/fiscal/purchases` | the feed by decision, **Fetch**, **Accept** (with the AP document the purchase became) and **Reject** |
| Import declarations | `/fiscal/imports` | the register by status, **Fetch**, **Approve** against a Vinea item, **Reject** |
| VAT return | `/tax/vat-returns` | the range, the sections, the tie, the late entries, **File**, and the filed returns with **Reverse** on detail |

**One new screen under Transactions → General Ledger** (Appendix C.1.12): **FX revaluation**
(`/gl/fx-revaluations`) — date, role, the preview per open foreign-currency document, **Post**,
and the runs with **Reverse**.

**Four screens gained what a fiscalized company needs to key on them:**

| screen | what it gained |
|---|---|
| Invoice, Credit note (AR and AP) | a **Fiscalization** section: payment method always; purchase code once the partner has a TIN, marked required on a fiscalized company; and on a fiscalized AR credit note the *Refund of* picker and the §4.16 reason |
| Document detail | the fiscal status chip, the receipt block, the **CIS §13/§14 print layout** with its SDC block and QR, Print gated by `fiscal_receipt_pending`, **Copy print**, and the three queue actions on the document |
| The reversal dialog | the refund reason on a fiscalized invoice, and `fiscal_refund_irreversible` / `fiscal_status_unresolved` said **before** the button rather than after it |

## Three backend changes, all of them reads

None of this step's screens needed a new mutating endpoint. Three reads were added or widened,
and each closes a hole the UI made visible.

**1. `GET /fiscal/document-context`** — `{fiscalized, refund_reasons}`, on tenant context with
**no fiscal permission**.

The Invoice and Credit-note screens gate on `ar:transactions_post`. The seeded **Sales Manager**
role holds that and neither `fiscal:setup_manage` nor `fiscal:reports_view`, so answering "is a
purchase code required here" out of `/fiscal/devices` would mean handing every sales manager the
authority to reconfigure the devices in order to key an invoice. Both answers are public facts
about the company — whether it declares, and the thirteen reasons RRA publishes for a refund —
and neither is a device, a key or a payload. `tests/fiscal/test_devices_api.py` proves the clerk
is still refused `/fiscal/devices` and `/fiscal/codes` and is not refused this.

**2. `DocumentSummary.fiscal_receipt_id`.** The *Refund of* picker is a listing: it offers the
partner's **fiscalized** invoices, and the only other way to know which those are is
`/fiscal/receipts`, which an AR clerk cannot read. The document listing needs `ar:reports_view`,
which they do have.

**3. `normalize_declared_lines()` on the adapter, and `ReceiptBlock.lines`.** The printing
module's own docstring already promised this — "step 7 renders the CIS receipt … the taxpayer
block, **the lines**, the totals, the SDC block and the QR, and everything it needs is assembled
here, neutral" — and the block carried everything except the lines. Building the printed lines
from the *document* instead was tried first and is wrong twice over:

* **A foreign-currency document is declared in RWF** from its frozen base amounts (decision 3).
  A receipt printing the document's own line amounts puts dollars under a franc total. Tape row
  4 now asserts the printed line at `prc 3 115.20` and `taxable 62 304` against a document whose
  own total is USD 47.20.
* **A line keyed with an item and no typed description has `description = NULL`**, and CIS §4 e
  requires a description on the paper. The declaration carries `itemNm`.

Tape row 1 asserts the four printed lines against the prompt's own literals, and that they sum
to the receipt's taxable total — the property a receipt whose lines came from somewhere else
would not have.

## The sandbox publishes §4.16

The credit note's reason picker is fed by `fiscal_codes`, the synced table, because
`RefundReason` lives in `app/fiscal/rwanda/` and nothing outside `app/fiscal/` may import it. The
sandbox published four code classes and not that one, so the picker would have been empty on
every company the e2e stack ever set up. It now publishes all thirteen, driven off the enum so
it cannot drift, with `REFUND_REASON_NAMES` beside the codes in `rwanda/codes.py`.

Published in **full** rather than sampled, deliberately: this is the one code table an
*operator* picks from directly, and a sandbox offering three of thirteen would leave the picker
looking complete and refusing the other ten at post.

## The reversal's refund, moved here from step 8 — and what `_receipt()` decides

Decision 7: reversing a fiscalized invoice queues a **full refund** rather than cancelling the
sale, so the document ends up holding two receipts — the `NS` it was declared under and the `NR`
that reversed it. The `NR` has no document of its own. If it is not reachable from the invoice it
is not reachable at all, and it is a legal document the customer is owed. So the panel lists
both and either prints.

**Changing `_receipt()`'s default is a semantic change, not a UI one**, and the first attempt at
this got it wrong: it returned the latest, which made a reversed invoice's own detail screen
report the refund's counters as though they were its own — a panel saying something true about
the wrong receipt, the defect class rule 13 exists for. The default is the document's **own**
receipt. This document *is* an invoice; the refund is a receipt *about* it.

**The audit — every reader of a document's receipts, and what each one sees:**

| reader | resolves it how | what a reversed invoice gives it |
|---|---|---|
| Document detail header, print gate, CIS layout (`printing.receipt_block`) | `_receipt()` | the **sale** — `n/m NS`, after the reversal as before it |
| Copy counter (`printing.record_copy`) | `_receipt()` | whichever is being reprinted, each with its own `copy_count` |
| Receipts enquiry / the panel's list (`enquiries.receipts`) | joins `FiscalReceipt.document_id` | **both**, in the order the authority issued them |
| VAT sales annex (`tax/annexes.py`) | reads `PartnerDocument.fiscal_receipt_id` **directly** | the **sale's** counters, which is what decision 12 asks of it |
| The *Refund of* picker (`schemas/subledger.py`) | the same column | the sale, which is what makes the invoice offerable |

Those are the only five. `_receipt()` itself has exactly **two** callers, both in `printing.py`;
nothing in `enquiries.py` or `annexes.py` goes through it, so neither silently flipped. The audit
is in the function's own docstring, where the next reader of it will find it.

`test_a_reversed_sale_keeps_its_own_receipt_and_prints_the_refund_separately` in
`tests/fiscal/test_reversal.py` asserts those separately rather than asserting that one function
returns one row: the header is the `NS`; the refund prints as its own receipt naming the sale it
refunds (§14); **copy counters are per receipt** — reprinting the sale after the reversal moves
the sale's count and not the refund's, and queues **no outbox row**, because a copy is a print of
a sale already declared; the enquiry returns both; and the annex names the sale. **Proven
sensitive**: flipping the default back to "latest" fails it on the first assertion
(`assert 'NR' == 'NS'`).

The e2e asserts the screen half — both counters on the page, Print pointed at the `NS`, the `NR`
one press away and selectable.

## A step-4 defect this step's spec found

Handing the VAT range back turned up a real one on `main`, and it is fixed here rather than
carried.

Filing posts Dr 2200 / Cr 1400 / net 2250 on a date **inside** the range it is about, and
reversing posts the mirror. Those are movements on the very accounts the tie reads. The figures
come back either way — a settlement and its own reversal cancel, and the settlement's lines carry
`tax_amount 0` so neither is ever counted as tax. But the **untagged list** did not come back:

```
sections                identical
ties[1400].untagged     before  []
                        after   [('VATR-000001', '-9000.000000'),
                                 ('VATR-000002',  '9000.000000')]
difference 0 · reconciled True
```

Two rows that explain nothing, the tie balancing over them, and the pair surviving — file and
reverse a range twice and the explanation carries four such rows. Self-consistent, and wrong.

The fix is narrow. `_untagged()` drops a `tax`-module entry that has been reversed **together
with** the entry that reversed it. A **standing** settlement still shows, which is decision 12's
whole reason for having them in scope — a VAT payment to the authority is exactly the kind of
movement the difference exists to surface. And it is limited to the `tax` module: an ordinary
journal and its reversal are two acts an accountant may well want to see.

`tests/tax/test_vat_filing.py::test_filing_and_reversing_leaves_the_return_it_found` is the pin,
beside the other `test_vat_*` cases rather than only in an e2e. **Proven sensitive** — removing
the clause reproduces the two rows above. The e2e asserts the same round trip through the
preview the screen renders, with one field excluded and asserted separately:
`high_water_entry_id`, which legitimately moves because entries really were posted and which
nothing a reader sees depends on.

## Decisions worth review

**The *Refund of* picker offers posted fiscalized invoices, not open ones.** The prompt says
"open fiscalized invoices of the partner". The service does not require the original to be open
— it refuses on cumulative refunded quantity (`refund_exceeds_original`, CIS §7.17), never on
whether the invoice has been paid — so a picker filtered to `open_amount != 0` would make a
legitimate credit note against a settled invoice unkeyable through the UI while the API still
accepted it. The filter is `status = posted` and `fiscal_receipt_id IS NOT NULL`. If the owner
means "open" strictly, it is one line in `document-screen.tsx`.

**A month-end revaluation needs the next period open.** Decision 13 posts the run at the
revaluation date *and its mirror the following day*, in one transaction, so a run dated 30
September reaches into October — and the kernel refuses a posting into a `future` period. This
is not a defect and the refusal is the right one; it is a sequencing fact worth writing down,
because the failure reads as `period_not_open` on a date the operator did not choose. The e2e
opens the next period before posting, which is what an accountant does by hand.

**The VAT return's tie chip says "Reconciled" while the difference is non-zero, and that is the
word working.** After filing, the settlement entry is itself an untagged movement on the range
it settled: its lines carry the tax codes with `tax_amount 0`, so they move `2200` and declare
nothing. The tie is reconciled when every franc of the difference is named by a line the report
lists — which it is, as `VATR-000001`. A tie that went red there would be telling an accountant
something false.

**The RRA logo is a bordered placeholder** (CIS §7.29). The asset is owner-supplied and has not
arrived. Named here rather than approximated with something that is not RRA's mark; it carries
into the phase report if it is still outstanding at step 9.

## Plan deviations

* **The routes are plural** — `/tax/vat-returns` and `/gl/fx-revaluations`. The prompt's step-7
  list writes them singular; the rule-14 register had already named the plural ones, and plural
  is the repo's own listing convention (`/ar/documents`, `/inventory/documents`,
  `/oe/sales-orders`): a screen that lists rows and opens one is plural. What the earlier
  register comment got wrong was the **placement** — there is no "General Ledger → Period end"
  group in the owner's tree, and the FX revaluation row sits directly after Cashbook batches
  under Transactions → GL, which is where it is. Labels and placement follow the prompt; only
  the path spelling follows the repo. Both corrections are in the register where the fourteen
  lines were.
* **`qrcode.react` is a new frontend dependency.** The CIS receipt prints a QR whose content is
  §7.24.7's, assembled by the backend and stored with the receipt — nothing on the client
  composes it. Its lockfile entry was generated with the **image's own npm**: the host's npm 11
  prunes a nested `@swc/helpers` that npm 10's `npm ci` then refuses to install around, so a
  lock written by the host fails the Docker build.
* **The register held fourteen `GAP (P7, step 7)` endpoints, not fifteen.** Its own prose said
  "Six lines" + "Five lines" + four acts = 15, but the middle block listed four entries. The
  count is checkable — `git show main:backend/tests/test_api_has_a_caller.py`, count the keys
  between the two section markers — and it is fourteen. Corrected in the register rather than
  carried. All fourteen are deleted by a screen a person can open and press; **none** by a hook
  written to satisfy the matcher, and `verify` and `attach-receipt` in particular are now
  pressed by the e2e rather than merely called.

## What the tests prove

**`frontend/e2e/p7-transactions.spec.ts`** — ten tests, serial, one file, on a reset database
with the `ebm-sandbox` service up. It activates a device at the start and suspends it at the
end, the way `p7-maintenance.spec.ts` does, so a shard that runs it and then the AR/AP tape does
not find the tape refused for reasons that have nothing to do with it.

Figures read off the page, per rule 13:

| screen | figure |
|---|---|
| VAT return, filing | the range is filed, a second filing is refused `already filed over …`, Reverse frees it, and the spec ends with **no posted return** on the fixture |
| The company with **no device** | `fiscalized: false`, no purchase-code requirement against a customer who has a TIN, no refund reason on its credit note, and an empty state on the queue — asserted on Kivu Traders, not assumed from the primary company's absence of one |
| Invoice | the purchase code is demanded inline, and the posted document totals **59,000** (25 x 2 000 + 18 %) |
| Document detail | the receipt counter in its CIS §7.25 shape, the authority's own `SDC010000005`, and the copy count 0 → 1 |
| Fiscal queue | the device's pending count, and a payload with no key in it |
| EBM purchases | **11,800** taxable on the one purchase the authority is holding |
| Import declarations | **240** declared |
| VAT return | the `2200` movement matched franc-for-franc against what the return declares of it, and the settlement entry named as the untagged movement afterwards |
| FX revaluation | **708** — 10 x USD 2.00 + 18 % = USD 23.60, carried at 1 320 and revalued at 1 350 |
| Verify / Attach | the sandbox is driven to `accept_then_timeout`, the row goes `unknown`, **Retry is disabled**, **Verify with device** moves it to `needs_receipt`, and **Attach receipt manually** is keyed with the six fields read off the authority's own ledger — its counters, not ours |
| A reversed sale | both counters listed, Print pointed at the **`NS`** (the document's own), and the `NR` selectable |
| The reversal dialog's refusals | `fiscal_refund_irreversible` on a signed credit note and `fiscal_status_unresolved` on a row driven to `unknown` — both read off the **disabled button** before anything is pressed, and the second lifts once the row is resolved through Verify and Attach |

Three things the run taught, each now written into the spec rather than left to be
rediscovered:

* **The authority goes down before the invoice is posted.** The worker drains every fifteen
  seconds, so "the row is still queued" cannot be asserted by being quick — and a queued row is
  the whole subject of the print gate. Bringing it back up is not enough either: the backoff has
  pushed the row minutes out, which is what **Retry now** exists for, so the queue is released
  through the screen. That is also what makes Retry now a *pressed* button rather than a
  matched string.
* **The sandbox is reset first.** Its ledger lives in the container's memory and `make db-reset`
  does not touch it, so a second run starts Vinea's `FIS` sequence at 1 while the authority still
  remembers invoice 1 — `994`, and a stale fixture wearing the clothes of a bug.
* **Labels were scanned for loose lookups before the first push**, which is the step-6 lesson
  as a rule rather than a memory. A script cross-matched every label these screens introduce
  against every `getByLabel("…")` and `getByRole("button", { name: "…" })` in the suite, in both
  directions. It found four collisions — `Accept`/`Accepted`, `Approve`/`Approved`,
  `Open`/`Confirm Reopen`, and `Print`/**`Copy print`** — none of which fails today because each
  lookup is row- or page-scoped, and all of which are now `exact`. The `Print` one is the
  instructive one: `ReportPage` now carries a second print control, `name` matching is loose and
  case-insensitive, and `ar-ap-reports.spec.ts` would have started failing the day a report
  gained a receipt. That spec is tightened too.
* **Every spec that opens a screen this step touched was run** — eleven files, 59 tests, not
  just the ones with "fiscal" in the name. That is how the FX literal was caught disagreeing
  with `dated-rate.spec.ts`, which seeds USD rates of its own: the invoice booked at 1 400 and
  the hand-worked 708 became −1 180.
* **A spec that only passes on a given database state is a spec about that database** (the P5
  step-9 rule), so asserting the rate before computing was not enough — it turns a silent wrong
  into a loud failure without making the spec independent. `exchange_rates` rows are append-only
  per date and there is **no update endpoint**, so no upsert was available: the FX test now
  brings its **own currency**, created per run (`X` + two digits — the ISO 4217 prefix reserved
  for non-currencies, which is what it is), seeds the rate at the booking date and at the
  revaluation date, asserts both are in force, and only then computes. Nothing is typed into
  Exchange rate: the document is dated the first of the month and the **dated lookup** is the
  thing under test on that line. It passes cold, after `dated-rate.spec.ts`, and in whichever
  shard it lands.
* **The VAT spec seeds its own tagged postings** — the invoice and the credit note keyed earlier
  in the same file — but it does not own the range. A return is a company-wide aggregate over a
  date range on a shared fixture: another spec posting a taxed AR document this month moves the
  sections, and one posting an untagged journal against `2200` moves the difference.

  Owning the range **is possible** and was not chosen: a spec can sign up its own company,
  register a device and initialize it against the sandbox from a real client — `p7-maintenance`
  already does the device half, and `p6-cycle-tape` already does the signup half. It is the
  expensive option, not a blocked one, and it is the right one the day this spec needs a
  hand-worked total. It does not: what it needs is the tie, and the tie is a **delta** — every
  franc of movement on a VAT account is either declared by the return or named by a line the
  report lists, which holds for any set of postings. The arithmetic runs across two panels of
  the same page (`2200`'s difference against the sum of the untagged rows under it), so it is a
  figure read off the screen rather than a constant that happens to match today. Said here
  rather than left to be discovered, because "it leans on `ar-ap-acceptance` rows" is the honest
  failure mode and this one does not.
* **It files, and it hands the range back.** `vat_period_filed` refuses any later filing that
  overlaps a **posted** return, so a spec that filed the current month on the shared fixture and
  walked away would refuse its own next run and step 9's tape after it. So the spec files, proves
  the refusal by filing again, reverses, files once more to prove the reversal freed the range,
  and reverses that too — ending with no posted return on the company, asserted. Reversing is
  also the only way the range *can* come back (`module_reversal("tax")`), and doing it here is
  what gives **Reverse** a caller that is pressed rather than merely present. The fixture being
  put back, the guard being proven and the rule-14 line being earned are the same three clicks.
* **`toISOString()` is banned in `e2e/` too**, and the first draft tripped it three times.
  `src/lib/no-utc-dates.test.ts` caught it: CI runs in UTC and could never have told a UTC
  rendering from a local one, while a run in Kigali between midnight and 02:00 would have dated
  documents into yesterday.

## Tree state

Against `main`, at `15e6583`:

```
backend:   16 files changed, 643 insertions(+), 106 deletions(-)
frontend:  31 files changed, 6278 insertions(+), 26 deletions(-)
docs:      25 files changed, 491 insertions(+), 1 deletion(-)   (22 of them screenshots)
```

Checks, every one against `15e6583` — the branch head, the tree that ships.

The backend suite was run three times before this and quoted none of them: two I killed myself
by running `make db-reset` under them (exit 137 — a sequencing error on my part, not a flake),
and one was superseded by a change to `app/tax/vat.py` after it had started. The suite has to be
the last thing, alone. It was.

| check | result |
|---|---|
| `make be-lint` | `All checks passed!` |
| `make be-test` (`-n 4`, in the container) | `1357 passed, 7 warnings in 1080.71s (0:18:00)`, `PYTEST_EXIT=0` |
| `tests/test_api_has_a_caller.py` | 15 passed — the fourteen lines gone, `POST /fiscal/outbox/drain` exempt `by design` |
| `npx tsc --noEmit` | clean |
| `npm run lint` | clean (pre-existing `react-hooks/exhaustive-deps` warnings only) |
| `npx vitest run` | 391 passed, 16 files |
| `npm run build` | compiled; the five new routes built |
| `e2e/p7-transactions.spec.ts` + `p7-maintenance` + `accessibility-transactions` | 53 passed, on a reset database with the sandbox up |
| every spec that opens a screen this step touched | 52 passed — `ar-ap-{acceptance,allocation,corrections,documents,reports}`, `dated-rate`, `empty-state-vs-failure`, `p6-{cycle-tape,enquiries-reports,orders,maintenance}` |

## Screenshots

`docs/screenshots/p7-step-7/` — **22 files**, eleven screens in light and dark, plus a `README.md`
giving the capture command, the `ONLY` filter and what each shot is for. Captured by
`frontend/scripts/capture-p7-transactions.ts` in a single pass on a reset database against the
`ebm-sandbox` container, and every one was opened and read before it was committed.

| # | screen | what it has rows of |
|---|---|---|
| 1 | Invoice, the Fiscalization section | a customer with a TIN, "Purchase code (required)", the authority's note |
| 2 | Credit note, the *Refund of* picker open | the partner's fiscalized invoices |
| 3 | Document detail, the fiscal panel | receipt `1/1 NS`, `SDC010000005`, Copy print, Print enabled |
| 4 | **The CIS receipt**, print media | the line, the totals by class, the SDC block, the dashed signature, the QR, the MRC |
| 5 | Fiscal queue | the device card with its counts, and the rows in send order |
| 6 | A sale row's declared payload | `totAmt 59000`, `taxAmtB 9000`, the receipt block — and no key |
| 7 | EBM purchases | the authority's one purchase, 11,800 taxable, undecided |
| 8 | Import declarations | the declared line, 240 |
| 9 | VAT return | the sections, and the tie with `2200`'s movement beside what the return declares |
| 10 | FX revaluation | the preview at 708 **and** a posted run with its entry and its next-day mirror |
| 11 | A reversed sale | both receipts, Print pointed at the `NS` |

## What step 9 has to know about this spec

`p7-transactions.spec.ts` runs on the **shared** Rugari Wines E2E company and consumes document
numbers there. Measured after one clean run:

| run | consumed | what is left standing |
|---|---|---|
| `VAT` | 1–4 | `VATR-000001` and `VATR-000003`, both **reversed**; 000002 and 000004 went to the two reversing *entries* — the run counts every `VAT` entry, which `test_reversing_a_return_reopens_its_range` already documents |
| `FXR` | 1–4 | `FXR-000001`, **reversed** — the run, its next-day mirror, the reversal's counter-entry and that counter's mirror |
| `FIS` | next 8 | per branch |
| `FSAR` / `FITM` / `FIP` | next 9 / 2 / 2 | |

So **step 9's tape through the screens must not assert `VATR-000001` or `FXR-000001` by
literal** on this company: whichever of the two specs lands first in a shard takes them, and the
chain would fail the first time it ran after this one. Either assert the shape (`/VATR-\d+/`, as
this spec does), or give the tape a company of its own — which is the expensive option and the
right one if step 9 wants hand-worked numbers.

Nothing this spec files is left posted: both returns are reversed and the revaluation run is
reversed, so the range and the date are free for whatever runs next.

## What step 8 owes

Enquiries → Tax (Fiscal receipts, Fiscal queue history) and Reports → Tax (VAT return with its
two annex CSVs, the X and Z daily reports with **Close day**, the fiscal receipts listing) plus
Reports → General Ledger → FX revaluation. `POST /fiscal/devices/{id}/close-day` is the one
`GAP (P7, step 8)` line left in the register, and `sources.py` still owes drill keys for the
`VATR-` and `FXR-` source types.
