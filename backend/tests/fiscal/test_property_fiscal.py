"""The compounding-error property for fiscalization (P7 step 2).

One machine drives a random sequence of receipts, invoices in both tax modes and both
currencies, credit notes against them, reversals and drains — **with the sandbox switched
between `up`, `down`, `timeout`, `accept_then_timeout` and `reject:<code>` between
operations** — and asserts the ledger, subledger, stock, order *and* fiscal invariant suites
after every single step.

The switching is the whole point. A queue is easy to get right when the authority always
answers; what this phase has to survive is a device that goes away halfway through a day, comes
back, refuses one payload, loses the answer to another, and is then asked to reverse a sale
somebody is no longer sure was registered. Checking only the end state hides an error one
operation introduces and the next one masks, which on a queue is the ordinary case: a row
requeued wrongly looks identical to a row that was never sent.

Illegal steps are skipped rather than failed — refunding more than was invoiced, reversing what
is already reversed, selling stock that is not there under `block`. The property under test is
the invariant suite, not the plumbing.

The second property is **decision 6's**: over random documents at a zero-decimal and a
two-decimal base, every header bucket is Σ of its lines, every line's wire tax is
`taxblAmt × r/(100+r)`, and the **residue census** — how far the wire's taxable amount falls
from the posted gross, line by line — is measured and printed rather than asserted away. That
census is one of the two sandbox questions step 5 carries to Kigali, so the number has to be a
number somebody can quote.
"""

import itertools
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import ROUND_HALF_UP, Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.fiscal import drainer
from app.fiscal import outbox as outbox_service
from app.fiscal.rwanda.sandbox import create_sandbox_app
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.money import base_currency
from app.models.currency import Currency
from app.models.fiscalization import FiscalOutboxKind, FiscalOutboxRow, FiscalOutboxStatus
from app.models.journal import JournalLine
from app.models.partner import TaxMode
from app.models.subledger import DocumentKind, DocumentStatus, PartnerDocument
from app.models.tax import TaxCode
from app.subledger import documents as documents_service
from tests.fiscal.conftest import SANDBOX_URL, FiscalPosting, fiscalize
from tests.fiscal.helpers import APRIL, MARCH, PURCHASE_CODE, REFUND_REASON
from tests.fiscal.invariants import assert_fiscal_invariants
from tests.inventory.invariants import assert_stock_invariants
from tests.kernel.conftest import USD_RATE
from tests.kernel.invariants import assert_ledger_invariants
from tests.order_entry.conftest import build_order_entry
from tests.order_entry.invariants import assert_order_invariants
from tests.subledger.invariants import assert_subledger_invariants

_EXAMPLE = itertools.count()
ZERO = Decimal(0)
HUNDRED = Decimal(100)
PENNY = Decimal("0.01")
#: Tax class `B`'s rate, in both the forms this file needs it: the payload carries a percentage
#: and the residue arithmetic wants the multiplier. Derived from one literal so the two cannot
#: drift apart.
STANDARD_RATE_PERCENT = Decimal("18.00")
STANDARD_RATE = STANDARD_RATE_PERCENT / HUNDRED

#: Which refusals and which queue states the machine actually produced. Measured, not assumed —
#: a state the generator never reaches is a state this suite does not cover, however green it
#: looks. P6 step 9 watched three deep passes each miss a different boundary; the census is what
#: turns that from a courtesy into something a reader can check.
_REFUSALS: Counter[str] = Counter()
_STATES: Counter[str] = Counter()

#: The sandbox behaviours the machine draws between operations. `accept_then_timeout` is the
#: one that matters most: RRA registers the sale and the answer is lost, which is the only way
#: to reach `unknown` honestly.
MODES = ("up", "up", "up", "down", "timeout", "accept_then_timeout", "reject:881", "reject:894")

OPERATIONS = ("receive", "sell", "credit", "reverse", "drain", "resolve")

PLAN = st.lists(
    st.tuples(
        st.sampled_from(OPERATIONS),
        st.sampled_from(MODES),
        # Quantity, unit price, discount, which item, which customer, which tax code, and
        # whether the document is keyed inclusive or in a foreign currency.
        st.integers(min_value=1, max_value=8).map(Decimal),
        st.integers(min_value=100, max_value=9000).map(Decimal),
        st.sampled_from((Decimal(0), Decimal(0), Decimal(10), Decimal("12.5"))),
        st.sampled_from(("stock", "service", "kit")),
        st.sampled_from(("tin", "walk_in")),
        # **Weighted toward the standard rate**, because the residue only exists where there
        # is tax: an exempt or zero-rated line has `prc` equal to the keyed price and its wire
        # amount equals the posted gross by construction. A uniform draw spent two thirds of
        # the census on lines that are exact for a reason that has nothing to do with decision
        # 6 — and the first deep pass duly reported no plain zero-decimal residue at all,
        # while `test_the_decision_6_residue_worked_by_hand` shows one of 0.30 on a line the
        # generator could have drawn.
        st.sampled_from(("VAT-OUT-18", "VAT-OUT-18", "VAT-EXEMPT", "VAT-ZERO")),
        st.sampled_from((TaxMode.EXCLUSIVE, TaxMode.INCLUSIVE)),
        st.booleans(),
        # **How many lines the invoice carries.** One-line invoices are the only ones the first
        # version of this machine produced, and a per-line census on one-line documents says
        # nothing about the figure a customer actually compares: the foot of the receipt
        # against the foot of the invoice. A residue of under a franc per line is up to twenty
        # francs on a twenty-line invoice, and only a document-level census can see that.
        st.integers(min_value=1, max_value=4),
    ),
    min_size=3,
    max_size=14,
)


