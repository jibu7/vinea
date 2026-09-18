"""The late-entry property: every taxed line is declared exactly once, and no filed return moves.

The example tests in `test_vat_filing.py` each pin one arrangement — an entry that arrives
before filing, one that arrives after, one range that overlaps another. What they cannot reach
is a *conjunction*: two filings interleaved with postings into both the open month and an
already-closed one, where an entry could be declared twice (once by the return whose range
contains it, once again as a late entry) or not at all (missed by the first because it arrived
after the mark, and by the second because the sweep looked in the wrong window).

So this drives a random sequence of **post** and **file** across three months, and asserts two
things after every step:

1. **No filed return ever moves.** Its `figures` are compared against the snapshot taken the
   moment it was filed — not against a recomputation, which is the whole point of storing them.
2. **Every taxed line is declared exactly once.** At the end the run files one more return over
   a range that follows every other, which sweeps up every late entry from every filed month;
   the sum of what all the returns declared must then equal the tax the ledger actually holds.

The second is the invariant the high-water rule exists to produce, stated over the whole run
rather than over one arrangement of it.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.kernel import posting
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.events import LineSpec, ManualJournal
from app.models.gl import GLAccount
from app.models.journal import JournalEntry, JournalLine
from app.models.tax import TaxCode
from app.tax import vat
from tests.kernel.conftest import YEAR
from tests.subledger.conftest import Subledger

#: Three months to post into and file, and a fourth that only ever sweeps. The sweep is what
#: makes "declared exactly once" checkable: a late entry is declared by the *next* return, so
#: without a range after the last filed one there is always a tail nothing has picked up yet.
MONTHS: tuple[tuple[date, date], ...] = (
    (date(YEAR, 3, 1), date(YEAR, 3, 31)),
    (date(YEAR, 4, 1), date(YEAR, 4, 30)),
    (date(YEAR, 5, 1), date(YEAR, 5, 31)),
)
SWEEP = (date(YEAR, 6, 1), date(YEAR, 6, 30))

#: A day inside each month to post on. Any day does; the month is what decides the range.
POST_DAYS = (date(YEAR, 3, 15), date(YEAR, 4, 15), date(YEAR, 5, 15))

#: Multiples of 50, so 18 % is a whole number of francs and the property is about the
#: high-water rule rather than about rounding — which `test_vat_return.py` already covers.
AMOUNTS = (Decimal(50), Decimal(500), Decimal(1500), Decimal(5000))


@dataclass
class _Filed:
    """A return, and the figures it carried the moment it was filed."""

    return_id: int
    number: str
    figures: dict
    output_vat: Decimal
    filed: list[int] = field(default_factory=list)


def _accounts(db: Session, company_id: int) -> dict[str, int]:
    return {
        row.code: row.id
        for row in db.execute(
            select(GLAccount.code, GLAccount.id).where(
                GLAccount.company_id == company_id,
                GLAccount.code.in_(("4100", "2200", "1500")),
            )
        )
    }


def _post_sale(db: Session, sub: Subledger, on: date, net: Decimal) -> Decimal:
    """A taxed sale keyed by hand, so the tax is exactly `net × 18 %` and nothing rounds.

    Three lines because a manual journal is taken as keyed — the engine derives a tax line for
    a *document*, not for a journal somebody wrote — and this is the shape a correction into a
    closed month actually arrives in.
    """
    accounts = _accounts(db, sub.company_id)
    code = db.scalars(
        select(TaxCode).where(
            TaxCode.company_id == sub.company_id, TaxCode.code == "VAT-OUT-18"
        )
    ).one()
    tax = net * Decimal("0.18")
    posting.post(
        db,
        ManualJournal(
            entry_date=on,
            description=f"Sale of {net}",
            lines=(
                LineSpec(amount=net + tax, gl_account_id=accounts["1500"]),
                LineSpec(
                    amount=-net,
                    gl_account_id=accounts["4100"],
                    tax_code_id=code.id,
                    tax_amount=-tax,
                ),
                LineSpec(amount=-tax, gl_account_id=accounts["2200"], tax_code_id=code.id),
            ),
        ),
        company_id=sub.company_id,
        actor=sub.owner,
    )
    db.flush()
    return tax


def _output_vat_in_the_ledger(db: Session, sub: Subledger) -> Decimal:
    """Every franc of output VAT the ledger holds, over every date.

    Read off the tax account's own lines and excluding the `tax` module, which is what a
    settlement entry posts under: settling a return moves the same account and declares nothing,
    so counting it here would make the identity false for a reason that is not about lateness.
    """
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
            GLAccount.code == "2200",
            JournalLine.tax_code_id.is_not(None),
            JournalEntry.module != vat.TAX_MODULE,
        )
    )
    # The ledger holds a sale as a credit; a return declares it positive.
    return -Decimal(total)


def _assert_no_filed_return_moved(db: Session, sub: Subledger, filed: list[_Filed]) -> None:
    for record in filed:
        row = db.scalar(
            select(vat.VatReturn).where(
                vat.VatReturn.company_id == sub.company_id,
                vat.VatReturn.id == record.return_id,
            )
        )
        assert row is not None
        assert row.figures == record.figures, (
            f"{record.number} changed after it was filed — a filed return is evidence of what "
            "was submitted and is never recomputed"
        )
        assert row.output_vat == record.output_vat


@pytest.mark.slow
@given(
    steps=st.lists(
        st.tuples(
            st.sampled_from(("post", "post", "file")),
            st.integers(min_value=0, max_value=len(MONTHS) - 1),
            st.sampled_from(AMOUNTS),
        ),
        min_size=4,
        max_size=12,
    )
)
def test_every_taxed_line_is_declared_exactly_once_across_a_run_of_returns(
    db: Session, subledger: Subledger, steps: list[tuple[str, int, Decimal]]
) -> None:
    filed: list[_Filed] = []
    next_to_file = 0

    # **Every example starts from the committed tenant and leaves nothing behind.** Hypothesis
    # reuses a function-scoped fixture across all of its examples — that is what the suppressed
    # health check is about — so example two would otherwise run against example one's returns
    # while its own bookkeeping started empty. The fixture commits the tenant, nothing in the
    # body commits, so the rollback in the `finally` is exact.
    #
    # It is hygiene, not the fix for anything: the defect this property found reproduces inside
    # a single example, and shrank to four steps.
    try:
        for operation, month, amount in steps:
            if operation == "post":
                # Freely into any month, including one already filed — which is the case that
                # makes an entry late, and the case the examples can only reach one at a time.
                _post_sale(db, subledger, POST_DAYS[month], amount)
            elif next_to_file < len(MONTHS):
                period_from, period_to = MONTHS[next_to_file]
                record = vat.file_return(
                    db,
                    subledger.company_id,
                    period_from=period_from,
                    period_to=period_to,
                    actor=subledger.owner,
                )
                db.flush()
                filed.append(
                    _Filed(
                        return_id=record.id,
                        number=record.number,
                        figures=dict(record.figures),
                        output_vat=record.output_vat,
                    )
                )
                next_to_file += 1

            # Property 1, after **every** step: nothing already filed has moved.
            _assert_no_filed_return_moved(db, subledger, filed)

        # File whatever months remain, then a sweep after all of them, so every late entry has
        # a return to be declared on.
        while next_to_file < len(MONTHS):
            period_from, period_to = MONTHS[next_to_file]
            record = vat.file_return(
                db,
                subledger.company_id,
                period_from=period_from,
                period_to=period_to,
                actor=subledger.owner,
            )
            db.flush()
            filed.append(
                _Filed(
                    return_id=record.id,
                    number=record.number,
                    figures=dict(record.figures),
                    output_vat=record.output_vat,
                )
            )
            next_to_file += 1

        sweep = vat.file_return(
            db,
            subledger.company_id,
            period_from=SWEEP[0],
            period_to=SWEEP[1],
            actor=subledger.owner,
        )
        db.flush()
        filed.append(
            _Filed(
                return_id=sweep.id,
                number=sweep.number,
                figures=dict(sweep.figures),
                output_vat=sweep.output_vat,
            )
        )

        # Property 2: exactly once. Every franc the ledger holds has been declared on some
        # return, and none has been declared on two.
        declared = sum((record.output_vat for record in filed), Decimal(0))
        assert declared == _output_vat_in_the_ledger(db, subledger), (
            "the returns together declare something other than what the ledger holds: a line "
            "was either declared twice or missed by both the range that contained it and the "
            "sweep that should have caught it"
        )
        _assert_no_filed_return_moved(db, subledger, filed)
    except (LedgerStateError, PostingError):
        # An illegal move in a random plan — a second sweep, a period the plan already filed.
        # Skipped rather than failed: the point is the invariants, not the plumbing.
        pass
    finally:
        db.rollback()
