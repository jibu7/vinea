"""The P5 migration back-fill.

A tenant provisioned *before* P5 must come out of `alembic upgrade head` able to post stock:
if `inventory_account_id` is NULL, or there is no in-transit warehouse, or the COUNT category
has no base unit, the first adjustment fails at runtime — long after anyone can connect the
failure to the upgrade. Same shape as `tests/test_p4_backfill.py`: build a tenant at the last
revision before this phase, upgrade to head, assert every back-filled row.

It runs on its own throwaway database so it cannot disturb the suite's schema.
"""

from collections.abc import Iterator

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from alembic import command
from app.core.permissions import ALL_PERMISSIONS
from tests.conftest import ADMIN_URL

BACKFILL_DB = f"{ADMIN_URL.database}_p5_backfill"
PRE_P5_REVISION = "0011_p4_doc_txn_type"

# The slice of `rw_sme_v1` the P5 back-fill keys off, as a pre-P5 tenant would have had it:
# 1300 present but *not* yet marked as a control account, and neither 1350 nor 3400 existing.
PRE_P5_ACCOUNTS = (
    ("1000", "Assets", "asset", None, False, None),
    ("1100", "Current Assets", "asset", "1000", False, None),
    ("1200", "Accounts Receivable", "asset", "1100", True, "ar"),
    ("1300", "Inventory", "asset", "1100", True, None),
    ("2000", "Liabilities", "liability", None, False, None),
    ("2100", "Accounts Payable", "liability", "2000", True, "ap"),
    ("3000", "Equity", "equity", None, False, None),
    ("3200", "Retained Earnings", "equity", "3000", True, None),
    ("4000", "Income", "income", None, False, None),
    ("5000", "Cost of Sales", "expense", None, False, None),
    ("5100", "Cost of Goods Sold", "expense", "5000", True, None),
    ("5200", "Inventory Adjustments", "expense", "5000", True, None),
    ("6000", "Operating Expenses", "expense", None, False, None),
    ("6990", "Sundry Expenses", "expense", "6000", True, None),
)

EXPECTED_SETTINGS = {
    "inventory_account_id": "1300",
    "inventory_in_transit_account_id": "1350",
    "inventory_adjustment_account_id": "5200",
    # Allowed to equal the adjustment account (decision 10), and it does by default.
    "stock_count_variance_account_id": "5200",
    "cogs_account_id": "5100",
}

EXPECTED_UOMS = {
    "COUNT": ("EA", 0),
    "WEIGHT": ("KG", 3),
    "VOLUME": ("L", 3),
    "LENGTH": ("M", 2),
}

EXPECTED_TRANSACTION_TYPES = {
    "ADJIN": ("adjustment_in", "5200"),
    "ADJOUT": ("adjustment_out", "5200"),
    "REVAL": ("revaluation", "5200"),
    "TRF": ("transfer", "1350"),
    "CNTV": ("count_variance", "5200"),
    "OPEN": ("opening_balance", "3400"),
}


def _alembic(url: str, revision: str) -> None:
    config = Config("alembic.ini")
    # alembic.ini goes through configparser interpolation, so a literal % must be escaped.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, revision)


@pytest.fixture
def pre_p5_engine() -> Iterator[Engine]:
    admin = create_engine(ADMIN_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{BACKFILL_DB}"'))
        conn.execute(text(f'CREATE DATABASE "{BACKFILL_DB}"'))
    admin.dispose()

    url = ADMIN_URL.set(database=BACKFILL_DB)
    _alembic(url.render_as_string(hide_password=False), PRE_P5_REVISION)
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


def _provision_pre_p5_tenant(engine: Engine, company_name: str = "Pre-P5 Ltd") -> int:
    """The P5 seed pack cannot run here — its tables do not exist at 0011 — so the tenant is
    built with the columns that existed then. A historical fixture, not a data fix."""
    with engine.connect() as conn:
        company_id = conn.execute(
            text(
                "INSERT INTO companies (name, vat_registered, fiscal_country, status, "
                "coa_template) VALUES (:name, false, 'RW', 'active', 'rw_sme_v1') "
                "RETURNING id"
            ),
            {"name": company_name},
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
        for code, name, class_, parent, postable, control in PRE_P5_ACCOUNTS:
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
                "VALUES (:cid, 'Administrator', 'Full access', "
                "CAST(:permissions AS jsonb), true)"
            ),
            {"cid": company_id, "permissions": '["inv:setup_manage", "gl:setup_manage"]'},
        )
    return company_id