#: The queue states a deep pass has to actually **reach**, and the floor each has to clear.
#:
#: `unknown` is the whole reason this list exists. It is the state a person has to resolve — the
#: answer never arrived, so RRA may or may not be holding the sale — and reaching it needs a
#: `timeout` or an `accept_then_timeout` drawn *and* a `drain` after it, which is two of
#: fourteen operations landing in the right order. P7 step 9's deep pass reached it **seven**
#: times against 55 692 `queued`, and seven is thin: a seed that reached it zero times would
#: make `verify` and `attach` vacuous for that run while the census printed a wall of green
#: numbers. Printing the counter was not enough, and step 9's own report said so before this
#: floor existed — a census is a report until it is a gate.
#:
#: **Two modes reach it, not one**, and that is measured rather than reasoned: dropping only
#: `accept_then_timeout` and re-running the two invariant machines deep still reached `unknown`
#: 105 times, because `timeout` lands in the same state. What separates them is whether RRA is
#: holding the sale, which is `test_drain.py`'s distinction to make, not this census's.
#:
#: The floor is **1** rather than P6's 3 because the route is that narrow: asking for three
#: would be asking the generator for something it clears by luck, and the answer to a floor
#: that fails for want of luck is a targeted property, not a bigger `max_examples`
#: (`test_property_order.py` says the same where it sets its own).
#: Only a run with enough examples can be held to a floor: the per-commit profile draws two
#: and would fail every one, so both censuses below stay silent on it. Read by
#: `_report_census` and by `_report_residue`.
_FLOORS_FROM_EXAMPLES = 100

REQUIRED_STATES = ("unknown", "needs_receipt", "failed", "cancelled", "sent")
STATE_FLOOR = 1


@pytest.fixture(scope="module", autouse=True)
def _report_census():  # noqa: ANN202
    yield
    if _REFUSALS:
        print("\n[property] refusals provoked:", dict(sorted(_REFUSALS.items())))
    if _STATES:
        print("[property] queue states reached:", dict(sorted(_STATES.items())))
    if settings.default.max_examples < _FLOORS_FROM_EXAMPLES:
        return
    short = {
        name: _STATES.get(name, 0)
        for name in REQUIRED_STATES
        if _STATES.get(name, 0) < STATE_FLOOR
    }
    assert not short, (
        f"the deep pass never reached {short}. A queue state the machine cannot produce is a "
        "state this suite does not cover, however green it looks — and `unknown` in particular "
        "is what `Verify with device` and `Attach receipt manually` exist for, so a run that "
        "missed it proved nothing about either.\n"
        "Before reaching for a bigger `max_examples`: check that `timeout` and "
        "`accept_then_timeout` are still in `MODES`, and that `drain` still follows them often "
        "enough to matter. If the generator cannot get near it, the fix is a targeted "
        "property.\n"
        f"reached: {dict(sorted(_STATES.items()))}"
    )


@contextmanager
def _sandbox(state) -> Iterator[httpx.Client]:  # noqa: ANN001
    """A client onto the in-process sandbox, **reset first**.

    Hypothesis reuses a function-scoped fixture across every example it draws, so the sandbox
    state — its mode and its device ledgers — survives from one example into the next. Without
    the reset, example two begins with whatever mode example one happened to leave behind, and
    the tenant it builds fails to initialize its device against a server that is pretending to
    be down.
    """
    state.reset()
    with TestClient(create_sandbox_app(state), base_url=SANDBOX_URL) as client:
        yield client


def _item(fixture: FiscalPosting, which: str):  # noqa: ANN202
    return {
        "stock": fixture.stock_item,
        "service": fixture.service_item,
        "kit": fixture.order.kit_item,
    }[which]


def _partner_id(fixture: FiscalPosting, which: str) -> int:
    return (fixture.customer if which == "tin" else fixture.walk_in).id


#: The pool a second, third and fourth line rotate through. Deliberately mixed: a document
#: whose lines are all one tax class exercises one bucket, and the header buckets are the thing
#: a multi-line payload can most easily get wrong.
_ROTATION = ("VAT-OUT-18", "VAT-ZERO", "VAT-OUT-18", "VAT-EXEMPT")


def _lines(
    fixture: FiscalPosting,
    *,
    count: int,
    quantity: Decimal,
    price: Decimal,
    discount: Decimal,
    which_item: str,
    tax_code: str,
) -> tuple:
    """`count` lines, perturbed off the drawn ones rather than drawn independently.

    Independent draws per line would need a nested strategy and would shrink badly; what the
    document-level census needs is only that the lines *differ* — in quantity, in price, in tax
    class and in whether they are discounted — so that their residues do not all round the same
    way and cancel. `+ 7` on the price keeps the extra lines off the round values Hypothesis
    favours, which is the bias the per-line census was blind to.
    """
    first = documents_service.LineInput(
        item_id=_item(fixture, which_item).id,
        quantity=quantity,
        unit_price=price,
        discount_percent=discount,
        tax_code_id=fixture.tax_codes[tax_code].id,
    )
    extra = [
        documents_service.LineInput(
            # The extra lines are the **stock** item whatever the first one was: a kit explodes
            # into components and a second kit line would spend the draw on the explosion
            # rather than on the arithmetic this census is about.
            item_id=fixture.stock_item.id,
            quantity=quantity + index,
            unit_price=price + 7 * index,
            discount_percent=discount if index % 2 else ZERO,
            tax_code_id=fixture.tax_codes[_ROTATION[index % len(_ROTATION)]].id,
        )
        for index in range(1, count)
    ]
    return (first, *extra)


def _assert_everything(db: Session, fixture: FiscalPosting) -> None:
    company_id = fixture.company_id
    assert_ledger_invariants(db, company_id)
    assert_subledger_invariants(db, company_id)
    assert_stock_invariants(db, company_id)
    assert_order_invariants(db, company_id)
    assert_fiscal_invariants(db, company_id)


def _record_states(db: Session, company_id: int) -> None:
    for row in db.scalars(
        select(FiscalOutboxRow).where(FiscalOutboxRow.company_id == company_id)
    ):
        _STATES[str(row.status)] += 1


def _open_documents(db: Session, company_id: int, kind: DocumentKind) -> list[PartnerDocument]:
    return list(
        db.scalars(
            select(PartnerDocument).where(
                PartnerDocument.company_id == company_id,
                PartnerDocument.kind == kind,
                PartnerDocument.status == DocumentStatus.POSTED,
            )
        )
    )


