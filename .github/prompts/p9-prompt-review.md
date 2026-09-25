# Review of `phase-9-fixed-assets.prompt.md`

Reviewed against `main` at `d1c83fc` (P8 close), the Master Plan v5.1, the P8 prompt, IAS 16 and Sage 200 Evolution's Fixed Assets module. Seven independent readers, each finding verified by three adversarial checks; 47 findings survived, 22 were refuted or had no votes, 12 more came from a completeness pass (two of those I confirmed by hand, the rest are marked unverified).

**Verdict.** The prompt is structurally sound and the tape's money arithmetic is right in every row (all 18 rows recomputed independently three times: VAT, the six reducing-balance charges 312 500 / 305 990 / 299 615 / 293 373 / 287 261 / 281 276, the 30 Jun register 26 160 000 / 11 140 015, the depreciation total 2 435 015, the gain/loss/surplus figures, and the DEP-000006 / DEP-000007 numbering). What is wrong is one error-code literal, several mechanisms two builders would build differently, four accounting choices an Evolution user or auditor will question, and a handful of stale process sentences copied from P8. Fix these in the prompt before step 1; the prompt itself forbids editing tape literals during the build.

---

## A. Literals and codes that are wrong or unreachable as written

1. **Tape row 0: the manual journal on 1610 is refused `control_account_direct_posting`, not `control_account_manual_posting`.** The registry check in `posting.py:378-396` runs before the ManualJournal branch, so once `(fixed_asset, fa)` is registered every non-fa event, manual journals included, gets `control_account_direct_posting`. `control_account_manual_posting` is only reachable for unregistered control types (bank/cash). Pinned by `tests/kernel/test_posting.py:196-197`; the P8 step-2 approvals row learned the same lesson. Fix row 0 and the sentence in decision 2 and the P2 recap (line 49). The cashbook-counterpart literal is already right. *(blocker)*

2. **Tape row 18: `write_off_has_no_proceeds` is unreachable.** After row 17 every active asset is charged for June and both 1660 lines are applied, so a June write-off naming a proceeds line also trips `depreciation_already_run_for_period` and `proceeds_line_applied`, and a July one trips `period_not_open`. Decision 8's prose lists the checks in the reverse of the order row 18 needs. State the plan order: `asset_not_active` → kind/proceeds shape on the request alone → proceeds-line validity → timing. Also make row 18's LAP-02 disposal a `write_off` with a reason (as a `sale` with no proceeds it would hit `sale_needs_proceeds` first). *(major)*

3. **Tape row 18: `reverse DEP-000007 → depreciation_run_not_latest`** also satisfies `asset_has_later_transactions`. Say in decision 7 that the reversal plan checks in the listed order and stops at the first refusal.

4. **Tape row 3: SIN-1 has two capitalized lines by then** (ACQ-000001 and ACQ-000002), and row 14 establishes the convention of naming every blocker. Either the guard names every non-reversed acquisition in number order and row 3 expects both, or decision 5 says it names the first.

5. **Tape row 18: transfer of the disposed PROJ-01 expects `asset_not_active`, which decisions 9 and 10 never list.** Add "the asset must be `active` (`asset_not_active`, checked first)" to the transfer, the revaluation and policy edits.

6. **Tape row 6 (unverified, my own reading): the 31 Jan attempt breaks two rules.** Decision 5 says the document date must be ≥ the source line's entry date (SIN-2 is 5 Feb) but gives that rule no error code; row 6 expects `period_not_open`. Name the date-order code (e.g. `acquisition_before_source_line`) and say the period check runs first.

7. **Tape row 10: "apply the same line to a second disposal" does not say which asset** (unverified). On LAP-01 `asset_not_active` fires first; say LAP-02.

8. **Tape rows 3, 10, 14 never state the date each reversal carries.** The AP/AR reverse endpoint takes the date from the caller (UI default: today); on a 2026-pinned company with Jan–Jun open, a reversal dated "today" is refused `period_not_open` before the guard is reached. Date SIN-1's reversal in row 3 and INV-1's in row 10 inside an open period, and date the run reversal in row 14 at the run's own date (31 May) so DEP-000006 posts and "register back to April" is unambiguous. Also state where the guard sits in the kernel (after `_load_reversible`, before `_resolve_lines`; period → entry state → module window → guard).

