# Phase 6 — Order Entry & three-way match: final report

Order entry is the phase where the ledger, the subledger and the stock ledger have to agree
about one document at a time. The shape that makes that possible is **decision 2**: a
stock-bearing partner document posts *two* entries in one transaction — the partner side and a
companion `inv` entry — and the stock side goes first, so the money side knows what the goods
were worth. Everything else in the phase is downstream of that, including the thing an operator
actually asks for: a purchase order that becomes a receipt that becomes an invoice, with the
accrual account proving itself at every step.

**Nothing an order holds is a column.** Invoiced, received, remaining, backordered, committed,
on order — every one is a query over lines, exactly as a GL balance is a query over journal
lines. The workflow status columns that do exist are written only by the service that changed
the fact underneath them, and `verify_order_statuses()` re-derives them after every posting test.

## What landed

| Step | What |
|---|---|
| 1 | Masters, settings and schema: item lines on partner documents, `items.purchase_account_id` / `weight_per_base_unit`, `ItemType.KIT` + `item_kit_components`, `ControlType.GRN_ACCRUAL`, the four `gl_settings` keys, `SO`/`PO`/`GRN`/`LCA`/`STK` numbering, the Rwanda seed pack and the back-fill |
| 2 | Goods receipts, the accrual posting, the three-way match and PPV |
| 3 | Sales and purchase orders, the derived quantities and their views, statuses, the order → document flows, kits and Breakup |
| 4 | Landed cost (Importation Split): shares by value, quantity or weight, the stockless-target rule, reversal |
| 5 | The acceptance tape, the enquiries and listings, the drill targets |
| 6 | Maintenance → Order defaults, and the Kit components section on Items |
| 7 | The transaction screens: item lines on the four P4 documents, sales order, purchase order, GRV, landed cost, Breakup |
| 8 | The two order enquiries and the four Order Entry reports |
| 9 | The debt register cleared, the depth pass, the tape through the screens, and this report |

## The invariant

`assert_order_invariants` runs after every posting test and in the property suites. At any date
and **per branch**: the GRN accrual account's balance equals Σ over GRN lines of (received value
− relieved value); the landed-cost clearing account equals Σ booked − Σ allocated; every line on
the accrual carries an `item_id`; and `verify_order_statuses()` agrees with the derived state.

Step 9 adds the tie from the outside, through the screens: `frontend/e2e/p6-cycle-tape.spec.ts`
drives procure-to-pay and order-to-cash from the UI on a company it signs up for itself, and
reads the trial balance back as rendered strings.
`backend/tests/order_entry/test_cycle_trial_balance.py` drives the same sequence through the
services. Both assert the same hand-worked table; neither reads the other's answer.

| Account | Debit | Credit |
|---|---|---|
| 1300 Inventory | 31,000 | 12,400 |
| 2350 Goods Received Not Invoiced | 25,000 | 25,000 |
| 1370 Landed Cost Clearing | 6,000 | 6,000 |
| 2100 Accounts Payable | — | 31,000 |
| 1200 Accounts Receivable | 20,000 | — |
| 4100 Sales Revenue | — | 20,000 |
| 5100 Cost of Goods Sold | 12,400 | — |

The 12,400 is the figure worth driving a browser for: 25 units arrived at 25,000, the freight
added 6,000 to those same 25, so the average is 1,240 and ten leave at 12,400. No screen shows
that average and every screen depends on it.

## Decisions as built

The fourteen decisions the phase prompt locks were built as written. Six are worth restating
because later phases will read this rather than the prompt:

1. **Item lines coexist with GL lines** on `partner_documents`; a line's account is resolved by
   the service — AR from the item's sales account, AP stock from the accrual, AP service from
   the item's purchase account.
2. **Two entries, one transaction** (decision 2). The companion draws from its own `STK-` run,
   and a document with no valued stock line has no companion and claims no number.
3. **Orders post nothing** (decision 3). Close releases the remainder and keeps the history;
   Cancel is only available while nothing is fulfilled.
4. **The accrual is a control account, the clearing account is not** (decision 5) — freight
   arrives as a line on a forwarder's invoice and duty as a cashbook payment, and a control
   account would refuse both.
5. **The match relieves at the frozen base value** × (matched ÷ received), the last match of a
   line taking the remainder, difference to PPV. Price *and* rate movements are PPV.
6. **A kit is a virtual bundle** (decision 8), exploded at entry and stored on the line, so an
   order keeps what it was keyed with while the catalogue moves under it. Components are stock
   or service items only — enforced from step 9, see F-9.1.

## Findings, by step

F-numbers are per step, as each step's report used them. What is listed here is what a reader of
the phase needs; each step's PR body carries the full set.

### Step 5
* **F-5.1** The census the property generator prints was a courtesy, not a gate: three
  consecutive deep passes came back green with a different censused refusal at zero each time.
  The floor makes it a gate.
* **F-5.2** The acceptance tape found a customer return coming back at *today's* average rather
  than the cost it left at.

