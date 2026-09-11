"""Inventory masters: UoM categories and units, items and barcodes, warehouses, and the
per-company inventory defaults.

Nothing here computes a quantity. What these rows do is make the *next* step's arithmetic
possible and unambiguous: a unit that converts to the item's base at a known factor, a
warehouse that sits in exactly one branch, an inventory account that only the inventory
module may post to. Masters referenced by posted history are deactivated, never deleted —
the same rule the chart of accounts and the partner masters follow.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import ConflictError, NotFoundError
from app.kernel.accounts import get_account
from app.kernel.errors import LedgerStateError
from app.kernel.posting import gl_settings_for
from app.models.company import Branch
from app.models.gl import ControlType, GLAccount, GLSettings
from app.models.inventory import (
    UOM_CONVERSION_SCALE,
    Item,
    ItemBarcode,
    ItemType,
    NegativeStockPolicy,
    Uom,
    UomCategory,
    Warehouse,
)
from app.models.tax import TaxCode
from app.models.user import User
from app.services.audit import record_audit

#: The contra keys on `gl_settings`; `inventory_account` and `inventory_in_transit_account`
#: are handled apart from these because they must be INV control accounts, not contras.
INVENTORY_CONTRA_SETTINGS = (
    "inventory_adjustment_account_id",
    "stock_count_variance_account_id",
    "cogs_account_id",
)
INVENTORY_CONTROL_SETTINGS = ("inventory_account_id", "inventory_in_transit_account_id")


def _audit(
    db: Session,
    company_id: int,
    action: str,
    entity: str,
    entity_id: int,
    *,
    actor: User,
    before: dict | None = None,
    after: dict | None = None,
    request: Request | None = None,
) -> None:
    record_audit(
        db,
        company_id=company_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        before=before,
        after=after,
        actor_user_id=actor.id,
        actor_email=actor.email,
        request=request,
    )


# --- UoM categories and units ----------------------------------------------------------------


def list_uom_categories(
    db: Session, company_id: int, *, include_inactive: bool = False
) -> list[UomCategory]:
    statement = select(UomCategory).where(UomCategory.company_id == company_id)
    if not include_inactive:
        statement = statement.where(UomCategory.is_active)
    return list(db.scalars(statement.order_by(UomCategory.code)))


def get_uom_category(db: Session, company_id: int, category_id: int) -> UomCategory:
    category = db.get(UomCategory, category_id)
    if category is None or category.company_id != company_id:
        raise NotFoundError("Unit of measure category not found")
    return category


def list_uoms(
    db: Session,
    company_id: int,
    *,
    category_id: int | None = None,
    include_inactive: bool = False,
) -> list[Uom]:
    statement = select(Uom).where(Uom.company_id == company_id)
    if category_id is not None:
        statement = statement.where(Uom.category_id == category_id)
    if not include_inactive:
        statement = statement.where(Uom.is_active)
    return list(db.scalars(statement.order_by(Uom.category_id, Uom.is_base.desc(), Uom.code)))


def get_uom(db: Session, company_id: int, uom_id: int) -> Uom:
    uom = db.get(Uom, uom_id)
    if uom is None or uom.company_id != company_id:
        raise NotFoundError("Unit of measure not found")
    return uom


def _assert_uom_code_free(db: Session, company_id: int, code: str) -> None:
    clash = db.scalar(select(Uom.id).where(Uom.company_id == company_id, Uom.code == code))
    if clash is not None:
        raise ConflictError(
            f"{code} is already a unit of measure",
            code="uom_code_taken",
            field_errors={"code": ["already in use"]},
        )


def create_uom_category(
    db: Session,
    company_id: int,
    *,
    code: str,
    name: str,
    base_uom_code: str,
    base_uom_name: str,
    base_uom_decimal_places: int = 0,
    actor: User,
    request: Request | None = None,
) -> tuple[UomCategory, Uom]:
    """A category and its base unit are created together.

    A category without a base unit converts nothing, and the partial unique index can keep a
    second base unit out but cannot require a first one. Making the pair atomic is what makes
    "every unit has a factor to *the* base" true by construction rather than by hope.
    """
    clash = db.scalar(
        select(UomCategory.id).where(
            UomCategory.company_id == company_id, UomCategory.code == code
        )
    )
    if clash is not None:
        raise ConflictError(
            f"{code} is already a unit of measure category",
            code="uom_category_code_taken",
            field_errors={"code": ["already in use"]},
        )
    _assert_uom_code_free(db, company_id, base_uom_code)
    category = UomCategory(company_id=company_id, code=code, name=name, is_active=True)
    db.add(category)
    db.flush()
    base = Uom(
        company_id=company_id,
        category_id=category.id,
        code=base_uom_code,
        name=base_uom_name,
        factor_to_base=Decimal(1),
        decimal_places=base_uom_decimal_places,
        is_base=True,
        is_active=True,
    )
    db.add(base)
    db.flush()
    _audit(
        db,
        company_id,
        "uom_category.created",
        "uom_categories",
        category.id,
        actor=actor,
        after={"code": code, "name": name, "base_uom": base_uom_code},
        request=request,
    )
    return category, base


def update_uom_category(
    db: Session,
    category: UomCategory,
    *,
    name: str | None = None,
    is_active: bool | None = None,
    actor: User,
    request: Request | None = None,
) -> UomCategory:
    before = {"name": category.name, "is_active": category.is_active}
    if name is not None:
        category.name = name
    if is_active is not None:
        if not is_active and _category_is_in_use(db, category):
            raise LedgerStateError(
                "Items still use this unit of measure category",
                code="uom_category_in_use",
            )
        category.is_active = is_active
    db.flush()
    after = {"name": category.name, "is_active": category.is_active}
    if after != before:
        _audit(
            db,
            category.company_id,
            "uom_category.updated",
            "uom_categories",
            category.id,
            actor=actor,
            before=before,
            after=after,
            request=request,
        )
    return category


def _category_is_in_use(db: Session, category: UomCategory) -> bool:
    return (
        db.scalar(
            select(Item.id)
            .where(
                Item.company_id == category.company_id,
                Item.uom_category_id == category.id,
                Item.is_active,
            )
            .limit(1)
        )
        is not None
    )


def create_uom(
    db: Session,
    company_id: int,
    *,
    category_id: int,
    code: str,
    name: str,
    factor_to_base: Decimal,
    decimal_places: int = 0,
    actor: User,
    request: Request | None = None,
) -> Uom:
    """A non-base unit. The base unit of a category is created with the category itself, and
    there is never a second one."""
    category = get_uom_category(db, company_id, category_id)
    _assert_uom_code_free(db, company_id, code)
    if factor_to_base <= 0:
        raise LedgerStateError(
            "A conversion factor must be greater than zero",
            code="invalid_uom_factor",
            field_errors={"factor_to_base": ["must be greater than zero"]},
        )
    uom = Uom(
        company_id=company_id,
        category_id=category.id,
        code=code,
        name=name,
        factor_to_base=factor_to_base,
        decimal_places=decimal_places,
        is_base=False,
        is_active=True,
    )
    db.add(uom)
    db.flush()
    _audit(
        db,
        company_id,
        "uom.created",
        "uoms",
        uom.id,
        actor=actor,
        after={"code": code, "category_id": category.id, "factor_to_base": str(factor_to_base)},
        request=request,
    )
    return uom


def update_uom(
    db: Session,
    uom: Uom,
    *,
    name: str | None = None,
    factor_to_base: Decimal | None = None,
    decimal_places: int | None = None,
    is_active: bool | None = None,
    actor: User,
    request: Request | None = None,
) -> Uom:
    before = {
        "name": uom.name,
        "factor_to_base": str(uom.factor_to_base),
        "decimal_places": uom.decimal_places,
        "is_active": uom.is_active,
    }
    if name is not None:
        uom.name = name
    if factor_to_base is not None:
        if uom.is_base:
            # It is the definition of the category's base, not a convertible quantity.
            raise LedgerStateError(
                "The base unit's factor is always 1",
                code="base_uom_factor_fixed",
                field_errors={"factor_to_base": ["the base unit is always 1"]},
            )
        if factor_to_base <= 0:
            raise LedgerStateError(
                "A conversion factor must be greater than zero",
                code="invalid_uom_factor",
                field_errors={"factor_to_base": ["must be greater than zero"]},
            )
        uom.factor_to_base = factor_to_base
    if decimal_places is not None:
        uom.decimal_places = decimal_places
    if is_active is not None:
        if not is_active and uom.is_base:
            raise LedgerStateError(
                "A category's base unit cannot be deactivated",
                code="base_uom_cannot_be_deactivated",
            )
        uom.is_active = is_active
    db.flush()
    after = {
        "name": uom.name,
        "factor_to_base": str(uom.factor_to_base),
        "decimal_places": uom.decimal_places,
        "is_active": uom.is_active,
    }
    if after != before:
        _audit(
            db,
            uom.company_id,
            "uom.updated",
            "uoms",
            uom.id,
            actor=actor,
            before=before,
            after=after,
            request=request,
        )
    return uom


def to_base_quantity(quantity: Decimal, uom: Uom, item: Item) -> Decimal:
    """Convert an entered quantity into the item's base unit, half-up to 6 dp (decision 8).

    Refuses across categories: litres do not become kilograms. The conversion happens here,
    once, before anything becomes a move — `stock_moves.quantity` is always in the item's
    base unit, so nothing downstream has to remember which unit a row was typed in.
    """
    if uom.category_id != item.uom_category_id:
        raise LedgerStateError(
            f"{uom.code} does not convert to this item's unit of measure",
            code="uom_category_mismatch",
            field_errors={"uom_id": ["wrong unit of measure category"]},
        )
    # The base unit's own factor is pinned to 1 by a check constraint, so the factor on the
    # entered unit is already "base units per entered unit" — no second lookup to disagree with.
    return (quantity * uom.factor_to_base).quantize(
        Decimal(1).scaleb(-UOM_CONVERSION_SCALE), rounding=ROUND_HALF_UP
    )


# --- Items -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemInput:
    code: str
    name: str
    uom_category_id: int
    base_uom_id: int
    item_type: ItemType = ItemType.STOCK
    description: str | None = None
    inventory_account_id: int | None = None
    cogs_account_id: int | None = None
    sales_account_id: int | None = None
    default_sales_tax_code_id: int | None = None
    default_purchase_tax_code_id: int | None = None
    selling_price: Decimal = Decimal(0)
    price_includes_tax: bool = False


def list_items(
    db: Session,
    company_id: int,
    *,
    search: str | None = None,
    item_type: ItemType | None = None,
    include_inactive: bool = False,
) -> list[Item]:
    statement = select(Item).where(Item.company_id == company_id)
    if item_type is not None:
        statement = statement.where(Item.item_type == item_type)
    if search:
        pattern = f"%{search}%"
        # Barcodes are how a warehouse clerk knows an item; the picker must find one by it.
        barcode_match = select(ItemBarcode.item_id).where(
            ItemBarcode.company_id == company_id, ItemBarcode.barcode.ilike(pattern)
        )
        statement = statement.where(
            or_(
                Item.code.ilike(pattern),
                Item.name.ilike(pattern),
                Item.description.ilike(pattern),
                Item.id.in_(barcode_match),
            )
        )
    if not include_inactive:
        statement = statement.where(Item.is_active)
    return list(db.scalars(statement.order_by(Item.code)))


def get_item(db: Session, company_id: int, item_id: int) -> Item:
    item = db.get(Item, item_id)
    if item is None or item.company_id != company_id:
        raise NotFoundError("Item not found")
    return item


def lookup_item(db: Session, company_id: int, term: str) -> tuple[Item, Uom, Decimal]:
    """Resolve an item code or a barcode (decision 8).

    Returns the item together with the unit and pack quantity the *scan* means: scanning the
    outer case yields the case unit, not the item's base unit, and the caller converts. An
    item code resolves to the item's base unit and a pack of one.
    """
    barcode = db.scalar(
        select(ItemBarcode).where(
            ItemBarcode.company_id == company_id,
            ItemBarcode.barcode == term,
            ItemBarcode.is_active,
        )
    )
    if barcode is not None:
        item = get_item(db, company_id, barcode.item_id)
        return item, get_uom(db, company_id, barcode.uom_id), barcode.pack_quantity
    item = db.scalar(select(Item).where(Item.company_id == company_id, Item.code == term))
    if item is None:
        raise NotFoundError("No item matches that code or barcode")
    return item, get_uom(db, company_id, item.base_uom_id), Decimal(1)


def _assert_item_code_free(db: Session, company_id: int, code: str) -> None:
    clash = db.scalar(select(Item.id).where(Item.company_id == company_id, Item.code == code))
    if clash is not None:
        raise ConflictError(
            f"{code} is already an item code",
            code="item_code_taken",
            field_errors={"code": ["already in use"]},
        )


def _assert_inventory_control_account(db: Session, company_id: int, account_id: int | None) -> None:
    """An item's inventory account overrides the company default, and the override has to
    stay an INV control account or stock stops reconciling to the GL (decision 2)."""
    if account_id is None:
        return
    account = get_account(db, company_id, account_id)
    if account.control_type != ControlType.INVENTORY:
        raise LedgerStateError(
            f"Account {account.code} is not an inventory control account",
            code="invalid_inventory_account",
            field_errors={"inventory_account_id": ["not an inventory control account"]},
        )


def _assert_postable_contra(
    db: Session, company_id: int, account_id: int | None, field: str
) -> None:
    if account_id is None:
        return
    account = get_account(db, company_id, account_id)
    if not account.is_postable or not account.is_active:
        raise LedgerStateError(
            f"Account {account.code} is not an active postable account",
            code="account_not_postable",
            field_errors={field: ["not an active postable account"]},
        )
    if account.control_type == ControlType.INVENTORY:
        # Both legs on the inventory account would post an entry that moves nothing.
        raise LedgerStateError(
            f"Account {account.code} is the inventory control account",
            code="contra_is_inventory_account",
            field_errors={field: ["cannot be the inventory control account"]},
        )


def _assert_tax_code(db: Session, company_id: int, tax_code_id: int | None, field: str) -> None:
    if tax_code_id is None:
        return
    tax_code = db.scalar(
        select(TaxCode).where(TaxCode.company_id == company_id, TaxCode.id == tax_code_id)
    )
    if tax_code is None:
        raise NotFoundError("Tax code not found")
    if not tax_code.is_active:
        raise LedgerStateError(
            f"Tax code {tax_code.code} is not active",
            code="tax_code_not_active",
            field_errors={field: ["not active"]},
        )


def _validate_item_defaults(db: Session, company_id: int, data: ItemInput) -> None:
    _assert_inventory_control_account(db, company_id, data.inventory_account_id)
    _assert_postable_contra(db, company_id, data.cogs_account_id, "cogs_account_id")
    _assert_postable_contra(db, company_id, data.sales_account_id, "sales_account_id")
    _assert_tax_code(db, company_id, data.default_sales_tax_code_id, "default_sales_tax_code_id")
    _assert_tax_code(
        db, company_id, data.default_purchase_tax_code_id, "default_purchase_tax_code_id"
    )


def create_item(
    db: Session, company_id: int, data: ItemInput, *, actor: User, request: Request | None = None
) -> Item:
    _assert_item_code_free(db, company_id, data.code)
    category = get_uom_category(db, company_id, data.uom_category_id)
    base_uom = get_uom(db, company_id, data.base_uom_id)
    if base_uom.category_id != category.id:
        raise LedgerStateError(
            f"{base_uom.code} is not a unit of {category.name}",
            code="uom_category_mismatch",
            field_errors={"base_uom_id": ["not in the chosen category"]},
        )
    _validate_item_defaults(db, company_id, data)
    item = Item(
        company_id=company_id,
        code=data.code,
        name=data.name,
        description=data.description,
        item_type=data.item_type,
        uom_category_id=category.id,
        base_uom_id=base_uom.id,
        inventory_account_id=data.inventory_account_id,
        cogs_account_id=data.cogs_account_id,
        sales_account_id=data.sales_account_id,
        default_sales_tax_code_id=data.default_sales_tax_code_id,
        default_purchase_tax_code_id=data.default_purchase_tax_code_id,
        selling_price=data.selling_price,
        price_includes_tax=data.price_includes_tax,
        is_active=True,
    )
    db.add(item)
    db.flush()
    _audit(
        db,
        company_id,
        "item.created",
        "items",
        item.id,
        actor=actor,
        after={"code": item.code, "name": item.name, "item_type": item.item_type.value},
        request=request,
    )
    return item


def _assert_no_moves(db: Session, item: Item, field: str) -> None:
    """Refuse a change that would reinterpret history rather than change the future."""
    from app.inventory.stock import has_moves

    if has_moves(db, item.company_id, item.id):
        raise LedgerStateError(
            f"{item.code} has stock movements; its "
            f"{field.removesuffix('_id').replace('_', ' ')} can no longer be changed",
            code="item_has_moves",
            field_errors={field: ["locked once stock has been posted"]},
        )


def _set_unit_of_measure(
    db: Session, item: Item, category_id: int | None, base_uom_id: int | None
) -> None:
    """The category and its base unit move together: the item's base unit must be a unit of
    the item's category, which is a three-column foreign key, not an opinion."""
    category = get_uom_category(db, item.company_id, category_id or item.uom_category_id)
    base_uom = get_uom(db, item.company_id, base_uom_id or item.base_uom_id)
    if base_uom.category_id != category.id:
        raise LedgerStateError(
            f"{base_uom.code} is not a unit of {category.name}",
            code="uom_category_mismatch",
            field_errors={"base_uom_id": ["not in the chosen category"]},
        )
    if category.id == item.uom_category_id and base_uom.id == item.base_uom_id:
        return
    _assert_no_moves(db, item, "base_uom_id")
    item.uom_category_id = category.id
    item.base_uom_id = base_uom.id


