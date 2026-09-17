"""Unrealized FX revaluation (decision 13), with every figure worked by hand.

The arithmetic, once, so the literals below are checkable:

  * An AR invoice for **USD 47.20** booked at **1 320** carries at 47.20 × 1 320 = **62 304**.
  * At the month-end rate **1 350** it is worth 47.20 × 1 350 = **63 720**.
  * The difference is **+1 416** — a gain, because a receivable in a currency that rose is
    worth more francs than it was booked at.

  * An AP invoice for **USD 100** booked at **1 320** carries at −132 000 (a payable is a
    credit, so the exposure is negative in ledger sense).
  * At 1 350 it is −135 000, a difference of **−3 000** — a loss, because the same rise makes
    a debt cost more.

Those two are deliberately the same rate movement in opposite directions: one posting map has
to produce a gain for one and a loss for the other without a rule of its own for either.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.kernel.errors import LedgerStateError, PostingError
from app.models.currency import ExchangeRate
from app.models.fiscal import PeriodStatus
from app.models.fiscalization import FxRevaluationRole, FxRevaluationStatus
from app.models.gl import GLAccount
from app.models.journal import JournalEntry, JournalLine
from app.models.partner import PartnerRole
from app.subledger import revaluation
from tests.kernel.conftest import YEAR
from tests.subledger.conftest import Subledger
from tests.subledger.test_documents import post_invoice

MARCH_END = date(YEAR, 3, 31)
APRIL_FIRST = date(YEAR, 4, 1)
BOOKING_RATE = Decimal(1320)
MONTH_END_RATE = Decimal(1350)


def _set_rate(db: Session, sub: Subledger, on: date, rate: Decimal) -> None:
    db.add(
        ExchangeRate(
            company_id=sub.company_id,
            currency_id=sub.ledger.cur("USD"),
            valid_from=on,
            rate=rate,
        )
    )
    db.flush()


def _entry_map(db: Session, sub: Subledger, entry_id: int) -> dict[str, Decimal]:
    rows = db.execute(
        select(GLAccount.code, JournalLine.base_amount)
        .join(
            JournalLine,
            (JournalLine.gl_account_id == GLAccount.id)
            & (JournalLine.company_id == GLAccount.company_id),
        )
        .where(JournalLine.company_id == sub.company_id, JournalLine.entry_id == entry_id)
    )
    out: dict[str, Decimal] = {}
    for code, amount in rows:
        out[code] = out.get(code, Decimal(0)) + amount
    return out


def _balance_at(db: Session, sub: Subledger, code: str, on: date) -> Decimal:
    """The account's balance as of a date, over every entry posted to it."""
    total = db.scalar(
        select(func.coalesce(func.sum(JournalLine.base_amount), 0))
        .join(
            JournalEntry,
            (JournalEntry.id == JournalLine.entry_id)
            & (JournalEntry.company_id == JournalLine.company_id),
        )
        .join(
            GLAccount,
            (GLAccount.id == JournalLine.gl_account_id)
            & (GLAccount.company_id == JournalLine.company_id),
        )
        .where(
            JournalLine.company_id == sub.company_id,
            GLAccount.code == code,
            JournalEntry.entry_date <= on,
        )
    )
    return Decimal(total)


@pytest.fixture
def open_usd_receivable(db: Session, subledger: Subledger) -> Subledger:
    """One open USD receivable, and a month-end rate above the rate it was booked at."""
    post_invoice(
        db,
        subledger,
        amount=Decimal("47.20"),
        currency="USD",
        exchange_rate=BOOKING_RATE,
    )
    _set_rate(db, subledger, MARCH_END, MONTH_END_RATE)
    db.flush()
    return subledger


def test_the_preview_states_the_arithmetic_document_by_document(
    db: Session, open_usd_receivable: Subledger
) -> None:
    view = revaluation.preview(
        db,
        open_usd_receivable.company_id,
        revaluation_date=MARCH_END,
        role=FxRevaluationRole.AR,
    )

    assert len(view.lines) == 1
    line = view.lines[0]
    assert line.open_amount == Decimal("47.20")
    assert line.booking_rate == BOOKING_RATE
    assert line.carrying_base == Decimal(62304)
    assert line.rate_at_date == MONTH_END_RATE
    assert line.revalued_base == Decimal(63720)
    assert line.difference == Decimal(1416)
    assert view.total_difference == Decimal(1416)


