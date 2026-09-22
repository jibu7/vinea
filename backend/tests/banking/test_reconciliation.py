"""The reconciliation: the figures identity, the two lock refusals, the snapshot, reopen, and
late lines by high-water mark (P8 decision 5).

The identity is worked by hand in each test that asserts it, and three of the figure sets below
are the acceptance tape's own rows 2, 8 and 9 reproduced at this level — so step 5's literals
are already known to come out of this code rather than hoped for.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.banking import matching
from app.banking import reconciliation as reconciliation_service
from app.kernel.errors import LedgerStateError
from app.models.banking import (
    BankMatch,
    BankMatchKind,
    BankMatchRule,
    BankStatementLine,
    ReconciliationStatus,
)
from tests.banking.conftest import Banking, bank_line_of, cashbook, key_statement
from tests.banking.invariants import assert_bank_invariants
from tests.kernel.conftest import YEAR

AUG_31 = date(YEAR, 8, 31)
SEP_3 = date(YEAR, 9, 3)
SEP_12 = date(YEAR, 9, 12)
SEP_28 = date(YEAR, 9, 28)
SEP_30 = date(YEAR, 9, 30)
OCT_3 = date(YEAR, 10, 3)
OCT_31 = date(YEAR, 10, 31)


def _lines(db: Session, banking: Banking, account: str = "BK-RWF"):  # noqa: ANN202
    return list(
        db.scalars(
            select(BankStatementLine)
            .where(BankStatementLine.bank_account_id == banking.bank(account).id)
            .order_by(BankStatementLine.id)
        )
    )


def _open(  # noqa: ANN202
    db: Session, banking: Banking, *, on: date, balance: Decimal, account: str = "BK-RWF"
):
    return reconciliation_service.open_reconciliation(
        db,
        banking.company_id,
        bank_account_id=banking.bank(account).id,
        reconciliation_date=on,
        statement_balance=balance,
        actor=banking.owner,
    )


# --- The figures ---------------------------------------------------------------------------------


def test_the_identity_holds_on_an_empty_account(db: Session, banking: Banking) -> None:
    """Nothing posted, nothing imported: everything is zero and the difference is the keyed
    balance. A reconciliation opened against a bank that says 1 000 is out by 1 000, which is
    the right answer and not an error."""
    reconciliation = _open(db, banking, on=AUG_31, balance=Decimal(1000))
    db.commit()

    figures = reconciliation_service.live_figures(db, banking.company_id, reconciliation)

    assert figures.ledger_balance == Decimal(0)
    assert figures.outstanding_total == Decimal(0)
    assert figures.difference == Decimal(1000)


def test_an_unticked_opening_entry_is_wholly_outstanding(
    db: Session, banking: Banking
) -> None:
    """The tape's row 2 before the tick: ledger 1 000 000, outstanding 1 000 000 (nothing has
    been ticked), so the difference is 1 000 000 − (1 000 000 − 1 000 000) = 1 000 000 and the
    lock is refused at exactly that."""
    cashbook(db, banking, account_code="1120", amount=Decimal(1000000), on=AUG_31)
    db.commit()
    reconciliation = _open(db, banking, on=AUG_31, balance=Decimal(1000000))
    db.commit()

    figures = reconciliation_service.live_figures(db, banking.company_id, reconciliation)

    assert figures.ledger_balance == Decimal(1000000)
    assert figures.outstanding_total == Decimal(1000000)
    assert figures.difference == Decimal(1000000)


def test_ticking_the_opening_entry_closes_the_difference(
    db: Session, banking: Banking
) -> None:
    """The tape's row 2 after the tick — paper mode reaching zero with no statement at all."""
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(1000000), on=AUG_31)
    line = bank_line_of(db, banking, entry, "1120")
    db.commit()
    reconciliation = _open(db, banking, on=AUG_31, balance=Decimal(1000000))
    matching.tick(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        journal_line_ids=[line.id],
        actor=banking.owner,
    )
    db.commit()

    figures = reconciliation_service.live_figures(db, banking.company_id, reconciliation)

    assert (figures.ledger_balance, figures.outstanding_total, figures.difference) == (
        Decimal(1000000),
        Decimal(0),
        Decimal(0),
    )


