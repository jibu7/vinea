"""The companion stock entry a stock-bearing partner document posts (P6 decision 2).

**Two entries, one transaction.** An invoice that sells goods moves two ledgers: the partner
side (`ar` / `ap`, P4's posting) and the stock side (`inv`, through P5's primitives). They are
separate entries because they are separate postings — a trial balance that showed one without
the other would be wrong, and an auditor following either run must not find a hole where the
other one was. `partner_documents.stock_entry_id` links them, and the `STK` sequence numbers
the companion.

**The stock side posts first**, and that ordering is load-bearing rather than tidy. Two of the
four cases need the value the stock ledger actually moved before the partner side can be
built at all:

* a **return to supplier** credits the accrual for exactly what was issued, and sends the
  difference between that and the credit claimed to purchase price variance;
* an **unmatched purchase** receives at the line's net cost and debits the accrual for the
  same value, so the accrual nets to zero inside the document.

A sale is the easy case — COGS is entirely on the companion — but it goes through the same
path, because a second path is how the two would come to disagree.

A document whose stock lines all valued nothing has **no companion and claims no number**:
there is no entry to write, and a `STK-` number with nothing behind it would be a hole in the
run.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory import stock as stock_service
from app.kernel.errors import LedgerStateError
from app.kernel.sequences import DocType
from app.models.inventory import StockMove
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind, PartnerDocumentLine
from app.models.user import User

ZERO = Decimal(0)


@dataclass(frozen=True)
class CompanionResult:
    """What the companion posted, and the value it moved per document line."""

    entry_id: int | None
    #: Index into the document's computed lines → the base-currency value that line moved.
    #: Positive is value *into* stock, negative is value out of it.
    values: dict[int, Decimal]


def _issued_unit_cost(db: Session, company_id: int, returns_line_id: int) -> Decimal | None:
    """The unit cost the line being returned was actually issued at.

    A customer return comes back at what it cost when it left, not at today's average
    (decision 2). Without this a sale at 1 000 returned after the average moved to 1 200 would
    put 200 of profit into stock that nobody ever earned.
    """
    line = db.scalar(
        select(PartnerDocumentLine).where(
            PartnerDocumentLine.company_id == company_id,
            PartnerDocumentLine.id == returns_line_id,
        )
    )
    if line is None:
        raise LedgerStateError(
            "The line being returned does not exist",
            code="returns_line_not_found",
            field_errors={"returns_line_id": ["not found"]},
        )
    if line.base_quantity is None or line.base_quantity == ZERO:
        return None
    # **`source_line_id` is not namespaced, so the document type has to be part of the key.**
    # Every kind of stock document writes its own line id into this one column: a GRN writes
    # goods-received line ids, a landed cost writes the *GRN* line ids it allocated onto, an
    # inventory document writes its own. Nothing keeps those id spaces apart, so matching on
    # `source_line_id` alone returns whichever row the planner reaches first.
    #
    # Found by the acceptance tape, at row 13. A landed cost had revalued the same receipt, so
    # its zero-quantity move — keyed on GRN line 1, carrying no `unit_cost`, because a
    # revaluation has none — collided with sale line 1 and won. `unit_cost is None` then looked
    # exactly like "this line was never issued", and the credit note fell through to the
    # *current* average: 5 bottles came back at 1 111.1 instead of the 1 000 they left at,
    # putting 556 of profit into stock that nobody earned. Silent, because the fallback is a
    # legitimate path for a goodwill credit with no `returns_line_id`.
    move = db.scalar(
        select(StockMove).where(
            StockMove.company_id == company_id,
            StockMove.source_doc_type == "partner_document",
            StockMove.source_line_id == line.id,
        )
    )
    if move is None or move.unit_cost is None:
        return None
    return abs(move.unit_cost)


def post_companion(
    db: Session,
    company_id: int,
    *,
    role: PartnerRole,
    kind: DocumentKind,
    computed: list,
    document_date,  # noqa: ANN001 - date; typed loosely to avoid a circular import
    description: str,
    reference: str | None,
    document_id: int,
    rate: Decimal,
    accrual_account_id: int | None,
    cogs_account_id_for,  # noqa: ANN001 - callable(item) -> int
    actor: User,
) -> CompanionResult:
    """Post the stock side of a partner document, before the partner side is built.

    The four rows of decision 2's posting map, and nothing else lives here — the partner side
    reads `CompanionResult.values` and decides what to do with them.
    """
    movers = [(index, line) for index, line in enumerate(computed) if line.moves_stock]
    if not movers:
        return CompanionResult(entry_id=None, values={})

    document = stock_service.StockDocument(
        doc_type=str(DocType.STOCK_COMPANION),
        move_date=document_date,
        description=description,
        reference=reference,
        source_doc_type="partner_document",
        source_doc_id=document_id,
    )

    receiving = (role == PartnerRole.AR and kind == DocumentKind.CREDIT_NOTE) or (
        role == PartnerRole.AP and kind == DocumentKind.INVOICE
    )
    lines: list[stock_service.StockLine] = []
    for _index, line in movers:
        if role == PartnerRole.AR:
            # A sale issues at the average and a return receives at what was issued; both
            # face COGS, so the contra is the item's COGS account either way.
            contra = cogs_account_id_for(line.item)
            unit_cost = None
            if kind == DocumentKind.CREDIT_NOTE:
                # Decision 2: a return comes back at the cost the line it returns was issued
                # at, **or at the current average** when it names no such line. The second
                # half is not a fallback for tidiness — `receive_stock` refuses a receipt with
                # no unit cost, so a credit note raised without `returns_line_id` (a goodwill
                # credit, a return nobody could tie to an invoice) would simply not post. The
                # property suite found exactly that, on its first pass.
                unit_cost = (
                    _issued_unit_cost(db, company_id, line.source.returns_line_id)
                    if line.source.returns_line_id is not None
                    else None
                )
                if unit_cost is None:
                    unit_cost = stock_service.item_state(
                        db, company_id, line.item.id
                    ).average
        else:
            # Both AP cases face the accrual: an unmatched purchase credits it as the goods
            # arrive, a return to supplier debits it as they go back.
            if accrual_account_id is None:
                raise LedgerStateError(
                    "No GRN accrual account is configured — set it on Order defaults",
                    code="gl_setting_missing",
                    field_errors={"grn_accrual_account_id": ["required"]},
                )
            contra = accrual_account_id
            # An unmatched purchase is valued at what the invoice says the goods cost, in base
            # currency at the document's own rate. A return to supplier is valued at the
            # average, like any other issue — what we paid is the *claim*, not the cost.
            unit_cost = (
                (line.net / line.base_quantity) * rate
                if kind == DocumentKind.INVOICE and line.base_quantity
                else None
            )
        lines.append(
            stock_service.StockLine(
                item_id=line.item.id,
                warehouse_id=line.warehouse_id,
                quantity=line.base_quantity,
                unit_cost=unit_cost,
                contra_account_id=contra,
                project_id=line.project_id,
                description=line.source.description or description,
                source_line_id=line.line_id,
            )
        )

    primitive = stock_service.receive_stock if receiving else stock_service.issue_stock
    posting = primitive(db, company_id, document=document, lines=lines, actor=actor)

    values: dict[int, Decimal] = {}
    for (index, _line), move in zip(movers, posting.keyed_moves, strict=True):
        values[index] = move.value
    return CompanionResult(
        entry_id=posting.entry.id if posting.entry is not None else None, values=values
    )