def _drive(  # noqa: C901 - one dispatch per operation reads better than six helpers
    db: Session,
    fixture: FiscalPosting,
    client: httpx.Client,
    plan: list[tuple],
) -> None:
    from decimal import Decimal as D

    from app.order_entry import grn as grn_service

    for (
        operation,
        mode,
        quantity,
        price,
        discount,
        which_item,
        which_partner,
        tax_code,
        tax_mode,
        foreign,
        line_count,
    ) in plan:
        client.post("/_sandbox/mode", json={"mode": mode})
        # **A savepoint per step**, so a refused step leaves nothing behind whatever the
        # service did before refusing — and so the tenant itself survives, since the company,
        # its partners, its items and its device were all created in this same transaction. A
        # plain rollback would take them with it, which is the mistake this file made first.
        step = db.begin_nested()
        try:
            if operation == "receive":
                grn_service.post_grn(
                    db,
                    fixture.company_id,
                    grn_service.GrnInput(
                        partner_id=fixture.order.supplier.id,
                        grn_date=MARCH,
                        description="Stock in",
                        warehouse_id=fixture.order.main.id,
                        lines=(
                            grn_service.GrnLineInput(
                                item_id=fixture.stock_item.id,
                                quantity=quantity * D(10),
                                unit_cost=D(1000),
                            ),
                        ),
                    ),
                    actor=fixture.owner,
                )
            elif operation == "sell":
                documents_service.post_document(
                    db,
                    fixture.company_id,
                    documents_service.PartnerRole.AR,
                    documents_service.DocumentInput(
                        kind=DocumentKind.INVOICE,
                        partner_id=_partner_id(fixture, which_partner),
                        document_date=MARCH,
                        description="Wine",
                        tax_mode=tax_mode,
                        purchase_code=PURCHASE_CODE,
                        # Both currencies. A foreign-currency sale is fiscalized in RWF from
                        # its frozen base amounts, so the payload and the ledger have to agree
                        # about a figure neither of them keyed.
                        currency_id=(
                            fixture.order.ledger.cur("USD") if foreign else None
                        ),
                        exchange_rate=USD_RATE if foreign else None,
                        lines=_lines(
                            fixture,
                            count=line_count,
                            quantity=quantity,
                            price=price,
                            discount=discount,
                            which_item=which_item,
                            tax_code=tax_code,
                        ),
                    ),
                    actor=fixture.owner,
                )
            elif operation == "credit":
                invoices = _open_documents(db, fixture.company_id, DocumentKind.INVOICE)
                if not invoices:
                    continue
                original = invoices[-1]
                line = original.lines[0]
                documents_service.post_document(
                    db,
                    fixture.company_id,
                    documents_service.PartnerRole.AR,
                    documents_service.DocumentInput(
                        kind=DocumentKind.CREDIT_NOTE,
                        partner_id=original.partner_id,
                        document_date=MARCH,
                        description="Returned",
                        tax_mode=original.tax_mode,
                        currency_id=original.currency_id,
                        exchange_rate=original.exchange_rate,
                        purchase_code=PURCHASE_CODE,
                        refund_reason=REFUND_REASON,
                        lines=(
                            documents_service.LineInput(
                                item_id=line.item_id,
                                quantity=min(quantity, line.quantity),
                                unit_price=line.unit_price,
                                discount_percent=line.discount_percent,
                                tax_code_id=line.tax_code_id,
                                returns_line_id=line.id,
                            ),
                        ),
                    ),
                    actor=fixture.owner,
                )
            elif operation == "reverse":
                posted = _open_documents(db, fixture.company_id, DocumentKind.INVOICE)
                if not posted:
                    continue
                documents_service.reverse_document(
                    db,
                    posted[-1],
                    on_date=APRIL,
                    reason="property machine",
                    refund_reason=REFUND_REASON,
                    actor=fixture.owner,
                )
            elif operation == "drain":
                drainer.drain_company(db, fixture.company_id, client=client)
            elif operation == "resolve":
                head = outbox_service.head_row(
                    db, fixture.company_id, fixture.device.id
                )
                if head is None:
                    continue
                client.post("/_sandbox/mode", json={"mode": "up"})
                if head.status in (
                    FiscalOutboxStatus.UNKNOWN,
                    FiscalOutboxStatus.FAILED,
                ):
                    drainer.verify_with_device(
                        db,
                        fixture.company_id,
                        fixture.device,
                        head,
                        actor=fixture.owner,
                        client=client,
                    )
                elif head.status == FiscalOutboxStatus.NEEDS_RECEIPT:
                    ledger = client.get("/_sandbox/ledger").json()
                    device = next(iter(ledger.values()), {"sales": {}})
                    fields = device["sales"].get(str(head.invc_no))
                    if fields is None:
                        continue
                    drainer.attach_receipt(
                        db,
                        fixture.company_id,
                        fixture.device,
                        head,
                        fields=fields,
                        note="property machine",
                        actor=fixture.owner,
                        client=client,
                    )
        except (PostingError, LedgerStateError, drainer.QueueActionError) as refused:
            step.rollback()
            _REFUSALS[getattr(refused, "code", type(refused).__name__)] += 1
            # An illegal step for the state we are in: a refund larger than the invoice, a
            # sale with no stock under `block`, a reversal of a row nobody has resolved.
            # Skipped, not failed — the property under test is the invariant suite.
            continue
        step.commit()
        db.flush()
        _record_states(db, fixture.company_id)
        _assert_everything(db, fixture)


@pytest.mark.slow
@given(plan=PLAN)
def test_the_invariants_hold_after_every_step_at_zero_decimals(
    db: Session, plan: list[tuple], sandbox_state
) -> None:  # noqa: ANN001
    """RWF: no minor unit, so the wire's two decimals and the ledger's none are furthest
    apart and the residue decision 6 names is at its largest."""
    with _sandbox(sandbox_state) as client:
        fixture = fiscalize(
            db, build_order_entry(db, f"fis-rwf-{next(_EXAMPLE)}"), client, tag="rwf"
        )
        _drive(db, fixture, client, plan)


def _use_a_two_decimal_base(db: Session, fixture: FiscalPosting) -> None:
    base = db.scalar(
        select(Currency).where(
            Currency.company_id == fixture.company_id, Currency.is_base.is_(True)
        )
    )
    base.decimal_places = 2
    db.flush()