### Step 6
* **F6-F5** An empty state that could not tell "no rows" from "the request failed". Fixed on the
  screen it was found on; the sweep is step 9's (F-9.6).
* A kit row with an item and no quantity was silently dropped rather than refused.

### Step 7
* **F-7.1** A new order defaulted to the first warehouse in the list rather than the company's.
  Found only because another spec had created a second warehouse; a suite with one warehouse
  could never have seen it.
* **F-7.2** The reset dialog invented its own wording instead of falling back to the service's.
* **F-7.3** A landed-cost refusal landed on the document rather than on the receipt line it
  names.

### Step 8
* **F8-F3** The same empty-state defect as F6-F5, on a different screen — which is what made it a
  sweep rather than a fix (F-9.6).
* **F8-F7** Step 7's two e2e cycles were intermittent. Recorded as timing; it was not (F-9.7).
* A **500** the sales-order enquiry had been serving for three steps: `EnquiryLineRead.description`
  was required and the column is nullable. The endpoint shipped at step 5 with no caller, so
  nothing asked it a question until step 8 built the screen — the rule-13 failure exactly, one
  layer below a screen.

### Step 9
* **F-9.1** `replace_kit_components` accepted a **non-stock** component, against decision 8. Such
  a line commits nothing, relieves nothing and costs nothing, and would take a share of the kit's
  revenue with it. Refused at the definition, and the picker no longer offers one.
* **F-9.2** The sales-order listing's backorder column summed base quantities across lines
  counted in different units — 3 kg short and 2 crates short read 5. It is a **count of short
  lines** now; the quantities live on the order, each with its unit.
* **F-9.3** The module-first ordering in `_module_document` was recorded at step 8 as
  unprovable. It is provable: a goods receipt's entry has a source link and no
  `inventory_documents` row, so inserting a bare header pointing at it makes the entry disagree
  with itself — no forbidden UPDATE required.
* **F-9.4** `make be-test` with `-n auto` exhausts `max_locks_per_transaction` on a 16-core
  machine: every worker migrates its own database and the lock table is the *cluster's*.
* **F-9.5** The suite rewrote ten committed PNGs on every run, so a green run left a dirty tree.
* **F-9.6** **Forty-two** listing, report and enquiry screens could not tell "no rows" from "the
  request failed" — the same defect P4 shipped six times and P6 met twice. Two were worse than
  the rest: the account enquiry ended its `.catch` at `console.error` and rendered "No
  transactions" over a full account, and the company-details periods table rendered itself with
  no rows, showing a company that appears to have no accounting periods.
* **F-9.7** Step 7's two cycles were not flaky for a timing reason. `OrderWorkspace` rendered
  three blank, editable rows over an order it had not loaded, and replaced the form wholesale
  when the query landed; anything typed in between was discarded without a word. The specs were
  waiting on the heading, which the shell renders before the order arrives. A person typing
  quickly loses their keystrokes the same way, so the fix is in the screen.
* **F-9.8** One assertion in those cycles was vacuous rather than flaky:
  `getByText("Processed")` matches "Unprocessed".
* **F-9.9** The sales-order **enquiry** reported a backorder against every line whose item has no
  shelf — a kit line read 2 while the four bottles it explodes into read 4 on the row below, the
  same promise counted twice, and a delivery charge read its whole quantity every time. The
  listing and the enquiry also *disagreed* about which lines were short, which both of them
  promise they cannot do. Found by driving order-to-cash through the screens and comparing the
  two, which nothing had done for an order with a kit on it.
* **F-9.10** `reverse_lca` chose its date by `pick % 2`, and Hypothesis shrinks every value
  toward its zero — so in the examples a deep pass spends most of its budget near, `pick` is 0
  and every boolean in the plan is False. Four deep passes counted the closed-period split as
  10 of 24, then 0 of 20, then 0 of 20, then 0 of 21. It alternates on a counter now: half of a
  rare event by construction rather than in expectation, and the two passes since read 7/7 and
  15/14.

  **That was one cause, not the cause, and the rest is `main`'s.** The floors sit at 3, the
  guards clear them by 5–15, and the deep ones are *conjunctions*: `grn_matched` needs a
  receipt, a match against it, and then a `reverse_grn` landing on that receipt — three
  operations deep in a 24-step plan. A seed that runs short on open sales orders zeroes it
  (`invoice_from_so: an open order to invoice` was 2 on one pass and 23 on another).

  Measured rather than argued, because the generator on this branch is `main`'s — one line
  differs, the `reverse_lca` date — and a claim of "pre-existing" from reading a diff is not
  evidence. **`main` at #45, deep profile, three runs: one passed, two failed**, the second
  naming `{'grn_matched': 0, 'weight_missing': 0, 'period_not_open': 0}` and the first
  reproducing the closed-period correlation at 1 reach. A gate that fails two runs in three is
  not a gate.

  **Not fixed here**, because the fix is a cost decision rather than a defect repair. The
  obvious lever is the deep profile's `max_examples`: 300 → 600 roughly doubles the reach
  counts and lifts the conjunctions clear, at the price of doubling the nightly (the whole
  `-m slow` set is 26 minutes at 300). Biasing the draw was tried at step 3 and bought nothing.
  Whoever picks the number should pick it against the reach counters, which is what they are
  for. Until then a red nightly on this suite is not necessarily a regression, and that is the
  part worth knowing.
