# P7 step 6 — EBM devices and the fiscal master fields

Step 6 is the first of P7's three UI steps and it is **not a gate**: changed specs plus the
guard tests were run locally, `git diff --stat` is quoted before any count, and CI is the
record for the branch head.

What it builds is the setup a person has to be able to do before anything can be fiscalized at
all. Steps 1–5 put five migrations' worth of columns on `tax_codes`, `uoms`, `items` and
`gl_settings`, and a device lifecycle behind five endpoints — and every one of those was
unreachable from the product. A company that did not come from the Rwanda seed pack had a VAT
return that refused to file and no screen to fix it on; a company that did could not register a
device at all.

## What landed

**One new screen.** **EBM devices**, Maintenance → Tax, after Tax types
(`/maintenance/ebm-devices`): the list per branch with status, environment, profile, `sdc_id`,
`mrc_no`, last success and the offline flag, plus Register, Initialize, Suspend and Sync codes.

**Five existing screens gained what step 1's migration had nowhere to set:**

| screen | what it gained | column |
|---|---|---|
| Tax types | the EBM class column (A/B/C/D) and its picker | `tax_codes.fiscal_tax_type` |
| Units of measure | the RRA quantity-unit code, a typeahead over the synced §4.6 list | `uoms.fiscal_quantity_unit` |
| Items | a **Fiscal** section: class code (typeahead over `fiscal_item_classes`), origin, packaging unit, product type, and the registration status with `item_cd` | the four `items.fiscal_*` columns |
| Customers / Suppliers | **Verify TIN**, with the authority's name and status beside the field | — (a lookup, nothing stored) |
| GL Defaults | the **Tax and revaluation** block: five accounts and the default purchase class code | the six `gl_settings` keys |

**And the backend half that made them writable.** The columns existed; the schemas and services
did not carry them. `TaxCodeCreate/Update/Read`, `UomCreate/Update/Read`,
`ItemCreate/Update/Read` and `GLSettingsRead/Update` each gained their fields, threaded through
`kernel/masters.py`, `inventory/masters.py` and the two API modules, with the `clear_*`
convention P4 set so a PATCH that omits a field never silently blanks it.

## Initialize is the only activation path

Step 1's decision, and the screen carries it rather than working around it: **there is no
Activate button**, and a suspended device is brought back by **re-initializing** it. The row's
button reads *Initialize* on a pending device and *Re-initialize* on a suspended one, and the
e2e asserts there is no control named "Activate" anywhere on the page.

That is not a UI preference. `activate()` in `app/fiscal/devices.py` refuses a device with no
`sdc_id` (`fiscal_device_not_initialized`), because activation *is* the storing of what the
authority sent back. A separate Activate would be a button that could only ever fail on the
case it existed for — and re-initializing is also how a device's keys are reissued, which is
the thing an operator actually wants when a device has been suspended.

**The e2e drives Initialize against the `ebm-sandbox` container**, over HTTP, from the browser
through Next.js to FastAPI to a server answering as RRA does. Every other test of that endpoint
mounts the sandbox in process with a `TestClient` transport; **this is the first time that
endpoint has taken a 200 over HTTP from a real client.** `SDC010000005` and `WIS01006230` are
read off the page after the call, not seeded into the form — a route table that only worked
in-process would fail here and nowhere else.

## No key is rendered, because no key arrives

`cmc_key`, `intrl_key` and `sign_key` are the only secrets this phase holds. `DeviceRead` has
no field for one; `FiscalDevice` in `features/fiscal/types.ts` has no field for one either, so
there is nothing on this side to render by accident. What the screen shows is `has_keys` —
*Keys held* or *No keys* — which is the question an operator asks. The e2e reads the whole page
body and asserts none of the three sandbox key values appears in it.

## The pickers, and why each offers a different list

Rule (e), the P6 Order-defaults pattern. No picker on the Defaults screen offers a control
account, and each offers exactly one side:

| key | offers | why |
|---|---|---|
| `2250` VAT settlement | liabilities | one account, one balance — a net payable sits as a credit and a net credit position as a debit, which is the shape reconciled against RRA's own statement |
| `1290` AR revaluation | non-control **assets** | deliberately not `1200`: a control account's balance is Σ open items at their booking rates, which a revaluation posted there would break |
| `2190` AP revaluation | non-control **liabilities** | its twin |
| `4410` / `6955` unrealized FX | income and expense | both lists are the same, because an SME booking both to one P&L account is making a legitimate choice; what they may not do is put an unrealized movement on the balance sheet |

