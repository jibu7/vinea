"""Job infrastructure: tenant isolation on the artifact bytes, retention, and the reaper.

`jobs` holds customer statement PDFs, so cross-tenant access is a data-breach class of bug,
not a correctness nicety — these tests hit it from both the service layer (RLS on the
non-superuser app role) and the HTTP layer.
"""

import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db import set_actor, set_tenant
from app.models.job import Job, JobStatus
from app.services import jobs as jobs_service
from app.subledger.statements import STATEMENT_JOB
from tests.conftest import make_tenant

PASSWORD = "correct horse battery staple"


def _queue(db: Session, company_id: int, user) -> Job:  # noqa: ANN001
    set_actor(db, user.id)
    set_tenant(db, company_id)
    job = jobs_service.enqueue(
        db,
        company_id,
        STATEMENT_JOB,
        {"role": "ar", "partner_ids": [], "as_of": "2026-03-10", "variant": "open_item"},
        actor=user,
    )
    job.status = JobStatus.SUCCEEDED
    job.artifact = b"%PDF-1.7 secret statement"
    job.artifact_size = len(job.artifact)
    job.artifact_name = "statement.pdf"
    job.artifact_content_type = "application/pdf"
    db.commit()
    return job


def test_a_job_is_invisible_to_another_tenant(db: Session, two_tenants) -> None:  # noqa: ANN001
    first, second = two_tenants
    job = _queue(db, first.company.id, first.user)

    set_tenant(db, second.company.id)
    assert db.scalars(select(Job)).all() == []
    assert jobs_service.load_artifact(db, second.company.id, job.id) is None
    with pytest.raises(NotFoundError):
        jobs_service.get_job(db, second.company.id, job.id)

    set_tenant(db, first.company.id)
    assert jobs_service.load_artifact(db, first.company.id, job.id) == b"%PDF-1.7 secret statement"


def test_rls_blocks_a_forged_company_id_on_the_job(db: Session, two_tenants) -> None:  # noqa: ANN001
    """WITH CHECK is what stops tenant B writing a row into tenant A."""
    first, second = two_tenants
    set_tenant(db, second.company.id)
    with pytest.raises(DBAPIError):
        db.execute(
            text(
                "INSERT INTO jobs (company_id, kind, status) "
                "VALUES (:cid, 'partner_statement', 'queued')"
            ),
            {"cid": first.company.id},
        )
    db.rollback()


def test_cross_tenant_download_is_refused_over_http(
    client: TestClient, db: Session, two_tenants
) -> None:  # noqa: ANN001
    first, second = two_tenants
    job = _queue(db, first.company.id, first.user)

    login = client.post(
        "/api/v1/auth/login", json={"email": second.user.email, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    assert client.get(f"/api/v1/subledger/jobs/{job.id}").status_code == 404
    assert client.get(f"/api/v1/subledger/jobs/{job.id}/artifact").status_code == 404

    client.post("/api/v1/auth/logout")
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": first.user.email, "password": PASSWORD}
        ).status_code
        == 200
    )
    owned = client.get(f"/api/v1/subledger/jobs/{job.id}/artifact")
    assert owned.status_code == 200 and owned.content.startswith(b"%PDF")


def test_listings_never_select_the_artifact_bytes(db: Session, two_tenants) -> None:  # noqa: ANN001
    """`Job.artifact` is deferred, so a status poll or a listing can never drag a statement
    PDF out of the database. Proven by watching the SQL, not by reading the mapper."""
    first, _ = two_tenants
    job = _queue(db, first.company.id, first.user)
    db.expire_all()

    statements: list[str] = []
    engine = db.get_bind()

    def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ANN202
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        listed = jobs_service.list_jobs(db, first.company.id)
        fetched = jobs_service.get_job(db, first.company.id, job.id)
        assert [row.id for row in listed] == [job.id]
        assert fetched.artifact_size == len(b"%PDF-1.7 secret statement")
        assert fetched.status == JobStatus.SUCCEEDED
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert statements, "expected the listing to hit the database"
    bytes_column = re.compile(r"\bjobs\.artifact\b(?!_)")
    offenders = [s for s in statements if bytes_column.search(s)]
    assert offenders == [], offenders

    # And the one path that is allowed to read them, does.
    statements.clear()
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert jobs_service.load_artifact(db, first.company.id, job.id) is not None
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert any(bytes_column.search(s) for s in statements)


