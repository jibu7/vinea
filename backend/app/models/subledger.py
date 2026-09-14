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
            ["company_id", "stock_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_partner_documents_stock_entry",
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
    #: The **companion stock entry** (P6 decision 2). A document carrying valued stock lines
    #: posts two entries in one transaction — the partner side through `ar`/`ap`, and this one
    #: through `inv` via the P5 primitives, which posts first so its value is known to the
    #: partner side. `NULL` when the document moved no stock, which is every P4 document and
    #: every P6 document whose lines are all service or non-stock: no stock line, no companion,
    #: and no number claimed from the `STK` run.
    stock_entry_id: Mapped[int | None] = mapped_column(BigInteger)
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
    """One line of a partner document — a GL line, or an **item line** (P6 decision 1).

    Both shapes live in this one table and one service handles both: a document may carry a GL
    line and an item line at once, which is what an invoice with a delivery charge on it looks
    like. What makes a line an item line is `item_id`; everything else here is nullable and
    describes how that item was sold or bought.

    Only a **stock** item moves stock. A service or non-stock item line is an ordinary line
    that happens to carry an item dimension, which is what lets P10 report on it.

    `quantity` stays the quantity as keyed, in `uom_id`; `base_quantity` is the same amount
    converted to the item's base unit and is what every derived figure counts — committed, on
    order, received, matched. Keeping both means a line keyed in cases still reads as cases on
    the screen it was keyed on, while the arithmetic never has to know about packs.

    The four link columns each point at the row this line answers to, and each is the join a
    derived quantity is computed over rather than a running total anybody maintains:
    `order_line_id` the SO line (AR) or PO line (AP) being fulfilled, `grn_line_id` the GRN
    line this invoice line matches, `returns_line_id` the invoice line a credit note returns —
    which is how a return is valued at the cost that was actually issued rather than today's
    average — and `kit_parent_line_id` the kit line this component was exploded from.
    """

    __tablename__ = "partner_document_lines"
    __table_args__ = (
        UniqueConstraint("document_id", "line_no", name="uq_partner_document_lines_line_no"),
        # The composite-FK target the self-references below need. Every other tenant table
        # carries one; this table had no reason to until a line could point at another line.
        UniqueConstraint("company_id", "id", name="uq_partner_document_lines_company_id_id"),
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
        ForeignKeyConstraint(
            ["company_id", "item_id"],
            ["items.company_id", "items.id"],
            name="fk_partner_document_lines_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "uom_id"],
            ["uoms.company_id", "uoms.id"],
            name="fk_partner_document_lines_uom",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "warehouse_id"],
            ["warehouses.company_id", "warehouses.id"],
            name="fk_partner_document_lines_warehouse",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "grn_line_id"],
            ["goods_received_note_lines.company_id", "goods_received_note_lines.id"],
            name="fk_partner_document_lines_grn_line",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "returns_line_id"],
            ["partner_document_lines.company_id", "partner_document_lines.id"],
            name="fk_partner_document_lines_returns_line",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "kit_parent_line_id"],
            ["partner_document_lines.company_id", "partner_document_lines.id"],
            name="fk_partner_document_lines_kit_parent_line",
            ondelete="CASCADE",
        ),
        Index("ix_partner_document_lines_document", "company_id", "document_id"),
        # The derived quantities of decision 4 are these three joins and nothing else, so each
        # gets the partial index that makes them a lookup rather than a scan of every line
        # ever posted. Partial because an item line is a minority of lines on most documents
        # and a linked one rarer still.
        Index(
            "ix_partner_document_lines_order_line",
            "company_id",
            "order_line_id",
            postgresql_where=text("order_line_id IS NOT NULL"),
        ),
        Index(
            "ix_partner_document_lines_grn_line",
            "company_id",
            "grn_line_id",
            postgresql_where=text("grn_line_id IS NOT NULL"),
        ),
        Index(
            "ix_partner_document_lines_item",
            "company_id",
            "item_id",
            postgresql_where=text("item_id IS NOT NULL"),
        ),
        CheckConstraint(
            "base_quantity IS NULL OR item_id IS NOT NULL",
            name="base_quantity_needs_an_item",
        ),
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
    # --- P6 item line (decision 1); all nullable, a GL line carries none of them ----------
    item_id: Mapped[int | None] = mapped_column(BigInteger)
    uom_id: Mapped[int | None] = mapped_column(BigInteger)
    #: `quantity` converted to the item's base unit. Every derived figure counts this.
    base_quantity: Mapped[Decimal | None] = mapped_column(MONEY)
    warehouse_id: Mapped[int | None] = mapped_column(BigInteger)
    #: The SO line (AR) or PO line (AP) this line fulfils. No foreign key yet — the order
    #: tables arrive with the order service, and the constraint arrives with them.
    order_line_id: Mapped[int | None] = mapped_column(BigInteger)
    #: The GRN line this supplier-invoice line matches — the join the relieved value and
    #: the matched quantity are both computed over.
    grn_line_id: Mapped[int | None] = mapped_column(BigInteger)
    #: The invoice line this credit-note line returns, so the return is valued at the cost
    #: that was issued rather than at today's average.
    returns_line_id: Mapped[int | None] = mapped_column(BigInteger)
    #: Set on a component line, pointing at the kit line it was exploded from.
    kit_parent_line_id: Mapped[int | None] = mapped_column(BigInteger)
    #: What a matched line took off the GRN accrual, in base currency. Set only when
    #: `grn_line_id` is, and immutable once posted — an arithmetic fact of this posting in the
    #: same sense `net_amount` is, not a total anybody maintains.
    #:
    #: **Stored rather than derived, deliberately.** A pro-rata share cannot be recomputed
    #: once a sibling has been reversed: value 1000 received over quantity 3 and matched
    #: 1 + 1 + 1 relieves 333 + 333 + 334, and reversing the first leaves the ledger having
    #: relieved 667 where a recomputation over the survivors says 666. The accrual proof would
    #: be off by a franc and would stay off.
    accrual_relieved: Mapped[Decimal | None] = mapped_column(MONEY)

    document: Mapped[PartnerDocument] = relationship(back_populates="lines")

    @property
    def is_item_line(self) -> bool:
        return self.item_id is not None


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