def update_item(
    db: Session,
    item: Item,
    *,
    code: str | None = None,
    name: str | None = None,
    description: str | None = None,
    item_type: ItemType | None = None,
    uom_category_id: int | None = None,
    base_uom_id: int | None = None,
    inventory_account_id: int | None | object = ...,
    cogs_account_id: int | None | object = ...,
    sales_account_id: int | None | object = ...,
    default_sales_tax_code_id: int | None | object = ...,
    default_purchase_tax_code_id: int | None | object = ...,
    selling_price: Decimal | None = None,
    price_includes_tax: bool | None = None,
    is_active: bool | None = None,
    actor: User,
    request: Request | None = None,
) -> Item:
    """Codes are renameable — history hangs off `item_id`, never off the code, so the
    "Rename Item Code" screen is a code change plus an audit row and nothing else.

    **Three fields lock the moment the item has history.** `item_type`, the unit of measure
    and the inventory account are free to change while the item is still, in effect, a draft;
    from the first posted move they are refused with `item_has_moves`. Each would otherwise
    rewrite the past rather than change the future:

    * a stock item that became a service would strand its moves — only stock items have any;
    * a new UoM category would change what every quantity already posted against the item
      *means*, since a move is stored in the base unit and nothing records which unit it was
      typed in;
    * a new inventory account would move the item's stock to another account in the valuation
      report while its posted lines stayed where they were, and `assert_stock_invariants` maps
      locations to accounts through exactly this field.

    This is the rule the tax codes and currencies already follow — free until something is
    posted against it, fixed from then on — and it was scheduled here in the step-1 report
    because it needs `stock_moves` to be able to ask the question.
    """
    before = {
        "code": item.code,
        "name": item.name,
        "item_type": item.item_type.value,
        "uom_category_id": item.uom_category_id,
        "base_uom_id": item.base_uom_id,
        "selling_price": str(item.selling_price),
        "is_active": item.is_active,
    }
    if code is not None and code != item.code:
        _assert_item_code_free(db, item.company_id, code)
        item.code = code
    if name is not None:
        item.name = name
    if description is not None:
        item.description = description
    if item_type is not None and item_type != item.item_type:
        _assert_no_moves(db, item, "item_type")
        item.item_type = item_type
    if uom_category_id is not None or base_uom_id is not None:
        _set_unit_of_measure(db, item, uom_category_id, base_uom_id)
    if inventory_account_id is not ...:
        if inventory_account_id != item.inventory_account_id:
            _assert_no_moves(db, item, "inventory_account_id")
        _assert_inventory_control_account(
            db,
            item.company_id,
            inventory_account_id,  # type: ignore[arg-type]
        )
        item.inventory_account_id = inventory_account_id  # type: ignore[assignment]
    for field, value in (
        ("cogs_account_id", cogs_account_id),
        ("sales_account_id", sales_account_id),
    ):
        if value is ...:
            continue
        _assert_postable_contra(db, item.company_id, value, field)  # type: ignore[arg-type]
        setattr(item, field, value)
    for field, value in (
        ("default_sales_tax_code_id", default_sales_tax_code_id),
        ("default_purchase_tax_code_id", default_purchase_tax_code_id),
    ):
        if value is ...:
            continue
        _assert_tax_code(db, item.company_id, value, field)  # type: ignore[arg-type]
        setattr(item, field, value)
    if selling_price is not None:
        if selling_price < 0:
            raise LedgerStateError(
                "A selling price cannot be negative",
                code="invalid_selling_price",
                field_errors={"selling_price": ["cannot be negative"]},
            )
        item.selling_price = selling_price
    if price_includes_tax is not None:
        item.price_includes_tax = price_includes_tax
    if is_active is not None:
        item.is_active = is_active
    db.flush()
    after = {
        "code": item.code,
        "name": item.name,
        "item_type": item.item_type.value,
        "uom_category_id": item.uom_category_id,
        "base_uom_id": item.base_uom_id,
        "selling_price": str(item.selling_price),
        "is_active": item.is_active,
    }
    if after != before:
        _audit(
            db,
            item.company_id,
            "item.renamed" if after["code"] != before["code"] else "item.updated",
            "items",
            item.id,
            actor=actor,
            before=before,
            after=after,
            request=request,
        )
    return item


