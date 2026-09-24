# P8 — Banking: the phase report

**Status: complete.** Everything the phase prompt specifies is built, tested and reachable from a
screen, and the Definition of Done's own sentence is met on the owner's statements rather than on
fixtures. A **real bank CSV** is imported through its committed mapping, previewed before
anything is written, and the second import skips what the first already holds. The same file
twice is refused. A reconciliation reaches a **zero difference and locks**, and it cannot lock at
anything else. Precondition (d) was held: six exports, four layouts, two banks. See *What the real
exports changed*.

What the phase claims is narrower than its screen count, and worth stating plainly: **the ledger
is the only truth, and a statement is evidence about it.** Banking stores no balance, writes no
journal line of its own and adds no event. A reconciliation is the dated proof that the bank's
record and the ledger agree, and once locked it never changes. Matching, posting from a line,
payment runs, the revaluation and the reports all rest on that.

---

## What landed, step by step

| Step | PR | What it built |
|---|---|---|
| 1 | #63 | Masters, schema and the parser. Migration `0027_p8_banking` builds every table the phase writes, `VN012` (the one-sided currency rule) and `VN013` (statement-line immutability), the partner bank details, `fx_revaluation_lines` made able to carry a bank line, the three `gl_settings` keys, `1130`, the three `DocType`s and the six permissions. `tests/test_p8_backfill.py` provisions a tenant at `0026` with posted bank lines and a USD settlement on the RWF account. `app/banking/accounts.py` with `ensure_row` and the engine half of the currency rule; `formats.py` (the mapping, the `generic` preset, the parser, the fingerprint); `statements.py` (preview, import, dedup, void). |
| — | #64 | Issue #54, the audit index, as its own PR between steps: the index discriminates the entity, so a history read stops scanning the tenant. |
| 2 | #65 · **gate** | Matching (the three auto rules, manual n:m under the balance rule, tick, unmatch), post-from-statement with the match in the posting's transaction, `bank_rules` as prefill, `reconciliation.py` (the figures, open, lock, reopen, late lines by `high_water_line_id`), `assert_bank_invariants` clauses 1–7 and 9 each proven sensitive, `test_boundary.py`, and the property machine with its census as a gate. |
| 3 | #66 | Payment runs: preview, post through `post_document()` + `allocate()`, the instruction CSV, the `remittance_pdf` job, reversal with the fallible leg first, `payment_run_member` on P4's document path, clause 8. |
| — | #67 | The P4 defect step 3's machine found: `allocate()` accepted an allocation dated before one of its documents. Fixed off `main` on the owner's ruling (`allocation_before_document`). |
| 4 | #68 | Bank revaluation as the `bank` and `all` scopes of the P7 run, with the other side to `1130` and never to the bank account; the scope-overlap refusal; the Cashbooks and reconciliation-report queries with both ties; the Bank account enquiry. |
| 5 | #69 · **gate** | The listings the workspace needs, and the nineteen-row acceptance tape as a backend test with every literal worked by hand: 236 expected-vs-actual pairs, no mismatch. It found clause 4 wrong over two successive locks. |
| 6 | #70 | Maintenance UI: **Bank accounts** (C.1.13) with the format editor and *Test with a file*, the rules, the Suppliers screen's *Bank details*, the Defaults screen's Banking block, and Chart of accounts' notice. |
| 7a | #71 | **Bank statements** and **Bank reconciliation** with the workspace (C.1.14). |
| 7b | #72 | **Payment runs** (C.1.15), "Paid in run" on the AP document, the FX revaluation screen's `bank` / `all` roles, and auto-match chained to Import. |
| 8 | #73 | The **Bank account enquiry**, the owner's **Cashbooks** and **Bank reconciliation** reports, the FX report's bank lines and the GL entry page's. |
| — | #74 | A P7 accessibility fix found by step 8's axe sweep: the VAT return's settlement panel as a valid definition list. |
| — | #75 | Precondition (d), part one: the BPR and BK exports, their mappings, and `empty_description` and `zero_is_empty`. |
| — | #76 → #77 | Part two: the KCB export and `empty_amount` (migration `0029`). #76 was merged into its base branch after #75 had already reached `main`, so its five commits were not on `main`. #77 landed them there. |
| — | #78 | A cell that is not a number is refused, never read as empty. That is the boundary of `empty_amount: skip`: an unreadable cell is not an empty one. |
| 9 | this PR · **gate** | The tape through the screens on its own company, in its own CI group; the owner's real exports imported by it; the sensitivity pass over twenty-two breaks; the five screenshots; this report. |

