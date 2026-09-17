"""Decision 10: the stock report, and the sequence step 3's brief asks for.

The tape is one item through six kinds of movement and then across a branch boundary —
a goods receipt, a sale, a customer return, a supplier invoice, a return to supplier, an
adjustment, and a cross-branch transfer. What it asserts after every one is the thing that
matters: the authority's own in/out code, in the order the rows were queued, and an on-hand
snapshot that **equals `stock_balances` summed over the branch's warehouses at the moment the
row was enqueued**.

Every expected figure below is worked by hand in the comments and written as a literal. The
payload is read as RRA spells it, because the payload is the thing under test — the *build*
never spells those names outside `app/fiscal/rwanda/`, which `test_boundary.py` holds.
"""

from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fiscal import stock as fiscal_stock
from app.inventory import documents as inventory_documents
from app.inventory import masters as inventory_masters
from app.inventory import transfers as transfer_service
from app.models.fiscalization import (
    FiscalOutboxKind,
    FiscalOutboxRow,
    FiscalOutboxStatus,
)
from app.models.inventory import StockBalance, Warehouse
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind
from app.subledger import documents as documents_service
from tests.fiscal.conftest import FiscalPosting, activate_depot_device
from tests.fiscal.helpers import (
    MARCH,
    credit_note,
    invoice,
    line_of,
    receive,
    return_to_supplier,
    supplier_invoice,
)
from tests.fiscal.invariants import assert_fiscal_invariants

D = Decimal

#: §4.15, and the only place this file names a code: what each movement of the tape below is
#: reported as. Decision 10's table, read back off the wire.
PURCHASE_IN = "02"
RETURN_IN = "03"
MOVEMENT_IN = "04"
ADJUSTMENT_IN = "06"
SALE_OUT = "11"
RETURN_OUT = "12"
MOVEMENT_OUT = "13"
ADJUSTMENT_OUT = "16"


def _rows(db: Session, company_id: int, *, device_id: int | None = None) -> list[FiscalOutboxRow]:
    query = select(FiscalOutboxRow).where(FiscalOutboxRow.company_id == company_id)
    if device_id is not None:
        query = query.where(FiscalOutboxRow.device_id == device_id)
    return list(db.scalars(query.order_by(FiscalOutboxRow.sequence_no)))


def _movements(
    db: Session, company_id: int, *, device_id: int | None = None
) -> list[FiscalOutboxRow]:
    return [
        row
        for row in _rows(db, company_id, device_id=device_id)
        if row.kind == FiscalOutboxKind.STOCK_IO
    ]


def _masters(
    db: Session, company_id: int, *, device_id: int | None = None
) -> list[FiscalOutboxRow]:
    return [
        row
        for row in _rows(db, company_id, device_id=device_id)
        if row.kind == FiscalOutboxKind.STOCK_MASTER
    ]


def _on_hand(db: Session, fixture: FiscalPosting, *, item_id: int, branch_id: int) -> Decimal:
    """`stock_balances` summed over the branch's own warehouses — the figure a master must
    equal. Read straight off the cache rather than through the fiscal service, so the two are
    not the same code agreeing with itself."""
    rows = db.scalars(
        select(StockBalance).where(
            StockBalance.company_id == fixture.company_id, StockBalance.item_id == item_id
        )
    )
    total = D(0)
    for row in rows:
        warehouse = db.get(Warehouse, row.warehouse_id)
        if warehouse.branch_id == branch_id and not warehouse.is_in_transit:
            total += row.quantity
    return total


# --- The sequence -----------------------------------------------------------------------------