# --- Barcodes ----------------------------------------------------------------------------------


def list_barcodes(db: Session, company_id: int, item_id: int) -> list[ItemBarcode]:
    return list(
        db.scalars(
            select(ItemBarcode)
            .where(ItemBarcode.company_id == company_id, ItemBarcode.item_id == item_id)
            .order_by(ItemBarcode.barcode)
        )
    )


def get_barcode(db: Session, company_id: int, barcode_id: int) -> ItemBarcode:
    barcode = db.get(ItemBarcode, barcode_id)
    if barcode is None or barcode.company_id != company_id:
        raise NotFoundError("Barcode not found")
    return barcode


def create_barcode(
    db: Session,
    company_id: int,
    item: Item,
    *,
    barcode: str,
    uom_id: int,
    pack_quantity: Decimal = Decimal(1),
    actor: User,
    request: Request | None = None,
) -> ItemBarcode:
    clash = db.scalar(
        select(ItemBarcode.id).where(
            ItemBarcode.company_id == company_id, ItemBarcode.barcode == barcode
        )
    )
    if clash is not None:
        raise ConflictError(
            f"Barcode {barcode} is already in use",
            code="barcode_taken",
            field_errors={"barcode": ["already in use"]},
        )
    uom = get_uom(db, company_id, uom_id)
    if uom.category_id != item.uom_category_id:
        raise LedgerStateError(
            f"{uom.code} does not convert to this item's unit of measure",
            code="uom_category_mismatch",
            field_errors={"uom_id": ["wrong unit of measure category"]},
        )
    if pack_quantity <= 0:
        raise LedgerStateError(
            "A pack quantity must be greater than zero",
            code="invalid_pack_quantity",
            field_errors={"pack_quantity": ["must be greater than zero"]},
        )
    row = ItemBarcode(
        company_id=company_id,
        item_id=item.id,
        barcode=barcode,
        uom_id=uom.id,
        pack_quantity=pack_quantity,
        is_active=True,
    )
    db.add(row)
    db.flush()
    _audit(
        db,
        company_id,
        "item_barcode.created",
        "item_barcodes",
        row.id,
        actor=actor,
        after={"barcode": barcode, "item_id": item.id, "uom_id": uom.id},
        request=request,
    )
    return row


