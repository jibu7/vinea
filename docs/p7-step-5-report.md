# P7 step 5 — the enquiries, the listings, and the acceptance tape

Step 5 is a gate. It adds the backend half of the phase a person looks at, and then it runs the
tape: nineteen rows in one sequence, every expected value a literal worked by hand, all five
invariant suites after each one.

Written against `claude/p7-step-4` at `1361e52`. Step 4 is not yet on `main`, so this branch
continues it rather than branching from a pulled `main`.

**The tape reproduces every expected value in the build order.** Two of those values could not
be reproduced by the code as step 4 left it, and one of them was a defect. Both are below,
with the third — a 500 on the queue screen's most-pressed button — which the endpoint tests
found.

## Three findings

### 1. A Z netted refunds into its payment buckets

Row 9 expects `credit 112 704 · cash 19 172`. It read `credit 107 984`, because `_absorb`
subtracted a refund from the bucket its document named:

```python
signed = -declared.gross if is_refund else declared.gross
figures.by_payment_method[method] = figures.by_payment_method.get(method, ZERO) + signed
```

Step 4's reasoning for that was "the money went back out the way it came in", which is how a
till reconciles. **It does not hold in the code that implements it.** The method on the bucket
is the *credit note's* own `payment_method`, not the original invoice's, so a refund keyed cash
against an invoice sold on credit takes money out of a drawer it never went into. In the tape
it happened to agree, because CRN-1 is on credit like INV-1 — which is exactly the kind of
coincidence a round fixture hides.

And every other figure on a Z is split NS from NR: the counts, the gross, the taxable and tax
per rate. A payment bucket that quietly nets the two is the one number on the page a reader
cannot take apart again.

So sales and refunds sit side by side now — `by_payment_method` and
`refunds_by_payment_method` — and the identity a reader can check is

| | |
|---|---|
| Σ `by_payment_method` | == `ns_gross` (112 704 + 19 172 == 131 876) |
| Σ `refunds_by_payment_method` | == `nr_gross` (4 720) |

Neither held before, because the netted bucket summed to `net_gross` and said so nowhere.

### 2. A Z counted receipt lines, not items sold

Row 9 expects `items 43`. It read `9`, which is the number of *lines* on the day's six
receipts. 43 is Σ of the line quantities on the five sales — 10 + 1 + 5 + 2, then 3, 20, 1, 1.

`DailyFigures.items_count` summed `DeclaredTotals.item_count`, which is the authority's
`totItemCnt`. That field is right where it is used: `totItemCnt` **is** the line count, it is
what `builders.py` sends, and it is the receipt's own `ITEMS NUMBER` (CIS §7.27). A day's
report asks a different question — how many things were sold — and a Z that answered "4" for an
invoice of eighteen bottles would fail a checkpoint sheet.

So `DeclaredTotals` gains `quantity` beside `item_count`, computed by the adapter from the
line quantities, and the Z splits it `items_ns` / `items_nr` like everything else. The printed
receipt keeps the line count, where §7.27 puts it.

**The two figures are named apart on purpose**, because "items" meaning two different things on
a receipt and on a day's summary is precisely how this went unnoticed.

### 3. Verify on an unreachable device was a 500

Found by the endpoint tests rather than by the tape, and it is the worst of the three because of
*when* it fires.

`drainer.verify_with_device` calls `adapter.initialize_device` and caught nothing:

```
app.fiscal.protocol.FiscalTransportError: /initializer/selectInitInfo failed: ConnectError
```

No handler exists for `FiscalTransportError`, so it escaped as an unhandled exception. **The
likeliest moment anybody presses Verify is while the device is unreachable** — that is usually
why the row is `unknown` in the first place — so the button on the queue screen would have been
a 500 with no message on precisely the occasion it exists for.

It is a `QueueActionError` now, carrying the reason, which is the same translation `devices.py`
already makes at its three call sites. The row is left exactly as it was: nothing was learned,
so nothing moves.

The tape could not have caught this. It drives the service with the sandbox client injected, so
its device is always reachable; only asking the *endpoint*, where the app builds its own client,
reaches the path.

## Two places the tape's own literals could not be met, and why

Neither is a code change. Both are the tape's text disagreeing with itself, and in each case a
second value in the same row settles it.

