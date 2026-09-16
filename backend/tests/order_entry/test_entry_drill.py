"""From a journal entry, back to the document that posted it — for every P6 source.

The entry page's Reverse is not a button on a module-owned entry; it is a **link**, because
the kernel refuses a GL reversal of one (`reverse_via_module_document`) and a screen that
offered the button anyway would be offering an action it knows will be refused. So the link has
to land on the right screen, and until P6 "the right screen" was a lookup on the entry's module.

That stopped working the moment this phase arrived. A goods receipt, a landed cost, an
inventory adjustment and the companion stock entry of a stock-bearing invoice are **all** posted
by the `inv` module, and they live on four different screens. Three of them are not
`inventory_documents` rows at all, so the module-table lookup answered "no document" and the
operator was left with a greyed-out button and nowhere to go — the P4 failure mode rule 13
exists for, one phase on: the row renders, the link is missing, and nothing fails.

Every case below asserts the **routing key** as well as the id, because an id that lands on the
wrong kind of page is worse than no link: `/inventory/documents/7` for landed cost 7 opens
somebody else's document, with no sign that it is the wrong one.
"""

from decimal import Decimal

from sqlalchemy.orm import Session

from app.api.v1.gl import _module_document
from app.models.inventory import InventoryDocument, InventoryDocumentStatus
from app.models.journal import JournalEntry
from app.models.order_entry import LandedCostBasis
from app.models.partner import PartnerRole, TaxMode
from app.models.subledger import DocumentKind
from app.order_entry import grn as grn_service
from app.order_entry import landed_cost as landed_cost_service
from app.order_entry import sources as order_sources
from app.subledger import documents as documents_service
from tests.kernel.conftest import post_simple
from tests.order_entry.conftest import MARCH, OrderEntry

D = Decimal


def _entry(db: Session, entry_id: int) -> JournalEntry:
    return db.get(JournalEntry, entry_id)


def _receive(db: Session, fixture: OrderEntry, quantity: str = "100", cost: str = "1000"):  # noqa: ANN202
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
                    item_id=fixture.stock_item.id, quantity=D(quantity), unit_cost=D(cost)
                ),
            ),
        ),
        actor=fixture.owner,
    )
    db.flush()
    return grn


def test_a_goods_receipt_entry_drills_to_the_receipt(
    db: Session, order_entry: OrderEntry
) -> None:
    """`GRN-000001` is an `inv` entry and is not an inventory document.

    Before this, the module table was asked for an `inventory_documents` row with that
    `journal_entry_id`, found none, and the entry page showed a disabled Reverse with a tooltip
    naming a module and no way to reach it.
    """
    grn = _receive(db, order_entry)
    assert _module_document(db, _entry(db, grn.journal_entry_id)) == (
        grn.id,
        grn.number,
        "goods_received_note",
    )


def test_a_landed_cost_and_its_reversal_both_drill_to_the_allocation(
    db: Session, order_entry: OrderEntry
) -> None:
    """Both entries of the pair, because a reversal is a document's entry too.

    A landed cost reverses as a **split** rather than a mirror — the value it added leaves
    through cost of sales as the goods are sold — so `reverses_entry_id` is null on the
    reversing entry by design, and the document is the only thing that knows the two belong
    together. Which is exactly why the reversal needs this link: there is no other way back.
    """
    grn = _receive(db, order_entry)
    document, _ = landed_cost_service.post_landed_cost(
        db,
        order_entry.company_id,
        landed_cost_service.LandedCostInput(
            cost_date=MARCH,
            description="Freight",
            amount=D(7777),
            basis=LandedCostBasis.QUANTITY,
            grn_line_ids=(grn.lines[0].id,),
        ),
        actor=order_entry.owner,
    )
    db.flush()
    allocation_entry_id = document.journal_entry_id
    assert _module_document(db, _entry(db, allocation_entry_id)) == (
        document.id,
        document.number,
        "landed_cost_document",
    )

    landed_cost_service.reverse_landed_cost(
        db, document, on_date=MARCH, reason="Wrong consignment", actor=order_entry.owner
    )
    db.flush()
    assert document.reversal_entry_id is not None
    assert _module_document(db, _entry(db, document.reversal_entry_id)) == (
        document.id,
        document.number,
        "landed_cost_document",
    )


