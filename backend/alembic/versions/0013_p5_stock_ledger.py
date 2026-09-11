"""P5 step 2 — the stock ledger: `stock_moves`, the two caches it proves, and the triggers
that make "derived from moves" a database rule rather than a code-review rule.

Revision ID: 0013_p5_stock_ledger
Revises: 0012_p5_masters
Create Date: 2026-09-11

`stock_moves` is to inventory what `journal_lines` is to the general ledger: append-only,
signed, and the only source of truth for what is on hand and what it is worth. Everything
else — `stock_balances(item, warehouse)` and `item_cost_state(item)` — is a cache written by
the stock service alone and re-derivable by `verify_stock_balances()`.

Three rules land here as DDL rather than as convention:

* **A valued move cannot exist without its journal line.** The check constraint
  `ck_stock_moves_value_matches_journal_link` says a move with a non-zero value carries
  both an entry and a line, and one with no value carries neither. With
  `uq_stock_moves_journal_line_id` beside it, that is the whole of "stock valuation equals
  the inventory GL balance": one line, one move, `value = base_amount`. Not a
  reconciliation — a foreign key.
* **Only the stock service writes stock rows.** `inventory_require_stock_service()` refuses
  an INSERT or UPDATE unless `app.stock_service` is on, exactly as the posting engine's own
  guard protects the journal (migration 0004). Anything that decides to keep a quantity of
  its own in step with the moves has to go through the service to do it.
* **Posted moves are immutable.** `inventory_block_move_mutation()` refuses UPDATE and DELETE
  outright. A correction is a reversing move (ADR-04), never an edit.

There is no back-fill. A tenant upgrading to this revision has no stock history — it could
not have had one, since nothing before this revision could write a move — so the three tables
start empty for everybody and the first posting is what fills them.

**The downgrade is destructive in a way that does not undo itself.** It drops the move ledger,
but the journal entries those moves posted are in `journal_entries` / `journal_lines`, and
those are append-only: the downgrade cannot and must not remove them. A tenant that has posted
stock and is then downgraded keeps an inventory account carrying value with no move behind it,
and re-upgrading does not bring the moves back — the tables come back empty.
`assert_stock_invariants` fails for that company from then on, correctly, and the way out is
the one in `docs/ops/inventory-control-account.md`. Measured, not assumed: three receipts
totalling 3 510 left `1300` at 3 510 with zero rows in `stock_moves` after a
`downgrade -1` / `upgrade head` cycle. Downgrade this revision on a database that has posted
stock only if you mean to reconstruct the moves by hand afterwards.

`journal_lines` and `gl_transaction_types` each gain a `(company_id, id)` unique constraint,
the composite targets `stock_moves.journal_line_id` and `stock_moves.transaction_type_id`
point at: a move names the line that posted its value and the type that produced it, and the
tenant pair means it can never name another tenant's.
"""

import sqlalchemy as sa

from alembic import op

revision = "0013_p5_stock_ledger"
down_revision = "0012_p5_masters"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(20, 6)
QUANTITY = sa.Numeric(20, 6)
COST = sa.Numeric(20, 10)
AVERAGE = sa.Numeric(20, 6)

TENANT_TABLES = ("stock_moves", "stock_balances", "item_cost_state")

#: Posting order. A plain sequence, not a `document_sequences` row: gaps are expected (a
#: rolled-back posting must not make the next one wait) and this is an *order*, not a number
#: an auditor follows. Gaplessness belongs to the document numbers, which already have it.
STOCK_SEQUENCE = "stock_moves_sequence_no_seq"

INVENTORY_FUNCTIONS = {
    # (a) single writer — the ADR-05 rule, applied to the stock ledger.
    "inventory_require_stock_service": """
        CREATE FUNCTION inventory_require_stock_service() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF coalesce(current_setting('app.stock_service', true), 'off') <> 'on' THEN
                RAISE EXCEPTION
                    'stock rows may only be written by the stock service (ADR-05)'
                    USING ERRCODE = 'VN009';
            END IF;
            RETURN NEW;
        END
        $$
    """,
    # (b) immutability — a posted move is a fact, and facts are not edited.
    "inventory_block_move_mutation": """
        CREATE FUNCTION inventory_block_move_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'stock move % is posted and immutable',
                CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END
                USING ERRCODE = 'VN010';
        END
        $$
    """,
}

