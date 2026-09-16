# P6 step 8 — Order entry enquiries and reports

1440x900, light and dark, captured against the dev stack by
`frontend/scripts/capture-p6-enquiries.ts` in a single pass on a reset database. Re-capture
with:

```sh
cd frontend
OUT=../docs/screenshots/p6-step-8 npx tsx scripts/capture-p6-enquiries.ts
```

`ONLY` re-captures a subset, so changing one screen does not rewrite the other twenty-five
files:

```sh
OUT=../docs/screenshots/p6-step-8 ONLY=5-goods-received-report npx tsx scripts/capture-p6-enquiries.ts
```

Unlike the step-7 script, this one's **documents are idempotent as well as its catalogue**: it
finds the consignment before it posts one. A script that posted a second order on every run
would leave the shots re-captured with `ONLY` showing two consignments and the rest showing
one, and every figure below would then be right for half the images and wrong for the other
half.

**One consignment, and the figures follow it from the first shot to the last.** 120 bottles
ordered from Kivu Vintners with a haulage line beside them; 80 received at 1 000 on DN-51107;
the forwarder's 8 000 booked to the clearing account and then landed on those 80 by quantity;
100 promised to the Hôtel des Mille Collines; 50 of them invoiced. So:

| Figure | Where it reads | Value |
|---|---|---|
| Still to come from the supplier | Purchase orders report | 40 EA |
| Still owed to the customer | Sales orders report | 50 EA |
| What the shelf cannot cover | Sales order enquiry, `Backordered` | 20 EA |
| Sitting in the accrual | Goods received report | FRw 80,000 |
| Account 2350 | Trial balance | FRw 80,000 |
| Landed on the bottles | Landed cost report | FRw 8,000 |
| Account 1370 after the allocation | Trial balance | zero |

The two 80 000s are the point of shots 5 and 6 standing next to each other: decision 5 makes
the GRN accrual's balance Σ (received − relieved) over every receipt, and this is where an
operator sees that hold without running a test. `p6-enquiries-reports.spec.ts` asserts the same
pair as **rendered strings** — two figures equal as decimals and different as text are still a
report nobody can reconcile.

1370 reading zero is the other invariant in the same frame: the freight was booked to the
clearing account and then allocated out of it, and clearing == booked − allocated.

| # | Screen | Files |
|---|---|---|
| 1 | Sales order enquiry: ordered, invoiced, remaining, backordered, and both entries of the invoice | `1-sales-order-enquiry-{light,dark}.png` |
| 2 | Purchase order enquiry: the receipt and the haulage invoice, side by side | `2-purchase-order-enquiry-{light,dark}.png` |
| 3 | Sales orders report, outstanding only, subtotalled by unit and by currency | `3-sales-orders-report-{light,dark,print}.png` |
| 4 | Purchase orders report — the haulage line already gone, received by its invoice | `4-purchase-orders-report-{light,dark,print}.png` |
| 5 | Goods received report, with the unmatched total and the tie it makes | `5-goods-received-report-{light,dark,print}.png` |
| 6 | The other half of that tie: 2350 on the trial balance, and 1370 at zero | `6-trial-balance-accrual-{light,dark}.png` |
| 7 | Landed cost report, per receipt line, saying where each share went | `7-landed-cost-report-{light,dark,print}.png` |
| 8 | Item enquiry: committed, on order, signed available, and the totals row | `8-item-enquiry-committed-{light,dark}.png` |
| 9 | Item enquiry on a **kit** — which says it has no position rather than showing zero | `9-item-enquiry-kit-{light,dark}.png` |
| 10 | A companion `STK-` entry, and the refusal that sends its reversal to the invoice | `10-companion-entry-drill-{light,dark}.png` |
| 11 | That invoice, naming **both** the entries it posted | `11-document-both-entries-{light,dark}.png` |

## The print previews

`-print.png` is the report as it leaves the printer, captured through Chromium's print media
emulation — the same mechanism `scripts/e2e-print-preview.ts` uses for the P3 reports, and what
the `@media print` layout in `ReportPage` exists for: the filter bar, the sidebar and the
buttons drop away, and a masthead carrying the company, the report name and the range it was
run for takes their place.

There is **no dark variant**: paper is white, and a dark printout is not a thing that exists.

The step-8 capture also found that the shell's "your email address is not verified" banner was
not `print:hidden` and was landing above the company name on every report anyone printed, in
this phase and the three before it. Fixed in the same change.

## What these screenshots cannot show

The journal behind any of it. The accrual tie is visible here as two figures on two screens,
and *why* those two figures are the same — that a receipt credits 2350, that a matched invoice
relieves it at the frozen value, that a landed cost debits stock and credits clearing — is the
acceptance tape's and `assert_order_invariants`' to prove, after every posting rather than at
one moment on one screen.
