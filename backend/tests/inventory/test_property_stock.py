"""The compounding-error property for the stock ledger (P5 step 2).

One Hypothesis machine drives a *random sequence* of receipts, issues, transfers,
revaluations and reversals across two warehouses, and asserts the whole of
`assert_stock_invariants` **and** `assert_ledger_invariants` after every single step. Checking
only the end state hides an error that one operation introduces and the next one masks, which
in a weighted-average ledger is the normal way for a costing bug to survive: the average
absorbs it, and the next issue quietly spends the difference.

It runs twice, against a base currency with **no** minor unit (RWF) and one with **two**
(USD). Those are genuinely different arithmetic: at 0 dp every issue rounds by up to half a
franc, so the gap between "the average times the quantity" and "what the location actually
holds" opens much faster — which is the gap the flush rule closes, and the reason the
generator is also allowed to draw quantities in the millions with costs carried to four
decimals.

Illegal moves are skipped rather than failed: an issue with nothing to issue under `block`,
a reversal of something already reversed. The property under test is the invariant suite, not
the plumbing.

Both machines carry `@pytest.mark.slow`, which is how the nightly deep workflow selects them
(`pytest -m slow`). Without the marker they would run at two examples in CI and *never* at
three hundred anywhere — a guard that exists and is never fired.
"""

import itertools
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory import stock as stock_service
from app.inventory.stock import location_balance
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.sequences import DocType
from app.models.currency import Currency, ExchangeRate
from app.models.inventory import NegativeStockPolicy, StockMove
from app.models.journal import JournalEntry
from tests.inventory.conftest import Stock, document, fresh_stock
from tests.inventory.invariants import assert_stock_invariants
from tests.kernel.conftest import YEAR
from tests.kernel.invariants import assert_ledger_invariants

START = date(YEAR, 3, 3)

#: Names a tenant per Hypothesis example. A counter rather than the draw, because Hypothesis
#: replays a shrinking example many times and every replay needs its own company.
_EXAMPLE = itertools.count()

OPERATIONS = ("receive", "issue", "empty", "transfer", "revalue", "reverse")

#: `empty` issues *exactly* what a location holds, and it has to be its own operation because
#: the generator can never draw it: with quantities drawn independently, an issue that happens
#: to equal the balance to six decimal places is a coincidence that does not occur. Emptying a
#: location is the whole shape the flush rule governs, so without this the suite passes green
#: with the flush rule deleted — which it did, until this was added.

#: Two shapes of quantity. The small ones make the everyday arithmetic; the large ones are
#: where a six-decimal average times a quantity stops reproducing the value, which is the
#: shape the flush rule exists for and the one a generator that only ever draws 1–20 units
#: can never produce.
QUANTITIES = st.one_of(
    st.integers(min_value=1, max_value=40).map(Decimal),
    st.integers(min_value=100_000, max_value=3_000_000).map(Decimal),
)
COSTS = st.decimals(
    min_value=Decimal("0.0001"), max_value=Decimal(5000), places=4, allow_nan=False
)
REVALUATIONS = st.integers(min_value=-5000, max_value=5000).filter(bool).map(Decimal)

PLAN = st.lists(
    st.tuples(
        st.sampled_from(OPERATIONS),
        QUANTITIES,
        COSTS,
        REVALUATIONS,
        st.booleans(),  # which warehouse
        st.integers(min_value=0, max_value=20),  # which entry to reverse
    ),
    min_size=1,
    max_size=14,
)


@dataclass
class _Clock:
    """Every step is a day later, so a reversal is never dated before what it reverses. Every
    fourth step is backdated instead — posting order is not date order, and the invariants are
    asserted at every date, so the backdated ones are where the two diverge."""

    day: int = 0
    steps: int = 0

    def tick(self) -> date:
        self.day += 1
        self.steps += 1
        if self.steps % 4 == 0:
            return START + timedelta(days=max(self.day - 3, 0))
        return START + timedelta(days=self.day)