**Row 6, `lastSaleInvcNo 5`.** The device holds **6**. Sales and refunds share the `FIS` run —
row 3's credit note is `invc_no 3`, as the tape itself says — so after five invoices and one
credit note the counter is 6. Row 6's own expected receipt, `5/6 NS`, says the same thing:
`rcptNo 5` within NS over `totRcptNo 6` across types. The rule the row is about is
`held >= ours`, and both sides are 6, so the outcome it expects (`needs_receipt`, then an
attached receipt) is what happens.

**Row 5, "drain at +0, +1 min, +5 min".** Those are the backoff *waits*, not absolute offsets.
The schedule is 1 → 5 → 15 (decision 4): a row attempted at +0 is next due at +1, and one
attempted at +1 is next due at **+6**. A third drain at an absolute +5 finds nothing due and
leaves two attempts behind — which is not the row's own expected "attempts 3 · `next_attempt_at`
+15 min". The tape drains at +0, +1, +6 and the fifteen minutes are measured from the third.

## What landed

**`app/fiscal/enquiries.py`** — five reads, and nothing in it is a column. The queue per device
(status counts, oldest-queued age in whole seconds, the `offline` flag, `last_success_at`, and
the **head row**, because every row behind it is waiting on that one); the row detail with its
frozen payload, the response and the action log; the receipts listing and its search; the item
enquiry.

`blocked` is derived rather than stored: the head is `failed`, `unknown` or `needs_receipt`,
each of which waits for a person. It is deliberately **not** the same as `offline`, which is
24 hours of queue (VSDC §2.2 item 4) — a dashboard that conflated them would cry wolf on every
refusal.

The receipts search is one box over four things somebody actually holds: the printed counter
(`3`, `3/4` or `3/4 NS`), the document number, the partner's name and the authority's invoice
number. The counter forms are parsed rather than matched as text, so the query uses the index
`fiscal_receipts` already carries for exactly this lookup.

The item enquiry lists items RRA has **never heard of**, and that is the point of it: decision 3
refuses a fiscalized line whose item has no class, so "which items would refuse a sale" is the
question, and a listing of `fiscal_items` alone could never answer it. `registered` and
`pending_rows` are separate fields because the interesting state is both at once — an item whose
class changed is registered *and* queued, and one "status" word would have to lie about one of
them.

**`app/fiscal/printing.py`** — decision 11's two backend rules. Print refused until the
authority has signed (`fiscal_receipt_pending`, CIS §10), carrying the queue status so the
button can show it instead; and a second print is a COPY, `copy_count` incremented and audited,
**with no second call to RRA**. A company that does not fiscalize gets `None` rather than a
refusal, which is decision 11's last sentence: `null` means "print the P4 layout", a 409 would
make every ordinary invoice unprintable.

**Two listings step 3 shipped and nobody had opened.** `GET /fiscal/purchase-feed` and
`GET /fiscal/import-declarations` are both named in step 5's brief, and neither had a test that
asked it for data — the same shape as step 4's `GET /gl/fx-revaluations/{id}`, which passed
review and returned 500 to every request. They answer, with the feed's `spplrTin 100000003` /
`spplrInvcNo 77` / `11 800` / `1 800` and the declaration's `IM 2026 000123` asserted off what
came back, and with the `decision` and `status` filters shown to exclude as well as include.
This is the second time this exact gap has been found by hand; **step 9's build-wide check is
what stops the third**.

**Eleven endpoints**, four of them mutating, each with its `GAP (P7, step 7)` line. The queue
actions (`retry`, `verify`, `attach-receipt`) are here rather than in step 7 because step 5 is
the last backend step and decision 15 already names the permission they sit under; without them
step 7's screen would have nothing to call.

**`redact_payload` on the Protocol.** The queue detail redacts a second time on the way out, and
doing it needed the adapter: which fields are secret is the authority's fact, and importing
`redact` from `rwanda/` would have been rule 12's exact violation —
`tests/fiscal/test_boundary.py` said so on the first run.

## Decisions worth review

### a. The queue actions land in step 5, not step 7

Step 5's brief lists the listings and the row detail; it does not name Retry / Verify / Attach.
They are here anyway, because the alternative is worse in both directions: step 7 would have to
build a screen and its three endpoints in one step with no backend test behind them, and the
service functions (written at step 2) would have gone four steps with no caller — which is the
shape rule 14 exists to prevent. They are registered as `GAP (P7, step 7)` naming the screen
that deletes the line.

