# P8 step 7a — Bank statements and the reconciliation workspace

Step 7 is split across two sessions. **This is part 1 of 2:** Bank statements and Bank
reconciliation (Appendix C.1.14). Payment runs, the AP document's "Paid in run", and the FX
revaluation screen's bank lines are part 2, in their own session, and none of them is started
here. Not a gate. The changed specs and the guard tests were run locally, `git diff --stat` is
quoted below before any count, and CI is the record for the branch head.

## What landed

```
$ git diff --stat origin/main..HEAD
 backend/app/api/v1/banking.py                      |  75 ++-
 backend/app/banking/matching.py                    |  36 +-
 backend/app/banking/reconciliation.py              |   7 +
 backend/app/schemas/banking.py                     |   5 +
 backend/tests/banking/test_api.py                  | 120 ++++
 backend/tests/test_api_has_a_caller.py             |  63 +-
 docs/Vinea_ERP_Master_Plan_v5.md                   |  34 +-
 .../3-bank-account-currency-locked-dark.png        | Bin 146310 -> 153118 bytes
 .../3-bank-account-currency-locked-light.png       | Bin 147469 -> 154626 bytes
 .../p8-step-7/7a-1-import-preview-error-{dark,light}.png        (new)
 .../p8-step-7/7a-2-statement-detail-{dark,light}.png            (new)
 .../p8-step-7/7a-3-workspace-mid-match-{dark,light}.png         (new)
 .../p8-step-7/7a-4-drawer-prefilled-by-rule-{dark,light}.png    (new)
 .../p8-step-7/7a-5-lock-refused-difference-{dark,light}.png     (new)
 .../p8-step-7/7a-6-locked-at-zero-{dark,light}.png              (new)
 .../p8-step-7/7a-7-bank-accounts-currency-hint-{dark,light}.png (new)
 docs/screenshots/p8-step-7/README.md               |  36 +
 frontend/e2e/p8-maintenance.spec.ts                |   8 +-
 frontend/e2e/p8-transactions.spec.ts               | 525 +++++++++++++++
 frontend/scripts/capture-p8-transactions.ts        | 257 +++++++
 .../app/(shell)/bank/reconciliations/[id]/page.tsx |   6 +
 .../src/app/(shell)/bank/reconciliations/page.tsx  |  11 +
 .../src/app/(shell)/bank/statements/[id]/page.tsx  |   6 +
 frontend/src/app/(shell)/bank/statements/page.tsx  |  12 +
 .../design/components/appendix-c-order.test.tsx    |  12 +
 frontend/src/design/components/input.tsx           |  16 +
 frontend/src/design/components/line-grid.tsx       |   9 +
 frontend/src/design/nav-tree.ts                    |  18 +
 frontend/src/features/banking/account-picker.tsx   |  72 ++
 .../src/features/banking/bank-accounts-screen.tsx  |  10 +-
 frontend/src/features/banking/hooks.ts             | 305 +++++++++
 frontend/src/features/banking/match-state.tsx      |  46 ++
 .../src/features/banking/post-from-line-drawer.tsx | 355 ++++++++++
 .../features/banking/reconciliation-workspace.tsx  | 742 +++++++++++++++++++++
 .../features/banking/reconciliations-screen.tsx    | 300 +++++++++
 .../features/banking/statement-detail-screen.tsx   | 227 +++++++
 .../features/banking/statement-format-editor.tsx   | 109 +--
 .../features/banking/statement-preview-result.tsx  | 135 ++++
 .../src/features/banking/statements-screen.tsx     | 549 +++++++++++++++
 frontend/src/features/banking/types.ts             | 215 ++++++
 frontend/src/i18n/messages/en.json                 | 236 ++++++-
 48 files changed, 4375 insertions(+), 182 deletions(-)
# (plus this report; the fourteen new PNGs are condensed to seven lines above)
```

### Bank statements — `/bank/statements` (Transactions → General Ledger, after FX revaluation)

* **The list per account** — number, source (Import / Paper), from, to, opening, closing, lines,
  skipped, status. It sits behind `QueryState`, and *Show void statements* asks the server for
  the void rows. The account filter covers **`bank`-kind accounts only**, because a cash account
  has no statement and offering it would mean offering a refusal. The account is carried in
  `?account=`.
