"""Three things the companion already does, now stated as tests.

Each was true when written and asserted nowhere, which is the same thing as being true by
accident. None of them is exotic — the source links are what a drill-down follows, the branch
and project are dimensions someone will report on, and the reversal window is the guard that
stops the ledger half of a reversal happening without the stock half.
"""

import ast
import pathlib
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory import StockMove
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind
from app.order_entry import grn as grn_service
from app.subledger import documents as documents_service
from tests.order_entry.conftest import MARCH, OrderEntry

ZERO = Decimal(0)
APP = pathlib.Path(documents_service.__file__).parent.parent


def _sale_with_stock(db: Session, fixture: OrderEntry, *, project_id: int | None = None):  # noqa: ANN202
    grn_service.post_grn(
        db,
        fixture.company_id,
        grn_service.GrnInput(
            partner_id=fixture.supplier.id,
            grn_date=MARCH,
            description="Opening",
            warehouse_id=fixture.main.id,
            lines=(
                grn_service.GrnLineInput(
                    item_id=fixture.stock_item.id,
                    quantity=Decimal(50),
                    unit_cost=Decimal(1_000),
                ),
            ),
        ),
        actor=fixture.owner,
    )
    document, _ = documents_service.post_document(
        db,
        fixture.company_id,
        PartnerRole.AR,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=fixture.customer.id,
            document_date=MARCH,
            description="Sale",
            lines=(
                documents_service.LineInput(
                    item_id=fixture.stock_item.id,
                    quantity=Decimal(5),
                    warehouse_id=fixture.main.id,
                    project_id=project_id,
                ),
            ),
        ),
        actor=fixture.owner,
    )
    return document


def test_companion_moves_carry_the_source_link_back_to_the_document(
    db: Session, order_entry: OrderEntry
) -> None:
    """`source_doc_type` / `source_doc_id` are what the drill-down follows: a move found in
    the stock ledger has to lead to the invoice that caused it, not merely to an entry."""
    document = _sale_with_stock(db, order_entry)

    moves = list(
        db.scalars(
            select(StockMove).where(
                StockMove.company_id == order_entry.company_id,
                StockMove.journal_entry_id == document.stock_entry_id,
            )
        )
    )

    assert moves, "the sale posted no move"
    for move in moves:
        assert move.source_doc_type == "partner_document"
        assert move.source_doc_id == document.id
    # And the line link, which is what a return reads to find its issued cost.
    assert {move.source_line_id for move in moves} == {line.id for line in document.lines}


def test_companion_lines_carry_the_warehouses_branch_and_the_lines_project(
    db: Session, order_entry: OrderEntry
) -> None:
    """Stock moves where the goods are, so the companion's journal lines carry the
    **warehouse's** branch — the stock invariant maps locations to accounts per branch — and
    the project the line was costed to."""
    from app.models.gl import Project
    from app.models.journal import JournalLine

    project = Project(company_id=order_entry.company_id, code="P1", name="Kigali rollout")
    db.add(project)
    db.flush()

    document = _sale_with_stock(db, order_entry, project_id=project.id)

    lines = list(
        db.scalars(
            select(JournalLine).where(JournalLine.entry_id == document.stock_entry_id)
        )
    )
    assert lines, "the companion posted no lines"
    for line in lines:
        assert line.branch_id == order_entry.main.branch_id
    # The inventory leg carries the item and the project it was costed to.
    inventory_lines = [
        line
        for line in lines
        if line.gl_account_id == order_entry.settings.inventory_account_id
    ]
    assert inventory_lines
    for line in inventory_lines:
        assert line.project_id == project.id
        assert line.item_id == order_entry.stock_item.id


def test_the_subledger_never_opens_the_inventory_reversal_window(
    db: Session, order_entry: OrderEntry
) -> None:
    """A static guard, read off the source.

    `module_reversal("inv")` is the inventory module's own window. The subledger reverses a
    companion by **calling the inventory service**, which opens it — and never by opening it
    itself, because the whole point of `reverse_via_module_document` is that the ledger half
    of a reversal cannot happen without the stock half. An `inv` window opened here would let
    it, and would look perfectly reasonable in review.

    Checked by reading the AST rather than by running a posting: the defect this prevents is
    a line of code that nobody would notice, not a behaviour a test would fail on.
    """
    offenders: list[str] = []
    for path in (
        APP / "subledger" / "documents.py",
        APP / "order_entry" / "companion.py",
    ):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(
                node.func, "id", None
            )
            if name != "module_reversal":
                continue
            for argument in node.args:
                if isinstance(argument, ast.Constant) and argument.value == "inv":
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], (
        "these open the inventory reversal window directly instead of calling the inventory "
        f"service: {offenders}"
    )


def test_the_guard_would_catch_an_inv_window(db: Session, order_entry: OrderEntry) -> None:
    """Anti-vacuity for the check above: it must actually recognise the shape it forbids."""
    source = 'posting.module_reversal("inv")'
    tree = ast.parse(source)
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", None) == "module_reversal"
        and any(
            isinstance(argument, ast.Constant) and argument.value == "inv"
            for argument in node.args
        )
    ]
    assert len(found) == 1
