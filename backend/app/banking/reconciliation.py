"""The reconciliation: dated proof that the ledger and the bank agree (P8 decision 5).

**The identity**, computed by one function and shown live while the reconciliation is open:

    ledger_balance      = Σ reconciled amounts of the account's lines dated <= the date
    outstanding         = the same sum over lines not in a match *effective* at that date
    unmatched_statement = the account's live statement lines dated <= the date, in no match
    difference          = statement_balance - (ledger_balance - outstanding)

`ledger_balance - outstanding` is "what the bank should be showing": the ledger, less the money
the ledger knows about that the bank does not yet. A lock is refused at anything but zero, and
refused outright while a statement line is unmatched — because a difference of zero with a
statement line nobody has explained is two errors cancelling, not a reconciliation.

**Once locked, nothing changes.** The four figures, the outstanding items and
`high_water_line_id` are stored, and a later posting — even one dated inside the period — moves
none of them. A line dated on or before the date but posted after the lock (`id >
high_water_line_id`) is a **late line**: outstanding in the *next* reconciliation, flagged
"dated inside BRC-n" on the workspace and the report, and never folded back. Membership by id
rather than by clock, which is P7's finding on the VAT return and the Z report both.

**Reopen** takes the signature back: only the account's latest locked reconciliation, with a
reason, audited. Its matches stand — withdrawing the proof is not withdrawing the assertions
that went into it — but they lose their `reconciliation_id`, so they are free to be unmatched
and to join whatever locks next.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.banking import accounts as accounts_service
from app.banking import matching
from app.core.errors import NotFoundError
from app.kernel.errors import LedgerStateError
from app.kernel.money import base_currency
from app.kernel.sequences import DocType, claim_number
from app.models.banking import (
    BankAccount,
    BankAccountKind,
    BankMatch,
    BankMatchJournalLine,
    BankReconciliation,
    BankStatementLine,
    ReconciliationStatus,
)
from app.models.journal import JournalEntry, JournalLine
from app.models.user import User
from app.services.audit import record_audit

ZERO = Decimal(0)


@dataclass(frozen=True)
class OutstandingLine:
    """A ledger line the bank has not shown yet — an unpresented payment (negative) or a
    deposit in transit (positive)."""

    journal_line_id: int
    entry_id: int
    entry_number: str
    entry_date: date
    doc_type: str
    description: str | None
    amount: Decimal
    #: Set where this line was posted *after* a locked reconciliation whose date it falls
    #: inside — decision 5's late line. The workspace and the report flag it by name.
    dated_inside: str | None = None

    def as_snapshot(self) -> dict[str, Any]:
        return {
            "journal_line_id": self.journal_line_id,
            "entry_number": self.entry_number,
            "entry_date": self.entry_date.isoformat(),
            "amount": str(self.amount),
        }


@dataclass(frozen=True)
class Figures:
    """What the strip shows, live while the reconciliation is open."""

    reconciliation_date: date
    statement_balance: Decimal
    ledger_balance: Decimal
    outstanding_total: Decimal
    difference: Decimal
    outstanding: list[OutstandingLine]
    unmatched_statement: list[BankStatementLine]

    @property
    def unmatched_statement_count(self) -> int:
        return len(self.unmatched_statement)

    @property
    def adjusted_bank_balance(self) -> Decimal:
        """*Balance per bank statement* plus deposits in transit less unpresented payments —
        the figure the reconciliation report foots to, and the one that must equal the cashbook
        balance. It is `ledger_balance` by construction at a zero difference."""
        return self.statement_balance + self.outstanding_total


def figures(
    db: Session,
    company_id: int,
    row: BankAccount,
    *,
    reconciliation_date: date,
    statement_balance: Decimal,
    high_water_line_id: int | None = None,
    reconciliation_id: int | None = None,
) -> Figures:
    """The one computation. Every caller reads it — the workspace strip, the lock's refusals,
    the report, and `assert_bank_invariants` clause 4.

    `high_water_line_id` and `reconciliation_id` are what make it able to reproduce a *locked*
    reconciliation rather than only compute a live one: with them it sees the lines that
    existed at the lock (`id <= high_water`) and counts a line as reconciled only where its
    match carries **this** reconciliation's id. That second part matters — a match made after
    the lock, effective at that date or not, belongs to a later reconciliation, and reading
    effectiveness instead would let it move a figure that is meant to be a permanent record.
    """
    base = base_currency(db, company_id).id
    ledger_statement = (
        select(JournalLine, JournalEntry)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.company_id == company_id,
            JournalLine.gl_account_id == row.gl_account_id,
            JournalEntry.entry_date <= reconciliation_date,
        )
    )
    if high_water_line_id is not None:
        ledger_statement = ledger_statement.where(JournalLine.id <= high_water_line_id)

    reconciled_line_ids = (
        _lines_in_this_reconciliation(db, company_id, reconciliation_id)
        if reconciliation_id is not None
        else _lines_in_effective_matches(db, company_id, row, reconciliation_date)
    )

    ledger_balance = ZERO
    outstanding: list[OutstandingLine] = []
    late = _late_line_labels(db, company_id, row) if reconciliation_id is None else {}
    for line, entry in db.execute(ledger_statement).all():
        amount = accounts_service.reconciled_amount(line, row, base)
        ledger_balance += amount
        if line.id not in reconciled_line_ids:
            outstanding.append(
                OutstandingLine(
                    journal_line_id=line.id,
                    entry_id=entry.id,
                    entry_number=entry.number,
                    entry_date=entry.entry_date,
                    doc_type=entry.doc_type,
                    description=line.description or entry.description,
                    amount=amount,
                    dated_inside=late.get(line.id),
                )
            )
    outstanding_total = sum((item.amount for item in outstanding), ZERO)
    unmatched = (
        []
        if reconciliation_id is not None
        else list(
            db.scalars(
                matching.unmatched_statement_lines(
                    db, company_id, row, on_or_before=reconciliation_date
                ).order_by(BankStatementLine.value_date, BankStatementLine.id)
            )
        )
    )
    return Figures(
        reconciliation_date=reconciliation_date,
        statement_balance=statement_balance,
        ledger_balance=ledger_balance,
        outstanding_total=outstanding_total,
        difference=statement_balance - (ledger_balance - outstanding_total),
        outstanding=sorted(outstanding, key=lambda item: (item.entry_date, item.journal_line_id)),
        unmatched_statement=unmatched,
    )


def _lines_in_effective_matches(
    db: Session, company_id: int, row: BankAccount, on_date: date
) -> set[int]:
    effective = matching.effective_match_ids(db, company_id, row, on_date)
    if not effective:
        return set()
    return set(
        db.scalars(
            select(BankMatchJournalLine.journal_line_id).where(
                BankMatchJournalLine.company_id == company_id,
                BankMatchJournalLine.match_id.in_(effective),
            )
        )
    )


def _lines_in_this_reconciliation(
    db: Session, company_id: int, reconciliation_id: int
) -> set[int]:
    """The assignment made at lock **is** the membership (clause 4). Not a date comparison:
    a match created afterwards may well be effective at the locked date, and counting it would
    restate a figure somebody signed."""
    assigned = select(BankMatch.id).where(
        BankMatch.company_id == company_id,
        BankMatch.reconciliation_id == reconciliation_id,
    )
    return set(
        db.scalars(
            select(BankMatchJournalLine.journal_line_id).where(
                BankMatchJournalLine.company_id == company_id,
                BankMatchJournalLine.match_id.in_(assigned),
            )
        )
    )


def _late_line_labels(db: Session, company_id: int, row: BankAccount) -> dict[int, str]:
    """`journal_lines.id` → the `BRC-` number whose date it falls inside but whose lock it
    missed.

    The workspace and the report both want to say "dated inside BRC-2" rather than merely
    "outstanding", because the two call for different actions: an ordinary outstanding item is
    waiting for the bank, and a late line is a posting somebody made after the month was
    signed off. The locked reconciliation's own figures do not move either way.
    """
    labels: dict[int, str] = {}
    locked = db.scalars(
        select(BankReconciliation)
        .where(
            BankReconciliation.company_id == company_id,
            BankReconciliation.bank_account_id == row.id,
            BankReconciliation.status == ReconciliationStatus.LOCKED,
        )
        .order_by(BankReconciliation.reconciliation_date)
    ).all()
    for reconciliation in locked:
        if reconciliation.high_water_line_id is None:
            continue
        rows = db.scalars(
            select(JournalLine.id)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(
                JournalLine.company_id == company_id,
                JournalLine.gl_account_id == row.gl_account_id,
                JournalLine.id > reconciliation.high_water_line_id,
                JournalEntry.entry_date <= reconciliation.reconciliation_date,
            )
        ).all()
        for line_id in rows:
            labels.setdefault(line_id, reconciliation.number)
    return labels


def late_lines(db: Session, company_id: int, reconciliation: BankReconciliation) -> list[int]:
    """Lines dated inside this locked reconciliation that were posted after its lock — the
    report's *Posted after lock* section."""
    if reconciliation.high_water_line_id is None:
        return []
    row = accounts_service.get(db, company_id, reconciliation.bank_account_id)
    return list(
        db.scalars(
            select(JournalLine.id)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(
                JournalLine.company_id == company_id,
                JournalLine.gl_account_id == row.gl_account_id,
                JournalLine.id > reconciliation.high_water_line_id,
                JournalEntry.entry_date <= reconciliation.reconciliation_date,
            )
            .order_by(JournalLine.id)
        )
    )


