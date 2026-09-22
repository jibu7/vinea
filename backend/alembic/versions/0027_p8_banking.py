"""P8 step 1 — the banking schema, the bank-account master, the seed pack and the back-fill.

Revision ID: 0027_p8_banking
Revises: 0026_p7_z_high_water
Create Date: 2026-09-21

The shape the rest of Phase 8 keys against, and nothing that posts. No journal line is written
here and no posting path changes: what lands is the vocabulary — the bank-account master over
the flagged GL accounts, the statement and its lines, the rules, the match and its two member
tables, the reconciliation, the payment run and its lines — plus the three settings keys, the
supplier bank details, and the two columns the P7 revaluation needs to be able to revalue a
bank balance.

**Banking adds no ledger and no second balance** (ADR-04). The one stored pair of figures is
`bank_reconciliations`, which is the snapshot of a proof, and the one cache is
`bank_accounts.last_reconciled_at/balance`, which caches a *stored row* rather than a derived
balance. Both are recomputed by `assert_bank_invariants`.

**Two rules come from the database rather than from convention**, which is rule 3's reasoning
one domain along:

* `VN012` — a line in the wrong currency on a **foreign-currency** bank account. One-sided on
  purpose (decision 2): a *base*-currency account may carry a USD receipt, because that is how
  a customer's USD invoice is paid through a Rwandan bank and P4 already values it at the
  keyed rate. The engine refuses it first for the field error; this trigger is what makes the
  refusal a guarantee. Each is proven sensitive without the other.
* `VN013` — an edit to, or a delete of, an imported statement line. A statement is the bank's
  record; a mistaken import is voided **as a whole**. Exactly one column is exempt, `is_void`,
  and the exemption is what lets a voided line leave the fingerprint uniqueness scope without
  being deleted — the same shape as P7's `copy_count` exemption on a fiscal receipt.

**Numbering.** Three runs enter `DocType` with this revision (`BST`, `BRC`, `PYR`), and they
could not have entered it earlier: a claimant naming a table that does not exist fails
`test_every_claimant_names_a_real_table_and_column`. None of the three numbers a posting — a
statement, a reconciliation and a payment run are records of banking acts, and every journal
entry the phase causes takes its number from the P2/P4 run that already owns it — so each
registers its own table as its only claimant.

**The `fx_revaluation_role` enum is rebuilt, not extended.** `ALTER TYPE … ADD VALUE` cannot
be used by the same transaction that adds it, which is P6 step 1's finding and the reason
`_rebuild_enum` exists; this revision reuses that shape (rename / recreate / retype) rather
than inventing a second one.

**What the back-fill does**, for a tenant provisioned before P8:

* `1130 Bank Revaluation` — a **plain** account under `1100`, never a control one, because
  decision 8 puts the contra of a bank revaluation here rather than on the bank account
  itself: a base-only line on a bank account (zero `amount`, non-zero `base_amount`) is a
  ledger line the statement can never show, and the reconciliation would carry it forever;
* the three `gl_settings` keys, pointing at `1130`, `6700` and `4300`;
* **a `bank_accounts` row for every existing bank/cash control account**, in the tenant's base
  currency, with the GL code and name — decision 2's "exactly one row per flagged account",
  applied to the accounts that predate the hook;
* the full permission list on every system Administrator, plus P8's five operational
  permissions on Accountant and `bank:reports_view` on Clerk (decision 11).

`tests/test_p8_backfill.py` provisions a tenant at `0026` **with posted bank lines, including
a USD settlement on the RWF bank account**, upgrades, and asserts every key and account lands,
every flagged account gets its row, the USD-on-RWF line survives, and nothing posted moved.
`make migrate-check` runs on an empty scratch database and proves DDL only (rule 10).

What the back-fill deliberately does **not** do:

**It sets no account's currency to anything but the base.** A tenant with a USD bank account
has to say so — and can, because `currency_id` may change while the GL account has no journal
line, and an account that *does* have lines was never held in another currency as far as this
build is concerned. Guessing from the lines would be the migration inventing a fact.

**It seeds no document sequences.** `claim_number` creates a run on demand, and P6's note
still holds: a seeded run sends the gapless checker at a table before anything has claimed
from it.

**It assumes `rw_sme_v1` is the only chart-of-accounts template**, as 0012, 0018 and 0022 did,
and for the same reason: `1130`'s parent is found by code and that code is this template's.
"""

import json

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0027_p8_banking"
down_revision = "0026_p7_z_high_water"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(20, 6)
RATE = sa.Numeric(20, 10)

