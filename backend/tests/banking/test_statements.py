"""Statement import: preview, dedup, the file-hash refusal, void and the immutability trigger.

The three committed samples are the ones the acceptance tape imports at step 5, so the counts
here are the counts that tape expects — `generic-bk-rwf-sep.csv` is six lines opening at
1 000 000 and closing at 1 090 500, and `generic-bk-rwf-overlap-oct.csv` is two new and one
skipped against it. Asserting them now means step 5's literals are already known to be
readable off these files rather than hoped for.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.banking import statements as statements_service
from app.core.errors import ConflictError
from app.kernel.errors import LedgerStateError
from app.models.banking import (
    BankMatch,
    BankMatchKind,
    BankMatchRule,
    BankMatchStatementLine,
    BankStatementLine,
    StatementSource,
    StatementStatus,
)
from tests.banking.conftest import Banking, sample

SEPTEMBER = "generic-bk-rwf-sep.csv"
OCTOBER = "generic-bk-rwf-overlap-oct.csv"


def _import(db: Session, banking: Banking, name: str, *, account: str = "BK-RWF", **kwargs):  # noqa: ANN202
    return statements_service.import_statement(
        db,
        banking.company_id,
        bank_account_id=banking.bank(account).id,
        content=sample(name),
        file_name=name,
        actor=banking.owner,
        **kwargs,
    )


# --- Preview ------------------------------------------------------------------------------


def test_the_preview_writes_nothing_and_reports_what_it_found(
    db: Session, banking: Banking
) -> None:
    preview = statements_service.preview(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        content=sample(SEPTEMBER),
        file_name=SEPTEMBER,
    )

    assert (preview.line_count, preview.new_count, preview.skipped_count) == (6, 6, 0)
    assert (preview.opening_balance, preview.closing_balance) == (
        Decimal("1000000"),
        Decimal("1090500"),
    )
    assert (preview.from_date, preview.to_date) == (date(2026, 9, 3), date(2026, 9, 20))
    assert preview.errors == []
    assert preview.duplicate_file is False
    assert db.scalar(select(BankStatementLine.id)) is None


def test_the_preview_counts_what_the_account_already_holds(
    db: Session, banking: Banking
) -> None:
    _import(db, banking, SEPTEMBER)
    db.commit()

    preview = statements_service.preview(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        content=sample(OCTOBER),
        file_name=OCTOBER,
    )

    assert (preview.line_count, preview.new_count, preview.skipped_count) == (3, 2, 1)
    assert [line.already_held for line in preview.lines] == [True, False, False]


def test_a_mapping_that_does_not_fit_shows_every_error_with_its_row(
    db: Session, banking: Banking
) -> None:
    """The preview is where a user finds out their mapping is wrong, and it has to show all of
    it — `import` refuses the file outright, which is no help in fixing the mapping."""
    preview = statements_service.preview(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        content=sample(SEPTEMBER),
        override_format={"preset": "custom", "date_format": "%d/%m/%Y"},
    )

    assert len(preview.errors) == 6
    assert [error.row for error in preview.errors] == [2, 3, 4, 5, 6, 7]


# --- Import -------------------------------------------------------------------------------


def test_an_import_stores_the_lines_with_the_mapping_it_read_them_with(
    db: Session, banking: Banking
) -> None:
    result = _import(db, banking, SEPTEMBER)
    db.commit()

    statement = result.statement
    assert statement.number == "BST-000001"
    assert (statement.line_count, statement.lines_skipped) == (6, 0)
    assert (result.new_count, result.skipped_count) == (6, 0)
    assert statement.source == StatementSource.CSV
    assert statement.status == StatementStatus.OPEN
    # The mapping as it was when this file was read — a later edit to the account's format
    # must not change what a stored statement meant.
    assert statement.format_snapshot["preset"] == "generic"
    assert statement.format_snapshot["date_format"] == "%Y-%m-%d"

    lines = statements_service.lines_of(db, banking.company_id, statement.id)
    assert [line.line_no for line in lines] == [1, 2, 3, 4, 5, 6]
    assert [line.amount for line in lines] == [
        Decimal("118000"),
        Decimal("59000"),
        Decimal("-384000"),
        Decimal("-2500"),
        Decimal("260000"),
        Decimal("40000"),
    ]
    assert all(line.bank_account_id == banking.bank("BK-RWF").id for line in lines)


def test_the_same_file_twice_is_refused(db: Session, banking: Banking) -> None:
    _import(db, banking, SEPTEMBER)
    db.commit()

    with pytest.raises(ConflictError) as excinfo:
        _import(db, banking, SEPTEMBER)

    assert excinfo.value.code == "statement_already_imported"
    assert "BST-000001" in excinfo.value.message
    db.rollback()


def test_an_overlapping_export_is_two_new_and_one_skipped(
    db: Session, banking: Banking
) -> None:
    """The normal case, not an error. October's export repeats 20 September and adds two days."""
    _import(db, banking, SEPTEMBER)
    db.commit()

    result = _import(db, banking, OCTOBER)
    db.commit()

    assert (result.new_count, result.skipped_count) == (2, 1)
    assert (result.statement.line_count, result.statement.lines_skipped) == (2, 1)
    # The statement still covers the weeks the bank exported, including the day it repeated.
    assert (result.statement.from_date, result.statement.to_date) == (
        date(2026, 9, 20),
        date(2026, 10, 4),
    )
    stored = db.scalars(
        select(BankStatementLine).where(
            BankStatementLine.statement_id == result.statement.id
        ).order_by(BankStatementLine.line_no)
    ).all()
    assert [line.description for line in stored] == ["CHQ 101", "CHQ 102"]