* **Import**: file picker → **Preview** → the parsed rows, the derived opening and closing, the
  counts (`N lines · N new · N already held`), every error by row and column → **Import
  statement**. The button is refused *before* it is pressed, with the reason beside it: rows that
  cannot be read, the file already imported, or (for a format with no balance column) the two
  balances not yet keyed. The result reads **"N new, M skipped"**, with links to the statement
  and to its reconciliation.
* **Key a paper statement**: opening and closing balances, then one row per line (value date,
  description, reference, money in *or* money out), with a running *Lines: n · net* total. Keyed
  lines go to the same table and follow the same path as an import.
* **Statement detail** — `/bank/statements/{id}`: the header facts, each line with its match state
  (*Unmatched* / *Matched · rule* with its ledger-line count / *Locked in BRC-n*), and **Void**.
  While any line is matched, `statement_has_matches` is shown beside the disabled button with the
  count.

### Bank reconciliation — `/bank/reconciliations` (the row after Bank statements)

* **The list per account**: number, date, status, statement balance, and the stored ledger /
  outstanding / difference once the reconciliation is locked. An open one says *Live on the
  workspace* rather than computing the figures a second time. The header shows the account's
  *Last reconciled date at balance*.
* **New**: bank account, date, and statement balance. The balance is **defaulted from the balance
  column** by a new GET (see Backend) that calls the server's own fallback function, so the dialog
  shows the figure an empty field would get. `reconciliation_open_exists` and
  `reconciliation_date_order` are shown before the button.

### The workspace — `/bank/reconciliations/{id}`

* **The figures strip**: statement balance (editable while open, so it can be re-keyed at
  lock), ledger balance, outstanding (with its line count), adjusted bank balance, unmatched
  statement lines, and difference. The figures are live while the reconciliation is open and the
  stored ones once it is locked.
* **Statement pane**: unmatched lines first, then matched lines, each with what it is matched
  to. **Ledger pane**: outstanding lines first, then matched lines, with late lines flagged
  *dated inside BRC-n*. Each entry links to the GL entry page.
* **Auto-match**, with the result read back as *n matched, m left for a person to choose*.
* **Select on both sides → Match.** The selection's two totals and its balance (*Selection
  balances* / *Selection is out by FRw 15,000*) are shown beside the button before it is
  pressed. `match_unbalanced` renders inline in the screen's own words, with the figure in the
  account's currency, because the server's message carries a raw `NUMERIC(20,6)`.
* **Tick** on an outstanding ledger line; **Unmatch** on either pane. On a locked match, Unmatch
  is disabled with *Locked in BRC-n. Reopen it to unmatch.*
* **Post from line** opens a drawer with two tabs:
  * **Cashbook entry** is the P3 cashbook batch's own `LineGrid` in `cashbook` mode with one row
    (`maxRows={1}`). It is prefilled from `GET …/prefill`: the rule's account, tax code and
    description, or the Banking default (`bank_charges_account_id` on a debit,
    `bank_interest_account_id` on a credit) where no rule matches. A note says which of the two
    it was. The amount is **fixed**: a new `fixedAmount` prop makes the cell read-only and
    disables *tax inclusive*, because the server posts the statement line's own amount and an
    edit would be discarded.
  * **Customer receipt / Supplier payment** follows the line's sign: pick the partner, and the
    settlement is posted unallocated.
* **Lock**: both refusals are listed in red beside the disabled button, each with its figure
  (*Statement lines in no match: n.*, *The difference is FRw -500. A reconciliation locks at
  zero.*). When neither applies, a green *Every statement line is matched and the difference is
  zero.*
* **Reopen**: drawn on a locked reconciliation. It is refused before the button when this is not
  the account's latest locked one (`reconciliation_not_latest`) or when another one is open.
  Pressing it opens a dialog that asks for a reason.

**The buttons follow the permissions, not just the grants.** Each button is drawn only when the
user holds the permission that presses it. **Post from line** needs `bank:reconcile` *and* the
posting's own permission: `gl:journal_post` for the cashbook tab, `ar:transactions_post` on a
credit line or `ap:transactions_post` on a debit line for the settlement tab. The button is drawn
only when at least one tab is permitted, and a tab without its permission says which permission
is missing. The read-only member (Clerk, `bank:reports_view`) sees both listings, the workspace
and its figures, and **no button at all**. The spec asserts this for all seven workspace actions.