### b. `verify`'s success path is proved at the service level, not over HTTP

The in-process sandbox is a **separate ASGI app** — decision 16 builds it that way and
`docker-compose.e2e.yml` runs it as `ebm-sandbox` — so a device pointed at `http://ebm.sandbox`
is not reachable from inside the backend test app. Verify is the one endpoint whose success path
needs a live upstream.

Rather than mock the adapter, the HTTP tests assert the two outcomes a real operator meets — the
wrong-state refusal and the unreachable-device refusal (finding 3) — and the success path is the
tape's row 6 and `test_drain.py`. **Step 9 owes the 200 over HTTP**, where Playwright drives
Verify against the real `ebm-sandbox` container; it is already in step 9's brief ("the unknown
row verified and attached").

This is the one place in the step where a route does not answer 200 in a test, and it is called
out rather than quietly left, because step 4's carried item asks step 9 to enforce exactly that
rule build-wide.

### c. The second redaction needed a test that can fail

`test_no_device_key_reaches_the_queue_detail` passes with the second redaction **deleted** —
the drainer already strips the keys, so on any row a real run produced there is nothing left to
remove. A guard whose test cannot fail is not a guard.

So `test_a_key_that_reached_a_stored_row_still_does_not_reach_the_screen` plants a key in the
stored payload, the way a legacy row or a restored backup or a future builder that forgets
would, and asserts the screen still refuses it — redacted to `***` rather than dropped, because
a screen that silently omitted the field would hide that there was ever anything there.

### d. The receipt block prints every programmed rate, which needed the adapter

Decision 11 asks for "every rate > 0 always, zero rates only when used" (CIS §7.22–7.23). The
first draft of `ReceiptClassLine` carried a `used` flag that was **always true**, because
`normalize_declared_totals` dropped any class whose taxable and tax were both zero — so a
receipt that sold nothing standard-rated lost class B entirely, rate and all, and could not
have printed `TOTAL B-18.00%: 0`.

The selection is the adapter's, since the rates are the authority's: a class is kept when it
carries amounts **or** a rate above zero, and a zero-rate class nothing was sold under is still
dropped. `used` now says which of the two a line is, which is what the step-7 template needs to
tell a rate that must print from a line with figures on it.

`test_every_programmed_rate_prints_and_an_unused_zero_rate_does_not` is the proof, and it needs
an **exempt-only** sale to exist at all — on any receipt that uses the standard rate the two
readings agree, which is why the always-true flag survived being written.

### e. A fiscal day still has a second's resolution, and the tape waits it out

Step 4 recorded this and the tape meets it: row 14's refund receipt is stamped by the sandbox
from the real clock, and Z-2 opens *exclusively* at Z-1's `to_at`. A receipt inside that same
second would fall in neither Z. The tape sleeps 1.05 s before row 14 rather than pretending
otherwise — the same thing `test_daily_report.py` does, for the same reason.

## The tape, row by row

Every value below is the run's own output, printed by the test.

