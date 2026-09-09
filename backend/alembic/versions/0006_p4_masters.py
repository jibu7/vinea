"""P4 step 1 — AR/AP masters: partners, per-role settings, contacts, sales reps,
payment terms, ageing bucket sets, and the AR/AP defaults on `gl_settings`.

Revision ID: 0006_p4_masters
Revises: 0005_p3_reference
Create Date: 2026-09-09

One `partners` table serves both roles; `partner_ar_settings` / `partner_ap_settings` hold
what differs. Every new table carries `company_id` with ENABLE + FORCE RLS and the
`tenant_isolation` policy (ADR-01), so `tests/test_rls_linter.py` stays green.

Existing tenants are back-filled: the four new accounts (post-dated receivable/payable,
settlement discount granted/received), the `gl_settings` AR/AP defaults, the AR/AP
transaction types, the default payment terms and the 30/60/90/120+ ageing bucket set.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006_p4_masters"
down_revision = "0005_p3_reference"
branch_labels = None
depends_on = None

partner_role = postgresql.ENUM("ar", "ap", name="partner_role", create_type=False)
tax_mode = postgresql.ENUM("exclusive", "inclusive", name="tax_mode", create_type=False)
due_basis = postgresql.ENUM(
    "days_from_document_date",
    "days_from_end_of_month",
    "fixed_day_of_month",
    name="due_basis",
    create_type=False,
)
ageing_basis = postgresql.ENUM(
    "document_date", "due_date", name="ageing_basis", create_type=False
)

ENUM_TYPES = (partner_role, tax_mode, due_basis, ageing_basis)

TENANT_TABLES = (
    "partners",
    "partner_ar_settings",
    "partner_ap_settings",
    "partner_contacts",
    "sales_reps",
    "payment_terms",
    "ageing_bucket_sets",
    "ageing_buckets",
)

MONEY = sa.Numeric(20, 6)
PERCENT = sa.Numeric(20, 10)

SETTINGS_ACCOUNTS = (
    ("realized_fx_gain_account_id", "realized_fx_gain_account"),
    ("realized_fx_loss_account_id", "realized_fx_loss_account"),
    ("settlement_discount_granted_account_id", "settlement_discount_granted_account"),
    ("settlement_discount_received_account_id", "settlement_discount_received_account"),
    ("post_dated_receivable_account_id", "post_dated_receivable_account"),
    ("post_dated_payable_account_id", "post_dated_payable_account"),
    ("ar_control_account_id", "ar_control_account"),
    ("ap_control_account_id", "ap_control_account"),
)

# (code, name, class, parent code) — added to `rw_sme_v1` by this phase.
NEW_ACCOUNTS = (
    ("1250", "Post-dated Receivables", "asset", "1100"),
    ("2150", "Post-dated Payables", "liability", "2000"),
    ("4350", "Settlement Discount Received", "income", "4000"),
    ("6960", "Settlement Discount Granted", "expense", "6000"),
)

SUBLEDGER_TRANSACTION_TYPES = (
    ("ar", "INV", "Customer invoice", "4100"),
    ("ar", "CRN", "Customer credit note", "4100"),
    ("ar", "RCT", "Customer receipt", "1120"),
    ("ar", "JNL", "AR journal", None),
    ("ap", "INV", "Supplier invoice", "6990"),
    ("ap", "DBN", "Return to supplier (debit note)", "6990"),
    ("ap", "PMT", "Supplier payment", "1120"),
    ("ap", "JNL", "AP journal", None),
)

DEFAULT_PAYMENT_TERMS = (
    ("COD", "Cash on delivery", "days_from_document_date", 0, "0", 0),
    ("NET30", "30 days from invoice", "days_from_document_date", 30, "0", 0),
    ("NET60", "60 days from invoice", "days_from_document_date", 60, "0", 0),
    ("EOM30", "30 days from end of month", "days_from_end_of_month", 30, "0", 0),
    ("2/10N30", "2% within 10 days, net 30", "days_from_document_date", 30, "2", 10),
)

DEFAULT_AGEING_BUCKETS = (
    ("Current", 0, 30),
    ("31 - 60", 31, 60),
    ("61 - 90", 61, 90),
    ("91 - 120", 91, 120),
    ("120+", 121, None),
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


def _role_settings_table(table: str) -> None:
    op.create_table(
        table,
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("partner_id", sa.BigInteger(), nullable=False),
        sa.Column("control_account_id", sa.BigInteger()),
        sa.Column("payment_terms_id", sa.BigInteger()),
        sa.Column("credit_limit", MONEY),
        sa.Column("sales_rep_id", sa.BigInteger()),
        sa.Column("default_tax_code_id", sa.BigInteger()),
        sa.Column("default_branch_id", sa.BigInteger()),
        sa.Column("default_project_id", sa.BigInteger()),
        sa.Column("default_gl_account_id", sa.BigInteger()),
        sa.Column("tax_mode", tax_mode, nullable=False, server_default="exclusive"),
        sa.Column("is_on_hold", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "partner_id", name=f"uq_{table}_company_partner"),
        _tenant_fk(f"fk_{table}_partner", "partner_id", "partners"),
        _tenant_fk(f"fk_{table}_control_account", "control_account_id", "gl_accounts"),
        _tenant_fk(f"fk_{table}_default_gl_account", "default_gl_account_id", "gl_accounts"),
        _tenant_fk(f"fk_{table}_payment_terms", "payment_terms_id", "payment_terms"),
        _tenant_fk(f"fk_{table}_sales_rep", "sales_rep_id", "sales_reps"),
        _tenant_fk(f"fk_{table}_default_tax_code", "default_tax_code_id", "tax_codes"),
        _tenant_fk(f"fk_{table}_default_branch", "default_branch_id", "branches"),
        _tenant_fk(f"fk_{table}_default_project", "default_project_id", "projects"),
        sa.CheckConstraint(
            "credit_limit IS NULL OR credit_limit >= 0",
            name=op.f(f"ck_{table}_credit_limit_positive"),
        ),
    )
    op.create_index(f"ix_{table}_company_id", table, ["company_id"])


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in ENUM_TYPES:
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "sales_reps",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_sales_reps_company_id_id"),
        sa.UniqueConstraint("company_id", "code", name="uq_sales_reps_company_code"),
    )
    op.create_index("ix_sales_reps_company_id", "sales_reps", ["company_id"])

    op.create_table(
        "payment_terms",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("due_basis", due_basis, nullable=False),
        sa.Column("due_days", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("due_day_of_month", sa.SmallInteger()),
        sa.Column("discount_percent", PERCENT, nullable=False, server_default="0"),
        sa.Column("discount_days", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_payment_terms_company_id_id"),
        sa.UniqueConstraint("company_id", "code", name="uq_payment_terms_company_code"),
        sa.CheckConstraint("due_days >= 0", name=op.f("ck_payment_terms_due_days_positive")),
        sa.CheckConstraint(
            "discount_percent >= 0 AND discount_percent < 100",
            name=op.f("ck_payment_terms_discount_percent_range"),
        ),
        sa.CheckConstraint(
            "discount_days >= 0", name=op.f("ck_payment_terms_discount_days_positive")
        ),
        sa.CheckConstraint(
            "(due_basis <> 'fixed_day_of_month') OR (due_day_of_month BETWEEN 1 AND 31)",
            name=op.f("ck_payment_terms_fixed_day_required"),
        ),
    )
    op.create_index("ix_payment_terms_company_id", "payment_terms", ["company_id"])

    op.create_table(
        "partners",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("customer_code", sa.String(30)),
        sa.Column("supplier_code", sa.String(30)),
        sa.Column("is_customer", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_supplier", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("tin", sa.String(20)),
        sa.Column("email", sa.String(320)),
        sa.Column("phone", sa.String(30)),
        sa.Column("address", postgresql.JSONB()),
        sa.Column("notes", sa.Text()),
        sa.Column("currency_id", sa.BigInteger()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_partners_company_id_id"),
        _tenant_fk("fk_partners_currency", "currency_id", "currencies"),
        sa.CheckConstraint(
            "is_customer OR is_supplier", name=op.f("ck_partners_at_least_one_role")
        ),
        sa.CheckConstraint(
            "is_customer = (customer_code IS NOT NULL)",
            name=op.f("ck_partners_customer_code_matches"),
        ),
        sa.CheckConstraint(
            "is_supplier = (supplier_code IS NOT NULL)",
            name=op.f("ck_partners_supplier_code_matches"),
        ),
    )
    op.create_index("ix_partners_company_id", "partners", ["company_id"])
    op.create_index("ix_partners_company_name", "partners", ["company_id", "name"])
    op.create_index(
        "uq_partners_company_customer_code",
        "partners",
        ["company_id", "customer_code"],
        unique=True,
        postgresql_where=sa.text("customer_code IS NOT NULL"),
    )
    op.create_index(
        "uq_partners_company_supplier_code",
        "partners",
        ["company_id", "supplier_code"],
        unique=True,
        postgresql_where=sa.text("supplier_code IS NOT NULL"),
    )

    _role_settings_table("partner_ar_settings")
    _role_settings_table("partner_ap_settings")

    op.create_table(
        "partner_contacts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("partner_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(100)),
        sa.Column("email", sa.String(320)),
        sa.Column("phone", sa.String(30)),
        sa.Column("notes", sa.Text()),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_audit_columns(),
        _tenant_fk("fk_partner_contacts_partner", "partner_id", "partners"),
    )
    op.create_index("ix_partner_contacts_company_id", "partner_contacts", ["company_id"])
    op.create_index(
        "ix_partner_contacts_company_partner", "partner_contacts", ["company_id", "partner_id"]
    )

    op.create_table(
        "ageing_bucket_sets",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("basis", ageing_basis, nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_ageing_bucket_sets_company_id_id"),
        sa.UniqueConstraint("company_id", "code", name="uq_ageing_bucket_sets_company_code"),
    )
    op.create_index("ix_ageing_bucket_sets_company_id", "ageing_bucket_sets", ["company_id"])
    op.create_index(
        "uq_ageing_bucket_sets_company_default",
        "ageing_bucket_sets",
        ["company_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    op.create_table(
        "ageing_buckets",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("bucket_set_id", sa.BigInteger(), nullable=False),
        sa.Column("sequence", sa.SmallInteger(), nullable=False),
        sa.Column("label", sa.String(50), nullable=False),
        sa.Column("from_days", sa.SmallInteger(), nullable=False),
        sa.Column("to_days", sa.SmallInteger()),
        *_audit_columns(),
        sa.UniqueConstraint("bucket_set_id", "sequence", name="uq_ageing_buckets_set_sequence"),
        _tenant_fk(
            "fk_ageing_buckets_set", "bucket_set_id", "ageing_bucket_sets", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "to_days IS NULL OR to_days >= from_days", name=op.f("ck_ageing_buckets_bucket_range")
        ),
    )
    op.create_index("ix_ageing_buckets_company_id", "ageing_buckets", ["company_id"])
    op.create_index(
        "ix_ageing_buckets_company_set", "ageing_buckets", ["company_id", "bucket_set_id"]
    )

    # --- gl_settings gains the AR/AP defaults --------------------------------------------
    for column, constraint in SETTINGS_ACCOUNTS:
        op.add_column("gl_settings", sa.Column(column, sa.BigInteger()))
        op.create_foreign_key(
            f"fk_gl_settings_{constraint}",
            "gl_settings",
            "gl_accounts",
            ["company_id", column],
            ["company_id", "id"],
            ondelete="RESTRICT",
        )

    _backfill_existing_tenants()

    # --- Row Level Security (ADR-01) -----------------------------------------------------
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


def _backfill_existing_tenants() -> None:
    """Tenants provisioned before P4 get the same seed the pack now produces."""
    for code, name, class_, parent_code in NEW_ACCOUNTS:
        op.execute(
            sa.text(
                """
                INSERT INTO gl_accounts
                    (company_id, code, name, class, parent_id, is_postable, is_control,
                     control_type, is_active)
                SELECT p.company_id, :code, :name, CAST(:class AS account_class), p.id,
                       true, false, NULL, true
                  FROM gl_accounts p
                 WHERE p.code = :parent_code
                   AND NOT EXISTS (
                       SELECT 1 FROM gl_accounts existing
                        WHERE existing.company_id = p.company_id AND existing.code = :code
                   )
                """
            ).bindparams(code=code, name=name, **{"class": class_}, parent_code=parent_code)
        )

    for column, account_code in (
        ("realized_fx_gain_account_id", "4400"),
        ("realized_fx_loss_account_id", "6950"),
        ("settlement_discount_granted_account_id", "6960"),
        ("settlement_discount_received_account_id", "4350"),
        ("post_dated_receivable_account_id", "1250"),
        ("post_dated_payable_account_id", "2150"),
        ("ar_control_account_id", "1200"),
        ("ap_control_account_id", "2100"),
    ):
        op.execute(
            sa.text(
                f"""
                UPDATE gl_settings s
                   SET {column} = a.id
                  FROM gl_accounts a
                 WHERE a.company_id = s.company_id
                   AND a.code = :account_code
                   AND s.{column} IS NULL
                """
            ).bindparams(account_code=account_code)
        )

    for module, code, name, account_code in SUBLEDGER_TRANSACTION_TYPES:
        op.execute(
            sa.text(
                """
                INSERT INTO gl_transaction_types
                    (company_id, module, code, name, default_gl_account_id, is_active)
                SELECT c.id, :module, :code, :name,
                       (SELECT a.id FROM gl_accounts a
                         WHERE a.company_id = c.id AND a.code = :account_code),
                       true
                  FROM companies c
                 WHERE NOT EXISTS (
                     SELECT 1 FROM gl_transaction_types t
                      WHERE t.company_id = c.id AND t.module = :module AND t.code = :code
                 )
                """
            ).bindparams(module=module, code=code, name=name, account_code=account_code)
        )

    for code, name, basis, due_days, discount_pct, discount_days in DEFAULT_PAYMENT_TERMS:
        op.execute(
            sa.text(
                """
                INSERT INTO payment_terms
                    (company_id, code, name, due_basis, due_days, discount_percent,
                     discount_days, is_active)
                SELECT c.id, :code, :name, CAST(:basis AS due_basis), :due_days,
                       CAST(:discount_pct AS numeric), :discount_days, true
                  FROM companies c
                 WHERE NOT EXISTS (
                     SELECT 1 FROM payment_terms t WHERE t.company_id = c.id AND t.code = :code
                 )
                """
            ).bindparams(
                code=code,
                name=name,
                basis=basis,
                due_days=due_days,
                discount_pct=discount_pct,
                discount_days=discount_days,
            )
        )

    op.execute(
        """
        INSERT INTO ageing_bucket_sets (company_id, code, name, basis, is_default, is_active)
        SELECT c.id, 'STD', 'Standard 30/60/90/120+', 'due_date', true, true
          FROM companies c
         WHERE NOT EXISTS (
             SELECT 1 FROM ageing_bucket_sets s WHERE s.company_id = c.id AND s.code = 'STD'
         )
        """
    )
    for sequence, (label, from_days, to_days) in enumerate(DEFAULT_AGEING_BUCKETS):
        op.execute(
            sa.text(
                """
                INSERT INTO ageing_buckets
                    (company_id, bucket_set_id, sequence, label, from_days, to_days)
                SELECT s.company_id, s.id, :sequence, :label, :from_days, :to_days
                  FROM ageing_bucket_sets s
                 WHERE s.code = 'STD'
                   AND NOT EXISTS (
                       SELECT 1 FROM ageing_buckets b
                        WHERE b.bucket_set_id = s.id AND b.sequence = :sequence
                   )
                """
            ).bindparams(
                sequence=sequence, label=label, from_days=from_days, to_days=to_days
            )
        )


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    for column, constraint in SETTINGS_ACCOUNTS:
        op.drop_constraint(f"fk_gl_settings_{constraint}", "gl_settings", type_="foreignkey")
        op.drop_column("gl_settings", column)
    op.drop_table("ageing_buckets")
    op.drop_table("ageing_bucket_sets")
    op.drop_table("partner_contacts")
    op.drop_table("partner_ap_settings")
    op.drop_table("partner_ar_settings")
    op.drop_table("partners")
    op.drop_table("payment_terms")
    op.drop_table("sales_reps")
    bind = op.get_bind()
    for enum_type in ENUM_TYPES:
        enum_type.drop(bind, checkfirst=True)
