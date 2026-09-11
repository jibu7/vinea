"""Inventory masters (Master Plan §5 P5 step 1): units of measure, items, barcodes and
warehouses.

Nothing here stores a quantity or a cost. `qty_on_hand` is the column this rebuild exists to
delete (architecture rule 1): from P5 step 2 on, quantities and values are derived from
`stock_moves` and cached only in tables the stock service writes and `verify_stock_balances()`
proves. An item is a *description* of a thing, a warehouse is a *place*; what is in the place
is an arithmetic fact about the move ledger, never a field on the master.

Tenant consistency is declarative, as everywhere else: every intra-tenant reference is a
composite foreign key `(company_id, x_id) → (company_id, id)`, so a row can never point at
another tenant's item, unit or branch even though FK checks bypass RLS.
"""

import enum
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.mixins import AuditedMixin, CompanyScopedMixin, pg_enum

MONEY = Numeric(20, 6)
QUANTITY = Numeric(20, 6)
FACTOR = Numeric(20, 10)

#: `journal_entries.module` / `gl_transaction_types.module` for everything inventory posts.
#: It is the module half of the `('inventory', 'inv')` row P4 seeded into
#: `control_account_modules` — the registry, not a new string, is what makes an INV control
#: account reachable, so this constant must keep agreeing with that row.
INVENTORY_MODULE = "inv"

#: Conversions land on the item's base unit at this scale before they become a move
#: (decision 8). Six decimals is also `stock_moves.quantity`'s scale, so nothing is lost
#: between the entry grid and the ledger.
UOM_CONVERSION_SCALE = 6


class ItemType(enum.StrEnum):
    """Only `STOCK` items ever have moves. Service and non-stock items exist so a P6 document
    line can carry them without inventing a second catalogue."""

    STOCK = "stock"
    SERVICE = "service"
    NON_STOCK = "non_stock"


class NegativeStockPolicy(enum.StrEnum):
    """Per company (decision 5). `BLOCK` is the default: an issue that would take a location
    below zero fails. `ALLOW` posts it at the last positive average and flags the move
    `cost_provisional` — the flag is the review trail, not a promise of later correction."""

    BLOCK = "block"
    ALLOW = "allow"


class InventoryTransactionKind(enum.StrEnum):
    """What a `module='inv'` transaction type *does* (decision 9). Users add their own types
    ("Damaged", "Samples") of an existing kind with their own contra account; the kind is what
    the posting map keys off, so the set is closed and the codes are not."""

    ADJUSTMENT_IN = "adjustment_in"
    ADJUSTMENT_OUT = "adjustment_out"
    REVALUATION = "revaluation"
    TRANSFER = "transfer"
    COUNT_VARIANCE = "count_variance"
    OPENING_BALANCE = "opening_balance"


# Named with an `_enum` suffix so the column attributes below can keep the natural names.
item_type_enum = pg_enum(ItemType, "item_type")
negative_stock_policy_enum = pg_enum(NegativeStockPolicy, "negative_stock_policy")
inventory_txn_kind_enum = pg_enum(InventoryTransactionKind, "inventory_txn_kind")


