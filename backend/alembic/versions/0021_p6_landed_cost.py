"""P6 step 4 — landed cost (Importation Split).

Revision ID: 0021_p6_landed_cost
Revises: 0020_p6_orders
Create Date: 2026-09-14

Two tables and two enums. Nothing else in the schema moves: the landed-cost clearing account,
its `gl_settings` key and the `oe:landed_cost_post` permission all arrived at step 1, and the
`LCA` run enters `DocType` in this commit because this is the commit that creates the table
holding its numbers.

**Two statuses, and there is no third.** A landed cost posts in the transaction it is created
in, so it is `posted` from the moment the row exists and `reversed` once undone. There is no
draft: shares are struck against the receipts and the stock position as they stand, and a
document that waited would post shares computed against a position that had moved underneath
it. There is no `partially_allocated` either — an allocation is all of its amount or none of
it, which is what the residue rule makes true and the clearing-account invariant proves.

**No `branch_id` on the header.** A journal entry carries no branch — its lines do — and one
freight bill may cover receipts that landed in two branches. Each share's journal line takes
the branch of the warehouse its target sits in, so the clearing account squares per branch by
construction and a header column would have had to name one of several places.

**Three line shapes, held apart by two check constraints.** A share that revalued stock has a
move; a share that went to cost of sales (the location held none of the item) has none and says
so in `went_to_cogs`; a share of zero posted nothing at all and is neither. `share = 0` and
`went_to_cogs` are the two facts that distinguish them, and
`(share = 0) = (stock_move_id IS NULL AND NOT went_to_cogs)` is what stops a fourth shape —
a non-zero share that revalued nothing and charged nothing — from being storable.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0021_p6_landed_cost"
down_revision = "0020_p6_orders"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(20, 6)
QUANTITY = sa.Numeric(20, 6)

TENANT_TABLES = ("landed_cost_documents", "landed_cost_lines")

landed_cost_basis = postgresql.ENUM(
    "value",
    "quantity",
    "weight",
    name="landed_cost_basis",
    # Created explicitly below; `create_table` must not emit a second CREATE TYPE for it.
    create_type=False,
)
landed_cost_status = postgresql.ENUM(
    "posted", "reversed", name="landed_cost_status", create_type=False
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
    name: str, local: str, table: str, ondelete: str = "RESTRICT"
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["company_id", local],
        [f"{table}.company_id", f"{table}.id"],
        name=name,
        ondelete=ondelete,
    )


def upgrade() -> None:
    bind = op.get_bind()
    landed_cost_basis.create(bind, checkfirst=True)
    landed_cost_status.create(bind, checkfirst=True)

    op.create_table(
        "landed_cost_documents",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("number", sa.String(30), nullable=False),
        sa.Column("cost_date", sa.Date(), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("reference", sa.String(50)),
        # Base currency. The clearing account holds what was booked to it in base, and an
        # allocation working in document currency would clear a different number.
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("basis", landed_cost_basis, nullable=False),
        sa.Column("status", landed_cost_status, nullable=False, server_default="posted"),
        # Informational only — where the cost came from, so a reader can get back to it.
        # Nothing is derived from either; the clearing account's balance is the arithmetic.
        sa.Column("source_document_id", sa.BigInteger()),
        sa.Column("source_cashbook_line_id", sa.BigInteger()),
        sa.Column("journal_entry_id", sa.BigInteger()),
        sa.Column("reversal_entry_id", sa.BigInteger()),
        sa.Column("reversed_on", sa.Date()),
        sa.Column("idempotency_key", sa.String(120)),
        sa.Column("idempotency_hash", sa.String(64)),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_landed_cost_documents_company_id_id"),
        sa.UniqueConstraint(
            "company_id", "number", name="uq_landed_cost_documents_company_number"
        ),
        _tenant_fk("fk_landed_cost_documents_journal_entry", "journal_entry_id", "journal_entries"),
        _tenant_fk(
            "fk_landed_cost_documents_reversal_entry", "reversal_entry_id", "journal_entries"
        ),
        _tenant_fk(
            "fk_landed_cost_documents_source_document", "source_document_id", "partner_documents"
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_landed_cost_documents_amount_positive")),
    )
    # The tenant discriminator's own index, which `CompanyScopedMixin` declares on every
    # company-scoped table.
    op.create_index("ix_landed_cost_documents_company_id", "landed_cost_documents", ["company_id"])
    op.create_index(
        "uq_landed_cost_documents_company_idempotency_key",
        "landed_cost_documents",
        ["company_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_landed_cost_documents_company_date",
        "landed_cost_documents",
        ["company_id", "cost_date"],
    )

    op.create_table(
        "landed_cost_lines",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("line_no", sa.SmallInteger(), nullable=False),
        sa.Column("grn_line_id", sa.BigInteger(), nullable=False),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=False),
        # What this line contributed to the divisor, in the document's basis. Stored because
        # the share cannot be re-derived once the receipts behind it have moved on.
        sa.Column("weight", MONEY, nullable=False),
        sa.Column("share", MONEY, nullable=False),
        sa.Column("went_to_cogs", sa.Boolean(), nullable=False, server_default=sa.false()),
        # What the target's location held when this allocation posted — the denominator the
        # reversal splits the share by. Stored because "how much was there to begin with" is
        # not answerable once the position has moved on; see `LandedCostLine`.
        sa.Column(
            "quantity_at_posting", QUANTITY, nullable=False, server_default=sa.text("0")
        ),
        sa.Column("stock_move_id", sa.BigInteger()),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_landed_cost_lines_company_id_id"),
        sa.UniqueConstraint("document_id", "line_no", name="uq_landed_cost_lines_line_no"),
        _tenant_fk(
            "fk_landed_cost_lines_document",
            "document_id",
            "landed_cost_documents",
            ondelete="CASCADE",
        ),
        _tenant_fk("fk_landed_cost_lines_grn_line", "grn_line_id", "goods_received_note_lines"),
        _tenant_fk("fk_landed_cost_lines_item", "item_id", "items"),
        _tenant_fk("fk_landed_cost_lines_warehouse", "warehouse_id", "warehouses"),
        _tenant_fk("fk_landed_cost_lines_stock_move", "stock_move_id", "stock_moves"),
        sa.CheckConstraint("weight >= 0", name=op.f("ck_landed_cost_lines_weight_not_negative")),
        sa.CheckConstraint(
            "quantity_at_posting >= 0",
            name=op.f("ck_landed_cost_lines_quantity_at_posting_not_negative"),
        ),
        sa.CheckConstraint("share >= 0", name=op.f("ck_landed_cost_lines_share_not_negative")),
        sa.CheckConstraint(
            "NOT went_to_cogs OR stock_move_id IS NULL",
            name=op.f("ck_landed_cost_lines_cogs_line_has_no_move"),
        ),
        sa.CheckConstraint(
            "(share = 0) = (stock_move_id IS NULL AND NOT went_to_cogs)",
            name=op.f("ck_landed_cost_lines_zero_share_posts_nothing"),
        ),
    )
    op.create_index("ix_landed_cost_lines_company_id", "landed_cost_lines", ["company_id"])
    op.create_index(
        "ix_landed_cost_lines_grn_line", "landed_cost_lines", ["company_id", "grn_line_id"]
    )
    op.create_index(
        "ix_landed_cost_lines_document", "landed_cost_lines", ["company_id", "document_id"]
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
    op.drop_table("landed_cost_lines")
    op.drop_table("landed_cost_documents")
    landed_cost_status.drop(op.get_bind(), checkfirst=True)
    landed_cost_basis.drop(op.get_bind(), checkfirst=True)
