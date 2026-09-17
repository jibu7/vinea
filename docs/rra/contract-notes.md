# The contract `app/fiscal/rwanda/` is written to

A reading map over the three specifications pinned beside this file, and a record of the places
where they disagree with each other or with a live receipt. Section numbers are the documents'
own; every claim here was checked against them rather than remembered.

**Where this file and a pinned PDF disagree, the PDF wins and this file is wrong.** Amend it in
the same commit as the code it explains.

## 1. Routes — two profiles, one vocabulary

`fiscal_devices.profile` selects the profile per device. The payload vocabulary is shared; the
`osdc` profile adds `cmcKey` to every request body. Both columns below were extracted from the
documents and match `app/fiscal/rwanda/routes.py` exactly.

| Operation | `vsdc` (v1.0.5) | `osdc` (v1.0.1) |
|---|---|---|
| initialize | `/initializer/selectInitInfo` | `/selectInitOsdcInfo` |
| code list | `/code/selectCodes` | `/selectCodeList` |
| item classes | `/itemClass/selectItemsClass` | `/selectItemClsList` |
| customer (TIN) lookup | `/customers/selectCustomer` | `/selectCustomer` |
| branch list | `/branches/selectBranches` | `/selectBhfList` |
| notices | `/notices/selectNotices` | `/selectNoticeList` |
| branch customers | `/branches/saveBrancheCustomers` | *(none published)* |
| save item | `/items/saveItems` | `/saveItem` |
| item list | `/items/selectItems` | `/selectItemList` |
| import list | `/imports/selectImportItems` | `/selectImportItemList` |
| import update | `/imports/updateImportItems` | `/updateImportItem` |
| save sale | `/trnsSales/saveSales` | `/saveTrnsSalesOsdc` |
| purchase feed | `/trnsPurchase/selectTrnsPurchaseSales` | `/selectTrnsPurchaseSalesList` |
| save purchase | `/trnsPurchase/savePurchases` | `/insertTrnsPurchase` |
| stock in/out | `/stock/saveStockItems` | `/insertStockIO` |
| stock master | `/stockMaster/saveStockMaster` | `/saveStockMaster` |
| stock move list | `/stock/selectStockItems` | `/selectStockMoveList` |

Servers (OSDC §1): production `https://api-ebm.rra.gov.rw`, test
`https://sdcsandbox.rra.gov.rw`. The MyRRA application forms are at `https://myrra.rra.gov.rw`
and `https://myrratest.rra.gov.rw`.

**One inconsistency in v1.0.5 itself:** its endpoint table lists stock in/out twice, as
`/stock/saveStockItems` (§3.3.8.2 body) and as `/saveStockItems/saveStockItems` (the summary
table). The build uses the first; the live run settles it if it matters.

## 2. The sales response, and the one receipt shape behind both

`normalize_receipt()` maps both onto `FiscalReceipt`. Both response samples are checked in at
`backend/tests/fiscal/samples/sales_response_{vsdc,osdc}.json`.

| `FiscalReceipt` field | `vsdc` v1.0.5 | `osdc` v1.0.1 |
|---|---|---|
| `rcpt_no` | `rcptNo` (number) | `curRcptNo` (**string**) |
| `tot_rcpt_no` | `totRcptNo` (number) | `totRcptNo` (**string**) |
| `intrl_data` | `intrlData` | `intrlData` |
| `rcpt_sign` | `rcptSign` | `rcptSign` |
| `sdc_datetime` | `vsdcRcptPbctDate` | `sdcDateTime` |
| `sdc_id` | `sdcId` | *(from initialization)* |
| `mrc_no` | `mrcNo` | *(from initialization)* |

The counters are typed differently by the two documents — `27` against `"1"` — and
`fiscal_receipts` stores integers, because `assert_fiscal_invariants` asserts they increase and
`"10" < "9"` as strings.

## 3. Code tables (VSDC §4)

