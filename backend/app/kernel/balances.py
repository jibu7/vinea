"""`period_balances` — the verifiable cache (ADR-04).

Maintained inside the posting transaction by `apply_lines`; `verify_period_balances`
recomputes every cell from `journal_lines` and reports drift. The cache is never read by
the P2 enquiries (they go to the lines); it exists for P10 reporting speed and must always
be provably equal to the raw ledger.
"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.journal import JournalEntry, JournalLine, JournalStatus, PeriodBalance

ZERO = Decimal(0)

Cell = tuple[int, int, int, int]  # (period_id, gl_account_id, branch_id, currency_id)


@dataclass
class Movement:
    debit_amount: Decimal = ZERO
    credit_amount: Decimal = ZERO
    debit_base: Decimal = ZERO
    credit_base: Decimal = ZERO

    def add(self, amount: Decimal, base_amount: Decimal) -> None:
        if amount >= 0:
            self.debit_amount += amount
        else:
            self.credit_amount += -amount
        if base_amount >= 0:
            self.debit_base += base_amount
        else:
            self.credit_base += -base_amount


def aggregate(period_id: int, lines: list[JournalLine]) -> dict[Cell, Movement]:
    cells: dict[Cell, Movement] = defaultdict(Movement)
    for line in lines:
        cells[(period_id, line.gl_account_id, line.branch_id, line.currency_id)].add(
            line.amount, line.base_amount
        )
    return cells


def apply_lines(db: Session, entry: JournalEntry, lines: list[JournalLine]) -> None:
    """Upsert the entry's movements. Cells are visited in sorted key order so two concurrent
    postings never take row locks in opposite orders (no deadlock)."""
    cells = aggregate(entry.period_id, lines)
    for key in sorted(cells):
        movement = cells[key]
        period_id, account_id, branch_id, currency_id = key
        statement = (
            insert(PeriodBalance)
            .values(
                company_id=entry.company_id,
                period_id=period_id,
                gl_account_id=account_id,
                branch_id=branch_id,
                currency_id=currency_id,
                debit_amount=movement.debit_amount,
                credit_amount=movement.credit_amount,
                debit_base=movement.debit_base,
                credit_base=movement.credit_base,
            )
            .on_conflict_do_update(
                constraint="uq_period_balances_cell",
                set_={
                    "debit_amount": PeriodBalance.debit_amount + movement.debit_amount,
                    "credit_amount": PeriodBalance.credit_amount + movement.credit_amount,
                    "debit_base": PeriodBalance.debit_base + movement.debit_base,
                    "credit_base": PeriodBalance.credit_base + movement.credit_base,
                    "updated_at": func.now(),
                },
            )
        )
        db.execute(statement)


@dataclass(frozen=True)
class Drift:
    period_id: int
    gl_account_id: int
    branch_id: int
    currency_id: int
    field: str
    cached: Decimal
    recomputed: Decimal


def recompute(db: Session, company_id: int) -> dict[Cell, Movement]:
    debit = case((JournalLine.amount > 0, JournalLine.amount), else_=ZERO)
    credit = case((JournalLine.amount < 0, -JournalLine.amount), else_=ZERO)
    debit_base = case((JournalLine.base_amount > 0, JournalLine.base_amount), else_=ZERO)
    credit_base = case((JournalLine.base_amount < 0, -JournalLine.base_amount), else_=ZERO)
    rows = db.execute(
        select(
            JournalEntry.period_id,
            JournalLine.gl_account_id,
            JournalLine.branch_id,
            JournalLine.currency_id,
            func.coalesce(func.sum(debit), ZERO),
            func.coalesce(func.sum(credit), ZERO),
            func.coalesce(func.sum(debit_base), ZERO),
            func.coalesce(func.sum(credit_base), ZERO),
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == company_id,
            JournalEntry.status == JournalStatus.POSTED,
        )
        .group_by(
            JournalEntry.period_id,
            JournalLine.gl_account_id,
            JournalLine.branch_id,
            JournalLine.currency_id,
        )
    ).all()
    return {
        (row[0], row[1], row[2], row[3]): Movement(row[4], row[5], row[6], row[7]) for row in rows
    }


def verify_period_balances(db: Session, company_id: int) -> list[Drift]:
    """Every cached cell must equal the recomputation and vice versa. Returns the drift
    list — empty means the cache is provably in sync."""
    expected = recompute(db, company_id)
    # Plain rows, not ORM entities: the identity map must not hide what is actually stored.
    cached = {
        (row.period_id, row.gl_account_id, row.branch_id, row.currency_id): row
        for row in db.execute(
            select(
                PeriodBalance.period_id,
                PeriodBalance.gl_account_id,
                PeriodBalance.branch_id,
                PeriodBalance.currency_id,
                PeriodBalance.debit_amount,
                PeriodBalance.credit_amount,
                PeriodBalance.debit_base,
                PeriodBalance.credit_base,
            ).where(PeriodBalance.company_id == company_id)
        ).all()
    }
    drift: list[Drift] = []
    for key in sorted(set(expected) | set(cached)):
        want = expected.get(key, Movement())
        have = cached.get(key)
        for name in ("debit_amount", "credit_amount", "debit_base", "credit_base"):
            cached_value = getattr(have, name) if have is not None else ZERO
            recomputed_value = getattr(want, name)
            if cached_value != recomputed_value:
                drift.append(Drift(*key, name, cached_value, recomputed_value))
    return drift
