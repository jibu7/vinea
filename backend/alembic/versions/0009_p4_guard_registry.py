"""P4 review — the control-account guard becomes a registry, and jobs get a retention policy.

Revision ID: 0009_p4_guard_registry
Revises: 0008_p4_jobs
Create Date: 2026-09-09

`control_account_modules(control_type, module)` replaces the hardcoded
`entry.module = control_type` test in `kernel_check_subledger_line()`. A control type
present in the registry is deny-by-default for every module not paired with it, so a later
module that legitimately raises AR (P10 POS receipts) registers `('ar', 'pos')` in its own
migration instead of the guard being loosened. Bank/cash stay out of the registry: they are
governed by the event-type rules in the Posting Engine, not by module ownership.

`jobs` gains `expires_at` (retention) and `artifact_size` so listings can report size without
ever selecting the bytes column.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0009_p4_guard_registry"
down_revision = "0008_p4_jobs"
branch_labels = None
depends_on = None

gl_control_type = postgresql.ENUM(
    "bank", "cash", "ar", "ap", "inventory", name="gl_control_type", create_type=False
)

REGISTRY_ROWS = (("ar", "ar"), ("ap", "ap"), ("inventory", "inv"))

SUBLEDGER_GUARD_FUNCTION = """
    CREATE OR REPLACE FUNCTION kernel_check_subledger_line() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        v_control gl_control_type;
        v_code text;
        v_module text;
        v_owned boolean;
        v_expected_partner text;
    BEGIN
        SELECT a.control_type, a.code INTO v_control, v_code
          FROM gl_accounts a WHERE a.id = NEW.gl_account_id;
        IF v_control IS NULL THEN
            RETURN NEW;
        END IF;

        SELECT EXISTS (
            SELECT 1 FROM control_account_modules m WHERE m.control_type = v_control
        ) INTO v_owned;
        IF NOT v_owned THEN
            RETURN NEW;  -- not module-owned (bank/cash); the engine's event rules apply
        END IF;

        SELECT e.module INTO v_module FROM journal_entries e WHERE e.id = NEW.entry_id;
        IF NOT EXISTS (
            SELECT 1 FROM control_account_modules m
             WHERE m.control_type = v_control AND m.module = v_module
        ) THEN
            RAISE EXCEPTION
                'account % is the % control account; module % may not post to it',
                v_code, upper(v_control::text), coalesce(v_module, '<null>')
                USING ERRCODE = 'VN007';
        END IF;

        v_expected_partner := CASE v_control
            WHEN 'ar' THEN 'customer' WHEN 'ap' THEN 'supplier' ELSE NULL END;
        IF v_expected_partner IS NOT NULL
           AND (NEW.partner_id IS NULL OR NEW.partner_type IS DISTINCT FROM v_expected_partner)
        THEN
            RAISE EXCEPTION 'account % requires a % on the line', v_code, v_expected_partner
                USING ERRCODE = 'VN008';
        END IF;
        IF v_control = 'inventory' AND NEW.item_id IS NULL THEN
            RAISE EXCEPTION 'account % requires an item on the line', v_code
                USING ERRCODE = 'VN008';
        END IF;
        RETURN NEW;
    END
    $$
"""

PREVIOUS_GUARD_FUNCTION = """
    CREATE OR REPLACE FUNCTION kernel_check_subledger_line() RETURNS trigger
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


def upgrade() -> None:
    op.create_table(
        "control_account_modules",
        sa.Column("control_type", gl_control_type, primary_key=True),
        sa.Column("module", sa.String(10), primary_key=True),
    )
    for control_type, module in REGISTRY_ROWS:
        op.execute(
            sa.text(
                "INSERT INTO control_account_modules (control_type, module) "
                "VALUES (CAST(:control_type AS gl_control_type), :module)"
            ).bindparams(control_type=control_type, module=module)
        )
    # `alembic/env.py` grants the app role SELECT on every table after the migrations run;
    # the registry is product configuration, changed only by a migration.
    op.execute(SUBLEDGER_GUARD_FUNCTION)

    op.add_column("jobs", sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.add_column("jobs", sa.Column("artifact_size", sa.Integer()))
    op.create_index("ix_jobs_expires_at", "jobs", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_jobs_expires_at", table_name="jobs")
    op.drop_column("jobs", "artifact_size")
    op.drop_column("jobs", "expires_at")
    op.execute(PREVIOUS_GUARD_FUNCTION)
    op.drop_table("control_account_modules")
