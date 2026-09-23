# P8 step 6 — Bank accounts, formats, rules and the supplier bank details

1440x900, light and dark, captured against the dev stack on a database reset by
`make db-reset`, by `frontend/scripts/capture-p8-maintenance.ts`, with the dev server warmed
first:

```sh
make db-reset
cd frontend
OUT=../docs/screenshots/p8-step-6 npx tsx scripts/capture-p8-maintenance.ts
```

`ONLY` re-captures a subset:

```sh
OUT=../docs/screenshots/p8-step-6 ONLY=1-bank-accounts npx tsx scripts/capture-p8-maintenance.ts
```

The script drives what each shot needs through the real endpoints before it photographs it —
a receipt on `1120`, a rule on `1121`, a supplier of its own with bank details — and leaves
the seeded supplier alone.

| # | Screen | Files |
|---|---|---|
| 1 | Bank accounts: cash and bank rows, `1121` held in USD with Bank of Kigali and its account number | `1-bank-accounts-{light,dark}.png` |
| 2 | *New bank account* — the GL account and its master in one form | `2-bank-account-new-{light,dark}.png` |
| 3 | `1120`'s Details, the currency **locked** because the account has a posting | `3-bank-account-currency-locked-{light,dark}.png` |
| 4 | `1121`'s Statement format, *Test with a file* run over the tape's USD sample | `4-statement-format-test-{light,dark}.png` |
| 5 | `1121`'s Rules, one rule suggesting `6700 · Bank Charges` | `5-bank-rules-{light,dark}.png` |
| 6 | A supplier's **Bank details** section, read back from the server | `6-supplier-bank-details-{light,dark}.png` |
| 7 | GL Defaults, the **Banking** block resolved to `code · name` | `7-gl-defaults-banking-{light,dark}.png` |
| 8 | Chart of accounts, a cash control account just created and the bank-account row it got | `8-chart-bank-row-{light,dark}.png` |

**Shot 4 is the one to read first.** The figures are not typed anywhere: `2 lines · 2 new ·
0 already held`, opening `$ 0.00`, closing `$ 495.00`, and the two rows with their running
balance are what the preview endpoint derived from
`backend/tests/banking/samples/generic-bk-usd-sep.csv` under the mapping on the screen — the
same file and the same `495.00` the acceptance tape imports. Nothing was written.

**Shot 3** shows the lock as a fact with its reason, rather than a picker that would be
refused on save. The reason uses the field's error slot, so it reads red; it is information,
not a failure, and the screen does not let the operator reach the refusal.

**Shot 1 was retaken last**, after the account number was kept to one line
(`whitespace-nowrap` — `00040-0000999-11` had broken across two). By then shot 8 had created
`1115 Petty Cash Musanze` on the chart, and it is in the list as a registered cash account:
the chart's create reaching this screen, which is the point of shot 8. The list behind the
drawers in shots 2–5 is from the first pass, and still shows the wrap.

**Shot 8** is one account photographed in both themes: the notice lives in the page's state,
and a second account for the dark shot would have put two in the chart. The *New account*
toast is still up in the corner.
