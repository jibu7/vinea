"""Chart of accounts, per-company GL settings and the projects master (Master Plan §4).

Tenant consistency is declarative: every cross-table reference inside a tenant is a
composite foreign key `(company_id, x_id) → (company_id, id)`, so a row can never point
at another tenant's account, branch or period even though FK checks bypass RLS.
"""

import enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.mixins import AuditedMixin, CompanyScopedMixin, pg_enum


class AccountClass(enum.StrEnum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"


PROFIT_AND_LOSS_CLASSES = frozenset({AccountClass.INCOME, AccountClass.EXPENSE})


class ControlType(enum.StrEnum):
    """Accounts owned by a module. Manual journals may not post to them; the owning
    module (cashbook for bank/cash, AR/AP/Inventory subledgers from P4/P5) does."""

    BANK = "bank"
    CASH = "cash"
    AR = "ar"
    AP = "ap"
    INVENTORY = "inventory"


CASHBOOK_CONTROL_TYPES = frozenset({ControlType.BANK, ControlType.CASH})


class GLAccount(AuditedMixin, CompanyScopedMixin, Base):
    __tablename__ = "gl_accounts"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_gl_accounts_company_id_id"),
        UniqueConstraint("company_id", "code", name="uq_gl_accounts_company_code"),
        ForeignKeyConstraint(
            ["company_id", "parent_id"],
            ["gl_accounts.company_id", "gl_accounts.id"],
            name="fk_gl_accounts_parent",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "is_control = (control_type IS NOT NULL)", name="control_type_matches_flag"
        ),
        CheckConstraint("NOT (is_control AND NOT is_postable)", name="control_is_postable"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    class_: Mapped[AccountClass] = mapped_column(
        "class", pg_enum(AccountClass, "account_class"), nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    is_postable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_control: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    control_type: Mapped[ControlType | None] = mapped_column(
        pg_enum(ControlType, "gl_control_type")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    @property
    def is_profit_and_loss(self) -> bool:
        return self.class_ in PROFIT_AND_LOSS_CLASSES


class GLSettings(AuditedMixin, CompanyScopedMixin, Base):
    """Module defaults for the account-determination chain (ADR-05) — one row per company."""

    __tablename__ = "gl_settings"
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_gl_settings_company_id"),
        ForeignKeyConstraint(
            ["company_id", "retained_earnings_account_id"],
            ["gl_accounts.company_id", "gl_accounts.id"],
            name="fk_gl_settings_retained_earnings_account",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    retained_earnings_account_id: Mapped[int | None] = mapped_column(BigInteger)


class Project(AuditedMixin, CompanyScopedMixin, Base):
    """Job/project costing dimension (D7/D8). Master only in P2; UI arrives in P3."""

    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_projects_company_id_id"),
        UniqueConstraint("company_id", "code", name="uq_projects_company_code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class GLTransactionType(AuditedMixin, CompanyScopedMixin, Base):
    """The 4th link of the account-determination chain (ADR-05): a named transaction type
    with a default contra account. One table serves every module (`module` discriminator),
    so AR/AP/Inventory/OE types in later phases are rows, not new tables."""

    __tablename__ = "gl_transaction_types"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "module", "code", name="uq_gl_transaction_types_company_module_code"
        ),
        ForeignKeyConstraint(
            ["company_id", "default_gl_account_id"],
            ["gl_accounts.company_id", "gl_accounts.id"],
            name="fk_gl_transaction_types_default_gl_account",
            ondelete="RESTRICT",
        ),
        # `__`-prefixed keys are reserved for kernel sentinels (e.g. the year-end close).
        CheckConstraint("code NOT LIKE '\\_\\_%'", name="code_not_reserved"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    module: Mapped[str] = mapped_column(String(10), nullable=False, default="gl")
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    default_gl_account_id: Mapped[int | None] = mapped_column(BigInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