def test_an_unpresented_payment_is_outstanding_and_the_difference_shows_it(
    db: Session, banking: Banking
) -> None:
    """The tape's row 8, arithmetic and all. A cheque the bank has not paid yet is outstanding
    at −70 000, so the bank should be showing 70 000 *more* than the ledger does."""
    opening = cashbook(db, banking, account_code="1120", amount=Decimal(1053000), on=SEP_3)
    cashbook(db, banking, account_code="1120", amount=Decimal(-70000), on=SEP_28)
    db.commit()
    opening_line = bank_line_of(db, banking, opening, "1120")
    matching.tick(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        journal_line_ids=[opening_line.id],
        actor=banking.owner,
    )
    reconciliation = _open(db, banking, on=SEP_30, balance=Decimal(1053000))
    db.commit()

    figures = reconciliation_service.live_figures(db, banking.company_id, reconciliation)

    assert figures.ledger_balance == Decimal(983000)
    assert figures.outstanding_total == Decimal(-70000)
    # 1 053 000 − (983 000 − (−70 000)) = 1 053 000 − 1 053 000 = 0
    assert figures.difference == Decimal(0)
    assert [item.amount for item in figures.outstanding] == [Decimal(-70000)]


def test_a_deposit_in_transit_is_outstanding_at_the_month_end(
    db: Session, banking: Banking
) -> None:
    """The other direction, and the one `effective_at` reads both sides for: the receipt is
    dated 30 September, the bank booked it on 3 October, and at 30 September it is ours and not
    theirs."""
    opening = cashbook(db, banking, account_code="1120", amount=Decimal(100000), on=SEP_3)
    deposit = cashbook(db, banking, account_code="1120", amount=Decimal(40000), on=SEP_30)
    db.commit()
    matching.tick(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        journal_line_ids=[bank_line_of(db, banking, opening, "1120").id],
        actor=banking.owner,
    )
    key_statement(db, banking, lines=[(OCT_3, "CASH DEPOSIT", Decimal(40000))])
    db.commit()
    matching.create_match(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        statement_line_ids=[_lines(db, banking)[0].id],
        journal_line_ids=[bank_line_of(db, banking, deposit, "1120").id],
        kind=BankMatchKind.MANUAL,
        rule=BankMatchRule.MANUAL,
        actor=banking.owner,
    )
    reconciliation = _open(db, banking, on=SEP_30, balance=Decimal(100000))
    db.commit()

    figures = reconciliation_service.live_figures(db, banking.company_id, reconciliation)

    assert figures.ledger_balance == Decimal(140000)
    assert figures.outstanding_total == Decimal(40000)
    assert figures.difference == Decimal(0)


# --- Opening --------------------------------------------------------------------------------------


def test_only_one_reconciliation_may_be_open_per_account(
    db: Session, banking: Banking
) -> None:
    _open(db, banking, on=AUG_31, balance=Decimal(0))
    db.commit()

    with pytest.raises(LedgerStateError) as excinfo:
        _open(db, banking, on=SEP_30, balance=Decimal(0))

    assert excinfo.value.code == "reconciliation_open_exists"
    db.rollback()


def test_a_cash_account_has_no_reconciliation(db: Session, banking: Banking) -> None:
    """A till is counted, not reconciled — P11's work, and this phase's refusal."""
    with pytest.raises(LedgerStateError) as excinfo:
        _open(db, banking, on=AUG_31, balance=Decimal(0), account="CASH")

    assert excinfo.value.code == "reconciliation_needs_bank"
    db.rollback()


def test_the_next_reconciliation_must_be_dated_after_the_last_locked_one(
    db: Session, banking: Banking
) -> None:
    """The tape's row 18. The figures are a chain — each one's outstanding items are what the
    one before it left — and a reconciliation dated back inside a locked period would be
    proving something against a state already signed off."""
    reconciliation = _open(db, banking, on=SEP_30, balance=Decimal(0))
    db.commit()
    reconciliation_service.lock(
        db, banking.company_id, reconciliation.id, actor=banking.owner
    )
    db.commit()

    with pytest.raises(LedgerStateError) as excinfo:
        _open(db, banking, on=date(YEAR, 9, 15), balance=Decimal(0))

    assert excinfo.value.code == "reconciliation_date_order"
    assert reconciliation.reconciliation_date.isoformat() in excinfo.value.message
    db.rollback()

    # And the day after the locked one is fine: the rule is an ordering, not a gap.
    following = _open(db, banking, on=date(YEAR, 10, 1), balance=Decimal(0))
    db.commit()
    assert following.status == ReconciliationStatus.OPEN


