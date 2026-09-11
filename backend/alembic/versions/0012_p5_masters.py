"""P5 step 1 — inventory masters: units of measure, items, barcodes, warehouses, the
inventory transaction types and the inventory keys on `gl_settings`.

Revision ID: 0012_p5_masters
Revises: 0011_p4_doc_txn_type
Create Date: 2026-09-11

No quantity or cost column appears anywhere in this revision, and none ever will: from the
next one on, quantities and values are derived from `stock_moves`. What lands here is the
vocabulary the move ledger needs — a unit with a factor to its category's base, an item with
a base unit, a warehouse inside exactly one branch, and the two INV control accounts only the
inventory module may post to.

`journal_lines.item_id` has existed since P2 as an unconstrained dimension because there was
no `items` table to point at. There is now, so it gets the same composite foreign key and
index every other dimension on the line carries.

Existing tenants are back-filled: the Stock in Transit and Opening Balance Suspense accounts,
the five `gl_settings` account keys plus the negative-stock policy and default warehouse, the
four UoM categories with their base units, the Main and in-transit warehouses, the six
inventory transaction types, the four document sequences, and the full permission list on
every system Administrator role. `tests/test_p5_backfill.py` asserts every one of them.

Two things the back-fill deliberately does not do:

**It will not mark an inventory account that already has journal lines.** Marking 1300 as an
INV control account is a permanent narrowing — only `module='inv'` may post to it, and every
line on it must carry an item. A tenant that has already journalled against 1300 has lines
that break both rules, and posted lines are append-only, so they cannot be fixed. Those
accounts stay uncontrolled, their `gl_settings` key stays NULL so inventory refuses to start
rather than posting into an unguarded account, and the company id is printed in the upgrade
output. See `_mark_inventory_control_accounts`.

**It assumes `rw_sme_v1` is the only chart-of-accounts template.** Every account this
back-fill resolves — 1300, 1350, 5100, 5200, 3400, and the contra accounts on the transaction
types — is found by *code*, and those codes are `rw_sme_v1`'s. `companies.coa_template` is
`rw_sme_v1` for every tenant today (§8 Q2 is still open on the template itself), so the
assumption holds; the day a second template ships, this revision is already applied and a new
one has to map the same keys for it. Recorded here so that is a known task rather than a
discovery.
"""

import json

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0012_p5_masters"
down_revision = "0011_p4_doc_txn_type"
branch_labels = None
depends_on = None

item_type = postgresql.ENUM(
    "stock", "service", "non_stock", name="item_type", create_type=False
)
negative_stock_policy = postgresql.ENUM(
    "block", "allow", name="negative_stock_policy", create_type=False
)
inventory_txn_kind = postgresql.ENUM(
    "adjustment_in",
    "adjustment_out",
    "revaluation",
    "transfer",
    "count_variance",
    "opening_balance",
    name="inventory_txn_kind",
    create_type=False,
)

ENUM_TYPES = (item_type, negative_stock_policy, inventory_txn_kind)

TENANT_TABLES = ("uom_categories", "uoms", "items", "item_barcodes", "warehouses")

MONEY = sa.Numeric(20, 6)
QUANTITY = sa.Numeric(20, 6)
FACTOR = sa.Numeric(20, 10)

INVENTORY_MODULE = "inv"

# (code, name, class, parent code, control type) — added to `rw_sme_v1` by this phase.
NEW_ACCOUNTS = (
    ("1350", "Stock in Transit", "asset", "1100", "inventory"),
    ("3400", "Opening Balance Suspense", "equity", "3000", None),
)

# (settings column, account code). Split in two because the two control keys may only be
# filled with an account this migration was actually able to mark as an INV control account
# — see `_mark_inventory_control_accounts`.
SETTINGS_CONTROL_ACCOUNTS = (
    ("inventory_account_id", "1300"),
    ("inventory_in_transit_account_id", "1350"),
)
SETTINGS_CONTRA_ACCOUNTS = (
    ("inventory_adjustment_account_id", "5200"),
    ("stock_count_variance_account_id", "5200"),
    ("cogs_account_id", "5100"),
)
SETTINGS_ACCOUNTS = (*SETTINGS_CONTROL_ACCOUNTS, *SETTINGS_CONTRA_ACCOUNTS)

