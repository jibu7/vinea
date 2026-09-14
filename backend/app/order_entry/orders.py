"""Sales and purchase orders — create, edit, close, cancel (P6 decision 3).

**Nothing in this module posts.** No journal entry, no stock move, no number claimed from any
run but the order's own. An order is a commitment: it holds a quantity, and every quantity it
holds is a query in `app.order_entry.quantities`, never a column here.

Three rules give the whole of an order's life after it is taken, and each exists because of
what it prevents:

* **A line with fulfilment cannot go below what has been fulfilled.** An order for 100 with 60
  received cannot be edited to 40 — the goods are on the shelf and the accrual carries their
  value, and an order that claimed 40 would make `received > ordered` true, which is the one
  thing `assert_order_invariants` will not have. Use **Close** for "the rest is not coming".
* **Partner and currency lock once anything is fulfilled.** Moving an invoiced order to another
  customer would re-point documents that have already reached the ledger; changing its currency
  would re-price lines against a rate that has already been booked.
* **Cancel is only reachable while nothing has been fulfilled at all.** An order that has
  delivered something is a thing that happened, and Close is what records that the remainder
  will not follow it. Both keep the number: an auditor following the `SO-` run must not find a
  hole where an order was withdrawn.

Editing replaces the line set. Lines the caller sends with an `line_id` are updated in place —
the id is what an invoice line points at, so it has to survive — lines without one are added,
and existing lines the caller omits are removed unless something has fulfilled them.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.inventory import masters as inventory_masters
from app.kernel.errors import LedgerStateError
from app.kernel.money import ZERO, base_currency, rate_on, resolve_tax_code, round_amount, split_tax
from app.kernel.posting import gl_settings_for
from app.kernel.sequences import DocType, claim_number
from app.models.currency import Currency
from app.models.gl import BackorderPolicy
from app.models.inventory import Item, ItemType, Warehouse
from app.models.order_entry import (
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    SalesOrder,
    SalesOrderLine,
    SalesOrderStatus,
)
from app.models.partner import PartnerRole, TaxMode
from app.models.user import User
from app.order_entry import kits, pricing
from app.order_entry import quantities as order_quantities
from app.services.audit import record_audit
from app.subledger import masters as partner_masters

ONE = Decimal(1)
HUNDRED = Decimal(100)


# --- Inputs -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderLineInput:
    """One line as keyed. `line_id` names an existing line being edited; `None` adds one.

    A **kit** line carries only the kit item and its quantity: the components are the service's
    to explode and Breakup's to edit, never the caller's to send here. Letting a caller post
    components directly would be a second way to build an explosion, and the two would drift.
    """

    item_id: int
    quantity: Decimal
    line_id: int | None = None
    uom_id: int | None = None
    unit_price: Decimal | None = None
    discount_percent: Decimal = ZERO
    tax_code_id: int | None = None
    warehouse_id: int | None = None
    project_id: int | None = None
    description: str | None = None


@dataclass(frozen=True)
class SalesOrderInput:
    partner_id: int
    order_date: date
    description: str
    expected_date: date | None = None
    reference: str | None = None
    currency_id: int | None = None
    exchange_rate: Decimal | None = None
    branch_id: int | None = None
    project_id: int | None = None
    warehouse_id: int | None = None
    payment_terms_id: int | None = None
    sales_rep_id: int | None = None
    tax_mode: TaxMode | None = None
    lines: tuple[OrderLineInput, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PurchaseOrderInput:
    partner_id: int
    order_date: date
    description: str
    expected_date: date | None = None
    reference: str | None = None
    currency_id: int | None = None
    exchange_rate: Decimal | None = None
    branch_id: int | None = None
    project_id: int | None = None
    #: The **delivery** warehouse. A GRN raised against this order receives into it.
    warehouse_id: int | None = None
    tax_mode: TaxMode | None = None
    lines: tuple[OrderLineInput, ...] = field(default_factory=tuple)


@dataclass
class _PricedLine:
    """A line after the catalogue and the tax code have had their say."""

    source: OrderLineInput
    item: Item
    uom_id: int
    base_quantity: Decimal
    warehouse: Warehouse
    unit_price: Decimal
    tax_code_id: int | None
    net: Decimal
    tax: Decimal
    #: Components, when this is a kit line. Each is (item, base quantity).
    components: list[kits.ExplodedComponent] = field(default_factory=list)

    @property
    def gross(self) -> Decimal:
        return self.net + self.tax


# --- Reads --------------------------------------------------------------------------------------


def get_sales_order(db: Session, company_id: int, order_id: int) -> SalesOrder:
    order = db.get(SalesOrder, order_id)
    if order is None or order.company_id != company_id:
        raise NotFoundError("Sales order not found")
    return order


def get_purchase_order(db: Session, company_id: int, order_id: int) -> PurchaseOrder:
    order = db.get(PurchaseOrder, order_id)
    if order is None or order.company_id != company_id:
        raise NotFoundError("Purchase order not found")
    return order


def list_sales_orders(
    db: Session,
    company_id: int,
    *,
    partner_id: int | None = None,
    status: SalesOrderStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: int | None = None,
    limit: int = 100,
) -> tuple[list[SalesOrder], int | None]:
    statement = select(SalesOrder).where(SalesOrder.company_id == company_id)
    if partner_id is not None:
        statement = statement.where(SalesOrder.partner_id == partner_id)
    if status is not None:
        statement = statement.where(SalesOrder.status == status)
    if date_from is not None:
        statement = statement.where(SalesOrder.order_date >= date_from)
    if date_to is not None:
        statement = statement.where(SalesOrder.order_date <= date_to)
    if cursor is not None:
        statement = statement.where(SalesOrder.id > cursor)
    rows = list(db.scalars(statement.order_by(SalesOrder.id).limit(limit + 1)))
    return rows[:limit], (rows[limit - 1].id if len(rows) > limit else None)


def list_purchase_orders(
    db: Session,
    company_id: int,
    *,
    partner_id: int | None = None,
    status: PurchaseOrderStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: int | None = None,
    limit: int = 100,
) -> tuple[list[PurchaseOrder], int | None]:
    statement = select(PurchaseOrder).where(PurchaseOrder.company_id == company_id)
    if partner_id is not None:
        statement = statement.where(PurchaseOrder.partner_id == partner_id)
    if status is not None:
        statement = statement.where(PurchaseOrder.status == status)
    if date_from is not None:
        statement = statement.where(PurchaseOrder.order_date >= date_from)
    if date_to is not None:
        statement = statement.where(PurchaseOrder.order_date <= date_to)
    if cursor is not None:
        statement = statement.where(PurchaseOrder.id > cursor)
    rows = list(db.scalars(statement.order_by(PurchaseOrder.id).limit(limit + 1)))
    return rows[:limit], (rows[limit - 1].id if len(rows) > limit else None)


# --- Resolution ---------------------------------------------------------------------------------


def _resolve_currency(db: Session, company_id: int, currency_id: int | None) -> Currency:
    if currency_id is None:
        return base_currency(db, company_id)
    currency = db.get(Currency, currency_id)
    if currency is None or currency.company_id != company_id or not currency.is_active:
        raise LedgerStateError(
            "Currency is not available",
            code="currency_not_found",
            field_errors={"currency_id": ["unknown or inactive currency"]},
        )
    return currency


def _resolve_warehouse(
    db: Session, company_id: int, warehouse_id: int | None, *, field_name: str
) -> Warehouse:
    if warehouse_id is None:
        raise LedgerStateError(
            "No warehouse on the line, the order or the defaults",
            code="warehouse_required",
            field_errors={field_name: ["required"]},
        )
    warehouse = inventory_masters.get_warehouse(db, company_id, warehouse_id)
    if warehouse.is_in_transit:
        raise LedgerStateError(
            "The in-transit warehouse is not selectable on a document",
            code="in_transit_warehouse_locked",
            field_errors={field_name: ["not selectable"]},
        )
    return warehouse


def _price_lines(
    db: Session,
    company_id: int,
    role: PartnerRole,
    lines: Sequence[OrderLineInput],
    *,
    order_date: date,
    currency: Currency,
    tax_mode: TaxMode,
    default_warehouse_id: int | None,
    default_tax_code_id: int | None,
) -> list[_PricedLine]:
    """Catalogue defaults, unit conversion, tax split — the same arithmetic a document line
    goes through, so an invoice raised from an order reproduces the order's figures.

    An order line's value may be **zero**, unlike a document line's. A kit component carries no
    money at all, and a line given away free is a promise a business is entitled to make; an
    order posts nothing, so there is no entry for a zero to unbalance.
    """
    if not lines:
        raise LedgerStateError(
            "An order needs at least one line",
            code="empty_document",
            field_errors={"lines": ["at least one line required"]},
        )
    places = currency.decimal_places
    priced: list[_PricedLine] = []
    for index, line in enumerate(lines):
        item = inventory_masters.get_item(db, company_id, line.item_id)
        if not item.is_active:
            raise LedgerStateError(
                f"{item.code} is not active",
                code="item_not_active",
                field_errors={f"lines.{index}.item_id": ["not active"]},
            )
        if item.item_type == ItemType.KIT and role == PartnerRole.AP:
            # A kit is a virtual bundle that exists to be sold, never bought (decision 8).
            raise LedgerStateError(
                f"{item.code} is a kit and cannot be purchased",
                code="kit_not_purchasable",
                field_errors={f"lines.{index}.item_id": ["a kit cannot be purchased"]},
            )
        uom = inventory_masters.get_uom(db, company_id, line.uom_id or item.base_uom_id)
        base_quantity = inventory_masters.to_base_quantity(line.quantity, uom, item)
        if line.quantity <= ZERO or base_quantity <= ZERO:
            raise LedgerStateError(
                "An ordered quantity must be greater than zero",
                code="invalid_quantity",
                field_errors={f"lines.{index}.quantity": ["must be greater than zero"]},
            )
        warehouse = _resolve_warehouse(
            db,
            company_id,
            line.warehouse_id or default_warehouse_id,
            field_name=f"lines.{index}.warehouse_id",
        )

        tax_code_id = line.tax_code_id
        if tax_code_id is None:
            tax_code_id = (
                item.default_sales_tax_code_id
                if role == PartnerRole.AR
                else item.default_purchase_tax_code_id
            )
        if tax_code_id is None:
            tax_code_id = default_tax_code_id

        unit_price = line.unit_price
        if unit_price is None:
            # Only the sales side has a catalogue price. Purchase prices are keyed on the order
            # (decision 3 — there is no purchase price history in this phase), so an AP line
            # that names none is a line priced at nothing rather than a line priced wrongly.
            unit_price = (
                pricing.catalogue_unit_price(
                    db,
                    company_id,
                    item,
                    tax_mode=tax_mode,
                    tax_code_id=tax_code_id,
                    on_date=order_date,
                )
                if role == PartnerRole.AR
                else ZERO
            )
        if unit_price < ZERO or line.discount_percent < ZERO or line.discount_percent > HUNDRED:
            raise LedgerStateError(
                "A price cannot be negative and a discount must be a percentage",
                code="invalid_amount",
                field_errors={f"lines.{index}.unit_price": ["invalid"]},
            )

        gross_or_net = round_amount(
            line.quantity * unit_price * (ONE - line.discount_percent / HUNDRED), places
        )
        net, tax = gross_or_net, ZERO
        if tax_code_id is not None and gross_or_net != ZERO:
            tax_code = resolve_tax_code(db, company_id, tax_code_id, order_date)
            split = split_tax(
                gross_or_net,
                tax_code.rate_pct,
                inclusive=tax_mode == TaxMode.INCLUSIVE,
                decimal_places=places,
            )
            net, tax = split.net, split.tax

        components: list[kits.ExplodedComponent] = []
        if item.item_type == ItemType.KIT:
            components = kits.explode(
                db, company_id, item, base_quantity, field_prefix=f"lines.{index}"
            )
        priced.append(
            _PricedLine(
                source=line,
                item=item,
                uom_id=uom.id,
                base_quantity=base_quantity,
                warehouse=warehouse,
                unit_price=unit_price,
                tax_code_id=tax_code_id,
                net=net,
                tax=tax,
                components=components,
            )
        )
    return priced


# --- The backorder policy -----------------------------------------------------------------------


def _assert_within_available(
    db: Session,
    company_id: int,
    priced: Sequence[_PricedLine],
    *,
    exclude_order_id: int | None,
) -> None:
    """Under `backorder_policy = block`, refuse a sales order line that promises what is not
    there (`exceeds_available`).

    Checked **before anything is written**, and the ordering is not cosmetic: every service in
    this phase refuses before it writes, which is what lets a caller — the property machine
    included — treat a refusal as a no-op rather than having to unwind a half-written order.
    The prospective commitment is therefore this order's own lines added to what *other* open
    orders already commit, with this order's current lines excluded so an edit is measured
    against its own effect rather than twice.

    Under `allow` — the default — this does nothing at all. A sales order may promise what is
    not on the shelf; that is a backorder, and the enquiry, the listing and the line grid all
    show it.
    """
    settings = gl_settings_for(db, company_id)
    if settings.backorder_policy != BackorderPolicy.BLOCK:
        return
    wanted: dict[tuple[int, int], Decimal] = {}
    for line in priced:
        for item, quantity in _stock_demand(line):
            key = (item.id, line.warehouse.id)
            wanted[key] = wanted.get(key, ZERO) + quantity
    for (item_id, warehouse_id), quantity in wanted.items():
        on_hand = order_quantities.position(db, company_id, item_id, warehouse_id).on_hand
        committed = order_quantities.committed_by_warehouse(
            db, company_id, item_id, exclude_order_id=exclude_order_id
        ).get(warehouse_id, ZERO)
        if committed + quantity > on_hand:
            item = inventory_masters.get_item(db, company_id, item_id)
            raise LedgerStateError(
                f"{item.code} has {on_hand - committed} available and this order wants "
                f"{quantity}",
                code="exceeds_available",
                field_errors={"lines": ["exceeds what is available"]},
            )


def _stock_demand(line: _PricedLine) -> list[tuple[Item, Decimal]]:
    """What a line actually takes off a shelf.

    A kit takes nothing — it is a bundle, and its **components** are the things that ship, which
    is why commitment comes from the component lines and revenue from the parent. A service or
    non-stock item takes nothing either: there is no shelf.
    """
    if line.item.item_type == ItemType.KIT:
        return [
            (component.item, component.base_quantity)
            for component in line.components
            if component.item.item_type == ItemType.STOCK
        ]
    if line.item.item_type == ItemType.STOCK:
        return [(line.item, line.base_quantity)]
    return []


# --- Create -------------------------------------------------------------------------------------


def create_sales_order(
    db: Session,
    company_id: int,
    data: SalesOrderInput,
    *,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> tuple[SalesOrder, bool]:
    """Take a customer's order. Never commits. Returns the order and whether this was a replay."""
    if idempotency_key:
        existing = _replay(db, SalesOrder, company_id, idempotency_key, idempotency_hash)
        if existing is not None:
            return existing, True

    partner = partner_masters.get_partner(db, company_id, data.partner_id)
    _assert_role(partner, PartnerRole.AR)
    settings = partner_masters.role_settings_or_default(db, company_id, partner.id, PartnerRole.AR)
    gl_settings = gl_settings_for(db, company_id)

    currency = _resolve_currency(db, company_id, data.currency_id or partner.currency_id)
    tax_mode = data.tax_mode or settings.tax_mode
    branch_id = data.branch_id or settings.default_branch_id
    warehouse = _resolve_warehouse(
        db,
        company_id,
        data.warehouse_id or gl_settings.default_warehouse_id,
        field_name="warehouse_id",
    )
    priced = _price_lines(
        db,
        company_id,
        PartnerRole.AR,
        data.lines,
        order_date=data.order_date,
        currency=currency,
        tax_mode=tax_mode,
        default_warehouse_id=warehouse.id,
        default_tax_code_id=getattr(settings, "default_tax_code_id", None),
    )
    _assert_within_available(db, company_id, priced, exclude_order_id=None)

    order = SalesOrder(
        company_id=company_id,
        number=claim_number(db, company_id, DocType.SALES_ORDER, branch_id).number,
        partner_id=partner.id,
        order_date=data.order_date,
        expected_date=data.expected_date,
        reference=data.reference,
        description=data.description,
        currency_id=currency.id,
        exchange_rate=data.exchange_rate or rate_on(db, currency, data.order_date),
        branch_id=branch_id or warehouse.branch_id,
        project_id=data.project_id or settings.default_project_id,
        warehouse_id=warehouse.id,
        payment_terms_id=data.payment_terms_id or settings.payment_terms_id,
        sales_rep_id=data.sales_rep_id or settings.sales_rep_id,
        tax_mode=tax_mode,
        status=SalesOrderStatus.OPEN,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    db.add(order)
    db.flush()
    _write_sales_lines(db, order, priced)
    _retotal_sales(db, order)
    record_audit(
        db,
        company_id=company_id,
        action="sales_order.created",
        entity="sales_orders",
        entity_id=order.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        after=_sales_snapshot(order),
        request=request,
    )
    return order, False


def create_purchase_order(
    db: Session,
    company_id: int,
    data: PurchaseOrderInput,
    *,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> tuple[PurchaseOrder, bool]:
    """Place an order with a supplier. Never commits.

    The order's warehouse is the **delivery** warehouse, and the branch follows it rather than
    the partner's default: the accrual a receipt against this order will credit is proved in
    the branch the goods land in (the branch rule, step 2), and an order whose header said
    otherwise would be describing a delivery that cannot happen.
    """
    if idempotency_key:
        existing = _replay(db, PurchaseOrder, company_id, idempotency_key, idempotency_hash)
        if existing is not None:
            return existing, True

    partner = partner_masters.get_partner(db, company_id, data.partner_id)
    _assert_role(partner, PartnerRole.AP)
    settings = partner_masters.role_settings_or_default(db, company_id, partner.id, PartnerRole.AP)
    gl_settings = gl_settings_for(db, company_id)

    currency = _resolve_currency(db, company_id, data.currency_id or partner.currency_id)
    tax_mode = data.tax_mode or settings.tax_mode
    warehouse = _resolve_warehouse(
        db,
        company_id,
        data.warehouse_id or gl_settings.default_warehouse_id,
        field_name="warehouse_id",
    )
    if data.branch_id is not None and data.branch_id != warehouse.branch_id:
        raise LedgerStateError(
            "The branch does not match the delivery warehouse",
            code="branch_warehouse_mismatch",
            field_errors={"branch_id": ["does not match the warehouse's branch"]},
        )
    priced = _price_lines(
        db,
        company_id,
        PartnerRole.AP,
        data.lines,
        order_date=data.order_date,
        currency=currency,
        tax_mode=tax_mode,
        default_warehouse_id=warehouse.id,
        default_tax_code_id=getattr(settings, "default_tax_code_id", None),
    )
    _assert_one_branch(priced)

    order = PurchaseOrder(
        company_id=company_id,
        number=claim_number(db, company_id, DocType.PURCHASE_ORDER, warehouse.branch_id).number,
        partner_id=partner.id,
        order_date=data.order_date,
        expected_date=data.expected_date,
        reference=data.reference,
        description=data.description,
        currency_id=currency.id,
        exchange_rate=data.exchange_rate or rate_on(db, currency, data.order_date),
        branch_id=warehouse.branch_id,
        project_id=data.project_id or settings.default_project_id,
        warehouse_id=warehouse.id,
        tax_mode=tax_mode,
        status=PurchaseOrderStatus.OPEN,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    db.add(order)
    db.flush()
    _write_purchase_lines(db, order, priced)
    _retotal_purchase(db, order)
    record_audit(
        db,
        company_id=company_id,
        action="purchase_order.created",
        entity="purchase_orders",
        entity_id=order.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        after=_purchase_snapshot(order),
        request=request,
    )
    return order, False


def _assert_one_branch(priced: Sequence[_PricedLine]) -> None:
    """A purchase order delivers to one place. Its lines may name warehouses, but they must all
    sit in the branch the accrual will be proved in — the step-2 rule carried forward, as the
    owner's note 4 requires: a PO's delivery warehouse is the GRN's warehouse."""
    branches = {line.warehouse.branch_id for line in priced}
    if len(branches) > 1:
        raise LedgerStateError(
            "A purchase order cannot span branches — one order, one place the goods land",
            code="order_spans_branches",
            field_errors={"lines": ["warehouses in more than one branch"]},
        )


def _assert_role(partner, role: PartnerRole) -> None:  # noqa: ANN001
    if not partner.has_role(role):
        raise LedgerStateError(
            f"{partner.name} is not a {'customer' if role == PartnerRole.AR else 'supplier'}",
            code="partner_role_missing",
            field_errors={"partner_id": ["wrong role"]},
        )
    if not partner.is_active:
        raise LedgerStateError(f"{partner.name} is inactive", code="partner_inactive")


def _replay(db: Session, model, company_id: int, key: str, request_hash: str | None):  # noqa: ANN001, ANN202
    existing = db.scalar(
        select(model).where(model.company_id == company_id, model.idempotency_key == key)
    )
    if existing is None:
        return None
    if request_hash is not None and existing.idempotency_hash != request_hash:
        raise LedgerStateError(
            f"Idempotency-Key {key} was already used for a different order ({existing.number})",
            code="idempotency_key_reused",
        )
    return existing


# --- Writing the line set -----------------------------------------------------------------------


def _write_sales_lines(db: Session, order: SalesOrder, priced: Sequence[_PricedLine]) -> None:
    """Append the priced lines to a fresh order, exploding kits as they land."""
    line_no = 0
    for line in priced:
        line_no += 1
        parent = SalesOrderLine(
            company_id=order.company_id,
            order_id=order.id,
            line_no=line_no,
            item_id=line.item.id,
            description=line.source.description,
            uom_id=line.uom_id,
            quantity=line.source.quantity,
            base_quantity=line.base_quantity,
            unit_price=line.unit_price,
            discount_percent=line.source.discount_percent,
            tax_code_id=line.tax_code_id,
            net_amount=line.net,
            tax_amount=line.tax,
            gross_amount=line.gross,
            warehouse_id=line.warehouse.id,
            project_id=line.source.project_id,
        )
        db.add(parent)
        db.flush()
        for component in line.components:
            line_no += 1
            db.add(
                _component_line(order, parent, component, line_no, warehouse_id=line.warehouse.id)
            )
    db.flush()


def _component_line(
    order: SalesOrder,
    parent: SalesOrderLine,
    component: kits.ExplodedComponent,
    line_no: int,
    *,
    warehouse_id: int,
) -> SalesOrderLine:
    """A component carries quantity and nothing else. The kit's whole price and tax sit on the
    parent, which is what makes commitment come from the components and revenue from the kit."""
    return SalesOrderLine(
        company_id=order.company_id,
        order_id=order.id,
        line_no=line_no,
        item_id=component.item.id,
        description=component.item.name,
        uom_id=component.item.base_uom_id,
        quantity=component.base_quantity,
        base_quantity=component.base_quantity,
        unit_price=ZERO,
        discount_percent=ZERO,
        tax_code_id=None,
        net_amount=ZERO,
        tax_amount=ZERO,
        gross_amount=ZERO,
        warehouse_id=warehouse_id,
        project_id=parent.project_id,
        kit_parent_line_id=parent.id,
    )


def _write_purchase_lines(
    db: Session, order: PurchaseOrder, priced: Sequence[_PricedLine]
) -> None:
    for line_no, line in enumerate(priced, 1):
        db.add(
            PurchaseOrderLine(
                company_id=order.company_id,
                order_id=order.id,
                line_no=line_no,
                item_id=line.item.id,
                description=line.source.description,
                uom_id=line.uom_id,
                quantity=line.source.quantity,
                base_quantity=line.base_quantity,
                unit_price=line.unit_price,
                discount_percent=line.source.discount_percent,
                tax_code_id=line.tax_code_id,
                net_amount=line.net,
                tax_amount=line.tax,
                gross_amount=line.gross,
                warehouse_id=line.warehouse.id,
                project_id=line.source.project_id,
            )
        )
    db.flush()


def _retotal_sales(db: Session, order: SalesOrder) -> None:
    db.refresh(order)
    order.net_amount = sum((line.net_amount for line in order.lines), ZERO)
    order.tax_amount = sum((line.tax_amount for line in order.lines), ZERO)
    order.total_amount = order.net_amount + order.tax_amount
    db.flush()


def _retotal_purchase(db: Session, order: PurchaseOrder) -> None:
    db.refresh(order)
    order.net_amount = sum((line.net_amount for line in order.lines), ZERO)
    order.tax_amount = sum((line.tax_amount for line in order.lines), ZERO)
    order.total_amount = order.net_amount + order.tax_amount
    db.flush()


# --- Edit ---------------------------------------------------------------------------------------


def update_sales_order(
    db: Session,
    company_id: int,
    order: SalesOrder,
    data: SalesOrderInput,
    *,
    actor: User,
    request: Request | None = None,
) -> SalesOrder:
    """Re-key an open order. Never commits.

    A closed or cancelled order is not editable: both record a decision, and re-opening one by
    editing it would lose the decision without recording that either.
    """
    _assert_editable(order.status, (SalesOrderStatus.CLOSED, SalesOrderStatus.CANCELLED))
    before = _sales_snapshot(order)
    fulfilment = order_quantities.sales_fulfilment(db, company_id, order_id=order.id)
    fulfilled_total = sum((row.fulfilled for row in fulfilment.values()), ZERO)

    _assert_header_unlocked(
        data.partner_id,
        data.currency_id,
        order=order,
        anything_fulfilled=fulfilled_total > ZERO,
    )
    settings = partner_masters.role_settings_or_default(
        db, company_id, order.partner_id, PartnerRole.AR
    )
    currency = _resolve_currency(db, company_id, data.currency_id or order.currency_id)
    tax_mode = data.tax_mode or order.tax_mode
    warehouse = _resolve_warehouse(
        db, company_id, data.warehouse_id or order.warehouse_id, field_name="warehouse_id"
    )
    priced = _price_lines(
        db,
        company_id,
        PartnerRole.AR,
        data.lines,
        order_date=data.order_date,
        currency=currency,
        tax_mode=tax_mode,
        default_warehouse_id=warehouse.id,
        default_tax_code_id=getattr(settings, "default_tax_code_id", None),
    )
    _assert_no_line_goes_below_fulfilment(priced, order.lines, fulfilment)
    _assert_within_available(db, company_id, priced, exclude_order_id=order.id)

    order.order_date = data.order_date
    order.expected_date = data.expected_date
    order.reference = data.reference
    order.description = data.description
    order.currency_id = currency.id
    order.exchange_rate = data.exchange_rate or rate_on(db, currency, data.order_date)
    order.project_id = data.project_id
    order.warehouse_id = warehouse.id
    order.payment_terms_id = data.payment_terms_id
    order.sales_rep_id = data.sales_rep_id
    order.tax_mode = tax_mode
    _replace_sales_lines(db, order, priced, fulfilment)
    _retotal_sales(db, order)
    order_quantities.refresh_sales_order_status(db, order)
    record_audit(
        db,
        company_id=company_id,
        action="sales_order.updated",
        entity="sales_orders",
        entity_id=order.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        before=before,
        after=_sales_snapshot(order),
        request=request,
    )
    return order


def update_purchase_order(
    db: Session,
    company_id: int,
    order: PurchaseOrder,
    data: PurchaseOrderInput,
    *,
    actor: User,
    request: Request | None = None,
) -> PurchaseOrder:
    _assert_editable(order.status, (PurchaseOrderStatus.CLOSED, PurchaseOrderStatus.CANCELLED))
    before = _purchase_snapshot(order)
    fulfilment = order_quantities.purchase_fulfilment(db, company_id, order_id=order.id)
    fulfilled_total = sum((row.fulfilled for row in fulfilment.values()), ZERO)

    _assert_header_unlocked(
        data.partner_id,
        data.currency_id,
        order=order,
        anything_fulfilled=fulfilled_total > ZERO,
    )
    settings = partner_masters.role_settings_or_default(
        db, company_id, order.partner_id, PartnerRole.AP
    )
    currency = _resolve_currency(db, company_id, data.currency_id or order.currency_id)
    tax_mode = data.tax_mode or order.tax_mode
    warehouse = _resolve_warehouse(
        db, company_id, data.warehouse_id or order.warehouse_id, field_name="warehouse_id"
    )
    priced = _price_lines(
        db,
        company_id,
        PartnerRole.AP,
        data.lines,
        order_date=data.order_date,
        currency=currency,
        tax_mode=tax_mode,
        default_warehouse_id=warehouse.id,
        default_tax_code_id=getattr(settings, "default_tax_code_id", None),
    )
    _assert_one_branch(priced)
    _assert_no_line_goes_below_fulfilment(priced, order.lines, fulfilment)

    order.order_date = data.order_date
    order.expected_date = data.expected_date
    order.reference = data.reference
    order.description = data.description
    order.currency_id = currency.id
    order.exchange_rate = data.exchange_rate or rate_on(db, currency, data.order_date)
    order.project_id = data.project_id
    order.warehouse_id = warehouse.id
    order.branch_id = warehouse.branch_id
    order.tax_mode = tax_mode
    _replace_purchase_lines(db, order, priced, fulfilment)
    _retotal_purchase(db, order)
    order_quantities.refresh_purchase_order_status(db, order)
    record_audit(
        db,
        company_id=company_id,
        action="purchase_order.updated",
        entity="purchase_orders",
        entity_id=order.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        before=before,
        after=_purchase_snapshot(order),
        request=request,
    )
    return order


def _assert_editable(status, terminal: tuple) -> None:  # noqa: ANN001
    if status in terminal:
        raise LedgerStateError(
            f"A {status.value} order cannot be edited",
            code="order_not_open",
            field_errors={"status": ["not open"]},
        )


def _assert_header_unlocked(
    partner_id: int, currency_id: int | None, *, order, anything_fulfilled: bool  # noqa: ANN001
) -> None:
    """Partner and currency lock once anything has been fulfilled (decision 3).

    Moving an invoiced order to another partner would re-point documents that have already
    reached the ledger at somebody else's account; changing its currency would re-price lines
    against a rate that has already been booked. Everything else on the header stays editable,
    because nothing else on it has been written into a posting.
    """
    if not anything_fulfilled:
        return
    if partner_id != order.partner_id:
        raise LedgerStateError(
            "The partner cannot change once the order has been fulfilled in part",
            code="order_partner_locked",
            field_errors={"partner_id": ["locked by fulfilment"]},
        )
    if currency_id is not None and currency_id != order.currency_id:
        raise LedgerStateError(
            "The currency cannot change once the order has been fulfilled in part",
            code="order_currency_locked",
            field_errors={"currency_id": ["locked by fulfilment"]},
        )


def _assert_no_line_goes_below_fulfilment(
    priced: Sequence[_PricedLine],
    existing: Sequence,
    fulfilment: dict[int, order_quantities.LineFulfilment],
) -> None:
    """No line may be reduced below what has already been invoiced or received, and no such
    line may be removed.

    The alternative — silently allowing it — makes `fulfilled > ordered` true, which is exactly
    the state `assert_order_invariants` exists to rule out and which no report could then
    explain. "The rest is not coming" is **Close**, which cancels the remainder and keeps the
    history rather than rewriting it.
    """
    kept: dict[int, _PricedLine] = {}
    for line in priced:
        if line.source.line_id is not None:
            kept[line.source.line_id] = line
    for row in existing:
        done = fulfilment.get(row.id)
        if done is None or done.fulfilled <= ZERO:
            continue
        replacement = kept.get(row.id)
        if replacement is None:
            raise LedgerStateError(
                f"Line {row.line_no} has been fulfilled and cannot be removed; close the order "
                "instead",
                code="line_has_fulfilment",
                field_errors={"lines": ["a fulfilled line cannot be removed"]},
            )
        if replacement.base_quantity < done.fulfilled:
            raise LedgerStateError(
                f"Line {row.line_no} has {done.fulfilled} fulfilled and cannot be reduced to "
                f"{replacement.base_quantity}",
                code="below_fulfilled_quantity",
                field_errors={"lines": ["below what has been fulfilled"]},
            )


def _replace_sales_lines(
    db: Session,
    order: SalesOrder,
    priced: Sequence[_PricedLine],
    fulfilment: dict[int, order_quantities.LineFulfilment],
) -> None:
    """Update the lines the caller kept, add the ones it introduced, delete the rest.

    A kit line is **re-exploded from the definition** whenever it is edited, and
    `kit_breakup_edited` goes back to false with it. Scaling a hand-edited explosion to a new
    quantity would have to decide what to do with the rounding, and the answer would be invisible
    on the screen that made the edit; re-exploding is predictable, and Breakup is one click away.
    A component that something has already invoiced is kept and re-quantified rather than
    replaced, because its id is what that invoice line points at.
    """
    existing = {line.id: line for line in order.lines}
    seen: set[int] = set()
    line_no = 0
    for line in priced:
        line_no += 1
        row = existing.get(line.source.line_id) if line.source.line_id is not None else None
        if row is None:
            row = SalesOrderLine(company_id=order.company_id, order_id=order.id, line_no=line_no)
            db.add(row)
        seen.add(id(row))
        row.line_no = line_no
        row.item_id = line.item.id
        row.description = line.source.description
        row.uom_id = line.uom_id
        row.quantity = line.source.quantity
        row.base_quantity = line.base_quantity
        row.unit_price = line.unit_price
        row.discount_percent = line.source.discount_percent
        row.tax_code_id = line.tax_code_id
        row.net_amount = line.net
        row.tax_amount = line.tax
        row.gross_amount = line.gross
        row.warehouse_id = line.warehouse.id
        row.project_id = line.source.project_id
        row.kit_parent_line_id = None
        row.kit_breakup_edited = False
        db.flush()
        for component in line.components:
            line_no += 1
            component_row = _match_component(existing, row.id, component.item.id)
            if component_row is None:
                component_row = _component_line(
                    order, row, component, line_no, warehouse_id=line.warehouse.id
                )
                db.add(component_row)
            else:
                component_row.line_no = line_no
                component_row.quantity = component.base_quantity
                component_row.base_quantity = component.base_quantity
                component_row.warehouse_id = line.warehouse.id
            seen.add(id(component_row))
            db.flush()
    _delete_unseen(db, order.lines, seen, fulfilment)


def _match_component(existing: dict, parent_id: int, item_id: int):  # noqa: ANN001, ANN202
    for row in existing.values():
        if row.kit_parent_line_id == parent_id and row.item_id == item_id:
            return row
    return None


def _replace_purchase_lines(
    db: Session,
    order: PurchaseOrder,
    priced: Sequence[_PricedLine],
    fulfilment: dict[int, order_quantities.LineFulfilment],
) -> None:
    existing = {line.id: line for line in order.lines}
    seen: set[int] = set()
    for line_no, line in enumerate(priced, 1):
        row = existing.get(line.source.line_id) if line.source.line_id is not None else None
        if row is None:
            row = PurchaseOrderLine(
                company_id=order.company_id, order_id=order.id, line_no=line_no
            )
            db.add(row)
        seen.add(id(row))
        row.line_no = line_no
        row.item_id = line.item.id
        row.description = line.source.description
        row.uom_id = line.uom_id
        row.quantity = line.source.quantity
        row.base_quantity = line.base_quantity
        row.unit_price = line.unit_price
        row.discount_percent = line.source.discount_percent
        row.tax_code_id = line.tax_code_id
        row.net_amount = line.net
        row.tax_amount = line.tax
        row.gross_amount = line.gross
        row.warehouse_id = line.warehouse.id
        row.project_id = line.source.project_id
    db.flush()
    _delete_unseen(db, order.lines, seen, fulfilment)


def _delete_unseen(
    db: Session,
    lines: Sequence,
    seen: set[int],
    fulfilment: dict[int, order_quantities.LineFulfilment],
) -> None:
    """Remove the lines the edit dropped. A line with fulfilment is never among them —
    `_assert_no_line_goes_below_fulfilment` refused the whole edit before anything was written —
    and the check is repeated here because this is the statement that would do the damage."""
    for row in list(lines):
        if id(row) in seen:
            continue
        done = fulfilment.get(row.id)
        assert done is None or done.fulfilled <= ZERO, (
            f"line {row.id} has fulfilment and must not be deleted"
        )
        db.delete(row)
    db.flush()


# --- Close and cancel ---------------------------------------------------------------------------


def close_sales_order(
    db: Session,
    order: SalesOrder,
    *,
    on_date: date,
    actor: User,
    request: Request | None = None,
) -> SalesOrder:
    """Give up the remaining quantities and keep the history (decision 3).

    The commitment is released because the order drops out of the open statuses the sums are
    taken over — there is no per-line flag to clear and no total to decrement.
    """
    _assert_editable(order.status, (SalesOrderStatus.CLOSED, SalesOrderStatus.CANCELLED))
    before = _sales_snapshot(order)
    order.status = SalesOrderStatus.CLOSED
    order.closed_on = on_date
    db.flush()
    _audit_transition(db, order, "sales_order.closed", "sales_orders", before, actor, request)
    return order


def cancel_sales_order(
    db: Session,
    order: SalesOrder,
    *,
    on_date: date,
    actor: User,
    request: Request | None = None,
) -> SalesOrder:
    """Abandon an order that has delivered nothing.

    An order with any fulfilment cannot be cancelled (`order_has_fulfilment`): the delivery
    happened, and Close is what records that the remainder will not follow it. The number is
    kept either way — a hole in the `SO-` run is not something an audit can be told about.
    """
    _assert_editable(order.status, (SalesOrderStatus.CLOSED, SalesOrderStatus.CANCELLED))
    fulfilment = order_quantities.sales_fulfilment(db, order.company_id, order_id=order.id)
    _assert_nothing_fulfilled(fulfilment, order.number)
    before = _sales_snapshot(order)
    order.status = SalesOrderStatus.CANCELLED
    order.cancelled_on = on_date
    db.flush()
    _audit_transition(db, order, "sales_order.cancelled", "sales_orders", before, actor, request)
    return order


def close_purchase_order(
    db: Session,
    order: PurchaseOrder,
    *,
    on_date: date,
    actor: User,
    request: Request | None = None,
) -> PurchaseOrder:
    _assert_editable(order.status, (PurchaseOrderStatus.CLOSED, PurchaseOrderStatus.CANCELLED))
    before = _purchase_snapshot(order)
    order.status = PurchaseOrderStatus.CLOSED
    order.closed_on = on_date
    db.flush()
    _audit_transition(
        db, order, "purchase_order.closed", "purchase_orders", before, actor, request
    )
    return order


def cancel_purchase_order(
    db: Session,
    order: PurchaseOrder,
    *,
    on_date: date,
    actor: User,
    request: Request | None = None,
) -> PurchaseOrder:
    _assert_editable(order.status, (PurchaseOrderStatus.CLOSED, PurchaseOrderStatus.CANCELLED))
    fulfilment = order_quantities.purchase_fulfilment(db, order.company_id, order_id=order.id)
    _assert_nothing_fulfilled(fulfilment, order.number)
    before = _purchase_snapshot(order)
    order.status = PurchaseOrderStatus.CANCELLED
    order.cancelled_on = on_date
    db.flush()
    _audit_transition(
        db, order, "purchase_order.cancelled", "purchase_orders", before, actor, request
    )
    return order


def _assert_nothing_fulfilled(
    fulfilment: dict[int, order_quantities.LineFulfilment], number: str
) -> None:
    if any(row.fulfilled > ZERO for row in fulfilment.values()):
        raise LedgerStateError(
            f"{number} has been fulfilled in part; close it instead of cancelling it",
            code="order_has_fulfilment",
            field_errors={"status": ["has fulfilment"]},
        )


def _audit_transition(
    db: Session,
    order,  # noqa: ANN001
    action: str,
    entity: str,
    before: dict,
    actor: User,
    request: Request | None,
) -> None:
    record_audit(
        db,
        company_id=order.company_id,
        action=action,
        entity=entity,
        entity_id=order.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        before=before,
        after=(_sales_snapshot if isinstance(order, SalesOrder) else _purchase_snapshot)(order),
        request=request,
    )


# --- Breakup (decision 8) -----------------------------------------------------------------------


def breakup_sales_order_line(
    db: Session,
    company_id: int,
    order: SalesOrder,
    line_id: int,
    components: tuple[kits.ComponentInput, ...],
    *,
    actor: User,
    request: Request | None = None,
) -> SalesOrderLine:
    """Edit one kit line's explosion — the **Breakup** screen (Transactions → Order Entry).

    Only on an **open, not-yet-invoiced** kit line. Once a kit has been invoiced its components
    have been costed and shipped, and editing what it was made of afterwards would restate a
    delivery that already happened.

    The parent line is untouched: the kit's quantity, price and tax are what the customer
    agreed, and Breakup is about what goes in the box, not what is charged for it.
    """
    _assert_editable(order.status, (SalesOrderStatus.CLOSED, SalesOrderStatus.CANCELLED))
    parent = db.get(SalesOrderLine, line_id)
    if parent is None or parent.company_id != company_id or parent.order_id != order.id:
        raise NotFoundError("Order line not found")
    item = inventory_masters.get_item(db, company_id, parent.item_id)
    if not kits.is_kit(item):
        raise LedgerStateError(
            f"{item.code} is not a kit",
            code="not_a_kit",
            field_errors={"line_id": ["not a kit line"]},
        )
    existing = [line for line in order.lines if line.kit_parent_line_id == parent.id]
    fulfilment = order_quantities.sales_fulfilment(
        db, company_id, [parent.id, *(line.id for line in existing)]
    )
    if any(row.fulfilled > ZERO for row in fulfilment.values()):
        raise LedgerStateError(
            f"{order.number} line {parent.line_no} has been invoiced and cannot be broken up",
            code="line_has_fulfilment",
            field_errors={"line_id": ["already invoiced"]},
        )

    before = {
        "components": [
            {"item_id": line.item_id, "base_quantity": str(line.base_quantity)}
            for line in existing
        ]
    }
    resolved = kits.resolve_breakup(db, company_id, item, components)
    for line in existing:
        db.delete(line)
    db.flush()
    line_no = max((line.line_no for line in order.lines), default=parent.line_no)
    for component in resolved:
        line_no += 1
        db.add(
            _component_line(
                order, parent, component, line_no, warehouse_id=parent.warehouse_id
            )
        )
    parent.kit_breakup_edited = True
    db.flush()
    record_audit(
        db,
        company_id=company_id,
        action="sales_order.line_broken_up",
        entity="sales_order_lines",
        entity_id=parent.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        before=before,
        after={
            "components": [
                {"item_id": row.item.id, "base_quantity": str(row.base_quantity)}
                for row in resolved
            ]
        },
        request=request,
    )
    return parent


# --- What a fulfilment may do to an order ---
#
# Read by the posting services, not by this one. They are here because they are order rules —
# "you cannot invoice more than was ordered" is a fact about the order, and the partner-document
# service should no more re-derive it than it should re-derive a tax rate.


def assert_sales_line_within_order(
    db: Session,
    company_id: int,
    *,
    index: int,
    line_id: int,
    base_quantity: Decimal,
    partner_id: int,
) -> SalesOrderLine:
    """The SO line this invoice line fulfils, or a refusal naming why it cannot.

    `invoice_exceeds_order` is the one with teeth: the user may lower a pre-filled quantity and
    may never raise it above what remains (decision 7). Without it `invoiced > ordered` becomes
    reachable, `committed` goes negative on that line, and the status derivation has to start
    guessing what an over-invoiced order means.
    """
    line = db.get(SalesOrderLine, line_id)
    if line is None or line.company_id != company_id:
        raise LedgerStateError(
            "That sales order line does not exist",
            code="sales_order_line_not_found",
            field_errors={f"lines.{index}.sales_order_line_id": ["not found"]},
        )
    order = get_sales_order(db, company_id, line.order_id)
    if order.partner_id != partner_id:
        raise LedgerStateError(
            f"{order.number} belongs to another customer",
            code="order_partner_mismatch",
            field_errors={f"lines.{index}.sales_order_line_id": ["wrong customer"]},
        )
    if order.status not in (SalesOrderStatus.OPEN, SalesOrderStatus.PARTIALLY_INVOICED):
        raise LedgerStateError(
            f"{order.number} is {order.status.value} and cannot be invoiced",
            code="order_not_open",
            field_errors={f"lines.{index}.sales_order_line_id": ["order not open"]},
        )
    done = order_quantities.sales_fulfilment(db, company_id, [line.id]).get(line.id)
    invoiced = done.fulfilled if done is not None else ZERO
    if invoiced + base_quantity > line.base_quantity:
        raise LedgerStateError(
            f"{order.number} line {line.line_no} ordered {line.base_quantity} with {invoiced} "
            "already invoiced",
            code="invoice_exceeds_order",
            field_errors={f"lines.{index}.quantity": ["exceeds what remains on the order"]},
        )
    return line


def assert_purchase_line_within_order(
    db: Session,
    company_id: int,
    *,
    index: int,
    line_id: int,
    base_quantity: Decimal,
    partner_id: int,
    field: str,
) -> PurchaseOrderLine:
    """The PO line this receipt or invoice fulfils, or a refusal.

    One guard for both ways a purchase order is received — a GRN for stock, an invoice for a
    service or for a direct purchase that carried the goods itself. `receipt_exceeds_order` has
    **no tolerance in v1** (decision 6): a delivery of 101 against an order for 100 is refused
    rather than accrued, because the accrual would then carry value for goods nobody ordered and
    the buyer would find out at the match.
    """
    line = db.get(PurchaseOrderLine, line_id)
    if line is None or line.company_id != company_id:
        raise LedgerStateError(
            "That purchase order line does not exist",
            code="purchase_order_line_not_found",
            field_errors={field: ["not found"]},
        )
    order = get_purchase_order(db, company_id, line.order_id)
    if order.partner_id != partner_id:
        raise LedgerStateError(
            f"{order.number} belongs to another supplier",
            code="order_partner_mismatch",
            field_errors={field: ["wrong supplier"]},
        )
    if order.status not in (PurchaseOrderStatus.OPEN, PurchaseOrderStatus.PARTIALLY_RECEIVED):
        raise LedgerStateError(
            f"{order.number} is {order.status.value} and cannot be received against",
            code="order_not_open",
            field_errors={field: ["order not open"]},
        )
    done = order_quantities.purchase_fulfilment(db, company_id, [line.id]).get(line.id)
    received = done.fulfilled if done is not None else ZERO
    if received + base_quantity > line.base_quantity:
        raise LedgerStateError(
            f"{order.number} line {line.line_no} ordered {line.base_quantity} with {received} "
            "already received",
            code="receipt_exceeds_order",
            field_errors={f"lines.{index}.quantity": ["exceeds what remains on the order"]},
        )
    return line


# --- Audit snapshots ----------------------------------------------------------------------------


def _sales_snapshot(order: SalesOrder) -> dict:
    return {
        "number": order.number,
        "status": order.status.value,
        "partner_id": order.partner_id,
        "order_date": order.order_date.isoformat(),
        "currency_id": order.currency_id,
        "warehouse_id": order.warehouse_id,
        "total_amount": str(order.total_amount),
        "lines": [
            {
                "line_no": line.line_no,
                "item_id": line.item_id,
                "base_quantity": str(line.base_quantity),
                "unit_price": str(line.unit_price),
                "warehouse_id": line.warehouse_id,
            }
            for line in order.lines
        ],
    }


def _purchase_snapshot(order: PurchaseOrder) -> dict:
    return {
        "number": order.number,
        "status": order.status.value,
        "partner_id": order.partner_id,
        "order_date": order.order_date.isoformat(),
        "currency_id": order.currency_id,
        "warehouse_id": order.warehouse_id,
        "total_amount": str(order.total_amount),
        "lines": [
            {
                "line_no": line.line_no,
                "item_id": line.item_id,
                "base_quantity": str(line.base_quantity),
                "unit_price": str(line.unit_price),
                "warehouse_id": line.warehouse_id,
            }
            for line in order.lines
        ],
    }
