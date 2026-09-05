"""The database is the authority: these tests bypass the Posting Engine entirely and hit
the tables with raw SQL as the (RLS-bound, non-superuser) app role."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.db import set_tenant
from app.kernel.errors import (
    SQLSTATE_IMMUTABLE,
    SQLSTATE_NOT_POSTABLE,
    SQLSTATE_PERIOD_NOT_OPEN,
    SQLSTATE_TOO_FEW_LINES,
    SQLSTATE_UNBALANCED,
    kernel_sqlstate,
)
from app.models.fiscal import PeriodStatus
from tests.conftest import make_tenant
from tests.kernel.conftest import YEAR, Ledger, post_simple

MARCH = date(YEAR, 3, 15)

INSERT_ENTRY = text(
    """
    INSERT INTO journal_entries (company_id, number, doc_type, event_type, entry_date,
                                 period_id, description, status)
    VALUES (:cid, :number, 'JE', 'manual_journal', :on, :period, 'raw', :status)
    RETURNING id
    """
)
INSERT_LINE = text(
    """
    INSERT INTO journal_lines (company_id, entry_id, line_no, gl_account_id, branch_id,
                               currency_id, exchange_rate, amount, base_amount, tax_amount)
    VALUES (:cid, :entry, :line_no, :account, :branch, :currency, 1, :amount, :amount, 0)
    """
)


def _period_id(ledger: Ledger, on: date) -> int:
    return next(p.id for p in ledger.periods if p.start_date <= on <= p.end_date)


def _as_engine(db: Session) -> None:
    """Impersonate the Posting Engine for the current transaction. These tests target the
    invariant triggers, so they have to get past the single-writer guard first (0004)."""
    db.execute(text("SELECT set_config('app.posting_engine', 'on', true)"))


def _insert_entry(
    db: Session,
    ledger: Ledger,
    *,
    on: date,
    number: str,
    status: str = "draft",
    period_id: int | None = None,
) -> int:
    _as_engine(db)
    return db.execute(
        INSERT_ENTRY,
        {
            "cid": ledger.company_id,
            "number": number,
            "on": on,
            "period": period_id if period_id is not None else _period_id(ledger, on),
            "status": status,
        },
    ).scalar_one()


def _insert_line(
    db: Session, ledger: Ledger, entry_id: int, line_no: int, account_id: int, amount: Decimal
) -> None:
    _as_engine(db)
    db.execute(
        INSERT_LINE,
        {
            "cid": ledger.company_id,
            "entry": entry_id,
            "line_no": line_no,
            "account": account_id,
            "branch": ledger.main_branch.id,
            "currency": ledger.base.id,
            "amount": amount,
        },
    )


def _raw_entry(db: Session, ledger: Ledger, *, on: date, lines: list[tuple[str, Decimal]]) -> int:
    """Insert header (draft) + lines, flip to posted, and force the deferred checks."""
    entry_id = _insert_entry(db, ledger, on=on, number=f"RAW-{on.isoformat()}-{len(lines)}")
    for line_no, (code, amount) in enumerate(lines, 1):
        _insert_line(db, ledger, entry_id, line_no, ledger.acct(code), amount)
    db.execute(
        text("UPDATE journal_entries SET status = 'posted' WHERE id = :id"), {"id": entry_id}
    )
    db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    return entry_id


def _sqlstate(excinfo: pytest.ExceptionInfo[DBAPIError]) -> str | None:
    return kernel_sqlstate(excinfo.value)


# --- (a) immutability ------------------------------------------------------------------


def test_direct_sql_update_of_a_posted_line_is_blocked(db: Session, ledger: Ledger) -> None:
    entry = post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(1000), on=MARCH)
    db.commit()

    with pytest.raises(DBAPIError) as excinfo:
        db.execute(
            text("UPDATE journal_lines SET amount = amount + 1 WHERE entry_id = :id"),
            {"id": entry.id},
        )
    assert _sqlstate(excinfo) == SQLSTATE_IMMUTABLE
    db.rollback()


def test_direct_sql_update_of_a_posted_header_is_blocked(db: Session, ledger: Ledger) -> None:
    entry = post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(1000), on=MARCH)
    db.commit()

    for statement in (
        "UPDATE journal_entries SET description = 'tampered' WHERE id = :id",
        "UPDATE journal_entries SET status = 'draft' WHERE id = :id",
        "UPDATE journal_entries SET entry_date = entry_date - 1 WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as excinfo:
            db.execute(text(statement), {"id": entry.id})
        assert _sqlstate(excinfo) == SQLSTATE_IMMUTABLE
        db.rollback()


def test_delete_of_posted_rows_is_blocked(db: Session, ledger: Ledger) -> None:
    entry = post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(1000), on=MARCH)
    db.commit()

    with pytest.raises(DBAPIError) as excinfo:
        db.execute(text("DELETE FROM journal_lines WHERE entry_id = :id"), {"id": entry.id})
    assert _sqlstate(excinfo) == SQLSTATE_IMMUTABLE
    db.rollback()
    with pytest.raises(DBAPIError) as excinfo:
        db.execute(text("DELETE FROM journal_entries WHERE id = :id"), {"id": entry.id})
    assert _sqlstate(excinfo) == SQLSTATE_IMMUTABLE
    db.rollback()


def test_appending_a_line_to_a_posted_entry_is_blocked(db: Session, ledger: Ledger) -> None:
    """Even a balanced pair cannot be smuggled onto an already-posted entry."""
    entry = post_simple(db, ledger, debit="6500", credit="2300", amount=Decimal(1000), on=MARCH)
    db.commit()

    with pytest.raises(DBAPIError) as excinfo:
        _insert_line(db, ledger, entry.id, 99, ledger.acct("6500"), Decimal(5))
    assert _sqlstate(excinfo) == SQLSTATE_IMMUTABLE
    db.rollback()


# --- (b) balanced at commit --------------------------------------------------------------


def test_unbalanced_entry_is_rejected_by_the_deferred_trigger(db: Session, ledger: Ledger) -> None:
    with pytest.raises(DBAPIError) as excinfo:
        _raw_entry(db, ledger, on=MARCH, lines=[("6500", Decimal(100)), ("2300", Decimal(-99))])
    assert _sqlstate(excinfo) == SQLSTATE_UNBALANCED
    db.rollback()


def test_unbalanced_entry_is_rejected_at_commit_too(db: Session, ledger: Ledger) -> None:
    """Without forcing the constraints, the check still fires when the transaction commits."""
    entry_id = _insert_entry(db, ledger, on=MARCH, number="RAW-COMMIT")
    _insert_line(db, ledger, entry_id, 1, ledger.acct("6500"), Decimal(100))
    _insert_line(db, ledger, entry_id, 2, ledger.acct("2300"), Decimal(-50))
    with pytest.raises(DBAPIError) as excinfo:
        db.commit()
    assert _sqlstate(excinfo) == SQLSTATE_UNBALANCED
    db.rollback()


def test_balanced_raw_entry_is_accepted(db: Session, ledger: Ledger) -> None:
    _raw_entry(db, ledger, on=MARCH, lines=[("6500", Decimal(100)), ("2300", Decimal(-100))])
    db.commit()


# --- (c) postable accounts ---------------------------------------------------------------


def test_posting_to_a_header_account_is_rejected(db: Session, ledger: Ledger) -> None:
    with pytest.raises(DBAPIError) as excinfo:
        _raw_entry(db, ledger, on=MARCH, lines=[("6000", Decimal(100)), ("2300", Decimal(-100))])
    assert _sqlstate(excinfo) == SQLSTATE_NOT_POSTABLE
    db.rollback()


def test_posting_to_an_inactive_account_is_rejected(db: Session, ledger: Ledger) -> None:
    db.execute(
        text("UPDATE gl_accounts SET is_active = false WHERE id = :id"),
        {"id": ledger.acct("6500")},
    )
    with pytest.raises(DBAPIError) as excinfo:
        _raw_entry(db, ledger, on=MARCH, lines=[("6500", Decimal(100)), ("2300", Decimal(-100))])
    assert _sqlstate(excinfo) == SQLSTATE_NOT_POSTABLE
    db.rollback()


# --- (d) open period ---------------------------------------------------------------------


def test_backdating_into_a_closed_period_is_rejected_by_the_db(db: Session, ledger: Ledger) -> None:
    db.execute(
        text("UPDATE accounting_periods SET status = :status WHERE id = :id"),
        {"status": PeriodStatus.CLOSED.value, "id": _period_id(ledger, MARCH)},
    )
    with pytest.raises(DBAPIError) as excinfo:
        _raw_entry(db, ledger, on=MARCH, lines=[("6500", Decimal(100)), ("2300", Decimal(-100))])
    assert _sqlstate(excinfo) == SQLSTATE_PERIOD_NOT_OPEN
    db.rollback()


def test_entry_date_must_lie_inside_its_period(db: Session, ledger: Ledger) -> None:
    with pytest.raises(DBAPIError) as excinfo:
        _insert_entry(
            db,
            ledger,
            on=date(YEAR, 4, 1),
            number="RAW-X",
            status="posted",
            period_id=_period_id(ledger, MARCH),
        )
    assert _sqlstate(excinfo) == SQLSTATE_PERIOD_NOT_OPEN
    db.rollback()


def test_posted_entry_needs_two_lines(db: Session, ledger: Ledger) -> None:
    with pytest.raises(DBAPIError) as excinfo:
        _raw_entry(db, ledger, on=MARCH, lines=[])
    assert _sqlstate(excinfo) == SQLSTATE_TOO_FEW_LINES
    db.rollback()


# --- tenant consistency via composite FKs -------------------------------------------------


def test_a_line_cannot_reference_another_tenants_account(db: Session, ledger: Ledger) -> None:
    """FK checks bypass RLS, so tenant consistency needs the composite FK. Run in platform
    mode so RLS itself does not hide the foreign account first."""
    other = make_tenant(db, company_name="Musanze Supplies Ltd", email="owner@musanze.example")
    set_tenant(db, other.company.id)
    foreign_account = db.execute(
        text("SELECT id FROM gl_accounts WHERE code = '6500' AND company_id = :cid"),
        {"cid": other.company.id},
    ).scalar_one()
    set_tenant(db, ledger.company_id)

    entry_id = _insert_entry(db, ledger, on=MARCH, number="RAW-FK")
    # Local GUC only (dies with the rollback) — platform_scope() would try to reset it inside
    # the aborted transaction.
    db.execute(text("SELECT set_config('app.platform_mode', 'on', true)"))
    with pytest.raises(IntegrityError) as excinfo:
        _insert_line(db, ledger, entry_id, 1, foreign_account, Decimal(5))
    assert "fk_journal_lines_gl_account" in str(excinfo.value.orig)
    db.rollback()