def test_the_statement_balance_defaults_from_the_latest_balance_column(
    db: Session, banking: Banking
) -> None:
    """The tape's row 8 opens `BRC-2` without keying a figure, because the imported statement
    carries one."""
    from app.banking import statements as statements_service
    from tests.banking.conftest import sample

    statements_service.import_statement(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        content=sample("generic-bk-rwf-sep.csv"),
        file_name="generic-bk-rwf-sep.csv",
        actor=banking.owner,
    )
    db.commit()

    reconciliation = reconciliation_service.open_reconciliation(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        reconciliation_date=date(2026, 9, 30),
        actor=banking.owner,
    )
    db.commit()

    assert reconciliation.statement_balance == Decimal("1090500")


# --- The two lock refusals ------------------------------------------------------------------------


def test_a_lock_is_refused_while_a_statement_line_is_unmatched(
    db: Session, banking: Banking
) -> None:
    """The first refusal, and the one that has to run first. A difference of zero with an
    unexplained statement line is two errors cancelling."""
    cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=SEP_3)
    key_statement(db, banking, lines=[(SEP_3, "A DEPOSIT", Decimal(1000))])
    db.commit()
    reconciliation = _open(db, banking, on=SEP_30, balance=Decimal(1000))
    db.commit()

    with pytest.raises(LedgerStateError) as excinfo:
        reconciliation_service.lock(
            db, banking.company_id, reconciliation.id, actor=banking.owner
        )

    assert excinfo.value.code == "statement_lines_unmatched"
    assert "1 statement line" in excinfo.value.message
    db.rollback()


def test_a_lock_is_refused_at_a_non_zero_difference(db: Session, banking: Banking) -> None:
    """The tape's row 9: the balance keyed as 1 090 000 instead of 1 090 500 is refused at
    −500, with the figure in the refusal."""
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(1090500), on=SEP_3)
    db.commit()
    matching.tick(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        journal_line_ids=[bank_line_of(db, banking, entry, "1120").id],
        actor=banking.owner,
    )
    reconciliation = _open(db, banking, on=SEP_30, balance=Decimal(1090000))
    db.commit()

    with pytest.raises(LedgerStateError) as excinfo:
        reconciliation_service.lock(
            db, banking.company_id, reconciliation.id, actor=banking.owner
        )

    assert excinfo.value.code == "reconciliation_difference"
    assert "-500" in excinfo.value.message
    db.rollback()

    # Restored to the right figure, it locks.
    reconciliation_service.lock(
        db,
        banking.company_id,
        reconciliation.id,
        statement_balance=Decimal(1090500),
        actor=banking.owner,
    )
    db.commit()
    assert reconciliation.status == ReconciliationStatus.LOCKED
    assert_bank_invariants(db, banking.company_id)


# --- Locking --------------------------------------------------------------------------------------


def test_locking_stores_the_figures_the_snapshot_and_the_high_water_mark(
    db: Session, banking: Banking
) -> None:
    opening = cashbook(db, banking, account_code="1120", amount=Decimal(1053000), on=SEP_3)
    cashbook(db, banking, account_code="1120", amount=Decimal(-70000), on=SEP_28)
    db.commit()
    matching.tick(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        journal_line_ids=[bank_line_of(db, banking, opening, "1120").id],
        actor=banking.owner,
    )
    reconciliation = _open(db, banking, on=SEP_30, balance=Decimal(1053000))
    db.commit()

    reconciliation_service.lock(
        db, banking.company_id, reconciliation.id, actor=banking.owner
    )
    db.commit()

    assert reconciliation.status == ReconciliationStatus.LOCKED
    assert reconciliation.ledger_balance == Decimal(983000)
    assert reconciliation.outstanding_total == Decimal(-70000)
    assert reconciliation.difference == Decimal(0)
    assert reconciliation.high_water_line_id is not None
    assert [Decimal(item["amount"]) for item in reconciliation.outstanding_snapshot] == [
        Decimal(-70000)
    ]
    # Clause 7's cache follows the row it caches.
    assert banking.bank("BK-RWF").last_reconciled_at == SEP_30
    assert banking.bank("BK-RWF").last_reconciled_balance == Decimal(1053000)
    assert_bank_invariants(db, banking.company_id)


