"""P6 step 1 — order-entry masters, settings and schema.

Revision ID: 0018_p6_masters
Revises: 0017_p5_count_enter
Create Date: 2026-09-14

The vocabulary Order Entry needs, and nothing that posts. No order table, no GRN and no
landed-cost document appears here: those arrive with the services that write them, in steps
2 to 4. What lands now is the shape the rest of the phase keys against.

**Item lines on partner documents** (decision 1). `partner_document_lines` grows the eight
nullable columns that turn a GL line into an item line, and one code path keeps serving both.
`order_line_id` and `grn_line_id` get **no foreign key yet** — the tables they point at do not
exist until steps 3 and 2 — and the step that creates each table adds its constraint. They are
indexed now because the derived quantities of decision 4 are exactly these joins.

**The GRN accrual is a control account** (decision 5). `2350 Goods Received Not Invoiced`,
credited by `inv` when goods are received and debited by `ap` when the invoice matches, which
is why it registers two modules where every earlier control type registered one. Every line on
it must carry an item, so `kernel_check_subledger_line` is replaced: its VN008 item rule was
written for `inventory` alone and now covers both item-bearing control types.

`1370 Landed Cost Clearing` is deliberately **not** a control account. Freight arrives as an
ordinary line on a forwarder's supplier invoice and duty as a cashbook payment to RRA; a
control account would refuse both. Its proof is arithmetic — booked minus allocated — rather
than a guard.

**Two enums gain a value**, and neither by `ALTER TYPE … ADD VALUE`. Postgres refuses to *use*
a value added in the still-open transaction ("unsafe use of new value"), and this project runs
every migration in one transaction, so the whole upgrade would abort the moment the back-fill
inserted a `grn_accrual` row. Each type is therefore rebuilt in place — rename, recreate with
the new label, retype the dependent columns, drop the old — which is transactional and leaves
the value immediately usable. The plpgsql guard function references `gl_control_type` by name
and resolves it at execution, so the rebuild does not disturb it.

Existing tenants are back-filled: the two accounts, the three `gl_settings` account keys, and
the full permission list on every system Administrator role. `tests/test_p6_backfill.py`
asserts every one of them.

What the back-fill deliberately does **not** do:

**It seeds no document sequences.** P5 seeded its four so the first adjustment would not have
to invent one, but `claim_number` calls `ensure_sequence` and creates a run on demand, and a
seeded run here would be worse than useless: `assert_ledger_invariants` walks every
`document_sequences` row a company has and asks the registry who may hold its numbers, so an
`SO` row would send the gapless check querying `sales_orders` two steps before that table
exists. The runs appear when something first claims from them.

**It assumes `rw_sme_v1` is the only chart-of-accounts template**, exactly as 0012 did and for
the same reason — 2350, 1370 and 5300 are found by code, and those codes are that template's.
Recorded so the second template is a known task rather than a discovery.
"""

import json

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0018_p6_masters"
down_revision = "0017_p5_count_enter"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(20, 6)
QUANTITY = sa.Numeric(20, 6)

TENANT_TABLES = ("item_kit_components",)

backorder_policy = postgresql.ENUM("allow", "block", name="backorder_policy", create_type=False)

# --- Enum rebuilds ---------------------------------------------------------------------------
# (type name, labels after this revision, [(table, column), …] using it)
CONTROL_TYPE_LABELS = ("bank", "cash", "ar", "ap", "inventory", "grn_accrual")
CONTROL_TYPE_COLUMNS = (
    ("gl_accounts", "control_type"),
    ("control_account_modules", "control_type"),
)
ITEM_TYPE_LABELS = ("stock", "service", "non_stock", "kit")
ITEM_TYPE_COLUMNS = (("items", "item_type"),)

# (code, name, class, parent code, control type) — added to `rw_sme_v1` by this phase.
NEW_ACCOUNTS = (
    ("1370", "Landed Cost Clearing", "asset", "1100", None),
    ("2350", "Goods Received Not Invoiced", "liability", "2000", "grn_accrual"),
)

#: (settings column, account code). All three are ordinary code lookups: the PPV and clearing
#: accounts are plain postable accounts, and the accrual account is one this revision creates
#: itself already carrying its control type, so there is no pre-existing balance to worry
#: about — which is exactly what made 0012's inventory marking delicate and this one not.
SETTINGS_ACCOUNTS = (
    ("grn_accrual_account_id", "2350"),
    ("purchase_price_variance_account_id", "5300"),
    ("landed_cost_clearing_account_id", "1370"),
)

REGISTRY_ROWS = (("grn_accrual", "inv"), ("grn_accrual", "ap"))

NEW_PERMISSIONS = ("oe:landed_cost_post",)

