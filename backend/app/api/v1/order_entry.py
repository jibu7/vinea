"""Order Entry (P6).

Order defaults (step 1), then sales orders, purchase orders, goods receipts and the three
order-to-document flows (step 3). The landed-cost document arrives with step 4.

**The flows post nothing.** `…/invoice`, `…/receive` and `…/process-invoice` each build a
document and hand it back for a person to look at; it is submitted through the endpoint that
already posts that kind of document, which is where every over-fulfilment guard lives. They are
POSTs rather than GETs because each one is a deliberate action against a named order and the
result depends on the company's stock position at the moment it is asked for, not because any
of them writes.
"""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_db, get_tenant_context
from app.api.idempotency import IdempotencyKey, fingerprint
from app.core import permissions
from app.core.errors import PermissionDeniedError
from app.models.inventory import GoodsReceivedNote, GrnStatus
from app.models.order_entry import (
    PurchaseOrder,
    PurchaseOrderStatus,
    SalesOrder,
    SalesOrderStatus,
)
from app.order_entry import flows as order_flows
from app.order_entry import grn as grn_service
from app.order_entry import kits as order_kits
from app.order_entry import masters
from app.order_entry import orders as orders_service
from app.order_entry import quantities as order_quantities
from app.schemas.common import Page
from app.schemas.order_entry import (
    BreakupWrite,
    GrnCreate,
    GrnLineRead,
    GrnRead,
    GrnReverse,
    GrnSummary,
    OrderDefaultsRead,
    OrderDefaultsUpdate,
    OrderTransition,
    PreparedDocumentRead,
    PreparedGrnLineRead,
    PreparedGrnRead,
    PreparedLineRead,
    PurchaseOrderLineRead,
    PurchaseOrderRead,
    PurchaseOrderSummary,
    PurchaseOrderWrite,
    SalesOrderLineRead,
    SalesOrderRead,
    SalesOrderSummary,
    SalesOrderWrite,
)

router = APIRouter(prefix="/oe", tags=["order-entry"])

#: Reading the defaults opens to anyone who may look at an order-entry screen at all; writing
#: them is `oe:setup_manage`, the same split every other module's defaults use.
VIEW_PERMISSIONS = (
    permissions.OE_SETUP_MANAGE,
    permissions.OE_SALES_ORDERS_MANAGE,
    permissions.OE_PURCHASE_ORDERS_MANAGE,
    permissions.OE_GRV_PROCESS,
    permissions.OE_REPORTS_VIEW,
)


def _require_view(auth: AuthContext) -> None:
    if not any(permission in auth.permissions for permission in VIEW_PERMISSIONS):
        raise PermissionDeniedError(
            f"Missing required permission(s): {' or '.join(VIEW_PERMISSIONS)}"
        )