Implemented in `app/fiscal/rwanda/codes.py`; the class numbers are in the constants there.
Tax type **04** (`A` A-EX, `B` B-18.00%, `C`, `D`), nation **05**, payment method **07**
(`01` cash … `07` other), quantity unit **10**, transaction progress **11** (`01`–`06`; there
is no `07`), stock in/out **12** (`01`–`06` in, `11`–`16` out, including `15 Discarding`),
transaction type **14** (`C`/`N`/`P`/`T`), taxpayer status **15**, packaging unit **17**,
product type **24** (`1` raw material, `2` finished product, `3` service without stock), import
item status **26** (`1` unsent, `2` waiting, `3` approved, `4` cancelled), registration type
**31** (`A`/`M`), refund reason **32** (`01`–`13`), currency **33**, sales receipt type **37**
(`S`/`R`), purchase receipt type **38** (`P`/`R`).

**Refund reasons are not a taxonomy anybody would design** (§4.16): `01` Missing Quantity,
`02` Missing Item, `03` Damaged, `04` Wasted, `05` Raw Material Shortage, `06` Refund,
`07` Wrong Customer TIN, `08` Wrong Customer name, `09` Wrong Amount/price, `10` Wrong
Quantity, `11` Wrong Item(s), `12` Wrong tax type, `13` Other reason. Kept verbatim.

**Response codes (§4.14).** `000` succeeded, `001` no search result; `881` purchase code
mandatory, `882` invalid, `883` already used, `884` invalid customer TIN; `891`–`899` client
errors of which `894` is "an error regarding server communication"; `900`–`912` device and
request errors; `921` "Sales or sales invoice data which is declared cannot be received" and
`922` "Sales invoice data can be received after receiving the sales data"; `990`–`999` server
errors of which `994` is "There is an overlapped Data".

`921`/`922` are RRA's own statement of the ordering rule, and the reason the outbox drains per
device in FIFO with one row in flight: a stock report that overtakes its sale is *refused*.

## 4. Item code (§4.17)

`itemCd` is mandatory and unique per item. The document's worked example:

```
RW2NTBA0000012
RW  Country of origin (Rwanda)
2   Product type (finished product)
NT  Packaging unit (Net)
BA  Quantity unit (Barrel)
0000012  increments from 0000001 to N
```

That breakdown is the document's own prose, and it is the **only** place the format appears
without an `X`. Every code emitted by an actual system has one. **§8 works the evidence
through and states the rule the build implements**; the short version is that `X` terminates a
two-character segment, on the packaging unit and the quantity unit alike.

An earlier reading of this section had it as "left-pad the quantity unit to two characters",
and dismissed the live receipts' `RW2NTXNOX0000011` as "one vendor's convention". It was the
specification's convention and the prose example was the outlier — see §8, including the guard
that now stops a test feeding the builder a quantity unit RRA does not publish.

## 5. What a CIS receipt must print

From the 2018 CIS specification, and confirmed against the three live receipts:

* §5 — labels `NS`, `NR`, `CS`, `CR`, `TS`, `TR`, `PS`. This phase issues `NS` and `NR`.
* §7.17 — one cancellation per original, referencing the original SDC receipt number.
* §7.18 — one original print; every reprint is watermarked `COPY`.
* §7.22–7.23 — every programmed rate above zero prints on every receipt.
* §7.24 — the `SDC INFORMATION` block, in this order: the designation, `Date: dd/mm/yyyy
  Time: hh:mm:ss`, `SDC ID: SDCXXXXXXXXX`, the `A/B RT` counter, `Internal Data` **separated by
  a dash after every 4th character**, `Receipt Signature` the same, and the QR code.
* §7.24.7 — the QR content, verbatim:
  `invoice_date(ddmmyyyy)#time(hhmmss)#sdc number#sdc_receipt_number#internal_data#receipt_signature`
* §7.25 — `A/B RT`: A is the counter per receipt type, B the total counter, RT the label.
* §7.27 — an item counter, excluding voids.
* §7.29 — the official RRA logo on every receipt (`Rwanda-Revenue-Authority-logo.png`).
* §7.30 — no receipt for goods the stock does not hold.

A live receipt renders the block like this (`CONTACTEUR.pdf`, reformatted only by extraction):

```
SDC INFORMATION
-----------------------------------
Date : 31-05-2022   Time : 18:00:09
SDC ID :  SDC010023311
RECEIPT NUMBER : 21/21NS
Internal Data : X33I-UQAZ-BTXC-RUUK-3R54-7I4U-AY
Receipt Signature : ISO5-EIP5-E7HD-ZCJC
-----------------------------------
RECEIPT NUMBER : 21
Date : 31-05-2022   Time : 18:00:09
MRC : WIS00024645
```

