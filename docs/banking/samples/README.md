# Real bank exports — precondition (d)

Five statements from two Rwandan banks in three layouts, supplied by the owner as the banks'
PDF statements and transcribed to CSV row for row. Every date, amount and running balance is the
bank's. Where a file carries a running balance it ties from opening to closing; the BK file has
no balance column, so its opening and closing are keyed at import.

| file | bank · layout | period | lines | opening | closing | mapping |
|---|---|---|---:|---:|---:|---|
| `bpr-2025-05.csv` | BPR (2025 e-statement) | 1 May – 1 Jun 2025 | 32 | 1,110,776.00 | 2,408,456.00 | `bpr.format.json` |
| `bpr-2025-06.csv` | BPR (2025 e-statement) | 1 Jun – 1 Jul 2025 | 45 | 2,408,456.00 | 4,274,862.00 | `bpr.format.json` |
| `bpr-2026-07.csv` | BPR (2025 e-statement) | 1 Jul – 1 Aug 2026 | 2 | 230,032.00 | 30,012.00 | `bpr.format.json` |
| `bpr-2022-09.csv` | BPR (2022 e-statement) | 1 Jul – 28 Sep 2022 | 21 | −181,940,953.28 | 238,769,800.00 | `bpr-2022.format.json` |
| `bk-2019-10.csv` | Bank of Kigali movement history | 1 Oct 2018 – 6 Oct 2019 | 249 | 1,600 (keyed) | 2,659 (keyed) | `bk.format.json` |

The three BPR-2025 files are one RWF current account, consecutive months plus a later one. The
BPR-2022 file is a different RWF account in overdraft (negative opening balance). The BK file is
an RWF current account over a year: 234 debits, 15 credits, Σ = +1,059.

## Anonymisation

Account holders, account numbers and the banks' page references are not in the files. Inside
the rows: mobile-money ids, phone numbers and ATM authorisation codes are sequential placeholders
(one per distinct real value, so a value that recurs still recurs); named counterparties are
`ACME TRADING LTD`, `BETA COMPANY`, `INSURER ONE`, `CUSTOMER ONE/TWO/THREE`; an account number
the 2022 layout prints as a reference is `403400000000000-`; the banks' `FT…`/`TT…`/`CHG…`
references keep their prefix and date part and take a hash-derived tail of the same length, and
a reference the bank used on several lines is still used on those lines. Loan references
(`LD…`, `PDLD…`), fee codes (`SMELCY.`), BK record and event numbers, and ATM ids are unchanged.
The 2022 PDF renders every balance, and one debit, with a stray `.0`/`.00` after the cents
(`-184,567,881.28.0`, `316,148.72.00`); that is the PDF renderer's, not the bank's ledger, and
the CSV carries the clean decimals.

## What the exports taught the parser

* **BPR 2025 — the fee lines have no description.** Every `CHG…` line (the RWF 20 transfer
  fee) carries a reference and an amount and nothing in the Description column. The generic
  preset refuses an empty description, so `bpr.format.json` says `empty_description:
  reference`. As the parser stood, the three files gave 16, 22 and 1 errors and imported
  nothing.
* **BPR 2025 — the bank's reference is not a transaction id.** `bpr-2025-06.csv` rows 28–29
  carry one `FT…` reference on two lines (RWF 200 and 20,000, same day, same description). So
  `external_id_column` is not mapped for BPR: duplicates are detected by fingerprint, which
  tells the two apart by amount.
* **BPR 2022 — the unused column carries `0.00`.** Every row fills both Debit and Credit, one
  of them with `0.00`. The generic parser reads an explicit zero as filled and refuses the row
  ("both the debit and the credit column carry an amount"): 21 errors, nothing imported. So
  `bpr-2022.format.json` says `zero_is_empty: true`. Its dates are `%d %b %y` (`30 JUL 22`).
  Four identical `SME MANAGEMENT FEE` lines on one day under one reference are the
  identical-tuple case the fingerprint's occurrence index exists for. The loan repayment on
  23 Sep is nine lines under two `PDLD…` references — a reference is a batch, not a line.
* **BK — no balance column.** `bk.format.json` maps no `balance_column`; opening and closing
  are keyed at import. Its dates are ISO; Record (the reference) is empty on 34 rows, and Event
  is shared between a withdrawal and its charge, so neither is an `external_id`. It parses on
  the step-1 parser unchanged: 249 lines, 0 errors.
