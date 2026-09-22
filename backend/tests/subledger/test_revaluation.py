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
from tests.subledger.invariants import assert_subledger_invariants
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


def test_the_control_account_still_reconciles_after_a_revaluation(
    db: Session, open_usd_receivable: Subledger
) -> None:
    """P4's invariant, which is the whole reason the run avoids `1200`.

    A control account's balance is Σ open items **at their booking rates**. A revaluation that
    posted into it would move the balance without moving an open item, and this assertion is
    what would have caught it — so it runs after the pair, not before.
    """
    revaluation.post_revaluation(
        db,
        open_usd_receivable.company_id,
        revaluation_date=MARCH_END,
        role=FxRevaluationRole.BOTH,
        actor=open_usd_receivable.owner,
    )
    db.flush()

    assert_subledger_invariants(db, open_usd_receivable.company_id)


def test_the_gain_is_the_rate_movement_times_what_is_open(
    db: Session, open_usd_receivable: Subledger
) -> None:
    """`difference == open_amount × (rate_at − booking_rate)`, per document.

    Asserted as the identity rather than as a literal, because the literal is already checked
    in `test_the_preview_states_the_arithmetic_document_by_document` — what this adds is that
    the two roundings (carrying and revalued) do not drift apart from the one-step product.
    """
    view = revaluation.preview(
        db,
        open_usd_receivable.company_id,
        revaluation_date=MARCH_END,
        role=FxRevaluationRole.AR,
    )

    for line in view.lines:
        assert line.difference == line.open_amount * (line.rate_at_date - line.booking_rate)


def test_each_currency_gets_its_own_pair_of_lines(db: Session, subledger: Subledger) -> None:
    """Decision 13 posts the gain or loss **per (role, currency)**.

    The posting map is role-agnostic, which is a statement about the *rule* and not about the
    grouping: two foreign currencies moving in opposite directions must not net into one pair
    of lines, because the gain on one and the loss on the other are different facts and a
    revaluation that showed their difference would report neither.

    USD rises 1 320 → 1 350 on 47.20 open: **+1 416**, a gain.
    EUR falls 1 400 → 1 380 on 100 open:   **−2 000**, a loss.
    Netted, they would be a single −584 that is true of nothing.
    """
    post_invoice(
        db, subledger, amount=Decimal("47.20"), currency="USD", exchange_rate=BOOKING_RATE
    )
    post_invoice(
        db, subledger, amount=Decimal(100), currency="EUR", exchange_rate=Decimal(1400)
    )
    _set_rate(db, subledger, MARCH_END, MONTH_END_RATE)
    db.add(
        ExchangeRate(
            company_id=subledger.company_id,
            currency_id=subledger.ledger.cur("EUR"),
            valid_from=MARCH_END,
            rate=Decimal(1380),
        )
    )
    db.flush()

    view = revaluation.preview(
        db, subledger.company_id, revaluation_date=MARCH_END, role=FxRevaluationRole.AR
    )
    groups = view.by_group()
    assert len(groups) == 2, "one group per (role, currency), not one per role"
    assert set(groups.values()) == {Decimal(1416), Decimal(-2000)}

    run = revaluation.post_revaluation(
        db,
        subledger.company_id,
        revaluation_date=MARCH_END,
        role=FxRevaluationRole.AR,
        actor=subledger.owner,
    )
    db.flush()

    # Four lines, not two: the gain and the loss reach different P&L accounts, and `1290`
    # carries both movements rather than their difference.
    posted = _entry_map(db, subledger, run.journal_entry_id)
    assert posted["4410"] == Decimal(-1416)
    assert posted["6955"] == Decimal(2000)
    assert posted["1290"] == Decimal(1416) + Decimal(-2000)

    lines = db.execute(
        select(JournalLine.gl_account_id).where(
            JournalLine.company_id == subledger.company_id,
            JournalLine.entry_id == run.journal_entry_id,
        )
    ).all()
    assert len(lines) == 4


def test_every_role_has_a_scope(db: Session, subledger: Subledger) -> None:
    """The guard that replaced a refusal (P8 decision 8).

    P7 raised `fx_revaluation_role_unsupported` for `bank` and `all`: step 1 rebuilt the enum so
    the column could hold them and step 4 built the scope, and between those points a named
    refusal was the honest answer to an enum value the API accepted and the service could not
    compute. Step 4 deletes both the refusal and the test that pinned it.

    What stands in its place is **totality**: every member of the enum has a scope set, so a
    future value added without one fails here rather than falling off a map as a `KeyError` in
    front of a user. That is the same protection the refusal gave, moved from runtime to the
    suite, and it costs nothing to keep.
    """
    for role in FxRevaluationRole:
        scopes = revaluation.scopes_of(role)
        assert scopes, f"{role.value} covers nothing"
        assert scopes <= {"ar", "ap", "bank"}, f"{role.value} names an unknown scope"

    # Decision 8's map, spelled out — the table the overlap test reads.
    assert revaluation.scopes_of(FxRevaluationRole.AR) == {"ar"}
    assert revaluation.scopes_of(FxRevaluationRole.AP) == {"ap"}
    assert revaluation.scopes_of(FxRevaluationRole.BOTH) == {"ar", "ap"}
    assert revaluation.scopes_of(FxRevaluationRole.BANK) == {"bank"}
    assert revaluation.scopes_of(FxRevaluationRole.ALL) == {"ar", "ap", "bank"}


def test_the_bank_and_all_roles_now_preview_without_refusing(
    db: Session, subledger: Subledger
) -> None:
    """The other half of the deletion: the two roles the scaffold refused now compute.

    A tenant with no foreign-currency bank account has no bank lines, and that is a preview of
    nothing rather than an error — the same way an `ar` run over a tenant with no open foreign
    receivable is.
    """
    for role in (FxRevaluationRole.BANK, FxRevaluationRole.ALL):
        view = revaluation.preview(
            db, subledger.company_id, revaluation_date=MARCH_END, role=role
        )
        assert view.role is role
        assert all(line.scope != "bank" for line in view.lines), (
            "this tenant has no foreign-currency bank account, so there is nothing to revalue"
        )
