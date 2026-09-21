"""Schema-level guarantees the rest of the product will rely on (ADR-06, §4 sketch)."""

import os
import subprocess
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import engine, set_tenant
from app.models.currency import Currency
from app.models.tax import TaxCode

# The dev container mounts the repository read-only at /repo and names it here; outside it,
# the source tree is inside the checkout already. Either way the git-backed assertions below
# have a work tree, so they run in both places rather than only in CI.
REPO_ROOT = Path(os.environ.get("REPO_ROOT") or Path(__file__).resolve().parents[2])


def _columns(table: str) -> dict[str, object]:
    return {column["name"]: column for column in inspect(engine).get_columns(table)}


def test_currencies_carry_decimal_places() -> None:
    column = _columns("currencies")["decimal_places"]
    assert column["nullable"] is False


def test_rwf_is_zero_decimal_and_usd_is_two(db: Session, two_tenants) -> None:
    first, _ = two_tenants
    set_tenant(db, first.company.id)

    places = dict(db.execute(select(Currency.code, Currency.decimal_places)).all())

    assert places == {"RWF": 0, "USD": 2}


def test_only_one_base_currency_per_company(db: Session, two_tenants) -> None:
    first, _ = two_tenants
    set_tenant(db, first.company.id)

    db.add(
        Currency(
            company_id=first.company.id,
            code="EUR",
            name="Euro",
            symbol="€",
            decimal_places=2,
            is_base=True,
            is_active=True,
        )
    )
    with pytest.raises(IntegrityError) as excinfo:
        db.flush()

    assert "uq_currencies_company_base" in str(excinfo.value)
    db.rollback()


def test_each_company_keeps_its_own_base_currency(db: Session, two_tenants) -> None:
    for tenant in two_tenants:
        set_tenant(db, tenant.company.id)
        base = db.scalars(select(Currency).where(Currency.is_base)).one()
        assert base.code == "RWF"


def test_tax_codes_carry_nature_and_validity_window() -> None:
    columns = _columns("tax_codes")

    assert columns["nature"]["nullable"] is False
    assert columns["valid_from"]["nullable"] is False
    assert columns["valid_to"]["nullable"] is True


def test_seeded_tax_codes_are_open_ended_from_the_fiscal_year_start(
    db: Session, two_tenants
) -> None:
    first, _ = two_tenants
    set_tenant(db, first.company.id)

    codes = db.scalars(select(TaxCode)).all()

    assert {code.valid_from for code in codes} == {date(date.today().year, 1, 1)}
    assert {code.valid_to for code in codes} == {None}


def test_money_and_rate_precision_follow_adr_06() -> None:
    rate = _columns("tax_codes")["rate_pct"]["type"]
    assert (rate.precision, rate.scale) == (20, 10)


#: Columns whose name contains "balance" and which are **not** the thing ADR-04 forbids, with
#: the reason. What the rule forbids is a stored balance of a Vinea account or partner — a
#: figure postings would have to maintain, and which would then be capable of disagreeing with
#: the ledger. Every entry below is a figure Vinea does not derive at all, or a snapshot of a
#: proof that a test recomputes.
#:
#: A register rather than a loosened substring, and asserted by **equality** rather than by
#: subset: a new `balance` column fails this test, and so does deleting one of these without
#: deleting its line. Both cost a line of review, which is the point.
ALLOWED_BALANCE_COLUMNS: dict[str, str] = {
    # --- P8: the bank's own figures. Vinea derives none of these ----------------------------
    "bank_statements.opening_balance": (
        "the balance the **bank** printed at the top of the statement. Not a balance of a "
        "Vinea account: it is evidence, keyed or read off the file's balance column, and the "
        "reconciliation compares the ledger against it rather than maintaining it."
    ),
    "bank_statements.closing_balance": "as above — the figure the bank printed at the bottom.",
    "bank_statement_lines.balance_after": (
        "the bank's own running balance on that line, stored as it came. Immutable by "
        "trigger (VN013) and never recomputed, because it is a transcription of the file."
    ),
    "bank_reconciliations.statement_balance": (
        "the bank balance the reconciliation was struck against — keyed, or defaulted from "
        "the latest statement line. The thing being proved *to*, not a derived figure."
    ),
    "bank_reconciliations.ledger_balance": (
        "the snapshot of a proof, stored at lock. `assert_bank_invariants` clause 4 "
        "recomputes it from the lines that existed at that moment and asserts it reproduces "
        "exactly — which is the verifiable-cache exemption ADR-04 names, the same standing "
        "`period_balances` has."
    ),
    "bank_accounts.last_reconciled_balance": (
        "a cache of a **stored row** — the latest locked reconciliation's statement balance — "
        "and not a balance of the account, which stays sum(journal_lines). Clause 7 "
        "recomputes it from that row every time the suite runs."
    ),
}


def test_no_mutable_balance_columns_exist() -> None:
    """ADR-04: balances are derived. Guard the rule from the first schema onwards."""
    inspector = inspect(engine)
    found = {
        f"{table}.{column['name']}"
        for table in inspector.get_table_names()
        for column in inspector.get_columns(table)
        if "balance" in column["name"].lower()
    }

    assert found == set(ALLOWED_BALANCE_COLUMNS), (
        "a column whose name contains 'balance' is either a stored balance (which ADR-04 "
        "forbids) or belongs in ALLOWED_BALANCE_COLUMNS with a reason: "
        f"unregistered={sorted(found - set(ALLOWED_BALANCE_COLUMNS))} "
        f"missing={sorted(set(ALLOWED_BALANCE_COLUMNS) - found)}"
    )


def test_amount_columns_never_use_floating_point() -> None:
    inspector = inspect(engine)
    offenders = [
        f"{table}.{column['name']}"
        for table in inspector.get_table_names()
        for column in inspector.get_columns(table)
        if str(column["type"]).upper().startswith(("FLOAT", "REAL", "DOUBLE"))
    ]
    assert offenders == []


def test_postgres_data_directories_are_ignored() -> None:
    ignored = subprocess.run(
        ["git", "check-ignore", "vinea-pgdata", "backend/vinea-pgdata"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ignored.returncode == 0, "vinea-pgdata is not gitignored"
    assert ignored.stdout.split() == ["vinea-pgdata", "backend/vinea-pgdata"]

    tracked = subprocess.run(
        ["git", "ls-files", "--", "*vinea-pgdata*", "*pgdata*"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.stdout.strip() == ""


def test_audit_log_records_who_and_when() -> None:
    columns = _columns("audit_log")
    for name in ("company_id", "actor_user_id", "action", "entity", "before", "after", "at"):
        assert name in columns


def test_row_level_security_helpers_exist(db: Session) -> None:
    functions = db.execute(
        text(
            "SELECT proname FROM pg_proc WHERE proname IN "
            "('app_current_company_id', 'app_platform_mode')"
        )
    ).scalars()
    assert set(functions) == {"app_current_company_id", "app_platform_mode"}
