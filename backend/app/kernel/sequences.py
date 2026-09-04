"""Gapless document numbering (ADR-07).

`claim_number` runs inside the caller's transaction: the `SELECT … FOR UPDATE` row lock is
held until commit, so concurrent posters of the same (company, doc_type) serialize and a
rolled-back posting gives its number back. That serialization is the price of gaplessness
required for fiscal documents; it is per doc type, not global.
"""

import enum
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.journal import DocumentSequence

NUMBER_WIDTH = 6


class DocType(enum.StrEnum):
    JOURNAL = "JE"
    CASHBOOK = "CB"
    YEAR_END = "YE"


DEFAULT_PREFIXES: dict[str, str] = {
    DocType.JOURNAL: "JE-",
    DocType.CASHBOOK: "CB-",
    DocType.YEAR_END: "YE-",
}


@dataclass(frozen=True)
class ClaimedNumber:
    number: str
    sequence_no: int
    doc_type: str


def format_number(prefix: str, sequence_no: int) -> str:
    return f"{prefix}{sequence_no:0{NUMBER_WIDTH}d}"


def ensure_sequence(
    db: Session,
    company_id: int,
    doc_type: str,
    *,
    branch_id: int | None = None,
    prefix: str | None = None,
) -> None:
    """Idempotent, race-safe creation (NULLS NOT DISTINCT unique scope + ON CONFLICT)."""
    statement = (
        insert(DocumentSequence)
        .values(
            company_id=company_id,
            branch_id=branch_id,
            doc_type=doc_type,
            prefix=prefix if prefix is not None else DEFAULT_PREFIXES.get(doc_type, f"{doc_type}-"),
            next_number=1,
        )
        .on_conflict_do_nothing(constraint="uq_document_sequences_scope")
    )
    db.execute(statement)


def claim_number(
    db: Session, company_id: int, doc_type: str, branch_id: int | None = None
) -> ClaimedNumber:
    """Claim the next number for (company, doc_type[, branch]). A branch-level sequence is
    used when one exists for that branch; otherwise the company-wide sequence."""
    doc_type = str(doc_type)
    scope_branch: int | None = None
    if branch_id is not None:
        exists = db.scalar(
            select(DocumentSequence.id).where(
                DocumentSequence.company_id == company_id,
                DocumentSequence.doc_type == doc_type,
                DocumentSequence.branch_id == branch_id,
            )
        )
        scope_branch = branch_id if exists is not None else None
    if scope_branch is None:
        ensure_sequence(db, company_id, doc_type)

    row = db.execute(
        text(
            """
            UPDATE document_sequences
               SET next_number = next_number + 1, updated_at = now()
             WHERE id = (
                 SELECT id FROM document_sequences
                  WHERE company_id = :company_id
                    AND doc_type = :doc_type
                    AND branch_id IS NOT DISTINCT FROM :branch_id
                  FOR UPDATE
             )
            RETURNING prefix, next_number - 1 AS claimed
            """
        ),
        {"company_id": company_id, "doc_type": doc_type, "branch_id": scope_branch},
    ).one()
    return ClaimedNumber(
        number=format_number(row.prefix, row.claimed), sequence_no=row.claimed, doc_type=doc_type
    )
