"""Inventory (Master Plan §5 P5): the masters — units of measure, items, barcodes and
warehouses — and, from step 2, the stock ledger they describe.

No master here stores a quantity or a cost. `qty_on_hand` is the column this rebuild exists
to delete (architecture rule 1): quantities and values are derived from `stock_moves` and
cached only in `stock_balances` / `item_cost_state`, which the stock service alone writes
and `verify_stock_balances()` proves. An item is a *description* of a thing, a warehouse is a
*place*; what is in the place is an arithmetic fact about the move ledger, never a field on
the master.

Tenant consistency is declarative, as everywhere else: every intra-tenant reference is a
composite foreign key `(company_id, x_id) → (company_id, id)`, so a row can never point at
another tenant's item, unit or branch even though FK checks bypass RLS.
"""

import enum
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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
#: A per-unit cost is a rate, and rates carry ten decimals everywhere in this codebase
#: (ADR-06) — the entered cost of a receipt is not money until it is multiplied by a
#: quantity and rounded.
COST = Numeric(20, 10)
#: The weighted average, to the six decimals decision 4 fixes it at.
AVERAGE = Numeric(20, 6)

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


# --- The stock ledger (P5 step 2) ---------------------------------------------------------


#: Posting-order counter for `stock_moves.sequence_no`. A plain Postgres sequence, claimed
#: by the stock service inside the posting transaction. Gaps are expected and harmless: this
#: is an *order*, not a document number, and a rolled-back posting must not make the next one
#: wait. Gaplessness belongs to `document_sequences` and to the numbers auditors follow.
STOCK_SEQUENCE = "stock_moves_sequence_no_seq"

#: The weighted average is carried to six decimals (decision 4) — the same scale as a
#: quantity, so `average × quantity` is exact arithmetic before it is rounded to the base
#: currency.
AVERAGE_SCALE = 6


