from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import set_tenant
from app.models.audit import AuditLog
from app.models.membership import CompanyMembership, MembershipStatus, Role
from app.services import email as email_service

OWNER = {
    "company_name": "Kigali Traders Ltd",
    "full_name": "Aline Uwase",
    "email": "owner@kigali.example",
    "password": "correct horse battery staple",
}
INVITEE_PASSWORD = "another good passphrase"


def _signup(client: TestClient) -> dict:
    return client.post("/api/v1/auth/signup", json=OWNER).json()


def _accountant_role_id(db: Session, company_id: int) -> int:
    set_tenant(db, company_id)
    return db.scalars(
        select(Role.id).where(Role.company_id == company_id, Role.name == "Accountant")
    ).one()


def test_invitation_round_trip(client: TestClient, db: Session) -> None:
    session = _signup(client)
    role_id = _accountant_role_id(db, session["company_id"])

    invited = client.post(
        "/api/v1/invitations",
        json={"email": "jean@kigali.example", "role_ids": [role_id]},
    )
    assert invited.status_code == 201
    assert invited.json()["status"] == "pending"

    pending = client.get("/api/v1/invitations").json()
    assert [item["email"] for item in pending] == ["jean@kigali.example"]
    assert pending[0]["role_ids"] == [role_id]

    token = email_service.outbox[-1].context["token"]
    invitee = TestClient(client.app)
    accepted = invitee.post(
        "/api/v1/invitations/accept",
        json={"token": token, "full_name": "Jean Bosco", "password": INVITEE_PASSWORD},
    )
    assert accepted.status_code == 200
    assert accepted.json()["company_id"] == session["company_id"]

    me = invitee.get("/api/v1/auth/me").json()
    assert me["email"] == "jean@kigali.example"
    assert me["memberships"][0]["company_id"] == session["company_id"]
    assert "gl:journal_post" in me["permissions"]
    assert "users:create" not in me["permissions"]

    assert client.get("/api/v1/invitations").json() == []
    set_tenant(db, session["company_id"])
    membership = db.scalars(
        select(CompanyMembership).where(CompanyMembership.email == "jean@kigali.example")
    ).one()
    assert membership.status == MembershipStatus.ACTIVE
    assert membership.invite_token_hash is None
    assert membership.accepted_at is not None


def test_invitation_is_audited(client: TestClient, db: Session) -> None:
    session = _signup(client)
    role_id = _accountant_role_id(db, session["company_id"])
    client.post("/api/v1/invitations", json={"email": "jean@kigali.example", "role_ids": [role_id]})
    token = email_service.outbox[-1].context["token"]
    TestClient(client.app).post(
        "/api/v1/invitations/accept",
        json={"token": token, "full_name": "Jean Bosco", "password": INVITEE_PASSWORD},
    )

    set_tenant(db, session["company_id"])
    actions = db.scalars(select(AuditLog.action).order_by(AuditLog.id)).all()
    assert actions == [
        "company.provisioned",
        "membership.invited",
        "membership.invitation_accepted",
    ]


def test_duplicate_invitation_is_rejected(client: TestClient) -> None:
    _signup(client)
    payload = {"email": "jean@kigali.example", "role_ids": []}

    assert client.post("/api/v1/invitations", json=payload).status_code == 201
    conflict = client.post("/api/v1/invitations", json=payload)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "membership_exists"


def test_invitation_can_be_revoked_before_acceptance(client: TestClient) -> None:
    _signup(client)
    invitation = client.post(
        "/api/v1/invitations", json={"email": "jean@kigali.example", "role_ids": []}
    ).json()
    token = email_service.outbox[-1].context["token"]

    assert client.delete(f"/api/v1/invitations/{invitation['id']}").status_code == 204

    accepted = TestClient(client.app).post(
        "/api/v1/invitations/accept",
        json={"token": token, "full_name": "Jean Bosco", "password": INVITEE_PASSWORD},
    )
    assert accepted.status_code == 401


def test_accepting_with_an_unknown_token_fails(client: TestClient) -> None:
    response = client.post(
        "/api/v1/invitations/accept",
        json={"token": "x" * 40, "full_name": "Nobody", "password": INVITEE_PASSWORD},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_invited_user_can_switch_between_companies(client: TestClient) -> None:
    first = _signup(client)
    assert (
        client.post(
            "/api/v1/invitations", json={"email": "jean@kigali.example", "role_ids": []}
        ).status_code
        == 201
    )
    first_token = email_service.outbox[-1].context["token"]

    musanze = TestClient(client.app)
    second = musanze.post(
        "/api/v1/auth/signup",
        json={
            "company_name": "Musanze Supplies Ltd",
            "full_name": "Bob Habimana",
            "email": "owner@musanze.example",
            "password": "correct horse battery staple",
        },
    ).json()
    assert (
        musanze.post(
            "/api/v1/invitations", json={"email": "jean@kigali.example", "role_ids": []}
        ).status_code
        == 201
    )
    second_token = email_service.outbox[-1].context["token"]

    jean = TestClient(client.app)
    jean.post(
        "/api/v1/invitations/accept",
        json={"token": first_token, "full_name": "Jean Bosco", "password": INVITEE_PASSWORD},
    )
    jean.cookies.clear()
    jean.post("/api/v1/invitations/accept", json={"token": second_token})

    jean.cookies.clear()
    login = jean.post(
        "/api/v1/auth/login", json={"email": "jean@kigali.example", "password": INVITEE_PASSWORD}
    )
    # Two memberships: the session starts without a company until one is chosen.
    assert login.json()["company_id"] is None
    assert {m["company_id"] for m in jean.get("/api/v1/auth/me").json()["memberships"]} == {
        first["company_id"],
        second["company_id"],
    }

    switched = jean.post("/api/v1/auth/switch-company", json={"company_id": second["company_id"]})
    assert switched.status_code == 200
    assert switched.json()["company_id"] == second["company_id"]
    assert jean.get("/api/v1/auth/me").json()["company"]["id"] == second["company_id"]


def test_switching_into_a_company_without_a_membership_is_refused(client: TestClient) -> None:
    _signup(client)
    outsider = TestClient(client.app)
    stranger = outsider.post(
        "/api/v1/auth/signup",
        json={
            "company_name": "Musanze Supplies Ltd",
            "full_name": "Bob Habimana",
            "email": "owner@musanze.example",
            "password": "correct horse battery staple",
        },
    ).json()

    response = client.post(
        "/api/v1/auth/switch-company", json={"company_id": stranger["company_id"]}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "no_membership"
