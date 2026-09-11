"""P5 step 1 — the inventory masters service.

The theme of every test here is that a master cannot be left in a shape the stock ledger
would have to guess about: a unit with no anchor to convert against, an item whose base unit
belongs to another category, a barcode that means two different things, a second in-transit
warehouse to split the in-transit balance across.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.inventory import masters
from app.kernel.errors import LedgerStateError
from app.models.audit import AuditLog
from app.models.gl import ControlType, GLAccount
from app.models.inventory import ItemType, NegativeStockPolicy, Uom
from tests.inventory.conftest import Inventory, Stock, receive
from tests.subledger.conftest import MARCH


def _stock_item(db: Session, inventory: Inventory, code: str = "WINE-001") -> object:
    return masters.create_item(
        db,
        inventory.company_id,
        masters.ItemInput(
            code=code,
            name="Rugari Red 750ml",
            uom_category_id=inventory.count.id,
            base_uom_id=inventory.each.id,
            selling_price=Decimal("8500"),
        ),
        actor=inventory.owner,
    )


# --- Units of measure ---------------------------------------------------------------------


def test_the_seed_gives_every_category_exactly_one_base_unit(
    db: Session, inventory: Inventory
) -> None:
    assert set(inventory.categories) == {"COUNT", "WEIGHT", "VOLUME", "LENGTH"}
    for code, base in inventory.base_uoms.items():
        assert base.is_base is True
        assert base.factor_to_base == Decimal(1), code
    assert inventory.base_uoms["WEIGHT"].decimal_places == 3


def test_a_category_is_created_with_its_base_unit(db: Session, inventory: Inventory) -> None:
    category, base = masters.create_uom_category(
        db,
        inventory.company_id,
        code="AREA",
        name="Area",
        base_uom_code="M2",
        base_uom_name="Square metre",
        base_uom_decimal_places=2,
        actor=inventory.owner,
    )

    assert base.category_id == category.id
    assert base.is_base is True
    assert base.factor_to_base == Decimal(1)


def test_a_second_base_unit_in_a_category_is_refused_by_the_database(
    db: Session, inventory: Inventory
) -> None:
    """The partial unique index, not the service, is the authority — a category with two
    bases would give the conversion arithmetic two anchors that can disagree."""
    db.add(
        Uom(
            company_id=inventory.company_id,
            category_id=inventory.count.id,
            code="EA2",
            name="Each again",
            factor_to_base=Decimal(1),
            decimal_places=0,
            is_base=True,
            is_active=True,
        )
    )
    with pytest.raises(Exception) as excinfo:
        db.flush()
    assert "uq_uoms_company_category_base" in str(excinfo.value)
    db.rollback()


def test_a_unit_code_is_unique_across_the_company(db: Session, inventory: Inventory) -> None:
    with pytest.raises(ConflictError) as excinfo:
        masters.create_uom(
            db,
            inventory.company_id,
            category_id=inventory.categories["WEIGHT"].id,
            code="EA",
            name="Clash",
            factor_to_base=Decimal(2),
            actor=inventory.owner,
        )
    assert excinfo.value.code == "uom_code_taken"


def test_conversion_lands_on_the_items_base_unit_at_six_decimals(
    db: Session, inventory: Inventory
) -> None:
    case = masters.create_uom(
        db,
        inventory.company_id,
        category_id=inventory.count.id,
        code="CASE12",
        name="Case of 12",
        factor_to_base=Decimal(12),
        actor=inventory.owner,
    )
    item = _stock_item(db, inventory)

    assert masters.to_base_quantity(Decimal("2.5"), case, item) == Decimal("30.000000")
    assert masters.to_base_quantity(Decimal(3), inventory.each, item) == Decimal("3.000000")


def test_conversion_across_categories_is_refused(db: Session, inventory: Inventory) -> None:
    """Litres do not become kilograms without a density nobody asked us to store."""
    item = _stock_item(db, inventory)

    with pytest.raises(LedgerStateError) as excinfo:
        masters.to_base_quantity(Decimal(1), inventory.base_uoms["WEIGHT"], item)

    assert excinfo.value.code == "uom_category_mismatch"


def test_a_base_unit_cannot_be_deactivated(db: Session, inventory: Inventory) -> None:
    with pytest.raises(LedgerStateError) as excinfo:
        masters.update_uom(db, inventory.each, is_active=False, actor=inventory.owner)
    assert excinfo.value.code == "base_uom_cannot_be_deactivated"


def test_a_base_units_factor_stays_one(db: Session, inventory: Inventory) -> None:
    with pytest.raises(LedgerStateError) as excinfo:
        masters.update_uom(
            db, inventory.each, factor_to_base=Decimal(12), actor=inventory.owner
        )
    assert excinfo.value.code == "base_uom_factor_fixed"


# --- Items --------------------------------------------------------------------------------


def test_an_items_base_unit_must_be_in_its_category(db: Session, inventory: Inventory) -> None:
    with pytest.raises(LedgerStateError) as excinfo:
        masters.create_item(
            db,
            inventory.company_id,
            masters.ItemInput(
                code="MIX-001",
                name="Mismatched",
                uom_category_id=inventory.count.id,
                base_uom_id=inventory.base_uoms["VOLUME"].id,
            ),
            actor=inventory.owner,
        )
    assert excinfo.value.code == "uom_category_mismatch"


def test_an_item_inventory_account_override_must_be_an_inv_control_account(
    db: Session, inventory: Inventory
) -> None:
    """Decision 2: stock reconciles to the GL only if every item's inventory account is one
    of the accounts nothing but the inventory module may post to."""
    with pytest.raises(LedgerStateError) as excinfo:
        masters.create_item(
            db,
            inventory.company_id,
            masters.ItemInput(
                code="BAD-001",
                name="Wrong account",
                uom_category_id=inventory.count.id,
                base_uom_id=inventory.each.id,
                inventory_account_id=inventory.ledger.acct("6990"),
            ),
            actor=inventory.owner,
        )
    assert excinfo.value.code == "invalid_inventory_account"


def test_an_item_accepts_the_in_transit_account_as_its_inventory_account(
    db: Session, inventory: Inventory
) -> None:
    account = db.scalars(
        select(GLAccount).where(
            GLAccount.company_id == inventory.company_id, GLAccount.code == "1350"
        )
    ).one()
    assert account.control_type == ControlType.INVENTORY

    item = masters.create_item(
        db,
        inventory.company_id,
        masters.ItemInput(
            code="TRN-001",
            name="In transit override",
            uom_category_id=inventory.count.id,
            base_uom_id=inventory.each.id,
            inventory_account_id=account.id,
        ),
        actor=inventory.owner,
    )
    assert item.inventory_account_id == account.id


def test_an_items_contra_account_may_not_be_the_inventory_account(
    db: Session, inventory: Inventory
) -> None:
    with pytest.raises(LedgerStateError) as excinfo:
        masters.create_item(
            db,
            inventory.company_id,
            masters.ItemInput(
                code="COGS-001",
                name="Both legs on stock",
                uom_category_id=inventory.count.id,
                base_uom_id=inventory.each.id,
                cogs_account_id=inventory.ledger.acct("1300"),
            ),
            actor=inventory.owner,
        )
    assert excinfo.value.code == "contra_is_inventory_account"


def test_item_codes_are_unique_per_company(db: Session, inventory: Inventory) -> None:
    _stock_item(db, inventory)
    with pytest.raises(ConflictError) as excinfo:
        _stock_item(db, inventory)
    assert excinfo.value.code == "item_code_taken"


def test_renaming_an_item_code_keeps_the_history_on_the_item(
    db: Session, inventory: Inventory
) -> None:
    """The trail hangs off `item_id`, never off the code — the P3/P4 rename pattern."""
    item = _stock_item(db, inventory)
    masters.update_item(db, item, code="WINE-100", actor=inventory.owner)
    db.flush()

    rows = list(
        db.scalars(
            select(AuditLog)
            .where(
                AuditLog.company_id == inventory.company_id,
                AuditLog.entity == "items",
                AuditLog.entity_id == str(item.id),
            )
            .order_by(AuditLog.id)
        )
    )
    actions = [row.action for row in rows]
    assert "item.created" in actions
    assert actions[-1] == "item.renamed"
    assert rows[-1].before["code"] == "WINE-001"
    assert rows[-1].after["code"] == "WINE-100"
    assert rows[-1].actor_email == inventory.owner.email


def test_a_plain_edit_is_audited_as_an_update_not_a_rename(
    db: Session, inventory: Inventory
) -> None:
    item = _stock_item(db, inventory)
    masters.update_item(db, item, selling_price=Decimal("9000"), actor=inventory.owner)
    db.flush()

    last = db.scalars(
        select(AuditLog)
        .where(AuditLog.entity == "items", AuditLog.entity_id == str(item.id))
        .order_by(AuditLog.id.desc())
    ).first()
    assert last.action == "item.updated"


def test_a_service_item_is_allowed_and_marked_as_such(db: Session, inventory: Inventory) -> None:
    item = masters.create_item(
        db,
        inventory.company_id,
        masters.ItemInput(
            code="SRV-001",
            name="Delivery",
            item_type=ItemType.SERVICE,
            uom_category_id=inventory.count.id,
            base_uom_id=inventory.each.id,
        ),
        actor=inventory.owner,
    )
    assert item.is_stock is False


# --- Barcodes -----------------------------------------------------------------------------


def test_a_barcode_resolves_to_its_unit_and_pack_quantity(
    db: Session, inventory: Inventory
) -> None:
    item = _stock_item(db, inventory)
    case = masters.create_uom(
        db,
        inventory.company_id,
        category_id=inventory.count.id,
        code="CASE6",
        name="Case of 6",
        factor_to_base=Decimal(6),
        actor=inventory.owner,
    )
    masters.create_barcode(
        db,
        inventory.company_id,
        item,
        barcode="5901234123457",
        uom_id=case.id,
        pack_quantity=Decimal(1),
        actor=inventory.owner,
    )

    found, uom, pack = masters.lookup_item(db, inventory.company_id, "5901234123457")

    assert found.id == item.id
    assert uom.id == case.id
    assert masters.to_base_quantity(pack, uom, item) == Decimal("6.000000")


def test_an_item_code_lookup_resolves_to_the_base_unit(db: Session, inventory: Inventory) -> None:
    item = _stock_item(db, inventory)

    found, uom, pack = masters.lookup_item(db, inventory.company_id, "WINE-001")

    assert found.id == item.id
    assert uom.id == inventory.each.id
    assert pack == Decimal(1)


def test_an_unknown_term_is_not_found(db: Session, inventory: Inventory) -> None:
    with pytest.raises(NotFoundError):
        masters.lookup_item(db, inventory.company_id, "nothing-like-this")


def test_a_barcode_is_unique_across_the_company(db: Session, inventory: Inventory) -> None:
    first = _stock_item(db, inventory, code="WINE-001")
    second = _stock_item(db, inventory, code="WINE-002")
    masters.create_barcode(
        db,
        inventory.company_id,
        first,
        barcode="5901234123457",
        uom_id=inventory.each.id,
        actor=inventory.owner,
    )

    with pytest.raises(ConflictError) as excinfo:
        masters.create_barcode(
            db,
            inventory.company_id,
            second,
            barcode="5901234123457",
            uom_id=inventory.each.id,
            actor=inventory.owner,
        )
    assert excinfo.value.code == "barcode_taken"


def test_a_barcode_unit_must_convert_to_the_items_base(db: Session, inventory: Inventory) -> None:
    item = _stock_item(db, inventory)
    with pytest.raises(LedgerStateError) as excinfo:
        masters.create_barcode(
            db,
            inventory.company_id,
            item,
            barcode="5901234123457",
            uom_id=inventory.base_uoms["WEIGHT"].id,
            actor=inventory.owner,
        )
    assert excinfo.value.code == "uom_category_mismatch"


# --- Warehouses ---------------------------------------------------------------------------


def test_the_seed_gives_one_default_and_one_in_transit_warehouse(
    db: Session, inventory: Inventory
) -> None:
    assert inventory.main.is_default is True
    assert inventory.main.branch_id == inventory.ledger.main_branch.id
    assert inventory.transit.is_in_transit is True
    assert inventory.transit.is_default is False


def test_listing_warehouses_hides_the_in_transit_one_by_default(
    db: Session, inventory: Inventory
) -> None:
    """It is a system location: every picker reads this endpoint, and none of them may offer
    in-transit as somewhere to send stock (decision 6)."""
    visible = masters.list_warehouses(db, inventory.company_id)
    assert [row.code for row in visible] == ["MAIN"]

    everything = masters.list_warehouses(db, inventory.company_id, include_in_transit=True)
    assert {row.code for row in everything} == {"MAIN", "TRANSIT"}


def test_making_a_warehouse_default_clears_the_previous_default(
    db: Session, inventory: Inventory
) -> None:
    depot = masters.create_warehouse(
        db,
        inventory.company_id,
        code="DEPOT",
        name="Musanze Depot",
        branch_id=inventory.ledger.main_branch.id,
        actor=inventory.owner,
    )
    masters.update_warehouse(db, depot, is_default=True, actor=inventory.owner)
    db.flush()

    assert depot.is_default is True
    assert inventory.main.is_default is False


def test_the_in_transit_warehouse_cannot_be_changed(db: Session, inventory: Inventory) -> None:
    with pytest.raises(LedgerStateError) as excinfo:
        masters.update_warehouse(db, inventory.transit, is_active=False, actor=inventory.owner)
    assert excinfo.value.code == "in_transit_warehouse_locked"


def test_a_warehouse_code_is_unique_per_company(db: Session, inventory: Inventory) -> None:
    with pytest.raises(ConflictError) as excinfo:
        masters.create_warehouse(
            db,
            inventory.company_id,
            code="MAIN",
            name="Another main",
            branch_id=inventory.ledger.main_branch.id,
            actor=inventory.owner,
        )
    assert excinfo.value.code == "warehouse_code_taken"


def test_the_default_warehouse_cannot_be_deactivated(db: Session, inventory: Inventory) -> None:
    with pytest.raises(LedgerStateError) as excinfo:
        masters.update_warehouse(db, inventory.main, is_active=False, actor=inventory.owner)
    assert excinfo.value.code == "default_warehouse_cannot_be_deactivated"


# --- Defaults -----------------------------------------------------------------------------


def test_the_seed_fills_every_inventory_default(db: Session, inventory: Inventory) -> None:
    settings = inventory.settings
    codes = {
        row.id: row.code
        for row in db.scalars(
            select(GLAccount).where(GLAccount.company_id == inventory.company_id)
        )
    }
    assert codes[settings.inventory_account_id] == "1300"
    assert codes[settings.inventory_in_transit_account_id] == "1350"
    assert codes[settings.inventory_adjustment_account_id] == "5200"
    assert codes[settings.stock_count_variance_account_id] == "5200"
    assert codes[settings.cogs_account_id] == "5100"
    assert settings.negative_stock_policy == NegativeStockPolicy.BLOCK
    assert settings.default_warehouse_id == inventory.main.id


def test_the_inventory_account_setting_must_be_an_inv_control_account(
    db: Session, inventory: Inventory
) -> None:
    with pytest.raises(LedgerStateError) as excinfo:
        masters.update_inventory_defaults(
            db,
            inventory.company_id,
            {"inventory_account_id": inventory.ledger.acct("6990")},
            actor=inventory.owner,
        )
    assert excinfo.value.code == "invalid_inventory_account"


def test_clearing_an_inventory_account_setting_is_refused(
    db: Session, inventory: Inventory
) -> None:
    """A NULL here does not fail now, it fails at whichever posting first needs it — the
    reasoning P4 wrote down for the AR/AP defaults, applied unchanged."""
    with pytest.raises(LedgerStateError) as excinfo:
        masters.update_inventory_defaults(
            db,
            inventory.company_id,
            {"inventory_adjustment_account_id": None},
            actor=inventory.owner,
        )
    assert excinfo.value.code == "required_setting"


def test_the_negative_stock_policy_switches_and_is_audited(
    db: Session, inventory: Inventory
) -> None:
    masters.update_inventory_defaults(
        db,
        inventory.company_id,
        {"negative_stock_policy": "allow"},
        actor=inventory.owner,
    )
    db.flush()

    assert inventory.settings.negative_stock_policy == NegativeStockPolicy.ALLOW
    last = db.scalars(
        select(AuditLog)
        .where(
            AuditLog.company_id == inventory.company_id,
            AuditLog.action == "inventory_defaults.updated",
        )
        .order_by(AuditLog.id.desc())
    ).first()
    assert last.before["negative_stock_policy"] == "block"
    assert last.after["negative_stock_policy"] == "allow"


def test_the_in_transit_warehouse_cannot_be_the_default_warehouse(
    db: Session, inventory: Inventory
) -> None:
    with pytest.raises(LedgerStateError) as excinfo:
        masters.update_inventory_defaults(
            db,
            inventory.company_id,
            {"default_warehouse_id": inventory.transit.id},
            actor=inventory.owner,
        )
    assert excinfo.value.code == "in_transit_warehouse_locked"


# --- Step 2: the three fields that lock once the item has moved -------------------------------
#
# Step 1 left `item_type`, the unit of measure and the inventory account out of `update_item`
# altogether, because the check that makes them safe needs `stock_moves`. It exists now, so
# they are back — free while the item is still a draft, refused from the first posted move.


def test_an_items_type_and_unit_change_freely_before_anything_has_moved(
    db: Session, inventory: Inventory
) -> None:
    category = masters.create_uom_category(
        db,
        inventory.company_id,
        code="MASS",
        name="Mass",
        base_uom_code="G",
        base_uom_name="Gram",
        base_uom_decimal_places=3,
        actor=inventory.owner,
    )
    item = _stock_item(db, inventory, code="DRAFT-1")
    db.flush()

    masters.update_item(
        db,
        item,
        item_type=ItemType.NON_STOCK,
        uom_category_id=category[0].id,
        base_uom_id=category[1].id,
        actor=inventory.owner,
    )

    assert item.item_type == ItemType.NON_STOCK
    assert (item.uom_category_id, item.base_uom_id) == (category[0].id, category[1].id)


def test_the_type_locks_once_stock_has_been_posted(db: Session, stock: Stock) -> None:
    """A stock item that became a service would strand every move it has."""
    receive(db, stock, quantity=Decimal(3), unit_cost=Decimal(500), on=MARCH)

    with pytest.raises(LedgerStateError) as excinfo:
        masters.update_item(db, stock.item, item_type=ItemType.SERVICE, actor=stock.owner)

    assert excinfo.value.code == "item_has_moves"
    assert excinfo.value.field_errors == {"item_type": ["locked once stock has been posted"]}
    db.rollback()


def test_the_unit_of_measure_locks_once_stock_has_been_posted(
    db: Session, stock: Stock
) -> None:
    """A move is stored in the item's base unit and nothing records which unit it was typed
    in, so a new category would change what every posted quantity *means*."""
    category, base = masters.create_uom_category(
        db,
        stock.company_id,
        code="MASS",
        name="Mass",
        base_uom_code="G",
        base_uom_name="Gram",
        base_uom_decimal_places=3,
        actor=stock.owner,
    )
    receive(db, stock, quantity=Decimal(3), unit_cost=Decimal(500), on=MARCH)

    with pytest.raises(LedgerStateError) as excinfo:
        masters.update_item(
            db,
            stock.item,
            uom_category_id=category.id,
            base_uom_id=base.id,
            actor=stock.owner,
        )

    assert excinfo.value.code == "item_has_moves"
    db.rollback()


def test_the_inventory_account_locks_once_stock_has_been_posted(
    db: Session, stock: Stock
) -> None:
    """The valuation report maps a location to an account through this field. Changing it
    after the fact would move the item's stock in the report while its posted lines stayed
    where they were — which is precisely the reconciliation `assert_stock_invariants` refuses
    to let drift."""
    receive(db, stock, quantity=Decimal(3), unit_cost=Decimal(500), on=MARCH)
    in_transit = stock.inventory.settings.inventory_in_transit_account_id

    with pytest.raises(LedgerStateError) as excinfo:
        masters.update_item(db, stock.item, inventory_account_id=in_transit, actor=stock.owner)

    assert excinfo.value.code == "item_has_moves"
    db.rollback()


def test_a_field_that_is_set_to_what_it_already_says_is_not_a_change(
    db: Session, stock: Stock
) -> None:
    """The lock is on *changing* the field. A round-tripped form that posts every field back
    unchanged — which is how the maintenance screens work — must not be refused."""
    receive(db, stock, quantity=Decimal(3), unit_cost=Decimal(500), on=MARCH)

    masters.update_item(
        db,
        stock.item,
        name="Rugari Red 750ml (case)",
        item_type=stock.item.item_type,
        uom_category_id=stock.item.uom_category_id,
        base_uom_id=stock.item.base_uom_id,
        inventory_account_id=stock.item.inventory_account_id,
        actor=stock.owner,
    )

    assert stock.item.name == "Rugari Red 750ml (case)"
