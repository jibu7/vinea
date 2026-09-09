"""P4 step 2 — the subledger posting contract: partner documents, allocations, and the
control-account guard enforced by the database.

Revision ID: 0007_p4_subledger
Revises: 0006_p4_masters
Create Date: 2026-09-09

What the database itself enforces after this migration:
- `journal_entries.module` records the module that emitted the entry;
- a journal line touching an AR/AP control account is refused unless the entry's module
  matches that control type (SQLSTATE VN007) — a manual journal aimed at the AR control
  account fails, with no escape hatch;
- such a line must carry the matching partner dimension (VN008), so the control-account
  invariant `SUM(partner open items) == control balance` is always checkable.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007_p4_subledger"
down_revision = "0006_p4_masters"
branch_labels = None
depends_on = None

partner_role = postgresql.ENUM("ar", "ap", name="partner_role", create_type=False)
tax_mode = postgresql.ENUM("exclusive", "inclusive", name="tax_mode", create_type=False)
document_kind = postgresql.ENUM(
    "invoice", "credit_note", "settlement", name="partner_document_kind", create_type=False
)
document_status = postgresql.ENUM(
    "posted", "reversed", name="partner_document_status", create_type=False
)
instrument_type = postgresql.ENUM(
    "cash", "bank", "cheque", "mobile", "other", name="instrument_type", create_type=False
)

NEW_ENUM_TYPES = (document_kind, document_status, instrument_type)

TENANT_TABLES = (
    "partner_documents",
    "partner_document_lines",
    "allocations",
    "allocation_lines",
)

MONEY = sa.Numeric(20, 6)
RATE = sa.Numeric(20, 10)

SUBLEDGER_GUARD_FUNCTION = """
    CREATE FUNCTION kernel_check_subledger_line() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        v_control gl_control_type;
        v_code text;
        v_module text;
        v_expected_partner text;
    BEGIN
        SELECT a.control_type, a.code INTO v_control, v_code
          FROM gl_accounts a WHERE a.id = NEW.gl_account_id;
        IF v_control IS NULL OR v_control NOT IN ('ar', 'ap') THEN
            RETURN NEW;
        END IF;

        SELECT e.module INTO v_module FROM journal_entries e WHERE e.id = NEW.entry_id;
        IF v_module IS DISTINCT FROM v_control::text THEN
            RAISE EXCEPTION
                'account % is the % control account; post through the subledger',
                v_code, upper(v_control::text)
                USING ERRCODE = 'VN007';
        END IF;

        v_expected_partner := CASE v_control WHEN 'ar' THEN 'customer' ELSE 'supplier' END;
        IF NEW.partner_id IS NULL OR NEW.partner_type IS DISTINCT FROM v_expected_partner THEN
            RAISE EXCEPTION 'account % requires a % on the line', v_code, v_expected_partner
                USING ERRCODE = 'VN008';
        END IF;
        RETURN NEW;
    END
    $$