class StockMove(AuditedMixin, CompanyScopedMixin, Base):
    """The inventory ledger. `stock_moves` is to inventory what `journal_lines` is to the GL:
    append-only, signed, and the only source of truth for what is on hand and what it is
    worth (decision 1).

    Every column here is a *fact of a posting*, never a running total. `quantity` is signed
    and always in the item's base unit; `value` is signed, in base currency, rounded to the
    base currency's decimals; `unit_cost` is the per-unit rate that was actually applied —
    the cost entered on a receipt, the average charged to an issue, the frozen rate of a
    transfer's receive leg — and is NULL exactly when the move carries no quantity, which is
    what a revaluation is.

    **Value and the journal agree by construction.** A move with a non-zero value carries the
    journal line that posted it, and `value` equals that line's `base_amount`; a check
    constraint refuses a valued move without its line. That is the whole of "stock valuation
    == inventory GL balance": not a reconciliation job, a foreign key.

    A move with `value = 0` (stock received at no cost, or issued while the average is zero)
    has no line, because the Posting Engine will not write a zero-amount line and the ledger
    has nothing to say about it. The quantity still moved, so the move still exists — this is
    the one place where "one line per move" is a correspondence rather than an identity, and
    `assert_stock_invariants` states it in exactly those terms.
    """

    __tablename__ = "stock_moves"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_stock_moves_company_id_id"),
        UniqueConstraint("company_id", "sequence_no", name="uq_stock_moves_company_sequence"),
        # One move per journal line, both ways: the line cannot be shared and the move
        # cannot invent one.
        UniqueConstraint("journal_line_id", name="uq_stock_moves_journal_line_id"),
        ForeignKeyConstraint(
            ["company_id", "item_id"],
            ["items.company_id", "items.id"],
            name="fk_stock_moves_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "warehouse_id"],
            ["warehouses.company_id", "warehouses.id"],
            name="fk_stock_moves_warehouse",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "period_id"],
            ["accounting_periods.company_id", "accounting_periods.id"],
            name="fk_stock_moves_period",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "project_id"],
            ["projects.company_id", "projects.id"],
            name="fk_stock_moves_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "journal_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_stock_moves_journal_entry",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "journal_line_id"],
            ["journal_lines.company_id", "journal_lines.id"],
            name="fk_stock_moves_journal_line",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "transaction_type_id"],
            ["gl_transaction_types.company_id", "gl_transaction_types.id"],
            name="fk_stock_moves_transaction_type",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "reverses_move_id"],
            ["stock_moves.company_id", "stock_moves.id"],
            name="fk_stock_moves_reverses_move",
            ondelete="RESTRICT",
        ),
        # A move with value has its line; a move without value has neither entry nor line.
        # This is the constraint that makes the valuation/GL identity impossible to break by
        # writing a move the ledger never saw.
        CheckConstraint(
            "(value = 0 AND journal_entry_id IS NULL AND journal_line_id IS NULL) "
            "OR (value <> 0 AND journal_entry_id IS NOT NULL AND journal_line_id IS NOT NULL)",
            name="value_matches_journal_link",
        ),
        # A move moves something: a quantity, a value, or both.
        CheckConstraint("quantity <> 0 OR value <> 0", name="move_is_not_empty"),
        # `unit_cost` is the rate applied to a quantity; a revaluation has no quantity and
        # therefore no rate (decision 1).
        CheckConstraint(
            "(unit_cost IS NULL) = (quantity = 0)", name="unit_cost_accompanies_quantity"
        ),
        Index("ix_stock_moves_company_item", "company_id", "item_id", "warehouse_id"),
        Index("ix_stock_moves_company_date", "company_id", "move_date"),
        Index("ix_stock_moves_company_entry", "company_id", "journal_entry_id"),
        Index(
            "ix_stock_moves_company_source",
            "company_id",
            "source_doc_type",
            "source_doc_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    move_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Posting order — the order the costing engine saw these moves in, which is *not*
    #: `move_date` order. A backdated receipt changes the average from here on and restates
    #: nothing behind it (decision 4).
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(COST)
    value: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    journal_entry_id: Mapped[int | None] = mapped_column(BigInteger)
    journal_line_id: Mapped[int | None] = mapped_column(BigInteger)
    transaction_type_id: Mapped[int | None] = mapped_column(BigInteger)
    source_doc_type: Mapped[str | None] = mapped_column(String(50))
    source_doc_id: Mapped[int | None] = mapped_column(BigInteger)
    source_line_id: Mapped[int | None] = mapped_column(BigInteger)
    project_id: Mapped[int | None] = mapped_column(BigInteger)
    #: Costed at the last positive average because the location had nothing to cost against
    #: (decision 5). Never corrected later; the flag is the review trail.
    cost_provisional: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    reverses_move_id: Mapped[int | None] = mapped_column(BigInteger)


class StockBalance(AuditedMixin, CompanyScopedMixin, Base):
    """Quantity and value per (item, warehouse) — a cache, written only by the stock service
    and proved by `verify_stock_balances()` against the moves it summarises.

    It exists for the same reason `period_balances` does: reading a location's position must
    not cost a full scan of the move ledger. It is never consulted for an as-of question —
    those reconstruct from moves.
    """

    __tablename__ = "stock_balances"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "item_id", "warehouse_id", name="uq_stock_balances_location"
        ),
        ForeignKeyConstraint(
            ["company_id", "item_id"],
            ["items.company_id", "items.id"],
            name="fk_stock_balances_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "warehouse_id"],
            ["warehouses.company_id", "warehouses.id"],
            name="fk_stock_balances_warehouse",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False, default=Decimal(0))
    value: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))


class ItemCostState(AuditedMixin, CompanyScopedMixin, Base):
    """The weighted average per item, and the last average that was taken while the item had
    stock (decision 4).

    `last_positive_average_cost` is what an issue is costed at once the total quantity has
    reached or passed zero — without it, an issue under the `allow` policy would have to be
    costed at nothing, and the value it took out of stock would be a number no one chose. It
    is a cache like the other two: `verify_stock_balances()` replays the moves in posting
    order and recomputes it.

    This row is also the **costing lock**: the service takes it `FOR UPDATE` before valuing
    anything, so two concurrent postings against one item queue up instead of both reading
    the same average.
    """

    __tablename__ = "item_cost_state"
    __table_args__ = (
        UniqueConstraint("company_id", "item_id", name="uq_item_cost_state_item"),
        ForeignKeyConstraint(
            ["company_id", "item_id"],
            ["items.company_id", "items.id"],
            name="fk_item_cost_state_item",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    average_cost: Mapped[Decimal] = mapped_column(AVERAGE, nullable=False, default=Decimal(0))
    last_positive_average_cost: Mapped[Decimal] = mapped_column(
        AVERAGE, nullable=False, default=Decimal(0)
    )
