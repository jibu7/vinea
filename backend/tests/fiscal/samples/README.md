# Payload samples

One JSON document per pydantic model in `app/fiscal/rwanda/payloads.py`, round-tripped by
`tests/fiscal/test_payloads.py`.

**These are the documents' own samples**, lifted from the `JSON REQUEST SAMPLE` /
`JSON RESPONSE SAMPLE` blocks of the two specifications pinned under `docs/rra/`:

* `docs/rra/VSDC_SPECIFICATION_DOCUMENT_v1.0.5_okay.pdf` — all but two of them;
* `docs/rra/osdc_documentation_v1.0.1_2022-04-08.pdf` — `init_response`,
  `sales_response_osdc` and `purchase_feed_response`.

Two edits were made, both recorded here and nowhere else:

1. **Whitespace removed from keys.** The specifications wrap long tables across columns, so
   text extraction yields keys like `"curRcptNo "` and `"sa lesSttsCd"`. No RRA field name
   contains a space, so this is lossless.
2. **One typo repaired.** The v1.0.5 sales sample reads `prcOrdCd”:”123456”` — a smart-quoted
   key missing its opening quote. Repaired to `"prcOrdCd": "123456"`; nothing else in that
   sample was touched.

## What round-tripping them proves

That the models parse what RRA publishes and re-emit it unchanged: no field dropped, no amount
re-scaled, no required field the document's own sample does not carry. Because requests are
`extra="forbid"`, it also proves the models know **every** field the samples use.

It does not prove the samples are internally consistent — they are not. The v1.0.5 sales sample
carries `taxAmtB: 94576` beside `totTaxAmt: 38135`, where the item list sums to 38135; and the
tax on a line is rounded to whole francs in the Rwandan samples (`taxblAmt: 200000` →
`taxAmt: 30508`) and to two decimals in the Korean ones (`taxblAmt: 660000` →
`taxAmt: 100677.97`). Those are the documents' own inconsistencies, and the build follows the
CIS specification and the certification checkpoint sheet where they disagree.

## Regenerating

The extraction script is not checked in — these files are the artefact. Replacing one means
copying the block out of the PDF again and applying the two edits above.