9. **Tape row 16 books a VAT-registered company's asset sale with no output VAT**, while row 10 charges VAT on the laptop. Key CB-1 as 1 770 000 gross with `VAT-OUT-18` tax-inclusive (net 1 500 000 to 1660, no other literal moves), or add a sentence that the sale is deliberately VAT-free.

10. **Row 14's DEP-000006 holds only if the run document takes its entry's number** (the P7 shape). Say so in decision 7, or a builder who lets `fa_documents` claim a number and the entry claim another consumes two.

## B. Decisions the owner should take before step 1 (accounting, Sage, IAS 16)

11. **Reducing balance compounds monthly at a nominal rate.** `nbv_open × 25 ÷ 1 200` every month removes 22.33 % of NBV in a year, not 25 %. VEH-01 over twelve months: 3 348 799 against 3 750 000 at a true 25 %, a 401 201 gap. Evolution (general knowledge, not web-verified) takes the annual rate against the NBV at the start of the financial year and spreads it over the year's periods, so its register shows 312 500 every month. Either switch decision 6 to that form (rows 7, 8, 11, 14, 17 change: VEH-01 312 500 each month, accumulated at 30 Jun 10 875 000, 1620 11 235 000, NBV 13 125 000, depreciation report 2 530 000), or keep the formula and say on the category form, in decision 6 and in precondition (b) that `annual_rate` is nominal, compounded monthly. *(major)*

12. **Contiguity deadlocks a period closed without a run inside a locked year, and the DoD contradicts the refusal.** Row 9's only exit is reopening the period; in a locked year that means `reopen_fiscal_year`, which reverses the year-end closing entry (`year_end.py:95-126`, `periods.py:128-138`). The DoD then says "a missed month is caught up by the next run", the opposite of `depreciation_run_order`. Evolution processes "up to" a period and books outstanding charges into the period being processed. Pick one: (A) keep the refusal, reword the DoD to "an asset put in service in a period already run is caught up by the next run; a period closed without a run must be reopened", and name the locked-year path in the ops doc; or (B) make contiguity a charge-level rule per asset and let a run for P post the missed periods as catch-up rows dated in P. *(major)*

13. **IAS 16.39 is not honoured.** An upward revaluation after a previously expensed write-down must first reverse the loss through profit or loss; decision 9 sends the whole increase to 3500 and decision 3 tracks no per-asset expensed loss. The machine's random "revaluations up and down" will produce this. Either add a fourth delta (`revaluation_loss_delta`), credit `6820` first up to the cumulative loss, and add the step-3 literal (DESK-01 1 250 000 back to 1 500 000: Cr 6820 150 000, Cr 3500 100 000); or keep "revaluation basic", say in the report that 16.39 reversals are not modelled, and exclude the up-after-down sequence from the machine. Evolution does not track prior write-downs per asset either, so building it makes Vinea more correct than the reference. *(major)*

14. **An Accountant cannot change an asset's depreciation policy.** Method, life, rate and residual sit under `fa:setup_manage`, the one permission the Accountant lacks, while decision 4 calls the category policies "defaults an accountant edits" and lets the acquisition form override them under `fa:transactions_post`. The Accountant already holds `gl:setup_manage` (`permissions.py:270`). Evolution: the accountant edits method and rate on the asset master; asset types and their accounts are administrator setup. Put policy edits (and probably category policy) under `fa:transactions_post` or grant the Accountant `fa:setup_manage`, and say which in the 0030 role back-fill. *(major)*

15. **The category lock has no exit once a run has posted.** Reversal of the acquisition is latest-transaction-only, so after one charge in a closed period a mis-categorised asset is stuck (wrong register grouping; wrong balance-sheet class once categories carry their own accounts; policy is *not* stuck, decision 10 fixes it). Evolution allows a category change on the master with a warning; history stays where it was and future postings follow the new type, which decision 3's per-row account snapshots already support. Either allow a change when old and new categories share all three accounts, or schedule a `reclassification` kind as a GAP, and name the write-off-and-take-on exit in the ops doc.

16. **A manual IAS 16.41 annual transfer will be double-counted.** Clause 8 lets other modules journal 3500; decision 8 then transfers the full ledger `S` on disposal, so an accountant who books the annual excess-depreciation transfer by journal has 3500 go negative. The register ties 1610/1620 to the trial balance but never 3500 to Σ `surplus_delta`. Either document that 3500 is fa-owned and must not be journalled per asset (GL Defaults help + ops doc) and add a register finding when the GL 3500 ≠ Σ surplus, or build the transfer as a module act. Evolution has no automatic transfer either; users journal it, which is exactly this conflict.

