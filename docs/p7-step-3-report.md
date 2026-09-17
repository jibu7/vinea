# P7 step 3 — purchases, imports, stock reporting

Step 3 is not a gate, but the brief asks one question of it in writing — *"say how in the
step-3 report"*, about the no-double-registration rule — and the answer needs the reasoning
around it. So this is committed rather than pasted, the way the step-2 report is: the payload
maps, the refusal table and the five new invariants are what a reviewer reads, and a scratch
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

1. **No AP document linked, and nothing already declares the invoice.** The confirmation is all
   RRA gets, and it is queued. This is the ordinary case: a supplier's invoice this company has
   not keyed, and may never.
1b. **No AP document linked, and Vinea has already declared this invoice.** Refused,
   `purchase_already_declared`, naming the document. **This is the case the first version of
   this step got wrong**, and it is worth reading twice: the three linked cases were each
   covered and the unlinked one was not, so a confirmation could reach RRA beside a live
   registration of the same invoice simply because nobody pressed the link. The link is
   optional; the *coincidence* is not. A posted AP document for the same supplier carrying the
   same supplier invoice number, with a live registration, **is** the purchase this feed row is
   the other side of. The refusal names the document, because the operator's next move depends
   on which it is: link it (case 2) and replace the registration with the confirmation, or
   correct one of the two references because they really are two invoices.

   Two things are deliberately **not** duplicates. A reference with no numeric form: RRA types
   `spplrInvcNo` as a number, so `INV/2026/0042` is not a key the authority can reconcile by and
   not one this build may match on — the pair is reconciled by hand, which is something step 7's
   Supplier invoice screen should say beside the field. And a **reversed** document: reversing a
   declared purchase declares the opposite (decision 6), so RRA holds a `P` and an `R` that net
   to nothing held, and the invoice is free to be confirmed from the supplier's side. Refusing
   there would block an accept over a registration that was already undone.
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

Two invariants state the result rather than the mechanism, and they are two because they
quantify over different things.

**Invariant 7 asks from the document's side**: a posted AP document has exactly one live
`purchase` row, **or** an accepted feed row with a live confirmation and no row of its own,
never both and never neither. That is the right question and it can only see the pair the
operator *linked* — which is exactly why case 1b escaped it.

**Invariant 10a asks from the authority's side**, over feed rows: no accepted feed row with a
live confirmation shares its (supplier TIN, supplier invoice number) with a live registration of
Vinea's own, linked or not. That pair is the identity RRA reconciles a purchase by, so this is
the state that must be unreachable rather than merely refused at one door. Both read
`feed.declared_documents`, so the refusal and the invariant cannot come to disagree about what
a duplicate is.

`test_one_supplier_invoice_declared_twice_is_caught_as_a_state` is the proof that the second is
doing work the first cannot: it patches the refusal out, walks through the door, and asserts the
invariant catches what is left behind. Delete the invariant and that test goes green over one
supplier invoice reaching RRA twice with the input VAT doubled; delete the refusal and the feed
test goes red. Neither alone covers both.

**What "unreachable" means here, precisely.** Not constrained by the schema: the identity RRA
reconciles by is (supplier TIN, supplier invoice number), and those live in three tables —
`partners.tin`, `partner_documents.reference` and `fiscal_purchase_feed` — so there is no unique
index that could span them without denormalising the pair onto a fourth. What invariant 10a
buys is that the state is **detected wherever the checker runs**, which is after every operation
in the fiscal suite including every feed decision, rather than being prevented at one door and
invisible everywhere else. That is a weaker claim than a constraint and a much stronger one than
a refusal, and it is worth saying which of the three this is.

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
| `purchase_already_declared` | `ap_document_id` | `test_an_unlinked_accept_of_an_invoice_already_declared_is_refused` | the same accept **with** the link, which cancels the registration and confirms |
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

## The five new invariants

Added to `assert_fiscal_invariants`, and each breakable:

7. **One document, one purchase row — or a confirmation instead, never both** (above).
8. **`invc_no` gapless per device on the `FIP` run.** Its own run; RRA reconciles it the same
   way it reconciles the sales run.
9. **`sar_no` gapless per device.** The `FSAR` run. `stock_master` carries no number of its own:
   it is a snapshot beside a movement rather than a movement.
10a. **One supplier invoice, one registration — quantified over the feed** (above). The same
   rule as 7, asked from the authority's side rather than the document's, which is what makes
   an *unlinked* confirmation of an already-declared invoice a state rather than an entry-point
   refusal.
11. **A movement never overtakes its document.** Every `stock_io` or `stock_master` row raised by
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

