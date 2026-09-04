import enum
from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, Numeric, String, UniqueConstraint
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
    """Tax codes are seeded per company in P1; the P2 kernel adds the GL account link
    and the posting-time tax calculation."""

    __tablename__ = "tax_codes"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_tax_codes_company_code"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    nature: Mapped[TaxNature] = mapped_column(pg_enum(TaxNature, "tax_nature"), nullable=False)
    rate_pct: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
