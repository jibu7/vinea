"""Chart of accounts, per-company GL settings and the projects master (Master Plan §4).

Tenant consistency is declarative: every cross-table reference inside a tenant is a
composite foreign key `(company_id, x_id) → (company_id, id)`, so a row can never point
at another tenant's account, branch or period even though FK checks bypass RLS.
"""

import enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.inventory import (
    InventoryTransactionKind,
    NegativeStockPolicy,
    inventory_txn_kind_enum,
    negative_stock_policy_enum,
)
from app.models.mixins import AuditedMixin, CompanyScopedMixin, pg_enum


class AccountClass(enum.StrEnum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"


PROFIT_AND_LOSS_CLASSES = frozenset({AccountClass.INCOME, AccountClass.EXPENSE})


class ControlType(enum.StrEnum):
    """Accounts owned by a module. Manual journals may not post to them; the owning
    module (cashbook for bank/cash, AR/AP/Inventory subledgers from P4/P5, order entry
    from P6) does."""

    BANK = "bank"
    CASH = "cash"
    AR = "ar"
    AP = "ap"
    INVENTORY = "inventory"
    #: P6 decision 5 — Goods Received Not Invoiced. Written by `inv` when a GRN receives
    #: stock and relieved by `ap` when the supplier invoice matches it, which is why it
    #: registers two modules rather than one. Its balance is provable at any date: the sum
    #: over GRN lines of (received value − relieved value), asserted by
    #: `assert_order_invariants`. Every line on it carries an item, like the INV accounts.
    GRN_ACCRUAL = "grn_accrual"


CASHBOOK_CONTROL_TYPES = frozenset({ControlType.BANK, ControlType.CASH})

#: Control accounts whose every journal line must carry an `item_id`. Inventory has required
#: it since P5; the GRN accrual joins it because the accrual proof is per GRN line, and a line
#: with no item cannot be attributed to one. Enforced in the engine *and* by the VN008 branch
#: of `kernel_check_subledger_line`, which reads this same pair of values.
ITEM_REQUIRED_CONTROL_TYPES = frozenset({ControlType.INVENTORY, ControlType.GRN_ACCRUAL})


class BackorderPolicy(enum.StrEnum):
    """Whether a sales order may commit more than is available (P6 decision 7).

    `ALLOW` is the default and the Evolution behaviour: the order takes the quantity, the
    shortfall shows as backordered on the order, the enquiry and the grid, and nothing is
    blocked. `BLOCK` refuses the line with `exceeds_available`.

    Commitments are advisory either way — this policy governs the *order*, never the posting.
    The hard stop on issuing stock you do not have stays `negative_stock_policy`.
    """

    ALLOW = "allow"
    BLOCK = "block"


backorder_policy_enum = pg_enum(BackorderPolicy, "backorder_policy")


class ControlAccountModule(Base):
    """Which modules may post to a module-owned control account (ADR-05).

    A **registry, not an equality test**: `('ar', 'ar')` is a row, not a rule baked into the
    engine, so P10's POS — which legitimately raises AR — registers `('ar', 'pos')` in its
    own migration instead of weakening the guard. Default is deny: a control type listed here
    at all is reachable only from the modules paired with it. Product-level configuration, so
    it is not company-scoped and carries no RLS policy.
    """

    __tablename__ = "control_account_modules"

    control_type: Mapped[ControlType] = mapped_column(
        pg_enum(ControlType, "gl_control_type"), primary_key=True
    )
    module: Mapped[str] = mapped_column(String(10), primary_key=True)


class GLAccount(AuditedMixin, CompanyScopedMixin, Base):
    __tablename__ = "gl_accounts"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_gl_accounts_company_id_id"),
        UniqueConstraint("company_id", "code", name="uq_gl_accounts_company_code"),
        ForeignKeyConstraint(
            ["company_id", "parent_id"],
            ["gl_accounts.company_id", "gl_accounts.id"],
            name="fk_gl_accounts_parent",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "is_control = (control_type IS NOT NULL)", name="control_type_matches_flag"
        ),
        CheckConstraint("NOT (is_control AND NOT is_postable)", name="control_is_postable"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    class_: Mapped[AccountClass] = mapped_column(
        "class", pg_enum(AccountClass, "account_class"), nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    is_postable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_control: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    control_type: Mapped[ControlType | None] = mapped_column(
        pg_enum(ControlType, "gl_control_type")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    @property
    def is_profit_and_loss(self) -> bool:
        return self.class_ in PROFIT_AND_LOSS_CLASSES


# P4 adds the AR/AP defaults; each is a composite FK back to `gl_accounts`.
SETTINGS_ACCOUNT_FIELDS = (
    "retained_earnings_account_id",
    "rounding_difference_account_id",
    "realized_fx_gain_account_id",
    "realized_fx_loss_account_id",
    "settlement_discount_granted_account_id",
    "settlement_discount_received_account_id",
    "post_dated_receivable_account_id",
    "post_dated_payable_account_id",
    "ar_control_account_id",
    "ap_control_account_id",
    # P5 inventory defaults.
    "inventory_account_id",
    "inventory_adjustment_account_id",
    "inventory_in_transit_account_id",
    "stock_count_variance_account_id",
    "cogs_account_id",
    # P6 order-entry defaults.
    "grn_accrual_account_id",
    "purchase_price_variance_account_id",
    "landed_cost_clearing_account_id",
    # P7 tax and revaluation defaults.
    "vat_settlement_account_id",
    "ar_revaluation_account_id",
    "ap_revaluation_account_id",
    "unrealized_fx_gain_account_id",
    "unrealized_fx_loss_account_id",
    # P8 banking defaults.
    "bank_revaluation_account_id",
    "bank_charges_account_id",
    "bank_interest_account_id",
)


def _settings_account_fk(field: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["company_id", field],
        ["gl_accounts.company_id", "gl_accounts.id"],
        name=f"fk_gl_settings_{field.removesuffix('_id')}",
        ondelete="RESTRICT",
    )


class GLSettings(AuditedMixin, CompanyScopedMixin, Base):
    """Module defaults for the account-determination chain (ADR-05) — one row per company."""

    __tablename__ = "gl_settings"
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_gl_settings_company_id"),
        *(_settings_account_fk(field) for field in SETTINGS_ACCOUNT_FIELDS),
        ForeignKeyConstraint(
            ["company_id", "default_warehouse_id"],
            ["warehouses.company_id", "warehouses.id"],
            name="fk_gl_settings_default_warehouse",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    retained_earnings_account_id: Mapped[int | None] = mapped_column(BigInteger)
    # Absorbs sub-unit differences left by per-line rounding on multi-currency entries.
    rounding_difference_account_id: Mapped[int | None] = mapped_column(BigInteger)
    # --- P4 AR/AP defaults ---------------------------------------------------------------
    # Realized FX at allocation time; the two may point at the same account.
    realized_fx_gain_account_id: Mapped[int | None] = mapped_column(BigInteger)
    realized_fx_loss_account_id: Mapped[int | None] = mapped_column(BigInteger)
    settlement_discount_granted_account_id: Mapped[int | None] = mapped_column(BigInteger)
    settlement_discount_received_account_id: Mapped[int | None] = mapped_column(BigInteger)
    # Cash that has not landed yet: post-dated cheques and similar instruments (§B.2).
    post_dated_receivable_account_id: Mapped[int | None] = mapped_column(BigInteger)
    post_dated_payable_account_id: Mapped[int | None] = mapped_column(BigInteger)
    ar_control_account_id: Mapped[int | None] = mapped_column(BigInteger)
    ap_control_account_id: Mapped[int | None] = mapped_column(BigInteger)
    # --- P5 inventory defaults -----------------------------------------------------------
    # Both INV control accounts (decision 2): only `module='inv'` may post to them, and every
    # such line carries an item. A manual journal against either fails on the DB trigger.
    inventory_account_id: Mapped[int | None] = mapped_column(BigInteger)
    inventory_in_transit_account_id: Mapped[int | None] = mapped_column(BigInteger)
    # Contra accounts. The count-variance default is allowed to equal the adjustment account
    # (decision 10) — a shrinkage and a written-off breakage are the same expense to most SMEs.
    inventory_adjustment_account_id: Mapped[int | None] = mapped_column(BigInteger)
    stock_count_variance_account_id: Mapped[int | None] = mapped_column(BigInteger)
    # Seeded now, first read by P6's `StockSold`; P5 posts nothing to it.
    cogs_account_id: Mapped[int | None] = mapped_column(BigInteger)
    # --- P6 order-entry defaults ---------------------------------------------------------
    # The GRN accrual is a control account (decision 5): only `inv` and `ap` may post to it
    # and every line carries an item, so it is guarded exactly like the INV accounts.
    grn_accrual_account_id: Mapped[int | None] = mapped_column(BigInteger)
    # Where a match writes the difference between what was accrued and what was invoiced —
    # price *and* rate movements both land here (decision 6).
    purchase_price_variance_account_id: Mapped[int | None] = mapped_column(BigInteger)
    # Deliberately a **plain** account, not a control one: freight arrives on a forwarder's
    # supplier invoice as an ordinary GL line and duty as a cashbook payment to RRA, and both
    # have to be able to land on it. Its proof is arithmetic rather than a guard — booked
    # minus allocated, zero when everything is allocated.
    landed_cost_clearing_account_id: Mapped[int | None] = mapped_column(BigInteger)
    backorder_policy: Mapped[BackorderPolicy] = mapped_column(
        backorder_policy_enum,
        nullable=False,
        default=BackorderPolicy.ALLOW,
        server_default=BackorderPolicy.ALLOW.value,
    )
    # Policy, not an account: the same settings row rather than a second settings store.
    negative_stock_policy: Mapped[NegativeStockPolicy] = mapped_column(
        negative_stock_policy_enum,
        nullable=False,
        default=NegativeStockPolicy.BLOCK,
        server_default=NegativeStockPolicy.BLOCK.value,
    )
    default_warehouse_id: Mapped[int | None] = mapped_column(BigInteger)
    # --- P7 tax and revaluation defaults ---------------------------------------------------
    #: Where a filed VAT return settles. One account, one balance: a net payable sits as a
    #: credit and a net credit position as a debit on the same account, which is the shape an
    #: accountant recognises and reconciles against the authority's own statement.
    vat_settlement_account_id: Mapped[int | None] = mapped_column(BigInteger)
    #: The contra side of an unrealized revaluation of open partner items (P7 decision 13).
    #: **Not** the AR/AP control accounts: those are subledger-only and their balance is the
    #: sum of open items at booking rates, which a revaluation would break.
    ar_revaluation_account_id: Mapped[int | None] = mapped_column(BigInteger)
    ap_revaluation_account_id: Mapped[int | None] = mapped_column(BigInteger)
    #: Unrealized, and deliberately separate from P4's realized pair: a gain that exists only
    #: because a rate moved on a balance-sheet date is not the same fact as one crystallised
    #: by a payment, and an accountant reads the two apart.
    unrealized_fx_gain_account_id: Mapped[int | None] = mapped_column(BigInteger)
    unrealized_fx_loss_account_id: Mapped[int | None] = mapped_column(BigInteger)
    # --- P8 banking defaults ---------------------------------------------------------------
    #: The contra side of a bank/cash revaluation (P8 decision 8) — **never the bank account
    #: itself.** A base-only line on a bank account (zero `amount`, non-zero `base_amount`) is
    #: a ledger line the statement can never show, so the reconciliation would carry it as
    #: outstanding forever. The balance sheet reads `1121 + 1130` exactly as it reads
    #: `1200 + 1290`.
    bank_revaluation_account_id: Mapped[int | None] = mapped_column(BigInteger)
    #: The default counterpart the drawer opens with when a statement **debit** is posted as a
    #: bank fee, and the credit side for interest. Defaults, not rules: a `bank_rules` row is
    #: what makes a specific recurring line prefill, and a person still presses Post.
    bank_charges_account_id: Mapped[int | None] = mapped_column(BigInteger)
    bank_interest_account_id: Mapped[int | None] = mapped_column(BigInteger)
    #: The item class a purchase line with no item is registered under — rent, freight, a
    #: consultant's fee. The authority requires a class on every line; Vinea does not require
    #: an item on an AP line, so one default bridges the two (decision 9).
    fiscal_default_purchase_class_code: Mapped[str | None] = mapped_column(String(20))


class Project(AuditedMixin, CompanyScopedMixin, Base):
    """Job/project costing dimension (D7/D8). Master only in P2; UI arrives in P3."""

    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_projects_company_id_id"),
        UniqueConstraint("company_id", "code", name="uq_projects_company_code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class GLTransactionType(AuditedMixin, CompanyScopedMixin, Base):
    """The 4th link of the account-determination chain (ADR-05): a named transaction type
    with a default contra account. One table serves every module (`module` discriminator),
    so AR/AP/Inventory/OE types in later phases are rows, not new tables."""

    __tablename__ = "gl_transaction_types"
    __table_args__ = (
        # P5: the composite-FK target `stock_moves.transaction_type_id` points at — a move
        # records which type produced it, and may not point at another tenant's.
        UniqueConstraint(
            "company_id", "id", name="uq_gl_transaction_types_company_id_id"
        ),
        UniqueConstraint(
            "company_id", "module", "code", name="uq_gl_transaction_types_company_module_code"
        ),
        ForeignKeyConstraint(
            ["company_id", "default_gl_account_id"],
            ["gl_accounts.company_id", "gl_accounts.id"],
            name="fk_gl_transaction_types_default_gl_account",
            ondelete="RESTRICT",
        ),
        # `__`-prefixed keys are reserved for kernel sentinels (e.g. the year-end close).
        CheckConstraint("code NOT LIKE '\\_\\_%'", name="code_not_reserved"),
        # Inventory is the first module whose types need to say what they *do* as well as
        # what they are called: the posting map keys off `kind`, so a user-defined "Damaged"
        # type is an `adjustment_out` with its own contra rather than a new code the engine
        # would have to recognise.
        CheckConstraint(
            "module <> 'inv' OR kind IS NOT NULL", name="inventory_type_has_a_kind"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    module: Mapped[str] = mapped_column(String(10), nullable=False, default="gl")
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # NULL for `gl`/`ar`/`ap`, whose behaviour is fixed by the document they sit on.
    kind: Mapped[InventoryTransactionKind | None] = mapped_column(inventory_txn_kind_enum)
    default_gl_account_id: Mapped[int | None] = mapped_column(BigInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
