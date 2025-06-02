from fastapi import APIRouter
from app.api.endpoints import (
    oe_document_types,
    sales_orders,
    purchase_orders,
    grvs
)

# Create main OE router
router = APIRouter()

# Include sub-routers
router.include_router(
    oe_document_types.router,
    prefix="/document-types",
    tags=["OE Document Types"]
)

router.include_router(
    sales_orders.router,
    prefix="/sales-orders",
    tags=["Sales Orders"]
)

router.include_router(
    purchase_orders.router,
    prefix="/purchase-orders",
    tags=["Purchase Orders"]
)

router.include_router(
    grvs.router,
    prefix="/grvs",
    tags=["Goods Received Vouchers"]
) 