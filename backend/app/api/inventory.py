from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from app.dependencies import get_current_active_user, require_permission
from app.core.database import get_sync_db
from app.models import User, InventoryItem, InventoryTransactionType, InventoryTransaction
from app.schemas.inventory import (
    InventoryItemCreate, InventoryItemUpdate, InventoryItemResponse,
    InventoryTransactionTypeCreate, InventoryTransactionTypeUpdate, InventoryTransactionTypeResponse,
    InventoryAdjustmentRequest, InventoryTransactionResponse,
    InventoryItemListingRequest, StockQuantityReportRequest
)
from app.services.inventory_service import InventoryService

router = APIRouter()

# Inventory Items Endpoints
@router.post("/items", response_model=InventoryItemResponse)
async def create_inventory_item(
    item: InventoryItemCreate,
    db: Session = Depends(get_sync_db),
    current_user: User = Depends(require_permission("inventory", "create"))
) -> InventoryItem:
    """Create a new inventory item"""
    service = InventoryService(db)
    return service.create_item(current_user.company_id, item)


@router.get("/items", response_model=List[InventoryItemResponse])
async def list_inventory_items(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    item_type: Optional[str] = None,
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_sync_db),
    current_user: User = Depends(require_permission("inventory", "view"))
) -> List[InventoryItem]:
    """List inventory items with filtering"""
    service = InventoryService(db)
    return service.list_items(
        current_user.company_id,
        skip=skip,
        limit=limit,
        item_type=item_type,
        is_active=is_active,
        search=search
    )


@router.get("/items/{item_id}", response_model=InventoryItemResponse)
async def get_inventory_item(
    item_id: int,
    db: Session = Depends(get_sync_db),
    current_user: User = Depends(require_permission("inventory", "view"))
) -> InventoryItem:
    """Get a single inventory item"""
    service = InventoryService(db)
    item = service.get_item(current_user.company_id, item_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Item not found"
        )
    return item


@router.put("/items/{item_id}", response_model=InventoryItemResponse)
async def update_inventory_item(
    item_id: int,
    item_data: InventoryItemUpdate,
    db: Session = Depends(get_sync_db),
    current_user: User = Depends(require_permission("inventory", "edit"))
) -> InventoryItem:
    """Update an inventory item"""
    service = InventoryService(db)
    return service.update_item(current_user.company_id, item_id, item_data)


@router.delete("/items/{item_id}")
async def delete_inventory_item(
    item_id: int,
    db: Session = Depends(get_sync_db),
    current_user: User = Depends(require_permission("inventory", "delete"))
) -> dict:
    """Delete an inventory item (soft delete if has transactions)"""
    service = InventoryService(db)
    service.delete_item(current_user.company_id, item_id)
    return {"message": "Item deleted successfully"}


# Transaction Types Endpoints
@router.post("/transaction-types", response_model=InventoryTransactionTypeResponse)
async def create_transaction_type(
    tt_data: InventoryTransactionTypeCreate,
    db: Session = Depends(get_sync_db),
    current_user: User = Depends(require_permission("inventory", "create"))
) -> InventoryTransactionType:
    """Create a new inventory transaction type"""
    service = InventoryService(db)
    return service.create_transaction_type(current_user.company_id, tt_data)


@router.get("/transaction-types", response_model=List[InventoryTransactionTypeResponse])
async def list_transaction_types(
    db: Session = Depends(get_sync_db),
    current_user: User = Depends(require_permission("inventory", "view"))
) -> List[InventoryTransactionType]:
    """List all inventory transaction types"""
    service = InventoryService(db)
    return service.list_transaction_types(current_user.company_id)


@router.get("/transaction-types/{tt_id}", response_model=InventoryTransactionTypeResponse)
async def get_transaction_type(
    tt_id: int,
    db: Session = Depends(get_sync_db),
    current_user: User = Depends(require_permission("inventory", "view"))
) -> InventoryTransactionType:
    """Get a single transaction type"""
    service = InventoryService(db)
    tt = service.get_transaction_type(current_user.company_id, tt_id)
    if not tt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transaction type not found"
        )
    return tt


