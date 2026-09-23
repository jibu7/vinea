# P8 step 8 — the Bank account enquiry, the Cashbooks and Bank reconciliation reports, and the bank lines on the FX report and the GL entry page

Branched from `main` at `d88213e` (the merge of PR #72, step 7b). **Not a gate.**

The rule-14 register has no P8 line to clear. Every endpoint this step adds is a GET, so the
register has nothing to say about it.

## What landed

The twelve PNGs are condensed to one line below, and this report is the 40th file.

```
$ git diff --stat main..HEAD
 backend/app/api/v1/banking.py                      |  24 +
 backend/app/banking/matching.py                    |  54 ++-
 backend/app/banking/reports.py                     |  25 +-
 backend/app/schemas/banking.py                     |  13 +
 backend/tests/banking/test_api.py                  |  76 ++++
 backend/tests/banking/test_reports.py              |  30 ++
 docs/Vinea_ERP_Master_Plan_v5.md                   |  13 +-
 docs/screenshots/p8-step-8/*.png                   | 12 files (new)
 docs/screenshots/p8-step-8/README.md               |  35 ++
 frontend/e2e/p8-enquiries-reports.spec.ts          | 498 +++++++++++++++++++++
 frontend/scripts/capture-p8-enquiries-reports.ts   | 257 +++++++++++
 .../app/(shell)/gl/enquiries/bank-account/page.tsx |  17 +
 frontend/src/app/(shell)/gl/entries/[id]/page.tsx  |  16 +
 .../gl/reports/bank-reconciliation/page.tsx        |  17 +
 .../src/app/(shell)/gl/reports/cashbooks/page.tsx  |  18 +
 .../design/components/appendix-c-order.test.tsx    |  25 +-
 frontend/src/design/nav-tree.ts                    |  22 +-
 frontend/src/features/banking/account-picker.tsx   |  25 ++
 .../banking/bank-account-enquiry-screen.tsx        | 229 ++++++++++
 frontend/src/features/banking/cashbooks-report.tsx | 470 +++++++++++++++++++
 frontend/src/features/banking/entry-bank-line.tsx  |  46 ++
 frontend/src/features/banking/hooks.ts             |  62 +++
 .../src/features/banking/reconciliation-report.tsx | 374 ++++++++++++++++
 frontend/src/features/banking/types.ts             | 106 +++++
 frontend/src/features/gl/fx-revaluation-lines.ts   |  30 ++
 frontend/src/features/gl/fx-revaluation-screen.tsx |  19 +-
 .../features/gl/reports/fx-revaluation-report.tsx  |  53 ++-
 frontend/src/i18n/messages/en.json                 | 132 +++++-
 39 files changed, 2633 insertions(+), 53 deletions(-)
```

### The screens

1. **Enquiries → General Ledger → Bank account enquiry** (`/gl/enquiries/bank-account`, after
   Trial balance enquiry). It shows decision 10's figures for one account at a date:
   * the book balance, in the account's currency and, for a foreign account, in base;
   * the last locked reconciliation (number, date, statement balance);
   * unmatched statement lines and outstanding ledger lines, each as a count and a sum;
   * the latest statement (number, to-date);
   * the open reconciliation.

   Every figure links somewhere:
   * the book balance → Cashbooks detail at that date;
   * the last reconciliation → its report;
   * the statement → its detail;
   * the open reconciliation, unmatched and outstanding → the workspace if a reconciliation is
     open, otherwise the account's reconciliation listing, where one is started.

   Account and date live in the URL, and the body sits behind `QueryState`. Cash accounts are
   listed too: the reconciliation and statement cards say "counted, not reconciled" rather than
   showing zeros that look like a clean bank.
2. **Reports → General Ledger → Cashbooks** (`/gl/reports/cashbooks`). Mode, account and range
   live in the URL.
   * **Detail:** opening; then each line with date, entry (linked), doc type, reference,
     description, partner, receipt / payment, running balance and *Reconciled* (`BRC-n`,
     *Matched*, or blank); totals; closing, and closing in base.
   * **Summary:** one row per bank and cash account — opening, receipts, payments, closing in
     the account's currency and in base, last reconciled (date and balance, linked to that
     report), unmatched statement lines, outstanding lines — with a base total. Each account
     drills to its detail.
   * Print and CSV follow the P3–P7 shape: `ReportPage`, a print masthead, and CSV cells with
     the wire's decimals.