def update_barcode(
    db: Session,
    row: ItemBarcode,
    *,
    uom_id: int | None = None,
    pack_quantity: Decimal | None = None,
    is_active: bool | None = None,
    actor: User,
    request: Request | None = None,
) -> ItemBarcode:
    before = {
        "uom_id": row.uom_id,
        "pack_quantity": str(row.pack_quantity),
        "is_active": row.is_active,
    }
    if uom_id is not None:
        item = get_item(db, row.company_id, row.item_id)
        uom = get_uom(db, row.company_id, uom_id)
        if uom.category_id != item.uom_category_id:
            raise LedgerStateError(
                f"{uom.code} does not convert to this item's unit of measure",
                code="uom_category_mismatch",
                field_errors={"uom_id": ["wrong unit of measure category"]},
            )
        row.uom_id = uom.id
    if pack_quantity is not None:
        if pack_quantity <= 0:
            raise LedgerStateError(
                "A pack quantity must be greater than zero",
                code="invalid_pack_quantity",
                field_errors={"pack_quantity": ["must be greater than zero"]},
            )
        row.pack_quantity = pack_quantity
    if is_active is not None:
        row.is_active = is_active
    db.flush()
    after = {
        "uom_id": row.uom_id,
        "pack_quantity": str(row.pack_quantity),
        "is_active": row.is_active,
    }
    if after != before:
        _audit(
            db,
            row.company_id,
            "item_barcode.updated",
            "item_barcodes",
            row.id,
            actor=actor,
            before=before,
            after=after,
            request=request,
        )
    return row


