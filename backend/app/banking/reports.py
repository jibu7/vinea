"""Cashbooks, the bank reconciliation report and the bank-account enquiry (P8 decisions 6 and 10).

**These are views over `journal_lines`.** Nothing here stores a figure, caches one, or adjusts
one; every number is derived on read, which is what lets the Cashbooks closing balance be *tied*
to the trial balance rather than merely agree with it by habit.

The tie is the point of decision 6 and is worth stating precisely, because the two sides are
denominated differently:

* **Base closing == the trial balance, for every account.** Σ `base_amount` over the account's
  lines to the date is exactly what `kernel.enquiries.trial_balance` sums. This holds at any
  date, for a base-currency account and a foreign-currency one alike, and it is the tie the tape
  asserts on `BK-RWF` (closing 1 000 500 == TB `1120` at 30 September).
* **Currency closing == `period_balances`, in the account's own currency, at a period end.** A
  foreign account's closing is Σ `amount`, which the trial balance cannot answer — it is a
  base-currency report. `period_balances` carries `debit_amount`/`credit_amount` per
  (period, account, branch, currency), so the currency figure ties there instead. It is a
  **period-end** tie because the cache is per period and has no opinion about a Tuesday; a
  cashbook is closed at a period end, so that is where the tie is wanted.

Both are exposed as functions rather than left to the tests, so the tape, the report's own e2e
and the enquiry all ask the same question of the same code.

The *Reconciled* column is the third reading: the `BRC-` of the locked reconciliation a line's
match belongs to, `matched` for a match not yet locked, and blank for outstanding. It comes from
`matching.match_by_journal_line`, which is also what the GL entry page reads, so a line cannot
say one thing on this report and another there.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.banking import accounts as accounts_service
from app.banking import matching
from app.banking import reconciliation as reconciliation_service
from app.kernel.money import base_currency
from app.models.banking import (
    BankAccount,
    BankMatchJournalLine,
    BankReconciliation,
    BankStatement,
    ReconciliationStatus,
    StatementStatus,
)
from app.models.currency import Currency
from app.models.fiscal import AccountingPeriod
from app.models.journal import JournalEntry, JournalLine, JournalStatus, PeriodBalance
from app.models.partner import Partner

ZERO = Decimal(0)

#: What the *Reconciled* column says for a line whose match has not been locked into a
#: reconciliation yet. Blank means outstanding; a `BRC-` number means signed off.
MATCHED_NOT_LOCKED = "matched"


# --- The tie ------------------------------------------------------------------------------------


def base_balance(
    db: Session, company_id: int, gl_account_id: int, *, as_of: date
) -> Decimal:
    """Σ `base_amount` over an account's posted lines to `as_of`.

    The same arithmetic `trial_balance` does for one account, which is the whole reason this
    function exists rather than the report summing inline: two sums of the same thing in two
    places is how a tie stops being a tie.
    """
    return db.scalar(
        select(func.coalesce(func.sum(JournalLine.base_amount), ZERO))
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == company_id,
            JournalLine.gl_account_id == gl_account_id,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.entry_date <= as_of,
        )
    ) or ZERO


def cached_currency_balance(
    db: Session, company_id: int, gl_account_id: int, *, currency_id: int, as_of: date
) -> Decimal | None:
    """The account's balance in one currency, read from the `period_balances` **cache**.

    `None` when `as_of` is not a period end — the cache is per period and cannot answer for a
    date inside one, and returning a wrong figure would be worse than declining. The caller
    (the tape, the report's e2e) ties the currency closing to this at a period end, which is
    where a cashbook is closed.

    Deliberately the cache rather than a second sum over `journal_lines`: the point of the tie is
    that the derived report and the maintained cache agree, and `verify_period_balances` already
    proves the cache against the lines (ADR-04). Summing the lines twice would prove nothing.
    """
    period = db.scalar(
        select(AccountingPeriod).where(
            AccountingPeriod.company_id == company_id,
            AccountingPeriod.end_date == as_of,
        )
    )
    if period is None:
        return None
    ends = select(AccountingPeriod.id).where(
        AccountingPeriod.company_id == company_id,
        AccountingPeriod.end_date <= as_of,
    )
    total = db.scalar(
        select(
            func.coalesce(
                func.sum(PeriodBalance.debit_amount - PeriodBalance.credit_amount), ZERO
            )
        ).where(
            PeriodBalance.company_id == company_id,
            PeriodBalance.gl_account_id == gl_account_id,
            PeriodBalance.currency_id == currency_id,
            PeriodBalance.period_id.in_(ends),
        )
    )
    return total or ZERO


# --- Cashbooks (decision 6) ---------------------------------------------------------------------


@dataclass(frozen=True)
class CashbookRow:
    """One ledger line on the account, in the account's own currency."""

    journal_line_id: int
    entry_id: int
    entry_date: date
    entry_number: str
    #: Decision 6's *document type* column — `journal_entries.doc_type`, the `RCT`/`PMT`/`CB`
    #: the entry was numbered under. There is no `transaction_type` on a journal line; that
    #: lives on `partner_documents` and is a different question.
    doc_type: str
    module: str
    reference: str | None
    description: str | None
    partner_name: str | None
    #: Credit-positive in the account's sense: money in is a receipt, money out a payment.
    receipt: Decimal
    payment: Decimal
    running_balance: Decimal
    base_amount: Decimal
    #: `BRC-000002`, `matched`, or None for outstanding.
    reconciled: str | None


@dataclass(frozen=True)
class CashbookDetail:
    bank_account_id: int
    code: str
    name: str
    currency_id: int
    currency_code: str
    date_from: date
    date_to: date
    opening_balance: Decimal
    #: The same opening in base currency, so `closing_base` is built from the same two pieces
    #: the currency figure is rather than from a third query.
    opening_base: Decimal
    rows: tuple[CashbookRow, ...] = field(default_factory=tuple)

    @property
    def receipts_total(self) -> Decimal:
        return sum((row.receipt for row in self.rows), ZERO)

    @property
    def payments_total(self) -> Decimal:
        return sum((row.payment for row in self.rows), ZERO)

    @property
    def closing_balance(self) -> Decimal:
        """Opening plus receipts less payments — and equal to the last row's running balance,
        which is the internal check a report of this shape gets for free."""
        return self.opening_balance + self.receipts_total - self.payments_total

    @property
    def closing_base(self) -> Decimal:
        """The same closing in base currency, which is the figure that ties to the trial
        balance for **any** account (`base_closing_ties` asserts it)."""
        return self.opening_base + sum((row.base_amount for row in self.rows), ZERO)


def _reconciled_label(db: Session, company_id: int, journal_line_id: int) -> str | None:
    match = matching.match_by_journal_line(db, company_id, journal_line_id)
    if match is None:
        return None
    if match.reconciliation_id is None:
        return MATCHED_NOT_LOCKED
    return db.scalar(
        select(BankReconciliation.number).where(
            BankReconciliation.company_id == company_id,
            BankReconciliation.id == match.reconciliation_id,
        )
    )


def cashbook_detail(
    db: Session,
    company_id: int,
    *,
    bank_account_id: int,
    date_from: date,
    date_to: date,
) -> CashbookDetail:
    """Every line on the account in the range, with the opening balance before it.

    Amounts are `reconciled_amount` — the account's own currency — because that is what the
    account is *held* in and what its statement is denominated in. There is one definition of
    that figure (`accounts.reconciled_amount`) and this reads it rather than restating it.
    """
    row = accounts_service.get(db, company_id, bank_account_id)
    base = base_currency(db, company_id)
    currency = db.get(Currency, row.currency_id) or base
    amount_column = accounts_service.reconciled_amount_column(row, base.id)

    opening = db.scalar(
        select(func.coalesce(func.sum(amount_column), ZERO))
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == company_id,
            JournalLine.gl_account_id == row.gl_account_id,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.entry_date < date_from,
        )
    ) or ZERO
    # Strictly before `date_from`, like the currency opening above it — `base_balance` is an
    # "on or before" sum, so the boundary day would be counted twice.
    opening_base = db.scalar(
        select(func.coalesce(func.sum(JournalLine.base_amount), ZERO))
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == company_id,
            JournalLine.gl_account_id == row.gl_account_id,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.entry_date < date_from,
        )
    ) or ZERO

    lines = db.execute(
        select(JournalLine, JournalEntry, amount_column, Partner.name)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .outerjoin(
            Partner,
            (Partner.id == JournalLine.partner_id)
            & (Partner.company_id == JournalLine.company_id),
        )
        .where(
            JournalLine.company_id == company_id,
            JournalLine.gl_account_id == row.gl_account_id,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.entry_date >= date_from,
            JournalEntry.entry_date <= date_to,
        )
        .order_by(JournalEntry.entry_date, JournalEntry.id, JournalLine.id)
    ).all()

    running = opening
    rows: list[CashbookRow] = []
    for line, entry, amount, partner_name in lines:
        running += amount
        rows.append(
            CashbookRow(
                journal_line_id=line.id,
                entry_id=entry.id,
                entry_date=entry.entry_date,
                entry_number=entry.number,
                doc_type=entry.doc_type,
                module=entry.module,
                reference=entry.reference,
                description=line.description or entry.description,
                partner_name=partner_name,
                receipt=amount if amount > ZERO else ZERO,
                payment=-amount if amount < ZERO else ZERO,
                running_balance=running,
                base_amount=line.base_amount,
                reconciled=_reconciled_label(db, company_id, line.id),
            )
        )
    return CashbookDetail(
        bank_account_id=row.id,
        code=row.code,
        name=row.name,
        currency_id=currency.id,
        currency_code=currency.code,
        date_from=date_from,
        date_to=date_to,
        opening_balance=opening,
        opening_base=opening_base,
        rows=tuple(rows),
    )


