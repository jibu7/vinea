"""Decision 7's reversal policy, and the two refusals that make it safe.

Reversing a fiscalized document is four different things depending on what RRA holds:

* nothing yet (`queued`) — cancel the row and reverse in the ledger. This is why a `failed`
  sale is corrected by reversing and re-posting rather than by editing a payload.
* a signed **sale** — RRA cannot un-sign it, so the reversal queues a full refund (label NR).
* a signed **refund** — refused. A refund of a refund is not in EBM's vocabulary.
* an unresolved row (`unknown`, `needs_receipt`) — refused until a person resolves it. RRA may
  be holding the sale, and cancelling the row would leave a registered sale unrefunded.

Each refusal is paired with the case that *does* go through, because a refusal test that passes
for the wrong reason proves nothing.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fiscal import drainer
from app.fiscal import outbox as outbox_service
from app.kernel.errors import LedgerStateError
from app.models.fiscalization import (
    FiscalOutboxKind,
    FiscalOutboxRow,
    FiscalOutboxStatus,
    FiscalReceipt,
    FiscalReceiptType,
)
from app.models.subledger import DocumentStatus
from app.subledger import documents as documents_service
from tests.fiscal.conftest import FiscalPosting
from tests.fiscal.helpers import (
    APRIL,
    REFUND_REASON,
    drain_to_the_sale,
    invoice,
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


def _mode(client: httpx.Client, mode: str) -> None:
    client.post("/_sandbox/mode", json={"mode": mode})


def _drain(db: Session, fixture: FiscalPosting, client: httpx.Client) -> None:
    drainer.drain_company(db, fixture.company_id, now=datetime.now(UTC), client=client)


def test_reversing_a_queued_sale_cancels_its_row(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """RRA never held it, so there is nothing to refund — the row is cancelled and the ledger
    reversal is the whole of it."""
    receive(fiscal_posting, db)
    document = invoice(fiscal_posting, db)

    documents_service.reverse_document(
        db, document, on_date=APRIL, reason="keyed twice", actor=fiscal_posting.owner
    )

    sale = next(
        row for row in _rows(db, fiscal_posting.company_id) if row.kind == FiscalOutboxKind.SALE
    )
    assert sale.status == FiscalOutboxStatus.CANCELLED
    assert document.status == DocumentStatus.REVERSED
    assert "keyed twice" in (sale.resolution_note or "")
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_reversing_a_failed_sale_cancels_its_row_so_it_can_be_re_posted(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """The correction path for a payload RRA refused: reverse, fix the master, post again.
    Never edit the frozen payload — what was wrong is what the document said."""
    receive(fiscal_posting, db, quantity="200")
    document = invoice(fiscal_posting, db)
    _mode(sandbox_client, "reject:881")
    _drain(db, fiscal_posting, sandbox_client)
    head = _rows(db, fiscal_posting.company_id)[0]
    assert head.status == FiscalOutboxStatus.FAILED

    documents_service.reverse_document(
        db, document, on_date=APRIL, reason="RRA refused it", actor=fiscal_posting.owner
    )

    statuses = {row.kind: row.status for row in _rows(db, fiscal_posting.company_id)}
    assert statuses[FiscalOutboxKind.SALE] == FiscalOutboxStatus.CANCELLED

    _mode(sandbox_client, "up")
    reposted = invoice(fiscal_posting, db)
    assert reposted.id != document.id
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_reversing_a_signed_sale_queues_a_full_refund(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    receive(fiscal_posting, db)
    document = invoice(fiscal_posting, db)
    _drain(db, fiscal_posting, sandbox_client)

    documents_service.reverse_document(
        db,
        document,
        on_date=APRIL,
        reason="goods never delivered",
        refund_reason=REFUND_REASON,
        actor=fiscal_posting.owner,
    )

    refund = next(
        row
        for row in _rows(db, fiscal_posting.company_id)
        if row.kind == FiscalOutboxKind.REFUND
    )
    assert refund.source_doc_type == outbox_service.REVERSAL_SOURCE
    assert refund.source_doc_id == document.id
    assert refund.payload["orgInvcNo"] == 1
    assert refund.payload["rfdRsnCd"] == REFUND_REASON
    # The original sale's row is untouched: RRA signed it, and pretending otherwise would
    # leave a registered sale with no refund against it.
    sale = next(
        row for row in _rows(db, fiscal_posting.company_id) if row.kind == FiscalOutboxKind.SALE
    )
    assert sale.status == FiscalOutboxStatus.SENT

    _drain(db, fiscal_posting, sandbox_client)
    receipts = {
        receipt.receipt_type: receipt
        for receipt in db.scalars(
            select(FiscalReceipt).where(FiscalReceipt.company_id == fiscal_posting.company_id)
        )
    }
    assert set(receipts) == {FiscalReceiptType.NORMAL_SALE, FiscalReceiptType.NORMAL_REFUND}
    assert receipts[FiscalReceiptType.NORMAL_REFUND].org_invc_no == 1
    # The invoice still points at **its own** receipt, not at the refund that undid it.
    assert document.fiscal_receipt_id == receipts[FiscalReceiptType.NORMAL_SALE].id
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_reversing_a_signed_sale_without_a_reason_code_is_refused(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    receive(fiscal_posting, db)
    document = invoice(fiscal_posting, db)
    _drain(db, fiscal_posting, sandbox_client)

    with pytest.raises(Exception) as refusal:
        documents_service.reverse_document(
            db, document, on_date=APRIL, reason="mistake", actor=fiscal_posting.owner
        )

    assert getattr(refusal.value, "code", None) == "refund_reason_required"
    assert document.status == DocumentStatus.POSTED, "nothing was written"


def test_a_signed_refund_cannot_be_reversed(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """A refund of a refund is not something EBM can express, so the correction is a new
    invoice — said as a refusal rather than left to produce an untranslatable payload."""
    from decimal import Decimal

    from tests.fiscal.helpers import credit_note, line_of

    receive(fiscal_posting, db)
    original = invoice(fiscal_posting, db)
    note = credit_note(
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
    _drain(db, fiscal_posting, sandbox_client)

    with pytest.raises(LedgerStateError) as refusal:
        documents_service.reverse_document(
            db,
            note,
            on_date=APRIL,
            reason="wrong credit note",
            refund_reason=REFUND_REASON,
            actor=fiscal_posting.owner,
        )

    assert refusal.value.code == "fiscal_refund_irreversible"
    assert note.status == DocumentStatus.POSTED


def test_a_document_whose_row_is_unknown_cannot_be_reversed(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """RRA may be holding the sale. Cancelling the row would leave a registered sale with no
    refund against it — so the row is resolved first, and then the reversal goes through."""
    receive(fiscal_posting, db)
    document = invoice(fiscal_posting, db)
    # Everything the FIFO holds in front of the sale — the item registration and the receipt of
    # stock that opened the shelf (P7 step 3) — so the row the device times out on is the sale.
    drain_to_the_sale(fiscal_posting, db, sandbox_client)
    _mode(sandbox_client, "timeout")
    _drain(db, fiscal_posting, sandbox_client)
    sale = next(
        row for row in _rows(db, fiscal_posting.company_id) if row.kind == FiscalOutboxKind.SALE
    )
    assert sale.status == FiscalOutboxStatus.UNKNOWN

    with pytest.raises(LedgerStateError) as refusal:
        documents_service.reverse_document(
            db,
            document,
            on_date=APRIL,
            reason="mistake",
            refund_reason=REFUND_REASON,
            actor=fiscal_posting.owner,
        )
    assert refusal.value.code == "fiscal_status_unresolved"
    assert document.status == DocumentStatus.POSTED

    # Resolved — RRA never saw it — and now the same reversal goes through.
    _mode(sandbox_client, "up")
    drainer.verify_with_device(
        db,
        fiscal_posting.company_id,
        fiscal_posting.device,
        sale,
        actor=fiscal_posting.owner,
        client=sandbox_client,
    )
    documents_service.reverse_document(
        db,
        document,
        on_date=APRIL,
        reason="mistake",
        refund_reason=REFUND_REASON,
        actor=fiscal_posting.owner,
    )
    assert document.status == DocumentStatus.REVERSED
    assert sale.status == FiscalOutboxStatus.CANCELLED
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_needs_receipt_row_also_blocks_the_reversal(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    receive(fiscal_posting, db)
    document = invoice(fiscal_posting, db)
    drainer.drain_company(
        db, fiscal_posting.company_id, client=sandbox_client, max_rows_per_device=1
    )
    _mode(sandbox_client, "accept_then_timeout")
    _drain(db, fiscal_posting, sandbox_client)
    sale = next(
        row for row in _rows(db, fiscal_posting.company_id) if row.kind == FiscalOutboxKind.SALE
    )
    _mode(sandbox_client, "up")
    drainer.verify_with_device(
        db,
        fiscal_posting.company_id,
        fiscal_posting.device,
        sale,
        actor=fiscal_posting.owner,
        client=sandbox_client,
    )
    assert sale.status == FiscalOutboxStatus.NEEDS_RECEIPT

    with pytest.raises(LedgerStateError) as refusal:
        documents_service.reverse_document(
            db,
            document,
            on_date=APRIL,
            reason="mistake",
            refund_reason=REFUND_REASON,
            actor=fiscal_posting.owner,
        )

    assert refusal.value.code == "fiscal_status_unresolved"


def test_the_reversal_refund_is_the_whole_document(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """Worked by hand: ten bottles at 2 000 exclusive is 23 600 inclusive, so the refund the
    reversal owes is the same 23 600 — a reversal is not a partial credit."""
    receive(fiscal_posting, db)
    document = invoice(fiscal_posting, db)
    _drain(db, fiscal_posting, sandbox_client)

    documents_service.reverse_document(
        db,
        document,
        on_date=APRIL,
        reason="cancelled order",
        refund_reason=REFUND_REASON,
        actor=fiscal_posting.owner,
    )

    refund = next(
        row
        for row in _rows(db, fiscal_posting.company_id)
        if row.kind == FiscalOutboxKind.REFUND
    )
    assert refund.payload["totTaxblAmt"] == 23600
    assert refund.payload["totTaxAmt"] == 3600
    assert refund.payload["rcptTyCd"] == "R"
    assert all(
        item["qty"] > 0 for item in refund.payload["itemList"]
    ), "a refund is positive on the wire; the minus signs belong to the printed receipt"


def test_the_backoff_table_is_clamped_not_indexed_past_its_end() -> None:
    """The unit behind "then every six hours, forever"."""
    assert outbox_service.backoff_for(1) == timedelta(minutes=1)
    assert outbox_service.backoff_for(5) == timedelta(minutes=360)
    assert outbox_service.backoff_for(99) == timedelta(minutes=360)
