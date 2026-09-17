"""P7 step 2 — the outbox names its source the way every other table does, and a document may
hold the refund that reversed it.

Revision ID: 0023_p7_outbox_source_type
Revises: 0022_p7_fiscal
Create Date: 2026-09-17

`fiscal_outbox.source_doc_type` arrived at step 1 as `VARCHAR(10)`, which is out of step with
the rest of the build: `journal_lines`, `stock_moves` and `audit_log` all carry
`source_doc_type VARCHAR(50)` holding a table-shaped name — `partner_document`,
`inventory_document`, `goods_received_note`. Ten characters fits none of them.

Step 2 is where that bites. A `sale` row names the partner document it reports, and the refund
a **reversal** owes names the same document under its own source type so that "one document,
one row" stays literally true per type; step 3 will name stock postings and goods receipts the
same way. All of those are longer than ten characters, and shortening them to fit would give
the outbox a private vocabulary for the one thing every other table already spells out.

**A separate revision rather than an edit to 0022**, which has been applied here and in every
CI run since it was pushed (architecture rule 10). The widening is safe on a populated table —
it is a `VARCHAR(10)` → `VARCHAR(50)` on a nullable column, which Postgres does without
rewriting — and the only rows that exist anywhere are in test databases.

**The second change: one receipt per document *per receipt type*.**

0022 made `fiscal_receipts` unique on (company, document), which reads as decision 5's "one
receipt per fiscalized document" and is right for as long as a document can only ever be sold.
Decision 7 says what happens when a **signed sale is reversed**: RRA cannot un-sign it, so the
reversal queues a full refund against the same invoice. That refund comes back with its own
counters, its own signature and its own QR — a second receipt, about the same document.

It has to be stored as a receipt rather than left in the queue row's response, because
decision 11 computes the Z report **from `fiscal_receipts`**. A refund RRA signed that the Z
could not see would make the day's close disagree with what the authority holds, which is the
one thing a Z is for.

So the constraint becomes (company, document, receipt_type): an invoice may hold its `NS` and
the `NR` that reversed it, and it still cannot hold two of either.
`uq_fiscal_receipts_company_outbox` is untouched — one receipt per queue row, always.

Neither change touches a posted table, so there is no back-fill to prove against posted rows:
`fiscal_outbox` is a queue, not a ledger, and this revision reads no row and writes none.
"""

import sqlalchemy as sa

from alembic import op

revision = "0023_p7_outbox_source_type"
down_revision = "0022_p7_fiscal"
branch_labels = None
depends_on = None

#: The width every other `source_doc_type` in the schema carries.
SOURCE_DOC_TYPE_WIDTH = 50
PREVIOUS_WIDTH = 10


def upgrade() -> None:
    op.alter_column(
        "fiscal_outbox",
        "source_doc_type",
        existing_type=sa.String(PREVIOUS_WIDTH),
        type_=sa.String(SOURCE_DOC_TYPE_WIDTH),
        existing_nullable=True,
    )
    op.drop_constraint(
        "uq_fiscal_receipts_company_document", "fiscal_receipts", type_="unique"
    )
    op.create_unique_constraint(
        "uq_fiscal_receipts_company_document_type",
        "fiscal_receipts",
        ["company_id", "document_id", "receipt_type"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_fiscal_receipts_company_document_type", "fiscal_receipts", type_="unique"
    )
    op.create_unique_constraint(
        "uq_fiscal_receipts_company_document", "fiscal_receipts", ["company_id", "document_id"]
    )
    op.alter_column(
        "fiscal_outbox",
        "source_doc_type",
        existing_type=sa.String(SOURCE_DOC_TYPE_WIDTH),
        type_=sa.String(PREVIOUS_WIDTH),
        existing_nullable=True,
    )
