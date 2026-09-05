"""The immutable journal (ADR-04) and its verifiable `period_balances` cache.

Amounts are **signed**: positive = debit, negative = credit. `SUM(base_amount) = 0` per
entry is asserted by a deferred constraint trigger; UPDATE/DELETE of posted rows is
blocked by trigger (migration 0002). Nothing here is enforced "by convention".
"""

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.mixins import AuditedMixin, CompanyScopedMixin, pg_enum

MONEY = Numeric(20, 6)
RATE = Numeric(20, 10)


class JournalStatus(enum.StrEnum):
    DRAFT = "draft"
    POSTED = "posted"


class JournalEntry(AuditedMixin, CompanyScopedMixin, Base):
    __tablename__ = "journal_entries"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_journal_entries_company_id_id"),
        UniqueConstraint("company_id", "number", name="uq_journal_entries_company_number"),
        ForeignKeyConstraint(
            ["company_id", "period_id"],
            ["accounting_periods.company_id", "accounting_periods.id"],
            name="fk_journal_entries_period",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "reverses_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_journal_entries_reverses_entry",
            ondelete="RESTRICT",
        ),
        # An entry can be reversed at most once; "is reversed" is derived, never stored.
        Index(
            "uq_journal_entries_reverses_entry_id",
            "reverses_entry_id",
            unique=True,
            postgresql_where=text("reverses_entry_id IS NOT NULL"),
        ),
        Index(
            "uq_journal_entries_company_idempotency_key",
            "company_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_journal_entries_company_entry_date", "company_id", "entry_date"),
        Index("ix_journal_entries_company_period", "company_id", "period_id"),
        Index(
            "ix_journal_entries_company_source",
            "company_id",
            "source_doc_type",
            "source_doc_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(10), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    source_doc_type: Mapped[str | None] = mapped_column(String(50))
    source_doc_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[JournalStatus] = mapped_column(
        pg_enum(JournalStatus, "journal_status"), nullable=False
    )
    posted_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reverses_entry_id: Mapped[int | None] = mapped_column(BigInteger)
    reversal_reason: Mapped[str | None] = mapped_column(String(500))
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    # Fingerprint of the request that produced this entry; replaying a key with a different
    # payload is a client bug, not a retry.
    idempotency_hash: Mapped[str | None] = mapped_column(String(64))

    lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="entry", order_by="JournalLine.line_no"
    )


class JournalLine(AuditedMixin, CompanyScopedMixin, Base):
    __tablename__ = "journal_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_journal_lines_entry",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "gl_account_id"],
            ["gl_accounts.company_id", "gl_accounts.id"],
            name="fk_journal_lines_gl_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "branch_id"],
            ["branches.company_id", "branches.id"],
            name="fk_journal_lines_branch",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "project_id"],
            ["projects.company_id", "projects.id"],
            name="fk_journal_lines_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "currency_id"],
            ["currencies.company_id", "currencies.id"],
            name="fk_journal_lines_currency",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "tax_code_id"],
            ["tax_codes.company_id", "tax_codes.id"],
            name="fk_journal_lines_tax_code",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("entry_id", "line_no", name="uq_journal_lines_entry_line_no"),
        CheckConstraint("exchange_rate > 0", name="positive_exchange_rate"),
        CheckConstraint(
            "(partner_type IS NULL) = (partner_id IS NULL)", name="partner_type_with_id"
        ),
        Index("ix_journal_lines_company_account", "company_id", "gl_account_id"),
        Index("ix_journal_lines_company_branch", "company_id", "branch_id"),
        Index("ix_journal_lines_company_project", "company_id", "project_id"),
        Index("ix_journal_lines_company_partner", "company_id", "partner_type", "partner_id"),
        Index("ix_journal_lines_company_tax_code", "company_id", "tax_code_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entry_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    gl_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    branch_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    project_id: Mapped[int | None] = mapped_column(BigInteger)
    partner_type: Mapped[str | None] = mapped_column(String(20))
    partner_id: Mapped[int | None] = mapped_column(BigInteger)
    item_id: Mapped[int | None] = mapped_column(BigInteger)
    currency_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    exchange_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    base_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tax_code_id: Mapped[int | None] = mapped_column(BigInteger)
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))
    # Set only by the Posting Engine, on the line it appends to absorb per-line FX rounding.
    is_rounding_line: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    description: Mapped[str | None] = mapped_column(String(500))
    source_doc_type: Mapped[str | None] = mapped_column(String(50))
    source_doc_id: Mapped[int | None] = mapped_column(BigInteger)
    source_line_id: Mapped[int | None] = mapped_column(BigInteger)

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")


class PeriodBalance(AuditedMixin, CompanyScopedMixin, Base):
    """Derived summary (account × period × branch × currency). Maintained in the posting
    transaction and re-derivable from `journal_lines` — `verify_period_balances` proves
    it (ADR-04). It is a cache, not a source of truth."""

    __tablename__ = "period_balances"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "period_id",
            "gl_account_id",
            "branch_id",
            "currency_id",
            name="uq_period_balances_cell",
        ),
        ForeignKeyConstraint(
            ["company_id", "period_id"],
            ["accounting_periods.company_id", "accounting_periods.id"],
            name="fk_period_balances_period",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "gl_account_id"],
            ["gl_accounts.company_id", "gl_accounts.id"],
            name="fk_period_balances_gl_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "branch_id"],
            ["branches.company_id", "branches.id"],
            name="fk_period_balances_branch",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "currency_id"],
            ["currencies.company_id", "currencies.id"],
            name="fk_period_balances_currency",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    period_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gl_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    branch_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    debit_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    credit_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    debit_base: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    credit_base: Mapped[Decimal] = mapped_column(MONEY, nullable=False)


class DocumentSequence(AuditedMixin, CompanyScopedMixin, Base):
    """Gapless numbering (ADR-07). Claimed with `SELECT … FOR UPDATE` inside the posting
    transaction, so a rolled-back posting never consumes a number."""

    __tablename__ = "document_sequences"
    __table_args__ = (
        # NULLS NOT DISTINCT makes the company-wide (branch_id IS NULL) row unique too,
        # which is what lets claim_number() upsert lazily under concurrency.
        UniqueConstraint(
            "company_id",
            "doc_type",
            "branch_id",
            name="uq_document_sequences_scope",
            postgresql_nulls_not_distinct=True,
        ),
        ForeignKeyConstraint(
            ["company_id", "branch_id"],
            ["branches.company_id", "branches.id"],
            name="fk_document_sequences_branch",
            ondelete="RESTRICT",
        ),
        CheckConstraint("next_number >= 1", name="next_number_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    branch_id: Mapped[int | None] = mapped_column(BigInteger)
    doc_type: Mapped[str] = mapped_column(String(10), nullable=False)
    prefix: Mapped[str] = mapped_column(String(10), nullable=False)
    next_number: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
