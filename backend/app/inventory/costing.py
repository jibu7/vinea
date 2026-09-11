"""Weighted-average costing (Master Plan §5 P5, decision 4) — the arithmetic, alone.

Nothing in this module touches the database. It takes the state the caller read
(`ItemState`, `LocationState`), a move to value, and returns the value that move carries and
the state that follows it. That separation is deliberate: the costing rules are the part of
inventory that is hardest to get right and easiest to test, and a pure function can be driven
through a thousand sequences by Hypothesis without a Postgres round trip per step.

The rules, in the order they bite:

* **Average per item per company, across every location, in posting order.** A backdated
  receipt changes the average from the moment it is *posted*; issues posted earlier keep the
  value they were given. Immutability and closed periods outrank date order, and there is no
  restatement pass hiding anywhere.
* **Receipt value** = `round(quantity × unit_cost)` to the base currency's decimals.
* **Issue value** = `round(quantity × average)` — **except** an issue that takes a location
  to exactly zero, which takes whatever value that location had left. That exception is the
  whole reason the ±1-minor-unit drift a six-decimal average leaves behind never accumulates:
  the location is flushed, not approximately flushed.
* **A frozen value** overrides both. That is how a transfer's receive leg carries the value
  its dispatch leg gave up (decision 6) and how a revaluation states its own amount.
* **Average** = Σvalue / Σquantity over all locations, to six decimals, while Σquantity > 0;
  once the item has no stock the last average taken while it had some is what an issue is
  costed at (decision 5's `allow` policy is the only way to reach that).

One rule the plan does not spell out, because it can only arise under `allow`: **covering a
deficit**. A negative location exists because an issue was costed at the last positive average
— a guess at what units that were not there were worth. A receipt settles that guess: those
units cost what the receipt paid, so the location ends holding the remainder at the receipt's
cost and the difference between guess and fact leaves as a variance of its own. That keeps the
receipt worth what it cost, keeps an emptied location worth nothing, and puts the correction
in the ledger where it can be seen instead of smearing it into the average. `crossing_residue`
is that rule; `stranded_value` is its narrow cousin for reversals, which mirror a value rather
than compute one.
"""

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal, localcontext

from app.kernel.money import MONEY_PRECISION, ZERO, round_amount
from app.models.inventory import AVERAGE_SCALE

AVERAGE_QUANTUM = Decimal(1).scaleb(-AVERAGE_SCALE)


