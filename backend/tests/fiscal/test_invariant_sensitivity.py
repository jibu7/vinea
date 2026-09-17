"""`assert_fiscal_invariants` is only worth running if it fails when it should.

Six invariants, six deliberate breakages, each a single edit to a row the ordinary path
produced. The pattern P5 established in `tests/inventory/test_checker_sensitivity.py`, and it
exists because an invariant suite that cannot fail is the most expensive kind of green: every
property test in the phase asserts these after every step, so a checker that had quietly
stopped looking would make the whole machine vacuous.

Each breakage is also a thing that could actually happen — a second row for one document, a
`sent` row with no receipt, a counter that repeats, a row overtaking a blocked one, a key that
reached a stored payload. None of them is contrived.
"""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fiscal import drainer
from app.fiscal import outbox as outbox_service
from app.models.fiscalization import (
    FiscalOutboxKind,
    FiscalOutboxRow,
    FiscalOutboxStatus,
    FiscalReceipt,
)
from tests.fiscal.conftest import FiscalPosting
from tests.fiscal.helpers import invoice, receive
from tests.fiscal.invariants import assert_fiscal_invariants


def _rows(db: Session, company_id: int) -> list[FiscalOutboxRow]:
    return list(
        db.scalars(
            select(FiscalOutboxRow)
            .where(FiscalOutboxRow.company_id == company_id)
            .order_by(FiscalOutboxRow.sequence_no)
        )
    )


@pytest.fixture
def drained(db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client):  # noqa: ANN201
    """Two invoices, both registered — the state every breakage below starts from."""
    receive(fiscal_posting, db, quantity="200")
    invoice(fiscal_posting, db)
    invoice(fiscal_posting, db)
    drainer.drain_company(db, fiscal_posting.company_id, client=sandbox_client)
    assert_fiscal_invariants(db, fiscal_posting.company_id)
    return fiscal_posting


def test_a_second_live_row_for_one_document_is_caught(
    db: Session, drained: FiscalPosting
) -> None:
    """Two sale rows for one invoice is a sale RRA would register twice."""
    sale = next(row for row in _rows(db, drained.company_id) if row.kind == FiscalOutboxKind.SALE)
    outbox_service.enqueue(
        db,
        drained.company_id,
        device=drained.device,
        kind=FiscalOutboxKind.SALE,
        payload=sale.payload,
        source_doc_type=sale.source_doc_type,
        source_doc_id=sale.source_doc_id,
        invc_no=99,
    )

    with pytest.raises(AssertionError, match="register the same sale twice"):
        assert_fiscal_invariants(db, drained.company_id)


def test_a_sent_row_with_no_receipt_is_caught(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """"Sent" has to mean "RRA signed it", or nothing else in the phase can rely on it.

    Broken by marking a row sent rather than by deleting a receipt, because a receipt cannot
    be deleted: `fiscal_block_receipt_mutation` raises on any UPDATE or DELETE, which is the
    P2 rule one domain along and is itself worth knowing held.
    """
    receive(fiscal_posting, db, quantity="200")
    invoice(fiscal_posting, db)
    invoice(fiscal_posting, db)
    # Two rows: the item registration and the first sale. The second sale is left queued.
    drainer.drain_company(
        db, fiscal_posting.company_id, client=sandbox_client, max_rows_per_device=2
    )
    unsent = _rows(db, fiscal_posting.company_id)[-1]
    assert unsent.status == FiscalOutboxStatus.QUEUED

    unsent.status = FiscalOutboxStatus.SENT
    db.flush()

    with pytest.raises(AssertionError, match="sent and holds 0 receipts"):
        assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_receipt_on_a_row_that_was_never_sent_is_caught(
    db: Session, drained: FiscalPosting
) -> None:
    sale = next(
        row
        for row in _rows(db, drained.company_id)
        if row.kind == FiscalOutboxKind.SALE and row.status == FiscalOutboxStatus.SENT
    )
    sale.status = FiscalOutboxStatus.QUEUED
    db.flush()

    with pytest.raises(AssertionError, match="holds a receipt"):
        assert_fiscal_invariants(db, drained.company_id)


def test_a_hole_in_the_invoice_numbers_is_caught(db: Session, drained: FiscalPosting) -> None:
    """RRA reconciles the run, and a hole in it is a question from Kigali."""
    sale = next(row for row in _rows(db, drained.company_id) if row.kind == FiscalOutboxKind.SALE)
    sale.invc_no = 7
    db.flush()

    with pytest.raises(AssertionError, match="invoice numbers are"):
        assert_fiscal_invariants(db, drained.company_id)


def test_a_repeated_receipt_counter_is_caught(db: Session, drained: FiscalPosting) -> None:
    """A receipt is a legal document with a number on it; a number that repeated is a thing
    somebody has to explain."""
    receipts = list(
        db.scalars(select(FiscalReceipt).where(FiscalReceipt.company_id == drained.company_id))
    )
    assert len(receipts) >= 2
    # `fiscal_receipts` is immutable by trigger, so the breakage is made on the ORM's copy —
    # which is exactly what the checker reads.
    receipts[-1].rcpt_no = receipts[0].rcpt_no

    with pytest.raises(AssertionError, match="counters are"):
        assert_fiscal_invariants(db, drained.company_id)
    db.expunge_all()


def test_a_row_that_overtook_a_blocked_one_is_caught(
    db: Session, drained: FiscalPosting
) -> None:
    """The invariant that catches a drainer "helpfully" skipping a stuck row — which RRA
    answers 921/922 to, and which puts a stock report before its sale."""
    rows = _rows(db, drained.company_id)
    rows[0].status = FiscalOutboxStatus.FAILED
    db.flush()

    with pytest.raises(AssertionError, match="is sent while row"):
        assert_fiscal_invariants(db, drained.company_id)


def test_a_key_in_a_stored_payload_is_caught(db: Session, drained: FiscalPosting) -> None:
    """The keys are the only secrets this phase holds, and a payload in a database is a
    payload in a backup."""
    from app.fiscal.rwanda.sandbox import SANDBOX_CMC_KEY

    sale = next(row for row in _rows(db, drained.company_id) if row.kind == FiscalOutboxKind.SALE)
    sale.payload = {**sale.payload, "cmcKeyX": SANDBOX_CMC_KEY}
    db.flush()

    with pytest.raises(AssertionError, match="device key is stored in"):
        assert_fiscal_invariants(db, drained.company_id)
