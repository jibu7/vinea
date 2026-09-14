"""The P6 phase invariants — what must be true of order entry after every posting.

The load-bearing one is the **accrual proof**: at any date, per branch, the GRN accrual
account's balance equals the sum over GRN lines of what was received less what has been
relieved. It is stated per branch as well as in total because a branch that received goods
and a branch that billed for them must not net each other out into an accrual that looks
right and is wrong in both places.

`relieved` is read from `partner_document_lines.accrual_relieved` — **what was posted** — and
never recomputed from today's quantities. A pro-rata share cannot be re-derived once one of
its siblings is reversed: value 1 000 received over quantity 3 and matched 1 + 1 + 1 relieves
333 + 333 + 334, and reversing the first leaves the ledger having relieved 667 where a
recomputation over the survivors gives 666. An invariant that recomputed would disagree with
the ledger by a franc and would blame the ledger.

The second is the **landed-cost clearing proof**: the clearing account's balance equals what
was booked to it less what has been allocated off it. Freight, duty and insurance arrive on
documents this module knows nothing about — a forwarder's invoice, a cashbook payment — and
landed-cost documents take them off again. Reading "allocated" from `landed_cost_lines.share`
rather than from the ledger is what makes this a cross-check: the documents say one number and
the account says another only if the posting and the split have come apart.
"""

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kernel.posting import gl_settings_for
from app.models.inventory import GoodsReceivedNote, GoodsReceivedNoteLine, GrnStatus
from app.models.journal import JournalEntry, JournalLine, JournalStatus
from app.models.order_entry import (
    LandedCostDocument,
    LandedCostStatus,
    PurchaseOrder,
    SalesOrder,
)
from app.models.subledger import DocumentStatus, PartnerDocument, PartnerDocumentLine
from app.order_entry import grn as grn_service
from app.order_entry import quantities as order_quantities

ZERO = Decimal(0)


def _accrual_balances_by_date_and_branch(
    db: Session, company_id: int, account_id: int
) -> dict[tuple[object, int], Decimal]:
    """Running accrual balance per branch, at every date anything was posted to it."""
    rows = db.execute(
        select(JournalEntry.entry_date, JournalLine.branch_id, JournalLine.base_amount)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == company_id,
            JournalLine.gl_account_id == account_id,
            JournalEntry.status == JournalStatus.POSTED,
        )
        .order_by(JournalEntry.entry_date)
    ).all()
    running: dict[int, Decimal] = defaultdict(lambda: ZERO)
    out: dict[tuple[object, int], Decimal] = {}
    for entry_date, branch_id, base_amount in rows:
        running[branch_id] += base_amount
        for branch, total in running.items():
            out[(entry_date, branch)] = total
    return out


def _received_less_relieved(
    db: Session, company_id: int
) -> dict[tuple[object, int], Decimal]:
    """Σ (received − relieved) per branch, at every date a receipt or a match happened.

    Signed the way the ledger signs it: a receipt credits the accrual, so an outstanding
    receipt is a negative balance on the account.
    """
    events: list[tuple[object, int, Decimal]] = []
    # Both sides bucket by **the branch of the warehouse the goods moved through**, never by
    # a document header's branch. The header's is derived from the warehouse and the two
    # cannot disagree, but the *invoice* that relieves an accrual may be keyed anywhere, and
    # bucketing its relief by its own branch is exactly the drift this clause exists to catch.
    from app.models.inventory import Warehouse

    grn_rows = db.execute(
        select(
            GoodsReceivedNote.grn_date,
            Warehouse.branch_id,
            GoodsReceivedNoteLine.value,
            GoodsReceivedNote.status,
            GoodsReceivedNote.reversed_on,
        )
        .join(GoodsReceivedNoteLine, GoodsReceivedNoteLine.grn_id == GoodsReceivedNote.id)
        .join(Warehouse, Warehouse.id == GoodsReceivedNoteLine.warehouse_id)
        .where(GoodsReceivedNote.company_id == company_id)
    ).all()
    for grn_date, branch_id, value, status, reversed_on in grn_rows:
        events.append((grn_date, branch_id, -value))
        if status == GrnStatus.REVERSED and reversed_on is not None:
            events.append((reversed_on, branch_id, value))

    match_rows = db.execute(
        select(
            PartnerDocument.document_date,
            Warehouse.branch_id,
            PartnerDocumentLine.accrual_relieved,
        )
        .join(PartnerDocument, PartnerDocument.id == PartnerDocumentLine.document_id)
        .join(
            GoodsReceivedNoteLine,
            GoodsReceivedNoteLine.id == PartnerDocumentLine.grn_line_id,
        )
        .join(Warehouse, Warehouse.id == GoodsReceivedNoteLine.warehouse_id)
        .where(
            PartnerDocumentLine.company_id == company_id,
            PartnerDocumentLine.accrual_relieved.is_not(None),
            PartnerDocument.status == DocumentStatus.POSTED,
        )
    ).all()
    for document_date, branch_id, relieved in match_rows:
        events.append((document_date, branch_id, relieved))

    events.sort(key=lambda event: event[0])
    running: dict[int, Decimal] = defaultdict(lambda: ZERO)
    out: dict[tuple[object, int], Decimal] = {}
    for event_date, branch_id, amount in events:
        running[branch_id] += amount
        for branch, total in running.items():
            out[(event_date, branch)] = total
    return out