def base_closing_ties(
    db: Session, company_id: int, detail: CashbookDetail
) -> bool:
    """The tie, as a function the tape and the e2e both call.

    `closing_base` against Σ `base_amount` to the date — which is what the trial balance sums
    for the account. A report that only *looked* right would pass a screenshot; this is the
    assertion that says it is the ledger.
    """
    row = db.get(BankAccount, detail.bank_account_id)
    assert row is not None
    return detail.closing_base == base_balance(
        db, company_id, row.gl_account_id, as_of=detail.date_to
    )


def currency_closing_ties(
    db: Session, company_id: int, detail: CashbookDetail
) -> bool | None:
    """The currency-side tie against `period_balances`, or `None` where it cannot be asked.

    `None` when `date_to` is not a period end — see `cached_currency_balance`. A caller that
    treats `None` as a failure would be asserting something the cache cannot answer; the tape
    asserts it at 30 September, which is one.
    """
    row = db.get(BankAccount, detail.bank_account_id)
    assert row is not None
    cached = cached_currency_balance(
        db,
        company_id,
        row.gl_account_id,
        currency_id=detail.currency_id,
        as_of=detail.date_to,
    )
    if cached is None:
        return None
    return detail.closing_balance == cached


@dataclass(frozen=True)
class CashbookSummaryRow:
    bank_account_id: int
    code: str
    name: str
    kind: str
    currency_code: str
    opening_balance: Decimal
    receipts: Decimal
    payments: Decimal
    closing_balance: Decimal
    closing_base: Decimal
    last_reconciled_at: date | None
    last_reconciled_balance: Decimal | None
    last_reconciliation_id: int | None
    unmatched_statement_lines: int
    outstanding_lines: int


