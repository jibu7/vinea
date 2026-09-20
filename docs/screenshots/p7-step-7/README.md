# P7 step 7 — the transaction screens a fiscalized company works through

1440x900, light and dark, captured against the **e2e** stack by
`frontend/scripts/capture-p7-transactions.ts` in a single pass on a reset database. The e2e
overlay is required, not optional: every receipt in these shots was signed by the `ebm-sandbox`
service over HTTP, and the purchase feed and the import declarations are what it published.

```sh
make db-reset
export COMPOSE_FILE=docker-compose.yml:docker-compose.e2e.yml
docker compose up -d --wait db backend frontend ebm-sandbox worker
cd frontend
OUT=../docs/screenshots/p7-step-7 npx tsx scripts/capture-p7-transactions.ts
```

`ONLY` re-captures a subset — navigation included, so a subset run does not walk a screen whose
fixture it is not setting up:

```sh
OUT=../docs/screenshots/p7-step-7 ONLY=4-cis-receipt-print npx tsx scripts/capture-p7-transactions.ts
```

| # | Screen | Files |
|---|---|---|
| 1 | Invoice, the Fiscalization section on a customer with a TIN | `1-invoice-fiscal-section-{light,dark}.png` |
| 2 | Credit note, the *Refund of* picker over the partner's fiscalized invoices | `2-credit-note-refund-of-{light,dark}.png` |
| 3 | Document detail, the fiscal panel with the receipt and Copy print | `3-document-fiscal-panel-{light,dark}.png` |
| 4 | **The CIS receipt**, print media — the SDC block, the counters and the QR | `4-cis-receipt-print-{light,dark}.png` |
| 5 | Fiscal queue, the device card and its rows in send order | `5-fiscal-queue-{light,dark}.png` |
| 6 | A **sale** row's declared payload, redacted — an item row's response is `{}`, so it would show the redaction over nothing | `6-queue-row-payload-{light,dark}.png` |
| 7 | EBM purchases, one undecided purchase the authority is holding | `7-ebm-purchases-{light,dark}.png` |
| 8 | Import declarations, waiting to be matched to a Vinea item | `8-import-declarations-{light,dark}.png` |
| 9 | VAT return, the figures with the tie under them | `9-vat-return-{light,dark}.png` |
| 10 | FX revaluation: the preview per open document **and** a posted run with its entry and its next-day mirror | `10-fx-revaluation-{light,dark}.png` |
| 11 | A reversed sale holding **both** receipts, with the one Print produces marked | `11-document-both-receipts-{light,dark}.png` |

## Shot 4 is taken under `emulateMedia({ media: "print" })`

Because that is the only state the CIS layout exists in. It is `hidden print:block`, and the
document detail hides its own panels at print time when a receipt is there — so a screenshot of
the screen would photograph everything except the thing this phase is about.

Two details in that shot are worth naming, because both are rules rather than styling:

* **`Internal Data` and `Receipt Signature` are dashed every four characters** (CIS §7.24).
  They are read aloud off the paper and typed into MyRRA by hand, and the grouping is what makes
  that possible.
* **The lines are the declaration's, not the document's.** A document keyed in a foreign
  currency is declared in RWF from its frozen base amounts (decision 3), so the receipt's line
  amounts come out of the stored request rather than off the ledger line. They also carry the
  item's name, which a line keyed with an item and no typed description does not.

## Shot 11 is the reversal, and it is the one worth reading twice

Reversing a fiscalized invoice queues a **full refund** rather than cancelling the sale
(decision 7), so the document ends up holding two receipts: the `NS` it was declared under and
the `NR` that reversed it. The `NR` has no document of its own, so if it is not reachable from
the invoice it is not reachable at all. The panel lists both, Print is pointed at the refund —
what the document most recently became — and the sale is one press away.

## The RRA logo is a bordered placeholder

CIS §7.29 puts the authority's logo at the head of the receipt. The asset is owner-supplied and
has not arrived, so the box is where it goes — named as a plan deviation in the step report
rather than approximated with something that is not RRA's mark.
