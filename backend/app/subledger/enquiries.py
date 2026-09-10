"""Customer and supplier enquiries: documents, allocations and a running base-currency
balance, with the document → journal entry link every drill-down needs."""

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.currency import Currency
from app.models.partner import Partner, PartnerRole
from app.models.subledger import Allocation, AllocationLine, DocumentStatus, PartnerDocument
from app.subledger import masters
from app.subledger.common import exposure_direction
from app.subledger.openitems import OpenItem, open_items_as_of

ZERO = Decimal(0)


@dataclass(frozen=True)
class EnquiryEntry:
    document: PartnerDocument
    running_base: Decimal


@dataclass(frozen=True)
class PartnerEnquiry:
    role: PartnerRole
    partner: Partner
    as_of: date
    entries: list[EnquiryEntry]
    open_items: list[OpenItem]
    balance_base: Decimal
    credit_limit: Decimal | None

    @property
    def exposure_base(self) -> Decimal:
        """The balance in the *role's* own sense: what the customer owes us, what we owe the
        supplier. `balance_base` is signed by the control account's side, so AP's is negative
        — subtracting it straight from the limit made owing a supplier *increase* their
        headroom. The posting-time check in `documents.py` has always turned the sign this
        way; this is the same turn, so screen and server answer the same question."""
        return exposure_direction(self.role) * self.balance_base

    @property
    def credit_available(self) -> Decimal | None:
        if self.credit_limit is None:
            return None
        return self.credit_limit - self.exposure_base


def _with_discount(db: Session, items: list[OpenItem], *, as_of: date) -> list[OpenItem]:
    """Fill in each item's settlement discount as at `as_of`.

    Decision 6 makes the discount a fact of the *allocation date*, not of the invoice — so the
    allocation screen, which fetches this enquiry as at the date it is allocating on, is the
    only place an operator can see what is still on offer. Without it they had to know the
    terms, type a number, and find out from a refusal whether the window had closed.

    Computed here rather than in `openitems` because `max_discount` lives in `allocations`,
    which imports `openitems` — this module sits above both.
    """
    from app.subledger.allocations import max_discount

    # One lookup per currency, not one per open item: a partner with fifty invoices is one
    # currency, or two.
    currencies: dict[int, Currency] = {}
    out: list[OpenItem] = []
    for item in items:
        currency = currencies.get(item.document.currency_id)
        if currency is None:
            currency = db.get(Currency, item.document.currency_id)
            if currency is None:
                out.append(item)
                continue
            currencies[item.document.currency_id] = currency
        out.append(
            replace(
                item,
                discount_available=max_discount(db, item.document, on=as_of, currency=currency),
            )
        )
    return out


def partner_enquiry(
    db: Session,
    company_id: int,
    role: PartnerRole,
    partner_id: int,
    *,
    as_of: date,
    date_from: date | None = None,
) -> PartnerEnquiry:
    partner = masters.get_partner(db, company_id, partner_id)
    statement = select(PartnerDocument).where(
        PartnerDocument.company_id == company_id,
        PartnerDocument.role == role,
        PartnerDocument.partner_id == partner_id,
        PartnerDocument.document_date <= as_of,
    )
    if date_from is not None:
        statement = statement.where(PartnerDocument.document_date >= date_from)
    documents = list(
        db.scalars(statement.order_by(PartnerDocument.document_date, PartnerDocument.id))
    )

    entries: list[EnquiryEntry] = []
    running = ZERO
    for document in documents:
        if document.status == DocumentStatus.REVERSED and (
            document.reversed_on is not None and document.reversed_on <= as_of
        ):
            continue
        running += document.direction * document.base_total_amount
        entries.append(EnquiryEntry(document=document, running_base=running))

    open_items = _with_discount(
        db, open_items_as_of(db, company_id, role=role, partner_id=partner_id, as_of=as_of),
        as_of=as_of,
    )
    settings = masters.role_settings_or_default(db, company_id, partner_id, role)
    return PartnerEnquiry(
        role=role,
        partner=partner,
        as_of=as_of,
        entries=entries,
        open_items=open_items,
        balance_base=sum((item.signed_base_amount for item in open_items), ZERO),
        credit_limit=settings.credit_limit,
    )


@dataclass(frozen=True)
class AllocationEntry:
    allocation: Allocation
    line: AllocationLine
    debit_number: str
    credit_number: str


def partner_allocations(
    db: Session,
    company_id: int,
    role: PartnerRole,
    *,
    partner_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[AllocationEntry]:
    statement = (
        select(AllocationLine, Allocation)
        .join(Allocation, Allocation.id == AllocationLine.allocation_id)
        .where(AllocationLine.company_id == company_id, Allocation.role == role)
    )
    if partner_id is not None:
        statement = statement.where(Allocation.partner_id == partner_id)
    if date_from is not None:
        statement = statement.where(Allocation.allocation_date >= date_from)
    if date_to is not None:
        statement = statement.where(Allocation.allocation_date <= date_to)
    rows = db.execute(
        statement.order_by(Allocation.allocation_date, Allocation.id, AllocationLine.line_no)
    ).all()

    numbers: dict[int, str] = {}
    for line, _allocation in rows:
        for document_id in (line.debit_document_id, line.credit_document_id):
            if document_id not in numbers:
                document = db.get(PartnerDocument, document_id)
                numbers[document_id] = document.number if document is not None else "?"
    return [
        AllocationEntry(
            allocation=allocation,
            line=line,
            debit_number=numbers[line.debit_document_id],
            credit_number=numbers[line.credit_document_id],
        )
        for line, allocation in rows
    ]
