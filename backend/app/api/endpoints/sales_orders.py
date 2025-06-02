from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import User, ARTransaction
from app.schemas.sales_order import (
    SalesOrder, SalesOrderCreate, SalesOrderUpdate, SalesOrderToInvoice
)
from app.services.sales_order_service import SalesOrderService

router = APIRouter()


@router.post("/", response_model=SalesOrder)
def create_sales_order(
    order_data: SalesOrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create a new sales order"""
    return SalesOrderService.create_sales_order(db, order_data, current_user.id)


@router.get("/", response_model=List[SalesOrder])
def get_sales_orders(
    customer_id: Optional[int] = Query(None, description="Filter by customer"),
    status: Optional[str] = Query(None, description="Filter by status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get list of sales orders"""
    return SalesOrderService.get_sales_orders(
        db, customer_id, status, skip, limit
    )


@router.get("/{order_id}", response_model=SalesOrder)
def get_sales_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get a specific sales order"""
    return SalesOrderService.get_sales_order(db, order_id)


@router.put("/{order_id}", response_model=SalesOrder)
def update_sales_order(
    order_id: int,
    order_update: SalesOrderUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update a sales order"""
    return SalesOrderService.update_sales_order(db, order_id, order_update)


@router.post("/{order_id}/confirm", response_model=SalesOrder)
def confirm_sales_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Confirm a sales order"""
    return SalesOrderService.confirm_sales_order(db, order_id)


@router.post("/convert-to-invoice")
def convert_to_invoice(
    invoice_data: SalesOrderToInvoice,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Convert a sales order to an AR invoice"""
    ar_transaction = SalesOrderService.convert_to_invoice(
        db, invoice_data, current_user.id
    )
    return {
        "message": "Sales order converted to invoice successfully",
        "ar_transaction_id": ar_transaction.id,
        "ar_transaction_number": ar_transaction.transaction_number
    }


@router.post("/{order_id}/cancel", response_model=SalesOrder)
def cancel_sales_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Cancel a sales order"""
    return SalesOrderService.cancel_sales_order(db, order_id) 