**6. Reversing a declared purchase declares the opposite — the right default, and a question
rather than a settled decision.** The sale side refuses a reversal of a signed refund
(`fiscal_refund_irreversible`). On the purchase side the build declares the opposite instead: a
reversed invoice goes out as `rcptTyCd R`, a reversed return as `P`, under
`partner_document_reversal` so invariant 7 stays literally true per source type.

The first of those is unambiguous — goods bought and then un-bought is a return, which is what
happened. **The second is the asymmetry, and it is not cosmetic:** a return to supplier that is
reversed means the goods never went back, and what reaches RRA is a `rcptTyCd P` purchase, with
a fresh `FIP` number and the same figures, for a transaction that did not occur. The taxpayer's
purchase register then shows a purchase nobody made beside a return nobody made. They net
correctly and individually describe nothing.

**The alternative was to refuse, as the sale side does** — `fiscal_return_irreversible`, correct
it with a fresh supplier invoice. It lost on three counts, and the first is the one that decided
it. `fiscal_refund_irreversible` is not a policy: `rcptTyCd` on `/trnsSales/saveSales` has `S`
and `R` and no third code, so a refund of a refund is a thing the payload *cannot say*. `P` and
`R` on `/trnsPurchase/savePurchases` are symmetric — the payload can say both, in either order,
any number of times — so a refusal there would be Vinea's opinion wearing the authority's
clothes. Second, it would strand a posted document: P4's reversal is how a mis-keyed AP document
is undone, and a return to supplier is the easiest of all of them to mis-key, so refusing leaves
a wrong document in the ledger with no way out but a compensating entry. Third, the net is right
either way, and a VAT return is built from Σ purchases − Σ returns; the objection is to the
shape of the register, not to its arithmetic.

**Carried to step 5's live run** as `docs/rra/contract-notes.md` §9.2, beside `orgSarNo`:
register a purchase, register its return, register the reversal of that return, and read
`/trnsPurchase/selectTrnsPurchaseSales` back. If RRA rejects the third, or holds it in a way an
accountant reading the register would call wrong, the answer is the refusal — one code, one
report line, and the ledger's reversal path untouched, because `on_reverse` already refuses an
`unknown` row and this is the same shape.

**7. A stock or purchase report never *changes* what the authority holds about an item.** It
registers an item RRA has never been told about, and otherwise passes the stored row through
untouched. A **sale** is what changes a registration: decision 8's hash is over the registered
fields and the price in it is the catalogue price, so a report that re-derived the tax class
from a purchase line's *input* code and the price from a cost would put both on a catalogue row
— and two paths disagreeing about what an item is would re-register it on every second
document. The tax class for a first registration is the item's default **sales** code, falling
back to its purchase code: a raw material that is only ever bought has only the second.

**8. A fingerprint identifies values, and there were two canonicalisers.** The item hash read
values through `str()`, so the same price arrived as `2360.0000000000` computed from the
catalogue and `2360.000000` read back off the `NUMERIC(20,6)` column that stored it. Step 2 only
ever took the first route, so the two never met; step 3's stock report reads the stored row **on
purpose** (decision 7 above), and the first run of the suite showed an item re-registered after
every movement — found by `test_a_drain_registers_the_item_then_the_sale_and_writes_the_receipt`
failing with an unexpected `item` row at index 4.

The first fix was a local canonicaliser in `app/fiscal/items.py`, and that was the wrong shape:
**ADR-11's `Idempotency-Key` fingerprint had had the right one since P2** (`1000` and `1000.00`
are one amount, however the client spelled them), and the fiscal hash simply did not use it. Two
implementations of one money rule, one of them wrong, which is rule 9 exactly. So there is now
one — `kernel.money.canonical` and `fingerprint_material`, in the kernel because it is a money
rule (rule 6: the exponent a value carries depends on which column it came out of and which
multiplication produced it, and none of that is an identity) — and both hashes use it.

`app/api/idempotency.py` changed shape slightly while it was there: the endpoint scope is now a
**hashed value** rather than an `f"{kind}:{body}"` prefix, so that function assembles no text at
all and the guard below has nothing to exempt. The digest changes, so an `Idempotency-Key`
issued before this commit and retried after it answers `409 idempotency_key_reused` rather than
replaying. Keys are per-request and short-lived, the failure is a client asked to use a new key
rather than a wrong posting, and the alternative was keeping a shape nobody would choose.

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
movement with no fiscal document *is*, and invariant 11 says "behind its document row **where
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

`app/fiscal/stock.FACING_BY_SOURCE` has no `partner_document` entry and says why; invariant 11
is what keeps the exception honest.

**15. Two guards on the fingerprint rule, because one shape cannot see both halves.** The ask
was an AST test forbidding equality, hashing or set membership over a stringified Decimal. Half
of that is decidable and half of it is not, and the measurement is in
`backend/tests/test_fingerprints.py`'s own docstring rather than here — but the short of it:

