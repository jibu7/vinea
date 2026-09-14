"""The compounding-error property for order entry (P6 step 2).

One Hypothesis machine drives a *random sequence* of goods receipts, partial matches, direct
item invoices, customer returns, supplier returns and reversals, and asserts the whole of the
ledger, subledger, stock **and** order invariant suites after every single step.

Checking only the end state hides an error that one operation introduces and the next one
masks — and the accrual is exactly the sort of account where that happens, because a receipt
and a match move it in opposite directions and a wrong share in one can be cancelled by a
wrong share in the other.

It runs twice, against a base currency with **no** minor unit (RWF) and one with **two**
(USD), because the pro-rata relief rounds differently at each and the "last match takes the
remainder" rule is precisely what absorbs the difference.

**The 1000-over-3 case is generated deliberately**, not left to chance. A receipt of three
units worth 1 000 matched one unit at a time relieves 333 + 333 + 334, and reversing the first
leaves the ledger having relieved 667 where a recomputation over the survivors gives 666. A
generator drawing quantities independently would essentially never produce a receipt whose
value does not divide by its quantity *and* three separate single-unit matches against it, so
the shape that forced `accrual_relieved` to be a stored column is written out as its own
example rather than hoped for.

Illegal steps are skipped rather than failed — matching more than was received, reversing what
is already reversed, issuing what is not there under `block`. The property under test is the
invariant suite, not the plumbing.

Both machines carry `@pytest.mark.slow`, which is how the nightly deep workflow selects them.
"""

import itertools
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kernel.errors import LedgerStateError, PostingError
from app.models.currency import Currency
from app.models.inventory import GrnStatus
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind, DocumentStatus, PartnerDocument
from app.order_entry import grn as grn_service
from app.subledger import documents as documents_service
from tests.inventory.invariants import assert_stock_invariants
from tests.kernel.invariants import assert_ledger_invariants
from tests.order_entry.conftest import MARCH, OrderEntry, build_order_entry
from tests.order_entry.invariants import assert_order_invariants
from tests.subledger.invariants import assert_subledger_invariants

_EXAMPLE = itertools.count()
#: Which refusals the generator actually provoked. **Measured, not assumed**: the machine
#: used to clamp every match to the remaining quantity, which meant `match_exceeds_receipt`
#: could not be drawn at all and the suite looked as though it covered a boundary it never
#: reached. Counting them is how that stays honest.
_REFUSALS: dict[str, int] = {}


@pytest.fixture(scope="module", autouse=True)
def _report_refusals():  # noqa: ANN202
    yield
    if _REFUSALS:
        print("\n[property] refusals provoked:", dict(sorted(_REFUSALS.items())))
ZERO = Decimal(0)

OPERATIONS = (
    "receive",
    "match",
    "sell",
    "return_in",
    "return_out",
    "reverse",
    # Reversing the *receipt* rather than a document. Legal only while nothing has matched
    # against it, so this is what exercises `grn_matched` — and, when it is legal, the path
    # where an unmatched receipt's accrual credit has to come back off the account.
    "reverse_grn",
)

QUANTITIES = st.integers(min_value=1, max_value=40).map(Decimal)
COSTS = st.decimals(min_value=Decimal("1"), max_value=Decimal("2000"), places=2)

PLAN = st.lists(
    st.tuples(
        st.sampled_from(OPERATIONS),
        QUANTITIES,
        COSTS,
        st.integers(min_value=0, max_value=20),  # which GRN line / document to act on
        # Which warehouse the goods move through, and — **drawn independently** — which
        # branch the document is keyed on. Independently is the whole point: the accrual is
        # proved per branch, and a rule that used the document's branch instead of the
        # warehouse's is invisible until the two differ.
        st.booleans(),  # depot, or main
        st.booleans(),  # key the document on the other branch
    ),
    min_size=1,
    max_size=10,
)


def _assert_everything(db: Session, company_id: int) -> None:
    assert_ledger_invariants(db, company_id)
    assert_subledger_invariants(db, company_id)
    assert_stock_invariants(db, company_id)
    assert_order_invariants(db, company_id)


def _grn_lines(db: Session, fixture: OrderEntry) -> list:
    out = []
    for grn in db.scalars(
        select(grn_service.GoodsReceivedNote).where(
            grn_service.GoodsReceivedNote.company_id == fixture.company_id
        )
    ):
        if grn.status != GrnStatus.REVERSED:
            out.extend(grn.lines)
    return out


