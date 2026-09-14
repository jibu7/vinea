"""`assert_order_invariants` against the sequences it is meant to hold through — including
the 1000-over-3 reversal that makes a recomputed relief disagree with the ledger."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.journal import JournalLine
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind
from app.order_entry import grn as grn_service
from app.subledger import documents as documents_service
from tests.order_entry.conftest import MARCH, OrderEntry
from tests.order_entry.invariants import assert_order_invariants

ZERO = Decimal(0)


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


def _match(db: Session, fixture: OrderEntry, grn_line_id: int, quantity: str, price: str):  # noqa: ANN202
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


def _accrual_balance(db: Session, fixture: OrderEntry) -> Decimal:
    total = ZERO
    for line in db.scalars(
        select(JournalLine).where(
            JournalLine.company_id == fixture.company_id,
            JournalLine.gl_account_id == fixture.settings.grn_accrual_account_id,
        )
    ):
        total += line.base_amount
    return total


def test_the_invariants_hold_through_receive_and_match(
    db: Session, order_entry: OrderEntry
) -> None:
    grn = _receive(db, order_entry, "60", "1000")
    assert_order_invariants(db, order_entry.company_id)
    assert _accrual_balance(db, order_entry) == Decimal(-60_000)

    _match(db, order_entry, grn.lines[0].id, "60", "1020")
    grn_service.refresh_status(db, grn)
    assert_order_invariants(db, order_entry.company_id)
    assert _accrual_balance(db, order_entry) == ZERO, "a fully matched receipt clears it"


def test_the_accrual_ties_out_after_the_1000_over_3_reversal(
    db: Session, order_entry: OrderEntry
) -> None:
    """The case that forced `accrual_relieved` to be stored.

    A receipt of 3 worth 1 000 matched one unit at a time relieves 333, 333, 334. Reverse the
    **first** and the ledger has relieved 667, while recomputing the surviving two as a share
    of 3 gives 666. The invariant reads what was posted, so it agrees with the ledger; a
    recomputing one would call this a franc of drift and blame the ledger for it.
    """
    grn = _receive(db, order_entry, "3", "333.333333")
    accrued = grn.lines[0].value
    assert accrued == Decimal(1_000)

    first = _match(db, order_entry, grn.lines[0].id, "1", "333")
    second = _match(db, order_entry, grn.lines[0].id, "1", "333")
    third = _match(db, order_entry, grn.lines[0].id, "1", "333")
    shares = [document.lines[0].accrual_relieved for document in (first, second, third)]
    assert sum(shares) == accrued, shares
    grn_service.refresh_status(db, grn)
    assert_order_invariants(db, order_entry.company_id)
    assert _accrual_balance(db, order_entry) == ZERO

    # Reverse the first match. What comes back is what that line posted, not a re-derived
    # share — so the accrual returns to exactly the first line's own relief.
    documents_service.reverse_document(
        db, first, on_date=MARCH, reason="Billed twice", actor=order_entry.owner
    )
    grn_service.refresh_status(db, grn)

    assert _accrual_balance(db, order_entry) == -shares[0]
    assert_order_invariants(db, order_entry.company_id)


def test_a_sale_keeps_the_invariants(db: Session, order_entry: OrderEntry) -> None:
    """The companion's 1:1 rule, exercised: a stock-bearing invoice has exactly one companion
    whose keyed moves correspond to its stock lines."""
    _receive(db, order_entry, "100", "1000")

    documents_service.post_document(
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
                    quantity=Decimal(30),
                    warehouse_id=order_entry.main.id,
                ),
                documents_service.LineInput(
                    item_id=order_entry.service_item.id, quantity=Decimal(1)
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    assert_order_invariants(db, order_entry.company_id)


def test_reversing_a_stock_bearing_document_reverses_its_companion(
    db: Session, order_entry: OrderEntry
) -> None:
    """The three-step sequence the deep property pass shrank to.

        receive 1 @ 1.00  ->  return it to the supplier  ->  reverse the return

    Reversing only the partner side left the companion's debit on the accrual standing: the
    account read zero while the unreversed receipt still said one, and the proof failed by
    exactly that franc. Both entries come back or neither does (decision 2).
    """
    _receive(db, order_entry, "1", "1.00")
    assert _accrual_balance(db, order_entry) == Decimal(-1)

    return_out, _ = documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.CREDIT_NOTE,
            partner_id=order_entry.supplier.id,
            document_date=MARCH,
            description="Returned",
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(1),
                    unit_price=Decimal("1.00"),
                    warehouse_id=order_entry.main.id,
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    assert return_out.stock_entry_id is not None
    assert_order_invariants(db, order_entry.company_id)

    documents_service.reverse_document(
        db, return_out, on_date=MARCH, reason="Keyed in error", actor=order_entry.owner
    )

    # The receipt is untouched and still unbilled, so the accrual must say so.
    assert _accrual_balance(db, order_entry) == Decimal(-1)
    assert_order_invariants(db, order_entry.company_id)
