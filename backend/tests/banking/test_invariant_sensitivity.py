"""Every clause of `assert_bank_invariants`, proven to catch the thing it guards.

An invariant suite is worth exactly what its clauses can see. This file breaks each one
*underneath the services* — by direct SQL, or with a trigger dropped — and asserts the suite
goes red. Without it, a clause with a typo'd column or an always-true comparison would sit in
the tape and the property machine reporting green about a question it never asked, which is
what P7 step 9's row 19 was and why that report says a census is a report until it is a gate.

Two conventions, both from P7 step 9 §C:

* **Break it underneath the service, not through it.** The services refuse these states, which
  is the point of the services — so a test that went through them would prove the refusal and
  say nothing about the clause. Direct SQL is how the state gets to exist at all.
* **Restore inside the test.** Every case runs in a transaction that is rolled back, or drops a
  trigger inside a `try/finally`. Nothing here leaves the schema changed.

Clause 8, the payment-run clause, arrived at step 3 with the runs themselves and is broken
below like the rest. Between step 2 and step 3 a `test_clause_8_is_not_written_yet` stood in its
place, so the gap was a fact a reader met rather than one they had to notice.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.banking import matching
from app.banking import reconciliation as reconciliation_service
from app.models.banking import (
    BankMatch,
    BankMatchKind,
    BankMatchRule,
    BankStatementLine,
)
from tests.banking.conftest import (
    Banking,
    ap_invoice,
    ap_supplier,
    bank_line_of,
    cashbook,
    key_statement,
)
from tests.banking.invariants import assert_bank_invariants
from tests.kernel.conftest import YEAR

SEP_3 = date(YEAR, 9, 3)
SEP_30 = date(YEAR, 9, 30)
OCT_3 = date(YEAR, 10, 3)


def _statement_line(db: Session, banking: Banking, account: str = "BK-RWF"):  # noqa: ANN202
    return db.scalars(
        select(BankStatementLine)
        .where(BankStatementLine.bank_account_id == banking.bank(account).id)
        .order_by(BankStatementLine.id)
    ).first()


def _a_matched_account(db: Session, banking: Banking) -> tuple[BankMatch, int, int]:
    """A balanced match on `BK-RWF`: one statement credit, one ledger line, 1 000 each."""
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=SEP_3)
    line = bank_line_of(db, banking, entry, "1120")
    key_statement(db, banking, lines=[(SEP_3, "A DEPOSIT", Decimal(1000))])
    db.commit()
    statement_line = _statement_line(db, banking)
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
    assert_bank_invariants(db, banking.company_id)
    return match, statement_line.id, line.id


# --- 1: members are unique, and on one account ----------------------------------------------------


def test_clause_1_catches_a_member_belonging_to_another_bank_account(
    db: Session, banking: Banking
) -> None:
    """Nothing in the database stops a match on one account holding a line of another: the
    member tables' FKs reach `(company_id, id)`, not the account. The service refuses it
    (`match_across_accounts`) and this clause is what notices if a path ever does not."""
    match, statement_line_id, _ = _a_matched_account(db, banking)
    usd_entry = cashbook(
        db, banking, account_code="1121", amount=Decimal("5.00"), on=SEP_3, currency="USD"
    )
    db.commit()
    usd_line = bank_line_of(db, banking, usd_entry, "1121")

    db.execute(
        text(
            "INSERT INTO bank_match_journal_lines (company_id, match_id, journal_line_id) "
            "VALUES (:cid, :match, :line)"
        ),
        {"cid": banking.company_id, "match": match.id, "line": usd_line.id},
    )
    db.flush()

    with pytest.raises(AssertionError, match="it is a match on the bank account"):
        assert_bank_invariants(db, banking.company_id)
    db.rollback()


def test_clause_1_catches_a_match_with_no_members(db: Session, banking: Banking) -> None:
    """A match asserting nothing is not a match, and it would sit in every listing as one."""
    match, _, _ = _a_matched_account(db, banking)

    db.execute(
        text("DELETE FROM bank_match_statement_lines WHERE match_id = :match"),
        {"match": match.id},
    )
    db.execute(
        text("DELETE FROM bank_match_journal_lines WHERE match_id = :match"),
        {"match": match.id},
    )
    db.flush()

    with pytest.raises(AssertionError, match="has no members"):
        assert_bank_invariants(db, banking.company_id)
    db.rollback()


# --- 2: a match with statement members balances ---------------------------------------------------


def test_clause_2_catches_a_match_that_does_not_balance(
    db: Session, banking: Banking
) -> None:
    """The service refuses this with `match_unbalanced` before writing anything, so the only
    way to the state is underneath it — which is exactly the path a future caller that forgot
    the check would take."""
    match, statement_line_id, _ = _a_matched_account(db, banking)
    second = cashbook(db, banking, account_code="1120", amount=Decimal(250), on=SEP_3)
    db.commit()
    stray = bank_line_of(db, banking, second, "1120")

    db.execute(
        text(
            "INSERT INTO bank_match_journal_lines (company_id, match_id, journal_line_id) "
            "VALUES (:cid, :match, :line)"
        ),
        {"cid": banking.company_id, "match": match.id, "line": stray.id},
    )
    db.flush()

    with pytest.raises(AssertionError, match="does not balance"):
        assert_bank_invariants(db, banking.company_id)
    db.rollback()


# --- 3: an assigned match was effective at its reconciliation's date ------------------------------


def test_clause_3_catches_a_match_assigned_to_a_reconciliation_it_postdates(
    db: Session, banking: Banking
) -> None:
    """A match whose statement line the bank booked in October cannot belong to September's
    reconciliation: it would put money in the figures that the bank had not shown."""
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=SEP_3)
    line = bank_line_of(db, banking, entry, "1120")
    key_statement(db, banking, lines=[(OCT_3, "LATE DEPOSIT", Decimal(1000))])
    db.commit()
    late_match = matching.create_match(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        statement_line_ids=[_statement_line(db, banking).id],
        journal_line_ids=[line.id],
        kind=BankMatchKind.MANUAL,
        rule=BankMatchRule.MANUAL,
        actor=banking.owner,
    )
    reconciliation = reconciliation_service.open_reconciliation(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        reconciliation_date=SEP_30,
        statement_balance=Decimal(0),
        actor=banking.owner,
    )
    db.commit()
    reconciliation_service.lock(
        db, banking.company_id, reconciliation.id, actor=banking.owner
    )
    db.commit()
    assert late_match.reconciliation_id is None  # it was not effective, so it was not assigned

    db.execute(
        text("UPDATE bank_matches SET reconciliation_id = :rec WHERE id = :match"),
        {"rec": reconciliation.id, "match": late_match.id},
    )
    db.flush()

    with pytest.raises(AssertionError, match="but its latest statement member is dated"):
        assert_bank_invariants(db, banking.company_id)
    db.rollback()


# --- 4: a locked reconciliation's figures reproduce -----------------------------------------------


def _a_locked_reconciliation(db: Session, banking: Banking):  # noqa: ANN202
    """A reconciliation locked at zero on `BK-RWF`, whatever the account already holds.

    The statement balance is read off the live figures rather than hard-coded, because two of
    the callers below run this *after* `_a_matched_account` has already put money on the
    account — and a helper that assumed an empty one would fail on arithmetic rather than on
    the clause it is there to exercise.
    """
    entry = cashbook(db, banking, account_code="1120", amount=Decimal(1000), on=SEP_3)
    db.commit()
    matching.tick(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        journal_line_ids=[bank_line_of(db, banking, entry, "1120").id],
        actor=banking.owner,
    )
    db.flush()
    live = reconciliation_service.figures(
        db,
        banking.company_id,
        banking.bank("BK-RWF"),
        reconciliation_date=SEP_30,
        statement_balance=Decimal(0),
    )
    reconciliation = reconciliation_service.open_reconciliation(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        reconciliation_date=SEP_30,
        statement_balance=live.ledger_balance - live.outstanding_total,
        actor=banking.owner,
    )
    db.commit()
    reconciliation_service.lock(
        db, banking.company_id, reconciliation.id, actor=banking.owner
    )
    db.commit()
    assert_bank_invariants(db, banking.company_id)
    return reconciliation


def test_clause_4_catches_a_stored_ledger_balance_that_does_not_reproduce(
    db: Session, banking: Banking
) -> None:
    """The clause the phase turns on. A figure that cannot be recomputed from the lines that
    existed at the lock is a figure nobody can defend to an auditor."""
    reconciliation = _a_locked_reconciliation(db, banking)

    db.execute(
        text("UPDATE bank_reconciliations SET ledger_balance = ledger_balance + 1 WHERE id = :id"),
        {"id": reconciliation.id},
    )
    db.flush()
    db.expire(reconciliation)

    with pytest.raises(AssertionError, match="stored a ledger balance"):
        assert_bank_invariants(db, banking.company_id)
    db.rollback()


def test_clause_4_catches_a_locked_reconciliation_with_no_high_water_mark(
    db: Session, banking: Banking
) -> None:
    """Without the mark nothing can say which lines the reconciliation was struck over, so
    every later figure is unprovable rather than merely wrong."""
    reconciliation = _a_locked_reconciliation(db, banking)

    db.execute(
        text("UPDATE bank_reconciliations SET high_water_line_id = NULL WHERE id = :id"),
        {"id": reconciliation.id},
    )
    db.flush()
    db.expire(reconciliation)

    with pytest.raises(AssertionError, match="locked without a high-water mark"):
        assert_bank_invariants(db, banking.company_id)
    db.rollback()


def test_clause_4_catches_a_snapshot_that_does_not_foot(
    db: Session, banking: Banking
) -> None:
    """The stored outstanding items are what the report prints as "what this reconciliation
    said"; a snapshot that does not add up to its own total is a printed page that contradicts
    its own footer."""
    reconciliation = _a_locked_reconciliation(db, banking)

    db.execute(
        text(
            "UPDATE bank_reconciliations SET outstanding_snapshot = "
            "'[{\"journal_line_id\": 1, \"entry_number\": \"CB-000001\", "
            "\"entry_date\": \"2026-09-03\", \"amount\": \"5\"}]'::jsonb WHERE id = :id"
        ),
        {"id": reconciliation.id},
    )
    db.flush()
    db.expire(reconciliation)

    with pytest.raises(AssertionError, match="outstanding items and reproduces|does not foot"):
        assert_bank_invariants(db, banking.company_id)
    db.rollback()


# --- 5: a foreign-currency account holds only its own currency ------------------------------------


def test_clause_5_catches_a_wrong_currency_line_with_the_trigger_dropped(
    db: Session, banking: Banking, admin_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decision 2's rule is enforced **twice** — the engine for the field error, `VN012` for the
    guarantee — so reaching this clause means putting both levers down. That is the honest
    question it answers: *if a migration replaced the trigger and a refactor moved the engine
    check, would anything notice the state they were both preventing?*
    """
    from app.kernel import posting

    monkeypatch.setattr(posting, "_check_bank_account_currency", lambda *args, **kwargs: None)
    with admin_engine.begin() as conn:
        conn.execute(text("DROP TRIGGER trg_journal_lines_needs_bank_currency ON journal_lines"))
    try:
        cashbook(db, banking, account_code="1121", amount=Decimal(1000), on=SEP_3)
        db.commit()

        with pytest.raises(AssertionError, match="carries lines in another"):
            assert_bank_invariants(db, banking.company_id)
    finally:
        db.rollback()
        with admin_engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TRIGGER trg_journal_lines_needs_bank_currency "
                    "BEFORE INSERT ON journal_lines FOR EACH ROW "
                    "EXECUTE FUNCTION kernel_check_bank_account_currency()"
                )
            )


