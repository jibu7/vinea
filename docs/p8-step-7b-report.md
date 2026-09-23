# P8 step 7b — Payment runs, the run's settlement on the AP document, and the FX revaluation screen's bank lines

Step 7 was split across two sessions. **This is part 2 of 2.** Part 1 (PR #71) built Bank
statements and the reconciliation workspace (C.1.14). This part builds Payment runs (C.1.15),
"Paid in run" on the AP document, the FX revaluation screen's `bank` / `all` roles and bank
lines, and chains auto-match to Import. It is not a gate. The changed specs and the guard tests
were run locally, `git diff --stat` is quoted below before any count, and CI is the record for
the branch head.

## What landed

```
$ git diff --stat origin/main..HEAD
 backend/app/api/v1/banking.py                      |  25 +-
 backend/app/api/v1/gl.py                           |   2 +
 backend/app/api/v1/subledger.py                    |  15 +-
 backend/app/banking/matching.py                    |   8 +-
 backend/app/banking/payment_runs.py                | 106 ++++-
 backend/app/schemas/banking.py                     |  15 +
 backend/app/schemas/gl.py                          |   2 +
 backend/app/schemas/subledger.py                   |   7 +
 backend/app/subledger/revaluation.py               |   2 +
 backend/tests/banking/test_api.py                  |  71 +++
 backend/tests/banking/test_bank_revaluation.py     |   1 +
 backend/tests/banking/test_payment_runs.py         |   5 +
 backend/tests/test_api_has_a_caller.py             |  30 +-
 docs/Vinea_ERP_Master_Plan_v5.md                   |  29 +-
 docs/p8-step-7a-report.md                          |   8 +
 docs/screenshots/p8-step-7/README.md               |  31 ++
 .../p8-step-7/7b-1-new-run-preview-discount-and-warnings-{dark,light}.png (new)
 .../p8-step-7/7b-2-run-detail-instruction-file-{dark,light}.png           (new)
 .../p8-step-7/7b-3-ap-document-paid-in-run-{dark,light}.png               (new)
 .../p8-step-7/7b-4-workspace-run-matched-one-to-three-{dark,light}.png    (new)
 .../p8-step-7/7b-5-fx-preview-bank-line-{dark,light}.png                  (new)
 frontend/e2e/p8-payment-runs.spec.ts               | 486 ++++++++++++++++++++
 frontend/e2e/p8-transactions.spec.ts               |  34 +-
 frontend/scripts/capture-p8-payment-runs.ts        | 252 +++++++++++
 .../src/app/(shell)/ap/payment-runs/[id]/page.tsx  |   6 +
 .../src/app/(shell)/ap/payment-runs/new/page.tsx   |  10 +
 frontend/src/app/(shell)/ap/payment-runs/page.tsx  |  13 +
 .../design/components/appendix-c-order.test.tsx    |   7 +
 frontend/src/design/nav-tree.ts                    |  11 +
 frontend/src/features/banking/hooks.ts             |  92 ++++
 .../features/banking/new-payment-run-screen.tsx    | 504 +++++++++++++++++++++
 .../features/banking/payment-run-detail-screen.tsx | 347 ++++++++++++++
 .../src/features/banking/payment-runs-screen.tsx   | 121 +++++
 .../src/features/banking/statements-screen.tsx     |  43 +-
 frontend/src/features/banking/types.ts             | 120 +++++
 frontend/src/features/gl/fx-revaluation-screen.tsx |  77 +++-
 frontend/src/features/gl/types.ts                  |  25 +-
 .../features/subledger/document-detail-screen.tsx  |  34 +-
 frontend/src/features/subledger/types.ts           |   8 +-
 frontend/src/i18n/messages/en.json                 | 126 +++++-
 45 files changed, 2585 insertions(+), 88 deletions(-)
# (plus this report; the ten new PNGs are condensed to five lines above)
```

### Payment runs — `/ap/payment-runs` (Transactions → Accounts Payable, after Payment)

* **The list**, per bank account (`bank` kind only, as on Bank statements, because a cash account
  is refused `payment_run_needs_bank`), behind `QueryState`: number, payment date, suppliers,
  total paid, status, and the reason for a reversal. Below it: *Runs: n · standing: m*. The
  account is carried in `?account=`.
* **New** — `/ap/payment-runs/new`: bank account, payment date, and a **due-by** filter (empty
  means any due date). The grid is **supplier × invoice**: the open supplier invoices in the
  account's currency, grouped under each supplier, each with its due date, open amount,
  `discount_available` at the payment date (P4's own figure, re-read when the date changes), a
  *Take* toggle (ticked by default where there is a discount, disabled where there is none) and
  an *Amount to pay* for a partial payment (empty pays all that is open).
  **`payment_exceeds_open` is said on the row as it is typed**, with the most the invoice takes.
  If the server refuses a line (`lines.N.amount`), that refusal is mapped back to its row too.
  * **Preview** shows each supplier with its bank details or the **Bank details missing** chip,
    and what that means (the payment still posts; the instruction file carries the row with the
    account fields empty). A supplier's **open credits are named** (*Open credits not netted:
    PMT-n…*): the run pays the invoices in full, and allocation is the Allocate screen's job.
    Per invoice: open, paying, discount taken, leaves the bank. Then the supplier's total, the
    run's discount total, and the run total.
  * **Post** is offered only for the selection that was previewed. Any change after the preview
    withdraws it, with *The selection changed after the preview. Preview again.* shown before the
    button. It posts under an **`Idempotency-Key` minted with each preview** (a draft UUID), so a
    retried press replays the run it made. A new preview gets a new key, so a key is never
    offered for a second request body. **Post shows the run it made**: it navigates to
    `/ap/payment-runs/{id}` with *PYR-n posted*.
* **The run** — `/ap/payment-runs/{id}`:
  * the facts: status, payment date, bank reference, suppliers, invoices, total paid;
  * **the lines**: supplier, invoice, the `PMT-` and the `ALC-`, each a link to its AP document,
    then settled / discount / paid, and *Paid from the bank*;
  * **Instruction file** (download, the server's CSV and filename);
  * **Remittance advices**: the run's `remittance_pdf` jobs with their status and artifact name,
    each with a **Download** once *Ready*. The jobs run after the post's response, so the list
    polls while any is queued or running;
  * **Reverse run**, with a dialog asking for the reversal date (defaulting to the payment date)
    and a reason. It is refused before the button, with the reason beside it: *already reversed*,
    or **`reconciliation_locked`** — *The bank's line for this run is matched inside BRC-n, which
    is locked. Reopen BRC-n to reverse the run.*

The listing is `bank:reports_view`. New, Post, Reverse and both downloads are
`bank:payment_run_post`, as the API gates them, and are drawn only for that permission.

### The AP document — "Paid in run PYR-n"

On a settlement a run posted, `/ap/documents/{id}` shows **Paid in run PYR-n**, with a link to
the run. Once the run is reversed it reads *· the run was reversed*. While the run stands,
Reverse is disabled and **`payment_run_member` is said beside it**: *Paid in PYR-n: a payment
run's payment is reversed with its run. Open PYR-n and reverse the run.* This check comes
**before** *Unallocate first*. A run's settlement is always allocated, so the old first answer
would have sent the person to undo an allocation the run owns.

### FX revaluation (P7's screen)

* **Labels for the two new roles**: *Bank and cash accounts* (`bank`) and *Customers, suppliers
  and bank* (`all`). The combobox already offered every `FxRevaluationRole` value, so before
  this step it drew the missing translation keys. The field is widened to hold the longest label.
* **Bank lines are keyed by bank account.** `document_id` is null on them. Before this step,
  every bank line got the React key `null` and drew blank cells. Now a line is keyed
  `document-{id}` or `bank-{id}`. The account's **code sits where the document number does** and
  its **name where the partner does**, in the preview and in the posted run's detail. The
  columns read *Document or account* and *Partner or bank account*.
* **`open_amount` is formatted in the line's own currency** (`$ 500.00`), not shown as the raw
  `NUMERIC(20,6)`. The two rates lose their trailing zeros, and the preview counts its lines.
* The FX **report** (step 8) is untouched.

### Import chains auto-match

After **Import statement** is confirmed, the screen calls `POST /banking/accounts/{id}/auto-match`
and the result reads **"N new, M skipped, K matched"**. **The import service is unchanged.** This
is the reading of decision 4's "on import, and on demand": the screen chains the workspace's own
auto-match to the confirm. The endpoint needs `bank:reconcile`. An importer without it gets
"N new, M skipped", and the import stands. If auto-match fails, a toast says so and the import
still stands. A keyed paper statement does not chain it, because the prompt names Import.

`p8-transactions` is adjusted to match. Its opening deposit is now matched **at import** (by
amount and date, "5 new, 0 skipped, 1 matched"; the detail reads *1 of 5*, and the workspace
opens at outstanding FRw -30,000 and difference FRw 215,500). **On-demand Auto-match is proven on
a line posted after the import**: the customer transfer is posted after the statement lands, so
the import's pass could not have found it, and Auto-match reads *1 matched, 0 left*. From there
the walk and every figure are as before.

### Backend (driven by the screens)

| change | why |
|---|---|
| `PaymentRunLineRead` gains `partner_name`, `supplier_code`, `document_number`, `settlement_number`, `settlement_status`, `allocation_number` (via `payment_runs.line_views`, which reads the numbers in three queries) | The run's page draws the `PMT-` and `ALC-` links by number. Without these it would have had to fetch every document and allocation the run touched. |
| `PaymentRunDetail.reconciliation_locked` (`payment_runs.locked_in`) | `reconciliation_locked` said before the Reverse button. The same matches `reverse_run` asks `assert_unmatchable` about. |
| `PaymentRunRead.supplier_count` (one grouped query in the listing) | The listing's *Suppliers* column. |
| `DocumentRead.payment_run_id / _number / _status` on a settlement (via `run_of_settlement`) | "Paid in run", and `payment_run_member` before Reverse. Only asked for a settlement. |
| `FxRevaluationLineRead.bank_account_name`, preview and stored | The account's name where the partner sits. |
| `matching._reconciliation_number` → `reconciliation_number` | `locked_in` needs it; it is renamed in place rather than reached into as a private name. |

Tests:

* `test_a_payment_run_is_previewed_posted_and_reversed_over_http` now asserts the line numbers,
  `supplier_count`, `reconciliation_locked`, the listing, and the settlement's
  `payment_run_*` fields (and their absence on the invoice), before and after the reversal.
* `test_a_run_inside_a_locked_reconciliation_is_not_reversible` asserts `locked_in` names the
  BRC.
* `test_reversing_the_run_releases_its_match` asserts matched-but-open is **not** locked.
* The bank-revaluation test asserts `bank_account_name`.
* **New:** `test_the_preview_names_a_suppliers_open_credits_and_never_nets_them`. The
  `open_credits` warning had **no test at any level** until this step, although the screen renders
  it.
* `locked_in` was proven sensitive: returning `None` failed the locked-reconciliation test with
  `assert None == 'BRC-000001'`, and the file was restored.

**A correction found while building the detail:** `payment_run_lines.amount` is the **cash
paid** (`post_run` stores `cash_amount`), not the amount settled. The first cut of the page read
it as settled. The page and `PaymentRunLineRead`'s docstring now say it: settled = `amount +
discount_amount` (`fix(bank)` commit).

## Rule 14 — the register

**The last three `GAP (P8, step 7)` lines are gone.** Each is deleted by a button in the product,
and each button is pressed by `e2e/p8-payment-runs.spec.ts`:

| endpoint | the button the spec presses |
|---|---|
| `POST /banking/payment-runs/preview` | **Preview** on `/ap/payment-runs/new` |
| `POST /banking/payment-runs` | **Post payment run** on the same screen |
| `POST /banking/payment-runs/{run_id}/reverse` | **Reverse the run** in the reason dialog on `/ap/payment-runs/{id}` |

**The register now carries no P8 line.** Step 8's endpoints (Cashbooks, the reconciliation
report, the bank account enquiry) are all GETs, so the phase closes with nothing in the register
for it. The register was proven sensitive: renaming the preview call site in `hooks.ts` failed
the test on exactly `POST /api/v1/banking/payment-runs/preview`, and the file was restored.

## Appendix C — C.1.15

**Payment runs** sits under Transactions → Accounts Payable, directly after Payment. It is
recorded in `nav-tree.ts` (permission `bank:payment_run_post` or `bank:reports_view`, as the API
gates it), in `appendix-c-order.test.tsx` (the row and the amendment note), and in the Master
Plan (the Appendix C table's Transactions → AP row, and a new **C.1.15** entry). C.1.14's Import
sentence now reads "N new, M skipped, K matched" and says why.

## Docs carried from 7a

Under decision 7 in `docs/p8-step-7a-report.md`: reopen taking an `Idempotency-Key`
**supersedes the step-3 reading** in `docs/p8-step-3-report.md` (its decision 3, and its summary
line "No idempotency column for `reverse_run` or `reconciliation.reopen`"). No column was added;
the key shares the row's existing `idempotency_key`. A run's reverse still takes none.

## The spec — `frontend/e2e/p8-payment-runs.spec.ts`

**CI placement:** the file lands in the `rest-*` shards through the path filter, and
`ci-e2e-groups.test.ts` (in the vitest run) confirms it is in exactly one group.

**Setup** runs on Rugari, on a bank account of the spec's own (suffixed, generic format, funded
with FRw 1,000,000), with 2/10 net 30 terms and three suffixed suppliers:

* *Nyanza Timber* on the terms, invoice 100,000;
* *Rubavu Glass* with a FRw 20,000 payment on account (the open credit), invoice 236,000;
* *Huye Corks* with no bank details, invoice 50,000.

All three invoices are posted through the API, today, so the payment is inside the discount
window.

**The walk:**

1. **New**:
   * The due-by filter is picked; *Discount available* reads **FRw 2,000** on the 2/10 invoice
     and *—* on the others. The three are ticked (*Selected: 3 of…*, with *Take* ticked by default).
   * 60,000 on the 50,000 invoice reads *More than is open. The most this invoice takes is
     FRw 50,000.*, and clearing it clears that.
   * **Preview**: *Suppliers: 3 · invoices: 3*, discount taken **FRw 2,000**, run total
     **FRw 384,000**, Nyanza **FRw 98,000**, *Bank details missing* on Huye with its sentence, and
     *Open credits not netted: PMT-n…* on Rubavu, which is still paid **FRw 236,000** in full.
   * Unticking *Take* withdraws Post (*The selection changed after the preview.*), and re-ticking
     restores it.
   * **Post** lands on the run.
2. **The run**: total **FRw 384,000**, suppliers **3**, invoices **3**. Nyanza's line reads 100,000
   settled and 98,000 paid. Each line's `PMT-` link goes to `/ap/documents/{its id}`, with an
   `ALC-` beside it, three distinct. **Instruction file** is downloaded and read: the header,
   **three rows**, `Huye Corks …,,,50000,RWF,PYR-n,HC…` with the account fields empty, and
   Nyanza's `…,98000,RWF,PYR-n,…`. The three advices reach *Ready*, and Nyanza's PDF through
   **`pdftotext`** carries the invoice number, `98,000` and `2,000`.
3. **The AP document** (Nyanza's `PMT-`): *Paid in run PYR-n*, linking to the run, total
   **FRw 98,000**, and `payment_run_member` said beside a disabled Reverse. The allocation reads
   98,000 with the 2,000 discount against the invoice.
4. **Import** of a one-line statement (`BULK PAYMENT PYR-n`, reference `PYR-n`, debit 384,000) on
   the run's account: the preview reads *1 lines · 1 new · 0 already held*, and the result
   **"1 new, 0 skipped, 1 matched"**. On the workspace (the reconciliation opened through the
   API), the line reads *Matched · payment run*, *Ledger lines: 3*, **FRw -384,000**, unmatched
   **0**. The three ledger lines carrying the run's reference each read *Matched to BULK PAYMENT
   PYR-n*.
5. **Reverse** with a reason. The button is disabled until the reason is keyed. Afterwards:
   *already reversed* is said before the button, and the reason is shown. The invoice's open
   amount is back to **FRw 100,000** (line quantity **1.00**), and the payment reads *Paid in run
   PYR-n · the run was reversed*. The listing shows the run *Reversed* and *Runs: 1 · standing: 0*.
   On the workspace the bank's line is **Unmatched** again and unmatched reads **1**.
6. **FX revaluation**:
   * The month's two dated USD rates are held (1,320 on the 1st, 1,350 at month end, added only
     where absent, as the P7 specs do), and USD 250.00 is received on `1121` at 1,320.
   * The *Side* is set to *Bank and cash accounts*. The `1121` row reads *Bank Account USD*.
   * **Open** is the API's balance formatted in USD (`$ 250.00` on a fresh DB; the spec adds 250
     to whatever `1121` already held, so a re-run on a used DB still holds). The difference is
     the API's, in base. *Lines: n* is the API's count.

Every screen asserts at least one formatted money value and one formatted quantity read off the
page. On the AP document the two come from two visits to one screen: the settlement has no lines,
so its quantity is read off the invoice's own detail after the reversal. Every label lookup is
`{ exact: true }`, except one: `getByRole("link", { name: /^ALC-/ })` is an anchored regex.

## Labels — `{ exact: true }` and the loose-lookup scan

Every string this step adds or changes was split on its placeholders into fixed fragments:
105 keys and 107 fragments, including the nav label *Payment runs*, which renders in the sidebar
on every page. Each fragment was scanned against every `getByLabel` / `getByText` /
`getByPlaceholder` / `getByTitle` / `name:` in `e2e/` that lacks `exact: true`, as a
case-insensitive substring (Playwright's default).

**50 hits, none a collision.** Every hit is a lookup on a screen that never renders the new
string:

* `"Posted"` — posting toasts in the AR/AP and GL specs; the new *Posted* is a run status chip.
* `"Reason"` — the reversal dialogs; *Reversal reason* is a column header on the runs listing.
  `ar-ap-corrections`' `getByLabel("Reason")` is on the document detail, but scoped to its
  reversal dialog, which gains no label.
* `"Reversed"` — the GL entry page.
* `"Ready"` — the AR statement job; it is a substring of *already*.
* `"Details"` — the Suppliers screen.
* `"Reference"` — the batch grid.
* `"To"` in `p7-cycle-tape` — the anchored regex on the VAT return.

**Regex lookups:** none mentions *Payment* or can match the new nav row. `/^Discount on /` (the
allocation screen) cannot match *Take the discount on …*. The specs were also run, which is the
check that settles it.

## Decisions worth review

1. **Auto-match on import is the screen's, not the service's.** This is the reading the prompt
   asks for (decision 1 of the 7a report, closed this way). The tape and the service tests are
   untouched, because nothing an import writes has changed. The cost is that an import through the
   API alone does not match. If the owner wants the service to do it, that is a separate
   decision, as 7a said.
2. **Post's `Idempotency-Key` is minted per preview**, not per screen. A key belongs to one
   request body. A key minted when the screen opened would be re-sent with a different selection
   after a second preview and refused `idempotency_key_reused`.
3. **Post needs a fresh preview.** The previewed payload is compared with the current one, and
   Post is withdrawn with the reason whenever they differ. The alternative, posting whatever is on
   the grid, would post a selection nobody has read the totals of.
4. **`payment_run_member` is shown as visible text beside Reverse** only for a run's settlement.
   The document detail's other refusals stay in the button's title, as P4 drew them. Changing
   those would touch every AR/AP spec's view of the page for no gain in this step.
5. **The `ALC-` link lands on the invoice.** An allocation has no page. It is listed on both
   documents it joins, and the invoice is the one the run paid.
6. **The instruction file and the advices are downloaded by fetch-and-blob (`downloadFromApi`)**,
   the VAT annexes' way, rather than as plain links. The server's filename is kept, and a
   `job_not_ready` or permission refusal arrives as a toast rather than a JSON page.
7. **Reversal date defaults to the payment date** in the dialog. The service's own default is the
   same, and an earlier date is refused `reversal_before_original`.

## Verification

Backend, in the container:

* `make be-lint`: all checks passed.
* `pytest -n 4` (the whole suite): **1635 passed** (25m14s). An earlier attempt at `-n 8` errored
  at setup on every test. I did not diagnose it; I restarted the backend container and re-ran at
  `-n 4`, the worker count 7a used, and that run is the one quoted.
* `--collect-only -q tests/banking tests/test_api_has_a_caller.py` collected **255**.

Frontend:

* `tsc --noEmit` is clean.
* `eslint` has no finding in a touched file. The two warnings are in `allocation-screen.tsx`, on
  `main`.
* `vitest run`: **431 passed** (16 files), including `appendix-c-order`, `ci-e2e-groups` and
  `no-utc-dates`.
* `playwright test --list`: **283 tests in 34 files**. The new spec lists 7, and with
  `p8-transactions` there are 17 in two files.
* `next build` was not run locally, because the dev server's `.next` is bind-mounted into its
  container. CI builds.

**The touched-spec set was derived from `git diff --stat`:**

| diff entry | spec |
|---|---|
| `ap/payment-runs/*`, `payment-run*-screen.tsx`, the hooks | `p8-payment-runs` |
| `statements-screen.tsx` (Import chains auto-match) | `p8-transactions` |
| `fx-revaluation-screen.tsx` | `p7-transactions`, `p7-cycle-tape` (both open the screen), `p8-payment-runs` |
| `document-detail-screen.tsx` (every spec that opens `/ar\|ap/documents/{id}`) | `ar-ap-corrections`, `p6-enquiries-reports`, `inventory-acceptance`, `p7-transactions`, `p7-enquiries-reports`, `p7-cycle-tape` |
| `nav-tree.ts` | `accessibility-transactions` (sweeps `/ap/payment-runs`), `accessibility-dashboard` (sidebar and palette) |
| backend `DocumentRead` / FX line shape | the same screens as above |

`p1-auth-screens` names `nav-tree.ts` in a comment only. `ar-ap-documents` opens the posting
forms, not the detail.

e2e ran on a database reset by `make db-reset` (with the `docker-compose.e2e.yml` overlay), after
**104 routes** had been warmed with curl (0 non-200). Those were every nav href including `/`,
plus `/login`, `/ap/payment-runs/new` and five detail routes:

| run | specs | result |
|---|---|---|
| the new spec, first run (a used DB) | `p8-payment-runs` | **failed** on a spec error: the allocation on the payment reads 98,000 cash with the 2,000 discount beside it, not 100,000 |
| second | `p8-payment-runs` | 7 passed |
| `p8-transactions` after the auto-match change | `p8-transactions` | 10 passed |
| **the record: every touched spec, one run, fresh reset, 1 worker** | `p8-payment-runs`, `p8-transactions`, `p7-transactions`, `p7-cycle-tape`, `p7-enquiries-reports`, `ar-ap-corrections`, `p6-enquiries-reports`, `inventory-acceptance` | **75 passed** (7.4m) |
| the axe sweeps the nav change reaches, `--workers=4` as CI runs them | `accessibility-transactions`, `accessibility-dashboard` | **37 passed** (1.3m), including *Accounts Payable › Payment runs (/ap/payment-runs)*, light and dark |

The FX fixes found by the screenshots (the picker width, the rates) came before the record run,
so the record run covers them.

## Screenshots

The shots are in `docs/screenshots/p8-step-7/`, prefixed `7b-`, light and dark. They were
captured by `frontend/scripts/capture-p8-payment-runs.ts` on a second fresh reset, and the README
there says what each one shows. Every one was opened and looked at before commit.

| # | shot |
|---|---|
| 1 | New: the discount available and taken (FRw 2,000), *Bank details missing*, the open credit named, run total FRw 384,000 |
| 2 | The run's page: `PMT-` and `ALC-` links, settled / discount / paid, *Instruction file*, three advices *Ready* |
| 3 | The AP document: *Paid in run PYR-000001*, and `payment_run_member` beside a disabled Reverse |
| 4 | The workspace: the bank's one line matched by the payment run rule to three ledger lines |
| 5 | FX revaluation, *Bank and cash accounts*: `1121 · Bank Account USD`, open $ 500.00, carrying 660,000, revalued 675,000, difference 15,000 |

**Looking at them found two defects**, which is what rule 13's screenshot is for. The *Side*
picker cut the new label to "Bank and cash a…", and the rates read `1350.0000000000`. Both are
fixed and the shots re-taken.

## Carried forward

* Nothing from step 7. Step 8 builds the Cashbooks and Bank reconciliation reports and the Bank
  account enquiry on GETs that already exist.
* The FX revaluation **report** (P7 step 8's screen) is untouched here, as the prompt directed.
  Its bank lines are **step 8's**: the prompt's step-8 paragraph says "The **FX revaluation
  report** (P7 step 8) gains the bank lines."

```
$ git status --short
$ git log @{u}..
```

Both empty at the head this report was committed on.
