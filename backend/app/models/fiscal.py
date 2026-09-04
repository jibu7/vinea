import enum
from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.mixins import AuditedMixin, CompanyScopedMixin, pg_enum


class PeriodStatus(enum.StrEnum):
    FUTURE = "future"
    OPEN = "open"
    CLOSED = "closed"
    LOCKED = "locked"


period_status_type = pg_enum(PeriodStatus, "period_status")


class FiscalYear(AuditedMixin, CompanyScopedMixin, Base):
    """Fiscal calendars are per company (ADR-08). P1 seeds one year of monthly periods;
    the Posting Engine enforces the statuses from P2."""

    __tablename__ = "fiscal_years"
    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_fiscal_years_company_name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[PeriodStatus] = mapped_column(
        period_status_type, nullable=False, default=PeriodStatus.OPEN
    )


class AccountingPeriod(AuditedMixin, CompanyScopedMixin, Base):
    __tablename__ = "accounting_periods"
    __table_args__ = (
        UniqueConstraint(
            "fiscal_year_id", "period_no", name="uq_accounting_periods_fiscal_year_period_no"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    fiscal_year_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fiscal_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[PeriodStatus] = mapped_column(
        period_status_type, nullable=False, default=PeriodStatus.OPEN
    )
