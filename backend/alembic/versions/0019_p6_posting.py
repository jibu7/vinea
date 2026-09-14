"""P6 step 2 — goods receipts, and the link the match is computed over.

Revision ID: 0019_p6_posting
Revises: 0018_p6_masters
Create Date: 2026-09-14

Two tables and one foreign key.

`goods_received_notes` / `goods_received_note_lines` are the *goods* half of the Sage GRV
two-step (§B.1): stock arrives, the inventory account rises, and the other leg is the GRN
accrual. The supplier invoice arrives later and relieves the accrual for what it actually
covers. Between the two, the accrual carries exactly the value of everything received and not
yet billed — which is the phase invariant, and the reason a receipt is its own document
rather than something folded into the invoice.

Neither table carries a matched quantity, and neither ever will. Matched quantity is the sum
of the posted, unreversed supplier-invoice lines carrying a GRN line's id, so a reversal
changes it by construction (decision 4). The v4 design this rebuild exists to delete kept a
running column here and reconciled it by hand.

**`partner_document_lines.grn_line_id` gets its composite foreign key here**, which 0018 had
to leave off because the table it points at did not exist. That was the owner's condition on
approving step 1: no bare link column survives the phase. `purchase_order_id` and
`purchase_order_line_id` stay unconstrained for one more step, for the same reason and with
the same commitment — step 3 creates the order tables and adds both.

The `value` column is frozen at receipt: `round(base_quantity x unit_cost x rate at grn_date)`
to the base currency's places. The match relieves it pro rata with the last match taking
whatever is left, so a fully matched line relieves exactly what it accrued. Recomputing it
later from a rate that has since moved is precisely how an accrual stops tying out.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0019_p6_posting"
down_revision = "0018_p6_masters"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(20, 6)
QUANTITY = sa.Numeric(20, 6)
COST = sa.Numeric(20, 10)
RATE = sa.Numeric(20, 10)

TENANT_TABLES = ("goods_received_notes", "goods_received_note_lines")

grn_status = postgresql.ENUM(
    "received",
    "partially_matched",
    "matched",
    "reversed",
    name="grn_status",
    create_type=False,
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
    grn_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "goods_received_notes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("number", sa.String(30), nullable=False),
        sa.Column("partner_id", sa.BigInteger(), nullable=False),
        # Unconstrained until step 3 creates `purchase_orders`.
        sa.Column("purchase_order_id", sa.BigInteger()),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("branch_id", sa.BigInteger(), nullable=False),
        sa.Column("grn_date", sa.Date(), nullable=False),
        sa.Column("supplier_reference", sa.String(50)),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("currency_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange_rate", RATE, nullable=False),
        sa.Column("status", grn_status, nullable=False, server_default="received"),
        sa.Column("journal_entry_id", sa.BigInteger()),
        sa.Column("reversal_entry_id", sa.BigInteger()),
        sa.Column("reversed_on", sa.Date()),
        sa.Column("idempotency_key", sa.String(120)),
        sa.Column("idempotency_hash", sa.String(64)),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_goods_received_notes_company_id_id"),
        sa.UniqueConstraint("company_id", "number", name="uq_goods_received_notes_company_number"),
        _tenant_fk("fk_goods_received_notes_partner", "partner_id", "partners"),
        _tenant_fk("fk_goods_received_notes_warehouse", "warehouse_id", "warehouses"),
        _tenant_fk("fk_goods_received_notes_branch", "branch_id", "branches"),
        _tenant_fk("fk_goods_received_notes_currency", "currency_id", "currencies"),
        _tenant_fk("fk_goods_received_notes_journal_entry", "journal_entry_id", "journal_entries"),
        _tenant_fk(
            "fk_goods_received_notes_reversal_entry", "reversal_entry_id", "journal_entries"
        ),
        sa.CheckConstraint(
            "exchange_rate > 0", name=op.f("ck_goods_received_notes_positive_exchange_rate")
        ),
    )
    op.create_index("ix_goods_received_notes_company_id", "goods_received_notes", ["company_id"])
    op.create_index(
        "ix_goods_received_notes_company_partner",
        "goods_received_notes",
        ["company_id", "partner_id"],
    )
    op.create_index(
        "ix_goods_received_notes_company_date", "goods_received_notes", ["company_id", "grn_date"]
    )
    op.create_index(
        "uq_goods_received_notes_company_idempotency_key",
        "goods_received_notes",
        ["company_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "goods_received_note_lines",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("grn_id", sa.BigInteger(), nullable=False),
        sa.Column("line_no", sa.SmallInteger(), nullable=False),
        # Unconstrained until step 3 creates `purchase_order_lines`.
        sa.Column("purchase_order_line_id", sa.BigInteger()),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("uom_id", sa.BigInteger(), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("quantity", QUANTITY, nullable=False),
        sa.Column("base_quantity", QUANTITY, nullable=False),
        sa.Column("unit_cost", COST, nullable=False),
        sa.Column("value", MONEY, nullable=False),
        sa.Column("project_id", sa.BigInteger()),
        sa.Column("stock_move_id", sa.BigInteger()),
        *_audit_columns(),
        sa.UniqueConstraint(
            "company_id", "id", name="uq_goods_received_note_lines_company_id_id"
        ),
        sa.UniqueConstraint("grn_id", "line_no", name="uq_goods_received_note_lines_line_no"),
        _tenant_fk(
            "fk_goods_received_note_lines_grn", "grn_id", "goods_received_notes", ondelete="CASCADE"
        ),
        _tenant_fk("fk_goods_received_note_lines_item", "item_id", "items"),
        _tenant_fk("fk_goods_received_note_lines_uom", "uom_id", "uoms"),
        _tenant_fk("fk_goods_received_note_lines_warehouse", "warehouse_id", "warehouses"),
        _tenant_fk("fk_goods_received_note_lines_stock_move", "stock_move_id", "stock_moves"),
        sa.CheckConstraint(
            "base_quantity > 0", name=op.f("ck_goods_received_note_lines_base_quantity_positive")
        ),
        sa.CheckConstraint(
            "unit_cost >= 0", name=op.f("ck_goods_received_note_lines_unit_cost_not_negative")
        ),
        sa.CheckConstraint(
            "value >= 0", name=op.f("ck_goods_received_note_lines_value_not_negative")
        ),
    )
    op.create_index(
        "ix_goods_received_note_lines_company_id", "goods_received_note_lines", ["company_id"]
    )
    op.create_index(
        "ix_goods_received_note_lines_grn", "goods_received_note_lines", ["company_id", "grn_id"]
    )
    op.create_index(
        "ix_goods_received_note_lines_item", "goods_received_note_lines", ["company_id", "item_id"]
    )

    # What this line actually took off the accrual, in base currency. An immutable fact of
    # the posting like `net_amount` beside it, **not** a running total — and it has to be
    # stored rather than recomputed, because a pro-rata share cannot be re-derived after one
    # of its siblings is reversed. Value 1000 received over quantity 3, matched 1 + 1 + 1,
    # relieves 333 + 333 + 334; reverse the first and the ledger has relieved 667, while
    # recomputing over the survivors gives 666. The accrual proof would be off by a franc and
    # would stay off.
    op.add_column("partner_document_lines", sa.Column("accrual_relieved", MONEY))

    # The carry-forward from step 1: the link the match is computed over is now a real
    # reference, not a loose integer.
    op.create_foreign_key(
        "fk_partner_document_lines_grn_line",
        "partner_document_lines",
        "goods_received_note_lines",
        ["company_id", "grn_line_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )

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
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.drop_constraint(
        "fk_partner_document_lines_grn_line", "partner_document_lines", type_="foreignkey"
    )
    op.drop_column("partner_document_lines", "accrual_relieved")
    op.drop_table("goods_received_note_lines")
    op.drop_table("goods_received_notes")
    grn_status.drop(op.get_bind(), checkfirst=True)
