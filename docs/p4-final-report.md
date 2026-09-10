# Phase 4 — final report

AR and AP as one symmetric partner-document module on the P2 kernel. This closes P4: steps 1–8
landed over earlier PRs, step 9 (tests, CI and this report) on `claude/p4-step-9`.

The Definition of Done from `.github/prompts/phase-4-ar-ap.prompt.md`, clause by clause, each
with the thing you can go and run rather than a claim.

## Definition of Done

| # | Clause | Evidence |
|---|---|---|
| 1 | The invoice → part-payment → allocation → statement cycle completes in RWF and in USD | `e2e/ar-ap-acceptance.spec.ts`, tapes `AR · USD` and `AR · RWF`. Each walks eleven screens and asserts the same figure on the enquiry, the age analysis and the Allocation report. |
| 2 | Realized FX posts at allocation | Same tapes. USD realizes 20,000 to `6950`; RWF asserts the allocation writes **no entry at all**, and that the report says so rather than offering a dead link. |
| 3 | …and nets correctly across full settlement | `test_full_settlement_realizes_exactly_the_booking_rate_difference` — Hypothesis sweeps the closing rate 1000–1600 either side of the booking rate and requires the control account at exactly zero, both roles. |
| 4 | `assert_subledger_invariants` passes, so control accounts reconcile to open items at any date | Asserted in `test_documents`, `test_allocations`, `test_reports`, `test_role_matrix`, and after every step of the arbitrary-sequence property test `test_the_subledger_survives_an_arbitrary_sequence`. |
| 5 | The credit-limit block fires and the override permission clears it | All three tapes: the seeded Accountant (`ar/ap:transactions_post`, no `*:credit_limit_override`) is refused inline on `partner_id`, the owner posts the same document. `test_the_credit_limit_override_audit_names_the_actor_and_the_excess` covers the audit row. |
| 6 | Settlement discount posts at allocation | The fifth tape, both roles: on 2/10 net 30, an invoice settled on **day 8** shows the 2,000 on offer, takes it, previews the posting, and the discount account moves — followed to the entry through the Allocation report. The same invoice at **day 11** offers nothing, refuses input, and leaves the 2,000 outstanding. Server-side: `test_settlement_discount_signs_follow_the_invoice_direction`, `test_a_discount_beyond_the_terms_is_refused`, `test_the_discount_window_closes`, `test_the_enquiry_reports_the_discount_on_offer_at_the_allocation_date`. |
| 7 | A post-dated receipt stays out of bank until matured | `AR · USD` and `AR · RWF` tapes: posted to `1250` with no bank line, allocated **while pending**, then banked from the new Post-dated receipts screen and the transfer entry checked. `test_a_post_dated_instrument_waits_in_its_own_account_until_maturity` and `test_a_run_banks_only_what_has_matured_and_reports_what_it_left` (two cheques, one due, one not) on both roles. |
| 8 | Every P4 item in the Appendix C tree is live and untagged | `module-nav.tsx` carries zero `P4` tags; `appendix-c-order.test.tsx` pins the whole tree in the owner's order and failed when this step added the two post-dated entries. |
| 9 | Nothing writes to the ledger outside the PostingEngine | `test_4f_direct_insert_outside_the_posting_engine_is_rejected` and its per-line sibling — the `app.posting_engine='on'` GUC trigger, not convention. |
| 10 | Every audited call passes a real actor | `test_audit_log_records_who_and_when` (schema-level), plus the per-feature audits: credit-limit override, AR/AP defaults, partner rename. |
| 11 | CI green on backend and frontend | Below. |

## Verification, as run

```
backend   ruff check .            All checks passed!
backend   pytest -q               393 passed
backend   alembic check           No new upgrade operations detected.
frontend  lint                    0 errors (2 pre-existing react-hooks warnings)
frontend  typecheck               clean
frontend  test (vitest)           102 passed (8 files)
frontend  build                   ok
frontend  npm run e2e             44 passed, incl. axe in both themes
          e2e:regression          PASSED
          e2e:print-preview       COMPLETE
```

