"""P2 acceptance hardening: FX rounding line, single-writer guard on the journal, and
period non-overlap enforced by the database.

Revision ID: 0004_p2_acceptance
Revises: 0003_p2_hardening
Create Date: 2026-09-04

- `journal_lines.is_rounding_line` marks the line the engine appends to absorb sub-unit
  differences left by per-line rounding on multi-currency entries (ADR-06).
- `gl_settings.rounding_difference_account_id` is where that line posts.
- `kernel_require_posting_engine` makes ADR-05 ("modules never write journal entries
  directly") a database rule rather than a code review rule: an INSERT into
  `journal_entries` / `journal_lines` is refused unless `app.posting_engine` is on, and
  only `posting._write` turns it on, for the duration of one posting.
- `ex_accounting_periods_no_overlap` — posting resolves a period from the entry date, so
  overlapping periods would make that lookup ambiguous.
"""

import sqlalchemy as sa

from alembic import op

revision = "0004_p2_acceptance"
down_revision = "0003_p2_hardening"
branch_labels = None
depends_on = None

SINGLE_WRITER_FUNCTION = """
    CREATE FUNCTION kernel_require_posting_engine() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF coalesce(current_setting('app.posting_engine', true), 'off') <> 'on' THEN
            RAISE EXCEPTION
                'journal rows may only be written by the Posting Engine (ADR-05)'
                USING ERRCODE = 'VN006';
        END IF;
        RETURN NEW;
    END
    $$
"""

# Named so they sort before the other BEFORE triggers on the same tables: PostgreSQL fires
# same-timing triggers in name order, and the writer guard must be the first thing to fail.
SINGLE_WRITER_TRIGGERS = (
    ("trg_journal_entries_engine_guard", "journal_entries"),
    ("trg_journal_lines_engine_guard", "journal_lines"),
)


def upgrade() -> None:
    op.add_column(
        "journal_lines",
        sa.Column(
            "is_rounding_line",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("gl_settings", sa.Column("rounding_difference_account_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_gl_settings_rounding_difference_account",
        "gl_settings",
        "gl_accounts",
        ["company_id", "rounding_difference_account_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )
    # Tenants provisioned before this migration already have the seeded FX-loss account.
    op.execute(
        """
        UPDATE gl_settings s
           SET rounding_difference_account_id = a.id
          FROM gl_accounts a
         WHERE a.company_id = s.company_id
           AND a.code = '6950'
           AND s.rounding_difference_account_id IS NULL
        """
    )

    op.execute(SINGLE_WRITER_FUNCTION)
    for name, table in SINGLE_WRITER_TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {name} BEFORE INSERT ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION kernel_require_posting_engine()"
        )

    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute(
        """
        ALTER TABLE accounting_periods
        ADD CONSTRAINT ex_accounting_periods_no_overlap
        EXCLUDE USING gist (
            company_id WITH =,
            daterange(start_date, end_date, '[]') WITH &&
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE accounting_periods DROP CONSTRAINT IF EXISTS ex_accounting_periods_no_overlap"
    )
    for name, table in SINGLE_WRITER_TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    op.execute("DROP FUNCTION IF EXISTS kernel_require_posting_engine()")
    op.drop_constraint(
        "fk_gl_settings_rounding_difference_account", "gl_settings", type_="foreignkey"
    )
    op.drop_column("gl_settings", "rounding_difference_account_id")
    op.drop_column("journal_lines", "is_rounding_line")
