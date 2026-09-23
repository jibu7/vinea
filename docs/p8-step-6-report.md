# P8 step 6 — Bank accounts, formats, rules and the supplier bank details

Step 6 is the first of P8's three UI steps and it is **not a gate**: the changed specs and the
guard tests were run locally, `git diff --stat` is quoted below before any count, and CI is the
record for the branch head.

## Where the work came from

**The first three commits on this branch came from an earlier session and were reviewed
here, not written here.** This session opened on a local `p8-step-6` already three commits
past the step-5 merge (`b3167e3`), never pushed, with `frontend/e2e/p8-maintenance.spec.ts`
untracked beside them:

| commit | what it says it does |
|---|---|
| `094da20` | feat(bank): create the GL account and its master in one call, and try a mapping before saving it |
| `58f9571` | feat(ap): the supplier's bank details replace as one form |
| `1847cce` | feat(bank): Bank accounts, the format editor and rules; Defaults, Suppliers and the chart |

They were pushed as found, the spec was committed as found (`cfc86d9`), and all four were then
treated as unreviewed. The review is what the rest of this report is. It found the three
commits sound and the spec **not**: the spec had never passed (see *What the review changed*).

## What landed

```
$ git diff --stat origin/main..HEAD
 backend/app/api/v1/banking.py                                   |  68 ++-
 backend/app/api/v1/subledger.py                                 |   9 +-
 backend/app/banking/accounts.py                                 |  75 +++-
 backend/app/schemas/banking.py                                  |  20 +-
 backend/app/schemas/subledger.py                                |   3 +-
 backend/app/scripts/seed_e2e.py                                 |  56 +++
 backend/tests/banking/test_api.py                               | 234 +++++++++++
 backend/tests/test_api_has_a_caller.py                          |  53 +--
 docs/Vinea_ERP_Master_Plan_v5.md                                |  26 +-
 docs/screenshots/p8-step-6/1-bank-accounts-dark.png             | Bin 0 -> 108907 bytes
 docs/screenshots/p8-step-6/1-bank-accounts-light.png            | Bin 0 -> 109617 bytes
 docs/screenshots/p8-step-6/2-bank-account-new-dark.png          | Bin 0 -> 120040 bytes
 docs/screenshots/p8-step-6/2-bank-account-new-light.png         | Bin 0 -> 121431 bytes
 .../p8-step-6/3-bank-account-currency-locked-dark.png           | Bin 0 -> 146310 bytes
 .../p8-step-6/3-bank-account-currency-locked-light.png          | Bin 0 -> 147469 bytes
 docs/screenshots/p8-step-6/4-statement-format-test-dark.png     | Bin 0 -> 175632 bytes
 docs/screenshots/p8-step-6/4-statement-format-test-light.png    | Bin 0 -> 176933 bytes
 docs/screenshots/p8-step-6/5-bank-rules-dark.png                | Bin 0 -> 143211 bytes
 docs/screenshots/p8-step-6/5-bank-rules-light.png               | Bin 0 -> 144301 bytes
 docs/screenshots/p8-step-6/6-supplier-bank-details-dark.png     | Bin 0 -> 155028 bytes
 docs/screenshots/p8-step-6/6-supplier-bank-details-light.png    | Bin 0 -> 156233 bytes
 docs/screenshots/p8-step-6/7-gl-defaults-banking-dark.png       | Bin 0 -> 100527 bytes
 docs/screenshots/p8-step-6/7-gl-defaults-banking-light.png      | Bin 0 -> 101525 bytes
 docs/screenshots/p8-step-6/8-chart-bank-row-dark.png            | Bin 0 -> 159264 bytes
 docs/screenshots/p8-step-6/8-chart-bank-row-light.png           | Bin 0 -> 159606 bytes
 docs/screenshots/p8-step-6/README.md                            |  52 +++
 frontend/e2e/p8-maintenance.spec.ts                             | 418 +++++++++++++++++++
 frontend/scripts/capture-p8-maintenance.ts                      | 275 ++++++++++++
 frontend/src/app/(shell)/maintenance/bank-accounts/page.tsx     |   7 +
 frontend/src/app/(shell)/maintenance/chart-of-accounts/page.tsx |  79 +++-
 frontend/src/app/(shell)/maintenance/defaults/page.tsx          |  36 +-
 frontend/src/design/components/appendix-c-order.test.tsx        |  16 +
 frontend/src/design/nav-tree.ts                                 |   9 +
 frontend/src/features/banking/bank-accounts-screen.tsx          | 620 ++++++++++++++++++++++++++++
 frontend/src/features/banking/bank-rules-panel.tsx              | 275 ++++++++++++
 frontend/src/features/banking/hooks.ts                          | 114 +++++
 frontend/src/features/banking/statement-format-editor.tsx       | 499 ++++++++++++++++++++++
 frontend/src/features/banking/types.ts                          | 144 +++++++
 frontend/src/features/gl/hooks.ts                               |   7 +-
 frontend/src/features/gl/types.ts                               |   6 +
 frontend/src/features/subledger/partners-screen.tsx             |  74 +++-
 frontend/src/features/subledger/types.ts                        |   9 +
 frontend/src/i18n/messages/en.json                              | 181 +++++++-
 frontend/src/lib/api.ts                                         |  15 +-
 44 files changed, 3311 insertions(+), 69 deletions(-)
# (plus this report)
```

