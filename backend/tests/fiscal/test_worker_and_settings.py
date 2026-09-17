"""The three things that make the queue drain on its own, and the setting a device locks.

* `app/fiscal/worker.py` — the loop. Postgres is the queue, so what this proves is that the
  loop finds the tenants holding due rows and nothing else: the cross-tenant read returns
  company ids, and everything after it is bound to one tenant.
* `POST /fiscal/outbox/drain` — the scheduler's hook, `by design` in the rule-14 register.
* `fiscal_requires_block` — CIS §7.30, enforced where the setting changes rather than only
  where the device is activated.
"""

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import FISCAL_QUEUE_MANAGE
from app.db import set_tenant
from app.fiscal import devices as device_service
from app.fiscal import worker
from app.inventory import masters as inventory_masters
from app.models.inventory import NegativeStockPolicy
from app.models.membership import Role
from app.services import email as email_service
from tests.conftest import make_tenant
from tests.fiscal.conftest import FiscalPosting
from tests.fiscal.helpers import invoice, receive

PASSWORD = "correct horse battery staple"


# --- The worker loop ---------------------------------------------------------------------------


def test_the_loop_finds_the_tenants_holding_a_due_row(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    receive(fiscal_posting, db)
    invoice(fiscal_posting, db)
    db.commit()

    assert worker.companies_with_due_rows(db, now=worker.datetime.now(worker.UTC)) == [
        fiscal_posting.company_id
    ]


def test_a_suspended_device_is_not_drained(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """Suspension is a decision that this device stops talking to RRA. A loop that kept
    sending for it would make the button a lie."""
    receive(fiscal_posting, db)
    invoice(fiscal_posting, db)
    device_service.suspend(
        db,
        fiscal_posting.company_id,
        fiscal_posting.device,
        reason="under investigation",
        actor=fiscal_posting.owner,
    )
    db.commit()

    assert worker.companies_with_due_rows(db, now=worker.datetime.now(worker.UTC)) == []


def test_a_tenant_with_nothing_due_is_not_woken(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    db.commit()

    assert worker.companies_with_due_rows(db, now=worker.datetime.now(worker.UTC)) == []
    # And the loop itself terminates without touching anything.
    worker.run(poll_seconds=0.01, iterations=1)


# --- The drain endpoint ------------------------------------------------------------------------


@pytest.fixture
def signed_in(client: TestClient, db: Session):  # noqa: ANN201
    tenant = make_tenant(db, company_name="Rugari Wines Ltd", email="drain@rugari.example")
    set_tenant(db, tenant.company.id)
    tenant.company.tin = "999000099"
    tenant.company.vat_registered = True
    db.commit()
    response = client.post(
        "/api/v1/auth/login", json={"email": "drain@rugari.example", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return tenant


def test_the_drain_endpoint_answers_for_a_company_with_no_queue(
    client: TestClient, signed_in
) -> None:  # noqa: ANN001
    """A tenant with no device has nothing to send, and says so rather than failing. The
    endpoint is the scheduler's hook — there is no screen, and `NO_UI` says why."""
    response = client.post("/api/v1/fiscal/outbox/drain")

    assert response.status_code == 200, response.text
    assert response.json() == {"rows": 0, "sent": 0, "outcomes": []}


def test_a_clerk_cannot_drain_the_queue(
    client: TestClient, db: Session, signed_in
) -> None:  # noqa: ANN001
    """The owner passes every check implicitly, so a permission is only ever *proven* against
    somebody who is not one."""
    set_tenant(db, signed_in.company.id)
    clerk_role = db.scalars(
        select(Role.id).where(
            Role.company_id == signed_in.company.id, Role.name == "Clerk"
        )
    ).one()
    client.post(
        "/api/v1/invitations",
        json={"email": "drain-clerk@rugari.example", "role_ids": [clerk_role]},
    )
    token = email_service.outbox[-1].context["token"]
    clerk = TestClient(client.app)
    clerk.post(
        "/api/v1/invitations/accept",
        json={
            "token": token,
            "full_name": "Clerk Person",
            "password": "another good passphrase",
        },
    )

    refused = clerk.post("/api/v1/fiscal/outbox/drain")

    assert refused.status_code == 403
    assert FISCAL_QUEUE_MANAGE in refused.json()["message"]


# --- `fiscal_requires_block` (CIS §7.30) ---------------------------------------------------------


def test_negative_stock_cannot_be_allowed_while_a_device_is_active(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """No receipt may be issued for goods the stock does not hold. `block` is what makes that
    true, so it cannot be turned off from the inventory defaults screen."""
    with pytest.raises(device_service.FiscalSetupError) as refusal:
        inventory_masters.update_inventory_defaults(
            db,
            fiscal_posting.company_id,
            {"negative_stock_policy": NegativeStockPolicy.ALLOW.value},
            actor=fiscal_posting.owner,
        )

    assert refusal.value.code == "fiscal_requires_block"
    assert "negative_stock_policy" in refusal.value.field_errors


def test_the_same_change_goes_through_once_no_device_is_active(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """The sensitivity half: the refusal is about the device, not about the setting."""
    device_service.suspend(
        db,
        fiscal_posting.company_id,
        fiscal_posting.device,
        reason="closing the shop",
        actor=fiscal_posting.owner,
    )

    inventory_masters.update_inventory_defaults(
        db,
        fiscal_posting.company_id,
        {"negative_stock_policy": NegativeStockPolicy.ALLOW.value},
        actor=fiscal_posting.owner,
    )

    settings = inventory_masters.inventory_defaults(db, fiscal_posting.company_id)
    assert settings.negative_stock_policy == NegativeStockPolicy.ALLOW


def test_activating_a_device_locks_the_policy_back_to_block(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    device_service.suspend(
        db,
        fiscal_posting.company_id,
        fiscal_posting.device,
        reason="temporarily",
        actor=fiscal_posting.owner,
    )
    inventory_masters.update_inventory_defaults(
        db,
        fiscal_posting.company_id,
        {"negative_stock_policy": NegativeStockPolicy.ALLOW.value},
        actor=fiscal_posting.owner,
    )

    device_service.activate(
        db, fiscal_posting.company_id, fiscal_posting.device, actor=fiscal_posting.owner
    )

    settings = inventory_masters.inventory_defaults(db, fiscal_posting.company_id)
    assert settings.negative_stock_policy == NegativeStockPolicy.BLOCK
