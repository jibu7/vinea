"""Job runner.

There was no job module before this one — P0 provisioned `redis` and `minio` in
`docker-compose.yml` and `settings.redis_url` exists, but nothing in `app/` ever imported a
queue, a worker or an object-storage client (ADR-10 was scheduled, not built). This is that
module, deliberately minimal: handlers register by kind, and `run_job` executes one in its
**own** session and tenant context so a background task never borrows the request's session.

Artifacts live in the row for now. Three rules keep that honest until object storage lands:
retention (`expires_at`), a bytes column that is `deferred` so no listing or status poll can
select it, and `sweep()` — which fails jobs abandoned mid-flight by a process restart and
deletes expired ones.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db import SessionLocal, set_actor, set_tenant
from app.models.job import Job, JobStatus
from app.models.user import User

Handler = Callable[[Session, Job], None]
_HANDLERS: dict[str, Handler] = {}

# How long a finished job (and its artifact) is downloadable before the reaper deletes it.
RETENTION = timedelta(days=7)
# A job still `running` after this has lost its process; nothing will ever finish it.
LEASE = timedelta(minutes=30)
ABANDONED_ERROR = "job_abandoned: the worker process did not finish this job"


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
    sweep(db, company_id)
    job = Job(
        company_id=company_id,
        kind=kind,
        status=JobStatus.QUEUED,
        params=params,
        requested_by=actor.id,
        expires_at=datetime.now(UTC) + RETENTION,
    )
    db.add(job)
    db.flush()
    return job


def get_job(db: Session, company_id: int, job_id: int) -> Job:
    """Never loads the artifact — `Job.artifact` is deferred."""
    job = db.get(Job, job_id)
    if job is None or job.company_id != company_id:
        raise NotFoundError("Job not found")
    return job


def list_jobs(
    db: Session, company_id: int, *, kind: str | None = None, limit: int = 50
) -> list[Job]:
    statement = select(Job).where(Job.company_id == company_id)
    if kind is not None:
        statement = statement.where(Job.kind == kind)
    return list(db.scalars(statement.order_by(Job.id.desc()).limit(limit)))


def load_artifact(db: Session, company_id: int, job_id: int) -> bytes | None:
    """The only read of the bytes column. RLS scopes it to the session's tenant; the explicit
    `company_id` predicate makes that intent visible in the query too."""
    return db.scalar(select(Job.artifact).where(Job.company_id == company_id, Job.id == job_id))


def sweep(db: Session, company_id: int, *, now: datetime | None = None) -> tuple[int, int]:
    """Fail abandoned jobs, delete expired ones. Returns (abandoned, deleted).

    Called on every enqueue (bounded, indexed) and exposed as an endpoint so a scheduler can
    drive it for tenants that never queue anything again.
    """
    moment = now or datetime.now(UTC)
    abandoned = db.execute(
        update(Job)
        .where(
            Job.company_id == company_id,
            Job.status == JobStatus.RUNNING,
            Job.started_at < moment - LEASE,
        )
        .values(status=JobStatus.FAILED, error=ABANDONED_ERROR, finished_at=moment)
    ).rowcount
    deleted = db.execute(
        delete(Job).where(
            Job.company_id == company_id,
            Job.expires_at.is_not(None),
            Job.expires_at < moment,
            Job.status.in_([JobStatus.SUCCEEDED, JobStatus.FAILED]),
        )
    ).rowcount
    return abandoned, deleted


def run_job(job_id: int, company_id: int, actor_user_id: int | None) -> None:
    """Executed out of band, after the response has been returned.

    Runs on a **fresh** `SessionLocal()`: `set_actor` supplies the requesting user's id for
    the ADR-09 `created_by`/`updated_by` stamps, and `set_tenant` sets the `app.company_id`
    GUC the `tenant_isolation` policies read. `app.platform_mode` stays `off`, so the task is
    bound to exactly one tenant. Failures are recorded on the job, never raised into the void.
    """
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
            job.artifact_size = len(job.artifact) if job.artifact is not None else None
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
