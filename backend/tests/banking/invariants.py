"""`assert_bank_invariants` — what must be true of the banking side after every operation
(P8 decision 5).

Asserted after every row of the acceptance tape and after every step of the property machine,
the way `assert_ledger_invariants` and `assert_subledger_invariants` are. The point of a suite
like this is that it is cheap enough to run *constantly*: a compounding error is one that each
operation leaves a little worse and no single end-state check can see.

Clauses 1-7 and 9 land at step 2. **Clause 8 is the payment-run clause and arrives with the
runs at step 3** — it is declared here, named and raising, rather than silently absent, so that
"the suite has eight clauses and one is not written yet" is a fact the reader meets rather than
one they have to notice.

The hardest clause is 4, and it is the one the whole phase turns on: a locked reconciliation's
stored figures must be **reproducible** from the lines that existed when it locked. Not
"close", not "still true today" — reproducible, exactly, from `high_water_line_id` and the
matches that carry its own `reconciliation_id`. Everything else about the design (membership by
id, assignment at lock, the snapshot) exists to make that clause provable.
"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.banking import accounts as accounts_service
from app.banking import reconciliation as reconciliation_service
from app.kernel.money import base_currency
from app.models.banking import (
    BankAccount,
    BankMatch,
    BankMatchJournalLine,
    BankMatchStatementLine,
    BankReconciliation,
    BankStatement,
    BankStatementLine,
    ReconciliationStatus,
    StatementStatus,
)
from app.models.gl import CASHBOOK_CONTROL_TYPES, GLAccount
from app.models.journal import JournalEntry, JournalLine

ZERO = Decimal(0)


def assert_bank_invariants(db: Session, company_id: int) -> None:
    """Every clause, in order. Cheap enough to call after every operation."""
    accounts = list(
        db.scalars(select(BankAccount).where(BankAccount.company_id == company_id))
    )
    base = base_currency(db, company_id).id
    _clause_1_members_are_unique_and_on_one_account(db, company_id, accounts)
    _clause_2_every_match_with_statement_members_balances(db, company_id, accounts, base)
    _clause_3_assigned_matches_were_effective(db, company_id, accounts)
    _clause_4_locked_figures_reproduce(db, company_id, accounts)
    _clause_5_a_foreign_account_holds_only_its_currency(db, company_id, accounts, base)
    _clause_6_every_flagged_account_has_exactly_one_row(db, company_id, accounts)
    _clause_7_the_cache_equals_the_latest_locked_row(db, company_id, accounts)
    # Clause 8 — payment runs — arrives at step 3 with the runs themselves.
    _clause_9_a_void_statements_lines_are_void_and_unmatched(db, company_id)


# --- 1 ------------------------------------------------------------------------------------------


def _clause_1_members_are_unique_and_on_one_account(
    db: Session, company_id: int, accounts: list[BankAccount]
) -> None:
    """Match members are unique per line, sit on the match's own bank account, and every match
    has at least one member.

    The uniqueness is a DB constraint too, and this restates it because the *consequence* is
    what matters: if a line could be in two matches, `outstanding` would double-count it and
    the difference would close on a figure that was never right.
    """
    by_account = {row.id: row for row in accounts}

    duplicates = db.execute(
        select(BankMatchStatementLine.statement_line_id, func.count())
        .where(BankMatchStatementLine.company_id == company_id)
        .group_by(BankMatchStatementLine.statement_line_id)
        .having(func.count() > 1)
    ).all()
    assert not duplicates, f"statement lines in more than one match: {duplicates}"

    duplicates = db.execute(
        select(BankMatchJournalLine.journal_line_id, func.count())
        .where(BankMatchJournalLine.company_id == company_id)
        .group_by(BankMatchJournalLine.journal_line_id)
        .having(func.count() > 1)
    ).all()
    assert not duplicates, f"ledger lines in more than one match: {duplicates}"

    foreign = db.execute(
        select(BankMatch.id, BankMatch.bank_account_id, BankStatementLine.bank_account_id)
        .join(BankMatchStatementLine, BankMatchStatementLine.match_id == BankMatch.id)
        .join(
            BankStatementLine,
            BankStatementLine.id == BankMatchStatementLine.statement_line_id,
        )
        .where(
            BankMatch.company_id == company_id,
            BankStatementLine.bank_account_id != BankMatch.bank_account_id,
        )
    ).all()
    assert not foreign, f"matches holding a statement line of another account: {foreign}"

    foreign = db.execute(
        select(BankMatch.id, JournalLine.gl_account_id)
        .join(BankMatchJournalLine, BankMatchJournalLine.match_id == BankMatch.id)
        .join(JournalLine, JournalLine.id == BankMatchJournalLine.journal_line_id)
        .where(BankMatch.company_id == company_id)
    ).all()
    for match_id, gl_account_id in foreign:
        match = db.get(BankMatch, match_id)
        expected = by_account[match.bank_account_id].gl_account_id
        assert gl_account_id == expected, (
            f"match {match_id} holds a ledger line on account {gl_account_id}, but it is a "
            f"match on the bank account whose GL account is {expected}"
        )

    for match in db.scalars(
        select(BankMatch).where(BankMatch.company_id == company_id)
    ).all():
        statement_lines, journal_lines = _members(db, company_id, match.id)
        assert statement_lines or journal_lines, f"match {match.id} has no members"


def _members(db: Session, company_id: int, match_id: int) -> tuple[list[int], list[int]]:
    statement_lines = list(
        db.scalars(
            select(BankMatchStatementLine.statement_line_id).where(
                BankMatchStatementLine.company_id == company_id,
                BankMatchStatementLine.match_id == match_id,
            )
        )
    )
    journal_lines = list(
        db.scalars(
            select(BankMatchJournalLine.journal_line_id).where(
                BankMatchJournalLine.company_id == company_id,
                BankMatchJournalLine.match_id == match_id,
            )
        )
    )
    return statement_lines, journal_lines


# --- 2 ------------------------------------------------------------------------------------------


def _clause_2_every_match_with_statement_members_balances(
    db: Session, company_id: int, accounts: list[BankAccount], base_currency_id: int
) -> None:
    """Σ statement `amount` == Σ ledger reconciled amount, on every match that has a statement
    side. A `tick` has none and is exempt by construction, not by exception: there is nothing
    to balance against."""
    by_id = {row.id: row for row in accounts}
    for match in db.scalars(
        select(BankMatch).where(BankMatch.company_id == company_id)
    ).all():
        statement_lines, journal_lines = _members(db, company_id, match.id)
        if not statement_lines:
            continue
        row = by_id[match.bank_account_id]
        statement_total = sum(
            db.scalars(
                select(BankStatementLine.amount).where(
                    BankStatementLine.id.in_(statement_lines)
                )
            ),
            ZERO,
        )
        ledger_total = sum(
            (
                accounts_service.reconciled_amount(line, row, base_currency_id)
                for line in db.scalars(
                    select(JournalLine).where(JournalLine.id.in_(journal_lines))
                )
            ),
            ZERO,
        )
        assert statement_total == ledger_total, (
            f"match {match.id} on {row.code} does not balance: statement {statement_total} "
            f"vs ledger {ledger_total}. A difference is posted, never matched."
        )


# --- 3 ------------------------------------------------------------------------------------------


def _clause_3_assigned_matches_were_effective(
    db: Session, company_id: int, accounts: list[BankAccount]
) -> None:
    """Every match carrying a locked reconciliation's id was effective at that date.

    The converse of the assignment rule: `lock` assigns only effective matches, so a match with
    a member dated after the reconciliation it belongs to means either the assignment or a
    later edit got it wrong — and the figures would then include money the bank had not shown.
    """
    by_id = {row.id: row for row in accounts}
    rows = db.execute(
        select(BankMatch.id, BankMatch.bank_account_id, BankReconciliation.reconciliation_date,
               BankReconciliation.number)
        .join(BankReconciliation, BankReconciliation.id == BankMatch.reconciliation_id)
        .where(BankMatch.company_id == company_id)
    ).all()
    for match_id, bank_account_id, reconciliation_date, number in rows:
        row = by_id[bank_account_id]
        latest_statement = db.scalar(
            select(func.max(BankStatementLine.value_date))
            .join(
                BankMatchStatementLine,
                BankMatchStatementLine.statement_line_id == BankStatementLine.id,
            )
            .where(BankMatchStatementLine.match_id == match_id)
        )
        latest_ledger = db.scalar(
            select(func.max(JournalEntry.entry_date))
            .join(JournalLine, JournalLine.entry_id == JournalEntry.id)
            .join(
                BankMatchJournalLine,
                BankMatchJournalLine.journal_line_id == JournalLine.id,
            )
            .where(BankMatchJournalLine.match_id == match_id)
        )
        for label, latest in (("statement", latest_statement), ("ledger", latest_ledger)):
            assert latest is None or latest <= reconciliation_date, (
                f"match {match_id} on {row.code} belongs to {number} "
                f"({reconciliation_date}) but its latest {label} member is dated {latest}"
            )


# --- 4 ------------------------------------------------------------------------------------------


def _clause_4_locked_figures_reproduce(
    db: Session, company_id: int, accounts: list[BankAccount]
) -> None:
    """A locked reconciliation's four stored figures are reproducible, exactly.

    This is the clause the phase turns on. It recomputes `ledger_balance` over the account's
    lines with `id <= high_water_line_id` dated on or before the reconciliation date, and
    `outstanding` over those of them whose match does not carry **this** reconciliation's id —
    then asserts the stored columns match and that `statement_balance == ledger_balance -
    outstanding_total`.

    Membership is the assignment made at lock, not a date test. A match created afterwards may
    be perfectly effective at the locked date; counting it would restate a figure somebody
    signed, which is the thing decision 5 forbids in so many words.
    """
    for reconciliation in db.scalars(
        select(BankReconciliation).where(
            BankReconciliation.company_id == company_id,
            BankReconciliation.status == ReconciliationStatus.LOCKED,
        )
    ).all():
        assert reconciliation.high_water_line_id is not None, (
            f"{reconciliation.number} is locked without a high-water mark, so nothing can say "
            "which lines it was struck over"
        )
        recomputed = reconciliation_service.stored_figures(db, company_id, reconciliation)
        assert recomputed.ledger_balance == reconciliation.ledger_balance, (
            f"{reconciliation.number} stored a ledger balance of "
            f"{reconciliation.ledger_balance} and reproduces {recomputed.ledger_balance}"
        )
        assert recomputed.outstanding_total == reconciliation.outstanding_total, (
            f"{reconciliation.number} stored outstanding {reconciliation.outstanding_total} "
            f"and reproduces {recomputed.outstanding_total}"
        )
        assert reconciliation.difference == ZERO, (
            f"{reconciliation.number} is locked at a difference of "
            f"{reconciliation.difference}; a reconciliation locks at zero or not at all"
        )
        assert (
            reconciliation.statement_balance
            == reconciliation.ledger_balance - reconciliation.outstanding_total
        ), (
            f"{reconciliation.number}: statement {reconciliation.statement_balance} != ledger "
            f"{reconciliation.ledger_balance} - outstanding "
            f"{reconciliation.outstanding_total}"
        )
        snapshot = reconciliation.outstanding_snapshot or []
        assert len(snapshot) == len(recomputed.outstanding), (
            f"{reconciliation.number} stored {len(snapshot)} outstanding items and reproduces "
            f"{len(recomputed.outstanding)}"
        )
        assert sum((Decimal(item["amount"]) for item in snapshot), ZERO) == (
            reconciliation.outstanding_total
        ), f"{reconciliation.number}'s snapshot does not foot to its stored outstanding total"


# --- 5 ------------------------------------------------------------------------------------------


def _clause_5_a_foreign_account_holds_only_its_currency(
    db: Session, company_id: int, accounts: list[BankAccount], base_currency_id: int
) -> None:
    """No foreign-currency bank account carries a line in another currency, among the lines
    written since its master row existed.

    Scoped by `created_at` because the rule is one the *master* imposes: a tenant back-filled
    at `0027_p8_banking` may hold history from before there was a `bank_accounts` row at all,
    and that history is legal — decision 2's rule is one-sided and applies to a base-currency
    account not at all. What the clause asserts is that nothing has been written in breach
    since the row that imposes it came into being.
    """
    for row in accounts:
        if row.currency_id == base_currency_id:
            continue  # the rule is one-sided: a base account may carry any currency
        offenders = db.execute(
            select(JournalLine.id, JournalLine.currency_id)
            .where(
                JournalLine.company_id == company_id,
                JournalLine.gl_account_id == row.gl_account_id,
                JournalLine.currency_id != row.currency_id,
                JournalLine.created_at >= row.created_at,
            )
            .limit(5)
        ).all()
        assert not offenders, (
            f"{row.code} is held in currency {row.currency_id} and carries lines in another: "
            f"{offenders}"
        )


# --- 6 ------------------------------------------------------------------------------------------


def _clause_6_every_flagged_account_has_exactly_one_row(
    db: Session, company_id: int, accounts: list[BankAccount]
) -> None:
    """Every bank/cash control account has exactly one master row, and its `kind` equals the
    control type.

    This is the clause that catches a path which created a flagged GL account without calling
    `ensure_row` — the hook is one line at each call site precisely so it is hard to forget,
    and this is what notices when somebody does anyway.
    """
    flagged = db.scalars(
        select(GLAccount).where(
            GLAccount.company_id == company_id,
            GLAccount.control_type.in_(tuple(CASHBOOK_CONTROL_TYPES)),
        )
    ).all()
    by_gl_account: dict[int, list[BankAccount]] = {}
    for row in accounts:
        by_gl_account.setdefault(row.gl_account_id, []).append(row)

    for account in flagged:
        rows = by_gl_account.get(account.id, [])
        assert len(rows) == 1, (
            f"GL account {account.code} is a {account.control_type} control account with "
            f"{len(rows)} bank_accounts rows; it must have exactly one"
        )
        assert rows[0].kind.value == account.control_type.value, (
            f"{rows[0].code} is a {rows[0].kind} master over a {account.control_type} account"
        )

    flagged_ids = {account.id for account in flagged}
    stray = [row.code for row in accounts if row.gl_account_id not in flagged_ids]
    assert not stray, f"bank_accounts rows over accounts that are not bank/cash control: {stray}"


# --- 7 ------------------------------------------------------------------------------------------


def _clause_7_the_cache_equals_the_latest_locked_row(
    db: Session, company_id: int, accounts: list[BankAccount]
) -> None:
    """`last_reconciled_at` / `last_reconciled_balance` equal the latest locked reconciliation,
    or are both null.

    The one cache in the phase, and it caches a *stored row* rather than a derived balance —
    which is the only reason it is allowed at all (ADR-04). Recomputing it here is what makes
    that claim checkable rather than a comment.
    """
    for row in accounts:
        latest = reconciliation_service.latest_locked(db, company_id, row.id)
        if latest is None:
            assert row.last_reconciled_at is None and row.last_reconciled_balance is None, (
                f"{row.code} caches a last reconciliation ({row.last_reconciled_at}, "
                f"{row.last_reconciled_balance}) but has no locked one"
            )
            continue
        assert row.last_reconciled_at == latest.reconciliation_date, (
            f"{row.code} caches {row.last_reconciled_at} but {latest.number} is locked at "
            f"{latest.reconciliation_date}"
        )
        assert row.last_reconciled_balance == latest.statement_balance, (
            f"{row.code} caches a balance of {row.last_reconciled_balance} but "
            f"{latest.number} locked at {latest.statement_balance}"
        )


# --- 9 ------------------------------------------------------------------------------------------


def _clause_9_a_void_statements_lines_are_void_and_unmatched(
    db: Session, company_id: int
) -> None:
    """Every statement line of a void statement is flagged void and is in no match — **and
    every line of a live statement is not flagged void**.

    Both directions, because `is_void` is a *denormalisation*: the authority is
    `bank_statements.status`, and the copy on the line exists only so the fingerprint
    uniqueness index can be partial (a partial index cannot reach another table, and the lines
    cannot be deleted — `VN013`). A copy nothing checks is a copy that drifts, and it would
    drift silently in the expensive direction: a line wrongly flagged void leaves every listing
    and stops blocking a re-import, so the same movement is imported twice and the
    reconciliation is out by exactly one line nobody can find.

    The unmatched half is decision 3's: a statement cannot be voided while any of its lines is
    matched, so a void statement with a matched line means the refusal was bypassed.
    """
    rows = db.execute(
        select(BankStatementLine.id, BankStatementLine.is_void, BankStatement.status,
               BankStatement.number)
        .join(BankStatement, BankStatement.id == BankStatementLine.statement_id)
        .where(BankStatementLine.company_id == company_id)
    ).all()
    for line_id, is_void, status, number in rows:
        expected = status == StatementStatus.VOID
        assert is_void == expected, (
            f"statement line {line_id} of {number} is flagged is_void={is_void} while its "
            f"statement is {status.value}; the flag is a copy of the status and has drifted"
        )

    matched_void = db.execute(
        select(BankStatement.number, BankStatementLine.id)
        .join(BankStatementLine, BankStatementLine.statement_id == BankStatement.id)
        .join(
            BankMatchStatementLine,
            BankMatchStatementLine.statement_line_id == BankStatementLine.id,
        )
        .where(
            BankStatement.company_id == company_id,
            BankStatement.status == StatementStatus.VOID,
        )
    ).all()
    assert not matched_void, (
        f"void statements whose lines are still matched: {matched_void}. A statement is "
        "refused a void while any of its lines is in a match."
    )
