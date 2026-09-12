"""P5 step 4 — warehouse transfers and stock count sessions.

Revision ID: 0015_p5_transfers_counts
Revises: 0014_p5_documents
Create Date: 2026-09-12

Two documents that `inventory_documents` cannot hold, for two different reasons.

* **A transfer is two postings.** Dispatch (source → in-transit) and receive (in-transit →
  destination) are separate journal entries, each carrying the branch of its own physical
  warehouse, because a transfer between branches has to leave both branch balances square
  (decision 6). `inventory_documents` carries one `journal_entry_id`, which is right for an
  adjustment and a journal batch and cannot be stretched to a document that posts twice —
  and a third time when a dispatch that never arrived is cancelled.
* **A count is a working paper before it is a posting.** A session freezes a system quantity
  per line at a watermark, is filled in over hours or days, and only then posts one
  count-variance document — which *is* an `inventory_documents` row (doc type `INCT`), so it
  reverses through exactly the machinery step 3 built and needs nothing new here. What is
  new is the sheet: `stock_count_sessions` and `stock_count_lines` (decision 7).

**The watermark.** `snapshot_sequence` on both the session and each line is
`stock_moves.sequence_no` as at the freeze. Staleness is then `sequence_no > snapshot_sequence`
for the line's location — a posting-order comparison rather than a timestamp one, so a clock
that drifts, a transaction that stays open, or a document dated last month cannot make a stale
line look fresh. `snapshot_at` is kept beside it for people to read, never for the check.

**No variance column, no on-hand column.** Variance is `counted_quantity_base -
system_quantity`, computed wherever it is needed; `system_quantity` is not a running balance
but a frozen observation, which is the one kind of quantity architecture rule 1 permits on a
table that is not `stock_moves`.

**No back-fill.** Nothing before this revision could transfer or count, so both pairs of
tables start empty for every tenant, and the downgrade is clean: dropping a transfer header
does not unwrite the moves and entries its legs posted, and those stand correct on their own.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0015_p5_transfers_counts"
down_revision = "0014_p5_documents"
branch_labels = None
depends_on = None

QUANTITY = sa.Numeric(20, 6)

TENANT_TABLES = (
    "stock_transfers",
    "stock_transfer_lines",
    "stock_count_sessions",
    "stock_count_lines",
)

TRANSFER_STATUS = postgresql.ENUM(
    "in_transit", "completed", "cancelled", name="stock_transfer_status", create_type=False
)
COUNT_STATUS = postgresql.ENUM(
    "counting", "completed", "cancelled", name="stock_count_status", create_type=False
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
    TRANSFER_STATUS.create(op.get_bind(), checkfirst=True)
    COUNT_STATUS.create(op.get_bind(), checkfirst=True)

    # --- Transfers ---------------------------------------------------------------------------
    op.create_table(
        "stock_transfers",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("number", sa.String(50), nullable=False),
        sa.Column("transfer_date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("reference", sa.String(100)),
        sa.Column("from_warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("to_warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("transaction_type_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger()),
        sa.Column("status", TRANSFER_STATUS, nullable=False, server_default="in_transit"),
        sa.Column("dispatch_entry_id", sa.BigInteger()),
        sa.Column("receive_entry_id", sa.BigInteger()),
        sa.Column("cancellation_entry_id", sa.BigInteger()),
        sa.Column("received_date", sa.Date()),
        sa.Column("cancelled_date", sa.Date()),
        sa.Column("idempotency_key", sa.String(255)),
        sa.Column("idempotency_hash", sa.String(64)),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_stock_transfers_company_id_id"),
        sa.UniqueConstraint("company_id", "number", name="uq_stock_transfers_company_number"),
        _tenant_fk("fk_stock_transfers_from_warehouse", "from_warehouse_id", "warehouses"),
        _tenant_fk("fk_stock_transfers_to_warehouse", "to_warehouse_id", "warehouses"),
        _tenant_fk(
            "fk_stock_transfers_transaction_type", "transaction_type_id", "gl_transaction_types"
        ),
        _tenant_fk("fk_stock_transfers_dispatch_entry", "dispatch_entry_id", "journal_entries"),
        _tenant_fk("fk_stock_transfers_receive_entry", "receive_entry_id", "journal_entries"),
        _tenant_fk(
            "fk_stock_transfers_cancellation_entry", "cancellation_entry_id", "journal_entries"
        ),
        _tenant_fk("fk_stock_transfers_project", "project_id", "projects"),
    )
    op.create_index("ix_stock_transfers_company_id", "stock_transfers", ["company_id"])
    op.create_index(
        "ix_stock_transfers_company_date", "stock_transfers", ["company_id", "transfer_date"]
    )
    op.create_index(
        "ix_stock_transfers_company_status", "stock_transfers", ["company_id", "status"]
    )
    # Replay protection for the dispatch, which is the posting that creates the transfer.
    op.create_index(
        "uq_stock_transfers_company_idempotency_key",
        "stock_transfers",
        ["company_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "stock_transfer_lines",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("transfer_id", sa.BigInteger(), nullable=False),
        sa.Column("line_no", sa.SmallInteger(), nullable=False),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", QUANTITY, nullable=False),
        sa.Column("uom_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity_base", QUANTITY, nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("dispatch_out_move_id", sa.BigInteger()),
        sa.Column("dispatch_in_move_id", sa.BigInteger()),
        sa.Column("receive_out_move_id", sa.BigInteger()),
        sa.Column("receive_in_move_id", sa.BigInteger()),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_stock_transfer_lines_company_id_id"),
        sa.UniqueConstraint(
            "company_id", "transfer_id", "line_no", name="uq_stock_transfer_lines_line_no"
        ),
        sa.UniqueConstraint("dispatch_out_move_id", name="uq_stock_transfer_lines_dispatch_out"),
        sa.UniqueConstraint("dispatch_in_move_id", name="uq_stock_transfer_lines_dispatch_in"),
        sa.UniqueConstraint("receive_out_move_id", name="uq_stock_transfer_lines_receive_out"),
        sa.UniqueConstraint("receive_in_move_id", name="uq_stock_transfer_lines_receive_in"),
        _tenant_fk(
            "fk_stock_transfer_lines_transfer",
            "transfer_id",
            "stock_transfers",
            ondelete="CASCADE",
        ),
        _tenant_fk("fk_stock_transfer_lines_item", "item_id", "items"),
        _tenant_fk("fk_stock_transfer_lines_uom", "uom_id", "uoms"),
        _tenant_fk(
            "fk_stock_transfer_lines_dispatch_out_move", "dispatch_out_move_id", "stock_moves"
        ),
        _tenant_fk(
            "fk_stock_transfer_lines_dispatch_in_move", "dispatch_in_move_id", "stock_moves"
        ),
        _tenant_fk(
            "fk_stock_transfer_lines_receive_out_move", "receive_out_move_id", "stock_moves"
        ),
        _tenant_fk(
            "fk_stock_transfer_lines_receive_in_move", "receive_in_move_id", "stock_moves"
        ),
    )
    op.create_index("ix_stock_transfer_lines_company_id", "stock_transfer_lines", ["company_id"])
    op.create_index(
        "ix_stock_transfer_lines_transfer",
        "stock_transfer_lines",
        ["company_id", "transfer_id"],
    )

    # --- Count sessions ----------------------------------------------------------------------
    op.create_table(
        "stock_count_sessions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("number", sa.String(50), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("count_date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("reference", sa.String(100)),
        sa.Column("transaction_type_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger()),
        sa.Column("status", COUNT_STATUS, nullable=False, server_default="counting"),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_sequence", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger()),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_stock_count_sessions_company_id_id"),
        sa.UniqueConstraint(
            "company_id", "number", name="uq_stock_count_sessions_company_number"
        ),
        _tenant_fk("fk_stock_count_sessions_warehouse", "warehouse_id", "warehouses"),
        _tenant_fk(
            "fk_stock_count_sessions_transaction_type",
            "transaction_type_id",
            "gl_transaction_types",
        ),
        _tenant_fk("fk_stock_count_sessions_document", "document_id", "inventory_documents"),
        _tenant_fk("fk_stock_count_sessions_project", "project_id", "projects"),
    )
    op.create_index("ix_stock_count_sessions_company_id", "stock_count_sessions", ["company_id"])
    op.create_index(
        "ix_stock_count_sessions_company_status",
        "stock_count_sessions",
        ["company_id", "status"],
    )
    op.create_index(
        "ix_stock_count_sessions_company_warehouse",
        "stock_count_sessions",
        ["company_id", "warehouse_id"],
    )

    op.create_table(
        "stock_count_lines",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("line_no", sa.SmallInteger(), nullable=False),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("system_quantity", QUANTITY, nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_sequence", sa.BigInteger(), nullable=False),
        sa.Column("counted_quantity", QUANTITY),
        sa.Column("uom_id", sa.BigInteger(), nullable=False),
        sa.Column("counted_quantity_base", QUANTITY),
        sa.Column("counted_at", sa.DateTime(timezone=True)),
        sa.Column("note", sa.Text()),
        sa.Column("stock_move_id", sa.BigInteger()),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_stock_count_lines_company_id_id"),
        sa.UniqueConstraint(
            "company_id", "session_id", "line_no", name="uq_stock_count_lines_line_no"
        ),
        sa.UniqueConstraint(
            "company_id", "session_id", "item_id", name="uq_stock_count_lines_item"
        ),
        sa.UniqueConstraint("stock_move_id", name="uq_stock_count_lines_stock_move_id"),
        # A counted quantity and its conversion to the base unit arrive together or not at
        # all: "counted" is one fact, and half of it would be a line nothing could post.
        sa.CheckConstraint(
            "(counted_quantity IS NULL) = (counted_quantity_base IS NULL)",
            name="counted_quantity_is_converted",
        ),
        _tenant_fk(
            "fk_stock_count_lines_session",
            "session_id",
            "stock_count_sessions",
            ondelete="CASCADE",
        ),
        _tenant_fk("fk_stock_count_lines_item", "item_id", "items"),
        _tenant_fk("fk_stock_count_lines_uom", "uom_id", "uoms"),
        _tenant_fk("fk_stock_count_lines_stock_move", "stock_move_id", "stock_moves"),
    )
    op.create_index("ix_stock_count_lines_company_id", "stock_count_lines", ["company_id"])
    op.create_index(
        "ix_stock_count_lines_session", "stock_count_lines", ["company_id", "session_id"]
    )

    # --- Row Level Security (ADR-01) ---------------------------------------------------------
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
    op.drop_table("stock_count_lines")
    op.drop_table("stock_count_sessions")
    op.drop_table("stock_transfer_lines")
    op.drop_table("stock_transfers")
    COUNT_STATUS.drop(op.get_bind(), checkfirst=True)
    TRANSFER_STATUS.drop(op.get_bind(), checkfirst=True)
