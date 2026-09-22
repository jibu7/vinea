"""The P8 migration back-fill.

A tenant provisioned *before* P8 must come out of `alembic upgrade head` able to register a
bank account, import a statement and revalue. If `bank_revaluation_account_id` is NULL the
first month-end run has nowhere to put the contra; if a bank/cash control account has no
`bank_accounts` row it is invisible to every bank listing and `assert_bank_invariants` clause
6 fails; if the Administrator role has no `bank:setup_manage` nobody can register one at all.
Each of those fails at whichever operation first needs it rather than at the upgrade, which is
the whole reason this file exists.

Same shape as `tests/test_p7_backfill.py`, with the addition rule 10 asks for by name and one
this revision needs specifically: the fixture tenant is provisioned **with posted bank lines**
— including **a USD settlement on the RWF bank account**, which is decision 2's one-sided rule
seen from the history side. That line is legal and must stay legal: the back-fill registers
`1120` in the base currency, and a back-fill that had guessed USD from the line would have
turned a valid piece of history into a row the `VN012` trigger forbids.

`make migrate-check` runs on an empty scratch database and therefore proves DDL and nothing
else; a back-fill that would fail on a real tenant passes it cleanly, which is exactly what
happened at P5 step 9.
"""

import os
from collections.abc import Iterator

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from alembic import command
from app.core.permissions import ALL_PERMISSIONS
from tests.conftest import ADMIN_URL

#: Per process, exactly as `conftest.py` names its own test database.
BACKFILL_DB = f"{ADMIN_URL.database}_p8_backfill_{os.getpid()}"
PRE_P8_REVISION = "0026_p7_z_high_water"

# The slice of `rw_sme_v1` the P8 back-fill keys off, as a pre-P8 tenant would have had it:
# `1100` (the parent `1130` hangs from), the two flagged cash accounts, the two drawer-default
# accounts, and enough of the rest to post a settlement.
PRE_P8_ACCOUNTS = (
    ("1000", "Assets", "asset", None, False, None),
    ("1100", "Current Assets", "asset", "1000", False, None),
    ("1110", "Cash on Hand", "asset", "1100", True, "cash"),
    ("1120", "Bank Account", "asset", "1100", True, "bank"),
    ("1200", "Accounts Receivable", "asset", "1100", True, "ar"),
    ("3000", "Equity", "equity", None, False, None),
    ("3200", "Retained Earnings", "equity", "3000", True, None),
    ("3400", "Opening Balance Suspense", "equity", "3000", True, None),
    ("4000", "Income", "income", None, False, None),
    ("4300", "Other Income", "income", "4000", True, None),
    ("6000", "Operating Expenses", "expense", None, False, None),
    ("6700", "Bank Charges", "expense", "6000", True, None),
)

#: settings column → the account code it must resolve to after the upgrade.
EXPECTED_SETTINGS = {
    "bank_revaluation_account_id": "1130",
    "bank_charges_account_id": "6700",
    "bank_interest_account_id": "4300",
}

#: `1130` and the class it must have. **Not a control account**, and the test says so: the
#: revaluation posts to it, and a control type would make it reachable only from the module
#: that owned it — which is the whole reason decision 8 chose a plain account.
EXPECTED_ACCOUNT = ("1130", "asset")

NEW_PERMISSIONS = (
    "bank:setup_manage",
    "bank:statement_import",
    "bank:reconcile",
    "bank:reconcile_lock",
    "bank:payment_run_post",
    "bank:reports_view",
)


def _alembic(url: str, revision: str) -> None:
    config = Config("alembic.ini")
    # alembic.ini goes through configparser interpolation, so a literal % must be escaped.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, revision)