def test_p5_backfills_a_pre_p5_tenant(pre_p5_engine: Engine) -> None:
    company_id = _provision_pre_p5_tenant(pre_p5_engine)
    url = ADMIN_URL.set(database=BACKFILL_DB).render_as_string(hide_password=False)

    _alembic(url, "head")

    with pre_p5_engine.connect() as conn:
        accounts = {
            row.code: row
            for row in conn.execute(
                text(
                    "SELECT id, code, control_type, is_control FROM gl_accounts "
                    "WHERE company_id = :cid"
                ),
                {"cid": company_id},
            )
        }
        # 1. The accounts P5 adds to rw_sme_v1.
        assert {"1350", "3400"} <= accounts.keys()
        assert accounts["1350"].control_type == "inventory"

        # The pre-P5 inventory account was not a control account; decision 2 makes it one, or
        # nothing stops a manual journal from posting straight into stock.
        assert accounts["1300"].control_type == "inventory"
        assert accounts["1300"].is_control is True

        # 2. Every inventory `gl_settings` key, resolved, none NULL.
        settings = conn.execute(
            text(
                "SELECT " + ", ".join(EXPECTED_SETTINGS) + ", negative_stock_policy, "
                "default_warehouse_id FROM gl_settings WHERE company_id = :cid"
            ),
            {"cid": company_id},
        ).one()
        for column, expected_code in EXPECTED_SETTINGS.items():
            value = getattr(settings, column)
            assert value is not None, f"{column} is NULL after the back-fill"
            assert value == accounts[expected_code].id, column
        # Block by default (decision 5) — an upgrade never loosens a policy.
        assert settings.negative_stock_policy == "block"

        # 3. The four UoM categories, each with exactly one base unit at its own scale.
        categories = {
            row.code: row
            for row in conn.execute(
                text("SELECT id, code FROM uom_categories WHERE company_id = :cid"),
                {"cid": company_id},
            )
        }
        assert set(categories) == set(EXPECTED_UOMS)
        units = list(
            conn.execute(
                text(
                    "SELECT category_id, code, factor_to_base, decimal_places, is_base "
                    "FROM uoms WHERE company_id = :cid"
                ),
                {"cid": company_id},
            )
        )
        assert len(units) == len(EXPECTED_UOMS)
        by_category = {unit.category_id: unit for unit in units}
        for code, (unit_code, decimals) in EXPECTED_UOMS.items():
            unit = by_category[categories[code].id]
            assert unit.code == unit_code
            assert unit.is_base is True
            assert unit.factor_to_base == 1
            assert unit.decimal_places == decimals

        # 4. Main and the in-transit warehouse, both on the main branch, exactly one of each.
        warehouses = {
            row.code: row
            for row in conn.execute(
                text(
                    "SELECT code, branch_id, is_default, is_in_transit, is_active "
                    "FROM warehouses WHERE company_id = :cid"
                ),
                {"cid": company_id},
            )
        }
        assert set(warehouses) == {"MAIN", "TRANSIT"}
        assert warehouses["MAIN"].is_default is True
        assert warehouses["TRANSIT"].is_in_transit is True
        main_branch = conn.execute(
            text("SELECT id FROM branches WHERE company_id = :cid AND is_main"),
            {"cid": company_id},
        ).scalar_one()
        assert warehouses["MAIN"].branch_id == main_branch
        assert warehouses["TRANSIT"].branch_id == main_branch
        assert settings.default_warehouse_id is not None

        # 5. The six inventory transaction types, each with its kind and contra account.
        types = {
            row.code: row
            for row in conn.execute(
                text(
                    "SELECT code, kind, default_gl_account_id FROM gl_transaction_types "
                    "WHERE company_id = :cid AND module = 'inv'"
                ),
                {"cid": company_id},
            )
        }
        assert set(types) == set(EXPECTED_TRANSACTION_TYPES)
        for code, (kind, account_code) in EXPECTED_TRANSACTION_TYPES.items():
            assert types[code].kind == kind, code
            assert types[code].default_gl_account_id == accounts[account_code].id, code

        # 6. The four document sequences, so the first adjustment does not have to invent one.
        sequences = {
            row.doc_type: row.prefix
            for row in conn.execute(
                text(
                    "SELECT doc_type, prefix FROM document_sequences WHERE company_id = :cid "
                    "AND doc_type IN ('INAJ', 'INJN', 'INTR', 'INCT')"
                ),
                {"cid": company_id},
            )
        }
        assert sequences == {
            "INAJ": "ADJ-",
            "INJN": "IJN-",
            "INTR": "TRF-",
            "INCT": "CNT-",
        }

        # 7. The Administrator role stores its permissions as *data*, so the constants in
        # `app.core.permissions` reach an existing tenant only because the migration puts
        # them there — and the whole list goes in, not just P5's two. The fixture's role was
        # seeded with two permissions, as a role provisioned several phases ago would have
        # been; after the upgrade it holds exactly what the code knows about.
        #
        # This compares against the *live* constant while the migration carries a frozen
        # copy. That is the point: the day P6 adds a permission without its own back-fill,
        # this line fails and says so.
        permissions = conn.execute(
            text(
                "SELECT permissions FROM roles WHERE company_id = :cid AND name "
                "= 'Administrator'"
            ),
            {"cid": company_id},
        ).scalar_one()
        assert set(permissions) == set(ALL_PERMISSIONS)
        assert len(permissions) == len(set(permissions)), "the union duplicated a permission"


