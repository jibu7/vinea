"""ADR-07: gapless numbering under *real* concurrency (threads, separate connections)."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db import engine, set_tenant
from app.kernel.sequences import DocType, claim_number, ensure_sequence, format_number
from app.models.journal import DocumentSequence, JournalEntry
from tests.kernel.conftest import YEAR, Ledger, post_simple
from tests.kernel.invariants import assert_ledger_invariants

THREADS = 12
CLAIMS_PER_THREAD = 5


def _session_for(company_id: int) -> Session:
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    set_tenant(session, company_id)
    return session


def test_claims_are_strictly_consecutive_under_concurrency(db: Session, ledger: Ledger) -> None:
    barrier = threading.Barrier(THREADS)
    results: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        session = _session_for(ledger.company_id)
        try:
            barrier.wait()
            for _ in range(CLAIMS_PER_THREAD):
                claimed = claim_number(session, ledger.company_id, DocType.JOURNAL)
                session.commit()
                with lock:
                    results.append(claimed.sequence_no)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        for future in [pool.submit(worker) for _ in range(THREADS)]:
            future.result()

    total = THREADS * CLAIMS_PER_THREAD
    assert sorted(results) == list(range(1, total + 1))  # no gaps, no duplicates
    sequence = db.scalars(
        select(DocumentSequence).where(
            DocumentSequence.company_id == ledger.company_id,
            DocumentSequence.doc_type == DocType.JOURNAL,
        )
    ).one()
    assert sequence.next_number == total + 1


def test_rolled_back_claim_does_not_consume_a_number(db: Session, ledger: Ledger) -> None:
    first = claim_number(db, ledger.company_id, DocType.CASHBOOK)
    db.rollback()
    set_tenant(db, ledger.company_id)
    second = claim_number(db, ledger.company_id, DocType.CASHBOOK)
    db.commit()

    assert first.sequence_no == second.sequence_no == 1
    assert second.number == format_number("CB-", 1) == "CB-000001"


def test_lazy_sequence_creation_is_race_safe(db: Session, ledger: Ledger) -> None:
    """Two sessions racing to create the same unseeded sequence must both end up claiming
    from one row (NULLS NOT DISTINCT + ON CONFLICT DO NOTHING)."""
    barrier = threading.Barrier(2)
    numbers: list[int] = []

    def worker() -> None:
        session = _session_for(ledger.company_id)
        try:
            barrier.wait()
            ensure_sequence(session, ledger.company_id, "INV")
            claimed = claim_number(session, ledger.company_id, "INV")
            session.commit()
            numbers.append(claimed.sequence_no)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in [pool.submit(worker) for _ in range(2)]:
            future.result()

    assert sorted(numbers) == [1, 2]
    rows = db.scalars(
        select(DocumentSequence).where(
            DocumentSequence.company_id == ledger.company_id, DocumentSequence.doc_type == "INV"
        )
    ).all()
    assert len(rows) == 1


def test_branch_sequence_is_used_when_present(db: Session, ledger: Ledger) -> None:
    musanze = ledger.branches["MUS"].id
    ensure_sequence(db, ledger.company_id, DocType.JOURNAL, branch_id=musanze, prefix="MUS-JE-")

    branch_claim = claim_number(db, ledger.company_id, DocType.JOURNAL, branch_id=musanze)
    main_claim = claim_number(
        db, ledger.company_id, DocType.JOURNAL, branch_id=ledger.main_branch.id
    )

    assert branch_claim.number == "MUS-JE-000001"
    assert main_claim.number == "JE-000001"  # falls back to the company-wide sequence
    db.rollback()


def test_concurrent_postings_stay_gapless_and_balanced(db: Session, ledger: Ledger) -> None:
    """The DoD parallelism test: threads post full entries simultaneously."""
    barrier = threading.Barrier(THREADS)

    def worker(index: int) -> None:
        session = _session_for(ledger.company_id)
        try:
            barrier.wait()
            for offset in range(3):
                post_simple(
                    session,
                    ledger,
                    debit="6500",
                    credit="2300",
                    amount=Decimal(1000 + index * 10 + offset),
                    on=date(YEAR, 1 + (index % 6), 10),
                    description=f"thread {index} entry {offset}",
                )
                session.commit()
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        for future in [pool.submit(worker, i) for i in range(THREADS)]:
            future.result()

    set_tenant(db, ledger.company_id)
    numbers = sorted(
        int(number[3:])
        for number in db.scalars(
            select(JournalEntry.number).where(JournalEntry.company_id == ledger.company_id)
        )
    )
    assert numbers == list(range(1, THREADS * 3 + 1))
    assert_ledger_invariants(db, ledger.company_id)
