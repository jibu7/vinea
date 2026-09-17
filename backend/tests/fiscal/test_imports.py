"""Decision 9's third half: import declarations, and what approving one does *not* do.

Approval **moves no stock and posts nothing**. The goods reached the ledger through a goods
receipt; saying so a second time would double them. What it does is tell RRA which Vinea item
the declared line became — so the assertions below are as much about what is absent (a journal
entry, a stock move, a change of on-hand) as about the payload that goes out.

The sandbox serves one fixture line: declaration `IM 2026 000123`, 240 units of imported wine
at USD 1 200, `imptItemSttsCd 2` — waiting.
"""

from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.fiscal import drainer
from app.fiscal import imports as import_service
from app.fiscal.devices import FiscalSetupError
from app.models.fiscalization import (
    FiscalImportDeclaration,
    FiscalImportStatus,
    FiscalOutboxKind,
    FiscalOutboxRow,
    FiscalOutboxStatus,
    FiscalSyncKind,
)
from app.models.inventory import StockMove
from app.models.journal import JournalEntry
from tests.fiscal.conftest import FiscalPosting
from tests.fiscal.invariants import assert_fiscal_invariants

D = Decimal

#: §4.18 — `imptItemSttsCd`. `3` approves, `4` is published as *Cancelled*.
APPROVED_CODE = "3"
CANCELLED_CODE = "4"

DECLARATION_NO = "IM 2026 000123"


def _fetch(db: Session, fixture: FiscalPosting, client: httpx.Client) -> int:
    return import_service.fetch(
        db, fixture.company_id, fixture.device, actor=fixture.owner, client=client
    )


def _decisions(db: Session, company_id: int) -> list[FiscalOutboxRow]:
    return list(
        db.scalars(
            select(FiscalOutboxRow)
            .where(
                FiscalOutboxRow.company_id == company_id,
                FiscalOutboxRow.kind == FiscalOutboxKind.IMPORT_UPDATE,
            )
            .order_by(FiscalOutboxRow.sequence_no)
        )
    )


def _counts(db: Session, company_id: int) -> tuple[int, int]:
    """(journal entries, stock moves) — the two things an approval must not change."""
    entries = db.scalar(
        select(func.count()).select_from(JournalEntry).where(
            JournalEntry.company_id == company_id
        )
    )
    moves = db.scalar(
        select(func.count()).select_from(StockMove).where(StockMove.company_id == company_id)
    )
    return entries, moves