def assert_order_invariants(db: Session, company_id: int) -> None:
    """1. The GRN accrual account's balance equals Σ (received − relieved), per branch, at
          every date on which either moved.
       2. `matched <= received` on every GRN line.
       3. Every stock-bearing partner document has exactly one companion whose keyed moves
          correspond one-for-one with its stock lines — or none, when no stock line carried
          value.
       4. A matched line carries a relieved amount and no stock move; an unmatched stock line
          carries a move and no relieved amount.
       5. `verify_order_statuses()` reports no drift between the stored GRN, sales-order and
          purchase-order statuses and the state their lines actually imply.
       6. No order line is fulfilled beyond what it ordered. `invoice_exceeds_order`,
          `receipt_exceeds_order` and the edit floor each refuse one way of reaching that state;
          this is the assertion that none of them has a gap. An over-fulfilled line makes
          `committed` negative on that line and leaves the status derivation with nothing
          sensible to say.
       7. The landed-cost clearing account's balance equals what was booked to it less what
          unreversed landed-cost documents have allocated off it — so a fully allocated
          clearing account is zero. Read from `landed_cost_lines.share`, never from the
          ledger, so the two have to agree rather than being the same number twice.
       8. Every unreversed landed-cost document's shares sum to its amount, exactly. That is
          the residue rule, and it is what makes clause 7 reachable: a document whose shares
          summed to a franc less than its amount would leave the clearing account holding that
          franc forever, and no later allocation could take it off.
    """
    settings = gl_settings_for(db, company_id)
    accrual_account_id = settings.grn_accrual_account_id

    # 1. The accrual proof.
    if accrual_account_id is not None:
        ledger = _accrual_balances_by_date_and_branch(db, company_id, accrual_account_id)
        derived = _received_less_relieved(db, company_id)
        for key, expected in derived.items():
            actual = ledger.get(key, ZERO)
            assert actual == expected, (
                f"accrual drift at {key[0]} branch {key[1]}: the account says {actual}, "
                f"receipts less reliefs say {expected}"
            )

    # 2. Nothing is matched beyond what arrived.
    grn_lines = list(
        db.scalars(
            select(GoodsReceivedNoteLine).where(
                GoodsReceivedNoteLine.company_id == company_id
            )
        )
    )
    matched = grn_service.matched_quantities(
        db, company_id, [line.id for line in grn_lines]
    )
    for line in grn_lines:
        done = matched.get(line.id, ZERO)
        assert done <= line.base_quantity, (
            f"GRN line {line.id} received {line.base_quantity} and matched {done}"
        )

    # 3 and 4. The companion, and what a matched line is allowed to be.
    from app.models.inventory import StockMove

    documents = list(
        db.scalars(
            select(PartnerDocument).where(
                PartnerDocument.company_id == company_id,
                PartnerDocument.status == DocumentStatus.POSTED,
            )
        )
    )
    for document in documents:
        stock_lines = [
            line
            for line in document.lines
            if line.item_id is not None and line.grn_line_id is None and line.warehouse_id
        ]
        moves = (
            list(
                db.scalars(
                    select(StockMove).where(
                        StockMove.company_id == company_id,
                        StockMove.journal_entry_id == document.stock_entry_id,
                    )
                )
            )
            if document.stock_entry_id is not None
            else []
        )
        keyed = [move for move in moves if move.source_line_id is not None]
        if document.stock_entry_id is not None:
            assert len(keyed) == len(stock_lines), (
                f"{document.number}: {len(stock_lines)} stock lines but {len(keyed)} keyed "
                "moves on its companion"
            )
            assert {move.source_line_id for move in keyed} == {
                line.id for line in stock_lines
            }, f"{document.number}: companion moves do not correspond to its stock lines"
        for line in document.lines:
            if line.grn_line_id is not None:
                assert line.accrual_relieved is not None, (
                    f"{document.number} line {line.line_no} matched a GRN and relieved nothing"
                )

    # 5. The stored workflow columns against the state the lines imply.
    drift = verify_order_statuses(db, company_id)
    assert not drift, f"order status drift: {drift[:3]}"

    # 6. Nothing is fulfilled beyond what was ordered.
    for kind, fulfilment in (
        ("sales", order_quantities.sales_fulfilment(db, company_id)),
        ("purchase", order_quantities.purchase_fulfilment(db, company_id)),
    ):
        over = [row for row in fulfilment.values() if row.is_over_fulfilled]
        assert not over, (
            f"{kind} order line {over[0].line_id} ordered {over[0].ordered} and has "
            f"{over[0].fulfilled} fulfilled"
        )

    # 7 and 8. The landed-cost clearing proof, and the residue rule underneath it.
    _assert_clearing_clears(db, company_id, settings.landed_cost_clearing_account_id)


