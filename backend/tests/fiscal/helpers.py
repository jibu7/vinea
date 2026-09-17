"""Posting helpers for the fiscal suite — a receipt of stock, an invoice, a credit note.

Here rather than in each test file because five files want the same three, and because the
*defaults* are part of what is being tested: an invoice keyed with a purchase code and a credit
note keyed with a reason code are what a fiscalized company's screens will send, so the helpers
send them and the refusal tests take them away one at a time.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind, PartnerDocument
from app.order_entry import grn as grn_service
from app.subledger import documents as documents_service
from tests.fiscal.conftest import FiscalPosting
from tests.kernel.conftest import YEAR

MARCH = date(YEAR, 3, 10)
APRIL = date(YEAR, 4, 10)

PURCHASE_CODE = "AB12CD"
#: §4.16 code 06, "Refund". RRA's own name for the ordinary case.
REFUND_REASON = "06"


def receive(
    fixture: FiscalPosting,
    db: Session,
    *,
    quantity: str = "100",
    unit_cost: str = "1000",
    item_id: int | None = None,
) -> object:
    """Opening stock, so a fiscalized sale is not refused by the `block` policy the device
    locked on."""
    grn, _ = grn_service.post_grn(
        db,
        fixture.company_id,
        grn_service.GrnInput(
            partner_id=fixture.order.supplier.id,
            grn_date=MARCH,
            description="Opening stock",
            warehouse_id=fixture.order.main.id,
            lines=(
                grn_service.GrnLineInput(
                    item_id=item_id or fixture.stock_item.id,
                    quantity=Decimal(quantity),
                    unit_cost=Decimal(unit_cost),
                ),
            ),
        ),
        actor=fixture.owner,
    )
    return grn


def invoice(
    fixture: FiscalPosting,
    db: Session,
    *,
    lines: tuple[documents_service.LineInput, ...] | None = None,
    partner_id: int | None = None,
    purchase_code: str | None = PURCHASE_CODE,
    document_date: date = MARCH,
    **overrides: object,
) -> PartnerDocument:
    document, _ = documents_service.post_document(
        db,
        fixture.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=partner_id or fixture.customer.id,
            document_date=document_date,
            description="Wine",
            purchase_code=purchase_code,
            lines=lines
            or (
                documents_service.LineInput(
                    item_id=fixture.stock_item.id,
                    quantity=Decimal(10),
                    unit_price=Decimal(2000),
                    tax_code_id=fixture.tax_codes["VAT-OUT-18"].id,
                ),
            ),
            **overrides,  # type: ignore[arg-type]
        ),
        actor=fixture.owner,
    )
    return document


def credit_note(
    fixture: FiscalPosting,
    db: Session,
    *,
    lines: tuple[documents_service.LineInput, ...],
    partner_id: int | None = None,
    refund_reason: str | None = REFUND_REASON,
    refund_of_document_id: int | None = None,
    document_date: date = MARCH,
    **overrides: object,
) -> PartnerDocument:
    document, _ = documents_service.post_document(
        db,
        fixture.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.CREDIT_NOTE,
            partner_id=partner_id or fixture.customer.id,
            document_date=document_date,
            description="Returned wine",
            refund_reason=refund_reason,
            refund_of_document_id=refund_of_document_id,
            purchase_code=PURCHASE_CODE,
            lines=lines,
            **overrides,  # type: ignore[arg-type]
        ),
        actor=fixture.owner,
    )
    return document


def line_of(document: PartnerDocument, line_no: int = 1) -> object:
    return next(line for line in document.lines if line.line_no == line_no)
