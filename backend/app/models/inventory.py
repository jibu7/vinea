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
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
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


# --- Stock documents (P5 step 3) -----------------------------------------------------------


class InventoryDocumentStatus(enum.StrEnum):
    """A stock document is posted the moment it exists — there is no draft row here, because
    a draft lives in the browser's IndexedDB until it is posted (the P3 convention). The only
    later transition is a reversal, which leaves the original standing and links to it."""

    POSTED = "posted"
    REVERSED = "reversed"


inventory_document_status_enum = pg_enum(InventoryDocumentStatus, "inventory_document_status")


class InventoryDocument(AuditedMixin, CompanyScopedMixin, Base):
    """The header of one stock document — an adjustment or a journal batch (decision 3).

    **Why this table exists at all**, when `stock_moves` already records every fact of the
    posting: three things the moves cannot carry.

    * A **number**. Decision 12 gives adjustments and batches their own `document_sequences`
      doc types, and an auditor follows those numbers. A move has a `sequence_no`, which is a
      posting *order*, deliberately gappy, and not a number anyone quotes.
    * A **unit**. A batch is one document with many lines, refused whole; the moves it wrote
      are individually indistinguishable from any other moves once they are in the ledger.
    * **Idempotency for a posting that valued nothing.** `Idempotency-Key` lives on
      `journal_entries`, and a posting in which nothing carried value produces no entry (a
      receipt at zero cost is still a real change to what is on the shelf). Such a document is
      replay-protected here, on its own unique key, or not at all.

    **Numbering.** The document takes its journal entry's number, exactly as `partner_documents`
    does in P4 — one number for the document and the entry it produced, so a trial balance and
    a stock enquiry name the same thing the same way. When the posting produced no entry there
    is no number to inherit, so the document claims one from the same gapless sequence. Every
    number in an `INAJ-` run is therefore accounted for by either an entry or a valueless
    document, and the sequence has no holes in it.

    **How it links to its moves.** Through `InventoryDocumentLine.stock_move_id`, set after
    the posting returns. It cannot be the other way round: `stock_moves` refuses UPDATE, so a
    move's `source_doc_id` would have to be known before the move is written, and the document
    cannot be numbered before the posting it is numbered from. P4 has the same ordering and
    resolves it the same way.
    """

    __tablename__ = "inventory_documents"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_inventory_documents_company_id_id"),
        UniqueConstraint("company_id", "number", name="uq_inventory_documents_company_number"),
        ForeignKeyConstraint(
            ["company_id", "journal_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_inventory_documents_journal_entry",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "reversal_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_inventory_documents_reversal_entry",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "reverses_document_id"],
            ["inventory_documents.company_id", "inventory_documents.id"],
            name="fk_inventory_documents_reverses_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "transaction_type_id"],
            ["gl_transaction_types.company_id", "gl_transaction_types.id"],
            name="fk_inventory_documents_transaction_type",
            ondelete="RESTRICT",
        ),
        # The replay key, unique per company while it is set — the P4 shape exactly.
        Index(
            "uq_inventory_documents_company_idempotency_key",
            "company_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_inventory_documents_company_date", "company_id", "document_date"),
        Index("ix_inventory_documents_company_doc_type", "company_id", "doc_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    #: `INAJ` for an adjustment, `INJN` for a journal batch — the `DocType` the posting used.
    doc_type: Mapped[str] = mapped_column(String(20), nullable=False)
    number: Mapped[str] = mapped_column(String(50), nullable=False)
    document_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(100))
    #: The header's type. A batch may leave this null and carry a type per line instead.
    transaction_type_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[InventoryDocumentStatus] = mapped_column(
        inventory_document_status_enum,
        nullable=False,
        default=InventoryDocumentStatus.POSTED,
        server_default=InventoryDocumentStatus.POSTED.value,
    )
    #: Null exactly when nothing in the posting carried value.
    journal_entry_id: Mapped[int | None] = mapped_column(BigInteger)
    reversal_entry_id: Mapped[int | None] = mapped_column(BigInteger)
    reverses_document_id: Mapped[int | None] = mapped_column(BigInteger)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    idempotency_hash: Mapped[str | None] = mapped_column(String(64))


