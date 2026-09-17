# P7 step 2 — the posting contract

The STOP-gate report. Committed rather than pasted into a conversation, because the substance
of a gate is the thing the gate is about: the payload map, the refusal table, the invariant
statement and the residue census are what a reviewer reads and what step 5's live run is
checked against, and a scratch file on a build machine is not where any of that belongs.

Written at `2dc43a1`, against `main` at `ce8cee4`.

## The schema

One migration, `0023_p7_outbox_source_type`, and it is two changes:

* `fiscal_outbox.source_doc_type` `VARCHAR(10)` → `VARCHAR(50)`. Step 1 chose ten, which fits
  none of the names the rest of the schema uses: `journal_lines`, `stock_moves` and `audit_log`
  all carry fifty and hold `partner_document`, `inventory_document`, `goods_received_note`.
  Step 2 writes `partner_document` and `partner_document_reversal`; step 3 will write the stock
  postings.
* `fiscal_receipts` unique on (company, document) → (company, document, **receipt_type**). A
  signed sale that is reversed owes RRA a refund, and that refund has its own counters,
  signature and QR. It has to be a stored receipt rather than a JSONB response, because
  decision 11 computes the Z report *from `fiscal_receipts`* — a refund RRA signed that the Z
  could not see would make the close disagree with the authority.

`alembic upgrade head` from zero, `alembic check` and `alembic downgrade base` are all green on
a scratch database. No back-fill: `fiscal_outbox` is a queue, and this revision reads no row.

## The payload map (kind × field × source)

### `sale` / `refund` — `/trnsSales/saveSales`

| field | source |
|---|---|
| `tin`, `bhfId` | `fiscal_devices.tin`, `.bhf_id` |
| `invcNo` | `document_sequences` `FIS`, branch-scoped, claimed in the posting transaction → `fiscal_outbox.invc_no` |
| `orgInvcNo` | the original invoice's `fiscal_outbox.invc_no`; `0` on a sale |
| `custTin`, `custNm`, `receipt.custMblNo` | `partners.tin`, `.name` (60), `.phone` |
| `salesTyCd` | constant `N` (v1.0.5: send only `N`) |
| `rcptTyCd` | `S` for an AR invoice, `R` for an AR credit note |
| `pmtTyCd` | `partner_documents.payment_method` → §4.10, total map |
| `salesSttsCd` | constant `02` |
| `cfmDt` | `journal_entries.posted_at` in `Africa/Kigali`, `yyyyMMddHHmmss` |
| `salesDt` | `partner_documents.document_date` |
| `stockRlsDt` | `cfmDt` when any line is a stock or kit item, else absent |
| `rfdDt`, `rfdRsnCd` | `partner_documents.refund_reason` (§4.16) |
| `prcOrdCd` | `partner_documents.purchase_code` |
| `totItemCnt` | count of lines that are not kit components |
| `taxblAmtA–D`, `taxAmtA–D` | Σ of the lines by `tax_codes.fiscal_tax_type` |
| `taxRtA–D` | the programmed rates, 0 / 18 / 0 / 0 |
| `totTaxblAmt`, `totTaxAmt`, `totAmt` | Σ of the buckets (`totAmt` = `totTaxblAmt`) |
| `remark` | `partner_documents.description` (400) |
| `regrId`/`modrId`, `regrNm`/`modrNm` | `users.id` (20), `users.email` (60) |
| `receipt.rptNo` | the device's next Z number — `max(fiscal_daily_reports.report_no) + 1` |
| `receipt.trdeNm`, `receipt.adrs` | `branches.name`, `branches.address` flattened to one line |
| `prchrAcptcYn` | constant `N` |

Per line:

| field | source |
|---|---|
| `itemSeq` | position among the non-kit-component lines |
| `itemCd`, `itemClsCd` | `fiscal_items.item_cd`, `.item_cls_cd` (← `items.fiscal_class_code`) |
| `itemNm` | `items.name` (200) |
| `bcd` | first active `item_barcodes.barcode`, or absent |
| `pkgUnitCd` | `items.fiscal_package_unit`, default `NT` |
| `qtyUnitCd` | `uoms.fiscal_quantity_unit` |
| `pkg`, `qty` | `partner_document_lines.quantity` |
| `prc` | VAT-inclusive unit price in **base**: `unit_price × (100+r)/100 × exchange_rate`, two decimals |
| `splyAmt` | `prc × qty` |
| `dcRt`, `dcAmt` | `partner_document_lines.discount_percent`, and `splyAmt × dcRt/100` |
| `taxTyCd` | `tax_codes.fiscal_tax_type` |
| `taxblAmt` | `splyAmt − dcAmt` |
| `taxAmt` | `taxblAmt × r/(100+r)`, half-up, two decimals |
| `totAmt` | `taxblAmt` |