def test_the_reaper_fails_jobs_abandoned_by_a_restart(db: Session, two_tenants) -> None:  # noqa: ANN001
    first, _ = two_tenants
    set_actor(db, first.user.id)
    set_tenant(db, first.company.id)
    job = jobs_service.enqueue(
        db,
        first.company.id,
        STATEMENT_JOB,
        {"role": "ar", "partner_ids": [], "as_of": "2026-03-10"},
        actor=first.user,
    )
    job.status = JobStatus.RUNNING
    job.started_at = datetime.now(UTC) - jobs_service.LEASE - timedelta(minutes=1)
    db.commit()

    abandoned, deleted = jobs_service.sweep(db, first.company.id)
    db.commit()
    db.refresh(job)
    assert (abandoned, deleted) == (1, 0)
    assert job.status == JobStatus.FAILED
    assert job.error == jobs_service.ABANDONED_ERROR


def test_retention_deletes_expired_artifacts(db: Session, two_tenants) -> None:  # noqa: ANN001
    first, _ = two_tenants
    job = _queue(db, first.company.id, first.user)
    assert job.expires_at is not None
    job.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()

    abandoned, deleted = jobs_service.sweep(db, first.company.id)
    db.commit()
    assert (abandoned, deleted) == (0, 1)
    assert db.scalars(select(Job)).all() == []


def test_a_running_job_is_never_reaped_before_its_lease(db: Session, two_tenants) -> None:  # noqa: ANN001
    first, _ = two_tenants
    set_actor(db, first.user.id)
    set_tenant(db, first.company.id)
    job = jobs_service.enqueue(
        db,
        first.company.id,
        STATEMENT_JOB,
        {"role": "ar", "partner_ids": [], "as_of": "2026-03-10"},
        actor=first.user,
    )
    job.status = JobStatus.RUNNING
    job.started_at = datetime.now(UTC)
    db.commit()
    assert jobs_service.sweep(db, first.company.id) == (0, 0)


def test_enqueue_stamps_a_retention_deadline(db: Session) -> None:
    tenant = make_tenant(db, company_name="Kigali Traders Ltd", email="owner@kigali.example")
    set_actor(db, tenant.user.id)
    set_tenant(db, tenant.company.id)
    job = jobs_service.enqueue(
        db,
        tenant.company.id,
        STATEMENT_JOB,
        {"role": "ar", "partner_ids": [], "as_of": "2026-03-10"},
        actor=tenant.user,
    )
    db.commit()
    assert job.expires_at is not None
    assert job.expires_at > datetime.now(UTC) + jobs_service.RETENTION - timedelta(minutes=1)


def test_the_reaper_endpoint_is_closed_to_a_reports_only_role(
    client: TestClient, db: Session
) -> None:
    """`POST /jobs/sweep` deletes rows. Reading a statement and reaping other people's jobs
    are different privileges: the endpoint takes the AR/AP *setup* permissions, so the seeded
    Accountant and Clerk roles — both of which hold only the reports ones — are refused."""
    from app.core import permissions as perms
    from app.models.membership import Role
    from app.services import email as email_service

    session = client.post(
        "/api/v1/auth/signup",
        json={
            "company_name": "Kigali Traders Ltd",
            "full_name": "Aline Uwase",
            "email": "owner@kigali.example",
            "password": PASSWORD,
        },
    ).json()
    set_tenant(db, session["company_id"])
    accountant_role = db.scalars(
        select(Role.id).where(
            Role.company_id == session["company_id"], Role.name == "Accountant"
        )
    ).one()
    client.post(
        "/api/v1/invitations",
        json={"email": "accountant@kigali.example", "role_ids": [accountant_role]},
    )
    token = email_service.outbox[-1].context["token"]
    accountant = TestClient(client.app)
    accountant.post(
        "/api/v1/invitations/accept",
        json={"token": token, "full_name": "Accountant Person", "password": PASSWORD},
    )

    # The premise: this role really does hold the reports permissions and not the setup ones.
    accountant_perms = next(
        spec["permissions"] for spec in perms.SYSTEM_ROLES if spec["name"] == "Accountant"
    )
    assert perms.AR_REPORTS_VIEW in accountant_perms
    assert perms.AR_SETUP_MANAGE not in accountant_perms
    assert perms.AP_SETUP_MANAGE not in accountant_perms

    assert accountant.get("/api/v1/subledger/jobs").status_code == 200
    denied = accountant.post("/api/v1/subledger/jobs/sweep")
    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_denied"
    assert "ar:setup_manage" in denied.json()["message"]

    # The owner, who holds everything, still may.
    assert client.post("/api/v1/subledger/jobs/sweep").status_code == 200
