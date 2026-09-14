"""Sales and purchase orders (P6 decision 3).

**An order is a commitment, not a posting.** Nothing in this module reaches the ledger or the
stock ledger: creating, editing, closing or cancelling an order writes no journal entry and no
stock move. What an order *does* is hold a quantity — committed on the sales side, on order on
the purchase side — and every one of those quantities is a query over these lines joined to the
documents that fulfilled them, never a running column somebody maintains.

That is the whole of the difference from the v4 design this rebuild exists to delete. There is
no `quantity_received` here, no `quantity_invoiced`, no `quantity_backordered`. A reversal moves
every one of those figures by construction, because there is nothing to correct: the invoice
line stops being posted, and the sum that counted it stops counting it.

The two **stored** columns that look like exceptions are not. `status` is a workflow column in
the `open_amount` pattern P4 set — a convenience for filtering, written only by the order
service, and checked against the state the lines actually imply by `verify_order_statuses()`.
`kit_breakup_edited` is a fact about what a person did, not a total: it records that Breakup
edited this line's explosion, which is precisely the thing no arithmetic over the definition
can recover once the definition itself moves on.

Tenant consistency is declarative as everywhere else: every intra-tenant reference is a
composite foreign key `(company_id, x_id) → (company_id, id)`, so a line can never point at
another tenant's item, warehouse or order.
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
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.mixins import AuditedMixin, CompanyScopedMixin, pg_enum
from app.models.partner import TaxMode, tax_mode_type

MONEY = Numeric(20, 6)
QUANTITY = Numeric(20, 6)
RATE = Numeric(20, 10)
PERCENT = Numeric(20, 10)


class SalesOrderStatus(enum.StrEnum):
    """Where a sales order stands against the invoices that fulfil it (decision 4).

    `OPEN` and `PARTIALLY_INVOICED` are the two states that still commit stock; `INVOICED`,
    `CLOSED` and `CANCELLED` commit nothing. The first three are **derived** from the lines and
    merely cached here; the last two are decisions a person made and no arithmetic can produce
    them, which is why closing and cancelling are the only two things that can write them.

    `CLOSED` cancels the remaining quantities and keeps the history — the order was partly
    delivered and the rest will not be. `CANCELLED` is only reachable while nothing has been
    fulfilled at all, because an order that has delivered something is a thing that happened.
    """

    OPEN = "open"
    PARTIALLY_INVOICED = "partially_invoiced"
    INVOICED = "invoiced"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class PurchaseOrderStatus(enum.StrEnum):
    """The purchase-side mirror of `SalesOrderStatus`, against receipts rather than invoices.

    A **stock** line is received by a GRN (or by a supplier invoice that carries the goods
    itself, with no GRN behind it); a **service or non-stock** line is received by its invoice,
    because there is nothing to put on a shelf. Decision 4 states both, and
    `purchase_order_line_quantities` is where the two are added up.
    """

    OPEN = "open"
    PARTIALLY_RECEIVED = "partially_received"
    RECEIVED = "received"
    CLOSED = "closed"
    CANCELLED = "cancelled"


sales_order_status_enum = pg_enum(SalesOrderStatus, "sales_order_status")
purchase_order_status_enum = pg_enum(PurchaseOrderStatus, "purchase_order_status")

#: The statuses that still hold a quantity. Committed and on-order are summed over lines of
#: orders in these states and nothing else — a closed or cancelled order releases what it held
#: by dropping out of this set, which is why neither needs a per-line flag to go with it.
OPEN_SALES_STATUSES = (SalesOrderStatus.OPEN, SalesOrderStatus.PARTIALLY_INVOICED)
OPEN_PURCHASE_STATUSES = (PurchaseOrderStatus.OPEN, PurchaseOrderStatus.PARTIALLY_RECEIVED)


class SalesOrder(AuditedMixin, CompanyScopedMixin, Base):
    """A customer's commitment to buy, and ours to supply.

    `exchange_rate` here is **display only** and the column says so twice — once in this
    docstring and once at its definition — because it is the single most inviting mistake in
    the table. Stock is never valued at an order's rate: the invoice raised from this order
    takes its own dated rate (P4), and the companion stock entry is a base-currency entry
    valued at the average. An order that booked a rate into the ledger would be a posting, and
    an order is not a posting.
    """

    __tablename__ = "sales_orders"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_sales_orders_company_id_id"),
        UniqueConstraint("company_id", "number", name="uq_sales_orders_company_number"),
        ForeignKeyConstraint(
            ["company_id", "partner_id"],
            ["partners.company_id", "partners.id"],
            name="fk_sales_orders_partner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "currency_id"],
            ["currencies.company_id", "currencies.id"],
            name="fk_sales_orders_currency",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "branch_id"],
            ["branches.company_id", "branches.id"],
            name="fk_sales_orders_branch",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "project_id"],
            ["projects.company_id", "projects.id"],
            name="fk_sales_orders_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "warehouse_id"],
            ["warehouses.company_id", "warehouses.id"],
            name="fk_sales_orders_warehouse",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "payment_terms_id"],
            ["payment_terms.company_id", "payment_terms.id"],
            name="fk_sales_orders_payment_terms",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "sales_rep_id"],
            ["sales_reps.company_id", "sales_reps.id"],
            name="fk_sales_orders_sales_rep",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_sales_orders_company_idempotency_key",
            "company_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_sales_orders_company_partner", "company_id", "partner_id"),
        Index("ix_sales_orders_company_status", "company_id", "status"),
        Index("ix_sales_orders_company_date", "company_id", "order_date"),
        CheckConstraint("exchange_rate > 0", name="positive_exchange_rate"),
        CheckConstraint("total_amount >= 0", name="total_not_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), nullable=False)
    partner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: When the customer expects it. Informational: nothing schedules off it in v1.
    expected_date: Mapped[date | None] = mapped_column(Date)
    reference: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    currency_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: **Display only.** Stock is never valued at an order's rate; the invoice raised from this
    #: order takes its own dated rate. See the class docstring.
    exchange_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    branch_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    project_id: Mapped[int | None] = mapped_column(BigInteger)
    #: The default for lines that name none. Commitment is per (item, warehouse), so a line
    #: without a warehouse would be a commitment against nowhere.
    warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payment_terms_id: Mapped[int | None] = mapped_column(BigInteger)
    sales_rep_id: Mapped[int | None] = mapped_column(BigInteger)
    tax_mode: Mapped[TaxMode] = mapped_column(tax_mode_type, nullable=False)
    status: Mapped[SalesOrderStatus] = mapped_column(
        sales_order_status_enum, nullable=False, default=SalesOrderStatus.OPEN
    )
    #: Sums of the rounded lines, so the header and the grid can never disagree by a rounding.
    net_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))
    total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))
    #: The day the remaining quantities were given up, or the order abandoned. Both are facts
    #: about a decision, which is why they are dates and not a recomputation.
    closed_on: Mapped[date | None] = mapped_column(Date)
    cancelled_on: Mapped[date | None] = mapped_column(Date)
    idempotency_key: Mapped[str | None] = mapped_column(String(120))
    idempotency_hash: Mapped[str | None] = mapped_column(String(64))

    lines: Mapped[list["SalesOrderLine"]] = relationship(
        back_populates="order",
        order_by="SalesOrderLine.line_no",
        cascade="all, delete-orphan",
    )


class SalesOrderLine(AuditedMixin, CompanyScopedMixin, Base):
    """One item committed, in the unit it was keyed in and in the item's base unit.

    There is no `quantity_invoiced` here and there never will be. Invoiced quantity is the sum
    of the posted, unreversed AR invoice lines carrying this line's id — the
    `sales_order_line_quantities` view — so a reversal moves it by construction (decision 4).

    A **kit** line is stored as this parent line (the kit item, its quantity, its price and its
    tax — the revenue) plus the component lines that carry `kit_parent_line_id`. Commitment and
    cost come from the components, revenue from the parent, and the explosion happens at line
    entry so the order records what was actually promised rather than a recipe that may since
    have changed.
    """

    __tablename__ = "sales_order_lines"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_sales_order_lines_company_id_id"),
        UniqueConstraint("order_id", "line_no", name="uq_sales_order_lines_line_no"),
        ForeignKeyConstraint(
            ["company_id", "order_id"],
            ["sales_orders.company_id", "sales_orders.id"],
            name="fk_sales_order_lines_order",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "item_id"],
            ["items.company_id", "items.id"],
            name="fk_sales_order_lines_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "uom_id"],
            ["uoms.company_id", "uoms.id"],
            name="fk_sales_order_lines_uom",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "warehouse_id"],
            ["warehouses.company_id", "warehouses.id"],
            name="fk_sales_order_lines_warehouse",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "tax_code_id"],
            ["tax_codes.company_id", "tax_codes.id"],
            name="fk_sales_order_lines_tax_code",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "project_id"],
            ["projects.company_id", "projects.id"],
            name="fk_sales_order_lines_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "kit_parent_line_id"],
            ["sales_order_lines.company_id", "sales_order_lines.id"],
            name="fk_sales_order_lines_kit_parent_line",
            ondelete="CASCADE",
        ),
        Index("ix_sales_order_lines_order", "company_id", "order_id"),
        Index("ix_sales_order_lines_item", "company_id", "item_id", "warehouse_id"),
        CheckConstraint("base_quantity > 0", name="base_quantity_positive"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        # A component carries neither price nor tax: the kit's revenue is all on the parent,
        # and a component that priced itself would sell the same goods twice.
        CheckConstraint(
            "kit_parent_line_id IS NULL OR (unit_price = 0 AND tax_code_id IS NULL)",
            name="kit_component_carries_no_money",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    item_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    uom_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: As keyed, in `uom_id`.
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    #: The same amount in the item's base unit — what every derived figure counts.
    base_quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))
    discount_percent: Mapped[Decimal] = mapped_column(PERCENT, nullable=False, default=Decimal(0))
    tax_code_id: Mapped[int | None] = mapped_column(BigInteger)
    net_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))
    gross_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))
    warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    project_id: Mapped[int | None] = mapped_column(BigInteger)
    #: Set on a component line, pointing at the kit line it was exploded from.
    kit_parent_line_id: Mapped[int | None] = mapped_column(BigInteger)
    #: Set on a **parent** kit line whose explosion Breakup has edited. Not a total — a fact
    #: about what a person did. Without it there is no way to tell an untouched explosion from
    #: one that happens to still match a definition, or from one whose definition has since
    #: changed underneath it, and the property that components sum to kit quantity x per-kit
    #: has no exception clause to key on.
    kit_breakup_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    order: Mapped[SalesOrder] = relationship(back_populates="lines")


class PurchaseOrder(AuditedMixin, CompanyScopedMixin, Base):
    """Our commitment to buy. The sales order's mirror, with two differences that are the
    phase's design rather than an omission.

    `warehouse_id` is the **delivery** warehouse, not merely a line default: a GRN raised
    against this order receives into it, and the accrual is proved per branch, so where the
    goods are going is a property of the order and not a choice made at receipt time
    (the branch rule, locked at step 2).

    There is no sales rep and no payment terms: decision 3 gives both to the sales side only.
    """

    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_purchase_orders_company_id_id"),
        UniqueConstraint("company_id", "number", name="uq_purchase_orders_company_number"),
        ForeignKeyConstraint(
            ["company_id", "partner_id"],
            ["partners.company_id", "partners.id"],
            name="fk_purchase_orders_partner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "currency_id"],
            ["currencies.company_id", "currencies.id"],
            name="fk_purchase_orders_currency",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "branch_id"],
            ["branches.company_id", "branches.id"],
            name="fk_purchase_orders_branch",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "project_id"],
            ["projects.company_id", "projects.id"],
            name="fk_purchase_orders_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "warehouse_id"],
            ["warehouses.company_id", "warehouses.id"],
            name="fk_purchase_orders_warehouse",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_purchase_orders_company_idempotency_key",
            "company_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_purchase_orders_company_partner", "company_id", "partner_id"),
        Index("ix_purchase_orders_company_status", "company_id", "status"),
        Index("ix_purchase_orders_company_date", "company_id", "order_date"),
        CheckConstraint("exchange_rate > 0", name="positive_exchange_rate"),
        CheckConstraint("total_amount >= 0", name="total_not_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), nullable=False)
    partner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_date: Mapped[date | None] = mapped_column(Date)
    reference: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    currency_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: **Display only**, exactly as on the sales side: a receipt values its goods at the rate
    #: on its own `grn_date`, never at the rate the order was taken at.
    exchange_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    branch_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    project_id: Mapped[int | None] = mapped_column(BigInteger)
    #: The delivery warehouse. A GRN against this order receives into it.
    warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_mode: Mapped[TaxMode] = mapped_column(tax_mode_type, nullable=False)
    status: Mapped[PurchaseOrderStatus] = mapped_column(
        purchase_order_status_enum, nullable=False, default=PurchaseOrderStatus.OPEN
    )
    net_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))
    total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))
    closed_on: Mapped[date | None] = mapped_column(Date)
    cancelled_on: Mapped[date | None] = mapped_column(Date)
    idempotency_key: Mapped[str | None] = mapped_column(String(120))
    idempotency_hash: Mapped[str | None] = mapped_column(String(64))

    lines: Mapped[list["PurchaseOrderLine"]] = relationship(
        back_populates="order",
        order_by="PurchaseOrderLine.line_no",
        cascade="all, delete-orphan",
    )


class PurchaseOrderLine(AuditedMixin, CompanyScopedMixin, Base):
    """One item on order, and the price we expect to pay for it.

    No `kit_parent_line_id`, deliberately and unlike the sales side: `kit_not_purchasable`
    refuses a kit on every AP path, so a purchase component line is a row that cannot be
    created. A column that is provably always NULL is a column a later reader has to work out
    is dead, and decision 8 is clear that a kit exists to be sold and never bought.
    """

    __tablename__ = "purchase_order_lines"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_purchase_order_lines_company_id_id"),
        UniqueConstraint("order_id", "line_no", name="uq_purchase_order_lines_line_no"),
        ForeignKeyConstraint(
            ["company_id", "order_id"],
            ["purchase_orders.company_id", "purchase_orders.id"],
            name="fk_purchase_order_lines_order",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "item_id"],
            ["items.company_id", "items.id"],
            name="fk_purchase_order_lines_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "uom_id"],
            ["uoms.company_id", "uoms.id"],
            name="fk_purchase_order_lines_uom",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "warehouse_id"],
            ["warehouses.company_id", "warehouses.id"],
            name="fk_purchase_order_lines_warehouse",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "tax_code_id"],
            ["tax_codes.company_id", "tax_codes.id"],
            name="fk_purchase_order_lines_tax_code",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "project_id"],
            ["projects.company_id", "projects.id"],
            name="fk_purchase_order_lines_project",
            ondelete="RESTRICT",
        ),
        Index("ix_purchase_order_lines_order", "company_id", "order_id"),
        Index("ix_purchase_order_lines_item", "company_id", "item_id", "warehouse_id"),
        CheckConstraint("base_quantity > 0", name="base_quantity_positive"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    item_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    uom_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    base_quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))
    discount_percent: Mapped[Decimal] = mapped_column(PERCENT, nullable=False, default=Decimal(0))
    tax_code_id: Mapped[int | None] = mapped_column(BigInteger)
    net_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))
    gross_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal(0))
    #: Where these goods will land. Defaulted from the order's delivery warehouse, and the
    #: branch of *this* warehouse is the branch the accrual will be proved in.
    warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    project_id: Mapped[int | None] = mapped_column(BigInteger)

    order: Mapped[PurchaseOrder] = relationship(back_populates="lines")