class InventoryDocumentLine(AuditedMixin, CompanyScopedMixin, Base):
    """One line as it was typed, and the move it became.

    The move is the truth; this row is the *intent*. It exists because the two are not the
    same statement: a move is always in the item's base unit, and "24" in the base unit does
    not record that someone entered "2 cases". When a count is disputed, what was keyed is the
    question, and `stock_moves` cannot answer it.

    `stock_move_id` is the link forward. It is nullable only for the window inside the posting
    transaction before the moves come back; every committed line has one.
    """

    __tablename__ = "inventory_document_lines"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_inventory_document_lines_company_id_id"),
        UniqueConstraint(
            "company_id", "document_id", "line_no", name="uq_inventory_document_lines_line_no"
        ),
        # One line per move: a move cannot be claimed by two lines, and a line cannot invent
        # a move that another document wrote.
        UniqueConstraint("stock_move_id", name="uq_inventory_document_lines_stock_move_id"),
        ForeignKeyConstraint(
            ["company_id", "document_id"],
            ["inventory_documents.company_id", "inventory_documents.id"],
            name="fk_inventory_document_lines_document",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "stock_move_id"],
            ["stock_moves.company_id", "stock_moves.id"],
            name="fk_inventory_document_lines_stock_move",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "item_id"],
            ["items.company_id", "items.id"],
            name="fk_inventory_document_lines_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "warehouse_id"],
            ["warehouses.company_id", "warehouses.id"],
            name="fk_inventory_document_lines_warehouse",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "uom_id"],
            ["uoms.company_id", "uoms.id"],
            name="fk_inventory_document_lines_uom",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "transaction_type_id"],
            ["gl_transaction_types.company_id", "gl_transaction_types.id"],
            name="fk_inventory_document_lines_transaction_type",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "contra_account_id"],
            ["gl_accounts.company_id", "gl_accounts.id"],
            name="fk_inventory_document_lines_contra_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "project_id"],
            ["projects.company_id", "projects.id"],
            name="fk_inventory_document_lines_project",
            ondelete="RESTRICT",
        ),
        Index("ix_inventory_document_lines_document", "company_id", "document_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    item_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: What was keyed, in `uom_id`. A magnitude: the direction is the transaction type's kind.
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    uom_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: The same quantity in the item's base unit — what became `stock_moves.quantity`.
    quantity_base: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    #: Only on an increase; an issue is costed at the average, never at a typed rate.
    unit_cost: Mapped[Decimal | None] = mapped_column(COST)
    #: Only on a revaluation, which states its own amount and moves no quantity.
    value: Mapped[Decimal | None] = mapped_column(MONEY)
    transaction_type_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Overrides the transaction type's own contra for this line only.
    contra_account_id: Mapped[int | None] = mapped_column(BigInteger)
    project_id: Mapped[int | None] = mapped_column(BigInteger)
    description: Mapped[str | None] = mapped_column(Text)
    stock_move_id: Mapped[int | None] = mapped_column(BigInteger)


# --- Transfers (P5 step 4) -----------------------------------------------------------------


class StockTransferStatus(enum.StrEnum):
    """Where a transfer's stock physically is.

    `IN_TRANSIT` is the state decision 6 exists to make visible: the stock has left the
    source, has not arrived anywhere, and is sitting in the in-transit warehouse against the
    in-transit account. It is a real position on the valuation report, not a gap between two
    postings.
    """

    IN_TRANSIT = "in_transit"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


stock_transfer_status_enum = pg_enum(StockTransferStatus, "stock_transfer_status")


class StockTransfer(AuditedMixin, CompanyScopedMixin, Base):
    """The header of a warehouse transfer — one document, **two** postings (decision 6).

    This is why a transfer is not an `inventory_documents` row. That table carries one
    `journal_entry_id`, because an adjustment and a journal batch are one posting each; a
    transfer is a dispatch *and* a receive, each its own entry, each carrying the branch of
    its own physical warehouse so the branch balances stay square when the two warehouses sit
    in different branches. A shape that holds two entries is a different shape.

    **Numbering** follows the step-3 rule: the document takes the number of the entry it
    posted — here, the dispatch leg's. The receive leg claims the next number from the same
    `INTR` sequence, so a completed transfer accounts for two numbers in the `TRF-` run and
    leaves no hole; a dispatch that valued nothing claims its own number, as a valueless
    adjustment does.

    **Status is a fact about stock, not a workflow.** There is no draft: a transfer exists
    because it was dispatched. `CANCELLED` is the reversal of a dispatch that never arrived,
    which is the only way stock in transit can come home — without it a mis-keyed dispatch
    would leave value in the in-transit account with nothing able to move it.
    """

    __tablename__ = "stock_transfers"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_stock_transfers_company_id_id"),
        UniqueConstraint("company_id", "number", name="uq_stock_transfers_company_number"),
        ForeignKeyConstraint(
            ["company_id", "from_warehouse_id"],
            ["warehouses.company_id", "warehouses.id"],
            name="fk_stock_transfers_from_warehouse",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "to_warehouse_id"],
            ["warehouses.company_id", "warehouses.id"],
            name="fk_stock_transfers_to_warehouse",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "transaction_type_id"],
            ["gl_transaction_types.company_id", "gl_transaction_types.id"],
            name="fk_stock_transfers_transaction_type",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "dispatch_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_stock_transfers_dispatch_entry",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "receive_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_stock_transfers_receive_entry",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "cancellation_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_stock_transfers_cancellation_entry",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "project_id"],
            ["projects.company_id", "projects.id"],
            name="fk_stock_transfers_project",
            ondelete="RESTRICT",
        ),
        # The replay key of the *dispatch*. Receiving carries its own key on its own posting,
        # which the kernel protects, because by then the transfer exists to be found.
        Index(
            "uq_stock_transfers_company_idempotency_key",
            "company_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_stock_transfers_company_date", "company_id", "transfer_date"),
        Index("ix_stock_transfers_company_status", "company_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(50), nullable=False)
    transfer_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(100))
    from_warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    to_warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    transaction_type_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    project_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[StockTransferStatus] = mapped_column(
        stock_transfer_status_enum,
        nullable=False,
        default=StockTransferStatus.IN_TRANSIT,
        server_default=StockTransferStatus.IN_TRANSIT.value,
    )
    #: Null exactly when the leg valued nothing — a transfer of stock carried at zero.
    dispatch_entry_id: Mapped[int | None] = mapped_column(BigInteger)
    receive_entry_id: Mapped[int | None] = mapped_column(BigInteger)
    cancellation_entry_id: Mapped[int | None] = mapped_column(BigInteger)
    #: The date each later leg was posted on; the dispatch's is `transfer_date`.
    received_date: Mapped[date | None] = mapped_column(Date)
    cancelled_date: Mapped[date | None] = mapped_column(Date)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    idempotency_hash: Mapped[str | None] = mapped_column(String(64))


