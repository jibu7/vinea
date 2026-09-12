"""P5 step 4 review — a received transfer can be reversed, not only a dispatched one.

Revision ID: 0016_p5_transfer_reversal
Revises: 0015_p5_transfers_counts
Create Date: 2026-09-12

Decision 11 covers every stock document, and 0015 left a transfer reversible only while it was
still on the road: `cancellation_entry_id` held the mirror of the dispatch leg and there was
nowhere to record the mirror of a receive. A transfer that arrived and should not have was
therefore the one posting in P5 that could not be undone.

So the one column becomes two, named for the legs they mirror rather than for the workflow
that produced them — a cancellation and a reversal both mirror the dispatch, and only a
reversal also mirrors the receive:

* `cancellation_entry_id` → `dispatch_reversal_entry_id`
* new `receive_reversal_entry_id`
* `cancelled_date` → `undone_date`, with `status` saying which of the two happened
* `stock_transfer_status` gains `reversed`

**Why a separate revision** rather than an edit to 0015: 0015 has been applied — to this
branch's development database and to every CI run since it was pushed — and an applied
migration is never edited (architecture rule 10). The rename is safe on any of them because
nothing has transferred stock yet outside a test database, and it is written as a rename
rather than a drop-and-add so that it would stay safe if something had.
"""

import sqlalchemy as sa

from alembic import op

revision = "0016_p5_transfer_reversal"
down_revision = "0015_p5_transfers_counts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres cannot add an enum value inside a transaction block that then uses it, but it
    # can add one that is only used by later statements — which is the case here: nothing in
    # this revision writes a row.
    op.execute("ALTER TYPE stock_transfer_status ADD VALUE IF NOT EXISTS 'reversed'")

    op.alter_column(
        "stock_transfers",
        "cancellation_entry_id",
        new_column_name="dispatch_reversal_entry_id",
    )
    op.alter_column("stock_transfers", "cancelled_date", new_column_name="undone_date")
    op.add_column(
        "stock_transfers", sa.Column("receive_reversal_entry_id", sa.BigInteger())
    )

    # The renamed column keeps its foreign key, which keeps 0015's name; rename it too, so the
    # constraint an error message quotes matches the column it is about.
    op.execute(
        "ALTER TABLE stock_transfers RENAME CONSTRAINT "
        "fk_stock_transfers_cancellation_entry TO fk_stock_transfers_dispatch_reversal_entry"
    )
    op.create_foreign_key(
        "fk_stock_transfers_receive_reversal_entry",
        "stock_transfers",
        "journal_entries",
        ["company_id", "receive_reversal_entry_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_stock_transfers_receive_reversal_entry", "stock_transfers", type_="foreignkey"
    )
    op.drop_column("stock_transfers", "receive_reversal_entry_id")
    op.execute(
        "ALTER TABLE stock_transfers RENAME CONSTRAINT "
        "fk_stock_transfers_dispatch_reversal_entry TO fk_stock_transfers_cancellation_entry"
    )
    op.alter_column("stock_transfers", "undone_date", new_column_name="cancelled_date")
    op.alter_column(
        "stock_transfers",
        "dispatch_reversal_entry_id",
        new_column_name="cancellation_entry_id",
    )
    # The enum value stays. Removing one means rewriting the type and every column that uses
    # it, and a downgrade that leaves an unused label behind is the lesser evil — the same
    # call `0009` made for the guard registry's enum.
