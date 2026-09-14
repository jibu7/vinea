"""The per-branch half of the accrual proof (P6 decision 5).

The accrual is proved **per branch**, not only in total, and that clause is the whole reason
this file exists: an accrual that nets to zero across the company while being +X in one branch
and −X in another is wrong in both places, and a total-only check calls it correct.

Goods land in a branch. The invoice that bills for them may be keyed anywhere — a head-office
clerk billing a depot's receipt is the ordinary case, not an exotic one — so **every GRN
-accrual line carries the branch of the warehouse the goods moved through**, never the
document's. Purchase price variance is the exception and is deliberate: a price disagreement
belongs to the document that noticed it.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kernel.errors import LedgerStateError
from app.models.company import Branch
from app.models.journal import JournalLine
from app.models.partner import PartnerRole
from app.models.subledger import DocumentKind
from app.order_entry import grn as grn_service
from app.subledger import documents as documents_service
from tests.order_entry.conftest import MARCH, OrderEntry
from tests.order_entry.invariants import assert_order_invariants

ZERO = Decimal(0)


def _second_branch(db: Session, fixture: OrderEntry):  # noqa: ANN202
    """The depot the fixture builds, in a branch of its own away from the default.

    Taken from the fixture rather than created here: the fixture needs it too — the property
    machine draws between the two branches — and two helpers each making a warehouse coded
    `DEP` collide on the uniqueness constraint the moment both run in one session. They did.
    """
    assert fixture.depot is not None and fixture.depot_branch_id is not None
    return db.get(Branch, fixture.depot_branch_id), fixture.depot


def _accrual_by_branch(db: Session, fixture: OrderEntry) -> dict[int, Decimal]:
    totals: dict[int, Decimal] = {}
    for line in db.scalars(
        select(JournalLine).where(
            JournalLine.company_id == fixture.company_id,
            JournalLine.gl_account_id == fixture.settings.grn_accrual_account_id,
        )
    ):
        totals[line.branch_id] = totals.get(line.branch_id, ZERO) + line.base_amount
    return totals


def test_a_receipt_in_one_branch_billed_on_another_clears_in_both(
    db: Session, order_entry: OrderEntry
) -> None:
    """The condition this file is named for.

    Goods are received into the depot (branch B). The supplier invoice is keyed on the
    document's default branch (A) and matches that receipt in full. Afterwards the accrual
    must be zero **in A and in B** — not merely zero when the two are added together.

    Before the branch rule was locked, the receipt credited B and the relief debited A: the
    company total read zero and both branches were wrong by the value of the goods.
    """
    branch_b, depot = _second_branch(db, order_entry)
    branch_a = order_entry.main.branch_id
    assert branch_a != branch_b.id

    grn, _ = grn_service.post_grn(
        db,
        order_entry.company_id,
        grn_service.GrnInput(
            partner_id=order_entry.supplier.id,
            grn_date=MARCH,
            description="Received into the depot",
            warehouse_id=depot.id,
            lines=(
                grn_service.GrnLineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(10),
                    unit_cost=Decimal(1_000),
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    # The receipt sits entirely in the depot's branch.
    assert grn.branch_id == branch_b.id
    assert _accrual_by_branch(db, order_entry) == {branch_b.id: Decimal(-10_000)}
    assert_order_invariants(db, order_entry.company_id)

    # Billed from head office, against the depot's receipt, at the receipt's price.
    documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=order_entry.supplier.id,
            document_date=MARCH,
            description="Head office bills the depot's goods",
            branch_id=branch_a,
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(10),
                    unit_price=Decimal(1_000),
                    grn_line_id=grn.lines[0].id,
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    by_branch = _accrual_by_branch(db, order_entry)
    assert by_branch.get(branch_b.id, ZERO) == ZERO, "the depot's accrual must clear"
    assert by_branch.get(branch_a, ZERO) == ZERO, "and head office must never have held any"
    assert_order_invariants(db, order_entry.company_id)


def test_purchase_price_variance_stays_on_the_document_branch(
    db: Session, order_entry: OrderEntry
) -> None:
    """The deliberate exception. The goods belong to the branch that received them; the
    disagreement about their price belongs to the document that noticed it."""
    branch_b, depot = _second_branch(db, order_entry)
    branch_a = order_entry.main.branch_id

    grn, _ = grn_service.post_grn(
        db,
        order_entry.company_id,
        grn_service.GrnInput(
            partner_id=order_entry.supplier.id,
            grn_date=MARCH,
            description="Depot receipt",
            warehouse_id=depot.id,
            lines=(
                grn_service.GrnLineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(10),
                    unit_cost=Decimal(1_000),
                ),
            ),
        ),
        actor=order_entry.owner,
    )
    invoice, _ = documents_service.post_document(
        db,
        order_entry.company_id,
        PartnerRole.AP,
        documents_service.DocumentInput(
            kind=DocumentKind.INVOICE,
            partner_id=order_entry.supplier.id,
            document_date=MARCH,
            description="Billed dearer than received",
            branch_id=branch_a,
            lines=(
                documents_service.LineInput(
                    item_id=order_entry.stock_item.id,
                    quantity=Decimal(10),
                    unit_price=Decimal(1_100),
                    grn_line_id=grn.lines[0].id,
                ),
            ),
        ),
        actor=order_entry.owner,
    )

    variance = [
        line
        for line in db.scalars(
            select(JournalLine).where(JournalLine.entry_id == invoice.journal_entry_id)
        )
        if line.gl_account_id == order_entry.settings.purchase_price_variance_account_id
    ]
    assert len(variance) == 1
    assert variance[0].base_amount == Decimal(1_000)
    assert variance[0].branch_id == branch_a, "PPV belongs to the document, not the depot"
    assert_order_invariants(db, order_entry.company_id)


def test_a_receipt_refuses_a_branch_that_is_not_its_warehouses(
    db: Session, order_entry: OrderEntry
) -> None:
    """The header branch is derived, and a caller-supplied one is checked rather than
    trusted — a receipt credited to a branch the goods never reached is exactly the drift the
    per-branch proof exists to prevent."""
    _branch_b, depot = _second_branch(db, order_entry)

    with pytest.raises(LedgerStateError) as error:
        grn_service.post_grn(
            db,
            order_entry.company_id,
            grn_service.GrnInput(
                partner_id=order_entry.supplier.id,
                grn_date=MARCH,
                description="Depot goods claimed for head office",
                warehouse_id=depot.id,
                branch_id=order_entry.main.branch_id,
                lines=(
                    grn_service.GrnLineInput(
                        item_id=order_entry.stock_item.id,
                        quantity=Decimal(1),
                        unit_cost=Decimal(100),
                    ),
                ),
            ),
            actor=order_entry.owner,
        )

    assert error.value.code == "branch_warehouse_mismatch"