* **The hashing half is the right shape for AST.** Which functions hash something is decidable,
  there are four in `app/`, and the rule — *their material comes from `fingerprint_material`,
  not from text they built themselves* — needs no types at all. It has one `by design`
  exemption, the sandbox's receipt signature, which is an HMAC over the wire payload RRA itself
  would sign rather than a fingerprint of Vinea's values. A register entry that earns its line.
* **The comparison half is the wrong shape, measured rather than asserted.** Scanning `app/` for
  a stringified value reaching `==`, `in`, a set, a dict key or a hash returned **74** hits. 65
  are f-string dict keys, every one a `field_errors={f"lines.{index}.quantity": …}` path — this
  build's idiom for naming a field to a screen, not an identity. 8 are comparisons and not one
  involves a Decimal: `str(account.id)` against a VARCHAR audit column, `str(doc_type)` on a
  StrEnum, `IDEMPOTENCY_INDEX in str(exc.orig)` on an exception message. The 74th was the real
  one, the `f"{kind}:{body}"` above, and it is gone. AST cannot tell a Decimal from an int, and
  a guard with 73 exemptions is a list nobody reads.
* **So the type-blind half is a property.** For any two Decimals that are `==`, the canonical
  form is the same string and both fingerprints are the same digest — over the registration
  rather than over one price, so it holds for every Decimal field decision 8 ever adds, which
  is the part an exemption register could never have promised. With the sharpness half beside
  it (`2360` and `2360.01` must differ) and the strictness half (a type with no canonical form
  raises rather than falling back to `str()`).

## Sensitivity pass

Each guard reverted on its own, on the final tree, with the test that caught it:

| guard | reverted by | caught by |
|---|---|---|
| a movement is reported behind its document, not from the stock service | adding `partner_document` to `FACING_BY_SOURCE` and dropping the `post_document` report | 6 tests, including `test_a_movement_is_queued_behind_the_sale_that_caused_it` and invariant 11 |
| cancelling a sale cancels the movement behind it | dropping `cancel_unsent_movements` from `sales.on_reverse` | `test_cancelling_a_sale_cancels_the_movement_behind_it`, `test_the_mirror_of_a_movement_that_was_never_reported_is_not_reported` |
| in-transit stock is nobody's | dropping `Warehouse.is_in_transit.is_(False)` from `on_hand` | `test_the_movement_sequence_reports_every_code_in_order` |
| a transfer inside one branch is silent | ignoring `counterpart_branch_id` | `test_a_transfer_inside_one_branch_is_invisible_to_the_authority` |
| one supplier invoice, one registration | not cancelling the document's row in `feed.accept` | `test_a_linked_document_whose_row_is_queued_is_cancelled_in_favour_of_the_confirmation` |
| the registration hash is over values, not representations | the fiscal hash's own canonicaliser back to `str(value)` | `test_a_stock_report_never_changes_what_the_authority_holds` and three drain tests |
| **both** fingerprints are over values | `kernel.money.canonical` dropping `.normalize()` | four properties in `tests/test_fingerprints.py` **and** ADR-11's own `test_equivalent_amount_and_date_spellings_still_replay` — the one implementation is why one revert breaks both |
| a fingerprint assembles no text of its own | a `str()` or an f-string added inside a hashing function | `test_no_fingerprint_builds_its_material_out_of_text`, whose own sensitivity is two snippets it must flag and one it must not |
| one supplier invoice, one registration — **unlinked** | dropping the `_refuse_a_duplicate_of_an_undeclared_link` call | `test_an_unlinked_accept_of_an_invoice_already_declared_is_refused` (`DID NOT RAISE`); with the refusal patched out instead, invariant 10a via `test_one_supplier_invoice_declared_twice_is_caught_as_a_state` |
| a reversed declaration is not a duplicate | dropping the `status == POSTED` clause in `declared_documents` | `test_a_reversed_declaration_leaves_the_invoice_free_to_confirm` |

The refusals carry their own sensitivity in the same test — the missing thing is put back and
the same document posts — and the table above the refusal table names which.

## Two open questions for step 5's live run

Both are recorded in `docs/rra/contract-notes.md` §9, which is where the live run reads from —
here for the reasoning, there for the transcript to be checked against.

**1. Is a reversed return to supplier a purchase that did not happen?** Decision 6 above, in
full. Summarised: the build declares the opposite because the payload can say it and refusing
would strand a posted document, and the register's *shape* is the cost. §9.2 says what to run.

**2. What does `orgSarNo` carry?** The build sends `orgSarNo = sarNo`, following v1.0.5's own
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