**`Idempotency-Key` comes from a draft UUID** minted when each dialog or drawer opens and
re-minted after a success. It covers import (re-minted when a different file is picked, so the
key is never offered for a second request), paper keying, open, lock, reopen, and both drawer
posts.

### Bank accounts — the currency lock is a hint now

`Field` gains a `hint` slot, rendered in the subtle ink and only when there is no error. The
locked-currency reason (`bank_account_has_lines`) moves there from the red error slot. The error
slot stays for a server refusal on save and outranks the hint. `p8-maintenance.spec.ts` now reads
the reason off `[data-field-hint]`. Step 6's shot 3 is re-taken.

### One preview rendering, not two

The format editor's *Test with a file* result is now `StatementPreviewResult`, and Import's
preview uses the same component: same endpoint, same file, one rendering. It is a refactor in
place (rule 9): the editor lost 109 lines and gained one import.

### Backend (three additions, all driven by the screens)

| change | why |
|---|---|
| `GET /banking/accounts/{id}/statement-lines?on_or_before=` — live lines with match state, unmatched first | The workspace's left pane. `unmatched-statement-lines` shows only what is left to do and cannot say what a matched line is matched *to*. `statement_line_states` was refactored in place into one query over a condition, shared by the statement detail and the account listing. |
| `GET /banking/accounts/{id}/default-statement-balance?on=` | The value *New* fills in. It calls `reconciliation.default_statement_balance`, the function `open_reconciliation` falls back to. |
| `POST …/reconciliations/{id}/reopen` takes `Idempotency-Key` | Decision 11 names reopen with open and lock, and it was the one of the three without a key. A replay returns the reopened row instead of refusing `reconciliation_not_locked` over it. The test was proven sensitive by deleting the replay branch (the test failed) and restored byte for byte. |

Neither GET is the register's business. Tests: `test_reopen_needs_an_idempotency_key`,
`test_the_left_pane_lists_unmatched_lines_first_with_their_match`,
`test_the_new_dialog_reads_the_balance_an_empty_field_would_get`, and the reopen replay added to
`test_the_workspace_ticks_locks_and_reopens_over_http`.

## Rule 14 — the register

**The twelve `GAP (P8, step 7)` lines for statements and the workspace are gone.** Each one is
deleted by a button in the product, and each button is pressed by `e2e/p8-transactions.spec.ts`:

| endpoint | the button the spec presses |
|---|---|
| `POST /banking/statements` | **Import statement** on Import's preview |
| `POST /banking/statements/manual` | **Save statement** on *Key a paper statement* |
| `POST /banking/statements/{id}/void` | **Void** in the statement's Void dialog |
| `POST /banking/reconciliations` | **Open reconciliation** in *New reconciliation* |
| `POST /banking/accounts/{id}/auto-match` | **Auto-match** |
| `POST /banking/matches` | **Match** (refused, then accepted) |
| `POST /banking/matches/tick` | **Tick** on the cheque |
| `DELETE /banking/matches/{id}` | **Unmatch** (the tick, then all five before the void) |
| `POST /banking/statement-lines/{id}/post-cashbook` | **Post cashbook entry** in the drawer |
| `POST /banking/statement-lines/{id}/post-settlement` | **Post receipt** in the drawer |
| `POST /banking/reconciliations/{id}/lock` | **Lock** |
| `POST /banking/reconciliations/{id}/reopen` | **Reopen reconciliation** in the reason dialog |

**The three payment-run lines stay**, deliberately: `POST /banking/payment-runs/preview`,
`POST /banking/payment-runs` and `POST /banking/payment-runs/{id}/reverse`. They belong to
step 7's second half, and the register's comment now says so. The register was proven sensitive:
renaming the auto-match call site in `hooks.ts` fails the test on exactly that endpoint, and the
file was then restored.

## Appendix C — C.1.14

**Bank statements** and **Bank reconciliation** sit under Transactions → General Ledger,
directly after FX revaluation. They are recorded in `nav-tree.ts`, in `appendix-c-order.test.tsx`
(the two rows and the amendment note) and in the Master Plan (the Appendix C table's
Transactions → GL row and a new **C.1.14** entry). This is C.2's promise made good: "bank
statement import & reconciliation workspace as transactions, not just reports".

