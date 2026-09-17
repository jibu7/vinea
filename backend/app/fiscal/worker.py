"""`python -m app.fiscal.worker` — the process that drains the outbox.

**Postgres is the queue.** ADR-10 scheduled a Redis-backed worker and P4 built none; this is
the first thing in the product that actually needs one, and it still does not need Redis. The
queue is a table with an index on (company, device, sequence_no), the claim is
`SELECT … FOR UPDATE SKIP LOCKED`, and the durability is the transaction the row was written
in. Adding a broker would add a second place a sale could be lost, to buy throughput a shop
issuing receipts does not need. Redis stays where P0 left it.

The loop is deliberately dull: every fifteen seconds, ask which tenants hold a row that is due,
then drain each on **its own tenant-scoped session**. The cross-tenant question is the only
thing that runs under `platform_scope()`, and it reads nothing but company ids — the draining
itself is bound to one tenant by `set_tenant`, exactly as `run_job` is.

Three ways a row is sent, and they are the same code:

* this loop, which is what makes the queue drain when nobody is looking;
* an after-response kick from the endpoint that enqueued, for latency — the `run_job` pattern;
* `POST /fiscal/outbox/drain`, the scheduler's hook, which is `by design` in the rule-14
  register for the same reason `jobs/sweep` is: there is no moment at which a person wants to
  press it.

A failure never stops the loop. A tenant whose drain raises is logged and the next tenant is
drained; a worker that exited on one bad row would stop every other shop's receipts.
"""

import logging
import signal
import time
from datetime import UTC, datetime
from types import FrameType

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, platform_scope, set_tenant
from app.fiscal import drainer, outbox
from app.models.fiscalization import FiscalDevice, FiscalDeviceStatus, FiscalOutboxRow

logger = logging.getLogger("app.fiscal.worker")

#: How often the loop looks. Fifteen seconds because a receipt is printed at a counter with a
#: customer standing at it: the after-response kick is what makes the common case immediate,
#: and this is the floor under everything the kick missed.
POLL_SECONDS = 15.0


def companies_with_due_rows(db: Session, *, now: datetime) -> list[int]:
    """Which tenants hold a row that is due — and nothing else about them.

    The one cross-tenant read in the phase. It returns company ids, so a bug here leaks the
    existence of a queue and no row of anybody's data; everything that follows is scoped to one
    tenant by `set_tenant`.
    """
    with platform_scope(db):
        rows = db.execute(
            select(FiscalOutboxRow.company_id)
            .join(
                FiscalDevice,
                (FiscalDevice.id == FiscalOutboxRow.device_id)
                & (FiscalDevice.company_id == FiscalOutboxRow.company_id),
            )
            .where(
                FiscalDevice.status == FiscalDeviceStatus.ACTIVE,
                FiscalOutboxRow.status.in_(tuple(outbox.NON_TERMINAL)),
                FiscalOutboxRow.next_attempt_at.is_not(None),
                FiscalOutboxRow.next_attempt_at <= now,
            )
            .distinct()
        ).all()
    return [company_id for (company_id,) in rows]


def drain_tenant(company_id: int, *, now: datetime | None = None) -> int:
    """One tenant, on a fresh session bound to it. Commits, and returns how many rows moved."""
    db = SessionLocal()
    try:
        set_tenant(db, company_id)
        outcomes = drainer.drain_company(db, company_id, now=now)
        db.commit()
        return len(outcomes)
    except Exception:  # noqa: BLE001 - one tenant's failure is not every tenant's
        db.rollback()
        logger.exception("fiscal drain failed for company=%s", company_id)
        return 0
    finally:
        db.close()


def tick(*, now: datetime | None = None) -> int:
    """One pass over every tenant with work. Returns the number of rows that moved."""
    moment = now or datetime.now(UTC)
    db = SessionLocal()
    try:
        companies = companies_with_due_rows(db, now=moment)
    finally:
        db.close()
    return sum(drain_tenant(company_id, now=moment) for company_id in companies)


class _Stop:
    """SIGTERM and SIGINT, so a container stop finishes the row in flight rather than losing
    it. The row would roll back to `queued` either way — the transaction is the durability —
    but a clean exit keeps the logs readable."""

    def __init__(self) -> None:
        self.requested = False

    def __call__(self, signum: int, frame: FrameType | None) -> None:
        logger.info("fiscal worker stopping on signal %s", signum)
        self.requested = True


def run(*, poll_seconds: float = POLL_SECONDS, iterations: int | None = None) -> None:
    """The loop. `iterations` bounds it, which is how the suite runs it at all."""
    stop = _Stop()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger.info("fiscal worker started, polling every %ss", poll_seconds)
    passes = 0
    while not stop.requested:
        moved = tick()
        if moved:
            logger.info("fiscal worker sent %s row(s)", moved)
        passes += 1
        if iterations is not None and passes >= iterations:
            return
        # Sleeping in slices so a signal is noticed within a second rather than at the end of
        # the poll interval.
        slept = 0.0
        while slept < poll_seconds and not stop.requested:
            time.sleep(min(1.0, poll_seconds - slept))
            slept += 1.0


if __name__ == "__main__":  # pragma: no cover - the process entry point
    logging.basicConfig(level=logging.INFO)
    run()
