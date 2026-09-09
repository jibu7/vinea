"""AR/AP master data (Master Plan §5 P4, §B.2).

One `partners` table serves both roles — the same entity is very often both a customer and
a supplier — with `is_customer` / `is_supplier` flags and separate, independently unique
codes. Everything that differs per role lives in `partner_ar_settings` /
`partner_ap_settings`, which share the column set defined by `_PartnerRoleSettingsMixin`
so the two tables can never drift apart.
"""

import enum
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.db import Base
from app.models.mixins import AuditedMixin, CompanyScopedMixin, pg_enum

MONEY = Numeric(20, 6)
PERCENT = Numeric(20, 10)


class PartnerRole(enum.StrEnum):
    """The discriminator that makes AR and AP one module instead of two."""

    AR = "ar"
    AP = "ap"


class TaxMode(enum.StrEnum):
    EXCLUSIVE = "exclusive"
    INCLUSIVE = "inclusive"


class DueBasis(enum.StrEnum):
    DAYS_FROM_DOCUMENT_DATE = "days_from_document_date"
    DAYS_FROM_END_OF_MONTH = "days_from_end_of_month"
    FIXED_DAY_OF_MONTH = "fixed_day_of_month"


class AgeingBasis(enum.StrEnum):
    DOCUMENT_DATE = "document_date"
    DUE_DATE = "due_date"


partner_role_type = pg_enum(PartnerRole, "partner_role")
tax_mode_type = pg_enum(TaxMode, "tax_mode")


class Partner(AuditedMixin, CompanyScopedMixin, Base):
    __tablename__ = "partners"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_partners_company_id_id"),
        Index(
            "uq_partners_company_customer_code",
            "company_id",
            "customer_code",
            unique=True,
            postgresql_where=text("customer_code IS NOT NULL"),
        ),
        Index(
            "uq_partners_company_supplier_code",
            "company_id",
            "supplier_code",
            unique=True,
            postgresql_where=text("supplier_code IS NOT NULL"),
        ),
        Index("ix_partners_company_name", "company_id", "name"),
        ForeignKeyConstraint(
            ["company_id", "currency_id"],
            ["currencies.company_id", "currencies.id"],
            name="fk_partners_currency",
            ondelete="RESTRICT",
        ),
        CheckConstraint("is_customer OR is_supplier", name="at_least_one_role"),
        CheckConstraint("is_customer = (customer_code IS NOT NULL)", name="customer_code_matches"),
        CheckConstraint("is_supplier = (supplier_code IS NOT NULL)", name="supplier_code_matches"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    customer_code: Mapped[str | None] = mapped_column(String(30))
    supplier_code: Mapped[str | None] = mapped_column(String(30))
    is_customer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_supplier: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tin: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(30))
    address: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(Text)
    # Default document currency; None means the company base currency.
    currency_id: Mapped[int | None] = mapped_column(BigInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def code_for(self, role: PartnerRole) -> str | None:
        return self.customer_code if role == PartnerRole.AR else self.supplier_code

    def has_role(self, role: PartnerRole) -> bool:
        return self.is_customer if role == PartnerRole.AR else self.is_supplier


class _PartnerRoleSettingsMixin:
    """Everything a partner needs to behave as a customer *or* as a supplier. Declared once
    so `partner_ar_settings` and `partner_ap_settings` stay structurally identical."""

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    partner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Overrides `gl_settings.{ar,ap}_control_account_id` for this partner.
    control_account_id: Mapped[int | None] = mapped_column(BigInteger)
    payment_terms_id: Mapped[int | None] = mapped_column(BigInteger)
    # NULL = no limit; 0 = no credit allowed (decision 8).
    credit_limit: Mapped[Decimal | None] = mapped_column(MONEY)
    sales_rep_id: Mapped[int | None] = mapped_column(BigInteger)
    default_tax_code_id: Mapped[int | None] = mapped_column(BigInteger)
    default_branch_id: Mapped[int | None] = mapped_column(BigInteger)
    default_project_id: Mapped[int | None] = mapped_column(BigInteger)
    # Revenue (AR) / expense (AP) account — the 2nd link of the ADR-05 determination chain.
    default_gl_account_id: Mapped[int | None] = mapped_column(BigInteger)

    @declared_attr
    def tax_mode(cls) -> Mapped[TaxMode]:  # noqa: N805
        return mapped_column(tax_mode_type, nullable=False, default=TaxMode.EXCLUSIVE)

    @declared_attr
    def is_on_hold(cls) -> Mapped[bool]:  # noqa: N805
        return mapped_column(Boolean, nullable=False, default=False)


def _role_settings_args(table: str) -> tuple[Any, ...]:
    return (
        UniqueConstraint("company_id", "partner_id", name=f"uq_{table}_company_partner"),
        ForeignKeyConstraint(
            ["company_id", "partner_id"],
            ["partners.company_id", "partners.id"],
            name=f"fk_{table}_partner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "control_account_id"],
            ["gl_accounts.company_id", "gl_accounts.id"],
            name=f"fk_{table}_control_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "default_gl_account_id"],
            ["gl_accounts.company_id", "gl_accounts.id"],
            name=f"fk_{table}_default_gl_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "payment_terms_id"],
            ["payment_terms.company_id", "payment_terms.id"],
            name=f"fk_{table}_payment_terms",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "sales_rep_id"],
            ["sales_reps.company_id", "sales_reps.id"],
            name=f"fk_{table}_sales_rep",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "default_tax_code_id"],
            ["tax_codes.company_id", "tax_codes.id"],
            name=f"fk_{table}_default_tax_code",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "default_branch_id"],
            ["branches.company_id", "branches.id"],
            name=f"fk_{table}_default_branch",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "default_project_id"],
            ["projects.company_id", "projects.id"],
            name=f"fk_{table}_default_project",
            ondelete="RESTRICT",
        ),
        CheckConstraint("credit_limit IS NULL OR credit_limit >= 0", name="credit_limit_positive"),
    )