TENANT_TABLES = (
    "bank_accounts",
    "bank_rules",
    "bank_statements",
    "bank_statement_lines",
    "bank_reconciliations",
    "bank_matches",
    "bank_match_statement_lines",
    "bank_match_journal_lines",
    "payment_runs",
    "payment_run_lines",
)

# (type name, labels). Created outright: every one is new, so there is no existing column to
# retype and none of 0018's `ALTER TYPE … ADD VALUE` problem.
NEW_ENUMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bank_account_kind", ("bank", "cash")),
    ("bank_statement_source", ("csv", "manual")),
    ("bank_statement_status", ("open", "void")),
    ("bank_match_kind", ("auto", "manual", "tick", "posted")),
    (
        "bank_match_rule",
        (
            "reference",
            "payment_run",
            "amount_date",
            "posted_from_statement",
            "manual",
            "tick",
        ),
    ),
    ("bank_reconciliation_status", ("open", "locked")),
    ("payment_run_status", ("posted", "reversed")),
)

#: Rebuilt, never `ALTER TYPE … ADD VALUE` (P6 step 1). `both` stays: an enum value some
#: tenant's history already uses is never removed.
FX_ROLE_LABELS = ("ar", "ap", "both", "bank", "all")
FX_ROLE_LABELS_AT_0026 = ("ar", "ap", "both")
FX_ROLE_COLUMNS = (("fx_revaluations", "role"),)

# (code, name, class, parent code) — added to `rw_sme_v1` by this phase. **Not a control
# account**: decision 8 posts the contra of a bank revaluation here precisely because the bank
# account itself must never carry a base-only line, and a control type would only move the
# problem — the balance sheet reads `1121 + 1130` exactly as it reads `1200 + 1290`.
NEW_ACCOUNTS = (("1130", "Bank Revaluation", "asset", "1100"),)

#: (settings column, account code). `1130` is created by this revision; `6700` and `4300` are
#: `rw_sme_v1` accounts every tenant already has, and the two keys are **defaults for a drawer**
#: rather than a posting map — a statement debit posted as a fee opens on `6700`, interest on
#: `4300`, and the person posting may change either.
SETTINGS_ACCOUNTS = (
    ("bank_revaluation_account_id", "1130"),
    ("bank_charges_account_id", "6700"),
    ("bank_interest_account_id", "4300"),
)

NEW_PERMISSIONS = (
    "bank:setup_manage",
    "bank:statement_import",
    "bank:reconcile",
    "bank:reconcile_lock",
    "bank:payment_run_post",
    "bank:reports_view",
)

#: Everything but `bank:setup_manage` — registering accounts and writing column mappings stays
#: with the administrator, the same split P7 made for the EBM device (decision 11).
ACCOUNTANT_PERMISSIONS = (
    "bank:statement_import",
    "bank:reconcile",
    "bank:reconcile_lock",
    "bank:payment_run_post",
    "bank:reports_view",
)
CLERK_PERMISSIONS = ("bank:reports_view",)

# `app.core.permissions.ALL_PERMISSIONS` frozen as of this revision (architecture rule 10: a
# migration must not drift with the constants it was written against).
ALL_PERMISSIONS_AT_0027 = (
    "users:create",
    "users:read",
    "users:update",
    "users:delete",
    "users:manage_roles",
    "roles:create",
    "roles:read",
    "roles:update",
    "roles:delete",
    "roles:manage_permissions",
    "company:read",
    "company:update",
    "accounting_periods:manage",
    "accounting_periods:reopen",
    "common:setup_currencies",
    "common:setup_taxes",
    "common:setup_branches",
    "gl:setup_manage",
    "gl:journal_post",
    "gl:reports_view",
    "projects:read",
    "projects:manage",
    "ar:setup_manage",
    "ar:transactions_post",
    "ar:reports_view",
    "ar:writeoff_approve",
    "ar:credit_limit_override",
    "ap:setup_manage",
    "ap:transactions_post",
    "ap:reports_view",
    "ap:credit_limit_override",
    "inv:setup_manage",
    "inv:transactions_adjust",
    "inv:reports_view",
    "inv:count_enter",
    "inv:count_process",
    "inv:item_rename",
    "oe:setup_manage",
    "oe:sales_orders_manage",
    "oe:purchase_orders_manage",
    "oe:grv_process",
    "oe:reports_view",
    "oe:landed_cost_post",
    "fiscal:setup_manage",
    "fiscal:queue_manage",
    "fiscal:reports_view",
    "fiscal:close_day",
    "tax:vat_return_view",
    "tax:vat_return_file",
    "gl:fx_revalue",
    "bank:setup_manage",
    "bank:statement_import",
    "bank:reconcile",
    "bank:reconcile_lock",
    "bank:payment_run_post",
    "bank:reports_view",
    "reporting:financial_statements_view",
    "reporting:financial_statements_generate",
    "reporting:templates_manage",
    "reporting:schedules_manage",
    "reporting:bank_reconciliation_manage",
    "reporting:ar_aging_view",
    "reporting:ap_aging_view",
    "reporting:gl_advanced_view",
    "reporting:comparative_analysis",
    "reporting:cash_flow_view",
    "reporting:trial_balance_view",
    "reporting:inventory_valuation_view",
    "reporting:dashboard_view",
    "reporting:export",
    "bom:setup_manage",
    "bom:manufacturing_create",
    "bom:manufacturing_process",
    "bom:reports_view",
    "bom:mrp_run",
    "pos:setup_manage",
    "pos:till_operate",
    "pos:till_manage",
    "pos:sales_create",
    "pos:returns_process",
    "pos:reports_view",
    "pos:reconcile",
)

