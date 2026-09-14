"""The P6 migration back-fill.

A tenant provisioned *before* P6 must come out of `alembic upgrade head` able to receive
goods and allocate a landed cost: if `grn_accrual_account_id` is NULL there is nowhere to
accrue a receipt, if `purchase_price_variance_account_id` is NULL the first match that
disagrees by a franc fails, and if the Administrator role has no `oe:landed_cost_post` nobody
can post one. Each of those fails at a posting rather than at the upgrade, long after anyone
can connect the two — which is the whole reason this file exists.

Same shape as `tests/test_p5_backfill.py`: build a tenant at the last revision before this
phase, upgrade to head, assert every back-filled row. It runs on its own throwaway database
so it cannot disturb the suite's schema.
"""

from collections.abc import Iterator

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from alembic import command
from app.core.permissions import ALL_PERMISSIONS
from tests.conftest import ADMIN_URL

BACKFILL_DB = f"{ADMIN_URL.database}_p6_backfill"
PRE_P6_REVISION = "0017_p5_count_enter"

# The slice of `rw_sme_v1` the P6 back-fill keys off, as a pre-P6 tenant would have had it:
# 5300 already present (P5 seeded it and nothing read it), 2350 and 1370 not yet existing, and
# the two parents the new accounts hang from.
PRE_P6_ACCOUNTS = (
    ("1000", "Assets", "asset", None, False, None),
    ("1100", "Current Assets", "asset", "1000", False, None),
    ("1200", "Accounts Receivable", "asset", "1100", True, "ar"),
    ("1300", "Inventory", "asset", "1100", True, "inventory"),
    ("2000", "Liabilities", "liability", None, False, None),
    ("2100", "Accounts Payable", "liability", "2000", True, "ap"),
    ("3000", "Equity", "equity", None, False, None),
    ("3200", "Retained Earnings", "equity", "3000", True, None),
    ("4000", "Income", "income", None, False, None),
    ("5000", "Cost of Sales", "expense", None, False, None),
    ("5100", "Cost of Goods Sold", "expense", "5000", True, None),
    ("5300", "Purchase Price Variance", "expense", "5000", True, None),
)

#: settings column → the account code it must resolve to after the upgrade.
EXPECTED_SETTINGS = {
    "grn_accrual_account_id": "2350",
    "purchase_price_variance_account_id": "5300",
    "landed_cost_clearing_account_id": "1370",
}

#: The registry rows that make the accrual reachable — and only from these two modules.
EXPECTED_REGISTRY_MODULES = {"inv", "ap"}


def _alembic(url: str, revision: str) -> None:
    config = Config("alembic.ini")
    # alembic.ini goes through configparser interpolation, so a literal % must be escaped.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, revision)


