"""Fixtures for the P6 posting contract: a company with stock masters, a supplier and a
customer, and the order-entry settings the phase reads."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory import masters as inventory_masters
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
    db.flush()
    return OrderEntry(
        inventory=inventory,
        customer=customer,
        supplier=supplier,
        stock_item=stock_item,
        service_item=service_item,
        accounts=accounts,
    )
