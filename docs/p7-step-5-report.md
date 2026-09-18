# P7 step 5 — the enquiries, the listings, and the acceptance tape

Step 5 is a gate. It adds the backend half of the phase a person looks at, and then it runs the
tape: nineteen rows in one sequence, every expected value a literal worked by hand, all five
invariant suites after each one.

## Branch lineage

**Step 4 was completed locally and never pushed.** `origin/claude/p7-step-4` sat at `51f8d0a`,
step 4's *handoff note* — the second of its twelve commits. The other ten, including every fix
the step-4 report describes, existed only on this machine, and no pull request had been opened:
49, 50 and 51 are steps 1, 2 and 3, and nothing followed them. `p7-step-5` therefore branched
from `1361e52` and carried both steps.

**Resolved at the step-5 gate, on the owner's direction:** step 4 goes first as its own pull
request, and this branch is rebased onto its merge. So

* **PR #56** — `feat(fiscal): P7 step 4 — VAT return, filing, X/Z and FX revaluation`, the
  twelve commits as they stood at `1361e52`. Not a gate step, so it carries no approvals row;
  its body is `docs/p7-step-4-report.md` and the gap-status table below.
* **This PR** — step 5 alone, rebased onto `main` after #56 merged, which also takes `b53ad8d`
  (PR #55).

The gate run quoted under *Checks* is at `9cc94a1`, the pre-rebase head, because that is the
tree the tape and the deep profile actually ran against. The rebased head's full run is CI's.

## Step-4 gap status

Five items were open against step 4 when this step started. **All five landed in step 4's own
commits** — none are step 5's work — but because step 4 was never PR'd, all five are unreviewed
and travel in this diff.

| item | where | state |
|---|---|---|
| discounts in `DailyFigures` | `33bbeea` | landed. Asserted end to end for the first time here: tape row 9, `discounts 708.00` |
| the Z reads what RRA signed, through the adapter's normalizer | `33bbeea` | landed (`normalize_declared_totals` on the Protocol). Step 5 extends it with `quantity` and the rate-keeping rule |
| `partner_name=""` on the revaluation detail | `b0229a7` | landed — `gl.py:1297` fills it from the loaded partner, and `test_the_revaluation_detail_names_the_partner_each_line_drills_to` asserts the name |
| the revaluation splits per (role, currency) | `f8f75aa` | landed — `subledger/test_revaluation.py::test_each_currency_gets_its_own_pair_of_lines` |
| the three step-4 tests | `f8f75aa` | all three landed; see below for which the tape reaches |

**Which of the three the tape reaches**, since two of them it does not:

* *the filed return's figures unchanged by a late entry* — **tape row 12**, which asserts the
  whole stored snapshot byte-for-byte after a backdated correction, not just the headline.
  (`tax/test_vat_filing.py::test_a_late_entry_is_declared_on_the_next_return_and_the_filed_one_never_moves`)
* *a range may not overlap a filed return* — **now tape row 11**, added in review. The named
  test pins the one-day boundary against an empty month; the tape asserts the same refusal
  against a return with 16 956 of declared VAT behind it.
  (`tax/test_vat_filing.py::test_a_range_may_not_overlap_a_filed_return`)
* *an untagged movement is listed rather than absorbed* — **not in the tape, and deliberately
  so.** The tape's row 10 asserts the *complement*: `untagged == []` and every tie reconciled,
  which is what the build order's row asks for ("untagged none"). Making the tape produce an
  untagged movement would mean posting a cashbook payment to RRA inside the month, which moves
  rows 10, 11 and 12's figures away from the literals the brief pins. The case keeps its own
  test. (`tax/test_vat_return.py::test_an_untagged_movement_is_listed_rather_than_absorbed`)

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

**This is the run's own output**, pasted from
`pytest tests/fiscal/test_acceptance_tape.py -s` at `9cc94a1` — 254 assertions, no mismatch.
Every `expected` is a literal written into the test by hand; every `actual` came back from the
services. The `invariants` line after each row is all five suites.

```
[p7 tape] expected vs actual
  0    device status                 expected             active  actual             active  ok
  0    sdc_id                        expected       SDC010000005  actual       SDC010000005  ok
  0    mrc_no                        expected        WIS01006230  actual        WIS01006230  ok
  0    keys on the wire              expected                 []  actual                 []  ok
  0    device holds keys             expected               True  actual               True  ok
  0    code class 04                 expected ['A', 'B', 'C', 'D']  actual ['A', 'B', 'C', 'D']  ok
  0    item_cd X                     expected     RW2NTXU0000001  actual     RW2NTXU0000001  ok
  0    item_cd S                     expected     RW3NTXU0000002  actual     RW3NTXU0000002  ok
  0    item_cd E                     expected     RW2NTXU0000003  actual     RW2NTXU0000003  ok
  0    item_cd Z                     expected     RW2NTXU0000004  actual     RW2NTXU0000004  ok
  0    item rows                     expected                  4  actual                  4  ok
  0    item rows sent                expected [<FiscalOutboxStatus.SENT: 'sent'>, <FiscalOutboxStatus.S...  actual [<FiscalOutboxStatus.SENT: 'sent'>, <FiscalOutboxStatus.S...  ok
  0    stock_io sarNo                expected                  1  actual                  1  ok
  0    stock_io sarTyCd              expected                 02  actual                 02  ok
  0    master X                      expected                100  actual                100  ok
  0    master E                      expected                 50  actual                 50  ok
  0    master Z                      expected                 20  actual                 20  ok
  0    invariants                    expected              green  actual              green  ok
  1    INV-1 net                     expected              45000  actual              45000  ok
  1    INV-1 tax                     expected               5400  actual               5400  ok
  1    INV-1 gross                   expected              50400  actual              50400  ok
  1    sale row status               expected             queued  actual             queued  ok
  1    sale invc_no                  expected                  1  actual                  1  ok
  1    bucket A                      expected            5000.00  actual               5000  ok
  1    bucket B                      expected           35400.00  actual              35400  ok
  1    tax B                         expected            5400.00  actual               5400  ok
  1    bucket C                      expected           10000.00  actual              10000  ok
  1    bucket D                      expected               0.00  actual                  0  ok
  1    totTaxblAmt                   expected           50400.00  actual              50400  ok
  1    totTaxAmt                     expected            5400.00  actual               5400  ok
  1    X prc                         expected            2360.00  actual               2360  ok
  1    X splyAmt                     expected           23600.00  actual              23600  ok
  1    X taxAmt                      expected            3600.00  actual               3600  ok
  1    S prc                         expected           11800.00  actual              11800  ok
  1    S taxAmt                      expected            1800.00  actual               1800  ok
  1    E prc                         expected            1000.00  actual               1000  ok
  1    E taxAmt                      expected               0.00  actual                  0  ok
  1    Z prc                         expected            5000.00  actual               5000  ok
  1    Z taxAmt                      expected               0.00  actual                  0  ok
  1    custTin                       expected          100000001  actual          100000001  ok
  1    prcOrdCd                      expected             AB12CD  actual             AB12CD  ok
  1    pmtTyCd                       expected                 02  actual                 02  ok
  1    print before the receipt      expected fiscal_receipt_pending  actual fiscal_receipt_pending  ok
  1    INV-1 receipt                 expected             1/1 NS  actual             1/1 NS  ok
  1    print after the receipt       expected             1/1 NS  actual             1/1 NS  ok
  1    stock_io sarNo                expected                  2  actual                  2  ok
  1    stock_io sarTyCd              expected                 11  actual                 11  ok
  1    master X                      expected                 90  actual                 90  ok
  1    master E                      expected                 45  actual                 45  ok
  1    master Z                      expected                 18  actual                 18  ok
  1    invariants                    expected              green  actual              green  ok
  2    INV-2 net                     expected               5400  actual               5400  ok
  2    INV-2 tax                     expected                972  actual                972  ok
  2    INV-2 gross                   expected               6372  actual               6372  ok
  2    prc                           expected            2360.00  actual               2360  ok
  2    splyAmt                       expected            7080.00  actual               7080  ok
  2    dcRt                          expected              10.00  actual                 10  ok
  2    dcAmt                         expected             708.00  actual                708  ok
  2    taxblAmt                      expected            6372.00  actual               6372  ok
  2    taxAmt                        expected             972.00  actual                972  ok
  2    pmtTyCd                       expected                 01  actual                 01  ok
  2    custTin                       expected               None  actual               None  ok
  2    INV-2 receipt                 expected             2/2 NS  actual             2/2 NS  ok
  2    master X                      expected                 87  actual                 87  ok
  2    invariants                    expected              green  actual              green  ok
  3    CRN-1 net                     expected               4000  actual               4000  ok
  3    CRN-1 tax                     expected                720  actual                720  ok
  3    CRN-1 gross                   expected               4720  actual               4720  ok
  3    rcptTyCd                      expected                  R  actual                  R  ok
  3    orgInvcNo                     expected                  1  actual                  1  ok
  3    rfdRsnCd                      expected                 06  actual                 06  ok
  3    refund invc_no                expected                  3  actual                  3  ok
  3    CRN-1 receipt                 expected             1/3 NR  actual             1/3 NR  ok
  3    stock_io sarTyCd              expected                 03  actual                 03  ok
  3    master X                      expected                 89  actual                 89  ok
  3    invariants                    expected              green  actual              green  ok
  3b   copy layout                   expected               True  actual               True  ok
  3b   copy_count                    expected                  1  actual                  1  ok
  3b   same SDC block                expected       SDC010000005  actual       SDC010000005  ok
  3b   same receipt number           expected             1/1 NS  actual             1/1 NS  ok
  3b   no new outbox row             expected                 19  actual                 19  ok
  3b   invariants                    expected              green  actual              green  ok
  4    INV-3 net USD                 expected              40.00  actual              40.00  ok
  4    INV-3 tax USD                 expected               7.20  actual               7.20  ok
  4    INV-3 gross USD               expected              47.20  actual              47.20  ok
  4    INV-3 base                    expected              62304  actual       62304.000000  ok
  4    prc RWF                       expected            3115.20  actual             3115.2  ok
  4    bucket B                      expected           62304.00  actual              62304  ok
  4    tax B                         expected            9504.00  actual               9504  ok
  4    INV-3 receipt                 expected             3/4 NS  actual             3/4 NS  ok
  4    master X                      expected                 69  actual                 69  ok
  4    invariants                    expected              green  actual              green  ok
  5    INV-4 net                     expected              10000  actual              10000  ok
  5    INV-4 tax                     expected               1800  actual               1800  ok
  5    INV-4 gross                   expected              11800  actual              11800  ok
  5    attempts                      expected                  3  actual                  3  ok
  5    status                        expected             queued  actual             queued  ok
  5    next attempt in minutes       expected                 15  actual                 15  ok
  5    print while queued            expected fiscal_receipt_pending  actual fiscal_receipt_pending  ok
  5    INV-4 receipt                 expected             4/5 NS  actual             4/5 NS  ok
  5    invariants                    expected              green  actual              green  ok
  6    row after the lost answer     expected            unknown  actual            unknown  ok
  6    device queue blocked          expected                 24  actual                 24  ok
  6    lastSaleInvcNo                expected                  6  actual                  6  ok
  6    row invc_no                   expected                  6  actual                  6  ok
  6    after verify                  expected      needs_receipt  actual      needs_receipt  ok
  6    after attach                  expected               sent  actual               sent  ok
  6    INV-5 receipt                 expected             5/6 NS  actual             5/6 NS  ok
  6    queue resumes                 expected               None  actual               None  ok
  6    stock_io sarTyCd              expected                 11  actual                 11  ok
  6    master E                      expected                 44  actual                 44  ok
  6    invariants                    expected              green  actual              green  ok
  7    SIN-1 net                     expected              50000  actual              50000  ok
  7    SIN-1 tax                     expected               9000  actual               9000  ok
  7    SIN-1 gross                   expected              59000  actual              59000  ok
  7    regTyCd                       expected                  M  actual                  M  ok
  7    pchsTyCd                      expected                  N  actual                  N  ok
  7    rcptTyCd                      expected                  P  actual                  P  ok
  7    purchase invcNo               expected                  1  actual                  1  ok
  7    bucket B                      expected           59000.00  actual              59000  ok
  7    tax B                         expected            9000.00  actual               9000  ok
  7    stock_io sarTyCd              expected                 02  actual                 02  ok
  7    stock_io qty                  expected              50.00  actual                 50  ok
  7    stock_io prc                  expected            1000.00  actual               1000  ok
  7    master X                      expected                119  actual                119  ok
  7    invariants                    expected              green  actual              green  ok
  8    feed spplrTin                 expected          100000003  actual          100000003  ok
  8    feed spplrInvcNo              expected                 77  actual                 77  ok
  8    feed taxable B                expected           11800.00  actual       11800.000000  ok
  8    feed tax B                    expected            1800.00  actual        1800.000000  ok
  8    decision                      expected           accepted  actual           accepted  ok
  8    regTyCd                       expected                  A  actual                  A  ok
  8    pchsSttsCd                    expected                 02  actual                 02  ok
  8    spplrInvcNo                   expected                 77  actual                 77  ok
  8    watermark advanced            expected               True  actual               True  ok
  8    no AP document                expected                  7  actual                  7  ok
  8    invariants                    expected              green  actual              green  ok
  9    Z number                      expected           Z-000001  actual           Z-000001  ok
  9    NS count                      expected                  5  actual                  5  ok
  9    NS gross                      expected          131876.00  actual          131876.00  ok
  9    NR count                      expected                  1  actual                  1  ok
  9    NR gross                      expected            4720.00  actual            4720.00  ok
  9    B taxable NS                  expected          115876.00  actual          115876.00  ok
  9    B tax NS                      expected           17676.00  actual           17676.00  ok
  9    B taxable NR                  expected            4720.00  actual            4720.00  ok
  9    B tax NR                      expected             720.00  actual             720.00  ok
  9    A taxable NS                  expected            6000.00  actual            6000.00  ok
  9    C taxable NS                  expected           10000.00  actual           10000.00  ok
  9    credit                        expected          112704.00  actual          112704.00  ok
  9    cash                          expected           19172.00  actual           19172.00  ok
  9    copies count                  expected                  1  actual                  1  ok
  9    copies gross                  expected           50400.00  actual           50400.00  ok
  9    discounts                     expected             708.00  actual             708.00  ok
  9    items sold                    expected              43.00  actual              43.00  ok
  9    items returned                expected               2.00  actual               2.00  ok
  9    refunds by method             expected            4720.00  actual            4720.00  ok
  9    X after the close — NS        expected                  0  actual                  0  ok
  9    X after the close — NR        expected                  0  actual                  0  ok
  9    X after the close — gross     expected               0.00  actual               0.00  ok
  9    X after the close — items     expected               0.00  actual               0.00  ok
  9    invariants                    expected              green  actual              green  ok
  10   standard sales base           expected              94200  actual       94200.000000  ok
  10   standard sales VAT            expected              16956  actual       16956.000000  ok
  10   zero-rated sales              expected              10000  actual       10000.000000  ok
  10   exempt sales                  expected               6000  actual        6000.000000  ok
  10   standard purchases base       expected              50000  actual       50000.000000  ok
  10   standard purchases VAT        expected               9000  actual        9000.000000  ok
  10   imports                       expected                  0  actual                  0  ok
  10   net payable                   expected               7956  actual        7956.000000  ok
  10   2200 movement                 expected             -16956  actual      -16956.000000  ok
  10   2200 difference               expected                  0  actual           0.000000  ok
  10   1400 movement                 expected               9000  actual        9000.000000  ok
  10   1400 difference               expected                  0  actual           0.000000  ok
  10   untagged                      expected                 []  actual                 []  ok
  10   every tie reconciled          expected               True  actual               True  ok
  10   invariants                    expected              green  actual              green  ok
  11   return number                 expected        VATR-000001  actual        VATR-000001  ok
  11   2200 settlement               expected              16956  actual       16956.000000  ok
  11   1400 settlement               expected              -9000  actual       -9000.000000  ok
  11   2250 settlement               expected              -7956  actual       -7956.000000  ok
  11   high water                    expected                 14  actual                 14  ok
  11   an overlapping range          expected   vat_period_filed  actual   vat_period_filed  ok
  11   invariants                    expected              green  actual              green  ok
  12   filed return unchanged        expected {'period_from': '2026-03-01', 'period_to': '2026-03-31', ...  actual {'period_from': '2026-03-01', 'period_to': '2026-03-31', ...  ok
  12   filed output VAT              expected              16956  actual       16956.000000  ok
  12   late entries                  expected                  1  actual                  1  ok
  12   late base                     expected               1000  actual        1000.000000  ok
  12   late tax                      expected                180  actual         180.000000  ok
  12   late return named             expected    {'VATR-000001'}  actual    {'VATR-000001'}  ok
  12   2200 on the trial balance     expected               -180  actual        -180.000000  ok
  12   invariants                    expected              green  actual              green  ok
  13   revaluation lines             expected                  1  actual                  1  ok
  13   open amount                   expected              47.20  actual          47.200000  ok
  13   carrying                      expected              62304  actual       62304.000000  ok
  13   revalued                      expected              63720  actual       63720.000000  ok
  13   gain                          expected               1416  actual        1416.000000  ok
  13   run number                    expected         FXR-000001  actual         FXR-000001  ok
  13   1290 at month end             expected               1416  actual        1416.000000  ok
  13   4410 at month end             expected              -1416  actual       -1416.000000  ok
  13   1290 after the mirror         expected                  0  actual           0.000000  ok
  13   4410 after the mirror         expected                  0  actual           0.000000  ok
  13   1200 untouched                expected      127156.000000  actual      127156.000000  ok
  13   a second run                  expected fx_revaluation_exists  actual fx_revaluation_exists  ok
  13   invariants                    expected              green  actual              green  ok
  14   rcptTyCd                      expected                  R  actual                  R  ok
  14   orgInvcNo                     expected                  5  actual                  5  ok
  14   rfdRsnCd                      expected                 07  actual                 07  ok
  14   reversal receipt              expected             2/7 NR  actual             2/7 NR  ok
  14   Z-2 number                    expected           Z-000002  actual           Z-000002  ok
  14   Z-2 NR count                  expected                  1  actual                  1  ok
  14   Z-2 NR gross                  expected           11800.00  actual           11800.00  ok
  14   Z-2 NS count                  expected                  0  actual                  0  ok
  14   invariants                    expected              green  actual              green  ok
  15   reversing a signed refund     expected fiscal_refund_irreversible  actual fiscal_refund_irreversible  ok
  15   invariants                    expected              green  actual              green  ok
  16   no purchase code              expected purchase_code_required  actual purchase_code_required  ok
  16   refused on the field          expected  ['purchase_code']  actual  ['purchase_code']  ok
  16   invariants                    expected              green  actual              green  ok
  17   row status                    expected             failed  actual             failed  ok
  17   result code                   expected                884  actual                884  ok
  17   queue blocked                 expected                 32  actual                 32  ok
  17   row cancelled                 expected          cancelled  actual          cancelled  ok
  17   no refund queued              expected                  2  actual                  2  ok
  17   reversal posted               expected           reversed  actual           reversed  ok
  17   queue resumes                 expected               None  actual               None  ok
  17   invariants                    expected              green  actual              green  ok
  18   allowing negative stock       expected fiscal_requires_block  actual fiscal_requires_block  ok
  18   invariants                    expected              green  actual              green  ok
  osdc 0 device status                 expected             active  actual             active  ok
  osdc 0 sdc_id                        expected       SDC010000005  actual       SDC010000005  ok
  osdc 0 mrc_no                        expected        WIS01006230  actual        WIS01006230  ok
  osdc 0 code class 04                 expected ['A', 'B', 'C', 'D']  actual ['A', 'B', 'C', 'D']  ok
  osdc 0 item_cd X                     expected     RW2NTXU0000001  actual     RW2NTXU0000001  ok
  osdc 0 item_cd S                     expected     RW3NTXU0000002  actual     RW3NTXU0000002  ok
  osdc 0 item_cd E                     expected     RW2NTXU0000003  actual     RW2NTXU0000003  ok
  osdc 0 item_cd Z                     expected     RW2NTXU0000004  actual     RW2NTXU0000004  ok
  osdc 0 master X                      expected                100  actual                100  ok
  osdc 0 invariants                    expected              green  actual              green  ok
  osdc 1 INV-1 gross                   expected              50400  actual              50400  ok
  osdc 1 bucket B                      expected           35400.00  actual              35400  ok
  osdc 1 tax B                         expected            5400.00  actual               5400  ok
  osdc 1 no key in the stored payload  expected               None  actual               None  ok
  osdc 1 INV-1 receipt                 expected             1/1 NS  actual             1/1 NS  ok
  osdc 1 invariants                    expected              green  actual              green  ok
  osdc 2 INV-2 gross                   expected               6372  actual               6372  ok
  osdc 2 INV-2 receipt                 expected             2/2 NS  actual             2/2 NS  ok
  osdc 2 master X                      expected                 87  actual                 87  ok
  osdc 2 invariants                    expected              green  actual              green  ok
  osdc 3 CRN-1 gross                   expected               4720  actual               4720  ok
  osdc 3 rcptTyCd                      expected                  R  actual                  R  ok
  osdc 3 orgInvcNo                     expected                  1  actual                  1  ok
  osdc 3 CRN-1 receipt                 expected             1/3 NR  actual             1/3 NR  ok
  osdc 3 master X                      expected                 89  actual                 89  ok
  osdc 3 invariants                    expected              green  actual              green  ok
```

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

Run in the backend container, which is where this project's checks run, **on the committed
head** — the first draft of this report quoted a run started before three later edits, which is
exactly the thing a quoted number is supposed to make impossible.

```
HEAD                                      9cc94a15b2a69267591150c6c5bb4020900ce59c
uv run ruff check .                    →  All checks passed!
uv run pytest tests/ -n 4              →  1360 passed, 7 warnings in 1108.80s (0:18:28)
uv run pytest tests/fiscal/test_acceptance_tape.py -s
                                       →  2 passed in 38.09s  (254 assertions, no mismatch)
uv run alembic check                   →  No new upgrade operations detected
                                          (step 5 adds no migration)
```

**Both sides**, so the diff's effect on the suite is a number rather than a claim:

| | commit | tests |
|---|---|---|
| merge base | `d8ed45b` | 1269 passed (16:52) |
| `p7-step-5` | `9cc94a1` | **1360 passed** (18:28) |

**+91 tests**, and the merge base does not carry step 4, so that figure is steps 4 **and** 5
together — see *Branch lineage* above.

`origin/main` has since moved to `b53ad8d` (PR #55, audit history endpoints breaking ties on
id), which this branch takes in the rebase. The baseline above is the merge base rather than
that tip, because the merge base is the code this diff was written against and is therefore
the only figure the diff is answerable for.

```
git diff --stat main..
   .github/prompts/phase-7-fiscalization.prompt.md |    6 +-
   backend/app/api/v1/fiscal.py                    |  312 ++++-
   backend/app/fiscal/daily.py                     |   42 +-
   backend/app/fiscal/drainer.py                   |   17 +-
   backend/app/fiscal/enquiries.py                 |  748 +++++++++++
   backend/app/fiscal/null.py                      |    6 +
   backend/app/fiscal/printing.py                  |  355 ++++++
   backend/app/fiscal/protocol.py                  |   20 +
   backend/app/fiscal/rwanda/adapter.py            |   25 +-
   backend/app/schemas/fiscal.py                   |  215 +++-
   backend/tests/fiscal/conftest.py                |    7 +
   backend/tests/fiscal/test_acceptance_tape.py    | 1507 +++++++++++++++++++++++
   backend/tests/fiscal/test_daily_report.py       |   20 +-
   backend/tests/fiscal/test_enquiries_api.py      |  617 ++++++++++
   backend/tests/test_api_has_a_caller.py          |   26 +
   docs/approvals.md                               |    1 +
   docs/p7-step-5-report.md                        |  681 ++++++++++
   docs/rra/README.md                              |   16 +-
   18 files changed, 4597 insertions(+), 24 deletions(-)

git status --short                     →  (clean)
git log @{u}..                         →  (empty — the branch is pushed and in sync)
```

### The deep Hypothesis profile

A gate step runs the property machines deeply; per-commit CI runs them at `max_examples=1`, so
a property that has only ever run in CI has not been run. `HYPOTHESIS_PROFILE=deep` is 300
examples.

```
HYPOTHESIS_PROFILE=deep uv run pytest tests/fiscal/test_property_fiscal.py \
                                      tests/tax/test_property_vat.py

7 passed, 1 warning in 1003.76s (0:16:43)

[property] refusals provoked: {'fiscal_status_unresolved': 3, 'insufficient_stock': 881, 'refund_exceeds_original': 92}
[property] queue states reached: {'cancelled': 1365, 'failed': 83, 'needs_receipt': 16, 'queued': 56645, 'sent': 2575, 'unknown': 141}

[decision 6] reach: {'awkward lines': 1171, 'census lines 0dp': 248, 'census lines 0dp taxed': 81, 'census lines 2dp': 186, 'census lines 2dp taxed': 66, 'discounted lines': 1304, 'discounted lines a franc or more': 134, 'documents 0dp base': 124, 'documents 0dp fx': 180, 'documents 2dp base': 105, 'documents 2dp fx': 125, 'multi-line documents 0dp base': 58, 'multi-line documents 0dp fx': 116, 'multi-line documents 2dp base': 48, 'multi-line documents 2dp fx': 62, 'payloads': 534}
[decision 6] per-line census (wire taxable - posted gross): {'0dp discounted one unit': 47, '0dp plain exact': 174, '0dp discounted exact': 12, '0dp plain one unit': 15, '2dp plain exact': 79, '2dp discounted exact': 96, '2dp discounted one unit': 11, 'awkward under a franc': 1171, 'constructed discounted under a franc': 1170, 'constructed discounted a franc or more': 134}
[decision 6] per-document census (wire foot - posted foot): {'0dp base tax exact': 57, '0dp base tax one unit': 67, '0dp base total exact': 83, '0dp base total one unit': 41, '0dp fx tax exact': 66, '0dp fx tax one unit': 114, '0dp fx total exact': 22, '0dp fx total one unit': 156, '0dp fx total within its budget': 2, '2dp base tax exact': 105, '2dp base total exact': 99, '2dp base total one unit': 1, '2dp base total within its budget': 5, '2dp fx tax exact': 82, '2dp fx tax one unit': 43, '2dp fx total exact': 110, '2dp fx total one unit': 15}
[decision 6] worst document residue: {'0dp base tax': '-0.500000', '0dp base tax in units': '-0.5', '0dp base tax of budget': '0.2477876106194690265486725664', '0dp base total': '-0.760000', '0dp base total in units': '-0.76', '0dp base total of budget': '-0.4950495049504950495049504950', '0dp fx tax': '-9.640000', '0dp fx tax in units': '-0.7412533640907343329488658208', '0dp fx tax of budget': '0.1983059607818317985717751548', '0dp fx total': '-13.380000', '0dp fx total in units': '-1.028835063437139561707035755', '0dp fx total of budget': '-0.2615709621663145765315920876', '2dp base total': '-0.020000', '2dp base total in units': '-2', '2dp base total of budget': '-0.09442870632672332389046270066', '2dp fx tax': '8.590000', '2dp fx tax in units': '0.6605151864667435601691657055', '2dp fx tax of budget': '0.4347994602003460073500135655', '2dp fx total': '-6.500000', '2dp fx total in units': '-0.4998077662437524029219530950', '2dp fx total of budget': '-0.2384844132091012990062537951', 'discounted line': '-1.090000'}
```
