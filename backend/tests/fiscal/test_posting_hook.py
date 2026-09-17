"""The posting contract: what a fiscalized AR document must be, and the row it leaves.

Two halves, matching the code.

**The happy path** — an invoice posts, and before `post_document` returned there was a `sale`
row on the device's queue carrying the authority's invoice number, the payload frozen from the
document as posted, and an `item` row ahead of it for every item RRA had not been told about.

**The refusal table.** Nine refusals, each with a test that takes exactly one thing away from a
document that would otherwise post, and each **proven sensitive** by the fact that the same
document posts when the thing is put back. That is the shape the phase asks for: a refusal test
that passes because the document was malformed in some other way proves nothing.
"""

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.kernel.errors import PostingError
from app.models.fiscalization import (
    FiscalItem,
    FiscalOutboxKind,
    FiscalOutboxRow,
    FiscalOutboxStatus,
    FiscalTaxType,
    PaymentMethod,
)
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind, PartnerDocument
from app.subledger import documents as documents_service
from tests.fiscal.conftest import FiscalPosting
from tests.fiscal.helpers import (
    MARCH,
    PURCHASE_CODE,
    credit_note,
    invoice,
    line_of,
    receive,
)
from tests.fiscal.invariants import assert_fiscal_invariants


def _rows(db: Session, company_id: int) -> list[FiscalOutboxRow]:
    return list(
        db.scalars(
            select(FiscalOutboxRow)
            .where(FiscalOutboxRow.company_id == company_id)
            .order_by(FiscalOutboxRow.sequence_no)
        )
    )


# --- The row a posting leaves ----------------------------------------------------------------


