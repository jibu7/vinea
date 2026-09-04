"""P2 Ledger Kernel: COA, projects, exchange rates, sequences, immutable journal,
period balances + the DB-level invariants (ADR-04..08).

Revision ID: 0002_p2_ledger_kernel
Revises: 0001_p1_saas_shell
Create Date: 2026-09-04

What the database itself enforces after this migration (never "by convention"):
- posted `journal_entries` / `journal_lines` cannot be UPDATEd or DELETEd, and no line can
  be added to an entry after the transaction that posted it (SQLSTATE VN001);
- every entry's `SUM(base_amount) = 0` at commit (deferred constraint trigger, VN002);
- a posted entry has at least two lines (VN005);
- lines reference active, postable accounts (VN003);
- a posted entry's period is `open` and covers `entry_date` (VN004);
- every tenant-internal reference is a composite FK `(company_id, x_id)`, so nothing can
  point at another tenant's account/branch/period/currency even though FK checks bypass RLS;
- each new `company_id` table has ENABLE + FORCE RLS and the `tenant_isolation` policy.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_p2_ledger_kernel"
down_revision = "0001_p1_saas_shell"
branch_labels = None
depends_on = None

account_class = postgresql.ENUM(
    "asset", "liability", "equity", "income", "expense", name="account_class", create_type=False
)
gl_control_type = postgresql.ENUM(
    "bank", "cash", "ar", "ap", "inventory", name="gl_control_type", create_type=False
)
journal_status = postgresql.ENUM("draft", "posted", name="journal_status", create_type=False)

ENUM_TYPES = (account_class, gl_control_type, journal_status)

TENANT_TABLES = (
    "gl_accounts",
    "gl_settings",
    "projects",
    "exchange_rates",
    "document_sequences",
    "journal_entries",
    "journal_lines",
    "period_balances",
)

MONEY = sa.Numeric(20, 6)
RATE = sa.Numeric(20, 10)


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


def _tenant_fk(name: str, local: str, table: str, remote: str = "id") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["company_id", local],
        [f"{table}.company_id", f"{table}.{remote}"],
        name=name,
        ondelete="RESTRICT",
    )


KERNEL_FUNCTIONS = {
    # (a) immutability — entries
    "kernel_block_posted_entry_mutation": """
        CREATE FUNCTION kernel_block_posted_entry_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status = 'posted' THEN
                RAISE EXCEPTION 'journal entry % is posted and immutable', OLD.id
                    USING ERRCODE = 'VN001';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END
        $$
    """,
    # (a) immutability — lines: nothing under a posted entry may change. The engine inserts
    # the header as 'draft', adds lines, then flips status to 'posted' (the only allowed
    # header update), so this needs no "same transaction" detection.
    "kernel_block_posted_line_mutation": """
        CREATE FUNCTION kernel_block_posted_line_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            v_entry_id bigint := CASE WHEN TG_OP = 'DELETE' THEN OLD.entry_id ELSE NEW.entry_id END;
            v_status journal_status;
        BEGIN
            SELECT e.status INTO v_status FROM journal_entries e WHERE e.id = v_entry_id;
            IF v_status IS NULL THEN
                RAISE EXCEPTION 'journal entry % is not visible in this context', v_entry_id
                    USING ERRCODE = 'VN001';
            END IF;
            IF v_status = 'posted' THEN
                RAISE EXCEPTION 'journal entry % is posted; its lines are immutable', v_entry_id
                    USING ERRCODE = 'VN001';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END
        $$
    """,
    # (b) balanced — deferred to commit so lines can be inserted one by one
    "kernel_assert_entry_balanced": """
        CREATE FUNCTION kernel_assert_entry_balanced() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            v_entry_id bigint := CASE WHEN TG_OP = 'DELETE' THEN OLD.entry_id ELSE NEW.entry_id END;
            v_sum numeric;
        BEGIN
            SELECT coalesce(sum(base_amount), 0) INTO v_sum
              FROM journal_lines WHERE entry_id = v_entry_id;
            IF v_sum <> 0 THEN
                RAISE EXCEPTION 'journal entry % does not balance: sum(base_amount) = %',
                    v_entry_id, v_sum USING ERRCODE = 'VN002';
            END IF;
            RETURN NULL;
        END
        $$
    """,
    # (c) postable, active account
    "kernel_check_line_account": """
        CREATE FUNCTION kernel_check_line_account() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            v_postable boolean;
            v_active boolean;
        BEGIN
            SELECT is_postable, is_active INTO v_postable, v_active
              FROM gl_accounts WHERE id = NEW.gl_account_id;
            IF v_postable IS NULL THEN
                RAISE EXCEPTION 'gl account % is not visible in this context', NEW.gl_account_id
                    USING ERRCODE = 'VN003';
            END IF;
            IF NOT v_postable OR NOT v_active THEN
                RAISE EXCEPTION 'gl account % is not an active postable account', NEW.gl_account_id
                    USING ERRCODE = 'VN003';
            END IF;
            RETURN NEW;
        END
        $$
    """,
    # (d) open period covering the entry date
    "kernel_check_entry_period": """
        CREATE FUNCTION kernel_check_entry_period() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            v_status period_status;
            v_start date;
            v_end date;
        BEGIN
            IF NEW.status <> 'posted' THEN
                RETURN NEW;
            END IF;
            SELECT status, start_date, end_date INTO v_status, v_start, v_end
              FROM accounting_periods WHERE id = NEW.period_id;
            IF v_status IS NULL THEN
                RAISE EXCEPTION 'accounting period % is not visible in this context', NEW.period_id
                    USING ERRCODE = 'VN004';
            END IF;
            IF v_status <> 'open' THEN
                RAISE EXCEPTION 'accounting period % is % — posting requires an open period',
                    NEW.period_id, v_status USING ERRCODE = 'VN004';
            END IF;
            IF NEW.entry_date < v_start OR NEW.entry_date > v_end THEN
                RAISE EXCEPTION 'entry date % lies outside accounting period %',
                    NEW.entry_date, NEW.period_id USING ERRCODE = 'VN004';
            END IF;
            RETURN NEW;
        END
        $$
    """,
    # posted entries carry at least two lines (deferred: lines land after the header)
    "kernel_assert_entry_has_lines": """
        CREATE FUNCTION kernel_assert_entry_has_lines() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            v_count integer;
        BEGIN
            IF NEW.status <> 'posted' THEN
                RETURN NULL;
            END IF;
            SELECT count(*) INTO v_count FROM journal_lines WHERE entry_id = NEW.id;
            IF v_count < 2 THEN
                RAISE EXCEPTION 'posted journal entry % needs at least two lines (has %)',
                    NEW.id, v_count USING ERRCODE = 'VN005';
            END IF;
            RETURN NULL;
        END
        $$
    """,
}

KERNEL_TRIGGERS = (
    (
        "trg_journal_entries_immutable",
        "journal_entries",
        "BEFORE UPDATE OR DELETE ON journal_entries FOR EACH ROW "
        "EXECUTE FUNCTION kernel_block_posted_entry_mutation()",
    ),
    (
        "trg_journal_entries_period_open",
        "journal_entries",
        "BEFORE INSERT OR UPDATE OF status, period_id, entry_date ON journal_entries "
        "FOR EACH ROW EXECUTE FUNCTION kernel_check_entry_period()",
    ),
    (
        "trg_journal_lines_immutable",
        "journal_lines",
        "BEFORE INSERT OR UPDATE OR DELETE ON journal_lines FOR EACH ROW "
        "EXECUTE FUNCTION kernel_block_posted_line_mutation()",
    ),
    (
        "trg_journal_lines_postable_account",
        "journal_lines",
        "BEFORE INSERT ON journal_lines FOR EACH ROW EXECUTE FUNCTION kernel_check_line_account()",
    ),
)

KERNEL_CONSTRAINT_TRIGGERS = (
    (
        "trg_journal_lines_balanced",
        "journal_lines",
        "AFTER INSERT OR UPDATE OR DELETE ON journal_lines DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION kernel_assert_entry_balanced()",
    ),
    (
        "trg_journal_entries_has_lines",
        "journal_entries",
        "AFTER INSERT OR UPDATE OF status ON journal_entries DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION kernel_assert_entry_has_lines()",
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in ENUM_TYPES:
        enum_type.create(bind, checkfirst=True)

    # Composite-FK targets on the P1 tables.
    op.create_unique_constraint(
        "uq_accounting_periods_company_id_id", "accounting_periods", ["company_id", "id"]
    )
    op.create_unique_constraint("uq_branches_company_id_id", "branches", ["company_id", "id"])
    op.create_unique_constraint("uq_currencies_company_id_id", "currencies", ["company_id", "id"])
    op.create_unique_constraint("uq_tax_codes_company_id_id", "tax_codes", ["company_id", "id"])

    op.create_table(
        "gl_accounts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("class", account_class, nullable=False),
        sa.Column("parent_id", sa.BigInteger()),
        sa.Column("is_postable", sa.Boolean(), nullable=False),
        sa.Column("is_control", sa.Boolean(), nullable=False),
        sa.Column("control_type", gl_control_type),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_gl_accounts_company_id_id"),
        sa.UniqueConstraint("company_id", "code", name="uq_gl_accounts_company_code"),
        _tenant_fk("fk_gl_accounts_parent", "parent_id", "gl_accounts"),
        sa.CheckConstraint(
            "is_control = (control_type IS NOT NULL)",
            name=op.f("ck_gl_accounts_control_type_matches_flag"),
        ),
        sa.CheckConstraint(
            "NOT (is_control AND NOT is_postable)",
            name=op.f("ck_gl_accounts_control_is_postable"),
        ),
    )
    op.create_index("ix_gl_accounts_company_id", "gl_accounts", ["company_id"])
    op.create_index("ix_gl_accounts_parent_id", "gl_accounts", ["parent_id"])

    op.create_table(
        "gl_settings",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("retained_earnings_account_id", sa.BigInteger()),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", name="uq_gl_settings_company_id"),
        _tenant_fk(
            "fk_gl_settings_retained_earnings_account",
            "retained_earnings_account_id",
            "gl_accounts",
        ),
    )
    op.create_index("ix_gl_settings_company_id", "gl_settings", ["company_id"])

    op.create_table(
        "projects",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_projects_company_id_id"),
        sa.UniqueConstraint("company_id", "code", name="uq_projects_company_code"),
    )
    op.create_index("ix_projects_company_id", "projects", ["company_id"])

    op.create_table(
        "exchange_rates",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("currency_id", sa.BigInteger(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("rate", RATE, nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint(
            "currency_id", "valid_from", name="uq_exchange_rates_currency_valid_from"
        ),
        _tenant_fk("fk_exchange_rates_currency", "currency_id", "currencies"),
        sa.CheckConstraint("rate > 0", name=op.f("ck_exchange_rates_positive_rate")),
    )
    op.create_index("ix_exchange_rates_company_id", "exchange_rates", ["company_id"])
    op.create_index("ix_exchange_rates_currency_id", "exchange_rates", ["currency_id"])

    op.create_table(
        "document_sequences",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("branch_id", sa.BigInteger()),
        sa.Column("doc_type", sa.String(10), nullable=False),
        sa.Column("prefix", sa.String(10), nullable=False),
        sa.Column("next_number", sa.BigInteger(), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint(
            "company_id",
            "doc_type",
            "branch_id",
            name="uq_document_sequences_scope",
            postgresql_nulls_not_distinct=True,
        ),
        _tenant_fk("fk_document_sequences_branch", "branch_id", "branches"),
        sa.CheckConstraint(
            "next_number >= 1", name=op.f("ck_document_sequences_next_number_positive")
        ),
    )
    op.create_index("ix_document_sequences_company_id", "document_sequences", ["company_id"])

    op.create_table(
        "journal_entries",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("number", sa.String(30), nullable=False),
        sa.Column("doc_type", sa.String(10), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("period_id", sa.BigInteger(), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("source_doc_type", sa.String(50)),
        sa.Column("source_doc_id", sa.BigInteger()),
        sa.Column("status", journal_status, nullable=False),
        sa.Column("posted_by", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column("reverses_entry_id", sa.BigInteger()),
        sa.Column("reversal_reason", sa.String(500)),
        sa.Column("idempotency_key", sa.String(64)),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_journal_entries_company_id_id"),
        sa.UniqueConstraint("company_id", "number", name="uq_journal_entries_company_number"),
        _tenant_fk("fk_journal_entries_period", "period_id", "accounting_periods"),
        _tenant_fk("fk_journal_entries_reverses_entry", "reverses_entry_id", "journal_entries"),
    )
    op.create_index("ix_journal_entries_company_id", "journal_entries", ["company_id"])
    op.create_index(
        "ix_journal_entries_company_entry_date", "journal_entries", ["company_id", "entry_date"]
    )
    op.create_index(
        "ix_journal_entries_company_period", "journal_entries", ["company_id", "period_id"]
    )
    op.create_index(
        "ix_journal_entries_company_source",
        "journal_entries",
        ["company_id", "source_doc_type", "source_doc_id"],
    )
    op.create_index(
        "uq_journal_entries_reverses_entry_id",
        "journal_entries",
        ["reverses_entry_id"],
        unique=True,
        postgresql_where=sa.text("reverses_entry_id IS NOT NULL"),
    )
    op.create_index(
        "uq_journal_entries_company_idempotency_key",
        "journal_entries",
        ["company_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "journal_lines",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("entry_id", sa.BigInteger(), nullable=False),
        sa.Column("line_no", sa.SmallInteger(), nullable=False),
        sa.Column("gl_account_id", sa.BigInteger(), nullable=False),
        sa.Column("branch_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger()),
        sa.Column("partner_type", sa.String(20)),
        sa.Column("partner_id", sa.BigInteger()),
        sa.Column("item_id", sa.BigInteger()),
        sa.Column("currency_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange_rate", RATE, nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("base_amount", MONEY, nullable=False),
        sa.Column("tax_code_id", sa.BigInteger()),
        sa.Column("tax_amount", MONEY, nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("source_doc_type", sa.String(50)),
        sa.Column("source_doc_id", sa.BigInteger()),
        sa.Column("source_line_id", sa.BigInteger()),
        *_audit_columns(),
        _tenant_fk("fk_journal_lines_entry", "entry_id", "journal_entries"),
        _tenant_fk("fk_journal_lines_gl_account", "gl_account_id", "gl_accounts"),
        _tenant_fk("fk_journal_lines_branch", "branch_id", "branches"),
        _tenant_fk("fk_journal_lines_project", "project_id", "projects"),
        _tenant_fk("fk_journal_lines_currency", "currency_id", "currencies"),
        _tenant_fk("fk_journal_lines_tax_code", "tax_code_id", "tax_codes"),
        sa.UniqueConstraint("entry_id", "line_no", name="uq_journal_lines_entry_line_no"),
        sa.CheckConstraint(
            "exchange_rate > 0", name=op.f("ck_journal_lines_positive_exchange_rate")
        ),
        sa.CheckConstraint(
            "(partner_type IS NULL) = (partner_id IS NULL)",
            name=op.f("ck_journal_lines_partner_type_with_id"),
        ),
    )
    op.create_index("ix_journal_lines_company_id", "journal_lines", ["company_id"])
    op.create_index("ix_journal_lines_entry_id", "journal_lines", ["entry_id"])
    op.create_index(
        "ix_journal_lines_company_account", "journal_lines", ["company_id", "gl_account_id"]
    )
    op.create_index("ix_journal_lines_company_branch", "journal_lines", ["company_id", "branch_id"])
    op.create_index(
        "ix_journal_lines_company_project", "journal_lines", ["company_id", "project_id"]
    )
    op.create_index(
        "ix_journal_lines_company_partner",
        "journal_lines",
        ["company_id", "partner_type", "partner_id"],
    )
    op.create_index(
        "ix_journal_lines_company_tax_code", "journal_lines", ["company_id", "tax_code_id"]
    )

    op.create_table(
        "period_balances",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("period_id", sa.BigInteger(), nullable=False),
        sa.Column("gl_account_id", sa.BigInteger(), nullable=False),
        sa.Column("branch_id", sa.BigInteger(), nullable=False),
        sa.Column("currency_id", sa.BigInteger(), nullable=False),
        sa.Column("debit_amount", MONEY, nullable=False),
        sa.Column("credit_amount", MONEY, nullable=False),
        sa.Column("debit_base", MONEY, nullable=False),
        sa.Column("credit_base", MONEY, nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint(
            "company_id",
            "period_id",
            "gl_account_id",
            "branch_id",
            "currency_id",
            name="uq_period_balances_cell",
        ),
        _tenant_fk("fk_period_balances_period", "period_id", "accounting_periods"),
        _tenant_fk("fk_period_balances_gl_account", "gl_account_id", "gl_accounts"),
        _tenant_fk("fk_period_balances_branch", "branch_id", "branches"),
        _tenant_fk("fk_period_balances_currency", "currency_id", "currencies"),
    )
    op.create_index("ix_period_balances_company_id", "period_balances", ["company_id"])

    # P1 tables gain their kernel links.
    op.add_column("fiscal_years", sa.Column("closing_entry_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_fiscal_years_closing_entry",
        "fiscal_years",
        "journal_entries",
        ["company_id", "closing_entry_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )
    op.add_column("tax_codes", sa.Column("gl_account_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_tax_codes_gl_account",
        "tax_codes",
        "gl_accounts",
        ["company_id", "gl_account_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )

    # --- Kernel invariants as triggers ---------------------------------------------
    for ddl in KERNEL_FUNCTIONS.values():
        op.execute(ddl)
    for name, _table, spec in KERNEL_TRIGGERS:
        op.execute(f"CREATE TRIGGER {name} {spec}")
    for name, _table, spec in KERNEL_CONSTRAINT_TRIGGERS:
        op.execute(f"CREATE CONSTRAINT TRIGGER {name} {spec}")

    # --- Row Level Security (ADR-01) -------------------------------------------------
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
    for name, table, _spec in (*KERNEL_CONSTRAINT_TRIGGERS, *KERNEL_TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    for function in KERNEL_FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {function}()")

    op.drop_constraint("fk_tax_codes_gl_account", "tax_codes", type_="foreignkey")
    op.drop_column("tax_codes", "gl_account_id")
    op.drop_constraint("fk_fiscal_years_closing_entry", "fiscal_years", type_="foreignkey")
    op.drop_column("fiscal_years", "closing_entry_id")

    op.drop_table("period_balances")
    op.drop_table("journal_lines")
    op.drop_table("journal_entries")
    op.drop_table("document_sequences")
    op.drop_table("exchange_rates")
    op.drop_table("projects")
    op.drop_table("gl_settings")
    op.drop_table("gl_accounts")

    op.drop_constraint("uq_tax_codes_company_id_id", "tax_codes", type_="unique")
    op.drop_constraint("uq_currencies_company_id_id", "currencies", type_="unique")
    op.drop_constraint("uq_branches_company_id_id", "branches", type_="unique")
    op.drop_constraint("uq_accounting_periods_company_id_id", "accounting_periods", type_="unique")

    bind = op.get_bind()
    for enum_type in ENUM_TYPES:
        enum_type.drop(bind, checkfirst=True)
