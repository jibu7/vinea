"""The device endpoints: who may call them, what they return, and what they never return.

The service tests above prove the behaviour; these prove the *surface* — that the permissions
are the ones decision 15 names, that a second tenant cannot see a device, and that no response
carries a key. A service that is correct behind an endpoint anyone can call is not correct.

The endpoints themselves have no screen until step 6, which is why each mutating one carries a
`GAP (P7, step 6)` line in `tests/test_api_has_a_caller.py`.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import (
    FISCAL_QUEUE_MANAGE,
    FISCAL_REPORTS_VIEW,
    FISCAL_SETUP_MANAGE,
    GL_FX_REVALUE,
    TAX_VAT_RETURN_FILE,
    TAX_VAT_RETURN_VIEW,
)
from app.db import set_tenant
from app.models.company import Branch
from app.models.membership import Role
from app.services import email as email_service
from tests.conftest import make_tenant

PASSWORD = "correct horse battery staple"
CLERK_PASSWORD = "another good passphrase"


@pytest.fixture
def signed_in(client: TestClient, db: Session):  # noqa: ANN201
    """An owner of a Rwandan, VAT-registered tenant, signed in through the real login."""
    tenant = make_tenant(db, company_name="Rugari Wines Ltd", email="api-owner@rugari.example")
    set_tenant(db, tenant.company.id)
    tenant.company.tin = "999000099"
    tenant.company.vat_registered = True
    db.commit()
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "api-owner@rugari.example", "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return tenant


@pytest.fixture
def branch_id(db: Session, signed_in) -> int:  # noqa: ANN001
    return db.scalars(
        select(Branch).where(
            Branch.company_id == signed_in.company.id, Branch.is_main.is_(True)
        )
    ).one().id


def register(client: TestClient, branch_id: int, **overrides) -> dict:  # noqa: ANN003
    payload = {
        "branch_id": branch_id,
        "profile": "vsdc",
        "environment": "test",
        "base_url": "http://ebm.sandbox",
        "dvc_srl_no": "SDC-SERIAL-0001",
        "bhf_id": "00",
        **overrides,
    }
    return client.post("/api/v1/fiscal/devices", json=payload)


def test_registering_a_device_returns_it_pending_and_keyless(
    client: TestClient, signed_in, branch_id: int
) -> None:
    response = register(client, branch_id)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["has_keys"] is False
    assert "cmc_key" not in body and "sign_key" not in body


def test_a_second_device_on_the_same_branch_is_a_conflict(
    client: TestClient, signed_in, branch_id: int
) -> None:
    register(client, branch_id)

    response = register(client, branch_id, dvc_srl_no="SDC-SERIAL-0002")

    assert response.status_code == 409
    assert response.json()["code"] == "fiscal_device_exists"


def test_an_unknown_branch_is_a_not_found_rather_than_a_server_error(
    client: TestClient, signed_in, branch_id: int
) -> None:
    response = register(client, branch_id + 10_000)

    assert response.status_code == 404
    assert response.json()["code"] == "branch_not_found"


def test_the_listing_never_carries_a_key_field(
    client: TestClient, signed_in, branch_id: int
) -> None:
    register(client, branch_id)

    body = client.get("/api/v1/fiscal/devices").json()

    assert len(body) == 1
    rendered = str(body)
    for column in ("cmc_key", "intrl_key", "sign_key"):
        assert column not in rendered


def test_the_seeded_administrator_holds_every_p7_permission(
    db: Session, signed_in
) -> None:  # noqa: ANN001
    """Decision 15's list, checked against the role a new tenant is actually seeded with —
    which is where a constant added to `ALL_PERMISSIONS` and forgotten in `SYSTEM_ROLES` would
    show up."""
    role = db.scalars(
        select(Role).where(
            Role.company_id == signed_in.company.id, Role.name == "Administrator"
        )
    ).one()

    assert {
        FISCAL_SETUP_MANAGE,
        FISCAL_QUEUE_MANAGE,
        FISCAL_REPORTS_VIEW,
        TAX_VAT_RETURN_VIEW,
        TAX_VAT_RETURN_FILE,
        GL_FX_REVALUE,
    } <= set(role.permissions)


def _invite_clerk(client: TestClient, db: Session, company_id: int) -> TestClient:
    """A member of the same tenant holding the Clerk role — which has no fiscal permission.

    The owner passes every check implicitly, so a permission can only be *proven* against
    somebody who is not one. Without this the endpoints could carry no dependency at all and
    every test above would still pass.
    """
    set_tenant(db, company_id)
    clerk_role = db.scalars(
        select(Role.id).where(Role.company_id == company_id, Role.name == "Clerk")
    ).one()
    client.post(
        "/api/v1/invitations", json={"email": "clerk@rugari.example", "role_ids": [clerk_role]}
    )
    token = email_service.outbox[-1].context["token"]
    clerk = TestClient(client.app)
    clerk.post(
        "/api/v1/invitations/accept",
        json={"token": token, "full_name": "Clerk Person", "password": CLERK_PASSWORD},
    )
    return clerk


def test_a_clerk_cannot_register_or_initialize_a_device(
    client: TestClient, db: Session, signed_in, branch_id: int
) -> None:
    device_id = register(client, branch_id).json()["id"]
    clerk = _invite_clerk(client, db, signed_in.company.id)

    refused = register(clerk, branch_id, dvc_srl_no="SDC-SERIAL-0002")
    assert refused.status_code == 403
    assert refused.json()["code"] == "permission_denied"
    assert FISCAL_SETUP_MANAGE in refused.json()["message"]

    for path in ("initialize", "suspend", "sync-codes", "sync-item-classes"):
        response = clerk.post(f"/api/v1/fiscal/devices/{device_id}/{path}", json={"reason": "x"})
        assert response.status_code == 403, path


def test_a_clerk_cannot_read_the_devices_either(
    client: TestClient, db: Session, signed_in, branch_id: int
) -> None:
    """Reading is `fiscal:setup_manage` **or** `fiscal:reports_view`, and the Clerk role holds
    neither — so the queue dashboard is grantable without the authority to reconfigure a
    device, and a clerk with no fiscal role at all still sees nothing."""
    register(client, branch_id)
    clerk = _invite_clerk(client, db, signed_in.company.id)

    assert clerk.get("/api/v1/fiscal/devices").status_code == 403


def test_another_tenant_cannot_see_or_touch_a_device(
    client: TestClient, db: Session, signed_in, branch_id: int
) -> None:
    """Tenancy, at the endpoint. The device id is a guessable integer, so the isolation that
    matters is the one that refuses a *valid* id belonging to somebody else."""
    device_id = register(client, branch_id).json()["id"]
    client.post("/api/v1/auth/logout")

    other = make_tenant(db, company_name="Kivu Traders Ltd", email="other@kivu.example")
    assert other.company.id != signed_in.company.id
    client.post(
        "/api/v1/auth/login", json={"email": "other@kivu.example", "password": PASSWORD}
    )

    assert client.get("/api/v1/fiscal/devices").json() == []
    assert client.get(f"/api/v1/fiscal/devices/{device_id}").status_code == 404
    assert (
        client.post(f"/api/v1/fiscal/devices/{device_id}/suspend", json={"reason": "no"})
        .status_code
        == 404
    )


def test_an_anonymous_caller_is_refused(client: TestClient) -> None:
    assert client.get("/api/v1/fiscal/devices").status_code == 401


def test_initialization_requires_an_idempotency_key(
    client: TestClient, signed_in, branch_id: int
) -> None:
    """Not naturally idempotent from the caller's side: a double-click while the authority is
    slow would re-initialize a live device, and re-initialization is how its keys are
    reissued."""
    device_id = register(client, branch_id).json()["id"]

    response = client.post(f"/api/v1/fiscal/devices/{device_id}/initialize")

    assert response.status_code == 422
    assert "Idempotency-Key" in str(response.json()["field_errors"])


def test_the_code_and_item_class_listings_start_empty_and_are_tenant_scoped(
    client: TestClient, signed_in
) -> None:
    """They are populated by a sync, which needs a device — so empty is the correct answer
    here, and a listing that invented rows would be worse than one that did not."""
    assert client.get("/api/v1/fiscal/codes").json() == []
    assert client.get("/api/v1/fiscal/item-classes").json() == []