def _use_a_two_decimal_base(db: Session, fixture: Stock) -> None:
    """Make USD the base currency, so the same machine runs against a ledger with a minor
    unit. The stock ledger has no currency dimension of its own — a move is always in base —
    so the base's `decimal_places` is the only thing that changes, which is exactly the
    variable under test.
    """
    company_id = fixture.company_id
    currencies = {
        row.code: row
        for row in db.scalars(select(Currency).where(Currency.company_id == company_id))
    }
    # The partial unique index allows exactly one base, so the old one steps down first.
    currencies["RWF"].is_base = False
    db.flush()
    currencies["USD"].is_base = True
    db.flush()
    db.execute(
        ExchangeRate.__table__.delete().where(
            ExchangeRate.company_id == company_id,
            ExchangeRate.currency_id == currencies["USD"].id,
        )
    )
    db.add(
        ExchangeRate(
            company_id=company_id,
            currency_id=currencies["RWF"].id,
            valid_from=date(YEAR, 1, 1),
            rate=Decimal("0.00077"),
        )
    )
    db.flush()


@dataclass
class _Machine:
    fixture: Stock
    clock: _Clock = field(default_factory=_Clock)
    entries: list[int] = field(default_factory=list)

    def run(self, db: Session, step: tuple) -> None:
        operation, quantity, cost, revaluation, second_warehouse, which = step
        warehouse = self.fixture.depot if second_warehouse else self.fixture.main
        on = self.clock.tick()
        try:
            if operation == "receive":
                posting = stock_service.receive_stock(
                    db,
                    self.fixture.company_id,
                    document=self._document(DocType.INV_ADJUSTMENT, on, "ADJIN"),
                    lines=[self._line(warehouse, quantity=quantity, unit_cost=cost)],
                    actor=self.fixture.owner,
                )
            elif operation in ("issue", "empty"):
                if operation == "empty":
                    on_hand = location_balance(
                        db, self.fixture.company_id, self.fixture.item.id, warehouse.id
                    ).quantity
                    if on_hand <= Decimal(0):
                        return
                    quantity = on_hand
                posting = stock_service.issue_stock(
                    db,
                    self.fixture.company_id,
                    document=self._document(DocType.INV_ADJUSTMENT, on, "ADJOUT"),
                    lines=[self._line(warehouse, quantity=quantity)],
                    actor=self.fixture.owner,
                )
            elif operation == "transfer":
                source, destination = (
                    (self.fixture.depot, self.fixture.main)
                    if second_warehouse
                    else (self.fixture.main, self.fixture.depot)
                )
                dispatch = stock_service.transfer_stock(
                    db,
                    self.fixture.company_id,
                    document=self._document(DocType.INV_TRANSFER, on, "TRF"),
                    item_id=self.fixture.item.id,
                    quantity=quantity,
                    from_warehouse_id=source.id,
                    to_warehouse_id=self.fixture.transit.id,
                    actor=self.fixture.owner,
                )
                self._remember(dispatch)
                assert_stock_invariants(db, self.fixture.company_id)
                assert_ledger_invariants(db, self.fixture.company_id)
                posting = stock_service.transfer_stock(
                    db,
                    self.fixture.company_id,
                    document=self._document(DocType.INV_TRANSFER, on, "TRF"),
                    item_id=self.fixture.item.id,
                    quantity=quantity,
                    from_warehouse_id=self.fixture.transit.id,
                    to_warehouse_id=destination.id,
                    actor=self.fixture.owner,
                )
            elif operation == "revalue":
                posting = stock_service.revalue_stock(
                    db,
                    self.fixture.company_id,
                    document=self._document(DocType.INV_ADJUSTMENT, on, "REVAL"),
                    lines=[self._line(warehouse, value=revaluation)],
                    actor=self.fixture.owner,
                )
            else:
                if not self.entries:
                    return
                target = self.entries[which % len(self.entries)]
                original = db.get(JournalEntry, target)
                posting = stock_service.reverse_stock_posting(
                    db,
                    self.fixture.company_id,
                    entry_id=target,
                    on_date=max(on, original.entry_date),
                    reason="property test",
                    actor=self.fixture.owner,
                )
        except (PostingError, LedgerStateError):
            # A move the rules refuse — nothing to check, and nothing was written.
            return
        self._remember(posting)

    def _remember(self, posting: stock_service.StockPosting) -> None:
        """Only a posting that reached the ledger can be reversed. A valueless one — stock
        received at no cost, or issued while the average is zero — writes moves and no entry
        (decision 1), so there is nothing for `reverse` to name."""
        if posting.entry is not None:
            self.entries.append(posting.entry.id)

    def _document(self, doc_type: str, on: date, type_code: str) -> stock_service.StockDocument:
        return document(
            doc_type, on, transaction_type_id=self.fixture.type_id(type_code), description="prop"
        )

    def _line(
        self,
        warehouse,  # noqa: ANN001 - the ORM warehouse row
        *,
        quantity: Decimal = Decimal(0),
        unit_cost: Decimal | None = None,
        value: Decimal | None = None,
    ) -> stock_service.StockLine:
        return stock_service.StockLine(
            item_id=self.fixture.item.id,
            warehouse_id=warehouse.id,
            quantity=quantity,
            unit_cost=unit_cost,
            value=value,
        )