def cashbook_summary(
    db: Session, company_id: int, *, date_from: date, date_to: date
) -> list[CashbookSummaryRow]:
    """One row per account (decision 6): the movement in the range and the state of its
    reconciliation.

    Built from `cashbook_detail` per account rather than from a second aggregate query, so the
    summary cannot disagree with the detail a user opens from it — the defect P4 shipped when a
    report read one field and a screen another.
    """
    rows: list[CashbookSummaryRow] = []
    for row in db.scalars(
        select(BankAccount)
        .where(BankAccount.company_id == company_id, BankAccount.is_active)
        .order_by(BankAccount.code)
    ):
        detail = cashbook_detail(
            db,
            company_id,
            bank_account_id=row.id,
            date_from=date_from,
            date_to=date_to,
        )
        rows.append(
            CashbookSummaryRow(
                bank_account_id=row.id,
                code=row.code,
                name=row.name,
                kind=row.kind.value,
                currency_code=detail.currency_code,
                opening_balance=detail.opening_balance,
                receipts=detail.receipts_total,
                payments=detail.payments_total,
                closing_balance=detail.closing_balance,
                closing_base=detail.closing_base,
                last_reconciled_at=row.last_reconciled_at,
                last_reconciled_balance=row.last_reconciled_balance,
                last_reconciliation_id=_latest_locked_id(db, company_id, row.id),
                unmatched_statement_lines=_unmatched_statement_count(db, company_id, row),
                outstanding_lines=_outstanding_count(db, company_id, row, as_of=date_to),
            )
        )
    return rows


