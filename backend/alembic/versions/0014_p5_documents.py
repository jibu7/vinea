"""P5 step 3 — stock documents: the adjustment and journal-batch headers and their lines.

Revision ID: 0014_p5_documents
Revises: 0013_p5_stock_ledger
Create Date: 2026-09-12

`stock_moves` already records every *fact* of a posting. What it cannot record is the three
things a document is for, and this revision adds exactly those and nothing else:

* **A number.** Decision 12 gives adjustments (`INAJ`) and journal batches (`INJN`) their own
  `document_sequences` doc types. A move carries `sequence_no`, which is a posting order —
  deliberately gappy, and not a number anyone quotes to an auditor.
* **A unit of work.** A batch is one document with many lines, refused whole. Once its moves
  are in the ledger they are indistinguishable from any others; the header is what says they
  arrived together.
* **Idempotency for a posting that valued nothing.** `Idempotency-Key` lives on
  `journal_entries`, and a posting in which no line carried value produces no entry — a
  receipt at zero cost is still a real change to what is on the shelf. Without a key of its
  own such a document would post its moves again on every retry. Hence the partial unique
  index on `(company_id, idempotency_key)`, the same shape `partner_documents` carries.

**Why the line table duplicates what the move already says.** It does not: a move is always in
the item's base unit, and "24" in the base unit does not record that someone keyed "2 cases".
The move is the truth, the line is the intent, and when a figure is disputed the intent is the
question. `stock_move_id` is the link forward from one to the other — forward, because
`stock_moves` refuses UPDATE, so a move cannot be told which document line it belongs to after
the fact. That is also why the line's FK to the move is `RESTRICT`: the move outlives the
document row in every ordering.

**No back-fill.** Nothing before this revision could write a stock document, so there is no
history to convert; both tables start empty for every tenant. The downgrade is genuinely
reversible for once — dropping a header does not unwrite the moves or the journal entries it
produced, and those remain correct on their own, which is precisely the property that let
step 2's design keep the document out of the ledger's critical path.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0014_p5_documents"
down_revision = "0013_p5_stock_ledger"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(20, 6)
QUANTITY = sa.Numeric(20, 6)
COST = sa.Numeric(20, 10)

TENANT_TABLES = ("inventory_documents", "inventory_document_lines")

DOCUMENT_STATUS = postgresql.ENUM(
    "posted", "reversed", name="inventory_document_status", create_type=False
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
    DOCUMENT_STATUS.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "inventory_documents",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("doc_type", sa.String(20), nullable=False),
        sa.Column("number", sa.String(50), nullable=False),
        sa.Column("document_date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("reference", sa.String(100)),
        sa.Column("transaction_type_id", sa.BigInteger()),
        sa.Column("status", DOCUMENT_STATUS, nullable=False, server_default="posted"),
        sa.Column("journal_entry_id", sa.BigInteger()),
        sa.Column("reversal_entry_id", sa.BigInteger()),
        sa.Column("reverses_document_id", sa.BigInteger()),
        sa.Column("idempotency_key", sa.String(255)),
        sa.Column("idempotency_hash", sa.String(64)),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_inventory_documents_company_id_id"),
        sa.UniqueConstraint(
            "company_id", "number", name="uq_inventory_documents_company_number"
        ),
        _tenant_fk("fk_inventory_documents_journal_entry", "journal_entry_id", "journal_entries"),
        _tenant_fk(
            "fk_inventory_documents_reversal_entry", "reversal_entry_id", "journal_entries"
        ),
        _tenant_fk(
            "fk_inventory_documents_reverses_document",
            "reverses_document_id",
            "inventory_documents",
        ),
        _tenant_fk(
            "fk_inventory_documents_transaction_type",
            "transaction_type_id",
            "gl_transaction_types",
        ),
    )
    op.create_index(
        "ix_inventory_documents_company_id", "inventory_documents", ["company_id"]
    )
    op.create_index(
        "ix_inventory_documents_company_date",
        "inventory_documents",
        ["company_id", "document_date"],
    )
    op.create_index(
        "ix_inventory_documents_company_doc_type",
        "inventory_documents",
        ["company_id", "doc_type"],
    )
    # Replay protection that does not depend on a journal entry existing — the whole reason
    # this table carries a key at all.
    op.create_index(
        "uq_inventory_documents_company_idempotency_key",
        "inventory_documents",
        ["company_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "inventory_document_lines",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("line_no", sa.SmallInteger(), nullable=False),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", QUANTITY, nullable=False),
        sa.Column("uom_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity_base", QUANTITY, nullable=False),
        sa.Column("unit_cost", COST),
        sa.Column("value", MONEY),
        sa.Column("transaction_type_id", sa.BigInteger(), nullable=False),
        sa.Column("contra_account_id", sa.BigInteger()),
        sa.Column("project_id", sa.BigInteger()),
        sa.Column("description", sa.Text()),
        sa.Column("stock_move_id", sa.BigInteger()),
        *_audit_columns(),
        sa.UniqueConstraint(
            "company_id", "id", name="uq_inventory_document_lines_company_id_id"
        ),
        sa.UniqueConstraint(
            "company_id", "document_id", "line_no", name="uq_inventory_document_lines_line_no"
        ),
        # One line per move, both ways: a move cannot be claimed by two lines.
        sa.UniqueConstraint(
            "stock_move_id", name="uq_inventory_document_lines_stock_move_id"
        ),
        _tenant_fk(
            "fk_inventory_document_lines_document",
            "document_id",
            "inventory_documents",
            ondelete="CASCADE",
        ),
        _tenant_fk("fk_inventory_document_lines_stock_move", "stock_move_id", "stock_moves"),
        _tenant_fk("fk_inventory_document_lines_item", "item_id", "items"),
        _tenant_fk("fk_inventory_document_lines_warehouse", "warehouse_id", "warehouses"),
        _tenant_fk("fk_inventory_document_lines_uom", "uom_id", "uoms"),
        _tenant_fk(
            "fk_inventory_document_lines_transaction_type",
            "transaction_type_id",
            "gl_transaction_types",
        ),
        _tenant_fk(
            "fk_inventory_document_lines_contra_account", "contra_account_id", "gl_accounts"
        ),
        _tenant_fk("fk_inventory_document_lines_project", "project_id", "projects"),
    )
    op.create_index(
        "ix_inventory_document_lines_company_id", "inventory_document_lines", ["company_id"]
    )
    op.create_index(
        "ix_inventory_document_lines_document",
        "inventory_document_lines",
        ["company_id", "document_id"],
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
    op.drop_table("inventory_document_lines")
    op.drop_table("inventory_documents")
    DOCUMENT_STATUS.drop(op.get_bind(), checkfirst=True)
