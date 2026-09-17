"""The P7 migration back-fill.

A tenant provisioned *before* P7 must come out of `alembic upgrade head` able to fiscalize,
file a VAT return and revalue. If `vat_settlement_account_id` is NULL the first filing has
nowhere to settle; if `unrealized_fx_loss_account_id` is NULL the first month-end run fails;
if `VAT-OUT-18` has no `fiscal_tax_type` every sale line on it is refused `tax_class_unmapped`
at post; and if the Administrator role has no `fiscal:setup_manage` nobody can register a
device at all. Each of those fails at whichever operation first needs it rather than at the
upgrade, which is the whole reason this file exists.

Same shape as `tests/test_p6_backfill.py`, with one addition rule 10 asks for by name: the
fixture tenant is provisioned **with posted rows** — a balanced, tax-bearing journal entry
and its three lines, written through the kernel's own triggers — and the test asserts
afterwards that not one of them changed. `make migrate-check`
runs on an empty scratch database and therefore proves DDL and nothing else; a back-fill that
would fail on a real tenant passes it cleanly, which is exactly what happened at P5 step 9.
This revision touches no posted table, and the point of the assertion is that the claim is
checked rather than made.
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

#: Per process, exactly as `conftest.py` names its own test database: this fixture drops and
#: re-creates the scratch database and terminates every other connection to it, so a fixed
#: name would have two xdist workers killing each other's connections mid-test.
BACKFILL_DB = f"{ADMIN_URL.database}_p7_backfill_{os.getpid()}"
PRE_P7_REVISION = "0021_p6_landed_cost"

# The slice of `rw_sme_v1` the P7 back-fill keys off, as a pre-P7 tenant would have had it:
# the four parents the new accounts hang from, the VAT pair, and the AR control account the
# revaluation must **not** touch.
PRE_P7_ACCOUNTS = (
    ("1000", "Assets", "asset", None, False, None),
    ("1100", "Current Assets", "asset", "1000", False, None),
    ("1200", "Accounts Receivable", "asset", "1100", True, "ar"),
    ("1500", "Prepayments & Deposits", "asset", "1100", True, None),
    ("1400", "VAT Input (Receivable)", "asset", "1100", True, None),
    ("2000", "Liabilities", "liability", None, False, None),
    ("2100", "Accounts Payable", "liability", "2000", True, "ap"),
    ("2200", "VAT Output (Payable)", "liability", "2000", True, None),
    ("3000", "Equity", "equity", None, False, None),
    ("3200", "Retained Earnings", "equity", "3000", True, None),
    ("4000", "Income", "income", None, False, None),
    ("4100", "Sales Revenue", "income", "4000", True, None),
    ("6000", "Operating Expenses", "expense", None, False, None),
)

#: settings column → the account code it must resolve to after the upgrade.
EXPECTED_SETTINGS = {
    "vat_settlement_account_id": "2250",
    "ar_revaluation_account_id": "1290",
    "ap_revaluation_account_id": "2190",
    "unrealized_fx_gain_account_id": "4410",
    "unrealized_fx_loss_account_id": "6955",
}

#: The five accounts and the class each must have. **None of them is a control account**, and
#: the test says so explicitly: a revaluation contra marked `ar` would be refused by the
#: subledger guard the first time the month-end job ran, and the failure would read as a
#: posting bug rather than as a chart-of-accounts one.
EXPECTED_ACCOUNTS = {
    "1290": ("asset", None),
    "2190": ("liability", None),
    "2250": ("liability", None),
    "4410": ("income", None),
    "6955": ("expense", None),
}

EXPECTED_TAX_TYPES = {
    "VAT-OUT-18": "B",
    "VAT-IN-18": "B",
    "VAT-EXEMPT": "A",
    "VAT-ZERO": "C",
    "VAT-IN-IMP": "B",
}


def _alembic(url: str, revision: str) -> None:
    config = Config("alembic.ini")
    # alembic.ini goes through configparser interpolation, so a literal % must be escaped.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, revision)


@pytest.fixture
def pre_p7_engine() -> Iterator[Engine]:
    admin = create_engine(ADMIN_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{BACKFILL_DB}"'))
        conn.execute(text(f'CREATE DATABASE "{BACKFILL_DB}"'))
    admin.dispose()

    url = ADMIN_URL.set(database=BACKFILL_DB)
    _alembic(url.render_as_string(hide_password=False), PRE_P7_REVISION)
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


def _provision_pre_p7_tenant(engine: Engine, company_name: str = "Pre-P7 Ltd") -> int:
    """A historical fixture, not a data fix: the P7 seed pack cannot run here, because 2250
    and the four revaluation accounts do not exist at 0021.

    It posts a real journal entry as well, so that the "nothing posted changed" assertion
    below has something to be about.
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
        currency_id = conn.execute(
            text(
                "INSERT INTO currencies (company_id, code, name, symbol, decimal_places, "
                "is_base, is_active) VALUES (:cid, 'RWF', 'Rwandan Franc', 'FRw', 0, true, true) "
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
        for code, name, class_, parent, postable, control in PRE_P7_ACCOUNTS:
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
        # The four codes every pre-P7 tenant has, none carrying a fiscal class.
        for code, name, nature, rate, account in (
            ("VAT-OUT-18", "Output VAT 18% (Sales)", "output", "18", "2200"),
            ("VAT-IN-18", "Input VAT 18% (Purchases)", "input", "18", "1400"),
            ("VAT-EXEMPT", "Exempt", "exempt", "0", None),
            ("VAT-ZERO", "Zero-rated", "zero_rated", "0", None),
        ):
            conn.execute(
                text(
                    """
                    INSERT INTO tax_codes (company_id, code, name, nature, rate_pct,
                                           gl_account_id, valid_from, is_active)
                    VALUES (:cid, :code, :name, CAST(:nature AS tax_nature),
                            CAST(:rate AS numeric), :account, DATE '2020-01-01', true)
                    """
                ),
                {
                    "cid": company_id,
                    "code": code,
                    "name": name,
                    "nature": nature,
                    "rate": rate,
                    "account": ids.get(account) if account else None,
                },
            )
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
                "VALUES (:cid, 'Administrator', 'Full access', "
                "CAST(:permissions AS jsonb), true)"
            ),
            {"cid": company_id, "permissions": '["gl:setup_manage", "ar:transactions_post"]'},
        )
    # A real transaction, not the fixture's AUTOCOMMIT connection: `trg_journal_lines_balanced`
    # is a DEFERRABLE INITIALLY DEFERRED constraint trigger, so under autocommit it would fire
    # after the first leg — with the entry one-sided — and refuse a posting that is perfectly
    # balanced by the time all three legs are in.
    txn_engine = create_engine(ADMIN_URL.set(database=BACKFILL_DB))
    with txn_engine.begin() as conn:
        _post_an_entry(
            conn,
            company_id=company_id,
            currency_id=currency_id,
            branch_id=branch_id,
            accounts=ids,
        )
    txn_engine.dispose()
    return company_id


