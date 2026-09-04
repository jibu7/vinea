from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import platform_scope, set_tenant
from app.models.audit import AuditLog
from app.models.company import Company, CompanyStatus

OPERATOR = {"email": "operator@vinea.example", "password": "correct horse battery staple"}


def _as_operator(client: TestClient, platform_admin) -> TestClient:
    session = TestClient(client.app)
    response = session.post("/api/v1/auth/login", json=OPERATOR)
    assert response.status_code == 200
    return session


def test_tenant_list_is_paginated(client: TestClient, two_tenants, platform_admin) -> None:
    operator = _as_operator(client, platform_admin)

    first_page = operator.get("/api/v1/operator/tenants", params={"limit": 1})
    assert first_page.status_code == 200
    body = first_page.json()
    assert len(body["items"]) == 1
    assert body["next_cursor"] is not None
    assert body["items"][0]["active_member_count"] == 1

    second_page = operator.get(
        "/api/v1/operator/tenants", params={"limit": 1, "cursor": body["next_cursor"]}
    ).json()
    assert second_page["items"][0]["id"] != body["items"][0]["id"]
    assert second_page["next_cursor"] is None


def test_tenant_list_can_be_searched(client: TestClient, two_tenants, platform_admin) -> None:
    operator = _as_operator(client, platform_admin)

    body = operator.get("/api/v1/operator/tenants", params={"search": "Musanze"}).json()

    assert [item["name"] for item in body["items"]] == ["Musanze Supplies Ltd"]


def test_suspending_a_tenant_locks_its_users_out(
    client: TestClient, two_tenants, platform_admin, db: Session
) -> None:
    first, _ = two_tenants
    operator = _as_operator(client, platform_admin)

    suspended = operator.post(
        f"/api/v1/operator/tenants/{first.company.id}/suspend",
        json={"reason": "Non-payment"},
    )
    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"

    owner = TestClient(client.app)
    login = owner.post(
        "/api/v1/auth/login",
        json={"email": first.user.email, "password": "correct horse battery staple"},
    )
    assert login.status_code == 401
    assert login.json()["code"] == "company_suspended"

    with platform_scope(db):
        db.expire_all()
        assert db.get(Company, first.company.id).status == CompanyStatus.SUSPENDED

    reactivated = operator.post(f"/api/v1/operator/tenants/{first.company.id}/activate")
    assert reactivated.status_code == 200
    assert reactivated.json()["status"] == "active"
    assert reactivated.json()["suspension_reason"] is None
    assert owner.post(
        "/api/v1/auth/login",
        json={"email": first.user.email, "password": "correct horse battery staple"},
    ).status_code == 200


def test_impersonation_grants_tenant_access_and_is_audited(
    client: TestClient, two_tenants, platform_admin, db: Session
) -> None:
    first, second = two_tenants
    operator = _as_operator(client, platform_admin)

    response = operator.post(f"/api/v1/operator/tenants/{first.company.id}/impersonate")
    assert response.status_code == 201
    assert response.json()["company_id"] == first.company.id

    me = operator.get("/api/v1/auth/me").json()
    assert me["company"]["id"] == first.company.id
    assert me["impersonated_by"] == platform_admin.id
    assert me["memberships"] == []
    # The impersonated session works inside the tenant it was issued for…
    assert operator.get("/api/v1/invitations").status_code == 200

    set_tenant(db, first.company.id)
    entries = db.scalars(
        select(AuditLog).where(AuditLog.action == "operator.impersonate")
    ).all()
    assert len(entries) == 1
    assert entries[0].company_id == first.company.id
    assert entries[0].actor_user_id == platform_admin.id
    assert entries[0].impersonated_by_user_id == platform_admin.id

    # …and nothing was written into the other tenant.
    set_tenant(db, second.company.id)
    assert db.scalars(select(AuditLog).where(AuditLog.action == "operator.impersonate")).all() == []


def test_suspension_is_audited(
    client: TestClient, two_tenants, platform_admin, db: Session
) -> None:
    first, _ = two_tenants
    operator = _as_operator(client, platform_admin)
    operator.post(
        f"/api/v1/operator/tenants/{first.company.id}/suspend", json={"reason": "Non-payment"}
    )

    set_tenant(db, first.company.id)
    entry = db.scalars(
        select(AuditLog).where(AuditLog.action == "operator.tenant_suspended")
    ).one()
    assert entry.after == {"status": "suspended", "suspension_reason": "Non-payment"}
    assert entry.actor_email == platform_admin.email


def test_unknown_tenant_returns_the_error_envelope(
    client: TestClient, platform_admin
) -> None:
    operator = _as_operator(client, platform_admin)

    response = operator.get("/api/v1/operator/tenants/999999")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