---

## Decisions worth review

**1. The payment run is one `PMT-` per supplier, and the clearing account was rejected** (decision
7, step 3). A run posts an ordinary P4 settlement and allocation per supplier in one transaction.
The "single bank line" is the **statement's**: the bank shows one debit carrying the run's
number, and the `payment_run` rule matches that one line to the run's N ledger lines. A clearing
account would buy a literal single ledger line at the price of an account, a control type and a
kernel event, only to restate a fact the bank already states. And a bank that shows one line per
beneficiary would then need the reverse mapping.

**2. The currency rule is one-sided** (decision 2). A foreign-currency bank account holds only its
currency (`bank_account_currency_mismatch` in the engine, `VN012` in the database, each proven
sensitive with the other disabled). A **base-currency** account may carry a foreign line, because
that is how a USD receipt reaches a Rwandan RWF account. The statement sees such a line at its
base amount: `reconciled_amount()` is `amount` on a foreign account and `base_amount` on a base
one, and that one function is the only definition. Tape row 5's RCT-4 (USD 200.00 at the bank's
1 300 → 260 000) is the case worked through.

**3. Reopen only the latest** (decision 5). A locked reconciliation is a snapshot. Reopening an
earlier one would restate a figure every later lock was built on, so only the account's latest
may be reopened (`reconciliation_not_latest`), and its matches stand while it is open.

**4. Bank revaluation is a scope of the P7 run, not a second run** (decision 8). The accountant
presses one revaluation at month end. `bank` and `all` join `ar`, `ap` and `both`;
`fx_revaluation_exists` became a scope intersection; and a bank line's other side is `1130`,
**never the bank account**. A base-only line on a bank account would be one the statement can
never show and the reconciliation would carry for ever. The balance sheet reads `1121 + 1130`.

**5. `control_account_direct_posting` for decision 4 and tape row 18** (step 2, approvals row). The
frozen prompt named `control_account_manual_posting`, which is a `ManualJournal`-only branch. What
a rule's drawer posts is a `CashbookEntry`, so the refusal that actually fires is P4's
`control_account_modules` registry, which is `VN007` in the database as well. Corrected in the
prompt at the gate, before the tape was written.

**6. Clause 4's membership** (step 5; the step-5 gate corrected the prompt). A locked
reconciliation's outstanding is recomputed over the lines in no match assigned to it **or to any
earlier locked reconciliation on the account**. The narrower reading counted August's cleared
float as outstanding again in September, and made `BRC-000002` reproduce 930 000 against a stored
−70 000. The tape found it: every step-2 test had a single lock. It is proven sensitive in both
directions (too narrow, and too wide with the date bound dropped).

**7. Decision 6's two ties** (step 5; the step-5 gate corrected the prompt). The base closing ties
to the trial balance for every account at any date (`base_closing_ties`). A foreign account's
closing *in its own currency* ties to `period_balances` at a period end (`currency_closing_ties`),
and the check declines rather than fails on any other date. There are two ties because the trial
balance cannot answer in USD and `period_balances` has no opinion about a Tuesday.

**8. Idempotency: no key on run reverse, one on reopen** (steps 3 and 7a). The step-3 gate ruled
no column for either, because both are refused on a second press by state
(`payment_run_already_reversed`; reopen is refused as not latest or already open). Step 7a then
gave reopen an `Idempotency-Key` on the reconciliation row's existing column, since decision 11
names it with open and lock, and a replay returns the reopened row. Reverse stays keyless and
refused by state.

**9. A settlement discount is taken only on full settlement of a line** (step 3, decision 7's
reading). A partial payment inside the discount window takes no discount: a discount buys
prompt settlement of the whole.

**10. `payment_run_lines.amount` is the cash paid** (step 7b). Settled = `amount +
discount_amount`. The first cut of the run page read it as settled. The page and the schema's
docstring now say which is which.