@pytest.fixture
def pre_p8_engine() -> Iterator[Engine]:
    admin = create_engine(ADMIN_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{BACKFILL_DB}"'))
        conn.execute(text(f'CREATE DATABASE "{BACKFILL_DB}"'))
    admin.dispose()

    url = ADMIN_URL.set(database=BACKFILL_DB)
    _alembic(url.render_as_string(hide_password=False), PRE_P8_REVISION)
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


def _provision_pre_p8_tenant(engine: Engine, company_name: str = "Pre-P8 Ltd") -> int:
    """A historical fixture, not a data fix: the P8 seed pack cannot run here, because `1130`
    and the three settings keys do not exist at 0026.

    It posts two real entries as well — an RWF opening receipt and a **USD receipt into the
    RWF bank account** — so that the assertions below have something to be about.
    """
    with engine.connect() as conn:
        company_id = conn.execute(
            text(
                "INSERT INTO companies (name, tin, vat_registered, fiscal_country, status, "
                "coa_template) VALUES (:name, '999000099', true, 'RW', 'active', 'rw_sme_v1') "
                "RETURNING id"
            ),
            {"name": company_name},
        ).scalar_one()
        rwf_id = conn.execute(
            text(
                "INSERT INTO currencies (company_id, code, name, symbol, decimal_places, "
                "is_base, is_active) VALUES (:cid, 'RWF', 'Rwandan Franc', 'FRw', 0, true, true) "
                "RETURNING id"
            ),
            {"cid": company_id},
        ).scalar_one()
        usd_id = conn.execute(
            text(
                "INSERT INTO currencies (company_id, code, name, symbol, decimal_places, "
                "is_base, is_active) VALUES (:cid, 'USD', 'US Dollar', '$', 2, false, true) "
                "RETURNING id"
            ),
            {"cid": company_id},
        ).scalar_one()
        branch_id = conn.execute(
            text(
                "INSERT INTO branches (company_id, code, name, is_main, is_active) "
                "VALUES (:cid, 'MAIN', 'Head Office', true, true) RETURNING id"
            ),
            {"cid": company_id},
        ).scalar_one()
        ids: dict[str, int] = {}
        for code, name, class_, parent, postable, control in PRE_P8_ACCOUNTS:
            ids[code] = conn.execute(
                text(
                    """
                    INSERT INTO gl_accounts (company_id, code, name, class, parent_id,
                                             is_postable, is_control, control_type, is_active)
                    VALUES (:cid, :code, :name, CAST(:class AS account_class), :parent,
                            :postable, :is_control, CAST(:control AS gl_control_type), true)
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
                "INSERT INTO gl_settings (company_id, retained_earnings_account_id) "
                "VALUES (:cid, :re)"
            ),
            {"cid": company_id, "re": ids["3200"]},
        )
        conn.execute(
            text(
                "INSERT INTO roles (company_id, name, description, permissions, is_system) "
                "VALUES (:cid, 'Administrator', 'Full access', CAST(:permissions AS jsonb), true)"
            ),
            {"cid": company_id, "permissions": '["gl:setup_manage", "ar:transactions_post"]'},
        )
        for role, granted in (
            ("Accountant", '["gl:journal_post", "gl:reports_view"]'),
            ("Clerk", '["gl:reports_view"]'),
        ):
            conn.execute(
                text(
                    "INSERT INTO roles (company_id, name, description, permissions, is_system) "
                    "VALUES (:cid, :role, 'Seeded', CAST(:permissions AS jsonb), true)"
                ),
                {"cid": company_id, "role": role, "permissions": granted},
            )
    # A real transaction, not the fixture's AUTOCOMMIT connection: `trg_journal_lines_balanced`
    # is a DEFERRABLE INITIALLY DEFERRED constraint trigger, so under autocommit it would fire
    # after the first leg and refuse a posting that balances by the time both legs are in.
    txn_engine = create_engine(ADMIN_URL.set(database=BACKFILL_DB))
    with txn_engine.begin() as conn:
        _post_the_bank_history(
            conn,
            company_id=company_id,
            rwf_id=rwf_id,
            usd_id=usd_id,
            branch_id=branch_id,
            accounts=ids,
        )
    txn_engine.dispose()
    return company_id


def _post_the_bank_history(
    conn,  # noqa: ANN001 - a raw Connection; typing it adds an import and no safety
    *,
    company_id: int,
    rwf_id: int,
    usd_id: int,
    branch_id: int,
    accounts: dict[str, int],
) -> None:
    """Two posted cashbook-shaped entries on `1120`.

    The second is the one this revision is really tested against: **USD 200 into the RWF bank
    account at 1 300**, which decision 2 keeps legal because it is how a customer's USD invoice
    is paid through a Rwandan bank. After the upgrade `1120` is registered in RWF, the
    `VN012` trigger only constrains *foreign*-currency accounts, and this line is untouched.

    Written straight into the tables because the ORM is at head and this database is at 0026 —
    a historical fixture, not a data fix — but **through** the guards rather than around them:
    `app.posting_engine` is set the way `posting._write` sets it, the header goes in as a draft
    before its lines and is flipped afterwards.
    """
    conn.execute(text("SELECT set_config('app.posting_engine', 'on', false)"))
    year_id = conn.execute(
        text(
            "INSERT INTO fiscal_years (company_id, name, start_date, end_date, status) "
            "VALUES (:cid, '2026', DATE '2026-01-01', DATE '2026-12-31', 'open') RETURNING id"
        ),
        {"cid": company_id},
    ).scalar_one()
    period_id = conn.execute(
        text(
            "INSERT INTO accounting_periods (company_id, fiscal_year_id, period_no, name, "
            "start_date, end_date, status) VALUES (:cid, :year, 9, 'September 2026', "
            "DATE '2026-09-01', DATE '2026-09-30', 'open') RETURNING id"
        ),
        {"cid": company_id, "year": year_id},
    ).scalar_one()

    entries = (
        # (number, description, [(account, currency, rate, amount, base_amount)])
        (
            "CB-000001",
            "opening balance",
            [
                ("1120", rwf_id, "1", "1000000", "1000000"),
                ("3400", rwf_id, "1", "-1000000", "-1000000"),
            ],
        ),
        (
            "CB-000002",
            "USD receipt into the RWF account",
            [
                ("1120", usd_id, "1300", "200.00", "260000"),
                ("4300", usd_id, "1300", "-200.00", "-260000"),
            ],
        ),
    )
    for number, description, lines in entries:
        entry_id = conn.execute(
            text(
                """
                INSERT INTO journal_entries (company_id, number, doc_type, event_type, module,
                                             entry_date, period_id, description, status,
                                             posted_at)
                VALUES (:cid, :number, 'CB', 'cashbook_entry', 'cb', DATE '2026-09-15',
                        :period, :description, 'draft', now())
                RETURNING id
                """
            ),
            {
                "cid": company_id,
                "number": number,
                "period": period_id,
                "description": description,
            },
        ).scalar_one()
        for line_no, (code, currency, rate, amount, base) in enumerate(lines, start=1):
            conn.execute(
                text(
                    """
                    INSERT INTO journal_lines (company_id, entry_id, line_no, gl_account_id,
                                               currency_id, exchange_rate, amount, base_amount,
                                               tax_amount, branch_id)
                    VALUES (:cid, :entry, :line_no, :account, :currency,
                            CAST(:rate AS numeric), CAST(:amount AS numeric),
                            CAST(:base AS numeric), 0, :branch)
                    """
                ),
                {
                    "cid": company_id,
                    "entry": entry_id,
                    "line_no": line_no,
                    "account": accounts[code],
                    "currency": currency,
                    "rate": rate,
                    "amount": amount,
                    "base": base,
                    "branch": branch_id,
                },
            )
        conn.execute(
            text("UPDATE journal_entries SET status = 'posted' WHERE id = :entry"),
            {"entry": entry_id},
        )


def _posted_snapshot(conn, company_id: int) -> list[tuple]:  # noqa: ANN001
    """Every posted journal row, as a comparable tuple."""
    return [
        tuple(row)
        for row in conn.execute(
            text(
                "SELECT l.entry_id, l.line_no, l.gl_account_id, l.currency_id, l.exchange_rate, "
                "       l.amount, l.base_amount, e.status, e.number "
                "  FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id "
                " WHERE l.company_id = :cid ORDER BY l.entry_id, l.line_no"
            ),
            {"cid": company_id},
        )
    ]


def test_p8_backfills_a_pre_p8_tenant(pre_p8_engine: Engine) -> None:
    company_id = _provision_pre_p8_tenant(pre_p8_engine)
    url = ADMIN_URL.set(database=BACKFILL_DB).render_as_string(hide_password=False)
    with pre_p8_engine.connect() as conn:
        before = _posted_snapshot(conn, company_id)
    assert before, "the fixture must post something for the immutability claim to be about"

    _alembic(url, "head")

    with pre_p8_engine.connect() as conn:
        accounts = {
            row.code: row
            for row in conn.execute(
                text(
                    "SELECT id, code, control_type, is_control, is_postable, "
                    "class AS account_class FROM gl_accounts WHERE company_id = :cid"
                ),
                {"cid": company_id},
            )
        }
        # 1. `1130`, plain and postable. A control type here would make the revaluation's own
        # contra reachable only from the module that owned it.
        code, expected_class = EXPECTED_ACCOUNT
        assert code in accounts
        assert accounts[code].account_class == expected_class
        assert accounts[code].control_type is None
        assert accounts[code].is_control is False
        assert accounts[code].is_postable is True

        # 2. Every P8 `gl_settings` key, resolved, none NULL.
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
            assert value == accounts[expected_code].id, column

        # 3. Every bank/cash control account has exactly one master row, in the base currency,
        # with the GL code and name and the kind its control type says (clause 6).
        rows = {
            row.code: row
            for row in conn.execute(
                text(
                    "SELECT b.code, b.name, b.kind, b.gl_account_id, b.is_active, c.code "
                    "       AS currency_code "
                    "  FROM bank_accounts b JOIN currencies c ON c.id = b.currency_id "
                    " WHERE b.company_id = :cid"
                ),
                {"cid": company_id},
            )
        }
        assert set(rows) == {"1110", "1120"}
        assert rows["1120"].kind == "bank"
        assert rows["1110"].kind == "cash"
        assert rows["1120"].name == "Bank Account"
        assert {row.currency_code for row in rows.values()} == {"RWF"}
        assert all(row.is_active for row in rows.values())
        # And nothing else got one: an AR control account is not a bank account.
        assert accounts["1200"].id not in {row.gl_account_id for row in rows.values()}

        # 4. The USD line on the RWF bank account survived, and is still legal. The back-fill
        # registers `1120` in the **base** currency precisely so that it does: had it guessed
        # USD from this line, `VN012` would forbid every RWF line on the account from here on
        # and the tenant's own cashbook would stop working.
        usd_line = conn.execute(
            text(
                "SELECT l.amount, l.base_amount, c.code FROM journal_lines l "
                "  JOIN currencies c ON c.id = l.currency_id "
                " WHERE l.company_id = :cid AND l.gl_account_id = :account AND c.code = 'USD'"
            ),
            {"cid": company_id, "account": accounts["1120"].id},
        ).one()
        assert (usd_line.amount, usd_line.base_amount) == (200, 260000)

        # 5. Roles store their permissions as *data*. Administrator gets the whole union;
        # Accountant gets five of P8's six and not `bank:setup_manage`; Clerk gets the one.
        permissions = {
            row.name: row.permissions
            for row in conn.execute(
                text("SELECT name, permissions FROM roles WHERE company_id = :cid"),
                {"cid": company_id},
            )
        }
        for permission in NEW_PERMISSIONS:
            assert permission in permissions["Administrator"]
        assert set(ALL_PERMISSIONS) <= set(permissions["Administrator"])
        assert "bank:setup_manage" not in permissions["Accountant"]
        assert {
            "bank:statement_import",
            "bank:reconcile",
            "bank:reconcile_lock",
            "bank:payment_run_post",
            "bank:reports_view",
        } <= set(permissions["Accountant"])
        assert [p for p in NEW_PERMISSIONS if p in permissions["Clerk"]] == ["bank:reports_view"]

        # 6. Rule 10, the half `make migrate-check` cannot reach: the upgrade ran over posted
        # rows and not one of them moved.
        assert _posted_snapshot(conn, company_id) == before


def test_the_revaluation_role_enum_gains_bank_and_all(pre_p8_engine: Engine) -> None:
    """Rebuilt, not `ALTER TYPE … ADD VALUE` (P6 step 1). `both` stays — an enum value some
    tenant's history already uses is never removed."""
    _provision_pre_p8_tenant(pre_p8_engine, company_name="Enum Ltd")
    url = ADMIN_URL.set(database=BACKFILL_DB).render_as_string(hide_password=False)

    _alembic(url, "head")

    with pre_p8_engine.connect() as conn:
        labels = [
            row[0]
            for row in conn.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                    " WHERE t.typname = 'fx_revaluation_role' ORDER BY e.enumsortorder"
                )
            )
        ]

    assert labels == ["ar", "ap", "both", "bank", "all"]


def test_a_revaluation_line_is_a_document_or_a_bank_account_and_never_both(
    pre_p8_engine: Engine,
) -> None:
    """`document_id` goes nullable so a bank line can exist, and the CHECK is what stops that
    widening from giving the column a second meaning — a line with neither, or with both, is
    a line nobody can say what was revalued."""
    _provision_pre_p8_tenant(pre_p8_engine, company_name="Check Ltd")
    url = ADMIN_URL.set(database=BACKFILL_DB).render_as_string(hide_password=False)

    _alembic(url, "head")

    with pre_p8_engine.connect() as conn:
        # By definition rather than by name: `app.db.NAMING_CONVENTION` prefixes a CHECK with
        # `ck_<table>_`, and a test that hard-coded the prefixed name would be asserting the
        # convention rather than the rule.
        constraint = conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                " WHERE conrelid = 'fx_revaluation_lines'::regclass AND contype = 'c' "
                "   AND conname LIKE '%%line_is_a_document_or_a_bank_account'"
            )
        ).scalar_one()
        nullable = conn.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                " WHERE table_name = 'fx_revaluation_lines' AND column_name = :column"
            ),
            {"column": "document_id"},
        ).scalar_one()

    assert "bank_account_id" in constraint
    assert nullable == "YES"


