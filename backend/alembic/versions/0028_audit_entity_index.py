"""The audit index discriminates the entity, so a history read stops scanning the tenant.

Revision ID: 0028_audit_entity_index
Revises: 0027_p8_banking
Create Date: 2026-09-22

Issue #54, measured while fixing #20. Not a correctness fix — the four history readers return
the right rows in a defined order either way. This is about what it costs to get them.

`audit_log` carried `ix_audit_log_company_at` from `0001_p1_saas_shell`, and every reader of
the table filters `company_id` **and** `entity` **and** `entity_id`:

    GET /subledger/{role}/partners/{id}/history
    GET /gl/accounts/{id}/history
    GET /inventory/items/{id}/history
    the queue-action history on the EBM queue enquiry (`app/fiscal/enquiries.py`)

Two of the three filter columns were in no index, so the scan read every audit row the tenant
had ever written and discarded the ones belonging to other entities: the scan was
`O(tenant's audit history)` where the result is `O(one entity's history)`. That is the shape
that matters rather than the constant — the screen gets slower for the tenants that use the
product most.

**This is a replacement, not an addition.** `(company_id, at)` is a prefix of nothing useful
once the wider index exists, and keeping both pays for two. Dropping it needs the claim
"nothing reads `audit_log` by company and time alone" to be true, and
`tests/test_audit_index.py` is what keeps it true: it walks the AST of `app/` and fails on any
`AuditLog` query that filters `company_id` without also filtering `entity` and `entity_id`.
The issue flagged that this is the kind of claim that stops being true quietly — a retention
reaper or an operator console would want exactly that prefix — so it is now a guard rather
than a sentence in a commit message.

**`ix_audit_log_company_id` stays.** It is `CompanyScopedMixin`'s, present on every tenant
table, and it is what the `tenant_isolation` RLS policy reads (`company_id =
app_current_company_id()`) on a query with no entity in it at all. It is also 1 296 kB against
the new index's 11 MB, so it is not the one worth reclaiming.

**Plain `CREATE INDEX`, not `CONCURRENTLY`.** Every migration in this repository runs inside
alembic's transaction, and `CONCURRENTLY` cannot; taking it would mean an `autocommit_block()`
whose failure mode is an `INVALID` index left behind for somebody to notice. The build takes
317 ms on the 200 000-row fixture the PR body measures, against a table that is append-only
and never read in the write path — the `SHARE` lock blocks `record_audit` for that long and
nothing else. A tenant large enough for that to matter is a tenant large enough to want this
index, and the decision can be revisited with a real distribution rather than a synthetic one.

No data is touched: this revision creates and drops indexes and nothing else, so there is no
back-fill to prove against posted rows (rule 10's other half does not apply here).
"""

from alembic import op

revision = "0028_audit_entity_index"
down_revision = "0027_p8_banking"
branch_labels = None
depends_on = None

#: The tenant discriminator and nothing more — every history query filtered two more columns
#: than this index carried.
OLD_INDEX = "ix_audit_log_company_at"
NEW_INDEX = "ix_audit_log_company_entity_at"

#: `at` and `id` ride on the end for the `ORDER BY at DESC, id DESC` that #20 pinned. They do
#: not buy the ordering on their own — the issue measured `(company_id, at, id)` and it changed
#: nothing, because a bitmap heap scan loses index order and the bitmap scan was chosen for
#: want of the selective columns. They are there so that the *selective* index can also answer
#: the sort once the planner reaches for an index scan, which it does as soon as the filter is
#: narrow enough to make one cheaper.
NEW_COLUMNS = ["company_id", "entity", "entity_id", "at", "id"]


def upgrade() -> None:
    op.drop_index(OLD_INDEX, table_name="audit_log")
    op.create_index(NEW_INDEX, "audit_log", NEW_COLUMNS)


def downgrade() -> None:
    op.drop_index(NEW_INDEX, table_name="audit_log")
    op.create_index(OLD_INDEX, "audit_log", ["company_id", "at"])
