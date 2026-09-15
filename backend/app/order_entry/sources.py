"""What a move or an entry came from, resolved to something a screen can open.

`stock_moves.source_doc_type` / `source_doc_id` and the same pair on `journal_entries` have
always carried the link. What they have never carried is the *number* — and a screen cannot
show "GRN-000002" or route to the right page from an integer and a snake-case string.

Until P6 there was one source type a move could have, so the enquiry screen wrote
`source_doc_type === "inventory_document"` inline and built the href by hand. P6 adds three
more (a partner document, a goods receipt, a landed cost), and a partner document is **two**
destinations rather than one: an AR document opens on the customer screen and an AP document
on the supplier screen, which is a fact about the document and not about the screen. Left as
it was, every P6 move would render the same blank cell the P4 review missed six times
(rule 13) — the row is there, the link is not, and nothing fails.

So the server resolves it. `target` is a stable key the screen maps to a route once, in one
place, instead of growing a branch per source type in every screen that lists moves.

**Resolution is bulk.** An item enquiry lists hundreds of moves; asking per row would be a
query per row. `resolve()` groups the references by type and issues one query for each type
actually present — at most four, whatever the page size.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory import GoodsReceivedNote, InventoryDocument
from app.models.order_entry import LandedCostDocument
from app.models.partner import PartnerRole
from app.models.subledger import PartnerDocument

#: The source types this phase can resolve. A type absent from here resolves to `None`, which
#: is the honest answer for `allocation` and `journal_entry` — neither is a document with a
#: page of its own, and inventing a route to nowhere would be worse than the empty cell.
INVENTORY_DOCUMENT = "inventory_document"
PARTNER_DOCUMENT = "partner_document"
GOODS_RECEIVED_NOTE = "goods_received_note"
LANDED_COST_DOCUMENT = "landed_cost_document"


@dataclass(frozen=True)
class SourceDocument:
    """One resolved source: what it is called, and what kind of page opens it."""

    source_doc_type: str
    source_doc_id: int
    number: str
    #: The routing key. `partner_document` splits into `ar_document` / `ap_document` here,
    #: because which subledger a document belongs to is the document's own property and the
    #: screen should not be re-deriving it from a partner lookup.
    target: str


Ref = tuple[str, int]


def resolve(db: Session, company_id: int, refs: Iterable[Ref]) -> dict[Ref, SourceDocument]:
    """`(source_doc_type, source_doc_id)` → the document, for every reference we can resolve.

    Unknown types, and ids that no longer resolve, are simply absent from the result: a caller
    renders what it got and leaves the rest blank. A missing row is not an error — a move may
    outlive nothing in this schema, but a source type this phase does not know about is a
    perfectly ordinary thing for an older move to carry.
    """
    wanted: dict[str, set[int]] = {}
    for source_type, source_id in refs:
        if source_type is None or source_id is None:
            continue
        wanted.setdefault(source_type, set()).add(int(source_id))
    if not wanted:
        return {}

    found: dict[Ref, SourceDocument] = {}

    def _simple(source_type: str, model, target: str) -> None:  # noqa: ANN001
        ids = wanted.get(source_type)
        if not ids:
            return
        for row_id, number in db.execute(
            select(model.id, model.number).where(
                model.company_id == company_id, model.id.in_(ids)
            )
        ).all():
            found[(source_type, int(row_id))] = SourceDocument(
                source_doc_type=source_type,
                source_doc_id=int(row_id),
                number=number,
                target=target,
            )

    _simple(INVENTORY_DOCUMENT, InventoryDocument, INVENTORY_DOCUMENT)
    _simple(GOODS_RECEIVED_NOTE, GoodsReceivedNote, GOODS_RECEIVED_NOTE)
    _simple(LANDED_COST_DOCUMENT, LandedCostDocument, LANDED_COST_DOCUMENT)

    partner_ids = wanted.get(PARTNER_DOCUMENT)
    if partner_ids:
        for row_id, number, role in db.execute(
            select(PartnerDocument.id, PartnerDocument.number, PartnerDocument.role).where(
                PartnerDocument.company_id == company_id, PartnerDocument.id.in_(partner_ids)
            )
        ).all():
            found[(PARTNER_DOCUMENT, int(row_id))] = SourceDocument(
                source_doc_type=PARTNER_DOCUMENT,
                source_doc_id=int(row_id),
                number=number,
                target="ar_document" if role == PartnerRole.AR else "ap_document",
            )
    return found
