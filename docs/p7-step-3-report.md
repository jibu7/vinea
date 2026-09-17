# P7 step 3 — purchases, imports, stock reporting

Step 3 is not a gate, but the brief asks one question of it in writing — *"say how in the
step-3 report"*, about the no-double-registration rule — and the answer needs the reasoning
around it. So this is committed rather than pasted, the way the step-2 report is: the payload
maps, the refusal table and the four new invariants are what a reviewer reads, and a scratch
file on a build machine is not where any of that belongs.

Written against `main` at `0b53421` (PR #50, step 2, merged).

## What landed

**Decision 9 — purchases.** `app/fiscal/purchases.py`: a posted AP invoice declares a purchase
(`rcptTyCd P`) and a return to supplier declares a return (`R`), both `regTyCd M`,
`pchsSttsCd 02`, `invcNo` from the branch-scoped `FIP` run, claimed and enqueued **in the
posting transaction** beside the sale side's hook. A line with no item travels under
`gl_settings.fiscal_default_purchase_class_code` with the GL account's name as `itemNm`.
Reversal declares the opposite (below).

**Decision 9 — the feed.** `app/fiscal/feed.py`: `fetch` pulls
`/trnsPurchase/selectTrnsPurchaseSales` by watermark into `fiscal_purchase_feed`, upserting by
(device, supplier TIN, supplier invoice number) and leaving a decided row alone. `accept` and
`reject` queue a `purchase_confirm` — `regTyCd A` with **RRA's own figures**, `pchsSttsCd 02`
or `04`.

**Decision 9 — imports.** `app/fiscal/imports.py`: `fetch` pulls `/imports/selectImportItems`
into `fiscal_import_declarations`; `approve` sends `imptItemSttsCd 3` naming the Vinea item the
line became, `reject` sends `4`. Approval moves no stock and posts nothing, and the test asserts
that by counting journal entries and stock moves before and after.

**Decision 10 — stock reporting.** `app/fiscal/stock.py`, hooked into `post_stock_moves` and
both reversal paths so that a posting a later phase adds reports itself rather than being
remembered about. One `stock_io` row per (branch, direction), then one `stock_master` row per
item touched, carrying the on-hand **at enqueue**.

**Decision 8's remainder.** Deactivating or renaming an item re-registers it
(`items.resync`, called from `update_item`); a device that has never been told about an item
gets its own `item` row before it is asked to report a movement of it (`items.told_about`).

**Eight endpoints** — two listings and six mutating ones, each with its own
`GAP (P7, step 7)` line naming the screen that deletes it. Nothing registers a purchase or
reports stock through an endpoint, and that is the design rather than a gap: a declaration is
written by the posting that caused it, in its own transaction.

## The payload map (kind × field × source)

### `purchase` — `/trnsPurchase/savePurchases`, `regTyCd M`

| field | source |
|---|---|
| `tin`, `bhfId` | `fiscal_devices.tin`, `.bhf_id` |
| `invcNo` | `document_sequences` `FIP`, branch-scoped, claimed in the posting transaction |
| `spplrTin`, `spplrNm` | `partners.tin`, `.name` (60) |
| `spplrInvcNo` | `partner_documents.reference`, **numeric when it is** — `INV/2026/0042` has no numeric form and goes as null rather than as a mangled integer |
| `regTyCd` | constant `M` (a purchase Vinea originated) |
| `pchsTyCd` | constant `N` |
| `rcptTyCd` | `P` for an AP invoice, `R` for a return to supplier |
| `pmtTyCd` | `partner_documents.payment_method` → §4.10 |
| `pchsSttsCd` | constant `02` |
| `cfmDt` | `journal_entries.posted_at` in `Africa/Kigali` |
| `pchsDt` | `partner_documents.document_date` |
| `totItemCnt` | count of lines that are not kit components |
| the buckets and totals | Σ of the lines by `tax_codes.fiscal_tax_type`, exactly as on a sale |
| `regrId`/`modrId`, `regrNm`/`modrNm` | `users.id` (20), `users.email` (60) |

Per line, the same relations the sale side uses (`prc` inclusive, `splyAmt = prc × qty`,
`taxblAmt = splyAmt − dcAmt`, `taxAmt = taxblAmt × r/(100+r)`), from the AP side:

| field | source |
|---|---|
| `itemCd` | `fiscal_items.item_cd`, **absent on a line with no item** — optional on this endpoint |
| `itemClsCd` | `items.fiscal_class_code`, else `gl_settings.fiscal_default_purchase_class_code` |
| `itemNm` | `items.name`, else the **GL account's** name |
| `pkgUnitCd`, `qtyUnitCd` | `items.fiscal_package_unit` (default `NT`), `uoms.fiscal_quantity_unit` (default `U` on a line with no unit) |
| `pkg`, `qty` | `partner_document_lines.quantity`, or `1` on a line with no item |
| `prc` | the VAT-inclusive unit price in base, grossed up from `unit_price` — or the line's posted **gross** on a GL line, which has no unit price and is one of itself |
| `taxTyCd` | `tax_codes.fiscal_tax_type`, or `D` when the line carries no tax code at all |

### `purchase_confirm` — the same path, `regTyCd A`

Every figure is **RRA's own**, read off the stored feed row: `spplrTin`, `spplrBhfId`,
`spplrNm`, `spplrInvcNo`, `rcptTyCd`, `pmtTyCd`, `cfmDt`, `salesDt` → `pchsDt`, `totItemCnt`,
the four buckets, the totals and the whole `itemList`. Vinea supplies three things and nothing
else: the `invcNo` from its own `FIP` run, `pchsSttsCd` (`02` accepted / `04` rejected) and the
actor. A confirmation carrying figures Vinea re-derived would be Vinea's opinion of somebody
else's sale.

### `stock_io` — `/stock/saveStockItems`

| field | source |
|---|---|
| `sarNo` | `document_sequences` `FSAR`, branch-scoped |
| `orgSarNo` | **`sarNo`** — see the open question below |
| `regTyCd` | constant `M` |
| `sarTyCd` | the facing × direction table below |
| `ocrnDt` | the posting's move date |
| `totItemCnt`, `totTaxblAmt`, `totTaxAmt`, `totAmt` | Σ of the lines |
| per line `itemCd`, `itemClsCd` | `fiscal_items` |
| per line `qty` | Σ `abs(stock_moves.quantity)` per item, in the item's **base** unit |
| per line `prc` | the weighted unit cost — value moved ÷ quantity moved, which is the only figure that reproduces `splyAmt` from `prc × qty` |
| per line `splyAmt`, `taxblAmt`, `totAmt` | Σ `abs(stock_moves.value)` per item |
| per line `taxAmt` | `taxblAmt × r/(100+r)` at the item's class rate |

### `stock_master` — `/stockMaster/saveStockMaster`

`itemCd` from `fiscal_items`, and `rsdQty` = Σ `stock_balances.quantity` over the branch's
**non-in-transit** warehouses, computed when the row is enqueued.

### `import_update` — `/imports/updateImportItems`

`taskCd`, `dclDe`, `itemSeq` and `hsCd` off RRA's own record (they are RRA's keys and Vinea
holds no version of the declaration); `itemCd` and `itemClsCd` from `fiscal_items` on an
approval and absent on a rejection; `imptItemSttsCd` `3` or `4`; `remark` the actor's note.

