"""P4 step 7 — partner documents record the transaction type they posted under.

A journal batch line posts an ordinary invoice- or credit-note-shaped document (the control
account moves the same way) but under the JNL transaction type. `kind` therefore stops being
enough to say what a document *is*: a JNL debit is shaped like an invoice and is not one.

Everything user-facing — enquiries, statements, listings, the allocation screen — reads this
column, and so must any figure derived from sales or purchases. Sales analysis and VAT returns
arrive in later phases; keying them on `kind` would silently sweep journal debits into sales.

Revision ID: 0011_p4_doc_txn_type
Revises: 0010_p4_rounding_account
"""

import sqlalchemy as sa

from alembic import op

# `alembic_version.version_num` is varchar(32), so revision ids stay short.
revision = "0011_p4_doc_txn_type"
down_revision = "0010_p4_rounding_account"
branch_labels = None
depends_on = None

# (role, kind) -> transaction type, mirroring DOCUMENT_MATRIX at the time of this revision.
BACKFILL = (
    ("ar", "invoice", "INV"),
    ("ar", "credit_note", "CRN"),
    ("ar", "settlement", "RCT"),
    ("ap", "invoice", "INV"),
    ("ap", "credit_note", "DBN"),
    ("ap", "settlement", "PMT"),
)


def upgrade() -> None:
    op.add_column(
        "partner_documents", sa.Column("transaction_type", sa.String(length=30), nullable=True)
    )
    for role, kind, transaction_type in BACKFILL:
        op.execute(
            sa.text(
                """
                UPDATE partner_documents
                   SET transaction_type = :transaction_type
                 WHERE transaction_type IS NULL
                   AND role = CAST(:role AS partner_role)
                   AND kind = CAST(:kind AS partner_document_kind)
                """
            ).bindparams(transaction_type=transaction_type, role=role, kind=kind)
        )
    op.alter_column("partner_documents", "transaction_type", nullable=False)
    op.create_index(
        "ix_partner_documents_company_transaction_type",
        "partner_documents",
        ["company_id", "role", "transaction_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_partner_documents_company_transaction_type", table_name="partner_documents")
    op.drop_column("partner_documents", "transaction_type")
