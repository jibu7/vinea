"""P6 step 3 — sales and purchase orders, and the links that make their quantities derivable.

Revision ID: 0020_p6_orders
Revises: 0019_p6_posting
Create Date: 2026-09-14

Four tables, four foreign keys, one declarative role check and two views.

**No table here carries a quantity that anything fulfilled.** There is no `quantity_invoiced`
on a sales order line and no `quantity_received` on a purchase order line — those are the
columns the v4 design kept and reconciled by hand, and the two views at the bottom of this
migration are what replaces them. `sales_order_line_quantities` and
`purchase_order_line_quantities` compute ordered and fulfilled from the documents that
actually posted, so a reversal moves both by construction. Every service function in
`app.order_entry.quantities` reads a view; nothing recomputes the join in Python.

**The link columns, and the role check without a trigger.** Step 1 gave
`partner_document_lines` a single `order_line_id` meaning "the SO line for an AR document, the
PO line for an AP one" — one column with two meanings and no foreign key it could carry, since
which table it pointed at depended on the document above it. It splits here into
`sales_order_line_id` and `purchase_order_line_id`, each with a composite foreign key like
every other reference in the schema, and the rule that an AR line may not name a purchase
order becomes three declarative pieces rather than a trigger:

1. `role` is denormalised onto the line;
2. a composite foreign key `(company_id, document_id, role)` into a new unique constraint on
   `partner_documents (company_id, id, role)` holds that copy true — a line cannot claim a role
   its own document does not have, and the document's role cannot change underneath it;
3. a plain CHECK then says the obvious thing about the two link columns.

A trigger would have done the same work at the cost of being invisible to anybody reading the
table and unavailable to the planner. The denormalised column is not a cache to be reconciled;
the foreign key makes it a restatement of a fact that already exists, in the strict sense that
no pair of rows can disagree.

**The back-fill.** `role` lands nullable, is filled from each line's document, and is then made
NOT NULL. `partner_document_lines` carries no immutability trigger — posted *entries* and
*moves* do (rule 10), and this is neither — so the UPDATE runs on a real tenant rather than
only on an empty scratch database, which `make migrate-check` could never prove.
`tests/test_p6_backfill.py::test_the_role_backfill_runs_on_posted_document_lines` provisions a
tenant at 0019 with a posted AR document and a posted AP document, upgrades, and reads the
column back for both.

`goods_received_notes.purchase_order_id` and `goods_received_note_lines.purchase_order_line_id`
get the foreign keys 0019 promised them: no bare link column survives the phase.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0020_p6_orders"
down_revision = "0019_p6_posting"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(20, 6)
QUANTITY = sa.Numeric(20, 6)
RATE = sa.Numeric(20, 10)
PERCENT = sa.Numeric(20, 10)

TENANT_TABLES = (
    "sales_orders",
    "sales_order_lines",
    "purchase_orders",
    "purchase_order_lines",
)

sales_order_status = postgresql.ENUM(
    "open",
    "partially_invoiced",
    "invoiced",
    "closed",
    "cancelled",
    name="sales_order_status",
    # Created explicitly below; `create_table` must not emit a second CREATE TYPE for it.
    create_type=False,
)
purchase_order_status = postgresql.ENUM(
    "open",
    "partially_received",
    "received",
    "closed",
    "cancelled",
    name="purchase_order_status",
    create_type=False,
)
partner_role = postgresql.ENUM("ar", "ap", name="partner_role", create_type=False)
tax_mode = postgresql.ENUM("exclusive", "inclusive", name="tax_mode", create_type=False)


# --- The derived quantities (decision 4), as views ------------------------------------------
#
# `security_invoker = true` is load-bearing, not a flourish. A view without it executes with
# its *owner's* privileges, and the owner here is the migration superuser — which bypasses row
# level security, so every tenant would read every other tenant's orders through a view that
# looks entirely innocent. With it, the caller's RLS applies exactly as if they had written the
# join themselves (ADR-01). The policy linter cannot catch this: it checks tables, and a view
# is not a table.

SALES_ORDER_LINE_QUANTITIES = """
CREATE VIEW sales_order_line_quantities WITH (security_invoker = true) AS
SELECT l.company_id,
       l.id                                  AS sales_order_line_id,
       l.order_id                            AS sales_order_id,
       l.item_id,
       l.warehouse_id,
       l.base_quantity                       AS ordered,
       COALESCE((
           SELECT SUM(dl.base_quantity)
             FROM partner_document_lines dl
             JOIN partner_documents d
               ON d.company_id = dl.company_id AND d.id = dl.document_id
            WHERE dl.company_id = l.company_id
              AND dl.sales_order_line_id = l.id
              AND d.status = 'posted'
              AND d.kind = 'invoice'
       ), 0)                                 AS invoiced
  FROM sales_order_lines l