# --- Warehouses --------------------------------------------------------------------------------


def list_warehouses(
    db: Session,
    company_id: int,
    *,
    include_inactive: bool = False,
    include_in_transit: bool = False,
) -> list[Warehouse]:
    """In-transit is excluded by default: it is a system location, and every picker that
    reads this endpoint would otherwise offer it as somewhere to send stock (decision 6)."""
    statement = select(Warehouse).where(Warehouse.company_id == company_id)
    if not include_in_transit:
        statement = statement.where(Warehouse.is_in_transit.is_(False))
    if not include_inactive:
        statement = statement.where(Warehouse.is_active)
    return list(db.scalars(statement.order_by(Warehouse.code)))


def get_warehouse(db: Session, company_id: int, warehouse_id: int) -> Warehouse:
    warehouse = db.get(Warehouse, warehouse_id)
    if warehouse is None or warehouse.company_id != company_id:
        raise NotFoundError("Warehouse not found")
    return warehouse


def in_transit_warehouse(db: Session, company_id: int) -> Warehouse:
    warehouse = db.scalar(
        select(Warehouse).where(Warehouse.company_id == company_id, Warehouse.is_in_transit)
    )
    if warehouse is None:
        raise LedgerStateError(
            "This company has no in-transit warehouse",
            code="in_transit_warehouse_missing",
        )
    return warehouse


