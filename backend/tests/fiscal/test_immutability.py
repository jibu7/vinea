"""The P7 tables the database refuses to let anybody rewrite.

`fiscal_receipts` and `fiscal_daily_reports` are guarded by triggers from revision `0022` —
`VN011`, the same shape the kernel uses for posted journal rows — and `vat_returns` is guarded
the same way (that one is pinned in `tests/tax/test_vat_filing.py`, beside the filing it is
about). Each is a **legal document**: a receipt is what the authority signed, a Z is a close, a
filed return is what was submitted. Correcting one is not a matter of an UPDATE, and
architecture rule 3 says the refusal belongs in the database rather than in a convention.

**This file exists because nothing was asserting it.** The guards have been there since step 1
and the suite knew about them only sideways: two sensitivity tests make their breakages on the
ORM's copy *because* the row cannot be written, and step 9's back-fill test met `VN011` by
accident while trying to arrange a clock skew. Each of those is evidence that the trigger fires,
and not one of them would have failed if somebody had dropped it. The Definition of Done names
the immutability of `fiscal_receipts` and `vat_returns` among the guards the sensitivity pass
reverts — and a guard with no test is a guard that pass cannot run.

Each test asserts **both halves**: the refusal, and the one change the trigger deliberately
allows. A trigger that refused everything would be wrong in a way no "it raises" test could
see — a reprint could not be counted, and a filed return could not be withdrawn.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.fiscal import daily, drainer
from app.models.fiscalization import FiscalDailyReport, FiscalReceipt
from tests.fiscal import helpers
from tests.fiscal.conftest import FiscalPosting


@pytest.fixture
def signed(db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client):  # noqa: ANN201
    """One sale, fiscalized end to end, so the device holds exactly one receipt."""
    helpers.receive(fiscal_posting, db)
    helpers.invoice(fiscal_posting, db)
    db.flush()
    drainer.drain_company(db, fiscal_posting.company_id, client=sandbox_client)
    db.flush()
    return fiscal_posting


def refused(db: Session, statement: str) -> str:
    """Run a statement that must be refused, and hand back what the database said.

    Raw SQL rather than the ORM, because what is under test is the **trigger** and an ORM write
    would have to get past SQLAlchemy's own idea of what is dirty first. Inside a SAVEPOINT, so
    the refusal takes the statement with it and leaves the fixture standing — the test has a
    second half to run.
    """
    with pytest.raises(DatabaseError) as raised, db.begin_nested():
        db.execute(text(statement))
    message = str(raised.value)
    assert "VN011" in message or "immutable" in message, message
    return message


def test_a_signed_receipt_refuses_every_change_but_its_copy_count(
    db: Session, signed: FiscalPosting
) -> None:
    receipt_id = db.scalars(
        select(FiscalReceipt.id).where(FiscalReceipt.company_id == signed.company_id)
    ).one()

    assert "immutable" in refused(
        db, f"UPDATE fiscal_receipts SET rcpt_no = rcpt_no + 1 WHERE id = {receipt_id}"
    )
    # The counters are the obvious target; the signature is the one that would matter most.
    refused(db, f"UPDATE fiscal_receipts SET rcpt_sign = 'FORGED' WHERE id = {receipt_id}")
    refused(db, f"DELETE FROM fiscal_receipts WHERE id = {receipt_id}")

    # …and the one change a reprint makes goes through, because CIS §7.18 allows a COPY and
    # decision 11 counts them.
    db.execute(
        text(f"UPDATE fiscal_receipts SET copy_count = copy_count + 1 WHERE id = {receipt_id}")
    )
    db.flush()
    db.expire_all()
    assert db.get(FiscalReceipt, receipt_id).copy_count == 1


def test_a_closed_day_refuses_everything(db: Session, signed: FiscalPosting) -> None:
    """A Z has no exempt column at all: it is a close, and there is nothing about it to amend."""
    report = daily.close_day(
        db,
        signed.company_id,
        signed.device.id,
        actor=signed.owner,
        now=datetime.now(UTC) + timedelta(seconds=2),
    )
    db.flush()
    report_id = report.id

    refused(db, f"UPDATE fiscal_daily_reports SET queued_rows = 9 WHERE id = {report_id}")
    refused(db, f"DELETE FROM fiscal_daily_reports WHERE id = {report_id}")
    # Including the mark 0026 added: a Z that could be told it owned a different run of
    # receipts would be a Z whose figures and whose membership are about different sets, which
    # is the state `assert_fiscal_invariants` clause 12 exists to make unreachable.
    refused(
        db, f"UPDATE fiscal_daily_reports SET high_water_rcpt_no = 99 WHERE id = {report_id}"
    )

    db.expire_all()
    stored = db.get(FiscalDailyReport, report_id)
    assert stored.queued_rows == 0
    assert stored.high_water_rcpt_no == 1