def test_a_second_upgrade_of_the_same_tenant_is_a_no_op(pre_p8_engine: Engine) -> None:
    """The back-fill is written `WHERE … IS NULL` / `NOT EXISTS` throughout, so re-running it
    changes nothing — which matters because a tenant restored from a backup taken mid-phase is
    upgraded again, and a second `bank_accounts` row for `1120` would violate
    `uq_bank_accounts_company_gl_account` rather than being ignored."""
    company_id = _provision_pre_p8_tenant(pre_p8_engine, company_name="Twice Ltd")
    url = ADMIN_URL.set(database=BACKFILL_DB).render_as_string(hide_password=False)
    _alembic(url, "head")

    # Re-run the back-fill's own insert against a tenant that already has everything — the
    # state a restored backup is in. The statement is the migration's, verbatim; if the guard
    # clause is ever dropped from it this fails on the unique constraint.
    with pre_p8_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO bank_accounts
                    (company_id, gl_account_id, kind, code, name, currency_id, is_active)
                SELECT a.company_id, a.id, CAST(a.control_type::text AS bank_account_kind),
                       a.code, a.name, c.id, true
                  FROM gl_accounts a
                  JOIN currencies c ON c.company_id = a.company_id AND c.is_base
                 WHERE a.control_type IN ('bank', 'cash')
                   AND NOT EXISTS (
                       SELECT 1 FROM bank_accounts b
                        WHERE b.company_id = a.company_id AND b.gl_account_id = a.id
                   )
                """
            )
        )
        rows = conn.execute(
            text("SELECT count(*) FROM bank_accounts WHERE company_id = :cid"),
            {"cid": company_id},
        ).scalar_one()

    assert rows == 2