## The `sarTyCd` table, as built

Decision 10's table is implemented as **(facing, direction) → code** rather than as
(document kind → code), and that is the one design decision in it worth a paragraph. A revenue
authority reports a movement by *who it was with* and *which way it went*; the document kind is
Vinea's word for the same pair. Keying on the pair means a **reversal needs no case of its
own** — reversing a sale is the customer side, incoming, which is a customer return; reversing a
goods receipt is the supplier side, outgoing, which is a return to the supplier. The neutral
half is `mapping.StockMovementFacing` and the codes live in `rwanda/builders.STOCK_IO_TYPE`.

| facing | source | in | out |
|---|---|---|---|
| customer | AR invoice and credit-note companions | `03` return in | `11` sale out |
| supplier | goods receipt, unlinked AP invoice, return to supplier | `02` purchase in | `12` return out |
| internal | adjustments, journal batches, count variances | `06` adjustment in | `16` adjustment out |
| transfer | a leg of a movement **between branches** | `04` movement in | `13` movement out |

Not reported at all: a movement inside one branch, every zero-quantity move (a revaluation, a
landed-cost share), and anything at an in-transit warehouse. `01` import in, `05`/`14`
processing and `15` discarding have no Vinea document — goods arrive through a goods receipt
whatever the customs paperwork says, assembly is P12, and a write-off is an adjustment out
because a second code for one movement would split one figure across two lines of RRA's stock
report.