def _latest_locked_id(db: Session, company_id: int, bank_account_id: int) -> int | None:
    latest = reconciliation_service.latest_locked(db, company_id, bank_account_id)
    return latest.id if latest is not None else None


def _unmatched_statement_count(db: Session, company_id: int, row: BankAccount) -> int:
    return len(list(db.scalars(matching.unmatched_statement_lines(db, company_id, row))))


def _outstanding_count(
    db: Session, company_id: int, row: BankAccount, *, as_of: date
) -> int:
    effective = matching.effective_match_ids(db, company_id, row, as_of)
    in_effective = (
        select(BankMatchJournalLine.journal_line_id)
        .where(BankMatchJournalLine.company_id == company_id)
        .where(BankMatchJournalLine.match_id.in_(effective or [0]))
    )
    return len(
        list(
            db.scalars(
                select(JournalLine.id)
                .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                .where(
                    JournalLine.company_id == company_id,
                    JournalLine.gl_account_id == row.gl_account_id,
                    JournalEntry.status == JournalStatus.POSTED,
                    JournalEntry.entry_date <= as_of,
                    JournalLine.id.not_in(in_effective),
                )
            )
        )
    )


# --- The bank reconciliation report (decision 6) ------------------------------------------------


@dataclass(frozen=True)
class ReconciliationReport:
    """The statement an accountant signs, and — on a locked one — the two readings side by side.

    `stored` is what the reconciliation **said**, reproduced from the lines that existed when it
    locked. `live` is what the same date computes today. They differ by exactly the late lines,
    which is why both are here: a report that showed only the live figures would silently restate
    a signed document, and one that showed only the stored figures could not explain a difference
    anybody noticed.
    """

    reconciliation_id: int
    number: str
    bank_account_id: int
    bank_account_code: str
    bank_account_name: str
    currency_code: str
    reconciliation_date: date
    status: str
    live: reconciliation_service.Figures
    stored: reconciliation_service.Figures | None
    #: Lines dated inside a locked reconciliation but posted after it — decision 5's late lines,
    #: the *Posted after lock* section. Empty on an open one, which has no "after" yet.
    posted_after_lock: tuple[reconciliation_service.OutstandingLine, ...] = ()


def reconciliation_report(
    db: Session, company_id: int, reconciliation_id: int
) -> ReconciliationReport:
    reconciliation = reconciliation_service.get(db, company_id, reconciliation_id)
    row = accounts_service.get(db, company_id, reconciliation.bank_account_id)
    base = base_currency(db, company_id)
    currency = db.get(Currency, row.currency_id) or base
    locked = reconciliation.status == ReconciliationStatus.LOCKED

    live = reconciliation_service.live_figures(db, company_id, reconciliation)
    stored = (
        reconciliation_service.stored_figures(db, company_id, reconciliation)
        if locked
        else None
    )
    late_ids = (
        set(reconciliation_service.late_lines(db, company_id, reconciliation))
        if locked
        else set()
    )
    return ReconciliationReport(
        reconciliation_id=reconciliation.id,
        number=reconciliation.number,
        bank_account_id=row.id,
        bank_account_code=row.code,
        bank_account_name=row.name,
        currency_code=currency.code,
        reconciliation_date=reconciliation.reconciliation_date,
        status=reconciliation.status.value,
        live=live,
        stored=stored,
        posted_after_lock=tuple(
            line for line in live.outstanding if line.journal_line_id in late_ids
        ),
    )