@pytest.mark.slow
@given(plan=PLAN)
def test_the_invariants_hold_after_every_step_at_two_decimals(
    db: Session, plan: list[tuple], sandbox_state
) -> None:  # noqa: ANN001
    """The same machine against a base currency with a minor unit, where the ledger and the
    wire round to the same place."""
    with _sandbox(sandbox_state) as client:
        fixture = fiscalize(
            db, build_order_entry(db, f"fis-usd-{next(_EXAMPLE)}"), client, tag="usd"
        )
        _use_a_two_decimal_base(db, fixture)
        _drive(db, fixture, client, plan)


# --- Decision 6: the payload map, and the residue census ---------------------------------------

#: How far the wire's taxable amount fell from the posted gross, counted by difference and
#: **keyed by the base currency's decimals**, because the answer differs between them and
#: "measured zero" beats "argued zero".
#:
#: Printed rather than asserted away: `prc` is the VAT-inclusive unit price at two decimals and
#: `prc x qty` need not equal a gross the ledger rounded to its own places. Whether RRA
#: tolerates the difference is one of the questions step 5 carries to the live environment, so
#: the number has to be one somebody can quote.
_RESIDUE: Counter[str] = Counter()

#: How many payloads the machine actually checked, and how many lines the census reached. An
#: anti-vacuity guard has to *guard* something: the first version of this file ended with
#: `assert checked >= 0`, which is true of every integer and was therefore a comment with an
#: `assert` in front of it. Floors, like the order-entry machine's.
_REACH: Counter[str] = Counter()

#: What a deep pass has to reach before these numbers mean anything. Both are far below what a
#: 300-example run produces (several hundred payloads, ~140 census lines), and far above what a
#: broken generator would.
PAYLOAD_FLOOR = 50
CENSUS_FLOOR = 20
#: Of those, how many must carry tax. See the assertion for why this one is the load-bearing.
TAXED_FLOOR = 10
#: How many constructed discounted lines must land past a whole franc. See the floor's own
#: comment; a deep pass produces several times this.
TWICE_ROUNDED_FLOOR = 20


@pytest.fixture(scope="module", autouse=True)
def _report_residue():  # noqa: ANN202
    yield
    if _RESIDUE:
        print("\n[decision 6] per-line census (wire taxable - posted gross):", dict(_RESIDUE))
    if _DOCUMENT_RESIDUE:
        print(
            "[decision 6] per-document census (wire foot - posted foot):",
            dict(sorted(_DOCUMENT_RESIDUE.items())),
        )
    if _WORST:
        print(
            "[decision 6] worst document residue:",
            {key: str(value) for key, value in sorted(_WORST.items())},
        )
    if _REACH:
        print("[decision 6] reach:", dict(sorted(_REACH.items())))
    if settings.default.max_examples < _FLOORS_FROM_EXAMPLES:
        return

    # **A floor per family that ran, and at least one family must have.** The first version of
    # this file ended with `assert checked >= 0`, which is true of every integer and was
    # therefore a comment with an `assert` in front of it.
    #
    # Scoped to the families present rather than to all of them, because a developer running
    # one property at a deep profile should not be told the others under-reached. The "at least
    # one" clause is what stops that tolerance becoming the vacuum it replaced: a whole-file
    # deep pass — which is what the nightly runs — always has all three.
    floors = {
        "payloads": PAYLOAD_FLOOR,
        "census lines 0dp": CENSUS_FLOOR,
        "census lines 2dp": CENSUS_FLOOR,
        "census lines 0dp taxed": TAXED_FLOOR,
        "census lines 2dp taxed": TAXED_FLOOR,
        "awkward lines": CENSUS_FLOOR,
        "documents 0dp base": CENSUS_FLOOR,
        "documents 0dp fx": CENSUS_FLOOR,
        "multi-line documents 0dp base": CENSUS_FLOOR,
        "discounted lines": CENSUS_FLOOR,
        # **The sub-floor that carries the claim.** A discounted line rounds twice, and the
        # whole point of that property is the residues a second rounding puts up to a franc
        # and past it —
        # the ones both of this file's old document bounds were written as though impossible.
        # A pass in which none appeared would be green about a case it never reached, which is
        # what happened when the plan drew the machine's own quantities: one line in 1 196.
        # Constructed, it is about a tenth of them, so this floor sits far below what a working
        # generator produces and far above what a broken one would.
        "discounted lines a franc or more": TWICE_ROUNDED_FLOOR,
    }
    assert any(_REACH.get(name) for name in floors), (
        "a deep pass reached none of the census families. Whatever ran, it measured nothing."
    )
    short = {
        name: _REACH.get(name, 0)
        for name, floor in floors.items()
        if _REACH.get(name) and _REACH[name] < floor
    }
    # A *taxed* sub-floor of zero is the interesting failure and the one the family floor above
    # cannot see: an exempt or zero-rated line is exact by construction, so a census made
    # entirely of them reports a comfortable zero about a question it never asked.
    for scale in ("0dp", "2dp"):
        if _REACH.get(f"census lines {scale}") and not _REACH.get(f"census lines {scale} taxed"):
            short[f"census lines {scale} taxed"] = 0
    # **No document may exceed the budget its own lines carry.** `_line_allowance` derives what
    # each line is allowed from the roundings it actually goes through; this is the claim that
    # a document is no further out than its lines are, which is the figure a customer comparing
    # a receipt with an invoice sees.
    #
    # **This replaced two bounds that were both false, and the nightly found the first.** They
    # were "no document further than one unit per line" and, tighter, "no document further than
    # one unit at all" — the latter on the evidence of 511 documents in which none had been.
    # A deep pass duly produced a 3-line RWF invoice 1.40 francs out, and 12.5% off 3 x 1 798
    # reproduces it exactly: line residues of -0.70, -0.50 and -0.20, which add because nothing
    # makes them cancel. The one-unit-per-line bound is false for a different reason — a single
    # discounted line can reach 1.09 on its own (see `_line_allowance`), so a one-line invoice
    # can breach a bound written as "a unit per line" without anything being wrong.
    over = {
        key: count
        for key, count in _DOCUMENT_RESIDUE.items()
        if key.endswith("OVER its budget")
    }
    assert not over, (
        f"documents further from the ledger than their own lines allow: {over}. Each line's "
        "allowance is derived from the roundings it goes through, so a document past the sum "
        "of them is not rounding — read the document before changing the bound.\n"
        f"worst: {dict(sorted((k, str(v)) for k, v in _WORST.items()))}"
    )
    # **What replaced the second assertion, and why it is not one.** There used to be a tighter
    # bound here — "no document more than one minor unit out at all" — asserted on the evidence
    # of a pass in which none had been. It was false, and a ratio against the derived budget
    # cannot take its place as a gate: `residue > budget` and `residue / budget > 1` are the
    # same condition, so asserting both would be the count above restated.
    #
    # So the ratio is *reported* instead, in the `of budget` keys of the worst-residue line
    # above. It is the number to read on a green run — the bound is reached by construction
    # (5 x 6 661 at 10% off is 1.09 out on one line against an allowance of 1.12), so a
    # regression shows up as the ratio climbing towards 1 well before any document crosses it.
    # The claim that the bound is *reached* rather than merely respected is asserted where it
    # can be, in the floors above and in the constructed discounted-line property.
    assert not short, (
        f"the deep pass under-reached {short}. A census that measured almost nothing is green "
        "for a reason that has nothing to do with what it is measuring — read the reach "
        "counters above, and if the answer is 'the generator never got near it', the fix is a "
        "targeted property that constructs the precondition, not a bigger max_examples.\n"
        f"{dict(sorted(_REACH.items()))}"
    )