def test_a_cash_account_has_no_statement_to_import(db: Session, banking: Banking) -> None:
    """Cash reconciles against nothing here — a till count is P11's. `CASH` appears in the
    Cashbooks report and it revalues, and that is all this phase does with it."""
    with pytest.raises(LedgerStateError) as excinfo:
        statements_service.import_statement(
            db,
            banking.company_id,
            bank_account_id=banking.bank("CASH").id,
            content=sample(SEPTEMBER),
            file_name=SEPTEMBER,
            actor=banking.owner,
        )

    assert excinfo.value.code == "statement_needs_bank"
    db.rollback()


def test_the_same_export_on_two_accounts_is_two_statements(db: Session, banking: Banking) -> None:
    """The fingerprint carries the bank account, so one layout landing on two accounts is two
    statements rather than a skip — which it has to be, because two accounts really can have
    had the same movement on the same day. The file hashes differ here too; the fingerprint is
    what carries the claim, and the assertion is that neither line is skipped.
    """
    _import(db, banking, SEPTEMBER)
    db.commit()

    second = statements_service.import_statement(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-USD").id,
        content=sample("generic-bk-usd-sep.csv"),
        file_name="generic-bk-usd-sep.csv",
        actor=banking.owner,
    )
    db.commit()

    assert (second.new_count, second.skipped_count) == (2, 0)
    assert second.statement.bank_account_id == banking.bank("BK-USD").id


def test_a_parse_error_imports_nothing(db: Session, banking: Banking) -> None:
    """Half a statement is not a statement. The refusal names the row, and the account is left
    holding exactly what it held before."""
    banking.bank("BK-RWF").statement_format = {"preset": "custom", "date_format": "%d/%m/%Y"}
    db.flush()

    with pytest.raises(statements_service.StatementParseError) as excinfo:
        _import(db, banking, SEPTEMBER)

    assert excinfo.value.code == "statement_parse_error"
    assert "Row 2" in excinfo.value.message
    assert "rows.2.date_column" in excinfo.value.field_errors
    db.rollback()
    assert db.scalar(select(BankStatementLine.id)) is None


def test_an_import_replays_under_its_idempotency_key(db: Session, banking: Banking) -> None:
    first = _import(db, banking, SEPTEMBER, idempotency_key="import-1")
    db.commit()

    again = _import(db, banking, SEPTEMBER, idempotency_key="import-1")

    assert again.replayed is True
    assert again.statement.id == first.statement.id
    assert db.scalar(select(text("count(*)")).select_from(BankStatementLine.__table__)) == 6


def test_a_manual_statement_takes_the_same_path(db: Session, banking: Banking) -> None:
    from app.banking.formats import ParsedLine

    lines = [
        ParsedLine(
            row=index,
            value_date=date(2026, 9, day),
            booking_date=None,
            description=description,
            reference=None,
            amount=amount,
            balance_after=None,
            external_id=None,
            occurrence=0,
        )
        for index, (day, description, amount) in enumerate(
            [(3, "DEPOSIT", Decimal(100)), (4, "FEE", Decimal(-10))], start=1
        )
    ]

    result = statements_service.import_manual(
        db,
        banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        lines=lines,
        opening_balance=Decimal(0),
        closing_balance=Decimal(90),
        actor=banking.owner,
    )
    db.commit()

    assert result.statement.source == StatementSource.MANUAL
    assert result.statement.file_sha256 is None
    assert (result.new_count, result.skipped_count) == (2, 0)


# --- Immutability and void ------------------------------------------------------------------


def test_a_stored_line_cannot_be_edited(db: Session, banking: Banking) -> None:
    """`VN013`. A statement is the bank's record of what happened; there is no legitimate edit
    to it, which is why the trigger has no exemption but the void flag."""
    result = _import(db, banking, SEPTEMBER)
    db.commit()
    line = statements_service.lines_of(db, banking.company_id, result.statement.id)[0]

    with pytest.raises(DBAPIError) as excinfo:
        db.execute(
            text("UPDATE bank_statement_lines SET amount = 1 WHERE id = :id"), {"id": line.id}
        )

    assert excinfo.value.orig.sqlstate == "VN013"
    db.rollback()


def test_a_stored_line_cannot_be_deleted(db: Session, banking: Banking) -> None:
    result = _import(db, banking, SEPTEMBER)
    db.commit()
    line = statements_service.lines_of(db, banking.company_id, result.statement.id)[0]

    with pytest.raises(DBAPIError) as excinfo:
        db.execute(text("DELETE FROM bank_statement_lines WHERE id = :id"), {"id": line.id})

    assert excinfo.value.orig.sqlstate == "VN013"
    db.rollback()