def test_the_register_lands_as_rows_and_advances_the_watermark(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    assert _fetch(db, fiscal_posting, sandbox_client) == 1

    row = db.scalars(
        select(FiscalImportDeclaration).where(
            FiscalImportDeclaration.company_id == fiscal_posting.company_id
        )
    ).one()
    assert row.dcl_no == DECLARATION_NO
    assert row.item_seq == 1
    assert row.hs_cd == "22042100"
    assert row.item_nm == "Imported wine"
    assert row.orgn_nat_cd == "ZA"
    assert row.qty == D(240)
    assert row.spplr_nm == "Cape Vineyards"
    assert row.invc_fcur_amt == D(1200)
    assert row.invc_fcur_cd == "USD"
    assert row.status == FiscalImportStatus.PENDING
    assert row.item_id is None
    assert fiscal_posting.device.watermarks[FiscalSyncKind.IMPORTS]
    assert _decisions(db, fiscal_posting.company_id) == []


def test_approving_names_the_item_and_moves_nothing(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """The whole content of the acknowledgment is the item code — and the whole point of the
    test is the three things that are unchanged after it."""
    _fetch(db, fiscal_posting, sandbox_client)
    declaration = import_service.list_declarations(db, fiscal_posting.company_id)[0]
    before = _counts(db, fiscal_posting.company_id)

    row = import_service.approve(
        db,
        fiscal_posting.company_id,
        declaration,
        item_id=fiscal_posting.stock_item.id,
        note="matched to GRN-1",
        actor=fiscal_posting.owner,
    )

    assert declaration.status == FiscalImportStatus.APPROVED
    assert declaration.item_id == fiscal_posting.stock_item.id
    assert declaration.decided_by == fiscal_posting.owner.id

    payload = row.payload
    assert payload["imptItemSttsCd"] == APPROVED_CODE
    assert payload["taskCd"] == "2231990000"
    assert payload["itemSeq"] == 1
    assert payload["hsCd"] == "22042100"
    assert payload["itemCd"].startswith("RW2NTXU"), payload["itemCd"]
    assert payload["itemClsCd"] == "5059020800"
    assert payload["remark"] == "matched to GRN-1"

    # **Nothing posted and nothing moved.** The goods arrived on a goods receipt; an approval
    # that booked them again would double the import.
    assert _counts(db, fiscal_posting.company_id) == before
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_rejecting_names_no_item(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """Declining says the goods on this line are not this taxpayer's, and naming a catalogue
    row for them would contradict that."""
    _fetch(db, fiscal_posting, sandbox_client)
    declaration = import_service.list_declarations(db, fiscal_posting.company_id)[0]
    before = _counts(db, fiscal_posting.company_id)

    row = import_service.reject(
        db,
        fiscal_posting.company_id,
        declaration,
        note="not ours",
        actor=fiscal_posting.owner,
    )

    assert declaration.status == FiscalImportStatus.REJECTED
    assert declaration.item_id is None
    assert row.payload["imptItemSttsCd"] == CANCELLED_CODE
    assert "itemCd" not in row.payload
    assert _counts(db, fiscal_posting.company_id) == before
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_line_decided_twice_is_refused(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    _fetch(db, fiscal_posting, sandbox_client)
    declaration = import_service.list_declarations(db, fiscal_posting.company_id)[0]
    import_service.reject(
        db, fiscal_posting.company_id, declaration, actor=fiscal_posting.owner
    )

    with pytest.raises(FiscalSetupError) as refusal:
        import_service.approve(
            db,
            fiscal_posting.company_id,
            declaration,
            item_id=fiscal_posting.stock_item.id,
            actor=fiscal_posting.owner,
        )
    assert refusal.value.code == "fiscal_declaration_already_decided"


def test_a_decided_line_is_not_re_opened_by_a_later_fetch(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    _fetch(db, fiscal_posting, sandbox_client)
    declaration = import_service.list_declarations(db, fiscal_posting.company_id)[0]
    import_service.approve(
        db,
        fiscal_posting.company_id,
        declaration,
        item_id=fiscal_posting.stock_item.id,
        actor=fiscal_posting.owner,
    )

    _fetch(db, fiscal_posting, sandbox_client)

    assert declaration.status == FiscalImportStatus.APPROVED
    assert len(import_service.list_declarations(db, fiscal_posting.company_id)) == 1


def test_an_approval_registers_the_item_ahead_of_itself(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """The acknowledgment carries an `itemCd`, so RRA has to have been told about the item
    first — and creation order is queue order, so nothing sequences the two by hand."""
    _fetch(db, fiscal_posting, sandbox_client)
    declaration = import_service.list_declarations(db, fiscal_posting.company_id)[0]

    row = import_service.approve(
        db,
        fiscal_posting.company_id,
        declaration,
        item_id=fiscal_posting.stock_item.id,
        actor=fiscal_posting.owner,
    )

    registration = db.scalars(
        select(FiscalOutboxRow).where(
            FiscalOutboxRow.company_id == fiscal_posting.company_id,
            FiscalOutboxRow.kind == FiscalOutboxKind.ITEM,
        )
    ).one()
    assert registration.sequence_no < row.sequence_no

    drainer.drain_company(db, fiscal_posting.company_id, client=sandbox_client)
    assert row.status == FiscalOutboxStatus.SENT
    assert_fiscal_invariants(db, fiscal_posting.company_id)