The owner's own *Bank reconciliation* row under Reports → General Ledger keeps its place and its
`P8` tag until step 8 builds that report. The two rows share a label but not a screen, as the two
"Fiscal receipts" rows do. Payment runs are C.1.15, in part 2. Nav gating follows the API: both
listings are `bank:reports_view`, and any of the rows' write permissions also opens the row.

## The spec — `frontend/e2e/p8-transactions.spec.ts`

**CI placement:** the file lands in the `rest-*` shards through the path filter, and
`ci-e2e-groups.test.ts` confirms it is in exactly one group. Setup runs on Rugari, on a bank
account the spec creates for itself (suffixed code, generic format, an `ACCOUNT FEE` rule to
`6700`), with a customer of its own and four cashbook entries. The statement CSV is **generated
by the spec from the entries it just posted**. There is no committed sample and no fixed year:
every date is `todayIso()`.

The walk, as the prompt orders it:

1. **Import preview with one error row.** The third data row's date is broken, so row 4 is
   reported against `date_column`, and *Import statement* is refused with *Rows that cannot be
   read: 1.* **The fixed file** reads cleanly — `5 lines · 5 new · 0 already held`, opening
   `FRw 0`, closing `FRw 1,215,500` — and imports as **"5 new, 0 skipped"**. The same file again
   is refused before the button (`0 new · 5 already held`, *already imported*).
2. **Statement detail**: `0 of 5` matched, and the fee line reads `FRw -2,500`.
3. **New** defaults the balance to `1215500`. The strip starts at ledger `FRw 970,000`,
   outstanding `FRw 970,000`, five lines unmatched, difference `FRw 1,215,500`. The receipt
   behind line 2 is then posted, and **Auto-match** reads *2 matched, 0 left*: `OPENING DEPOSIT`
   by amount and date and `TRANSFER DEP…` by reference, both read off the panes with what each is
   matched to. The strip moves to difference `FRw 97,500` (= 40,000 + 60,000 − 2,500, exactly
   what the ledger lacks). **Tick** on the cheque takes it to `FRw 167,500`, and **Unmatch**
   puts it back.
4. **Manual match, refused then n:m**: the bulk deposit against one ledger line reads *Selection
   is out by FRw 15,000*, and Match is refused inline with that figure. Adding the second ledger
   line gives *Selection balances*, and the match is made by hand over two ledger lines.
5. **The fee posted from its line by the rule**: the drawer is prefilled with
   `6700 · Bank Charges`, the amount `2,500` is read-only, and *CB-n posted and matched*. **The
   receipt posted from its line**: tab *Customer receipt*, the customer picked, *RCT-n posted and
   matched*. Unmatched `0`, ledger `FRw 1,145,500`, outstanding `FRw -70,000`, difference `FRw 0`.
6. **Lock refused with the difference shown**: re-keying `1215000` gives *The difference is
   FRw -500…* and a disabled Lock. **Locked at zero** at `1215500`, and the account's
   `last_reconciled_balance` reads 1215500. **Unmatch refused**: disabled on the locked match,
   with its title. A payment dated inside the period but posted after the lock is flagged
   *dated inside BRC-n*, and the stored ledger figure does not move.
7. **The read-only member** sees both listings (`Reconciliations: 1 · locked: 1`) and the
   workspace figures. None of Reopen, Lock, Unmatch, Tick, Post from line, Auto-match, Match,
   Import or *Key a paper statement* is drawn.
8. **Reopen** with a reason. The late line is now outstanding (`FRw -71,000`). **Void refused**:
   *Lines in a match: 5.* beside a disabled button. **After unmatching** all five, void is
   allowed. The statement leaves the listing and comes back under *Show void statements* marked
   Void.
