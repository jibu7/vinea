# P7 — Rwanda fiscalization and VAT returns: the phase report

**Status: code-complete, certification pending.** Everything the phase prompt specifies is
built, tested and reachable from a screen. It is not certified, and it cannot be on this phase
alone: nobody holds RRA test-environment access (the Definition of Done's precondition (d)), and
the checkpoint sheet requires training receipts, a proforma receipt and a PLU report, all three
of which the prompt puts out of scope. `docs/rra/certification.md` is the runbook, maps all 75
checkpoint rows, and says exactly what remains.

What the phase actually claims is narrower and is worth stating plainly: **a sale that reaches
the ledger reaches RRA, or somebody can see why it has not.** The rest — the receipt layout, the
X and Z, the VAT return, the revaluation — is built on that one property.

---

## What landed, step by step

| Step | PR | What it built |
|---|---|---|
| — | #47 | The phase prompt, frozen. |
| — | #48 | `docs/rra/` — the three RRA specifications pinned with their SHA-256, the checkpoint sheet, the logo, and `contract-notes.md`: what nine live EBM receipts settled about rounding, item codes and the printed tax. |
| 1 | #49 | Masters, settings, schema and the adapter skeleton. Migration `0022_p7_fiscal`: eleven tables, the columns on `partner_documents` / `items` / `uoms` / `tax_codes`, six `gl_settings` keys, five seed accounts, `VAT-IN-IMP`, the `DocType` entries and the six permissions, with `tests/test_p7_backfill.py` proving a pre-P7 tenant comes out able to fiscalize. `app/fiscal/` with the Protocol, `NullAdapter`, both route profiles, the payload models (one test per model against the documents' own JSON samples) and **the EBM sandbox** — a FastAPI app that answers as RRA does, mounted in-process by the backend suite and run as a container for the e2e. |
| 2 | #50 · **gate** | The posting contract. The hook in `post_document()`, ten post-time refusals each landing on the field it names, `invc_no` claimed and the `sale`/`refund` row inserted **before `post_document` returns**, item registration on first use and on change, the drainer with per-device FIFO and backoff under an injected clock, `unknown` / verify / attach / cancel-on-reversal, `normalize_receipt()` for both profiles, and `assert_fiscal_invariants` (six clauses). Hypothesis drives random sequences with the sandbox switched between modes. |
| — | #52, #53, #55 | Three fixes between gates: the residue bound derived rather than measured (both measured numbers were false), the CI install guard taught to read three more places, and audit history breaking ties on id. |
| 3 | #51 | Purchases, imports and stock reporting. `purchase` on every posted AP document, the EBM purchase feed with accept/reject and the no-double-registration rule, the import register, and `stock_io` + `stock_master` beside every `StockPosting` with the `sarTyCd` table. Four more invariants. |
| 4 | #56 | The VAT return (the query, the tie with untagged movements, late entries by high-water mark, `VatReturnPosted`, filing, reversal, the two annex CSVs), the X/Z computation and the Z close, and `FxRevalued` made real — `fx_revaluations` with preview, the posting and its next-day mirror, and the three refusals. `fiscal:close_day` added as its own permission (`0025`). |
| 5 | #57 · **gate** | The enquiries and listings — the queue per device and per row with its action log, the receipts listing and search, the item enquiry — and the **nineteen-row acceptance tape** as a test, every expected value a literal worked by hand, run under both route profiles. |
| 6 | #58 | Maintenance UI: **EBM devices**, plus the EBM class on Tax types, the RRA quantity unit on Units of measure, the Fiscal section on Items, Verify TIN on Customers and Suppliers, and the tax block on GL Defaults. Five register lines cleared. |
| 7 | #59 | Transaction UI: payment method, purchase code, *Refund of* and the reason code on the capture screens; the fiscal panel, the gated Print, the Copy print and the queue actions on the document; **Fiscal queue**, **EBM purchases**, **Import declarations**, **VAT return** and **FX revaluation**. Fourteen register lines cleared. |
| 8 | #60 | Enquiries and Reports UI: Fiscal receipts, Fiscal queue history, the VAT return report with both annexes, the Daily fiscal report with **Close day**, the Fiscal receipts listing, and the FX revaluation report. The last register line cleared. |
| 9 | this PR · **gate** | The tape through the screens, the Z's membership amendment, the sensitivity pass, `docs/rra/certification.md`, and the close. |

---

## Decisions worth review

**1. A Z owns receipts by counter, not by clock** (step 9, revision `0026`, amending decision
11). The one substantive backend change this step makes, and the reason is a defect step 8 found
and could not fix inside its own rule that the backend does not move: a Z's population was cut on
`sdc_datetime`, which is RRA's clock on RRA's server, while the close was cut on `now()`, which
is Vinea's. Under any skew a receipt in flight at the close returns stamped *before* the close,
lands inside a Z whose figures are already frozen, and the next Z opens exclusively after that
instant — a receipt in no day at all, on a report a revenue authority reads, and nothing asserted
otherwise. Membership is now `high_water(prev) < tot_rcpt_no <= high_water(this)`, and
`assert_fiscal_invariants` clause 12 asserts the days tile the device. See
`docs/p7-step-9-report.md` §A for the key's argument.

**2. The outbox is written by the posting, never by a handler.** A document is fiscalized because
it was posted, in the same transaction. Everything else in the phase — the print gate, the Z, the
listing, the invariants — depends on that being true rather than usually true.

**3. `failed`, `unknown` and `needs_receipt` block the device's queue.** Sending out of order is
worse than waiting: RRA answers `921`/`922` to a stock report that arrives before its sale, and
receipt counters are a sequence. The cost is that one refused sale stops a shop's declarations
until somebody decides, which is deliberate and is what the queue screen exists to make visible.

**4. An `unknown` row is never blindly resent.** *Verify with device* reads the authority's own
counters and decides; a person then keys the receipt off MyRRA. A resend would be a duplicate,
and `994` returns no receipt data, so the sale would be registered and unprintable.

**5. Reversing a fiscalized invoice queues a refund; reversing a refund is refused.** RRA cannot
un-sign a sale. A document therefore holds two receipts after a reversal, and `_receipt()`
returns the document's **own** — the header and the print gate are about the invoice, and the
`NR` prints as its own receipt naming the sale it refunds.

**6. The VAT return is reconciled, not balanced.** A VAT payment to RRA and a journal keyed
without a code are real movements no tax line explains. The report lists every one of them by
name under the account it moved, and the settlement entry it posts becomes one of them on the
next month's tie — which is why its lines carry the tax codes with `tax_amount 0`.

**7. The revaluation contra is `1290`/`2190`, never the control accounts.** `1200`/`2100` are
subledger-only and their balance is Σ open items at booking rates (P4's invariant, still asserted
after every row of the tape).

**8. No field name of the authority's exists outside `app/fiscal/`** — including on the read
side, where a Z totals a day through `normalize_declared_totals` rather than by reading `totAmt`
off a stored payload. `tests/fiscal/test_boundary.py` holds it, and it is what makes a second
country a package rather than a refactor.

---

## Plan deviations, with reasons

1. **The route-profile reading.** Decision 1 asks for "two route profiles in one table",
   selected per device by `fiscal_devices.profile`. That is what was built — the VSDC v1.0.5
   paths and the OSDC v1.0.1 paths, the payload vocabulary shared, `cmcKey` added on the `osdc`
   profile only, and `normalize_receipt()` mapping both sales-response shapes
   (`rcptNo`/`vsdcRcptPbctDate` and `curRcptNo`/`sdcDateTime`) onto one `FiscalReceipt`. The
   reading worth flagging is that these are **two servers**, not two path tables on one: OSDC
   speaks to the EBM 2.1 API server directly. Nothing above the adapter can tell, and the
   acceptance tape runs rows 0–3 under each. **Which profile RRA certifies a cloud vendor for is
   a conversation, not a code decision**, and the build deliberately takes no position.
2. **The VAT filing entry.** Decision 12 specifies Dr output VAT / Cr input VAT with the net to
   `2250`, lines carrying the tax codes with `tax_amount 0`. Built as written; what is worth
   review is the consequence — the settlement is itself an untagged movement on the next return's
   tie, named rather than absorbed, and a net *credit* position sits as a debit on the same
   account rather than on a second one.
3. **The purchase-registration rule.** An accepted feed row decides which registration RRA keeps:
   where the document's own `purchase` row has not been sent, the accept cancels it in favour of
   the confirmation; where it has, the accept queues nothing, because a confirmation would then
   be the second registration of one supplier invoice. Invariant 10a asks the same question from
   the authority's side, where the identity is (supplier TIN, supplier invoice number).
4. **Decision 6's line arithmetic came from Sage/Ishyiga, not from a pinned document.**
   `splyAmt = prc x qty`, `dcAmt = splyAmt x dcRt/100`, `taxblAmt = splyAmt − dcAmt`, tax at two
   decimals per line and summed into the header. The pinned PDFs win where they say otherwise;
   nine live receipts corroborate the per-line rounding (TESKO 10057 is the only one where the
   two candidate methods differ, and it comes down on the per-line side). `contract-notes.md`
   §7 states the provenance before the rule, deliberately.
5. **Decision 11 amended** — see *Decisions worth review* 1 and the approvals row.
6. **Decision 15 amended** — `fiscal:close_day` (step 4, revision `0025`). The frozen list named
   no permission for *closing* the day; `fiscal:reports_view` is a reading and a Z is an act, and
   putting it under `fiscal:queue_manage` would have granted it to whoever may press Retry.
7. **Appendix C renumbering.** The prompt reserved C.1.10 for step 7's Transactions → Tax block
   and C.1.11 for the FX revaluation row, written before it was clear that step 6 would need an
   entry of its own. It did — **EBM devices** is C.1.10 — so step 7's two blocks are **C.1.11**
   and **C.1.12**, and step 8's Enquiries → Tax and Reports → Tax are the other half of C.1.11.
   The step-8 report's "C.1.10" reference is corrected in the prompt file by this step's docs
   commit.
8. **Two literals in the prompt's acceptance tape were wrong**, corrected on the owner's
   direction at the step-5 gate: row 5's three drains are backoff *waits* (+0, +1, +6), not
   offsets from the posting; and row 6's `lastSaleInvcNo` is **6**, because sales and refunds
   share the `FIS` run — as row 3's `invc_no 3` and row 6's own expected `5/6 NS` both say.
9. **`_receipt()` returns the document's own receipt, not the latest.** The first attempt
   returned the latest, which made a reversed invoice's detail screen report the refund's
   counters as though they were its own — a panel saying something true about the wrong receipt.
   The audit of all five readers is in the function's docstring.
10. **`_untagged()`'s two clauses are asymmetric**, and that is the fix rather than an oversight:
    a `tax`-module entry that has been reversed drops out **together with** the entry that
    reversed it, so filing and reversing a range leaves the return it found rather than two rows
    that explain nothing and a tie that balances over them.
11. **The reversal's `NR` is reachable from the invoice**, because it has no document of its own
    and is a legal document the customer is owed. Moved into step 7 from step 8 as a condition of
    the step-2 approval.
12. **The Defaults screen edits five accounts, not six.** The prompt's step-6 line says six; rule
    (e) enumerates five — `2250`, `1290`, `2190`, `4410`, `6955` — plus the default purchase
    class code, which is six `gl_settings` keys in all.
13. **The register held fourteen `GAP (P7, step 7)` lines, not fifteen.** Its own prose was one
    out; corrected in the register rather than carried, because a count in a comment is the kind
    of thing a reader trusts.
14. **There is no "General Ledger → Period end" group** in the owner's tree, so the FX
    revaluation row sits directly after Cashbook batches under Transactions → GL. The listing
    routes are plural (`/tax/vat-returns`, `/gl/fx-revaluations`) because that is the repo's own
    convention; labels and placement follow the prompt.
15. **A partner document's stock report is enqueued by the document, not by the stock service.**
    The companion stock entry posts first (P6 decision 2), so a movement enqueued from inside the
    stock service would sit ahead of the sale that caused it. Invariant 11 keeps the exception
    honest.
16. **The RRA logo is a bordered placeholder.** The asset is pinned under `docs/rra/`; the layout
    prints a placeholder rather than an approximation of §7.29.
17. **The phase closes code-complete, certification pending** — the Definition of Done's second
    branch, taken because precondition (d) was never held.

---

## The sandbox questions: still open

The live run against `https://sdcsandbox.rra.gov.rw` did not happen, because nobody holds a TIN,
branch id and device serial approved on `myrratest.rra.gov.rw`. The owner confirmed that at step
5 and it is unchanged. Every question below is one only Kigali can answer, and none of them
blocks code that is written to the pinned documents. `docs/rra/certification.md` §5 says what
evidence settles each.

| # | Question | Built as | Costs, if the answer differs |
|---|---|---|---|
| 1 | Does RRA tolerate a non-zero `dcAmt` on an undiscounted line? | the residue between the two-decimal wire price and the posted line goes to `dcAmt` | a payload rule in `mapping.py` |
| 2 | Is a refund sent with positive amounts under `rcptTyCd R`, or negative ones? | positive, with `orgInvcNo` | a payload rule in `mapping.py`; the printed side is settled by checkpoint 56 |
| 3 | Does a copy need a `CS` counter? | no call to RRA; the label is a reprint | one call, and `copy_count` becomes a counter |
| 4 | Does `X` terminate a short item-code segment? | yes, padded to two characters | one function; RRA certified both conventions, so this is likely not enforced |
| 5 | Is the QR payload §7.24.7's? | yes | the payload string; the layout does not change |
| 6 | Does a live device treat *Verify* as a re-registration and reissue its keys? | it re-runs `initialize_device` | Verify needs its own read-only call, and decision 4 changes |
| 7 | `orgSarNo` (§9.1) and the purchase-reversal direction (§9.2)? | not exercised | new payload fields |
| 8 | Does Vinea's Z reconcile with a physical device's own daily report? | Vinea's Z owns receipts by counter | the Z prints its counter window so the comparison can be made by hand |

Two more, closed by decision rather than by evidence: **RWF decimals** — the wire is two and the
ledger is zero (architecture rule 6), and since step 8 that is also a *rendering* contract, so
the residue stays visible on screen as well as in the data; and **the sandbox's missing code
class 05**, which is a fixture gap in the in-repo sandbox rather than a product one and is closed
by the first live sync.

---

## Owner items

1. **Re-check RRA's "How to acquire EBM" page** and paste the current enclosure list into
   `docs/rra/certification.md` §1 with its date. The list there is as of 16 September 2026 and
   nothing in this repository can refresh it.
2. **The certification application has not been sent.** Six of its ten enclosures are documents
   that do not exist yet (brochure, warranty statement, user manual, installation guide,
   programming manual, support SLA).
3. **Test-environment access** on `myrratest.rra.gov.rw`. Everything in §3 of the runbook waits
   on it, and so do the eight questions above.
4. **Training receipts, proforma receipts and the PLU report** need a phase. Certification cannot
   complete without them.
5. **After P7 closes:** issue #54 (the audit index) as its own PR, and the `now()`-tie sweep from
   #20 — `fiscal/worker.py` orders by `sequence_no` rather than by timestamp, confirmed at step
   9; the `stock_moves` half of that sweep stays post-P7.
