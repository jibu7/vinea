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
    # P4 — AR/AP partner documents. The partner document and the journal entry it produces
    # share one number, so there is exactly one gapless sequence per document type.
    AR_INVOICE = "ARIN"
    AR_CREDIT_NOTE = "ARCN"
    AR_RECEIPT = "ARRC"
    AP_INVOICE = "APIN"
    AP_DEBIT_NOTE = "APDN"
    AP_PAYMENT = "APPY"
    # Journal batches post ordinary invoice/credit-note documents under the JNL transaction
    # type, but they must never consume an invoice number — a journal debit is not an invoice
    # and an audit that follows INV- numbering would find a hole. Separate sequences, and
    # separate prefixes because `partner_documents.number` is unique per company.
    AR_JOURNAL = "ARJN"
    AP_JOURNAL = "APJN"
    ALLOCATION = "ALC"
    # The realized-FX / settlement-discount entry an allocation posts, and the transfer
    # that moves a matured post-dated instrument into the bank.
    ALLOCATION_JOURNAL = "ALJ"
    INSTRUMENT_MATURITY = "MAT"
    # P5 — inventory documents. One sequence per document type, as in P4: an adjustment is
    # not a transfer and an auditor following ADJ- numbering must not find a hole where a
    # count happened to be posted.
    INV_ADJUSTMENT = "INAJ"
    INV_JOURNAL = "INJN"
    INV_TRANSFER = "INTR"
    INV_COUNT = "INCT"
    # A count *session* is not a posting: it is the sheet somebody walks the aisles with, and
    # it has to be nameable from the moment it is opened — days before it posts anything, and
    # still when it is cancelled having posted nothing at all. Its own run, because every
    # number in the `CNT-` run belongs to a variance document that reached the ledger, and a
    # session claiming one would leave a hole where a posting should be (the gapless check in
    # `tests/kernel/invariants.py` states exactly that).
    INV_COUNT_SESSION = "INCS"


DEFAULT_PREFIXES: dict[str, str] = {
    DocType.JOURNAL: "JE-",
    DocType.CASHBOOK: "CB-",
    DocType.YEAR_END: "YE-",
    DocType.AR_INVOICE: "INV-",
    DocType.AR_CREDIT_NOTE: "CRN-",
    DocType.AR_RECEIPT: "RCT-",
    DocType.AP_INVOICE: "SIN-",
    DocType.AP_DEBIT_NOTE: "DBN-",
    DocType.AP_PAYMENT: "PMT-",
    DocType.AR_JOURNAL: "ARJ-",
    DocType.AP_JOURNAL: "APJ-",
    DocType.ALLOCATION: "ALC-",
    DocType.ALLOCATION_JOURNAL: "ALJ-",
    DocType.INSTRUMENT_MATURITY: "MAT-",
    DocType.INV_ADJUSTMENT: "ADJ-",
    DocType.INV_JOURNAL: "IJN-",
    DocType.INV_TRANSFER: "TRF-",
    DocType.INV_COUNT: "CNT-",
    DocType.INV_COUNT_SESSION: "CNS-",
}


@dataclass(frozen=True)
class SequenceClaimant:
    """A table that can hold a number claimed from `document_sequences`.

    The registry below is the answer to "who holds the numbers in this run?", and it is here,
    beside the sequence itself, rather than in the checker that reads it — because a module
    that starts claiming numbers has to say so *where the numbers are defined*, not by editing
    a test three directories away that it has no reason to look at.

    Columns are named as strings, deliberately. The kernel does not import the subledger or
    the inventory module, and a registry of model classes would invert that; a table and a
    column name cost one query and no dependency.
    """

    #: The table holding the number.
    table: str
    number_column: str = "number"
    #: The column naming the doc type, when one table serves several runs (`journal_entries`).
    #: `None` means the table serves exactly the doc types registered against it.
    doc_type_column: str | None = None
    #: A SQL predicate narrowing to the rows that hold a number **of their own**. A document
    #: that posted a journal entry shares that entry's number, and counting both would make
    #: every ordinary document look like a duplicate claim.
    where: str | None = None


#: Nearly every run: the entry a posting produced, which is also the number its document
#: quotes (`partner_documents`, `inventory_documents`).
_ENTRY = SequenceClaimant(table="journal_entries", doc_type_column="doc_type")
#: A stock document whose posting valued nothing has no entry to take a number from and
#: claims one itself (P5 decision 1).
_VALUELESS_STOCK_DOCUMENT = SequenceClaimant(
    table="inventory_documents", doc_type_column="doc_type", where="journal_entry_id IS NULL"
)
#: The same case for a transfer, whose number is its dispatch leg's.
_VALUELESS_TRANSFER = SequenceClaimant(
    table="stock_transfers", where="dispatch_entry_id IS NULL"
)
#: A count sheet is nameable from the moment it is opened and may never post at all, so it
#: has a run of its own rather than consuming a posting number (P5 decision 7).
_COUNT_SHEET = SequenceClaimant(table="stock_count_sessions")
#: An allocation is numbered whether or not it posts anything: the realized FX and settlement
#: discount it may produce are a *different* run (`ALJ-`), so `ALC-` belongs to this table
#: alone (P4).
_ALLOCATION = SequenceClaimant(table="allocations")

#: **doc type → who may hold its numbers.** Every `DocType` must appear here, and
#: `tests/kernel/test_sequence_registry.py` fails the build if one does not;
#: `assert_ledger_invariants` fails on any sequence a company actually uses whose doc type is
#: unregistered. A later phase registers its claimant here — it never edits the checker.
SEQUENCE_CLAIMANTS: dict[str, tuple[SequenceClaimant, ...]] = {
    DocType.JOURNAL: (_ENTRY,),
    DocType.CASHBOOK: (_ENTRY,),
    DocType.YEAR_END: (_ENTRY,),
    DocType.AR_INVOICE: (_ENTRY,),
    DocType.AR_CREDIT_NOTE: (_ENTRY,),
    DocType.AR_RECEIPT: (_ENTRY,),
    DocType.AP_INVOICE: (_ENTRY,),
    DocType.AP_DEBIT_NOTE: (_ENTRY,),
    DocType.AP_PAYMENT: (_ENTRY,),
    DocType.AR_JOURNAL: (_ENTRY,),
    DocType.AP_JOURNAL: (_ENTRY,),
    DocType.ALLOCATION: (_ALLOCATION,),
    DocType.ALLOCATION_JOURNAL: (_ENTRY,),
    DocType.INSTRUMENT_MATURITY: (_ENTRY,),
    DocType.INV_ADJUSTMENT: (_ENTRY, _VALUELESS_STOCK_DOCUMENT),
    DocType.INV_JOURNAL: (_ENTRY, _VALUELESS_STOCK_DOCUMENT),
    DocType.INV_TRANSFER: (_ENTRY, _VALUELESS_TRANSFER),
    DocType.INV_COUNT: (_ENTRY, _VALUELESS_STOCK_DOCUMENT),
    DocType.INV_COUNT_SESSION: (_COUNT_SHEET,),
}


def claimants_for(doc_type: str) -> tuple[SequenceClaimant, ...]:
    """Who may hold a number in this run, or a refusal naming what to do about it."""
    claimants = SEQUENCE_CLAIMANTS.get(str(doc_type))
    if not claimants:
        raise KeyError(
            f"{doc_type} claims numbers from document_sequences but registers no claimant. "
            "Add one to SEQUENCE_CLAIMANTS in app/kernel/sequences.py — the gapless check "
            "reads that registry, and a run nobody owns cannot be proven gapless."
        )
    return claimants


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