def test_the_backfill_gives_every_tenant_its_own_rows(pre_p5_engine: Engine) -> None:
    """Two pre-P5 tenants on the same database. The back-fill is a set of company-scoped
    INSERT…SELECT statements, so the failure this guards against is a `WHERE` that matched
    one company and seeded the other's rows onto it — which the per-company unique indexes
    on `is_default` and `is_in_transit` would then reject, failing the whole upgrade."""
    first = _provision_pre_p5_tenant(pre_p5_engine)
    second = _provision_pre_p5_tenant(pre_p5_engine, company_name="Second Pre-P5 Ltd")
    url = ADMIN_URL.set(database=BACKFILL_DB).render_as_string(hide_password=False)

    _alembic(url, "head")

    with pre_p5_engine.connect() as conn:
        for company_id in (first, second):
            warehouses = {
                row.code: row.is_in_transit
                for row in conn.execute(
                    text(
                        "SELECT code, is_in_transit FROM warehouses WHERE company_id = :cid"
                    ),
                    {"cid": company_id},
                )
            }
            assert warehouses == {"MAIN": False, "TRANSIT": True}, company_id

            units = conn.execute(
                text("SELECT count(*) FROM uoms WHERE company_id = :cid"), {"cid": company_id}
            ).scalar_one()
            assert units == len(EXPECTED_UOMS), company_id

            types = conn.execute(
                text(
                    "SELECT count(*) FROM gl_transaction_types WHERE company_id = :cid "
                    "AND module = 'inv'"
                ),
                {"cid": company_id},
            ).scalar_one()
            assert types == len(EXPECTED_TRANSACTION_TYPES), company_id

            default_warehouse = conn.execute(
                text("SELECT default_warehouse_id FROM gl_settings WHERE company_id = :cid"),
                {"cid": company_id},
            ).scalar_one()
            # Each company's default points at *its own* Main warehouse.
            own = conn.execute(
                text(
                    "SELECT id FROM warehouses WHERE company_id = :cid AND code = 'MAIN'"
                ),
                {"cid": company_id},
            ).scalar_one()
            assert default_warehouse == own, company_id


