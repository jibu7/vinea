"""Decision 8's other half: what re-registers an item, and which device hears about it.

Step 2 built registration on first fiscal use and on a change of the registered fields. Step 3
adds the two cases that were missing:

* **deactivation** — `useYn` is a field the authority holds, so switching an item off has to
  reach it. An item nobody in Vinea can sell that RRA still lists is the drift the hash exists
  to catch, and the Items screen is where it happens rather than a sale;
* **a second device** — the authority holds items per (taxpayer, branch), so a branch that has
  never been told about an item cannot report a movement of it. The cross-branch transfer is
  what needs this, and `told_about` reads the outbox to decide.
"""

from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory import masters as inventory_masters
from app.models.fiscalization import FiscalItem, FiscalOutboxKind, FiscalOutboxRow
from tests.fiscal.conftest import FiscalPosting, activate_depot_device
from tests.fiscal.helpers import receive
from tests.fiscal.invariants import assert_fiscal_invariants

D = Decimal


def _registrations(db: Session, company_id: int) -> list[FiscalOutboxRow]:
    return list(
        db.scalars(
            select(FiscalOutboxRow)
            .where(
                FiscalOutboxRow.company_id == company_id,
                FiscalOutboxRow.kind == FiscalOutboxKind.ITEM,
            )
            .order_by(FiscalOutboxRow.sequence_no)
        )
    )


def test_deactivating_an_item_re_registers_it_as_unusable(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """Decision 8: "Deactivating an item re-registers it with `useYn N`."

    Queued, not called — it goes into the device's outbox in this transaction like every other
    fiscal fact.
    """
    receive(fiscal_posting, db)
    assert [row.payload["useYn"] for row in _registrations(db, fiscal_posting.company_id)] == [
        "Y"
    ]

    inventory_masters.update_item(
        db, fiscal_posting.stock_item, is_active=False, actor=fiscal_posting.owner
    )

    assert [row.payload["useYn"] for row in _registrations(db, fiscal_posting.company_id)] == [
        "Y",
        "N",
    ]
    stored = db.scalars(
        select(FiscalItem).where(FiscalItem.company_id == fiscal_posting.company_id)
    ).one()
    assert stored.use_yn is False
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_renaming_an_item_re_registers_it_and_an_account_change_does_not(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """The hash is over exactly the fields the authority holds. A rename is one of them; a
    purchase account is not, because RRA was never told the account."""
    receive(fiscal_posting, db)
    before = len(_registrations(db, fiscal_posting.company_id))

    inventory_masters.update_item(
        db,
        fiscal_posting.stock_item,
        purchase_account_id=fiscal_posting.order.accounts["6990"].id,
        actor=fiscal_posting.owner,
    )
    assert len(_registrations(db, fiscal_posting.company_id)) == before

    inventory_masters.update_item(
        db, fiscal_posting.stock_item, name="Rugari Red 750ml (reserve)",
        actor=fiscal_posting.owner,
    )
    registrations = _registrations(db, fiscal_posting.company_id)
    assert len(registrations) == before + 1
    assert registrations[-1].payload["itemNm"] == "Rugari Red 750ml (reserve)"


def test_an_item_the_authority_never_held_is_not_registered_on_the_way_out(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """Minting a code for something leaving the catalogue would be the wrong moment to start:
    the code is permanent and keys every receipt issued against it."""
    inventory_masters.update_item(
        db, fiscal_posting.stock_item, is_active=False, actor=fiscal_posting.owner
    )

    assert _registrations(db, fiscal_posting.company_id) == []
    assert (
        db.scalars(
            select(FiscalItem).where(FiscalItem.company_id == fiscal_posting.company_id)
        ).all()
        == []
    )


def test_a_second_device_is_told_about_the_item_before_it_reports_one(
    db: Session, fiscal_posting: FiscalPosting, sandbox_client: httpx.Client
) -> None:
    """The authority holds items per (taxpayer, branch), and `fiscal_items` holds one
    registration per *company* — because the item code is company-wide and a second code would
    orphan every receipt issued against the first.

    So a device that has never been told gets its own `item` row, ahead of the movement that
    needs it. Without it the depot's stock master would name an item RRA does not hold at that
    branch, and RRA would refuse the movement.
    """
    depot_device = activate_depot_device(db, fiscal_posting, sandbox_client)
    receive(fiscal_posting, db)

    main_rows = [
        row
        for row in _registrations(db, fiscal_posting.company_id)
        if row.device_id == fiscal_posting.device.id
    ]
    assert len(main_rows) == 1

    from app.inventory import transfers as transfer_service
    from tests.fiscal.helpers import MARCH

    transfer_service.post_transfer(
        db,
        fiscal_posting.company_id,
        transfer_service.TransferInput(
            transfer_date=MARCH,
            description="To Musanze",
            from_warehouse_id=fiscal_posting.order.main.id,
            to_warehouse_id=fiscal_posting.order.depot.id,
            lines=(
                transfer_service.TransferLineInput(
                    item_id=fiscal_posting.stock_item.id, quantity=D(20)
                ),
            ),
        ),
        actor=fiscal_posting.owner,
    )

    depot_rows = [
        row
        for row in db.scalars(
            select(FiscalOutboxRow)
            .where(
                FiscalOutboxRow.company_id == fiscal_posting.company_id,
                FiscalOutboxRow.device_id == depot_device.id,
            )
            .order_by(FiscalOutboxRow.sequence_no)
        )
    ]
    assert depot_rows[0].kind == FiscalOutboxKind.ITEM, (
        "the depot's device is told about the item before it is asked to report a movement of it"
    )
    assert depot_rows[1].kind == FiscalOutboxKind.STOCK_IO
    # One `fiscal_items` row all the same: the code is the taxpayer's, not the branch's.
    stored = db.scalars(
        select(FiscalItem).where(FiscalItem.company_id == fiscal_posting.company_id)
    ).one()
    assert stored.item_cd.endswith("0000001")
    assert_fiscal_invariants(db, fiscal_posting.company_id)


def test_a_stock_report_never_changes_what_the_authority_holds(
    db: Session, fiscal_posting: FiscalPosting
) -> None:
    """A **sale** is what changes an item's registration (decision 8's hash is over the
    registered fields, and the price in it is the catalogue price). A stock movement only
    ensures the item is registered — reading the stored row rather than re-deriving it, so a
    purchase-side tax class and a cost-side price can never overwrite a catalogue row.

    The regression this pins: the first version of the hash read Decimals through `str()`, and
    the same price arrived as `2360.0000000000` when computed and `2360.000000` when read back
    off the `NUMERIC(20,6)` column — so a movement re-registered the item on every second
    document. `kernel.money.fingerprint_material` is the fix, shared with ADR-11's own
    fingerprint, and `tests/test_fingerprints.py` is what keeps it there: this test is the
    behaviour at the fiscal end, that one is the rule.
    """
    receive(fiscal_posting, db)
    receive(fiscal_posting, db, quantity="50")
    receive(fiscal_posting, db, quantity="25")

    assert len(_registrations(db, fiscal_posting.company_id)) == 1, (
        "one registration, however many movements name the item"
    )
    assert_fiscal_invariants(db, fiscal_posting.company_id)
