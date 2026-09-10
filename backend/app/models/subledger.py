"""Partner documents, their lines, and allocations (Master Plan §5 P4).

Six document kinds are a **role × kind matrix**, not six tables: `(ar, invoice)` is a
customer invoice and `(ap, credit_note)` is a return to supplier. `direction` is the sign
the document puts on its control account (+1 debit, −1 credit), which is what makes AR and
AP the same code.

`open_amount` is a stored column written only by the allocation service, in the same spirit
as `period_balances`: derived, cached, and provably equal to a recomputation from
`allocation_lines` (`app.subledger.openitems.verify_open_items`). Any as-of-date query
recomputes instead of reading it.
"""

import enum
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.mixins import AuditedMixin, CompanyScopedMixin, pg_enum
from app.models.partner import PartnerRole, TaxMode, partner_role_type, tax_mode_type

MONEY = Numeric(20, 6)
RATE = Numeric(20, 10)
PERCENT = Numeric(20, 10)


class DocumentKind(enum.StrEnum):
    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"
    # Receipt (AR) / payment (AP) — the money side of the subledger.
    SETTLEMENT = "settlement"


class DocumentStatus(enum.StrEnum):
    POSTED = "posted"
    REVERSED = "reversed"


class InstrumentType(enum.StrEnum):
    CASH = "cash"
    BANK = "bank"
    CHEQUE = "cheque"
    MOBILE = "mobile"
    OTHER = "other"


document_kind_type = pg_enum(DocumentKind, "partner_document_kind")
document_status_type = pg_enum(DocumentStatus, "partner_document_status")
instrument_type_type = pg_enum(InstrumentType, "instrument_type")