def test_locking_assigns_every_effective_match_that_has_none(
    db: Session, banking: Banking
) -> None:
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=SEP_3)
    db.commit()
    match = matching.tick(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        journal_line_ids=[bank_line_of(db, banking, entry, "1120").id],
        actor=banking.owner,
    )
    reconciliation = _open(db, banking, on=SEP_30, balance=Decimal(1000))
    db.commit()

    reconciliation_service.lock(
        db, banking.company_id, reconciliation.id, actor=banking.owner
    )
    db.commit()

    assert match.reconciliation_id == reconciliation.id
    assert_bank_invariants(db, banking.company_id)


def test_a_match_made_after_a_lock_does_not_move_the_locked_figures(
    db: Session, banking: Banking
) -> None:
    """Clause 4's reason for reading membership rather than effectiveness. The later match is
    perfectly effective at the locked date; counting it would restate a signed figure."""
    opening = cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=SEP_3)
    later = cashbook(db, banking, account_code="1120", amount=Decimal(-200), on=SEP_12)
    db.commit()
    matching.tick(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        journal_line_ids=[bank_line_of(db, banking, opening, "1120").id],
        actor=banking.owner,
    )
    # Ledger 1 000 − 200 = 800, outstanding −200 (the payment is unticked), so the bank is
    # still showing 1 000: 1 000 − (800 − (−200)) = 0.
    reconciliation = _open(db, banking, on=SEP_30, balance=Decimal(1000))
    db.commit()
    reconciliation_service.lock(
        db, banking.company_id, reconciliation.id, actor=banking.owner
    )
    db.commit()
    stored = (reconciliation.ledger_balance, reconciliation.outstanding_total)
    assert stored == (Decimal(800), Decimal(-200))

    # Tick the 12 September payment *after* the lock. It is effective at 30 September.
    matching.tick(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        journal_line_ids=[bank_line_of(db, banking, later, "1120").id],
        actor=banking.owner,
    )
    db.commit()

    assert (reconciliation.ledger_balance, reconciliation.outstanding_total) == stored
    reproduced = reconciliation_service.stored_figures(
        db, banking.company_id, reconciliation
    )
    assert reproduced.ledger_balance == reconciliation.ledger_balance
    assert reproduced.outstanding_total == reconciliation.outstanding_total
    assert_bank_invariants(db, banking.company_id)


def test_unmatching_inside_a_locked_reconciliation_is_refused(
    db: Session, banking: Banking
) -> None:
    """The tape's row 10."""
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=SEP_3)
    db.commit()
    match = matching.tick(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        journal_line_ids=[bank_line_of(db, banking, entry, "1120").id],
        actor=banking.owner,
    )
    reconciliation = _open(db, banking, on=SEP_30, balance=Decimal(1000))
    db.commit()
    reconciliation_service.lock(
        db, banking.company_id, reconciliation.id, actor=banking.owner
    )
    db.commit()

    with pytest.raises(LedgerStateError) as excinfo:
        matching.unmatch(db, banking.company_id, match.id, actor=banking.owner)

    assert excinfo.value.code == "reconciliation_locked"
    assert reconciliation.number in excinfo.value.message
    db.rollback()


# --- Late lines -----------------------------------------------------------------------------------


