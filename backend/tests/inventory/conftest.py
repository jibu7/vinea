"""Inventory fixtures: the kernel ledger plus what the Rwanda seed pack already put in it —
four UoM categories with their base units, the Main and in-transit warehouses, and the six
inventory transaction types."""

from dataclasses import dataclass

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.gl import GLSettings, GLTransactionType
from app.models.inventory import INVENTORY_MODULE, Uom, UomCategory, Warehouse
from app.models.user import User
from tests.kernel.conftest import Ledger
from tests.kernel.conftest import ledger as ledger  # noqa: PLC0414 - re-exported fixture


@dataclass
class Inventory:
    ledger: Ledger
    categories: dict[str, UomCategory]
    base_uoms: dict[str, Uom]
    warehouses: dict[str, Warehouse]
    transaction_types: dict[str, GLTransactionType]
    settings: GLSettings

    @property
    def company_id(self) -> int:
        return self.ledger.company_id

    @property
    def owner(self) -> User:
        return self.ledger.owner

    @property
    def main(self) -> Warehouse:
        return self.warehouses["MAIN"]

    @property
    def transit(self) -> Warehouse:
        return self.warehouses["TRANSIT"]

    @property
    def count(self) -> UomCategory:
        return self.categories["COUNT"]

    @property
    def each(self) -> Uom:
        return self.base_uoms["COUNT"]


@pytest.fixture
def inventory(db: Session, ledger: Ledger) -> Inventory:
    company_id = ledger.company_id
    categories = {
        row.code: row
        for row in db.scalars(select(UomCategory).where(UomCategory.company_id == company_id))
    }
    code_by_category_id = {category.id: code for code, category in categories.items()}
    base_uoms = {
        code_by_category_id[row.category_id]: row
        for row in db.scalars(select(Uom).where(Uom.company_id == company_id, Uom.is_base))
    }
    warehouses = {
        row.code: row
        for row in db.scalars(select(Warehouse).where(Warehouse.company_id == company_id))
    }
    transaction_types = {
        row.code: row
        for row in db.scalars(
            select(GLTransactionType).where(
                GLTransactionType.company_id == company_id,
                GLTransactionType.module == INVENTORY_MODULE,
            )
        )
    }
    settings = db.scalars(select(GLSettings).where(GLSettings.company_id == company_id)).one()
    return Inventory(
        ledger=ledger,
        categories=categories,
        base_uoms=base_uoms,
        warehouses=warehouses,
        transaction_types=transaction_types,
        settings=settings,
    )