`_postable_account` now takes the permitted classes and refuses the rest with
`invalid_gl_setting_account_class`, alongside the control-account refusal it always had. The
e2e pins it as a **typeahead assertion** the way P6 step 6 pinned its three, and asserts *both*
halves of each: `1200 · Accounts Receivable` is absent from the AR revaluation picker and
`1290 · AR Revaluation` is present; `1110 · Cash on Hand` is absent from the VAT settlement
picker and `2250 · VAT Payable (RRA)` is present. The first half alone would be satisfied by an
empty picker, which is the P5 step 6 defect rather than the rule working.

The first cut asserted "type `1200`, expect zero options" and was wrong: cmdk scores
*subsequences*, so `1200` still matches `1290 · AR Revaluation` and the count was never zero.
Asserting on the option is the claim that was actually worth making.

## Guards added, and each one broken before it was trusted

| guard | where | broken by | what failed |
|---|---|---|---|
| a settings account of the wrong class is refused | `api/v1/gl.py::_postable_account` | `if False and classes is not None …` | `test_p7_settings_round_trip_and_refuse_the_wrong_class` — `assert 200 == 409` |
| a device status spelled as a literal in a fiscal screen | `lib/api-enums.test.ts`, new `SCOPED_FIELDS` | `status === "active"` in `ebm-devices/page.tsx` | `ebm-devices/page.tsx spells a wire value by hand` |
| the five GAP lines are deleted by real call sites | `test_api_has_a_caller.py` | `api.post("/fiscal/nope", …)` in `useSuspendFiscalDevice` | `POST /api/v1/fiscal/devices/{device_id}/suspend` back in the missing list |
| a picker renders what it holds, not its placeholder | `Combobox` `fallbackLabel`, asserted in `p7-maintenance.spec.ts` | removing `fallbackLabel={fiscalOrigin}` | `Expected substring: "RW" / Received string: "Defaults to Rwanda"` |

A fifth was not written and is worth naming: **nothing guards against a new field's label making
an existing `getByLabel` ambiguous.** CI caught it (see below), loudly and with both matching
elements printed — a good failure. A static guard would have to know which spec drives which
screen, which it cannot, so the honest answer is the process one: run the specs that open the
screens you changed.

**The fourth was not planned — it was found by looking at a screenshot**, which is what rule 13
is for. `Country of origin`, `Packaging unit` and the RRA quantity unit are all synced *from the
device*, and the sandbox publishes no nation table (§4.4, class `05`); the picker therefore had
no option carrying `RW` and fell back to its placeholder — rendering "Defaults to Rwanda" over
an item whose origin was set to `RW`. A screen rendering perfectly and saying something untrue
is the P4 defect class exactly. `Combobox` gained `fallbackLabel`: when `value` is set and no
option carries it, the trigger shows the bare code. Worse than the code and its name, far
better than a lie.

Worth naming as an open question for the phase rather than a step-6 fix: **the sandbox's code
tables carry classes 04, 10 and 17 and not 05.** That is step 1's sandbox and it is not wrong —
those are the classes the tape needs — but a real RRA sync will carry more, and the screen has
to be honest either way. It now is.

The enum guard is **scoped** rather than global, and that is the interesting choice. `status` is
a field name eight enums in this product use, and one of `FiscalDeviceStatus`'s three values is
the string `"active"` — which every `is_active` comparison in the app is about. A global rule
on it would fire on dozens of correct lines, and a guard that cries wolf is a guard somebody
switches off. It is scoped to `src/app/(shell)/maintenance/ebm-devices/` and `src/features/fiscal/`,
where `status` means exactly one thing, and the test asserts both halves: that it fires on the
literal, and that the innocent `status === "posted"` carries no `FiscalDeviceStatus` key. The
scopes are checked against the real directory listing, so a rename cannot silently switch the
rule off. `profile`, `environment`, `fiscal_tax_type` and `fiscal_item_type` are unambiguous
across the app and are guarded globally.

## Rule 14 — the register

**The five `GAP (P7, step 6)` lines are gone**, deleted in this commit by the screen that calls
each endpoint:

| endpoint | caller |
|---|---|
| `POST /fiscal/devices` | Register device |
| `POST /fiscal/devices/{id}/initialize` | Initialize / Re-initialize |
| `POST /fiscal/devices/{id}/suspend` | Suspend |
| `POST /fiscal/devices/{id}/sync-codes` | Sync codes |
| `POST /fiscal/devices/{id}/sync-item-classes` | Sync codes (same button — see below) |