## The no-double-registration rule — how

The brief asks for this in writing. Two things can tell RRA about one supplier invoice: this
company's own AP document (`regTyCd M`) and the supplier's feed row confirmed here
(`regTyCd A`). Both reaching RRA doubles the input VAT held against the taxpayer. `feed.accept`
is where the whole rule lives, and it has four cases:

1. **No AP document linked.** The confirmation is all RRA gets, and it is queued. This is the
   ordinary case: a supplier's invoice this company has not keyed, and may never.
2. **Linked, and the document's own row is `queued` or `failed`.** RRA never received it, so the
   row is **cancelled in favour of the confirmation** and the confirmation is queued. The
   confirmation is the better of the two: it carries RRA's own figures and the supplier's
   invoice number, which is what RRA reconciles the pair by. This is the case decision 9 names.
3. **Linked, and the document's own row is `sent`.** RRA already holds this purchase, under
   Vinea's own `regTyCd M` registration. A confirmation would arrive under a *second* `FIP`
   number for the same supplier invoice — which is the second registration the rule forbids — so
   **nothing is queued**: the link is recorded, the row is accepted, and the reason goes on the
   audit trail and back to the caller so a screen can say why. The alternative considered was to
   confirm anyway and leave the registration standing; that is the double registration, and
   cancelling a `sent` row would be a lie about what RRA holds.
4. **Linked, and the document's own row is `unknown` or `needs_receipt`.** Refused,
   `fiscal_status_unresolved`. Cancelling the registration and confirming over it both need the
   same question answered first: does RRA hold it?

Invariant 7 states the result rather than the mechanism: a posted AP document has exactly one
live `purchase` row, **or** an accepted feed row with a live confirmation and no row of its own,
never both and never neither.

## The refusal table, and the test that proves each sensitive

Every purchase refusal runs in `purchases.plan()`, which — like the sale side — happens
**before** the companion stock entry and the journal, so a document that cannot be declared
costs a 422 rather than a rollback.

| code | field it lands on | test | proven sensitive by |
|---|---|---|---|
| `fiscal_device_missing` | `branch_id` | `test_a_purchase_on_a_branch_with_no_device_is_refused` | the same document on the branch that has one |
| `fiscal_purchase_class_missing` | `lines.N.gl_account_id` / `.item_id` | `test_a_line_with_no_class_and_no_default_is_refused` | the same document posts once the default purchase class is set |
| `tax_class_unmapped` | `lines.N.tax_code_id` | `test_a_tax_code_with_no_ebm_class_is_refused_on_a_purchase_too` | the same invoice posts once the class is restored |
| `fiscal_status_unresolved` | — | `test_a_declaration_whose_outcome_is_unknown_blocks_the_reversal` | the row resolved to `queued`, and the same reversal goes through |
| `fiscal_status_unresolved` | `ap_document_id` | `test_a_linked_document_whose_outcome_is_unknown_refuses_the_decision` | the row resolved, and the same accept goes through |
| `fiscal_feed_already_decided` | — | `test_a_row_decided_twice_is_refused` | the first decision, which succeeds |
| `fiscal_declaration_already_decided` | — | `test_a_line_decided_twice_is_refused` | the first decision, which succeeds |

Two refusals reach the **stock** report, and both are about a field the authority requires of an
item it will hold a record for:

| code | where | test | proven sensitive by |
|---|---|---|---|
| `fiscal_class_missing` | an item moved on a fiscalized company with no EBM class | `test_a_movement_of_an_unclassed_item_is_refused_rather_than_misreported` | the class restored, the same receipt posts |
| `fiscal_uom_unmapped` | its base unit has no EBM quantity unit | `test_a_movement_in_a_unit_with_no_ebm_quantity_unit_is_refused` | the unit mapped, the same receipt posts |