def _post_an_entry(
    conn,  # noqa: ANN001 - a raw Connection; typing it adds an import and no safety
    *,
    company_id: int,
    currency_id: int,
    branch_id: int,
    accounts: dict[str, int],
) -> None:
    """One posted, balanced, tax-bearing entry — the shape the VAT return will read.

    `posted` rather than draft on purpose: a draft entry is mutable and would prove nothing
    about a back-fill running over rows the kernel's own trigger protects.

    Written straight into the tables because the ORM is at head and this database is at 0021 —
    a historical fixture, not a data fix — but **through** the guards rather than around them:
    `app.posting_engine` is set the way `posting._write` sets it, the header goes in as a draft
    before its lines and is flipped afterwards, and the period, postable-account, balance and
    control-account checks are all left to fire if this fixture gets anything wrong. The debit
    is `1500` rather than the AR control account for exactly that reason: a control-account
    line needs a partner dimension, and inventing a partner here to satisfy a guard would be
    the fixture working around the product instead of matching it.
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
            "start_date, end_date, status) VALUES (:cid, :year, 3, 'March 2026', "
            "DATE '2026-03-01', DATE '2026-03-31', 'open') RETURNING id"
        ),
        {"cid": company_id, "year": year_id},
    ).scalar_one()
    entry_id = conn.execute(
        text(
            """
            INSERT INTO journal_entries (company_id, number, doc_type, event_type, module,
                                         entry_date, period_id, description, status, posted_at)
            VALUES (:cid, 'JE-000001', 'JE', 'manual_journal', 'gl', DATE '2026-03-15',
                    :period, 'opening sale', 'draft', now())
            RETURNING id
            """
        ),
        {"cid": company_id, "period": period_id},
    ).scalar_one()
    tax_code_id = conn.execute(
        text("SELECT id FROM tax_codes WHERE company_id = :cid AND code = 'VAT-OUT-18'"),
        {"cid": company_id},
    ).scalar_one()
    # 1 180 gross: 1 000 revenue carrying the code and its tax, 180 on the VAT account.
    lines = (
        ("1500", "1180", "1180", None, "0"),
        ("4100", "-1000", "-1000", tax_code_id, "-180"),
        ("2200", "-180", "-180", tax_code_id, "0"),
    )
    for line_no, (code, amount, base, tax_code, tax_amount) in enumerate(lines, start=1):
        conn.execute(
            text(
                """
                INSERT INTO journal_lines (company_id, entry_id, line_no, gl_account_id,
                                           currency_id, exchange_rate, amount, base_amount,
                                           tax_code_id, tax_amount, branch_id, partner_type,
                                           partner_id)
                VALUES (:cid, :entry, :line_no, :account, :currency, 1,
                        CAST(:amount AS numeric), CAST(:base AS numeric), :tax_code,
                        CAST(:tax_amount AS numeric), :branch, NULL, NULL)
                """
            ),
            {
                "cid": company_id,
                "entry": entry_id,
                "line_no": line_no,
                "account": accounts[code],
                "currency": currency_id,
                "amount": amount,
                "base": base,
                "tax_code": tax_code,
                "tax_amount": tax_amount,
                "branch": branch_id,
            },
        )
    # Draft first, lines, then posted — the order the engine writes in, because
    # `kernel_block_posted_line_mutation` refuses a line against an entry that is already
    # posted.
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
                "SELECT l.entry_id, l.line_no, l.gl_account_id, l.amount, l.base_amount, "
                "       l.tax_code_id, l.tax_amount, e.status, e.number "
                "  FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id "
                " WHERE l.company_id = :cid ORDER BY l.entry_id, l.line_no"
            ),
            {"cid": company_id},
        )
    ]


def test_p7_backfills_a_pre_p7_tenant(pre_p7_engine: Engine) -> None:
    company_id = _provision_pre_p7_tenant(pre_p7_engine)
    url = ADMIN_URL.set(database=BACKFILL_DB).render_as_string(hide_password=False)
    with pre_p7_engine.connect() as conn:
        before = _posted_snapshot(conn, company_id)
    assert before, "the fixture must post something for the immutability claim to be about"

    _alembic(url, "head")

    with pre_p7_engine.connect() as conn:
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
        # 1. The five accounts P7 adds, each plain and postable.
        assert EXPECTED_ACCOUNTS.keys() <= accounts.keys()
        for code, (expected_class, expected_control) in EXPECTED_ACCOUNTS.items():
            account = accounts[code]
            assert account.account_class == expected_class, code
            assert account.control_type == expected_control, (
                f"{code} must not be a control account: the revaluation posts to it and "
                "control accounts are reachable only from the module that owns them"
            )
            assert account.is_control is False, code
            assert account.is_postable is True, code

        # 2. Every P7 `gl_settings` key, resolved, none NULL.
        settings = conn.execute(
            text(
                "SELECT " + ", ".join(EXPECTED_SETTINGS) + ", fiscal_default_purchase_class_code "
                "FROM gl_settings WHERE company_id = :cid"
            ),
            {"cid": company_id},
        ).one()
        for column, expected_code in EXPECTED_SETTINGS.items():
            value = getattr(settings, column)
            assert value is not None, f"{column} is NULL after the back-fill"
            assert value == accounts[expected_code].id, column
        # The default purchase class is deliberately **not** back-filled: it is a code from
        # the authority's own classification, which this tenant has not synced yet, and a
        # made-up one would register every GL-only purchase line under a class that does not
        # describe it. Nullable, and the Defaults screen asks for it.
        assert settings.fiscal_default_purchase_class_code is None

        # 3. The fiscal class on every tax code, including the import code this revision adds.
        tax_types = dict(
            conn.execute(
                text("SELECT code, fiscal_tax_type FROM tax_codes WHERE company_id = :cid"),
                {"cid": company_id},
            ).all()
        )
        assert tax_types == EXPECTED_TAX_TYPES

        # 4. The import code inherits the tenant's own input VAT account and date window,
        # rather than a hard-coded `1400` — a company that pointed input VAT elsewhere did so
        # deliberately, and a code effective from today would be refused on a back-dated
        # import by `resolve_tax_code`.
        imported = conn.execute(
            text(
                "SELECT gl_account_id, valid_from, rate_pct, nature FROM tax_codes "
                "WHERE company_id = :cid AND code = 'VAT-IN-IMP'"
            ),
            {"cid": company_id},
        ).one()
        assert imported.gl_account_id == accounts["1400"].id
        assert str(imported.valid_from) == "2020-01-01"
        assert imported.rate_pct == 18
        assert imported.nature == "input"

        # 5. The Administrator role stores its permissions as *data*, so the constants in
        # `app.core.permissions` reach an existing tenant only because the migration puts them
        # there — and the whole list goes in, not just P7's six.
        permissions = conn.execute(
            text(
                "SELECT permissions FROM roles WHERE company_id = :cid AND name = 'Administrator'"
            ),
            {"cid": company_id},
        ).scalar_one()
        for permission in (
            "fiscal:setup_manage",
            "fiscal:queue_manage",
            "fiscal:reports_view",
            "tax:vat_return_view",
            "tax:vat_return_file",
            "gl:fx_revalue",
        ):
            assert permission in permissions
        assert set(ALL_PERMISSIONS) <= set(permissions)

        # 6. Rule 10, the half `make migrate-check` cannot reach: the upgrade ran over posted
        # rows and not one of them moved.
        assert _posted_snapshot(conn, company_id) == before


def test_the_new_fiscal_columns_land_nullable_on_existing_rows(pre_p7_engine: Engine) -> None:
    """Every mapping column is nullable and arrives NULL.

    Not a tautology: a non-null default would be worse than nothing here. `fiscal_class_code`
    defaulted to anything makes every existing item look registrable under a class nobody
    chose, and the posting refusal that exists to catch exactly that (`fiscal_class_missing`)
    would never fire. The whole design is that an unmapped master is *refused*, loudly, at the
    first fiscalized sale — so the back-fill must leave them empty.
    """
    company_id = _provision_pre_p7_tenant(pre_p7_engine, company_name="Nullable Ltd")
    url = ADMIN_URL.set(database=BACKFILL_DB).render_as_string(hide_password=False)

    _alembic(url, "head")

    with pre_p7_engine.connect() as conn:
        nullable = {
            (row.table_name, row.column_name): row.is_nullable
            for row in conn.execute(
                text(
                    """
                    SELECT table_name, column_name, is_nullable
                      FROM information_schema.columns
                     WHERE table_schema = 'public'
                       AND (table_name, column_name) IN (
                           ('items', 'fiscal_class_code'),
                           ('items', 'fiscal_origin_country'),
                           ('items', 'fiscal_package_unit'),
                           ('items', 'fiscal_item_type'),
                           ('uoms', 'fiscal_quantity_unit'),
                           ('tax_codes', 'fiscal_tax_type'),
                           ('partner_documents', 'payment_method'),
                           ('partner_documents', 'purchase_code'),
                           ('partner_documents', 'refund_of_document_id'),
                           ('partner_documents', 'refund_reason'),
                           ('partner_documents', 'fiscal_receipt_id')
                       )
                    """
                )
            )
        }
        assert len(nullable) == 11, f"a column is missing after the upgrade: {sorted(nullable)}"
        assert set(nullable.values()) == {"YES"}
        # And the company itself is not fiscalized: no device, so nothing changes for it.
        devices = conn.execute(
            text("SELECT count(*) FROM fiscal_devices WHERE company_id = :cid"),
            {"cid": company_id},
        ).scalar_one()
        assert devices == 0


def test_a_second_upgrade_of_the_same_tenant_is_a_no_op(pre_p7_engine: Engine) -> None:
    """The back-fill is written `WHERE … IS NULL` / `NOT EXISTS` throughout, so re-running it
    changes nothing — which matters because a tenant restored from a backup taken mid-phase
    is upgraded again, and a second `VAT-IN-IMP` would violate the unique constraint rather
    than being ignored."""
    company_id = _provision_pre_p7_tenant(pre_p7_engine, company_name="Twice Ltd")
    url = ADMIN_URL.set(database=BACKFILL_DB).render_as_string(hide_password=False)
    _alembic(url, "head")

    # Re-run the *back-fill's* own insert against a tenant that already has everything —
    # the state a restored backup is in. The statement is the migration's, verbatim; if the
    # guard clause is ever dropped from it this fails on the unique constraint.
    with pre_p7_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tax_codes
                    (company_id, code, name, nature, rate_pct, gl_account_id, valid_from,
                     is_active, fiscal_tax_type)
                SELECT existing.company_id, 'VAT-IN-IMP', 'Input VAT 18% (Imports)',
                       CAST('input' AS tax_nature), 18, existing.gl_account_id,
                       existing.valid_from, true, CAST('B' AS fiscal_tax_type)
                  FROM tax_codes existing
                 WHERE existing.code = 'VAT-IN-18'
                   AND NOT EXISTS (
                       SELECT 1 FROM tax_codes duplicate
                        WHERE duplicate.company_id = existing.company_id
                          AND duplicate.code = 'VAT-IN-IMP'
                   )
                """
            )
        )

    with pre_p7_engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT count(*) FROM tax_codes WHERE company_id = :cid AND code = 'VAT-IN-IMP'"
            ),
            {"cid": company_id},
        ).scalar_one()
        assert count == 1