class PartnerDocument(AuditedMixin, CompanyScopedMixin, Base):
    __tablename__ = "partner_documents"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_partner_documents_company_id_id"),
        UniqueConstraint("company_id", "number", name="uq_partner_documents_company_number"),
        # Sales/purchase figures key on the transaction type, never on `kind` — see 0011.
        Index(
            "ix_partner_documents_company_transaction_type",
            "company_id",
            "role",
            "transaction_type",
        ),
        ForeignKeyConstraint(
            ["company_id", "partner_id"],
            ["partners.company_id", "partners.id"],
            name="fk_partner_documents_partner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "journal_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_partner_documents_journal_entry",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "reversal_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_partner_documents_reversal_entry",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "matured_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_partner_documents_matured_entry",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "currency_id"],
            ["currencies.company_id", "currencies.id"],
            name="fk_partner_documents_currency",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "branch_id"],
            ["branches.company_id", "branches.id"],
            name="fk_partner_documents_branch",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "project_id"],
            ["projects.company_id", "projects.id"],
            name="fk_partner_documents_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "control_account_id"],
            ["gl_accounts.company_id", "gl_accounts.id"],
            name="fk_partner_documents_control_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "cash_account_id"],
            ["gl_accounts.company_id", "gl_accounts.id"],
            name="fk_partner_documents_cash_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "payment_terms_id"],
            ["payment_terms.company_id", "payment_terms.id"],
            name="fk_partner_documents_payment_terms",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "sales_rep_id"],
            ["sales_reps.company_id", "sales_reps.id"],
            name="fk_partner_documents_sales_rep",
            ondelete="RESTRICT",
        ),
        CheckConstraint("direction IN (-1, 1)", name="direction_sign"),
        CheckConstraint("total_amount > 0", name="total_positive"),
        CheckConstraint(
            "open_amount >= 0 AND open_amount <= total_amount", name="open_amount_range"
        ),
        CheckConstraint("exchange_rate > 0", name="positive_exchange_rate"),
        Index(
            "uq_partner_documents_company_idempotency_key",
            "company_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_partner_documents_company_partner", "company_id", "role", "partner_id"),
        Index("ix_partner_documents_company_date", "company_id", "document_date"),
        Index(
            "ix_partner_documents_open",
            "company_id",
            "role",
            "partner_id",
            postgresql_where=text("open_amount > 0"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[PartnerRole] = mapped_column(partner_role_type, nullable=False)
    kind: Mapped[DocumentKind] = mapped_column(document_kind_type, nullable=False)
    number: Mapped[str] = mapped_column(String(30), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(10), nullable=False)
    #: The `gl_transaction_types` code this document was posted under, within the role's
    #: module. Stored rather than derived from `kind`, because a journal batch posts an
    #: invoice-shaped document under JNL: everything a *user* sees, and every figure keyed on
    #: what kind of business a document represents, reads this and not `kind`.
    transaction_type: Mapped[str] = mapped_column(String(30), nullable=False)
    partner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    journal_entry_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    document_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    currency_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    exchange_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    branch_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    project_id: Mapped[int | None] = mapped_column(BigInteger)
    payment_terms_id: Mapped[int | None] = mapped_column(BigInteger)
    sales_rep_id: Mapped[int | None] = mapped_column(BigInteger)
    tax_mode: Mapped[TaxMode] = mapped_column(tax_mode_type, nullable=False)
    control_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    base_total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    # Derived from `allocation_lines`; written only by the allocation service.
    open_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    direction: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    instrument_type: Mapped[InstrumentType | None] = mapped_column(instrument_type_type)
    maturity_date: Mapped[date | None] = mapped_column(Date)
    cash_account_id: Mapped[int | None] = mapped_column(BigInteger)
    matured_entry_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[DocumentStatus] = mapped_column(document_status_type, nullable=False)
    reversal_entry_id: Mapped[int | None] = mapped_column(BigInteger)
    reversed_on: Mapped[date | None] = mapped_column(Date)
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    idempotency_hash: Mapped[str | None] = mapped_column(String(64))

    lines: Mapped[list["PartnerDocumentLine"]] = relationship(
        back_populates="document", order_by="PartnerDocumentLine.line_no"
    )

    @property
    def is_pending_instrument(self) -> bool:
        """A post-dated instrument whose cash has not landed in the bank yet."""
        return self.maturity_date is not None and self.matured_entry_id is None


class PartnerDocumentLine(AuditedMixin, CompanyScopedMixin, Base):
    """GL/service lines. Item lines arrive with inventory (P5/P6) — the table is shaped to
    take an `item_id` then, and carries nothing item-specific now."""

    __tablename__ = "partner_document_lines"
    __table_args__ = (
        UniqueConstraint("document_id", "line_no", name="uq_partner_document_lines_line_no"),
        ForeignKeyConstraint(
            ["company_id", "document_id"],
            ["partner_documents.company_id", "partner_documents.id"],
            name="fk_partner_document_lines_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "gl_account_id"],
            ["gl_accounts.company_id", "gl_accounts.id"],
            name="fk_partner_document_lines_gl_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "tax_code_id"],
            ["tax_codes.company_id", "tax_codes.id"],
            name="fk_partner_document_lines_tax_code",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "branch_id"],
            ["branches.company_id", "branches.id"],
            name="fk_partner_document_lines_branch",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "project_id"],
            ["projects.company_id", "projects.id"],
            name="fk_partner_document_lines_project",
            ondelete="RESTRICT",
        ),
        Index("ix_partner_document_lines_document", "company_id", "document_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(1))
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(PERCENT, nullable=False, default=Decimal(0))
    gl_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_code_id: Mapped[int | None] = mapped_column(BigInteger)
    branch_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    project_id: Mapped[int | None] = mapped_column(BigInteger)
    net_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    document: Mapped[PartnerDocument] = relationship(back_populates="lines")


class Allocation(AuditedMixin, CompanyScopedMixin, Base):
    """Matches debit documents against credit documents for one partner in one currency.
    Unallocating posts a mirror allocation with negated lines (`reverses_allocation_id`),
    exactly as a journal reversal mirrors an entry — nothing is ever deleted."""

    __tablename__ = "allocations"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_allocations_company_id_id"),
        UniqueConstraint("company_id", "number", name="uq_allocations_company_number"),
        ForeignKeyConstraint(
            ["company_id", "partner_id"],
            ["partners.company_id", "partners.id"],
            name="fk_allocations_partner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "currency_id"],
            ["currencies.company_id", "currencies.id"],
            name="fk_allocations_currency",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "journal_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_allocations_journal_entry",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "reverses_allocation_id"],
            ["allocations.company_id", "allocations.id"],
            name="fk_allocations_reverses_allocation",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_allocations_reverses_allocation_id",
            "reverses_allocation_id",
            unique=True,
            postgresql_where=text("reverses_allocation_id IS NOT NULL"),
        ),
        Index(
            "uq_allocations_company_idempotency_key",
            "company_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_allocations_company_partner", "company_id", "role", "partner_id"),
        Index("ix_allocations_company_date", "company_id", "allocation_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), nullable=False)
    role: Mapped[PartnerRole] = mapped_column(partner_role_type, nullable=False)
    partner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    allocation_date: Mapped[date] = mapped_column(Date, nullable=False)
    # NULL when the allocation needed no GL movement (same rate, no discount).
    journal_entry_id: Mapped[int | None] = mapped_column(BigInteger)
    reverses_allocation_id: Mapped[int | None] = mapped_column(BigInteger)
    description: Mapped[str | None] = mapped_column(String(500))
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    idempotency_hash: Mapped[str | None] = mapped_column(String(64))

    lines: Mapped[list["AllocationLine"]] = relationship(
        back_populates="allocation", order_by="AllocationLine.line_no"
    )


class AllocationLine(AuditedMixin, CompanyScopedMixin, Base):
    """One debit document matched against one credit document. `amount` is negative on a
    reversing allocation, so open items are `total − Σ amount` with no status filtering."""

    __tablename__ = "allocation_lines"
    __table_args__ = (
        UniqueConstraint("allocation_id", "line_no", name="uq_allocation_lines_line_no"),
        ForeignKeyConstraint(
            ["company_id", "allocation_id"],
            ["allocations.company_id", "allocations.id"],
            name="fk_allocation_lines_allocation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "debit_document_id"],
            ["partner_documents.company_id", "partner_documents.id"],
            name="fk_allocation_lines_debit_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "credit_document_id"],
            ["partner_documents.company_id", "partner_documents.id"],
            name="fk_allocation_lines_credit_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "discount_document_id"],
            ["partner_documents.company_id", "partner_documents.id"],
            name="fk_allocation_lines_discount_document",
            ondelete="RESTRICT",
        ),
        CheckConstraint("amount <> 0", name="amount_not_zero"),
        CheckConstraint(
            "(discount_amount = 0) = (discount_document_id IS NULL)",
            name="discount_document_with_amount",
        ),
        CheckConstraint(
            "debit_document_id <> credit_document_id", name="distinct_documents"
        ),
        Index("ix_allocation_lines_debit", "company_id", "debit_document_id"),
        Index("ix_allocation_lines_credit", "company_id", "credit_document_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    allocation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    debit_document_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    credit_document_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))
    discount_document_id: Mapped[int | None] = mapped_column(BigInteger)
    # Realized FX for this pair, in base currency (debit-side rate minus credit-side rate).
    fx_base_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))

    allocation: Mapped[Allocation] = relationship(back_populates="lines")
