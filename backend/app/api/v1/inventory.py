"""Inventory API (P5). Step 1 is masters: units of measure, items and barcodes,
warehouses and the company's inventory defaults.

Transaction types live on the GL router (`/gl/transaction-types?module=inv`) — one table
serves every module, so inventory gets a `module` filter rather than a second endpoint.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import idempotency
from app.api.deps import AuthContext, get_tenant_context
from app.core import permissions
from app.core.errors import PermissionDeniedError
from app.db import get_db
from app.inventory import documents as inventory_documents
from app.inventory import masters
from app.models.audit import AuditLog
from app.models.inventory import ItemType
from app.schemas.common import Page
from app.schemas.inventory import (
    BarcodeCreate,
    BarcodeRead,
    BarcodeUpdate,
    InventoryDefaultsRead,
    InventoryDefaultsUpdate,
    ItemAuditRead,
    ItemCreate,
    ItemLookupRead,
    ItemRead,
    ItemUpdate,
    StockDocumentCreate,
    StockDocumentLineRead,
    StockDocumentRead,
    StockDocumentReverse,
    StockDocumentSummary,
    UomCategoryCreate,
    UomCategoryRead,
    UomCategoryUpdate,
    UomCategoryWithUnits,
    UomCreate,
    UomRead,
    UomUpdate,
    WarehouseCreate,
    WarehouseRead,
    WarehouseUpdate,
)

router = APIRouter(prefix="/inventory", tags=["inventory"])


def _require_view(auth: AuthContext) -> None:
    """Reading a master is enough for anyone who can see inventory *or* maintain it — the
    item picker on a document is read-only but belongs to a poster, not a reporter."""
    allowed = (
        permissions.INV_REPORTS_VIEW,
        permissions.INV_SETUP_MANAGE,
        permissions.INV_TRANSACTIONS_ADJUST,
    )
    if not any(permission in auth.permissions for permission in allowed):
        raise PermissionDeniedError(f"Missing required permission(s): {' or '.join(allowed)}")


# --- Units of measure ---------------------------------------------------------------------


@router.get("/uom-categories")
def list_uom_categories(
    include_inactive: bool = False,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[UomCategoryWithUnits]:
    _require_view(auth)
    categories = masters.list_uom_categories(
        db, auth.company_id, include_inactive=include_inactive
    )
    units = masters.list_uoms(db, auth.company_id, include_inactive=include_inactive)
    by_category: dict[int, list[UomRead]] = {}
    for unit in units:
        by_category.setdefault(unit.category_id, []).append(UomRead.model_validate(unit))
    return [
        UomCategoryWithUnits(
            **UomCategoryRead.model_validate(category).model_dump(),
            uoms=by_category.get(category.id, []),
        )
        for category in categories
    ]


@router.post("/uom-categories", status_code=status.HTTP_201_CREATED)
def create_uom_category(
    payload: UomCategoryCreate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.INV_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> UomCategoryWithUnits:
    category, base = masters.create_uom_category(
        db,
        auth.company_id,
        code=payload.code,
        name=payload.name,
        base_uom_code=payload.base_uom_code,
        base_uom_name=payload.base_uom_name,
        base_uom_decimal_places=payload.base_uom_decimal_places,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return UomCategoryWithUnits(
        **UomCategoryRead.model_validate(category).model_dump(),
        uoms=[UomRead.model_validate(base)],
    )


@router.patch("/uom-categories/{category_id}")
def update_uom_category(
    category_id: int,
    payload: UomCategoryUpdate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.INV_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> UomCategoryRead:
    category = masters.get_uom_category(db, auth.company_id, category_id)
    masters.update_uom_category(
        db,
        category,
        name=payload.name,
        is_active=payload.is_active,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return UomCategoryRead.model_validate(category)


@router.get("/uoms")
def list_uoms(
    category_id: int | None = None,
    include_inactive: bool = False,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[UomRead]:
    _require_view(auth)
    rows = masters.list_uoms(
        db, auth.company_id, category_id=category_id, include_inactive=include_inactive
    )
    return [UomRead.model_validate(row) for row in rows]


@router.post("/uoms", status_code=status.HTTP_201_CREATED)
def create_uom(
    payload: UomCreate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.INV_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> UomRead:
    uom = masters.create_uom(
        db,
        auth.company_id,
        category_id=payload.category_id,
        code=payload.code,
        name=payload.name,
        factor_to_base=payload.factor_to_base,
        decimal_places=payload.decimal_places,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return UomRead.model_validate(uom)


@router.patch("/uoms/{uom_id}")
def update_uom(
    uom_id: int,
    payload: UomUpdate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.INV_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> UomRead:
    uom = masters.get_uom(db, auth.company_id, uom_id)
    masters.update_uom(
        db,
        uom,
        name=payload.name,
        factor_to_base=payload.factor_to_base,
        decimal_places=payload.decimal_places,
        is_active=payload.is_active,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return UomRead.model_validate(uom)


# --- Items --------------------------------------------------------------------------------


@router.get("/items")
def list_items(
    search: str | None = None,
    item_type: ItemType | None = None,
    include_inactive: bool = False,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[ItemRead]:
    _require_view(auth)
    rows = masters.list_items(
        db,
        auth.company_id,
        search=search,
        item_type=item_type,
        include_inactive=include_inactive,
    )
    return [ItemRead.model_validate(row) for row in rows]


@router.get("/items/lookup")
def lookup_item(
    q: str,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> ItemLookupRead:
    """Resolve a typed item code or a scanned barcode (decision 8). Declared before
    `/items/{item_id}` so "lookup" is never read as an id."""
    _require_view(auth)
    item, uom, pack_quantity = masters.lookup_item(db, auth.company_id, q)
    return ItemLookupRead(
        item=ItemRead.model_validate(item),
        uom=UomRead.model_validate(uom),
        pack_quantity=pack_quantity,
        matched_barcode=None if q == item.code else q,
    )


@router.post("/items", status_code=status.HTTP_201_CREATED)
def create_item(
    payload: ItemCreate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.INV_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> ItemRead:
    item = masters.create_item(
        db,
        auth.company_id,
        masters.ItemInput(**payload.model_dump()),
        actor=auth.user,
        request=request,
    )
    db.commit()
    return ItemRead.model_validate(item)


@router.get("/items/{item_id}")
def get_item(
    item_id: int,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> ItemRead:
    _require_view(auth)
    return ItemRead.model_validate(masters.get_item(db, auth.company_id, item_id))


@router.patch("/items/{item_id}")
def update_item(
    item_id: int,
    payload: ItemUpdate,
    request: Request,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> ItemRead:
    """Renaming the code needs `inv:item_rename`; everything else needs `inv:setup_manage`.
    The rename is separated because the code is what every enquiry and report reads by."""
    item = masters.get_item(db, auth.company_id, item_id)
    renaming = payload.code is not None and payload.code != item.code
    needed = permissions.INV_ITEM_RENAME if renaming else permissions.INV_SETUP_MANAGE
    if needed not in auth.permissions:
        raise PermissionDeniedError(f"Missing required permission(s): {needed}")
    masters.update_item(
        db,
        item,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        item_type=payload.item_type,
        uom_category_id=payload.uom_category_id,
        base_uom_id=payload.base_uom_id,
        inventory_account_id=_optional(
            payload.inventory_account_id, payload.clear_inventory_account
        ),
        cogs_account_id=_optional(payload.cogs_account_id, payload.clear_cogs_account),
        sales_account_id=_optional(payload.sales_account_id, payload.clear_sales_account),
        default_sales_tax_code_id=_optional(
            payload.default_sales_tax_code_id, payload.clear_sales_tax_code
        ),
        default_purchase_tax_code_id=_optional(
            payload.default_purchase_tax_code_id, payload.clear_purchase_tax_code
        ),
        selling_price=payload.selling_price,
        price_includes_tax=payload.price_includes_tax,
        is_active=payload.is_active,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return ItemRead.model_validate(item)


def _optional(value: int | None, clear: bool) -> int | None | object:
    """`...` means "leave alone"; `None` means "clear" — the P4 convention, so a PATCH that
    omits a field never silently blanks it."""
    if clear:
        return None
    return ... if value is None else value


@router.get("/items/{item_id}/history")
def get_item_history(
    item_id: int,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[ItemAuditRead]:
    """Rename history for the item code. The trail hangs off `item_id`, so a code can move
    without the history following it — the same shape as the customer/supplier rename."""
    _require_view(auth)
    item = masters.get_item(db, auth.company_id, item_id)
    rows = db.scalars(
        select(AuditLog)
        .where(
            AuditLog.company_id == auth.company_id,
            AuditLog.entity == "items",
            AuditLog.entity_id == str(item.id),
        )
        # `at` is the transaction clock, so two rows written by one request tie on it;
        # the id breaks the tie in write order.
        .order_by(AuditLog.at.desc(), AuditLog.id.desc())
    ).all()
    return [ItemAuditRead.model_validate(row) for row in rows]


# --- Barcodes -----------------------------------------------------------------------------


@router.get("/items/{item_id}/barcodes")
def list_barcodes(
    item_id: int,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[BarcodeRead]:
    _require_view(auth)
    item = masters.get_item(db, auth.company_id, item_id)
    return [
        BarcodeRead.model_validate(row)
        for row in masters.list_barcodes(db, auth.company_id, item.id)
    ]


@router.post("/items/{item_id}/barcodes", status_code=status.HTTP_201_CREATED)
def create_barcode(
    item_id: int,
    payload: BarcodeCreate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.INV_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> BarcodeRead:
    item = masters.get_item(db, auth.company_id, item_id)
    row = masters.create_barcode(
        db,
        auth.company_id,
        item,
        barcode=payload.barcode,
        uom_id=payload.uom_id,
        pack_quantity=payload.pack_quantity,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return BarcodeRead.model_validate(row)


@router.patch("/barcodes/{barcode_id}")
def update_barcode(
    barcode_id: int,
    payload: BarcodeUpdate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.INV_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> BarcodeRead:
    row = masters.get_barcode(db, auth.company_id, barcode_id)
    masters.update_barcode(
        db,
        row,
        uom_id=payload.uom_id,
        pack_quantity=payload.pack_quantity,
        is_active=payload.is_active,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return BarcodeRead.model_validate(row)


# --- Warehouses ---------------------------------------------------------------------------


@router.get("/warehouses")
def list_warehouses(
    include_inactive: bool = False,
    include_in_transit: bool = False,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[WarehouseRead]:
    _require_view(auth)
    rows = masters.list_warehouses(
        db,
        auth.company_id,
        include_inactive=include_inactive,
        include_in_transit=include_in_transit,
    )
    return [WarehouseRead.model_validate(row) for row in rows]


@router.post("/warehouses", status_code=status.HTTP_201_CREATED)
def create_warehouse(
    payload: WarehouseCreate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.INV_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> WarehouseRead:
    warehouse = masters.create_warehouse(
        db,
        auth.company_id,
        code=payload.code,
        name=payload.name,
        branch_id=payload.branch_id,
        is_default=payload.is_default,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return WarehouseRead.model_validate(warehouse)


@router.patch("/warehouses/{warehouse_id}")
def update_warehouse(
    warehouse_id: int,
    payload: WarehouseUpdate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.INV_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> WarehouseRead:
    warehouse = masters.get_warehouse(db, auth.company_id, warehouse_id)
    masters.update_warehouse(
        db,
        warehouse,
        name=payload.name,
        branch_id=payload.branch_id,
        is_default=payload.is_default,
        is_active=payload.is_active,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return WarehouseRead.model_validate(warehouse)


# --- Defaults -----------------------------------------------------------------------------


@router.get("/defaults")
def get_defaults(
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> InventoryDefaultsRead:
    _require_view(auth)
    return InventoryDefaultsRead.model_validate(
        masters.inventory_defaults(db, auth.company_id)
    )


@router.patch("/defaults")
def update_defaults(
    payload: InventoryDefaultsUpdate,
    request: Request,
    auth: AuthContext = permissions.require(permissions.INV_SETUP_MANAGE),
    db: Session = Depends(get_db),
) -> InventoryDefaultsRead:
    settings = masters.update_inventory_defaults(
        db,
        auth.company_id,
        payload.model_dump(exclude_unset=True),
        actor=auth.user,
        request=request,
    )
    db.commit()
    return InventoryDefaultsRead.model_validate(settings)


# --- Stock documents (P5 step 3) -----------------------------------------------------------


def _require_post(auth: AuthContext) -> None:
    if permissions.INV_TRANSACTIONS_ADJUST not in auth.permissions:
        raise PermissionDeniedError(
            f"Missing required permission(s): {permissions.INV_TRANSACTIONS_ADJUST}"
        )


def _line_inputs(payload: StockDocumentCreate) -> tuple[inventory_documents.DocumentLineInput, ...]:
    return tuple(
        inventory_documents.DocumentLineInput(
            item_id=line.item_id,
            warehouse_id=line.warehouse_id,
            quantity=line.quantity,
            uom_id=line.uom_id,
            unit_cost=line.unit_cost,
            value=line.value,
            transaction_type_id=line.transaction_type_id,
            contra_account_id=line.contra_account_id,
            project_id=line.project_id,
            description=line.description,
        )
        for line in payload.lines
    )


def _document_read(db: Session, company_id: int, document) -> StockDocumentRead:
    lines = inventory_documents.lines_of(db, company_id, document.id)
    return StockDocumentRead(
        **StockDocumentSummary.model_validate(document).model_dump(),
        transaction_type_id=document.transaction_type_id,
        lines=[StockDocumentLineRead.model_validate(line) for line in lines],
    )


@router.post("/adjustments", status_code=status.HTTP_201_CREATED)
def post_adjustment(
    payload: StockDocumentCreate,
    request: Request,
    response: Response,
    idempotency_key: str = idempotency.IdempotencyKey,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> StockDocumentRead:
    """A single-item adjustment — an increase, a decrease or a revaluation by transaction type.

    Replaying the key returns the original document with `200` rather than posting a second
    one; that is the whole contract of `Idempotency-Key` here, and it holds even when the
    posting valued nothing and so produced no journal entry to hang the key on.
    """
    _require_post(auth)
    document, replayed = inventory_documents.post_adjustment(
        db,
        auth.company_id,
        inventory_documents.DocumentInput(
            document_date=payload.document_date,
            description=payload.description,
            reference=payload.reference,
            transaction_type_id=payload.transaction_type_id,
            lines=_line_inputs(payload),
        ),
        actor=auth.user,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency.fingerprint("inventory_adjustment", payload),
        request=request,
    )
    db.commit()
    if replayed:
        response.status_code = status.HTTP_200_OK
    return _document_read(db, auth.company_id, document)


@router.post("/journal-batches", status_code=status.HTTP_201_CREATED)
def post_journal_batch(
    payload: StockDocumentCreate,
    request: Request,
    response: Response,
    idempotency_key: str = idempotency.IdempotencyKey,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> StockDocumentRead:
    """Many items, many warehouses, mixed directions — one document, one journal entry, one
    unit of work (decisions 3 and 9). A line that fails takes the whole batch with it.

    This is also the go-live path for opening stock: inventory accounts are control accounts
    (decision 2), so opening balances cannot arrive as a GL journal — they come through here
    under an `opening_balance` transaction type.
    """
    _require_post(auth)
    document, replayed = inventory_documents.post_batch(
        db,
        auth.company_id,
        inventory_documents.DocumentInput(
            document_date=payload.document_date,
            description=payload.description,
            reference=payload.reference,
            transaction_type_id=payload.transaction_type_id,
            lines=_line_inputs(payload),
        ),
        actor=auth.user,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency.fingerprint("inventory_batch", payload),
        request=request,
    )
    db.commit()
    if replayed:
        response.status_code = status.HTTP_200_OK
    return _document_read(db, auth.company_id, document)


@router.post("/documents/{document_id}/reverse", status_code=status.HTTP_201_CREATED)
def reverse_stock_document(
    document_id: int,
    payload: StockDocumentReverse,
    request: Request,
    idempotency_key: str = idempotency.IdempotencyKey,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> StockDocumentRead:
    """Decision 11: the kernel reversal plus reversing moves at the original values.

    Under the `block` policy a receipt whose quantity has since been issued cannot be
    reversed — the reversing move would take a location below zero, and it fails with
    `insufficient_stock` before anything is written.
    """
    _require_post(auth)
    reversal = inventory_documents.reverse_document(
        db,
        auth.company_id,
        document_id,
        on_date=payload.reversal_date,
        reason=payload.reason,
        actor=auth.user,
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency.fingerprint("inventory_reversal", payload),
        request=request,
    )
    db.commit()
    return _document_read(db, auth.company_id, reversal)


@router.get("/documents")
def list_stock_documents(
    doc_type: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> Page[StockDocumentSummary]:
    _require_view(auth)
    rows, next_cursor = inventory_documents.list_documents(
        db,
        auth.company_id,
        doc_type=doc_type,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
        limit=limit,
    )
    return Page(
        items=[StockDocumentSummary.model_validate(row) for row in rows],
        next_cursor=next_cursor,
    )


@router.get("/documents/{document_id}")
def get_stock_document(
    document_id: int,
    auth: AuthContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> StockDocumentRead:
    _require_view(auth)
    document = inventory_documents.get_document(db, auth.company_id, document_id)
    return _document_read(db, auth.company_id, document)
