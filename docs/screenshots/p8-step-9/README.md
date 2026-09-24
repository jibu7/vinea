# P8 step 9 — the five shots the Definition of Done names

1440x900 viewport, full page, light and dark. They are taken by `frontend/e2e/p8-cycle-tape.spec.ts`
itself, on its own signed-up company, when that state is on the screen. A separate capture script
would have to build the same nineteen rows again to reach them. Capture is off unless
`P8_CAPTURE_DIR` is set, so CI never writes a file:

```sh
make db-reset          # with COMPOSE_FILE=docker-compose.yml:docker-compose.e2e.yml
cd frontend
P8_CAPTURE_DIR=../docs/screenshots/p8-step-9 npx playwright test e2e/p8-cycle-tape.spec.ts
```

The four shots whose state lives in the URL are taken after a reload, so no success toast covers
the rows it announces. The revaluation preview's state is held by the screen, not the URL. It is
taken as it stands, before Post, when there is no toast yet.

| file | the state |
|---|---|
| `9-1-workspace-locked-at-zero` | `BRC-000002` locked. Statement 1,090,500, ledger 1,020,500, outstanding −70,000 (`PMT-000004`, the one line still *Outstanding*), difference 0. Six statement lines, each *Locked in BRC-000002*. `CB-000001` reads *Locked in BRC-000001*: August's paper-mode tick. |
| `9-2-reconciliation-report-outstanding-and-late` | The report on `BRC-000002` after the backdated cheque. *At lock* and *Now* side by side: the adjusted bank balance is 1,020,500 against 1,000,500, and the difference is 0 in both columns. One outstanding item (`PMT-000004`, −70,000). *Posted after lock: 1*: `PMT-000005`, −20,000, dated inside `BRC-000002`. |
| `9-3-cashbooks-reconciled-column` | 1120 for September. Opening 1,000,000, receipts 477,000, payments 476,500, closing 1,000,500, which the tape also reads off the Trial balance screen. The Reconciled column shows `BRC-000002` on the eight lines that lock proved, and *Matched* on the two cheques whose `BRC-4` was reopened. |
| `9-4-payment-run-instruction-file` | `PYR-000001`: three suppliers, S2's 2,000 discount, 384,000 from the bank, and the three `PMT-`/`ALC-` links. The *Instruction file* button and the three remittance advices, all *Ready*. The tape downloads the file, reads it (a header and three rows; S3's bank and account-number fields are empty) and reads S2's advice through `pdftotext`. |
| `9-5-revaluation-preview-bank-line` | 30 Sep, side *Customers, suppliers and bank*. The bank line for `1121` shows USD 495.00, carried at 653,400, revalued at 1,350 to 668,250: +14,850. `SIN-000004`'s line shows −3,000. Lines: 2. |

The company name in the header carries the run's suffix: the tape signs up a company of its own
on every run.
