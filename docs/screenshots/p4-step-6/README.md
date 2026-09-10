# P4 step 6 — AR/AP maintenance screens

1440x900, light and dark, captured against the dev stack by
`frontend/scripts/capture-p4-screens.ts`. Re-capture with:

```sh
cd frontend
OUT=../docs/screenshots/p4-step-6 npx tsx scripts/capture-p4-screens.ts
```

| # | Screen | Files |
|---|---|---|
| 1 | Customer master, AR settings tab | `1-customer-ar-settings-{light,dark}.png` |
| 2 | Supplier master (list) | `2-supplier-master-{light,dark}.png` |
| 2b | Supplier drawer, AP settings tab | `2b-supplier-ap-settings-{light,dark}.png` |
| 3 | Payment terms | `3-payment-terms-{light,dark}.png` |
| 4 | Ageing bucket-set editor | `4-bucket-set-editor-{light,dark}.png` |
| 5 | AR/AP defaults, read-only | `5-ar-defaults-readonly-{light,dark}.png` |
| 6 | Allocation screen with the preview panel | `6-allocation-preview-{light,dark}.png` |
| 7 | AR journal batch, line entered | `7-ar-batch-{light,dark}.png` |
| 7b | AP journal batch, line entered | `7-ap-batch-{light,dark}.png` |
| 8 | Customer enquiry, drill-down open | `8-customer-enquiry-drilldown-{light,dark}.png` |
| 9 | Age analysis | `9-age-analysis-{light,dark}.png` |
| 10 | Statement job ready to download | `10-statement-ready-{light,dark}.png` |

The supplier row is seeded by `seed_e2e` (`E2ESUP001`, Musanze Packaging Ltd) so the list is
never empty here or in the AP specs; it is created through the masters service with a real
actor, not a raw insert.

Shot 5 is signed in as `e2e.readonly@vinea.example` (the seeded Clerk role: `*:reports_view`
and nothing else). The disabled Save, the `gl:setup_manage` note beside it and the shortened
sidebar are all real permission state, not a styled mock.
