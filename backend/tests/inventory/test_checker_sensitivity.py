"""Do the checkers actually catch a wrong database?

Every derived number in this system is allowed to exist only because some function can prove
it against the rows it summarises — `verify_period_balances` for `period_balances`,
`verify_stock_balances` for `stock_balances` and `item_cost_state`, the trial-balance loop for
the ledger as a whole. A prover that has never been shown failing is an assertion about
itself.

So this file corrupts the database on purpose, one row at a time, through raw SQL that goes
around the services entirely, and watches each checker say so. It also pins the thing the P5
step-2 review asked about directly: that `assert_ledger_invariants` still fails on a corrupted
**old** period when it is run in the reduced `trial_balance_dates` mode, which does not rebuild
a trial balance for every historic date on every call.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.inventory import stock as stock_service
from app.kernel.balances import verify_period_balances
from app.kernel.enquiries import trial_balance
from app.models.inventory import ItemCostState, StockBalance
from app.models.journal import PeriodBalance
from tests.inventory.conftest import Stock, receive
from tests.inventory.conftest import ledger as ledger  # noqa: PLC0414 - re-exported fixture
from tests.kernel.conftest import Ledger, post_simple
from tests.kernel.invariants import assert_ledger_invariants
from tests.subledger.conftest import MARCH

APRIL = MARCH + timedelta(days=31)
ZERO = Decimal(0)


# --- (a) The ledger checker ---------------------------------------------------------------


def test_the_per_date_loop_asserts_the_trial_balance_foots_at_every_posting_date(
    db: Session, ledger: Ledger
) -> None:
    """What the loop actually asserts, stated as a test rather than as a comment.

    For every date on which anything was posted, `trial_balance(as_of=date).foots` — debits
    equal credits across the whole chart, cumulatively, for every posted line dated on or
    before that date. Two properties follow, and the second is why the reduced mode is safe:

    * the report is *cumulative*, so the trial balance at the newest date sums every line the
      company has ever posted, including the oldest;
    * it is built from `journal_lines`, not from the `period_balances` cache, so it is a check
      on the ledger rather than on the summary.
    """
    post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(1000), on=MARCH)
    post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(250), on=APRIL)

    march = trial_balance(db, ledger.company_id, as_of=MARCH)
    april = trial_balance(db, ledger.company_id, as_of=APRIL)

    assert march.foots and april.foots
    # Cumulative: April's report contains March's lines.
    assert april.total_debit == march.total_debit + Decimal(250)


def test_the_reduced_mode_still_fails_on_a_corrupted_old_period_balance(
    db: Session, ledger: Ledger
) -> None:
    """The review's question, answered against a running database.

    `trial_balance_dates` lets a caller that runs this suite after every step of a long
    scenario build each date's report once instead of once per step. The worry it raises is
    obvious: if an old date is not revisited, can an old period rot unnoticed?

    No — and not because of the trial balance. `verify_period_balances` recomputes **every**
    cached cell from the journal lines on every call, with no date filter at all, so a
    corrupted March cell is caught by a suite run in July. The per-date loop and the cache
    prover check different things, and only one of them was ever date-shaped.
    """
    post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(1000), on=MARCH)
    post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(250), on=APRIL)
    every_date = {MARCH, APRIL}
    assert_ledger_invariants(db, ledger.company_id, trial_balance_dates=set(every_date))

    march_period = next(p for p in ledger.periods if p.start_date <= MARCH <= p.end_date)
    corrupted = db.scalars(
        select(PeriodBalance).where(
            PeriodBalance.company_id == ledger.company_id,
            PeriodBalance.period_id == march_period.id,
        )
    ).first()
    db.execute(
        text("UPDATE period_balances SET debit_base = debit_base + 7 WHERE id = :id"),
        {"id": corrupted.id},
    )
    db.flush()

    # Reduced mode: every historic date is already in the set, so none is rebuilt. It fails
    # anyway.
    with pytest.raises(AssertionError, match="period_balances drift"):
        assert_ledger_invariants(
            db, ledger.company_id, trial_balance_dates=set(every_date)
        )

    drift = verify_period_balances(db, ledger.company_id)
    assert drift, "the cache prover is what caught it"
    db.rollback()


def test_the_reduced_mode_still_fails_on_a_corrupted_old_journal_line(
    db: Session, ledger: Ledger, admin_engine
) -> None:
    """The harder corruption: not the summary but the ledger itself, in a period nobody is
    looking at any more.

    Reaching it needs the immutability trigger switched off from the owning role, which is the
    point — a posted line cannot be edited through the application at all, so the only way to
    get a wrong one is the way an operator with a psql prompt would. The suite still fails,
    twice over: every posted entry must sum to zero, and the trial balance at the newest date
    is cumulative and therefore contains the corrupted March line.
    """
    post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(1000), on=MARCH)
    post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(250), on=APRIL)
    db.commit()

    # Two triggers stand in the way, and both have to come down: `trg_journal_lines_immutable`
    # refuses the UPDATE outright, and the deferred `trg_journal_lines_balanced` would refuse
    # the COMMIT afterwards because the entry no longer sums to zero. That is worth saying
    # plainly — through the application this corruption is not reachable at all, and even with
    # the owner's own role it takes two deliberate acts.
    with admin_engine.connect() as conn:
        for trigger in ("trg_journal_lines_immutable", "trg_journal_lines_balanced"):
            conn.execute(text(f"ALTER TABLE journal_lines DISABLE TRIGGER {trigger}"))
        conn.execute(
            text(
                "UPDATE journal_lines SET base_amount = base_amount + 5 WHERE id = ("
                "  SELECT l.id FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id"
                "   WHERE e.company_id = :cid AND e.entry_date = :on LIMIT 1)"
            ),
            {"cid": ledger.company_id, "on": MARCH},
        )
        conn.commit()
        for trigger in ("trg_journal_lines_immutable", "trg_journal_lines_balanced"):
            conn.execute(text(f"ALTER TABLE journal_lines ENABLE TRIGGER {trigger}"))
        conn.commit()
    db.rollback()
    db.expire_all()

    with pytest.raises(AssertionError) as excinfo:
        assert_ledger_invariants(db, ledger.company_id, trial_balance_dates={MARCH, APRIL})
    assert "does not balance" in str(excinfo.value)

    # And the cumulative report would have caught it too, at the newest date, without ever
    # revisiting March.
    assert not trial_balance(db, ledger.company_id, as_of=APRIL).foots
    db.rollback()


# --- (b) The stock checker -----------------------------------------------------------------


def test_verify_stock_balances_catches_a_corrupted_stock_balances_row(
    db: Session, stock: Stock
) -> None:
    """`stock_balances` is a cache of the moves. Move the cache and nothing else; the prover
    recomputes from `stock_moves` and reports both numbers."""
    receive(db, stock, quantity=Decimal(10), unit_cost=Decimal(100), on=MARCH)
    assert not stock_service.verify_stock_balances(db, stock.company_id)

    db.execute(text("SELECT set_config('app.stock_service', 'on', true)"))
    db.execute(
        text(
            "UPDATE stock_balances SET quantity = quantity + 3, value = value - 250 "
            "WHERE company_id = :cid"
        ),
        {"cid": stock.company_id},
    )
    db.execute(text("SELECT set_config('app.stock_service', 'off', true)"))

    drift = stock_service.verify_stock_balances(db, stock.company_id)

    assert len(drift.balances) == 1
    row = drift.balances[0]
    assert (row.stored_quantity, row.recomputed_quantity) == (Decimal(13), Decimal(10))
    assert (row.stored_value, row.recomputed_value) == (Decimal(750), Decimal(1000))
    with pytest.raises(AssertionError, match="stock cache drift"):
        from tests.inventory.invariants import assert_stock_invariants

        assert_stock_invariants(db, stock.company_id)
    db.rollback()


def test_verify_stock_balances_catches_a_corrupted_item_cost_state_row(
    db: Session, stock: Stock
) -> None:
    """The other cache, and the one a reader would never question: the average. It is replayed
    from the moves in posting order, including the last average taken while the item had
    stock, so a hand-edited average is caught the same way a hand-edited quantity is."""
    receive(db, stock, quantity=Decimal(10), unit_cost=Decimal(100), on=MARCH)
    assert not stock_service.verify_stock_balances(db, stock.company_id)

    db.execute(text("SELECT set_config('app.stock_service', 'on', true)"))
    db.execute(
        text(
            "UPDATE item_cost_state SET average_cost = 99.5, last_positive_average_cost = 12 "
            "WHERE company_id = :cid"
        ),
        {"cid": stock.company_id},
    )
    db.execute(text("SELECT set_config('app.stock_service', 'off', true)"))

    drift = stock_service.verify_stock_balances(db, stock.company_id)

    assert len(drift.costs) == 1
    row = drift.costs[0]
    assert (row.stored_average, row.recomputed_average) == (Decimal("99.5"), Decimal(100))
    assert (row.stored_last_positive, row.recomputed_last_positive) == (
        Decimal(12),
        Decimal(100),
    )
    db.rollback()


def test_verify_stock_balances_catches_a_cache_row_that_should_not_exist(
    db: Session, stock: Stock
) -> None:
    """Drift is not only a wrong number — it is also a row with no moves behind it at all,
    which is what a partially-rolled-back writer would leave."""
    receive(db, stock, quantity=Decimal(10), unit_cost=Decimal(100), on=MARCH)

    db.execute(text("SELECT set_config('app.stock_service', 'on', true)"))
    db.add(
        StockBalance(
            company_id=stock.company_id,
            item_id=stock.item.id,
            warehouse_id=stock.depot.id,
            quantity=Decimal(4),
            value=Decimal(400),
        )
    )
    db.flush()
    db.execute(text("SELECT set_config('app.stock_service', 'off', true)"))

    drift = stock_service.verify_stock_balances(db, stock.company_id)

    ghost = next(row for row in drift.balances if row.warehouse_id == stock.depot.id)
    assert (ghost.stored_quantity, ghost.recomputed_quantity) == (Decimal(4), ZERO)
    db.rollback()


def test_the_cost_state_prover_replays_rather_than_recomputing_from_the_caches(
    db: Session, stock: Stock
) -> None:
    """Why the average prover has to replay the move ledger and not just divide.

    `last_positive_average_cost` is not a function of the current totals — it is the average as
    it stood at the last moment the item had stock, which only the sequence of moves knows. An
    item issued down to nothing holds no value to divide, and the number that must survive is
    the one from before it emptied.
    """
    receive(db, stock, quantity=Decimal(4), unit_cost=Decimal(250), on=MARCH)
    from tests.inventory.conftest import issue

    issue(db, stock, quantity=Decimal(4), on=MARCH)

    state = db.scalars(
        select(ItemCostState).where(ItemCostState.company_id == stock.company_id)
    ).one()
    assert state.last_positive_average_cost == Decimal(250)
    assert not stock_service.verify_stock_balances(db, stock.company_id)
