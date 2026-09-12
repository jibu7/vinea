"""Inventory fixtures: the kernel ledger plus what the Rwanda seed pack already put in it —
four UoM categories with their base units, the Main and in-transit warehouses, and the six
inventory transaction types."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory import masters
from app.inventory import stock as stock_service
from app.kernel.sequences import DocType
from app.models.gl import GLSettings, GLTransactionType
from app.models.inventory import (
    INVENTORY_MODULE,
    Item,
    NegativeStockPolicy,
    Uom,
    UomCategory,
    Warehouse,
)
from app.models.user import User
from tests.kernel.conftest import Ledger, build_ledger
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
    return build_inventory(db, ledger)


def build_inventory(db: Session, ledger: Ledger) -> Inventory:
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


@dataclass
class Stock:
    """An inventory fixture with something to move: one stock item, a second warehouse in a
    second branch, and the transaction types the seed provides.

    Depot sits in Musanze rather than in the main branch on purpose — the per-branch half of
    `assert_stock_invariants` is vacuous when every warehouse shares a branch, and a transfer
    that crosses branches is the shape the branch dimension exists for.
    """

    inventory: Inventory
    item: Item
    depot: Warehouse

    @property
    def company_id(self) -> int:
        return self.inventory.company_id

    @property
    def owner(self) -> User:
        return self.inventory.owner

    @property
    def main(self) -> Warehouse:
        return self.inventory.main

    @property
    def transit(self) -> Warehouse:
        return self.inventory.transit

    def type_id(self, code: str) -> int:
        return self.inventory.transaction_types[code].id

    def ledger_account(self, code: str) -> int:
        return self.inventory.ledger.acct(code)


@pytest.fixture
def stock(db: Session, inventory: Inventory) -> Stock:
    return build_stock(db, inventory)


def build_stock(db: Session, inventory: Inventory) -> Stock:
    item = masters.create_item(
        db,
        inventory.company_id,
        masters.ItemInput(
            code="WINE-750",
            name="Rugari Red 750ml",
            uom_category_id=inventory.count.id,
            base_uom_id=inventory.each.id,
        ),
        actor=inventory.owner,
    )
    depot = masters.create_warehouse(
        db,
        inventory.company_id,
        code="DEPOT",
        name="Musanze Depot",
        branch_id=inventory.ledger.branches["MUS"].id,
        actor=inventory.owner,
    )
    db.flush()
    return Stock(inventory=inventory, item=item, depot=depot)


def document(
    doc_type: str,
    on: date,
    *,
    transaction_type_id: int | None = None,
    description: str = "stock movement",
    idempotency_key: str | None = None,
    source_doc_type: str | None = None,
    source_doc_id: int | None = None,
) -> stock_service.StockDocument:
    return stock_service.StockDocument(
        doc_type=doc_type,
        move_date=on,
        description=description,
        transaction_type_id=transaction_type_id,
        idempotency_key=idempotency_key,
        source_doc_type=source_doc_type,
        source_doc_id=source_doc_id,
    )


def receive(
    db: Session,
    fixture: Stock,
    *,
    quantity: Decimal,
    unit_cost: Decimal,
    warehouse: Warehouse | None = None,
    on: date,
    type_code: str = "ADJIN",
    doc_type: str = DocType.INV_ADJUSTMENT,
    idempotency_key: str | None = None,
) -> stock_service.StockPosting:
    return stock_service.receive_stock(
        db,
        fixture.company_id,
        document=document(
            doc_type,
            on,
            transaction_type_id=fixture.type_id(type_code),
            idempotency_key=idempotency_key,
        ),
        lines=[
            stock_service.StockLine(
                item_id=fixture.item.id,
                warehouse_id=(warehouse or fixture.main).id,
                quantity=quantity,
                unit_cost=unit_cost,
            )
        ],
        actor=fixture.owner,
    )


def issue(
    db: Session,
    fixture: Stock,
    *,
    quantity: Decimal,
    warehouse: Warehouse | None = None,
    on: date,
    type_code: str = "ADJOUT",
    doc_type: str = DocType.INV_ADJUSTMENT,
) -> stock_service.StockPosting:
    return stock_service.issue_stock(
        db,
        fixture.company_id,
        document=document(doc_type, on, transaction_type_id=fixture.type_id(type_code)),
        lines=[
            stock_service.StockLine(
                item_id=fixture.item.id,
                warehouse_id=(warehouse or fixture.main).id,
                quantity=quantity,
            )
        ],
        actor=fixture.owner,
    )


def transfer_now(
    db: Session,
    fixture: Stock,
    *,
    quantity: Decimal,
    source: Warehouse,
    destination: Warehouse,
    on: date,
) -> tuple[stock_service.StockPosting, stock_service.StockPosting]:
    """Both legs, one transaction — the "Transfer now" action of decision 6."""
    leg = [stock_service.TransferLine(item_id=fixture.item.id, quantity=quantity)]
    dispatch = stock_service.transfer_stock(
        db,
        fixture.company_id,
        document=document(
            DocType.INV_TRANSFER, on, transaction_type_id=fixture.type_id("TRF"),
            description="transfer dispatch",
        ),
        lines=leg,
        from_warehouse_id=source.id,
        to_warehouse_id=fixture.transit.id,
        actor=fixture.owner,
    )
    arrival = stock_service.transfer_stock(
        db,
        fixture.company_id,
        document=document(
            DocType.INV_TRANSFER, on, transaction_type_id=fixture.type_id("TRF"),
            description="transfer receive",
        ),
        lines=leg,
        from_warehouse_id=fixture.transit.id,
        to_warehouse_id=destination.id,
        actor=fixture.owner,
    )
    return dispatch, arrival


def set_policy(db: Session, fixture: Stock, policy: NegativeStockPolicy) -> None:
    fixture.inventory.settings.negative_stock_policy = policy
    db.flush()


def fresh_stock(db: Session, tag: str) -> Stock:
    """A tenant of its own — ledger, inventory masters, item and Depot — under a name and an
    email unique to `tag`.

    For the property tests. Hypothesis reuses a function-scoped fixture across every example
    it draws, so a suite asserted after every step would re-examine an ever-growing history
    and cost O(examples²); measured at 150 examples that was 6½ minutes and climbing. A tenant
    per example bounds each one to its own steps, which is what "after every step" was
    supposed to mean.
    """
    ledger = build_ledger(
        db,
        company_name=f"Kigali Traders Ltd {tag}",
        email=f"owner+{tag}@kigali.example",
    )
    return build_stock(db, build_inventory(db, ledger))
