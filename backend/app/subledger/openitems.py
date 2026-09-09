"""Open items — derived, and provably so (P4 decision 3).

`partner_documents.open_amount` is a stored column written only by the allocation service.
`verify_open_items` recomputes every document from `allocation_lines` and reports drift, the
same contract `period_balances` has with `journal_lines`. Anything that asks "what was open
on date X" calls `open_items_as_of`, which never reads the stored column.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.kernel.money import base_currency, round_amount
from app.models.partner import PartnerRole
from app.models.subledger import Allocation, AllocationLine, DocumentStatus, PartnerDocument

ZERO = Decimal(0)


def _slices(
    db: Session, company_id: int, *, as_of: date | None = None
) -> dict[int, list[Decimal]]:
    """Every amount taken off a document, kept **as individual slices** rather than a total.

    Base-currency rounding is not additive, and the ledger rounds each slice: an allocation
    posts `round(amount × rate)` per side, a discount `round(discount × rate)`. Summing first
    and rounding once would disagree with the control account by up to half a minor unit per
    slice, which is exactly the drift `assert_subledger_invariants` refuses to tolerate.
    """
    slices: dict[int, list[Decimal]] = defaultdict(list)
    statement = (
        select(
            AllocationLine.debit_document_id,
            AllocationLine.credit_document_id,
            AllocationLine.amount,
            AllocationLine.discount_document_id,
            AllocationLine.discount_amount,
        )
        .join(Allocation, Allocation.id == AllocationLine.allocation_id)
        .where(AllocationLine.company_id == company_id)
    )
    if as_of is not None:
        statement = statement.where(Allocation.allocation_date <= as_of)
    for debit_id, credit_id, amount, discount_id, discount in db.execute(statement).all():
        slices[debit_id].append(amount)
        slices[credit_id].append(amount)
        if discount_id is not None:
            slices[discount_id].append(discount)
    return slices


def recompute_open_amount(db: Session, document: PartnerDocument) -> Decimal:
    """One document's open amount, straight from the allocation lines that touch it."""
    if document.status == DocumentStatus.REVERSED:
        return ZERO
    allocated = db.scalar(
        select(func.coalesce(func.sum(AllocationLine.amount), ZERO)).where(
            AllocationLine.company_id == document.company_id,
            (AllocationLine.debit_document_id == document.id)
            | (AllocationLine.credit_document_id == document.id),
        )
    )
    discounted = db.scalar(
        select(func.coalesce(func.sum(AllocationLine.discount_amount), ZERO)).where(
            AllocationLine.company_id == document.company_id,
            AllocationLine.discount_document_id == document.id,
        )
    )
    return document.total_amount - (allocated or ZERO) - (discounted or ZERO)


@dataclass(frozen=True)
class OpenItemDrift:
    document_id: int
    number: str
    stored: Decimal
    recomputed: Decimal


def verify_open_items(db: Session, company_id: int) -> list[OpenItemDrift]:
    """Empty list means every stored `open_amount` equals its recomputation."""
    slices = _slices(db, company_id)
    drift: list[OpenItemDrift] = []
    for document in db.scalars(
        select(PartnerDocument).where(PartnerDocument.company_id == company_id)
    ):
        expected = (
            ZERO
            if document.status == DocumentStatus.REVERSED
            else document.total_amount - sum(slices.get(document.id, []), ZERO)
        )
        if document.open_amount != expected:
            drift.append(
                OpenItemDrift(document.id, document.number, document.open_amount, expected)
            )
    return drift


@dataclass(frozen=True)
class OpenItem:
    document: PartnerDocument
    open_amount: Decimal
    # Residual of the *posted* base amount less each allocated slice converted at this
    # document's own booking rate — the same arithmetic the PostingEngine did, so this is
    # exactly the document's remaining contribution to the control account.
    open_base_amount: Decimal = field(default=ZERO)

    @property
    def signed_base_amount(self) -> Decimal:
        return self.open_base_amount * self.document.direction


def _open_base(
    document: PartnerDocument, taken: list[Decimal], base_decimal_places: int
) -> Decimal:
    return document.base_total_amount - sum(
        (
            round_amount(amount * document.exchange_rate, base_decimal_places)
            for amount in taken
        ),
        ZERO,
    )


def open_items_as_of(
    db: Session,
    company_id: int,
    *,
    role: PartnerRole | None = None,
    partner_id: int | None = None,
    as_of: date,
    include_settled: bool = False,
) -> list[OpenItem]:
    """Reconstructs the open-item position on `as_of`: documents dated on or before it, net
    of allocations dated on or before it. A document reversed *after* `as_of` was still open
    then, so its reversal is ignored."""
    statement = select(PartnerDocument).where(
        PartnerDocument.company_id == company_id,
        PartnerDocument.document_date <= as_of,
    )
    if role is not None:
        statement = statement.where(PartnerDocument.role == role)
    if partner_id is not None:
        statement = statement.where(PartnerDocument.partner_id == partner_id)
    documents = list(
        db.scalars(statement.order_by(PartnerDocument.document_date, PartnerDocument.id))
    )
    slices = _slices(db, company_id, as_of=as_of)
    places = base_currency(db, company_id).decimal_places

    items: list[OpenItem] = []
    for document in documents:
        if document.reversed_on is not None and document.reversed_on <= as_of:
            continue
        taken = slices.get(document.id, [])
        open_amount = document.total_amount - sum(taken, ZERO)
        if open_amount == ZERO and not include_settled:
            continue
        items.append(
            OpenItem(
                document=document,
                open_amount=open_amount,
                open_base_amount=_open_base(document, taken, places),
            )
        )
    return items


def partner_balance_base(
    db: Session, company_id: int, role: PartnerRole, partner_id: int, *, as_of: date
) -> Decimal:
    return sum(
        (
            item.signed_base_amount
            for item in open_items_as_of(
                db, company_id, role=role, partner_id=partner_id, as_of=as_of
            )
        ),
        ZERO,
    )
