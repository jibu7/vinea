from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.mixins import CompanyScopedMixin


class AuditLog(CompanyScopedMixin, Base):
    """Master-data changes and sensitive actions (ADR-09). Rows are written once, so the
    generic `updated_*` audit columns do not apply here — `at` is the event time."""

    __tablename__ = "audit_log"
    #: Issue #54. Every reader filters `company_id` **and** `entity` **and** `entity_id`, so an
    #: index on the tenant alone made a history read scan the tenant's whole audit trail and
    #: throw away the rows belonging to other entities — `O(tenant's history)` for a result
    #: that is `O(one entity's history)`.
    #:
    #: `at` and `id` ride on the end for the `ORDER BY at DESC, id DESC` that #20 pinned. They
    #: do not buy the ordering by themselves: `(company_id, at, id)` was measured and changed
    #: nothing, because the scan was the cost and not the sort.
    #:
    #: This **replaces** `ix_audit_log_company_at` (revision `0028_audit_entity_index`) rather
    #: than joining it, which is only safe while nothing reads this table by company and time
    #: alone. `tests/test_audit_index.py` is what keeps that true.
    __table_args__ = (
        Index(
            "ix_audit_log_company_entity_at",
            "company_id",
            "entity",
            "entity_id",
            "at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    actor_email: Mapped[str | None] = mapped_column(String(320))
    impersonated_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(255))