def quantize_average(value: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = MONEY_PRECISION
        return value.quantize(AVERAGE_QUANTUM, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class LocationState:
    """What one (item, warehouse) cell holds."""

    quantity: Decimal = ZERO
    value: Decimal = ZERO

    def after(self, quantity: Decimal, value: Decimal) -> "LocationState":
        return LocationState(quantity=self.quantity + quantity, value=self.value + value)


@dataclass(frozen=True)
class ItemState:
    """The item's totals across every location, and the average that follows from them."""

    quantity: Decimal = ZERO
    value: Decimal = ZERO
    last_positive_average: Decimal = ZERO

    @property
    def average(self) -> Decimal:
        """Σvalue / Σquantity to six decimals while there is stock; the last average taken
        while there was, once there is not."""
        if self.quantity > ZERO:
            with localcontext() as ctx:
                ctx.prec = MONEY_PRECISION
                return quantize_average(self.value / self.quantity)
        return self.last_positive_average

    def after(self, quantity: Decimal, value: Decimal) -> "ItemState":
        moved = replace(self, quantity=self.quantity + quantity, value=self.value + value)
        # The average is only "positive" in the sense that it was taken while stock existed;
        # a written-down item can have a zero or negative one and that is still the number an
        # issue against no stock must use.
        if moved.quantity > ZERO:
            return replace(moved, last_positive_average=moved.average)
        return moved


@dataclass(frozen=True)
class Valued:
    """A move, priced. `flushed` records that the value came from emptying the location
    rather than from the average — the tape checks that distinction row by row."""

    quantity: Decimal
    value: Decimal
    unit_cost: Decimal | None
    flushed: bool = False


def value_move(
    *,
    quantity: Decimal,
    unit_cost: Decimal | None,
    frozen_value: Decimal | None,
    item: ItemState,
    location: LocationState,
    decimal_places: int,
) -> Valued:
    """Price one move. `quantity` is signed and in the item's base unit; `decimal_places` is
    the base currency's.

    `frozen_value` wins when it is given: a transfer's receive leg is worth exactly what its
    dispatch gave up, and a revaluation is worth what it says.
    """
    if quantity == ZERO:
        if frozen_value is None:
            raise ValueError("a zero-quantity move must carry its own value")
        return Valued(quantity=ZERO, value=frozen_value, unit_cost=None)

    if frozen_value is not None:
        return Valued(
            quantity=quantity,
            value=frozen_value,
            unit_cost=_implied_unit_cost(frozen_value, quantity),
        )

    if quantity > ZERO:
        if unit_cost is None:
            raise ValueError("a receipt must carry a unit cost")
        return Valued(
            quantity=quantity,
            value=round_amount(quantity * unit_cost, decimal_places),
            unit_cost=unit_cost,
        )

    # An issue that empties the location takes the location's remaining value, whatever the
    # average says it "should" be worth. This is the flush.
    if location.quantity + quantity == ZERO:
        return Valued(
            quantity=quantity,
            value=-location.value,
            unit_cost=_implied_unit_cost(-location.value, quantity),
            flushed=True,
        )
    average = item.average
    return Valued(
        quantity=quantity,
        value=-round_amount(-quantity * average, decimal_places),
        unit_cost=average,
    )


def crossing_residue(
    *,
    before: LocationState,
    after: LocationState,
    unit_cost: Decimal | None,
    decimal_places: int,
) -> Decimal | None:
    """The variance to expel when a receipt brings a location **out of deficit**.

    A location goes negative only under the `allow` policy, and only because an issue was
    costed at the last positive average — a guess at what the missing units were worth. When
    stock arrives, that guess is settled: the units that were owed cost what this receipt paid
    for them, so the location must end holding the **remainder at the receipt's cost**, and the
    difference between the guess and the fact is a variance.

    Expressed as the target rather than as a formula, because the target is the thing that has
    to be true: after the crossing the location holds `round(quantity × unit_cost)`, and
    whatever the arithmetic left over goes out as a move of its own.

    This is not a restatement of the provisional issue — that move keeps the value it posted,
    and `cost_provisional` still marks it. It is a new fact recognised on the day the deficit
    was covered, which is the only day anyone could have known it.

    Returns None when the move does not cross (a receipt into a location that was already
    non-negative, or one that leaves it in deficit still), or when there is no cost to rate
    the remainder at.
    """
    if before.quantity >= ZERO or after.quantity < ZERO or unit_cost is None:
        return None
    target = round_amount(after.quantity * unit_cost, decimal_places)
    return (target - after.value) or None


def stranded_value(location: LocationState) -> Decimal | None:
    """Value a location is left holding at zero quantity, or None when it holds none.

    The narrow case, for reversals. A reversal mirrors a value rather than computing one, so
    it cannot apply the crossing rule — it is undoing a move, not covering a deficit — but it
    can still empty a location whose value has moved since, and a location holding stock worth
    something while holding no stock is not a thing the valuation report can render.
    """
    if location.quantity == ZERO and location.value != ZERO:
        return -location.value
    return None


def _implied_unit_cost(value: Decimal, quantity: Decimal) -> Decimal:
    """The per-unit rate a move worked out at, for the enquiry and the movement report.

    `value` stays authoritative — this is a quotient carried to ten decimals, and for a
    flushed or frozen move it need not reproduce the value when multiplied back out. The
    column records the rate applied, not a second opinion on the amount.
    """
    with localcontext() as ctx:
        ctx.prec = MONEY_PRECISION
        return (value / quantity).quantize(Decimal(1).scaleb(-10), rounding=ROUND_HALF_UP)
