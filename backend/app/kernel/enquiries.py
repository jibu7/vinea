"""GL enquiries (Appendix C: Reports → GL → Account transactions, Trial Balance).

Both read the raw `journal_lines` (never `period_balances`), so they are correct by
construction at any date, including mid-period.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, case, func, select, tuple_
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models.gl import AccountClass, GLAccount
from app.models.journal import JournalEntry, JournalLine, JournalStatus

ZERO = Decimal(0)


@dataclass(frozen=True)
class TrialBalanceRow:
    gl_account_id: int
    code: str
    name: str
    class_: AccountClass
    debit: Decimal
    credit: Decimal

    @property
    def net(self) -> Decimal:
        return self.debit - self.credit


@dataclass(frozen=True)
class TrialBalance:
    as_of: date
    branch_id: int | None
    project_id: int | None
    rows: list[TrialBalanceRow]

    @property
    def total_debit(self) -> Decimal:
        return sum((row.debit for row in self.rows), ZERO)

    @property
    def total_credit(self) -> Decimal:
        return sum((row.credit for row in self.rows), ZERO)

    @property
    def foots(self) -> bool:
        return self.total_debit == self.total_credit


def trial_balance(
    db: Session,
    company_id: int,
    *,
    as_of: date,
    branch_id: int | None = None,
    project_id: int | None = None,
) -> TrialBalance:
    """Cumulative base-currency debit/credit per account for all posted lines dated on or
    before `as_of`. Filtering by branch/project keeps the report footing only when every
    entry is dimension-balanced; the unfiltered TB always foots (Σ base_amount = 0)."""
    debit = case((JournalLine.base_amount > 0, JournalLine.base_amount), else_=ZERO)
    credit = case((JournalLine.base_amount < 0, -JournalLine.base_amount), else_=ZERO)
    statement = (
        select(
            GLAccount.id,
            GLAccount.code,
            GLAccount.name,
            GLAccount.class_,
            func.coalesce(func.sum(debit), ZERO),
            func.coalesce(func.sum(credit), ZERO),
        )
        .join(JournalLine, JournalLine.gl_account_id == GLAccount.id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            GLAccount.company_id == company_id,
            JournalLine.company_id == company_id,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.entry_date <= as_of,
        )
        .group_by(GLAccount.id, GLAccount.code, GLAccount.name, GLAccount.class_)
        .order_by(GLAccount.code)
    )
    if branch_id is not None:
        statement = statement.where(JournalLine.branch_id == branch_id)
    if project_id is not None:
        statement = statement.where(JournalLine.project_id == project_id)
    rows = [
        TrialBalanceRow(
            gl_account_id=row[0],
            code=row[1],
            name=row[2],
            class_=row[3],
            debit=row[4],
            credit=row[5],
        )
        for row in db.execute(statement).all()
    ]
    return TrialBalance(as_of=as_of, branch_id=branch_id, project_id=project_id, rows=rows)


@dataclass(frozen=True)
class AccountTransaction:
    line_id: int
    entry_id: int
    entry_number: str
    entry_date: date
    description: str | None
    branch_id: int
    project_id: int | None
    currency_id: int
    amount: Decimal
    base_amount: Decimal
    running_base: Decimal


@dataclass(frozen=True)
class AccountTransactions:
    gl_account_id: int
    date_from: date
    date_to: date
    opening_base: Decimal
    items: list[AccountTransaction]
    next_cursor: int | None


def account_transactions(
    db: Session,
    company_id: int,
    gl_account_id: int,
    *,
    date_from: date,
    date_to: date,
    branch_id: int | None = None,
    project_id: int | None = None,
    cursor: int | None = None,
    limit: int = 100,
) -> AccountTransactions:
    """Lines on one account in (entry_date, entry_id, line id) order with a running
    base-currency balance. `opening_base` is the balance brought forward before `date_from`;
    paging continues the running balance exactly from the cursor line."""
    account = db.get(GLAccount, gl_account_id)
    if account is None or account.company_id != company_id:
        raise NotFoundError("GL account not found")

    def _scoped(statement: Select) -> Select:
        statement = statement.where(JournalLine.gl_account_id == gl_account_id)
        if branch_id is not None:
            statement = statement.where(JournalLine.branch_id == branch_id)
        if project_id is not None:
            statement = statement.where(JournalLine.project_id == project_id)
        return statement

    sum_base = func.coalesce(func.sum(JournalLine.base_amount), ZERO)
    opening = db.scalar(
        _scoped(
            select(sum_base)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(
                JournalLine.company_id == company_id,
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.entry_date < date_from,
            )
        )
    )
    running = opening if opening is not None else ZERO

    order_key = tuple_(JournalEntry.entry_date, JournalEntry.id, JournalLine.id)
    in_range = _scoped(
        select(JournalLine, JournalEntry)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == company_id,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.entry_date >= date_from,
            JournalEntry.entry_date <= date_to,
        )
        .order_by(JournalEntry.entry_date, JournalEntry.id, JournalLine.id)
    )
    if cursor is not None:
        anchor = db.execute(
            select(JournalEntry.entry_date, JournalEntry.id, JournalLine.id)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(JournalLine.id == cursor, JournalLine.company_id == company_id)
        ).one_or_none()
        if anchor is None:
            raise NotFoundError("Cursor line not found")
        before_cursor = _scoped(
            select(sum_base)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(
                JournalLine.company_id == company_id,
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.entry_date >= date_from,
                JournalEntry.entry_date <= date_to,
                order_key <= tuple_(*anchor),
            )
        )
        running += db.scalar(before_cursor) or ZERO
        in_range = in_range.where(order_key > tuple_(*anchor))

    rows = db.execute(in_range.limit(limit + 1)).all()
    has_more = len(rows) > limit
    items: list[AccountTransaction] = []
    for line, entry in rows[:limit]:
        running += line.base_amount
        items.append(
            AccountTransaction(
                line_id=line.id,
                entry_id=entry.id,
                entry_number=entry.number,
                entry_date=entry.entry_date,
                description=line.description or entry.description,
                branch_id=line.branch_id,
                project_id=line.project_id,
                currency_id=line.currency_id,
                amount=line.amount,
                base_amount=line.base_amount,
                running_base=running,
            )
        )
    return AccountTransactions(
        gl_account_id=gl_account_id,
        date_from=date_from,
        date_to=date_to,
        opening_base=opening if opening is not None else ZERO,
        items=items,
        next_cursor=items[-1].line_id if has_more and items else None,
    )
