# Phase 5 — Inventory: final report

Stock is built the way the ledger and the subledger were: **an append-only move table is the
truth, and everything else is a cache that can be recomputed and proved.** `stock_moves` is to
inventory what `journal_lines` is to the GL. No screen, service or document updates a quantity
or a cost; they post moves, and `verify_stock_balances()` recomputes both caches from them.

## What landed

| Step | What |
|---|---|
| 1 | Masters: UoM categories and units, items, barcodes, warehouses, inventory transaction types, the five `gl_settings` keys, permissions, Rename Item Code, the Rwanda seed pack and the back-fill for existing tenants |
| 2 | `stock_moves`, `stock_balances` / `item_cost_state`, `verify_stock_balances()`, weighted-average costing in posting order, the negative-stock policy, `receive_stock()` / `issue_stock()`, `assert_stock_invariants` |
| 3 | Adjustments and journal batches on the P4 batch pattern, opening balances, reversal |
| 4 | Warehouse transfers through the in-transit warehouse, count sessions with snapshot, stale detection, preview and Process |
| 5 | Item enquiry, the four reports, and the costing tape at document level |
| 6 | Maintenance → Inventory screens |
| 7 | Transactions → Inventory screens |
| 8 | Item enquiry and the Movement, Count, Transaction and Valuation reports |
| 9 | The acceptance chain, the permission split, the module-reversal rule, the documents screens, and this report |

## The invariant

`assert_stock_invariants` runs after every posting test and in the property suites. It asserts,
for every INV account at any date: Σ `stock_balances.value` of its locations equals the account
balance, per branch as well; Σ moves equals every cache; the average equals value / quantity
wherever quantity > 0; a location at zero quantity holds zero value; no non-provisional negative
quantity under `block`; and every INV journal line carries an `item_id` and has exactly one move
behind it.

The acceptance chain asserts the same tie from the outside, through two screens that share no
code: the valuation report's total against its own GL figure, and against the trial balance.

## Plan deviations

1. **"Variable barcodes" is a P11 screen, not a P5 one.** The appendix's label means the POS
   scale-label pattern — a prefix, item-code digits, then weight or price digits, decoded at the
   till. Step 6 read it as "the barcode listing" and built that, which is a real and needed
   screen but a different one. The row keeps the appendix's label and position and is tagged
   `P11`; the screen that was built sits beside it as **"Barcodes"**. The misreading is on the
   specification side — the phase prompt's step-6 list named "Variable barcodes" among the
   screens to ship.

2. **Costing tape row 9.** The phase prompt froze row 9 as `Depot 2 / 334 · avg 167 · no
   correction of 8b`. The engine covers the 8b deficit first — 3 @ 150 pays off the one-unit
   shortfall and leaves two units at 150 — so the location closes at `2 / 300 · avg 150` with a
   residue move of −34 to the adjustment account. Corrected on the owner's direction at the
   step-2 gate; the prompt was brought into line at step 8.

3. **A document table that the plan did not name.** Decision 3 says "one stock document → one
   journal entry", which needed a header and lines of its own — `inventory_documents` and
   `inventory_document_lines`, on the P4 `partner_documents` pattern. The plan describes the
   posting contract, not the tables that carry it.

4. **A count session gets its own number run.** `CNT-` numbers variance *documents*; a session
   is a working paper that exists days before it posts anything and may be cancelled having
   posted nothing, so it cannot draw from a run every number of which must reach the ledger.
   Its own sequence keeps both gapless.

5. **A transfer can be cancelled as well as reversed.** The plan's decision 11 covers reversal;
   a transfer that is still on the road is a different question — nothing has arrived, and the
   answer is to send it back rather than to mirror two legs. `0016_p5_transfer_reversal` split
   the one column into `dispatch_reversal_entry_id` and `receive_reversal_entry_id` so a
   received transfer can be undone too, which 0015 could not express.

6. **Transactions → Inventory gains "Documents"** (Appendix C.1.7). Decision 11's reversal had
   no screen and the only Reverse button in the product was the general ledger's, which posts
   the reversing entry and no reversing moves. See below.

## Defects found by building the screens

Each was found by opening a screen with data in it, which is what rule 13 is for. Each is fixed
at the definition rather than at the render, and each is proven sensitive by reverting the fix.

1. **The GL Reverse button could break the phase invariant.** `ReversalRequested` skips the
   control-account guard by design — it mirrors an entry that was legitimately posted — and
   inherits the original's module, so it would write the reversing side of an INV account and no
   reversing moves. Verified: `assert_stock_invariants` then fails with `INV line 3 has no stock
   move behind it`. Reachable from a button, because the adjustment screen navigates to
   `/gl/entries/{id}` after posting. Fixed in the kernel with a context flag in the shape of the
   posting-engine flag: `posting.module_reversal(module)`; anything else raises
   `reverse_via_module_document`. **AR and AP had the identical hole since P4** and are covered by
   the same rule — `test_the_gl_reversal_endpoint_refuses_a_partner_document_entry`.