# --- A tenant that has already journalled against its inventory account ----------------------


def _post_manual_journal_on(
    engine: Engine, company_id: int, *, debit_code: str, credit_code: str
) -> int:
    """A posted, balanced manual journal as a pre-P5 tenant could legitimately have written
    it — 1300 was an ordinary postable account then, so a bookkeeper adjusting stock by hand
    was doing nothing wrong.

    Written directly, with the single-writer guard opened by hand, for the same reason
    `tests/test_p4_backfill.py` does it: this is a *historical* row. The posting engine only
    ever writes the current schema, so it cannot produce the shape the past had.
    """
    # A transactional connection, not the fixture's AUTOCOMMIT one: "a posted entry has at
    # least two lines" is a deferred constraint trigger, so under autocommit it fires on the
    # entry insert, before its lines can exist.
    txn_engine = create_engine(ADMIN_URL.set(database=BACKFILL_DB))
    with txn_engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.posting_engine', 'on', false)"))
        conn.execute(text("SET CONSTRAINTS ALL DEFERRED"))
        ids = {
            row.code: row.id
            for row in conn.execute(
                text("SELECT id, code FROM gl_accounts WHERE company_id = :cid"),
                {"cid": company_id},
            )
        }
        branch_id = conn.execute(
            text("SELECT id FROM branches WHERE company_id = :cid"), {"cid": company_id}
        ).scalar_one()
        currency_id = conn.execute(
            text("SELECT id FROM currencies WHERE company_id = :cid"), {"cid": company_id}
        ).scalar_one()
        year_id = conn.execute(
            text(
                "INSERT INTO fiscal_years (company_id, name, start_date, end_date, status) "
                "VALUES (:cid, '2026', '2026-01-01', '2026-12-31', 'open') RETURNING id"
            ),
            {"cid": company_id},
        ).scalar_one()
        period_id = conn.execute(
            text(
                "INSERT INTO accounting_periods (company_id, fiscal_year_id, period_no, name, "
                "start_date, end_date, status) VALUES (:cid, :year, 3, 'Mar 2026', "
                "'2026-03-01', '2026-03-31', 'open') RETURNING id"
            ),
            {"cid": company_id, "year": year_id},
        ).scalar_one()
        entry_id = conn.execute(
            text(
                "INSERT INTO journal_entries (company_id, number, doc_type, event_type, "
                "module, entry_date, period_id, description, status) VALUES (:cid, "
                "'JE-000001', 'JE', 'manual_journal', 'gl', '2026-03-10', :period, "
                "'Stock written up by hand, before P5', 'draft') RETURNING id"
            ),
            {"cid": company_id, "period": period_id},
        ).scalar_one()
        for line_no, (code, amount) in enumerate(
            ((debit_code, 5000), (credit_code, -5000)), start=1
        ):
            conn.execute(
                text(
                    """
                    INSERT INTO journal_lines
                        (company_id, entry_id, line_no, gl_account_id, branch_id, currency_id,
                         exchange_rate, amount, base_amount, tax_amount, is_rounding_line)
                    VALUES (:cid, :entry, :line_no, :account, :branch, :currency, 1, :amount,
                            :amount, 0, false)
                    """
                ),
                {
                    "cid": company_id,
                    "entry": entry_id,
                    "line_no": line_no,
                    "account": ids[code],
                    "branch": branch_id,
                    "currency": currency_id,
                    "amount": amount,
                },
            )
        # Posted last: the lines of a posted entry are immutable the moment it is posted, so
        # they have to exist first — the same order the posting engine works in.
        conn.execute(
            text("UPDATE journal_entries SET status = 'posted' WHERE id = :id"),
            {"id": entry_id},
        )
    txn_engine.dispose()
    return entry_id