One button drives both syncs, deliberately: they are two calls to the authority with two
watermarks, but they are one *job*, and an operator who refreshed the code tables and not the
classification would have a Tax-types screen offering A–D and an Items screen whose class
typeahead found nothing, with no way to tell why.

`lookup-tin` never needed a line — it is a `GET` — and it is called now anyway, by Verify TIN.
So are `/fiscal/codes`, `/fiscal/item-classes` and `/fiscal/items`.

**A correction to the brief.** The instruction for this step said "the four `GAP (P7, step 7)`
lines stay". There are **fifteen**: six for the purchase feed and the import register, five for
the VAT return and the FX revaluation, and four for the queue screen's three actions plus the
copy print. All fifteen stay — the four are the last group. Nothing else in the register moved.

## Appendix C

The tree gains one row: **EBM devices** under Maintenance → Tax, directly after Tax types,
pinned in `appendix-c-order.test.tsx` and recorded in the Master Plan as **C.1.10**. Everything
else this step built is a column or a section on a screen the tree already carries, and none of
it is a navigable row.

**A numbering deviation, flagged.** The prompt reserved C.1.10 for step 7's Transactions → Tax
block and C.1.11 for its FX revaluation row — written before it was clear that step 6 would
need an entry of its own. It does, so step 7's two become **C.1.11** and **C.1.12**. The
alternative was an out-of-order appendix, which is worse.

**A second correction.** The prompt's step-6 line reads "**Defaults** gains the six accounts and
the default purchase class code". There are **five** accounts — `2250`, `1290`, `2190`, `4410`,
`6955`, exactly as rule (e) enumerates them — plus the class code, which is six `gl_settings`
keys in total. That is what step 1 built and what this screen writes.

## Rule 13 — a screen is not done until a test has opened it with data

`frontend/e2e/p7-maintenance.spec.ts`, six tests in a `serial` describe. Every screen is opened
with data in it and a figure is asserted:

| screen | figure asserted |
|---|---|
| EBM devices | `SDC010000005`, `MRC WIS01006230`, `Active`, `Keys held` — read back after a reload, from the authority's own answer |
| Items | **money** — `2,400`, where the column holds `2400.000000` and RWF has no decimals |
| Tax types | `18%` beside the class `B`, and `A` / `C` on the exempt and zero-rated codes |
| Units of measure | **quantity** — `6`, where the column holds `6.0000000000`, beside `BX` |
| Customers | `Customer C Ltd` — the *authority's* name for TIN `100000001`, not the name in the Vinea row |
| GL Defaults | the five accounts as `code · name`, and zero options for `1200` and `1110` |

EBM devices has no money and no quantity on it — what that screen holds is the authority's
identifiers — so those are what it asserts, which is what rule (a)'s "where one exists" means.

**The fixture is put back.** An active device makes the company *fiscalized*, and from that
moment every posting goes through the fiscal hook: an invoice with no purchase code is refused,
an item with no class code is refused. That would break the AR/AP tape and the inventory specs
from a distance, in whichever shard ran after this one. So the last test suspends the device,
which is also the action the screen has to demonstrate, and leaves `is_fiscalized` false. The
Tax-types test puts `VAT-OUT-18` back to `B` for the same reason.

Screenshots: `docs/screenshots/p7-step-6/`, seven screens × light and dark, captured by
`frontend/scripts/capture-p7-maintenance.ts` against the e2e stack with the sandbox up. The
README beside them says what each shows and why.

## Enums, dates, and the drift gate

Every enum value on these screens comes from `frontend/src/lib/api-enums.ts` — regenerated,
never typed — and the drift gate (`backend/tests/test_api_enums_export.py`) is green.
`FiscalProfile`, `FiscalEnvironment`, `FiscalDeviceStatus`, `FiscalTaxType` and
`FiscalItemTypeCode` are all now in `GUARDED_TYPES`.

Dates go through `lib/format.ts`: the device's last-success timestamp renders with
`formatDate`, and the `.toISOString(` ban is green over `src` and `e2e`.

## Checks

Run in the backend container and against the `docker compose` e2e stack, on committed heads.
**Not a gate step**, so this is the changed specs plus the guard tests; CI is the record for the
branch.