# (category code, name, base unit code, base unit name, base unit decimals)
UOM_CATEGORIES = (
    ("COUNT", "Count", "EA", "Each", 0),
    ("WEIGHT", "Weight", "KG", "Kilogram", 3),
    ("VOLUME", "Volume", "L", "Litre", 3),
    ("LENGTH", "Length", "M", "Metre", 2),
)

# (code, name, kind, contra account code)
INVENTORY_TRANSACTION_TYPES = (
    ("ADJIN", "Adjustment in", "adjustment_in", "5200"),
    ("ADJOUT", "Adjustment out", "adjustment_out", "5200"),
    ("REVAL", "Stock revaluation", "revaluation", "5200"),
    ("TRF", "Warehouse transfer", "transfer", "1350"),
    ("CNTV", "Count variance", "count_variance", "5200"),
    ("OPEN", "Opening stock", "opening_balance", "3400"),
)

# (doc_type, prefix) — the same shape P4 gave the AR/AP documents.
DOCUMENT_SEQUENCES = (
    ("INAJ", "ADJ-"),
    ("INJN", "IJN-"),
    ("INTR", "TRF-"),
    ("INCT", "CNT-"),
)

# The two constants P5 adds. Only these are removed on downgrade: the rest of the list below
# predates this revision, so taking them away would not be an inverse, it would be damage.
NEW_PERMISSIONS = ("inv:count_process", "inv:item_rename")

# `app.core.permissions.ALL_PERMISSIONS` frozen as of this revision (architecture rule 10: a
# migration is a historical record and may not change when the application changes). The
# Administrator role stores its permission list as *data*, so a system role provisioned before
# this revision holds whatever the constant said on the day it was seeded — P2's list for a P2
# tenant, P4's for a P4 one. Back-filling only P5's two constants would leave those tenants
# permanently short of everything they had already missed. The whole list is unioned in.
#
# `tests/test_p5_backfill.py` compares the result against the *live* constant, so the day P6
# adds a permission without its own back-fill, that test says so.
ALL_PERMISSIONS_AT_0012 = (
    "users:create",
    "users:read",
    "users:update",
    "users:delete",
    "users:manage_roles",
    "roles:create",
    "roles:read",
    "roles:update",
    "roles:delete",
    "roles:manage_permissions",
    "company:read",
    "company:update",
    "accounting_periods:manage",
    "accounting_periods:reopen",
    "common:setup_currencies",
    "common:setup_taxes",
    "common:setup_branches",
    "gl:setup_manage",
    "gl:journal_post",
    "gl:reports_view",
    "projects:read",
    "projects:manage",
    "ar:setup_manage",
    "ar:transactions_post",
    "ar:reports_view",
    "ar:writeoff_approve",
    "ar:credit_limit_override",
    "ap:setup_manage",
    "ap:transactions_post",
    "ap:reports_view",
    "ap:credit_limit_override",
    "inv:setup_manage",
    "inv:transactions_adjust",
    "inv:reports_view",
    "inv:count_process",
    "inv:item_rename",
    "oe:setup_manage",
    "oe:sales_orders_manage",
    "oe:purchase_orders_manage",
    "oe:grv_process",
    "oe:reports_view",
    "reporting:financial_statements_view",
    "reporting:financial_statements_generate",
    "reporting:templates_manage",
    "reporting:schedules_manage",
    "reporting:bank_reconciliation_manage",
    "reporting:ar_aging_view",
    "reporting:ap_aging_view",
    "reporting:gl_advanced_view",
    "reporting:comparative_analysis",
    "reporting:cash_flow_view",
    "reporting:trial_balance_view",
    "reporting:inventory_valuation_view",
    "reporting:dashboard_view",
    "reporting:export",
    "bom:setup_manage",
    "bom:manufacturing_create",
    "bom:manufacturing_process",
    "bom:reports_view",
    "bom:mrp_run",
    "pos:setup_manage",
    "pos:till_operate",
    "pos:till_manage",
    "pos:sales_create",
    "pos:returns_process",
    "pos:reports_view",
    "pos:reconcile",
)


