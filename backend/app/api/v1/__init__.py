from fastapi import APIRouter

from app.api.v1 import (
    auth,
    company,
    fiscal,
    gl,
    inventory,
    invitations,
    memberships,
    operator,
    order_entry,
    subledger,
    tax,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(company.router)
api_router.include_router(invitations.router)
api_router.include_router(memberships.router)
api_router.include_router(operator.router)
api_router.include_router(gl.router)
api_router.include_router(subledger.router)
api_router.include_router(inventory.router)
api_router.include_router(order_entry.router)
api_router.include_router(fiscal.router)
api_router.include_router(tax.router)
