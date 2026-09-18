# RRA documents this phase is written to

Phase 7 implements Rwanda fiscalization against published RRA material. It is **pinned** here —
version, date, source and SHA-256 — so that a field name in
`backend/app/fiscal/rwanda/payloads.py` can be traced to a document somebody can open, and so
that a later revision is a visible change rather than a silent one.

## The three specifications

| File | Document | Version | Date | SHA-256 |
|---|---|---|---|---|
| `VSDC_SPECIFICATION_DOCUMENT_v1.0.5_okay.pdf` | VSDC API documentation | 1.0.5 | 2023-05-16 | `25df854736b60e7e4eb128bf99904813c5d96acf99464b7b48f23c7fab3df9c1` |
| `osdc_documentation_v1.0.1_2022-04-08.pdf` | OSDC documentation | 1.0.1 | 2022-04-08 | `98e9d6e9b11ab71c44982c5fa5c365a1aead01c8db684d885b9c406774655feb` |
| `CIS_for_VSDC_technical_Specifications_New.pdf` | Technical Specification of CIS for VSDC | 1.0 | 2018-03 | `b842ac71d675b8eb8abbc5da407c4c50489422e3412f30ba841038ed01c77f3b` |

Sources:

* VSDC — `https://www.rra.gov.rw/fileadmin/user_upload/VSDC_SPECIFICATION_DOCUMENT_v1.0.5_okay.pdf`
* OSDC — `https://www.rra.gov.rw/fileadmin/user_upload/osdc_documentation_v1.0.1_2022-04-08.pdf`
* CIS — `https://www.rra.gov.rw/fileadmin/user_upload/CIS_for_VSDC_technical_Specifications_New.pdf`

`backend/tests/fiscal/test_rra_documents.py` enforces every hash above: a file whose content
does not match is a revision the payload field names have to be re-checked against before the
hash is updated.

## The owner-supplied material

| File | What it is | SHA-256 |
|---|---|---|
| `EXCEL_SHEET_application_form_RRA_VSDC_okay(Compliance table).csv` | The RRA certification checkpoint sheet — 75 numbered requirements | `fea718cf650d7ac64589c1a05d904219587a818f66d48914a82dd5411419ba76` |
| `Rwanda-Revenue-Authority-logo.png` | The RRA logo, for CIS §7.29 (checkpoint 32) | `5dcc58d56a2bd56d46275de972c42bb2dfbe62562c4e8912733d58c2b3973b6d` |

### The live receipts, and why they are not here

Nine live EBM 2.1 receipts, from six Rwandan vendors, were read while this adapter was written.
**None is committed.** They are real tax documents carrying real taxpayers' TINs, trading
names, addresses and telephone numbers, this repository is public, and the evidence outlives
the file: everything they settled is below, in `contract-notes.md` §7 and §8, and in tests that
name each receipt by invoice number.

| Receipt | What it settled |
|---|---|
| `CONTACTEUR.pdf` (removed from the tree) | Two decimals on the printed tax (60 000 → 9 152.54). Item code `RW2NTXNOX0000011`. |
| `desktop iyaga transport 2.pdf` (removed from the tree) | One exempt line — the `A-EX` bucket in the totals block. |
| `12.pdf` (removed from the tree) | A **copy** (`CS`), which is CIS §7.18's reprint rule in practice. |
| RWANLY invoice 1 (removed from the tree) | A copy (`1/1CS`), one exempt line, tax `0.00`. Item code `RW2NTXU0000002`. |
| RWANLY invoice 22 (removed from the tree) | `22/22NS`, 168 000 → 25 627.12. Item code `RW2NTXNOX0000014`. |
| RWANLY invoice 57 | An exempt-only sale. Item code `RW2NTXNOX0001366`. |
| **TESKO invoice 10057** | **The decisive one.** Eight lines, `Total Tax B 18,122.04` — which only the per-line rounding reproduces. Eight item codes spanning one-, two- and three-character segments. |
| HUSSEIN invoice 9434 | Three lines, 282 240 → 43 053.56. `CN2AMXM2X0000001`, the strongest single item-code case. |
| MTN NSIN000032912 | A different POS stack (Ishyiga middleware: `ISH:` on the receipt number). 95 000 → 14 491.53. |
| M TOOLS invoice 7329 | **The dissenter.** `EnvyERP v2.1`, item codes `RW2OUNO…` with no terminators — the other production convention (§8). |

Three findings came out of them, all pinned by tests rather than by the files:

