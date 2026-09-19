# P7 step 6 — EBM devices and the fiscal master fields

1440x900, light and dark, captured against the **e2e** stack by
`frontend/scripts/capture-p7-maintenance.ts` in a single pass on a reset database. The e2e
overlay is required, not optional: shot 1 is of a device that has actually been initialized,
and initializing one calls the `ebm-sandbox` service.

```sh
export COMPOSE_FILE=docker-compose.yml:docker-compose.e2e.yml
docker compose up -d --wait db backend frontend ebm-sandbox
cd frontend
OUT=../docs/screenshots/p7-step-6 npx tsx scripts/capture-p7-maintenance.ts
```

`ONLY` re-captures a subset, so changing one screen does not rewrite the other twelve files:

```sh
OUT=../docs/screenshots/p7-step-6 ONLY=3-tax-types-ebm-class npx tsx scripts/capture-p7-maintenance.ts
```

| # | Screen | Files |
|---|---|---|
| 1 | EBM devices, one device active with what the authority sent back | `1-ebm-devices-{light,dark}.png` |
| 2 | The register dialog — branch, profile, environment, base URL, serial | `2-ebm-device-register-{light,dark}.png` |
| 3 | Tax types, with the EBM class column beside the rate | `3-tax-types-ebm-class-{light,dark}.png` |
| 4 | Units of measure, with the RRA quantity-unit column | `4-uom-quantity-unit-{light,dark}.png` |
| 5 | Item dialog, the Fiscal section and what RRA holds back | `5-item-fiscal-section-{light,dark}.png` |
| 6 | Verify TIN on a customer, showing the authority's own name | `6-verify-tin-{light,dark}.png` |
| 7 | GL Defaults, the tax and revaluation block | `7-gl-defaults-tax-block-{light,dark}.png` |

**Shot 1 is the one to read first.** `SDC010000005` and `WIS01006230` are not fixtures typed
into a form — they are what the sandbox returned over HTTP when Initialize was pressed, which
is the first time that endpoint has been reached by a real client over a network rather than
by an in-process test transport. Beside them, `Keys held`: the device is holding its three
keys and the screen cannot say more than that, because `DeviceRead` has no field for a key.
The row also carries **Re-initialize** where a suspended device would be brought back —
there is no Activate button anywhere on the screen, and there is not meant to be.

Shot 2 shows the four things a device is registered with. The branch picker offers only
branches that have no device: one device per branch, because two would each hold their own
gapless receipt run for the same shop and the authority reconciles against those counters.

Shots 3 and 4 are the two columns the step-1 migration had nowhere to set. The EBM class on
`VAT-OUT-18` reads **B** against a rate of 18%, `VAT-EXEMPT` reads A and `VAT-ZERO` reads C —
the Rwanda pack's mapping, shown rather than assumed. The quantity unit on the case of six
reads `BX`, over a conversion factor the column holds as `6.0000000000` and the screen renders
as `6`.

Shot 5 is the Fiscal section on `FISCAL-DEMO`: the class code picked from the authority's own
classification (a typeahead over the server — tens of thousands of rows), the origin, the
packaging unit and the product type, with the registration pair beneath. It reads **Not
registered** and that is correct: an item registers with RRA on its first *fiscal use*, in the
posting's own transaction, so a catalogue screen is where the four fields are set and not
where registration is triggered.

Shot 6 is Verify TIN answering. `Customer C Ltd` is the authority's name for TIN
`100000001`, not the name in the Vinea row — which is the entire reason for asking.

Shot 7 is the GL Defaults tax block, resolved to `code · name`. Each picker offers a different
list: VAT settlement offers liabilities, AR revaluation assets, AP revaluation liabilities,
and the two unrealized-FX contras the P&L. None of them offers a control account — the AR
revaluation contra is deliberately **not** `1200`, whose balance is the sum of open items at
their booking rates, which a revaluation posted there would break.
