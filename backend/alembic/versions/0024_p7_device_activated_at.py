"""P7 step 2 review — a device records when it started fiscalizing.

Revision ID: 0024_p7_device_activated_at
Revises: 0023_p7_outbox_source_type
Create Date: 2026-09-17

`assert_fiscal_invariants` clause 1 is meant to catch the failure the whole phase exists to
make impossible: **a sale that reached the ledger and never reached RRA**. It could not. The
check skipped any posted AR document with no queue row and no receipt, so that a company which
turned a device on halfway through its life would not be told its earlier invoices were holes —
and "no row and no receipt" is *exactly* the shape of the failure. The assertion beneath that
skip was reachable only in a state that cannot occur.

A skip needs a discriminator, and the honest one is "was this branch's device fiscalizing when
this document posted?". `fiscal_devices` could not answer it: it carried `status` and no record
of **when**. So it does now.

`activated_at` is set on every activation, not only the first. A device that was suspended and
brought back starts a new continuous period, and the invariant asserts over that period alone —
documents posted while the device was suspended are legitimately row-less, because a suspended
device makes the company unfiscalized and `post_document` never reaches the hook. Conservative
in the one direction that matters: it never accuses a document that had no device to send to.

Back-filled to `updated_at` for devices that are already active, which is the closest thing an
existing row has to "when this was last made live". Nothing outside a test database has one.
"""

import sqlalchemy as sa

from alembic import op

revision = "0024_p7_device_activated_at"
down_revision = "0023_p7_outbox_source_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fiscal_devices",
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
    )
    # An active device that has no activation moment would make the invariant skip every
    # document on its branch — the same vacuum, one column along. `updated_at` is what an
    # existing row knows; a device initialized after this revision sets the real thing.
    op.execute(
        "UPDATE fiscal_devices SET activated_at = updated_at "
        "WHERE status = 'active' AND activated_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("fiscal_devices", "activated_at")
