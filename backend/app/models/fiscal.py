import enum
from datetime import date

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    SmallInteger,
    String,
    UniqueConstraint,
    literal_column,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
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
    """Fiscal calendars are per company (ADR-08). A year is `open` while trading and
    `locked` once `close_fiscal_year` has posted `closing_entry_id`; reopening reverses
    that entry (see `app.kernel.periods`)."""

    __tablename__ = "fiscal_years"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_fiscal_years_company_name"),
        ForeignKeyConstraint(
            ["company_id", "closing_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_fiscal_years_closing_entry",
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[PeriodStatus] = mapped_column(
        period_status_type, nullable=False, default=PeriodStatus.OPEN
    )
    closing_entry_id: Mapped[int | None] = mapped_column(BigInteger)


class AccountingPeriod(AuditedMixin, CompanyScopedMixin, Base):
    __tablename__ = "accounting_periods"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_accounting_periods_company_id_id"),
        UniqueConstraint(
            "fiscal_year_id", "period_no", name="uq_accounting_periods_fiscal_year_period_no"
        ),
        # Posting resolves a period from the entry date alone, so overlap would make that
        # lookup ambiguous — the database refuses it outright (needs btree_gist).
        ExcludeConstraint(
            (literal_column("company_id"), "="),
            (literal_column("daterange(start_date, end_date, '[]')"), "&&"),
            name="ex_accounting_periods_no_overlap",
            using="gist",
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