def _documents(db: Session, fixture: OrderEntry) -> list[PartnerDocument]:
    return list(
        db.scalars(
            select(PartnerDocument).where(
                PartnerDocument.company_id == fixture.company_id,
                PartnerDocument.status == DocumentStatus.POSTED,
            )
        )
    )


def _step(  # noqa: PLR0913
    db: Session,
    fixture: OrderEntry,
    operation: str,
    quantity,  # noqa: ANN001
    cost,  # noqa: ANN001
    pick: int,
    use_depot: bool,
    cross_branch: bool,
) -> None:
    """One operation, or nothing when the draw does not describe a legal one."""
    warehouse_id = fixture.depot.id if use_depot else fixture.main.id
    # The branch the *document* is keyed on, which may be neither the warehouse's nor the
    # default. `None` lets the document resolve its own.
    document_branch_id = (
        (fixture.main.branch_id if use_depot else fixture.depot_branch_id)
        if cross_branch
        else None
    )
    if operation == "receive":
        grn_service.post_grn(
            db,
            fixture.company_id,
            grn_service.GrnInput(
                partner_id=fixture.supplier.id,
                grn_date=MARCH,
                description="Receipt",
                warehouse_id=warehouse_id,
                lines=(
                    grn_service.GrnLineInput(
                        item_id=fixture.stock_item.id, quantity=quantity, unit_cost=cost
                    ),
                ),
            ),
            actor=fixture.owner,
        )
        return

    if operation == "match":
        lines = _grn_lines(db, fixture)
        if not lines:
            return
        line = lines[pick % len(lines)]
        # **Not clamped to the remaining quantity, deliberately.** Clamping made the machine
        # unable to draw an over-match at all, so `match_exceeds_receipt` was never exercised
        # by the property suite — only by a unit test that asks for it directly. The refusal
        # is caught and skipped like any other illegal step, and the quantity is drawn as it
        # fell, so the boundary gets hit from both sides.
        documents_service.post_document(
            db,
            fixture.company_id,
            PartnerRole.AP,
            documents_service.DocumentInput(
                kind=DocumentKind.INVOICE,
                partner_id=fixture.supplier.id,
                document_date=MARCH,
                branch_id=document_branch_id,
                description="Supplier invoice",
                lines=(
                    documents_service.LineInput(
                        item_id=fixture.stock_item.id,
                        quantity=quantity,
                        unit_price=cost,
                        grn_line_id=line.id,
                    ),
                ),
            ),
            actor=fixture.owner,
        )
        return

    if operation in ("sell", "return_in"):
        documents_service.post_document(
            db,
            fixture.company_id,
            PartnerRole.AR,
            documents_service.DocumentInput(
                kind=DocumentKind.INVOICE if operation == "sell" else DocumentKind.CREDIT_NOTE,
                partner_id=fixture.customer.id,
                document_date=MARCH,
                branch_id=document_branch_id,
                description="Sale" if operation == "sell" else "Return",
                lines=(
                    documents_service.LineInput(
                        item_id=fixture.stock_item.id,
                        quantity=quantity,
                        unit_price=cost,
                        warehouse_id=warehouse_id,
                    ),
                ),
            ),
            actor=fixture.owner,
        )
        return

    if operation == "return_out":
        documents_service.post_document(
            db,
            fixture.company_id,
            PartnerRole.AP,
            documents_service.DocumentInput(
                kind=DocumentKind.CREDIT_NOTE,
                partner_id=fixture.supplier.id,
                document_date=MARCH,
                branch_id=document_branch_id,
                description="Return to supplier",
                lines=(
                    documents_service.LineInput(
                        item_id=fixture.stock_item.id,
                        quantity=quantity,
                        unit_price=cost,
                        warehouse_id=warehouse_id,
                    ),
                ),
            ),
            actor=fixture.owner,
        )
        return

    if operation == "reverse_grn":
        grns = [
            grn
            for grn in db.scalars(
                select(grn_service.GoodsReceivedNote).where(
                    grn_service.GoodsReceivedNote.company_id == fixture.company_id
                )
            )
            if grn.status != GrnStatus.REVERSED
        ]
        if not grns:
            return
        grn_service.reverse_grn(
            db,
            grns[pick % len(grns)],
            on_date=MARCH,
            reason="Property reversal",
            actor=fixture.owner,
        )
        return

    if operation == "reverse":
        documents = _documents(db, fixture)
        if not documents:
            return
        document = documents[pick % len(documents)]
        documents_service.reverse_document(
            db, document, on_date=MARCH, reason="Property reversal", actor=fixture.owner
        )