def _clear_default_warehouse(db: Session, company_id: int, keep_id: int | None) -> None:
    for row in db.scalars(
        select(Warehouse).where(Warehouse.company_id == company_id, Warehouse.is_default)
    ):
        if row.id != keep_id:
            row.is_default = False
    db.flush()


def create_warehouse(
    db: Session,
    company_id: int,
    *,
    code: str,
    name: str,
    branch_id: int,
    is_default: bool = False,
    actor: User,
    request: Request | None = None,
) -> Warehouse:
    """`is_in_transit` is deliberately not a parameter: the single in-transit warehouse is
    created by the seed and by the P5 migration's back-fill, never by a user."""
    clash = db.scalar(
        select(Warehouse.id).where(Warehouse.company_id == company_id, Warehouse.code == code)
    )
    if clash is not None:
        raise ConflictError(
            f"{code} is already a warehouse code",
            code="warehouse_code_taken",
            field_errors={"code": ["already in use"]},
        )
    branch = db.scalar(
        select(Branch).where(Branch.company_id == company_id, Branch.id == branch_id)
    )
    if branch is None:
        raise NotFoundError("Branch not found")
    if is_default:
        _clear_default_warehouse(db, company_id, None)
    warehouse = Warehouse(
        company_id=company_id,
        code=code,
        name=name,
        branch_id=branch.id,
        is_default=is_default,
        is_in_transit=False,
        is_active=True,
    )
    db.add(warehouse)
    db.flush()
    _audit(
        db,
        company_id,
        "warehouse.created",
        "warehouses",
        warehouse.id,
        actor=actor,
        after={"code": code, "name": name, "branch_id": branch.id, "is_default": is_default},
        request=request,
    )
    return warehouse


