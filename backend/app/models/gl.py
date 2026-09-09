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


class ControlAccountModule(Base):
    """Which modules may post to a module-owned control account (ADR-05).

    A **registry, not an equality test**: `('ar', 'ar')` is a row, not a rule baked into the
    engine, so P10's POS — which legitimately raises AR — registers `('ar', 'pos')` in its
    own migration instead of weakening the guard. Default is deny: a control type listed here
    at all is reachable only from the modules paired with it. Product-level configuration, so
    it is not company-scoped and carries no RLS policy.
    """

    __tablename__ = "control_account_modules"

    control_type: Mapped[ControlType] = mapped_column(
        pg_enum(ControlType, "gl_control_type"), primary_key=True
    )
    module: Mapped[str] = mapped_column(String(10), primary_key=True)


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


# P4 adds the AR/AP defaults; each is a composite FK back to `gl_accounts`.
SETTINGS_ACCOUNT_FIELDS = (
    "retained_earnings_account_id",
    "rounding_difference_account_id",
    "realized_fx_gain_account_id",
    "realized_fx_loss_account_id",
    "settlement_discount_granted_account_id",
    "settlement_discount_received_account_id",
    "post_dated_receivable_account_id",
    "post_dated_payable_account_id",
    "ar_control_account_id",
    "ap_control_account_id",
)


def _settings_account_fk(field: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["company_id", field],
        ["gl_accounts.company_id", "gl_accounts.id"],
        name=f"fk_gl_settings_{field.removesuffix('_id')}",
        ondelete="RESTRICT",
    )


class GLSettings(AuditedMixin, CompanyScopedMixin, Base):
    """Module defaults for the account-determination chain (ADR-05) — one row per company."""

    __tablename__ = "gl_settings"
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_gl_settings_company_id"),
        *(_settings_account_fk(field) for field in SETTINGS_ACCOUNT_FIELDS),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    retained_earnings_account_id: Mapped[int | None] = mapped_column(BigInteger)
    # Absorbs sub-unit differences left by per-line rounding on multi-currency entries.
    rounding_difference_account_id: Mapped[int | None] = mapped_column(BigInteger)
    # --- P4 AR/AP defaults ---------------------------------------------------------------
    # Realized FX at allocation time; the two may point at the same account.
    realized_fx_gain_account_id: Mapped[int | None] = mapped_column(BigInteger)
    realized_fx_loss_account_id: Mapped[int | None] = mapped_column(BigInteger)
    settlement_discount_granted_account_id: Mapped[int | None] = mapped_column(BigInteger)
    settlement_discount_received_account_id: Mapped[int | None] = mapped_column(BigInteger)
    # Cash that has not landed yet: post-dated cheques and similar instruments (§B.2).
    post_dated_receivable_account_id: Mapped[int | None] = mapped_column(BigInteger)
    post_dated_payable_account_id: Mapped[int | None] = mapped_column(BigInteger)
    ar_control_account_id: Mapped[int | None] = mapped_column(BigInteger)
    ap_control_account_id: Mapped[int | None] = mapped_column(BigInteger)


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