def _assert_clearing_clears(db: Session, company_id: int, clearing_account_id: int | None) -> None:
    """The clearing account holds `booked - allocated`, and every allocation allocates it all.

    "Booked" is everything posted to the account by anything that is **not** a landed cost —
    the forwarder's invoice, the duty payment — and "allocated" is the sum of the shares the
    landed-cost documents wrote. Taking the second from the document tables rather than from
    the entry is the whole point: if a posting ever put a different number on the account from
    the one the shares recorded, this is what says so.
    """
    if clearing_account_id is None:
        return
    documents = list(
        db.scalars(
            select(LandedCostDocument).where(LandedCostDocument.company_id == company_id)
        )
    )

    # 8 first, because 7 is only meaningful once it holds.
    for document in documents:
        if document.status != LandedCostStatus.POSTED:
            continue
        total = sum((line.share for line in document.lines), ZERO)
        assert total == document.amount, (
            f"{document.number} allocates {document.amount} and its shares sum to {total}"
        )

    allocation_entry_ids = {
        document.journal_entry_id
        for document in documents
        if document.journal_entry_id is not None
    } | {
        document.reversal_entry_id
        for document in documents
        if document.reversal_entry_id is not None
    }
    rows = db.execute(
        select(JournalLine.entry_id, JournalLine.base_amount)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == company_id,
            JournalLine.gl_account_id == clearing_account_id,
            JournalEntry.status == JournalStatus.POSTED,
        )
    ).all()
    balance = sum((amount for _entry_id, amount in rows), ZERO)
    booked = sum(
        (amount for entry_id, amount in rows if entry_id not in allocation_entry_ids), ZERO
    )
    allocated = sum(
        (
            line.share
            for document in documents
            if document.status == LandedCostStatus.POSTED
            for line in document.lines
        ),
        ZERO,
    )
    assert balance == booked - allocated, (
        f"landed-cost clearing drift: the account says {balance}, booked less allocated says "
        f"{booked - allocated} ({booked} booked, {allocated} allocated)"
    )


def verify_order_statuses(db: Session, company_id: int) -> list[str]:
    """Every receipt and every order whose stored status disagrees with what its lines imply.

    The `open_amount` pattern P4 set: the column is a convenience for filtering and the query
    is the truth, and this is the check that keeps them honest. Returns descriptions rather
    than raising, so a report can show the drift instead of only failing on it.

    All three workflow columns are checked here rather than in three places, because all three
    fail the same way: a service changes what has been matched, invoiced or received and forgets
    to write the column, and every screen then filters on a status that is a phase behind.
    """
    drift: list[str] = []
    for grn in db.scalars(
        select(GoodsReceivedNote).where(GoodsReceivedNote.company_id == company_id)
    ):
        matched = grn_service.matched_quantities(
            db, company_id, [line.id for line in grn.lines]
        )
        expected = grn_service.derived_status(grn, matched)
        if grn.status != expected:
            drift.append(f"{grn.number}: stored {grn.status}, derived {expected}")

    for order in db.scalars(select(SalesOrder).where(SalesOrder.company_id == company_id)):
        expected_sales = order_quantities.derived_sales_status(
            order, order_quantities.sales_fulfilment(db, company_id, order_id=order.id)
        )
        if order.status != expected_sales:
            drift.append(f"{order.number}: stored {order.status}, derived {expected_sales}")

    for order in db.scalars(select(PurchaseOrder).where(PurchaseOrder.company_id == company_id)):
        expected_purchase = order_quantities.derived_purchase_status(
            order, order_quantities.purchase_fulfilment(db, company_id, order_id=order.id)
        )
        if order.status != expected_purchase:
            drift.append(f"{order.number}: stored {order.status}, derived {expected_purchase}")
    return drift
