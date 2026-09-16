# What `app/fiscal/rwanda/` was written against

The three RRA PDFs could not be fetched in the build environment (`README.md` says why), so
this file is the contract the Rwanda adapter was built to: the paths, code tables, field names
and formats as the Phase 7 prompt reproduces them, each against the document section it comes
from. It exists so that every RRA name in the code has a citable source *in the repository*,
and so that checking the code against the real documents is a diff against this file rather
than a re-reading of the source.

**This is a transcription, not a specification.** Where it and a pinned PDF disagree, the PDF
wins and this file is wrong. Amend it in the same commit as the code it explains.

## 1. Routes — two profiles, one vocabulary

`fiscal_devices.profile` selects the profile per device. The payload vocabulary is shared; the
`osdc` profile adds `cmcKey` to every request body.

| Operation | `vsdc` (v1.0.5) | `osdc` (v1.0.1) |
|---|---|---|
| initialize | `/initializer/selectInitInfo` | `/selectInitOsdcInfo` |
| code list | `/code/selectCodes` | `/selectCodeList` |
| item classes | `/itemClass/selectItemsClass` | `/selectItemClsList` |
| customer (TIN) lookup | `/customers/selectCustomer` | `/selectCustomer` |
| branch list | `/branches/selectBranches` | `/selectBhfList` |
| notices | `/notices/selectNotices` | `/selectNoticeList` |
| branch customers | `/branches/saveBrancheCustomers` | *(no OSDC path given)* |
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

Servers (OSDC §2.2): test `https://sdcsandbox.rra.gov.rw`, production `https://api-ebm.rra.gov.rw`.

## 2. The sales response, and the one receipt shape behind both

`normalize_receipt()` maps both onto `FiscalReceipt`.

| `FiscalReceipt` field | `vsdc` v1.0.5 | `osdc` v1.0.1 |
|---|---|---|
| `rcpt_no` | `rcptNo` | `curRcptNo` |
| `tot_rcpt_no` | `totRcptNo` | `totRcptNo` |
| `intrl_data` | `intrlData` | `intrlData` |
| `rcpt_sign` | `rcptSign` | `rcptSign` |
| `sdc_datetime` | `vsdcRcptPbctDate` | `sdcDateTime` |
| `sdc_id` | `sdcId` | *(from initialization)* |
| `mrc_no` | `mrcNo` | *(from initialization)* |

Every response carries `resultCd`, `resultMsg`, `resultDt`; `000` is success.

## 3. Code tables (VSDC §4)

Only the classes this phase reads. `sync_codes` refreshes them from `/code/selectCodes`; the
seeded values below are the fallback and the thing the sandbox serves.

* **04 — tax type.** `A` exempt, `B` standard 18%, `C` zero-rated, `D` non-VAT. The programmed
  rates printed on every receipt are A 0, B 18, C 0, D 0.
* **07 — payment method.** `01` cash, `02` credit, `03` cash/credit, `04` bank cheque,
  `05` debit/credit card, `06` mobile money, `07` other.
* **10 — quantity unit.** The unit an item is counted in (`U` each, `KG`, `L`, …). Mapped from
  `uoms.fiscal_quantity_unit`.
* **11 — transaction progress.** `01` wait for approval, `02` approved, `03` cancel requested,
  `04` cancelled, `05` credit note requested, `06` credit note approved, `07` transferred.
  Sales and purchases are sent at `02`.
* **12 — stock in/out type.** `01` import, `02` purchase, `03` return, `04` stock movement
  (in), `05` processing, `06` adjustment (in), `11` sale, `12` return, `13` stock movement
  (out), `14` processing, `16` adjustment (out).
* **17 — packaging unit.** `NT` (unpackaged / net) is the default; `BX`, `CT`, … as published.
* **24 — product type.** `1` raw material, `2` finished product, `3` service.
* **31 — registration type.** `M` manual, `A` automatic (used on purchases: a purchase this
  system originates is `M`, a confirmation of one RRA already holds is `A`).
* **32 — refund reason.** `01`–`13` as published; required on a credit note.
* **37 — sales receipt type.** `S` sale, `R` refund (with `salesTyCd` `N` normal, `C` copy,
  `T` training, `P` proforma — v1.0.5 says send only `N`).
* **38 — purchase receipt type.** `P` purchase, `R` return.

**Response codes.** `000` success; `801`–`899` request/validation refusals, of which this phase
names `881` (purchase code required for a business customer), `882`/`883` (purchase code
invalid / already used) and `884` (unknown TIN); `894` is the transport/temporary class;
`9xx` are server refusals; `994` is a duplicate — it returns no receipt data, which is why a
blind resend of a sale whose response never arrived is refused.

## 4. Item code format (VSDC §4.17)

`itemCd` = origin nation (2) + product type (1) + packaging unit (2) + quantity unit (2) +
a 7-digit per-taxpayer sequence, e.g. `RW1NTXU0000006` — origin `RW`, type `1`, packaging
`NT`, quantity unit `XU` (the documents' own samples left-pad the quantity-unit segment with
`X` to two characters), sequence `0000006`. Vinea claims the 7 digits from the `FITM` run.

**Open:** whether the padding character and width are exactly this. The samples show it; the
sandbox run at step 5 settles it.

## 5. What a CIS receipt must print (CIS for VSDC v1.0)

* §4 a–n — receipt minimum content.
* §5 — receipt labels `NS` normal sale, `NR` normal refund, `CS`/`CR` copy, `TS`/`TR`
  training, `PS` proforma. This phase issues `NS` and `NR` only.
* §7.17 — one cancellation per original, referencing the original SDC receipt number.
* §7.18 — one original print; every reprint is watermarked `COPY`.
* §7.22–7.23 — every programmed rate greater than zero prints on every receipt.
* §7.24 — the SDC Information block, and the QR content
  `invoice_date(ddmmyyyy)#time(hhmmss)#sdc number#sdc_receipt_number#internal_data#receipt_signature`.
* §7.25 — the `A/B RT` counter (`rcptNo` within the type / `totRcptNo` across types).
* §7.29 — the RRA logo.
* §7.30 — no receipt for goods the stock does not hold. This is why activating a device locks
  `gl_settings.negative_stock_policy` at `block`.
* §13–17 — the receipt examples the print layout follows.
* §18–19 — the X and Z daily reports.
* §3.2.1.ii — the MRC format `BBBCCNNNNNN` for software developers.

## 6. Ordering rule

VSDC §3.1: every stock in/out must have its sales invoice information sent **in advance**.
That is the reason the outbox drains per device in FIFO with one row in flight, and why a
non-terminal row blocks the device's queue behind it.

VSDC §2.2 item 4: the VSDC stops issuing after 24 hours without connectivity — hence the
`offline` flag on a device whose oldest queued row is older than 24 hours.
