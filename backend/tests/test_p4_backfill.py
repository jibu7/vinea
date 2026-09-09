"""The P4 migration back-fill (review item 6).

A tenant provisioned *before* P4 must come out of `alembic upgrade head` fully configured:
if `realized_fx_gain_account_id` or a settlement-discount account is NULL, the first
allocation that moves a rate fails at runtime. This test builds a pre-P4 tenant against
revision `0005_p3_reference` — the last revision before this phase — upgrades to head, and
asserts every back-filled row.

It runs on its own throwaway database so it cannot disturb the suite's schema.
"""

from collections.abc import Iterator

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from alembic import command
from tests.conftest import ADMIN_URL

BACKFILL_DB = f"{ADMIN_URL.database}_backfill"
PRE_P4_REVISION = "0005_p3_reference"

# The slice of `rw_sme_v1` the back-fill keys off, as a pre-P4 tenant would have had it.
PRE_P4_ACCOUNTS = (
    ("1000", "Assets", "asset", None, False, None),
    ("1100", "Current Assets", "asset", "1000", False, None),
    ("1120", "Bank Account", "asset", "1100", True, "bank"),
    ("1200", "Accounts Receivable", "asset", "1100", True, "ar"),
    ("2000", "Liabilities", "liability", None, False, None),
    ("2100", "Accounts Payable", "liability", "2000", True, "ap"),
    ("3000", "Equity", "equity", None, False, None),
    ("3200", "Retained Earnings", "equity", "3000", True, None),
    ("4000", "Income", "income", None, False, None),
    ("4100", "Sales Revenue", "income", "4000", True, None),
    ("4400", "Foreign Exchange Gain", "income", "4000", True, None),
    ("6000", "Operating Expenses", "expense", None, False, None),
    ("6950", "Foreign Exchange Loss", "expense", "6000", True, None),
    ("6990", "Sundry Expenses", "expense", "6000", True, None),
)

EXPECTED_SETTINGS = {
    "ar_control_account_id": "1200",
    "ap_control_account_id": "2100",
    "realized_fx_gain_account_id": "4400",
    "realized_fx_loss_account_id": "6950",
    "settlement_discount_granted_account_id": "6960",
    "settlement_discount_received_account_id": "4350",
    "post_dated_receivable_account_id": "1250",
    "post_dated_payable_account_id": "2150",
}


def _alembic(url: str, revision: str) -> None:
    config = Config("alembic.ini")
    # alembic.ini goes through configparser interpolation, so a literal % must be escaped.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, revision)