* **Rounding is per line, then summed** — TESKO 10057 is the only receipt where the two
  candidate methods differ, and it comes down on the per-line side (`contract-notes.md` §7).
* **The item-code rule**, from sixteen machine-generated codes — and the discovery that RRA
  accepts a second convention too (§8).
* **Two decimals on the printed tax**, on every receipt that prints one.

Note that `git rm` removed the five that had been committed and **does not scrub them from
history**: they entered on `main` via PR #48 and remain in that history until somebody rewrites
it, which is a separate and destructive operation nobody has asked for.

## What the documents settled

Read `contract-notes.md` for the contract itself. The findings that changed code:

* **`saveStockMaster` takes one item per call**, not a list (§3.3.8.3). The model had a list.
* **The refund reasons (§4.16) are not a tidy taxonomy** — `06` is "Refund", `07` is "Wrong
  Customer TIN". The build had guessed thirteen plausible names; all thirteen were wrong.
* **Money crosses the wire as a JSON number**, not a quoted string (`"taxAmt":30508`).
* **The item-code quantity-unit segment is left-padded with `X` to two characters and never
  truncated** — §4.17's own example uses a two-character code unpadded, and the spec's other
  samples pad a one-character code.
* **Transaction progress (§4.11) has six codes, not seven**, and stock in/out (§4.15) has a
  `15 Discarding` the build had missed.

## Still open — what step 5's sandbox run is for

The line-arithmetic, refund-sign and copy-label rules in `contract-notes.md` §7, §7a and §7b
are **working assumptions** taken from the owner's description of the Sage 200 Evolution
(Ishyiga VSDC driver) integration, not from a pinned document. The pinned RRA PDFs win where
they say otherwise. The live run is what tests them, and it should come back with an answer to
each of these:

1. **The line relations.** `splyAmt = prc x qty`, `dcAmt = splyAmt x dcRt / 100`,
   `taxblAmt = splyAmt - dcAmt`, `taxAmt = taxblAmt x r/(100+r)` at two decimals, per line and
   summed into the header. Register a sale with a discounted line and an undiscounted one and
   confirm the engine accepts both. Read the wire-vs-ledger census beside it.
2. **The refund sign.** A credit note with positive amounts under `rcptTyCd R` and a live
   `orgInvcNo`.
3. **The copy label.** That a reprint needs no call, and that the relabel to `n/nCS` is the
   whole of what changes.
4. **The item-code segment rule.** The build terminates a two-character packaging or quantity
   segment with `X`. Sixteen machine-generated codes from five vendors do this; four codes from
   a sixth (`EnvyERP`) do not, and **RRA certified both** — so this is likely not enforced at
   all. Register an item with a two-character quantity unit and one with a three-character unit
   and record what comes back, so the question is closed by evidence rather than by inference
   (`contract-notes.md` §8).
5. **The QR payload.** CIS §7.24.7 gives a format. Two of the nine live receipts print a
   scannable QR and both were photographed folded, at a resolution no decoder will read —
   `cv2.QRCodeDetector` fails on every crop and scale tried. **A flat scan of any fiscalized
   receipt, or one line of its QR text, closes this**; until then step 8 builds to §7.24.7 as
   written.

And one precondition, now answered:

6. **Test-environment access.** The live run needs a TIN, branch id and device serial approved
   on `https://myrratest.rra.gov.rw`. **Status (18 September 2026): not held.** The owner
   confirmed it at step 5; no application is outstanding. So step 5 closed without the live
   run, and questions 1–5 above stay open — every one of them is a question only Kigali can
   answer, and none of them blocks code that is written to the documents.

   What this costs, stated plainly so it is not rediscovered at step 9: the phase will close
   **code-complete, certification pending**. `docs/rra/certification.md` (step 9) is where the
   runbook for obtaining access goes, and the moment access exists, rows 0–3 of
   `tests/fiscal/test_acceptance_tape.py` are the script to run against
   `https://sdcsandbox.rra.gov.rw` — the tape is written so that the only thing which changes
   is the device's `base_url`, TIN and serial.

## Precondition (b), confirmed

On **16 September 2026** RRA's integration page linked exactly the VSDC v1.0.5 and 2018 CIS
documents pinned above, and OSDC v1.0.1 is the revision published on `rra.gov.rw`. **No newer
revision of any of the three has been published.** The hashes above are therefore current, not
merely reproducible, and `test_rra_documents.py` will fail the day one of them is replaced.
