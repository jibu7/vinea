from sqlalchemy import (
    BigInteger,
    Boolean,
    Index,
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
    `companies.base_currency_id` to drift. Rates live in `exchange_rates` from P2."""

    __tablename__ = "currencies"
    __table_args__ = (
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