class UomCategory(AuditedMixin, CompanyScopedMixin, Base):
    """A family of units that convert into each other — Count, Weight, Volume, Length.

    Conversion never crosses a category: a quantity in litres cannot become a quantity in
    kilograms without a density nobody asked us to store.
    """

    __tablename__ = "uom_categories"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_uom_categories_company_id_id"),
        UniqueConstraint("company_id", "code", name="uq_uom_categories_company_code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Uom(AuditedMixin, CompanyScopedMixin, Base):
    """A unit, and how many base units it is worth.

    The category's base unit is the row with `is_base` — a partial unique index makes it
    exactly one per category — and its factor is pinned to 1 by a check constraint, so
    "convert to base" is always a multiplication and never a lookup that can disagree
    with itself.
    """

    __tablename__ = "uoms"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_uoms_company_id_id"),
        UniqueConstraint("company_id", "code", name="uq_uoms_company_code"),
        # The three-column target `items` uses to prove its base unit is in its category.
        UniqueConstraint("company_id", "category_id", "id", name="uq_uoms_company_category_id"),
        ForeignKeyConstraint(
            ["company_id", "category_id"],
            ["uom_categories.company_id", "uom_categories.id"],
            name="fk_uoms_category",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_uoms_company_category_base",
            "company_id",
            "category_id",
            unique=True,
            postgresql_where=text("is_base"),
        ),
        CheckConstraint("factor_to_base > 0", name="factor_positive"),
        CheckConstraint("NOT is_base OR factor_to_base = 1", name="base_factor_is_one"),
        CheckConstraint("decimal_places BETWEEN 0 AND 6", name="decimal_places_range"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    category_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    factor_to_base: Mapped[Decimal] = mapped_column(FACTOR, nullable=False, default=Decimal(1))
    # How many decimals this unit is entered and displayed with — "each" is 0, "kg" is 3.
    decimal_places: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    is_base: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Item(AuditedMixin, CompanyScopedMixin, Base):
    """The catalogue row. Accounts and tax codes on it are *defaults* — the 3rd link of the
    ADR-05 determination chain — and the COGS, sales and tax defaults are data for P6: P5
    reads none of them.
    """

    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_items_company_id_id"),
        UniqueConstraint("company_id", "code", name="uq_items_company_code"),
        Index("ix_items_company_name", "company_id", "name"),
        ForeignKeyConstraint(
            ["company_id", "uom_category_id"],
            ["uom_categories.company_id", "uom_categories.id"],
            name="fk_items_uom_category",
            ondelete="RESTRICT",
        ),
        # Three columns, so the base unit cannot be a unit from another category — the
        # conversion arithmetic would otherwise be free to be nonsense.
        ForeignKeyConstraint(
            ["company_id", "uom_category_id", "base_uom_id"],
            ["uoms.company_id", "uoms.category_id", "uoms.id"],
            name="fk_items_base_uom",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "inventory_account_id"],
            ["gl_accounts.company_id", "gl_accounts.id"],
            name="fk_items_inventory_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "cogs_account_id"],
            ["gl_accounts.company_id", "gl_accounts.id"],
            name="fk_items_cogs_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "sales_account_id"],
            ["gl_accounts.company_id", "gl_accounts.id"],
            name="fk_items_sales_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "default_sales_tax_code_id"],
            ["tax_codes.company_id", "tax_codes.id"],
            name="fk_items_default_sales_tax_code",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "default_purchase_tax_code_id"],
            ["tax_codes.company_id", "tax_codes.id"],
            name="fk_items_default_purchase_tax_code",
            ondelete="RESTRICT",
        ),
        CheckConstraint("selling_price >= 0", name="selling_price_not_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    item_type: Mapped[ItemType] = mapped_column(
        item_type_enum, nullable=False, default=ItemType.STOCK
    )
    uom_category_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    base_uom_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    inventory_account_id: Mapped[int | None] = mapped_column(BigInteger)
    cogs_account_id: Mapped[int | None] = mapped_column(BigInteger)
    sales_account_id: Mapped[int | None] = mapped_column(BigInteger)
    default_sales_tax_code_id: Mapped[int | None] = mapped_column(BigInteger)
    default_purchase_tax_code_id: Mapped[int | None] = mapped_column(BigInteger)
    # One default selling price (decision 8); price lists are explicitly out of scope.
    selling_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))
    price_includes_tax: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    @property
    def is_stock(self) -> bool:
        return self.item_type == ItemType.STOCK


class ItemBarcode(AuditedMixin, CompanyScopedMixin, Base):
    """Many barcodes per item, each resolving to a unit and a pack quantity — scanning the
    outer case is a different quantity from scanning the bottle, which is the whole point of
    the "Variable barcodes" screen."""

    __tablename__ = "item_barcodes"
    __table_args__ = (
        UniqueConstraint("company_id", "barcode", name="uq_item_barcodes_company_barcode"),
        ForeignKeyConstraint(
            ["company_id", "item_id"],
            ["items.company_id", "items.id"],
            name="fk_item_barcodes_item",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "uom_id"],
            ["uoms.company_id", "uoms.id"],
            name="fk_item_barcodes_uom",
            ondelete="RESTRICT",
        ),
        Index("ix_item_barcodes_company_item", "company_id", "item_id"),
        CheckConstraint("pack_quantity > 0", name="pack_quantity_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    uom_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Units of `uom_id` per scan — a case of 12 scanned in cases is 1, in bottles 12.
    pack_quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False, default=Decimal(1))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Warehouse(AuditedMixin, CompanyScopedMixin, Base):
    """A stock location, always inside a branch — the branch is the dimension every move's
    journal line carries, so a warehouse without one could not post.

    `is_in_transit` marks the single system warehouse transfers pass through (decision 6). It
    is created by the seed, mapped to `inventory_in_transit_account`, and is not selectable on
    any other document: stock is either somewhere, or on its way somewhere, and "on its way"
    is a place with a balance rather than a gap between two postings.
    """

    __tablename__ = "warehouses"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_warehouses_company_id_id"),
        UniqueConstraint("company_id", "code", name="uq_warehouses_company_code"),
        ForeignKeyConstraint(
            ["company_id", "branch_id"],
            ["branches.company_id", "branches.id"],
            name="fk_warehouses_branch",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_warehouses_company_default",
            "company_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
        Index(
            "uq_warehouses_company_in_transit",
            "company_id",
            unique=True,
            postgresql_where=text("is_in_transit"),
        ),
        CheckConstraint("NOT (is_default AND is_in_transit)", name="in_transit_is_not_default"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    branch_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_in_transit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
