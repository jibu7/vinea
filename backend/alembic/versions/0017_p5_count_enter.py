"""P5 step 9 — counting is its own permission, separate from adjustment posting.

Revision ID: 0017_p5_count_enter
Revises: 0016_p5_transfer_reversal
Create Date: 2026-09-13

`_require_count` opened a count session and its sheet to `inv:count_process` **or**
`inv:transactions_adjust`. The second is the problem: the only way to let a stock-taker count
was to give them the authority to post adjustments, which is precisely the authority a count
exists to take out of their hands — the point of keying a sheet and processing it as one
document is that nobody writes stock off a shelf by hand.

So counting gets `inv:count_enter`. Entering a count still moves nothing: the sheet is a
working paper until Process, which goes on checking `inv:count_process` by itself, so the
person who counts need not be the person who posts.

Data-only, like 0012's permission half — no schema changes. The Administrator role stores its
permission list as *data*, so a role seeded before this revision does not have the new
constant and no amount of application code will give it one.
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "0017_p5_count_enter"
down_revision = "0016_p5_transfer_reversal"
branch_labels = None
depends_on = None

#: The one constant this revision adds, and the only one removed on downgrade. Everything else
#: in the list below predates it, so taking those away would not be an inverse, it would be
#: damage — the same rule 0012 states.
NEW_PERMISSIONS = ("inv:count_enter",)

#: `app.core.permissions.ALL_PERMISSIONS` frozen as of this revision (architecture rule 10: a
#: migration is a historical record and may not change when the application changes). The whole
#: list is unioned rather than just the new constant, for 0012's reason: the list is stored
#: data, so a role seeded at an older revision holds whatever the constant said that day, and
#: back-filling only the newest name would leave it permanently short of everything it had
#: already missed.
#:
#: `tests/test_p5_backfill.py` compares the migrated result against the *live* constant, so the
#: day P6 adds a permission without its own back-fill, that test says so.
ALL_PERMISSIONS_AT_0017 = (
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


def upgrade() -> None:
    """Union the whole permission list into every system Administrator role.

    Idempotent: a role that already holds the list is not rewritten, and one that holds extras
    keeps them.
    """
    op.execute(
        sa.text(
            """
            UPDATE roles r
               SET permissions = r.permissions || (
                     SELECT coalesce(jsonb_agg(missing.value), '[]'::jsonb)
                       FROM jsonb_array_elements(CAST(:all_permissions AS jsonb))
                            AS missing(value)
                      WHERE NOT (r.permissions @> jsonb_build_array(missing.value))
                   )
             WHERE r.is_system
               AND r.name = 'Administrator'
               AND NOT (r.permissions @> CAST(:all_permissions AS jsonb))
            """
        ).bindparams(all_permissions=json.dumps(list(ALL_PERMISSIONS_AT_0017)))
    )


def downgrade() -> None:
    for permission in NEW_PERMISSIONS:
        op.execute(
            sa.text(
                "UPDATE roles SET permissions = permissions - CAST(:permission AS text) "
                "WHERE is_system AND name = 'Administrator'"
            ).bindparams(permission=permission)
        )