3. **Reports → General Ledger → Bank reconciliation** (`/gl/reports/bank-reconciliation`).
   Open with `?reconciliation=` or `?account=`, or use the two pickers. It works over one
   reconciliation, open or locked:
   * account, number, date and status;
   * the statement: *Balance per bank statement*, *Add: deposits in transit*, *Less:
     unpresented payments*, *= Adjusted bank balance*, *Balance per cashbook*, *Difference*;
   * on a locked one, the stored figures (*At lock*) beside the live recomputation (*Now*);
   * the outstanding items (the stored snapshot on a locked one);
   * the unmatched statement lines, on an open one;
   * *Posted after lock*, with each line's *Dated inside BRC-n*, on a locked one.

   Print and CSV. A link to the workspace is hidden in print.
4. **FX revaluation report** (P7 step 8). It now uses the 7b screen's treatment:
   * a bank line is keyed by its bank account and shows the account's code and name where a
     document line shows its number and partner;
   * the open amount is in the line's own currency;
   * the rates are trimmed;
   * the headers read *Document or account* / *Partner or bank account*;
   * the run picker names each run's scope (all five role labels), and its date is formatted.

   A bank line drills to the account's Cashbooks detail at the run date. The three labelling
   helpers moved out of the 7b screen into `fx-revaluation-lines.ts`, so both screens read one
   definition.
5. **GL entry page.** When an entry has any line on a bank account, a *Bank* column appears.
   * Each bank line shows its account and one of:
     * *Locked in BRC-n*, linked to that report;
     * *Matched · rule*;
     * *Outstanding*.
   * A late line also reads *Dated inside BRC-n*.
   * Without `bank:reports_view` the column is not drawn.

   `sources.py` gains nothing.
6. **Nav.** The owner's *Bank reconciliation* and *Cashbooks* rows under Reports → GL lose their
   P8 tag and go live on `bank:reports_view`, with no C.1 entry. The enquiry row is recorded
   under **C.1.14**, in `appendix-c-order.test.tsx` (the amendment note and the table) and in the
   Master Plan (its paragraph under C.1.14, a new *Enquiries → GL* row in the Appendix C table,
   and the Reports → GL row with its two routes). No P8 tag is left in the tree, so the guard
   test "no finished phase's tag left" now includes P8.

### The backend: three reads the screens needed