```
backend, at 4c7f805 (the tree the full suite ran against — every later commit is frontend only)
  uv run ruff check .                  →  All checks passed!
  uv run pytest -q -n 4                →  1365 passed, 7 warnings in 1088.27s (0:18:08)
  uv run alembic check                 →  No new upgrade operations detected
                                          (step 6 adds no migration)

backend, at b5fe3bb (the branch head)
  uv run ruff check .                  →  All checks passed!
  uv run pytest -q -n 4 \
    tests/test_api_has_a_caller.py \
    tests/test_api_enums_export.py \
    tests/kernel/test_gl_api.py \
    tests/inventory/test_masters_api.py →  87 passed in 49.06s

frontend, at b5fe3bb
  npx tsc --noEmit                     →  clean
  npx vitest run                       →  16 files, 377 passed
  npm run lint                         →  no errors (three pre-existing exhaustive-deps warnings)
  npm run build                        →  Compiled successfully; /maintenance/ebm-devices 9.04 kB

e2e, at b5fe3bb, on a reset database with ebm-sandbox in the stack
  npx playwright test e2e/p7-maintenance.spec.ts
                                       →  7 passed (27.1s)
  npx playwright test e2e/accessibility-maintenance.spec.ts -g "ebm-devices"
                                       →  1 passed — no serious/critical axe violations,
                                          light and dark

e2e, after the CI failure below, **every spec that opens a screen this step changed**, on a
reset database:
  npx playwright test e2e/inventory-maintenance.spec.ts e2e/inventory-acceptance.spec.ts \
                     e2e/p6-maintenance.spec.ts e2e/p7-maintenance.spec.ts
                                       →  35 passed (3.1m)
  npx playwright test e2e/p6-maintenance.spec.ts   (the `Type` hardening landed after the run
                                                    above had already collected its specs)
                                       →  6 passed (31.5s)
```

The ar-ap specs open a changed screen too — Verify TIN on Customers and Suppliers — and they
are validated by CI rather than locally: `e2e (rest-1)`, `e2e (tape)` and `e2e (a11y)` were
**green on the first push**, and those nine specs run in them.

**+4 backend tests**, named rather than counted from a baseline:
`test_p7_settings_round_trip_and_refuse_the_wrong_class`,
`test_tax_code_carries_its_ebm_class`, `test_a_unit_carries_the_authoritys_quantity_code` and
`test_an_item_carries_the_four_fields_the_authority_registers_it_by`. `main` was not re-run for
a both-sides figure: this is not a gate step, and a figure taken from a run nobody would use is
worse than none.

**The fixture is verifiably restored.** After the e2e run, read straight out of the database:

```
is_fiscalized(company 1): False
devices:                  [(1, 'suspended')]
VAT-OUT-18 class:         [('B',)]
```

```
git diff --stat main..HEAD
 backend/app/api/v1/gl.py                           |  68 ++-
 backend/app/api/v1/inventory.py                    |  28 ++
 backend/app/inventory/masters.py                   |  36 ++
 backend/app/kernel/masters.py                      |  18 +
 backend/app/schemas/gl.py                          |  37 +-
 backend/app/schemas/inventory.py                   |  31 ++
 backend/tests/inventory/test_masters_api.py        | 103 +++++
 backend/tests/kernel/test_gl_api.py                | 123 ++++++
 backend/tests/test_api_has_a_caller.py             |  38 +-
 docs/Vinea_ERP_Master_Plan_v5.md                   |  35 +-
 docs/p7-step-6-report.md                           | 196 +++++++++
 docs/screenshots/p7-step-6/*.png                   | 14 files, binary
 docs/screenshots/p7-step-6/README.md               |  63 +
 frontend/e2e/p7-maintenance.spec.ts                | 465 +++++++++++++++++++++
 frontend/scripts/capture-p7-maintenance.ts         | 308 ++++++++++++++
 frontend/src/app/(shell)/maintenance/defaults/page.tsx        | 240 +++++----
 frontend/src/app/(shell)/maintenance/ebm-devices/page.tsx     | 447 +++++++++++++++
 frontend/src/app/(shell)/maintenance/inventory-items/page.tsx | 181 ++++++-
 frontend/src/app/(shell)/maintenance/taxes/page.tsx           |  35 +
 frontend/src/app/(shell)/maintenance/uom-categories/page.tsx  |  47 +
 frontend/src/design/components/appendix-c-order.test.tsx      |  17 +
 frontend/src/design/components/combobox.tsx        |  26 +-
 frontend/src/design/nav-tree.ts                    |  12 +
 frontend/src/features/fiscal/hooks.ts              | 162 +++++++
 frontend/src/features/fiscal/types.ts              | 147 +++++++
 frontend/src/features/gl/hooks.ts                  |   7 +-
 frontend/src/features/gl/types.ts                  |  35 ++
 frontend/src/features/inventory/types.ts           |  32 +-
 frontend/src/features/subledger/i18n-coverage.test.ts         |  10 +
 frontend/src/features/subledger/partners-screen.tsx           |  70 ++
 frontend/src/i18n/messages/en.json                 | 124 +++++-
 frontend/src/lib/api-enums.test.ts                 |  67 ++-
 45 files changed, 3090 insertions(+), 118 deletions(-)
```

