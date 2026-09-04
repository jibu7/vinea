import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, func
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


def pg_enum(python_enum: type[enum.Enum], name: str) -> Enum:
    """Native PG enum storing the member *values* (not the Python attribute names)."""
    return Enum(
        python_enum,
        name=name,
        values_callable=lambda e: [member.value for member in e],
    )


class AuditedMixin:
    """`created_at/created_by/updated_at/updated_by` on every table (ADR-09).

    `created_by`/`updated_by` are stamped from the request actor by the session hook in
    `app.db`; they stay NULL for rows created by migrations or unauthenticated signup.
    """

    __audited__ = True

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @declared_attr
    def created_by(cls) -> Mapped[int | None]:  # noqa: N805
        return mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))

    @declared_attr
    def updated_by(cls) -> Mapped[int | None]:  # noqa: N805
        return mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))


class CompanyScopedMixin:
    """Tenant discriminator (ADR-01). Every table carrying it also gets an RLS policy —
    enforced by `tests/test_rls_linter.py`."""

    @declared_attr
    def company_id(cls) -> Mapped[int]:  # noqa: N805
        return mapped_column(
            BigInteger,
            ForeignKey("companies.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
