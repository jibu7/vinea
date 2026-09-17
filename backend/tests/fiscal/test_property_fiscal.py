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
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fiscal import drainer
from app.fiscal import outbox as outbox_service
from app.fiscal.rwanda.sandbox import create_sandbox_app
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.money import base_currency
from app.models.currency import Currency
from app.models.fiscalization import FiscalOutboxKind, FiscalOutboxRow, FiscalOutboxStatus
from app.models.partner import TaxMode
from app.models.subledger import DocumentKind, DocumentStatus, PartnerDocument
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
    ),
    min_size=3,
    max_size=14,
)


@pytest.fixture(scope="module", autouse=True)
def _report_census():  # noqa: ANN202
    yield
    if _REFUSALS:
        print("\n[property] refusals provoked:", dict(sorted(_REFUSALS.items())))
    if _STATES:
        print("[property] queue states reached:", dict(sorted(_STATES.items())))


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
                        lines=(
                            documents_service.LineInput(
                                item_id=_item(fixture, which_item).id,
                                quantity=quantity,
                                unit_price=price,
                                discount_percent=discount,
                                tax_code_id=fixture.tax_codes[tax_code].id,
                            ),
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
#: Only a run with enough examples can be held to a floor. The per-commit profile draws two.
_FLOORS_FROM_EXAMPLES = 100


@pytest.fixture(scope="module", autouse=True)
def _report_residue():  # noqa: ANN202
    yield
    if _RESIDUE:
        print("\n[decision 6] residue census (wire taxable - posted gross):", dict(_RESIDUE))
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
    assert not short, (
        f"the deep pass under-reached {short}. A census that measured almost nothing is green "
        "for a reason that has nothing to do with what it is measuring — read the reach "
        "counters above, and if the answer is 'the generator never got near it', the fix is a "
        "targeted property that constructs the precondition, not a bigger max_examples.\n"
        f"{dict(sorted(_REACH.items()))}"
    )


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
            if document is None or document.currency_id != base_id:
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
        rate = Decimal("18.00") if item["taxTyCd"] == "B" else ZERO
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
    """The bound step 5 quotes to RRA: **never as much as one franc on a line.**

    A plain, standard-rated, exclusive line on a zero-decimal base, priced so that the wire and
    the ledger cannot agree by luck. What is asserted is that they never disagree by a whole
    unit — the wire's `taxAmt` and the ledger's differ by the rounding of a single tax split,
    and a difference of a franc or more would mean something else had gone wrong.
    """
    with _sandbox(sandbox_state) as client:
        fixture = fiscalize(
            db, build_order_entry(db, f"fis-odd-{next(_EXAMPLE)}"), client, tag="odd"
        )
        steps = [
            ("receive", "up", Decimal(8), Decimal(1000), ZERO, "stock", "tin",
             "VAT-OUT-18", TaxMode.EXCLUSIVE, False)
        ]
        steps += [
            ("sell", "up", quantity, price, ZERO, "stock", "tin",
             "VAT-OUT-18", TaxMode.EXCLUSIVE, False)
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