@router.get("/defaults")
def get_defaults(
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> OrderDefaultsRead:
    _require_view(auth)
    return OrderDefaultsRead.model_validate(masters.order_defaults(db, auth.company_id))


@router.put("/defaults")
def update_defaults(
    payload: OrderDefaultsUpdate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.OE_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> OrderDefaultsRead:
    settings = masters.update_order_defaults(
        db,
        auth.company_id,
        payload.model_dump(exclude_unset=True),
        actor=auth.user,
        request=request,
    )
    db.commit()
    return OrderDefaultsRead.model_validate(settings)


# --- Sales orders -------------------------------------------------------------------------


def _sales_order_read(db: Session, order: SalesOrder) -> SalesOrderRead:
    """The order with its derived quantities on every line.

    `invoiced` and `remaining` are read from the view, not from the order — there is no column
    to read (decision 4) — and they are put on the line here rather than being fetched by the
    screen, because a grid that asked separately would show a header and a line grid that
    disagreed the moment somebody else posted an invoice between the two requests.
    """
    fulfilment = order_quantities.sales_fulfilment(db, order.company_id, order_id=order.id)
    return SalesOrderRead(
        **{
            field: getattr(order, field)
            for field in SalesOrderRead.model_fields
            if field != "lines"
        },
        lines=[
            SalesOrderLineRead(
                **{
                    field: getattr(line, field)
                    for field in SalesOrderLineRead.model_fields
                    if field not in ("invoiced", "remaining")
                },
                invoiced=_done(fulfilment, line.id),
                remaining=_left(fulfilment, line.id, line.base_quantity),
            )
            for line in order.lines
        ],
    )


def _purchase_order_read(db: Session, order: PurchaseOrder) -> PurchaseOrderRead:
    fulfilment = order_quantities.purchase_fulfilment(db, order.company_id, order_id=order.id)
    return PurchaseOrderRead(
        **{
            field: getattr(order, field)
            for field in PurchaseOrderRead.model_fields
            if field != "lines"
        },
        lines=[
            PurchaseOrderLineRead(
                **{
                    field: getattr(line, field)
                    for field in PurchaseOrderLineRead.model_fields
                    if field not in ("received", "remaining")
                },
                received=_done(fulfilment, line.id),
                remaining=_left(fulfilment, line.id, line.base_quantity),
            )
            for line in order.lines
        ],
    )


def _done(fulfilment: dict, line_id: int) -> Decimal:
    row = fulfilment.get(line_id)
    return row.fulfilled if row is not None else Decimal(0)


def _left(fulfilment: dict, line_id: int, ordered: Decimal) -> Decimal:
    row = fulfilment.get(line_id)
    return row.remaining if row is not None else ordered


def _line_inputs(payload) -> tuple[orders_service.OrderLineInput, ...]:  # noqa: ANN001
    return tuple(
        orders_service.OrderLineInput(
            item_id=line.item_id,
            quantity=line.quantity,
            line_id=line.line_id,
            uom_id=line.uom_id,
            unit_price=line.unit_price,
            discount_percent=line.discount_percent,
            tax_code_id=line.tax_code_id,
            warehouse_id=line.warehouse_id,
            project_id=line.project_id,
            description=line.description,
        )
        for line in payload.lines
    )


@router.get("/sales-orders")
def list_sales_orders(
    partner_id: int | None = None,
    status_filter: SalesOrderStatus | None = Query(default=None, alias="status"),
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> Page[SalesOrderSummary]:
    _require_view(auth)
    rows, next_cursor = orders_service.list_sales_orders(
        db,
        auth.company_id,
        partner_id=partner_id,
        status=status_filter,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
        limit=limit,
    )
    return Page(
        items=[SalesOrderSummary.model_validate(row) for row in rows], next_cursor=next_cursor
    )


@router.get("/sales-orders/{order_id}")
def get_sales_order(
    order_id: int,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> SalesOrderRead:
    _require_view(auth)
    return _sales_order_read(db, orders_service.get_sales_order(db, auth.company_id, order_id))


@router.post("/sales-orders", status_code=status.HTTP_201_CREATED)
def create_sales_order(
    payload: SalesOrderWrite,
    request: Request,
    response: Response,
    idempotency_key: str = IdempotencyKey,
    auth: AuthContext = permissions.require(permissions.OE_SALES_ORDERS_MANAGE),
    db: Session = Depends(get_db),
) -> SalesOrderRead:
    order, replayed = orders_service.create_sales_order(
        db,
        auth.company_id,
        orders_service.SalesOrderInput(
            partner_id=payload.partner_id,
            order_date=payload.order_date,
            description=payload.description,
            expected_date=payload.expected_date,
            reference=payload.reference,
            currency_id=payload.currency_id,
            exchange_rate=payload.exchange_rate,
            branch_id=payload.branch_id,
            project_id=payload.project_id,
            warehouse_id=payload.warehouse_id,
            payment_terms_id=payload.payment_terms_id,
            sales_rep_id=payload.sales_rep_id,
            tax_mode=payload.tax_mode,
            lines=_line_inputs(payload),
        ),
        actor=auth.user,
        idempotency_key=idempotency_key,
        idempotency_hash=fingerprint("sales_order", payload),
        request=request,
    )
    db.commit()
    response.status_code = status.HTTP_200_OK if replayed else status.HTTP_201_CREATED
    return _sales_order_read(db, order)


@router.put("/sales-orders/{order_id}")
def update_sales_order(
    order_id: int,
    payload: SalesOrderWrite,
    request: Request,
    auth: AuthContext = permissions.require(permissions.OE_SALES_ORDERS_MANAGE),
    db: Session = Depends(get_db),
) -> SalesOrderRead:
    order = orders_service.get_sales_order(db, auth.company_id, order_id)
    orders_service.update_sales_order(
        db,
        auth.company_id,
        order,
        orders_service.SalesOrderInput(
            partner_id=payload.partner_id,
            order_date=payload.order_date,
            description=payload.description,
            expected_date=payload.expected_date,
            reference=payload.reference,
            currency_id=payload.currency_id,
            exchange_rate=payload.exchange_rate,
            branch_id=payload.branch_id,
            project_id=payload.project_id,
            warehouse_id=payload.warehouse_id,
            payment_terms_id=payload.payment_terms_id,
            sales_rep_id=payload.sales_rep_id,
            tax_mode=payload.tax_mode,
            lines=_line_inputs(payload),
        ),
        actor=auth.user,
        request=request,
    )
    db.commit()
    return _sales_order_read(db, order)


@router.post("/sales-orders/{order_id}/close")
def close_sales_order(
    order_id: int,
    payload: OrderTransition,
    request: Request,
    auth: AuthContext = permissions.require(permissions.OE_SALES_ORDERS_MANAGE),
    db: Session = Depends(get_db),
) -> SalesOrderRead:
    order = orders_service.get_sales_order(db, auth.company_id, order_id)
    orders_service.close_sales_order(
        db, order, on_date=payload.on_date, actor=auth.user, request=request
    )
    db.commit()
    return _sales_order_read(db, order)


@router.post("/sales-orders/{order_id}/cancel")
def cancel_sales_order(
    order_id: int,
    payload: OrderTransition,
    request: Request,
    auth: AuthContext = permissions.require(permissions.OE_SALES_ORDERS_MANAGE),
    db: Session = Depends(get_db),
) -> SalesOrderRead:
    order = orders_service.get_sales_order(db, auth.company_id, order_id)
    orders_service.cancel_sales_order(
        db, order, on_date=payload.on_date, actor=auth.user, request=request
    )
    db.commit()
    return _sales_order_read(db, order)


@router.put("/sales-orders/{order_id}/lines/{line_id}/breakup")
def breakup_sales_order_line(
    order_id: int,
    line_id: int,
    payload: BreakupWrite,
    request: Request,
    auth: AuthContext = permissions.require(permissions.OE_SALES_ORDERS_MANAGE),
    db: Session = Depends(get_db),
) -> SalesOrderRead:
    """**Breakup** (decision 8): edit one kit line's explosion on an open, not-yet-invoiced
    sales order. A PUT because it replaces the whole explosion — a kit is only ever presented
    as a complete list, and a per-row create/update/delete would let a screen leave a half-edited
    bundle behind."""
    order = orders_service.get_sales_order(db, auth.company_id, order_id)
    orders_service.breakup_sales_order_line(
        db,
        auth.company_id,
        order,
        line_id,
        tuple(
            order_kits.ComponentInput(item_id=row.item_id, base_quantity=row.quantity)
            for row in payload.components
        ),
        actor=auth.user,
        request=request,
    )
    db.commit()
    return _sales_order_read(db, order)


@router.post("/sales-orders/{order_id}/invoice")
def invoice_sales_order(
    order_id: int,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PreparedDocumentRead:
    """Prepare an AR invoice from the order's open lines (decision 7). **Posts nothing** — the
    prepared document goes back through `POST /subledger/ar/documents`, which is where the
    over-fulfilment guard lives."""
    _require_view(auth)
    order = orders_service.get_sales_order(db, auth.company_id, order_id)
    return _prepared_document(
        order_flows.prepare_invoice_from_sales_order(db, auth.company_id, order)
    )


# --- Purchase orders -----------------------------------------------------------------------


@router.get("/purchase-orders")
def list_purchase_orders(
    partner_id: int | None = None,
    status_filter: PurchaseOrderStatus | None = Query(default=None, alias="status"),
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> Page[PurchaseOrderSummary]:
    _require_view(auth)
    rows, next_cursor = orders_service.list_purchase_orders(
        db,
        auth.company_id,
        partner_id=partner_id,
        status=status_filter,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
        limit=limit,
    )
    return Page(
        items=[PurchaseOrderSummary.model_validate(row) for row in rows], next_cursor=next_cursor
    )


@router.get("/purchase-orders/{order_id}")
def get_purchase_order(
    order_id: int,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PurchaseOrderRead:
    _require_view(auth)
    return _purchase_order_read(
        db, orders_service.get_purchase_order(db, auth.company_id, order_id)
    )


@router.post("/purchase-orders", status_code=status.HTTP_201_CREATED)
def create_purchase_order(
    payload: PurchaseOrderWrite,
    request: Request,
    response: Response,
    idempotency_key: str = IdempotencyKey,
    auth: AuthContext = permissions.require(permissions.OE_PURCHASE_ORDERS_MANAGE),
    db: Session = Depends(get_db),
) -> PurchaseOrderRead:
    order, replayed = orders_service.create_purchase_order(
        db,
        auth.company_id,
        orders_service.PurchaseOrderInput(
            partner_id=payload.partner_id,
            order_date=payload.order_date,
            description=payload.description,
            expected_date=payload.expected_date,
            reference=payload.reference,
            currency_id=payload.currency_id,
            exchange_rate=payload.exchange_rate,
            branch_id=payload.branch_id,
            project_id=payload.project_id,
            warehouse_id=payload.warehouse_id,
            tax_mode=payload.tax_mode,
            lines=_line_inputs(payload),
        ),
        actor=auth.user,
        idempotency_key=idempotency_key,
        idempotency_hash=fingerprint("purchase_order", payload),
        request=request,
    )
    db.commit()
    response.status_code = status.HTTP_200_OK if replayed else status.HTTP_201_CREATED
    return _purchase_order_read(db, order)


@router.put("/purchase-orders/{order_id}")
def update_purchase_order(
    order_id: int,
    payload: PurchaseOrderWrite,
    request: Request,
    auth: AuthContext = permissions.require(permissions.OE_PURCHASE_ORDERS_MANAGE),
    db: Session = Depends(get_db),
) -> PurchaseOrderRead:
    order = orders_service.get_purchase_order(db, auth.company_id, order_id)
    orders_service.update_purchase_order(
        db,
        auth.company_id,
        order,
        orders_service.PurchaseOrderInput(
            partner_id=payload.partner_id,
            order_date=payload.order_date,
            description=payload.description,
            expected_date=payload.expected_date,
            reference=payload.reference,
            currency_id=payload.currency_id,
            exchange_rate=payload.exchange_rate,
            branch_id=payload.branch_id,
            project_id=payload.project_id,
            warehouse_id=payload.warehouse_id,
            tax_mode=payload.tax_mode,
            lines=_line_inputs(payload),
        ),
        actor=auth.user,
        request=request,
    )
    db.commit()
    return _purchase_order_read(db, order)


@router.post("/purchase-orders/{order_id}/close")
def close_purchase_order(
    order_id: int,
    payload: OrderTransition,
    request: Request,
    auth: AuthContext = permissions.require(permissions.OE_PURCHASE_ORDERS_MANAGE),
    db: Session = Depends(get_db),
) -> PurchaseOrderRead:
    order = orders_service.get_purchase_order(db, auth.company_id, order_id)
    orders_service.close_purchase_order(
        db, order, on_date=payload.on_date, actor=auth.user, request=request
    )
    db.commit()
    return _purchase_order_read(db, order)


@router.post("/purchase-orders/{order_id}/cancel")
def cancel_purchase_order(
    order_id: int,
    payload: OrderTransition,
    request: Request,
    auth: AuthContext = permissions.require(permissions.OE_PURCHASE_ORDERS_MANAGE),
    db: Session = Depends(get_db),
) -> PurchaseOrderRead:
    order = orders_service.get_purchase_order(db, auth.company_id, order_id)
    orders_service.cancel_purchase_order(
        db, order, on_date=payload.on_date, actor=auth.user, request=request
    )
    db.commit()
    return _purchase_order_read(db, order)


@router.post("/purchase-orders/{order_id}/receive")
def receive_purchase_order(
    order_id: int,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PreparedGrnRead:
    """Prepare a goods receipt from the order's open stock lines. **Posts nothing** — it goes
    back through `POST /oe/goods-received-notes`."""
    _require_view(auth)
    order = orders_service.get_purchase_order(db, auth.company_id, order_id)
    prepared = order_flows.prepare_receipt_from_purchase_order(db, auth.company_id, order)
    return PreparedGrnRead(
        partner_id=prepared.grn.partner_id,
        grn_date=prepared.grn.grn_date,
        description=prepared.grn.description,
        warehouse_id=prepared.grn.warehouse_id,
        purchase_order_id=prepared.grn.purchase_order_id,
        supplier_reference=prepared.grn.supplier_reference,
        currency_id=prepared.grn.currency_id,
        lines=[
            PreparedGrnLineRead(
                item_id=line.item_id,
                quantity=line.quantity,
                unit_cost=line.unit_cost,
                warehouse_id=line.warehouse_id,
                description=line.description,
                project_id=line.project_id,
                purchase_order_line_id=line.purchase_order_line_id,
                remaining=remaining,
            )
            for line, remaining in zip(prepared.grn.lines, prepared.remaining, strict=True)
        ],
    )


@router.post("/purchase-orders/{order_id}/process-invoice")
def process_invoice_for_purchase_order(
    order_id: int,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PreparedDocumentRead:
    """Prepare a supplier invoice from the order's open **service** lines — the lines a GRN
    cannot carry, because a service is received by its invoice (decision 4). Posts nothing."""
    _require_view(auth)
    order = orders_service.get_purchase_order(db, auth.company_id, order_id)
    return _prepared_document(
        order_flows.prepare_service_invoice_from_purchase_order(db, auth.company_id, order)
    )


# --- Goods receipts -----------------------------------------------------------------------


def _grn_read(db: Session, grn: GoodsReceivedNote) -> GrnRead:
    matched = grn_service.matched_quantities(
        db, grn.company_id, [line.id for line in grn.lines]
    )
    return GrnRead(
        **{field: getattr(grn, field) for field in GrnRead.model_fields if field != "lines"},
        lines=[
            GrnLineRead(
                **{
                    field: getattr(line, field)
                    for field in GrnLineRead.model_fields
                    if field not in ("matched", "unmatched")
                },
                matched=matched.get(line.id, Decimal(0)),
                unmatched=line.base_quantity - matched.get(line.id, Decimal(0)),
            )
            for line in grn.lines
        ],
    )


@router.get("/goods-received-notes")
def list_goods_received_notes(
    partner_id: int | None = None,
    status_filter: GrnStatus | None = Query(default=None, alias="status"),
    cursor: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> Page[GrnSummary]:
    _require_view(auth)
    statement = select(GoodsReceivedNote).where(GoodsReceivedNote.company_id == auth.company_id)
    if partner_id is not None:
        statement = statement.where(GoodsReceivedNote.partner_id == partner_id)
    if status_filter is not None:
        statement = statement.where(GoodsReceivedNote.status == status_filter)
    if cursor is not None:
        statement = statement.where(GoodsReceivedNote.id > cursor)
    rows = list(db.scalars(statement.order_by(GoodsReceivedNote.id).limit(limit + 1)))
    next_cursor = rows[limit - 1].id if len(rows) > limit else None
    return Page(
        items=[GrnSummary.model_validate(row) for row in rows[:limit]], next_cursor=next_cursor
    )


@router.get("/goods-received-notes/{grn_id}")
def get_goods_received_note(
    grn_id: int,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> GrnRead:
    _require_view(auth)
    return _grn_read(db, grn_service.get_grn(db, auth.company_id, grn_id))


@router.post("/goods-received-notes", status_code=status.HTTP_201_CREATED)
def post_goods_received_note(
    payload: GrnCreate,
    request: Request,
    response: Response,
    idempotency_key: str = IdempotencyKey,
    auth: AuthContext = permissions.require(permissions.OE_GRV_PROCESS),
    db: Session = Depends(get_db),
) -> GrnRead:
    grn, replayed = grn_service.post_grn(
        db,
        auth.company_id,
        grn_service.GrnInput(
            partner_id=payload.partner_id,
            grn_date=payload.grn_date,
            description=payload.description,
            warehouse_id=payload.warehouse_id,
            purchase_order_id=payload.purchase_order_id,
            supplier_reference=payload.supplier_reference,
            currency_id=payload.currency_id,
            branch_id=payload.branch_id,
            lines=tuple(
                grn_service.GrnLineInput(
                    item_id=line.item_id,
                    quantity=line.quantity,
                    unit_cost=line.unit_cost,
                    uom_id=line.uom_id,
                    warehouse_id=line.warehouse_id,
                    description=line.description,
                    project_id=line.project_id,
                    purchase_order_line_id=line.purchase_order_line_id,
                )
                for line in payload.lines
            ),
        ),
        actor=auth.user,
        idempotency_key=idempotency_key,
        idempotency_hash=fingerprint("goods_received_note", payload),
        request=request,
    )
    db.commit()
    response.status_code = status.HTTP_200_OK if replayed else status.HTTP_201_CREATED
    return _grn_read(db, grn)


@router.post("/goods-received-notes/{grn_id}/reverse", status_code=status.HTTP_201_CREATED)
def reverse_goods_received_note(
    grn_id: int,
    payload: GrnReverse,
    request: Request,
    idempotency_key: str = IdempotencyKey,
    auth: AuthContext = permissions.require(permissions.OE_GRV_PROCESS),
    db: Session = Depends(get_db),
) -> GrnRead:
    """Undo a receipt. A GRN with any matched quantity refuses (`grn_matched`) — the invoice
    that matched it comes back first."""
    grn = grn_service.get_grn(db, auth.company_id, grn_id)
    grn_service.reverse_grn(
        db,
        grn,
        on_date=payload.on_date,
        reason=payload.reason,
        actor=auth.user,
        idempotency_key=idempotency_key,
        idempotency_hash=fingerprint("goods_received_note_reverse", payload),
        request=request,
    )
    db.commit()
    return _grn_read(db, grn)


@router.post("/goods-received-notes/{grn_id}/process-invoice")
def process_invoice_for_grn(
    grn_id: int,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PreparedDocumentRead:
    """Prepare a supplier invoice in **matching mode** from the receipt's unmatched lines —
    the second half of the Evolution two-step (§B.1). Posts nothing."""
    _require_view(auth)
    grn = grn_service.get_grn(db, auth.company_id, grn_id)
    return _prepared_document(order_flows.prepare_invoice_from_grn(db, auth.company_id, grn))


# --- Shared ---------------------------------------------------------------------------------


def _prepared_document(prepared: order_flows.PreparedDocument) -> PreparedDocumentRead:
    document = prepared.document
    return PreparedDocumentRead(
        role=prepared.role,
        kind=document.kind,
        partner_id=document.partner_id,
        document_date=document.document_date,
        description=document.description,
        reference=document.reference,
        currency_id=document.currency_id,
        branch_id=document.branch_id,
        project_id=document.project_id,
        payment_terms_id=document.payment_terms_id,
        sales_rep_id=document.sales_rep_id,
        tax_mode=document.tax_mode,
        lines=[
            _prepared_line(line, remaining)
            for line, remaining in zip(document.lines, prepared.remaining, strict=True)
        ],
    )


def _prepared_line(line, remaining: Decimal | None) -> PreparedLineRead:  # noqa: ANN001
    return PreparedLineRead(
        item_id=line.item_id,
        quantity=line.quantity,
        unit_price=line.unit_price,
        discount_percent=line.discount_percent,
        tax_code_id=line.tax_code_id,
        warehouse_id=line.warehouse_id,
        project_id=line.project_id,
        description=line.description,
        sales_order_line_id=line.sales_order_line_id,
        purchase_order_line_id=line.purchase_order_line_id,
        grn_line_id=line.grn_line_id,
        kit_components=(
            [_prepared_line(component, None) for component in line.kit_components]
            if line.kit_components is not None
            else None
        ),
        remaining=remaining,
    )
