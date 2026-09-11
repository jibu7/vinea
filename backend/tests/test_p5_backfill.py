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

        # 7. The Administrator role stores its permissions as data, so the two new inventory
        # constants reach an existing tenant only because the migration puts them there.
        permissions = conn.execute(
            text(
                "SELECT permissions FROM roles WHERE company_id = :cid AND name "
                "= 'Administrator'"
            ),
            {"cid": company_id},
        ).scalar_one()
        assert "inv:count_process" in permissions
        assert "inv:item_rename" in permissions
        # And nothing it already held was lost.
        assert "gl:setup_manage" in permissions


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