@router.put("/transaction-types/{tt_id}", response_model=InventoryTransactionTypeResponse)
async def update_transaction_type(
    tt_id: int,
    tt_data: InventoryTransactionTypeUpdate,
    db: Session = Depends(get_sync_db),
    current_user: User = Depends(require_permission("inventory", "edit"))
) -> InventoryTransactionType:
    """Update a transaction type"""
    service = InventoryService(db)
    return service.update_transaction_type(current_user.company_id, tt_id, tt_data)


@router.delete("/transaction-types/{tt_id}")
async def delete_transaction_type(
    tt_id: int,
    db: Session = Depends(get_sync_db),
    current_user: User = Depends(require_permission("inventory", "delete"))
) -> dict:
    """Delete a transaction type"""
    service = InventoryService(db)
    service.delete_transaction_type(current_user.company_id, tt_id)
    return {"message": "Transaction type deleted successfully"}


# Inventory Adjustment Endpoints
@router.post("/adjustments", response_model=InventoryTransactionResponse)
async def process_inventory_adjustment(
    adjustment: InventoryAdjustmentRequest,
    db: Session = Depends(get_sync_db),
    current_user: User = Depends(require_permission("inventory", "post"))
) -> InventoryTransaction:
    """Process an inventory adjustment with GL integration"""
    service = InventoryService(db)
    return service.process_adjustment(
        current_user.company_id,
        current_user.id,
        adjustment
    )


# Transaction History
@router.get("/items/{item_id}/transactions", response_model=List[InventoryTransactionResponse])
async def get_item_transactions(
    item_id: int,
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: Session = Depends(get_sync_db),
    current_user: User = Depends(require_permission("inventory", "view"))
) -> List[InventoryTransaction]:
    """Get transaction history for an item"""
    service = InventoryService(db)
    
    # Verify item exists and belongs to user's company
    item = service.get_item(current_user.company_id, item_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Item not found"
        )
    
    return service.get_item_transactions(
        current_user.company_id,
        item_id,
        date_from=date_from,
        date_to=date_to
    )


# Reports
@router.post("/reports/stock-quantity")
async def generate_stock_quantity_report(
    report_params: StockQuantityReportRequest,
    db: Session = Depends(get_sync_db),
    current_user: User = Depends(require_permission("inventory", "view"))
) -> dict:
    """Generate stock quantity report"""
    service = InventoryService(db)
    return service.get_stock_quantity_report(
        current_user.company_id,
        item_type=report_params.item_type,
        show_zero_qty=report_params.show_zero_qty,
        item_code_from=report_params.item_code_from,
        item_code_to=report_params.item_code_to
    )


@router.get("/reports/item-listing")
async def generate_item_listing_report(
    item_type: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_sync_db),
    current_user: User = Depends(require_permission("inventory", "view"))
) -> dict:
    """Generate inventory item listing report"""
    service = InventoryService(db)
    items = service.list_items(
        current_user.company_id,
        skip=0,
        limit=10000,  # Get all items for report
        item_type=item_type,
        is_active=is_active,
        search=search
    )
    
    report_data = []
    for item in items:
        report_data.append({
            'item_code': item.item_code,
            'description': item.description,
            'item_type': item.item_type.value,
            'unit_of_measure': item.unit_of_measure,
            'quantity_on_hand': float(item.quantity_on_hand),
            'cost_price': float(item.cost_price),
            'selling_price': float(item.selling_price),
            'total_value': float(item.quantity_on_hand * item.cost_price),
            'is_active': item.is_active
        })
    
    return {
        'report_name': 'Inventory Item Listing',
        'generated_at': datetime.now().isoformat(),
        'company_id': current_user.company_id,
        'filters': {
            'item_type': item_type,
            'is_active': is_active,
            'search': search
        },
        'data': report_data,
        'summary': {
            'total_items': len(report_data),
            'total_value': sum(item['total_value'] for item in report_data)
        }
    } 