def test_a_gain_posts_to_the_revaluation_account_and_never_the_control(
    db: Session, open_usd_receivable: Subledger
) -> None:
    run = revaluation.post_revaluation(
        db,
        open_usd_receivable.company_id,
        revaluation_date=MARCH_END,
        role=FxRevaluationRole.AR,
        actor=open_usd_receivable.owner,
    )
    db.flush()

    assert run.status is FxRevaluationStatus.POSTED
    assert run.journal_entry_id is not None
    assert run.mirror_entry_id is not None

    posted = _entry_map(db, open_usd_receivable, run.journal_entry_id)
    assert posted == {"1290": Decimal(1416), "4410": Decimal(-1416)}
    # The heart of decision 13: `1200` holds Σ open items at booking rates, and a revaluation
    # that touched it would break P4's invariant on its first run.
    assert "1200" not in posted


def test_the_mirror_reverses_it_the_following_day(
    db: Session, open_usd_receivable: Subledger
) -> None:
    run = revaluation.post_revaluation(
        db,
        open_usd_receivable.company_id,
        revaluation_date=MARCH_END,
        role=FxRevaluationRole.AR,
        actor=open_usd_receivable.owner,
    )
    db.flush()

    mirror = db.get(JournalEntry, run.mirror_entry_id)
    assert mirror is not None
    assert mirror.entry_date == APRIL_FIRST
    assert mirror.reverses_entry_id == run.journal_entry_id
    assert _entry_map(db, open_usd_receivable, mirror.id) == {
        "1290": Decimal(-1416),
        "4410": Decimal(1416),
    }


def test_an_ap_exposure_that_grew_is_a_loss(db: Session, subledger: Subledger) -> None:
    """Same rate movement, opposite sign — and no rule in the posting map for either."""
    post_invoice(
        db,
        subledger,
        role=PartnerRole.AP,
        amount=Decimal(100),
        currency="USD",
        exchange_rate=BOOKING_RATE,
    )
    _set_rate(db, subledger, MARCH_END, MONTH_END_RATE)
    db.flush()

    run = revaluation.post_revaluation(
        db,
        subledger.company_id,
        revaluation_date=MARCH_END,
        role=FxRevaluationRole.AP,
        actor=subledger.owner,
    )
    db.flush()

    assert _entry_map(db, subledger, run.journal_entry_id) == {
        "2190": Decimal(-3000),
        "6955": Decimal(3000),
    }


def test_a_second_run_over_the_same_date_is_refused(
    db: Session, open_usd_receivable: Subledger
) -> None:
    revaluation.post_revaluation(
        db,
        open_usd_receivable.company_id,
        revaluation_date=MARCH_END,
        role=FxRevaluationRole.AR,
        actor=open_usd_receivable.owner,
    )
    db.flush()

    with pytest.raises(LedgerStateError) as raised:
        revaluation.post_revaluation(
            db,
            open_usd_receivable.company_id,
            revaluation_date=MARCH_END,
            role=FxRevaluationRole.AR,
            actor=open_usd_receivable.owner,
        )
    assert raised.value.code == "fx_revaluation_exists"

    # And `both` clashes with the AR run it overlaps, rather than revaluing AR a second time.
    with pytest.raises(LedgerStateError) as raised:
        revaluation.post_revaluation(
            db,
            open_usd_receivable.company_id,
            revaluation_date=MARCH_END,
            role=FxRevaluationRole.BOTH,
            actor=open_usd_receivable.owner,
        )
    assert raised.value.code == "fx_revaluation_exists"