"""

# Sorts after `trg_journal_lines_engine_guard`, so the single-writer rule still fails first.
SUBLEDGER_GUARD_TRIGGER = (
    "trg_journal_lines_subledger_guard",
    "journal_lines",
    "BEFORE INSERT ON journal_lines FOR EACH ROW "
    "EXECUTE FUNCTION kernel_check_subledger_line()",
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


def _tenant_fk(name: str, local: str, table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["company_id", local],
        [f"{table}.company_id", f"{table}.id"],
        name=name,
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in NEW_ENUM_TYPES:
        enum_type.create(bind, checkfirst=True)

    op.add_column(
        "journal_entries",
        sa.Column("module", sa.String(10), nullable=False, server_default=sa.text("'gl'")),
    )

    op.create_table(
        "partner_documents",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("role", partner_role, nullable=False),
        sa.Column("kind", document_kind, nullable=False),
        sa.Column("number", sa.String(30), nullable=False),
        sa.Column("doc_type", sa.String(10), nullable=False),
        sa.Column("partner_id", sa.BigInteger(), nullable=False),
        sa.Column("journal_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("document_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date()),
        sa.Column("currency_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange_rate", RATE, nullable=False),
        sa.Column("branch_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger()),
        sa.Column("payment_terms_id", sa.BigInteger()),
        sa.Column("sales_rep_id", sa.BigInteger()),
        sa.Column("tax_mode", tax_mode, nullable=False),
        sa.Column("control_account_id", sa.BigInteger(), nullable=False),
        sa.Column("reference", sa.String(500)),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("net_amount", MONEY, nullable=False),
        sa.Column("tax_amount", MONEY, nullable=False),
        sa.Column("total_amount", MONEY, nullable=False),
        sa.Column("base_total_amount", MONEY, nullable=False),
        sa.Column("open_amount", MONEY, nullable=False),
        sa.Column("direction", sa.SmallInteger(), nullable=False),
        sa.Column("instrument_type", instrument_type),
        sa.Column("maturity_date", sa.Date()),
        sa.Column("cash_account_id", sa.BigInteger()),
        sa.Column("matured_entry_id", sa.BigInteger()),
        sa.Column("status", document_status, nullable=False),
        sa.Column("reversal_entry_id", sa.BigInteger()),
        sa.Column("reversed_on", sa.Date()),
        sa.Column("idempotency_key", sa.String(64)),
        sa.Column("idempotency_hash", sa.String(64)),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_partner_documents_company_id_id"),
        sa.UniqueConstraint("company_id", "number", name="uq_partner_documents_company_number"),
        _tenant_fk("fk_partner_documents_partner", "partner_id", "partners"),
        _tenant_fk("fk_partner_documents_journal_entry", "journal_entry_id", "journal_entries"),
        _tenant_fk("fk_partner_documents_reversal_entry", "reversal_entry_id", "journal_entries"),
        _tenant_fk("fk_partner_documents_matured_entry", "matured_entry_id", "journal_entries"),
        _tenant_fk("fk_partner_documents_currency", "currency_id", "currencies"),
        _tenant_fk("fk_partner_documents_branch", "branch_id", "branches"),
        _tenant_fk("fk_partner_documents_project", "project_id", "projects"),
        _tenant_fk("fk_partner_documents_control_account", "control_account_id", "gl_accounts"),
        _tenant_fk("fk_partner_documents_cash_account", "cash_account_id", "gl_accounts"),
        _tenant_fk("fk_partner_documents_payment_terms", "payment_terms_id", "payment_terms"),
        _tenant_fk("fk_partner_documents_sales_rep", "sales_rep_id", "sales_reps"),
        sa.CheckConstraint(
            "direction IN (-1, 1)", name=op.f("ck_partner_documents_direction_sign")
        ),
        sa.CheckConstraint("total_amount > 0", name=op.f("ck_partner_documents_total_positive")),
        sa.CheckConstraint(
            "open_amount >= 0 AND open_amount <= total_amount",
            name=op.f("ck_partner_documents_open_amount_range"),
        ),
        sa.CheckConstraint(
            "exchange_rate > 0", name=op.f("ck_partner_documents_positive_exchange_rate")
        ),
    )
    op.create_index("ix_partner_documents_company_id", "partner_documents", ["company_id"])
    op.create_index(
        "ix_partner_documents_company_partner",
        "partner_documents",
        ["company_id", "role", "partner_id"],
    )
    op.create_index(
        "ix_partner_documents_company_date", "partner_documents", ["company_id", "document_date"]
    )
    op.create_index(
        "ix_partner_documents_open",
        "partner_documents",
        ["company_id", "role", "partner_id"],
        postgresql_where=sa.text("open_amount > 0"),
    )
    op.create_index(
        "uq_partner_documents_company_idempotency_key",
        "partner_documents",
        ["company_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "partner_document_lines",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("line_no", sa.SmallInteger(), nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("quantity", MONEY, nullable=False),
        sa.Column("unit_price", MONEY, nullable=False),
        sa.Column("discount_percent", RATE, nullable=False, server_default="0"),
        sa.Column("gl_account_id", sa.BigInteger(), nullable=False),
        sa.Column("tax_code_id", sa.BigInteger()),
        sa.Column("branch_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger()),
        sa.Column("net_amount", MONEY, nullable=False),
        sa.Column("tax_amount", MONEY, nullable=False),
        sa.Column("gross_amount", MONEY, nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint("document_id", "line_no", name="uq_partner_document_lines_line_no"),
        _tenant_fk("fk_partner_document_lines_document", "document_id", "partner_documents"),
        _tenant_fk("fk_partner_document_lines_gl_account", "gl_account_id", "gl_accounts"),
        _tenant_fk("fk_partner_document_lines_tax_code", "tax_code_id", "tax_codes"),
        _tenant_fk("fk_partner_document_lines_branch", "branch_id", "branches"),
        _tenant_fk("fk_partner_document_lines_project", "project_id", "projects"),
    )
    op.create_index(
        "ix_partner_document_lines_company_id", "partner_document_lines", ["company_id"]
    )
    op.create_index(
        "ix_partner_document_lines_document",
        "partner_document_lines",
        ["company_id", "document_id"],
    )

    op.create_table(
        "allocations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("number", sa.String(30), nullable=False),
        sa.Column("role", partner_role, nullable=False),
        sa.Column("partner_id", sa.BigInteger(), nullable=False),
        sa.Column("currency_id", sa.BigInteger(), nullable=False),
        sa.Column("allocation_date", sa.Date(), nullable=False),
        sa.Column("journal_entry_id", sa.BigInteger()),
        sa.Column("reverses_allocation_id", sa.BigInteger()),
        sa.Column("description", sa.String(500)),
        sa.Column("idempotency_key", sa.String(64)),
        sa.Column("idempotency_hash", sa.String(64)),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_allocations_company_id_id"),
        sa.UniqueConstraint("company_id", "number", name="uq_allocations_company_number"),
        _tenant_fk("fk_allocations_partner", "partner_id", "partners"),
        _tenant_fk("fk_allocations_currency", "currency_id", "currencies"),
        _tenant_fk("fk_allocations_journal_entry", "journal_entry_id", "journal_entries"),
        _tenant_fk("fk_allocations_reverses_allocation", "reverses_allocation_id", "allocations"),
    )
    op.create_index("ix_allocations_company_id", "allocations", ["company_id"])
    op.create_index(
        "ix_allocations_company_partner", "allocations", ["company_id", "role", "partner_id"]
    )
    op.create_index(
        "ix_allocations_company_date", "allocations", ["company_id", "allocation_date"]
    )
    op.create_index(
        "uq_allocations_reverses_allocation_id",
        "allocations",
        ["reverses_allocation_id"],
        unique=True,
        postgresql_where=sa.text("reverses_allocation_id IS NOT NULL"),
    )
    op.create_index(
        "uq_allocations_company_idempotency_key",
        "allocations",
        ["company_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "allocation_lines",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("allocation_id", sa.BigInteger(), nullable=False),
        sa.Column("line_no", sa.SmallInteger(), nullable=False),
        sa.Column("debit_document_id", sa.BigInteger(), nullable=False),
        sa.Column("credit_document_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("discount_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("discount_document_id", sa.BigInteger()),
        sa.Column("fx_base_amount", MONEY, nullable=False, server_default="0"),
        *_audit_columns(),
        sa.UniqueConstraint("allocation_id", "line_no", name="uq_allocation_lines_line_no"),
        _tenant_fk("fk_allocation_lines_allocation", "allocation_id", "allocations"),
        _tenant_fk("fk_allocation_lines_debit_document", "debit_document_id", "partner_documents"),
        _tenant_fk(
            "fk_allocation_lines_credit_document", "credit_document_id", "partner_documents"
        ),
        _tenant_fk(
            "fk_allocation_lines_discount_document", "discount_document_id", "partner_documents"
        ),
        sa.CheckConstraint("amount <> 0", name=op.f("ck_allocation_lines_amount_not_zero")),
        sa.CheckConstraint(
            "(discount_amount = 0) = (discount_document_id IS NULL)",
            name=op.f("ck_allocation_lines_discount_document_with_amount"),
        ),
        sa.CheckConstraint(
            "debit_document_id <> credit_document_id",
            name=op.f("ck_allocation_lines_distinct_documents"),
        ),
    )
    op.create_index("ix_allocation_lines_company_id", "allocation_lines", ["company_id"])
    op.create_index(
        "ix_allocation_lines_debit", "allocation_lines", ["company_id", "debit_document_id"]
    )
    op.create_index(
        "ix_allocation_lines_credit", "allocation_lines", ["company_id", "credit_document_id"]
    )

    op.execute(SUBLEDGER_GUARD_FUNCTION)
    name, _table, spec = SUBLEDGER_GUARD_TRIGGER
    op.execute(f"CREATE TRIGGER {name} {spec}")

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
    name, table, _spec = SUBLEDGER_GUARD_TRIGGER
    op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    op.execute("DROP FUNCTION IF EXISTS kernel_check_subledger_line()")
    op.drop_table("allocation_lines")
    op.drop_table("allocations")
    op.drop_table("partner_document_lines")
    op.drop_table("partner_documents")
    op.drop_column("journal_entries", "module")
    bind = op.get_bind()
    for enum_type in NEW_ENUM_TYPES:
        enum_type.drop(bind, checkfirst=True)