**One new screen.** **Bank accounts**, Maintenance → General Ledger, directly after Defaults
(`/maintenance/bank-accounts`):

* **the list** — code, name, GL account, kind, currency, bank, account number, last reconciled
  date and balance, active; *Show inactive*; *Deactivate* / *Activate* per row;
* **New bank account** — the GL account *and* its master row in one call
  (`POST /banking/accounts` with `new_account`), class asset, postable, the control type taken
  from the kind; created through the kernel's own `create_account`, so the chart's rules are
  the chart's and a refusal on the banking half leaves no orphan GL account
  (`test_create_is_all_or_nothing`). Because it writes a chart row, it takes `gl:setup_manage`
  as well as `bank:setup_manage` — a custom role holding only the second must not get a way
  round the first (`test_create_needs_the_chart_permission_as_well`);
* **Unregistered bank and cash accounts**, with **Register** — the empty state on every
  healthy tenant, and said so rather than hidden;
* the drawer's **Details** — bank name, account number, holder, branch, SWIFT/BIC, and the
  currency, **locked once the account has lines**: `BankAccountRead.has_lines` (computed from
  `journal_lines`, never stored — a column holding the answer would be a copy of the ledger)
  turns the picker into the fact, with the reason, instead of a picker that `bank_account_has_lines`
  would refuse on save;
* the drawer's **Statement format** — preset picker, the column mapping, and **Test with a
  file**, which posts the file *and the mapping on the screen* to
  `POST /banking/statements/preview` (new optional `statement_format` form field) and shows the
  rows it would import, the counts, the opening and closing balance, and the errors by row. It
  writes nothing — not the statement, not the mapping it was asked to try;
* the drawer's **Rules** — the list per account, New rule, edit, deactivate.

**Three existing screens gained a section:**

| screen | what it gained |
|---|---|
| Suppliers | **Bank details** — beneficiary bank, account number, name. Sent as one fact (`clear_bank_details` *with* the values replaces all three), so a field blanked on the screen is blank on the next read rather than coming back |
| GL Defaults | **Banking** — `bank_revaluation_account_id` (assets), `bank_charges_account_id` and `bank_interest_account_id` (income and expense) |
| Chart of accounts | creating a `bank` / `cash` control account shows **the row it created** — code, name, kind, currency — with a link to Bank accounts |

**The e2e fixture** (`seed_e2e.py`): Rugari Wines E2E holds `1121 Bank Account USD` (Bank of
Kigali, `00040-0000999-11`) beside `1120`, made through the same `register(new_account=…)` call
*New* makes, in USD from its first moment; Kivu Traders holds only the seeded pair.

## The pickers exclude control accounts — pinned by a typeahead assertion

Every bank and cash account is an asset **and** a control account, which is exactly why the
Bank revaluation picker (assets) is the one that matters: `1120` and `1121` are what decision 8
says a revaluation must never touch. The e2e opens it and asserts both halves, the P7 way:
`1120 · Bank Account`, `1121 · Bank Account USD` and `1200 · Accounts Receivable` absent,
`1130 · Bank Revaluation` present — the first half alone would pass over an empty picker. Bank
charges is asserted the same way (`1110 · Cash on Hand` absent, `6700 · Bank Charges` present).

## Rule 14 — the register

**The four `GAP (P8, step 6)` lines are gone**, each deleted by a screen a person can open and
press, and each pressed by `e2e/p8-maintenance.spec.ts`:

| endpoint | the button |
|---|---|
| `POST /banking/accounts` | **Create** on *New bank account*; **Register** on the unregistered list |
| `PATCH /banking/accounts/{id}` | **Save details**; **Save format**; *Deactivate* / *Activate* on a row |
| `POST /banking/accounts/{id}/rules` | **Save** in the *New rule* dialog |
| `PATCH /banking/rules/{id}` | **Save** in *Edit rule*; *Deactivate rule* |

Checked by tracing each hook in `features/banking/hooks.ts` to the handler that calls it and
the `onClick` that calls the handler — no hook exists without a button behind it.

**Register is the one button no test presses with a row in front of it, and that is because
no row can exist.** The only path that makes a `bank` / `cash` control account is
`POST /gl/accounts`, which calls `ensure_row` in the same transaction; `GLAccountUpdate` has
no `control_type`, so an edit cannot make one; and step 1's back-fill registered every account
that predates P8. The list is therefore empty on every tenant, the e2e asserts the empty
state's text, and `POST /banking/accounts` is held in place by *Create*, which the e2e does
press. Register stays as the recovery path the prompt names, for a tenant that a future path
leaves inconsistent — `assert_bank_invariants` clause 6 is what would find that tenant.

**A fifth line went too**, and it was down for step 7: `POST /banking/statements/preview`.
*Test with a file* is its first caller, and a `GAP` line beside a button that calls the
endpoint would be the register lying. Step 7's Import will be its second. The register now
carries **fifteen** P8 lines, all `GAP (P8, step 7)`: the three left on Bank statements (the
confirm half of Import, manual keying, Void), the nine on the reconciliation workspace and the
three on payment runs.

`api-enums.ts` is not in the diff: every banking enum the screens use (`BankAccountKind`,
`StatementFormatPreset`, `StatementAmountMode`, …) was generated at step 1, and
`tests/test_api_enums_export.py` passes against it. Nothing here types a wire value.

## Appendix C

Bank accounts is **C.1.13**, not in the owner's tree: in
`frontend/src/design/components/appendix-c-order.test.tsx` (the row after Defaults, and the
amendment note) and in the Master Plan — the Appendix C table's Maintenance → General Ledger
row and a new C.1.13 entry saying why the row exists and what is on the screen. The rest of
the step is sections on screens the tree already carries and needs no row. Transactions → GL's
Bank statements and Bank reconciliation are left for step 7 as C.1.14.

## Labels — `{ exact: true }` and the loose-lookup scan

Every label lookup in the new spec is `{ exact: true }`. The strings this step added to
screens that already existed — on Defaults *Banking*, *Bank revaluation*, *Bank charges*,
*Bank interest*; on Chart of accounts *Bank account code*, *Bank account name*, *Kind*,
*Held in*, *Open Bank accounts*, *Dismiss*; on Suppliers *Bank details*, *Beneficiary bank*,
*Beneficiary account number*, *Beneficiary name* — plus every string on the new screen, were
scanned against every `getByLabel` / `getByText` / `getByPlaceholder` / `name:` in `e2e/` that
lacks `exact: true`, as a case-insensitive substring (Playwright's default). Five hits outside
the new spec, none a collision:

| lookup | why it is not one |
|---|---|
| `p7-maintenance.spec.ts:352` `getByLabel("Name")` | the Units of measure dialog, which gained nothing |
| `p6-maintenance.spec.ts:209` tab `"Details"` | the Items drawer's tablist |
| `p7-cycle-tape.spec.ts:829` `"To"` | already an anchored regex, `/^To$/` |
| `ar-ap-batches.spec.ts:37` `"Reference"`, `ar-ap-reports.spec.ts:131` `"Ready"` | screens this step did not touch |

Regex lookups in the specs that open Suppliers, Defaults or the chart were read by hand;
none can match a new label. And the specs were run, which is the check that actually closes
this: P7 step 6 is the reminder that a static scan cannot know which spec drives which screen.

## What the review changed

**The spec had never passed.** Two defects, both of the kind only running it finds:

1. *Kivu Traders holds only the seeded pair* signed in as `SECONDARY_EMAIL` and went straight
   to the screen. That user holds **two** memberships, so the session has no company until one
   is chosen, and the list rendered "Select a company for this session" over no rows. It now
   picks Kivu Traders through the header switcher, the way `p7-transactions.spec.ts` does.
2. *Chart of accounts shows the bank-account row* looked for an **"Account class"** label; the
   dialog's is **"Class"**. `{ exact: true }` did its job here — a loose lookup would have
   failed the same way, but for a reason nobody would read correctly.

**And one fixture was not put back.** The Suppliers test wrote bank details onto the seeded
supplier and left them, while the file's header said only the Defaults key was shared. Step 7's
payment run reads that supplier as having none (`bank_details_missing`), so the test now blanks
the three and reads them back as `null` from the API.

The three product commits needed no change. `role === "ap"` in `partners-screen.tsx` is a
comparison against the file's own `PartnerRole` union (and the file's existing idiom, line
162), so the type checker holds it.

