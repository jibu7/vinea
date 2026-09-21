# RRA certification — the runbook

**Read this first: P7 alone cannot pass certification, and that is by design.** The checkpoint
sheet requires **training receipts (TS/TR, rows 11, 41, 42)**, a **proforma receipt (PS, rows
12, 43)** and a **PLU report (row 24)**. All three are explicitly out of scope for Phase 7 (the
phase prompt's "Out of scope — do not build"), and none of them is a defect or an oversight:
they are work that has not been scheduled. Vinea can be *submitted* for certification only once
they exist. Everything else on the sheet is built, and §4 below says where each row lives.

This file is the runbook for what happens after that: what to send, what to register, how to
run the tape against RRA's own sandbox, and which questions only a live device can answer.

Phase 7 therefore closes **code-complete, certification pending**, which is the Definition of
Done's second branch. The deviation is named in `docs/p7-final-report.md`.

---

## 1. The application

**Where it goes:** `cis_sdc_certification@rra.gov.rw`.

**What it needs.** The list below is the one on RRA's *"How to acquire EBM"* page as it stood
when this phase pinned its documents — **as of 16 September 2026**. Nothing in this repository
can re-check it: the agent that wrote this file has no browser, and a list transcribed from
memory is worse than a list with a date on it.

> **Owner item (JP).** Re-open RRA's "How to acquire EBM" page, paste the current list here,
> and change the date on this heading. Until that happens the list below is what the phase was
> written against and is not evidence of what RRA asks for today.

| # | Enclosure | Where it comes from | Status |
|---|---|---|---|
| 1 | Business registration (RDB certificate) | the company | owner |
| 2 | RSSB clearance | the company | owner |
| 3 | Tax clearance certificate | the company | owner |
| 4 | Physical address of the business | the company | owner |
| 5 | Product brochure | marketing | **not written** |
| 6 | Product warranty statement | legal | **not written** |
| 7 | User manual | `docs/` — nothing yet covers the fiscal screens end to end | **not written** |
| 8 | Installation guide | `README.md` covers the developer stack, not an installation | **not written** |
| 9 | Programming and configuration manual | the EBM devices screen and the Defaults keys are the material | **not written** |
| 10 | Software support SLA | commercial | **not written** |

Rows 5–10 are checkpoint rows 1–5 on the compliance sheet, and they are documents rather than
code. They are listed here so that "the build is ready" is never mistaken for "the application
is ready".

**Status of the application itself: not sent.** Recorded in `docs/p7-final-report.md` as an
owner item, not as a build gap.

---

## 2. The MRC, and where Vinea keeps each part

CIS §3.2.1.ii gives the machine registration code as **`BBBCCNNNNNN`** — eleven characters:

| Segment | Width | What it is | Where Vinea holds it |
|---|---|---|---|
| `BBB` | 3 | the **software developer's** identifier, assigned by RRA on certification | not held yet — it does not exist until RRA issues it |
| `CC` | 2 | the **certificate number** of the certified CIS | not held yet — issued with the certificate |
| `NNNNNN` | 6 | the **serial** of this installation of the CIS | `fiscal_devices.dvc_srl_no`, keyed on the EBM devices screen and sent to `/initializer/selectInitInfo` |

Vinea does not compose an MRC. It **stores the one the authority returns**, whole, in
`fiscal_devices.mrc_no` (the sandbox returns `WIS01006230`), and prints it on every receipt
under CIS §7.24. That is the right split: the first five characters are facts about the
*vendor's certification*, not about a tenant's install, and a build that generated them would
be inventing an identifier RRA owns.

When the certificate is issued, the `BBB` and `CC` RRA assigns belong in this file — the code
needs no change to accept them, because it never derives them.

---

## 3. The test environment, and the run

Three things have to exist before any of this is possible, and none of them is code:

1. **A taxpayer account on `https://myrratest.rra.gov.rw`** — RRA's test portal.
2. **An approved TIN, branch id and device serial** on that account. The branch id is the
   two-character `bhfId`; the head office is `00`.
3. **The sandbox endpoint**, `https://sdcsandbox.rra.gov.rw`, which speaks the same contract as
   `app/fiscal/rwanda/sandbox.py` answers in this repository.

**Status (21 September 2026): not held.** Confirmed by the owner at step 5 and unchanged since;
no application is outstanding. This is the Definition of Done's precondition (d), and it is the
reason the phase closes certification-pending.

### When access exists — the run, in order

Everything below is a screen, because the point of the run is that an operator can do it.

1. **Register the device.** Maintenance → Tax → EBM devices → *Register device*: the branch,
   profile `vsdc` (§5 below on which profile), environment **`production`** is wrong here —
   choose `test`, base URL `https://sdcsandbox.rra.gov.rw`, the approved device serial, and the
   approved `bhfId`.
2. **Initialize.** The same row's *Initialize* button. What must come back: an `sdcId`, an
   `mrcNo`, a `dvcId` and the three keys. The keys are encrypted with Fernet on the way in and
   are readable through no endpoint — `has_keys` is all any screen is told. If initialization
   is refused, the message carries RRA's own `resultCd`; `884` is an unknown taxpayer and `894`
   is a transport-class failure that will retry.
3. **Sync codes.** The same row's *Sync codes*. Compare the §4 code tables that come back
   against `app/fiscal/rwanda/codes.py`: a code the live table carries and the seed does not is
   a revision to record here before anything is declared against it.
4. **Rows 0–3 of the acceptance tape**, through the screens, exactly as
   `frontend/e2e/p7-cycle-tape.spec.ts` drives them against the container:
   * register the items (first fiscal use registers them; the item enquiry shows `item_cd`),
   * an invoice to a customer with a TIN and a purchase code → the queue → the receipt,
   * the printed receipt, then a copy print,
   * a credit note with a §4.16 reason against that invoice.

**Record the run as `docs/rra/sandbox-run-<yyyy-mm-dd>.md`**: the four rows, what came back, and
**the six answers in §5 below**. Redact the three device keys and the taxpayer's own TIN if it
is not the company's — the redaction test (`backend/tests/fiscal/test_key_redaction.py`) covers
what Vinea stores, not what somebody pastes into a markdown file.

---

## 4. The checkpoint sheet, row by row

Every one of the 75 rows of
`EXCEL_SHEET_application_form_RRA_VSDC_okay(Compliance table).csv`, mapped to the screen, test
or document that shows it — or to **not in P7**, with what it would take.

Rows 1–5 are the enclosures of §1. Rows 6–75 are the build.

| # | Requirement | Where it is shown |
|---|---|---|
| 1 | Product brochure | **not in P7** — §1, owner |
| 2 | Product warranty statement | **not in P7** — §1, owner |
| 3 | User manual | **not in P7** — §1, owner |
| 4 | Installation guide | **not in P7** — §1, owner |
| 5 | Programming and configuration manual | **not in P7** — §1, owner |
| 6 | Configure the MRC of each connected device | §2 above · `fiscal_devices.mrc_no`, Maintenance → Tax → EBM devices |
| 7 | Generate every receipt type with the §4 detail | `frontend/src/features/fiscal/receipt-layout.tsx` · `p7-cycle-tape.spec.ts` asserts the printed sheet — **partly**: TS/TR/PS are not built (rows 11, 12) |
| 8 | Normal Sale (NS) | `p7-cycle-tape.spec.ts` "the invoice is signed, prints its SDC block" · receipt `1/1 NS` on paper |
| 9 | Normal Refund (NR) | `p7-cycle-tape.spec.ts` "the credit note names the invoice it refunds" · receipt `1/2 NR` |
| 10 | Copy (CS/CR) of a normal receipt | `p7-cycle-tape.spec.ts` copy print — `COPY` and the §15 warning on the paper. **Open:** whether the copy takes a `CS` counter (§5.3) |
| 11 | Training (TS, TR) | **not in P7** — out of scope by the phase prompt. Needs a training mode, a receipt label and a separate counter run |
| 12 | Proforma (PS) | **not in P7** — there is no proforma document; P6 kept quotations out |
| 13 | Communication protocol to connect CIS to VSDC | `app/fiscal/rwanda/routes.py` (both profiles) · `app/fiscal/rwanda/payloads.py` · `tests/fiscal/test_payloads.py` round-trips the documents' own samples |
| 14 | Request a receipt signature by sending receipt data | `app/fiscal/rwanda/builders.py` · `app/fiscal/drainer.py` |
| 15 | Receive the response and add it to the receipt | `normalize_receipt()` in `rwanda/adapter.py` · `fiscal_receipts` · the SDC block on the printed sheet |
| 16 | **No receipt on error or failure from VSDC** | `fiscal_receipt_pending` — `app/fiscal/printing.py`; the Print button is disabled and says why (`p7-cycle-tape.spec.ts`, the authority-down test) |
| 17 | Print a receipt for every transaction | the document detail print layout, for every fiscalized AR invoice and credit note |
| 18 | Print on different formats | the receipt is a 76 mm roll layout inside an A4 print sheet; `page.pdf()` in the tape renders it |
| 19 | 13 digits inclusive of 2 decimals | `NUMERIC(20,6)` amounts, `NUMBER 18,2` on the wire (`payloads.py`) |
| 20 | Daily X and Z reports (§18, §19) | Reports → Tax → Daily fiscal report · `app/fiscal/daily.py` · `tests/fiscal/test_daily_report.py` |
| 21 | A verifiable software version number on each receipt | **not in P7** — the receipt layout has no version line. One field, and a build stamp to put in it |
| 22 | No receipt when the VSDC is not functioning | as row 16 |
| 23 | Manage and send stock information | `app/fiscal/stock.py` — `stock_io` and `stock_master` beside every `StockPosting` · `tests/fiscal/test_stock_report.py` |
| 24 | PLU report (§21) | **not in P7** — out of scope by the phase prompt |
| 25 | Detailed reports of sales, purchases, stock, items and importation | Reports → Tax (VAT return, daily fiscal, receipts listing) · Enquiries → Tax (receipts, queue history) · `/fiscal/purchases` · `/fiscal/imports` · the P5 stock reports |
| 26 | Register the means of payment | `partner_documents.payment_method` → `pmtTyCd 01–07` (decision 7); on the Invoice and Credit note screens, printed on the receipt, and bucketed on the Z |
| 27 | No transaction value without identifying the good/service and quantity | `fiscal_item_required` — every line of a fiscalized document carries an `item_id` (decision 3) |
| 28 | No modification of an approved receipt; refund by reference instead | `fiscal_receipts` is immutable by trigger; posted entries are append-only (architecture rule 3); a reversal queues an **NR** against `orgInvcNo` (decision 7) |
| 29 | One original per receipt | `copy_count` starts at 0 and every further print is a copy — `app/fiscal/printing.py` |
| 30 | Re-print as COPY | as row 10 |
| 31 | QR code as specified | `receipt-layout.tsx` builds §7.24.7's payload. **Open:** no live receipt's QR has been decoded (§5.5) |
| 32 | Official RRA logo on every receipt | `docs/rra/Rwanda-Revenue-Authority-logo.png` is pinned; the layout prints a bordered placeholder — **a plan deviation, named in the final report** |
| 33 | Header: name and address, at least three lines | `receipt-layout.tsx` — taxpayer name, TIN, branch address |
| 34 | TIN in the first block, SDC information before the last | the layout's order, asserted as text on the printed sheet by the tape |
| 35 | Client TIN, name and mobile; mobile where there is no TIN | `custTin` / `custNm` / `custMblNo` from the partner (decision 6); `Client ID:` on the paper |
| 36 | Date, hour, minute, second of issue | `sdcDateTime`, printed `Date: dd/mm/yyyy Time: hh:mm:ss` |
| 37 | Purchase code from sales | `purchase_code` on the Invoice screen, required for a customer with a TIN (`purchase_code_required`) |
| 38 | Purchase code from purchases | `prcOrdCd` on the purchase payload — `app/fiscal/purchases.py` |
| 39 | Price discounts on a receipt | `dcRt` / `dcAmt` per line, and the discount line under the item on the paper |
| 40 | A copy carries the same sales information | the copy print renders the same stored receipt block — nothing is recomputed and no call is made |
| 41 | Training receipt content and label | **not in P7** — see row 11 |
| 42 | Training mode activated by a different function | **not in P7** — see row 11 |
| 43 | Proforma receipt content and label | **not in P7** — see row 12 |
| 44 | A journal issued at the same time as the receipt | `journal_entries` — the posting *is* the journal, and the receipt is derived from it; the document detail drills receipt → document → entry |
| 45 | Configure tax rates A–D | Maintenance → Tax → Tax types, with the EBM class column · `tax_codes.fiscal_tax_type` |
| 46 | Print tax values with their labels | `TOTAL B-18.00%`, `TOTAL TAX B` on the paper |
| 47 | Round tax to two decimals, half-up | `split_tax()` in `app/kernel/money.py`; half-up is the kernel's rule (architecture rule 6) |
| 48 | A rate > 0 prints on every receipt | `receipt-layout.tsx` · `tests/fiscal/test_receipt_block.py` |
| 49 | A zero rate prints only when used | same |
| 50 | A consecutive receipt counter regardless of type | `tot_rcpt_no` — `assert_fiscal_invariants` clause 4 proves it strictly increases per device |
| 51 | Counters start at 1 and increment by 1 | the authority issues them; invariant 4 checks what comes back, and invariant 3 checks `invc_no` is gapless |
| 52 | Item count = lines on the receipt | `totItemCnt`, printed as `ITEMS NUMBER` (CIS §7.27) |
| 53 | Manage stock for countable items | P5's stock ledger; `fiscal_requires_block` locks the negative-stock policy so a fiscalized company cannot sell what it does not hold (CIS §7.30) |
| 54 | Print a copy from an existing normal receipt | as row 10 |
| 55 | COPY/TRAINING/PROFORMA label and the text after the tax block | the COPY half is built and asserted on paper; the other two are rows 11 and 12 |
| 56 | A refund prints with a minus in front of every amount | `receipt-layout.tsx` — `sign = -1` on a refund (§14) |
| 57 | A refund references the normal receipt it is made against | `REF. NORMAL RECEIPT#` on the paper; `org_invc_no` on the row; `refund_original_required` refuses one that resolves to no invoice |
| 58 | Initialization API | `initialize_device` · `p7-cycle-tape.spec.ts` row 0, over HTTP against the container |
| 59 | Codes API | `sync_codes` with the `lastReqDt` watermark · `fiscal_codes` |
| 60 | Item codes created automatically on item creation | `fiscal_items.item_cd` from the `FITM` run, §4.17's format (decision 8). **Open:** the segment-terminator rule (§5.4) |
| 61 | Item classification API (UNSPSC) | `sync_item_classes` · the class typeahead on the Item screen · `fiscal_class_missing` at post |
| 62 | Customer API | Verify TIN on Customers and Suppliers — a synchronous read, not a queue row |
| 63 | SaveItem API | the `item` outbox kind, queued on first fiscal use and on change (hash differs) |
| 64 | SelectItem API | `app/fiscal/rwanda/routes.py`; the item enquiry shows registration status |
| 65 | Notice API | fetched and listed only — the phase prompt keeps the notice board out beyond that |
| 66 | Import items API | `fetch_imports` → `fiscal_import_declarations` → `/fiscal/imports` |
| 67 | Importation request date strictly increasing | the `lastReqDt` watermark, stored **only after a `000`** (decision 2) |
| 68 | Imported item status update API | Approve / Reject on `/fiscal/imports` (`imptItemSttsCd 3` / `4`) |
| 69 | Sales transaction save API | the `sale` / `refund` outbox kinds · the whole of the tape |
| 70 | Select purchase-sales transaction API | `fetch_purchase_feed` → `/fiscal/purchases` |
| 71 | Purchase transaction save API | the `purchase` and `purchase_confirm` kinds (decision 9) |
| 72 | Stock In/Out save API | as row 23 |
| 73 | SaveStockMaster API | `stock_master`, one row per (item, branch) touched, snapshotted at enqueue |
| 74 | Stock synchronised in real time across importation, purchase and sale | the outbox is written **in the posting transaction** — `assert_fiscal_invariants` clause 1 |
| 75 | Display and handle errors received from VSDC | the queue screen's row detail: `resultCd`, the message, the action log; `failed` blocks the device and says so |

**Count:** 75 rows. **Not in P7:** 1–5 (owner documents), 11, 12, 21, 24, 41, 42, 43 — **twelve
rows**: five are documents somebody has to write, six are the training / proforma / PLU work the
phase prompt excluded, and one (21) is a single missing field on the receipt — a verifiable
software version number, which is one line in the layout and a build stamp to put in it.

---

## 5. The open questions, and what would settle each

These are the questions no amount of reading settles, listed with the *evidence* that closes
them rather than the opinion that would.

1. **`dcAmt` on an undiscounted line.** The wire carries an inclusive price at two decimals and
   the ledger rounds to the franc, so a line with no discount can still have a residue in
   `dcAmt` (P7 decision 6; the census is in `docs/p7-step-2-report.md`).
   **Settled by:** registering a sale with one discounted and one undiscounted line against
   `sdcsandbox.rra.gov.rw` and reading the `resultCd`. An `881`-class refusal means the residue
   is not tolerated and `mapping.py` changes; `000` means it is.
2. **The refund sign on the wire.** Built as positive amounts under `rcptTyCd R` with
   `orgInvcNo` (the printed side is settled — checkpoint 56 requires a minus on the paper).
   **Settled by:** one live credit note. The ledger does not change either way; a payload rule
   changes in `mapping.py`.
3. **Whether a copy needs a `CS` counter.** One live receipt (`12.pdf`, `1/1CS`) relabels
   rather than recounts, which is one vendor's behaviour and not a rule in the document.
   **Settled by:** printing a copy against the live sandbox and seeing whether the authority is
   called at all. Vinea currently makes no call (v1.0.5 sends `N` only).
4. **The item-code segment rule.** `X` terminates a one-character packaging or quantity
   segment; sixteen machine-generated codes from five vendors do this and four from a sixth do
   not, **and RRA certified both** (`contract-notes.md` §8). RRA accepted a 16-character code on
   a signed receipt, so the field is not fixed-width either.
   **Settled by:** registering one item with a two-character quantity unit and one with a
   three-character unit and recording what comes back.
5. **The QR payload.** Built to CIS §7.24.7. Two of the nine live receipts print a scannable QR
   and both were photographed folded, at a resolution no decoder reads.
   **Settled by:** a flat scan of any fiscalized receipt, or one line of its QR text.
6. **Verify's behaviour against a live device.** `Verify with device` re-runs
   `initialize_device` and reads `lastSaleInvcNo` / `lastPchsInvcNo` / `lastSaleRcptNo`. Against
   the sandbox that is safe; on a real device re-initialization is **also how keys are
   reissued**, and whether RRA treats a verification call as a re-registration is unknown.
   **Settled by:** one verification against the live sandbox, watching whether the returned keys
   change. If they do, Verify needs its own read-only call and decision 4 changes.
   *(Raised at step 2 and carried since — `docs/p7-step-2-report.md`.)*
7. **`orgSarNo` (VSDC §9.1) and the purchase-reversal direction (§9.2).** Neither is exercised by
   the sandbox and neither is reachable from a screen today.
   **Settled by:** a live return to supplier against a registered purchase.
8. **Reconciling Vinea's Z with the device's own daily report.** Vinea's Z is computed from
   `fiscal_receipts` and owns its members **by receipt counter** (revision `0026`, amending
   decision 11). A physical VSDC keeps its own daily totals.
   **Settled by:** taking a Z on the live sandbox and comparing it to what the device reports
   for the same period. The counter window is printed on the Z screen (`Covers receipts 1–4`)
   precisely so the comparison is possible by hand.
9. **The class-05 gap in the sandbox.** The in-repo sandbox's code fixtures do not carry code
   class `05` (`docs/p7-step-3-report.md`). It is a fixture gap rather than a product one, and
   the live sync in §3 step 3 is what closes it.
10. **RWF decimals.** The wire is two decimals and the ledger is zero (architecture rule 6), and
    since step 8 that is also a **rendering** contract: the daily report and the receipts
    listing print declared figures at two decimals on a zero-decimal currency, deliberately, so
    the residue stays visible on the screen as well as in the data.
    **Settled by:** nothing — it is decided. Recorded here because it is the question every
    reviewer asks, and the answer has a reason.

---

## 6. Which route profile to certify

`fiscal_devices.profile` selects between the v1.0.5 **VSDC** paths and the v1.0.1 **OSDC**
paths; the payload vocabulary is shared, `cmcKey` rides on the `osdc` profile only, and
`normalize_receipt()` maps both response shapes onto one `FiscalReceipt`. Both are exercised —
the backend acceptance tape runs rows 0–3 under each.

**Which one RRA certifies a cloud vendor for is a conversation, not a code decision**, and the
build deliberately does not take a position. Ask `cis_sdc_certification@rra.gov.rw` before the
application goes in, and record the answer here.
