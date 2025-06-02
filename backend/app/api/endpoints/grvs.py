from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas.grv import (
    GoodsReceivedVoucher, GRVCreate, GRVUpdate, GRVToInvoice
)
from app.services.grv_service import GRVService

router = APIRouter()


@router.post("/", response_model=GoodsReceivedVoucher)
def create_grv(
    grv_data: GRVCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create a new goods received voucher"""
    return GRVService.create_grv(db, grv_data, current_user.id)


@router.get("/", response_model=List[GoodsReceivedVoucher])
def get_grvs(
    purchase_order_id: Optional[int] = Query(None, description="Filter by purchase order"),
    supplier_id: Optional[int] = Query(None, description="Filter by supplier"),
    status: Optional[str] = Query(None, description="Filter by status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get list of goods received vouchers"""
    return GRVService.get_grvs(
        db, purchase_order_id, supplier_id, status, skip, limit
    )


@router.get("/{grv_id}", response_model=GoodsReceivedVoucher)
def get_grv(
    grv_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get a specific goods received voucher"""
    return GRVService.get_grv(db, grv_id)


@router.put("/{grv_id}", response_model=GoodsReceivedVoucher)
def update_grv(
    grv_id: int,
    grv_update: GRVUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update a goods received voucher"""
    return GRVService.update_grv(db, grv_id, grv_update)


@router.post("/{grv_id}/post-to-inventory", response_model=GoodsReceivedVoucher)
def post_grv_to_inventory(
    grv_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Post a GRV to inventory - updates stock quantities"""
    return GRVService.post_grv_to_inventory(db, grv_id, current_user.id)


@router.post("/convert-to-invoice")
def convert_to_invoice(
    invoice_data: GRVToInvoice,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Convert a GRV to an AP supplier invoice"""
    ap_transaction = GRVService.convert_to_invoice(
        db, invoice_data, current_user.id
    )
    return {
        "message": "GRV converted to supplier invoice successfully",
        "ap_transaction_id": ap_transaction.id,
        "ap_transaction_number": ap_transaction.transaction_number
    }


@router.post("/{grv_id}/cancel", response_model=GoodsReceivedVoucher)
def cancel_grv(
    grv_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Cancel a goods received voucher"""
    return GRVService.cancel_grv(db, grv_id) 