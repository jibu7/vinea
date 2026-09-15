# P6 step 6 — Order entry maintenance screens

1440x900, light and dark, captured against the dev stack by
`frontend/scripts/capture-p6-maintenance.ts` in a single pass on a reset database. Re-capture
with:

```sh
cd frontend
OUT=../docs/screenshots/p6-step-6 npx tsx scripts/capture-p6-maintenance.ts
```

`ONLY` re-captures a subset, so changing one screen does not rewrite the other seven files:

```sh
OUT=../docs/screenshots/p6-step-6 ONLY=3-kit-components npx tsx scripts/capture-p6-maintenance.ts
```

| # | Screen | Files |
|---|---|---|
| 1 | Order defaults, the three accounts resolved | `1-order-defaults-{light,dark}.png` |
| 2 | Inventory items, with a kit in the catalogue | `2-inventory-items-kit-{light,dark}.png` |
| 3 | Item drawer, Kit components tab | `3-kit-components-{light,dark}.png` |
| 3b | The same section in edit mode | `3b-kit-components-editor-{light,dark}.png` |
| 4 | Item dialog, the two fields P6 added | `4-item-purchase-and-weight-{light,dark}.png` |

Shot 1's accounts are the seed pack's and the P6 back-fill's, resolved to `code · name`: an
empty picker or a "Not set" over a configured company is exactly the defect
`src/lib/api-enums.ts` was generated to make impossible, so the shot is of the resolved
labels. The default warehouse beside the policy is read-only — it is the *inventory* default
and Maintenance → Inventory → Defaults owns writing it.

Shots 2–3b are of `KIT-GIFT2`, a gift pack of two `WINE-750` and one `GIFTBOX`, seeded
through the real endpoints by the capture script. The definition is recognisably a definition
— a reviewer can see that two bottles and a box is what a gift pack of two reds is made of,
which a kit of two arbitrary codes would not show. The price reads `3,500`, RWF's zero
decimals, over a column holding `3500.000000`; the quantities read in each component's own
base unit.

Shot 4 is `WINE-750` open for edit, showing the purchase account (where an AP line for a
service or non-stock item lands) and the weight per base unit (read by a landed-cost split on
the `weight` basis, which refuses a target item that has none). Both columns shipped with the
step-1 migration and had nowhere to be set until this screen.
