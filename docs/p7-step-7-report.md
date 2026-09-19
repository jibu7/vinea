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
| VAT return | `/tax/vat-return` | the range, the sections, the tie, the late entries, **File**, and the filed returns with **Reverse** on detail |

**One new screen under Transactions → General Ledger** (Appendix C.1.12): **FX revaluation**
(`/gl/fx-revaluation`) — date, role, the preview per open foreign-currency document, **Post**,
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

* **`/tax/vat-return` and `/gl/fx-revaluation`, singular**, and the FX screen under
  Transactions → General Ledger. The step-3 and step-5 `NO_UI` comments guessed `/tax/vat-returns`,
  `/gl/fx-revaluations` and a "General Ledger → Period end" group that does not exist. The
  prompt's step-7 list is the contract and those comments predate the screens being placed. The
  register now says so where the fourteen lines were.
* **`qrcode.react` is a new frontend dependency.** The CIS receipt prints a QR whose content is
  §7.24.7's, assembled by the backend and stored with the receipt — nothing on the client
  composes it. Its lockfile entry was generated with the **image's own npm**: the host's npm 11
  prunes a nested `@swc/helpers` that npm 10's `npm ci` then refuses to install around, so a
  lock written by the host fails the Docker build.
* **The step-5 `NO_UI` comment said "five lines" for a block that held four.** Corrected in
  place. A count in a comment is the kind of thing a reader trusts.

## What the tests prove

**`frontend/e2e/p7-transactions.spec.ts`** — ten tests, serial, one file, on a reset database
with the `ebm-sandbox` service up. It activates a device at the start and suspends it at the
end, the way `p7-maintenance.spec.ts` does, so a shard that runs it and then the AR/AP tape does
not find the tape refused for reasons that have nothing to do with it.

Figures read off the page, per rule 13:

| screen | figure |
|---|---|
| Invoice | the purchase code is demanded inline, and the posted document totals **59,000** (25 x 2 000 + 18 %) |
| Document detail | the receipt counter in its CIS §7.25 shape, the authority's own `SDC010000005`, and the copy count 0 → 1 |
| Fiscal queue | the device's pending count, and a payload with no key in it |
| EBM purchases | **11,800** taxable on the one purchase the authority is holding |
| Import declarations | **240** declared |
| VAT return | the `2200` movement matched franc-for-franc against what the return declares of it, and the settlement entry named as the untagged movement afterwards |
| FX revaluation | **708** — 10 x USD 2.00 + 18 % = USD 23.60, carried at 1 320 and revalued at 1 350 |

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
* **`toISOString()` is banned in `e2e/` too**, and the first draft tripped it three times.
  `src/lib/no-utc-dates.test.ts` caught it: CI runs in UTC and could never have told a UTC
  rendering from a local one, while a run in Kigali between midnight and 02:00 would have dated
  documents into yesterday.

## Tree state

Against `main`, at `91db681`:

```
backend:   13 files changed,   376 insertions(+),  78 deletions(-)
frontend:  30 files changed,  5585 insertions(+),  25 deletions(-)
docs:      23 files changed,   283 insertions(+),   1 deletion(-)   (20 of them screenshots)
```

Checks, all run against that commit:

| check | result |
|---|---|
| `make be-lint` | `All checks passed!` |
| `make be-test` (`-n 4`, in the container) | *filled in below* |
| `tests/test_api_has_a_caller.py` | 15 passed — the fourteen lines gone, `POST /fiscal/outbox/drain` exempt `by design` |
| `npx tsc --noEmit` | clean |
| `npm run lint` | clean (pre-existing `react-hooks/exhaustive-deps` warnings only) |
| `npx vitest run` | 391 passed, 16 files |
| `npm run build` | compiled; the five new routes built |
| `e2e/p7-transactions.spec.ts` + `e2e/p7-maintenance.spec.ts` | 17 passed, on a reset database with the sandbox up |
| `e2e/accessibility-transactions.spec.ts` | 32 passed, including the four new Tax rows and the FX row |

## What step 8 owes

Enquiries → Tax (Fiscal receipts, Fiscal queue history) and Reports → Tax (VAT return with its
two annex CSVs, the X and Z daily reports with **Close day**, the fiscal receipts listing) plus
Reports → General Ledger → FX revaluation. `POST /fiscal/devices/{id}/close-day` is the one
`GAP (P7, step 8)` line left in the register, and `sources.py` still owes drill keys for the
`VATR-` and `FXR-` source types.
