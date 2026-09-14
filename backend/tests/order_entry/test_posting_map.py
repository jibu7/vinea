"""The rows of decision 2 that the other files leave uncovered.

Kept together because each is a *bullet of the decision* rather than a feature: the negative
-stock policy on a sale, the credit note that names no line it returns, and the supplier
invoice that arrives with no receipt behind it.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kernel.errors import LedgerStateError, PostingError
from app.models.inventory import NegativeStockPolicy, StockMove
from app.models.journal import JournalLine
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


def _receive(db: Session, fixture: OrderEntry, quantity: str, unit_cost: str):  # noqa: ANN202
    grn, _ = grn_service.post_grn(
        db,
        fixture.company_id,
        grn_service.GrnInput(
            partner_id=fixture.supplier.id,
            grn_date=MARCH,
            description="Receipt",
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
    return grn


def _sell(db: Session, fixture: OrderEntry, quantity: str):  # noqa: ANN202
    document, _ = documents_service.post_document(
        db,
        fixture.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=fixture.customer.id,
            document_date=MARCH,
            description="Sale",
            lines=(
                documents_service.LineInput(
                    item_id=fixture.stock_item.id,
                    quantity=Decimal(quantity),
                    warehouse_id=fixture.main.id,
                ),
            ),
        ),
        actor=fixture.owner,
    )
    return document


def test_under_block_an_oversold_invoice_is_refused_whole(
    db: Session, order_entry: OrderEntry
) -> None:
    """Decision 2: `insufficient_stock` lands on the line's quantity and the **whole document**
    is refused — no partner entry, no companion, nothing half-written."""
    _receive(db, order_entry, "10", "1000")
    order_entry.settings.negative_stock_policy = NegativeStockPolicy.BLOCK
    db.flush()

    with pytest.raises((LedgerStateError, PostingError)) as error:
        _sell(db, order_entry, "25")

    assert error.value.code == "insufficient_stock"
    assert any("quantity" in key for key in (error.value.field_errors or {})), (
        error.value.field_errors
    )


def test_under_allow_the_oversold_move_is_flagged_provisional(
    db: Session, order_entry: OrderEntry
) -> None:
    """The other half of decision 2's policy branch: the sale goes through, costed at the last
    positive average, and the move carries the flag that is its review trail."""
    _receive(db, order_entry, "10", "1000")
    order_entry.settings.negative_stock_policy = NegativeStockPolicy.ALLOW
    db.flush()

    document = _sell(db, order_entry, "25")

    moves = list(
        db.scalars(
            select(StockMove).where(
                StockMove.company_id == order_entry.company_id,
                StockMove.journal_entry_id == document.stock_entry_id,
            )
        )
    )
    assert moves, "the sale posted no move"
    assert any(move.cost_provisional for move in moves), (
        "an issue that took the location below zero must be flagged"
    )


def test_a_credit_note_with_no_returned_line_comes_back_at_the_average(
    db: Session, order_entry: OrderEntry
) -> None:
    """Decision 2's "at the current average otherwise". A goodwill credit, or a return nobody
    could tie to an invoice line, still has to post — and has to be valued at something the
    ledger can defend. The deep property pass found this path refusing outright."""
    _receive(db, order_entry, "10", "1000")
    _receive(db, order_entry, "10", "3000")
    # 20 units worth 40,000 → an average of 2,000.

    credit, _ = documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.CREDIT_NOTE,
            partner_id=order_entry.customer.id,
            document_date=MARCH,
            description="Goodwill credit, no line named",
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(2),
                    warehouse_id=order_entry.main.id,
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    companion = _amounts(db, order_entry, credit.stock_entry_id)
    assert companion["1300"] == Decimal(4_000), "2 at the 2,000 average"
    assert companion["5100"] == Decimal(-4_000)


def test_a_supplier_invoice_with_no_receipt_behind_it_nets_the_accrual_to_zero(
    db: Session, order_entry: OrderEntry
) -> None:
    """Decision 2's unmatched-purchase row. Goods and bill arrive together, so the receipt and
    the relief happen inside one document: the companion credits the accrual as the stock
    lands and the partner side debits it for the same value, leaving nothing behind."""
    invoice, _ = documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=order_entry.supplier.id,
            document_date=MARCH,
            description="Goods billed on arrival",
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(10),
                    unit_price=Decimal(1_200),
                    warehouse_id=order_entry.main.id,
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    assert invoice.stock_entry_id is not None, "no GRN link means the goods arrive here"
    companion = _amounts(db, order_entry, invoice.stock_entry_id)
    assert companion["1300"] == Decimal(12_000), "stock in at the invoice's net cost"
    assert companion["2350"] == Decimal(-12_000)

    partner = _amounts(db, order_entry, invoice.journal_entry_id)
    assert partner["2350"] == Decimal(12_000), "and straight back out again"
    assert partner["2100"] == Decimal(-12_000)

    # The whole point: across both entries the accrual is untouched.
    accrual = ZERO
    for line in db.scalars(
        select(JournalLine).where(
            JournalLine.company_id == order_entry.company_id,
            JournalLine.gl_account_id == order_entry.settings.grn_accrual_account_id,
        )
    ):
        accrual += line.base_amount
    assert accrual == ZERO