9. **A paper statement keyed line by line** on a second account of the spec's own: `Lines: 2 ·
   net FRw 7,500`, then "2 new, 0 skipped".

Every screen asserts at least one formatted money value and one formatted quantity read off the
page. Every label lookup is `{ exact: true }`.

## Labels — `{ exact: true }` and the loose-lookup scan

Every string this step adds was split on its placeholders into fixed fragments: 214 keys, 201
fragments, plus the two nav labels, which render in the sidebar on every page. Each fragment was
scanned against every `getByLabel` / `getByText` / `getByPlaceholder` / `getByTitle` / `name:`
in `e2e/` that lacks `exact: true`, as a case-insensitive substring (Playwright's default).

* **The nav labels**: no hit.
* **The en.json fragments**: 90 hits, and none is a collision, because every hit is a lookup on
  a screen that never renders the new string. The new strings render only under `/bank/*`, in the
  drawer, and in the Bank accounts hint. The hits were `"Posted"` (AR/AP and GL posting toasts),
  `"Reason"` (the reversal dialogs), `"Reference"` / `"Description"` (batch and inventory grids),
  `"Post receipt"` (the AR receipt screen in p6), and `"Ready"` (the AR statement job).
  `p7-cycle-tape`'s `"To"` is really an anchored regex, `/^To$/`.
* **The spec's own**: two loose `getByRole("dialog", { name: "Post from a statement line" })` in
  the new spec itself were made exact.

Regex lookups mentioning statement, reconcile, bank, import, match, lock, void, reopen or tick
were read by hand. Only `/Queue statement/` (AR statements) and the spec's own two toast regexes
turned up, and none can match a new label. The specs were also run, which is the check that
actually settles it.

## Decisions worth review

1. **Import does not auto-match; the workspace's Auto-match does.** Decision 4 says auto-match
   runs "on import, and on demand". The step-5 backend runs it only on demand: the import endpoint
   does not call `auto_match`, and the acceptance tape calls it explicitly after each import. This
   step builds screens, so I did not change the import service underneath the tape. The import
   result links to the reconciliation, where Auto-match is one press. If the owner wants
   import-time matching, it is a small backend change: call `auto_match` inside `import_statement`
   and adjust the tape's explicit calls, which would then find nothing left to match. It needs its
   own decision because it changes what an import writes.
2. **Blocked actions are disabled, with the reason beside them**, not pressable-then-refused.
   This follows the AR/AP document detail's Reverse (`document-detail-screen.tsx`). It applies to
   Import, Void, New, Lock, Reopen and Unmatch on a locked match. **Match is the exception**: it
   stays pressable on an unbalanced selection so that `match_unbalanced` is the server's refusal
   rendered inline, as the prompt asks. The selection's balance is still shown beforehand.
3. **The statement balance is re-keyed on the strip, not in the lock dialog.** Lock takes an
   optional `statement_balance` (tape row 9 corrects a wrong balance that way). The strip's field
   recomputes the difference locally, rounded half-up to the currency's decimals, while an edit is
   pending, and the lock refusal is shown from that local figure. The server recomputes on lock
   and remains the authority.
4. **The drawer's cashbook tab posts one description** (the grid row's) as both the entry's and
   the line's, because `PostCashbookFromLine` carries one. The header holds the entry date and the
   reference.
5. **The drawer is the P3 cashbook grid; the settlement tab is not the AR/AP settlement form.**
   The settlement tab posts through `post-settlement` with a partner, date, reference and
   description, all an unallocated settlement needs. Reusing the full P4 document screen would
   have brought allocation into a drawer whose premise is "unallocated". So the AR/AP specs are
   not in the touched set.
6. **The panes stack below the `2xl` breakpoint.** Side by side at 1440px, each pane was too
   narrow to hold `FRw 1,000,000` on one line (found in the first screenshot). Above 2xl they sit
   side by side.
7. **Reopen's replay shares `bank_reconciliations.idempotency_key` with open and lock**, as lock
   already did. The row therefore carries the key of whichever of the three came last. That is
   enough for the replay of the call that just happened, which is what a dropped response needs.

## Verification

Backend, in the container:

* `make be-lint` — All checks passed.
* `--collect-only -q tests/banking tests/test_api_has_a_caller.py tests/test_api_enums_export.py`
  collected **269**. The same paths run with `-n 4`: **269 passed** (3m49s).

Frontend:

* `npm run typecheck` clean. `npm run lint` has no finding in a touched file; the five warnings
  are in `journal-batches/new/page.tsx`, `ebm-devices/page.tsx` and `allocation-screen.tsx`, all
  on `main`.
* `vitest run` — **425 passed** (16 files), including `appendix-c-order`, `ci-e2e-groups` and
  `no-utc-dates`.
* `playwright test --list` — **275 tests in 33 files**. The new spec lists 10.

**The touched-spec set was derived from `git diff --stat`, the step-6 way:**

| diff entry | spec |
|---|---|
| `/bank/*` pages and `features/banking/*` screens | `p8-transactions` |
| `bank-accounts-screen`, `statement-format-editor`, `statement-preview-result` | `p8-maintenance` |
| `line-grid.tsx` (`fixedAmount`, `cashbook` mode only; the only other `mode="cashbook"` caller is the cashbook batch) | `gl-cashbook` |
| `nav-tree.ts` | `accessibility-transactions` (sweeps the two new routes), `accessibility-dashboard` (sidebar and palette) |
| `input.tsx` (`Field.hint`) | set only on the two banking screens above; everywhere else the render is unchanged |
| AR/AP settlement form | not reused (decision 5), so no spec |

e2e ran on a database reset by `make db-reset` (with the `docker-compose.e2e.yml` overlay), after
100 routes had been warmed with curl — every live nav route including `/`, plus `/login` and the
two detail routes (0 non-200):

| run | specs | result |
|---|---|---|
| the new spec, first run | `p8-transactions` | **failed**: expected `RWF 0`, the seeded symbol is `FRw` (a spec error) |
| second | `p8-transactions` | **failed**, a product defect: *Show void statements* filtered a list the server had already emptied of void rows. The hook now sends `include_void` (`a070679`) |
| third | `p8-transactions` | 10 passed |
| **the record: every touched spec, one run, fresh reset** | `p8-transactions`, `p8-maintenance`, `gl-cashbook` | **21 passed** (1.6m, 1 worker) |
| the axe sweeps the nav change reaches | `accessibility-transactions`, `accessibility-dashboard`, `--workers=4` as CI runs them | **36 passed** (1.4m), including *General Ledger › Bank statements (/bank/statements)* and *› Bank reconciliation (/bank/reconciliations)*, light and dark |

The screenshot fixes (`c3b97d5`: stacked panes, the non-breaking chip) came after the first
`p8-transactions` passes and before the record run, so the record covers them.

## Screenshots

In `docs/screenshots/p8-step-7/`, prefixed `7a-`, light and dark, captured by
`frontend/scripts/capture-p8-transactions.ts` on a reset database. The README there says what
each shot is. Every one was opened and looked at before commit.

| # | shot |
|---|---|
| 1 | Import preview with an error row: row 4, `date_column`, *Import statement* refused |
| 2 | Statement detail: three of five matched (amount and date, reference, by hand over two), Void refused with the count |
| 3 | Workspace mid-match: *Selection is out by FRw 15,000*, difference FRw 97,500, both lock refusals listed |
| 4 | The fee's drawer, prefilled by the rule: `6700 · Bank Charges`, amount fixed at 2,500 |
| 5 | Lock refused: *The difference is FRw -500* |
| 6 | Locked at zero: FRw 1,215,500 / 1,145,500 / -70,000, every match *Locked in BRC-000001* |
| 7 | Bank accounts: the currency lock as a neutral hint |

Step 6's shot 3 (`p8-step-6/3-bank-account-currency-locked-*`) is re-taken for the same hint.

**Looking at them found two defects**, which is what rule 13's screenshot is for. Side by side at
1440px, the panes broke amounts across two lines (`FRw` / `1,000,000`). And the *Locked in
BRC-000001* chip broke the number itself (`BRC-` / `000001`), the same defect step 6 found on an
account number. Both were fixed and the shots re-taken. The capture script hides the toast layer
while it shoots, because Radix pauses a toast's timer while the page holds the pointer, so waiting
for it to clear was not reliable.

## Carried to step 7b

* The three `GAP (P8, step 7)` payment-run lines: Payment runs (`/ap/payment-runs`, C.1.15),
  the AP document's "Paid in run PYR-n" with `payment_run_member` before Reverse, and the FX
  revaluation screen's `bank` / `all` roles with the bank lines.
* Decision 1 above (auto-match on import), if the owner wants it.

```
$ git status --short
$ git log @{u}..
```

Both empty at the head this report was committed on.