#: The **document-level** residue: the foot of the receipt against the foot of the invoice.
#:
#: This is the number a customer compares and the number step 4's VAT return reconciles against
#: — the per-line census answers a different question. A line under a franc is under a franc;
#: twenty of them on one invoice is up to twenty francs at the foot, and nothing in a per-line
#: census can see that.
#:
#: It also covers the **FX path**, which the per-line census cannot: a line's `gross_amount` is
#: in the document's own currency, so a USD invoice could only be compared by converting it
#: back — but a document's `base_total_amount` is already in base, and so is the payload. And
#: FX is where fractional unit prices actually come from: `_price_in_base` multiplies an
#: inclusive price by the booking rate, so `prc` on a USD line is very rarely a round figure,
#: which is exactly the case the awkward-price property constructs by hand on the base side.
_DOCUMENT_RESIDUE: Counter[str] = Counter()
#: key → the largest absolute residue seen. A distribution says how often; this says how bad.
_WORST: dict[str, Decimal] = {}


def _worst(key: str, value: Decimal) -> None:
    if abs(value) > abs(_WORST.get(key, ZERO)):
        _WORST[key] = value


def _line_allowance(unit: Decimal, quantity: Decimal, rate: Decimal) -> Decimal:
    """How far one line's wire taxable amount may sit from the posted gross, **derived**.

    Not a threshold somebody measured and rounded up. The ledger rounds a line twice and the
    wire rounds it once, and each rounding has a known worst case:

    * the ledger rounds the discounted **net** to the currency's places, so `net_posted` is up
      to `u/2` from the exact net — and that error reaches the gross multiplied by `1 + r`,
      because the tax is then taken on the rounded figure;
    * the ledger rounds that **tax** to the same places, another `u/2`;
    * the wire's `prc` is the inclusive unit price at **two decimals**, so `splyAmt` is up to
      `0.005 x qty` out, and the discount it subtracts is rounded to two decimals as well.

    Summed: `u(1 + r)/2 + u/2 = u(2 + r)/2`, plus `0.005(qty + 1)` for the wire's side. At the
    standard rate on a zero-decimal base that ceiling is **1.09 francs**, and it is reached
    exactly — 5 x 6 661 at 10% off, exclusive, is 1.09 below the ledger — so it is tight rather
    than generous.

    **An undiscounted line cannot get there**, which is why the awkward-price property below
    can assert a whole franc: `qty x price` on integer inputs is an integer, so the net rounds
    exactly and the first term vanishes. Only a discount makes the net fractional, and only
    then does a line round twice.
    """
    return unit * (2 + rate) / 2 + PENNY / 2 * (quantity + 1)


def _posted_tax_in_base(db: Session, document: PartnerDocument) -> Decimal:
    """The document's tax, in base, **read off the ledger**.

    The tax-account lines are the ones carrying a `tax_code_id` with `tax_amount` zero — the
    shape P2 fixed and the VAT return reads. Taken from the journal rather than recomputed from
    `partner_documents.tax_amount`, which is in the document's own currency: converting it here
    would be this census agreeing with the code it is measuring.
    """
    total = db.scalar(
        select(func.coalesce(func.sum(func.abs(JournalLine.base_amount)), 0))
        .join(
            TaxCode,
            (TaxCode.id == JournalLine.tax_code_id)
            & (TaxCode.company_id == JournalLine.company_id),
        )
        .where(
            JournalLine.company_id == document.company_id,
            JournalLine.entry_id == document.journal_entry_id,
            # **The line posted to the tax code's own account.** Not "carries a tax code and
            # zero tax": an exempt or zero-rated *revenue* line is exactly that, and the first
            # version of this query counted whole revenue lines as tax — which is how it
            # reported a document 17 472 francs out and was wrong rather than alarming.
            JournalLine.gl_account_id == TaxCode.gl_account_id,
        )
    )
    return Decimal(str(total or 0))