def update_warehouse(
    db: Session,
    warehouse: Warehouse,
    *,
    name: str | None = None,
    branch_id: int | None = None,
    is_default: bool | None = None,
    is_active: bool | None = None,
    actor: User,
    request: Request | None = None,
) -> Warehouse:
    if warehouse.is_in_transit and (is_default or is_active is False or branch_id is not None):
        raise LedgerStateError(
            "The in-transit warehouse is a system location and cannot be changed",
            code="in_transit_warehouse_locked",
        )
    before = {
        "name": warehouse.name,
        "branch_id": warehouse.branch_id,
        "is_default": warehouse.is_default,
        "is_active": warehouse.is_active,
    }
    if name is not None:
        warehouse.name = name
    if branch_id is not None:
        branch = db.scalar(
            select(Branch).where(Branch.company_id == warehouse.company_id, Branch.id == branch_id)
        )
        if branch is None:
            raise NotFoundError("Branch not found")
        warehouse.branch_id = branch.id
    if is_default is not None:
        if is_default:
            _clear_default_warehouse(db, warehouse.company_id, warehouse.id)
        warehouse.is_default = is_default
    if is_active is not None:
        if not is_active and warehouse.is_default:
            raise LedgerStateError(
                "The default warehouse cannot be deactivated",
                code="default_warehouse_cannot_be_deactivated",
            )
        warehouse.is_active = is_active
    db.flush()
    after = {
        "name": warehouse.name,
        "branch_id": warehouse.branch_id,
        "is_default": warehouse.is_default,
        "is_active": warehouse.is_active,
    }
    if after != before:
        _audit(
            db,
            warehouse.company_id,
            "warehouse.updated",
            "warehouses",
            warehouse.id,
            actor=actor,
            before=before,
            after=after,
            request=request,
        )
    return warehouse


