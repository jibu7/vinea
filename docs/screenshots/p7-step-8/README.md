# P7 step 8 — the Tax enquiries and reports, and the FX report

1440x900, light and dark, full page, captured against the **e2e** stack by
`frontend/scripts/capture-p7-enquiries-reports.ts` in a single pass on a reset database. The e2e
overlay is required, not optional: every receipt in these shots was signed by the `ebm-sandbox`
service over HTTP, and the one document that is in the ledger and not on the receipts is there
because the authority was switched **down** before it was posted.

```sh
make db-reset
export COMPOSE_FILE=docker-compose.yml:docker-compose.e2e.yml
docker compose up -d --wait db backend frontend ebm-sandbox worker
cd frontend
OUT=../docs/screenshots/p7-step-8 npx tsx scripts/capture-p7-enquiries-reports.ts
```

`ONLY` re-captures a subset — navigation included, so a subset run does not walk a screen whose
fixture it is not setting up:

```sh
OUT=../docs/screenshots/p7-step-8 ONLY=6-receipts-listing-tie npx tsx scripts/capture-p7-enquiries-reports.ts
```

## The prices are the point

Every document in these shots is keyed at **1 499 excl. at 18 %**, which the ledger rounds to
whole francs (RWF has no decimals, rule 6) and the wire carries at two (1 768.82 inclusive,
decision 6). Three of them is **5 306** in the ledger and **5 306.46** on the paper. At
2 000 × 10 the two agree exactly and shots 5 and 6 would be photographs of two reports with
nothing to report.

| # | Screen | What it has rows of | Files |
|---|---|---|---|
| 1 | **Fiscal receipts** (Enquiries → Tax) | the counters as the paper prints them, `n/m NS` and `n/m NR`, with the document and the journal entry beside each | `1-receipts-enquiry-{light,dark}.png` |
| 2 | **Fiscal queue history** (Enquiries → Tax) | one document's rows in send order, and the action log open under them | `2-queue-history-{light,dark}.png` |
| 3 | **VAT return** (Reports → Tax) | the sections as filed, the tie with every untagged movement named, and both annex buttons | `3-vat-return-report-{light,dark}.png` |
| 4 | **Daily fiscal report — X**, with a sale still in flight | the rows in flight **by status** (`3 Queued`), the text saying each of those sales appears on the Z of the day the authority signs it, `Queued when taken 3`, and Close day still pressable — decision 11 on the screen | `4-daily-x-close-day-{light,dark}.png` |
| 5 | **Daily fiscal report — the Z tab** | the closed day with its §19.1 figures: declared, posted beside it, the residue named, the classes, and the payment methods with sales and refunds never netted | `5-daily-z-figures-{light,dark}.png` |
| 6 | **Fiscal receipts listing — the tie** (Reports → Tax) | declared against the ledger, the difference, and the one document that is in the ledger and not signed with `queued` against it | `6-receipts-listing-tie-{light,dark}.png` |
| 7 | **FX revaluation** (Reports → General Ledger) | a run's lines per open foreign-currency document, with the run and its next-day mirror named | `7-fx-revaluation-report-{light,dark}.png` |

Shots 4 and 5 are the same screen on its two tabs, and the seed closes the day between them so
they are not the same picture: the Z holds the three signed documents, and the X that follows it
holds nothing but the sale the authority could not be reached for. A first pass captured both
full-page from the X tab and produced the same image **to the byte** — which is the sort of
thing a screenshot pass exists to catch, and only catches if somebody looks.

**The seed waits past the close's second**, and the reason is on the line. A Z's bounds are
floored to a second and its range is `from_at <` … `<= to_at`, so a receipt issued in the very
second a Z was taken falls inside a range whose figures are already frozen — and the next range
opens exclusively at that instant, so neither Z counts it. This script does a day's trading in
under a second, which is the only way to reach that boundary. Without the wait, shot 4 came out a
warning over a column of zeros because the sale below it had been swallowed by the Z above.

Every shot was opened and read before it was committed.
