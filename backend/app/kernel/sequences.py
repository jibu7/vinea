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
    # P6 — order entry. `STK` is the companion stock entry a stock-bearing partner document
    # posts beside its own (decision 2); it is a *separate run* from the document's number
    # because the two entries are two postings and an auditor following either run must not
    # find a hole where the other one was.
    #
    # A run enters this enum in the same commit as the table that holds its numbers: a doc
    # type with no claimant fails `test_every_doc_type_registers_a_claimant`, and a claimant
    # naming a table that does not exist fails
    # `test_every_claimant_names_a_real_table_and_column`. `STK` could be here at step 1
    # because its claimant is `journal_entries`, which has existed since P2; `GRN` arrived with
    # the table that holds its numbers, `SO` and `PO` with theirs, and `LCA` with
    # `landed_cost_documents` at step 4.
    STOCK_COMPANION = "STK"
    #: A goods receipt. The GRN *is* the stock document, so its entry takes this number —
    #: and a receipt whose every line cost nothing posts no entry at all and holds the
    #: number itself, which is why this run registers two claimants (decision 6).
    GOODS_RECEIVED = "GRN"
    #: An order is a **commitment, not a posting** (decision 3): it produces no journal entry,
    #: so there is no entry whose number it could take and the order table holds its own. It
    #: keeps that number when it is cancelled, too — a cancelled order is a thing that happened
    #: and an auditor following the `SO-` run must not find a hole where one was withdrawn.
    SALES_ORDER = "SO"
    PURCHASE_ORDER = "PO"
    #: A landed-cost allocation (Importation Split, decision 9). Its entry takes this number,
    #: the way a GRN's does — and unlike a GRN there is no valueless case to register beside
    #: it: a landed cost **always** values something, because an allocation of zero is refused
    #: before a document exists.
    LANDED_COST = "LCA"
    # P7 — fiscalization. Five of these are **branch-scoped**: a device's numbers are its own,
    # because the revenue authority keys a sale by (taxpayer, branch, invoice number) and two
    # branches sharing a run would each see the other's holes. `claim_number` falls back to
    # the company-wide row when no branch row exists, and device activation creates the
    # branch-level rows — so the fallback is what a *non-fiscalized* company gets and the
    # branch row is what a device gets.
    #
    # Four of them number an **integer column** rather than a formatted string: what the
    # authority receives is `invcNo: 7`, not `FIS-000007`. The claimant registry says so
    # (`numeric=True`) and the gapless checker reads the integer, rather than the run being
    # left out of the check — a fiscal number space is the last one that should go unproven.
    #: The authority's invoice number for a sale or refund (`fiscal_outbox.invc_no`).
    FISCAL_SALE = "FIS"
    #: The same, for a purchase or a purchase confirmation.
    FISCAL_PURCHASE = "FIP"
    #: The stock-movement number on a stock in/out report.
    FISCAL_STOCK = "FSAR"
    #: The 7-digit sequence inside a registered item code (`RW2NTXU0000001`). Company-wide,
    #: not branch-scoped: an item is registered once for the taxpayer, not once per shop.
    FISCAL_ITEM = "FITM"
    #: Z-report numbers per device.
    FISCAL_Z_REPORT = "FZR"
    # P7 — the two things this phase posts through the kernel.
    #: A filed VAT return and its settlement entry (decision 12).
    VAT_RETURN = "VAT"
    #: An unrealized FX revaluation run (decision 13). ADR-05 reserved `FXR` for it at P2 and
    #: this is the phase that makes it real.
    FX_REVALUATION = "FXR"
    # P8 — banking. None of these three numbers a posting: a statement, a reconciliation and a
    # payment run are **records of banking acts**, and every journal entry the phase causes
    # takes its number from the P2/P4 run that already owns it (a `CB-` cashbook entry, a
    # `PMT-` settlement, an `ALC-`/`ALJ-` allocation). So all three claimants are the phase's
    # own tables, and each run is gapless over rows that exist whether or not anything posted.
    #: An imported (or keyed) bank statement.
    BANK_STATEMENT = "BST"
    #: A bank reconciliation, numbered when it is **opened** — it is nameable from that moment,
    #: it may be reopened, and an auditor following the `BRC-` run must not find a hole where
    #: one was opened and never locked. The same argument as P5's count session.
    BANK_RECONCILIATION = "BRC"
    #: A supplier payment run. Claimed at posting — there are no draft rows — and held by every
    #: row including the reversed ones, as orders do.
    PAYMENT_RUN = "PYR"


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
    DocType.STOCK_COMPANION: "STK-",
    DocType.GOODS_RECEIVED: "GRN-",
    DocType.SALES_ORDER: "SO-",
    DocType.PURCHASE_ORDER: "PO-",
    DocType.LANDED_COST: "LCA-",
    # The four numeric runs carry a prefix only because `document_sequences.prefix` is NOT
    # NULL and `format_number` is shared. Nothing prints these strings: what the payload
    # carries is the integer, and what the *receipt* prints is the document's own number.
    DocType.FISCAL_SALE: "FIS-",
    DocType.FISCAL_PURCHASE: "FIP-",
    DocType.FISCAL_STOCK: "FSAR-",
    DocType.FISCAL_ITEM: "FITM-",
    DocType.FISCAL_Z_REPORT: "Z-",
    DocType.VAT_RETURN: "VATR-",
    DocType.FX_REVALUATION: "FXR-",
    DocType.BANK_STATEMENT: "BST-",
    DocType.BANK_RECONCILIATION: "BRC-",
    DocType.PAYMENT_RUN: "PYR-",
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
    #: The column holds the **integer** the sequence issued, not a formatted `PREFIX-000007`.
    #:
    #: P7 is the first phase whose numbers leave the building as numbers: a revenue authority
    #: receives `invcNo: 7`, and storing `FIS-000007` beside it so that one checker could keep
    #: parsing trailing digits would be a formatted copy of a value the wire never carries.
    #: The alternative considered and rejected was to leave these runs out of the gapless
    #: check — which is exactly backwards, since a fiscal invoice number with a hole in it is
    #: a question from a revenue authority rather than an untidy report.
    numeric: bool = False
    #: A SQL expression yielding the **branch** a row belongs to, in terms of the claimant's
    #: own table. Required of any claimant whose run can be branch-scoped, and absent on every
    #: run that cannot.
    #:
    #: An expression rather than a predicate, because the checker asks two different questions
    #: of it. A *branch-level* sequence asks "which of these rows are mine" — the expression
    #: equals that branch. The *company-wide* row for the same doc type asks the complement:
    #: "which rows are nobody's branch" — because a company may run a branch-level sequence on
    #: one branch and the company-wide fallback everywhere else, and counting a branch's rows
    #: against the fallback would report a gap in a run that has none.
    #:
    #: Until P7 the gapless checker skipped branch-scoped runs outright, saying so in a
    #: comment: nothing claimed one, and checking the wrong number space quietly is worse than
    #: not checking. P7's device runs are the first that do, and the checker now refuses a
    #: branch-scoped run whose claimant cannot say which branch a row belongs to — so the skip
    #: cannot come back by accident.
    branch_expression: str | None = None


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
#: A goods receipt whose every line was received at zero cost: quantity moved, the ledger had
#: nothing to record, and the number is the GRN's own. The same case as a valueless stock
#: document, one table along.
_VALUELESS_GRN = SequenceClaimant(
    table="goods_received_notes", where="journal_entry_id IS NULL"
)
#: An allocation is numbered whether or not it posts anything: the realized FX and settlement
#: discount it may produce are a *different* run (`ALJ-`), so `ALC-` belongs to this table
#: alone (P4).
_ALLOCATION = SequenceClaimant(table="allocations")
#: An order posts nothing, ever, so there is no entry to inherit a number from and the order
#: table is the run's only claimant. No `where` clause: every row in these tables holds a
#: number of its own, including the cancelled ones (P6 decision 3).
_SALES_ORDER = SequenceClaimant(table="sales_orders")
_PURCHASE_ORDER = SequenceClaimant(table="purchase_orders")
#: P7. An outbox row holds the authority's number for its kind: `invc_no` on a sale, a refund,
#: a purchase and a purchase confirmation, `sar_no` on a stock movement. Two runs share the
#: `invc_no` column and are told apart by `kind`, which is what `where` is doing here — a
#: `doc_type_column` would need the table to carry a doc type it has no other use for.
#: A device belongs to exactly one branch, so a row's branch is its device's — one subquery,
#: and no branch column denormalised onto the outbox to go stale.
def _device_branch(table: str) -> str:
    return f"(SELECT d.branch_id FROM fiscal_devices d WHERE d.id = {table}.device_id)"


