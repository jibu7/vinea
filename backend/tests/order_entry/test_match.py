"""The three-way match (P6 decision 6): what the invoice relieves, and what it varies by."""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kernel.errors import LedgerStateError
from app.models.inventory import GrnStatus
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


def _invoice(db: Session, fixture: OrderEntry, grn_line_id: int, quantity: str, price: str):  # noqa: ANN202
    document, _ = documents_service.post_document(
        db,
        fixture.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=fixture.supplier.id,
            document_date=MARCH,
            description="Supplier invoice",
            lines=(
                documents_service.LineInput(
                    item_id=fixture.stock_item.id,
                    quantity=Decimal(quantity),
                    unit_price=Decimal(price),
                    grn_line_id=grn_line_id,
                ),
            ),
        ),
        actor=fixture.owner,
    )
    return document


def test_a_full_match_at_the_receipt_price_clears_the_accrual_and_moves_no_stock(
    db: Session, order_entry: OrderEntry
) -> None:
    grn = _receive(db, order_entry, "60", "1000")

    invoice = _invoice(db, order_entry, grn.lines[0].id, "60", "1000")

    amounts = _amounts(db, order_entry, invoice.journal_entry_id)
    assert amounts["2350"] == Decimal(60_000), "the accrual comes off for what was received"
    assert amounts["2100"] == Decimal(-60_000)
    assert "5300" not in amounts, "no variance when the price did not move"
    # The goods arrived on the GRN; the invoice only says what they cost.
    assert invoice.stock_entry_id is None
    assert invoice.lines[0].accrual_relieved == Decimal(60_000)

    db.refresh(grn)
    assert grn_service.refresh_status(db, grn) == GrnStatus.MATCHED


def test_a_price_movement_lands_in_purchase_price_variance(
    db: Session, order_entry: OrderEntry
) -> None:
    """The accrual comes off for exactly what the receipt put there; the disagreement between
    reckoning and billing is what PPV is for."""
    grn = _receive(db, order_entry, "60", "1000")

    invoice = _invoice(db, order_entry, grn.lines[0].id, "60", "1020")

    amounts = _amounts(db, order_entry, invoice.journal_entry_id)
    assert amounts["2350"] == Decimal(60_000), "relieved at the accrued value, not the billed one"
    assert amounts["5300"] == Decimal(1_200), "60 x 20 of price movement"
    assert amounts["2100"] == Decimal(-61_200)


def test_two_partial_matches_relieve_exactly_what_was_accrued(
    db: Session, order_entry: OrderEntry
) -> None:
    """The rounding rule: the match that completes a line takes what is left, so a fully
    matched line relieves to the franc however the shares fell."""
    grn = _receive(db, order_entry, "3", "333.333333")
    accrued = grn.lines[0].value

    first = _invoice(db, order_entry, grn.lines[0].id, "1", "333")
    second = _invoice(db, order_entry, grn.lines[0].id, "1", "333")
    third = _invoice(db, order_entry, grn.lines[0].id, "1", "333")

    relieved = sum(
        document.lines[0].accrual_relieved for document in (first, second, third)
    )
    assert relieved == accrued, "a fully matched line relieves exactly what it accrued"

    db.refresh(grn)
    assert grn_service.refresh_status(db, grn) == GrnStatus.MATCHED


def test_matching_more_than_was_received_is_refused(
    db: Session, order_entry: OrderEntry
) -> None:
    grn = _receive(db, order_entry, "10", "100")
    _invoice(db, order_entry, grn.lines[0].id, "6", "100")

    with pytest.raises(LedgerStateError) as error:
        _invoice(db, order_entry, grn.lines[0].id, "5", "100")

    assert error.value.code == "match_exceeds_receipt"


def test_a_matched_receipt_refuses_reversal(db: Session, order_entry: OrderEntry) -> None:
    """The invoice that matched it has already relieved part of the accrual; reversing the
    receipt underneath would leave a relief for goods that were never received."""
    grn = _receive(db, order_entry, "10", "100")
    _invoice(db, order_entry, grn.lines[0].id, "4", "100")

    with pytest.raises(LedgerStateError) as error:
        grn_service.reverse_grn(
            db, grn, on_date=MARCH, reason="Keyed twice", actor=order_entry.owner
        )

    assert error.value.code == "grn_matched"


def test_a_return_to_supplier_debits_the_accrual_at_the_issued_cost(
    db: Session, order_entry: OrderEntry
) -> None:
    """The fourth row of decision 2's posting map.

    Goods go back: the stock side issues them at the **average**, because that is what they
    cost us, and the accrual is debited for exactly that. The supplier is credited with what
    we are *claiming* — the price we are asking back — and the difference between the two is
    purchase price variance, the same account a price movement on the way in lands in.
    """
    _receive(db, order_entry, "10", "1000")

    debit_note, _ = documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.CREDIT_NOTE,
            partner_id=order_entry.supplier.id,
            document_date=MARCH,
            description="Five returned as cracked",
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(5),
                    unit_price=Decimal(1_020),
                    warehouse_id=order_entry.main.id,
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    # Companion: the goods leave at the 1,000 average, against the accrual.
    companion = _amounts(db, order_entry, debit_note.stock_entry_id)
    assert companion["1300"] == Decimal(-5_000), "stock leaves at what it cost"
    assert companion["2350"] == Decimal(5_000), "the accrual takes it back"

    # Partner side: the supplier is credited with the 5,100 claimed.
    partner = _amounts(db, order_entry, debit_note.journal_entry_id)
    assert partner["2100"] == Decimal(5_100)


def test_reversing_a_matched_invoice_reopens_the_grn_line(
    db: Session, order_entry: OrderEntry
) -> None:
    """Decision 6's last clause: a matched invoice reverses like any other P4 document, and
    the GRN line's matched quantity falls **by construction** — nothing recalculates it and
    nothing has to remember to.

    `matched` is a query over posted, unreversed invoice lines, so the reversal that changes
    the document's status is the same act that changes the receipt's. The accrual goes back up
    by exactly what that invoice relieved, and the GRN becomes matchable again.
    """
    grn = _receive(db, order_entry, "10", "1000")
    line_id = grn.lines[0].id

    invoice = _invoice(db, order_entry, line_id, "10", "1000")
    db.refresh(grn)
    assert grn_service.refresh_status(db, grn) == GrnStatus.MATCHED
    assert grn_service.matched_quantities(db, order_entry.company_id, [line_id])[
        line_id
    ] == Decimal(10)

    documents_service.reverse_document(
        db, invoice, on_date=MARCH, reason="Billed against the wrong receipt",
        actor=order_entry.owner,
    )

    # The line is open again, with nothing having been recomputed.
    assert grn_service.matched_quantities(db, order_entry.company_id, [line_id]).get(
        line_id, ZERO
    ) == ZERO
    assert grn_service.refresh_status(db, grn) == GrnStatus.RECEIVED

    # And it can be matched afresh — the whole point of reopening it.
    again = _invoice(db, order_entry, line_id, "10", "1000")
    assert again.lines[0].accrual_relieved == Decimal(10_000)
    assert grn_service.refresh_status(db, grn) == GrnStatus.MATCHED