def _audit_columns() -> list[sa.Column]:
    now = sa.func.now()
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_by", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    ]


def _company_column() -> sa.Column:
    return sa.Column(
        "company_id",
        sa.BigInteger(),
        sa.ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )


def _tenant_fk(
    name: str, local: str, table: str, remote: str = "id", ondelete: str = "RESTRICT"
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["company_id", local],
        [f"{table}.company_id", f"{table}.{remote}"],
        name=name,
        ondelete=ondelete,
    )


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in ENUM_TYPES:
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "uom_categories",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_uom_categories_company_id_id"),
        sa.UniqueConstraint("company_id", "code", name="uq_uom_categories_company_code"),
    )
    op.create_index("ix_uom_categories_company_id", "uom_categories", ["company_id"])

    op.create_table(
        "uoms",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("factor_to_base", FACTOR, nullable=False, server_default="1"),
        sa.Column("decimal_places", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("is_base", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_uoms_company_id_id"),
        sa.UniqueConstraint("company_id", "code", name="uq_uoms_company_code"),
        sa.UniqueConstraint("company_id", "category_id", "id", name="uq_uoms_company_category_id"),
        _tenant_fk("fk_uoms_category", "category_id", "uom_categories"),
        sa.CheckConstraint("factor_to_base > 0", name=op.f("ck_uoms_factor_positive")),
        sa.CheckConstraint(
            "NOT is_base OR factor_to_base = 1", name=op.f("ck_uoms_base_factor_is_one")
        ),
        sa.CheckConstraint(
            "decimal_places BETWEEN 0 AND 6", name=op.f("ck_uoms_decimal_places_range")
        ),
    )
    op.create_index("ix_uoms_company_id", "uoms", ["company_id"])
    # Exactly one base unit per category — the arithmetic has a single anchor or it has none.
    op.create_index(
        "uq_uoms_company_category_base",
        "uoms",
        ["company_id", "category_id"],
        unique=True,
        postgresql_where=sa.text("is_base"),
    )

    op.create_table(
        "items",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("item_type", item_type, nullable=False, server_default="stock"),
        sa.Column("uom_category_id", sa.BigInteger(), nullable=False),
        sa.Column("base_uom_id", sa.BigInteger(), nullable=False),
        sa.Column("inventory_account_id", sa.BigInteger()),
        sa.Column("cogs_account_id", sa.BigInteger()),
        sa.Column("sales_account_id", sa.BigInteger()),
        sa.Column("default_sales_tax_code_id", sa.BigInteger()),
        sa.Column("default_purchase_tax_code_id", sa.BigInteger()),
        sa.Column("selling_price", MONEY, nullable=False, server_default="0"),
        sa.Column(
            "price_includes_tax", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_items_company_id_id"),
        sa.UniqueConstraint("company_id", "code", name="uq_items_company_code"),
        _tenant_fk("fk_items_uom_category", "uom_category_id", "uom_categories"),
        # Three columns: the base unit must be a unit *of the item's own category*.
        sa.ForeignKeyConstraint(
            ["company_id", "uom_category_id", "base_uom_id"],
            ["uoms.company_id", "uoms.category_id", "uoms.id"],
            name="fk_items_base_uom",
            ondelete="RESTRICT",
        ),
        _tenant_fk("fk_items_inventory_account", "inventory_account_id", "gl_accounts"),
        _tenant_fk("fk_items_cogs_account", "cogs_account_id", "gl_accounts"),
        _tenant_fk("fk_items_sales_account", "sales_account_id", "gl_accounts"),
        _tenant_fk("fk_items_default_sales_tax_code", "default_sales_tax_code_id", "tax_codes"),
        _tenant_fk(
            "fk_items_default_purchase_tax_code", "default_purchase_tax_code_id", "tax_codes"
        ),
        sa.CheckConstraint("selling_price >= 0", name=op.f("ck_items_selling_price_not_negative")),
    )
    op.create_index("ix_items_company_id", "items", ["company_id"])
    op.create_index("ix_items_company_name", "items", ["company_id", "name"])

    op.create_table(
        "item_barcodes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("barcode", sa.String(50), nullable=False),
        sa.Column("uom_id", sa.BigInteger(), nullable=False),
        sa.Column("pack_quantity", QUANTITY, nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "barcode", name="uq_item_barcodes_company_barcode"),
        _tenant_fk("fk_item_barcodes_item", "item_id", "items", ondelete="CASCADE"),
        _tenant_fk("fk_item_barcodes_uom", "uom_id", "uoms"),
        sa.CheckConstraint(
            "pack_quantity > 0", name=op.f("ck_item_barcodes_pack_quantity_positive")
        ),
    )
    op.create_index("ix_item_barcodes_company_id", "item_barcodes", ["company_id"])
    op.create_index("ix_item_barcodes_company_item", "item_barcodes", ["company_id", "item_id"])

    op.create_table(
        "warehouses",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("branch_id", sa.BigInteger(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_in_transit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_warehouses_company_id_id"),
        sa.UniqueConstraint("company_id", "code", name="uq_warehouses_company_code"),
        _tenant_fk("fk_warehouses_branch", "branch_id", "branches"),
        sa.CheckConstraint(
            "NOT (is_default AND is_in_transit)",
            name=op.f("ck_warehouses_in_transit_is_not_default"),
        ),
    )
    op.create_index("ix_warehouses_company_id", "warehouses", ["company_id"])
    op.create_index(
        "uq_warehouses_company_default",
        "warehouses",
        ["company_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    # One in-transit warehouse per company (decision 6) — a second one would split the
    # in-transit balance that has to equal the in-transit account.
    op.create_index(
        "uq_warehouses_company_in_transit",
        "warehouses",
        ["company_id"],
        unique=True,
        postgresql_where=sa.text("is_in_transit"),
    )

    # --- gl_transaction_types gains a kind ------------------------------------------------
    op.add_column("gl_transaction_types", sa.Column("kind", inventory_txn_kind))
    # Bare name: the metadata naming convention supplies the `ck_<table>_` prefix.
    op.create_check_constraint(
        "inventory_type_has_a_kind",
        "gl_transaction_types",
        "module <> 'inv' OR kind IS NOT NULL",
    )

    # --- gl_settings gains the inventory defaults -----------------------------------------
    for column, _ in SETTINGS_ACCOUNTS:
        op.add_column("gl_settings", sa.Column(column, sa.BigInteger()))
        op.create_foreign_key(
            f"fk_gl_settings_{column.removesuffix('_id')}",
            "gl_settings",
            "gl_accounts",
            ["company_id", column],
            ["company_id", "id"],
            ondelete="RESTRICT",
        )
    op.add_column(
        "gl_settings",
        sa.Column(
            "negative_stock_policy",
            negative_stock_policy,
            nullable=False,
            server_default="block",
        ),
    )
    op.add_column("gl_settings", sa.Column("default_warehouse_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_gl_settings_default_warehouse",
        "gl_settings",
        "warehouses",
        ["company_id", "default_warehouse_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )

    # --- journal_lines.item_id becomes a real dimension -----------------------------------
    # It has been on the line since P2 with nothing to point at. `items` exists now, so it
    # gets the constraint and the index the other dimensions have had all along.
    op.create_foreign_key(
        "fk_journal_lines_item",
        "journal_lines",
        "items",
        ["company_id", "item_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_journal_lines_company_item", "journal_lines", ["company_id", "item_id"])

    _backfill_existing_tenants()

    # --- Row Level Security (ADR-01) ------------------------------------------------------
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (company_id = app_current_company_id() OR app_platform_mode())
            WITH CHECK (company_id = app_current_company_id() OR app_platform_mode())
            """
        )


def _backfill_existing_tenants() -> None:
    """A tenant provisioned before P5 comes out of `alembic upgrade head` able to post stock.

    The P4 lesson applies unchanged: a NULL default here does not fail at migration time, it
    fails at whichever posting first needs it, long after anyone can connect the two.
    """
    for code, name, class_, parent_code, control in NEW_ACCOUNTS:
        op.execute(
            sa.text(
                """
                INSERT INTO gl_accounts
                    (company_id, code, name, class, parent_id, is_postable, is_control,
                     control_type, is_active)
                SELECT p.company_id, :code, :name, CAST(:class AS account_class), p.id,
                       true, :is_control, CAST(:control AS gl_control_type), true
                  FROM gl_accounts p
                 WHERE p.code = :parent_code
                   AND NOT EXISTS (
                       SELECT 1 FROM gl_accounts existing
                        WHERE existing.company_id = p.company_id AND existing.code = :code
                   )
                """
            ).bindparams(
                code=code,
                name=name,
                **{"class": class_},
                parent_code=parent_code,
                is_control=control is not None,
                control=control,
            )
        )

    _mark_inventory_control_accounts()

    # Contra keys map by code alone: an adjustment or COGS account is an ordinary postable
    # account, so there is nothing about it that the upgrade could fail to establish.
    for column, account_code in SETTINGS_CONTRA_ACCOUNTS:
        op.execute(
            sa.text(
                f"""
                UPDATE gl_settings s
                   SET {column} = a.id
                  FROM gl_accounts a
                 WHERE a.company_id = s.company_id
                   AND a.code = :account_code
                   AND s.{column} IS NULL
                """
            ).bindparams(account_code=account_code)
        )

    # The two control keys additionally require the account to *be* an INV control account.
    # Where `_mark_inventory_control_accounts` had to leave one unmarked, the key stays NULL
    # and the first inventory posting fails loudly with `gl_setting_missing` — which is the
    # right failure. Pointing the setting at an unmarked account would be the wrong one: the
    # guard only fires on accounts carrying a control type, so inventory would post into an
    # account nothing was protecting, and the item-required rule would never run.
    for column, account_code in SETTINGS_CONTROL_ACCOUNTS:
        op.execute(
            sa.text(
                f"""
                UPDATE gl_settings s
                   SET {column} = a.id
                  FROM gl_accounts a
                 WHERE a.company_id = s.company_id
                   AND a.code = :account_code
                   AND a.control_type = 'inventory'
                   AND s.{column} IS NULL
                """
            ).bindparams(account_code=account_code)
        )

    for category_code, category_name, unit_code, unit_name, decimals in UOM_CATEGORIES:
        op.execute(
            sa.text(
                """
                INSERT INTO uom_categories (company_id, code, name, is_active)
                SELECT c.id, :code, :name, true
                  FROM companies c
                 WHERE NOT EXISTS (
                     SELECT 1 FROM uom_categories u
                      WHERE u.company_id = c.id AND u.code = :code
                 )
                """
            ).bindparams(code=category_code, name=category_name)
        )
        op.execute(
            sa.text(
                """
                INSERT INTO uoms (company_id, category_id, code, name, factor_to_base,
                                  decimal_places, is_base, is_active)
                SELECT k.company_id, k.id, :unit_code, :unit_name, 1, :decimals, true, true
                  FROM uom_categories k
                 WHERE k.code = :category_code
                   AND NOT EXISTS (
                       SELECT 1 FROM uoms u
                        WHERE u.company_id = k.company_id AND u.code = :unit_code
                   )
                """
            ).bindparams(
                category_code=category_code,
                unit_code=unit_code,
                unit_name=unit_name,
                decimals=decimals,
            )
        )

    # Main and the in-transit warehouse, both on the tenant's main branch.
    for code, name, is_default, is_in_transit in (
        ("MAIN", "Main Warehouse", True, False),
        ("TRANSIT", "Stock in Transit", False, True),
    ):
        op.execute(
            sa.text(
                """
                INSERT INTO warehouses
                    (company_id, code, name, branch_id, is_default, is_in_transit, is_active)
                SELECT b.company_id, :code, :name, b.id, :is_default, :is_in_transit, true
                  FROM branches b
                 WHERE b.is_main
                   AND NOT EXISTS (
                       SELECT 1 FROM warehouses w
                        WHERE w.company_id = b.company_id
                          AND (w.code = :code OR (w.is_in_transit AND :is_in_transit)
                               OR (w.is_default AND :is_default))
                   )
                """
            ).bindparams(
                code=code, name=name, is_default=is_default, is_in_transit=is_in_transit
            )
        )
    op.execute(
        """
        UPDATE gl_settings s
           SET default_warehouse_id = w.id
          FROM warehouses w
         WHERE w.company_id = s.company_id
           AND w.is_default
           AND s.default_warehouse_id IS NULL
        """
    )

    for code, name, kind, account_code in INVENTORY_TRANSACTION_TYPES:
        op.execute(
            sa.text(
                """
                INSERT INTO gl_transaction_types
                    (company_id, module, code, name, kind, default_gl_account_id, is_active)
                SELECT c.id, :module, :code, :name, CAST(:kind AS inventory_txn_kind),
                       (SELECT a.id FROM gl_accounts a
                         WHERE a.company_id = c.id AND a.code = :account_code),
                       true
                  FROM companies c
                 WHERE NOT EXISTS (
                     SELECT 1 FROM gl_transaction_types t
                      WHERE t.company_id = c.id AND t.module = :module AND t.code = :code
                 )
                """
            ).bindparams(
                module=INVENTORY_MODULE,
                code=code,
                name=name,
                kind=kind,
                account_code=account_code,
            )
        )

    for doc_type, prefix in DOCUMENT_SEQUENCES:
        op.execute(
            sa.text(
                """
                INSERT INTO document_sequences
                    (company_id, branch_id, doc_type, prefix, next_number)
                SELECT c.id, NULL, :doc_type, :prefix, 1
                  FROM companies c
                 WHERE NOT EXISTS (
                     SELECT 1 FROM document_sequences s
                      WHERE s.company_id = c.id AND s.doc_type = :doc_type
                        AND s.branch_id IS NULL
                 )
                """
            ).bindparams(doc_type=doc_type, prefix=prefix)
        )

    _backfill_administrator_permissions()


def _mark_inventory_control_accounts() -> None:
    """Make 1300/1350 INV control accounts — but only where that is still a true statement.

    Marking an account `inventory` means two things become permanent: nothing outside
    `module='inv'` may ever post to it again, and every line on it must carry an `item_id`.
    A pre-P5 tenant that has already journalled against its inventory account has lines that
    satisfy neither. They were legal the day they were written, and posted lines are
    append-only (architecture rule 3), so they cannot be corrected into compliance. Marking
    the account anyway would hand that tenant a control account its own history contradicts,
    and P5 step 2's `assert_stock_invariants` — stock valuation == the inventory GL balance —
    could never come true for it: the balance would carry journal amounts with no moves
    behind them.

    So the rule is: mark only where the account has no journal lines at all, and name the
    companies that were skipped in the upgrade's output. Those tenants keep a working ledger
    and an inventory module that refuses to start until someone migrates their stock history
    deliberately, which is a decision for a person, not for a migration.
    """
    bind = op.get_bind()
    skipped = bind.execute(
        sa.text(
            """
            SELECT a.company_id, a.code, count(l.id) AS line_count
              FROM gl_accounts a
              JOIN journal_lines l ON l.gl_account_id = a.id
             WHERE a.code IN ('1300', '1350')
               AND a.control_type IS NULL
             GROUP BY a.company_id, a.code
             ORDER BY a.company_id, a.code
            """
        )
    ).all()
    for row in skipped:
        print(
            f"{revision}: company {row.company_id}: account {row.code} already carries "
            f"{row.line_count} journal line(s) and is NOT being marked as an inventory "
            "control account. Its gl_settings key stays NULL and inventory posting will "
            "refuse to start for this company until the account is migrated by hand."
        )

    op.execute(
        """
        UPDATE gl_accounts a
           SET is_control = true, control_type = 'inventory'
         WHERE a.code IN ('1300', '1350')
           AND a.control_type IS NULL
           AND NOT EXISTS (
               SELECT 1 FROM journal_lines l WHERE l.gl_account_id = a.id
           )
        """
    )


def _backfill_administrator_permissions() -> None:
    """Union the whole permission list into every system Administrator role.

    The list is stored data, so a role seeded at P2 still holds P2's list — back-filling only
    P5's two constants would leave it permanently short of everything it had already missed
    (P4's credit-limit overrides, P3's project permissions, and so on). The union is
    idempotent: a role that already holds the list is not rewritten, and one that holds extras
    keeps them.
    """
    op.execute(
        sa.text(
            """
            UPDATE roles r
               SET permissions = r.permissions || (
                     SELECT coalesce(jsonb_agg(missing.value), '[]'::jsonb)
                       FROM jsonb_array_elements(CAST(:all_permissions AS jsonb))
                            AS missing(value)
                      WHERE NOT (r.permissions @> jsonb_build_array(missing.value))
                   )
             WHERE r.is_system
               AND r.name = 'Administrator'
               AND NOT (r.permissions @> CAST(:all_permissions AS jsonb))
            """
        ).bindparams(all_permissions=json.dumps(list(ALL_PERMISSIONS_AT_0012)))
    )


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    for permission in NEW_PERMISSIONS:
        op.execute(
            sa.text(
                "UPDATE roles SET permissions = permissions - CAST(:permission AS text) "
                "WHERE is_system AND name = 'Administrator'"
            ).bindparams(permission=permission)
        )
    op.execute(
        sa.text("DELETE FROM document_sequences WHERE doc_type = ANY(:types)").bindparams(
            types=[doc_type for doc_type, _ in DOCUMENT_SEQUENCES]
        )
    )
    op.execute(
        sa.text("DELETE FROM gl_transaction_types WHERE module = :module").bindparams(
            module=INVENTORY_MODULE
        )
    )
    op.drop_index("ix_journal_lines_company_item", table_name="journal_lines")
    op.drop_constraint("fk_journal_lines_item", "journal_lines", type_="foreignkey")
    op.drop_constraint("fk_gl_settings_default_warehouse", "gl_settings", type_="foreignkey")
    op.drop_column("gl_settings", "default_warehouse_id")
    op.drop_column("gl_settings", "negative_stock_policy")
    for column, _ in SETTINGS_ACCOUNTS:
        op.drop_constraint(
            f"fk_gl_settings_{column.removesuffix('_id')}", "gl_settings", type_="foreignkey"
        )
        op.drop_column("gl_settings", column)
    # Bare name again — `op.drop_constraint` applies the same naming convention.
    op.drop_constraint("inventory_type_has_a_kind", "gl_transaction_types", type_="check")
    op.drop_column("gl_transaction_types", "kind")
    op.drop_table("item_barcodes")
    op.drop_table("items")
    op.drop_table("warehouses")
    op.drop_table("uoms")
    op.drop_table("uom_categories")
    op.execute("DELETE FROM gl_accounts WHERE code IN ('1350', '3400')")
    bind = op.get_bind()
    for enum_type in ENUM_TYPES:
        enum_type.drop(bind, checkfirst=True)