# --- 6: every flagged account has exactly one row -------------------------------------------------


def test_clause_6_catches_a_flagged_account_with_no_master_row(
    db: Session, banking: Banking
) -> None:
    """The clause that catches a path which created a bank/cash control account without calling
    `ensure_row` — the hook is one unconditional line at each call site so it is hard to
    forget, and this is what notices when somebody does anyway."""
    db.execute(
        text("DELETE FROM bank_accounts WHERE id = :id"),
        {"id": banking.bank("CASH").id},
    )
    db.flush()

    with pytest.raises(AssertionError, match="with 0 bank_accounts rows"):
        assert_bank_invariants(db, banking.company_id)
    db.rollback()


def test_clause_6_catches_a_kind_that_disagrees_with_the_control_type(
    db: Session, banking: Banking
) -> None:
    """`kind` is a copy of the control type, and the difference decides behaviour — a `cash`
    master over a `bank` account would have no statements and no reconciliation."""
    db.execute(
        text("UPDATE bank_accounts SET kind = 'cash' WHERE id = :id"),
        {"id": banking.bank("BK-RWF").id},
    )
    db.flush()
    # The suite re-queries, but the session's identity map still holds the row as it was; a raw
    # UPDATE does not reach it. Expiring is what makes the test about the clause rather than
    # about SQLAlchemy's cache.
    db.expire_all()

    with pytest.raises(AssertionError, match="master over a"):
        assert_bank_invariants(db, banking.company_id)
    db.rollback()