# --- Guards ------------------------------------------------------------------------------------

BANKING_FUNCTIONS = {
    "bank_block_statement_line_mutation": """
        CREATE FUNCTION bank_block_statement_line_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'bank statement line % is the bank''s record and is immutable',
                    OLD.id USING ERRCODE = 'VN013';
            END IF;
            -- `is_void` is the one column that may change, and only by voiding the whole
            -- statement. A voided line has to leave the fingerprint uniqueness scope (so the
            -- same file can be imported again), every listing and every count — and it cannot
            -- be deleted, because it is genuinely what the bank's export said. The same shape
            -- as P7's `copy_count` exemption: countable without being editable.
            --
            -- `to_jsonb(NEW) - 'col'` rather than a column-by-column comparison, so a later
            -- revision that adds a column to this table cannot silently fall outside the guard.
            IF (to_jsonb(NEW) - 'is_void' - 'updated_at' - 'updated_by')
               IS DISTINCT FROM (to_jsonb(OLD) - 'is_void' - 'updated_at' - 'updated_by')
            THEN
                RAISE EXCEPTION
                    'bank statement line % is immutable; only its void flag may change', OLD.id
                    USING ERRCODE = 'VN013';
            END IF;
            RETURN NEW;
        END
        $$
    """,
    "kernel_check_bank_account_currency": """
        CREATE FUNCTION kernel_check_bank_account_currency() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            v_currency bigint;
            v_code text;
        BEGIN
            -- One-sided (decision 2). Only a bank account held in something other than the
            -- company's base currency constrains its lines; a base-currency account may carry
            -- a foreign line, because that is how a USD receipt reaches a Rwandan bank and P4
            -- values it at the rate keyed on the document.
            SELECT b.currency_id, b.code INTO v_currency, v_code
              FROM bank_accounts b
              JOIN currencies c ON c.id = b.currency_id
             WHERE b.gl_account_id = NEW.gl_account_id
               AND NOT c.is_base;
            IF v_currency IS NULL OR NEW.currency_id = v_currency THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION
                'bank account % is held in another currency; this line may not sit on it',
                v_code USING ERRCODE = 'VN012';
        END
        $$
    """,
}