Decision 8 requires a class "for any item that is sold". Step 3 extends that to any item
**moved or purchased as an item**, and the reason is not symmetry: RRA's stock master is keyed
by `itemCd`, so an item it holds no record of cannot be reported at all, and a record needs a
class. A line with *no item* is the case decision 9 gives the default purchase class to, and it
keeps it.

## The four new invariants

Added to `assert_fiscal_invariants`, and each breakable:

7. **One document, one purchase row — or a confirmation instead, never both** (above).
8. **`invc_no` gapless per device on the `FIP` run.** Its own run; RRA reconciles it the same
   way it reconciles the sales run.
9. **`sar_no` gapless per device.** The `FSAR` run. `stock_master` carries no number of its own:
   it is a snapshot beside a movement rather than a movement.
10. **A movement never overtakes its document.** Every `stock_io` or `stock_master` row raised by
   a partner document sits behind that document's own sale, refund or purchase row on the same
   device. This is the **one ordering the build has to arrange rather than inherit**: everything
   else in the queue is in creation order and creation order is the right order, but the
   companion stock entry posts *before* the partner side (P6 decision 2), so a movement reported
   from inside the stock service would sit ahead of the sale that caused it and RRA would answer
   `921`/`922`. `post_document` and `reverse_document` report their own companions, after their
   queue row is in, and `app/fiscal/stock.FACING_BY_SOURCE` deliberately has no
   `partner_document` entry.

## Decisions worth review

**1. The facing table, not a document table.** Above. The alternative was a
(doc_type, direction) map, which cannot tell an AR invoice's companion from a return to
supplier — both are `STK` issues — and needs a case per reversal path.

**2. `StockDocument` gained `counterpart_branch_id`.** A transfer dispatches into the in-transit
warehouse and receives out of it, so neither leg can see where the stock came from or is going.
Decision 10 wants a movement between two branches reported and a movement between two shelves of
one branch reported by nobody, and the two legs are otherwise indistinguishable: a dispatch's own
warehouse is in the source branch either way. The transfer service sets it on both legs (and on
both mirrors of a cancel or a reversal) as the *other* end's branch; it is `None` everywhere
else, which is correct for every posting that is not a leg of anything. The field is
country-neutral — it says which branch the other half of this movement belongs to — so it is
not a rule-12 leak, and the boundary scan agrees.

**3. One `stock_io` row per (branch, direction), not per posting.** Decision 10 says "one
`stock_io` row", which is literally what an ordinary posting produces. Two cases make it more:
a posting that reaches warehouses in two branches is two movements because the authority holds
one stock figure per branch and a device belongs to one; and a posting that both receives and
issues is two movements because `sarTyCd` says which way stock went and there is no code for
both. Neither is a choice — there is no third thing either could be.

**4. In-transit stock is excluded from the movement and from the snapshot.** Goods that have
left one shop and not arrived at the next are on nobody's shelf, and the in-transit warehouse
sits in whichever branch the seed put it in rather than in the branch that is short of them.
Without this a cross-branch dispatch would report a *receipt* into the main branch.

**5. A branch with no active device is skipped, not refused.** Decision 2 refuses a **sale** on
a branch with no device, because a receipt is owed to a customer. An adjustment in a depot owes
nobody one, and refusing it would make a company unable to correct its own stock at a branch it
has not fiscalized yet.

**6. Reversing a declared purchase declares the opposite, and nothing is refused.** The sale
side refuses a reversal of a signed refund (`fiscal_refund_irreversible`) because a refund of a
refund is not in EBM's vocabulary. On the purchase side both directions *are* — a reversed
invoice goes back as `rcptTyCd R` and a reversed return as `P` — so the symmetric case has
nothing to refuse and the reversal simply declares the opposite for the same figures, under
`partner_document_reversal` so invariant 7 stays literally true per source type. Decision 9 does
not name this case; this is the answer chosen for it.