class StockTransferLine(AuditedMixin, CompanyScopedMixin, Base):
    """One item on the transfer, and the four moves it becomes.

    Four, because each leg is a pair: dispatch takes the quantity out of the source and puts
    it into transit; receive takes it out of transit and puts it into the destination. The
    columns are named for what they are rather than collapsed into a link table, because each
    of the four answers a different question — "what left Main", "what is in transit", "what
    left transit", "what arrived at Depot" — and a report that asks one of them should not
    have to filter a generic list to find out.

    A cancelled transfer's reversing moves are not recorded here: they are mirrors, found
    through `stock_moves.reverses_move_id` from the dispatch pair, and duplicating the link
    would create a second place for it to be wrong.
    """

    __tablename__ = "stock_transfer_lines"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_stock_transfer_lines_company_id_id"),
        UniqueConstraint(
            "company_id", "transfer_id", "line_no", name="uq_stock_transfer_lines_line_no"
        ),
        # A move belongs to one line of one transfer, in one role.
        UniqueConstraint("dispatch_out_move_id", name="uq_stock_transfer_lines_dispatch_out"),
        UniqueConstraint("dispatch_in_move_id", name="uq_stock_transfer_lines_dispatch_in"),
        UniqueConstraint("receive_out_move_id", name="uq_stock_transfer_lines_receive_out"),
        UniqueConstraint("receive_in_move_id", name="uq_stock_transfer_lines_receive_in"),
        ForeignKeyConstraint(
            ["company_id", "transfer_id"],
            ["stock_transfers.company_id", "stock_transfers.id"],
            name="fk_stock_transfer_lines_transfer",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "item_id"],
            ["items.company_id", "items.id"],
            name="fk_stock_transfer_lines_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "uom_id"],
            ["uoms.company_id", "uoms.id"],
            name="fk_stock_transfer_lines_uom",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "dispatch_out_move_id"],
            ["stock_moves.company_id", "stock_moves.id"],
            name="fk_stock_transfer_lines_dispatch_out_move",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "dispatch_in_move_id"],
            ["stock_moves.company_id", "stock_moves.id"],
            name="fk_stock_transfer_lines_dispatch_in_move",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "receive_out_move_id"],
            ["stock_moves.company_id", "stock_moves.id"],
            name="fk_stock_transfer_lines_receive_out_move",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "receive_in_move_id"],
            ["stock_moves.company_id", "stock_moves.id"],
            name="fk_stock_transfer_lines_receive_in_move",
            ondelete="RESTRICT",
        ),
        Index("ix_stock_transfer_lines_transfer", "company_id", "transfer_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    transfer_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    item_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: What was keyed, in `uom_id`; always a magnitude — a transfer has a direction already.
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    uom_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity_base: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    dispatch_out_move_id: Mapped[int | None] = mapped_column(BigInteger)
    dispatch_in_move_id: Mapped[int | None] = mapped_column(BigInteger)
    receive_out_move_id: Mapped[int | None] = mapped_column(BigInteger)
    receive_in_move_id: Mapped[int | None] = mapped_column(BigInteger)


# --- Stock counts (P5 step 4) --------------------------------------------------------------


class StockCountStatus(enum.StrEnum):
    """A count session's life: counting → processed, or counting → abandoned.

    `COMPLETED` is terminal and stays terminal, including when the variance document it
    posted is later reversed (decision 11) — the count happened, and a reversal is a second
    event rather than an unwinding of the first.
    """

    COUNTING = "counting"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


stock_count_status_enum = pg_enum(StockCountStatus, "stock_count_status")


class StockCountSession(AuditedMixin, CompanyScopedMixin, Base):
    """A stock take of one warehouse, frozen at a moment (decision 7).

    **What a snapshot is.** `snapshot_at` is the wall clock, for people. `snapshot_sequence`
    is the posting-order watermark, for correctness: every move carries a `sequence_no` from
    one company-wide counter, so "a move landed after this snapshot" is exactly
    `sequence_no > snapshot_sequence` — a comparison no clock skew, no long transaction and
    no backdated document can confuse. The session freezes a `system_quantity` per line at
    that watermark and never reads a live balance again; that is what makes a variance a
    statement about a moment rather than about whenever Process happened to run.

    **It is a working paper, not a posting.** Nothing in the ledger changes until Process,
    which posts one count-variance document (an `inventory_documents` row of doc type `INCT`)
    holding every non-zero variance. The session then points at that document and is
    Completed. A session with nothing but zero variances completes with no document at all,
    because a count that agrees with the books is a true and complete answer that the ledger
    has nothing to say about.

    **Its number.** The session claims one from its own `INCS` run (`CNS-`) when it is opened,
    before anything can be posted — a count sheet is handed to somebody and referred to for
    days, so it has to be nameable while it is still empty. The variance document it
    eventually posts takes its number from the entry it posts, out of the separate `INCT` run
    (`CNT-`). Two runs rather than one because a `CNT-` number stands for something that
    reached the ledger: a cancelled session, or one whose count agreed with the books, posts
    nothing, and letting it consume a posting number would leave a hole where an auditor
    would look for a document.
    """

    __tablename__ = "stock_count_sessions"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_stock_count_sessions_company_id_id"),
        UniqueConstraint("company_id", "number", name="uq_stock_count_sessions_company_number"),
        ForeignKeyConstraint(
            ["company_id", "warehouse_id"],
            ["warehouses.company_id", "warehouses.id"],
            name="fk_stock_count_sessions_warehouse",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "transaction_type_id"],
            ["gl_transaction_types.company_id", "gl_transaction_types.id"],
            name="fk_stock_count_sessions_transaction_type",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "document_id"],
            ["inventory_documents.company_id", "inventory_documents.id"],
            name="fk_stock_count_sessions_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "project_id"],
            ["projects.company_id", "projects.id"],
            name="fk_stock_count_sessions_project",
            ondelete="RESTRICT",
        ),
        Index("ix_stock_count_sessions_company_status", "company_id", "status"),
        Index("ix_stock_count_sessions_company_warehouse", "company_id", "warehouse_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(50), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: The date the variance document posts on — the day the stock was counted, which is not
    #: necessarily the day Process is run.
    count_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(100))
    transaction_type_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    project_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[StockCountStatus] = mapped_column(
        stock_count_status_enum,
        nullable=False,
        default=StockCountStatus.COUNTING,
        server_default=StockCountStatus.COUNTING.value,
    )
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: `stock_moves.sequence_no` as at the snapshot. A line is stale when its location has a
    #: move above its own watermark; see `StockCountLine.snapshot_sequence`.
    snapshot_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: The variance document Process posted. Null on a session that is still counting, was
    #: cancelled, or found no variance at all.
    document_id: Mapped[int | None] = mapped_column(BigInteger)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StockCountLine(AuditedMixin, CompanyScopedMixin, Base):
    """One item on the count sheet: what the books said, and what the counter found.

    The **variance is not a column**. It is `counted_quantity_base - system_quantity`, and
    storing it would be a second place for the same fact to live and disagree from
    (architecture rule 1, the same reason no master carries a quantity). A null
    `counted_quantity_base` is an uncounted line, which is not the same as a line counted at
    zero — the first contributes nothing to the posting, the second writes off everything the
    location held.

    Each line carries **its own** watermark, not just the session's, because re-snapshotting
    is per line: when one item moves mid-count, that line is re-frozen and recounted while
    the rest of the sheet, which nothing disturbed, stays exactly as it was counted.
    """

    __tablename__ = "stock_count_lines"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_stock_count_lines_company_id_id"),
        UniqueConstraint(
            "company_id", "session_id", "line_no", name="uq_stock_count_lines_line_no"
        ),
        # One line per item per session: two lines for one item would make the variance
        # ambiguous and let a double count post twice.
        UniqueConstraint(
            "company_id", "session_id", "item_id", name="uq_stock_count_lines_item"
        ),
        # And one line per move, as on a document line: a line cannot claim a move that
        # another line already accounts for.
        UniqueConstraint("stock_move_id", name="uq_stock_count_lines_stock_move_id"),
        CheckConstraint(
            "(counted_quantity IS NULL) = (counted_quantity_base IS NULL)",
            name="counted_quantity_is_converted",
        ),
        ForeignKeyConstraint(
            ["company_id", "session_id"],
            ["stock_count_sessions.company_id", "stock_count_sessions.id"],
            name="fk_stock_count_lines_session",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "item_id"],
            ["items.company_id", "items.id"],
            name="fk_stock_count_lines_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "uom_id"],
            ["uoms.company_id", "uoms.id"],
            name="fk_stock_count_lines_uom",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "stock_move_id"],
            ["stock_moves.company_id", "stock_moves.id"],
            name="fk_stock_count_lines_stock_move",
            ondelete="RESTRICT",
        ),
        Index("ix_stock_count_lines_session", "company_id", "session_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    item_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: What the books said this location held at `snapshot_at`, in the item's base unit.
    system_quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: What was keyed, in `uom_id`, and the same figure in the item's base unit. Null until
    #: somebody counts the line; cleared again when the line is re-snapshotted.
    counted_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY)
    uom_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    counted_quantity_base: Mapped[Decimal | None] = mapped_column(QUANTITY)
    counted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    #: The move this line's variance became, set by Process. Null on a line with no variance.
    stock_move_id: Mapped[int | None] = mapped_column(BigInteger)
