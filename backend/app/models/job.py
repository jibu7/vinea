"""Background jobs and their artifacts.

Long-running or heavy output (statement PDFs) never blocks a request: the endpoint enqueues
a job and the client polls, then downloads. Artifacts live in the row so a job is atomic with
its tenant — object storage can take over later without changing the API.
"""

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.mixins import AuditedMixin, CompanyScopedMixin, pg_enum


class JobStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


job_status_type = pg_enum(JobStatus, "job_status")


class Job(AuditedMixin, CompanyScopedMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_company_status", "company_id", "status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        job_status_type, nullable=False, default=JobStatus.QUEUED
    )
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    artifact_name: Mapped[str | None] = mapped_column(String(200))
    artifact_content_type: Mapped[str | None] = mapped_column(String(100))
    artifact: Mapped[bytes | None] = mapped_column(LargeBinary)
    requested_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
