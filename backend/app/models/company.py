import enum
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.mixins import AuditedMixin, CompanyScopedMixin, pg_enum


class CompanyStatus(enum.StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class Company(AuditedMixin, Base):
    """A tenant. Not itself company-scoped, so it carries no RLS policy; access is
    gated by `company_memberships` (ADR-02) and the operator console."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    tin: Mapped[str | None] = mapped_column(String(20))
    vat_registered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fiscal_country: Mapped[str] = mapped_column(String(2), nullable=False, default="RW")
    address: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    plan_code: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[CompanyStatus] = mapped_column(
        pg_enum(CompanyStatus, "company_status"), nullable=False, default=CompanyStatus.ACTIVE
    )
    # Which chart-of-accounts template the tenant was provisioned from; the accounts
    # themselves are materialised by the P2 ledger kernel.
    coa_template: Mapped[str] = mapped_column(String(50), nullable=False, default="rw_sme_v1")
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspension_reason: Mapped[str | None] = mapped_column(String(500))

    @property
    def is_active(self) -> bool:
        return self.status == CompanyStatus.ACTIVE


class Branch(AuditedMixin, CompanyScopedMixin, Base):
    __tablename__ = "branches"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_branches_company_code"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_main: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    address: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
