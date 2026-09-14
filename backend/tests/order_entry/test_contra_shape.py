"""The contra leg's shape, pinned on both sides of the rule that governs it.

P5 aggregates a document's contra lines by (account, branch, project): four adjustment lines
against one expense account post **one** contra line, and that line carries no item, because
an aggregate across items could not name one truthfully.

P6's GRN accrual is the first contra account that is itself a control account. The VN008
branch of `kernel_check_subledger_line` demands an item on every line touching such an
account, and the accrual proof is stated per GRN line — so an aggregate there would be both
refused by the database and unprovable if it were not.

Both halves are asserted here, in one file, because the rule is the *difference* between them.
A change that made every contra item-bearing would fix the second assertion and break the
first, and a reader who saw only one of them would not know that was a trade.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory import documents as inventory_documents
from app.inventory import masters as inventory_masters
from app.models.inventory import ItemType
from app.models.journal import JournalLine
from app.order_entry import grn as grn_service
from tests.order_entry.conftest import MARCH, OrderEntry

ZERO = Decimal(0)


def _second_item(db: Session, fixture: OrderEntry):  # noqa: ANN202
    item = inventory_masters.create_item(
        db,
        fixture.company_id,
        inventory_masters.ItemInput(
            code="WINE-375",
            name="Rugari Red 375ml",
            uom_category_id=fixture.inventory.count.id,
            base_uom_id=fixture.each.id,
            item_type=ItemType.STOCK,
        ),
        actor=fixture.owner,
    )
    db.flush()
    return item


def _lines_on(db: Session, entry_id: int, account_id: int) -> list[JournalLine]:
    return list(
        db.scalars(
            select(JournalLine).where(
                JournalLine.entry_id == entry_id, JournalLine.gl_account_id == account_id
            )
        )
    )


def test_a_two_item_adjustment_still_posts_one_contra_line_with_no_item(
    db: Session, order_entry: OrderEntry
) -> None:
    """The P5 shape, unchanged. The adjustment account is an ordinary expense account, so the
    two lines aggregate into one contra and that contra names no item — there is no single
    item it belongs to, and inventing one would be a dimension that says something untrue."""
    other = _second_item(db, order_entry)
    adjustment_type = order_entry.inventory.transaction_types["ADJIN"]

    document, _ = inventory_documents.post_document(
        db,
        order_entry.company_id,
        inventory_documents.DocumentInput(
            document_date=MARCH,
            description="Opening two items",
            transaction_type_id=adjustment_type.id,
            lines=(
                inventory_documents.DocumentLineInput(
                    item_id=order_entry.stock_item.id,
                    warehouse_id=order_entry.main.id,
                    quantity=Decimal(10),
                    unit_cost=Decimal(100),
                ),
                inventory_documents.DocumentLineInput(
                    item_id=other.id,
                    warehouse_id=order_entry.main.id,
                    quantity=Decimal(5),
                    unit_cost=Decimal(200),
                ),
            ),
        ),
        doc_type=inventory_documents.DocType.INV_JOURNAL,
        actor=order_entry.owner,
    )

    contra = _lines_on(
        db, document.journal_entry_id, order_entry.settings.inventory_adjustment_account_id
    )
    assert len(contra) == 1, "P5 aggregates an ordinary contra across items"
    assert contra[0].item_id is None
    assert contra[0].base_amount == Decimal(-2_000)


def test_a_two_item_receipt_posts_one_accrual_line_per_item(
    db: Session, order_entry: OrderEntry
) -> None:
    """The P6 shape. The accrual is a control account, so every line on it names its item and
    the two do not aggregate — which is what makes the per-GRN-line accrual proof possible,
    and what VN008 refuses to let us forget."""
    other = _second_item(db, order_entry)

    grn, _ = grn_service.post_grn(
        db,
        order_entry.company_id,
        grn_service.GrnInput(
            partner_id=order_entry.supplier.id,
            grn_date=MARCH,
            description="Two items received",
            warehouse_id=order_entry.main.id,
            lines=(
                grn_service.GrnLineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(10),
                    unit_cost=Decimal(100),
                ),
                grn_service.GrnLineInput(
                    item_id=other.id, quantity=Decimal(5), unit_cost=Decimal(200)
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    accrual = _lines_on(
        db, grn.journal_entry_id, order_entry.settings.grn_accrual_account_id
    )
    assert len(accrual) == 2, "an item-required contra splits by item"
    assert {line.item_id for line in accrual} == {order_entry.stock_item.id, other.id}
    by_item = {line.item_id: line.base_amount for line in accrual}
    assert by_item[order_entry.stock_item.id] == Decimal(-1_000)
    assert by_item[other.id] == Decimal(-1_000)
    # And the two still sum to what the inventory side received, so the split is a split and
    # not an extra franc from anywhere.
    assert sum(by_item.values()) == Decimal(-2_000)