# --- Inventory defaults ------------------------------------------------------------------------


def inventory_defaults(db: Session, company_id: int) -> GLSettings:
    return gl_settings_for(db, company_id)


def update_inventory_defaults(
    db: Session,
    company_id: int,
    changes: dict[str, object],
    *,
    actor: User,
    request: Request | None = None,
) -> GLSettings:
    """Sets the inventory keys on the one settings row (decision 10). Only keys present in
    `changes` are touched.

    All five accounts are required, and clearing one is refused with `required_setting` — the
    same reasoning P4 wrote down for the AR/AP defaults: a NULL here does not fail at this
    call, it fails at whichever posting next needs it, long after the operator who cleared it
    has gone. The two control keys must be INV control accounts and the three contras must
    not be: a contra pointing back at the inventory account posts an entry that moves nothing.
    """
    settings = gl_settings_for(db, company_id)
    account_fields = (*INVENTORY_CONTROL_SETTINGS, *INVENTORY_CONTRA_SETTINGS)
    unknown = set(changes) - {*account_fields, "negative_stock_policy", "default_warehouse_id",
                              "clear_default_warehouse"}
    if unknown:
        raise LedgerStateError(
            f"Not an inventory default: {', '.join(sorted(unknown))}", code="unknown_gl_setting"
        )
    tracked = (*account_fields, "negative_stock_policy", "default_warehouse_id")
    before = {field: _settings_value(settings, field) for field in tracked}

    for field in account_fields:
        if field not in changes:
            continue
        account_id = changes[field]
        if account_id is None:
            raise LedgerStateError(
                f"The {field.removesuffix('_id').replace('_', ' ')} is required — "
                "inventory cannot post without it",
                code="required_setting",
                field_errors={field: ["required"]},
            )
        if field in INVENTORY_CONTROL_SETTINGS:
            _assert_inventory_control_setting(db, company_id, int(account_id), field)
        else:
            _assert_postable_contra(db, company_id, int(account_id), field)
        setattr(settings, field, int(account_id))

    policy = changes.get("negative_stock_policy")
    if policy is not None:
        settings.negative_stock_policy = NegativeStockPolicy(policy)

    if changes.get("clear_default_warehouse"):
        settings.default_warehouse_id = None
    elif changes.get("default_warehouse_id") is not None:
        warehouse = get_warehouse(db, company_id, int(changes["default_warehouse_id"]))  # type: ignore[arg-type]
        if warehouse.is_in_transit:
            raise LedgerStateError(
                "The in-transit warehouse cannot be the default warehouse",
                code="in_transit_warehouse_locked",
                field_errors={"default_warehouse_id": ["not selectable"]},
            )
        settings.default_warehouse_id = warehouse.id

    db.flush()
    after = {field: _settings_value(settings, field) for field in tracked}
    if after != before:
        _audit(
            db,
            company_id,
            "inventory_defaults.updated",
            "gl_settings",
            settings.id,
            actor=actor,
            before=before,
            after=after,
            request=request,
        )
    return settings


def _settings_value(settings: GLSettings, field: str) -> object:
    value = getattr(settings, field)
    return value.value if isinstance(value, NegativeStockPolicy) else value


def _assert_inventory_control_setting(
    db: Session, company_id: int, account_id: int | None, field: str
) -> None:
    """The two control keys may only point at an account that carries the INV control type.

    A tenant whose 1300 migration 0012 declined to mark cannot satisfy this from any screen,
    and that is deliberate: narrowing an account permanently is a decision for a person with
    the company's history in front of them. `docs/ops/inventory-control-account.md` is the
    documented path — journal the old balance to suspense while the account is still
    ordinary, mark it, set this key, then bring the stock back through the opening batch.
    """
    if account_id is None:
        return
    account = db.scalar(
        select(GLAccount).where(GLAccount.company_id == company_id, GLAccount.id == account_id)
    )
    if account is None:
        raise NotFoundError("Account not found")
    if account.control_type != ControlType.INVENTORY:
        raise LedgerStateError(
            f"Account {account.code} is not an inventory control account",
            code="invalid_inventory_account",
            field_errors={field: ["not an inventory control account"]},
        )