def test_voiding_takes_every_line_out_of_every_count(db: Session, banking: Banking) -> None:
    result = _import(db, banking, SEPTEMBER)
    db.commit()

    statements_service.void(
        db, banking.company_id, result.statement.id, reason="wrong account", actor=banking.owner
    )
    db.commit()

    assert result.statement.status == StatementStatus.VOID
    assert statements_service.list_statements(db, banking.company_id) == []
    assert all(
        line.is_void
        for line in statements_service.lines_of(db, banking.company_id, result.statement.id)
    )


def test_a_voided_file_can_be_imported_again(db: Session, banking: Banking) -> None:
    """The point of the whole void design: a mistaken import is taken back out and the same
    file goes in again. Neither the file hash nor the line fingerprints of a voided statement
    block anything."""
    first = _import(db, banking, SEPTEMBER)
    db.commit()
    statements_service.void(db, banking.company_id, first.statement.id, actor=banking.owner)
    db.commit()

    second = _import(db, banking, SEPTEMBER)
    db.commit()

    assert second.statement.number == "BST-000002"
    assert (second.new_count, second.skipped_count) == (6, 0)


def test_voiding_is_refused_while_a_line_is_matched(db: Session, banking: Banking) -> None:
    """Refused rather than cascading. A match may belong to a *locked* reconciliation, and
    deleting it silently would move a figure that is meant to be a permanent record."""
    result = _import(db, banking, SEPTEMBER)
    db.commit()
    line = statements_service.lines_of(db, banking.company_id, result.statement.id)[0]
    match = BankMatch(
        company_id=banking.company_id,
        bank_account_id=banking.bank("BK-RWF").id,
        kind=BankMatchKind.MANUAL,
        rule=BankMatchRule.MANUAL,
        matched_at=date.today(),
    )
    db.add(match)
    db.flush()
    db.add(
        BankMatchStatementLine(
            company_id=banking.company_id, match_id=match.id, statement_line_id=line.id
        )
    )
    db.commit()

    with pytest.raises(LedgerStateError) as excinfo:
        statements_service.void(db, banking.company_id, result.statement.id, actor=banking.owner)

    assert excinfo.value.code == "statement_has_matches"
    assert "1 matched line" in excinfo.value.message
    db.rollback()


def test_a_statement_cannot_be_voided_twice(db: Session, banking: Banking) -> None:
    result = _import(db, banking, SEPTEMBER)
    db.commit()
    statements_service.void(db, banking.company_id, result.statement.id, actor=banking.owner)
    db.commit()

    with pytest.raises(LedgerStateError) as excinfo:
        statements_service.void(db, banking.company_id, result.statement.id, actor=banking.owner)

    assert excinfo.value.code == "statement_already_void"
    db.rollback()


def test_a_format_with_no_balance_column_needs_the_two_balances_keyed(
    db: Session, banking: Banking
) -> None:
    """Refused rather than defaulted to zero. A reconciliation opened afterwards defaults its
    statement balance from the latest line's `balance_after`; a statement carrying a made-up
    zero would hand it a figure nobody keyed and nobody checked."""
    banking.bank("BK-RWF").statement_format = {
        "preset": "custom",
        "balance_column": None,
    }
    db.flush()

    with pytest.raises(LedgerStateError) as excinfo:
        _import(db, banking, SEPTEMBER)

    assert excinfo.value.code == "statement_balances_required"
    db.rollback()

    # Keyed, the same file imports.
    banking.bank("BK-RWF").statement_format = {"preset": "custom", "balance_column": None}
    db.flush()
    result = _import(
        db,
        banking,
        SEPTEMBER,
        opening_balance=Decimal("1000000"),
        closing_balance=Decimal("1090500"),
    )
    db.commit()
    assert (result.statement.opening_balance, result.statement.closing_balance) == (
        Decimal("1000000"),
        Decimal("1090500"),
    )


def test_a_file_with_no_lines_at_all_is_refused(db: Session, banking: Banking) -> None:
    with pytest.raises(LedgerStateError) as excinfo:
        statements_service.import_statement(
            db,
            banking.company_id,
            bank_account_id=banking.bank("BK-RWF").id,
            content=b"Date,Description,Reference,Debit,Credit,Balance\n",
            file_name="empty.csv",
            actor=banking.owner,
        )

    assert excinfo.value.code == "statement_empty"
    db.rollback()


def test_a_key_reused_for_a_different_file_is_refused(db: Session, banking: Banking) -> None:
    """The ADR-11 rule, on this endpoint: replaying a key returns the original import, but
    sending the *same* key with a different file is a client bug and is named as one rather
    than silently returning somebody else's statement."""
    _import(db, banking, SEPTEMBER, idempotency_key="import-1", idempotency_hash="hash-a")
    db.commit()

    with pytest.raises(LedgerStateError) as excinfo:
        _import(db, banking, OCTOBER, idempotency_key="import-1", idempotency_hash="hash-b")

    assert excinfo.value.code == "idempotency_key_reused"
    db.rollback()
