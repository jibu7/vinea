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
e2e pins it as a **typeahead assertion** the way P6 step 6 pinned its three: typing `1200` into
the AR revaluation picker and `1110` into the VAT settlement picker leaves **zero** options.

## Guards added, and each one broken before it was trusted

| guard | where | broken by | what failed |
|---|---|---|---|
| a settings account of the wrong class is refused | `api/v1/gl.py::_postable_account` | `if False and classes is not None …` | `test_p7_settings_round_trip_and_refuse_the_wrong_class` — `assert 200 == 409` |
| a device status spelled as a literal in a fiscal screen | `lib/api-enums.test.ts`, new `SCOPED_FIELDS` | `status === "active"` in `ebm-devices/page.tsx` | `ebm-devices/page.tsx spells a wire value by hand` |
| the five GAP lines are deleted by real call sites | `test_api_has_a_caller.py` | `api.post("/fiscal/nope", …)` in `useSuspendFiscalDevice` | `POST /api/v1/fiscal/devices/{device_id}/suspend` back in the missing list |

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

<!-- FILLED AT COMMIT -->

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
