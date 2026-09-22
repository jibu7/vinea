"""The banking refusals — and the one *decline* — a random plan cannot reliably reach, each with
its precondition **constructed** rather than hoped for.

P7's rule, and the reason it exists. The first deep pass of P8's machine came back with
`match_unbalanced` at 2 and `reconciliation_locked` at 0 against a floor of 3 — with every reach
counter healthy. That is the census saying *the generator cannot get there*, not *the guard is
broken*, and the answer to it is a targeted property rather than a bigger `max_examples`:
buying reach with examples is the expensive way to fix a generator, and it makes the nightly
longer to be lucky more often.

**Step 3 added a fourth, and it is a decline rather than a refusal.** `auto-match: found a tie
and declined` read 7 at step 2 and **0** on step 3's first deep pass: the plan gained payment
runs, supplier invoices and the file-import path, so every other operation is drawn less often
and the narrow window in which two equal unmatched ledger lines sit inside one statement line's
±3 days closed. Nothing about the matcher changed — which is exactly the case P7's rule is
written for, and the tie now has its own property below. It is the most important of the four
to hold: a matcher that guessed between two candidates would put a fact in the reconciliation
that nobody checked, and unlike a refusal there is no error message to notice its absence.

The first three are **conjunctions three operations deep**:

* `match_unbalanced` needs a live statement line and an unmatched ledger line to exist *at the
  same time* and to be of *different* amounts — and this phase's statements are generated from
  the ledger, so a blindly drawn pair usually balances. The machine matched 46 of 48 blind
  pairs successfully, which is the generator being too good at its job.
* `reconciliation_locked` needs a lock to have succeeded, a match to have been assigned to it,
  and *that* match to be the one drawn for unmatching.
* `statement_lines_unmatched` needs an unmatched statement line dated on or before a
  reconciliation date *and* a lock attempted while it is still there. The machine attempted 77
  locks and reached it once: auto-match runs often and this phase's statements are generated
  from the ledger, so by lock time there is usually nothing left unmatched. The **interesting**
  case is narrower still — a difference of *zero* with an unmatched line, which is the two
  errors cancelling that the refusal ordering exists to stop — and a random plan will
  essentially never produce it.

Each property below draws over the *shape* of its precondition — amounts, dates, how many
lines — so it is still a property rather than an example, but the conjunction itself is built.
The refusals are counted into the machine's own census, so the nightly prints one set of
numbers rather than two.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.banking import matching
from app.banking import reconciliation as reconciliation_service
from app.banking import statements as statements_service
from app.banking.formats import ParsedLine
from app.kernel.errors import LedgerStateError
from app.models.banking import BankMatchKind, BankMatchRule, BankStatementLine
from tests.banking.conftest import Banking, bank_line_of, build_banking, cashbook
from tests.banking.invariants import assert_bank_invariants
from tests.banking.test_property_banking import _REACH, _REFUSALS, _count
from tests.kernel.conftest import YEAR

BASE_DAY = date(YEAR, 9, 1)
ZERO = Decimal(0)

_TENANTS = iter(range(1, 10_000))


def _tenant(db: Session, label: str) -> Banking:
    index = next(_TENANTS)
    return build_banking(
        db,
        company_name=f"Targeted {label} {index} Ltd",
        email=f"targeted.{label}.{index}@example.test",
    )


def _key_one_line(
    db: Session, banking: Banking, *, on: date, description: str, amount: Decimal
) -> BankStatementLine:
    statements_service.import_manual(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        lines=[
            ParsedLine(
                row=1,
                value_date=on,
                booking_date=None,
                description=description,
                reference=None,
                amount=amount,
                balance_after=None,
                external_id=None,
                occurrence=0,
            )
        ],
        opening_balance=ZERO,
        closing_balance=amount,
        actor=banking.owner,
    )
    db.flush()
    return db.scalars(
        select(BankStatementLine)
        .where(BankStatementLine.bank_account_id == banking.bank("BK-RWF").id)
        .order_by(BankStatementLine.id.desc())
    ).first()


#: Non-zero on both sides, and either sign. Zero is excluded by construction rather than by
#: `assume`: a statement line of nothing is not a movement — `bank_statement_lines.amount <> 0`
#: is a CHECK — and a *ledger* line of nothing is refused by the engine as `zero_amount_line`.
#: Drawing them and filtering afterwards would spend examples on states neither side can hold.
_NON_ZERO_HUNDREDS = st.integers(min_value=1, max_value=900).flatmap(
    lambda size: st.sampled_from([Decimal(size * 100), Decimal(-size * 100)])
)


@pytest.mark.slow
@given(
    ledger=_NON_ZERO_HUNDREDS,
    statement=_NON_ZERO_HUNDREDS,
    day=st.integers(min_value=0, max_value=25),
)
@settings(deadline=None)
def test_a_match_whose_two_sides_disagree_is_always_refused_with_the_difference(
    db: Session,
    ledger: Decimal,
    statement: Decimal,
    day: int,
) -> None:
    """`match_unbalanced`, with its precondition built.

    The construction is the whole property: a ledger line of one amount and a statement line of
    a *different* one, both live and both unmatched. What is drawn is the shape — both amounts,
    both signs, the date — so the claim is still "for any such pair", and what is constructed is
    only their coexistence. Both signs matter: a statement credit against a ledger debit is the
    shape the tape's row 8 refuses at 110 000.

    The refusal must name the difference, because that figure is what the workspace shows
    beside the button: `match_unbalanced` with no number is a screen saying "no" and nothing
    else.
    """
    if ledger == statement:
        return  # a balanced pair is the other test's subject
    banking = _tenant(db, "unbalanced")
    on = BASE_DAY + timedelta(days=day)

    entry = cashbook(db, banking, account_code="1120", amount=ledger, on=on)
    db.flush()
    ledger_line = bank_line_of(db, banking, entry, "1120")
    statement_line = _key_one_line(
        db, banking, on=on, description="A MOVEMENT", amount=statement
    )

    with pytest.raises(LedgerStateError) as excinfo:
        matching.create_match(
            db,
            banking.company_id,
            bank_account_id=banking.bank("BK-RWF").id,
            statement_line_ids=[statement_line.id],
            journal_line_ids=[ledger_line.id],
            kind=BankMatchKind.MANUAL,
            rule=BankMatchRule.MANUAL,
            actor=banking.owner,
        )

    assert excinfo.value.code == "match_unbalanced"
    _count(_REFUSALS, "match_unbalanced")
    # The difference, in the message and in the field error the screen renders.
    difference = statement - ledger
    assert f"{difference:+f}" in str(excinfo.value.field_errors)
    db.rollback()


@pytest.mark.slow
@given(
    amount=st.integers(min_value=1, max_value=900),
    day=st.integers(min_value=0, max_value=20),
    extra=st.integers(min_value=0, max_value=3),
)
@settings(deadline=None)
def test_a_match_inside_a_locked_reconciliation_is_always_refused_an_unmatch(
    db: Session, amount: int, day: int, extra: int
) -> None:
    """`reconciliation_locked`, with its precondition built.

    Three operations deep: a lock that succeeded, a match assigned to it, and that match chosen
    for unmatching. The machine reached the first two often and the third never, because the
    draw that picks a match to unmatch has no idea which ones are assigned.

    The property is that the refusal holds **whatever else is on the account** — `extra` puts
    unrelated matched lines in before the lock, so the assigned set is not always a single row
    — and that it names the reconciliation, because "reopen it first" is useless advice if the
    screen cannot say which one.
    """
    banking = _tenant(db, "locked")
    on = BASE_DAY + timedelta(days=day)
    row = banking.bank("BK-RWF")

    lines = []
    for index in range(extra + 1):
        entry = cashbook(
            db,
            banking,
            account_code="1120",
            amount=Decimal((amount + index) * 100),
            on=on,
        )
        db.flush()
        lines.append(bank_line_of(db, banking, entry, "1120"))
    matches = [
        matching.tick(
            db,
            banking.company_id,
            bank_account_id=row.id,
            journal_line_ids=[line.id],
            actor=banking.owner,
        )
        for line in lines
    ]
    db.flush()

    live = reconciliation_service.figures(
        db,
        banking.company_id,
        row,
        reconciliation_date=on + timedelta(days=5),
        statement_balance=ZERO,
    )
    reconciliation = reconciliation_service.open_reconciliation(
        db,
        banking.company_id,
        bank_account_id=row.id,
        reconciliation_date=on + timedelta(days=5),
        statement_balance=live.ledger_balance - live.outstanding_total,
        actor=banking.owner,
    )
    db.flush()
    reconciliation_service.lock(
        db, banking.company_id, reconciliation.id, actor=banking.owner
    )
    db.flush()
    assert all(match.reconciliation_id == reconciliation.id for match in matches)

    for match in matches:
        with pytest.raises(LedgerStateError) as excinfo:
            matching.unmatch(db, banking.company_id, match.id, actor=banking.owner)
        assert excinfo.value.code == "reconciliation_locked"
        _count(_REFUSALS, "reconciliation_locked")
        assert reconciliation.number in excinfo.value.message

    # The refusals left the state exactly as it was — which is the half a refusal message
    # cannot show, and the reason clause 4 still reproduces afterwards.
    assert_bank_invariants(db, banking.company_id)
    db.rollback()


@pytest.mark.slow
@given(
    amount=st.integers(min_value=1, max_value=900),
    day=st.integers(min_value=0, max_value=20),
)
@settings(deadline=None)
def test_reopening_frees_exactly_the_matches_the_lock_had_taken(
    db: Session, amount: int, day: int
) -> None:
    """The other side of the same conjunction, and the reason the refusal above is not simply
    "matches are permanent": reopening the reconciliation gives them back.

    Constructed for the same reason — the machine reopened 7 times in 300 examples, and never
    with an assigned match it then unmatched.
    """
    banking = _tenant(db, "reopen")
    on = BASE_DAY + timedelta(days=day)
    row = banking.bank("BK-RWF")

    entry = cashbook(
        db, banking, account_code="1120", amount=Decimal(amount * 100), on=on
    )
    db.flush()
    match = matching.tick(
        db,
        banking.company_id,
        bank_account_id=row.id,
        journal_line_ids=[bank_line_of(db, banking, entry, "1120").id],
        actor=banking.owner,
    )
    reconciliation = reconciliation_service.open_reconciliation(
        db,
        banking.company_id,
        bank_account_id=row.id,
        reconciliation_date=on + timedelta(days=5),
        statement_balance=Decimal(amount * 100),
        actor=banking.owner,
    )
    db.flush()
    reconciliation_service.lock(
        db, banking.company_id, reconciliation.id, actor=banking.owner
    )
    db.flush()
    assert match.reconciliation_id == reconciliation.id

    reconciliation_service.reopen(
        db, banking.company_id, reconciliation.id, reason="targeted", actor=banking.owner
    )
    db.flush()

    assert match.reconciliation_id is None
    # And now it unmatches, which is what "the matches stand, the sign-off is withdrawn" means
    # from the operator's side.
    matching.unmatch(db, banking.company_id, match.id, actor=banking.owner)
    db.flush()
    assert_bank_invariants(db, banking.company_id)
    db.rollback()


@pytest.mark.slow
@given(
    matched=_NON_ZERO_HUNDREDS,
    unexplained=_NON_ZERO_HUNDREDS,
    day=st.integers(min_value=0, max_value=20),
)
@settings(deadline=None)
def test_a_lock_is_refused_on_an_unexplained_statement_line_even_at_a_zero_difference(
    db: Session, matched: Decimal, unexplained: Decimal, day: int
) -> None:
    """`statement_lines_unmatched`, and the reason it is checked **before** the difference.

    The state constructed here is the dangerous one: the ledger foots to the bank's figure
    exactly — difference zero, the reconciliation *looks* finished — while the bank is showing a
    movement nobody has accounted for. That is two errors cancelling, and a lock that read only
    the difference would sign it off.

    A random plan will not produce it: it needs an unmatched statement line and a statement
    balance that happens to close without it, at the same date. The machine attempted 77 locks
    across a deep pass and reached the refusal once, with every reach counter healthy — the
    census saying "the generator cannot get there".

    `unexplained` is drawn over both signs and every size precisely because the difference is
    engineered to be zero regardless: the claim is that the refusal does not depend on the
    unexplained line's size, which is what "checked first" means.
    """
    banking = _tenant(db, "unexplained")
    on = BASE_DAY + timedelta(days=day)
    row = banking.bank("BK-RWF")

    # A ledger line, ticked, so it is not outstanding and the ledger foots on its own.
    entry = cashbook(db, banking, account_code="1120", amount=matched, on=on)
    db.flush()
    matching.tick(
        db,
        banking.company_id,
        bank_account_id=row.id,
        journal_line_ids=[bank_line_of(db, banking, entry, "1120").id],
        actor=banking.owner,
    )
    # And a statement line the ledger knows nothing about, dated inside the period.
    _key_one_line(db, banking, on=on, description="UNEXPLAINED", amount=unexplained)

    reconciliation = reconciliation_service.open_reconciliation(
        db,
        banking.company_id,
        bank_account_id=row.id,
        reconciliation_date=on + timedelta(days=5),
        # Exactly the closing figure: the difference is zero and the screen looks done.
        statement_balance=matched,
        actor=banking.owner,
    )
    db.flush()
    live = reconciliation_service.live_figures(db, banking.company_id, reconciliation)
    assert live.difference == ZERO, "the construction has to reach a zero difference"
    assert live.unmatched_statement_count == 1

    with pytest.raises(LedgerStateError) as excinfo:
        reconciliation_service.lock(
            db, banking.company_id, reconciliation.id, actor=banking.owner
        )

    assert excinfo.value.code == "statement_lines_unmatched"
    _count(_REFUSALS, "statement_lines_unmatched")
    assert "1 statement line" in excinfo.value.message
    db.rollback()


# --- the tie: a decline rather than a refusal ---------------------------------------------------


@pytest.mark.slow
@given(
    amount=st.integers(min_value=1, max_value=900),
    day=st.integers(min_value=0, max_value=20),
    gap=st.integers(min_value=0, max_value=3),
)
@settings(deadline=None)
def test_auto_match_declines_a_tie_and_leaves_both_candidates(
    db: Session, amount: int, day: int, gap: int
) -> None:
    """**Ambiguity is not a match**, with the tie constructed.

    Two ledger lines of the same amount within the `amount_date` rule's ±3 days, and one
    statement line that could be either. The rule finds two candidates, and `auto_match` must
    leave the line alone and report both — because matching either would be a coin toss the
    reconciliation would then carry as a fact somebody signed.

    What is drawn is the shape: the amount, the date, and the gap between the two ledger lines
    (0 to 3 days, all inside the window). The conjunction — two equal unmatched lines coexisting
    with a statement line that names neither — is built, because step 3's deep pass reached it
    zero times once payment runs joined the plan.

    **The anti-vacuity control is the second half**: the same construction with one of the two
    lines removed must match. Without it this property would pass just as happily against a
    matcher that matched nothing at all, which is the failure mode the whole census exists to
    catch.
    """
    banking = _tenant(db, "tie")
    on = BASE_DAY + timedelta(days=day)
    value = Decimal(amount)

    first = cashbook(db, banking, account_code="1120", amount=value, on=on)
    second = cashbook(
        db, banking, account_code="1120", amount=value, on=on + timedelta(days=gap)
    )
    db.flush()
    # Deliberately no document number or reference in the text: `_by_reference` must find
    # nothing, so the tie is `_by_amount_and_date`'s and the rule order is not what is under
    # test here.
    line = _key_one_line(db, banking, on=on, description="A DEPOSIT", amount=value)

    result = matching.auto_match(
        db, banking.company_id, banking.bank("BK-RWF").id, actor=banking.owner
    )

    assert result.matched == [], "a tie is not a match"
    assert line.id in result.ambiguous, "and the line is reported so a person can choose"
    candidates = result.ambiguous[line.id]
    assert len(candidates) == 2
    assert {candidate.rule for candidate in candidates} == {BankMatchRule.AMOUNT_DATE}
    assert {candidate.entry_id for candidate in candidates} == {first.id, second.id}
    _count(_REACH, "auto-match: found a tie and declined")
    assert_bank_invariants(db, banking.company_id)
    db.rollback()

    # The control. One candidate instead of two, everything else identical.
    banking = _tenant(db, "tie-control")
    cashbook(db, banking, account_code="1120", amount=value, on=on)
    db.flush()
    line = _key_one_line(db, banking, on=on, description="A DEPOSIT", amount=value)

    result = matching.auto_match(
        db, banking.company_id, banking.bank("BK-RWF").id, actor=banking.owner
    )

    assert result.ambiguous == {}, "one candidate is not a tie"
    assert len(result.matched) == 1, (
        "the construction cannot match at all — this property would pass over a matcher that "
        "matched nothing"
    )
    assert result.matched[0].rule == BankMatchRule.AMOUNT_DATE
    assert matching.members_of(db, banking.company_id, result.matched[0].id)[0] == [line.id]
    assert_bank_invariants(db, banking.company_id)
    db.rollback()
