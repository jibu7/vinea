"""A statement counts the rows its format skipped for having no amount.

Revision ID: 0029_statement_no_amount_skips
Revises: 0028_audit_entity_index
Create Date: 2026-09-24

The KCB export (`docs/banking/samples/kcb-2023-12.csv`) opens with a `BALANCE B/FWD` row: a
date, a description, a balance, and nothing in either amount column. Its mapping says
`empty_amount: skip`, so the parser passes over that row instead of refusing the file. How many
rows it passed over belongs on the statement, beside `lines_skipped`. That way an import's
result, and the same import replayed on its idempotency key, both say "1 row skipped for no
amount" instead of folding it into the lines already held, which are a different fact.

`ADD COLUMN … NOT NULL DEFAULT 0`: a statement imported before this revision skipped nothing
for this reason, because the option did not exist, so zero is its true value, not a
placeholder. On Postgres 11+ a constant default is a catalogue change; no row is rewritten and
no UPDATE runs, so there is no back-fill to prove against posted rows (rule 10's other half
does not apply). `bank_statements` has no mutation trigger in any case; the append-only
triggers are on its lines.
"""

import sqlalchemy as sa

from alembic import op

#: Full name, through `op.f`, so the naming convention does not prefix it a second time.
CHECK = "ck_bank_statements_no_amount_count_not_negative"

revision = "0029_statement_no_amount_skips"
down_revision = "0028_audit_entity_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bank_statements",
        sa.Column("lines_skipped_no_amount", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        op.f(CHECK),
        "bank_statements",
        "lines_skipped_no_amount >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(op.f(CHECK), "bank_statements", type_="check")
    op.drop_column("bank_statements", "lines_skipped_no_amount")