### `item` — `/items/saveItems`

| field | source |
|---|---|
| `itemCd` | `fiscal_items.item_cd`, minted once from §4.17 + the `FITM` sequence |
| `itemClsCd` | `items.fiscal_class_code` |
| `itemTyCd` | `items.fiscal_item_type`, defaulted from `items.item_type` |
| `itemNm` | `items.name` |
| `orgnNatCd` | `items.fiscal_origin_country`, default `RW` |
| `pkgUnitCd`, `qtyUnitCd` | `items.fiscal_package_unit` (default `NT`), `uoms.fiscal_quantity_unit` |
| `taxTyCd` | `tax_codes.fiscal_tax_type` |
| `dftPrc` | **`items.selling_price`**, made inclusive — the catalogue price, not the line's |
| `bcd` | first active barcode |
| `useYn` | `items.is_active` |

## The refusal table, and the test that proves each sensitive

Every refusal runs in `plan()`, which happens **before** the companion stock entry, the
credit-limit audit and the journal — so a document that cannot be fiscalized costs a 422, not a
rollback. Each row's sensitivity test posts the *same* document once the missing thing is put
back.

| code | field it lands on | test | proven sensitive by |
|---|---|---|---|
| `fiscal_device_missing` | `branch_id` | `test_an_invoice_on_a_branch_with_no_device_is_refused` | the same invoice posts on the branch that has one (every other test in the file) |
| `fiscal_item_required` | `lines.N.item_id` | `test_a_gl_only_line_is_refused` | the item-line invoice posts |
| `fiscal_class_missing` | `lines.N.item_id` | `test_an_item_with_no_ebm_class_is_refused` | same test: class cleared → refused, class restored → posts |
| `fiscal_uom_unmapped` | `lines.N.uom_id` | `test_a_unit_with_no_ebm_quantity_unit_is_refused` | same test, unit restored |
| `tax_class_unmapped` | `lines.N.tax_code_id` | `test_a_tax_code_with_no_ebm_class_is_refused` | same test: class cleared → refused, class restored → posts |
| `purchase_code_required` | `purchase_code` | `test_a_sale_to_a_customer_with_a_tin_needs_a_purchase_code` | same test, code supplied; and the walk-in test, which needs none |
| `refund_original_required` | `refund_of_document_id` | `test_a_credit_note_naming_no_original_is_refused` | the linked credit note posts |
| `refund_spans_invoices` | `lines` | `test_a_credit_note_returning_two_invoices_is_refused` | one-invoice credit notes post |
| `refund_exceeds_original` | `lines.N.quantity` | `test_a_credit_note_returning_more_than_was_invoiced_is_refused` | the first credit of six goes through; the second does not |
| `refund_reason_required` | `refund_reason` | `test_a_credit_note_with_no_reason_code_is_refused` | same test, reason supplied |

`refund_reason_required` is the tenth. Decision 7 requires the reason code and does not name a
refusal for it; this is that name.

Two more refusals live on the reversal path (decision 7) and one on the settings screen:

| code | where | test |
|---|---|---|
| `fiscal_refund_irreversible` | reversing a credit note whose refund RRA signed | `test_a_signed_refund_cannot_be_reversed` |
| `fiscal_status_unresolved` | reversing a document whose row is `unknown` or `needs_receipt` | `test_a_document_whose_row_is_unknown_cannot_be_reversed`, and the `needs_receipt` twin |
| `fiscal_requires_block` | switching negative stock to `allow` while a device is active | `test_negative_stock_cannot_be_allowed_while_a_device_is_active` + the suspended-device twin |

## `assert_fiscal_invariants`, and its own sensitivity pass

Six invariants, in `backend/tests/fiscal/invariants.py`:

1. **one document, one row** — a posted fiscalized AR document has exactly one non-cancelled
   `sale`/`refund` row, and every row names exactly one document;
2. **`sent` means signed** — every `sent` sale or refund has exactly one receipt, and no row in
   any other state has one;
3. **`invc_no` gapless per device** — 1..N with no hole, cancelled rows included;
4. **the counters only rise** — `rcpt_no` strictly increasing within a receipt type and
   `tot_rcpt_no` across types, per device;
5. **FIFO holds** — no row of a later `sequence_no` is `sent` while an earlier row of the same
   device is still non-terminal;
