"""Matching: the balance rule, the three auto rules in order, n:m, tick, unmatch, and posting
from a statement line (P8 decision 4).

Every figure here is worked by hand in the test that uses it. `assert_bank_invariants` runs
after anything that changes state, which is what makes these tests about the *suite* as much as
about the operation — a match that passes its own assertion and breaks clause 2 is a match that
would have reached the tape.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.banking import matching
from app.banking import statements as statements_service
from app.core.errors import ConflictError
from app.kernel.errors import LedgerStateError, PostingError
from app.models.banking import (
    BankMatch,
    BankMatchJournalLine,
    BankMatchKind,
    BankMatchRule,
    BankStatementLine,
)
from app.models.journal import JournalEntry
from tests.banking.conftest import Banking, bank_line_of, cashbook, key_statement
from tests.banking.invariants import assert_bank_invariants
from tests.kernel.conftest import YEAR

SEP_3 = date(YEAR, 9, 3)
SEP_5 = date(YEAR, 9, 5)
SEP_12 = date(YEAR, 9, 12)
SEP_30 = date(YEAR, 9, 30)
OCT_2 = date(YEAR, 10, 2)


def _statement_lines(db: Session, banking: Banking, account: str = "BK-RWF"):  # noqa: ANN202
    return list(
        db.scalars(
            select(BankStatementLine)
            .where(BankStatementLine.bank_account_id == banking.bank(account).id)
            .order_by(BankStatementLine.id)
        )
    )


# --- The balance rule ---------------------------------------------------------------------------


def test_a_match_that_balances_is_written(db: Session, banking: Banking) -> None:
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(118000), on=SEP_3)
    line = bank_line_of(db, banking, entry, "1120")
    key_statement(
        db, banking, lines=[(SEP_3, "INV-1 C1", Decimal(118000))]
    )
    db.commit()
    statement_line = _statement_lines(db, banking)[0]

    match = matching.create_match(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        statement_line_ids=[statement_line.id],
        journal_line_ids=[line.id],
        kind=BankMatchKind.MANUAL,
        rule=BankMatchRule.MANUAL,
        actor=banking.owner,
    )
    db.commit()

    assert match.kind == BankMatchKind.MANUAL
    assert matching.members_of(db, banking.company_id, match.id) == (
        [statement_line.id],
        [line.id],
    )
    assert_bank_invariants(db, banking.company_id)


def test_a_match_that_does_not_balance_is_refused_with_the_difference(
    db: Session, banking: Banking
) -> None:
    """The tape's row 8: a statement credit of 40 000 against a payment of −70 000 is out by
    110 000, and the difference is posted rather than matched."""
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(-70000), on=SEP_12)
    line = bank_line_of(db, banking, entry, "1120")
    key_statement(db, banking, lines=[(SEP_12, "CASH DEPOSIT DEP 4471", Decimal(40000))])
    db.commit()
    statement_line = _statement_lines(db, banking)[0]

    with pytest.raises(LedgerStateError) as excinfo:
        matching.create_match(
            db,
            banking.company_id,
            bank_account_id=banking.bank("BK-RWF").id,
            statement_line_ids=[statement_line.id],
            journal_line_ids=[line.id],
            kind=BankMatchKind.MANUAL,
            rule=BankMatchRule.MANUAL,
            actor=banking.owner,
        )

    assert excinfo.value.code == "match_unbalanced"
    assert "110000" in excinfo.value.message.replace(" ", "")
    db.rollback()
    # Nothing was written: the refusals run before the first insert.
    assert db.scalar(select(BankMatch.id)) is None


def test_a_refused_match_leaves_no_members(db: Session, banking: Banking) -> None:
    """The half of "refuse before you write" that a refusal message cannot show."""
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=SEP_3)
    line = bank_line_of(db, banking, entry, "1120")
    key_statement(db, banking, lines=[(SEP_3, "A DEPOSIT", Decimal(999))])
    db.commit()
    statement_line = _statement_lines(db, banking)[0]

    with pytest.raises(LedgerStateError):
        matching.create_match(
            db,
            banking.company_id,
            bank_account_id=banking.bank("BK-RWF").id,
            statement_line_ids=[statement_line.id],
            journal_line_ids=[line.id],
            kind=BankMatchKind.MANUAL,
            rule=BankMatchRule.MANUAL,
            actor=banking.owner,
        )
    db.rollback()

    assert db.scalar(select(BankMatchJournalLine.id)) is None


def test_n_to_m_is_one_operation_in_both_directions(db: Session, banking: Banking) -> None:
    """A bulk deposit against three receipts, and one payment against two statement lines —
    the two shapes decision 4 names, each a single match."""
    receipts = [
        bank_line_of(
            db,
            banking,
            cashbook(db, banking, account_code="1120", amount=amount, on=SEP_3),
            "1120",
        )
        for amount in (Decimal(40000), Decimal(50000), Decimal(28000))
    ]
    payment = bank_line_of(
        db,
        banking,
        cashbook(db, banking, account_code="1120", amount=Decimal(-30000), on=SEP_5),
        "1120",
    )
    key_statement(
        db,
        banking,
        lines=[
            (SEP_3, "BULK DEPOSIT", Decimal(118000)),
            (SEP_5, "TRANSFER PART 1", Decimal(-20000)),
            (SEP_5, "TRANSFER PART 2", Decimal(-10000)),
        ],
    )
    db.commit()
    lines = _statement_lines(db, banking)

    many_to_one = matching.create_match(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        statement_line_ids=[lines[0].id],
        journal_line_ids=[line.id for line in receipts],
        kind=BankMatchKind.MANUAL,
        rule=BankMatchRule.MANUAL,
        actor=banking.owner,
    )
    one_to_many = matching.create_match(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        statement_line_ids=[lines[1].id, lines[2].id],
        journal_line_ids=[payment.id],
        kind=BankMatchKind.MANUAL,
        rule=BankMatchRule.MANUAL,
        actor=banking.owner,
    )
    db.commit()

    assert len(matching.members_of(db, banking.company_id, many_to_one.id)[1]) == 3
    assert len(matching.members_of(db, banking.company_id, one_to_many.id)[0]) == 2
    assert_bank_invariants(db, banking.company_id)


def test_a_line_cannot_be_in_two_matches(db: Session, banking: Banking) -> None:
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=SEP_3)
    line = bank_line_of(db, banking, entry, "1120")
    key_statement(
        db,
        banking,
        lines=[(SEP_3, "ONE", Decimal(1000)), (SEP_3, "TWO", Decimal(1000))],
    )
    db.commit()
    lines = _statement_lines(db, banking)
    matching.create_match(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        statement_line_ids=[lines[0].id],
        journal_line_ids=[line.id],
        kind=BankMatchKind.MANUAL,
        rule=BankMatchRule.MANUAL,
        actor=banking.owner,
    )
    db.commit()

    with pytest.raises(ConflictError) as excinfo:
        matching.create_match(
            db,
            banking.company_id,
            bank_account_id=banking.bank("BK-RWF").id,
            statement_line_ids=[lines[1].id],
            journal_line_ids=[line.id],
            kind=BankMatchKind.MANUAL,
            rule=BankMatchRule.MANUAL,
            actor=banking.owner,
        )

    assert excinfo.value.code == "journal_line_matched"
    db.rollback()


def test_a_match_across_two_bank_accounts_is_refused(db: Session, banking: Banking) -> None:
    """The tape's row 18. Two accounts' reconciliations would otherwise both claim the same
    movement."""
    usd = bank_line_of(
        db,
        banking,
        cashbook(
            db,
            banking,
            account_code="1121",
            amount=Decimal("500.00"),
            on=SEP_3,
            currency="USD",
        ),
        "1121",
    )
    key_statement(db, banking, lines=[(SEP_3, "INWARD", Decimal(500))])
    db.commit()
    statement_line = _statement_lines(db, banking)[0]

    with pytest.raises(LedgerStateError) as excinfo:
        matching.create_match(
            db,
            banking.company_id,
            bank_account_id=banking.bank("BK-RWF").id,
            statement_line_ids=[statement_line.id],
            journal_line_ids=[usd.id],
            kind=BankMatchKind.MANUAL,
            rule=BankMatchRule.MANUAL,
            actor=banking.owner,
        )

    assert excinfo.value.code == "match_across_accounts"
    db.rollback()


# --- Tick, and unmatch ---------------------------------------------------------------------------


def test_a_tick_has_no_statement_side_and_still_reconciles(
    db: Session, banking: Banking
) -> None:
    """Paper mode. There is no statement to balance against, so the balance rule has nothing to
    check — and the ledger line is no longer outstanding, which is the whole effect."""
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(1000000), on=SEP_3)
    line = bank_line_of(db, banking, entry, "1120")
    db.commit()

    match = matching.tick(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        journal_line_ids=[line.id],
        actor=banking.owner,
    )
    db.commit()

    assert (match.kind, match.rule) == (BankMatchKind.TICK, BankMatchRule.TICK)
    assert matching.members_of(db, banking.company_id, match.id) == ([], [line.id])
    assert_bank_invariants(db, banking.company_id)


def test_unmatch_removes_the_assertion(db: Session, banking: Banking) -> None:
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=SEP_3)
    line = bank_line_of(db, banking, entry, "1120")
    db.commit()
    match = matching.tick(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        journal_line_ids=[line.id],
        actor=banking.owner,
    )
    db.commit()

    matching.unmatch(db, banking.company_id, match.id, actor=banking.owner)
    db.commit()

    assert db.scalar(select(BankMatch.id)) is None
    assert db.scalar(select(BankMatchJournalLine.id)) is None
    assert_bank_invariants(db, banking.company_id)


def test_an_empty_match_is_refused(db: Session, banking: Banking) -> None:
    with pytest.raises(LedgerStateError) as excinfo:
        matching.create_match(
            db,
            banking.company_id,
            bank_account_id=banking.bank("BK-RWF").id,
            statement_line_ids=[],
            journal_line_ids=[],
            kind=BankMatchKind.MANUAL,
            rule=BankMatchRule.MANUAL,
            actor=banking.owner,
        )

    assert excinfo.value.code == "match_is_empty"
    db.rollback()


# --- Effectiveness -------------------------------------------------------------------------------


def test_a_deposit_in_transit_is_not_effective_at_the_month_end(
    db: Session, banking: Banking
) -> None:
    """The tape's shape, stated as a property: a receipt dated 30 September matched to a
    statement credit the bank booked on 2 October is outstanding at 30 September and reconciled
    at 31 October. Reading only the ledger side would reconcile it a month early."""
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(40000), on=SEP_30)
    line = bank_line_of(db, banking, entry, "1120")
    key_statement(db, banking, lines=[(OCT_2, "CASH DEPOSIT", Decimal(40000))])
    db.commit()
    statement_line = _statement_lines(db, banking)[0]
    match = matching.create_match(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        statement_line_ids=[statement_line.id],
        journal_line_ids=[line.id],
        kind=BankMatchKind.MANUAL,
        rule=BankMatchRule.MANUAL,
        actor=banking.owner,
    )
    db.commit()

    assert matching.effective_at(db, banking.company_id, match.id, SEP_30) is False
    assert matching.effective_at(db, banking.company_id, match.id, date(YEAR, 10, 31)) is True


# --- The three auto rules, in order ---------------------------------------------------------------


def test_the_reference_rule_matches_on_a_token_the_bank_printed(
    db: Session, banking: Banking
) -> None:
    """(i) The statement's narrative contains the ledger's own number — `INWARD TRF C1 INV-3`
    contains `INV-3`. The search is that way round because the bank prints its text *around*
    what the payer typed."""
    entry = cashbook(
        db,
        banking,
        account_code="1120",
        amount=Decimal(118000),
        on=SEP_3,
        reference="INV-1 C1",
    )
    line = bank_line_of(db, banking, entry, "1120")
    key_statement(db, banking, lines=[(SEP_3, "INWARD TRF INV-1 C1", Decimal(118000))])
    db.commit()

    result = matching.auto_match(
        db, banking.company_id, banking.bank("BK-RWF").id, actor=banking.owner
    )
    db.commit()

    assert result.matched_count == 1
    assert result.matched[0].rule == BankMatchRule.REFERENCE
    assert matching.members_of(db, banking.company_id, result.matched[0].id)[1] == [line.id]
    assert_bank_invariants(db, banking.company_id)


def test_the_amount_and_date_rule_matches_within_three_days(
    db: Session, banking: Banking
) -> None:
    """(iii) Nothing to go on but a figure, so the window is tight."""
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(59000), on=SEP_3)
    line = bank_line_of(db, banking, entry, "1120")
    key_statement(db, banking, lines=[(SEP_5, "MOMO DEPOSIT 0788", Decimal(59000))])
    db.commit()

    result = matching.auto_match(
        db, banking.company_id, banking.bank("BK-RWF").id, actor=banking.owner
    )
    db.commit()

    assert result.matched_count == 1
    assert result.matched[0].rule == BankMatchRule.AMOUNT_DATE
    assert matching.members_of(db, banking.company_id, result.matched[0].id)[1] == [line.id]


def test_the_amount_and_date_rule_does_not_reach_past_its_window(
    db: Session, banking: Banking
) -> None:
    """Four days is outside ±3, and the rule declines rather than stretching. A window that
    grew to fit would make the weakest rule the widest."""
    cashbook(db, banking, account_code="1120", amount=Decimal(59000), on=SEP_3)
    key_statement(db, banking, lines=[(date(YEAR, 9, 7), "A DEPOSIT", Decimal(59000))])
    db.commit()

    result = matching.auto_match(
        db, banking.company_id, banking.bank("BK-RWF").id, actor=banking.owner
    )

    assert result.matched_count == 0


def test_two_candidates_of_the_same_amount_are_left_for_a_person(
    db: Session, banking: Banking
) -> None:
    """Ambiguity is not a match. Two receipts of 50 000 in one week is an ordinary Tuesday, and
    matching either would put a coin toss in the reconciliation as fact."""
    for day in (SEP_3, SEP_5):
        cashbook(db, banking, account_code="1120", amount=Decimal(50000), on=day)
    key_statement(db, banking, lines=[(date(YEAR, 9, 4), "A DEPOSIT", Decimal(50000))])
    db.commit()

    result = matching.auto_match(
        db, banking.company_id, banking.bank("BK-RWF").id, actor=banking.owner
    )
    db.commit()

    assert result.matched_count == 0
    assert len(result.ambiguous) == 1
    assert len(next(iter(result.ambiguous.values()))) == 2
    assert_bank_invariants(db, banking.company_id)


def test_the_reference_rule_wins_over_amount_and_date(db: Session, banking: Banking) -> None:
    """The order is an order of **evidence**. Two receipts of the same amount three days apart
    would tie under `amount_date`; the one the bank named by number is not a guess, so
    `reference` settles it and the tie never happens."""
    named = cashbook(
        db,
        banking,
        account_code="1120",
        amount=Decimal(50000),
        on=SEP_3,
        reference="INV-77",
    )
    cashbook(db, banking, account_code="1120", amount=Decimal(50000), on=SEP_5)
    key_statement(db, banking, lines=[(date(YEAR, 9, 4), "TRF INV-77 THANKS", Decimal(50000))])
    db.commit()

    result = matching.auto_match(
        db, banking.company_id, banking.bank("BK-RWF").id, actor=banking.owner
    )
    db.commit()

    assert result.matched_count == 1
    assert result.matched[0].rule == BankMatchRule.REFERENCE
    assert matching.members_of(db, banking.company_id, result.matched[0].id)[1] == [
        bank_line_of(db, banking, named, "1120").id
    ]


def test_auto_match_never_touches_an_existing_match(db: Session, banking: Banking) -> None:
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(59000), on=SEP_3)
    line = bank_line_of(db, banking, entry, "1120")
    key_statement(db, banking, lines=[(SEP_3, "A DEPOSIT", Decimal(59000))])
    db.commit()
    ticked = matching.tick(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        journal_line_ids=[line.id],
        actor=banking.owner,
    )
    db.commit()

    result = matching.auto_match(
        db, banking.company_id, banking.bank("BK-RWF").id, actor=banking.owner
    )
    db.commit()

    assert result.matched_count == 0
    assert db.scalar(select(BankMatch.id)) == ticked.id


def test_a_matched_statement_line_is_skipped_by_auto_match(
    db: Session, banking: Banking
) -> None:
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(59000), on=SEP_3)
    line = bank_line_of(db, banking, entry, "1120")
    key_statement(db, banking, lines=[(SEP_3, "A DEPOSIT", Decimal(59000))])
    db.commit()
    statement_line = _statement_lines(db, banking)[0]
    matching.create_match(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        statement_line_ids=[statement_line.id],
        journal_line_ids=[line.id],
        kind=BankMatchKind.MANUAL,
        rule=BankMatchRule.MANUAL,
        actor=banking.owner,
    )
    db.commit()

    assert (
        matching.auto_match(
            db, banking.company_id, banking.bank("BK-RWF").id, actor=banking.owner
        ).matched_count
        == 0
    )


def test_a_voided_statements_lines_leave_the_matcher(db: Session, banking: Banking) -> None:
    """Decision 3, seen from the matcher: a voided statement's lines are in no listing and no
    count, so `auto_match` has nothing to work with."""
    cashbook(db, banking, account_code="1120", amount=Decimal(59000), on=SEP_3)
    result = key_statement(db, banking, lines=[(SEP_3, "A DEPOSIT", Decimal(59000))])
    db.commit()
    statements_service.void(
        db, banking.company_id, result.statement.id, actor=banking.owner
    )
    db.commit()

    assert (
        matching.auto_match(
            db, banking.company_id, banking.bank("BK-RWF").id, actor=banking.owner
        ).matched_count
        == 0
    )
    assert_bank_invariants(db, banking.company_id)


def test_the_usd_receipt_into_the_rwf_account_matches_on_its_base_amount(
    db: Session, banking: Banking
) -> None:
    """Decision 2's one-sided rule, seen by the matcher. A USD 200 receipt into the RWF account
    reaches the bank as francs, so the statement shows the *base* amount — and the reconciled
    amount of that ledger line is its `base_amount`, which is what the rule compares."""
    entry = cashbook(
        db,
        banking,
        account_code="1120",
        amount=Decimal("200.00"),
        on=SEP_3,
        currency="USD",
    )
    line = bank_line_of(db, banking, entry, "1120")
    db.commit()
    base_amount = line.base_amount
    assert base_amount != Decimal("200.00")

    key_statement(db, banking, lines=[(SEP_3, "INWARD TRF USD 200", base_amount)])
    db.commit()

    result = matching.auto_match(
        db, banking.company_id, banking.bank("BK-RWF").id, actor=banking.owner
    )
    db.commit()

    assert result.matched_count == 1
    assert matching.members_of(db, banking.company_id, result.matched[0].id)[1] == [line.id]
    assert_bank_invariants(db, banking.company_id)


# --- Rules as prefill ----------------------------------------------------------------------------


def test_a_rule_prefills_the_drawer(db: Session, banking: Banking) -> None:
    from app.banking import accounts as accounts_service

    accounts_service.create_rule(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        pattern="ACCOUNT FEE",
        gl_account_id=banking.ledger.acct("6700"),
        description="Monthly account fee",
        priority=10,
        actor=banking.owner,
    )
    key_statement(db, banking, lines=[(SEP_12, "MONTHLY ACCOUNT FEE", Decimal(-2500))])
    db.commit()
    line = _statement_lines(db, banking)[0]

    prefill = matching.prefill_for(db, banking.company_id, line)

    assert prefill.gl_account_id == banking.ledger.acct("6700")
    assert prefill.description == "Monthly account fee"
    assert prefill.kind == "payment"


def test_without_a_rule_the_drawer_opens_on_the_settings_defaults(
    db: Session, banking: Banking
) -> None:
    """`bank_charges_account_id` on a debit, `bank_interest_account_id` on a credit — the two
    keys step 1 seeded, and the reason they are defaults rather than a posting map."""
    key_statement(
        db,
        banking,
        lines=[
            (SEP_12, "SOME CHARGE", Decimal(-500)),
            (SEP_12, "SOME CREDIT", Decimal(500)),
        ],
    )
    db.commit()
    debit, credit = _statement_lines(db, banking)

    assert matching.prefill_for(db, banking.company_id, debit).gl_account_id == (
        banking.ledger.acct("6700")
    )
    assert matching.prefill_for(db, banking.company_id, credit).gl_account_id == (
        banking.ledger.acct("4300")
    )


def test_the_highest_priority_rule_wins(db: Session, banking: Banking) -> None:
    from app.banking import accounts as accounts_service

    for pattern, account, priority in (
        ("FEE", "6990", 50),
        ("ACCOUNT FEE", "6700", 10),
    ):
        accounts_service.create_rule(
            db,
            banking.company_id,
            bank_account_id=banking.bank("BK-RWF").id,
            pattern=pattern,
            gl_account_id=banking.ledger.acct(account),
            priority=priority,
            actor=banking.owner,
        )
    key_statement(db, banking, lines=[(SEP_12, "MONTHLY ACCOUNT FEE", Decimal(-2500))])
    db.commit()
    line = _statement_lines(db, banking)[0]

    assert matching.prefill_for(db, banking.company_id, line).gl_account_id == (
        banking.ledger.acct("6700")
    )


# --- Posting from a statement line ----------------------------------------------------------------


def test_posting_a_fee_from_its_line_writes_the_entry_and_the_match_together(
    db: Session, banking: Banking
) -> None:
    """The tape's row 9. The posting and the match are one transaction: a posted line is never
    left unmatched, and a failed posting leaves no match."""
    key_statement(db, banking, lines=[(SEP_12, "MONTHLY ACCOUNT FEE", Decimal(-2500))])
    db.commit()
    line = _statement_lines(db, banking)[0]

    posted = matching.post_cashbook_from_line(
        db,
        banking.company_id,
        line.id,
        gl_account_id=banking.ledger.acct("6700"),
        actor=banking.owner,
    )
    db.commit()

    entry = db.get(JournalEntry, posted.entry_id)
    assert entry.doc_type == "CB"
    assert entry.entry_date == SEP_12
    assert posted.match.rule == BankMatchRule.POSTED_FROM_STATEMENT
    assert posted.match.kind == BankMatchKind.POSTED
    assert matching.members_of(db, banking.company_id, posted.match.id) == (
        [line.id],
        [posted.journal_line_id],
    )
    # The bank side is the derived line, and it carries the statement line's own sign.
    bank_line = bank_line_of(db, banking, entry, "1120")
    assert bank_line.amount == Decimal(-2500)
    assert_bank_invariants(db, banking.company_id)


def test_a_failed_posting_leaves_no_match(db: Session, banking: Banking) -> None:
    """The other half of the same transaction, and the one nobody sees on a screen: a drawer
    aimed at a control account is refused by the engine, and the reconciliation must not be
    left claiming a line that was never written.

    The refusal is **`control_account_direct_posting`**, not the
    `control_account_manual_posting` decision 4 names. Both exist and the difference is the
    point: `manual_posting` is the `ManualJournal`-only branch, and what a drawer posts is a
    `CashbookEntry`, so what refuses it is P4's `control_account_modules` registry — `cb` is
    not among the modules paired with `ar`. The registry is the stronger of the two (it is
    also `VN007` in the database), so the guard decision 4 was reaching for is there and then
    some; the prompt's wording is one refusal out.
    """
    key_statement(db, banking, lines=[(SEP_12, "SOMETHING", Decimal(-2500))])
    db.commit()
    line = _statement_lines(db, banking)[0]

    with pytest.raises(PostingError) as excinfo:
        matching.post_cashbook_from_line(
            db,
            banking.company_id,
            line.id,
            gl_account_id=banking.ledger.acct("1200"),  # the AR control account
            actor=banking.owner,
        )

    assert excinfo.value.code == "control_account_direct_posting"
    db.rollback()
    assert db.scalar(select(BankMatch.id)) is None


def test_a_credit_posts_as_a_receipt_and_a_debit_as_a_payment(
    db: Session, banking: Banking
) -> None:
    """The kernel decides the sign and it follows the bank's own line: money in is a receipt,
    money out a payment. Nothing here asks the user which."""
    key_statement(
        db,
        banking,
        lines=[
            (SEP_12, "INTEREST", Decimal(1200)),
            (SEP_12, "CHARGE", Decimal(-500)),
        ],
    )
    db.commit()
    credit, debit = _statement_lines(db, banking)

    receipt = matching.post_cashbook_from_line(
        db, banking.company_id, credit.id, gl_account_id=banking.ledger.acct("4300"),
        actor=banking.owner,
    )
    payment = matching.post_cashbook_from_line(
        db, banking.company_id, debit.id, gl_account_id=banking.ledger.acct("6700"),
        actor=banking.owner,
    )
    db.commit()

    assert bank_line_of(
        db, banking, db.get(JournalEntry, receipt.entry_id), "1120"
    ).amount == Decimal(1200)
    assert bank_line_of(
        db, banking, db.get(JournalEntry, payment.entry_id), "1120"
    ).amount == Decimal(-500)
    assert_bank_invariants(db, banking.company_id)


def test_posting_from_an_already_matched_line_is_refused(
    db: Session, banking: Banking
) -> None:
    """Otherwise the fee is posted twice: once by whoever matched it and once by whoever
    pressed Post on a screen that had not refreshed."""
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(-2500), on=SEP_12)
    line = bank_line_of(db, banking, entry, "1120")
    key_statement(db, banking, lines=[(SEP_12, "MONTHLY ACCOUNT FEE", Decimal(-2500))])
    db.commit()
    statement_line = _statement_lines(db, banking)[0]
    matching.create_match(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        statement_line_ids=[statement_line.id],
        journal_line_ids=[line.id],
        kind=BankMatchKind.MANUAL,
        rule=BankMatchRule.MANUAL,
        actor=banking.owner,
    )
    db.commit()

    with pytest.raises(ConflictError) as excinfo:
        matching.post_cashbook_from_line(
            db,
            banking.company_id,
            statement_line.id,
            gl_account_id=banking.ledger.acct("6700"),
            actor=banking.owner,
        )

    assert excinfo.value.code == "statement_line_matched"
    db.rollback()


def test_posting_a_receipt_from_a_line_produces_an_unallocated_settlement(
    db: Session, banking: Banking
) -> None:
    """Decision 4's other drawer. **Unallocated**: which invoices a receipt pays is the P4
    allocation screen's decision, and a reconciliation that allocated as a side effect would be
    making a subledger decision from a bank statement."""
    from app.models.subledger import DocumentStatus, PartnerDocument

    key_statement(db, banking, lines=[(SEP_12, "CASH DEPOSIT DEP 4471", Decimal(40000))])
    db.commit()
    line = _statement_lines(db, banking)[0]

    posted = matching.post_settlement_from_line(
        db,
        banking.company_id,
        line.id,
        partner_id=banking.customer.id,
        actor=banking.owner,
    )
    db.commit()

    document = db.get(PartnerDocument, posted.document_id)
    assert document.status == DocumentStatus.POSTED
    assert document.number.startswith("RCT-")
    assert document.open_amount == Decimal(40000)
    assert posted.match.rule == BankMatchRule.POSTED_FROM_STATEMENT
    assert_bank_invariants(db, banking.company_id)