"""

PURCHASE_ORDER_LINE_QUANTITIES = """
CREATE VIEW purchase_order_line_quantities WITH (security_invoker = true) AS
SELECT l.company_id,
       l.id                                  AS purchase_order_line_id,
       l.order_id                            AS purchase_order_id,
       l.item_id,
       l.warehouse_id,
       l.base_quantity                       AS ordered,
       COALESCE((
           SELECT SUM(gl.base_quantity)
             FROM goods_received_note_lines gl
             JOIN goods_received_notes g
               ON g.company_id = gl.company_id AND g.id = gl.grn_id
            WHERE gl.company_id = l.company_id
              AND gl.purchase_order_line_id = l.id
              AND g.status <> 'reversed'
       ), 0)
     + COALESCE((
           SELECT SUM(dl.base_quantity)
             FROM partner_document_lines dl
             JOIN partner_documents d
               ON d.company_id = dl.company_id AND d.id = dl.document_id
            WHERE dl.company_id = l.company_id
              AND dl.purchase_order_line_id = l.id
              AND dl.grn_line_id IS NULL
              AND d.status = 'posted'
              AND d.kind = 'invoice'
       ), 0)                                 AS received
  FROM purchase_order_lines l
"""
# The second term of `received` is the direct purchase: a supplier invoice line that carried
# the goods itself, with no GRN behind it (decision 2's unmatched-purchase row). It is the same
# clause that receives a **service** line — a service is "received" by its invoice and never by
# a GRN (decision 4) — so one expression serves both and there is no branch on item type to
# keep in step with the catalogue. `grn_line_id IS NULL` is what stops a *matched* line being
# counted twice: those goods arrived on the receipt the first term already counted.


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


def _order_header_columns(status_enum: postgresql.ENUM, default_status: str) -> list[sa.Column]:
    """Everything a sales order and a purchase order hold in common — which is nearly all of
    it, because the two sides of order entry differ in what fulfils them, not in what they
    are."""
    return [
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("number", sa.String(30), nullable=False),
        sa.Column("partner_id", sa.BigInteger(), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("expected_date", sa.Date()),
        sa.Column("reference", sa.String(500)),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("currency_id", sa.BigInteger(), nullable=False),
        # Display only — an order never values stock. See `app/models/order_entry.py`.
        sa.Column("exchange_rate", RATE, nullable=False),
        sa.Column("branch_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger()),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("tax_mode", tax_mode, nullable=False),
        sa.Column("status", status_enum, nullable=False, server_default=default_status),
        sa.Column("net_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("tax_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("total_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("closed_on", sa.Date()),
        sa.Column("cancelled_on", sa.Date()),
        sa.Column("idempotency_key", sa.String(120)),
        sa.Column("idempotency_hash", sa.String(64)),
        *_audit_columns(),
    ]


def _order_line_columns(order_table: str) -> list[sa.Column]:
    return [
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("line_no", sa.SmallInteger(), nullable=False),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("uom_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", QUANTITY, nullable=False),
        sa.Column("base_quantity", QUANTITY, nullable=False),
        sa.Column("unit_price", MONEY, nullable=False, server_default="0"),
        sa.Column("discount_percent", PERCENT, nullable=False, server_default="0"),
        sa.Column("tax_code_id", sa.BigInteger()),
        sa.Column("net_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("tax_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("gross_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger()),
        *_audit_columns(),
    ]


def _order_indexes(table: str) -> None:
    op.create_index(f"ix_{table}_company_id", table, ["company_id"])
    op.create_index(f"ix_{table}_company_partner", table, ["company_id", "partner_id"])
    op.create_index(f"ix_{table}_company_status", table, ["company_id", "status"])
    op.create_index(f"ix_{table}_company_date", table, ["company_id", "order_date"])
    op.create_index(
        f"uq_{table}_company_idempotency_key",
        table,
        ["company_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def _order_line_indexes(table: str) -> None:
    op.create_index(f"ix_{table}_company_id", table, ["company_id"])
    op.create_index(f"ix_{table}_order", table, ["company_id", "order_id"])
    # Committed and on-order are summed per (item, warehouse); this is that lookup.
    op.create_index(f"ix_{table}_item", table, ["company_id", "item_id", "warehouse_id"])


def upgrade() -> None:
    bind = op.get_bind()
    sales_order_status.create(bind, checkfirst=True)
    purchase_order_status.create(bind, checkfirst=True)

    # --- Sales orders ----------------------------------------------------------------------
    op.create_table(
        "sales_orders",
        *_order_header_columns(sales_order_status, "open"),
        sa.Column("payment_terms_id", sa.BigInteger()),
        sa.Column("sales_rep_id", sa.BigInteger()),
        sa.UniqueConstraint("company_id", "id", name="uq_sales_orders_company_id_id"),
        sa.UniqueConstraint("company_id", "number", name="uq_sales_orders_company_number"),
        _tenant_fk("fk_sales_orders_partner", "partner_id", "partners"),
        _tenant_fk("fk_sales_orders_currency", "currency_id", "currencies"),
        _tenant_fk("fk_sales_orders_branch", "branch_id", "branches"),
        _tenant_fk("fk_sales_orders_project", "project_id", "projects"),
        _tenant_fk("fk_sales_orders_warehouse", "warehouse_id", "warehouses"),
        _tenant_fk("fk_sales_orders_payment_terms", "payment_terms_id", "payment_terms"),
        _tenant_fk("fk_sales_orders_sales_rep", "sales_rep_id", "sales_reps"),
        sa.CheckConstraint(
            "exchange_rate > 0", name=op.f("ck_sales_orders_positive_exchange_rate")
        ),
        sa.CheckConstraint("total_amount >= 0", name=op.f("ck_sales_orders_total_not_negative")),
    )
    _order_indexes("sales_orders")

    op.create_table(
        "sales_order_lines",
        *_order_line_columns("sales_orders"),
        sa.Column("kit_parent_line_id", sa.BigInteger()),
        sa.Column(
            "kit_breakup_edited", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.UniqueConstraint("company_id", "id", name="uq_sales_order_lines_company_id_id"),
        sa.UniqueConstraint("order_id", "line_no", name="uq_sales_order_lines_line_no"),
        _tenant_fk("fk_sales_order_lines_order", "order_id", "sales_orders", ondelete="CASCADE"),
        _tenant_fk("fk_sales_order_lines_item", "item_id", "items"),
        _tenant_fk("fk_sales_order_lines_uom", "uom_id", "uoms"),
        _tenant_fk("fk_sales_order_lines_warehouse", "warehouse_id", "warehouses"),
        _tenant_fk("fk_sales_order_lines_tax_code", "tax_code_id", "tax_codes"),
        _tenant_fk("fk_sales_order_lines_project", "project_id", "projects"),
        _tenant_fk(
            "fk_sales_order_lines_kit_parent_line",
            "kit_parent_line_id",
            "sales_order_lines",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "base_quantity > 0", name=op.f("ck_sales_order_lines_base_quantity_positive")
        ),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_sales_order_lines_quantity_positive")),
        # A kit's revenue is all on the parent line; a component that priced itself would sell
        # the same goods twice.
        sa.CheckConstraint(
            "kit_parent_line_id IS NULL OR (unit_price = 0 AND tax_code_id IS NULL)",
            name=op.f("ck_sales_order_lines_kit_component_carries_no_money"),
        ),
    )
    _order_line_indexes("sales_order_lines")

    # --- Purchase orders -------------------------------------------------------------------
    op.create_table(
        "purchase_orders",
        *_order_header_columns(purchase_order_status, "open"),
        sa.UniqueConstraint("company_id", "id", name="uq_purchase_orders_company_id_id"),
        sa.UniqueConstraint("company_id", "number", name="uq_purchase_orders_company_number"),
        _tenant_fk("fk_purchase_orders_partner", "partner_id", "partners"),
        _tenant_fk("fk_purchase_orders_currency", "currency_id", "currencies"),
        _tenant_fk("fk_purchase_orders_branch", "branch_id", "branches"),
        _tenant_fk("fk_purchase_orders_project", "project_id", "projects"),
        _tenant_fk("fk_purchase_orders_warehouse", "warehouse_id", "warehouses"),
        sa.CheckConstraint(
            "exchange_rate > 0", name=op.f("ck_purchase_orders_positive_exchange_rate")
        ),
        sa.CheckConstraint("total_amount >= 0", name=op.f("ck_purchase_orders_total_not_negative")),
    )
    _order_indexes("purchase_orders")

    op.create_table(
        "purchase_order_lines",
        *_order_line_columns("purchase_orders"),
        sa.UniqueConstraint("company_id", "id", name="uq_purchase_order_lines_company_id_id"),
        sa.UniqueConstraint("order_id", "line_no", name="uq_purchase_order_lines_line_no"),
        _tenant_fk(
            "fk_purchase_order_lines_order", "order_id", "purchase_orders", ondelete="CASCADE"
        ),
        _tenant_fk("fk_purchase_order_lines_item", "item_id", "items"),
        _tenant_fk("fk_purchase_order_lines_uom", "uom_id", "uoms"),
        _tenant_fk("fk_purchase_order_lines_warehouse", "warehouse_id", "warehouses"),
        _tenant_fk("fk_purchase_order_lines_tax_code", "tax_code_id", "tax_codes"),
        _tenant_fk("fk_purchase_order_lines_project", "project_id", "projects"),
        sa.CheckConstraint(
            "base_quantity > 0", name=op.f("ck_purchase_order_lines_base_quantity_positive")
        ),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_purchase_order_lines_quantity_positive")),
    )
    _order_line_indexes("purchase_order_lines")

    # --- The links 0019 promised ------------------------------------------------------------
    op.create_foreign_key(
        "fk_goods_received_notes_purchase_order",
        "goods_received_notes",
        "purchase_orders",
        ["company_id", "purchase_order_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_goods_received_note_lines_purchase_order_line",
        "goods_received_note_lines",
        "purchase_order_lines",
        ["company_id", "purchase_order_line_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_goods_received_note_lines_purchase_order_line",
        "goods_received_note_lines",
        ["company_id", "purchase_order_line_id"],
        postgresql_where=sa.text("purchase_order_line_id IS NOT NULL"),
    )

    # --- The document line's two order links, and the role that keeps them apart -------------
    op.create_unique_constraint(
        "uq_partner_documents_company_id_role", "partner_documents", ["company_id", "id", "role"]
    )
    op.add_column("partner_document_lines", sa.Column("role", partner_role))
    op.execute(
        """
        UPDATE partner_document_lines AS dl
           SET role = d.role
          FROM partner_documents AS d
         WHERE d.id = dl.document_id
           AND d.company_id = dl.company_id
        """
    )
    op.alter_column("partner_document_lines", "role", nullable=False)
    # This is what makes the denormalised copy a restatement rather than a cache: a line whose
    # role disagreed with its document's has no row to reference, so it cannot exist.
    op.create_foreign_key(
        "fk_partner_document_lines_document_role",
        "partner_document_lines",
        "partner_documents",
        ["company_id", "document_id", "role"],
        ["company_id", "id", "role"],
        ondelete="RESTRICT",
    )

    op.drop_index("ix_partner_document_lines_order_line", table_name="partner_document_lines")
    op.drop_column("partner_document_lines", "order_line_id")
    op.add_column("partner_document_lines", sa.Column("sales_order_line_id", sa.BigInteger()))
    op.add_column("partner_document_lines", sa.Column("purchase_order_line_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_partner_document_lines_sales_order_line",
        "partner_document_lines",
        "sales_order_lines",
        ["company_id", "sales_order_line_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_partner_document_lines_purchase_order_line",
        "partner_document_lines",
        "purchase_order_lines",
        ["company_id", "purchase_order_line_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_partner_document_lines_sales_order_line",
        "partner_document_lines",
        ["company_id", "sales_order_line_id"],
        postgresql_where=sa.text("sales_order_line_id IS NOT NULL"),
    )
    op.create_index(
        "ix_partner_document_lines_purchase_order_line",
        "partner_document_lines",
        ["company_id", "purchase_order_line_id"],
        postgresql_where=sa.text("purchase_order_line_id IS NOT NULL"),
    )
    # Bare name: the metadata naming convention turns it into
    # `ck_partner_document_lines_order_link_matches_role`, which is what the model declares.
    op.create_check_constraint(
        "order_link_matches_role",
        "partner_document_lines",
        "(role <> 'ar' OR purchase_order_line_id IS NULL) "
        "AND (role <> 'ap' OR sales_order_line_id IS NULL)",
    )

    op.execute(SALES_ORDER_LINE_QUANTITIES)
    op.execute(PURCHASE_ORDER_LINE_QUANTITIES)

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


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS purchase_order_line_quantities")
    op.execute("DROP VIEW IF EXISTS sales_order_line_quantities")

    # Bare name again, so the naming convention rebuilds the same one `upgrade` created.
    op.drop_constraint("order_link_matches_role", "partner_document_lines", type_="check")
    op.drop_index(
        "ix_partner_document_lines_purchase_order_line", table_name="partner_document_lines"
    )
    op.drop_index(
        "ix_partner_document_lines_sales_order_line", table_name="partner_document_lines"
    )
    op.drop_constraint(
        "fk_partner_document_lines_purchase_order_line",
        "partner_document_lines",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_partner_document_lines_sales_order_line", "partner_document_lines", type_="foreignkey"
    )
    op.drop_column("partner_document_lines", "purchase_order_line_id")
    op.drop_column("partner_document_lines", "sales_order_line_id")
    op.add_column("partner_document_lines", sa.Column("order_line_id", sa.BigInteger()))
    op.create_index(
        "ix_partner_document_lines_order_line",
        "partner_document_lines",
        ["company_id", "order_line_id"],
        postgresql_where=sa.text("order_line_id IS NOT NULL"),
    )
    op.drop_constraint(
        "fk_partner_document_lines_document_role", "partner_document_lines", type_="foreignkey"
    )
    op.drop_column("partner_document_lines", "role")
    op.drop_constraint(
        "uq_partner_documents_company_id_role", "partner_documents", type_="unique"
    )

    op.drop_index(
        "ix_goods_received_note_lines_purchase_order_line",
        table_name="goods_received_note_lines",
    )
    op.drop_constraint(
        "fk_goods_received_note_lines_purchase_order_line",
        "goods_received_note_lines",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_goods_received_notes_purchase_order", "goods_received_notes", type_="foreignkey"
    )

    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.drop_table("purchase_order_lines")
    op.drop_table("purchase_orders")
    op.drop_table("sales_order_lines")
    op.drop_table("sales_orders")
    purchase_order_status.drop(op.get_bind(), checkfirst=True)
    sales_order_status.drop(op.get_bind(), checkfirst=True)