@pytest.fixture
def pre_p6_engine() -> Iterator[Engine]:
    admin = create_engine(ADMIN_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{BACKFILL_DB}"'))
        conn.execute(text(f'CREATE DATABASE "{BACKFILL_DB}"'))
    admin.dispose()

    url = ADMIN_URL.set(database=BACKFILL_DB)
    _alembic(url.render_as_string(hide_password=False), PRE_P6_REVISION)
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


def _provision_pre_p6_tenant(engine: Engine, company_name: str = "Pre-P6 Ltd") -> int:
    """The P6 seed pack cannot run here — 2350 and 1370 do not exist at 0017 — so the tenant
    is built with the columns that existed then. A historical fixture, not a data fix."""
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
        for code, name, class_, parent, postable, control in PRE_P6_ACCOUNTS:
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


def test_p6_backfills_a_pre_p6_tenant(pre_p6_engine: Engine) -> None:
    company_id = _provision_pre_p6_tenant(pre_p6_engine)
    url = ADMIN_URL.set(database=BACKFILL_DB).render_as_string(hide_password=False)

    _alembic(url, "head")

    with pre_p6_engine.connect() as conn:
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
        # 1. The two accounts P6 adds to rw_sme_v1, each with the right shape.
        assert {"2350", "1370"} <= accounts.keys()
        accrual = accounts["2350"]
        assert accrual.control_type == "grn_accrual", "the accrual must be a control account"
        assert accrual.is_control is True
        assert accrual.is_postable is True
        assert accrual.account_class == "liability"

        clearing = accounts["1370"]
        # Deliberately plain: freight arrives on a supplier invoice and duty on a cashbook
        # payment, and a control account would refuse both.
        assert clearing.control_type is None, "the clearing account must not be a control account"
        assert clearing.is_control is False
        assert clearing.is_postable is True
        assert clearing.account_class == "asset"

        # 2. Every order-entry `gl_settings` key, resolved, none NULL.
        settings = conn.execute(
            text(
                "SELECT " + ", ".join(EXPECTED_SETTINGS) + ", backorder_policy "
                "FROM gl_settings WHERE company_id = :cid"
            ),
            {"cid": company_id},
        ).one()
        for column, expected_code in EXPECTED_SETTINGS.items():
            value = getattr(settings, column)
            assert value is not None, f"{column} is NULL after the back-fill"
            assert value == accounts[expected_code].id, column
        # Allow by default (decision 7) — a sales order may promise what is not on the shelf.
        assert settings.backorder_policy == "allow"

        # 3. The registry rows that make the accrual reachable, and from exactly two modules.
        modules = {
            row.module
            for row in conn.execute(
                text(
                    "SELECT module FROM control_account_modules WHERE control_type = 'grn_accrual'"
                )
            )
        }
        assert modules == EXPECTED_REGISTRY_MODULES

        # 4. The Administrator role stores its permissions as *data*, so the constants in
        # `app.core.permissions` reach an existing tenant only because the migration puts
        # them there — and the whole list goes in, not just P6's one. The fixture's role was
        # seeded with two permissions, as a role provisioned several phases ago would have
        # been.
        permissions = conn.execute(
            text(
                "SELECT permissions FROM roles "
                "WHERE company_id = :cid AND name = 'Administrator'"
            ),
            {"cid": company_id},
        ).scalar_one()
        assert "oe:landed_cost_post" in permissions
        assert set(ALL_PERMISSIONS) <= set(permissions)


def test_the_new_enum_labels_survive_the_upgrade(pre_p6_engine: Engine) -> None:
    """The two enums are *rebuilt*, not extended — `ALTER TYPE … ADD VALUE` cannot be used in
    the one transaction this project runs its migrations in. A rebuild that lost a label, or
    dropped a column's default on the way through, would corrupt every existing row, so both
    are asserted rather than assumed."""
    company_id = _provision_pre_p6_tenant(pre_p6_engine, company_name="Enum Ltd")
    url = ADMIN_URL.set(database=BACKFILL_DB).render_as_string(hide_password=False)

    _alembic(url, "head")

    with pre_p6_engine.connect() as conn:
        def labels(type_name: str) -> list[str]:
            return [
                row.enumlabel
                for row in conn.execute(
                    text(
                        "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                        "WHERE t.typname = :name ORDER BY e.enumsortorder"
                    ),
                    {"name": type_name},
                )
            ]

        assert labels("gl_control_type") == ["bank", "cash", "ar", "ap", "inventory", "grn_accrual"]
        assert labels("item_type") == ["stock", "service", "non_stock", "kit"]
        assert labels("backorder_policy") == ["allow", "block"]

        # The existing control accounts still read back correctly through the rebuilt type.
        assert (
            conn.execute(
                text(
                    "SELECT control_type FROM gl_accounts "
                    "WHERE company_id = :cid AND code = '1300'"
                ),
                {"cid": company_id},
            ).scalar_one()
            == "inventory"
        )
        # And `items.item_type` keeps the server default the rebuild had to drop and restore.
        default = conn.execute(
            text(
                "SELECT pg_get_expr(d.adbin, d.adrelid) FROM pg_attribute a "
                "JOIN pg_class c ON c.oid = a.attrelid "
                "LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
                "WHERE c.relname = 'items' AND a.attname = 'item_type'"
            )
        ).scalar_one()
        assert default == "'stock'::item_type", f"the item_type default was lost: {default}"


# --- The step-3 back-fill: `partner_document_lines.role` ------------------------------------
#
# Rule 10's real requirement, and the one `make migrate-check` can never meet: a migration that
# UPDATEs is tested **against the rows it is meant to touch**. An empty scratch database proves
# the DDL and nothing else — the P5 step-9 back-fill passed it cleanly and could never have run
# on a real tenant.
#
# This one is an ordinary UPDATE on `partner_document_lines`, which carries no immutability
# trigger (posted *entries* and *moves* do, and this is neither). What has to be proved is that
# it reaches every row and puts the right role on each, because the column then goes NOT NULL
# and acquires a foreign key into `partner_documents (company_id, id, role)`: a row the update
# missed fails the ALTER, and a row it got wrong fails the constraint. Both fail the upgrade
# rather than the data, which is the design — but only if the upgrade is ever run over rows.

PRE_ORDERS_REVISION = "0019_p6_posting"


@pytest.fixture
def pre_orders_engine() -> Iterator[Engine]:
    admin = create_engine(ADMIN_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{BACKFILL_DB}"'))
        conn.execute(text(f'CREATE DATABASE "{BACKFILL_DB}"'))
    admin.dispose()

    url = ADMIN_URL.set(database=BACKFILL_DB)
    _alembic(url.render_as_string(hide_password=False), PRE_ORDERS_REVISION)
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


def _post_a_document(  # noqa: PLR0913
    conn,  # noqa: ANN001
    *,
    company_id: int,
    period_id: int,
    branch_id: int,
    currency_id: int,
    partner_id: int,
    partner_type: str,
    control_account_id: int,
    contra_account_id: int,
    role: str,
    number: str,
    doc_type: str,
    direction: int,
) -> int:
    """One posted partner document with one line, written straight into the tables.

    Straight SQL because the ORM is at head and this database is at 0019 — a historical
    fixture, not a data fix. The entry is balanced and carries the partner dimension the
    control-account guard demands, so it goes in through the same triggers a real posting does
    rather than around them: `app.posting_engine` is set the way the engine sets it, and every
    other guard — the period check, the postable-account check, the balance assertion, the
    control-account registry — is left to fire if this fixture gets anything wrong.
    """
    conn.execute(text("SELECT set_config('app.posting_engine', 'on', false)"))
    entry_id = conn.execute(
        text(
            """
            INSERT INTO journal_entries (company_id, number, doc_type, event_type, entry_date,
                                         period_id, description, status, module)
            VALUES (:cid, :number, :doc_type, 'PartnerDocumentPosted', CURRENT_DATE, :period,
                    :description, 'draft', :role)
            RETURNING id
            """
        ),
        {
            "cid": company_id,
            "number": number,
            "doc_type": doc_type,
            "period": period_id,
            "description": f"{role} document",
            "role": role,
        },
    ).scalar_one()
    legs = (
        (control_account_id, direction * 1000, True),
        (contra_account_id, -direction * 1000, False),
    )
    for line_no, (account_id, amount, with_partner) in enumerate(legs, 1):
        conn.execute(
            text(
                """
                INSERT INTO journal_lines (company_id, entry_id, line_no, gl_account_id,
                                           branch_id, currency_id, exchange_rate, amount,
                                           base_amount, tax_amount, partner_type, partner_id)
                VALUES (:cid, :entry, :line_no, :account, :branch, :currency, 1, :amount,
                        :amount, 0, :partner_type, :partner_id)
                """
            ),
            {
                "cid": company_id,
                "entry": entry_id,
                "line_no": line_no,
                "account": account_id,
                "branch": branch_id,
                "currency": currency_id,
                "amount": amount,
                "partner_type": partner_type if with_partner else None,
                "partner_id": partner_id if with_partner else None,
            },
        )
    # Draft first, lines, then posted — the order the engine itself writes in, because
    # `kernel_block_posted_line_mutation` refuses a line against an entry that is already
    # posted. A fixture that inserted a posted header and then its legs would be taking a route
    # the product cannot take.
    conn.execute(
        text("UPDATE journal_entries SET status = 'posted' WHERE id = :entry"),
        {"entry": entry_id},
    )
    document_id = conn.execute(
        text(
            """
            INSERT INTO partner_documents (company_id, role, kind, number, doc_type,
                                           transaction_type, partner_id, journal_entry_id,
                                           document_date, currency_id, exchange_rate, branch_id,
                                           tax_mode, control_account_id, description, net_amount,
                                           tax_amount, total_amount, base_total_amount,
                                           open_amount, direction, status)
            VALUES (:cid, CAST(:role AS partner_role), 'invoice', :number, :doc_type, 'INV',
                    :partner, :entry, CURRENT_DATE, :currency, 1, :branch, 'exclusive',
                    :control, 'Back-fill fixture', 1000, 0, 1000, 1000, 1000, :direction,
                    'posted')
            RETURNING id
            """
        ),
        {
            "cid": company_id,
            "role": role,
            "number": number,
            "doc_type": doc_type,
            "partner": partner_id,
            "entry": entry_id,
            "currency": currency_id,
            "branch": branch_id,
            "control": control_account_id,
            "direction": direction,
        },
    ).scalar_one()
    conn.execute(
        text(
            """
            INSERT INTO partner_document_lines (company_id, document_id, line_no, quantity,
                                                unit_price, gl_account_id, branch_id,
                                                net_amount, tax_amount, gross_amount)
            VALUES (:cid, :doc, 1, 1, 1000, :account, :branch, 1000, 0, 1000)
            """
        ),
        {
            "cid": company_id,
            "doc": document_id,
            "account": contra_account_id,
            "branch": branch_id,
        },
    )
    return document_id


def _provision_pre_orders_tenant(engine: Engine) -> dict[str, int]:
    company_id = _provision_pre_p6_tenant(engine, company_name="Pre-orders Ltd")
    with engine.connect() as conn:
        branch_id = conn.execute(
            text("SELECT id FROM branches WHERE company_id = :cid"), {"cid": company_id}
        ).scalar_one()
        currency_id = conn.execute(
            text("SELECT id FROM currencies WHERE company_id = :cid"), {"cid": company_id}
        ).scalar_one()
        accounts = {
            row.code: row.id
            for row in conn.execute(
                text("SELECT code, id FROM gl_accounts WHERE company_id = :cid"),
                {"cid": company_id},
            )
        }
        year_id = conn.execute(
            text(
                "INSERT INTO fiscal_years (company_id, name, start_date, end_date, status) "
                "VALUES (:cid, 'FY', date_trunc('year', CURRENT_DATE)::date, "
                "(date_trunc('year', CURRENT_DATE) + interval '1 year - 1 day')::date, 'open') "
                "RETURNING id"
            ),
            {"cid": company_id},
        ).scalar_one()
        period_id = conn.execute(
            text(
                "INSERT INTO accounting_periods (company_id, fiscal_year_id, period_no, name, "
                "start_date, end_date, status) VALUES (:cid, :year, 1, 'P1', "
                "date_trunc('year', CURRENT_DATE)::date, "
                "(date_trunc('year', CURRENT_DATE) + interval '1 year - 1 day')::date, 'open') "
                "RETURNING id"
            ),
            {"cid": company_id, "year": year_id},
        ).scalar_one()
        customer_id = conn.execute(
            text(
                "INSERT INTO partners (company_id, name, customer_code, is_customer) "
                "VALUES (:cid, 'A customer', 'CUST001', true) RETURNING id"
            ),
            {"cid": company_id},
        ).scalar_one()
        supplier_id = conn.execute(
            text(
                "INSERT INTO partners (company_id, name, supplier_code, is_supplier) "
                "VALUES (:cid, 'A supplier', 'SUPP001', true) RETURNING id"
            ),
            {"cid": company_id},
        ).scalar_one()

    # A real transaction, not the fixture's AUTOCOMMIT connection: `trg_journal_lines_balanced`
    # is a DEFERRABLE INITIALLY DEFERRED constraint trigger, so under autocommit it would fire
    # after the first leg — with the entry one-sided — and refuse a posting that is perfectly
    # balanced by the time both legs are in.
    txn_engine = create_engine(ADMIN_URL.set(database=BACKFILL_DB))
    with txn_engine.begin() as conn:
        ar_document = _post_a_document(
            conn,
            company_id=company_id,
            period_id=period_id,
            branch_id=branch_id,
            currency_id=currency_id,
            partner_id=customer_id,
            partner_type="customer",
            control_account_id=accounts["1200"],
            contra_account_id=accounts["5100"],
            role="ar",
            number="INV-000001",
            doc_type="ARIN",
            direction=1,
        )
        ap_document = _post_a_document(
            conn,
            company_id=company_id,
            period_id=period_id,
            branch_id=branch_id,
            currency_id=currency_id,
            partner_id=supplier_id,
            partner_type="supplier",
            control_account_id=accounts["2100"],
            contra_account_id=accounts["5100"],
            role="ap",
            number="SIN-000001",
            doc_type="APIN",
            direction=-1,
        )
    txn_engine.dispose()
    return {"company_id": company_id, "ar": ar_document, "ap": ap_document}


def test_the_role_backfill_runs_on_posted_document_lines(pre_orders_engine: Engine) -> None:
    """A tenant with posted AR and AP documents comes out of the upgrade with every line
    carrying its own document's role — and with the constraint that keeps it that way."""
    ids = _provision_pre_orders_tenant(pre_orders_engine)
    url = ADMIN_URL.set(database=BACKFILL_DB).render_as_string(hide_password=False)

    _alembic(url, "head")

    with pre_orders_engine.connect() as conn:
        roles = {
            row.document_id: row.role
            for row in conn.execute(
                text(
                    "SELECT document_id, role FROM partner_document_lines "
                    "WHERE company_id = :cid"
                ),
                {"cid": ids["company_id"]},
            )
        }
        assert roles == {ids["ar"]: "ar", ids["ap"]: "ap"}

        # NOT NULL, so a row the update missed would have failed the upgrade rather than
        # arriving here as a silent null.
        nullable = conn.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'partner_document_lines' AND column_name = 'role'"
            )
        ).scalar_one()
        assert nullable == "NO"

        # And the copy cannot drift: the role is a foreign key into the document's own
        # (company_id, id, role), so a line claiming the other role has no row to reference.
        with pytest.raises(Exception) as refused:  # noqa: B017 - the DB error is the point
            conn.execute(
                text(
                    "UPDATE partner_document_lines SET role = 'ap' WHERE document_id = :doc"
                ),
                {"doc": ids["ar"]},
            )
        assert "fk_partner_document_lines_document_role" in str(refused.value)


def test_the_order_link_check_holds_after_the_upgrade(pre_orders_engine: Engine) -> None:
    """The other half of decision 1's declarative rule: with `role` in place, an AR line naming
    a purchase order is refused by a plain CHECK, no trigger involved."""
    ids = _provision_pre_orders_tenant(pre_orders_engine)
    url = ADMIN_URL.set(database=BACKFILL_DB).render_as_string(hide_password=False)

    _alembic(url, "head")

    with pre_orders_engine.connect() as conn:
        constraints = {
            row.conname
            for row in conn.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'partner_document_lines'::regclass"
                )
            )
        }
        assert "ck_partner_document_lines_order_link_matches_role" in constraints
        assert "fk_partner_document_lines_sales_order_line" in constraints
        assert "fk_partner_document_lines_purchase_order_line" in constraints
        # `order_line_id` is gone: one column with two meanings and no foreign key it could
        # carry, replaced by two that each have one.
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'partner_document_lines' "
                    "AND column_name = 'order_line_id'"
                )
            ).scalar_one()
            == 0
        )
        assert ids["ar"]