| # | figures asserted | result |
|---|---|---|
| 0 | device `active`, `sdc_id SDC010000005`, `mrc_no WIS01006230`, no key field on the wire · code class 04 = A/B/C/D · `item_cd` X `RW2NTXU0000001`, S `RW3NTXU0000002`, E `RW2NTXU0000003`, Z `RW2NTXU0000004` · four `item` rows `sent` · `sarNo 1` `sarTyCd 02` · masters X 100 / E 50 / Z 20 | ok |
| 1 | net 45 000 · tax 5 400 · gross 50 400 · `invc_no 1` · A 5 000 / B 35 400 tax 5 400 / C 10 000 / D 0 · `totTaxblAmt 50 400` · `totTaxAmt 5 400` · X `prc 2 360.00` `splyAmt 23 600` `taxAmt 3 600`; S 11 800 / 1 800; E 1 000 / 0; Z 5 000 / 0 · `custTin 100000001` · `prcOrdCd AB12CD` · `pmtTyCd 02` · Print refused `fiscal_receipt_pending`, then allowed · receipt `1/1 NS` · `sarNo 2` `sarTyCd 11` · masters X 90 / E 45 / Z 18 | ok |
| 2 | net 5 400 · tax 972 · gross 6 372 · `prc 2 360.00` · `splyAmt 7 080` · `dcRt 10` · `dcAmt 708` · `taxblAmt 6 372` · `taxAmt 972` · `pmtTyCd 01` · `custTin` absent · receipt `2/2 NS` · master X 87 | ok |
| 3 | net 4 000 · tax 720 · gross 4 720 · `rcptTyCd R` · `orgInvcNo 1` · `rfdRsnCd 06` · `invc_no 3` · receipt `1/3 NR` · `sarTyCd 03` · master X 89 | ok |
| 3b | COPY, `copy_count 1`, same SDC block, same receipt number, **no new outbox row** | ok |
| 4 | USD 40 / 7.20 / 47.20 · base 62 304 · `prc 3 115.20` · B 62 304 · `taxAmtB 9 504` · receipt `3/4 NS` · master X 69 | ok |
| 5 | net 10 000 · tax 1 800 · gross 11 800 · attempts 3 · next due +15 min · `queued` · Print refused · sandbox up → receipt `4/5 NS` | ok |
| 6 | row `unknown`, queue blocked · device holds 6, row is 6 → `needs_receipt` · attached → `sent`, receipt `5/6 NS` · queue resumes · `sarTyCd 11` · master E 44 | ok |
| 7 | net 50 000 · tax 9 000 · gross 59 000 · `regTyCd M` · `pchsTyCd N` · `rcptTyCd P` · purchase `invcNo 1` · B 59 000 / `taxAmtB 9 000` · `sarTyCd 02` qty 50 @ 1 000 · master X 119 | ok |
| 8 | feed `spplrTin 100000003` `spplrInvcNo 77` B 11 800 / 1 800 → accepted · `regTyCd A` · `pchsSttsCd 02` · watermark advanced · **no AP document** | ok |
| 9 | `Z-000001` · NS 5 / 131 876 · NR 1 / 4 720 · B NS 115 876 / 17 676, NR 4 720 / 720 · A NS 6 000 · C NS 10 000 · credit 112 704 · cash 19 172 · refunds credit 4 720 · copies 1 / 50 400 · items sold 43, returned 2 · X after the close all zero | ok |
| 10 | standard 94 200 / 16 956 · zero-rated 10 000 · exempt 6 000 · purchases 50 000 / 9 000 · imports 0 · net payable 7 956 · 2200 movement −16 956 difference 0 · 1400 movement +9 000 difference 0 · untagged none · every tie reconciled | ok |
| 11 | `VATR-000001` · 2200 +16 956 · 1400 −9 000 · 2250 −7 956 · high water = the last entry that had arrived | ok |
| 12 | filed return byte-for-byte unchanged · output VAT still 16 956 · one late **entry**, 1 000 base / 180 tax, naming `VATR-000001` · 2200 −180 on the trial balance | ok |
| 13 | open USD 47.20 · carrying 62 304 · revalued 63 720 · gain 1 416 · `FXR-000001` · 1290 +1 416 / 4410 −1 416 at month end, both 0 after the mirror · **1200 untouched** · second run refused `fx_revaluation_exists` | ok |
| 14 | `rcptTyCd R` · `orgInvcNo 5` · `rfdRsnCd 07` · receipt `2/7 NR` · `Z-000002`: NR 1 / 11 800, NS 0 | ok |
| 15 | refused `fiscal_refund_irreversible` | ok |
| 16 | refused `purchase_code_required`, on the `purchase_code` field | ok |
| 17 | row `failed` `884`, queue blocked · reverse → row `cancelled`, **no refund queued**, reversal posts · queue resumes | ok |
| 18 | refused `fiscal_requires_block` | ok |

`assert_ledger_invariants`, `assert_subledger_invariants`, `assert_stock_invariants`,
`assert_order_invariants` and `assert_fiscal_invariants` were green after every row above,
including the three refusal rows.

**Under the `osdc` profile**, rows 0–3 again: the same four item codes, `1/1 NS`, `2/2 NS`,
`1/3 NR`, the same masters, and no `cmcKey` in any stored payload. Different paths, `cmcKey`
added at send, and a sales response shaped `curRcptNo / sdcDateTime` rather than
`rcptNo / vsdcRcptPbctDate` — and nothing above the adapter can tell, which is the whole of
what the route table and `normalize_receipt()` are for.

### One clause of the brief the tape does not literally hold