`intrlData` is 26 characters and `rcptSign` 16, both dashed every four — which the in-repo
sandbox reproduces exactly.

**The copy receipt (`12.pdf`) shows `1/1CS` on invoice 1**, so a copy is relabelled rather than
separately counted: its counters are the original's. That is consistent with decision 11's "no
second call to RRA", and it is evidence from one vendor rather than a rule in the document.

## 6. The certification checkpoint sheet

75 numbered requirements in
`EXCEL_SHEET_application_form_RRA_VSDC_okay(Compliance table).csv`. The ones that bear on what
this phase builds:

* **47** — "The CIS shall round values of tax on two decimals. (<5 - down, >=5 -up)". Half-up,
  which is the kernel's rule already.
* **48/49** — a rate above zero prints always; a zero rate prints only when used.
* **50/51** — a consecutive receipt counter regardless of type, starting at 1, incrementing
  by 1.
* **56** — "The Refund receipt has always to be printed with a negative, (-) minus sign in
  front of each amount". This settles the *print* side of decision 6's second question; it says
  nothing about the payload.
* **57** — a refund must reference the normal receipt number it is made against.
* **22** — no receipt of any type may be issued when the VSDC is unreachable. Decision 11's
  `fiscal_receipt_pending` is this requirement.
* **67** — the importation `lastReqDt` must be strictly greater than the previous request's.
* **10, 11, 12** — copy (`CS`/`CR`), training (`TS`/`TR`) and proforma (`PS`) receipts are all
  certification requirements. **Training and proforma are out of scope for this phase**, so
  certification cannot complete on P7 alone; recorded for `docs/rra/certification.md` at step 9.
* **24** — a PLU report (CIS §21) is required and is not in this phase's scope either.

## 7. How a line's tax is rounded — settled on the Sage Evolution standard

Four pieces of evidence, and they pulled two ways until a fifth settled it.

* **RRA's own Rwandan API sample** sends whole francs: `taxblAmt: 200000` with `taxAmt: 30508`,
  where 200 000 × 18/118 is 30 508.47. Its Korean samples send two decimals
  (`taxblAmt: 660000`, `taxAmt: 100677.97`).
* **Checkpoint 47** requires tax values rounded *on two decimals*.
* **Two live receipts print two decimals.** `CONTACTEUR.pdf`: `Total Tax B Rwf 9,152.54` on a
  `Total B-18%` of 60 000. Invoice 22: `Total Tax B Rwf 25,627.12` on 168 000
  (= 25 627.1186). Same vendor, different amounts, same rule.
* **Sage 200 Evolution**, which integrates with EBM 2.1 through certified middleware, derives
  the line tax **directly from the inclusive taxable amount** using the standard split,
  `taxblAmt × r / (100 + r)`, and treats that as how a base-currency ledger is reconciled
  against a two-decimal wire.

### What the build does

`taxAmt` is `taxblAmt × r / (100 + r)`, half-up to two decimals. Not the posted tax.

An earlier version sent the posted franc figure on the reasoning that the ledger is the truth
and the receipt derives from it. That reasoning is still right about *direction* and was wrong
about *which field carries the residue*: the authority recomputes this number, so a payload
that disagrees is a payload arguing with the engine that validates it.

**The consequence, stated rather than buried:** a receipt's tax can differ from the posted tax
by under a franc per line, so the VAT return — a query over the ledger — **reconciles to** the
receipts rather than equalling them. Step 4's tie report shows that difference as a line rather
than asserting equality, and step 5's live run is what confirms the authority accepts the
derived figure.

### The discount field, and the phantom discount

Sage sends `dcRt: 0` **and** `dcAmt: 0` on an undiscounted line, and records that a non-zero
`dcAmt` against `dcRt: 0` is a payload validation failure — the VSDC engine validates line
arithmetic strictly.

This build used to do exactly the rejected thing. It multiplied the VAT-inclusive unit price by
the quantity and put the difference from the posted gross into `dcAmt`, because the discount
amount is the one field the documents do not derive from another. On a zero-decimal base that
produced a discount on a line nobody discounted, in a measurable fraction of cases.