17. **`period_not_monthly` is reachable today.** `create_fiscal_year` allows partial first/last months and `tests/kernel/test_periods.py:127-140` already creates a year starting 15 March. Decision 6's "which no tenant can currently produce" is false; as written the refusal blocks depreciation for such a tenant entirely. Evolution charges a partial period in full (13-period years are common). Replace the parenthetical with: a partial period counts as one depreciation period and takes a full charge; `depreciated_through` must be a period end, not "a month end".

18. **The seed proves the per-account tie with n = 1.** All five categories sit on 1610/1620/6800 and the tape has one branch axis with two keys but one account pair, so decision 7's `(account, branch)` grouping, clause 1's "for every `fixed_asset` control account" and the register's per-account totals are never exercised with two cost accounts. P5, the cited precedent, seeded two inventory control accounts. Seed a second pair (e.g. 1611/1621) for `VEH` or `BLDG`, or put the machine's three categories on two pairs. Evolution asset types carry cost, accumulated, expense and profit/loss accounts each.

19. **Additions/improvements after capitalization, cost adjustments and depreciation adjustments are neither built nor listed out of scope** (unverified; Evolution has all three). Add them to the out-of-scope list or as a GAP with a phase.

20. **Precondition (b) is a blocker with nowhere it gets recorded.** `docs/approvals.md` has no P9 row and its own preamble says an unwritten approval cannot later be told from an assumed one; P8 discharged the same precondition by inference ("the owner handed it over", `p8-step-1-report.md:23-27`). Say that the owner's approval of this review, quoted, is that confirmation, and have step 1's report record it in the owner's words.

## C. Mechanisms two competent builders would build differently

21. **"Charged through P" is undefined for an asset with nothing chargeable.** Decision 3 counts rows; decision 7 says a run with nothing chargeable posts nothing; decisions 8–10 require "charged through the period before P". A fully depreciated straight-line asset, a reducing-balance asset at its floor, a take-on with `accumulated == cost − residual`, or an asset in service after the run's period never gets a row, so `depreciation_behind` fires forever and it can never be disposed, transferred or revalued. The straight-line formula also has no value at `periods_charged == useful_life_months`, reachable on day one through a fully depreciated take-on. Pin: an asset with no chargeable amount in a period counts as charged for it, no row and no line, and the timing refusals consult chargeable periods rather than row counts; `depreciation.py` returns 0 when remaining life is 0. Separately decide the reducing-balance close-out (the rounded charge becomes 0 at NBV 23 on VEH-01 after 629 months and the asset never closes; Evolution users write such assets off or set a residual). Evolution excludes fully depreciated assets from Process Depreciation and still lets them be disposed. *(major)*

22. **Reversal mirrors carry no header source link.** `posting.reverse()` builds a `ReversalRequested` with no `source_doc_type/id`, so DEP-000006 and every reversed ACQ/DSP/RVL/AXF has a NULL header link; the GL entry page's resolver (`gl.py:288-368`) has no `fa` hop, so step 8's "Reverse via …" link renders as text for every mirror, the exact P7 hole. Invariant clause 3 ("every journal line on those accounts belongs to an entry whose `source_doc_id` is an `fa_documents` row") is unsatisfiable for mirrors as written. Fix: post `ReversalRequested(…, source_doc_type="fa_document", source_doc_id=doc.id)` directly under `module_reversal("fa")` (the fields are inherited from `PostingEvent` and `_write` stamps them), add an `fa` entry to the resolver keyed on `fa_documents.journal_entry_id` / `reversal_entry_id`, and write clause 3 over lines or via `reverses_entry_id`. Note: decision 7 omits `module_reversal("fa")` for the run reversal; a builder using plain `posting.reverse()` is refused `reverse_via_module_document`. *(major)*

23. **The reversal shape is P7's, not P5's, and the prompt never says which document owns the mirror rows.** `fa_documents` gets a status flag and `reversal_entry_id` (the `fx_revaluations` shape); DEP-000006 is a journal-entry number with no `fa_documents` row. Say explicitly: mirror `fa_transactions` rows carry the original's `document_id` and are identified by `reverses_transaction_id`; `/fa/documents` lists the original as reversed with its mirror entry linked beneath it. *(major)*

