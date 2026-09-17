"""Decision 9's first half: what a posted AP document declares to the authority.

A purchase is **declared, not issued**. There is no receipt to print — the supplier's own
device printed one — so the queue row comes back with an acknowledgment and nothing else, and
`assert_fiscal_invariants` reads "no receipt on a purchase row" as an invariant rather than an
omission.

Two things this file is careful about. The payload's figures are asserted as literals worked by
hand, because "the same as what the code computed" is not an assertion. And the refusals are
each proven sensitive in the same test that raises them — the missing thing is put back and the
same document posts.
"""

from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fiscal import outbox as outbox_service
from app.kernel.errors import PostingError
from app.models.fiscalization import (
    FiscalOutboxKind,
    FiscalOutboxRow,
    FiscalOutboxStatus,
    FiscalReceipt,
)
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind
from app.subledger import documents as documents_service
from tests.fiscal.conftest import DEFAULT_PURCHASE_CLASS, FiscalPosting
from tests.fiscal.helpers import (
    APRIL,
    MARCH,
    drain_to_the_sale,
    line_of,
    receive,
    return_to_supplier,
    supplier_invoice,
)
from tests.fiscal.invariants import assert_fiscal_invariants

D = Decimal

#: §4.13 — the purchase side's `rcptTyCd`.
PURCHASE = "P"
RETURN = "R"
#: §4.12 — `regTyCd`. `M` is a purchase this system originated.
MANUAL = "M"
#: §4.11 — `pchsSttsCd 02`, approved.
APPROVED = "02"


def _rows(db: Session, company_id: int) -> list[FiscalOutboxRow]:
    return list(
        db.scalars(
            select(FiscalOutboxRow)
            .where(FiscalOutboxRow.company_id == company_id)
            .order_by(FiscalOutboxRow.sequence_no)
        )
    )


def _purchases(db: Session, company_id: int) -> list[FiscalOutboxRow]:
    return [
        row for row in _rows(db, company_id) if row.kind == FiscalOutboxKind.PURCHASE
    ]


# --- The declaration --------------------------------------------------------------------------


