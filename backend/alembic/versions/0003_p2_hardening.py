"""P2 hardening: transaction-type master (4th link of the determination chain) and the
idempotency payload fingerprint.

Revision ID: 0003_p2_hardening
Revises: 0002_p2_ledger_kernel
Create Date: 2026-09-04
"""

import sqlalchemy as sa

from alembic import op

revision = "0003_p2_hardening"
down_revision = "0002_p2_ledger_kernel"
branch_labels = None
depends_on = None

TENANT_TABLES = ("gl_transaction_types",)


def upgrade() -> None:
    now = sa.func.now()
    op.create_table(
        "gl_transaction_types",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "company_id",
            sa.BigInteger(),
            sa.ForeignKey("companies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("module", sa.String(10), nullable=False),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("default_gl_account_id", sa.BigInteger()),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_by", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.UniqueConstraint(
            "company_id", "module", "code", name="uq_gl_transaction_types_company_module_code"
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "default_gl_account_id"],
            ["gl_accounts.company_id", "gl_accounts.id"],
            name="fk_gl_transaction_types_default_gl_account",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "code NOT LIKE '\\_\\_%'", name=op.f("ck_gl_transaction_types_code_not_reserved")
        ),
    )
    op.create_index("ix_gl_transaction_types_company_id", "gl_transaction_types", ["company_id"])

    op.add_column("journal_entries", sa.Column("idempotency_hash", sa.String(64)))

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
    op.drop_column("journal_entries", "idempotency_hash")
    op.drop_table("gl_transaction_types")
