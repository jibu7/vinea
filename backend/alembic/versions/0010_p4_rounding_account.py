"""P4 review — rounding differences get their own account, separate from realized FX.

`gl_settings.rounding_difference_account_id` and `realized_fx_loss_account_id` both pointed at
6950 (Foreign Exchange Loss) in the Rwanda pack, so a sub-unit rounding residue was posted to,
and reportable as, an exchange loss. They are not the same thing: a settlement can round with
no rate movement at all — 100.00 USD against 33.33 + 33.33 + 33.34 at one rate rounds to whole
francs differently on each side — and booking that as FX overstates exchange exposure with
noise that has nothing to do with rates.

This adds 6970 Rounding Difference and repoints the key at it. Only tenants still pointing at
6950 are moved: a company that has already chosen its own rounding account keeps it. Existing
journal lines are left alone — they are posted history, and the immutability triggers would
refuse anyway; the split takes effect from here.

Revision ID: 0010_p4_rounding_account
Revises: 0009_p4_guard_registry
"""

import sqlalchemy as sa

from alembic import op

revision = "0010_p4_rounding_account"
down_revision = "0009_p4_guard_registry"
branch_labels = None
depends_on = None

ROUNDING_CODE = "6970"
ROUNDING_NAME = "Rounding Difference"
PARENT_CODE = "6000"
FX_LOSS_CODE = "6950"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO gl_accounts
                (company_id, code, name, class, parent_id, is_postable, is_control,
                 control_type, is_active)
            SELECT p.company_id, :code, :name, CAST('expense' AS account_class), p.id,
                   true, false, NULL, true
              FROM gl_accounts p
             WHERE p.code = :parent_code
               AND NOT EXISTS (
                   SELECT 1 FROM gl_accounts existing
                    WHERE existing.company_id = p.company_id AND existing.code = :code
               )
            """
        ).bindparams(code=ROUNDING_CODE, name=ROUNDING_NAME, parent_code=PARENT_CODE)
    )

    # Repoint only where rounding is still sharing the FX loss account, or is unset.
    op.execute(
        sa.text(
            """
            UPDATE gl_settings s
               SET rounding_difference_account_id = rounding.id
              FROM gl_accounts rounding
             WHERE rounding.company_id = s.company_id
               AND rounding.code = :rounding_code
               AND (
                     s.rounding_difference_account_id IS NULL
                  OR s.rounding_difference_account_id = (
                        SELECT fx.id FROM gl_accounts fx
                         WHERE fx.company_id = s.company_id AND fx.code = :fx_code
                     )
               )
            """
        ).bindparams(rounding_code=ROUNDING_CODE, fx_code=FX_LOSS_CODE)
    )


def downgrade() -> None:
    # Point rounding back at FX loss, then drop the account — but only if nothing was posted
    # to it, since a referenced account cannot be removed without losing history.
    op.execute(
        sa.text(
            """
            UPDATE gl_settings s
               SET rounding_difference_account_id = fx.id
              FROM gl_accounts fx, gl_accounts rounding
             WHERE fx.company_id = s.company_id AND fx.code = :fx_code
               AND rounding.company_id = s.company_id AND rounding.code = :rounding_code
               AND s.rounding_difference_account_id = rounding.id
            """
        ).bindparams(rounding_code=ROUNDING_CODE, fx_code=FX_LOSS_CODE)
    )
    op.execute(
        sa.text(
            """
            DELETE FROM gl_accounts a
             WHERE a.code = :code
               AND NOT EXISTS (SELECT 1 FROM journal_lines l WHERE l.gl_account_id = a.id)
            """
        ).bindparams(code=ROUNDING_CODE)
    )