# Named so they sort before the other BEFORE triggers on the same table: PostgreSQL fires
# same-timing triggers in name order, and the writer guard must be the first thing to fail.
INVENTORY_TRIGGERS = (
    (
        "trg_stock_moves_service_guard",
        "stock_moves",
        "BEFORE INSERT ON stock_moves FOR EACH ROW "
        "EXECUTE FUNCTION inventory_require_stock_service()",
    ),
    (
        "trg_stock_balances_service_guard",
        "stock_balances",
        "BEFORE INSERT OR UPDATE ON stock_balances FOR EACH ROW "
        "EXECUTE FUNCTION inventory_require_stock_service()",
    ),
    (
        "trg_item_cost_state_service_guard",
        "item_cost_state",
        "BEFORE INSERT OR UPDATE ON item_cost_state FOR EACH ROW "
        "EXECUTE FUNCTION inventory_require_stock_service()",
    ),
    (
        "trg_stock_moves_immutable",
        "stock_moves",
        "BEFORE UPDATE OR DELETE ON stock_moves FOR EACH ROW "
        "EXECUTE FUNCTION inventory_block_move_mutation()",
    ),
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
    # Composite-FK targets the move table needs; both tables predate the tenant-pair
    # convention's arrival on them because nothing had referenced them from inside a tenant
    # before.
    op.create_unique_constraint(
        "uq_journal_lines_company_id_id", "journal_lines", ["company_id", "id"]
    )
    op.create_unique_constraint(
        "uq_gl_transaction_types_company_id_id", "gl_transaction_types", ["company_id", "id"]
    )
    op.execute(f"CREATE SEQUENCE {STOCK_SEQUENCE}")

    op.create_table(
        "stock_moves",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("move_date", sa.Date(), nullable=False),
        sa.Column("period_id", sa.BigInteger(), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("quantity", QUANTITY, nullable=False),
        sa.Column("unit_cost", COST),
        sa.Column("value", MONEY, nullable=False),
        sa.Column("journal_entry_id", sa.BigInteger()),
        sa.Column("journal_line_id", sa.BigInteger()),
        sa.Column("transaction_type_id", sa.BigInteger()),
        sa.Column("source_doc_type", sa.String(50)),
        sa.Column("source_doc_id", sa.BigInteger()),
        sa.Column("source_line_id", sa.BigInteger()),
        sa.Column("project_id", sa.BigInteger()),
        sa.Column(
            "cost_provisional", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("reverses_move_id", sa.BigInteger()),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_stock_moves_company_id_id"),
        sa.UniqueConstraint("company_id", "sequence_no", name="uq_stock_moves_company_sequence"),
        sa.UniqueConstraint("journal_line_id", name="uq_stock_moves_journal_line_id"),
        _tenant_fk("fk_stock_moves_item", "item_id", "items"),
        _tenant_fk("fk_stock_moves_warehouse", "warehouse_id", "warehouses"),
        _tenant_fk("fk_stock_moves_period", "period_id", "accounting_periods"),
        _tenant_fk("fk_stock_moves_project", "project_id", "projects"),
        _tenant_fk("fk_stock_moves_journal_entry", "journal_entry_id", "journal_entries"),
        _tenant_fk("fk_stock_moves_journal_line", "journal_line_id", "journal_lines"),
        _tenant_fk(
            "fk_stock_moves_transaction_type", "transaction_type_id", "gl_transaction_types"
        ),
        _tenant_fk("fk_stock_moves_reverses_move", "reverses_move_id", "stock_moves"),
        sa.CheckConstraint(
            "(value = 0 AND journal_entry_id IS NULL AND journal_line_id IS NULL) "
            "OR (value <> 0 AND journal_entry_id IS NOT NULL AND journal_line_id IS NOT NULL)",
            name=op.f("ck_stock_moves_value_matches_journal_link"),
        ),
        sa.CheckConstraint(
            "quantity <> 0 OR value <> 0", name=op.f("ck_stock_moves_move_is_not_empty")
        ),
        sa.CheckConstraint(
            "(unit_cost IS NULL) = (quantity = 0)",
            name=op.f("ck_stock_moves_unit_cost_accompanies_quantity"),
        ),
    )
    op.create_index("ix_stock_moves_company_id", "stock_moves", ["company_id"])
    op.create_index(
        "ix_stock_moves_company_item", "stock_moves", ["company_id", "item_id", "warehouse_id"]
    )
    op.create_index("ix_stock_moves_company_date", "stock_moves", ["company_id", "move_date"])
    op.create_index(
        "ix_stock_moves_company_entry", "stock_moves", ["company_id", "journal_entry_id"]
    )
    op.create_index(
        "ix_stock_moves_company_source",
        "stock_moves",
        ["company_id", "source_doc_type", "source_doc_id"],
    )

    op.create_table(
        "stock_balances",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", QUANTITY, nullable=False, server_default="0"),
        sa.Column("value", MONEY, nullable=False, server_default="0"),
        *_audit_columns(),
        sa.UniqueConstraint(
            "company_id", "item_id", "warehouse_id", name="uq_stock_balances_location"
        ),
        _tenant_fk("fk_stock_balances_item", "item_id", "items"),
        _tenant_fk("fk_stock_balances_warehouse", "warehouse_id", "warehouses"),
    )
    op.create_index("ix_stock_balances_company_id", "stock_balances", ["company_id"])

    op.create_table(
        "item_cost_state",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("average_cost", AVERAGE, nullable=False, server_default="0"),
        sa.Column("last_positive_average_cost", AVERAGE, nullable=False, server_default="0"),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "item_id", name="uq_item_cost_state_item"),
        _tenant_fk("fk_item_cost_state_item", "item_id", "items"),
    )
    op.create_index("ix_item_cost_state_company_id", "item_cost_state", ["company_id"])

    for ddl in INVENTORY_FUNCTIONS.values():
        op.execute(ddl)
    for name, _table, spec in INVENTORY_TRIGGERS:
        op.execute(f"CREATE TRIGGER {name} {spec}")

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


def downgrade() -> None:
    # Destructive and not self-undoing: the moves go, the journal entries they posted stay
    # (append-only, ADR-04), so the inventory account is left carrying value nothing explains.
    # See the module docstring.
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    for name, table, _spec in INVENTORY_TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    for function in INVENTORY_FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {function}()")

    op.drop_table("item_cost_state")
    op.drop_table("stock_balances")
    op.drop_table("stock_moves")
    op.execute(f"DROP SEQUENCE IF EXISTS {STOCK_SEQUENCE}")
    op.drop_constraint(
        "uq_gl_transaction_types_company_id_id", "gl_transaction_types", type_="unique"
    )
    op.drop_constraint("uq_journal_lines_company_id_id", "journal_lines", type_="unique")
