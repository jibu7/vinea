from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.mixins import AuditedMixin, CompanyScopedMixin


class Currency(AuditedMixin, CompanyScopedMixin, Base):
    """Per-company currency list. The base currency is the single row with `is_base`
    (a partial unique index enforces "exactly one"), so there is no mutable
    `companies.base_currency_id` to drift. Rates live in `exchange_rates`."""

    __tablename__ = "currencies"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_currencies_company_id_id"),
        UniqueConstraint("company_id", "code", name="uq_currencies_company_code"),
        Index(
            "uq_currencies_company_base",
            "company_id",
            unique=True,
            postgresql_where=text("is_base"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(3), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(10))
    decimal_places: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)
    is_base: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ExchangeRate(AuditedMixin, CompanyScopedMixin, Base):
    """Dated rates (ADR-06): `rate` = base-currency units per **one** unit of `currency`
    (e.g. 1 USD = 1300 RWF → rate 1300). The rate effective on a date is the row with
    the greatest `valid_from <= date`. Never a single mutable column."""

    __tablename__ = "exchange_rates"
    __table_args__ = (
        UniqueConstraint("currency_id", "valid_from", name="uq_exchange_rates_currency_valid_from"),
        ForeignKeyConstraint(
            ["company_id", "currency_id"],
            ["currencies.company_id", "currencies.id"],
            name="fk_exchange_rates_currency",
            ondelete="RESTRICT",
        ),
        CheckConstraint("rate > 0", name="positive_rate"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    currency_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