def _drive(
    db: Session, fixture: Stock, plan: list[tuple], policy: NegativeStockPolicy
) -> None:
    fixture.inventory.settings.negative_stock_policy = policy
    db.flush()
    machine = _Machine(fixture=fixture)
    # The posting dates whose trial balance has already been built, for this tenant. Each date
    # is reported on once rather than once per step; see `assert_ledger_invariants` for why
    # that loses nothing.
    footed: set[date] = set()
    for step in plan:
        machine.run(db, step)
        assert_stock_invariants(db, fixture.company_id)
        assert_ledger_invariants(db, fixture.company_id, trial_balance_dates=footed)
    # And once more against the moves themselves, in case every step happened to be refused.
    assert not stock_service.verify_stock_balances(db, fixture.company_id)


@pytest.mark.slow
@given(plan=PLAN, allow_negative=st.booleans())
def test_the_invariants_hold_after_every_step_at_zero_decimals(
    db: Session, plan: list[tuple], allow_negative: bool
) -> None:
    """RWF: no minor unit, so every issue rounds by up to half a franc and the drift the flush
    rule absorbs opens as fast as it ever will."""
    _drive(
        db,
        fresh_stock(db, f"rwf-{next(_EXAMPLE)}"),
        plan,
        NegativeStockPolicy.ALLOW if allow_negative else NegativeStockPolicy.BLOCK,
    )


@pytest.mark.slow
@given(plan=PLAN, allow_negative=st.booleans())
def test_the_invariants_hold_after_every_step_at_two_decimals(
    db: Session, plan: list[tuple], allow_negative: bool
) -> None:
    """The same machine against a base currency with a minor unit — different rounding, same
    invariants."""
    fixture = fresh_stock(db, f"usd-{next(_EXAMPLE)}")
    _use_a_two_decimal_base(db, fixture)
    _drive(
        db,
        fixture,
        plan,
        NegativeStockPolicy.ALLOW if allow_negative else NegativeStockPolicy.BLOCK,
    )


@pytest.mark.parametrize("policy", list(NegativeStockPolicy))
def test_a_run_of_every_operation_leaves_a_ledger_that_verifies(
    db: Session, stock: Stock, policy: NegativeStockPolicy
) -> None:
    """A fixed script, so the suite has one deterministic run of the same machine that does
    not depend on what the generator happened to draw — and so `pytest -k` has something to
    reproduce a Hypothesis failure against."""
    plan = [
        ("receive", Decimal(100), Decimal("12.5"), Decimal(1), False, 0),
        ("receive", Decimal(3), Decimal("7.3333"), Decimal(1), True, 0),
        ("issue", Decimal(7), Decimal(1), Decimal(1), False, 0),
        ("transfer", Decimal(11), Decimal(1), Decimal(1), False, 0),
        ("revalue", Decimal(1), Decimal(1), Decimal(-250), False, 0),
        ("issue", Decimal(3), Decimal(1), Decimal(1), True, 0),
        ("reverse", Decimal(1), Decimal(1), Decimal(1), False, 0),
        ("receive", Decimal(2_500_000), Decimal("1.0007"), Decimal(1), False, 0),
        ("empty", Decimal(1), Decimal(1), Decimal(1), False, 0),
    ]

    _drive(db, stock, plan, policy)

    assert db.scalar(
        select(StockMove.id).where(StockMove.company_id == stock.company_id).limit(1)
    ) is not None
