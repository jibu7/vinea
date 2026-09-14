"""The three-way match: what a supplier invoice line takes off the GRN accrual (decision 6).

The goods arrived on a GRN and the accrual was credited with what they were reckoned to be
worth. The invoice now says what they actually cost. The difference is **purchase price
variance**, and it is a real difference whether it came from a price that moved or a rate that
did — both are movements between reckoning and billing, and both belong in the same account.

**What a match relieves.** The GRN line's frozen value, pro rata to the quantity being
matched, rounded half-up — *except* that the match which completes a line takes whatever is
left of that line's value rather than its own rounded share. That is what makes a fully
matched line relieve exactly what it accrued, to the franc, however many invoices it took and
however the rounding fell along the way.

**Why the relieved amount is then stored and never recomputed.** A pro-rata share cannot be
re-derived once one of its siblings has been reversed. Value 1 000 received over a quantity of
3, matched 1 + 1 + 1, relieves 333 + 333 + 334; reverse the first and the ledger has relieved
667, while recomputing over the survivors gives 666. The accrual proof would be off by a franc
and would stay off. So `partner_document_lines.accrual_relieved` records what was posted, and
`assert_order_invariants` reads it rather than working it out again.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kernel.errors import LedgerStateError
from app.kernel.money import round_amount
from app.models.inventory import GoodsReceivedNote, GoodsReceivedNoteLine, GrnStatus
from app.order_entry import grn as grn_service

ZERO = Decimal(0)


def resolve_match(
    db: Session,
    company_id: int,
    *,
    index: int,
    grn_line_id: int,
    base_quantity: Decimal,
    supplier_id: int,
    decimal_places: int,
) -> tuple[GoodsReceivedNoteLine, Decimal]:
    """The GRN line this invoice line matches, and what it relieves.

    Refuses a match that would take a GRN line past what was received
    (`match_exceeds_receipt`), one against another supplier's receipt, and one against a
    receipt that has been reversed — in each case before anything is posted.
    """
    grn_line = db.scalar(
        select(GoodsReceivedNoteLine).where(
            GoodsReceivedNoteLine.company_id == company_id,
            GoodsReceivedNoteLine.id == grn_line_id,
        )
    )
    if grn_line is None:
        raise LedgerStateError(
            "That goods-received line does not exist",
            code="grn_line_not_found",
            field_errors={f"lines.{index}.grn_line_id": ["not found"]},
        )
    grn = db.scalar(
        select(GoodsReceivedNote).where(
            GoodsReceivedNote.company_id == company_id,
            GoodsReceivedNote.id == grn_line.grn_id,
        )
    )
    if grn is None or grn.partner_id != supplier_id:
        raise LedgerStateError(
            "That goods-received line belongs to another supplier",
            code="grn_supplier_mismatch",
            field_errors={f"lines.{index}.grn_line_id": ["wrong supplier"]},
        )
    if grn.status == GrnStatus.REVERSED:
        raise LedgerStateError(
            f"{grn.number} was reversed and cannot be matched",
            code="grn_reversed",
            field_errors={f"lines.{index}.grn_line_id": ["reversed"]},
        )

    already_matched = grn_service.matched_quantities(db, company_id, [grn_line.id]).get(
        grn_line.id, ZERO
    )
    if already_matched + base_quantity > grn_line.base_quantity:
        raise LedgerStateError(
            f"{grn.number} line {grn_line.line_no} received "
            f"{grn_line.base_quantity} and {already_matched} is already matched",
            code="match_exceeds_receipt",
            field_errors={f"lines.{index}.quantity": ["exceeds what was received"]},
        )

    already_relieved = grn_service.relieved_values(db, company_id, [grn_line.id]).get(
        grn_line.id, ZERO
    )
    if already_matched + base_quantity == grn_line.base_quantity:
        # The match that completes the line takes what is left, so the line relieves exactly
        # what it accrued however the rounding fell on the way.
        relieved = grn_line.value - already_relieved
    else:
        relieved = round_amount(
            grn_line.value * base_quantity / grn_line.base_quantity, decimal_places
        )
    return grn_line, relieved
