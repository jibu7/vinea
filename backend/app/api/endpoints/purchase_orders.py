from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas.purchase_order import (
    PurchaseOrder, PurchaseOrderCreate, PurchaseOrderUpdate,
    PurchaseOrderLine
)
from app.services.purchase_order_service import PurchaseOrderService

router = APIRouter()


@router.post("/", response_model=PurchaseOrder)
def create_purchase_order(
    order_data: PurchaseOrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create a new purchase order"""
    return PurchaseOrderService.create_purchase_order(db, order_data, current_user.id)


@router.get("/", response_model=List[PurchaseOrder])
def get_purchase_orders(
    supplier_id: Optional[int] = Query(None, description="Filter by supplier"),
    status: Optional[str] = Query(None, description="Filter by status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get list of purchase orders"""
    return PurchaseOrderService.get_purchase_orders(
        db, supplier_id, status, skip, limit
    )


@router.get("/open-lines", response_model=List[PurchaseOrderLine])
def get_open_purchase_order_lines(
    supplier_id: Optional[int] = Query(None, description="Filter by supplier"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get purchase order lines that have not been fully received"""
    return PurchaseOrderService.get_open_po_lines(db, supplier_id)


@router.get("/{order_id}", response_model=PurchaseOrder)
def get_purchase_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get a specific purchase order"""
    return PurchaseOrderService.get_purchase_order(db, order_id)


@router.put("/{order_id}", response_model=PurchaseOrder)
def update_purchase_order(
    order_id: int,
    order_update: PurchaseOrderUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update a purchase order"""
    return PurchaseOrderService.update_purchase_order(db, order_id, order_update)


@router.post("/{order_id}/confirm", response_model=PurchaseOrder)
def confirm_purchase_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Confirm a purchase order"""
    return PurchaseOrderService.confirm_purchase_order(db, order_id)


@router.post("/{order_id}/cancel", response_model=PurchaseOrder)
def cancel_purchase_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Cancel a purchase order"""
    return PurchaseOrderService.cancel_purchase_order(db, order_id) 