Every load-bearing assertion added in step 9 was checked by breaking it, and each failed as
intended:

| Mutation | Test that caught it |
|---|---|
| Realized FX off by 1,000 | `AR · USD` tape, allocation preview |
| Open balance off by 80,000 | `AR · USD` tape, enquiry |
| Credit-limit "breach" reduced below the limit | `AR · USD` tape, the block never fires |
| Entry amounts back to the document's currency | `AR · USD` tape, the control-account row |
| Batch debit read from `Current` instead of `31 - 60` | AR batch tape |
| AP's realized difference expected in the loss account | `AP · USD` tape, allocation preview |
| A bank line expected before maturity | `AR · RWF` tape, post-dated step |
| Discount invoice dated 12 days back instead of 8 | AR discount tape — nothing on offer |
| AP's discount expected in the AR discount account | AP discount tape, allocation preview |
| Maturity run over *every* outstanding instrument, ignoring the date | `test_a_run_banks_only_what_has_matured_and_reports_what_it_left`, both roles |
| `max_discount` without its invoice check | `test_the_enquiry_reports_the_discount_on_offer_at_the_allocation_date`, both roles |
| AP headroom back to `credit_limit - balance_base` | `test_credit_headroom_falls_as_the_partner_owes_more_in_both_roles[ap]` |

## Screenshots

`docs/screenshots/p4-step-9/` — document workspace and the entry behind it, both themes, 1440×900:

- `11-document-workspace-{light,dark}.png` — an invoice in USD at a booking rate of 1300, the
  partner typeahead showing open balance and credit headroom, and the Exclusive / Tax /
  Inclusive footer saying tax is the server's.
- `12-fx-invoice-entry-{light,dark}.png` — the entry it posts: `FRw 1,300,000` on both legs, and
  titled "Customer Invoice". Both of those are step-9 fixes; see below.
- `13-post-dated-{light,dark}.png` — instruments waiting to be banked, with the run disabled
  because none is due at the chosen date.
- `14-allocation-report-{light,dark}.png` — the report that used to render nothing, with rows,
  per-currency formatting, drill-down links, and "(posts nothing)" where an allocation wrote
  no entry.

`docs/screenshots/p4-step-6/` — the allocation screen with a realized difference in its preview
(`6-allocation-preview-*`) and the age analysis with real figures (`9-age-analysis-*`).

## What step 9 found

Four defects, each surfaced by writing a test that looked at something no test had looked at.

1. **The journal entry screen formatted `base_amount` with the line's own currency.** A USD
   1,000 invoice rendered as `$ 1,300,000.00` — the base figure wearing the document's symbol
   and decimals, while the footer totalled the same numbers as base. Only a foreign-currency
   document could show it, and P4 is what put those in the ledger.
2. **The Allocation report was permanently empty.** It read `allocation.lines` off an endpoint
   that returns one flat row per pairing, so a subledger with 28 allocations rendered "Nothing
   to report". No test had ever opened that screen.
3. **AP credit headroom grew as you owed more.** `credit_limit - balance_base` is an AR-shaped
   sum and AP balances are negative. The posting-time check had always turned the sign; the
   enquiry and the partner typeahead had not. `exposure_direction` is now one definition, shared,
   with a test holding it to the document matrix.
4. **A post-dated instrument could be raised and never matured.** `mature_instruments` shipped
   as an endpoint and a scheduled job with no screen at all.

Plus four smaller ones: allocated and discount amounts printed at NUMERIC wire scale
(`200000.000000`); every entry that was not a cashbook batch titled "Journal Batch", including
customer invoices; the Allocation report's "all partners" filter option labelled with its own
empty-state message ("Nothing to report for this selection.") while listing every allocation
beneath it; and `max_discount` returning a discount for *any* document, so once the enquiry
started asking about every open item a receipt was offered 2% of itself.