# `app.core.permissions.ALL_PERMISSIONS` frozen as of this revision (architecture rule 10: a
# migration must not drift with the constants it was written against).
ALL_PERMISSIONS_AT_0018 = (
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
    "inv:count_enter",
    "inv:count_process",
    "inv:item_rename",
    "oe:setup_manage",
    "oe:sales_orders_manage",
    "oe:purchase_orders_manage",
    "oe:grv_process",
    "oe:reports_view",
    "oe:landed_cost_post",
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

#: `kernel_check_subledger_line` with the item rule widened from `inventory` to every
#: item-bearing control type. The rest of the function is 0009's, unchanged: the registry
#: lookup, the VN007 module check and the AR/AP partner rule.
SUBLEDGER_GUARD_FUNCTION = """
    CREATE OR REPLACE FUNCTION kernel_check_subledger_line() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        v_control gl_control_type;
        v_code text;
        v_module text;
        v_owned boolean;
        v_expected_partner text;
    BEGIN
        SELECT a.control_type, a.code INTO v_control, v_code
          FROM gl_accounts a WHERE a.id = NEW.gl_account_id;
        IF v_control IS NULL THEN
            RETURN NEW;
        END IF;

        SELECT EXISTS (
            SELECT 1 FROM control_account_modules m WHERE m.control_type = v_control
        ) INTO v_owned;
        IF NOT v_owned THEN
            RETURN NEW;  -- not module-owned (bank/cash); the engine's event rules apply
        END IF;

        SELECT e.module INTO v_module FROM journal_entries e WHERE e.id = NEW.entry_id;
        IF NOT EXISTS (
            SELECT 1 FROM control_account_modules m
             WHERE m.control_type = v_control AND m.module = v_module
        ) THEN
            RAISE EXCEPTION
                'account % is the % control account; module % may not post to it',
                v_code, upper(v_control::text), coalesce(v_module, '<null>')
                USING ERRCODE = 'VN007';
        END IF;

        v_expected_partner := CASE v_control
            WHEN 'ar' THEN 'customer' WHEN 'ap' THEN 'supplier' ELSE NULL END;
        IF v_expected_partner IS NOT NULL
           AND (NEW.partner_id IS NULL OR NEW.partner_type IS DISTINCT FROM v_expected_partner)
        THEN
            RAISE EXCEPTION 'account % requires a % on the line', v_code, v_expected_partner
                USING ERRCODE = 'VN008';
        END IF;
        IF v_control IN ('inventory', 'grn_accrual') AND NEW.item_id IS NULL THEN
            RAISE EXCEPTION 'account % requires an item on the line', v_code
                USING ERRCODE = 'VN008';
        END IF;
        RETURN NEW;
    END
    $$
"""

#: 0009's version, restored on downgrade — item required for `inventory` only.
PREVIOUS_GUARD_FUNCTION = SUBLEDGER_GUARD_FUNCTION.replace(
    "IF v_control IN ('inventory', 'grn_accrual') AND NEW.item_id IS NULL THEN",
    "IF v_control = 'inventory' AND NEW.item_id IS NULL THEN",
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


def _rebuild_enum(name: str, labels: tuple[str, ...], columns: tuple[tuple[str, str], ...]) -> None:
    """Add a label to an existing enum in a way that works inside one transaction.

    `ALTER TYPE … ADD VALUE` is transactional in Postgres 12+ but the new label cannot be
    *used* until that transaction commits, and this project runs the whole upgrade in one —
    so the back-fill's first `grn_accrual` insert would abort everything. Rebuilding the type
    has no such restriction.

    A server default has to come off before the retype and go back afterwards: Postgres will
    not cast an existing default expression to the new type even when the new type is a
    superset, and `items.item_type DEFAULT 'stock'` is exactly that case. The default is read
    back from the catalogue rather than passed in, so this stays correct if a later revision
    adds or changes one.
    """
    bind = op.get_bind()
    defaults: dict[tuple[str, str], str | None] = {}
    for table, column in columns:
        defaults[(table, column)] = bind.execute(
            sa.text(
                """
                SELECT pg_get_expr(d.adbin, d.adrelid)
                  FROM pg_attribute a
                  JOIN pg_class c ON c.oid = a.attrelid
                  LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                 WHERE c.relname = :table AND a.attname = :column
                   AND a.attnum > 0 AND NOT a.attisdropped
                """
            ).bindparams(table=table, column=column)
        ).scalar()
        if defaults[(table, column)] is not None:
            op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT")

    op.execute(f"ALTER TYPE {name} RENAME TO {name}_old")
    op.execute(f"CREATE TYPE {name} AS ENUM ({', '.join(repr(label) for label in labels)})")
    for table, column in columns:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {name} "
            f"USING {column}::text::{name}"
        )
    op.execute(f"DROP TYPE {name}_old")

    for (table, column), default in defaults.items():
        if default is None:
            continue
        # The catalogue renders the default already cast to the *old* type name, which no
        # longer exists; re-cast the literal to the rebuilt one.
        literal = default.split("::")[0]
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT {literal}::{name}"
        )


