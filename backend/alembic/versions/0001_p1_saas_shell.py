"""P1 SaaS shell: tenancy, identity, memberships, roles, audit log, company setup + RLS

Revision ID: 0001_p1_saas_shell
Revises:
Create Date: 2026-09-04

Every table carrying `company_id` gets `ENABLE`/`FORCE ROW LEVEL SECURITY` and the
`tenant_isolation` policy (ADR-01). `tests/test_rls_linter.py` fails if a later
migration adds a tenant table without one.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_p1_saas_shell"
down_revision = None
branch_labels = None
depends_on = None

company_status = postgresql.ENUM(
    "active", "suspended", "closed", name="company_status", create_type=False
)
membership_status = postgresql.ENUM(
    "pending", "active", "suspended", name="membership_status", create_type=False
)
period_status = postgresql.ENUM(
    "future", "open", "closed", "locked", name="period_status", create_type=False
)
tax_nature = postgresql.ENUM(
    "output", "input", "exempt", "zero_rated", name="tax_nature", create_type=False
)
user_token_purpose = postgresql.ENUM(
    "password_reset", "email_verification", name="user_token_purpose", create_type=False
)

ENUM_TYPES = (
    company_status,
    membership_status,
    period_status,
    tax_nature,
    user_token_purpose,
)

TENANT_TABLES = (
    "branches",
    "currencies",
    "tax_codes",
    "fiscal_years",
    "accounting_periods",
    "roles",
    "company_memberships",
    "membership_roles",
    "audit_log",
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


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in ENUM_TYPES:
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("is_platform_admin", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True)),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        *_audit_columns(),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "companies",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("tin", sa.String(20)),
        sa.Column("vat_registered", sa.Boolean(), nullable=False),
        sa.Column("fiscal_country", sa.String(2), nullable=False),
        sa.Column("address", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("plan_code", sa.String(50)),
        sa.Column("status", company_status, nullable=False),
        sa.Column("coa_template", sa.String(50), nullable=False),
        sa.Column("suspended_at", sa.DateTime(timezone=True)),
        sa.Column("suspension_reason", sa.String(500)),
        *_audit_columns(),
    )

    op.create_table(
        "user_tokens",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", user_token_purpose, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        *_audit_columns(),
        sa.UniqueConstraint("token_hash", name="uq_user_tokens_token_hash"),
    )
    op.create_index("ix_user_tokens_user_id", "user_tokens", ["user_id"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "replaced_by_id",
            sa.BigInteger(),
            sa.ForeignKey("refresh_tokens.id", ondelete="SET NULL"),
        ),
        sa.Column("user_agent", sa.String(255)),
        sa.Column("ip_address", sa.String(45)),
        *_audit_columns(),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])

    op.create_table(
        "branches",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("is_main", sa.Boolean(), nullable=False),
        sa.Column("address", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "code", name="uq_branches_company_code"),
    )
    op.create_index("ix_branches_company_id", "branches", ["company_id"])

    op.create_table(
        "currencies",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("code", sa.String(3), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("symbol", sa.String(10)),
        sa.Column("decimal_places", sa.SmallInteger(), nullable=False),
        sa.Column("is_base", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "code", name="uq_currencies_company_code"),
    )
    op.create_index("ix_currencies_company_id", "currencies", ["company_id"])
    op.create_index(
        "uq_currencies_company_base",
        "currencies",
        ["company_id"],
        unique=True,
        postgresql_where=sa.text("is_base"),
    )

    op.create_table(
        "tax_codes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("nature", tax_nature, nullable=False),
        sa.Column("rate_pct", sa.Numeric(20, 10), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date()),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "code", name="uq_tax_codes_company_code"),
    )
    op.create_index("ix_tax_codes_company_id", "tax_codes", ["company_id"])

    op.create_table(
        "fiscal_years",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("status", period_status, nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "name", name="uq_fiscal_years_company_name"),
    )
    op.create_index("ix_fiscal_years_company_id", "fiscal_years", ["company_id"])

    op.create_table(
        "accounting_periods",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column(
            "fiscal_year_id",
            sa.BigInteger(),
            sa.ForeignKey("fiscal_years.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_no", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("status", period_status, nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint(
            "fiscal_year_id", "period_no", name="uq_accounting_periods_fiscal_year_period_no"
        ),
    )
    op.create_index("ix_accounting_periods_company_id", "accounting_periods", ["company_id"])
    op.create_index(
        "ix_accounting_periods_fiscal_year_id", "accounting_periods", ["fiscal_year_id"]
    )

    op.create_table(
        "roles",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(255)),
        sa.Column("permissions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "name", name="uq_roles_company_name"),
    )
    op.create_index("ix_roles_company_id", "roles", ["company_id"])

    op.create_table(
        "company_memberships",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("is_owner", sa.Boolean(), nullable=False),
        sa.Column("status", membership_status, nullable=False),
        sa.Column("invite_token_hash", sa.String(64)),
        sa.Column("invite_expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "invited_by_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("invited_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        *_audit_columns(),
        sa.UniqueConstraint(
            "user_id", "company_id", name="uq_company_memberships_user_company"
        ),
        sa.UniqueConstraint(
            "company_id", "email", name="uq_company_memberships_company_email"
        ),
        sa.UniqueConstraint(
            "invite_token_hash", name="uq_company_memberships_invite_token_hash"
        ),
    )
    op.create_index("ix_company_memberships_company_id", "company_memberships", ["company_id"])
    op.create_index("ix_company_memberships_user_id", "company_memberships", ["user_id"])

    op.create_table(
        "membership_roles",
        sa.Column(
            "membership_id",
            sa.BigInteger(),
            sa.ForeignKey("company_memberships.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role_id",
            sa.BigInteger(),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        _company_column(),
        *_audit_columns(),
    )
    op.create_index("ix_membership_roles_company_id", "membership_roles", ["company_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column(
            "actor_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("actor_email", sa.String(320)),
        sa.Column(
            "impersonated_by_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity", sa.String(100), nullable=False),
        sa.Column("entity_id", sa.String(64)),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("user_agent", sa.String(255)),
    )
    op.create_index("ix_audit_log_company_id", "audit_log", ["company_id"])
    op.create_index("ix_audit_log_company_at", "audit_log", ["company_id", "at"])

    # --- Row Level Security (ADR-01) -------------------------------------------------
    op.execute(
        """
        CREATE FUNCTION app_current_company_id() RETURNS bigint
        LANGUAGE sql STABLE AS $$
            SELECT nullif(current_setting('app.company_id', true), '')::bigint
        $$
        """
    )
    # Platform-level work (login, invitation accept, provisioning, operator console) has
    # no tenant yet; those code paths opt in explicitly via app.db.platform_scope().
    op.execute(
        """
        CREATE FUNCTION app_platform_mode() RETURNS boolean
        LANGUAGE sql STABLE AS $$
            SELECT coalesce(current_setting('app.platform_mode', true), 'off') = 'on'
        $$
        """
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
    op.execute("DROP FUNCTION IF EXISTS app_platform_mode()")
    op.execute("DROP FUNCTION IF EXISTS app_current_company_id()")

    op.drop_table("audit_log")
    op.drop_table("membership_roles")
    op.drop_table("company_memberships")
    op.drop_table("roles")
    op.drop_table("accounting_periods")
    op.drop_table("fiscal_years")
    op.drop_table("tax_codes")
    op.drop_table("currencies")
    op.drop_table("branches")
    op.drop_table("refresh_tokens")
    op.drop_table("user_tokens")
    op.drop_table("companies")
    op.drop_table("users")

    bind = op.get_bind()
    for enum_type in ENUM_TYPES:
        enum_type.drop(bind, checkfirst=True)