**11. Auto-match on import is the screen's chain, not the import transaction** (step 7b). The
import service writes lines and nothing else, and the screen runs the account's auto-match after
the confirm, which is the same endpoint the workspace's *Auto-match* presses. An import through
the API alone does not match. If that should change, it is a separate decision.

**12. Cashbooks' Description reads the entry's where the line carries only the reference** (step
8). The kernel stamps the reference on a cashbook's bank line (the statement matcher relies on
it), so the column would otherwise print the reference twice. A line whose description is its
own keeps it.

**13. What the machine changed.**
- Step 2 moved three of decision 12's floors to targeted properties (`match_unbalanced`,
  `reconciliation_locked`, `statement_lines_unmatched`), each with its precondition built rather
  than hoped for. The third was improved and not just rescued: a zero difference with an
  unexplained line is exactly what that refusal exists for, and the machine never reached it.
- The `run reversal: succeeded` reach floor was withdrawn at step 5 after five deep passes read
  0, 0, 7, 8 and 2. It is a two-deep conjunction over eighteen operations: reachable, but not
  reliably. `test_reversing_a_payment_run_holds_every_invariant` constructs it every time and
  asserts all three suites between the reversal's legs.
- Step 3's machine found a **P4 defect**, `allocate()` accepting an allocation dated before one
  of its documents. It was fixed on its own as #67.

**14. The statement preview is capped at twenty rows**, by design (decision 3). It is there to show
the mapping reads the file. The counts, the derived opening and closing, and **every** parse error
by row cover the whole file, and the import result and the statement's detail carry every line.

**15. What step 9's sensitivity pass found.** Twenty of the twenty-two breaks each failed a test
named for the guard. Dropping either member-uniqueness constraint (`bank_match_statement_lines`,
`bank_match_journal_lines`) failed **nothing**: all 302 banking tests stayed green, because
`_refuse_already_matched` refuses first and every test goes through the service. The database
half of "a line is in at most one match" had no test. Two now write the second member row
directly and expect Postgres to refuse it, and each fails when its constraint is dropped
(`docs/p8-step-9-report.md` §B).

---

## Plan deviations, with reasons

1. **The P8 tape is in a CI group of its own.** The step-9 brief put it in its own group "like
   p6/p7's tapes". In fact those two ride the `rest` shards: the group named `tape` holds only P4's
   `ar-ap-acceptance.spec.ts`. The P8 tape is given its own group, `p8-tape`, as asked. It is
   serial, signs up a company and imports several hundred statement lines, and naming it keeps the
   three shards a split of the rest. `ci-e2e-groups.test.ts` still proves the partition.
2. **No `Idempotency-Key` on payment-run reverse** — decision 11 lists it. See *Decisions* 8.
3. **Settlement discount on full settlement only** — see *Decisions* 9.
4. **The prompt's decision 4 / row 18 refusal name, and decision 5 clause 4 and decision 6's tie,
   were corrected at the step-2 and step-5 gates** — see *Decisions* 5–7.
5. **The five screenshots are taken by the tape**, not by a capture script. `P8_CAPTURE_DIR`
   turns on one `capture()` call at each of the five states, and CI never sets it. A script would
   have had to rebuild the same nineteen rows to reach them, and would drift from the tape it
   photographs.
6. **The step-5 approvals row was missing from `docs/approvals.md`.** The prompt refers to it
   twice (decision 5 clause 4, decision 6) and the step-5 report records the two corrections as
   made "on the owner's direction at this gate", but `main` carried rows for P8 steps 2 and 3 only.
   This step adds the row, and it says what is known rather than quoting words that were not
   captured: the P6 precedent. **The cell is the owner's to fill.**

---

## What the real exports changed

**Precondition (d) held.** The owner supplied six statements from two Rwandan banks, as PDFs,
transcribed to CSV row for row and anonymised by the owner (`docs/banking/samples/`):