def test_a_line_posted_after_the_lock_is_late_and_moves_nothing(
    db: Session, banking: Banking
) -> None:
    """The tape's row 13. A cheque dated 26 September but posted on 2 October falls inside a
    reconciliation that is already signed: its figures do not move, the line is outstanding in
    the *next* one, and the workspace flags which reconciliation it is dated inside."""
    opening = cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=SEP_3)
    db.commit()
    matching.tick(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        journal_line_ids=[bank_line_of(db, banking, opening, "1120").id],
        actor=banking.owner,
    )
    reconciliation = _open(db, banking, on=SEP_30, balance=Decimal(1000))
    db.commit()
    reconciliation_service.lock(
        db, banking.company_id, reconciliation.id, actor=banking.owner
    )
    db.commit()
    stored = (reconciliation.ledger_balance, reconciliation.outstanding_total)

    # Dated inside the locked period, posted after it.
    late = cashbook(db, banking, account_code="1120", amount=Decimal(-200), on=SEP_28)
    db.commit()
    late_line = bank_line_of(db, banking, late, "1120")

    assert (reconciliation.ledger_balance, reconciliation.outstanding_total) == stored
    assert reconciliation_service.late_lines(db, banking.company_id, reconciliation) == [
        late_line.id
    ]
    assert late_line.id > reconciliation.high_water_line_id

    # And the next reconciliation carries it, flagged with the one it is dated inside. The
    # cheque is still unpresented, so the bank is showing 1 000 while the ledger says 800:
    # 1 000 − (800 − (−200)) = 0.
    following = _open(db, banking, on=OCT_31, balance=Decimal(1000))
    db.commit()
    figures = reconciliation_service.live_figures(db, banking.company_id, following)
    outstanding = {item.journal_line_id: item for item in figures.outstanding}
    assert outstanding[late_line.id].dated_inside == reconciliation.number
    assert figures.outstanding_total == Decimal(-200)
    assert figures.difference == Decimal(0)
    assert_bank_invariants(db, banking.company_id)


# --- Reopen ---------------------------------------------------------------------------------------


def test_reopen_releases_the_matches_and_rewinds_the_cache(
    db: Session, banking: Banking
) -> None:
    """The tape's row 16. The matches stand — somebody ticked those lines and that is still
    true — and what is withdrawn is the sign-off."""
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=SEP_3)
    db.commit()
    match = matching.tick(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        journal_line_ids=[bank_line_of(db, banking, entry, "1120").id],
        actor=banking.owner,
    )
    reconciliation = _open(db, banking, on=SEP_30, balance=Decimal(1000))
    db.commit()
    reconciliation_service.lock(
        db, banking.company_id, reconciliation.id, actor=banking.owner
    )
    db.commit()

    reconciliation_service.reopen(
        db,
        banking.company_id,
        reconciliation.id,
        reason="the bank restated a fee",
        actor=banking.owner,
    )
    db.commit()

    assert reconciliation.status == ReconciliationStatus.OPEN
    assert reconciliation.reopened_reason == "the bank restated a fee"
    assert reconciliation.high_water_line_id is None
    assert reconciliation.outstanding_snapshot is None
    # The match stands and is free again.
    assert db.get(BankMatch, match.id) is not None
    assert match.reconciliation_id is None
    # The cache rewinds to whatever is now the latest locked one — here, nothing.
    assert banking.bank("BK-RWF").last_reconciled_at is None
    assert banking.bank("BK-RWF").last_reconciled_balance is None
    assert_bank_invariants(db, banking.company_id)


def test_only_the_latest_locked_reconciliation_may_be_reopened(
    db: Session, banking: Banking
) -> None:
    """The tape's row 16 again: `BRC-2` is refused while `BRC-4` stands."""
    first = _open(db, banking, on=SEP_30, balance=Decimal(0))
    db.commit()
    reconciliation_service.lock(db, banking.company_id, first.id, actor=banking.owner)
    db.commit()
    second = _open(db, banking, on=OCT_31, balance=Decimal(0))
    db.commit()
    reconciliation_service.lock(db, banking.company_id, second.id, actor=banking.owner)
    db.commit()

    with pytest.raises(LedgerStateError) as excinfo:
        reconciliation_service.reopen(
            db, banking.company_id, first.id, reason="a mistake", actor=banking.owner
        )

    assert excinfo.value.code == "reconciliation_not_latest"
    assert second.number in excinfo.value.message
    db.rollback()