Now: an undiscounted line has `splyAmt = taxblAmt` and `dcAmt = 0`; a discounted line has
`splyAmt` as the inclusive-price extension, `dcRt` the keyed percentage and `dcAmt` the money
it came to. `splyAmt − dcAmt == taxblAmt` holds exactly either way, the in-repo sandbox
enforces both rules, and the property census in `backend/tests/fiscal/test_builders.py` now
reports every draw exact where it used to report residues.

## 7a. Refunds are positive on the wire

Sage transmits every refund quantity, price and amount as a **positive** number. The direction
is the header's business: `rcptTyCd: "R"` and `orgInvcNo` naming the sale being reversed.
Negative numbers are rejected by the VSDC schema as negative quantities or invalid decimals.

Minus signs and the REFUND label belong to the printed document (CIS §14, and checkpoint 56,
which requires a refund to print with a minus in front of each amount) — never to the payload.
`test_a_refund_carries_no_negative_number` walks the whole payload recursively.

## 7b. A copy is a print, not a call

Sage does not contact the VSDC API when reprinting. A reprint retrieves the **stored** fiscal
block — signature, QR payload, SDC timestamp and the original `rcptNo`/`totRcptNo` — and
re-renders it. The ERP increments its own print counter and stamps the layout with `COPY` and
`THIS IS NOT AN OFFICIAL RECEIPT` (CIS §7.18, §15).

This settles the copy-counter question decision 11 left open: **the `CS` counter belonged to
older physical EBM hardware with a copy button, and a software CIS integration does not request
a separate counter from the server.** Vinea's `fiscal_receipts.copy_count` is therefore local
and is the one column the row's immutability trigger excludes — which is now a decision with a
reason behind it rather than a convenience.

## 8. The item code, and what the receipts settled about it

§4.17 gives the format — origin, product type, packaging unit, quantity unit, seven-digit
sequence — and then gives worked examples rather than a rule for the two code segments. The
examples are therefore the specification, and there are five.

**Five come out of real VSDC/EBM systems and agree with each other:**

| Code | Source | Packaging | Quantity unit | Segments |
|---|---|---|---|---|
| `RW1NTXU0000006` | VSDC, item carries `qtyUnitCd: "U"` | `NT` | `U` (42) | `NTX` + `U` |
| `KR2AMXBLL0000001` | VSDC, Korean sample | `AM` | `BLL` (7) | `AMX` + `BLL` |
| `RW2NTXU0000002` | Live receipt, invoice 1 | `NT` | `U` (42) | `NTX` + `U` |
| `RW2NTXNOX0000014` | Live receipt, invoice 22 | `NT` | `NO` (31) | `NTX` + `NOX` |
| `RW2NTXNOX0000011` | Live receipt, `CONTACTEUR.pdf` | `NT` | `NO` (31) | `NTX` + `NOX` |

**The rule they share: `X` terminates a two-character segment.** Both segments take it —
packaging unit and quantity unit alike — and one- and three-character codes are left exactly as
they are. It is a delimiter, not padding: §4.5 and §4.6 both publish codes of one, two and
three characters, so a reader needs to know where a segment ends.

**The fifth is the outlier, and it is the one in prose.** §4.17's hand-written worked example
is `RW2NTBA0000012`, and the document's own breakdown reads "NT: Packaging Unit (NET) / BA:
Quantity Unity (Barrel)" — no terminator on either segment. The build follows the five machines
over the one sentence, and produces `RW2NTXBAX0000012` where the document writes
`RW2NTBA0000012`. `test_the_prose_worked_example_is_the_one_the_rule_does_not_reproduce` pins
that disagreement so it stays visible.

**This is a sandbox question, not a settled fact** — see the list in `README.md`. Both readings
are inferences about a format RRA has not written down. Step 5's live run registers an item
whose quantity unit is two characters and one whose unit is three, and reads back what the
authority accepts.

### The near-miss worth recording

The first version of this rule read `X` as *left-padding to two characters*, fitted on the two
shortest examples, and was pinned by a test that fed the builder a quantity unit of **`NOX`** —
a string that does not appear in §4.6 at all. A wrong rule and a wrong input cancelled out, and
the test was green. The unit on invoice 22 is `NO` (31, Number); `NOX` was never a code.

`codes.QUANTITY_UNITS` now carries §4.6's own list and
`test_no_case_above_feeds_a_quantity_unit_the_authority_does_not_publish` refuses any case fed
a unit RRA does not publish. That guard, not the rule, is what would have caught this.
