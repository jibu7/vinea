"""The drainer: FIFO, the backoff, and the three outcomes that are not "sent".

Every clock here is injected. A backoff proved by sleeping is a test nobody runs, and one
proved by mocking `datetime.now` is a test about mocking.

The sandbox is the real RRA-shaped server the adapter talks to, switched between `up`, `down`,
`timeout`, `accept_then_timeout` and `reject:<code>` — so each branch of the retry policy is
driven by a device behaving that way rather than by a double that was told to return a code.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fiscal import drainer
from app.fiscal import outbox as outbox_service
from app.models.fiscalization import (
    FiscalItem,
    FiscalOutboxKind,
    FiscalOutboxRow,
    FiscalOutboxStatus,
    FiscalReceipt,
    FiscalReceiptType,
)
from app.models.subledger import PartnerDocument
from tests.fiscal.conftest import FiscalPosting
from tests.fiscal.helpers import drain_to_the_sale, invoice, receive
from tests.fiscal.invariants import assert_fiscal_invariants


def now() -> datetime:
    """The drainer's clock, read **when a test asks for it**.

    Not a module constant. A row is due from the moment it was written, so a clock captured at
    import time — before any fixture ran — sits before every row this file enqueues and the
    queue is permanently not yet due. Every assertion about the backoff is relative to a value
    this returns, which keeps them exact without pinning a date.
    """
    return datetime.now(UTC)


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


def _drain(
    db: Session,
    fixture: FiscalPosting,
    client: httpx.Client,
    *,
    at: datetime | None = None,
    max_rows: int = 50,
) -> list[drainer.DrainOutcome]:
    return drainer.drain_company(
        db,
        fixture.company_id,
        now=at or now(),
        client=client,
        max_rows_per_device=max_rows,
    )


# --- The happy path --------------------------------------------------------------------------


def test_a_drain_registers_the_item_then_the_sale_and_writes_the_receipt(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    receive(fiscal_posting, db)
    document = invoice(fiscal_posting, db)

    outcomes = _drain(db, fiscal_posting, sandbox_client)

    # The item first, because it is registered on first fiscal use and creation order is queue
    # order; then the receipt's own stock report; then the sale and *its* stock report, which
    # is behind it because RRA requires the sale before the movement it caused (decision 10).
    assert [outcome.kind for outcome in outcomes] == [
        FiscalOutboxKind.ITEM,
        FiscalOutboxKind.STOCK_IO,
        FiscalOutboxKind.STOCK_MASTER,
        FiscalOutboxKind.SALE,
        FiscalOutboxKind.STOCK_IO,
        FiscalOutboxKind.STOCK_MASTER,
    ]
    assert all(outcome.status == FiscalOutboxStatus.SENT for outcome in outcomes)

    receipt = db.scalars(
        select(FiscalReceipt).where(FiscalReceipt.company_id == fiscal_posting.company_id)
    ).one()
    assert receipt.receipt_type == FiscalReceiptType.NORMAL_SALE
    assert receipt.rcpt_no == 1
    assert receipt.tot_rcpt_no == 1
    assert receipt.sdc_id == "SDC010000005"
    # CIS §7.24.7: six `#`-separated fields, the device's date and time first.
    assert len(receipt.qr_payload.split("#")) == 6
    assert db.get(PartnerDocument, document.id).fiscal_receipt_id == receipt.id

    registered = db.scalars(
        select(FiscalItem).where(FiscalItem.company_id == fiscal_posting.company_id)
    ).one()
    assert registered.registered_at is not None
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_the_stored_receipt_carries_the_bytes_that_produced_it(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """`request` is the frozen payload, not a rebuild — what an auditor needs beside a receipt
    is what was actually sent."""
    receive(fiscal_posting, db)
    invoice(fiscal_posting, db)
    _drain(db, fiscal_posting, sandbox_client)

    sale = next(
        row for row in _rows(db, fiscal_posting.company_id) if row.kind == FiscalOutboxKind.SALE
    )
    receipt = db.scalars(
        select(FiscalReceipt).where(FiscalReceipt.company_id == fiscal_posting.company_id)
    ).one()
    assert receipt.request == sale.payload
    assert receipt.invc_no == sale.invc_no


# --- Transport, backoff, and the FIFO ---------------------------------------------------------


def test_an_unreachable_device_backs_off_and_blocks_nothing_it_has_not_reached(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """1 → 5 → 15 → 60 → 360 minutes, then every six hours. Read off the row rather than
    waited for."""
    receive(fiscal_posting, db)
    invoice(fiscal_posting, db)
    _mode(sandbox_client, "down")

    expected = [1, 5, 15, 60, 360, 360]
    at = now()
    for attempt, minutes in enumerate(expected, start=1):
        _drain(db, fiscal_posting, sandbox_client, at=at)
        head = _rows(db, fiscal_posting.company_id)[0]
        assert head.status == FiscalOutboxStatus.QUEUED
        assert head.attempts == attempt
        assert head.next_attempt_at == at + timedelta(minutes=minutes)
        at = head.next_attempt_at

    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_row_that_is_not_due_is_not_sent(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    receive(fiscal_posting, db)
    invoice(fiscal_posting, db)
    _mode(sandbox_client, "down")
    _drain(db, fiscal_posting, sandbox_client)

    _mode(sandbox_client, "up")
    assert _drain(db, fiscal_posting, sandbox_client) == [], "still backing off"

    later = _rows(db, fiscal_posting.company_id)[0].next_attempt_at
    outcomes = _drain(db, fiscal_posting, sandbox_client, at=later)
    # The row whose time came, and everything the FIFO was holding behind it: the item
    # registration, the receipt of stock and its snapshot, then the sale and its own two.
    assert [outcome.status for outcome in outcomes] == [FiscalOutboxStatus.SENT] * 6
    assert outbox_service.head_row(
        db, fiscal_posting.company_id, fiscal_posting.device.id
    ) is None


def test_a_refused_row_blocks_the_queue_behind_it(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """The FIFO stated as a consequence rather than as a mechanism: RRA needs the sale before
    the movement it caused, so a row that will not go stops the ones behind it."""
    receive(fiscal_posting, db, quantity="200")
    invoice(fiscal_posting, db)
    invoice(fiscal_posting, db)
    _mode(sandbox_client, "reject:881")

    _drain(db, fiscal_posting, sandbox_client)

    rows = _rows(db, fiscal_posting.company_id)
    assert rows[0].status == FiscalOutboxStatus.FAILED
    assert rows[0].last_result_cd == "881"
    assert [row.status for row in rows[1:]] == [FiscalOutboxStatus.QUEUED] * (len(rows) - 1)

    _mode(sandbox_client, "up")
    assert _drain(db, fiscal_posting, sandbox_client) == [], (
        "a failed head is not retried by a timer, and nothing behind it may overtake it"
    )
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_retry_now_releases_a_failed_row_and_the_queue_drains_in_order(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    receive(fiscal_posting, db, quantity="200")
    invoice(fiscal_posting, db)
    invoice(fiscal_posting, db)
    _mode(sandbox_client, "reject:894")
    # `894` is the one retryable refusal, so force a hard failure to have something to release.
    _mode(sandbox_client, "reject:881")
    _drain(db, fiscal_posting, sandbox_client)

    head = _rows(db, fiscal_posting.company_id)[0]
    _mode(sandbox_client, "up")
    drainer.retry_now(db, fiscal_posting.company_id, head, actor=fiscal_posting.owner)

    outcomes = _drain(db, fiscal_posting, sandbox_client)

    assert all(outcome.status == FiscalOutboxStatus.SENT for outcome in outcomes)
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_temporary_refusal_is_retried_and_a_permanent_one_is_not(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    receive(fiscal_posting, db)
    invoice(fiscal_posting, db)

    _mode(sandbox_client, "reject:894")
    _drain(db, fiscal_posting, sandbox_client)
    assert _rows(db, fiscal_posting.company_id)[0].status == FiscalOutboxStatus.QUEUED

    _mode(sandbox_client, "reject:882")
    _drain(db, fiscal_posting, sandbox_client, at=now() + timedelta(minutes=5))
    assert _rows(db, fiscal_posting.company_id)[0].status == FiscalOutboxStatus.FAILED


# --- `unknown`: the request left and nothing came back ------------------------------------------


def test_a_lost_answer_is_unknown_and_is_never_resent(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """The state the whole policy exists for. The sandbox **registers the sale and then fails
    to answer**, which is precisely the case a resend turns into a `994` duplicate carrying no
    receipt data."""
    receive(fiscal_posting, db)
    invoice(fiscal_posting, db)
    # One row, so the item is registered and the sale is still queued behind it. The `unknown`
    # state is about the *sale*, and a drain that had already sent it would have nothing left
    # to lose an answer for.
    _drain(db, fiscal_posting, sandbox_client, max_rows=1)
    _mode(sandbox_client, "accept_then_timeout")

    _drain(db, fiscal_posting, sandbox_client)

    sale = next(
        row for row in _rows(db, fiscal_posting.company_id) if row.kind == FiscalOutboxKind.SALE
    )
    assert sale.status == FiscalOutboxStatus.UNKNOWN
    assert sale.next_attempt_at is None

    _mode(sandbox_client, "up")
    assert _drain(db, fiscal_posting, sandbox_client, at=now() + timedelta(days=1)) == [], (
        "no timer resends an unknown row"
    )
    with pytest.raises(drainer.QueueActionError):
        drainer.retry_now(db, fiscal_posting.company_id, sale, actor=fiscal_posting.owner)
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_verifying_an_unknown_row_reads_the_device_counters(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """RRA holds the sale, so the row becomes `needs_receipt` rather than being resent — the
    counter came back at or above ours."""
    receive(fiscal_posting, db)
    invoice(fiscal_posting, db)
    _drain(db, fiscal_posting, sandbox_client, max_rows=1)
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


def test_verifying_a_row_the_device_never_saw_puts_it_back_in_the_queue(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """The other half of the same question. The sale never arrived, so the device's counter is
    below ours and the row is safe to send."""
    receive(fiscal_posting, db)
    invoice(fiscal_posting, db)
    drain_to_the_sale(fiscal_posting, db, sandbox_client)
    _mode(sandbox_client, "timeout")
    _drain(db, fiscal_posting, sandbox_client)
    sale = next(
        row for row in _rows(db, fiscal_posting.company_id) if row.kind == FiscalOutboxKind.SALE
    )
    assert sale.status == FiscalOutboxStatus.UNKNOWN

    _mode(sandbox_client, "up")
    drainer.verify_with_device(
        db,
        fiscal_posting.company_id,
        fiscal_posting.device,
        sale,
        actor=fiscal_posting.owner,
        client=sandbox_client,
    )

    assert sale.status == FiscalOutboxStatus.QUEUED
    outcomes = _drain(db, fiscal_posting, sandbox_client, at=now() + timedelta(minutes=1))
    # The sale, and the two rows its own stock movement queued behind it (P7 step 3).
    assert [outcome.status for outcome in outcomes] == [FiscalOutboxStatus.SENT] * 3
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_receipt_read_off_the_portal_can_be_attached_by_hand(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """`needs_receipt` → `sent`, with the actor's note on the row. The sandbox's `/_sandbox/
    ledger` stands in for MyRRA, which is where an operator would read the same six fields."""
    receive(fiscal_posting, db)
    invoice(fiscal_posting, db)
    _drain(db, fiscal_posting, sandbox_client, max_rows=1)
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

    held = sandbox_client.get("/_sandbox/ledger").json()
    fields = next(iter(held.values()))["sales"][str(sale.invc_no)]
    receipt = drainer.attach_receipt(
        db,
        fiscal_posting.company_id,
        fiscal_posting.device,
        sale,
        fields=fields,
        note="read off MyRRA on 10 March",
        actor=fiscal_posting.owner,
        client=sandbox_client,
    )

    assert sale.status == FiscalOutboxStatus.SENT
    assert sale.resolved_by == fiscal_posting.owner.id
    assert sale.resolution_note == "read off MyRRA on 10 March"
    assert receipt.rcpt_no == 1
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_receipt_cannot_be_attached_to_a_row_that_does_not_need_one(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    receive(fiscal_posting, db)
    invoice(fiscal_posting, db)
    sale = next(
        row for row in _rows(db, fiscal_posting.company_id) if row.kind == FiscalOutboxKind.SALE
    )

    with pytest.raises(drainer.QueueActionError):
        drainer.attach_receipt(
            db,
            fiscal_posting.company_id,
            fiscal_posting.device,
            sale,
            fields={"rcptNo": 1, "totRcptNo": 1},
            note="invented",
            actor=fiscal_posting.owner,
            client=sandbox_client,
        )


# --- The dashboard's `offline` flag ------------------------------------------------------------


def test_a_device_whose_queue_is_a_day_old_reads_offline(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """VSDC §2.2 item 4: the device stops issuing after 24 h without connectivity, so a queue
    that old is a shop about to be unable to sell."""
    receive(fiscal_posting, db)
    invoice(fiscal_posting, db)
    row = _rows(db, fiscal_posting.company_id)[0]

    assert not outbox_service.is_offline(
        db, fiscal_posting.company_id, fiscal_posting.device.id, now=row.created_at
    )
    assert outbox_service.is_offline(
        db,
        fiscal_posting.company_id,
        fiscal_posting.device.id,
        now=row.created_at + timedelta(hours=25),
    )
