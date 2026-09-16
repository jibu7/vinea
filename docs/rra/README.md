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
| `CONTACTEUR.pdf` | A live EBM 2.1 receipt — normal sale, four standard-rated lines | `dfbf3acc21000dcfd6f75d6fd0b34be0a783247f10ce59a60e3ec994e760a5b6` |
| `desktop iyaga transport 2.pdf` | A live EBM 2.1 receipt — normal sale, one exempt line | `a4060396bf86d961f53318b80592bf5d85a80b0e1d92159379f9e8a2b003368d` |
| `12.pdf` | A live EBM 2.1 receipt — a **copy** (`CS`) | `833085bb04b29e0c12f787d9365bfccac0ef1dac7ab4ce0e0c43b243aca40116` |
| `Screenshot 2026-09-16 142523.png`, `Screenshot 2026-09-16 142549.png` | Owner-supplied screenshots | `57be62635329c8e2d02a6099339a45983b9c1be238586c3b96280521824fea8b`, `e887d685e3969cf2068463a5a6473a53b81c22abe4a076787687da3257d220a3` |

The three receipt PDFs are another vendor's output, not RRA's own samples. They are evidence of
what a certified system actually prints — which is what step 8's layout is built to — and they
are treated as evidence rather than as specification wherever they and the 2018 document
differ. `contract-notes.md` records each place they do.

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

1. **Test-environment access.** Step 5's live run against `https://sdcsandbox.rra.gov.rw` needs
   a TIN, branch id and device serial approved on `https://myrratest.rra.gov.rw`. Not a blocker
   for steps 1–4.
2. **Whether RRA recomputes a line's tax at two decimals.** The evidence points both ways and
   is set out in `contract-notes.md` §7. The build sends the ledger's franc figure, which is
   what RRA's own Rwandan sample does.
3. **The QR payload.** CIS §7.24.7 gives a format; none of the three live receipts exposes its
   QR content as text, so the format is unconfirmed.