def test_the_movement_sequence_reports_every_code_in_order(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """GRN → sale → credit note → supplier invoice → return to supplier → adjustment →
    cross-branch transfer.

    Worked by hand, on item X at a cost of 1 000 a bottle:

    | # | movement                     | qty | on hand (main) | sarTyCd |
    |---|------------------------------|-----|----------------|---------|
    | 1 | GRN-1 receives               | 100 |            100 | 02      |
    | 2 | INV-1 sells 10               |  10 |             90 | 11      |
    | 3 | CRN-1 returns 2 of them      |   2 |             92 | 03      |
    | 4 | SIN-1 buys 50, no GRN        |  50 |            142 | 02      |
    | 5 | RTS-1 sends 5 back           |   5 |            137 | 12      |
    | 6 | ADJ-1 writes 7 off           |   7 |            130 | 16      |
    | 7 | TRF-1 dispatches 20 to depot |  20 |            110 | 13      |
    | 8 | TRF-1 arrives at the depot   |  20 |   depot 20 (*) | 04      |

    (*) on the **depot's** device, which is the whole point of row 8: the authority holds one
    stock figure per branch, so a movement between two of them is two reports.
    """
    depot_device = activate_depot_device(db, fiscal_posting, sandbox_client)
    main_branch = fiscal_posting.device.branch_id
    depot_branch = fiscal_posting.order.depot_branch_id
    item = fiscal_posting.stock_item

    # --- 1. the goods receipt -------------------------------------------------------------
    receive(fiscal_posting, db)
    _expect_movement(db, fiscal_posting, index=0, code=PURCHASE_IN, quantity=D(100))
    _expect_master(db, fiscal_posting, index=0, on_hand=D(100), item_id=item.id,
                   branch_id=main_branch)

    # --- 2. the sale ------------------------------------------------------------------------
    sale = invoice(fiscal_posting, db)
    _expect_movement(db, fiscal_posting, index=1, code=SALE_OUT, quantity=D(10))
    _expect_master(db, fiscal_posting, index=1, on_hand=D(90), item_id=item.id,
                   branch_id=main_branch)

    # --- 3. the customer return -------------------------------------------------------------
    credit_note(
        fiscal_posting,
        db,
        lines=(
            documents_service.LineInput(
                item_id=item.id,
                quantity=D(2),
                unit_price=D(2000),
                tax_code_id=fiscal_posting.tax_codes["VAT-OUT-18"].id,
                returns_line_id=line_of(sale).id,
            ),
        ),
    )
    _expect_movement(db, fiscal_posting, index=2, code=RETURN_IN, quantity=D(2))
    _expect_master(db, fiscal_posting, index=2, on_hand=D(92), item_id=item.id,
                   branch_id=main_branch)

    # --- 4. the supplier invoice, unmatched -------------------------------------------------
    purchase = supplier_invoice(fiscal_posting, db)
    _expect_movement(db, fiscal_posting, index=3, code=PURCHASE_IN, quantity=D(50))
    _expect_master(db, fiscal_posting, index=3, on_hand=D(142), item_id=item.id,
                   branch_id=main_branch)

    # --- 5. the return to supplier ----------------------------------------------------------
    return_to_supplier(
        fiscal_posting,
        db,
        lines=(
            documents_service.LineInput(
                item_id=item.id,
                quantity=D(5),
                unit_price=D(1000),
                tax_code_id=fiscal_posting.tax_codes["VAT-IN-18"].id,
                returns_line_id=line_of(purchase).id,
            ),
        ),
    )
    _expect_movement(db, fiscal_posting, index=4, code=RETURN_OUT, quantity=D(5))
    _expect_master(db, fiscal_posting, index=4, on_hand=D(137), item_id=item.id,
                   branch_id=main_branch)

    # --- 6. the adjustment ------------------------------------------------------------------
    inventory_documents.post_adjustment(
        db,
        fiscal_posting.company_id,
        inventory_documents.DocumentInput(
            document_date=MARCH,
            description="Breakage",
            lines=[
                inventory_documents.DocumentLineInput(
                    item_id=item.id,
                    warehouse_id=fiscal_posting.order.main.id,
                    quantity=D(7),
                    transaction_type_id=fiscal_posting.order.inventory.transaction_types[
                        "ADJOUT"
                    ].id,
                )
            ],
        ),
        actor=fiscal_posting.owner,
    )
    _expect_movement(db, fiscal_posting, index=5, code=ADJUSTMENT_OUT, quantity=D(7))
    _expect_master(db, fiscal_posting, index=5, on_hand=D(130), item_id=item.id,
                   branch_id=main_branch)

    # --- 7 and 8. the cross-branch transfer, both legs --------------------------------------
    transfer_service.post_transfer(
        db,
        fiscal_posting.company_id,
        transfer_service.TransferInput(
            transfer_date=MARCH,
            description="To Musanze",
            from_warehouse_id=fiscal_posting.order.main.id,
            to_warehouse_id=fiscal_posting.order.depot.id,
            lines=(
                transfer_service.TransferLineInput(item_id=item.id, quantity=D(20)),
            ),
        ),
        actor=fiscal_posting.owner,
    )
    _expect_movement(db, fiscal_posting, index=6, code=MOVEMENT_OUT, quantity=D(20))
    _expect_master(db, fiscal_posting, index=6, on_hand=D(110), item_id=item.id,
                   branch_id=main_branch)
    # The arrival is on the depot's device, whose own runs start at 1.
    arrival = _movements(db, fiscal_posting.company_id, device_id=depot_device.id)
    assert len(arrival) == 1
    assert arrival[0].payload["sarTyCd"] == MOVEMENT_IN
    assert arrival[0].sar_no == 1, "the depot device's FSAR run is its own"
    assert D(arrival[0].payload["itemList"][0]["qty"]) == D(20)
    depot_master = _masters(db, fiscal_posting.company_id, device_id=depot_device.id)[0]
    assert D(depot_master.payload["rsdQty"]) == D(20)
    assert _on_hand(db, fiscal_posting, item_id=item.id, branch_id=depot_branch) == D(20)

    assert_fiscal_invariants(db, fiscal_posting.company_id)


def _expect_movement(
    db: Session,
    fixture: FiscalPosting,
    *,
    index: int,
    code: str,
    quantity: Decimal,
) -> None:
    movements = _movements(db, fixture.company_id, device_id=fixture.device.id)
    row = movements[index]
    assert row.payload["sarTyCd"] == code, (
        f"movement {index + 1} was reported as {row.payload['sarTyCd']}, not {code}"
    )
    assert row.sar_no == index + 1, "the FSAR run is gapless and in movement order"
    assert D(row.payload["itemList"][0]["qty"]) == quantity


def _expect_master(
    db: Session,
    fixture: FiscalPosting,
    *,
    index: int,
    on_hand: Decimal,
    item_id: int,
    branch_id: int,
) -> None:
    """The snapshot, and the cache it has to agree with **at this moment**.

    Both halves: the literal worked by hand above, and `stock_balances` read independently. The
    literal alone would not catch a snapshot taken at send time rather than at enqueue; the
    cache alone would be the code agreeing with itself.
    """
    master = _masters(db, fixture.company_id, device_id=fixture.device.id)[index]
    assert D(master.payload["rsdQty"]) == on_hand
    assert _on_hand(db, fixture, item_id=item_id, branch_id=branch_id) == on_hand


# --- What is not reported ---------------------------------------------------------------------


def test_a_transfer_inside_one_branch_is_invisible_to_the_authority(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """Decision 10: no row. The branch's position is unchanged, so there is nothing to say —
    and saying it twice, out then in, would be two movements that cancel on RRA's report."""
    second = inventory_masters.create_warehouse(
        db,
        fiscal_posting.company_id,
        code="MAIN2",
        name="Main annexe",
        branch_id=fiscal_posting.device.branch_id,
        actor=fiscal_posting.owner,
    )
    receive(fiscal_posting, db)
    before = len(_movements(db, fiscal_posting.company_id))

    transfer_service.post_transfer(
        db,
        fiscal_posting.company_id,
        transfer_service.TransferInput(
            transfer_date=MARCH,
            description="Across the yard",
            from_warehouse_id=fiscal_posting.order.main.id,
            to_warehouse_id=second.id,
            lines=(
                transfer_service.TransferLineInput(
                    item_id=fiscal_posting.stock_item.id, quantity=D(5)
                ),
            ),
        ),
        actor=fiscal_posting.owner,
    )

    assert len(_movements(db, fiscal_posting.company_id)) == before
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_revaluation_moves_no_quantity_and_reports_nothing(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """Decision 10 names it: a landed-cost revaluation and every zero-quantity move report
    nothing. The authority's stock report is about quantity, and there is none."""
    receive(fiscal_posting, db)
    before = len(_movements(db, fiscal_posting.company_id))

    inventory_documents.post_adjustment(
        db,
        fiscal_posting.company_id,
        inventory_documents.DocumentInput(
            document_date=MARCH,
            description="Write the shelf down",
            lines=[
                inventory_documents.DocumentLineInput(
                    item_id=fiscal_posting.stock_item.id,
                    warehouse_id=fiscal_posting.order.main.id,
                    value=D(-1000),
                    transaction_type_id=fiscal_posting.order.inventory.transaction_types[
                        "REVAL"
                    ].id,
                )
            ],
        ),
        actor=fiscal_posting.owner,
    )

    assert len(_movements(db, fiscal_posting.company_id)) == before
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_non_fiscalized_company_reports_no_movement(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """The negative control: P5 and P6's stock service, unchanged, on a company with no active
    device."""
    from app.fiscal import devices as device_service

    device_service.suspend(
        db,
        fiscal_posting.company_id,
        fiscal_posting.device,
        reason="not fiscalizing yet",
        actor=fiscal_posting.owner,
    )
    receive(fiscal_posting, db)

    assert _rows(db, fiscal_posting.company_id) == []


def test_a_movement_is_queued_behind_the_sale_that_caused_it(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """VSDC §3.1, and the one ordering the build has to arrange rather than inherit.

    The companion stock entry posts **before** the partner side (P6 decision 2), so a movement
    reported from inside the stock service would sit ahead of the sale in the device's FIFO and
    RRA would answer `921`/`922`. Proven sensitive by moving the report back into the stock
    service: the assertion below fails, and so does invariant 11.
    """
    receive(fiscal_posting, db)
    invoice(fiscal_posting, db)

    rows = _rows(db, fiscal_posting.company_id)
    sale = next(row for row in rows if row.kind == FiscalOutboxKind.SALE)
    movement = next(
        row
        for row in rows
        if row.kind == FiscalOutboxKind.STOCK_IO
        and row.source_doc_type == fiscal_stock.PARTNER_DOCUMENT_SOURCE
    )
    assert sale.sequence_no < movement.sequence_no
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_the_reversal_of_a_receipt_is_reported_the_other_way_round(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """The facing is a side of the business and the direction is the movement's, so a mirror
    needs no case of its own: undoing a goods receipt is goods going back to the supplier."""
    from app.order_entry import grn as grn_service

    grn = receive(fiscal_posting, db)
    grn_service.reverse_grn(
        db, grn, on_date=MARCH, reason="keyed twice", actor=fiscal_posting.owner
    )

    movements = _movements(db, fiscal_posting.company_id)
    assert [row.payload["sarTyCd"] for row in movements] == [PURCHASE_IN, RETURN_OUT]
    assert D(_masters(db, fiscal_posting.company_id)[-1].payload["rsdQty"]) == D(0)
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_an_adjustment_in_and_out_on_one_document_is_two_movements(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """`sarTyCd` says which way stock went, so a posting that does both is two movements.

    A count that found one item over and another short is the case: one direction chosen for a
    figure that has two would report half of it backwards.
    """
    receive(fiscal_posting, db)
    weighted = fiscal_posting.order.weighted_item
    weighted.fiscal_class_code = "5059020800"
    weighted.default_sales_tax_code_id = fiscal_posting.tax_codes["VAT-OUT-18"].id
    db.flush()
    before = len(_movements(db, fiscal_posting.company_id))

    inventory_documents.post_batch(
        db,
        fiscal_posting.company_id,
        inventory_documents.DocumentInput(
            document_date=MARCH,
            description="Count corrections",
            lines=[
                inventory_documents.DocumentLineInput(
                    item_id=weighted.id,
                    warehouse_id=fiscal_posting.order.main.id,
                    quantity=D(3),
                    unit_cost=D(500),
                    transaction_type_id=fiscal_posting.order.inventory.transaction_types[
                        "ADJIN"
                    ].id,
                ),
                inventory_documents.DocumentLineInput(
                    item_id=fiscal_posting.stock_item.id,
                    warehouse_id=fiscal_posting.order.main.id,
                    quantity=D(4),
                    transaction_type_id=fiscal_posting.order.inventory.transaction_types[
                        "ADJOUT"
                    ].id,
                ),
            ],
        ),
        actor=fiscal_posting.owner,
    )

    raised = _movements(db, fiscal_posting.company_id)[before:]
    assert [row.payload["sarTyCd"] for row in raised] == [ADJUSTMENT_IN, ADJUSTMENT_OUT]
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_movement_of_an_unclassed_item_is_refused_rather_than_misreported(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """The authority's stock master is keyed by item code, so an item it holds no record of
    cannot be reported — and a record needs a class. Refused on the field that is missing
    rather than sent under a class nobody chose."""
    from app.kernel.errors import PostingError

    fiscal_posting.stock_item.fiscal_class_code = None
    db.flush()

    with pytest.raises(PostingError) as refusal:
        receive(fiscal_posting, db)
    assert refusal.value.code == "fiscal_class_missing"

    # Proven sensitive: the class restored, the same receipt goes through.
    fiscal_posting.stock_item.fiscal_class_code = "5059020800"
    db.flush()
    receive(fiscal_posting, db)
    assert _movements(db, fiscal_posting.company_id)


def test_a_movement_in_a_unit_with_no_ebm_quantity_unit_is_refused(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """The authority holds one quantity per item, in the unit the item was registered with —
    so an unmapped base unit is a quantity RRA cannot read, and the movement is refused on the
    field the Units-of-measure screen fixes."""
    from app.kernel.errors import PostingError

    fiscal_posting.order.each.fiscal_quantity_unit = None
    db.flush()

    with pytest.raises(PostingError) as refusal:
        receive(fiscal_posting, db)
    assert refusal.value.code == "fiscal_uom_unmapped"

    # Proven sensitive: the unit mapped, the same receipt goes through.
    fiscal_posting.order.each.fiscal_quantity_unit = "U"
    db.flush()
    receive(fiscal_posting, db)
    assert _movements(db, fiscal_posting.company_id)


def test_cancelling_a_sale_cancels_the_movement_behind_it(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """The FIFO is what makes this necessary.

    A document's movement sits *behind* its sale, so a sale still `queued` means the movement
    is queued too. Cancelling only the sale — which is what decision 7 does when RRA never
    received it — would leave the movement at the head of the queue, to be sent to an authority
    with no document to attach it to. RRA answers `921`/`922` to exactly that.

    Proven sensitive by deleting the `cancel_unsent_movements` call in `sales.on_reverse`: the
    movement row stays `queued` and the next drain sends it.
    """
    receive(fiscal_posting, db)
    sale = invoice(fiscal_posting, db)

    documents_service.reverse_document(
        db,
        sale,
        on_date=MARCH,
        reason="keyed twice",
        refund_reason="06",
        actor=fiscal_posting.owner,
    )

    of_the_sale = [
        row
        for row in _rows(db, fiscal_posting.company_id)
        if row.source_doc_type == fiscal_stock.PARTNER_DOCUMENT_SOURCE
        and row.source_doc_id == sale.id
    ]
    assert of_the_sale, "the sale and its movement"
    assert all(row.status == FiscalOutboxStatus.CANCELLED for row in of_the_sale), [
        (str(row.kind), str(row.status)) for row in of_the_sale
    ]
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_the_mirror_of_a_movement_that_was_never_reported_is_not_reported(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """RRA has nothing to correct. A receipt with no issue behind it would make its own stock
    figure wrong in the direction the correction was meant to fix."""
    receive(fiscal_posting, db)
    sale = invoice(fiscal_posting, db)
    before = len(_movements(db, fiscal_posting.company_id))

    documents_service.reverse_document(
        db,
        sale,
        on_date=MARCH,
        reason="keyed twice",
        refund_reason="06",
        actor=fiscal_posting.owner,
    )

    live = [
        row
        for row in _movements(db, fiscal_posting.company_id)
        if row.status != FiscalOutboxStatus.CANCELLED
    ]
    assert len(live) == before - 1, "the sale's movement was cancelled and no mirror queued"
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_reversed_signed_sale_reports_its_mirror(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """The other side of the same rule: RRA signed the sale and received the movement, so the
    reversal owes it a refund **and** the goods coming back — behind the refund, as always."""
    from app.fiscal import drainer

    receive(fiscal_posting, db)
    sale = invoice(fiscal_posting, db)
    drainer.drain_company(db, fiscal_posting.company_id, client=sandbox_client)

    documents_service.reverse_document(
        db,
        sale,
        on_date=MARCH,
        reason="returned",
        refund_reason="06",
        actor=fiscal_posting.owner,
    )

    movements = _movements(db, fiscal_posting.company_id)
    assert [row.payload["sarTyCd"] for row in movements] == [
        PURCHASE_IN,
        SALE_OUT,
        RETURN_IN,
    ]
    refund = next(
        row
        for row in _rows(db, fiscal_posting.company_id)
        if row.kind == FiscalOutboxKind.REFUND
    )
    assert refund.sequence_no < movements[-1].sequence_no
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_journal_batch_that_moves_stock_reports_an_adjustment(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """An `ARJN` document is not a sale (decision 3), so there is no invoice for RRA to attach
    a movement to — and sending one as a sale is the `921`/`922` refusal.

    The goods left the shelf all the same, and a shelf that moved without the authority hearing
    about it is the drift decision 10 exists to stop. So it is reported as an **adjustment**,
    which is what a movement with no fiscal document is.
    """
    receive(fiscal_posting, db)
    before = len(_movements(db, fiscal_posting.company_id))

    document, _ = documents_service.post_document(
        db,
        fiscal_posting.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            transaction_type="JNL",
            partner_id=fiscal_posting.customer.id,
            document_date=MARCH,
            description="Opening balance, with goods",
            lines=(
                documents_service.LineInput(
                    item_id=fiscal_posting.stock_item.id,
                    quantity=D(3),
                    unit_price=D(2000),
                    tax_code_id=fiscal_posting.tax_codes["VAT-OUT-18"].id,
                ),
            ),
        ),
        actor=fiscal_posting.owner,
    )

    assert document.doc_type == "ARJN"
    rows = _rows(db, fiscal_posting.company_id)
    assert not [row for row in rows if row.kind == FiscalOutboxKind.SALE]
    raised = _movements(db, fiscal_posting.company_id)[before:]
    assert [row.payload["sarTyCd"] for row in raised] == [ADJUSTMENT_OUT]
    assert_fiscal_invariants(db, fiscal_posting.company_id)
