"""Goods receipts — the *goods* half of the GRV two-step (P6 decision 6)."""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kernel.errors import LedgerStateError
from app.models.inventory import GrnStatus
from app.models.journal import JournalLine
from app.order_entry import grn as grn_service
from tests.order_entry.conftest import MARCH, OrderEntry

ZERO = Decimal(0)


def _receive(db: Session, fixture: OrderEntry, quantity: str, unit_cost: str, **extra):  # noqa: ANN202
    return grn_service.post_grn(
        db,
        fixture.company_id,
        grn_service.GrnInput(
            partner_id=fixture.supplier.id,
            grn_date=MARCH,
            description="March delivery",
            warehouse_id=fixture.main.id,
            lines=(
                grn_service.GrnLineInput(
                    item_id=fixture.stock_item.id,
                    quantity=Decimal(quantity),
                    unit_cost=Decimal(unit_cost),
                ),
            ),
            **extra,
        ),
        actor=fixture.owner,
    )[0]


def _amounts_by_account(db: Session, fixture: OrderEntry, entry_id: int) -> dict[str, Decimal]:
    """Base amounts on one entry, keyed by account code."""
    code_by_id = {account.id: code for code, account in fixture.accounts.items()}
    totals: dict[str, Decimal] = {}
    for line in db.scalars(select(JournalLine).where(JournalLine.entry_id == entry_id)):
        code = code_by_id[line.gl_account_id]
        totals[code] = totals.get(code, ZERO) + line.base_amount
    return totals


def test_a_receipt_raises_stock_and_credits_the_accrual(
    db: Session, order_entry: OrderEntry
) -> None:
    """The whole of the two-step's first half: the goods are ours and we owe for them, and
    the accrual is where "we owe for them" lives until the invoice arrives."""
    grn = _receive(db, order_entry, "60", "1000")

    assert grn.number.startswith("GRN-")
    assert grn.status == GrnStatus.RECEIVED
    assert grn.journal_entry_id is not None
    line = grn.lines[0]
    assert line.base_quantity == Decimal(60)
    assert line.value == Decimal(60_000)

    amounts = _amounts_by_account(db, order_entry, grn.journal_entry_id)
    assert amounts["1300"] == Decimal(60_000)
    assert amounts["2350"] == Decimal(-60_000)


def test_every_accrual_line_carries_its_item(db: Session, order_entry: OrderEntry) -> None:
    """Decision 5's dimension rule, and the reason P5's contra had to learn about items: the
    VN008 guard refuses a line on the accrual naming no item, so a receipt posts one accrual
    line per item rather than one aggregate across them."""
    grn = _receive(db, order_entry, "10", "100")

    accrual_lines = db.scalars(
        select(JournalLine).where(
            JournalLine.entry_id == grn.journal_entry_id,
            JournalLine.gl_account_id == order_entry.settings.grn_accrual_account_id,
        )
    ).all()

    assert accrual_lines, "the receipt posted nothing to the accrual"
    for line in accrual_lines:
        assert line.item_id is not None, "an accrual line with no item is refused by VN008"


def test_a_kit_cannot_be_received(db: Session, order_entry: OrderEntry) -> None:
    from app.inventory import masters as inventory_masters
    from app.models.inventory import ItemType

    kit = inventory_masters.create_item(
        db,
        order_entry.company_id,
        inventory_masters.ItemInput(
            code="GIFT-01",
            name="Gift pack",
            uom_category_id=order_entry.inventory.count.id,
            base_uom_id=order_entry.each.id,
            item_type=ItemType.KIT,
        ),
        actor=order_entry.owner,
    )
    db.flush()

    with pytest.raises(LedgerStateError) as error:
        grn_service.post_grn(
            db,
            order_entry.company_id,
            grn_service.GrnInput(
                partner_id=order_entry.supplier.id,
                grn_date=MARCH,
                description="Kit receipt",
                warehouse_id=order_entry.main.id,
                lines=(
                    grn_service.GrnLineInput(
                        item_id=kit.id, quantity=Decimal(1), unit_cost=Decimal(100)
                    ),
                ),
            ),
            actor=order_entry.owner,
        )

    assert error.value.code == "kit_not_purchasable"


def test_an_unmatched_receipt_reverses(db: Session, order_entry: OrderEntry) -> None:
    grn = _receive(db, order_entry, "10", "500")

    grn_service.reverse_grn(
        db, grn, on_date=MARCH, reason="Delivered to the wrong branch", actor=order_entry.owner
    )

    assert grn.status == GrnStatus.REVERSED
    assert grn.reversal_entry_id is not None
