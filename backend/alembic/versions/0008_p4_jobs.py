"""P4 step 5 — background jobs and their artifacts (statement PDFs).

Revision ID: 0008_p4_jobs
Revises: 0007_p4_subledger
Create Date: 2026-09-09
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008_p4_jobs"
down_revision = "0007_p4_subledger"
branch_labels = None
depends_on = None

job_status = postgresql.ENUM(
    "queued", "running", "succeeded", "failed", name="job_status", create_type=False
)


def upgrade() -> None:
    job_status.create(op.get_bind(), checkfirst=True)
    now = sa.func.now()
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "company_id",
            sa.BigInteger(),
            sa.ForeignKey("companies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("status", job_status, nullable=False),
        sa.Column("params", postgresql.JSONB()),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column("artifact_name", sa.String(200)),
        sa.Column("artifact_content_type", sa.String(100)),
        sa.Column("artifact", sa.LargeBinary()),
        sa.Column("requested_by", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_by", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.create_index("ix_jobs_company_id", "jobs", ["company_id"])
    op.create_index("ix_jobs_company_status", "jobs", ["company_id", "status"])

    op.execute("ALTER TABLE jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE jobs FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON jobs
        USING (company_id = app_current_company_id() OR app_platform_mode())
        WITH CHECK (company_id = app_current_company_id() OR app_platform_mode())
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON jobs")
    op.drop_table("jobs")
    job_status.drop(op.get_bind(), checkfirst=True)