# --- The lifecycle ------------------------------------------------------------------------------


def get(db: Session, company_id: int, reconciliation_id: int) -> BankReconciliation:
    row = db.get(BankReconciliation, reconciliation_id)
    if row is None or row.company_id != company_id:
        raise NotFoundError("Reconciliation not found")
    return row


def list_reconciliations(
    db: Session, company_id: int, *, bank_account_id: int | None = None, limit: int = 50
) -> list[BankReconciliation]:
    statement = select(BankReconciliation).where(BankReconciliation.company_id == company_id)
    if bank_account_id is not None:
        statement = statement.where(BankReconciliation.bank_account_id == bank_account_id)
    return list(
        db.scalars(
            statement.order_by(
                BankReconciliation.reconciliation_date.desc(), BankReconciliation.id.desc()
            ).limit(limit)
        )
    )


def open_for(db: Session, company_id: int, bank_account_id: int) -> BankReconciliation | None:
    return db.scalar(
        select(BankReconciliation).where(
            BankReconciliation.company_id == company_id,
            BankReconciliation.bank_account_id == bank_account_id,
            BankReconciliation.status == ReconciliationStatus.OPEN,
        )
    )


def latest_locked(
    db: Session, company_id: int, bank_account_id: int
) -> BankReconciliation | None:
    return db.scalar(
        select(BankReconciliation)
        .where(
            BankReconciliation.company_id == company_id,
            BankReconciliation.bank_account_id == bank_account_id,
            BankReconciliation.status == ReconciliationStatus.LOCKED,
        )
        .order_by(
            BankReconciliation.reconciliation_date.desc(), BankReconciliation.id.desc()
        )
        .limit(1)
    )