# --- The bank account enquiry (decision 10) -----------------------------------------------------


@dataclass(frozen=True)
class BankAccountEnquiry:
    """Everything the enquiry screen shows for one account, each figure a link's worth of data.

    Read on demand and stored nowhere. `last_reconciled_*` comes off the master row, which is the
    one cache the phase allows — and invariant clause 7 is what proves it equals the latest locked
    reconciliation rather than drifting from it.
    """

    bank_account_id: int
    code: str
    name: str
    kind: str
    currency_code: str
    book_balance: Decimal
    book_balance_base: Decimal
    last_reconciliation_id: int | None
    last_reconciliation_number: str | None
    last_reconciled_at: date | None
    last_reconciled_balance: Decimal | None
    open_reconciliation_id: int | None
    open_reconciliation_number: str | None
    unmatched_statement_count: int
    unmatched_statement_total: Decimal
    outstanding_count: int
    outstanding_total: Decimal
    latest_statement_id: int | None
    latest_statement_number: str | None
    latest_statement_to: date | None


def bank_account_enquiry(
    db: Session, company_id: int, bank_account_id: int, *, as_of: date | None = None
) -> BankAccountEnquiry:
    row = accounts_service.get(db, company_id, bank_account_id)
    base = base_currency(db, company_id)
    currency = db.get(Currency, row.currency_id) or base
    moment = as_of or date.today()
    amount_column = accounts_service.reconciled_amount_column(row, base.id)

    book = db.scalar(
        select(func.coalesce(func.sum(amount_column), ZERO))
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == company_id,
            JournalLine.gl_account_id == row.gl_account_id,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.entry_date <= moment,
        )
    ) or ZERO

    unmatched = list(db.scalars(matching.unmatched_statement_lines(db, company_id, row)))
    latest_locked = reconciliation_service.latest_locked(db, company_id, row.id)
    standing = reconciliation_service.open_for(db, company_id, row.id)
    # "Outstanding" here is the **live** reading — matches effective at the date — which is what
    # the workspace strip shows and what a person comparing the two screens expects.
    #
    # Deliberately *without* `reconciliation_id`: passing the standing reconciliation's id asks
    # the narrower question clause 4 needs ("is this line in a match carrying *this* id"), and a
    # line already locked into an earlier `BRC-` would answer no and be counted outstanding all
    # over again. The first version did exactly that and
    # `test_the_enquiry_answers_every_figure_decision_10_names` found it: two outstanding lines
    # where the ledger has one.
    outstanding = reconciliation_service.figures(
        db,
        company_id,
        row,
        reconciliation_date=moment,
        statement_balance=ZERO,
    ).outstanding
    statement = db.scalars(
        select(BankStatement)
        .where(
            BankStatement.company_id == company_id,
            BankStatement.bank_account_id == row.id,
            # A void statement is not the latest anything — its lines are out of every
            # listing and its number is history (clause 9).
            BankStatement.status == StatementStatus.OPEN,
        )
        .order_by(BankStatement.id.desc())
    ).first()

    return BankAccountEnquiry(
        bank_account_id=row.id,
        code=row.code,
        name=row.name,
        kind=row.kind.value,
        currency_code=currency.code,
        book_balance=book,
        book_balance_base=base_balance(db, company_id, row.gl_account_id, as_of=moment),
        last_reconciliation_id=latest_locked.id if latest_locked else None,
        last_reconciliation_number=latest_locked.number if latest_locked else None,
        last_reconciled_at=row.last_reconciled_at,
        last_reconciled_balance=row.last_reconciled_balance,
        open_reconciliation_id=standing.id if standing else None,
        open_reconciliation_number=standing.number if standing else None,
        unmatched_statement_count=len(unmatched),
        unmatched_statement_total=sum((line.amount for line in unmatched), ZERO),
        outstanding_count=len(outstanding),
        outstanding_total=sum((line.amount for line in outstanding), ZERO),
        latest_statement_id=statement.id if statement else None,
        latest_statement_number=statement.number if statement else None,
        latest_statement_to=statement.to_date if statement else None,
    )
