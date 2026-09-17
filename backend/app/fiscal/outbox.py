"""The durable queue (ADR-10, decision 4) — writing to it, and reading the next row to send.

**A row is written in the same transaction as the thing it reports.** `post_document()` inserts
the `sale` row before it returns; nothing calls a revenue authority from a request handler. That
is the whole guarantee the phase turns on: a document that reached the ledger has a row, a row
that exists had its document committed, and neither can happen without the other because they
commit together.

**Per-device FIFO, one in flight.** `sequence_no` is the row id, so creation order is queue
order, and a `failed`, `unknown` or `needs_receipt` row *blocks the device's queue behind it*.
That is deliberate: VSDC §3.1 requires a stock movement to be preceded by the sale that caused
it, and the receipt counters are a sequence. Sending out of order is worse than waiting — RRA
answers `921`/`922` and the queue is then wrong *and* stuck.

**The payload is frozen here.** It is rendered inside the posting transaction from the document
as posted, and the drainer sends exactly those bytes. A drainer that re-rendered would send
whatever the masters say today, which is not what the posting said.

Nothing in this module talks to a revenue authority. It writes rows, picks the next one, and
owns the backoff schedule; `app/fiscal/drainer.py` is what sends.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.fiscalization import (
    FiscalDevice,
    FiscalDeviceStatus,
    FiscalOutboxKind,
    FiscalOutboxRow,
    FiscalOutboxStatus,
)

#: What `fiscal_outbox.source_doc_type` holds for a partner document, and for the **refund a
#: reversal owes**. Two names rather than two rows of one name, so "one document, one row"
#: stays literally true per source type: reversing a signed sale owes RRA a refund, and that
#: refund is a different fact about the same document rather than a second sale of it.
DOCUMENT_SOURCE = "partner_document"
REVERSAL_SOURCE = "partner_document_reversal"

#: The backoff, in minutes, for a row RRA could not be reached about (decision 4):
#: 1 → 5 → 15 → 60 → 360, then every six hours **forever**. Forever rather than a
#: give-up count, because there is no state after "give up" that is better than "still
#: queued": the sale is posted, RRA has not been told, and the only correct end is telling
#: them. A row that has been retrying for a week is a row an operator needs to see on the
#: dashboard, which is what the `offline` flag is for — not one the queue should discard.
BACKOFF_MINUTES: tuple[int, ...] = (1, 5, 15, 60, 360)

#: A device with a queued row older than this is flagged `offline` (VSDC §2.2 item 4: the VSDC
#: stops issuing after 24 h without connectivity, so a queue this old is a shop that is about
#: to be unable to sell).
OFFLINE_AFTER = timedelta(hours=24)

#: Statuses a row can still move out of by itself. `sending` is in the list because a process
#: that died mid-send left one there and nothing else will ever finish it.
NON_TERMINAL: frozenset[FiscalOutboxStatus] = frozenset(
    {
        FiscalOutboxStatus.QUEUED,
        FiscalOutboxStatus.SENDING,
        FiscalOutboxStatus.FAILED,
        FiscalOutboxStatus.UNKNOWN,
        FiscalOutboxStatus.NEEDS_RECEIPT,
    }
)

#: The three a person has to resolve. They block the device's queue behind them and no timer
#: clears them — that is the point of each: RRA refused (`failed`), RRA may be holding the
#: sale (`unknown`), or RRA is holding it and Vinea has no receipt (`needs_receipt`).
BLOCKING: frozenset[FiscalOutboxStatus] = frozenset(
    {
        FiscalOutboxStatus.FAILED,
        FiscalOutboxStatus.UNKNOWN,
        FiscalOutboxStatus.NEEDS_RECEIPT,
    }
)


def backoff_for(attempts: int) -> timedelta:
    """How long to wait before attempt number `attempts + 1`.

    Clamped to the last step rather than indexed past the end, which is what makes "then every
    six hours, forever" a property of the table instead of a special case beside it.
    """
    index = min(max(attempts, 1), len(BACKOFF_MINUTES)) - 1
    return timedelta(minutes=BACKOFF_MINUTES[index])


def enqueue(
    db: Session,
    company_id: int,
    *,
    device: FiscalDevice,
    kind: FiscalOutboxKind,
    payload: dict,
    source_doc_type: str | None = None,
    source_doc_id: int | None = None,
    invc_no: int | None = None,
    sar_no: int | None = None,
    now: datetime | None = None,
) -> FiscalOutboxRow:
    """One row, due immediately, at the back of its device's queue.

    `sequence_no` is the row id and is reserved before the insert rather than back-filled
    after it: an `UPDATE` to set it would be a second statement whose failure would leave a
    row at position zero, ahead of every other row on the device — the worst possible place
    for a row that is not meant to be there.
    """
    moment = now or datetime.now(UTC)
    row_id = int(
        db.execute(
            text("SELECT nextval(pg_get_serial_sequence('fiscal_outbox', 'id'))")
        ).scalar_one()
    )
    row = FiscalOutboxRow(
        id=row_id,
        company_id=company_id,
        device_id=device.id,
        kind=kind,
        source_doc_type=source_doc_type,
        source_doc_id=source_doc_id,
        sequence_no=row_id,
        invc_no=invc_no,
        sar_no=sar_no,
        payload=payload,
        status=FiscalOutboxStatus.QUEUED,
        attempts=0,
        next_attempt_at=moment,
    )
    db.add(row)
    db.flush()
    return row


def rows_for_document(
    db: Session, company_id: int, *, source_doc_type: str, source_doc_id: int
) -> list[FiscalOutboxRow]:
    """Every queue row this document produced, oldest first."""
    return list(
        db.scalars(
            select(FiscalOutboxRow)
            .where(
                FiscalOutboxRow.company_id == company_id,
                FiscalOutboxRow.source_doc_type == source_doc_type,
                FiscalOutboxRow.source_doc_id == source_doc_id,
            )
            .order_by(FiscalOutboxRow.sequence_no)
        )
    )


def next_row(
    db: Session, company_id: int, device_id: int, *, now: datetime, lock: bool = True
) -> FiscalOutboxRow | None:
    """The device's **oldest non-terminal row**, if it is due, and nothing otherwise.

    Oldest first and one at a time, so a blocked row blocks what is behind it. Read that
    literally: this does not skip past a `failed` row to find a `queued` one. It takes the
    head of the queue and hands it back only when the head is ready to be sent — a head that
    is `failed`, `unknown` or `needs_receipt` returns `None`, and the device's queue stops
    until a person resolves it.

    `FOR UPDATE SKIP LOCKED` so two drainers never take the same row. `SKIP LOCKED` rather
    than a wait: if another worker holds this device's head, this worker has nothing useful to
    do on this device and should move to the next one.
    """
    query = (
        select(FiscalOutboxRow)
        .where(
            FiscalOutboxRow.company_id == company_id,
            FiscalOutboxRow.device_id == device_id,
            FiscalOutboxRow.status.in_(tuple(NON_TERMINAL)),
        )
        .order_by(FiscalOutboxRow.sequence_no)
        .limit(1)
    )
    if lock:
        query = query.with_for_update(skip_locked=True)
    head = db.scalars(query).first()
    if head is None:
        return None
    if head.status in BLOCKING:
        return None
    if head.next_attempt_at is not None and head.next_attempt_at > now:
        return None
    return head


def head_row(db: Session, company_id: int, device_id: int) -> FiscalOutboxRow | None:
    """The head of the device's queue whatever its state — what the dashboard shows when it
    says a device is stuck, and what `next_row` refused to hand back."""
    return db.scalars(
        select(FiscalOutboxRow)
        .where(
            FiscalOutboxRow.company_id == company_id,
            FiscalOutboxRow.device_id == device_id,
            FiscalOutboxRow.status.in_(tuple(NON_TERMINAL)),
        )
        .order_by(FiscalOutboxRow.sequence_no)
        .limit(1)
    ).first()


def devices_with_work(db: Session, company_id: int) -> Sequence[FiscalDevice]:
    """Active devices holding at least one non-terminal row.

    A suspended device is skipped deliberately: suspension is a decision that this device
    stops talking to RRA, and a drainer that kept sending for it would make the button a lie.
    """
    return list(
        db.scalars(
            select(FiscalDevice)
            .where(
                FiscalDevice.company_id == company_id,
                FiscalDevice.status == FiscalDeviceStatus.ACTIVE,
                select(FiscalOutboxRow.id)
                .where(
                    FiscalOutboxRow.company_id == company_id,
                    FiscalOutboxRow.device_id == FiscalDevice.id,
                    FiscalOutboxRow.status.in_(tuple(NON_TERMINAL)),
                )
                .exists(),
            )
            .order_by(FiscalDevice.id)
        )
    )


def oldest_queued_at(db: Session, company_id: int, device_id: int) -> datetime | None:
    """When the oldest still-unsent row was created — the age the `offline` flag reads."""
    return db.scalar(
        select(func.min(FiscalOutboxRow.created_at)).where(
            FiscalOutboxRow.company_id == company_id,
            FiscalOutboxRow.device_id == device_id,
            FiscalOutboxRow.status.in_(tuple(NON_TERMINAL)),
        )
    )


def is_offline(db: Session, company_id: int, device_id: int, *, now: datetime) -> bool:
    oldest = oldest_queued_at(db, company_id, device_id)
    if oldest is None:
        return False
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=UTC)
    return now - oldest >= OFFLINE_AFTER