def _census_document(
    db: Session, document: PartnerDocument, payload: dict, *, places: int, foreign: bool
) -> None:
    """One document's two residues: the total, and the tax.

    **The unit is the document's own, converted.** On a base-currency document the ledger
    rounded each line to the franc, so a franc per line is the bound. On a USD document at
    1 300.5 it rounded to the *cent* — and a cent is thirteen francs, so the same rounding
    step lands thirteen times larger in base. Measuring an FX document against the franc would
    report a correct build as six francs out and call it a defect; measuring it against its own
    minor unit says what it actually is.
    """
    currency = db.get(Currency, document.currency_id)
    # The smallest amount the ledger could have rounded to, expressed in base.
    unit = Decimal(1).scaleb(-currency.decimal_places) * document.exchange_rate
    scale = f"{places}dp {'fx' if foreign else 'base'}"
    lines = len([line for line in document.lines if line.kit_parent_line_id is None])
    _REACH[f"documents {scale}"] += 1
    if lines > 1:
        _REACH[f"multi-line documents {scale}"] += 1
    # **The document's own budget**, summed from its lines rather than assumed from their
    # count. Each line contributes what its rate and quantity allow it to (see
    # `_line_allowance`), so a four-line invoice of standard-rated goods is allowed more than a
    # four-line invoice of exempt ones — which is the truth, and "one unit per line" was not.
    budget = sum(
        (
            _line_allowance(
                unit,
                Decimal(str(item["qty"])),
                STANDARD_RATE if item["taxTyCd"] == "B" else ZERO,
            )
            for item in payload["itemList"]
        ),
        ZERO,
    )

    for field, wire, posted in (
        ("total", Decimal(str(payload["totAmt"])), document.base_total_amount),
        ("tax", Decimal(str(payload["totTaxAmt"])), _posted_tax_in_base(db, document)),
    ):
        residue = wire - posted
        _worst(f"{scale} {field}", residue)
        _worst(f"{scale} {field} in units", residue / unit)
        # **The number to read on a green run.** The counts below say how often; the ratio says
        # how much of the derived headroom the worst document actually used, so a regression
        # that doubled the residue is visible here long before it crosses the bound.
        _worst(f"{scale} {field} of budget", residue / budget)
        if residue == ZERO:
            _DOCUMENT_RESIDUE[f"{scale} {field} exact"] += 1
        elif abs(residue) <= unit:
            _DOCUMENT_RESIDUE[f"{scale} {field} one unit"] += 1
        elif abs(residue) <= budget:
            # Past a unit but inside what its own lines are allowed. A multi-line invoice lands
            # here routinely: the residues do not have to cancel and mostly do not.
            _DOCUMENT_RESIDUE[f"{scale} {field} within its budget"] += 1
        else:
            _DOCUMENT_RESIDUE[f"{scale} {field} OVER its budget"] += 1


def _census_line(
    wire: Decimal, posted: Decimal, *, places: int, discounted: bool, taxed: bool
) -> None:
    """One line's residue, in **minor units of the base currency**.

    Bucketed by the smallest unit that currency has, so the two bases are comparable: a franc
    on RWF and a cent on a two-decimal base are both "one unit", which is what the wire's own
    rounding step costs at most.

    Split by **discounted or not**, because the two have different causes and only one of them
    is decision 6's residue. An undiscounted line differs only where the wire's two decimals
    and the ledger's places differ; a discounted line differs because the wire takes the
    discount off an already-rounded `splyAmt` while the ledger applies it before rounding the
    line at all. The census reports both so the step-5 conversation can be about the right one.
    """
    unit = Decimal(1).scaleb(-places)
    difference = wire - posted
    scale = f"{places}dp {'discounted' if discounted else 'plain'}"
    _REACH[f"census lines {places}dp"] += 1
    if taxed:
        _REACH[f"census lines {places}dp taxed"] += 1
    if difference == ZERO:
        _RESIDUE[f"{scale} exact"] += 1
    elif abs(difference) <= unit:
        _RESIDUE[f"{scale} one unit"] += 1
    else:
        _RESIDUE[f"{scale} more than one unit"] += 1


@pytest.mark.slow
@pytest.mark.parametrize("two_decimals", [False, True], ids=["0dp", "2dp"])
@given(plan=PLAN)
@settings(deadline=None)
def test_the_payload_agrees_with_itself_and_the_residue_is_measured(
    db: Session, plan: list[tuple], sandbox_state, two_decimals: bool
) -> None:  # noqa: ANN001
    """Decision 6, over whatever the machine posted.

    Three assertions and one measurement:

    * every header bucket is Σ of the lines carrying that class, and the totals are Σ of the
      buckets — so a payload cannot disagree with itself;
    * every line's wire tax is `taxblAmt × r / (100 + r)` at two decimals, exactly;
    * every amount is non-negative, refunds included (the minus signs belong to the printed
      receipt, never to the wire);
    * and the residue between the wire's taxable amount and the posted gross is counted.
    """
    with _sandbox(sandbox_state) as client:
        fixture = fiscalize(
            db, build_order_entry(db, f"fis-map-{next(_EXAMPLE)}"), client, tag="map"
        )
        if two_decimals:
            _use_a_two_decimal_base(db, fixture)
        base = base_currency(db, fixture.company_id)
        places, base_id = base.decimal_places, base.id
        _drive(db, fixture, client, [step for step in plan if step[0] != "drain"])

        rows = db.scalars(
            select(FiscalOutboxRow).where(
                FiscalOutboxRow.company_id == fixture.company_id,
                FiscalOutboxRow.kind.in_(
                    (FiscalOutboxKind.SALE, FiscalOutboxKind.REFUND)
                ),
            )
        )
        for row in rows:
            _assert_payload_agrees_with_itself(row.payload)
            _assert_no_negative_amount(row.payload)
            _REACH["payloads"] += 1
            document = db.get(PartnerDocument, row.source_doc_id)
            # **Base-currency documents only.** `gross_amount` is in the *document's* currency
            # and the wire is in base, so a USD invoice would be comparing dollars with francs
            # — which is how this census first read 218 lines "a unit or more" apart and meant
            # nothing at all. The residue decision 6 names is the wire's two decimals against
            # the ledger's; a foreign document's own conversion rounding is a different
            # question, and one the ledger already owns.
            if document is None:
                continue
            # **Every document**, foreign included: `base_total_amount` and the payload are
            # both in base, so the comparison is sound where the per-line one is not.
            _census_document(
                db,
                document,
                row.payload,
                places=places,
                foreign=document.currency_id != base_id,
            )
            if document.currency_id != base_id:
                continue
            posted_lines = [
                line for line in document.lines if line.kit_parent_line_id is None
            ]
            # **`strict=True`.** The payload carries one item per non-kit-component line, so a
            # length mismatch is a mapping defect — and a silent truncation would go on to
            # compare the wrong pairs and report a residue about two different lines.
            for line, item in zip(posted_lines, row.payload["itemList"], strict=True):
                _census_line(
                    Decimal(str(item["taxblAmt"])),
                    line.gross_amount,
                    places=places,
                    discounted=line.discount_percent > 0,
                    taxed=Decimal(str(item["taxAmt"])) > 0,
                )