def _drive(db: Session, fixture: OrderEntry, plan: list[tuple]) -> None:
    _assert_everything(db, fixture.company_id)
    for operation, quantity, cost, pick, use_depot, cross_branch in plan:
        try:
            _step(
                db,
                fixture,
                operation,
                quantity,
                Decimal(cost),
                pick,
                use_depot,
                cross_branch,
            )
        except (LedgerStateError, PostingError) as refused:
            _REFUSALS[getattr(refused, "code", "?")] = (
                _REFUSALS.get(getattr(refused, "code", "?"), 0) + 1
            )
            # An illegal step for the state we are in: matched beyond the receipt, issued
            # what is not there, reversed what is already reversed. Skipped, not failed.
            #
            # **No rollback**, deliberately — every one of these services refuses before it
            # writes, so there is nothing to undo, and rolling back here would discard the
            # tenant itself: the company, its partners and its items were created in this same
            # transaction, and the next step would fail looking for a supplier that no longer
            # existed. That is what it did before this comment was written.
            continue
        for grn in db.scalars(
            select(grn_service.GoodsReceivedNote).where(
                grn_service.GoodsReceivedNote.company_id == fixture.company_id
            )
        ):
            grn_service.refresh_status(db, grn)
        db.flush()
        _assert_everything(db, fixture.company_id)


@pytest.mark.slow
@given(plan=PLAN)
def test_the_invariants_hold_after_every_step_at_zero_decimals(
    db: Session, plan: list[tuple]
) -> None:
    """RWF: no minor unit, so every pro-rata relief rounds by up to half a franc and the
    "last match takes the remainder" rule is doing the most work it ever does."""
    _drive(db, build_order_entry(db, f"oe-rwf-{next(_EXAMPLE)}"), plan)


@pytest.mark.slow
@given(plan=PLAN)
def test_the_invariants_hold_after_every_step_at_two_decimals(
    db: Session, plan: list[tuple]
) -> None:
    """The same machine against a base currency with a minor unit."""
    fixture = build_order_entry(db, f"oe-usd-{next(_EXAMPLE)}")
    _use_a_two_decimal_base(db, fixture)
    _drive(db, fixture, plan)


def _use_a_two_decimal_base(db: Session, fixture: OrderEntry) -> None:
    base = db.scalar(
        select(Currency).where(
            Currency.company_id == fixture.company_id, Currency.is_base.is_(True)
        )
    )
    base.decimal_places = 2
    db.flush()


def test_the_1000_over_3_reversal_ties_out(db: Session, order_entry: OrderEntry) -> None:
    """The shape a generator would never draw, written out.

    Three units worth 1 000 relieve 333 + 333 + 334. Reverse the first and the ledger has
    relieved 667 where a recomputation over the survivors gives 666 — the franc that makes
    `accrual_relieved` a stored column rather than a derived one.
    """
    grn, _ = grn_service.post_grn(
        db,
        order_entry.company_id,
        grn_service.GrnInput(
            partner_id=order_entry.supplier.id,
            grn_date=MARCH,
            description="Three units worth a thousand",
            warehouse_id=order_entry.main.id,
            lines=(
                grn_service.GrnLineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(3),
                    unit_cost=Decimal("333.333333"),
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    assert grn.lines[0].value == Decimal(1_000)

    posted = []
    for _ in range(3):
        document, _ = documents_service.post_document(
            db,
            order_entry.company_id,
            PartnerRole.AP,
            documents_service.DocumentInput(
                kind=DocumentKind.INVOICE,
                partner_id=order_entry.supplier.id,
                document_date=MARCH,
                description="One unit",
                lines=(
                    documents_service.LineInput(
                        item_id=order_entry.stock_item.id,
                        quantity=Decimal(1),
                        unit_price=Decimal("333.333333"),
                        grn_line_id=grn.lines[0].id,
                    ),
                ),
            ),
            actor=order_entry.owner,
        )
        posted.append(document)
    shares = [document.lines[0].accrual_relieved for document in posted]
    assert sorted(shares) == [Decimal(333), Decimal(333), Decimal(334)], shares

    grn_service.refresh_status(db, grn)
    _assert_everything(db, order_entry.company_id)

    documents_service.reverse_document(
        db, posted[0], on_date=MARCH, reason="Billed twice", actor=order_entry.owner
    )
    grn_service.refresh_status(db, grn)
    _assert_everything(db, order_entry.company_id)
