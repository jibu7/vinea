from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import ALL_PERMISSIONS, SYSTEM_ROLES, has_permissions
from app.db import set_tenant
from app.models.membership import Role
from app.services import email as email_service

OWNER = {
    "company_name": "Kigali Traders Ltd",
    "full_name": "Aline Uwase",
    "email": "owner@kigali.example",
    "password": "correct horse battery staple",
}
CLERK_PASSWORD = "another good passphrase"


def test_seeded_roles_only_grant_known_permissions() -> None:
    for spec in SYSTEM_ROLES:
        assert has_permissions(ALL_PERMISSIONS, spec["permissions"]), spec["name"]


def _invite_clerk(client: TestClient, db: Session, company_id: int) -> TestClient:
    set_tenant(db, company_id)
    clerk_role = db.scalars(
        select(Role.id).where(Role.company_id == company_id, Role.name == "Clerk")
    ).one()
    client.post(
        "/api/v1/invitations", json={"email": "clerk@kigali.example", "role_ids": [clerk_role]}
    )
    token = email_service.outbox[-1].context["token"]
    clerk = TestClient(client.app)
    clerk.post(
        "/api/v1/invitations/accept",
        json={"token": token, "full_name": "Clerk Person", "password": CLERK_PASSWORD},
    )
    return clerk


def test_require_blocks_a_role_without_the_permission(client: TestClient, db: Session) -> None:
    session = client.post("/api/v1/auth/signup", json=OWNER).json()
    clerk = _invite_clerk(client, db, session["company_id"])

    response = clerk.post("/api/v1/invitations", json={"email": "someone@kigali.example"})

    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "permission_denied"
    assert "users:create" in body["message"]


def test_require_allows_a_role_that_holds_the_permission(client: TestClient, db: Session) -> None:
    session = client.post("/api/v1/auth/signup", json=OWNER).json()
    clerk = _invite_clerk(client, db, session["company_id"])

    assert clerk.get("/api/v1/invitations").status_code == 200


def test_tenant_endpoints_reject_a_session_without_a_company(client: TestClient) -> None:
    client.post("/api/v1/auth/signup", json=OWNER)
    outsider = TestClient(client.app)
    outsider.post(
        "/api/v1/auth/signup",
        json={
            "company_name": "Musanze Supplies Ltd",
            "full_name": "Bob Habimana",
            "email": "owner@musanze.example",
            "password": "correct horse battery staple",
        },
    )
    # A cookie-less client has no session at all.
    anonymous = TestClient(client.app)
    assert anonymous.get("/api/v1/invitations").status_code == 401


def test_operator_console_is_closed_to_ordinary_users(client: TestClient) -> None:
    client.post("/api/v1/auth/signup", json=OWNER)

    response = client.get("/api/v1/operator/tenants")

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"