## Verification

Backend, in the container:

* `make be-lint` — All checks passed.
* `--collect-only` over `tests/banking tests/subledger tests/test_api_has_a_caller.py
  tests/test_api_enums_export.py tests/test_seed_e2e.py tests/test_maintenance_api.py` —
  **463 collected**; the same paths run: **463 passed** (5m49s, `-n 4`).
  A first attempt named a `tests/gl` that does not exist and pytest printed only warnings —
  which is what the collect-first rule is for.

Frontend:

* `npm run typecheck` clean; `npm run lint` — no finding in a touched file (the two warnings
  are `allocation-screen.tsx`'s, pre-existing).
* `vitest run` — **413 passed** (16 files), including `appendix-c-order`, `api-enums`,
  `no-utc-dates` and `ci-e2e-groups` (the new spec lands in the `rest-*` shards by the path
  filter).
* `playwright test --list`: **263** tests in 32 files; the new spec lists 10.

e2e, on a database reset by `make db-reset` with the dev server warmed route by route
(including `/`, whose cold compile timed out the first login of the first attempt):

| run | specs | result |
|---|---|---|
| the new spec, alone, first | `p8-maintenance` | **failed** — the first login timed out on a cold `/`; then two spec defects (see *What the review changed*) |
| the new spec, after the fixes | `p8-maintenance` | 10 passed (34.0s) |
| **the record: every spec that opens a touched screen, one run, fresh reset** | `p8-maintenance`, `p7-maintenance` (Defaults), `ar-ap-maintenance`, `ar-ap-corrections`, `ar-ap-allocation`, `ar-ap-batches`, `ar-ap-documents`, `ar-ap-reports`, `ar-ap-acceptance`, `dated-rate` (Suppliers / Customers — one `partners-screen.tsx`) | **54 passed** (6.3m, 1 worker) |
| the axe sweeps the nav change reaches | `accessibility-maintenance`, `accessibility-dashboard`, `--workers=4` as CI runs them | **32 passed** (1.4m) — *General Ledger › Bank accounts (/maintenance/bank-accounts) — light and dark* among them |

`ar-ap-acceptance` is in the list because it opens Suppliers, not because anything it asserts
moved. The Customers screen renders through the same component and gained nothing (the section
is `role === "ap"` only), which the AR specs in that run confirm.

After the record run the only product change is `whitespace-nowrap` on one `<p>`
(`a5c571b`), found in screenshot 1; `tsc` was re-run over it.

## Screenshots

`docs/screenshots/p8-step-6/`, light and dark, captured by
`frontend/scripts/capture-p8-maintenance.ts` — see its README. Each was opened and looked at
before commit.

| # | shot |
|---|---|
| 1 | the list — `1110`, `1115`, `1120`, `1121` (USD, Bank of Kigali, `00040-0000999-11`) |
| 2 | *New bank account* |
| 3 | `1120`, currency locked with the reason |
| 4 | *Test with a file*: `2 lines · 2 new · 0 already held`, `$ 0.00` → `$ 495.00`, the two rows |
| 5 | Rules on `1121` |
| 6 | a supplier's Bank details, read back |
| 7 | GL Defaults' Banking block |
| 8 | Chart of accounts: `1115 · Petty Cash Musanze is registered as a bank account` |

**Looking at them found a defect**, which is what rule 13's screenshot is for: the account
number broke across two lines in the list (`00040-0000999-` / `11`), which on a number
somebody reads down a phone to a bank is not cosmetic. Fixed in `a5c571b` and shot 1 retaken.

**And one thing is left for review rather than changed.** The currency lock's reason is shown
through the field's error slot, so it reads red (shot 3). The prompt asks for
`bank_account_has_lines` *inline*, and the text is right; whether it should be a neutral hint
instead is a design call, not a defect.

## Carried to step 7

* `POST /banking/statements/preview` has its first caller; Import will be its second.
* The Banking defaults are what step 7's *Post from line* drawer reads for a fee or interest
  with no rule; the e2e restores `bank_interest_account_id` to `4300` for that reason.
* The seeded supplier holds no bank details, deliberately — the payment run's
  `bank_details_missing` warning needs a supplier to show it on.