def test_a_supplier_invoice_declares_itself_in_the_posting_transaction(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """50 bottles at 1 000, input VAT at 18 %.

    Worked by hand: net 50 000 · tax 9 000 · gross 59 000. On the wire the price carries the
    tax inside it — `prc 1 180.00` — so `splyAmt = 1 180 × 50 = 59 000`, `taxblAmt 59 000` and
    `taxAmt = 59 000 × 18/118 = 9 000`. The B bucket is the whole of it.
    """
    document = supplier_invoice(fiscal_posting, db)

    row = _purchases(db, fiscal_posting.company_id)[0]
    assert row.source_doc_type == outbox_service.DOCUMENT_SOURCE
    assert row.source_doc_id == document.id
    assert row.invc_no == 1, "the FIP run is its own, and starts at one"
    assert row.status == FiscalOutboxStatus.QUEUED

    payload = row.payload
    assert payload["regTyCd"] == MANUAL
    assert payload["pchsTyCd"] == "N"
    assert payload["rcptTyCd"] == PURCHASE
    assert payload["pchsSttsCd"] == APPROVED
    assert payload["spplrTin"] == "100000002"
    assert payload["spplrNm"] == "Kigali Glass"
    assert payload["spplrInvcNo"] == 77, "the supplier's own reference, numeric because it is"
    assert D(payload["taxblAmtB"]) == D(59_000)
    assert D(payload["taxAmtB"]) == D(9_000)
    assert D(payload["totTaxblAmt"]) == D(59_000)
    assert D(payload["totTaxAmt"]) == D(9_000)
    assert D(payload["totAmt"]) == D(59_000)

    line = payload["itemList"][0]
    assert D(line["prc"]) == D("1180.00")
    assert D(line["splyAmt"]) == D(59_000)
    assert D(line["taxblAmt"]) == D(59_000)
    assert D(line["taxAmt"]) == D(9_000)
    assert line["taxTyCd"] == "B"
    assert line["itemCd"], "an item line carries the registered code"
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_return_to_supplier_declares_the_other_receipt_type(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """`rcptTyCd R` — "refund after purchase". 5 bottles at 1 000: net 5 000, tax 900."""
    purchase = supplier_invoice(fiscal_posting, db)

    return_to_supplier(
        fiscal_posting,
        db,
        lines=(
            documents_service.LineInput(
                item_id=fiscal_posting.stock_item.id,
                quantity=D(5),
                unit_price=D(1000),
                tax_code_id=fiscal_posting.tax_codes["VAT-IN-18"].id,
                returns_line_id=line_of(purchase).id,
            ),
        ),
    )

    rows = _purchases(db, fiscal_posting.company_id)
    assert [row.payload["rcptTyCd"] for row in rows] == [PURCHASE, RETURN]
    assert [row.invc_no for row in rows] == [1, 2], "one FIP run, gapless"
    assert D(rows[1].payload["totTaxblAmt"]) == D(5_900)
    assert D(rows[1].payload["totTaxAmt"]) == D(900)
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_purchase_row_never_holds_a_receipt(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """A declaration is acknowledged, not signed: the supplier's device printed the receipt.

    The invariant that says so is "sent means signed", which asserts a receipt on a sale or a
    refund and **no receipt on anything else** — so this is the row that would break it if a
    purchase were ever treated as a sale.
    """
    supplier_invoice(fiscal_posting, db)
    from app.fiscal import drainer

    drainer.drain_company(db, fiscal_posting.company_id, client=sandbox_client)

    row = _purchases(db, fiscal_posting.company_id)[0]
    assert row.status == FiscalOutboxStatus.SENT
    assert row.last_result_cd == "000"
    assert (
        db.scalars(
            select(FiscalReceipt).where(
                FiscalReceipt.company_id == fiscal_posting.company_id
            )
        ).all()
        == []
    )
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_gl_only_line_travels_under_the_default_purchase_class(
    db: Session, fiscal_defaults: FiscalPosting
) -> None:
    """Rent and freight are the ordinary purchase line and have no item — which is exactly
    what the sale side refuses (`fiscal_item_required`).

    `itemCd` is absent, `itemClsCd` is the company's default purchase class, and `itemNm` is
    the **GL account's** name rather than the keyed description: a description often says
    "March" and tells the authority nothing about what was bought.
    """
    document = supplier_invoice(
        fiscal_defaults,
        db,
        lines=(
            documents_service.LineInput(
                gl_account_id=fiscal_defaults.order.accounts["6990"].id,
                quantity=D(1),
                unit_price=D(120_000),
                tax_code_id=fiscal_defaults.tax_codes["VAT-IN-18"].id,
                description="March",
            ),
        ),
    )

    line = _purchases(db, fiscal_defaults.company_id)[0].payload["itemList"][0]
    assert "itemCd" not in line, "optional on a purchase, and there is no item"
    assert line["itemClsCd"] == DEFAULT_PURCHASE_CLASS
    assert line["itemNm"] == fiscal_defaults.order.accounts["6990"].name
    assert D(line["qty"]) == D(1)
    # 120 000 net, grossed up once: 141 600 inclusive, of which 21 600 is the tax.
    assert D(line["prc"]) == D("141600.00")
    assert D(line["taxblAmt"]) == D(141_600)
    assert D(line["taxAmt"]) == D(21_600)
    assert document.stock_entry_id is None, "a GL line moves no stock"
    assert_fiscal_invariants(db, fiscal_defaults.company_id)


def test_a_line_with_no_class_and_no_default_is_refused(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """RRA requires a class on every purchase line, and there are two places one can come
    from. With neither, the document is refused on the field the operator can fix."""
    with pytest.raises(PostingError) as refusal:
        supplier_invoice(
            fiscal_posting,
            db,
            lines=(
                documents_service.LineInput(
                    gl_account_id=fiscal_posting.order.accounts["6990"].id,
                    quantity=D(1),
                    unit_price=D(1000),
                    tax_code_id=fiscal_posting.tax_codes["VAT-IN-18"].id,
                ),
            ),
        )
    assert refusal.value.code == "fiscal_purchase_class_missing"
    assert "lines.0.gl_account_id" in refusal.value.field_errors

    # Proven sensitive: the default set, the same document posts.
    from app.models.gl import GLSettings

    settings_row = db.scalar(
        select(GLSettings).where(GLSettings.company_id == fiscal_posting.company_id)
    )
    settings_row.fiscal_default_purchase_class_code = DEFAULT_PURCHASE_CLASS
    db.flush()
    supplier_invoice(
        fiscal_posting,
        db,
        lines=(
            documents_service.LineInput(
                gl_account_id=fiscal_posting.order.accounts["6990"].id,
                quantity=D(1),
                unit_price=D(1000),
                tax_code_id=fiscal_posting.tax_codes["VAT-IN-18"].id,
            ),
        ),
    )
    assert len(_purchases(db, fiscal_posting.company_id)) == 1


def test_a_tax_code_with_no_ebm_class_is_refused_on_a_purchase_too(
    db: Session, fiscal_defaults: FiscalPosting
) -> None:
    """The class is what RRA reports the input VAT under, so a code with none cannot be sent.

    An *absent* tax code is a different matter and is not refused — `D` is RRA's own non-VAT
    class and a cost keyed with no code is exactly that.
    """
    code = fiscal_defaults.tax_codes["VAT-IN-18"]
    code.fiscal_tax_type = None
    db.flush()

    with pytest.raises(PostingError) as refusal:
        supplier_invoice(fiscal_defaults, db)
    assert refusal.value.code == "tax_class_unmapped"
    assert "lines.0.tax_code_id" in refusal.value.field_errors

    # Proven sensitive: the class restored, the same invoice posts.
    code.fiscal_tax_type = "B"
    db.flush()
    supplier_invoice(fiscal_defaults, db)
    assert len(_purchases(db, fiscal_defaults.company_id)) == 1


def test_a_line_with_no_tax_code_is_declared_as_non_vat(
    db: Session, fiscal_defaults: FiscalPosting
) -> None:
    """`D`, not a refusal. Refusing here would stop an AP invoice over a line the authority
    has a published code for."""
    supplier_invoice(
        fiscal_defaults,
        db,
        lines=(
            documents_service.LineInput(
                gl_account_id=fiscal_defaults.order.accounts["6990"].id,
                quantity=D(1),
                unit_price=D(4000),
            ),
        ),
    )

    payload = _purchases(db, fiscal_defaults.company_id)[0].payload
    assert payload["itemList"][0]["taxTyCd"] == "D"
    assert D(payload["taxblAmtD"]) == D(4_000)
    assert D(payload["taxAmtD"]) == D(0)


# --- What does not declare --------------------------------------------------------------------


def test_a_payment_is_not_a_purchase(db: Session, fiscal_posting: FiscalPosting) -> None:
    """Paying an invoice is not buying anything. A settlement that declared itself would
    double every cash supplier's purchases."""
    supplier_invoice(fiscal_posting, db)
    before = len(_purchases(db, fiscal_posting.company_id))

    documents_service.post_document(
        db,
        fiscal_posting.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.SETTLEMENT,
            partner_id=fiscal_posting.order.supplier.id,
            document_date=MARCH,
            description="Paid",
            amount=D(10_000),
            cash_account_id=fiscal_posting.order.accounts["1120"].id,
        ),
        actor=fiscal_posting.owner,
    )

    assert len(_purchases(db, fiscal_posting.company_id)) == before
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_an_ap_journal_batch_is_not_a_purchase(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """`APJN` documents are opening balances and corrections keyed under the `JNL` transaction
    type. Declaring one would put a balance brought forward in RRA's purchase register."""
    documents_service.post_document(
        db,
        fiscal_posting.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=fiscal_posting.order.supplier.id,
            document_date=MARCH,
            description="Opening balance",
            transaction_type="JNL",
            lines=(
                documents_service.LineInput(
                    gl_account_id=fiscal_posting.order.accounts["6990"].id,
                    quantity=D(1),
                    unit_price=D(5000),
                ),
            ),
        ),
        actor=fiscal_posting.owner,
    )

    assert _purchases(db, fiscal_posting.company_id) == []
    assert_fiscal_invariants(db, fiscal_posting.company_id)


# --- Reversal ---------------------------------------------------------------------------------


def test_reversing_a_queued_declaration_cancels_its_row(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """RRA never received it, so there is nothing to undo there — the row is cancelled and the
    ordinary P4 reversal proceeds."""
    document = supplier_invoice(fiscal_posting, db)

    documents_service.reverse_document(
        db, document, on_date=APRIL, reason="keyed twice", actor=fiscal_posting.owner
    )

    row = _rows(db, fiscal_posting.company_id)
    declared = [candidate for candidate in row if candidate.kind == FiscalOutboxKind.PURCHASE]
    assert [candidate.status for candidate in declared] == [FiscalOutboxStatus.CANCELLED]
    assert "cancelled by the reversal" in declared[0].resolution_note
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_reversing_a_declared_purchase_declares_the_opposite(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """A declaration RRA acknowledged cannot be withdrawn, and unlike a refund of a refund the
    opposite *is* in the vocabulary: a reversed invoice goes back as `rcptTyCd R` for the same
    figures. So there is nothing to refuse, which is the one place the purchase side differs
    from the sale side."""
    from app.fiscal import drainer

    document = supplier_invoice(fiscal_posting, db)
    drainer.drain_company(db, fiscal_posting.company_id, client=sandbox_client)

    documents_service.reverse_document(
        db, document, on_date=APRIL, reason="returned the lot", actor=fiscal_posting.owner
    )

    declared = _purchases(db, fiscal_posting.company_id)
    assert [row.payload["rcptTyCd"] for row in declared] == [PURCHASE, RETURN]
    assert declared[1].source_doc_type == outbox_service.REVERSAL_SOURCE
    assert D(declared[1].payload["totTaxblAmt"]) == D(59_000)
    assert declared[1].invc_no == 2
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_declaration_whose_outcome_is_unknown_blocks_the_reversal(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """RRA may be holding it. Cancelling the row would leave a declaration standing and
    declaring the opposite would undo one that was never made."""
    from app.kernel.errors import LedgerStateError

    document = supplier_invoice(fiscal_posting, db)
    row = _purchases(db, fiscal_posting.company_id)[0]
    row.status = FiscalOutboxStatus.UNKNOWN
    db.flush()

    with pytest.raises(LedgerStateError) as refusal:
        documents_service.reverse_document(
            db, document, on_date=APRIL, reason="mistake", actor=fiscal_posting.owner
        )
    assert refusal.value.code == "fiscal_status_unresolved"

    # Proven sensitive: resolved back to `queued`, and the same reversal goes through.
    row.status = FiscalOutboxStatus.QUEUED
    db.flush()
    documents_service.reverse_document(
        db, document, on_date=APRIL, reason="mistake", actor=fiscal_posting.owner
    )
    assert row.status == FiscalOutboxStatus.CANCELLED


def test_a_purchase_is_declared_behind_the_item_it_names(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """Creation order is queue order, so an item registered on first fiscal use sits ahead of
    the declaration naming it — the same rule the sale side relies on."""
    document = supplier_invoice(fiscal_posting, db)

    rows = _rows(db, fiscal_posting.company_id)
    item = next(row for row in rows if row.kind == FiscalOutboxKind.ITEM)
    purchase = next(row for row in rows if row.kind == FiscalOutboxKind.PURCHASE)
    assert item.sequence_no < purchase.sequence_no
    assert purchase.source_doc_id == document.id


def test_a_purchase_of_an_already_received_item_declares_and_moves_nothing(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """A **matched** supplier invoice: the goods arrived on the goods receipt, which reported
    the movement, and the invoice only says what they cost. So it declares a purchase and
    raises no second movement — reporting one would tell RRA the stock arrived twice."""
    grn = receive(fiscal_posting, db)
    drain_to_the_sale(fiscal_posting, db, sandbox_client)

    supplier_invoice(
        fiscal_posting,
        db,
        lines=(
            documents_service.LineInput(
                item_id=fiscal_posting.stock_item.id,
                quantity=D(100),
                unit_price=D(1000),
                tax_code_id=fiscal_posting.tax_codes["VAT-IN-18"].id,
                grn_line_id=grn.lines[0].id,
            ),
        ),
    )

    rows = _rows(db, fiscal_posting.company_id)
    movements = [row for row in rows if row.kind == FiscalOutboxKind.STOCK_IO]
    assert len(movements) == 1, "the receipt's movement, and no second one for the invoice"
    assert len(_purchases(db, fiscal_posting.company_id)) == 1
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_purchase_on_a_branch_with_no_device_is_refused(
    db: Session, fiscal_defaults: FiscalPosting
) -> None:
    """The same refusal the sale side makes, in its own words: there is nothing to declare
    this purchase *to*. The depot branch has no device (the order-entry fixture puts it in a
    branch of its own) and the main branch has one."""
    with pytest.raises(PostingError) as refusal:
        supplier_invoice(
            fiscal_defaults,
            db,
            branch_id=fiscal_defaults.order.depot_branch_id,
            lines=(
                documents_service.LineInput(
                    gl_account_id=fiscal_defaults.order.accounts["6990"].id,
                    quantity=D(1),
                    unit_price=D(1000),
                    tax_code_id=fiscal_defaults.tax_codes["VAT-IN-18"].id,
                ),
            ),
        )
    assert refusal.value.code == "fiscal_device_missing"
    assert "branch_id" in refusal.value.field_errors

    # Proven sensitive: the same document on the branch that has a device.
    supplier_invoice(
        fiscal_defaults,
        db,
        lines=(
            documents_service.LineInput(
                gl_account_id=fiscal_defaults.order.accounts["6990"].id,
                quantity=D(1),
                unit_price=D(1000),
                tax_code_id=fiscal_defaults.tax_codes["VAT-IN-18"].id,
            ),
        ),
    )
    assert len(_purchases(db, fiscal_defaults.company_id)) == 1