# --- 7: the cache equals the latest locked row ----------------------------------------------------


def test_clause_7_catches_a_stale_last_reconciled_balance(
    db: Session, banking: Banking
) -> None:
    """The one cache in the phase. It is allowed at all only because it caches a *stored row*
    and something recomputes it — this is that something."""
    _a_locked_reconciliation(db, banking)

    db.execute(
        text(
            "UPDATE bank_accounts SET last_reconciled_balance = last_reconciled_balance + 1 "
            "WHERE id = :id"
        ),
        {"id": banking.bank("BK-RWF").id},
    )
    db.flush()
    db.expire_all()

    with pytest.raises(AssertionError, match="caches a balance of"):
        assert_bank_invariants(db, banking.company_id)
    db.rollback()


def test_clause_7_catches_a_cache_on_an_account_with_no_locked_reconciliation(
    db: Session, banking: Banking
) -> None:
    db.execute(
        text(
            "UPDATE bank_accounts SET last_reconciled_at = DATE '2026-09-30', "
            "last_reconciled_balance = 1 WHERE id = :id"
        ),
        {"id": banking.bank("BK-RWF").id},
    )
    db.flush()
    db.expire_all()

    with pytest.raises(AssertionError, match="but has no locked one"):
        assert_bank_invariants(db, banking.company_id)
    db.rollback()