**7. A stock or purchase report never *changes* what the authority holds about an item.** It
registers an item RRA has never been told about, and otherwise passes the stored row through
untouched. A **sale** is what changes a registration: decision 8's hash is over the registered
fields and the price in it is the catalogue price, so a report that re-derived the tax class
from a purchase line's *input* code and the price from a cost would put both on a catalogue row
— and two paths disagreeing about what an item is would re-register it on every second
document. The tax class for a first registration is the item's default **sales** code, falling
back to its purchase code: a raw material that is only ever bought has only the second.

**8. `registration_hash` canonicalises Decimals, and that was a live defect.** The hash read
values through `str()`, and the same price arrived as `2360.0000000000` computed from the
catalogue and `2360.000000` read back off the `NUMERIC(20,6)` column that stored it. Step 2 only
ever took the first route, so the two never met; step 3's stock report reads the stored row **on
purpose** (decision 7 above), and the first run of the suite showed an item re-registered after
every movement. Found by `test_a_drain_registers_the_item_then_the_sale_and_writes_the_receipt`
failing with an unexpected `item` row at index 4. `items._canonical` formats a normalised
Decimal, and `test_a_stock_report_never_changes_what_the_authority_holds` pins it.

**9. `spplrInvcNo` is `partner_documents.reference`.** It is the field a person keys the
supplier's own invoice number into, and RRA types the field as a number — so a reference like
`INV/2026/0042` goes as null rather than as a mangled integer, and stays on the Vinea document
where a human can read it. Step 7's Supplier invoice screen should say so beside the field.

**10. Item registration is per (taxpayer, branch) on the wire and per company in Vinea.**
`saveItems` carries `bhfId`, so RRA holds items per branch; `fiscal_items` holds one row per
company, because the item code is company-wide (decision 5) and a second code would orphan every
receipt issued against the first. `items.told_about` reads the outbox — the durable record of the
conversation — to decide whether *this* device has been told, and queues a registration for it if
not. A catalogue edit (`items.resync`) queues on one active device rather than all of them: a
branch that never sells or moves the item never needs to know, and it will be told the moment it
does.

**11. Cancelling a document's row cancels the movement behind it.** The FIFO is what makes
this necessary rather than tidy. A document's movement sits *after* its sale or purchase
(decision 10), so a sale still `queued` means its movement is queued too — and decision 7's
"cancel the row, RRA never held it" would leave the movement at the head of the queue, to be
sent to an authority with no document to attach it to. That is the `921`/`922` refusal the FIFO
exists to avoid, arriving by the one route the FIFO cannot see.
`stock.cancel_unsent_movements` is called from both `on_reverse` paths, and only for `queued`
and `failed` rows — any other state means RRA may already hold it, which is the same rule
decision 7 applies to the document's own row.

**12. The mirror of a movement that was never reported is not reported.** Its other side. If
the original movement was cancelled, RRA has nothing to correct, and a receipt with no issue
behind it would make RRA's own stock figure wrong in the direction the correction was meant to
fix. `stock.was_reported` is the test, and the facing of a mirror is the **document's** rather
than the reversal's.

**13. A journal batch that moves stock is reported as an adjustment.** Decision 3 keeps
`ARJN`/`APJN` out of the sales register — an opening balance is not a sale — so a movement it
causes has no document row to sit behind, and sending it as a sale is precisely the `921`/`922`
refusal. The goods left the shelf all the same. So it goes out as `06`/`16`, which is what a
movement with no fiscal document *is*, and invariant 10 says "behind its document row **where
it has one**" for that reason. The alternative — not reporting it — is a shelf that moved
without the authority hearing about it, which is the drift decision 10 exists to stop.

**14. `orgSarNo` is the movement's own number.** Step 1 mapped it to "the sale or purchase this
movement belongs to" and step 3 changes that, because a *sales invoice number* in a field named
"original stock adjustment reference" is very likely the wrong field: `/stock/saveStockItems`
carries no `invcNo` at all, and §3.1's "the sales invoice information first" is a statement
about **order**, which the FIFO answers. What the documents do show is one sample, and in it
`sarNo` and `orgSarNo` are both `2` on a sale-out movement. The DTO field is gone rather than
left unset, so there is nothing carrying a value nobody reads. See the open question below.

