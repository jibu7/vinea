"""Fixtures for the P6 posting contract: a company with stock masters, a supplier and a
customer, and the order-entry settings the phase reads."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory import masters as inventory_masters
from app.models.company import Branch
from app.models.gl import GLAccount, GLSettings
from app.models.inventory import Item, ItemType, Uom, Warehouse
from app.models.partner import Partner
from app.models.user import User
from app.subledger import masters as partner_masters
from tests.inventory.conftest import Inventory, build_inventory
from tests.kernel.conftest import YEAR, Ledger, build_ledger
from tests.kernel.conftest import ledger as ledger  # noqa: PLC0414 - re-exported fixture

MARCH = date(YEAR, 3, 10)
APRIL = date(YEAR, 4, 10)


@dataclass
class OrderEntry:
    inventory: Inventory
    customer: Partner
    supplier: Partner
    stock_item: Item
    service_item: Item
    accounts: dict[str, GLAccount]
    #: A virtual bundle of 2 x `stock_item` (P6 decision 8). A kit is never on a shelf, never
    #: received and never purchased; what it does is explode at line entry into components that
    #: are, which is the whole of what the kit tests exercise.
    kit_item: Item | None = None
    #: Units of `stock_item` per kit.
    kit_per_unit: Decimal = Decimal(2)
    #: A **second stock item, with a weight**, so the landed cost's `weight` basis has
    #: something it can succeed on. `stock_item` deliberately keeps no weight — it is what
    #: `weight_missing` is proved against — so a pool of one item could only ever exercise the
    #: refusal, and a census would show the guard "reached" while the basis itself was never
    #: once posted. Both items in the pool is what makes that distinction real.
    weighted_item: Item | None = None
    #: A depot in a branch of its own. The accrual is proved **per branch**, so a company with
    #: one branch can never exercise that half of the proof: every posting lands in the same
    #: bucket and a rule that used the wrong branch would look perfectly correct.
    depot: Warehouse | None = None
    depot_branch_id: int | None = None

    @property
    def company_id(self) -> int:
        return self.inventory.company_id

    @property
    def ledger(self) -> Ledger:
        return self.inventory.ledger

    @property
    def owner(self) -> User:
        return self.inventory.owner

    @property
    def main(self) -> Warehouse:
        return self.inventory.main

    @property
    def each(self) -> Uom:
        return self.inventory.each

    @property
    def settings(self) -> GLSettings:
        return self.inventory.settings


@pytest.fixture
def order_entry(db: Session, ledger: Ledger) -> OrderEntry:
    return _build(db, build_inventory(db, ledger))


def build_order_entry(db: Session, tag: str) -> OrderEntry:
    """A tenant of its own, for the property tests.

    Hypothesis reuses a function-scoped fixture across every example it draws, so a suite
    asserted after every step would re-examine an ever-growing history and cost O(examples²).
    A tenant per example bounds each one to its own steps — the reasoning P5's `fresh_stock`
    wrote down, and the same shape.
    """
    ledger = build_ledger(
        db,
        company_name=f"Rugari Wines Ltd {tag}",
        email=f"owner+{tag}@rugari.example",
    )
    return _build(db, build_inventory(db, ledger))


def _build(db: Session, inventory: Inventory) -> OrderEntry:
    company_id = inventory.company_id
    accounts = {
        row.code: row
        for row in db.scalars(select(GLAccount).where(GLAccount.company_id == company_id))
    }
    customer = partner_masters.create_partner(
        db,
        company_id,
        # The role is implied by which code is set — there is no separate role argument.
        partner_masters.PartnerInput(name="Bralirwa Distributors", customer_code="CUST001"),
        actor=inventory.owner,
    )
    supplier = partner_masters.create_partner(
        db,
        company_id,
        partner_masters.PartnerInput(name="Kigali Glass", supplier_code="SUPP001"),
        actor=inventory.owner,
    )
    stock_item = inventory_masters.create_item(
        db,
        company_id,
        inventory_masters.ItemInput(
            code="WINE-750",
            name="Rugari Red 750ml",
            uom_category_id=inventory.count.id,
            base_uom_id=inventory.each.id,
            item_type=ItemType.STOCK,
            selling_price=Decimal(2000),
            sales_account_id=accounts["4100"].id,
            cogs_account_id=accounts["5100"].id,
        ),
        actor=inventory.owner,
    )
    service_item = inventory_masters.create_item(
        db,
        company_id,
        inventory_masters.ItemInput(
            code="DELIVERY",
            name="Delivery charge",
            uom_category_id=inventory.count.id,
            base_uom_id=inventory.each.id,
            item_type=ItemType.SERVICE,
            selling_price=Decimal(5000),
            sales_account_id=accounts["4200"].id,
            purchase_account_id=accounts["6990"].id,
        ),
        actor=inventory.owner,
    )
    kit_item = inventory_masters.create_item(
        db,
        company_id,
        inventory_masters.ItemInput(
            code="GIFT-2",
            name="Two-bottle gift pack",
            uom_category_id=inventory.count.id,
            base_uom_id=inventory.each.id,
            item_type=ItemType.KIT,
            selling_price=Decimal(3500),
            sales_account_id=accounts["4100"].id,
            cogs_account_id=accounts["5100"].id,
        ),
        actor=inventory.owner,
    )
    inventory_masters.replace_kit_components(
        db,
        company_id,
        kit_item,
        [{"component_item_id": stock_item.id, "quantity_per_kit": Decimal(2)}],
        actor=inventory.owner,
    )

    weighted_item = inventory_masters.create_item(
        db,
        company_id,
        inventory_masters.ItemInput(
            code="CASE-6",
            name="Six-bottle case",
            uom_category_id=inventory.count.id,
            base_uom_id=inventory.each.id,
            item_type=ItemType.STOCK,
            selling_price=Decimal(11000),
            sales_account_id=accounts["4100"].id,
            cogs_account_id=accounts["5100"].id,
            weight_per_base_unit=Decimal("7.5"),
        ),
        actor=inventory.owner,
    )

    depot_branch = Branch(
        company_id=company_id, code="DEPOT", name="Musanze Depot", is_main=False
    )
    db.add(depot_branch)
    db.flush()
    depot = inventory_masters.create_warehouse(
        db,
        company_id,
        code="DEP",
        name="Musanze store",
        branch_id=depot_branch.id,
        actor=inventory.owner,
    )
    db.flush()
    return OrderEntry(
        kit_item=kit_item,
        weighted_item=weighted_item,
        depot=depot,
        depot_branch_id=depot_branch.id,
        inventory=inventory,
        customer=customer,
        supplier=supplier,
        stock_item=stock_item,
        service_item=service_item,
        accounts=accounts,
    )