def default_statement_balance(
    db: Session, company_id: int, row: BankAccount, on_date: date
) -> Decimal | None:
    """The latest live statement line's `balance_after` on or before the date.

    `None` where the account's format carries no balance column, or where no statement reaches
    that far — the user keys it, which is also what paper mode does.
    """
    return db.scalar(
        select(BankStatementLine.balance_after)
        .where(
            BankStatementLine.company_id == company_id,
            BankStatementLine.bank_account_id == row.id,
            BankStatementLine.is_void.is_(False),
            BankStatementLine.value_date <= on_date,
            BankStatementLine.balance_after.is_not(None),
        )
        .order_by(BankStatementLine.value_date.desc(), BankStatementLine.id.desc())
        .limit(1)
    )


def open_reconciliation(
    db: Session,
    company_id: int,
    *,
    bank_account_id: int,
    reconciliation_date: date,
    statement_balance: Decimal | None = None,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> BankReconciliation:
    """Open one. Numbered here rather than at lock: it is nameable from this moment, it may be
    reopened, and an auditor following the `BRC-` run must not find a hole where one was opened
    and never locked (P5's count-session argument)."""
    row = accounts_service.get(db, company_id, bank_account_id)
    replayed = _replay(db, company_id, idempotency_key, idempotency_hash)
    if replayed is not None:
        return replayed
    if row.kind != BankAccountKind.BANK:
        raise LedgerStateError(
            f"{row.code} is a cash account; a till is counted, not reconciled",
            code="reconciliation_needs_bank",
            field_errors={"bank_account_id": ["a cash account has no reconciliation"]},
        )
    standing = open_for(db, company_id, bank_account_id)
    if standing is not None:
        raise LedgerStateError(
            f"{standing.number} is already open on {row.code}",
            code="reconciliation_open_exists",
            field_errors={"bank_account_id": [f"{standing.number} is still open"]},
        )
    previous = latest_locked(db, company_id, bank_account_id)
    if previous is not None and reconciliation_date <= previous.reconciliation_date:
        raise LedgerStateError(
            f"{previous.number} is locked at {previous.reconciliation_date.isoformat()}; "
            "the next reconciliation must be dated after it",
            code="reconciliation_date_order",
            field_errors={
                "reconciliation_date": [
                    f"must be after {previous.reconciliation_date.isoformat()}"
                ]
            },
        )
    balance = statement_balance
    if balance is None:
        balance = default_statement_balance(db, company_id, row, reconciliation_date)
    if balance is None:
        raise LedgerStateError(
            "This account's statements carry no balance, so the statement balance must be "
            "keyed",
            code="statement_balance_required",
            field_errors={"statement_balance": ["required"]},
        )

    number = claim_number(db, company_id, DocType.BANK_RECONCILIATION)
    reconciliation = BankReconciliation(
        company_id=company_id,
        bank_account_id=row.id,
        number=number.number,
        reconciliation_date=reconciliation_date,
        statement_balance=balance,
        status=ReconciliationStatus.OPEN,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    db.add(reconciliation)
    db.flush()
    record_audit(
        db,
        company_id=company_id,
        action="bank_reconciliation.opened",
        entity="bank_reconciliations",
        entity_id=reconciliation.id,
        after={
            "number": reconciliation.number,
            "bank_account": row.code,
            "date": reconciliation_date.isoformat(),
            "statement_balance": str(balance),
        },
        actor_user_id=actor.id,
        actor_email=actor.email,
        request=request,
    )
    return reconciliation


def live_figures(db: Session, company_id: int, reconciliation: BankReconciliation) -> Figures:
    """The strip, for an open reconciliation — and for a locked one, the *live recomputation*
    the report prints beside the stored figures (decision 5's last paragraph)."""
    row = accounts_service.get(db, company_id, reconciliation.bank_account_id)
    return figures(
        db,
        company_id,
        row,
        reconciliation_date=reconciliation.reconciliation_date,
        statement_balance=reconciliation.statement_balance,
    )


def stored_figures(db: Session, company_id: int, reconciliation: BankReconciliation) -> Figures:
    """A locked reconciliation reproduced from the lines that existed when it locked. What
    clause 4 asserts equals the stored columns, and what the report prints as "what this
    reconciliation said"."""
    row = accounts_service.get(db, company_id, reconciliation.bank_account_id)
    return figures(
        db,
        company_id,
        row,
        reconciliation_date=reconciliation.reconciliation_date,
        statement_balance=reconciliation.statement_balance,
        high_water_line_id=reconciliation.high_water_line_id,
        reconciliation_id=reconciliation.id,
    )


def lock(
    db: Session,
    company_id: int,
    reconciliation_id: int,
    *,
    statement_balance: Decimal | None = None,
    actor: User,
    idempotency_key: str | None = None,
    request: Request | None = None,
) -> BankReconciliation:
    """Sign it off — at a zero difference with every statement line explained, and at nothing
    else.

    The two refusals are not one test in two parts. A **difference of zero with an unexplained
    statement line** is two errors cancelling: the ledger happens to foot to the bank's figure
    while the bank is showing a movement nobody has accounted for. Refusing the unmatched lines
    first is what stops that closing.
    """
    reconciliation = get(db, company_id, reconciliation_id)
    if reconciliation.status == ReconciliationStatus.LOCKED:
        if idempotency_key and reconciliation.idempotency_key == idempotency_key:
            return reconciliation
        raise LedgerStateError(
            f"{reconciliation.number} is already locked",
            code="reconciliation_already_locked",
        )
    row = accounts_service.get(db, company_id, reconciliation.bank_account_id)
    if statement_balance is not None:
        reconciliation.statement_balance = statement_balance
        db.flush()

    current = live_figures(db, company_id, reconciliation)
    if current.unmatched_statement:
        count = current.unmatched_statement_count
        raise LedgerStateError(
            f"{count} statement line(s) on or before "
            f"{reconciliation.reconciliation_date.isoformat()} are in no match",
            code="statement_lines_unmatched",
            field_errors={"statement_lines": [f"{count} unmatched"]},
        )
    if current.difference != ZERO:
        raise LedgerStateError(
            f"The reconciliation is out by {current.difference:+f}",
            code="reconciliation_difference",
            field_errors={"statement_balance": [f"difference {current.difference:+f}"]},
        )

    # **Zero, not NULL, on an account with no lines.** `max()` over nothing is NULL, and NULL
    # in this column means "unknown" — which would be a lie: what is known about an empty
    # account is that *no* line existed, and the mark for that is 0. The difference is not
    # cosmetic. `late_lines` and the workspace's "dated inside BRC-n" both skip a NULL mark, so
    # a reconciliation locked before the account's first posting would never flag a late line
    # again, however many were posted into its period afterwards — and invariant clause 4 could
    # not reproduce it either, because nothing said which lines it was struck over.
    #
    # Found by the property machine rather than by review: a bank account opened and reconciled
    # before its first transaction is an ordinary thing to do, and no hand-written test did it.
    high_water = db.scalar(
        select(func.coalesce(func.max(JournalLine.id), 0)).where(
            JournalLine.company_id == company_id,
            JournalLine.gl_account_id == row.gl_account_id,
        )
    )
    assigned = _assign_effective_matches(db, company_id, row, reconciliation)

    reconciliation.ledger_balance = current.ledger_balance
    reconciliation.outstanding_total = current.outstanding_total
    reconciliation.difference = current.difference
    reconciliation.high_water_line_id = high_water
    reconciliation.outstanding_snapshot = [item.as_snapshot() for item in current.outstanding]
    reconciliation.status = ReconciliationStatus.LOCKED
    reconciliation.locked_by = actor.id
    reconciliation.locked_at = datetime.now(UTC)
    if idempotency_key:
        reconciliation.idempotency_key = idempotency_key
    row.last_reconciled_at = reconciliation.reconciliation_date
    row.last_reconciled_balance = reconciliation.statement_balance
    db.flush()
    record_audit(
        db,
        company_id=company_id,
        action="bank_reconciliation.locked",
        entity="bank_reconciliations",
        entity_id=reconciliation.id,
        after={
            "number": reconciliation.number,
            "statement_balance": str(reconciliation.statement_balance),
            "ledger_balance": str(current.ledger_balance),
            "outstanding_total": str(current.outstanding_total),
            "matches_assigned": assigned,
            "high_water_line_id": high_water,
        },
        actor_user_id=actor.id,
        actor_email=actor.email,
        request=request,
    )
    return reconciliation


def _assign_effective_matches(
    db: Session, company_id: int, row: BankAccount, reconciliation: BankReconciliation
) -> int:
    """Every match effective at the date that has no reconciliation yet becomes this one's.

    "That has none" is the whole clause: a match already assigned belongs to an earlier
    reconciliation and stays there, which is what keeps that one's stored figures reproducible
    from its own membership.
    """
    effective = matching.effective_match_ids(
        db, company_id, row, reconciliation.reconciliation_date
    )
    if not effective:
        return 0
    rows = db.scalars(
        select(BankMatch).where(
            BankMatch.company_id == company_id,
            BankMatch.id.in_(effective),
            BankMatch.reconciliation_id.is_(None),
        )
    ).all()
    for match in rows:
        match.reconciliation_id = reconciliation.id
    db.flush()
    return len(rows)


def reopen(
    db: Session,
    company_id: int,
    reconciliation_id: int,
    *,
    reason: str,
    actor: User,
    request: Request | None = None,
) -> BankReconciliation:
    """Withdraw the signature on the account's **latest** locked reconciliation.

    Only the latest, because the figures are a chain: every reconciliation's outstanding items
    are what the one before it left behind, and reopening one in the middle would leave the
    ones after it proving something against a state that no longer stands.

    The matches stand and only lose their `reconciliation_id`. Withdrawing the proof is not
    withdrawing the assertions that went into it — somebody ticked those lines against a
    statement and that is still true; what is withdrawn is the sign-off.
    """
    reconciliation = get(db, company_id, reconciliation_id)
    if reconciliation.status != ReconciliationStatus.LOCKED:
        raise LedgerStateError(
            f"{reconciliation.number} is not locked", code="reconciliation_not_locked"
        )
    latest = latest_locked(db, company_id, reconciliation.bank_account_id)
    if latest is None or latest.id != reconciliation.id:
        raise LedgerStateError(
            f"{reconciliation.number} is not the latest locked reconciliation on this account"
            + (f"; {latest.number} is" if latest is not None else ""),
            code="reconciliation_not_latest",
            field_errors={"reconciliation_id": ["only the latest may be reopened"]},
        )
    if not reason or not reason.strip():
        raise LedgerStateError(
            "Reopening a reconciliation needs a reason",
            code="reason_required",
            field_errors={"reason": ["required"]},
        )

    before = {
        "status": ReconciliationStatus.LOCKED.value,
        "ledger_balance": str(reconciliation.ledger_balance),
        "outstanding_total": str(reconciliation.outstanding_total),
    }
    released = db.scalars(
        select(BankMatch).where(
            BankMatch.company_id == company_id,
            BankMatch.reconciliation_id == reconciliation.id,
        )
    ).all()
    for match in released:
        match.reconciliation_id = None

    reconciliation.status = ReconciliationStatus.OPEN
    reconciliation.ledger_balance = None
    reconciliation.outstanding_total = None
    reconciliation.difference = None
    reconciliation.high_water_line_id = None
    reconciliation.outstanding_snapshot = None
    reconciliation.locked_by = None
    reconciliation.locked_at = None
    reconciliation.reopened_by = actor.id
    reconciliation.reopened_at = datetime.now(UTC)
    reconciliation.reopened_reason = reason

    # **Flush before asking what is now the latest locked one.** The session is
    # `autoflush=False` (it has to be — the posting engine depends on it), so without this the
    # `latest_locked` query below still sees *this* reconciliation as locked and hands it back
    # as its own predecessor: the cache would then keep pointing at the reconciliation that was
    # just reopened. Clause 7 caught it, which is the whole reason the clause recomputes the
    # cache rather than trusting it.
    db.flush()

    # The cache follows the row it caches: back to whatever is now the latest locked one on
    # this account, or to nothing.
    row = accounts_service.get(db, company_id, reconciliation.bank_account_id)
    previous = latest_locked(db, company_id, reconciliation.bank_account_id)
    row.last_reconciled_at = previous.reconciliation_date if previous else None
    row.last_reconciled_balance = previous.statement_balance if previous else None
    db.flush()
    record_audit(
        db,
        company_id=company_id,
        action="bank_reconciliation.reopened",
        entity="bank_reconciliations",
        entity_id=reconciliation.id,
        before=before,
        after={
            "status": ReconciliationStatus.OPEN.value,
            "reason": reason,
            "matches_released": len(released),
        },
        actor_user_id=actor.id,
        actor_email=actor.email,
        request=request,
    )
    return reconciliation


def _replay(
    db: Session, company_id: int, key: str | None, request_hash: str | None
) -> BankReconciliation | None:
    if not key:
        return None
    reconciliation = db.scalar(
        select(BankReconciliation).where(
            BankReconciliation.company_id == company_id,
            BankReconciliation.idempotency_key == key,
        )
    )
    if reconciliation is None:
        return None
    if request_hash is not None and reconciliation.idempotency_hash != request_hash:
        raise LedgerStateError(
            f"Idempotency-Key {key} was already used for a different request "
            f"({reconciliation.number}); use a new key",
            code="idempotency_key_reused",
        )
    return reconciliation