24. **"Non-reversed", "latest" and "dated after" are undefined for mirror rows and same-day rows.** At row 14 the newest rows on every asset are the DEP-000006 mirrors; if they count, DEP-000004's refusal names DEP-000006 and clause 4's "a cancelled asset has no non-reversed row" is false. RVL-000001 and DEP-000004 are both dated 30 Apr and decision 9 requires that order. Define: a row is non-reversed iff `reverses_transaction_id IS NULL` and no row reverses it; "latest"/"later" is by greatest `fa_transactions.id` among non-reversed rows, never `transaction_date`; write clause 9 by id. *(major)*

25. **A revaluation dated inside P after P's run breaks clause 4 at intermediate dates.** The run is dated at P's end; a revaluation dated 5 Apr eliminates accumulated that includes the 30 Apr charge, so `asset_figures(as_at=10 Apr)` shows negative accumulated and NBV overstated by P's charge. Pin the revaluation's date to the period end (the form takes a period), and add a mid-period `as_at` property. Evolution's revaluation is a period-level act after the period's depreciation. *(major)*

26. **Take-on inputs are not consistent by construction.** Row 4 omits VEH-01's `in_service_date` though decision 5 says both sources take it; `periods_used` duplicates what the two dates imply and nothing refuses a disagreement; `remaining_periods` hits 0 or negative for a take-on with `periods_used ≥ life` (`useful_life_exhausted` exists only for policy edits); and a `depreciated_through` before Vinea's first period has no period to charge from (`find_period` raises `no_accounting_period`). Pin: derive `periods_used` from the dates by default, require `in_service_date ≤ depreciated_through`, floor remaining life, and start charging at the first period Vinea holds. Evolution captures purchase date, depreciation start date and accumulated to date and derives the rest. *(major)*

27. **Posting maps produce zero-amount lines the kernel refuses** (`zero_amount_line`, `posting.py:516-521`): take-on contra when fully depreciated, revaluation cost line when `F == C`, disposal gain/loss when `P == NBV`, accumulated lines when `A == 0`, transfer with `A == 0`, and every write-off's "Dr 1660 P" (row 15 already assumes the omission). Add to decision 1: a zero line is omitted; a document whose every line is zero posts no entry. Refuse a split whose share rounds to zero.

28. **The DEP and FAX valueless claimants need `doc_type_column="doc_type"`.** `fa_documents` holds five doc types with one `number` column; the nil-VAT shape the prompt cites has one type per table. Without the narrowing, a project-only `AXF-000001` is counted in the DEP run and `assert_ledger_invariants` fails on the prompt's own machine. Cite `_VALUELESS_STOCK_DOCUMENT` (`sequences.py:228-230`) as the shape. Also say a reversed valueless run claims no number.

29. **`run_as_job=true … the P7 precedent` does not exist.** `FX_REVALUATION_JOB` has a handler and no enqueue site; `POST /gl/fx-revaluations` posts synchronously. The real enqueue shape is the statements endpoint (`subledger.py:1291-1316`, a separate 202 `JobRead`). Name the shape, its response contract, and which rule-14 entry covers it.

30. **The uncapitalized-purchases tie breaks on any non-fa credit on 1650** (a debit note against an uncapitalized purchase, a cashbook refund, a manual journal). P6's actual clearing proof (`tests/order_entry/invariants.py:260-320`) nets *all* non-module lines into "booked"; the prompt's per-line report does not. Define the tie the P6 way with an "other credits" reconciling line, or let a credit line be applied against a debit line. Same for 1660. Evolution has no clearing account, so this tie is the price of the deliberate departure. *(major)*

31. **The advisory lock has no precedent, no key and one taker.** Nothing in `app/` takes `pg_advisory_xact_lock`; the period `FOR SHARE` does not serialize two runs. The stated hazard (an acquisition's catch-up read) is harmless by decision 6's own rule; the real race is a disposal/transfer/revaluation dated in P committing concurrently with the run for P. Give the key and make every posting act take it.

32. **AP/AR document lines have no stored link to their journal lines**, so "Capitalize / Capitalized in ACQ-n" per 1650 line on `/ap/documents/{id}` cannot be joined by account alone (SIN-1 has two 1650 lines). `journal_lines.source_line_id` exists and partner line ids are reserved before posting; have `post_document` set it on net lines, and key the step-4 state endpoint by `journal_line_id`.

33. **Category account edits after assets exist are undefined** (critic, unverified). Decision 3 snapshots the three accounts per row "so a later category edit must not re-attribute history", but decisions 5, 7, 8, 9, 10 all post to "the category's" accounts. Say the posting maps use the asset's snapshot (its acquisition row's accounts) or refuse account edits on a category with assets.