* **F-9.11** `ci-e2e-groups.test.ts` reads `args:` with a line regex and cannot see a YAML block
  scalar. Written that way, five spec files would have looked named by no group and matched by
  the shard filter, been scheduled twice, and the partition test would have reported green over
  it. A block scalar now fails there by name.

## Plan deviations

1. **Reports → Order Entry and Enquiries → Order Entry are additions to the owner's tree.**
   Recorded as Appendix C.1.8. The appendix's Enquiries and Reports sections carry no Order Entry
   at all, while the phase's own step list names six screens. The Goods received report is the
   one worth naming twice: its unmatched total is what decision 5 makes the accrual balance, so
   it is where an operator watches the accrual prove itself against the trial balance.
2. **Transactions → OE is Sales order, Breakup and Landed cost**, not the appendix's "Purchase
   order, Breakup". Recorded as Appendix C.1.9 at step 9. The purchase order lives in the
   owner's AP block, where the tree puts it; Landed cost is a row decision 9 needed that the
   appendix never had.
3. **The backorder is a count at order level.** The plan asks for the backorder "on the order,
   the enquiry and the grid" (decision 7) and P6 built all three. What step 9 changed is that
   the order-*level* figure is a count of short lines rather than a quantity, because base
   quantities across units are not addable.
4. **`inventory_documents` is not the only `inv` document.** P6 puts three more kinds under the
   `inv` module — goods receipts, landed costs and the companion entries of partner documents —
   so the GL entry page's module link resolves through the entry's own source link when the
   module table has no row.

## What was deferred, and where it landed

* **Assembly of a kit into stock, and nested kits** — P12. Decision 8 is explicit that a kit is a
  bundle, not a manufactured thing, and the explosion is one level so it terminates by
  construction rather than by a depth limit.
* **Tolerance on `receipt_exceeds_order`** — not in v1, and it has no phase home yet. A receipt
  may not exceed its order line at all.
* **Reservation** — out of scope, and the figure it would change is documented as a snapshot on
  every screen that shows it: nothing in the system holds stock for an order, so each order is
  told what it would be short of if everyone else were served first. The per-order figures are
  therefore **not additive**, and no screen totals them.

## The rule-14 register at phase end

Four lines, and P6 carries none of them:

| Endpoint | Kind | Reason |
|---|---|---|
| `POST /subledger/jobs/sweep` | `by design` | The retention reaper. It fails jobs abandoned by a restarted process and deletes expired artifacts on a schedule; there is no moment at which a person wants to press it. |
| `POST /operator/tenants/{id}/activate` | `GAP (SaaS admin, C.2)` | The operator console has no screens in any phase yet. A `GAP` rather than `by design` because the plan says it is coming. |
| `POST /operator/tenants/{id}/suspend` | `GAP (SaaS admin, C.2)` | As above. |
| `POST /operator/tenants/{id}/impersonate` | `GAP (SaaS admin, C.2)` | As above. |

There were twenty P6 entries at one point. The phase builds its services in steps 1–5 and its
screens in steps 6–8, so between those two points the endpoints existed and nothing called them;
each entry named the step that would delete it, and each step did. Every one was deleted by a
screen a person can open and press, not by a hook written to satisfy the matcher — which is what
`useReverseStockDocument` was (C.1.7), and what the AR/AP reversal was for a whole phase.

## What P7 inherits

* **A phase of screens with no regression against their *data*.** Step 9's sweep put every
  listing, report and enquiry behind one `QueryState` and banned the old idiom by name, but the
  thing that found F-9.9 was a person's sequence driven through a browser, not a unit test.
  `p6-cycle-tape.spec.ts` is the shape to copy: drive it, then read a figure back.
* **A census that does not currently hold.** The floors clear by 5–15 against a floor of 3, and
  `main` at #45 failed two deep runs in three. Step 9 removed one cause (a rare branch drawn
  from a shrunk integer's parity rather than alternated) and measured the rest; see F-9.10 for
  the numbers and for the lever nobody has pulled. **P7 should not read a red nightly on this
  suite as a regression without checking the reach counters first** — and should decide the
  `max_examples` question rather than inherit it a third time.
* **The `NO_UI` register at four lines**, three of which are the SaaS admin console. P7's
  fiscalization queue is the next thing that could add to it, and the rule is the phase's: build
  the screen, or write the line.
* **`docs/screenshots/p6-step-9`** for the four screens step 9 changed: the sales-order listing
  with its count of short lines, the enquiry where the quantities live, a refused query saying
  what the service said, and a trial balance that foots over a company a whole phase has posted
  into.