Row 12's "late entries 1 000 / 180" is asserted as a **total over one journal entry's lines**,
not as a single row. `_late` emits one `LateEntry` per journal line — the base on the revenue
line, the tax on the tax-account line — which is the same shape `_accumulate` reads, so a
three-line correction is two rows summing to 1 000 and 180. The tape asserts the entry count
(1), both totals, and that they name `VATR-000001`.

## The sensitivity pass

Each guard step 5 adds, reverted, the test run, the failure read.

| guard | reverted to | what failed |
|---|---|---|
| `verify_with_device` catches transport failure | the bare call | `FiscalTransportError: /initializer/selectInitInfo failed: ConnectError` escaping as a 500 |
| the Z splits payment buckets | step 4's netting | `tape row 9: credit expected 112704.00, got 107984.00` |
| the Z counts quantities NS/NR | `totItemCnt` over both | `tape row 9: items sold expected 43.00, got 8.00`, and `test_a_z_totals_equal_the_sum_of_its_own_receipts` |
| the print gate | `live = []` | `DID NOT RAISE LedgerStateError` (tape row 1) and `assert 200 == 409` |
| the copy counter | the increment removed | `tape row 3b: copy_count expected 1, got 0` |
| the queue detail's second redaction | the raw payload | `a planted key reached the screen` — **and the ordinary key test passed**, which is why the planted one exists |
| a class is kept for its rate, not only its amounts (CIS §7.22–7.23) | amounts alone | `assert {'A'} == {'A', 'B'}` — an exempt-only receipt losing the programmed 18 %, so it could not have printed `TOTAL B-18.00%: 0`. The test is `test_every_programmed_rate_prints_and_an_unused_zero_rate_does_not`, and it asserts both halves: B present and `used False` at rate 18, C and D absent because they are zero rates nothing was sold under. |

## The sandbox questions, and the live run

**The live run did not happen.** Precondition (d) — a TIN, branch id and device serial approved
on `myrratest.rra.gov.rw` — is **not held**, confirmed by the owner at this step, with no
application outstanding. `docs/rra/README.md` §6 now records that as a state rather than a
blank.

So the three questions this step was meant to close stay open. All three are questions only
Kigali can answer, and none of them blocks code written to the documents:

1. **Does RRA tolerate the `dcAmt` residue?** (decision 6) Bounded and measured at step 2:
   never a whole franc on an undiscounted line over 1 054 lines built to make it as large as
   possible, up to 1.09 francs on a discounted one. The build holds itself to that bound with
   property tests.
2. **Is a refund sent with positive amounts under `rcptTyCd R`, or negative ones?** (decision 6)
   Built positive, following Sage 200 Evolution, with the minus signs belonging to the printed
   receipt (CIS §14). `_assert_no_negative_amount` walks the whole payload recursively.
3. **Does a copy need a CS counter?** (decision 11) Built with no counter and no call: v1.0.5
   sends `salesTyCd N` only, a copy is a print of a sale already declared, and the Z counts
   copies separately (row 9: `copies 1 / 50 400`). If Kigali says a copy is a receipt,
   `printing.record_copy` is the one function that changes.

Two more remain from step 1's list and are unchanged: the item-code segment rule (§4.17) and
the QR payload's `sdc_receipt_number` (built as `totRcptNo`, argued from uniqueness in
`contract-notes.md` §5).

**The phase is heading for "code-complete, certification pending."** That is a plan deviation
and step 9's final report owes it a line; `docs/rra/certification.md` is where the runbook for
obtaining access goes. The moment access exists, rows 0–3 of the tape are the script — the only
things that change are the device's `base_url`, TIN and serial.

## Carried to later steps

1. **Step 7 deletes four `GAP (P7, step 7)` lines** — Retry now, Verify with device, Attach
   receipt manually on `/fiscal/queue`, and Copy print on the document detail.
2. **Step 9 owes Verify a 200 over HTTP** (decision *b*), against the real `ebm-sandbox`
   container. Step 4's carried item — "every endpoint answers 200 at least once, as a
   build-wide check" — should be written so this one is named rather than silently excused.
3. **Decision 15 should gain `fiscal:close_day`**, still outstanding from step 4.

## Checks

Run in the backend container, which is where this project's checks run.

```
uv run ruff check .                       →  All checks passed!
uv run pytest tests/ -n 4                 →  see below
uv run alembic check                      →  No new upgrade operations detected
                                             (step 5 adds no migration)
```