def _assert_payload_agrees_with_itself(payload: dict) -> None:
    items = payload["itemList"]
    for tax_class in "ABCD":
        taxable = sum(
            (
                Decimal(str(item["taxblAmt"]))
                for item in items
                if item["taxTyCd"] == tax_class
            ),
            ZERO,
        )
        tax = sum(
            (
                Decimal(str(item["taxAmt"]))
                for item in items
                if item["taxTyCd"] == tax_class
            ),
            ZERO,
        )
        assert Decimal(str(payload[f"taxblAmt{tax_class}"])) == taxable, (
            f"bucket {tax_class} is not the sum of its lines"
        )
        assert Decimal(str(payload[f"taxAmt{tax_class}"])) == tax

    assert Decimal(str(payload["totTaxblAmt"])) == sum(
        (Decimal(str(payload[f"taxblAmt{c}"])) for c in "ABCD"), ZERO
    )
    assert Decimal(str(payload["totTaxAmt"])) == sum(
        (Decimal(str(payload[f"taxAmt{c}"])) for c in "ABCD"), ZERO
    )
    # `totAmt` is the gross the customer pays, which on a VAT-inclusive receipt *is* the
    # taxable amount: adding the tax would double the VAT.
    assert Decimal(str(payload["totAmt"])) == Decimal(str(payload["totTaxblAmt"]))

    for item in items:
        rate = STANDARD_RATE_PERCENT if item["taxTyCd"] == "B" else ZERO
        taxable = Decimal(str(item["taxblAmt"]))
        expected = (
            (taxable * rate / (HUNDRED + rate)).quantize(PENNY, rounding=ROUND_HALF_UP)
            if rate
            else ZERO
        )
        assert Decimal(str(item["taxAmt"])) == expected, (
            f"line {item['itemSeq']}: taxAmt is not taxblAmt x r/(100+r)"
        )
        assert Decimal(str(item["splyAmt"])) - Decimal(str(item["dcAmt"])) == taxable
        if Decimal(str(item["dcRt"])) == ZERO:
            assert Decimal(str(item["dcAmt"])) == ZERO, (
                "a line nobody discounted may not carry a discount amount: the VSDC engine "
                "reads that as a payload validation failure"
            )


