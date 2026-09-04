import enum
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    ForeignKeyConstraint,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.mixins import AuditedMixin, CompanyScopedMixin, pg_enum


class TaxNature(enum.StrEnum):
    """VAT convention per Appendix C.1: output VAT is charged on *sales*, input VAT is
    paid on *purchases* (the client menu spec had the two labels swapped)."""

    OUTPUT = "output"
    INPUT = "input"
    EXEMPT = "exempt"
    ZERO_RATED = "zero_rated"


class TaxCode(AuditedMixin, CompanyScopedMixin, Base):
    """Tax codes carry rate, nature, GL account and an effective-date window (§2.3).
    `gl_account_id` is where the tax itself posts (VAT output → payable, input →
    receivable); exempt / zero-rated codes have none."""

    __tablename__ = "tax_codes"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_tax_codes_company_id_id"),
        UniqueConstraint("company_id", "code", name="uq_tax_codes_company_code"),
        ForeignKeyConstraint(
            ["company_id", "gl_account_id"],
            ["gl_accounts.company_id", "gl_accounts.id"],
            name="fk_tax_codes_gl_account",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    nature: Mapped[TaxNature] = mapped_column(pg_enum(TaxNature, "tax_nature"), nullable=False)
    rate_pct: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    gl_account_id: Mapped[int | None] = mapped_column(BigInteger)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def is_effective_on(self, on_date: date) -> bool:
        return self.valid_from <= on_date and (self.valid_to is None or on_date <= self.valid_to)