class PartnerArSettings(_PartnerRoleSettingsMixin, AuditedMixin, CompanyScopedMixin, Base):
    __tablename__ = "partner_ar_settings"
    __table_args__ = _role_settings_args("partner_ar_settings")


class PartnerApSettings(_PartnerRoleSettingsMixin, AuditedMixin, CompanyScopedMixin, Base):
    __tablename__ = "partner_ap_settings"
    __table_args__ = _role_settings_args("partner_ap_settings")


ROLE_SETTINGS: dict[PartnerRole, type[_PartnerRoleSettingsMixin]] = {
    PartnerRole.AR: PartnerArSettings,
    PartnerRole.AP: PartnerApSettings,
}


class PartnerContact(AuditedMixin, CompanyScopedMixin, Base):
    """The thin CRM from §B.2 — contacts and notes on a partner, nothing more."""

    __tablename__ = "partner_contacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "partner_id"],
            ["partners.company_id", "partners.id"],
            name="fk_partner_contacts_partner",
            ondelete="RESTRICT",
        ),
        Index("ix_partner_contacts_company_partner", "company_id", "partner_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    partner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(30))
    notes: Mapped[str | None] = mapped_column(Text)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SalesRep(AuditedMixin, CompanyScopedMixin, Base):
    __tablename__ = "sales_reps"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_sales_reps_company_id_id"),
        UniqueConstraint("company_id", "code", name="uq_sales_reps_company_code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PaymentTerms(AuditedMixin, CompanyScopedMixin, Base):
    """Due-date basis plus the early-settlement discount (§B.2). The discount is *taken at
    allocation time*, never accrued on the invoice."""

    __tablename__ = "payment_terms"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_payment_terms_company_id_id"),
        UniqueConstraint("company_id", "code", name="uq_payment_terms_company_code"),
        CheckConstraint("due_days >= 0", name="due_days_positive"),
        CheckConstraint(
            "discount_percent >= 0 AND discount_percent < 100", name="discount_percent_range"
        ),
        CheckConstraint("discount_days >= 0", name="discount_days_positive"),
        CheckConstraint(
            "(due_basis <> 'fixed_day_of_month') "
            "OR (due_day_of_month BETWEEN 1 AND 31)",
            name="fixed_day_required",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    due_basis: Mapped[DueBasis] = mapped_column(
        pg_enum(DueBasis, "due_basis"), nullable=False, default=DueBasis.DAYS_FROM_DOCUMENT_DATE
    )
    due_days: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    due_day_of_month: Mapped[int | None] = mapped_column(SmallInteger)
    discount_percent: Mapped[Decimal] = mapped_column(PERCENT, nullable=False, default=Decimal(0))
    discount_days: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def due_date(self, document_date: date) -> date:
        from calendar import monthrange
        from datetime import timedelta

        if self.due_basis == DueBasis.DAYS_FROM_DOCUMENT_DATE:
            return document_date + timedelta(days=self.due_days)
        if self.due_basis == DueBasis.DAYS_FROM_END_OF_MONTH:
            last = monthrange(document_date.year, document_date.month)[1]
            return date(document_date.year, document_date.month, last) + timedelta(
                days=self.due_days
            )
        target = self.due_day_of_month or 1
        month_start = document_date.replace(day=1)
        if document_date.day > target:
            month_start = (month_start + timedelta(days=32)).replace(day=1)
        last = monthrange(month_start.year, month_start.month)[1]
        return date(month_start.year, month_start.month, min(target, last))

    def discount_deadline(self, document_date: date) -> date | None:
        from datetime import timedelta

        if self.discount_percent <= 0:
            return None
        return document_date + timedelta(days=self.discount_days)


class AgeingBucketSet(AuditedMixin, CompanyScopedMixin, Base):
    """Configurable ageing buckets per company. The *basis* (document or due date) belongs
    to the set, not to the report call — two sets can age the same ledger differently."""

    __tablename__ = "ageing_bucket_sets"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_ageing_bucket_sets_company_id_id"),
        UniqueConstraint("company_id", "code", name="uq_ageing_bucket_sets_company_code"),
        Index(
            "uq_ageing_bucket_sets_company_default",
            "company_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    basis: Mapped[AgeingBasis] = mapped_column(
        pg_enum(AgeingBasis, "ageing_basis"), nullable=False, default=AgeingBasis.DUE_DATE
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AgeingBucket(AuditedMixin, CompanyScopedMixin, Base):
    """`to_days IS NULL` is the open-ended final bucket (…120+)."""

    __tablename__ = "ageing_buckets"
    __table_args__ = (
        UniqueConstraint("bucket_set_id", "sequence", name="uq_ageing_buckets_set_sequence"),
        ForeignKeyConstraint(
            ["company_id", "bucket_set_id"],
            ["ageing_bucket_sets.company_id", "ageing_bucket_sets.id"],
            name="fk_ageing_buckets_set",
            ondelete="CASCADE",
        ),
        CheckConstraint("to_days IS NULL OR to_days >= from_days", name="bucket_range"),
        Index("ix_ageing_buckets_company_set", "company_id", "bucket_set_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bucket_set_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sequence: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    label: Mapped[str] = mapped_column(String(50), nullable=False)
    from_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    to_days: Mapped[int | None] = mapped_column(SmallInteger)

    def contains(self, days: int) -> bool:
        return days >= self.from_days and (self.to_days is None or days <= self.to_days)