def _assert_no_negative_amount(value: object, path: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_no_negative_amount(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_negative_amount(item, f"{path}[{index}]")
    elif isinstance(value, int | float) and not isinstance(value, bool):
        assert value >= 0, (
            f"{path} is negative. A refund goes out positive under `rcptTyCd R`; the minus "
            "signs belong to the printed receipt (CIS §14), never to the wire."
        )


# --- The residue, on prices that cannot come out even (decision 6) ------------------------------
#
# **Why this exists, and it is the same lesson P6 wrote down at F-9.10.** The machine above
# reported a zero-decimal census of 211 lines in which every *undiscounted* line was exact, while
# `test_the_decision_6_residue_worked_by_hand` shows an undiscounted line 0.30 out. Both are
# true, and the reconciliation is the generator: the residue on a plain standard-rated exclusive
# line is `0.18 x price x qty − round(0.18 x price x qty)`, which is zero exactly when
# `price x qty` divides by 50 — and Hypothesis draws integers with a heavy bias toward round
# values, because round values shrink well. Round prices are precisely the ones with no residue,
# so the machine spent its census on the case that cannot show anything.
#
# Buying reach with `max_examples` would not have fixed that; the bias is in the shape of the
# draw, not its count. So this property **constructs the precondition** instead: a price
# congruent to 3 mod 5 can never make `price x qty` divisible by 50 for any quantity under 25,
# so every line it draws has a residue, and the property is about how large one can get.

#: `n x 5 + 3` — never a multiple of five, so `price x qty` is never a multiple of fifty for the
#: quantities this machine draws, and the residue is never trivially zero.
UNROUND_PRICE = st.integers(min_value=20, max_value=1800).map(lambda n: Decimal(n * 5 + 3))

AWKWARD_PLAN = st.lists(
    st.tuples(st.integers(min_value=1, max_value=8).map(Decimal), UNROUND_PRICE),
    min_size=2,
    max_size=6,
)


@pytest.mark.slow
@given(plan=AWKWARD_PLAN)
@settings(deadline=None)
def test_the_residue_is_under_one_unit_on_every_price_that_cannot_come_out_even(
    db: Session, plan: list[tuple], sandbox_state
) -> None:  # noqa: ANN001
    """**Never as much as one franc on an undiscounted line**, and the qualifier is load-bearing.

    A plain, standard-rated, exclusive line on a zero-decimal base, priced so that the wire and
    the ledger cannot agree by luck. What is asserted is that they never disagree by a whole
    unit — the wire's `taxAmt` and the ledger's differ by the rounding of a **single** tax
    split, and a difference of a franc or more would mean something else had gone wrong.

    A franc holds here because `qty x price` on integer inputs is an integer: the ledger's net
    rounding is exact, so the line rounds once. Put a discount on it and the net becomes
    fractional, the line rounds twice, and the bound is `u(2 + r)/2` rather than `u` — which is
    `test_a_discounted_line_rounds_twice_and_stays_inside_the_derived_bound` below, and which
    the step-2 report quoted to RRA without the qualifier.
    """
    with _sandbox(sandbox_state) as client:
        fixture = fiscalize(
            db, build_order_entry(db, f"fis-odd-{next(_EXAMPLE)}"), client, tag="odd"
        )
        # One line per invoice, deliberately: this property is about the **per-line** bound, and
        # a second line whose price `_lines` nudges by seven could land on a round value and
        # dilute the "every line has a residue" that makes the census here mean something. The
        # document-level census gets its multi-line coverage from the machine above.
        steps = [
            ("receive", "up", Decimal(8), Decimal(1000), ZERO, "stock", "tin",
             "VAT-OUT-18", TaxMode.EXCLUSIVE, False, 1)
        ]
        steps += [
            ("sell", "up", quantity, price, ZERO, "stock", "tin",
             "VAT-OUT-18", TaxMode.EXCLUSIVE, False, 1)
            for quantity, price in plan
        ]
        _drive(db, fixture, client, steps)

        for row in db.scalars(
            select(FiscalOutboxRow).where(
                FiscalOutboxRow.company_id == fixture.company_id,
                FiscalOutboxRow.kind == FiscalOutboxKind.SALE,
            )
        ):
            document = db.get(PartnerDocument, row.source_doc_id)
            if document is None:
                continue
            posted_lines = [
                line for line in document.lines if line.kit_parent_line_id is None
            ]
            for line, item in zip(posted_lines, row.payload["itemList"], strict=True):
                residue = Decimal(str(item["taxblAmt"])) - line.gross_amount
                _REACH["awkward lines"] += 1
                _RESIDUE[
                    "awkward exact" if residue == ZERO else "awkward under a franc"
                ] += 1
                assert abs(residue) < Decimal(1), (
                    f"{document.number} line {item['itemSeq']}: the wire says "
                    f"{item['taxblAmt']} and the ledger {line.gross_amount}, a whole franc "
                    "apart or more. The two may differ by the rounding of one tax split and "
                    "no more than that."
                )


# --- The residue on a line that rounds twice (decision 6) ---------------------------------------
#
# **Why this exists.** The property above drives undiscounted lines only — deliberately, because
# it is about the per-line bound and a second line could dilute it — and it therefore proves a
# franc about the case that rounds *once*. The census machine draws discounts, so the case that
# rounds twice was reachable all along and the numbers went into the same buckets; what nothing
# did was construct it, so how large it can get was never asked. The answer is 1.09 francs, and
# the file's two document-level bounds were both written as though it were under one.
#
# Same lesson, same shape as P7's targeted refusals: where a case matters and the generator
# reaches it only if the seed is kind, build the case.

#: **The precondition, constructed rather than drawn.** The first version of this drew the
#: machine's own quantities, prices and discounts, and one line in 1 196 got past a franc — the
#: same bias the awkward-price property was written for, because a line only rounds twice to any
#: effect when the *first* rounding is near its worst.
#:
#: It is worth exactly half a unit when the discounted net lands on `x.5`. At 10% off that is
#: `9 x qty x price / 10`, whose fractional part is `.5` exactly when `qty x price` ends in a
#: five — so an odd quantity against a price ending in five gets it on every single draw, and
#: what is left to vary is the tax rounding on top. (12.5% has the same shape one modulus over:
#: `qty x price` congruent to 4 mod 8. One discount is enough to drive the arithmetic, and the
#: machine draws the other.)
TEN_PERCENT = Decimal(10)
ODD_QUANTITY = st.sampled_from((Decimal(1), Decimal(3), Decimal(5), Decimal(7)))
#: `n x 10 + 5`, inside the machine's own 100-9 000 band.
PRICE_ENDING_IN_FIVE = st.integers(min_value=10, max_value=899).map(
    lambda n: Decimal(n * 10 + 5)
)

DISCOUNTED_PLAN = st.lists(
    st.tuples(ODD_QUANTITY, PRICE_ENDING_IN_FIVE),
    min_size=2,
    max_size=6,
)


@pytest.mark.slow
@given(plan=DISCOUNTED_PLAN)
@settings(deadline=None)
def test_a_discounted_line_rounds_twice_and_stays_inside_the_derived_bound(
    db: Session, plan: list[tuple], sandbox_state
) -> None:  # noqa: ANN001
    """The bound step 5 should quote to RRA for a discounted line: **`u(2 + r)/2`.**

    The ledger rounds the discounted net to the franc and then rounds the tax on that rounded
    net, so two roundings reach the gross and the second is taken on a figure the first already
    moved. The wire keeps both at two decimals. `_line_allowance` derives what that is worth;
    this drives the case and holds the build to it.

    That the bound is **reached** rather than merely respected is the
    `discounted lines a franc or more` floor in `_report_residue`, not an assertion here: a
    property that only ever saw residues of 0.2 would pass against any ceiling and would be
    measuring nothing. A deep pass puts about a tenth of these lines a franc or more out, which
    is the fact the two document bounds this replaced denied.
    """
    with _sandbox(sandbox_state) as client:
        fixture = fiscalize(
            db, build_order_entry(db, f"fis-disc-{next(_EXAMPLE)}"), client, tag="disc"
        )
        steps = [
            ("receive", "up", Decimal(8), Decimal(1000), ZERO, "stock", "tin",
             "VAT-OUT-18", TaxMode.EXCLUSIVE, False, 1)
        ]
        steps += [
            ("sell", "up", quantity, price, TEN_PERCENT, "stock", "tin",
             "VAT-OUT-18", TaxMode.EXCLUSIVE, False, 1)
            for quantity, price in plan
        ]
        _drive(db, fixture, client, steps)

        for row in db.scalars(
            select(FiscalOutboxRow).where(
                FiscalOutboxRow.company_id == fixture.company_id,
                FiscalOutboxRow.kind == FiscalOutboxKind.SALE,
            )
        ):
            document = db.get(PartnerDocument, row.source_doc_id)
            if document is None:
                continue
            posted_lines = [
                line for line in document.lines if line.kit_parent_line_id is None
            ]
            for line, item in zip(posted_lines, row.payload["itemList"], strict=True):
                residue = Decimal(str(item["taxblAmt"])) - line.gross_amount
                rate = STANDARD_RATE if item["taxTyCd"] == "B" else ZERO
                # `unit` is one franc: `build_order_entry` bases the tenant on RWF and this
                # property does not move it, which is the whole point — a zero-decimal base is
                # where the wire's two decimals and the ledger's none are furthest apart.
                allowance = _line_allowance(
                    Decimal(1), Decimal(str(item["qty"])), rate
                )
                _REACH["discounted lines"] += 1
                if abs(residue) >= Decimal(1):
                    _REACH["discounted lines a franc or more"] += 1
                # Prefixed, because `_census_line` already writes `0dp discounted ...` from
                # the machine and two censuses in one counter have to stay tellable apart.
                _RESIDUE[
                    "constructed discounted exact"
                    if residue == ZERO
                    else "constructed discounted under a franc"
                    if abs(residue) < Decimal(1)
                    else "constructed discounted a franc or more"
                ] += 1
                _worst("discounted line", residue)
                assert abs(residue) <= allowance, (
                    f"{document.number} line {item['itemSeq']}: the wire says "
                    f"{item['taxblAmt']} and the ledger {line.gross_amount}, {abs(residue)} "
                    f"apart against an allowance of {allowance}. A discounted line rounds "
                    "twice and no more than twice; past that is not rounding."
                )
