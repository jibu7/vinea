"""Job runner. Handlers register themselves by kind; `run_job` executes one in its own
session and tenant context so a background thread never borrows the request's session."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db import SessionLocal, set_actor, set_tenant
from app.models.job import Job, JobStatus
from app.models.user import User

Handler = Callable[[Session, Job], None]
_HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def register(func: Handler) -> Handler:
        _HANDLERS[kind] = func
        return func

    return register


def enqueue(
    db: Session, company_id: int, kind: str, params: dict[str, Any], *, actor: User
) -> Job:
    if kind not in _HANDLERS:
        raise NotFoundError(f"Unknown job kind {kind}")
    job = Job(
        company_id=company_id,
        kind=kind,
        status=JobStatus.QUEUED,
        params=params,
        requested_by=actor.id,
    )
    db.add(job)
    db.flush()
    return job


def get_job(db: Session, company_id: int, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.company_id != company_id:
        raise NotFoundError("Job not found")
    return job


def run_job(job_id: int, company_id: int, actor_user_id: int | None) -> None:
    """Executed out of band. Failures are recorded on the job, never raised into the void."""
    db = SessionLocal()
    try:
        set_actor(db, actor_user_id)
        set_tenant(db, company_id)
        job = db.get(Job, job_id)
        if job is None or job.status != JobStatus.QUEUED:
            return
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(UTC)
        db.commit()
        try:
            _HANDLERS[job.kind](db, job)
            job.status = JobStatus.SUCCEEDED
        except Exception as exc:  # noqa: BLE001 - the failure belongs on the job row
            db.rollback()
            job = db.get(Job, job_id)
            assert job is not None
            job.status = JobStatus.FAILED
            job.error = f"{type(exc).__name__}: {exc}"[:2000]
        job.finished_at = datetime.now(UTC)
        db.commit()
    finally:
        db.close()