6. **no device key** in any stored payload or response, walked over every row a test produced.

`tests/fiscal/test_invariant_sensitivity.py` breaks each one deliberately and asserts the
checker fails — seven tests, all green. Number 2's breakage is made by marking a row `sent`
rather than by deleting a receipt, because a receipt cannot be deleted:
`fiscal_block_receipt_mutation` raises on any UPDATE or DELETE, which the attempt confirmed.

## Decisions worth review

**1. The Protocol grew three methods, and they are the outbox seam.** Decision 4 says the
payload is *frozen at enqueue* and a retry resends the same bytes. That needs the adapter's two
halves apart: `render(device, kind, dto)` builds the payload inside the posting transaction and
`send(device, kind, payload)` puts those exact bytes on the wire. The fourteen business calls
stay the vocabulary a caller reads and are now `send(render(...))`, so nothing is dead. The
third is `mint_item_code`: §4.17's composition is a format the authority publishes, and a
service that built it would be country logic above the boundary.

`normalize_receipt` is also declared on the Protocol now, taking a **response body** rather than
an envelope — because the queue's manual "attach a receipt" has no envelope, and a second way of
reading a receipt would eventually disagree with the first.

**2. A gateway timeout is a timeout.** The step-1 adapter mapped `504` to a plain transport
failure, so a lost answer would have been *resent* — the one thing the `unknown` policy exists
to prevent. `408` and `504` now raise `FiscalTimeout`. The sandbox models a timeout as a `504`,
so this was load-bearing rather than theoretical: without it the `accept_then_timeout` path
requeued instead of going `unknown`.

**3. `dftPrc` is the catalogue price, not the line's.** Found by reading the map against
decision 8. The registered price is part of the hash that decides re-registration, so a line
price would queue an `item` row on every sale at a new figure — a shop that negotiates would
spend its queue telling RRA about its own discounts. Now `items.selling_price`, made inclusive
once, with `test_selling_the_same_item_at_a_different_price_does_not_re_register_it`.

**4. The reversal's refund is its own `source_doc_type`.** `partner_document_reversal`, so
"one document, one row" stays literally true per source type. Its receipt is stored (see the
schema note) but the invoice keeps pointing at *its own* receipt — the invoice must not print
the refund that undid it.

**5. `verify_with_device` is offered on `failed` as well as `unknown`.** Decision 4 names it for
`unknown`; a `failed` row is safe to ask about too, and the answer is the same shape. `retry_now`
is **not** offered on `unknown` or `needs_receipt`, which is the refusal that matters.

**6. `_require_device` resolves an unset branch to the main branch.** The kernel does the same
when it posts, and the check runs before the posting — without it a one-branch company with one
device would be told it has no device on a document about to post to that very branch.

## The two sandbox questions, carried to step 5

1. **Does RRA tolerate the residue?** On a zero-decimal base the wire's `taxblAmt` is derived
   from a two-decimal `prc`, and the ledger rounded the same line to the franc. The census
   above is how far apart they get.
2. **Is a refund sent with positive amounts under `rcptTyCd R`, or negative ones?** Built
   positive, following Sage 200 Evolution, with the minus signs belonging to the printed
   receipt (CIS §14). `_assert_no_negative_amount` walks the whole payload recursively and
   holds the build to it.

A third has come up and belongs beside them: **what does the QR's `sdc_receipt_number` field
mean** — `rcptNo` or `totRcptNo`? The 2018 document does not say and the pair prints as
`rcptNo/totRcptNo`. Built as `totRcptNo`, because that is what identifies a receipt uniquely on
the device; a sample receipt from the test environment settles it.


## Checks at submission

```
uv run ruff check .            All checks passed!
uv run pytest -n 4 -q          1195 passed, 7 warnings in 314.21s (0:05:14)
alembic upgrade head && alembic check && alembic downgrade base   (scratch database) green
git status --short             (empty)
git log @{u}..                 (empty)
```

Deep Hypothesis passes, `HYPOTHESIS_PROFILE=deep`, 300 examples each:

```
0-dp machine: states {cancelled 408, failed 9, needs_receipt 2, queued 1916, sent 429, unknown 40}
2-dp machine: states {cancelled 311, failed 29, needs_receipt 25, queued 2773, sent 511, unknown 128}
```

Every non-terminal queue state is reached on both machines, including `unknown` — which only
the sandbox's `accept_then_timeout` can produce honestly, because it is the state where RRA
registered the sale and the answer was lost.