BANKING_TRIGGERS = (
    (
        "trg_bank_statement_lines_immutable",
        "bank_statement_lines",
        "BEFORE UPDATE OR DELETE ON bank_statement_lines FOR EACH ROW "
        "EXECUTE FUNCTION bank_block_statement_line_mutation()",
    ),
    (
        # Sorts after `trg_journal_lines_engine_guard`, so the single-writer rule still fails
        # first — the same consideration `trg_journal_lines_subledger_guard` records.
        "trg_journal_lines_needs_bank_currency",
        "journal_lines",
        "BEFORE INSERT ON journal_lines FOR EACH ROW "
        "EXECUTE FUNCTION kernel_check_bank_account_currency()",
    ),
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


def _enum(name: str) -> postgresql.ENUM:
    """The type as a column reference — created once by `_create_enums`, never by a column."""
    return postgresql.ENUM(name=name, create_type=False)


def _create_enums() -> None:
    for name, labels in NEW_ENUMS:
        op.execute(f"CREATE TYPE {name} AS ENUM ({', '.join(repr(label) for label in labels)})")


def _drop_enums() -> None:
    for name, _ in reversed(NEW_ENUMS):
        op.execute(f"DROP TYPE IF EXISTS {name}")


def _rebuild_enum(name: str, labels: tuple[str, ...], columns: tuple[tuple[str, str], ...]) -> None:
    """0018's rebuild, unchanged in shape: drop the defaults, rename the type, create the new
    one, retype every column through `text`, drop the old type, restore the defaults."""
    bind = op.get_bind()
    defaults: dict[tuple[str, str], str | None] = {}
    for table, column in columns:
        defaults[(table, column)] = bind.execute(
            sa.text(
                """
                SELECT pg_get_expr(d.adbin, d.adrelid)
                  FROM pg_attribute a
                  JOIN pg_class c ON c.oid = a.attrelid
                  LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                 WHERE c.relname = :table AND a.attname = :column
                   AND a.attnum > 0 AND NOT a.attisdropped
                """
            ).bindparams(table=table, column=column)
        ).scalar()
        if defaults[(table, column)] is not None:
            op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT")

    op.execute(f"ALTER TYPE {name} RENAME TO {name}_old")
    op.execute(f"CREATE TYPE {name} AS ENUM ({', '.join(repr(label) for label in labels)})")
    for table, column in columns:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {name} USING {column}::text::{name}"
        )
    op.execute(f"DROP TYPE {name}_old")

    for (table, column), default in defaults.items():
        if default is None:
            continue
        literal = default.split("::")[0]
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT {literal}::{name}")


# --- Tables ------------------------------------------------------------------------------------


