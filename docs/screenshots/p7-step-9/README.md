# P7 step 9 — the five shots the Definition of Done names

1440x900, light and dark, full page, captured against the **e2e** stack by
`frontend/scripts/capture-p7-step9.ts` in a single pass on a reset database. The e2e overlay is
required, not optional: every receipt in these shots was signed by the `ebm-sandbox` service
over HTTP, and the blocked device is blocked because the authority was switched to `reject:884`
before the sale that stuck was posted.

```sh
make db-reset
export COMPOSE_FILE=docker-compose.yml:docker-compose.e2e.yml
docker compose up -d --wait db backend frontend ebm-sandbox worker
cd frontend
OUT=../docs/screenshots/p7-step-9 npx tsx scripts/capture-p7-step9.ts
```

`ONLY` re-captures a subset — navigation included, so a subset run does not walk a screen whose
fixture it is not setting up:

```sh
OUT=../docs/screenshots/p7-step-9 ONLY=4-daily-z npx tsx scripts/capture-p7-step9.ts
```

## The prices are the point

Every document in these shots is keyed at **1 499 excl. at 18 %**, which the ledger rounds to
whole francs (RWF has no decimals, rule 6) and the wire carries at two (1 768.82 inclusive,
decision 6). Three of them is **5 306** in the ledger and **5 306.46** on the paper. At
2 000 × 10 the two agree exactly, and the Z and the return would be photographs of reports with
nothing to reconcile.

## The five

| # | File | What is in it |
|---|---|---|
| 1 | `1-invoice-receipt-{light,dark}.png` | A fiscalized invoice **as it prints** — captured under `print` media, because the CIS receipt is `hidden print:block` and a screenshot of the screen shows the screen. The SDC block, the counter with its `NS` label, the internal data and the signature dashed every four characters, the QR, the MRC. |
| 2 | `2-queue-blocked-{light,dark}.png` | The queue with a device the authority refused: `Blocked`, the row's `884`, and the actions a person has. |
| 3 | `2-queue-recovered-{light,dark}.png` | The same screen after the authority came back and **Retry now** released it. A screenshot of a queue with nothing wrong proves the route compiles; the pair proves the recovery. |
| 4 | `3-vat-return-tie-{light,dark}.png` | The VAT return with **both** VAT accounts carrying a movement and both chips reading *Reconciled* — the tie with something to reconcile rather than 0 against 0. |
| 5 | `4-daily-z-{light,dark}.png` | A closed Z, with the counter window it owns beside the dates it prints (`Covers receipts 1–…`, revision `0026`). |
| 6 | `5-revaluation-preview-{light,dark}.png` | The revaluation preview: the open foreign-currency documents, their carrying and revalued base, and the difference — before anything is posted. |

Six files per theme rather than five, because the DoD's second item is a **state and its
recovery**: "the queue with a blocked device and its recovery" is two screens, and one of them
alone would be half the claim.
