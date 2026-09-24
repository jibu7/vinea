# P8 step 8 — the Bank account enquiry, Cashbooks, the Bank reconciliation report, and the bank lines on the FX report and the GL entry page

1440x900 (the reconciliation report at 1440x1400), light and dark, captured on a database reset
by `make db-reset` with the e2e overlay, by `frontend/scripts/capture-p8-enquiries-reports.ts`,
with the dev server warmed:

```sh
make db-reset
cd frontend
OUT=../docs/screenshots/p8-step-8 npx tsx scripts/capture-p8-enquiries-reports.ts
```

The script makes a bank account of its own, *I&M Bank Operating*, and posts the ledger the e2e
posts: a FRw 1,000,000 transfer in, deposits of 250,000 and 120,000, and a cheque of 80,000. It
imports the bank's statement of the three receipts (closing 1,370,000), matches them 1:1, and
locks `BRC-000001` at a zero difference, with the cheque as the one unpresented payment. Then it
posts a FRw 30,000 bank charge dated the reconciliation's own date, after the lock. For the FX
report it posts a USD 500.00 receipt on `1121` at 1,320, holds the month's two dated rates
(1,320 on the 1st, 1,350 at month end), and posts a bank-role revaluation at the month end.
Every screen here only reads, so the setup goes through the API.

| # | Screen | Files |
|---|---|---|
| 1 | Bank account enquiry: book balance FRw 1,260,000; last locked `BRC-000001` at statement balance 1,370,000; nothing open; 0 unmatched statement lines; 2 outstanding ledger lines, FRw −110,000; latest statement `BST-000001`. Every figure is a link | `8-1-bank-account-enquiry-{light,dark}.png` |
| 2 | Cashbooks, detail: opening 0, five lines with running balance, *Reconciled* reading `BRC-000001` on the three matched receipts and blank on the cheque and the late charge; totals, closing 1,260,000 and closing in RWF | `8-2-cashbooks-detail-reconciled-column-{light,dark}.png` |
| 3 | Cashbooks, summary: one row per bank and cash account. The new account: closing 1,260,000, last reconciled 23/09 at 1,370,000, 0 unmatched, 2 outstanding. `1121` in USD ($ 500.00) and in RWF (660,000) | `8-3-cashbooks-summary-{light,dark}.png` |
| 4 | Bank reconciliation report on the locked `BRC-000001`: *At lock* beside *Now*. Statement 1,370,000; less unpresented 80,000 at lock and 110,000 now; adjusted = cashbook 1,290,000 at lock and 1,260,000 now; difference 0 in both. *Outstanding items: 1* (the cheque) and *Posted after lock: 1* (the charge, *Dated inside BRC-000001*) | `8-4-reconciliation-report-outstanding-and-late-{light,dark}.png` |
| 5 | FX revaluation report on the bank-role run: `1121 · Bank Account USD` where a document line has its number and partner; open $ 500.00 in the account's currency; booking rate 1320 (derived: carrying 660,000 ÷ 500.00); carrying 660,000; revalued at 1350 to 675,000; difference 15,000; *Lines revalued: 1* | `8-5-fx-report-bank-line-{light,dark}.png` |
| 6 | GL entry page on the 250,000 deposit: the *Bank* column on the bank line reads *Locked in BRC-000001*, linked to the report. The contra line on 3400 has nothing | `8-6-gl-entry-bank-line-{light,dark}.png` |

**Looking at them found two defects**, both fixed before these were taken. Cashbooks' Description
column printed the entry's reference (`CHQ…`), the same token as the Reference column beside it,
because the kernel stamps a cashbook entry's bank line with its reference. It now reads the
entry's description where the line carries only the reference. The FX report's run picker also
showed the raw ISO date (`2026-09-30`) while the rest of the page used `30/09/2026`.