# --- 8: a run equals its members ------------------------------------------------------------------


def _a_posted_run(db: Session, banking: Banking):  # noqa: ANN202
    """One supplier, one invoice, one run — the smallest thing clause 8 has an opinion about."""
    from app.banking import payment_runs as payment_run_service

    cashbook(db, banking, account_code="1120", amount=Decimal(100000), on=SEP_3)
    supplier = ap_supplier(db, banking, name="Sensitivity Supplies", code="SENS")
    invoice = ap_invoice(db, banking, supplier, amount=Decimal(40000), on=SEP_3)
    db.commit()
    run = payment_run_service.post_run(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        payment_date=SEP_3,
        lines=[payment_run_service.RunLineInput(document_id=invoice.id)],
        actor=banking.owner,
    )
    db.commit()
    return run


def test_clause_8_catches_a_run_whose_total_is_not_its_members(
    db: Session, banking: Banking
) -> None:
    """The run row says 40 000 and the settlement says 40 000. Move one of them by SQL and the
    clause is the only thing that notices — the ledger is still balanced and the open items are
    still right, because a run's `total` is a claim *about* documents rather than a posting."""
    run = _a_posted_run(db, banking)

    db.execute(
        text("UPDATE payment_runs SET total = total + 1 WHERE id = :id"), {"id": run.id}
    )
    db.flush()
    db.expire_all()

    with pytest.raises(AssertionError, match="but its settlements total"):
        assert_bank_invariants(db, banking.company_id)
    db.rollback()


def test_clause_8_catches_a_member_that_lost_the_runs_reference(
    db: Session, banking: Banking
) -> None:
    """The reference is what decision 4's `payment_run` rule matches the bank's single line
    on. A member that lost it is a payment the reconciliation can never find, and nothing else
    in the suite would see it."""
    run = _a_posted_run(db, banking)
    settlement_id = payment_run_line_settlement(db, run.id)

    db.execute(
        text("UPDATE partner_documents SET reference = 'PYR-000999' WHERE id = :id"),
        {"id": settlement_id},
    )
    db.flush()
    db.expire_all()

    with pytest.raises(AssertionError, match="the bank's line would never find it"):
        assert_bank_invariants(db, banking.company_id)
    db.rollback()


def test_clause_8_catches_a_member_settled_through_another_account(
    db: Session, banking: Banking
) -> None:
    """A settlement that left a different bank account is a different banking act, whatever
    the run row says about it."""
    run = _a_posted_run(db, banking)
    settlement_id = payment_run_line_settlement(db, run.id)

    db.execute(
        text("UPDATE partner_documents SET cash_account_id = :account WHERE id = :id"),
        {"account": banking.ledger.acct("1110"), "id": settlement_id},
    )
    db.flush()
    db.expire_all()

    with pytest.raises(AssertionError, match="settled through account"):
        assert_bank_invariants(db, banking.company_id)
    db.rollback()


def test_clause_8_catches_a_reversed_run_with_a_member_still_standing(
    db: Session, banking: Banking
) -> None:
    """**The half-undone reversal.** `reverse_run` unallocates N allocations and reverses N
    settlements; this is the state a failure in the middle of that would leave, and the clause
    is what makes it visible between the legs rather than at the end."""
    run = _a_posted_run(db, banking)

    db.execute(
        text("UPDATE payment_runs SET status = 'reversed' WHERE id = :id"), {"id": run.id}
    )
    db.flush()
    db.expire_all()

    with pytest.raises(AssertionError, match="is reversed but"):
        assert_bank_invariants(db, banking.company_id)
    db.rollback()