@pytest.fixture
def pre_p4_engine() -> Iterator[Engine]:
    admin = create_engine(ADMIN_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{BACKFILL_DB}"'))
        conn.execute(text(f'CREATE DATABASE "{BACKFILL_DB}"'))
    admin.dispose()

    url = ADMIN_URL.set(database=BACKFILL_DB)
    _alembic(url.render_as_string(hide_password=False), PRE_P4_REVISION)
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        yield engine
    finally:
        engine.dispose()
        admin = create_engine(ADMIN_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": BACKFILL_DB},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{BACKFILL_DB}"'))
        admin.dispose()


def _provision_pre_p4_tenant(engine: Engine) -> int:
    """The P4 seed pack cannot run here — its tables do not exist at 0005 — so the tenant is
    built with the columns that existed then. This is a historical fixture, not a data fix."""
    with engine.connect() as conn:
        company_id = conn.execute(
            text(
                "INSERT INTO companies (name, vat_registered, fiscal_country, status, "
                "coa_template) VALUES ('Pre-P4 Ltd', false, 'RW', 'active', 'rw_sme_v1') "
                "RETURNING id"
            )
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO currencies (company_id, code, name, symbol, decimal_places, "
                "is_base, is_active) VALUES (:cid, 'RWF', 'Rwandan Franc', 'FRw', 0, true, true)"
            ),
            {"cid": company_id},
        )
        conn.execute(
            text(
                "INSERT INTO branches (company_id, code, name, is_main, is_active) "
                "VALUES (:cid, 'MAIN', 'Head Office', true, true)"
            ),
            {"cid": company_id},
        )
        ids: dict[str, int] = {}
        for code, name, class_, parent, postable, control in PRE_P4_ACCOUNTS:
            ids[code] = conn.execute(
                text(
                    """
                    INSERT INTO gl_accounts (company_id, code, name, class, parent_id,
                                             is_postable, is_control, control_type, is_active)
                    VALUES (:cid, :code, :name, CAST(:class AS account_class), :parent,
                            :postable, :is_control,
                            CAST(:control AS gl_control_type), true)
                    RETURNING id
                    """
                ),
                {
                    "cid": company_id,
                    "code": code,
                    "name": name,
                    "class": class_,
                    "parent": ids.get(parent) if parent else None,
                    "postable": postable,
                    "is_control": control is not None,
                    "control": control,
                },
            ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO gl_settings (company_id, retained_earnings_account_id, "
                "rounding_difference_account_id) VALUES (:cid, :re, :rd)"
            ),
            {"cid": company_id, "re": ids["3200"], "rd": ids["6950"]},
        )
    return company_id


def test_p4_backfills_a_pre_p4_tenant(pre_p4_engine: Engine) -> None:
    company_id = _provision_pre_p4_tenant(pre_p4_engine)
    url = ADMIN_URL.set(database=BACKFILL_DB).render_as_string(hide_password=False)
    _alembic(url, "head")

    with pre_p4_engine.connect() as conn:
        codes = {
            row.code: row.id
            for row in conn.execute(
                text("SELECT id, code FROM gl_accounts WHERE company_id = :cid"),
                {"cid": company_id},
            )
        }
        # 1. The four accounts P4 adds to rw_sme_v1.
        assert {"1250", "2150", "4350", "6960"} <= codes.keys()

        # 2. All eight gl_settings keys, resolved to the right accounts, none NULL.
        settings = conn.execute(
            text(
                "SELECT " + ", ".join(EXPECTED_SETTINGS) + " FROM gl_settings "
                "WHERE company_id = :cid"
            ),
            {"cid": company_id},
        ).one()
        for column, expected_code in EXPECTED_SETTINGS.items():
            value = getattr(settings, column)
            assert value is not None, f"{column} is NULL after the back-fill"
            assert value == codes[expected_code], column

        # 3. AR/AP transaction types, with their default accounts.
        types = {
            (row.module, row.code): row.default_gl_account_id
            for row in conn.execute(
                text(
                    "SELECT module, code, default_gl_account_id FROM gl_transaction_types "
                    "WHERE company_id = :cid AND module IN ('ar','ap')"
                ),
                {"cid": company_id},
            )
        }
        assert set(types) == {
            ("ar", "INV"),
            ("ar", "CRN"),
            ("ar", "RCT"),
            ("ar", "JNL"),
            ("ap", "INV"),
            ("ap", "DBN"),
            ("ap", "PMT"),
            ("ap", "JNL"),
        }
        assert types[("ar", "INV")] == codes["4100"]
        assert types[("ar", "RCT")] == codes["1120"]
        assert types[("ap", "PMT")] == codes["1120"]
        assert types[("ar", "JNL")] is None

        # 4. Payment terms.
        terms = {
            row.code: (row.due_basis, row.due_days, row.discount_percent, row.discount_days)
            for row in conn.execute(
                text(
                    "SELECT code, due_basis, due_days, discount_percent, discount_days "
                    "FROM payment_terms WHERE company_id = :cid"
                ),
                {"cid": company_id},
            )
        }
        assert set(terms) == {"COD", "NET30", "NET60", "EOM30", "2/10N30"}
        assert terms["NET30"][:2] == ("days_from_document_date", 30)
        assert terms["2/10N30"][2:] == (2, 10)

        # 5. The default ageing bucket set and its five buckets.
        buckets = conn.execute(
            text(
                """
                SELECT s.code, s.basis, s.is_default, b.sequence, b.label, b.from_days,
                       b.to_days
                  FROM ageing_bucket_sets s
                  JOIN ageing_buckets b ON b.bucket_set_id = s.id
                 WHERE s.company_id = :cid
                 ORDER BY b.sequence
                """
            ),
            {"cid": company_id},
        ).all()
        assert [(row.label, row.from_days, row.to_days) for row in buckets] == [
            ("Current", 0, 30),
            ("31 - 60", 31, 60),
            ("61 - 90", 61, 90),
            ("91 - 120", 91, 120),
            ("120+", 121, None),
        ]
        assert buckets[0].code == "STD" and buckets[0].basis == "due_date"
        assert buckets[0].is_default is True

        # 6. The control-account registry from 0009.
        registry = {
            (row.control_type, row.module)
            for row in conn.execute(
                text("SELECT control_type, module FROM control_account_modules")
            )
        }
        assert registry == {("ar", "ar"), ("ap", "ap"), ("inventory", "inv")}