def test_both_entries_of_a_stock_bearing_invoice_drill_to_the_invoice(
    db: Session, order_entry: OrderEntry
) -> None:
    """A stock-bearing AR invoice posts two entries, and **both** have to lead back to it.

    The receivable side is an `ar` entry and resolves through the partner-document table as it
    always has. The companion is an `inv` entry with an `STK-` number, and the partner document
    names it in `stock_entry_id` rather than `journal_entry_id` — so no module table answers
    for it, and its source link is the only route. Reversing it from the GL would undo the
    ledger half of a sale and leave the goods sold, which is the reason the kernel refuses and
    the reason this link has to exist.

    Posted with a **kit**, so the companion is the multi-move case (decision 8): the revenue is
    the parent line's and the cost is the components'.
    """
    _receive(db, order_entry)
    invoice, _ = documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=order_entry.customer.id,
            document_date=MARCH,
            description="Gift packs",
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.kit_item.id,
                    quantity=D(4),
                    unit_price=D(3500),
                    warehouse_id=order_entry.main.id,
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    db.flush()

    expected = (invoice.id, invoice.number, "ar_document")
    assert _module_document(db, _entry(db, invoice.journal_entry_id)) == expected
    assert invoice.stock_entry_id is not None, "a kit sale moves stock"
    assert _module_document(db, _entry(db, invoice.stock_entry_id)) == expected


def test_a_supplier_invoice_drills_to_the_supplier_side(
    db: Session, order_entry: OrderEntry
) -> None:
    """The same document shape, the other subledger — and the target says which.

    `ar_document` and `ap_document` open different screens, and which one a document belongs to
    is the document's own property. A screen re-deriving it from a partner lookup would be a
    second place for the answer to live.
    """
    invoice, _ = documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=order_entry.supplier.id,
            document_date=MARCH,
            description="Delivery charge",
            tax_mode=TaxMode.EXCLUSIVE,
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.service_item.id, quantity=D(1), unit_price=D(5000)
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    db.flush()
    assert _module_document(db, _entry(db, invoice.journal_entry_id)) == (
        invoice.id,
        invoice.number,
        "ap_document",
    )


def test_a_manual_journal_has_no_document_to_drill_to(
    db: Session, order_entry: OrderEntry
) -> None:
    """A `gl` entry is reversed from the general ledger, so there is nothing to link to.

    Worth stating rather than assuming. The screen offers its Reverse dialog only while this
    comes back empty, so a resolution that reached for a document here — and any module table
    will happily answer for the wrong entry if asked with the wrong key — would take the button
    away from the one kind of entry it belongs to.
    """
    entry = post_simple(
        db,
        order_entry.ledger,
        debit="6100",
        credit="2300",
        amount=D(1000),
        on=MARCH,
        description="Manual journal",
    )
    assert entry.module == "gl"
    assert _module_document(db, entry) == (None, None, None)


def test_the_module_table_is_asked_before_the_source_link(
    db: Session, order_entry: OrderEntry
) -> None:
    """The **ordering** in `_module_document`, proven rather than asserted in a docstring.

    Two resolutions live in that function: the module's own table first, the entry's
    `source_doc_type` / `source_doc_id` second. Every entry any service can post agrees with
    itself, so the two answer the same thing and the order between them is invisible — step 8
    recorded exactly that ("the ordering itself is not observable from a test") and left it
    unproven, which is a branch nothing can see and therefore a branch nothing protects.

    An entry that **disagrees with itself** is what makes it visible, and it does not need a
    forbidden UPDATE to build. A goods receipt's entry is an `inv` entry whose source link
    names the receipt and which has no `inventory_documents` row. Give it one — a bare header
    inserted with the session, pointing at that same entry — and the entry now has a module-table
    row saying "inventory document" and a source link saying "goods receipt". Nothing rewrites
    a posted row: the receipt and its entry are untouched, and the only write is an INSERT into
    a header table that has no append-only guard on it.

    Module first means the answer is the inventory document. Swap the two blocks in
    `_module_document` and this test reads `GRN-…` instead, which is what the step-8 note said
    could not be demonstrated.
    """
    grn = _receive(db, order_entry)
    entry = _entry(db, grn.journal_entry_id)
    # The entry really is the disagreeing shape: its source link names the receipt, and that is
    # what the second resolution would return.
    assert (entry.source_doc_type, int(entry.source_doc_id)) == (
        order_sources.GOODS_RECEIVED_NOTE,
        grn.id,
    )
    assert entry.module == "inv"

    impostor = InventoryDocument(
        company_id=order_entry.company_id,
        doc_type="INAJ",
        number=f"INAJ-DRILL-{grn.id}",
        document_date=MARCH,
        description="A header claiming the receipt's entry",
        journal_entry_id=entry.id,
        status=InventoryDocumentStatus.POSTED,
    )
    db.add(impostor)
    db.flush()

    # The module table wins. Both resolutions can answer; the first one asked is the one that
    # does, and it is the module table.
    assert _module_document(db, entry) == (
        impostor.id,
        impostor.number,
        "inventory_document",
    )
    # And the source link is still sitting there naming the receipt, so the assertion above is
    # about which resolution ran and not about the second one having nothing to say.
    ref = (order_sources.GOODS_RECEIVED_NOTE, grn.id)
    resolved = order_sources.resolve(db, order_entry.company_id, [ref])[ref]
    assert (resolved.source_doc_id, resolved.number) == (grn.id, grn.number)