And one the maturity run needed: it returned only what it banked, which made "nothing was
due", "nothing happened" and "three cheques have no cash account" the same empty list. It now
returns `matured`, `waiting` and `skipped` (with a reason), and the screen reports all three.

## Decisions worth review

1. **Post-dated instruments got a screen, and the nav contract grew two entries.** Appendix C
   does not list them, but neither does it list the document they act on. `appendix-c-order.test.tsx`
   records the reasoning next to the change. The run is per-date, not per-row: `mature_instruments`
   takes a date and banks everything matured by it in one transaction, and a per-row button would
   either lie about that or need a second service behind it.
2. **The maturity run sends no `Idempotency-Key`.** It selects on `matured_entry_id IS NULL`, so
   a re-run is idempotent by construction; sending a key the endpoint does not read would claim a
   guarantee that came from somewhere else.
3. **Banking a cheque needs its maturity period open.** The transfer posts on the instrument's own
   maturity date and the kernel refuses a `future` period — so a cheque maturing in October cannot
   be banked until October opens. That is correct, and it is why the tape dates the document back
   and matures it today rather than the reverse.
4. **`E2E_PASSWORD` is required, with no fallback.** CI generates a fresh one per run. The
   alternative considered was a committed `e2e.env`, which is the same literal in a tracked file.
5. **Settlement discount has no VAT adjustment**, as P4 decision 6 directs. Posted gross; the
   credit-note-based treatment is a fiscalization-phase question.
6. **The allocation screen now shows the discount on offer**, read from the pair's *invoice*
   whichever side of the control account it sits on — the same rule `_discount_side` uses. Before,
   an operator had to know the terms, type a number, and learn from a refusal whether the window
   had closed.
7. **The AP tape runs in USD only.** A base-currency AP pass would exercise no exchange
   difference, and the role matrix already covers both roles in both currencies server-side.

## Deviations from the plan, with reasons

1. **Drafts are `localStorage`, not IndexedDB** (P3 step 8's wording, inherited by P4 step 7).
   Recorded as a decision in `src/lib/drafts.ts`: drafts are small, per-device and disposable, and
   the synchronous API is what makes save-on-every-keystroke trivial. The quota cap is explicit.
2. **The discount tape reaches eleven days into the past.** The discount is a fact of the
   allocation date, that date has to be in an open period, and the kernel refuses a `future` one —
   so the invoice is dated back rather than the allocation dated forward. Those eleven days must
   also fall in open periods. It fails loudly with `period_closed` if the fixture's closed period
   ever lands inside that window; it is the one date-dependency in the suite.
3. **`GET /subledger/{role}/instruments` is new API surface in a "tests and CI" step.** Listing
   what is outstanding is a precondition for a screen that can mature anything, and `pending_instruments`
   answers a different question ("what would a run move today?").
4. **`next dev` no longer watches Playwright's output directories.** It recompiled through every
   e2e run and, after a few, login stopped hydrating inside the timeout — a test-infrastructure
   fix, not a product one, but it is why the suite is now stable enough to trust.

## Known, not fixed

- The document workspace renders a second "Search Ctrl K" chip inside its own header, next to the
  app shell's. Cosmetic, visible in `11-document-workspace-*.png`, and not P4's.
- **No e2e posts a document that picks its rate up from the `exchange_rates` table.** Every
  foreign-currency document in the tape types its booking rate on the screen, which is the
  stronger UI assertion but leaves the dated-rate lookup to `tests/kernel` and to
  `gl-inline-errors.spec.ts`, which only covers the *missing*-rate refusal. The abandoned
  `claude/p4-step-9-e2e` branch took the other route; this is the one thing it exercised that
  this branch does not.
- `frontend/scripts/e2e-{journal,cashbook,step4-maintenance,step6-verify,step7-reports}.ts` are
  ad-hoc verification scripts against a hand-made dev tenant, not part of `npm run e2e`. They now
  take their credentials from the environment, but nothing runs them.