def upgrade() -> None:
    # --- Enums ----------------------------------------------------------------------------
    _rebuild_enum("gl_control_type", CONTROL_TYPE_LABELS, CONTROL_TYPE_COLUMNS)
    _rebuild_enum("item_type", ITEM_TYPE_LABELS, ITEM_TYPE_COLUMNS)
    backorder_policy.create(op.get_bind(), checkfirst=True)

    # --- The accrual account becomes reachable from `inv` and `ap` -------------------------
    for control_type, module in REGISTRY_ROWS:
        op.execute(
            sa.text(
                "INSERT INTO control_account_modules (control_type, module) "
                "VALUES (CAST(:control_type AS gl_control_type), :module) "
                "ON CONFLICT DO NOTHING"
            ).bindparams(control_type=control_type, module=module)
        )
    op.execute(SUBLEDGER_GUARD_FUNCTION)

    # --- items ----------------------------------------------------------------------------
    op.add_column("items", sa.Column("purchase_account_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_items_purchase_account",
        "items",
        "gl_accounts",
        ["company_id", "purchase_account_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )
    op.add_column("items", sa.Column("weight_per_base_unit", QUANTITY))
    op.create_check_constraint(
        "weight_per_base_unit_positive",
        "items",
        "weight_per_base_unit IS NULL OR weight_per_base_unit > 0",
    )

    op.create_table(
        "item_kit_components",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("kit_item_id", sa.BigInteger(), nullable=False),
        sa.Column("component_item_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity_per_kit", QUANTITY, nullable=False),
        sa.Column("line_no", sa.SmallInteger(), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint(
            "company_id", "kit_item_id", "component_item_id", name="uq_item_kit_components_pair"
        ),
        sa.UniqueConstraint(
            "company_id", "kit_item_id", "line_no", name="uq_item_kit_components_line_no"
        ),
        _tenant_fk(
            "fk_item_kit_components_kit_item", "kit_item_id", "items", ondelete="CASCADE"
        ),
        _tenant_fk(
            "fk_item_kit_components_component_item", "component_item_id", "items"
        ),
        sa.CheckConstraint(
            "quantity_per_kit > 0",
            name=op.f("ck_item_kit_components_quantity_per_kit_positive"),
        ),
        sa.CheckConstraint(
            "kit_item_id <> component_item_id",
            name=op.f("ck_item_kit_components_kit_is_not_its_own_component"),
        ),
    )
    op.create_index("ix_item_kit_components_company_id", "item_kit_components", ["company_id"])
    op.create_index(
        "ix_item_kit_components_company_kit", "item_kit_components", ["company_id", "kit_item_id"]
    )

    # --- partner_document_lines becomes able to carry an item ------------------------------
    op.create_unique_constraint(
        "uq_partner_document_lines_company_id_id",
        "partner_document_lines",
        ["company_id", "id"],
    )
    for column in (
        "item_id",
        "uom_id",
        "warehouse_id",
        "order_line_id",
        "grn_line_id",
        "returns_line_id",
        "kit_parent_line_id",
    ):
        op.add_column("partner_document_lines", sa.Column(column, sa.BigInteger()))
    op.add_column("partner_document_lines", sa.Column("base_quantity", MONEY))
    for column, target, name, ondelete in (
        ("item_id", "items", "item", "RESTRICT"),
        ("uom_id", "uoms", "uom", "RESTRICT"),
        ("warehouse_id", "warehouses", "warehouse", "RESTRICT"),
        ("returns_line_id", "partner_document_lines", "returns_line", "RESTRICT"),
        ("kit_parent_line_id", "partner_document_lines", "kit_parent_line", "CASCADE"),
    ):
        op.create_foreign_key(
            f"fk_partner_document_lines_{name}",
            "partner_document_lines",
            target,
            ["company_id", column],
            ["company_id", "id"],
            ondelete=ondelete,
        )
    # `order_line_id` and `grn_line_id` carry no constraint yet — steps 2 and 3 add one when
    # `goods_received_note_lines` and the order-line tables exist.
    op.create_check_constraint(
        "base_quantity_needs_an_item",
        "partner_document_lines",
        "base_quantity IS NULL OR item_id IS NOT NULL",
    )
    for name, column in (
        ("order_line", "order_line_id"),
        ("grn_line", "grn_line_id"),
        ("item", "item_id"),
    ):
        op.create_index(
            f"ix_partner_document_lines_{name}",
            "partner_document_lines",
            ["company_id", column],
            postgresql_where=sa.text(f"{column} IS NOT NULL"),
        )

    # --- The companion stock entry link ----------------------------------------------------
    op.add_column("partner_documents", sa.Column("stock_entry_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_partner_documents_stock_entry",
        "partner_documents",
        "journal_entries",
        ["company_id", "stock_entry_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )

    # --- gl_settings gains the order-entry defaults ----------------------------------------
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
        sa.Column("backorder_policy", backorder_policy, nullable=False, server_default="allow"),
    )

    _backfill_existing_tenants()

    # --- Row Level Security (ADR-01) -------------------------------------------------------
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
    """A tenant provisioned before P6 comes out of `alembic upgrade head` able to receive
    goods and allocate a landed cost.

    The P4 and P5 lesson, unchanged: a NULL default here does not fail at migration time, it
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

    # All three keys map by code alone. Unlike 0012's inventory marking there is nothing
    # delicate here: 2350 and 1370 are created by this revision, so no tenant can have posted
    # to either, and 5300 is an ordinary expense account that stays ordinary.
    for column, account_code in SETTINGS_ACCOUNTS:
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

    _backfill_administrator_permissions()


def _backfill_administrator_permissions() -> None:
    """The Administrator role stores its permissions as *data*, so a role seeded before this
    revision does not have `oe:landed_cost_post` and no amount of application code will give
    it one. The union goes in, not just this phase's addition — a tenant several phases old
    may be missing more than one."""
    op.execute(
        sa.text(
            """
            UPDATE roles r
               SET permissions = r.permissions || (
                   SELECT COALESCE(jsonb_agg(missing.value), '[]'::jsonb)
                     FROM jsonb_array_elements(CAST(:all_permissions AS jsonb))
                          AS missing(value)
                    WHERE NOT (r.permissions @> jsonb_build_array(missing.value))
               )
             WHERE r.is_system AND r.name = 'Administrator'
               AND NOT (r.permissions @> CAST(:all_permissions AS jsonb))
            """
        ).bindparams(all_permissions=json.dumps(list(ALL_PERMISSIONS_AT_0018)))
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

    op.drop_column("gl_settings", "backorder_policy")
    for column, _ in SETTINGS_ACCOUNTS:
        op.drop_constraint(
            f"fk_gl_settings_{column.removesuffix('_id')}", "gl_settings", type_="foreignkey"
        )
        op.drop_column("gl_settings", column)

    op.drop_constraint("fk_partner_documents_stock_entry", "partner_documents", type_="foreignkey")
    op.drop_column("partner_documents", "stock_entry_id")

    for name in ("order_line", "grn_line", "item"):
        op.drop_index(f"ix_partner_document_lines_{name}", table_name="partner_document_lines")
    op.drop_constraint("base_quantity_needs_an_item", "partner_document_lines", type_="check")
    for name in ("item", "uom", "warehouse", "returns_line", "kit_parent_line"):
        op.drop_constraint(
            f"fk_partner_document_lines_{name}", "partner_document_lines", type_="foreignkey"
        )
    for column in (
        "item_id",
        "uom_id",
        "base_quantity",
        "warehouse_id",
        "order_line_id",
        "grn_line_id",
        "returns_line_id",
        "kit_parent_line_id",
    ):
        op.drop_column("partner_document_lines", column)
    op.drop_constraint(
        "uq_partner_document_lines_company_id_id", "partner_document_lines", type_="unique"
    )

    op.drop_index("ix_item_kit_components_company_kit", table_name="item_kit_components")
    op.drop_index("ix_item_kit_components_company_id", table_name="item_kit_components")
    op.drop_table("item_kit_components")
    op.drop_constraint("weight_per_base_unit_positive", "items", type_="check")
    op.drop_column("items", "weight_per_base_unit")
    op.drop_constraint("fk_items_purchase_account", "items", type_="foreignkey")
    op.drop_column("items", "purchase_account_id")

    # The guard goes back before the registry rows and the enum label they use, or the
    # restored function would reference a label that no longer exists.
    op.execute(PREVIOUS_GUARD_FUNCTION)
    op.execute(sa.text("DELETE FROM control_account_modules WHERE control_type = 'grn_accrual'"))
    op.execute(
        sa.text("DELETE FROM gl_accounts WHERE code = ANY(:codes)").bindparams(
            codes=[code for code, *_ in NEW_ACCOUNTS]
        )
    )
    backorder_policy.drop(op.get_bind(), checkfirst=True)
    _rebuild_enum("item_type", ("stock", "service", "non_stock"), ITEM_TYPE_COLUMNS)
    _rebuild_enum(
        "gl_control_type", ("bank", "cash", "ar", "ap", "inventory"), CONTROL_TYPE_COLUMNS
    )