_OUTBOX_BRANCH = _device_branch("fiscal_outbox")
_DAILY_REPORT_BRANCH = _device_branch("fiscal_daily_reports")
_FISCAL_SALE_ROW = SequenceClaimant(
    table="fiscal_outbox",
    number_column="invc_no",
    where="kind IN ('sale', 'refund')",
    numeric=True,
    branch_expression=_OUTBOX_BRANCH,
)
_FISCAL_PURCHASE_ROW = SequenceClaimant(
    table="fiscal_outbox",
    number_column="invc_no",
    where="kind IN ('purchase', 'purchase_confirm')",
    numeric=True,
    branch_expression=_OUTBOX_BRANCH,
)
_FISCAL_STOCK_ROW = SequenceClaimant(
    table="fiscal_outbox",
    number_column="sar_no",
    where="kind = 'stock_io'",
    numeric=True,
    branch_expression=_OUTBOX_BRANCH,
)
#: The registered item code carries its sequence as its last seven digits (`RW2NTXU0000001`),
#: so the ordinary trailing-digit parse reads it and this claimant is **not** numeric.
_FISCAL_ITEM = SequenceClaimant(table="fiscal_items", number_column="item_cd")
_FISCAL_Z_REPORT = SequenceClaimant(
    table="fiscal_daily_reports",
    number_column="report_no",
    numeric=True,
    branch_expression=_DAILY_REPORT_BRANCH,
)
#: A return over a range with no VAT movement at all declares nothing and therefore posts no
#: settlement entry — a nil return is still an act of filing, and RRA expects one. So it holds
#: its own number, the same shape as a valueless stock document (P5 decision 1) and as a
#: revaluation whose every difference was zero. A return that *did* post shares its entry's
#: number, which is why this claimant is narrowed rather than counting every row.
_NIL_VAT_RETURN = SequenceClaimant(table="vat_returns", where="journal_entry_id IS NULL")
#: A revaluation run whose every difference was zero posts nothing, so there is no entry to
#: take a number from and the run holds its own — the same shape as a valueless stock document
#: (P5 decision 1). A run that *did* post shares its entry's number, which is why this
#: claimant is narrowed rather than counting every row.
_VALUELESS_REVALUATION = SequenceClaimant(
    table="fx_revaluations", where="journal_entry_id IS NULL"
)
#: P8. Each of these tables holds its own number outright — no `where`, and no `_ENTRY`
#: alternative — because none of the three rows *is* a posting. A statement records what the
#: bank said; a reconciliation records a proof; a payment run records a banking act whose
#: postings are ordinary `PMT-` documents with numbers of their own. A voided statement, a
#: reopened reconciliation and a reversed run all keep their numbers, the way a cancelled
#: order keeps its `SO-` (P6 decision 3): each is a thing that happened.
_BANK_STATEMENT = SequenceClaimant(table="bank_statements")
_BANK_RECONCILIATION = SequenceClaimant(table="bank_reconciliations")
_PAYMENT_RUN = SequenceClaimant(table="payment_runs")