def _create_masters() -> None:
    op.create_table(
        "bank_accounts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("gl_account_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", _enum("bank_account_kind"), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("currency_id", sa.BigInteger(), nullable=False),
        sa.Column("bank_name", sa.String(200)),
        sa.Column("account_number", sa.String(50)),
        sa.Column("account_holder", sa.String(200)),
        sa.Column("bank_branch", sa.String(200)),
        sa.Column("swift_bic", sa.String(20)),
        sa.Column("statement_format", postgresql.JSONB()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_reconciled_at", sa.Date()),
        sa.Column("last_reconciled_balance", MONEY),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_bank_accounts_company_id_id"),
        sa.UniqueConstraint("company_id", "code", name="uq_bank_accounts_company_code"),
        sa.UniqueConstraint(
            "company_id", "gl_account_id", name="uq_bank_accounts_company_gl_account"
        ),
        _tenant_fk("fk_bank_accounts_gl_account", "gl_account_id", "gl_accounts"),
        _tenant_fk("fk_bank_accounts_currency", "currency_id", "currencies"),
        sa.CheckConstraint(
            "kind <> 'cash' OR statement_format IS NULL", name="cash_account_has_no_format"
        ),
    )
    op.create_index("ix_bank_accounts_company_id", "bank_accounts", ["company_id"])

    op.create_table(
        "bank_rules",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("bank_account_id", sa.BigInteger(), nullable=False),
        sa.Column("pattern", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(10)),
        sa.Column("gl_account_id", sa.BigInteger()),
        sa.Column("tax_code_id", sa.BigInteger()),
        sa.Column("partner_type", sa.String(10)),
        sa.Column("partner_id", sa.BigInteger()),
        sa.Column("description", sa.String(200)),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_bank_rules_company_id_id"),
        _tenant_fk(
            "fk_bank_rules_bank_account", "bank_account_id", "bank_accounts", ondelete="CASCADE"
        ),
        _tenant_fk("fk_bank_rules_gl_account", "gl_account_id", "gl_accounts"),
        _tenant_fk("fk_bank_rules_tax_code", "tax_code_id", "tax_codes"),
        _tenant_fk("fk_bank_rules_partner", "partner_id", "partners"),
    )
    op.create_index("ix_bank_rules_company_id", "bank_rules", ["company_id"])
    op.create_index(
        "ix_bank_rules_account_priority",
        "bank_rules",
        ["company_id", "bank_account_id", "priority"],
    )


def _create_statements() -> None:
    op.create_table(
        "bank_statements",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("bank_account_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.String(30), nullable=False),
        sa.Column("source", _enum("bank_statement_source"), nullable=False),
        sa.Column("file_name", sa.String(255)),
        sa.Column("file_sha256", sa.String(64)),
        sa.Column("format_snapshot", postgresql.JSONB()),
        sa.Column("from_date", sa.Date(), nullable=False),
        sa.Column("to_date", sa.Date(), nullable=False),
        sa.Column("opening_balance", MONEY, nullable=False),
        sa.Column("closing_balance", MONEY, nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lines_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "status", _enum("bank_statement_status"), nullable=False, server_default="open"
        ),
        sa.Column("imported_by", sa.BigInteger()),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("voided_by", sa.BigInteger()),
        sa.Column("voided_at", sa.DateTime(timezone=True)),
        sa.Column("idempotency_key", sa.String(64)),
        sa.Column("idempotency_hash", sa.String(64)),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_bank_statements_company_id_id"),
        sa.UniqueConstraint("company_id", "number", name="uq_bank_statements_company_number"),
        _tenant_fk("fk_bank_statements_bank_account", "bank_account_id", "bank_accounts"),
        sa.CheckConstraint("to_date >= from_date", name="range_is_forward"),
        sa.CheckConstraint("line_count >= 0 AND lines_skipped >= 0", name="counts_not_negative"),
    )
    op.create_index("ix_bank_statements_company_id", "bank_statements", ["company_id"])
    # Partial: a voided statement's hash blocks nothing, so the same file can be imported
    # again after a mistaken import is taken back out (decision 3).
    op.create_index(
        "uq_bank_statements_account_sha256",
        "bank_statements",
        ["bank_account_id", "file_sha256"],
        unique=True,
        postgresql_where=sa.text("status <> 'void' AND file_sha256 IS NOT NULL"),
    )
    op.create_index(
        "ix_bank_statements_account_dates",
        "bank_statements",
        ["company_id", "bank_account_id", "from_date", "to_date"],
    )

    op.create_table(
        "bank_statement_lines",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("statement_id", sa.BigInteger(), nullable=False),
        sa.Column("bank_account_id", sa.BigInteger(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("value_date", sa.Date(), nullable=False),
        sa.Column("booking_date", sa.Date()),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("reference", sa.String(200)),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("balance_after", MONEY),
        sa.Column("external_id", sa.String(100)),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("is_void", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_bank_statement_lines_company_id_id"),
        _tenant_fk(
            "fk_bank_statement_lines_statement",
            "statement_id",
            "bank_statements",
            ondelete="CASCADE",
        ),
        _tenant_fk("fk_bank_statement_lines_bank_account", "bank_account_id", "bank_accounts"),
        sa.CheckConstraint("amount <> 0", name="amount_not_zero"),
    )
    op.create_index("ix_bank_statement_lines_company_id", "bank_statement_lines", ["company_id"])
    op.create_index(
        "uq_bank_statement_lines_account_fingerprint",
        "bank_statement_lines",
        ["bank_account_id", "fingerprint"],
        unique=True,
        postgresql_where=sa.text("NOT is_void"),
    )
    op.create_index(
        "uq_bank_statement_lines_account_external_id",
        "bank_statement_lines",
        ["bank_account_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL AND NOT is_void"),
    )
    op.create_index(
        "ix_bank_statement_lines_account_date",
        "bank_statement_lines",
        ["company_id", "bank_account_id", "value_date"],
    )


def _create_reconciliations_and_matches() -> None:
    op.create_table(
        "bank_reconciliations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("bank_account_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.String(30), nullable=False),
        sa.Column("reconciliation_date", sa.Date(), nullable=False),
        sa.Column("statement_balance", MONEY, nullable=False),
        sa.Column("ledger_balance", MONEY),
        sa.Column("outstanding_total", MONEY),
        sa.Column("difference", MONEY),
        sa.Column(
            "status", _enum("bank_reconciliation_status"), nullable=False, server_default="open"
        ),
        sa.Column("high_water_line_id", sa.BigInteger()),
        sa.Column("outstanding_snapshot", postgresql.JSONB()),
        sa.Column("locked_by", sa.BigInteger()),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("reopened_by", sa.BigInteger()),
        sa.Column("reopened_at", sa.DateTime(timezone=True)),
        sa.Column("reopened_reason", sa.Text()),
        sa.Column("idempotency_key", sa.String(64)),
        sa.Column("idempotency_hash", sa.String(64)),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_bank_reconciliations_company_id_id"),
        sa.UniqueConstraint(
            "company_id", "number", name="uq_bank_reconciliations_company_number"
        ),
        _tenant_fk("fk_bank_reconciliations_bank_account", "bank_account_id", "bank_accounts"),
    )
    op.create_index("ix_bank_reconciliations_company_id", "bank_reconciliations", ["company_id"])
    # One open reconciliation per account, from the database — so `reconciliation_open_exists`
    # cannot be raced past by two people opening one at the same moment.
    op.create_index(
        "uq_bank_reconciliations_open_per_account",
        "bank_reconciliations",
        ["bank_account_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "ix_bank_reconciliations_account_date",
        "bank_reconciliations",
        ["company_id", "bank_account_id", "reconciliation_date"],
    )

    op.create_table(
        "bank_matches",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("bank_account_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", _enum("bank_match_kind"), nullable=False),
        sa.Column("rule", _enum("bank_match_rule"), nullable=False),
        sa.Column("reconciliation_id", sa.BigInteger()),
        sa.Column("matched_by", sa.BigInteger()),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text()),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_bank_matches_company_id_id"),
        _tenant_fk("fk_bank_matches_bank_account", "bank_account_id", "bank_accounts"),
        _tenant_fk(
            "fk_bank_matches_reconciliation", "reconciliation_id", "bank_reconciliations"
        ),
    )
    op.create_index("ix_bank_matches_company_id", "bank_matches", ["company_id"])
    op.create_index("ix_bank_matches_account", "bank_matches", ["company_id", "bank_account_id"])
    op.create_index(
        "ix_bank_matches_reconciliation",
        "bank_matches",
        ["company_id", "reconciliation_id"],
        postgresql_where=sa.text("reconciliation_id IS NOT NULL"),
    )

    # A statement line is in at most one match; a journal line is in at most one match. Unique
    # constraints rather than a service rule, because "no line is in two matches" is what makes
    # `outstanding` a partition of the account's lines rather than a query that happens to
    # agree with one.
    op.create_table(
        "bank_match_statement_lines",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("statement_line_id", sa.BigInteger(), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint(
            "company_id", "id", name="uq_bank_match_statement_lines_company_id_id"
        ),
        sa.UniqueConstraint(
            "statement_line_id", name="uq_bank_match_statement_lines_statement_line"
        ),
        _tenant_fk(
            "fk_bank_match_statement_lines_match",
            "match_id",
            "bank_matches",
            ondelete="CASCADE",
        ),
        _tenant_fk(
            "fk_bank_match_statement_lines_line",
            "statement_line_id",
            "bank_statement_lines",
        ),
    )
    op.create_index(
        "ix_bank_match_statement_lines_company_id", "bank_match_statement_lines", ["company_id"]
    )
    op.create_index(
        "ix_bank_match_statement_lines_match",
        "bank_match_statement_lines",
        ["company_id", "match_id"],
    )

    op.create_table(
        "bank_match_journal_lines",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("journal_line_id", sa.BigInteger(), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint(
            "company_id", "id", name="uq_bank_match_journal_lines_company_id_id"
        ),
        sa.UniqueConstraint("journal_line_id", name="uq_bank_match_journal_lines_journal_line"),
        _tenant_fk(
            "fk_bank_match_journal_lines_match", "match_id", "bank_matches", ondelete="CASCADE"
        ),
        _tenant_fk("fk_bank_match_journal_lines_line", "journal_line_id", "journal_lines"),
    )
    op.create_index(
        "ix_bank_match_journal_lines_company_id", "bank_match_journal_lines", ["company_id"]
    )
    op.create_index(
        "ix_bank_match_journal_lines_match",
        "bank_match_journal_lines",
        ["company_id", "match_id"],
    )


def _create_payment_runs() -> None:
    op.create_table(
        "payment_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("bank_account_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.String(30), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("currency_id", sa.BigInteger(), nullable=False),
        sa.Column("total", MONEY, nullable=False),
        sa.Column("reference", sa.String(50), nullable=False),
        sa.Column(
            "status", _enum("payment_run_status"), nullable=False, server_default="posted"
        ),
        sa.Column("posted_by", sa.BigInteger()),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reversal_reason", sa.Text()),
        sa.Column("reversed_by", sa.BigInteger()),
        sa.Column("reversed_at", sa.DateTime(timezone=True)),
        sa.Column("idempotency_key", sa.String(64)),
        sa.Column("idempotency_hash", sa.String(64)),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_payment_runs_company_id_id"),
        sa.UniqueConstraint("company_id", "number", name="uq_payment_runs_company_number"),
        _tenant_fk("fk_payment_runs_bank_account", "bank_account_id", "bank_accounts"),
        _tenant_fk("fk_payment_runs_currency", "currency_id", "currencies"),
    )
    op.create_index("ix_payment_runs_company_id", "payment_runs", ["company_id"])
    op.create_index(
        "ix_payment_runs_account_date",
        "payment_runs",
        ["company_id", "bank_account_id", "payment_date"],
    )

    op.create_table(
        "payment_run_lines",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("partner_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("discount_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("settlement_document_id", sa.BigInteger()),
        sa.Column("allocation_id", sa.BigInteger()),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_payment_run_lines_company_id_id"),
        _tenant_fk("fk_payment_run_lines_run", "run_id", "payment_runs", ondelete="CASCADE"),
        _tenant_fk("fk_payment_run_lines_partner", "partner_id", "partners"),
        _tenant_fk("fk_payment_run_lines_document", "document_id", "partner_documents"),
        _tenant_fk(
            "fk_payment_run_lines_settlement", "settlement_document_id", "partner_documents"
        ),
        _tenant_fk("fk_payment_run_lines_allocation", "allocation_id", "allocations"),
    )
    op.create_index("ix_payment_run_lines_company_id", "payment_run_lines", ["company_id"])
    op.create_index("ix_payment_run_lines_run", "payment_run_lines", ["company_id", "run_id"])


def _extend_existing_tables() -> None:
    # --- partners: where a payment run sends the money -------------------------------------
    op.add_column("partners", sa.Column("bank_name", sa.String(200)))
    op.add_column("partners", sa.Column("bank_account_number", sa.String(50)))
    op.add_column("partners", sa.Column("bank_account_holder", sa.String(200)))

    # --- gl_settings: the three banking defaults -------------------------------------------
    for column, _ in SETTINGS_ACCOUNTS:
        op.add_column("gl_settings", sa.Column(column, sa.BigInteger()))
        op.create_foreign_key(
            f"fk_gl_settings_{column.removesuffix('_id')}",
            "gl_settings",
            "gl_accounts",
            ["company_id", column],
            ["company_id", "id"],
            ondelete="RESTRICT",
        )

    # --- fx_revaluation_lines: a line is a document *or* a bank account ---------------------
    #
    # `booking_rate` goes nullable with `document_id`: a bank balance has no single rate it was
    # booked at, so on a bank line the figure is `carrying_base / open_amount` where the
    # balance is non-zero and nothing at all where it is zero.
    op.alter_column("fx_revaluation_lines", "document_id", nullable=True)
    op.alter_column("fx_revaluation_lines", "booking_rate", type_=RATE, nullable=True)
    op.add_column("fx_revaluation_lines", sa.Column("bank_account_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_fx_revaluation_lines_bank_account",
        "fx_revaluation_lines",
        "bank_accounts",
        ["company_id", "bank_account_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "line_is_a_document_or_a_bank_account",
        "fx_revaluation_lines",
        "(document_id IS NULL) <> (bank_account_id IS NULL)",
    )


# --- Back-fill ---------------------------------------------------------------------------------


def _backfill_existing_tenants() -> None:
    """A tenant provisioned before P8 comes out of `alembic upgrade head` able to register a
    bank account, import a statement, reconcile and revalue.

    The P4–P7 lesson, unchanged: a NULL default here does not fail at migration time, it fails
    at whichever operation first needs it, long after anyone can connect the two.
    """
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

    for column, account_code in SETTINGS_ACCOUNTS:
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

    # Decision 2's "every bank/cash control account has exactly one row", applied to the
    # accounts that predate the hook. Base currency, the GL code and the GL name — the master
    # states what is already known and invents nothing. A tenant whose bank account is really
    # held in USD says so afterwards on the Bank accounts screen, which it can do for as long
    # as that GL account has no journal line.
    op.execute(
        sa.text(
            """
            INSERT INTO bank_accounts
                (company_id, gl_account_id, kind, code, name, currency_id, is_active)
            SELECT a.company_id, a.id, CAST(a.control_type::text AS bank_account_kind),
                   a.code, a.name, c.id, true
              FROM gl_accounts a
              JOIN currencies c ON c.company_id = a.company_id AND c.is_base
             WHERE a.control_type IN ('bank', 'cash')
               AND NOT EXISTS (
                   SELECT 1 FROM bank_accounts b
                    WHERE b.company_id = a.company_id AND b.gl_account_id = a.id
               )
            """
        )
    )

    _backfill_role_permissions()


def _backfill_role_permissions() -> None:
    """Roles store their permissions as *data*, so a role seeded before this revision has none
    of P8's six and no amount of application code will give it one.

    Administrator gets the whole union, not just this phase's addition — a tenant several
    phases old may be missing more than one. Accountant and Clerk get exactly what decision 11
    names, and only what they are missing, so a company that has edited either role keeps its
    edits.
    """
    op.execute(
        sa.text(
            """
            UPDATE roles r
               SET permissions = r.permissions || (
                   SELECT COALESCE(jsonb_agg(missing.value), '[]'::jsonb)
                     FROM jsonb_array_elements(CAST(:all_permissions AS jsonb))
                          AS missing(value)
                    WHERE NOT (r.permissions @> jsonb_build_array(missing.value))
               )
             WHERE r.is_system AND r.name = 'Administrator'
               AND NOT (r.permissions @> CAST(:all_permissions AS jsonb))
            """
        ).bindparams(all_permissions=json.dumps(list(ALL_PERMISSIONS_AT_0027)))
    )
    for role_name, granted in (
        ("Accountant", ACCOUNTANT_PERMISSIONS),
        ("Clerk", CLERK_PERMISSIONS),
    ):
        op.execute(
            sa.text(
                """
                UPDATE roles r
                   SET permissions = r.permissions || (
                       SELECT COALESCE(jsonb_agg(missing.value), '[]'::jsonb)
                         FROM jsonb_array_elements(CAST(:granted AS jsonb)) AS missing(value)
                        WHERE NOT (r.permissions @> jsonb_build_array(missing.value))
                   )
                 WHERE r.is_system AND r.name = :role_name
                   AND NOT (r.permissions @> CAST(:granted AS jsonb))
                """
            ).bindparams(role_name=role_name, granted=json.dumps(list(granted)))
        )


def upgrade() -> None:
    _create_enums()
    _rebuild_enum("fx_revaluation_role", FX_ROLE_LABELS, FX_ROLE_COLUMNS)
    _create_masters()
    _create_statements()
    _create_reconciliations_and_matches()
    _create_payment_runs()
    _extend_existing_tables()

    for body in BANKING_FUNCTIONS.values():
        op.execute(body)
    for name, _table, definition in BANKING_TRIGGERS:
        op.execute(f"CREATE TRIGGER {name} {definition}")

    _backfill_existing_tenants()

    # --- Row Level Security (ADR-01) -------------------------------------------------------
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
    for name, table, _ in BANKING_TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    for name in BANKING_FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {name}()")

    for permission in NEW_PERMISSIONS:
        op.execute(
            sa.text(
                "UPDATE roles SET permissions = permissions - CAST(:permission AS text) "
                "WHERE is_system"
            ).bindparams(permission=permission)
        )

    op.drop_constraint(
        "line_is_a_document_or_a_bank_account", "fx_revaluation_lines", type_="check"
    )
    op.drop_constraint(
        "fk_fx_revaluation_lines_bank_account", "fx_revaluation_lines", type_="foreignkey"
    )
    # Rows this phase added carry no document, so they cannot survive the column going back to
    # NOT NULL — and they are a P8 artefact, which is exactly what a downgrade past P8 removes.
    op.execute("DELETE FROM fx_revaluation_lines WHERE document_id IS NULL")
    op.drop_column("fx_revaluation_lines", "bank_account_id")
    op.alter_column("fx_revaluation_lines", "booking_rate", type_=RATE, nullable=False)
    op.alter_column("fx_revaluation_lines", "document_id", nullable=False)

    for column, _ in SETTINGS_ACCOUNTS:
        op.drop_constraint(
            f"fk_gl_settings_{column.removesuffix('_id')}", "gl_settings", type_="foreignkey"
        )
        op.drop_column("gl_settings", column)
    for column in ("bank_account_holder", "bank_account_number", "bank_name"):
        op.drop_column("partners", column)

    for table in (
        "payment_run_lines",
        "payment_runs",
        "bank_match_journal_lines",
        "bank_match_statement_lines",
        "bank_matches",
        "bank_reconciliations",
        "bank_statement_lines",
        "bank_statements",
        "bank_rules",
        "bank_accounts",
    ):
        op.drop_table(table)

    op.execute(
        sa.text("DELETE FROM gl_accounts WHERE code = ANY(:codes)").bindparams(
            codes=[code for code, *_ in NEW_ACCOUNTS]
        )
    )
    # `bank` and `all` go back out with the scopes that used them. A tenant that has actually
    # run one is refused here by the `role::text::fx_revaluation_role` cast, and that is the
    # right answer rather than a coercion: a run over bank balances has no meaning at 0026,
    # and silently relabelling it `both` would leave a posted entry describing something it
    # did not do.
    _rebuild_enum("fx_revaluation_role", FX_ROLE_LABELS_AT_0026, FX_ROLE_COLUMNS)
    _drop_enums()
