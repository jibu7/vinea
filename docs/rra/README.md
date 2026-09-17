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

Five live EBM 2.1 receipts from one Rwandan vendor were held here while the adapter was
written against them. **They have been removed**: they are real tax documents carrying real
taxpayers' TINs, trading names, addresses and telephone numbers, this repository is public, and
their use case is finished — everything they settled is written down below and in
`contract-notes.md`, which is the durable form of the evidence.

What they were, and what each proved:

| Receipt | What it showed |
|---|---|
| `CONTACTEUR.pdf` | Normal sale, four standard-rated lines. `Total Tax B Rwf 9,152.54` on a `Total B-18%` of 60 000 — two decimals on the printed tax. Item code `RW2NTXNOX0000011`. |
| `desktop iyaga transport 2.pdf` | Normal sale with one exempt line — the `A-EX` bucket in the totals block. |
| `12.pdf` | A **copy** (`CS`), which is what CIS §7.18's reprint rule looks like in practice. |
| Invoice 1 (screenshot) | A copy (`1/1CS`), one exempt line, `Total A-EX Rwf 510,000.00` with tax `0.00`. Item code `RW2NTXU0000002`. |
| Invoice 22 (screenshot) | `22/22NS`, one standard-rated line of 168 000 with tax 25 627.12 — the second two-decimal data point. Item code `RW2NTXNOX0000014`. |

Two findings came out of them and are pinned by tests rather than by the files:

* **The rounding question gained two data points**, both on the two-decimal side
  (`contract-notes.md` §7).
* **The item-code rule was wrong**, and five machine-generated codes — three from the
  specification, two from these receipts — settle it (`contract-notes.md` §8,
  `backend/tests/fiscal/test_routes_and_codes.py`).

Note that `git rm` removes them going forward and **does not scrub them from history**: they
entered on `main` via PR #48 and remain in that history until somebody rewrites it, which is a
separate and destructive operation nobody has asked for.

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

## Still open

The rounding, refund-sign and copy-counter questions are **closed**, on the Sage 200 Evolution
standard — the certified Rwandan integration this build takes its conventions from. See
`contract-notes.md` §7, §7a and §7b. What remains:

1. **Test-environment access.** Step 5's live run against `https://sdcsandbox.rra.gov.rw` needs
   a TIN, branch id and device serial approved on `https://myrratest.rra.gov.rw`. Not a blocker
   for steps 1–4. **Status: still unstated** — the answer came back as an unfilled blank
   (`not held / applied on <date>`), so nobody has recorded which it is.
2. **The item-code segment rule.** `contract-notes.md` §8. The build terminates a
   two-character packaging or quantity segment with `X`, following five machine-generated codes
   against §4.17's one hand-written example, which it therefore does not reproduce. The live
   run should register an item with a two-character quantity unit and one with a
   three-character unit, and read back what the authority accepts.
3. **The QR payload.** CIS §7.24.7 gives a format; none of the five live receipts exposed its
   QR content as text, so the format is unconfirmed.

## Precondition (b), confirmed

On **16 September 2026** RRA's integration page linked exactly the VSDC v1.0.5 and 2018 CIS
documents pinned above, and OSDC v1.0.1 is the revision published on `rra.gov.rw`. **No newer
revision of any of the three has been published.** The hashes above are therefore current, not
merely reproducible, and `test_rra_documents.py` will fail the day one of them is replaced.