def test_an_inventory_account_with_history_is_not_marked_as_a_control_account(
    pre_p5_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """The condition the whole control back-fill turns on.

    Marking 1300 `inventory` narrows it permanently: only `module='inv'` may post to it and
    every line on it must carry an item. This tenant's existing line satisfies neither, and
    posted lines are append-only, so it can never be made to. The upgrade must therefore
    succeed *and* leave the account alone — and say which company it left alone, because a
    silent skip is how an operator finds out months later that inventory never started.
    """
    untouched = _provision_pre_p5_tenant(pre_p5_engine, company_name="Journalled Ltd")
    clean = _provision_pre_p5_tenant(pre_p5_engine, company_name="Clean Ltd")
    _post_manual_journal_on(pre_p5_engine, untouched, debit_code="1300", credit_code="3200")
    url = ADMIN_URL.set(database=BACKFILL_DB).render_as_string(hide_password=False)

    _alembic(url, "head")

    with pre_p5_engine.connect() as conn:
        for company_id, expected_control in ((untouched, None), (clean, "inventory")):
            account = conn.execute(
                text(
                    "SELECT control_type, is_control FROM gl_accounts "
                    "WHERE company_id = :cid AND code = '1300'"
                ),
                {"cid": company_id},
            ).one()
            assert account.control_type == expected_control, company_id
            assert account.is_control is (expected_control is not None), company_id

            setting = conn.execute(
                text(
                    "SELECT inventory_account_id, inventory_adjustment_account_id "
                    "FROM gl_settings WHERE company_id = :cid"
                ),
                {"cid": company_id},
            ).one()
            # The control key follows the account: NULL where the account could not be
            # marked, so inventory refuses to start rather than posting into an unguarded
            # account. The contra key is an ordinary account and is filled either way.
            if expected_control is None:
                assert setting.inventory_account_id is None
            else:
                assert setting.inventory_account_id is not None
            assert setting.inventory_adjustment_account_id is not None, company_id

        # 1350 is created by this revision, so it has no history and is marked in both.
        transit_control = {
            row.company_id: row.control_type
            for row in conn.execute(
                text("SELECT company_id, control_type FROM gl_accounts WHERE code = '1350'")
            )
        }
        assert transit_control[untouched] == "inventory"
        assert transit_control[clean] == "inventory"

    # And the skip is announced, by company id, in the upgrade's output.
    output = capsys.readouterr().out
    assert f"company {untouched}: account 1300" in output
    assert "NOT being marked" in output
    assert f"company {clean}:" not in output


def test_the_rest_of_the_backfill_still_lands_for_a_tenant_with_history(
    pre_p5_engine: Engine,
) -> None:
    """Skipping the control mark is not skipping the phase: the tenant still gets its units,
    warehouses, transaction types and sequences, so the only thing standing between it and a
    working inventory module is the one decision a person has to make."""
    company_id = _provision_pre_p5_tenant(pre_p5_engine, company_name="Journalled Ltd")
    _post_manual_journal_on(pre_p5_engine, company_id, debit_code="1300", credit_code="3200")
    url = ADMIN_URL.set(database=BACKFILL_DB).render_as_string(hide_password=False)

    _alembic(url, "head")

    with pre_p5_engine.connect() as conn:
        counts = conn.execute(
            text(
                """
                SELECT (SELECT count(*) FROM uoms WHERE company_id = :cid) AS uoms,
                       (SELECT count(*) FROM warehouses WHERE company_id = :cid) AS warehouses,
                       (SELECT count(*) FROM gl_transaction_types
                         WHERE company_id = :cid AND module = 'inv') AS types,
                       (SELECT count(*) FROM document_sequences
                         WHERE company_id = :cid
                           AND doc_type IN ('INAJ','INJN','INTR','INCT')) AS sequences
                """
            ),
            {"cid": company_id},
        ).one()
    assert counts.uoms == len(EXPECTED_UOMS)
    assert counts.warehouses == 2
    assert counts.types == len(EXPECTED_TRANSACTION_TYPES)
    assert counts.sequences == 4