* `GET /banking/journal-entries/{id}/bank-lines` serves the GL entry page. It calls
  `matching.list_ledger_lines` (the workspace pane's own query) with a new `entry_id` narrowing,
  once per bank account the entry touches. The entry page and the workspace therefore cannot
  disagree about a line, and the GL module's entry read learns nothing about banking.
  `LedgerLineRow` / `LedgerLineRead` gain `reconciliation_id`, so a `BRC-n` can be a link.
* The reconciliation report carries `bank_account_name`.
* The Cashbooks summary row carries `last_reconciliation_id`.

A new HTTP test, `test_the_entry_page_reads_each_bank_line_locked_matched_or_outstanding`, covers
all three states, the contra line's absence, and the two new fields.

## Decisions worth review

1. **The entry page reads banking through a banking endpoint, not a field on the GL entry.**
   Putting the bank state on `JournalEntryRead` would have made `api/v1/gl.py` import the banking
   module. A separate GET keeps the direction the module boundary already has, and it lets the
   column follow `bank:reports_view`, the permission every other banking read uses.
2. **Cashbooks' Description column reads the entry's description where the bank line carries
   only the reference.** The kernel stamps a cashbook entry's cash line with `event.reference or
   event.description` (`posting.py`), which the statement matcher relies on, so I did not change
   it. The report already has a Reference column, and printing the same token twice was found on
   the first screenshot. The rule is narrow: a line whose description is its own keeps it. Test:
   `test_the_description_column_reads_the_entry_not_the_reference_twice`. The GL entry page still
   shows the line's own description under the account, as it always has.
3. **The reconciliation report's outstanding list on a locked reconciliation is the stored
   one**, the snapshot it was signed with. The late lines are listed separately under *Posted
   after lock*, and *Now* shows the live figures. The two readings sit side by side as decision
   5 asks, and nothing on the page restates the signed document.
4. **Deposits in transit and unpresented payments are split on the screen** from the
   outstanding list: positive lines are deposits, negative lines are payments. They sum to
   `outstanding_total`, and the adjusted bank balance is the server's `adjusted_bank_balance`,
   not a second computation.
5. **The enquiry's "open reconciliation" card links to the listing when none is open**, where
   *New reconciliation* is. The unmatched and outstanding figures link there too in that case,
   because without an open reconciliation there is no workspace to open.
6. **What Evolution does.** Its Cashbook Detail report has no reconciliation-status column; the
   *Reconciled* column is decision 6's. Its Bank Reconciliation report prints only the current
   state. This report prints *At lock* beside *Now*, as decision 5 requires.

## The e2e: `frontend/e2e/p8-enquiries-reports.spec.ts`

The spec runs on Rugari, on a suffixed bank account of its own. Its ledger is posted through the
API, dated today:

| line | amount | on the statement | at the lock |
|---|---:|---|---|
| opening | +1,000,000 | yes | matched, locked in `BRC-n` |
| receipt A | +250,000 | yes | matched, locked in `BRC-n` |
| receipt B | +120,000 | yes | matched, locked in `BRC-n` |
| payment | −80,000 | no | outstanding (unpresented) |
| late payment | −30,000 | no | posted after the lock, dated inside it |

One statement is imported as multipart through the page's own `fetch` and matched 1:1 by hand,
and one reconciliation is locked at a zero difference (statement 1,370,000, cashbook 1,290,000,
one unpresented 80,000). The walk:

1. **The enquiry.** It checks FRw 1,260,000, the BRC and its 1,370,000, *0* unmatched, *2*
   outstanding at FRw −110,000, and the BST. It then checks every link's `href` and follows four
   of them: the statement, the listing, the report and Cashbooks.
2. **Cashbooks detail.** *Reconciled* reads `BRC-n` on receipt A and is blank on the payment
   and on the late line. The payment's row reads the entry's description. The page shows *Totals
   over 5 lines*, receipts FRw 1,370,000 and closing 1,260,000. **Then the Trial balance
   enquiry**, for the same account and date: its Net cell is asserted equal to the text of
   Cashbooks' *Closing in RWF*. Two screens, one figure.
3. **Cashbooks summary.** Closing 1,260,000, *0* unmatched, *2* outstanding; the account's link
   lands on its detail.
4. **The reconciliation report** on the locked BRC:
   * *At lock*: statement 1,370,000, unpresented 80,000, adjusted = cashbook 1,290,000,
     difference 0;
   * *Now*: cashbook 1,260,000, unpresented 110,000;
   * *Outstanding items: 1* (the payment's entry);
   * *Posted after lock: 1* (the late line, *Dated inside BRC-n*, −30,000).
5. **The print**, through `page.pdf()` (print media) and `pdftotext -layout`. It asserts
   `= Adjusted bank balance` followed by `FRw 1,290,000`, *Outstanding items: 1*, and the
   payment's entry number, and it asserts that *Open the workspace* did not print.
6. **The CSV, from a real download.** It checks the file name, the adjusted balance of 1290000,
   unpresented 80000, exactly one *Outstanding* row (the payment, −80000), and exactly one
   *Posted after lock* row (the late line).
7. **The FX report** on a posted bank-role run at the month end. The spec reuses a standing
   `bank` / `all` run at that date, or posts one itself after a USD receipt on `1121` (the
   month's two rates are added only if absent, and the next period is opened for the mirror).
   Picked from the run picker, `tr[data-revaluation-line="1121"]` reads *Bank Account USD*. The
   spec checks that the line links to Cashbooks at the run date, and checks the open amount in
   USD, the difference in RWF, and the line count. The teardown reverses the run only if this
   spec posted it.
8. **The GL entry page** on receipt A reads *Locked in BRC-n*, linked to its report, beside the
   formatted 250,000. The late payment's page reads *Outstanding* and *Dated inside BRC-n*, and
   the contra line carries nothing.

**Money and quantity per screen.** Every report and enquiry screen asserts a formatted money
value and a formatted quantity read off the page. Two screens assert money only:
* The **GL entry page** shows no formatted quantity. Its quantity assertion is a DOM count: one
  bank-line cell, and two rows on the late entry.
* The **Trial balance screen** is the tie's second half. It asserts the one figure it is there
  for.

The statement detail and the reconciliation listing are only landed on through the enquiry's
links, with an exact heading asserted. Their own specs are `p8-transactions`.

**It needs no CI config.** A new spec joins the `rest-1..3` shards through the path filter.
`ci-e2e-groups.test.ts` (in vitest) passes and proves every spec belongs to exactly one group.

## Labels: `{ exact: true }` and the loose-lookup scan

Every label lookup in the new spec is `{ exact: true }`, except one anchored regex: the run
option, `^FXR-n — `.

**What was scanned.** Every string this step adds or changes, 118 keys in all: the three
namespaces, `gl.bankReconciliation`, the five changed FX report keys and the three nav labels.
Each was split on its placeholders into 101 fixed fragments. Each fragment was checked against
every `getByLabel` / `getByText` / `getByPlaceholder` / `getByTitle` / `name:` in `e2e/` that
lacks `exact: true`, as a case-insensitive substring, which is Playwright's default.

**Six distinct lookups hit, and none is a collision:**
* **`"Posted"`**, `.first()`, in five specs: the posting toast and status chip. The new strings
  containing "posted" are on the reconciliation report and the enquiry, which those specs never
  open. The entry page's new column has no "posted" in it.
* **`"Sent"`**: the fiscal queue chip. Its hits are *unpresented* inside the report and the
  enquiry.
* **`"Reference"`, `"Name"`, `"Description"`**: scoped to the batch grid or a dialog that gains
  no label.
* **`"To"`** in `p7-cycle-tape`: a comment.

## Checks

**Backend (in the container):**
* `ruff check .` is clean.
* The full suite, `pytest -n 4 -q`, gave **1636 passed** (24m48s) at the commit before the
  description fix. After the fix, `tests/banking/test_reports.py` gave 17 passed and the banking
  suite without `slow` gave 234 passed.
* `--collect-only`: main **1635**, branch **1637** (the entry-page test and the description
  test). Main was collected with main's `tests/` mounted over the container's.

**Frontend:**
* `tsc --noEmit` is clean.
* `eslint` has no finding in a touched file. The one warning is in `journal-batches/new`, on
  main.
* `vitest run`: **439 passed** (16 files), including `appendix-c-order` and `ci-e2e-groups`.
* `playwright test --list`: main **283 tests in 34 files**, branch **294 in 35**. That is the
  new spec's 8 plus 3 axe tests, one each for the three new routes.
* `next build` was not run locally, because the dev server's `.next` is bind-mounted into its
  container. CI builds.

**The touched-spec set, derived from `git diff --stat`:**

| diff entry | specs |
|---|---|
| `gl/entries/[id]/page.tsx` (every spec that opens `/gl/entries/…`) | `ar-ap-acceptance`, `gl-cashbook`, `dated-rate`, `ar-ap-documents`, `inventory-transactions`, `gl-reversal`, `p6-orders`, `p6-cycle-tape`, `journal-flow`, `ar-ap-allocation`, `p7-cycle-tape`, `p6-enquiries-reports`, `inventory-acceptance`, `p7-enquiries-reports`, `ar-ap-corrections`, `p7-transactions` |
| `fx-revaluation-report.tsx`, the FX report keys in `en.json` | `p7-enquiries-reports` |
| `fx-revaluation-screen.tsx` (the helpers moved out) | `p7-transactions`, `p7-cycle-tape`, `p8-payment-runs` |
| banking `account-picker` / `hooks` / `types` (appended to; used by the statements, reconciliation and run screens) | `p8-transactions`, `p8-payment-runs` |
| the new screens | `p8-enquiries-reports` |
| `nav-tree.ts` (the sidebar on every page; three new routes under Enquiries and Reports) | all five axe sweeps |

`report-page.tsx` did not change. `p1-auth-screens` and `p6-enquiries-reports` name `nav-tree.ts`
in comments only.

**Runs.** e2e ran on a database reset by `make db-reset` with the `docker-compose.e2e.yml`
overlay, after warming **102 nav routes** (every href, `/` and `/login` included) and 8 detail
routes by curl. Every one returned 200.

| run | specs | result |
|---|---|---|
| the new spec, first run (a used DB) | `p8-enquiries-reports` | 8 passed |
| **the record: every touched spec, one run, fresh reset, 1 worker** | the 19 above | **111 passed** (13.9m) |
| the axe sweeps on the same DB, right after, `--workers=4` | the five | **1 failed**, 105 passed; see below |
| the axe sweeps on a fresh reset, as CI runs them | the five | **106 passed** (5.0m), including the three new routes, light and dark |
| after the screenshot fixes, fresh reset | `p8-enquiries-reports`, `p7-enquiries-reports` | **19 passed** (1.6m) |

**The one axe failure is on main and is not this diff's.** `/tax/reports/vat-return`, light and
dark, fails `definition-list` (serious) on `section:nth-child(6) > dl`. That is the settlement
panel of `vat-return-report.tsx`: a `<p>` sits inside a `dt`/`dd` group, and a group holds only a
link. The panel renders only once a VAT return is filed. `p7-enquiries-reports` files one in the
record run, and the sweep ran on that database. CI runs the a11y group on its own freshly seeded
runner, where no return is filed, which is why it has never shown there. It is outside this
step's scope, so I left it and am reporting it here. The fix is two lines: move the note out of
the `dl`, and give the link group a `dt`/`dd`.

## Screenshots

The screenshots are in `docs/screenshots/p8-step-8/`, light and dark, with a README. They were
captured by `frontend/scripts/capture-p8-enquiries-reports.ts` on a fresh reset:

1. the enquiry;
2. Cashbooks detail with the *Reconciled* column;
3. Cashbooks summary;
4. the reconciliation report on a locked BRC, with an outstanding item and a late line;
5. the FX report with `1121`'s bank line;
6. the GL entry page's bank line, *Locked in BRC-000001*.

**Looking at them found two defects**, both fixed and re-verified before the final shots.
Cashbooks' Description printed the reference (decision 2 above). The FX run picker showed the raw
ISO date.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