## Plan deviations

**One, and it is the ordering.** Decision 10 says `stock_io` and `stock_master` come "from the
stock service on every posting". They do for every posting except a **partner document's
companion**, which the stock service deliberately skips and `post_document` /
`reverse_document` report instead.

The reason is decision 10's own last sentence — "stock rows follow their sale in the FIFO by
construction" — which is false if the report comes from the stock service, because the companion
stock entry posts *before* the partner side (P6 decision 2: a return to supplier and an
unmatched purchase both need the value the stock ledger moved before their partner side can be
built at all). A movement enqueued from inside the stock service would carry a lower
`sequence_no` than the sale that caused it, and RRA answers `921`/`922` to a stock report that
arrives before its invoice. The two options were to report from the document, or to reserve the
sale's queue position before the companion posts — which means reserving a block of positions,
because the item registrations have to precede the sale too, and the count of those is not known
until the plan is built. The first is one exception with a comment at both ends; the second is
machinery.

`app/fiscal/stock.FACING_BY_SOURCE` has no `partner_document` entry and says why; invariant 10
is what keeps the exception honest.

## Sensitivity pass

Each guard reverted on its own, on the final tree, with the test that caught it:

| guard | reverted by | caught by |
|---|---|---|
| a movement is reported behind its document, not from the stock service | adding `partner_document` to `FACING_BY_SOURCE` and dropping the `post_document` report | 6 tests, including `test_a_movement_is_queued_behind_the_sale_that_caused_it` and invariant 10 |
| cancelling a sale cancels the movement behind it | dropping `cancel_unsent_movements` from `sales.on_reverse` | `test_cancelling_a_sale_cancels_the_movement_behind_it`, `test_the_mirror_of_a_movement_that_was_never_reported_is_not_reported` |
| in-transit stock is nobody's | dropping `Warehouse.is_in_transit.is_(False)` from `on_hand` | `test_the_movement_sequence_reports_every_code_in_order` |
| a transfer inside one branch is silent | ignoring `counterpart_branch_id` | `test_a_transfer_inside_one_branch_is_invisible_to_the_authority` |
| one supplier invoice, one registration | not cancelling the document's row in `feed.accept` | `test_a_linked_document_whose_row_is_queued_is_cancelled_in_favour_of_the_confirmation` |
| the registration hash is over values, not representations | `_canonical` back to `str(value)` | `test_a_stock_report_never_changes_what_the_authority_holds` and three drain tests |

The refusals carry their own sensitivity in the same test — the missing thing is put back and
the same document posts — and the table above the refusal table names which.

## An open question for step 5's live run

**What does `orgSarNo` carry?** The build sends `orgSarNo = sarNo`, following v1.0.5's own
worked example for `saveStockItems` (`"sarNo": 2, "orgSarNo": 2`, pinned at
`tests/fiscal/samples/save_stock_io_request.json`). The name says "original SAR number", which
reads as the movement a correction corrects — but the document neither says so nor shows one, and
the only sample it gives is a plain movement where the two are equal. Nothing in this phase
corrects a movement by SAR number, so `= sarNo` is both what the sample shows and the only value
there is; if Kigali says a mirror should name the movement it reverses, it is one field and the
original row's `sar_no`.

Also worth checking on the live run: the document's own stock sample carries
`taxblAmt 35 000` with `taxAmt 6 000`, which is neither `35 000 × 18 %` (6 300) nor
`35 000 × 18/118` (5 338.98). The build derives the second, the same relation every other payload
in this phase uses; the sample suggests the field is not validated on this endpoint, and the live
run is where that stops being a guess.

## Checks at submission

```
uv run ruff check .            All checks passed!
uv run pytest -n 4 -q          1255 passed, 7 warnings in 331.86s (0:05:31)
git status --short             (empty)
git log @{u}..                 (empty)
```

Step 2 closed at 1 200; the 55 new ones are the movement sequence and its eight negative
controls, the purchase declaration and its refusals, the feed's four accept paths, the import
register, and item registration's two missing cases. No migration: step 1's schema already
holds every table and column this step writes, so `alembic check` has nothing new to say.