| file | bank · layout | lines | mapping |
|---|---|---:|---|
| `bpr-2025-05.csv`, `bpr-2025-06.csv`, `bpr-2026-07.csv` | BPR, the 2025 e-statement | 32, 45, 2 | `bpr.format.json` |
| `bpr-2022-09.csv` | BPR, the 2022 e-statement (an overdraft) | 21 | `bpr-2022.format.json` |
| `bk-2019-10.csv` | Bank of Kigali movement history | 249 | `bk.format.json` |
| `kcb-2023-12.csv` | KCB online statement | 281 + the B/FWD row | `kcb.format.json` |

**Three parser options, each forced by a file that imported nothing without it.** Each is proven
sensitive by switching it off, and the committed files then give the README's error counts
exactly:

- `empty_description: reference` — every BPR-2025 `CHG…` fee line has a reference and no
  description. Off: 16, 22 and 1 errors across the three BPR-2025 files.
- `zero_is_empty` — BPR-2022 writes `0.00` in the column a row does not use. Off: 21 errors.
- `empty_amount: skip` — KCB opens with `BALANCE B/FWD`, a row with a balance and no amount. Off:
  1 error, and the whole file refused. With it on, the row is skipped and counted apart
  (`lines_skipped_no_amount`, **migration `0029`**), and the opening balance is derived from the
  first line: 0.00, which is what the B/FWD row said. #78 then drew the boundary: an **unreadable**
  cell is refused, never read as empty.

**No `external_id` for BPR.** `bpr-2025-06.csv` rows 28–29 carry one `FT…` reference on two lines
(RWF 200 and 20,000, same day, same description). A bank reference is not a transaction id, so
duplicates are found by fingerprint, which tells the two apart by amount.

**KCB is the BPR-2025 account on the KCB system** after KCB took BPR over. It is a fourth layout
of one account, not a second customer.

**In the tape.** `frontend/e2e/p8-cycle-tape.spec.ts` imports each, on an account of its own under
its committed mapping (set through the API), through the Import dialog, and reads the result and
the statement's detail:

| file | read off the preview and the result |
|---|---|
| `bpr-2025-06.csv` | 45 lines · 45 new · 0 already held; closing FRw 4,274,862; "45 new, 0 skipped" |
| `bpr-2025-05.csv` (same account, after June) | opening FRw 1,110,776; "32 new, 0 skipped"; detail 32 lines, closing FRw 2,408,456 |
| `kcb-2023-12.csv` | read cleanly; 281 lines · 281 new; "Skipped for no amount: 1"; opening FRw 0, closing FRw 4,867 |
| `bk-2019-10.csv` | 249 lines · 249 new; opening 1,600 and closing 2,659 keyed in the preview; "249 new, 0 skipped"; detail closing FRw 2,659 |

The screen writes the brief's "281 new, 0 skipped, 1 skipped for no amount" as two phrases, "281
new, 0 skipped, 0 matched" and "Skipped for no amount: 1". The tape reads both. RWF has no minor
unit, so the brief's "derived opening 0.00" renders as FRw 0.

---

## Owner items

1. **The step-5 approvals row** (see *Plan deviations* 6): the owner's words, if they were given.
2. **This report's own row.** It is opened at submission, not filled in on the author's behalf.
3. **A bank-specific instruction-file layout** (`bank_accounts.instruction_format`) is out of scope
   until a bank's layout is supplied. The generic CSV stands.
4. **camt.053 / MT940** stay out of scope. The parser is one interface, so the next format is a
   parser and not a redesign.

---

## Post-P8 queue

1. **An unreadable amount cell produces two errors**, `not a number` and then `no debit and no
   credit`. `_read_amount` should stop at the first: the second restates the first and sends the
   reader to the mapping instead of the cell.
2. **`12x` reads as 12.** `_AMOUNT_NOISE` strips letters anywhere, to tolerate `RWF 1,200`. The
   rule should allow a currency code only at the start or the end of the cell.
3. **`-n 8` runs Postgres out of lock-table space.** Raise `max_locks_per_transaction` in the
   test compose, and migrate one template database that each worker clones instead of running 29
   migrations per worker.
4. **e2e against `next build && next start` instead of `next dev`.** That ends the cold-compile
   warm-ups, and the `document-title` flake on `/gl/reports/chart-of-accounts` (seen once, dark
   pass, a P3 screen; not diagnosed).
5. *Owner items* 3 and 4 above (a bank-specific instruction layout, and camt.053 / MT940) stay
   where they are.