## Not in this step

* **Nothing posts.** The queue, the receipts, the VAT return and the FX revaluation are steps
  7 and 8, and their `GAP (P7, step 7)` and `(P7, step 8)` lines are untouched.
* **No migration.** Every column this step writes was added by `0022_p7_fiscal`; `alembic
  check` reports no new operations.
* **The class typeahead is server-searched and debounced** (250 ms), because the classification
  is tens of thousands of rows and a picker that loaded them all would be a picker nobody could
  use. `Combobox` gained an `onSearch` mode that turns local filtering off and a
  `fallbackLabel` for the selected row when the server's current page no longer holds it —
  without the latter the control would read as empty over a field that holds something, which
  is the defect class rule 13 exists for.

## CI went red, and why

The first push failed `e2e (rest-2)` and `e2e (rest-3)` — four tests in three files, all with
one cause, all mine:

```
Error: locator.fill: strict mode violation:
  getByRole('dialog').getByLabel('Code') resolved to 2 elements:
    1) <input placeholder="WINE-750" …>            aka getByRole('textbox', { name: 'Code' })
    2) <button aria-haspopup="dialog" …>           aka getByRole('button', { name: 'Class code' })
```

`getByLabel` matches on **substring**. The Fiscal section this step adds to the item dialog
carries a field labelled **Class code**, so every existing `getByLabel("Code")` inside that
dialog became ambiguous: `inventory-acceptance`, `inventory-maintenance` (twice) and
`p6-maintenance`. The sibling line in each of those tests already read
`getByLabel("Name", { exact: true })` — `Code` was loose only because nothing had collided with
it yet.

**Fixed by making the lookup exact**, which is what the specs' own convention already was: nine
sites across four files, one token each. The alternative — renaming the field — would have
traded the authority's own vocabulary (`itemClsCd` is a *class code*) for a test's convenience.

**Why local runs missed it.** The new spec and the axe sweep were run, and the inventory and
order-entry maintenance specs were not — the ones that drive the screen this step changed. That
is the process error behind the code one, and the fix for it is not a guard but an order of
operations: run the specs that open the screens you touched, not only the specs you wrote.

**A scan, rather than fixing only what CI happened to hit.** Every label and role name this step
adds was checked against every loose lookup in `e2e/`. It found the nine `Code` sites and one
more: `getByRole("combobox", { name: "Type" })` in `p6-maintenance`, which "Product type" also
contains. That one is *not* broken — the new field is a `Combobox` (a plain button) and the item
type is a `Select` (role `combobox`), so the roles keep them apart — but it is one accidental
role change from the same failure, so it is exact now too, labelled as insurance rather than as
a fix.

**And one flake, seen once and closed.** Re-running the four files locally, `p6-maintenance`'s
kit test failed at `closeDrawer` with a healthy drawer still open. It did not reproduce on a
reset database, but the race is real and predates this step: creating an item closes the create
dialog and opens the drawer, both are Radix dialogs, both render a Close button with the same
label, and `getByRole("dialog")` matches whichever is mounted. A `closeDrawer` that ran while
the POST was still in flight clicked the *create dialog's* Close, and the drawer opened behind
the assertion. `closeDrawer` now waits for the drawer specifically — it is the one with the
Details tab — rather than for "a dialog". This step did not cause that race, but it widened the
window: the item dialog is taller and the Items page has three more queries to settle.

## The tree

```
git status --short
[empty]

git log @{u}..
[empty]
```

Three commits on `p7-step-6`, from a pulled `main` at `43e57a4`:

```
a379818  docs(p7): the step-6 report
b5fe3bb  fix(fiscal): a synced-code picker renders what it holds, not its placeholder
4c7f805  feat(fiscal): P7 step 6 — EBM devices and the fiscal master fields
```