def test_reopening_rewinds_the_cache_to_the_previous_locked_one(
    db: Session, banking: Banking
) -> None:
    """The tape's row 16's second half: reopening `BRC-4` puts the cache back to `BRC-2`'s own
    date and balance, not to nothing."""
    september = cashbook(
        db, banking, account_code="1120", amount=Decimal(1090500), on=SEP_3
    )
    db.commit()
    matching.tick(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        journal_line_ids=[bank_line_of(db, banking, september, "1120").id],
        actor=banking.owner,
    )
    first = _open(db, banking, on=SEP_30, balance=Decimal(1090500))
    db.commit()
    reconciliation_service.lock(db, banking.company_id, first.id, actor=banking.owner)
    db.commit()

    october = cashbook(db, banking, account_code="1120", amount=Decimal(-90000), on=OCT_3)
    db.commit()
    matching.tick(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        journal_line_ids=[bank_line_of(db, banking, october, "1120").id],
        actor=banking.owner,
    )
    second = _open(db, banking, on=OCT_31, balance=Decimal(1000500))
    db.commit()
    reconciliation_service.lock(db, banking.company_id, second.id, actor=banking.owner)
    db.commit()
    assert banking.bank("BK-RWF").last_reconciled_balance == Decimal(1000500)

    reconciliation_service.reopen(
        db, banking.company_id, second.id, reason="the bank restated", actor=banking.owner
    )
    db.commit()

    assert banking.bank("BK-RWF").last_reconciled_at == SEP_30
    assert banking.bank("BK-RWF").last_reconciled_balance == Decimal(1090500)
    assert_bank_invariants(db, banking.company_id)


def test_reopening_needs_a_reason(db: Session, banking: Banking) -> None:
    reconciliation = _open(db, banking, on=SEP_30, balance=Decimal(0))
    db.commit()
    reconciliation_service.lock(
        db, banking.company_id, reconciliation.id, actor=banking.owner
    )
    db.commit()

    with pytest.raises(LedgerStateError) as excinfo:
        reconciliation_service.reopen(
            db, banking.company_id, reconciliation.id, reason="  ", actor=banking.owner
        )

    assert excinfo.value.code == "reason_required"
    db.rollback()


def test_an_open_reconciliation_cannot_be_reopened(db: Session, banking: Banking) -> None:
    reconciliation = _open(db, banking, on=SEP_30, balance=Decimal(0))
    db.commit()

    with pytest.raises(LedgerStateError) as excinfo:
        reconciliation_service.reopen(
            db, banking.company_id, reconciliation.id, reason="why", actor=banking.owner
        )

    assert excinfo.value.code == "reconciliation_not_locked"
    db.rollback()


def test_an_account_with_no_lines_locks_with_a_high_water_mark_of_zero(
    db: Session, banking: Banking
) -> None:
    """Found by the property machine (P8 step 2), not by review.

    A bank account opened and reconciled before its first transaction is an ordinary thing to
    do: the ledger is zero, the bank says zero, and it locks at a zero difference. `max()` over
    no lines is NULL, and NULL in `high_water_line_id` means *unknown* — which would be a lie,
    since what is known about an empty account is that no line existed.

    The consequence is not cosmetic. `late_lines` and the workspace's "dated inside BRC-n" both
    skip a NULL mark, so such a reconciliation would never flag a late line again however many
    were posted into its period; and clause 4 could not reproduce its figures, because nothing
    said which lines it was struck over.
    """
    reconciliation = _open(db, banking, on=SEP_30, balance=Decimal(0))
    db.commit()

    reconciliation_service.lock(
        db, banking.company_id, reconciliation.id, actor=banking.owner
    )
    db.commit()

    assert reconciliation.high_water_line_id == 0
    assert_bank_invariants(db, banking.company_id)

    # And a line posted afterwards, dated inside the locked period, is properly late.
    late = cashbook(db, banking, account_code="1120", amount=Decimal(-200), on=SEP_28)
    db.commit()
    late_line = bank_line_of(db, banking, late, "1120")

    assert reconciliation_service.late_lines(db, banking.company_id, reconciliation) == [
        late_line.id
    ]
    following = _open(db, banking, on=OCT_31, balance=Decimal(0))
    db.commit()
    figures = reconciliation_service.live_figures(db, banking.company_id, following)
    assert {item.dated_inside for item in figures.outstanding} == {reconciliation.number}
    assert_bank_invariants(db, banking.company_id)