def payment_run_line_settlement(db: Session, run_id: int) -> int:
    from app.models.banking import PaymentRunLine

    return db.scalars(
        select(PaymentRunLine.settlement_document_id).where(PaymentRunLine.run_id == run_id)
    ).one()


# --- 9: a void statement's lines are void, and a live one's are not -------------------------------


def test_clause_9_catches_a_live_statements_line_flagged_void(
    db: Session, banking: Banking
) -> None:
    """**The direction that matters, and the one nothing else protects.**

    `VN013` makes a statement line immutable with exactly one exemption — `is_void` — because a
    voided line has to leave the fingerprint uniqueness scope and cannot be deleted. That
    exemption is a hole by design, and this clause is the only thing standing in it. A line
    wrongly flagged void leaves every listing *and* stops blocking a re-import, so the same
    movement is imported twice and the reconciliation is out by exactly one line nobody can
    find.
    """
    key_statement(db, banking, lines=[(SEP_3, "A DEPOSIT", Decimal(1000))])
    db.commit()
    line = _statement_line(db, banking)
    assert_bank_invariants(db, banking.company_id)

    # The trigger permits this: it is the one column it exempts.
    db.execute(
        text("UPDATE bank_statement_lines SET is_void = true WHERE id = :id"),
        {"id": line.id},
    )
    db.flush()

    with pytest.raises(AssertionError, match="the flag is a copy of the status and has drifted"):
        assert_bank_invariants(db, banking.company_id)
    db.rollback()


def test_clause_9_catches_a_void_statements_line_left_live(
    db: Session, banking: Banking
) -> None:
    """The other direction. A line left un-flagged on a voided statement keeps blocking the
    re-import of the file it came from — which is the whole operation void exists to enable."""
    from app.banking import statements as statements_service

    result = key_statement(db, banking, lines=[(SEP_3, "A DEPOSIT", Decimal(1000))])
    db.commit()
    statements_service.void(db, banking.company_id, result.statement.id, actor=banking.owner)
    db.commit()
    assert_bank_invariants(db, banking.company_id)

    db.execute(
        text("UPDATE bank_statement_lines SET is_void = false WHERE statement_id = :id"),
        {"id": result.statement.id},
    )
    db.flush()

    with pytest.raises(AssertionError, match="the flag is a copy of the status and has drifted"):
        assert_bank_invariants(db, banking.company_id)
    db.rollback()


def test_clause_9_catches_a_matched_line_on_a_void_statement(
    db: Session, banking: Banking
) -> None:
    """Void is refused while any of the statement's lines is matched. This is the clause that
    notices if that refusal is ever bypassed — a locked reconciliation could otherwise be
    proving itself against a statement somebody has withdrawn."""
    match, statement_line_id, _ = _a_matched_account(db, banking)
    statement_id = db.scalar(
        select(BankStatementLine.statement_id).where(
            BankStatementLine.id == statement_line_id
        )
    )

    db.execute(
        text("UPDATE bank_statements SET status = 'void' WHERE id = :id"),
        {"id": statement_id},
    )
    db.execute(
        text("UPDATE bank_statement_lines SET is_void = true WHERE statement_id = :id"),
        {"id": statement_id},
    )
    db.flush()

    with pytest.raises(AssertionError, match="void statements whose lines are still matched"):
        assert_bank_invariants(db, banking.company_id)
    db.rollback()


# --- Anti-vacuity ---------------------------------------------------------------------------------


def test_the_suite_passes_on_a_healthy_tenant(db: Session, banking: Banking) -> None:
    """The control. Every test above asserts the suite *fails*; without this one they would all
    still pass over a suite that failed unconditionally."""
    _a_matched_account(db, banking)
    _a_locked_reconciliation(db, banking)

    assert_bank_invariants(db, banking.company_id)


def test_every_clause_is_reached_by_this_file() -> None:
    """The register of what is proven. A clause added without a sensitivity test is a clause
    nobody has shown can fail, and this is what makes that visible in the diff that adds it."""
    from tests.banking import invariants

    body = open(invariants.__file__, encoding="utf-8").read()  # noqa: SIM115, PTH123
    declared = {
        line.split("def _clause_")[1].split("_")[0]
        for line in body.splitlines()
        if line.startswith("def _clause_")
    }

    assert declared == {"1", "2", "3", "4", "5", "6", "7", "8", "9"}, (
        f"clauses in the suite: {sorted(declared)}. Every one needs a sensitivity test in this "
        "file — a clause added without one is a clause nobody has shown can fail."
    )