34. **The depreciation report's tie fails on a range that splits a catch-up** (critic, confirmed by construction). The report keys by `charge_period_id`, the GL movement by entry date; January alone reads 442 500 by charge period against 427 500 on 6800. Define the range by run date with catch-up rows shown under their run, or tie by run.

35. **Clause 3's "1:1" cannot mean row↔line** (critic, unverified): the run aggregates by `(account, branch)`. Write the grouping key, and say whether an acquisition posts one line per asset or per account.

36. **Which branch the clearing, proceeds and surplus lines carry is unstated** (critic, unverified). A branch override at capitalization leaves 1650 +X on the source line's branch and −X on the asset's forever, and the trial balance filters by branch.

37. **Back-fill mixed case.** Conversion is per account, the category null-out is phrased per tenant, and the two-tenant test covers none/both only. State the rule for "1610 has lines, 1620 clean" and add a third tenant. Also: `_rebuild_enum` lives inside 0018 and is not importable, and `gl_control_type` types two columns (plus the guard function's `v_control`); say "copy the helper with both columns".

38. **`not_a_plain_account` duplicates `invalid_gl_setting_account`.** Route the six keys through `_SETTING_ACCOUNT_RULES` with classes (1650/1660 asset, 3500 equity, 4500 income, 6810/6820 expense), add them to `SETTINGS_ACCOUNT_FIELDS` for the composite FK, and to the Defaults page's key list.

39. **`code` uniqueness traps a re-acquisition.** A cancelled asset keeps its code, `code` is unique per company, assets are never deleted and `code` is not in the editable set, so the re-acquired laptop must be LAP-01A. Use a partial unique index on non-cancelled assets or let a take-on target a cancelled row.

40. **Decision 9 lets the revaluation form change life and residual under `fa:transactions_post`**, which decision 10 reserves for `fa:setup_manage` (critic, unverified). Resolve with item 14.

## D. Harness and process corrections

41. **`tests/kernel/test_property_ledger.py:22-26` draws manual journals on 1610 and 1620** (critic; confirmed). Once decision 2 converts them, the P2 machine goes red with `control_account_direct_posting`. Step 1 must drop the two codes from that pool in the same commit as the seed change, or step 1's own rule ("the phase must not inherit a red nightly") fails on P9's first push. *(blocker for step 1)*

42. **`tests/kernel/test_posting.py:296-308` pins the stub test to `DepreciationPosted`.** Re-point it at `ManufactureCompleted` or `PosSaleCompleted` in the commit that retires the stub; the name survives so the failure is an assertion, not an import error.

43. **Rugari has no EBM device after `make db-reset`.** The precondition sentence is copied from P8 and false: `seed_e2e.py` creates no device, the P7 specs register one at their start and suspend it at their end. Keep the fiscalized proof on the backend fiscal fixture at step 3; the seeded "Disposal of assets" item needs `fiscal_class_code`, a UOM mapping and a tax mapping, not only a sales account.

44. **`session_replication_role` is superuser-only**; the suite's `vinea_app_test` role cannot set it. Say stored-row clauses are broken through `admin_engine` with `DISABLE TRIGGER` in a `finally` (the `test_checker_sensitivity.py:131` pattern). VN014 blocks UPDATE/DELETE only, so a rogue INSERT needs no bypass.

45. **Rugari's seeded FA rows must be idempotent** (the seed is get-or-create and its test runs it twice) and the seeded run's period must sit between the period the seed closes and the current month (later periods are `future`). Say the run screen will show the closed period as a "closed with no run" finding on Rugari, deliberately.

46. **`ci-e2e-groups.test.ts` proves the partition; it does not place a spec.** Step 9 needs a `- group: p9-tape` matrix entry in `ci.yml` (args on one line) and `p9-cycle-tape` added to all three identical rest-N exclusion regexes.

47. **"`db-reset` at the start of the file only" is wrong for an own-company tape.** The P8 tape performs no reset; it signs up its own company and each CI shard stands up a fresh stack. Copied verbatim from P8.

48. **The placement rule is Maintenance-specific.** In Transactions the live tail is Tax, not Order Entry; Enquiries and Reports have no tagged tail. State once: Fixed Assets goes last among live blocks in every intent, before any phase-tagged rows.

49. **"Two mandatory STOP gates" vs "Gate steps (2, 5, 9)" and step 9's own STOP.** Say three.

50. **Frontmatter names three reports; the body and DoD have four.** Add "uncapitalized purchases"; list it as a plan addition in the deviations.

51. **`document-route.ts` is not named.** `sources.py` yields a routing key; the frontend map (`ROUTES`, seven keys, unknown → plain text) and its spelling test must gain `fa_document`, or the GL entry page's link renders as text with every backend test green.

52. **Models live in `app/models/fixed_assets.py`**, not in the package; `sources.py` and the enum export import `app.models.*`, and the boundary test would otherwise refuse them.

53. **The enum drift gate is vacuously green**: P9's enums reach the browser only if appended to `EXPORTED`; the frontend literal guard needs the P9 field and type names too.

54. **Nav rows carry a `permission`** (critic; confirmed, `nav-tree.ts:20-36`); the prompt names none for thirteen rows, and the run screen straddles `fa:reports_view` / `fa:depreciation_run`. The line-state endpoint's permission is also unstated: a Sales Manager opening an AR document would take a 403 from an `fa` read.

55. **Screens named that exist under other names.** "The periods screen" is the Accounting periods tab of `/maintenance/company-details` (no tape has driven it yet); "Trial balance screen" should be `/gl/enquiries/trial-balance`, the route the P8 tie used.

56. **Step assignment overlaps** (unverified): policy edits appear at step 1 and step 3 and need step 2's `asset_figures`; document/run listings appear at step 2 and step 5. Step 2 is also the largest single session in the series and could split 2a/2b as step 7 does.

57. **Smaller pins:** the guard's registration site ("from the app factory, beside the job handlers") should name the import that does it; parents of the six new accounts under 1600/3000/4000/6000; `annual_rate NUMERIC(9,4)` is a percent, not a rule-6 rate, say so; the `[property]` census marker the nightly greps; step 6's "New asset" links to a step-7 route; "Reverse via fa document" will show the raw module code (add a label map next to `documentHref`, i18n'd); DoD "every document ties 1:1 to its entry" is false for the two valueless kinds; `Idempotency-Key` on the reversal of a valueless document has no column to live in and the P8 owner decision said reversals carry none.

## E. What held up

Every tape money literal, entry, register total and sequence number; the enum-rebuild claim (0018); `ReversalRequested` numbering and `module_reversal`; the four period statuses and `assert_period_open` giving `period_not_open` before any write; ROUND_HALF_UP; the DB guard function needing no change for the two new control types; DocType/prefix widths (`NUMBER_WIDTH = 6`); role names and the jsonb role back-fill; the GAP wording and matcher; routes, report pattern, screenshot directories, approvals/final-report forms; the GAP labels for steps 2 and 3; the four-report count in the body; the elimination method, per-asset surplus and disposal-only realisation (IAS 16.35(b)/16.41-compliant); the branch-transfer four-line entry and the post-transfer charge landing wholly on the new branch; the clearing-account route as a stricter-than-Evolution and correct choice; the wear-and-tear "second book" statement; "one convention per company".

## F. Where the prompt is open-ended, what Evolution does

- Depreciation start: Evolution offers "from purchase date" or "from the first of the next period" per asset; the prompt's full-charge-in-service-month is one of those, stated clearly.
- Reducing balance: annual rate on the opening-year NBV spread over the year's periods (item 11).
- Fully depreciated assets: excluded from Process Depreciation, still disposable (item 21).
- Missed periods: processed "up to" a period, outstanding charges booked into the period being processed (item 12).
- Revaluation: a period-level transaction after the period's depreciation (item 25); posts to the reserve on the asset type without tracking prior write-downs (item 13).
- Category change: allowed on the master with a warning, history stays (item 15).
- Asset types: carry cost, accumulated, expense and profit/loss-on-disposal accounts, method and rate (items 14, 18).
- Take-on: purchase date, depreciation start date, accumulated to date; the term is derived (item 26).
- Disposal proceeds: excluding VAT, invoiced through AR when VAT applies (item 9).
- Reports: Asset listing, Depreciation, Disposals, plus an asset movement/history report; "uncapitalized purchases" is a Vinea artefact of the clearing route (item 50).
