"""The companion stock entry (P6 decision 2) — two entries, one transaction."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.journal import JournalEntry, JournalLine
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind
from app.order_entry import grn as grn_service
from app.subledger import documents as documents_service
from tests.order_entry.conftest import MARCH, OrderEntry

ZERO = Decimal(0)


def _amounts(db: Session, fixture: OrderEntry, entry_id: int) -> dict[str, Decimal]:
    code_by_id = {account.id: code for code, account in fixture.accounts.items()}
    totals: dict[str, Decimal] = {}
    for line in db.scalars(select(JournalLine).where(JournalLine.entry_id == entry_id)):
        code = code_by_id[line.gl_account_id]
        totals[code] = totals.get(code, ZERO) + line.base_amount
    return totals


def _stock_on_hand(db: Session, fixture: OrderEntry, quantity: str, unit_cost: str) -> None:
    grn_service.post_grn(
        db,
        fixture.company_id,
        grn_service.GrnInput(
            partner_id=fixture.supplier.id,
            grn_date=MARCH,
            description="Opening receipt",
            warehouse_id=fixture.main.id,
            lines=(
                grn_service.GrnLineInput(
                    item_id=fixture.stock_item.id,
                    quantity=Decimal(quantity),
                    unit_cost=Decimal(unit_cost),
                ),
            ),
        ),
        actor=fixture.owner,
    )


def test_a_sale_posts_two_entries_and_the_companion_carries_the_cost(
    db: Session, order_entry: OrderEntry
) -> None:
    """Order-to-cash through one post: revenue and the receivable on the partner side, cost
    and the inventory relief on the companion, linked by `stock_entry_id`."""
    _stock_on_hand(db, order_entry, "100", "1000")

    document, _ = documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=order_entry.customer.id,
            document_date=MARCH,
            description="Sale of 30",
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(30),
                    warehouse_id=order_entry.main.id,
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    # Partner side: 30 x 2,000 from the catalogue.
    partner = _amounts(db, order_entry, document.journal_entry_id)
    assert partner["1200"] == Decimal(60_000)
    assert partner["4100"] == Decimal(-60_000)

    # Companion: 30 at the average of 1,000.
    assert document.stock_entry_id is not None
    companion = _amounts(db, order_entry, document.stock_entry_id)
    assert companion["5100"] == Decimal(30_000)
    assert companion["1300"] == Decimal(-30_000)

    # Its own run, not the invoice's number.
    entry = db.get(JournalEntry, document.stock_entry_id)
    assert entry.number.startswith("STK-")
    assert entry.module == "inv"


def test_a_document_with_no_stock_line_has_no_companion(
    db: Session, order_entry: OrderEntry
) -> None:
    """No stock line carried value, so there is no entry to write and no `STK-` number is
    claimed — a number with nothing behind it would be a hole in the run."""
    document, _ = documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=order_entry.customer.id,
            document_date=MARCH,
            description="Services only",
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.service_item.id, quantity=Decimal(1)
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    assert document.stock_entry_id is None


def test_a_customer_return_comes_back_at_the_cost_it_left_at(
    db: Session, order_entry: OrderEntry
) -> None:
    """Decision 2's returns rule. The average moves after the sale; the return must still
    come back at what *this* sale issued, or the difference becomes profit nobody earned."""
    _stock_on_hand(db, order_entry, "100", "1000")
    invoice, _ = documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=order_entry.customer.id,
            document_date=MARCH,
            description="Sale at a 1,000 average",
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(10),
                    warehouse_id=order_entry.main.id,
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    # A dearer receipt moves the average well above what the sale issued at.
    _stock_on_hand(db, order_entry, "100", "3000")

    credit, _ = documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.CREDIT_NOTE,
            partner_id=order_entry.customer.id,
            document_date=MARCH,
            description="Five returned",
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(5),
                    warehouse_id=order_entry.main.id,
                    returns_line_id=invoice.lines[0].id,
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    companion = _amounts(db, order_entry, credit.stock_entry_id)
    # 5 at the issued 1,000 — not at the 2,000-ish average the second receipt created.
    assert companion["1300"] == Decimal(5_000)
    assert companion["5100"] == Decimal(-5_000)


def test_the_companion_names_why_the_stock_left(db: Session, order_entry: OrderEntry) -> None:
    """Decision 13, on the entry rather than in a comment.

    `journal_entries.event_type` is the column that answers "why did this stock move", and
    before this step every companion issue answered `stock_issued` — a sale and a return to
    supplier were indistinguishable in the ledger. `StockSold` is the real class now (the stub
    that carried ADR-05's name is gone), and the return to supplier deliberately does **not**
    use it: naming that one `stock_sold` would put a false answer in the column.
    """
    _stock_on_hand(db, order_entry, "100", "1000")

    sale, _ = documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=order_entry.customer.id,
            document_date=MARCH,
            description="Sale",
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(10),
                    unit_price=Decimal(2000),
                    warehouse_id=order_entry.main.id,
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    returned, _ = documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.CREDIT_NOTE,
            partner_id=order_entry.supplier.id,
            document_date=MARCH,
            description="Return to supplier",
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(5),
                    unit_price=Decimal(1000),
                    warehouse_id=order_entry.main.id,
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    def event_type_of(document) -> str:  # noqa: ANN001
        entry = db.get(JournalEntry, document.stock_entry_id)
        return entry.event_type

    assert event_type_of(sale) == "stock_sold"
    assert event_type_of(returned) == "stock_issued"
    # Both are companions on the same document type: the label is the only difference.
    assert db.get(JournalEntry, sale.stock_entry_id).doc_type == "STK"
    assert db.get(JournalEntry, returned.stock_entry_id).doc_type == "STK"
