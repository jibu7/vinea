"""The costing lock, under *real* concurrency — threads, separate connections, no mocks.

`app/inventory/stock.py` claims that `item_cost_state` is the costing lock: taken `FOR UPDATE`
before anything is valued, so two postings against one item queue instead of both reading the
same average. That is a claim about a running database under contention, and the only way to
find out whether it is true is to contend.

It matters more here than almost anywhere else in the product. A weighted average is a
read-modify-write of shared state: two receipts that both read "10 units worth 1 000" and both
write back their own answer do not produce a wrong *number*, they produce a cache that no
longer matches the moves — and the moves are what the valuation report and the GL both hang
off. Lost-update on the average is the bug that ends with stock not reconciling and nobody able
to say when it started.

The same shape covers the negative-stock policy: `block` is only a guarantee if two threads
cannot both pass the "is there enough?" test against the same balance and both take it.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import engine, set_tenant
from app.inventory import stock as stock_service
from app.kernel.errors import LedgerStateError
from app.kernel.sequences import DocType
from app.models.inventory import ItemCostState, NegativeStockPolicy, StockMove
from tests.inventory.conftest import Stock, document, receive
from tests.inventory.invariants import assert_stock_invariants
from tests.kernel.invariants import assert_ledger_invariants
from tests.subledger.conftest import MARCH

THREADS = 8


def _session_for(company_id: int) -> Session:
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    set_tenant(session, company_id)
    return session


@pytest.mark.concurrency
def test_concurrent_receipts_do_not_lose_an_update_to_the_average(
    db: Session, stock: Stock
) -> None:
    """Eight threads receive into the same item at the same instant.

    Every receipt must land, the cache must equal the replay of the moves, and the average
    must be the one the whole sequence implies — not the one whichever thread committed last
    happened to compute from a stale read.
    """
    db.commit()  # the worker connections can only see committed masters
    barrier = threading.Barrier(THREADS)
    failures: list[BaseException] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        session = _session_for(stock.company_id)
        try:
            barrier.wait()
            stock_service.receive_stock(
                session,
                stock.company_id,
                document=document(
                    DocType.INV_ADJUSTMENT,
                    MARCH,
                    transaction_type_id=stock.type_id("ADJIN"),
                    description=f"concurrent receipt {index}",
                ),
                lines=[
                    stock_service.StockLine(
                        item_id=stock.item.id,
                        warehouse_id=stock.main.id,
                        quantity=Decimal(10),
                        # A different cost per thread, so a lost update changes the answer
                        # rather than hiding inside identical arithmetic.
                        unit_cost=Decimal(100 + index),
                    )
                ],
                actor=stock.owner,
            )
            session.commit()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread
            with lock:
                failures.append(exc)
            session.rollback()
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        for future in [pool.submit(worker, index) for index in range(THREADS)]:
            future.result()

    assert failures == [], f"a concurrent receipt was refused: {failures[:2]}"
    db.expire_all()

    moves = db.execute(
        select(func.count(), func.sum(StockMove.quantity), func.sum(StockMove.value)).where(
            StockMove.company_id == stock.company_id
        )
    ).one()
    # 8 receipts of 10, at 100..107: 10 × (100 + 101 + … + 107) = 10 × 828 = 8 280.
    assert moves == (THREADS, Decimal(80), Decimal(8280))

    state = db.scalars(
        select(ItemCostState).where(ItemCostState.company_id == stock.company_id)
    ).one()
    assert state.average_cost == Decimal("103.5")  # 8 280 / 80, exactly
    assert not stock_service.verify_stock_balances(db, stock.company_id), (
        "the cache no longer matches the moves — a read-modify-write was lost"
    )
    assert_stock_invariants(db, stock.company_id)
    assert_ledger_invariants(db, stock.company_id)


@pytest.mark.concurrency
def test_block_holds_when_two_threads_race_for_the_last_units(
    db: Session, stock: Stock
) -> None:
    """Ten units on the shelf and eight threads each asking for ten.

    Exactly one may win. `block` is not a guarantee at all if two threads can both read "10 on
    hand", both decide there is enough, and both post — the location would go to −70 under a
    policy whose entire job is to refuse the first unit below zero.
    """
    receive(db, stock, quantity=Decimal(10), unit_cost=Decimal(100), on=MARCH)
    assert stock.inventory.settings.negative_stock_policy == NegativeStockPolicy.BLOCK
    db.commit()

    barrier = threading.Barrier(THREADS)
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        session = _session_for(stock.company_id)
        try:
            barrier.wait()
            stock_service.issue_stock(
                session,
                stock.company_id,
                document=document(
                    DocType.INV_ADJUSTMENT,
                    MARCH,
                    transaction_type_id=stock.type_id("ADJOUT"),
                    description=f"concurrent issue {index}",
                ),
                lines=[
                    stock_service.StockLine(
                        item_id=stock.item.id,
                        warehouse_id=stock.main.id,
                        quantity=Decimal(10),
                    )
                ],
                actor=stock.owner,
            )
            session.commit()
            with lock:
                outcomes.append("posted")
        except LedgerStateError as exc:
            session.rollback()
            with lock:
                outcomes.append(exc.code)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        for future in [pool.submit(worker, index) for index in range(THREADS)]:
            future.result()

    assert outcomes.count("posted") == 1, f"more than one issue took the same stock: {outcomes}"
    assert outcomes.count("insufficient_stock") == THREADS - 1, outcomes
    db.expire_all()

    position = db.execute(
        select(func.sum(StockMove.quantity)).where(StockMove.company_id == stock.company_id)
    ).scalar_one()
    assert position == Decimal(0), "the shelf went negative under the block policy"
    assert not stock_service.verify_stock_balances(db, stock.company_id)
    assert_stock_invariants(db, stock.company_id)
    assert_ledger_invariants(db, stock.company_id)


@pytest.mark.concurrency
def test_the_posting_order_sequence_is_unique_under_contention(
    db: Session, stock: Stock
) -> None:
    """`sequence_no` is the order the costing engine saw these moves in, and a duplicate would
    make that order ambiguous — `verify_stock_balances` replays by it, so two moves sharing a
    number is two different possible answers for the same ledger.

    Gaps are fine and expected: a rolled-back posting must not make the next one wait, which
    is exactly why this is a plain sequence rather than a `document_sequences` row. What must
    hold is uniqueness and agreement with the order the locks were taken in.
    """
    db.commit()
    barrier = threading.Barrier(THREADS)

    def worker(index: int) -> None:
        session = _session_for(stock.company_id)
        try:
            barrier.wait()
            stock_service.receive_stock(
                session,
                stock.company_id,
                document=document(
                    DocType.INV_ADJUSTMENT,
                    MARCH,
                    transaction_type_id=stock.type_id("ADJIN"),
                    description=f"sequence probe {index}",
                ),
                lines=[
                    stock_service.StockLine(
                        item_id=stock.item.id,
                        warehouse_id=stock.main.id,
                        quantity=Decimal(1),
                        unit_cost=Decimal(50),
                    )
                ],
                actor=stock.owner,
            )
            session.commit()
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        for future in [pool.submit(worker, index) for index in range(THREADS)]:
            future.result()

    db.expire_all()
    numbers = list(
        db.scalars(
            select(StockMove.sequence_no)
            .where(StockMove.company_id == stock.company_id)
            .order_by(StockMove.sequence_no)
        )
    )

    assert len(numbers) == THREADS
    assert len(set(numbers)) == THREADS, f"two moves share a posting-order number: {numbers}"
    # And the id order agrees with the sequence order, because both are claimed after the
    # costing lock is held.
    by_id = list(
        db.scalars(
            select(StockMove.sequence_no)
            .where(StockMove.company_id == stock.company_id)
            .order_by(StockMove.id)
        )
    )
    assert by_id == numbers
    assert not stock_service.verify_stock_balances(db, stock.company_id)
