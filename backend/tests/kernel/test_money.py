"""ADR-06 rounding rule, property-tested; dated rates; tax split (§2.3)."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session

from app.kernel.errors import PostingError
from app.kernel.money import (
    is_rounded,
    quantum,
    rate_on,
    round_amount,
    round_for,
    split_tax,
    to_base,
)
from app.models.currency import ExchangeRate
from tests.kernel.conftest import EUR_RATE, USD_RATE, YEAR, Ledger

amounts = st.decimals(
    min_value=Decimal("-1e13"),
    max_value=Decimal("1e13"),
    allow_nan=False,
    allow_infinity=False,
    places=8,
)
places = st.integers(min_value=0, max_value=6)


@given(amount=amounts, decimal_places=places)
@settings(max_examples=500)
def test_rounding_is_half_up_to_currency_places(amount: Decimal, decimal_places: int) -> None:
    rounded = round_amount(amount, decimal_places)
    unit = quantum(decimal_places)

    assert rounded == rounded.quantize(unit)  # no more decimals than the currency allows
    assert abs(rounded - amount) <= unit / 2  # nearest representable value
    assert round_amount(rounded, decimal_places) == rounded  # idempotent
    assert rounded == amount.quantize(unit, rounding=ROUND_HALF_UP)  # the documented rule
    if rounded != 0:
        assert (rounded > 0) == (amount > 0)  # never flips sign


@pytest.mark.parametrize(
    ("amount", "decimal_places", "expected"),
    [
        (Decimal("2.5"), 0, Decimal("3")),  # ties go away from zero…
        (Decimal("-2.5"), 0, Decimal("-3")),  # …in both directions
        (Decimal("0.125"), 2, Decimal("0.13")),
        (Decimal("1300.4999"), 0, Decimal("1300")),
        (Decimal("43345.665"), 0, Decimal("43346")),
    ],
)
def test_rounding_examples(amount: Decimal, decimal_places: int, expected: Decimal) -> None:
    assert round_amount(amount, decimal_places) == expected


def test_float_money_is_rejected_not_coerced() -> None:
    with pytest.raises(TypeError):
        round_amount(1.5, 2)  # type: ignore[arg-type]


@given(parts=st.lists(amounts, min_size=1, max_size=20), decimal_places=places)
@settings(max_examples=200)
def test_document_total_is_the_sum_of_rounded_lines(
    parts: list[Decimal], decimal_places: int
) -> None:
    """§2.3: totals are sums of rounded lines — so the total is itself exactly representable."""
    total = sum(round_amount(p, decimal_places) for p in parts)
    assert is_rounded(total, decimal_places)


def test_round_for_uses_the_currency_places(ledger: Ledger) -> None:
    assert round_for(ledger.currencies["RWF"])(Decimal("99.5")) == Decimal("100")
    assert round_for(ledger.currencies["USD"])(Decimal("99.555")) == Decimal("99.56")


@given(
    amount=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1e9"), places=2),
    rate=st.sampled_from([Decimal("18"), Decimal("16"), Decimal("7.5"), Decimal("0")]),
    decimal_places=st.sampled_from([0, 2]),
)
@settings(max_examples=300)
def test_tax_split_reconciles(amount: Decimal, rate: Decimal, decimal_places: int) -> None:
    amount = round_amount(amount, decimal_places)
    inclusive = split_tax(amount, rate, inclusive=True, decimal_places=decimal_places)
    exclusive = split_tax(amount, rate, inclusive=False, decimal_places=decimal_places)

    assert inclusive.gross == amount  # what the customer paid is exactly what was entered
    assert exclusive.net == amount
    assert is_rounded(inclusive.tax, decimal_places) and is_rounded(exclusive.tax, decimal_places)
    assert inclusive.tax >= 0 and exclusive.tax >= 0
    if rate == 0:
        assert inclusive.tax == exclusive.tax == 0


def test_tax_split_rwanda_example() -> None:
    # 59 RWF inclusive of 18% → 50 net + 9 VAT (RWF has no decimals)
    split = split_tax(Decimal("59"), Decimal("18"), inclusive=True, decimal_places=0)
    assert (split.net, split.tax) == (Decimal("50"), Decimal("9"))
    split = split_tax(Decimal("100"), Decimal("18"), inclusive=False, decimal_places=0)
    assert (split.net, split.tax) == (Decimal("100"), Decimal("18"))


def test_to_base_rounds_per_line_to_base_places(db: Session, ledger: Ledger) -> None:
    usd = ledger.currencies["USD"]
    converted = to_base(db, Decimal("33.33"), usd, date(YEAR, 3, 1))
    assert converted.rate == USD_RATE
    assert converted.amount == Decimal("33.33")
    assert converted.base_amount == Decimal("43346")  # 43345.665 → 43346 (RWF, 0 dp)

    base = to_base(db, Decimal("1234"), ledger.base, date(YEAR, 3, 1))
    assert base.rate == 1 and base.base_amount == Decimal("1234")


def test_rate_resolution_by_effective_date(db: Session, ledger: Ledger) -> None:
    usd = ledger.currencies["USD"]
    db.add(
        ExchangeRate(
            company_id=ledger.company_id,
            currency_id=usd.id,
            valid_from=date(YEAR, 6, 1),
            rate=Decimal("1350"),
        )
    )
    db.flush()

    assert rate_on(db, usd, date(YEAR, 5, 31)) == USD_RATE
    assert rate_on(db, usd, date(YEAR, 6, 1)) == Decimal("1350")
    assert rate_on(db, usd, date(YEAR, 12, 31)) == Decimal("1350")
    assert rate_on(db, ledger.currencies["EUR"], date(YEAR, 2, 1)) == EUR_RATE
    with pytest.raises(PostingError) as excinfo:
        rate_on(db, usd, date(YEAR - 1, 12, 31))
    assert excinfo.value.code == "no_exchange_rate"


def test_explicit_rate_override_wins(db: Session, ledger: Ledger) -> None:
    converted = to_base(
        db, Decimal("10"), ledger.currencies["USD"], date(YEAR, 3, 1), rate=Decimal("1000")
    )
    assert converted.base_amount == Decimal("10000")
