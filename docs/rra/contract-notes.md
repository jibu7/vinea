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

The same document's other Rwandan samples are `RW1NTXU0000001` / `RW1NTXU0000006`, whose items
carry `qtyUnitCd: "U"` in the same request bodies — a one-character code left-padded to two
with `X`. So the rule the build implements is **left-pad the quantity unit with `X` to at least
two characters, never truncate**; three-character codes (`BLL`, `CMT`, `TNE`, `GRM`, `MWT`, …)
go through whole, as the document's own `KR2AMXBLL0000001` keeps `BLL`.

**The live receipts disagree.** `CONTACTEUR.pdf` carries `RW2NTXNOX0000011` for an item whose
quantity unit is `NO` (Number) — an `X` on *both* sides. That is one vendor's convention, not
the specification's, and it is recorded here rather than copied.

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

## 7. The open question: how the tax on a line is rounded

Three pieces of evidence, pulling two ways.

* **RRA's own Rwandan API sample** sends whole francs: `taxblAmt: 200000` with `taxAmt: 30508`,
  where 200 000 × 18/118 is 30 508.47. Its Korean samples send two decimals
  (`taxblAmt: 660000`, `taxAmt: 100677.97`).
* **Checkpoint 47** requires tax values rounded *on two decimals*.
* **A live receipt prints two decimals**: `CONTACTEUR.pdf` shows `Total Tax B Rwf 9,152.54` on
  a `Total B-18%` of 60 000 — 60 000 × 18/118 to two places.

Vinea's ledger holds RWF tax in whole francs, because rule 6 rounds to the currency's decimal
places and RWF has none. The build therefore **sends the posted franc figure**, which is what
RRA's own Rwandan sample does, and accepts that RRA may print a receipt whose tax differs from
the ledger's by under one franc per line. The alternative — sending a two-decimal tax — would
make the receipt disagree with the ledger, and the VAT return is a query over the ledger, so
the return would then not tie to the receipts.

The in-repo sandbox models this by accepting a line's tax within **one base-currency unit** of a
two-decimal recomputation, which is strict enough to catch the defect the check exists for (a
line taxed exclusively and reported inclusively is out by 18 % against 15.25 %). The live run at
step 5 is what settles it.

`backend/tests/fiscal/test_builders.py` censuses the divergence on every property run rather
than asserting it away.
