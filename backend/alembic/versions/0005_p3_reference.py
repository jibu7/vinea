"""P3 reference field on journal entries.

Revision ID: 0005_p3_reference
Revises: 0004_p2_acceptance
Create Date: 2026-09-07

- `journal_entries.reference` is nullable, indexed (company_id, reference).
  Ties journal and cashbook entries to source documents (cheques, invoices, payroll runs).
"""

import sqlalchemy as sa

from alembic import op

revision = "0005_p3_reference"
down_revision = "0004_p2_acceptance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("journal_entries", sa.Column("reference", sa.String(500), nullable=True))
    op.create_index(
        "ix_journal_entries_company_reference",
        "journal_entries",
        ["company_id", "reference"],
    )


def downgrade() -> None:
    op.drop_index("ix_journal_entries_company_reference", table_name="journal_entries")
    op.drop_column("journal_entries", "reference")
