# P8 step 7a — Bank statements and the reconciliation workspace

1440x900 (the three workspace shots at 1440x1500, which holds both panes), light and dark,
captured against the dev stack on a database reset by `make db-reset`, by
`frontend/scripts/capture-p8-transactions.ts`, with the dev server warmed first:

```sh
make db-reset
cd frontend
OUT=../docs/screenshots/p8-step-7 npx tsx scripts/capture-p8-transactions.ts
```

The script makes a bank account of its own in the generic format with an `ACCOUNT FEE` rule,
posts five cashbook entries on it, **generates the statement CSV from them** (dated today, no
committed sample), and drives the screens through their own buttons: Import, New
reconciliation, Auto-match, a manual match, both halves of Post from line, Lock. The `7a-`
prefix leaves the numbers free for step 7's second half (payment runs, FX revaluation's bank
lines) in the same folder.

| # | Screen | Files |
|---|---|---|
| 1 | Import's preview over a file whose third row is dated `DD/MM/YYYY`: the error by row and column, the four good rows, the derived balances, *Import statement* refused before the button | `7a-1-import-preview-error-{light,dark}.png` |
| 2 | The statement's detail: five lines, three matched — by amount and date, by reference, by hand over two ledger lines — and two unmatched; *Void* refused beside the button with the count | `7a-2-statement-detail-{light,dark}.png` |
| 3 | The workspace mid-match: one bank line and one of the two ledger lines it covers selected, *Selection is out by FRw 15,000* beside Match; the strip live at a difference of FRw 97,500 with both lock refusals listed | `7a-3-workspace-mid-match-{light,dark}.png` |
| 4 | *Post from line* on the monthly fee: the P3 cashbook grid, one row, prefilled by the rule (`6700 · Bank Charges`, "Monthly account fee"), the amount fixed at 2,500 | `7a-4-drawer-prefilled-by-rule-{light,dark}.png` |
| 5 | Lock refused: every line matched, the statement balance re-keyed 500 short, *The difference is FRw -500* before the disabled button | `7a-5-lock-refused-difference-{light,dark}.png` |
| 6 | Locked at zero: the stored figures (statement FRw 1,215,500, ledger FRw 1,145,500, outstanding FRw -70,000 — the one unpresented cheque), every match *Locked in BRC-000001*, Unmatch disabled, Reopen offered | `7a-6-locked-at-zero-{light,dark}.png` |
| 7 | Bank accounts: the currency lock as a neutral hint under the field, not the red error slot | `7a-7-bank-accounts-currency-hint-{light,dark}.png` |

Step 6's shot 3 (`../p8-step-6/3-bank-account-currency-locked-*.png`) was re-taken for the same
hint. The Bank accounts list blurred behind its drawer carries this script's own account as a
fourth row, because it was captured after this folder's run.

**Looking at them found two defects**, both fixed before these were taken: side by side at this
width the panes broke amounts over two lines (`FRw` / `1,000,000`), so they stack below 2xl;
and the *Locked in BRC-000001* chip broke the number itself (`BRC-` / `000001`).

## P8 step 7b — Payment runs, the AP document's "Paid in run", the 1:3 match, FX bank lines

Same size and themes (the New shot at 1440x1700, which holds the grid and the preview; the run's
detail at 1440x1100; the workspace at 1440x1500), captured on a database reset by
`make db-reset`, by `frontend/scripts/capture-p8-payment-runs.ts`, with the dev server warmed:

```sh
make db-reset
cd frontend
OUT=../docs/screenshots/p8-step-7 npx tsx scripts/capture-p8-payment-runs.ts
```

The script makes a bank account of its own with FRw 1,000,000 in it, terms of 2 % 10 days /
net 30, and three suppliers — *Nyanza Timber Ltd* on those terms, *Huye Cork Supplies* with no
bank details, *Rubavu Glassworks* with a FRw 20,000 payment on account — each with one invoice
posted today (100,000, 50,000 and 236,000). It drives the screens through their own buttons:
the selection, Preview, Post, Import. Then it posts a USD 500.00 receipt on `1121` at 1,320 and
holds the month's two dated rates (1,320 on the 1st, 1,350 at month end), as the P7 specs do.

| # | Screen | Files |
|---|---|---|
| 1 | New payment run: the three invoices selected, `discount_available` FRw 2,000 on the 2/10 invoice with *Take* ticked; the preview — *Bank details missing* on Huye Cork Supplies with what that means, *Open credits not netted: PMT-n* on Rubavu Glassworks, FRw 2,000 taken, run total FRw 384,000, Post offered | `7b-1-new-run-preview-discount-and-warnings-{light,dark}.png` |
| 2 | The run's page: three lines with their invoice, `PMT-` and `ALC-` links, settled / discount / paid (100,000 − 2,000 = 98,000 on Nyanza), paid from the bank FRw 384,000; *Instruction file*; the three remittance advices *Ready* with their PDFs | `7b-2-run-detail-instruction-file-{light,dark}.png` |
| 3 | The AP document the run posted for Nyanza Timber: *Paid in run PYR-n* linking to the run, the allocation (98,000 with the 2,000 discount), and Reverse disabled with `payment_run_member` said beside it | `7b-3-ap-document-paid-in-run-{light,dark}.png` |
| 4 | The workspace after the one-line statement was imported: *BULK PAYMENT PYR-n* matched by the payment run rule to three ledger lines (*Ledger lines: 3*, the three `PMT-`s named), each `PMT-` reading *Matched to BULK PAYMENT PYR-n* — matched by the auto-match Import now chains | `7b-4-workspace-run-matched-one-to-three-{light,dark}.png` |
| 5 | FX revaluation, *Bank and cash accounts*: `1121 · Bank Account USD` where the document and partner sit, open **$ 500.00** in the account's currency, carrying 660,000, revalued at 1,350 to 675,000, difference 15,000 | `7b-5-fx-preview-bank-line-{light,dark}.png` |

**Looking at them found two defects**, both fixed before these were taken: the FX screen's
*Side* picker cut "Bank and cash accounts" to "Bank and cash a…", and its rates read
`1350.0000000000`.
