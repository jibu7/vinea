"""P7 step 4 — `fiscal:close_day`, and the roles that hold it.

Decision 15 lists the phase's new permissions and names none for **closing the fiscal day**:
it gives `fiscal:reports_view` the X/Z *view*, which is a reading, and the close is not one. A
Z is irreversible, claims a number from the device's `FZR` run and moves the boundary every
later day is measured from. Step 4 first put it under `fiscal:queue_manage`, which would have
granted it to whoever may press Retry on a queue row — a different job from closing a till.

So it gets its own constant, back-filled here. A deviation from decision 15's list, recorded in
`docs/p7-step-4-report.md` rather than made quietly.

Revision ID: 0025_p7_close_day_permission
Revises: 0024_p7_device_activated_at
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "0025_p7_close_day_permission"
down_revision = "0024_p7_device_activated_at"
branch_labels = None
depends_on = None

#: The one permission this revision adds. Frozen as a literal rather than imported from
#: `app.core.permissions` (architecture rule 10: a migration must not drift with the constants
#: it was written against).
CLOSE_DAY = "fiscal:close_day"

#: Who holds it out of the box. The administrator holds everything; the accountant already
#: reads the queue and files the return, and closing the day belongs beside those.
SYSTEM_ROLES = ("Administrator", "Accountant")


def upgrade() -> None:
    for role in SYSTEM_ROLES:
        op.execute(
            sa.text(
                """
                UPDATE roles
                   SET permissions = permissions || CAST(:permission AS jsonb)
                 WHERE is_system
                   AND name = :role
                   AND NOT (permissions @> CAST(:permission AS jsonb))
                """
            ).bindparams(permission=json.dumps([CLOSE_DAY]), role=role)
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE roles SET permissions = permissions - CAST(:permission AS text) "
            "WHERE is_system"
        ).bindparams(permission=CLOSE_DAY)
    )