def test_posting_an_invoice_queues_its_sale_in_the_same_transaction(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """The guarantee the whole phase turns on: the row exists before `post_document` returns,
    so it commits with the document or not at all."""
    receive(fiscal_posting, db)

    document = invoice(fiscal_posting, db)

    rows = _rows(db, fiscal_posting.company_id)
    sale = next(row for row in rows if row.kind == FiscalOutboxKind.SALE)
    assert sale.status == FiscalOutboxStatus.QUEUED
    assert sale.source_doc_id == document.id
    assert sale.invc_no == 1
    assert sale.sequence_no == sale.id, "creation order is queue order"
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_the_item_is_registered_ahead_of_the_sale_that_names_it(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """RRA rejects a sale naming an item it has never been told about. Nothing sequences the
    two calls by hand — the `item` row is written first, and creation order is queue order."""
    receive(fiscal_posting, db)

    invoice(fiscal_posting, db)

    rows = _rows(db, fiscal_posting.company_id)
    kinds = [row.kind for row in rows]
    assert kinds == [FiscalOutboxKind.ITEM, FiscalOutboxKind.SALE]
    registered = db.scalars(
        select(FiscalItem).where(FiscalItem.company_id == fiscal_posting.company_id)
    ).one()
    assert registered.item_cd.startswith("RW2NTXU"), registered.item_cd
    assert registered.item_cd.endswith("0000001")


def test_a_second_sale_of_the_same_item_registers_it_again_only_if_it_changed(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """The change detector is a hash of exactly what RRA holds. Selling the same item twice
    costs no call; renaming it costs one."""
    receive(fiscal_posting, db, quantity="200")
    invoice(fiscal_posting, db)
    invoice(fiscal_posting, db)

    item_rows = [
        row for row in _rows(db, fiscal_posting.company_id) if row.kind == FiscalOutboxKind.ITEM
    ]
    assert len(item_rows) == 1, "an unchanged item is not re-registered"

    fiscal_posting.stock_item.name = "Rugari Red 750ml (new label)"
    db.flush()
    invoice(fiscal_posting, db)

    item_rows = [
        row for row in _rows(db, fiscal_posting.company_id) if row.kind == FiscalOutboxKind.ITEM
    ]
    assert len(item_rows) == 2, "a rename changes what RRA holds, so it is re-registered"


def test_selling_the_same_item_at_a_different_price_does_not_re_register_it(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """What RRA holds is the item's **catalogue** price, not what one line was sold at.

    The registered price is part of the hash, so a line price here would queue an `item` row
    on every sale at a new figure — a shop that negotiates would spend its queue telling RRA
    about its own discounts. Found by reading the map against decision 8 rather than by a
    failing test, which is why it has one now.
    """
    receive(fiscal_posting, db, quantity="300")
    invoice(fiscal_posting, db)
    invoice(
        fiscal_posting,
        db,
        lines=(
            documents_service.LineInput(
                item_id=fiscal_posting.stock_item.id,
                quantity=Decimal(10),
                unit_price=Decimal(1750),
                tax_code_id=fiscal_posting.tax_codes["VAT-OUT-18"].id,
            ),
        ),
    )

    item_rows = [
        row for row in _rows(db, fiscal_posting.company_id) if row.kind == FiscalOutboxKind.ITEM
    ]
    assert len(item_rows) == 1
    # 2 000 catalogue, exclusive, standard-rated: 2 000 x 1.18 = 2 360.
    assert item_rows[0].payload["dftPrc"] == 2360


def test_the_frozen_payload_carries_the_posted_figures(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """Ten bottles at 2 000 exclusive, standard-rated. Worked by hand:

        inclusive unit price  2 000 x 1.18 = 2 360.00
        supply                2 360.00 x 10 = 23 600.00
        tax                   23 600 x 18/118 = 3 600.00

    The posted line agrees — net 20 000, tax 3 600 — which is the point: the payload is the
    posting, converted to the wire's two decimals, not a second opinion about it.
    """
    receive(fiscal_posting, db)

    document = invoice(fiscal_posting, db)

    sale = next(
        row for row in _rows(db, fiscal_posting.company_id) if row.kind == FiscalOutboxKind.SALE
    )
    # Numbers, not strings: `NUMBER 18,2` goes on the wire as an unquoted JSON number, and
    # integral where the value is — which on a zero-decimal base currency is nearly always.
    assert sale.payload["totTaxblAmt"] == 23600
    assert sale.payload["totTaxAmt"] == 3600
    assert sale.payload["taxblAmtB"] == 23600
    assert sale.payload["itemList"][0]["prc"] == 2360
    assert sale.payload["custTin"] == "100000001"
    assert sale.payload["prcOrdCd"] == PURCHASE_CODE
    assert document.net_amount == Decimal(20000)
    assert document.tax_amount == Decimal(3600)


def test_the_decision_6_residue_worked_by_hand(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """The residue decision 6 names, as one line with the arithmetic written out.

    Five units at 313 exclusive, standard-rated, on a base currency with no minor unit:

        posted   net  = 5 x 313                    = 1 565
                 tax  = round(1 565 x 0.18)        =   282      (281.70, to the franc)
                 gross                             = 1 847
        wire     prc  = round(313 x 1.18, 2)       =   369.34
                 splyAmt = round(369.34 x 5, 2)    = 1 846.70
                 taxAmt  = round(1 846.70 x 18/118, 2) = 281.70

    So the wire is **0.30 below** the ledger on this line, and that is not a defect in either:
    the ledger rounded a franc-denominated tax to the franc and the wire is a two-decimal
    field. It is here as a literal because the property census counts these by bucket, and a
    census whose buckets were wrong would report the residue as zero and nobody would know.
    """
    receive(fiscal_posting, db)

    document = invoice(
        fiscal_posting,
        db,
        lines=(
            documents_service.LineInput(
                item_id=fiscal_posting.stock_item.id,
                quantity=Decimal(5),
                unit_price=Decimal(313),
                tax_code_id=fiscal_posting.tax_codes["VAT-OUT-18"].id,
            ),
        ),
    )

    assert document.net_amount == Decimal(1565)
    assert document.tax_amount == Decimal(282)
    assert line_of(document).gross_amount == Decimal(1847)

    sale = next(
        row for row in _rows(db, fiscal_posting.company_id) if row.kind == FiscalOutboxKind.SALE
    )
    item = sale.payload["itemList"][0]
    assert item["prc"] == 369.34
    assert item["splyAmt"] == 1846.70
    assert item["taxblAmt"] == 1846.70
    assert item["taxAmt"] == 281.70
    assert Decimal(str(item["taxblAmt"])) - line_of(document).gross_amount == Decimal("-0.30")


def test_a_walk_in_sale_needs_no_purchase_code_and_defaults_to_cash(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    receive(fiscal_posting, db)

    document = invoice(
        fiscal_posting, db, partner_id=fiscal_posting.walk_in.id, purchase_code=None
    )

    sale = next(
        row for row in _rows(db, fiscal_posting.company_id) if row.kind == FiscalOutboxKind.SALE
    )
    assert document.payment_method == PaymentMethod.CASH
    assert sale.payload["pmtTyCd"] == "01"
    assert "custTin" not in sale.payload


def test_a_credit_note_is_a_refund_naming_the_invoice_it_returns(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    receive(fiscal_posting, db)
    original = invoice(fiscal_posting, db)

    credit_note(
        fiscal_posting,
        db,
        lines=(
            documents_service.LineInput(
                item_id=fiscal_posting.stock_item.id,
                quantity=Decimal(2),
                unit_price=Decimal(2000),
                tax_code_id=fiscal_posting.tax_codes["VAT-OUT-18"].id,
                returns_line_id=line_of(original).id,
            ),
        ),
    )

    refund = next(
        row
        for row in _rows(db, fiscal_posting.company_id)
        if row.kind == FiscalOutboxKind.REFUND
    )
    assert refund.payload["rcptTyCd"] == "R"
    assert refund.payload["orgInvcNo"] == 1
    assert refund.payload["rfdRsnCd"] == "06"
    assert refund.invc_no == 2, "the refund takes the next number in the device's run"
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_non_fiscalized_company_queues_nothing(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """The negative control the whole hook rests on: a company with no active device posts
    exactly as P6 left it, with no fiscal row and no refusal."""
    from app.fiscal import devices as device_service

    receive(fiscal_posting, db)
    device_service.suspend(
        db,
        fiscal_posting.company_id,
        fiscal_posting.device,
        reason="not fiscalizing yet",
        actor=fiscal_posting.owner,
    )

    invoice(fiscal_posting, db, purchase_code=None)

    assert _rows(db, fiscal_posting.company_id) == []


def test_a_journal_batch_is_not_a_sale(db: Session, fiscal_posting: FiscalPosting) -> None:
    """`ARJN` documents are opening balances and corrections. Registering one as a sale would
    put a balance brought forward on a customer's receipt."""
    document, _ = documents_service.post_document(
        db,
        fiscal_posting.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            transaction_type="JNL",
            partner_id=fiscal_posting.customer.id,
            document_date=MARCH,
            description="Opening balance",
            lines=(
                documents_service.LineInput(
                    unit_price=Decimal(5000),
                    gl_account_id=fiscal_posting.order.accounts["4100"].id,
                ),
            ),
        ),
        actor=fiscal_posting.owner,
    )

    assert document.doc_type == "ARJN"
    assert _rows(db, fiscal_posting.company_id) == []


# --- The refusal table -----------------------------------------------------------------------
#
# Each of these takes exactly one thing away from a document that posts, and each is paired
# with the fact that it posts when the thing is there. The `posts_when_...` half is what makes
# the refusal half mean something: without it a test would pass over a document that was
# malformed in a way nobody checked.


def test_an_invoice_on_a_branch_with_no_device_is_refused(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    receive(fiscal_posting, db)

    with pytest.raises(PostingError) as refusal:
        invoice(fiscal_posting, db, branch_id=fiscal_posting.order.depot_branch_id)

    assert refusal.value.code == "fiscal_device_missing"
    assert "branch_id" in refusal.value.field_errors


def test_a_gl_only_line_is_refused(db: Session, fiscal_posting: FiscalPosting) -> None:
    with pytest.raises(PostingError) as refusal:
        invoice(
            fiscal_posting,
            db,
            lines=(
                documents_service.LineInput(
                    unit_price=Decimal(5000),
                    gl_account_id=fiscal_posting.order.accounts["4100"].id,
                    tax_code_id=fiscal_posting.tax_codes["VAT-OUT-18"].id,
                ),
            ),
        )

    assert refusal.value.code == "fiscal_item_required"
    assert "lines.0.item_id" in refusal.value.field_errors


def test_an_item_with_no_ebm_class_is_refused(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    receive(fiscal_posting, db)
    fiscal_posting.stock_item.fiscal_class_code = None
    db.flush()

    with pytest.raises(PostingError) as refusal:
        invoice(fiscal_posting, db)

    assert refusal.value.code == "fiscal_class_missing"
    assert "lines.0.item_id" in refusal.value.field_errors

    fiscal_posting.stock_item.fiscal_class_code = "5059020800"
    db.flush()
    assert invoice(fiscal_posting, db) is not None, "the same document posts once the class is"


def test_a_unit_with_no_ebm_quantity_unit_is_refused(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    receive(fiscal_posting, db)
    fiscal_posting.order.each.fiscal_quantity_unit = None
    db.flush()

    with pytest.raises(PostingError) as refusal:
        invoice(fiscal_posting, db)

    assert refusal.value.code == "fiscal_uom_unmapped"
    assert "lines.0.uom_id" in refusal.value.field_errors

    fiscal_posting.order.each.fiscal_quantity_unit = "U"
    db.flush()
    assert invoice(fiscal_posting, db) is not None


def test_a_tax_code_with_no_ebm_class_is_refused(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    receive(fiscal_posting, db)
    fiscal_posting.tax_codes["VAT-OUT-18"].fiscal_tax_type = None
    db.flush()

    with pytest.raises(PostingError) as refusal:
        invoice(fiscal_posting, db)

    assert refusal.value.code == "tax_class_unmapped"
    assert "lines.0.tax_code_id" in refusal.value.field_errors

    fiscal_posting.tax_codes["VAT-OUT-18"].fiscal_tax_type = FiscalTaxType.B
    db.flush()
    assert invoice(fiscal_posting, db) is not None


def test_a_sale_to_a_customer_with_a_tin_needs_a_purchase_code(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    receive(fiscal_posting, db)

    with pytest.raises(PostingError) as refusal:
        invoice(fiscal_posting, db, purchase_code=None)

    assert refusal.value.code == "purchase_code_required"
    assert "purchase_code" in refusal.value.field_errors

    assert invoice(fiscal_posting, db) is not None, "with the code, the same document posts"


def test_a_credit_note_naming_no_original_is_refused(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    receive(fiscal_posting, db)
    invoice(fiscal_posting, db)

    goodwill = (
        documents_service.LineInput(
            item_id=fiscal_posting.stock_item.id,
            quantity=Decimal(1),
            unit_price=Decimal(2000),
            tax_code_id=fiscal_posting.tax_codes["VAT-OUT-18"].id,
        ),
    )
    with pytest.raises(PostingError) as refusal:
        credit_note(fiscal_posting, db, lines=goodwill)

    assert refusal.value.code == "refund_original_required"
    assert "refund_of_document_id" in refusal.value.field_errors


def test_a_credit_note_returning_two_invoices_is_refused(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    receive(fiscal_posting, db, quantity="200")
    first = invoice(fiscal_posting, db)
    second = invoice(fiscal_posting, db)

    with pytest.raises(PostingError) as refusal:
        credit_note(
            fiscal_posting,
            db,
            lines=(
                documents_service.LineInput(
                    item_id=fiscal_posting.stock_item.id,
                    quantity=Decimal(1),
                    unit_price=Decimal(2000),
                    tax_code_id=fiscal_posting.tax_codes["VAT-OUT-18"].id,
                    returns_line_id=line_of(first).id,
                ),
                documents_service.LineInput(
                    item_id=fiscal_posting.stock_item.id,
                    quantity=Decimal(1),
                    unit_price=Decimal(2000),
                    tax_code_id=fiscal_posting.tax_codes["VAT-OUT-18"].id,
                    returns_line_id=line_of(second).id,
                ),
            ),
        )

    assert refusal.value.code == "refund_spans_invoices"


def test_a_credit_note_returning_more_than_was_invoiced_is_refused(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """Cumulative, not per document: two credits of six against a line of ten is the case a
    per-document check waves through."""
    receive(fiscal_posting, db)
    original = invoice(fiscal_posting, db)
    returned = line_of(original).id

    def six() -> tuple[documents_service.LineInput, ...]:
        return (
            documents_service.LineInput(
                item_id=fiscal_posting.stock_item.id,
                quantity=Decimal(6),
                unit_price=Decimal(2000),
                tax_code_id=fiscal_posting.tax_codes["VAT-OUT-18"].id,
                returns_line_id=returned,
            ),
        )

    credit_note(fiscal_posting, db, lines=six())
    with pytest.raises(PostingError) as refusal:
        credit_note(fiscal_posting, db, lines=six())

    assert refusal.value.code == "refund_exceeds_original"
    assert "lines.0.quantity" in refusal.value.field_errors


def test_a_credit_note_with_no_reason_code_is_refused(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    receive(fiscal_posting, db)
    original = invoice(fiscal_posting, db)
    lines = (
        documents_service.LineInput(
            item_id=fiscal_posting.stock_item.id,
            quantity=Decimal(1),
            unit_price=Decimal(2000),
            tax_code_id=fiscal_posting.tax_codes["VAT-OUT-18"].id,
            returns_line_id=line_of(original).id,
        ),
    )

    with pytest.raises(PostingError) as refusal:
        credit_note(fiscal_posting, db, lines=lines, refund_reason=None)

    assert refusal.value.code == "refund_reason_required"
    assert "refund_reason" in refusal.value.field_errors

    assert credit_note(fiscal_posting, db, lines=lines) is not None


def test_nothing_was_written_by_a_refused_posting(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """The premise the placement of the hook rests on: the refusals run **before** the
    companion stock entry, the credit-limit audit and the journal, so a document that cannot
    be fiscalized costs a `422` rather than a rollback."""
    receive(fiscal_posting, db)
    rows_before = len(_rows(db, fiscal_posting.company_id))
    documents_before = db.scalar(select(func.count()).select_from(PartnerDocument))

    with pytest.raises(PostingError):
        invoice(fiscal_posting, db, purchase_code=None)

    assert len(_rows(db, fiscal_posting.company_id)) == rows_before
    assert db.scalar(select(func.count()).select_from(PartnerDocument)) == documents_before