#: A claimant is checked against the live schema, so a run can only be registered once the
#: table that holds its numbers exists — see the P6 note in `DocType`.
#:
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
    # The companion entry is always an entry — a stock line with no value posts no companion
    # at all and claims no number — so `_ENTRY` is its only claimant (decision 2).
    DocType.STOCK_COMPANION: (_ENTRY,),
    DocType.GOODS_RECEIVED: (_ENTRY, _VALUELESS_GRN),
    DocType.SALES_ORDER: (_SALES_ORDER,),
    DocType.PURCHASE_ORDER: (_PURCHASE_ORDER,),
    # One claimant, not two: an allocation of nothing never becomes a document, so there
    # is no valueless landed cost to hold a number of its own.
    DocType.LANDED_COST: (_ENTRY,),
    DocType.FISCAL_SALE: (_FISCAL_SALE_ROW,),
    DocType.FISCAL_PURCHASE: (_FISCAL_PURCHASE_ROW,),
    DocType.FISCAL_STOCK: (_FISCAL_STOCK_ROW,),
    DocType.FISCAL_ITEM: (_FISCAL_ITEM,),
    DocType.FISCAL_Z_REPORT: (_FISCAL_Z_REPORT,),
    # The return's entry takes the return's number, as every posting document's does — and a
    # nil return, which posts none, holds it itself.
    DocType.VAT_RETURN: (_ENTRY, _NIL_VAT_RETURN),
    DocType.FX_REVALUATION: (_ENTRY, _VALUELESS_REVALUATION),
    DocType.BANK_STATEMENT: (_BANK_STATEMENT,),
    DocType.BANK_RECONCILIATION: (_BANK_RECONCILIATION,),
    DocType.PAYMENT_RUN: (_PAYMENT_RUN,),
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
