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
from dataclasses import dataclass

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
from app.models.gl import GLSettings
from app.models.inventory import Item
from app.models.membership import CompanyMembership
from app.models.partner import Partner
from app.models.tax import TaxCode
from app.models.user import User
from app.subledger import masters as partner_masters
from tests.conftest import make_tenant

# An ordinary AR/AP tenant that never fiscalizes, re-exported the way `tests/tax` re-exports
# it — with the `ledger` it is built on, which is a fixture rather than the `build_ledger`
# function this module already calls. Decision 11's last sentence needs one: a company with no
# device prints the P4 layout, and proving that needs a company with no device.
from tests.kernel.conftest import ledger as ledger  # noqa: PLC0414
from tests.order_entry.conftest import OrderEntry, build_order_entry
from tests.subledger.conftest import subledger as subledger  # noqa: PLC0414

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


# --- A fiscalized tenant with stock, items and a customer -----------------------------------
#
# Built on the P6 order-entry fixture rather than beside it, deliberately: what step 2 has to
# be true of is the *real* posting path — `post_document` with its companion stock entry, its
# kits and its credit-limit check — and a fiscal fixture that stood up its own simplified
# company would be proving the hook against a company nothing else in the build uses.


@dataclass
class FiscalPosting:
    """A Rwandan tenant that fiscalizes: an active device on the main branch, items RRA could
    hold, and a customer with a TIN beside one without."""

    order: OrderEntry
    device: FiscalDevice
    customer: Partner
    walk_in: Partner
    tax_codes: dict[str, TaxCode]

    @property
    def company_id(self) -> int:
        return self.order.company_id

    @property
    def owner(self) -> User:
        return self.order.owner

    @property
    def stock_item(self) -> Item:
        return self.order.stock_item

    @property
    def service_item(self) -> Item:
        return self.order.service_item


def fiscalize(
    db: Session, order: OrderEntry, sandbox_client: httpx.Client, *, tag: str = "0"
) -> FiscalPosting:
    """Turn an order-entry tenant into one RRA would recognise.

    Everything here is something a screen does at steps 6–8 — a TIN on the company, a class on
    each item, a quantity unit on the unit, a device registered and initialized. Doing it in a
    fixture is how step 2 is testable before those screens exist; each line is also the list of
    what those screens owe.
    """
    company = db.get(Company, order.company_id)
    company.tin = COMPANY_TIN
    company.vat_registered = True

    order.each.fiscal_quantity_unit = "U"
    tax_codes = {
        row.code: row
        for row in db.scalars(select(TaxCode).where(TaxCode.company_id == order.company_id))
    }
    for item in (order.stock_item, order.service_item, order.kit_item, order.weighted_item):
        if item is not None:
            item.fiscal_class_code = "5059020800"
            # A sale line with no tax code at all has no class RRA could report it under, so
            # the fixture gives the catalogue one — which is what the Items screen does.
            item.default_sales_tax_code_id = tax_codes["VAT-OUT-18"].id
    customer = partner_masters.create_partner(
        db,
        order.company_id,
        partner_masters.PartnerInput(
            name="Umucyo Traders Ltd",
            customer_code=f"CUSTIN{tag}",
            tin="100000001",
            phone="+250788000001",
        ),
        actor=order.owner,
    )
    walk_in = partner_masters.create_partner(
        db,
        order.company_id,
        partner_masters.PartnerInput(name="Walk-in customer", customer_code=f"WALKIN{tag}"),
        actor=order.owner,
    )
    # The supplier gets a TIN too: `spplrTin` is what RRA reconciles a purchase declaration
    # against its supplier's own sale, and a supplier without one would make the whole
    # purchase side of decision 9 testable only in its degenerate case.
    order.supplier.tin = "100000002"
    main_branch = db.scalars(
        select(Branch).where(Branch.company_id == order.company_id, Branch.is_main.is_(True))
    ).one()
    device = device_service.register_device(
        db,
        order.company_id,
        branch_id=main_branch.id,
        profile=FiscalProfile.VSDC,
        environment=FiscalEnvironment.TEST,
        base_url=SANDBOX_URL,
        dvc_srl_no=f"SDC-SERIAL-{tag}",
        bhf_id="00",
        actor=order.owner,
    )
    device_service.initialize_device(
        db, order.company_id, device, actor=order.owner, client=sandbox_client
    )
    db.flush()
    return FiscalPosting(
        order=order, device=device, customer=customer, walk_in=walk_in, tax_codes=tax_codes
    )


@pytest.fixture
def fiscal_posting(db: Session, sandbox_client: httpx.Client) -> FiscalPosting:
    return fiscalize(db, build_order_entry(db, "fiscal"), sandbox_client)


def activate_depot_device(
    db: Session, fixture: FiscalPosting, sandbox_client: httpx.Client, *, tag: str = "depot"
) -> FiscalDevice:
    """A **second** device, on the depot's branch — what a cross-branch transfer needs.

    The authority holds one stock figure per (taxpayer, branch) and a device belongs to one
    branch, so a movement between branches is two reports on two devices. A company with one
    device can never exercise that: every movement lands on the same device and a rule that
    reported to the wrong one would look perfectly correct. The order-entry fixture already
    puts the depot in a branch of its own for the same reason on the accrual side.
    """
    device = device_service.register_device(
        db,
        fixture.company_id,
        branch_id=fixture.order.depot_branch_id,
        profile=FiscalProfile.VSDC,
        environment=FiscalEnvironment.TEST,
        base_url=SANDBOX_URL,
        dvc_srl_no=f"SDC-SERIAL-{tag}",
        bhf_id="01",
        actor=fixture.owner,
    )
    device_service.initialize_device(
        db, fixture.company_id, device, actor=fixture.owner, client=sandbox_client
    )
    db.flush()
    return device


@pytest.fixture
def fiscal_defaults(db: Session, fiscal_posting: FiscalPosting) -> FiscalPosting:
    """The same tenant with the **default purchase class** set.

    Decision 9 gives a purchase line with no item — rent, freight — the company's default
    purchase class, and decision 14 lists the key without seeding a value for it: what a
    company buys is not something a seed can know. Setting it is what step 6's Defaults screen
    does, and doing it in a fixture is how the purchase report is testable before that screen
    exists.
    """
    settings_row = db.scalar(
        select(GLSettings).where(GLSettings.company_id == fiscal_posting.company_id)
    )
    settings_row.fiscal_default_purchase_class_code = DEFAULT_PURCHASE_CLASS
    db.flush()
    return fiscal_posting


#: A class from the synced §3.3.2.2 list the sandbox serves — "General services", which is what
#: rent and freight are.
DEFAULT_PURCHASE_CLASS = "8514900000"
