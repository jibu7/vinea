"""Schema-level guarantees the rest of the product will rely on (ADR-06, §4 sketch)."""

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

REPO_ROOT = Path(__file__).resolve().parents[2]


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


def test_no_mutable_balance_columns_exist() -> None:
    """ADR-04: balances are derived. Guard the rule from the first schema onwards."""
    inspector = inspect(engine)
    offenders = [
        f"{table}.{column['name']}"
        for table in inspector.get_table_names()
        for column in inspector.get_columns(table)
        if "balance" in column["name"].lower()
    ]
    assert offenders == []


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
