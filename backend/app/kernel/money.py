"""Money, rounding, exchange rates and tax arithmetic (ADR-06, §2.3).

One rounding rule, applied everywhere: **per line, half-up (ties away from zero), to the
currency's `decimal_places`** (RWF → 0, USD → 2). Document totals are sums of rounded lines.
Base-currency amounts are frozen on each journal line at posting time.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kernel.errors import PostingError
from app.models.currency import Currency, ExchangeRate
from app.models.tax import TaxCode

# Numeric(20,6) × Numeric(20,10) needs more than the default 28 significant digits.
MONEY_PRECISION = 40
ONE = Decimal(1)
ZERO = Decimal(0)


def quantum(decimal_places: int) -> Decimal:
    if decimal_places < 0:
        raise ValueError("decimal_places must be >= 0")
    return ONE.scaleb(-decimal_places)


def round_amount(amount: Decimal, decimal_places: int) -> Decimal:
    """The ADR-06 rule. `Decimal` only — floats are rejected, not coerced."""
    if not isinstance(amount, Decimal):
        raise TypeError(f"money must be Decimal, got {type(amount).__name__}")
    with localcontext() as ctx:
        ctx.prec = MONEY_PRECISION
        return amount.quantize(quantum(decimal_places), rounding=ROUND_HALF_UP)


def round_for(currency: Currency) -> Callable[[Decimal], Decimal]:
    places = currency.decimal_places

    def _round(amount: Decimal) -> Decimal:
        return round_amount(amount, places)

    return _round


def is_rounded(amount: Decimal, decimal_places: int) -> bool:
    return amount == round_amount(amount, decimal_places)


def base_currency(db: Session, company_id: int) -> Currency:
    currency = db.scalar(
        select(Currency).where(Currency.company_id == company_id, Currency.is_base)
    )
    if currency is None:
        raise PostingError("Company has no base currency", code="no_base_currency")
    return currency


def rate_on(db: Session, currency: Currency, on_date: date) -> Decimal:
    """Base units per one unit of `currency` on `on_date` (latest `valid_from <= date`)."""
    if currency.is_base:
        return ONE
    rate = db.scalar(
        select(ExchangeRate.rate)
        .where(
            ExchangeRate.company_id == currency.company_id,
            ExchangeRate.currency_id == currency.id,
            ExchangeRate.valid_from <= on_date,
        )
        .order_by(ExchangeRate.valid_from.desc())
        .limit(1)
    )
    if rate is None:
        raise PostingError(
            f"No exchange rate for {currency.code} on or before {on_date.isoformat()}",
            code="missing_exchange_rate",
            field_errors={"currency_id": ["no exchange rate effective on the entry date"]},
        )
    return rate


@dataclass(frozen=True)
class Converted:
    amount: Decimal  # rounded to the transaction currency
    base_amount: Decimal  # rounded to the base currency
    rate: Decimal


def to_base(
    db: Session,
    amount: Decimal,
    currency: Currency,
    on_date: date,
    *,
    base: Currency | None = None,
    rate: Decimal | None = None,
) -> Converted:
    """Round `amount` to its currency, convert at the dated rate (or an explicit override),
    round the result to the base currency. Both roundings happen per line."""
    base = base or base_currency(db, currency.company_id)
    if currency.is_base:
        rounded = round_amount(amount, currency.decimal_places)
        return Converted(amount=rounded, base_amount=rounded, rate=ONE)
    effective_rate = rate if rate is not None else rate_on(db, currency, on_date)
    if effective_rate <= 0:
        raise PostingError("Exchange rate must be positive", code="invalid_exchange_rate")
    rounded = round_amount(amount, currency.decimal_places)
    with localcontext() as ctx:
        ctx.prec = MONEY_PRECISION
        converted = rounded * effective_rate
    return Converted(
        amount=rounded,
        base_amount=round_amount(converted, base.decimal_places),
        rate=effective_rate,
    )


@dataclass(frozen=True)
class TaxSplit:
    net: Decimal
    tax: Decimal

    @property
    def gross(self) -> Decimal:
        return self.net + self.tax


def split_tax(
    amount: Decimal, rate_pct: Decimal, *, inclusive: bool, decimal_places: int
) -> TaxSplit:
    """Tax on one line. Inclusive: `amount` is gross → tax = amount × r/(100+r).
    Exclusive: `amount` is net → tax = amount × r/100. Tax is rounded per line; the net is
    what remains, so net + tax reproduces the entered amount exactly when inclusive."""
    with localcontext() as ctx:
        ctx.prec = MONEY_PRECISION
        amount = round_amount(amount, decimal_places)
        if inclusive:
            tax = round_amount(amount * rate_pct / (Decimal(100) + rate_pct), decimal_places)
            return TaxSplit(net=amount - tax, tax=tax)
        tax = round_amount(amount * rate_pct / Decimal(100), decimal_places)
        return TaxSplit(net=amount, tax=tax)


# --- Fingerprints -----------------------------------------------------------------------------
#
# **One string per value, not one per representation.** Two things in this build identify
# something by hashing a set of values — ADR-11's `Idempotency-Key` fingerprint, and P7's
# item-registration hash — and both are wrong the moment a Decimal reaches them through
# `str()`: `NUMERIC(20,6)` hands back `2360.000000` where the arithmetic that produced it
# yielded `2360.0000000000`, the two are `==` as numbers and differ as text, and a fingerprint
# over the text calls one amount two.
#
# It lives here rather than beside either caller because it is a **money** rule (rule 6): the
# amounts are `NUMERIC(20,6)` and the rates `NUMERIC(20,10)`, so a value's exponent depends on
# which column it came out of and which multiplication produced it, and nothing about that is
# an identity. P7 step 3 found this the expensive way — `app/fiscal/items.py` grew a second
# canonicaliser rather than using the one ADR-11 had had since P2, and the stock report, which
# reads stored rows on purpose, re-registered every item it touched.


def canonical(value: object) -> str:
    """The canonical string for one value. The `json.dumps(default=…)` hook both hashes use.

    Cosmetic differences must not look like a different request or a different item: `1000` and
    `1000.00` are the same amount and `2026-03-15` the same date, however they were spelled.

    **Strict on purpose.** A type with no canonical form raises rather than falling back to
    `str()`, because a silent fallback is the defect this function exists to prevent — the next
    unserialisable type to reach a fingerprint should stop the build, not be hashed by its
    `repr`.
    """
    if isinstance(value, Decimal):
        return str(value.normalize())
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"no canonical form for {type(value).__name__}")


def fingerprint_material(values: object) -> str:
    """The bytes a fingerprint hashes: JSON, keys sorted, Decimals and dates canonicalised.

    The single place either hash turns values into text, so that `tests/test_fingerprints.py`
    has one thing to point at — and so that a caller cannot reach a hash through a `str()` of
    its own without that test noticing.
    """
    return json.dumps(values, sort_keys=True, separators=(",", ":"), default=canonical)


def resolve_tax_code(db: Session, company_id: int, tax_code_id: int, on_date: date) -> TaxCode:
    """A tax code applies only inside its effective window (§2.3: rates change, history
    must not)."""
    tax_code = db.get(TaxCode, tax_code_id)
    if tax_code is None or tax_code.company_id != company_id:
        raise PostingError(
            f"Tax code {tax_code_id} not found",
            code="tax_code_not_found",
            field_errors={"tax_code_id": ["unknown tax code"]},
        )
    if not tax_code.is_active or not tax_code.is_effective_on(on_date):
        raise PostingError(
            f"Tax code {tax_code.code} is not effective on {on_date.isoformat()}",
            code="tax_code_not_effective",
            field_errors={"tax_code_id": ["not effective on the entry date"]},
        )
    return tax_code
