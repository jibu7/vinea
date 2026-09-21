"""P7 step 9 — a Z owns receipts by counter, not by clock.

Revision ID: 0026_p7_z_high_water
Revises: 0025_p7_close_day_permission
Create Date: 2026-09-21

Step 8 found the hole. A Z's range was cut on `sdc_datetime` — RRA's clock, on RRA's server —
while the close was cut on `now()`, Vinea's. Under any skew between the two, a receipt in
flight at the close comes back stamped *before* the close, lands inside a Z whose figures are
already frozen, and the next Z opens exclusively after that same instant: the receipt is on
neither day. Nothing asserted otherwise, and in production it would happen on any slow day.

It is the same shape as a VAT late entry, and decision 12 already answered that one:
**membership, not a timestamp range.** So a Z gains a high-water mark and owns every receipt
whose `tot_rcpt_no` is above the previous Z's mark and at or below its own; the open X owns
everything above the last mark. `tot_rcpt_no` is the key because it is strictly increasing per
device across receipt types (proven, not assumed — `assert_fiscal_invariants` clause 4), it is
clock-free, and it is the number RRA keys the receipt by, so a Z reads "covers receipts 12–19"
to an inspector.

Additive and nullable, which is the only shape available: `fiscal_daily_reports` is immutable
by trigger (`VN011`, revision 0022), so there is no UPDATE to be had here and none is wanted —
a stored Z is a legal document and a migration that rewrote one would be rewriting history
(architecture rule 3). A NULL mark therefore means exactly what it says: a Z closed before
this revision, whose membership stays its legacy `sdc_datetime` range. The first Z closed
after the upgrade takes its mark from the device's current counter, and `app/fiscal/daily.py`
translates the legacy boundary into a counter when it needs the two to meet.

`tests/test_p7_z_high_water_backfill.py` provisions a tenant at 0025 with a stored Z and its
receipts, upgrades, and asserts the old Z computes unchanged while the next one closes by
membership — rule 10's half that `make migrate-check` cannot reach.
"""

import sqlalchemy as sa

from alembic import op

revision = "0026_p7_z_high_water"
down_revision = "0025_p7_close_day_permission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fiscal_daily_reports",
        sa.Column("high_water_rcpt_no", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("fiscal_daily_reports", "high_water_rcpt_no")