2. **A processed count flagged every line it had corrected as stale.** Processing posts a move
   for every non-zero variance, so the count's own posting sat above its own snapshot watermark
   and the report flagged exactly the lines that worked. `counts.stale_lines` now returns empty
   for a session that is not counting; Process refuses a completed or cancelled one before it
   ever gets there.

3. **The documents listing showed 0 in every Value cell.**
   `inventory_document_lines.value` is what somebody *keyed*, and only a revaluation keys a
   value. Totals now come from the moves, where the engine put the answer.

4. **The api-enums drift gate could not run under `docker compose exec`.** `TARGET` resolved to
   `/frontend/...` in a container whose tree is `/app`, so the gate failed on a missing file
   instead of comparing anything — and this project runs its backend checks in Docker. Resolved
   through `REPO_ROOT`.

5. **A stock-taker could only be let near a count sheet by being given adjustment rights.**
   `_require_count` accepted `inv:transactions_adjust`, which is the authority a count exists to
   take out of their hands. Split into `inv:count_enter`, back-filled by `0017_p5_count_enter`.

## The first item after P5 — not a backlog line

**An AR or AP document cannot be corrected from any screen.** Two correction endpoints exist
server-side and **neither has a single caller in the frontend**:

* `POST /subledger/{role}/documents/{id}/reverse`
* `POST /subledger/{role}/allocations/{id}/unallocate`

Grepping the whole of `frontend/src` for a call to either returns nothing, and there is no
`/ar/documents/{id}` route to put one on — P4 shipped capture screens, an enquiry and the
reports, and no document detail. So an invoice posted in error, or an allocation made against
the wrong invoice, is uncorrectable by anybody using the product.

This is the same hole as C.1.7's, one phase older and twice over. It is **the first work after
P5**, not a backlog entry: `/ar/documents` and `/ap/documents` with a detail screen carrying
Reverse, and Unallocate on the allocation screen, on exactly the pattern
`/inventory/documents` now sets.

**What this PR changed about it.** Before, the GL entry page's Reverse button would act on an
AR invoice's entry: it posted the reversing entry, left the open item standing, and broke
`SUM(open items) == control balance` silently. That is now refused with
`reverse_via_module_document`, which names the endpoint the reversal belongs to. So the PR
trades a silent corruption for a visible dead end — strictly better, and still a gap that
should not outlive the next phase boundary.

## Findings recorded, not fixed here

1. **`journal_entries.source_doc_id` cannot be back-filled.** Decision 3 asks for source links
   both ways and only one way was wired; the service now reserves the document id before posting
   so new documents link both ways. Historical rows cannot be corrected: both `journal_entries`
   and `stock_moves` are append-only *in the database*, unconditionally, and a migration that
   reached for `DISABLE TRIGGER` would trade the kernel's central guarantee for a convenience
   column. Nothing is lost — `inventory_documents.journal_entry_id` is the direction that always
   worked — and every screen resolves that way. Carried into architecture rule 10: a migration
   that UPDATEs a posted table is tested against posted rows, because `make migrate-check` runs
   on an empty database and proves DDL only.

2. **`toISOString().slice(0, 10)` renders "today" in UTC**, which east of Greenwich is yesterday
   for the first hours of every day. 23 files, 31 occurrences. `todayIso` / `monthToDateIso` are
   in `lib/format.ts` and the new screens use them; the sweep is its own PR with a lint rule and
   a grep test so the pattern cannot return. Filed as issue #30.

## The sensitivity pass

Each guard reverted on its own, against the final SHA, with the test that caught it:

| Guard | Reverted | Result |
|---|---|---|
| INV control-account registry | the `allowed_modules` check in `_validate_account` | `tests/inventory/test_guard.py` — 3 failed |
| Stale-line refusal | Process's `count_line_stale` raise | `tests/inventory/test_counts.py` — 1 failed |
| Module reversal rule | `reverse_via_module_document` | `tests/inventory/test_documents_api.py` — 1 failed |
| Flush rule | the empty-location branch in `costing.py` | 7 failed across `test_property_stock.py` (4), `test_property_documents.py`, `test_transfers.py`, `test_stock.py` |

**The costing tape does not catch the flush rule**, and this pass is how that was discovered.
Tape row 6 is `Adjustment out: 12, Main (empties it) | value 1 391`, and 12 × 115.888889 =
1390.666… rounds half-up to the same 1,391 — so the row is satisfied whether the rule fires or
not. It demonstrates the flush; it does not test it. The rule is well guarded by the property
suites and by `test_a_large_quantity_does_not_drift_the_way_a_six_decimal_average_would`, which
was written for exactly this. The tape's figures are the owner-approved ones and stay as they
are; a note at row 6 records the finding where the next reader will meet it.

## Screenshots

`docs/screenshots/p5-step-6` (maintenance), `p5-step-7` (transactions), `p5-step-8` (enquiry and
reports), `p5-step-9` (documents). Every one has rows and a figure in it; an empty-state
screenshot proves the route compiles and nothing else.
