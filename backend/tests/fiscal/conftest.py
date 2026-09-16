"""Fixtures for the fiscalization suite.

The sandbox is mounted **in process**, so the adapter under test is the real one: its payload,
its route table, its parsing and its error policy all run, and what answers is an app that
checks the payload the way RRA does. A mock returning a canned dict would have exercised none
of it.

Starlette's `TestClient` rather than `httpx.ASGITransport`, because the adapter is synchronous
and that transport is async-only. `TestClient` *is* an `httpx.Client` — it subclasses one — so
what the adapter receives is an ordinary client, and it routes whatever host the device's
`base_url` names into the app.
"""

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import set_actor, set_tenant
from app.fiscal import devices as device_service
from app.fiscal.rwanda.sandbox import SandboxState, create_sandbox_app
from app.models.company import Branch, Company
from app.models.fiscalization import FiscalDevice, FiscalEnvironment, FiscalProfile
from app.models.membership import CompanyMembership
from app.models.user import User
from tests.conftest import make_tenant

#: Any host: the test transport never resolves it, and a device's `base_url` is a column, so
#: the value only has to be a URL the adapter can join a path onto.
SANDBOX_URL = "http://ebm.sandbox"
COMPANY_TIN = "999000099"


@pytest.fixture
def sandbox_state() -> SandboxState:
    return SandboxState()


@pytest.fixture
def sandbox_client(sandbox_state: SandboxState) -> Iterator[httpx.Client]:
    with TestClient(create_sandbox_app(sandbox_state), base_url=SANDBOX_URL) as client:
        yield client


@pytest.fixture
def fiscal_company(db: Session) -> Company:
    """A VAT-registered Rwandan tenant with a TIN — the precondition for any device."""
    tenant = make_tenant(db, company_name="Rugari Wines Ltd", email="owner@rugari.example")
    set_tenant(db, tenant.company.id)
    set_actor(db, tenant.user.id)
    tenant.company.tin = COMPANY_TIN
    tenant.company.vat_registered = True
    db.commit()
    return tenant.company


@pytest.fixture
def fiscal_owner(db: Session, fiscal_company: Company) -> User:
    membership = db.scalar(
        select(CompanyMembership).where(CompanyMembership.company_id == fiscal_company.id)
    )
    return db.get(User, membership.user_id)


@pytest.fixture
def main_branch(db: Session, fiscal_company: Company) -> Branch:
    return db.scalars(
        select(Branch).where(Branch.company_id == fiscal_company.id, Branch.is_main.is_(True))
    ).one()


@pytest.fixture
def registered_device(
    db: Session, fiscal_company: Company, fiscal_owner: User, main_branch: Branch
) -> FiscalDevice:
    device = device_service.register_device(
        db,
        fiscal_company.id,
        branch_id=main_branch.id,
        profile=FiscalProfile.VSDC,
        environment=FiscalEnvironment.TEST,
        base_url=SANDBOX_URL,
        dvc_srl_no="SDC-SERIAL-0001",
        bhf_id="00",
        actor=fiscal_owner,
    )
    db.commit()
    return device


@pytest.fixture
def active_device(
    db: Session,
    fiscal_company: Company,
    fiscal_owner: User,
    registered_device: FiscalDevice,
    sandbox_client: httpx.Client,
) -> FiscalDevice:
    device_service.initialize_device(
        db, fiscal_company.id, registered_device, actor=fiscal_owner, client=sandbox_client
    )
    db.commit()
    return registered_device