def test_a_date_that_is_not_a_period_end_is_refused(
    db: Session, open_usd_receivable: Subledger
) -> None:
    with pytest.raises(PostingError) as raised:
        revaluation.post_revaluation(
            db,
            open_usd_receivable.company_id,
            revaluation_date=date(YEAR, 3, 15),
            role=FxRevaluationRole.AR,
            actor=open_usd_receivable.owner,
        )
    assert raised.value.code == "fx_revaluation_not_period_end"


def test_a_closed_period_is_refused(db: Session, open_usd_receivable: Subledger) -> None:
    march = next(
        period for period in open_usd_receivable.ledger.periods if period.end_date == MARCH_END
    )
    march.status = PeriodStatus.CLOSED
    db.flush()

    with pytest.raises(PostingError) as raised:
        revaluation.post_revaluation(
            db,
            open_usd_receivable.company_id,
            revaluation_date=MARCH_END,
            role=FxRevaluationRole.AR,
            actor=open_usd_receivable.owner,
        )
    assert "closed" in str(raised.value)


def test_reversing_a_run_lets_the_date_be_revalued_again(
    db: Session, open_usd_receivable: Subledger
) -> None:
    run = revaluation.post_revaluation(
        db,
        open_usd_receivable.company_id,
        revaluation_date=MARCH_END,
        role=FxRevaluationRole.AR,
        actor=open_usd_receivable.owner,
    )
    db.flush()

    reversed_run = revaluation.reverse_revaluation(
        db,
        open_usd_receivable.company_id,
        run.id,
        reason="Rate corrected after the close",
        actor=open_usd_receivable.owner,
    )
    db.flush()
    assert reversed_run.status is FxRevaluationStatus.REVERSED
    assert reversed_run.reversal_entry_id is not None

    # A run is undone by a counter-pair, because its own mirror already reversed its entry.
    # What has to cancel is the balance at the revaluation date, where the entry stood alone.
    counter = _entry_map(db, open_usd_receivable, reversed_run.reversal_entry_id)
    assert counter == {"1290": Decimal(-1416), "4410": Decimal(1416)}
    assert _balance_at(db, open_usd_receivable, "1290", MARCH_END) == Decimal(0)
    assert _balance_at(db, open_usd_receivable, "1290", APRIL_FIRST) == Decimal(0)

    again = revaluation.post_revaluation(
        db,
        open_usd_receivable.company_id,
        revaluation_date=MARCH_END,
        role=FxRevaluationRole.AR,
        actor=open_usd_receivable.owner,
    )
    db.flush()
    assert again.status is FxRevaluationStatus.POSTED
    assert again.id != run.id


def test_a_run_with_nothing_to_revalue_posts_nothing_and_holds_its_number(
    db: Session, subledger: Subledger
) -> None:
    """Every document is in the base currency, so there is no exposure and no entry — but the
    run happened and is evidence that it did, so it takes a number of its own. That is what
    `_VALUELESS_REVALUATION` is narrowed to `journal_entry_id IS NULL` for."""
    post_invoice(db, subledger, amount=Decimal(100000))
    db.flush()

    run = revaluation.post_revaluation(
        db,
        subledger.company_id,
        revaluation_date=MARCH_END,
        role=FxRevaluationRole.BOTH,
        actor=subledger.owner,
    )
    db.flush()

    assert run.journal_entry_id is None
    assert run.mirror_entry_id is None
    assert run.number.startswith("FXR-")
    assert revaluation.lines_of(db, subledger.company_id, run.id) == []


def test_the_run_keeps_the_rate_it_used(db: Session, open_usd_receivable: Subledger) -> None:
    """A later correction to `exchange_rates` must not restate a posted revaluation."""
    run = revaluation.post_revaluation(
        db,
        open_usd_receivable.company_id,
        revaluation_date=MARCH_END,
        role=FxRevaluationRole.AR,
        actor=open_usd_receivable.owner,
    )
    db.flush()

    stored = revaluation.lines_of(db, open_usd_receivable.company_id, run.id)
    assert len(stored) == 1
    assert stored[0].rate_at_date == MONTH_END_RATE
    assert stored[0].carrying_base == Decimal(62304)
    assert stored[0].revalued_base == Decimal(63720)
    assert stored[0].difference == Decimal(1416)
