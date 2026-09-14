"""Item lines on partner documents (P6 decision 1) — the defaults, before any stock moves."""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kernel.errors import LedgerStateError
from app.models.journal import JournalLine
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind
from app.subledger import documents as documents_service
from tests.order_entry.conftest import MARCH, OrderEntry

ZERO = Decimal(0)


def _post(db: Session, fixture: OrderEntry, role: PartnerRole, kind: DocumentKind, *lines):  # noqa: ANN202
    partner = fixture.customer if role == PartnerRole.AR else fixture.supplier
    return documents_service.post_document(
        db,
        fixture.company_id,
        role,
        documents_service.DocumentInput(
            kind=kind,
            partner_id=partner.id,
            document_date=MARCH,
            description="Item line document",
            lines=tuple(lines),
        ),
        actor=fixture.owner,
    )[0]


def _amounts(db: Session, fixture: OrderEntry, entry_id: int) -> dict[str, Decimal]:
    code_by_id = {account.id: code for code, account in fixture.accounts.items()}
    totals: dict[str, Decimal] = {}
    for line in db.scalars(select(JournalLine).where(JournalLine.entry_id == entry_id)):
        code = code_by_id[line.gl_account_id]
        totals[code] = totals.get(code, ZERO) + line.base_amount
    return totals


def test_a_service_item_line_takes_its_price_and_account_from_the_catalogue(
    db: Session, order_entry: OrderEntry
) -> None:
    """The 3rd link of the ADR-05 chain, on the AR side: the line names an item and nothing
    else, and the price and the revenue account both come from the catalogue."""
    document = _post(
        db,
        order_entry,
        PartnerRole.AR,
        DocumentKind.INVOICE,
        documents_service.LineInput(item_id=order_entry.service_item.id, quantity=Decimal(2)),
    )

    # 2 x 5,000 from `items.selling_price`.
    assert document.total_amount == Decimal(10_000)
    line = document.lines[0]
    assert line.item_id == order_entry.service_item.id
    assert line.base_quantity == Decimal(2)
    # Credited to the item's own sales account, not the transaction type's default.
    assert _amounts(db, order_entry, document.journal_entry_id)["4200"] == Decimal(-10_000)


def test_an_ap_service_line_expenses_to_the_items_purchase_account(
    db: Session, order_entry: OrderEntry
) -> None:
    """A service has no goods behind it, so there is nothing accrued to relieve — it
    expenses, which is the branch of decision 1 that distinguishes it from a stock line."""
    document = _post(
        db,
        order_entry,
        PartnerRole.AP,
        DocumentKind.INVOICE,
        documents_service.LineInput(
            item_id=order_entry.service_item.id, quantity=Decimal(1), unit_price=Decimal(7_000)
        ),
    )

    assert _amounts(db, order_entry, document.journal_entry_id)["6990"] == Decimal(7_000)


def test_a_kit_cannot_be_purchased(db: Session, order_entry: OrderEntry) -> None:
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
            selling_price=Decimal(3_500),
        ),
        actor=order_entry.owner,
    )
    db.flush()

    with pytest.raises(LedgerStateError) as error:
        _post(
            db,
            order_entry,
            PartnerRole.AP,
            DocumentKind.INVOICE,
            documents_service.LineInput(
                item_id=kit.id, quantity=Decimal(1), unit_price=Decimal(100)
            ),
        )

    assert error.value.code == "kit_not_purchasable"


def test_a_gl_line_and_an_item_line_coexist_on_one_document(
    db: Session, order_entry: OrderEntry
) -> None:
    """What an invoice with a delivery charge on it looks like — and the reason decision 2
    insists on one code path rather than a second service for item documents."""
    document = _post(
        db,
        order_entry,
        PartnerRole.AR,
        DocumentKind.INVOICE,
        documents_service.LineInput(item_id=order_entry.service_item.id, quantity=Decimal(1)),
        documents_service.LineInput(
            unit_price=Decimal(1_500), gl_account_id=order_entry.accounts["4300"].id
        ),
    )

    assert document.total_amount == Decimal(6_500)
    assert [line.item_id for line in document.lines] == [order_entry.service_item.id, None]
